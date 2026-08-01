"""detect_bar_lopsidedness — MCP 接口适配层

为已有的 bar_lopsidedness 管线包一层 MCP 友好接口: 输入单波段 GALFIT
feedme + survey, 返回 JSON (是否含 Bar / Lopsidedness)。

忠实于原实现: 三步等照度拟合、棒检测四步验证、偏侧双方法 AND 全部调用
管线原函数, 阈值 / PSF / 判据原样不变。唯一因接口变化而做的是像素尺度从
image WCS 读取 
"""

import os
import sys
import warnings
from typing import Annotated, Any, Literal, Optional

import numpy as np
import pandas as pd
from astropy.io import fits

# 核心算法 (自包含, 迁移自管线包, 见 bar_lopsidedness_core.py)
from .bar_lopsidedness_core import (
    fit_isophotes,
    detect_bar,
    analyze_dolfi_a1,
    analyze_center_offset_v2,
)
from .parse_lyric import (
    extract_fits_metadata, 
    parse_image_infos_from_lyric, 
    parse_region_info_from_lyric
)
from .parse_feedme import parse_feedme

warnings.filterwarnings('ignore')


# ---- 原实现参数 (忠实, 值复制自管线, 标注来源) ----
# 来源: bar_lopsidedness_pipeline_scripts_.../src/run_batch.py:26 (CGS_Z0_CRITERIA)
CGS_Z0_CRITERIA = {
    'e_max_threshold': 0.365,
    'pa_stability': 29.04,
    'e_drop': 0.324,
    'pa_change_outer': 74.78,
}
# 来源: run_lopsidedness.analyze_center_offset_v2 内部硬编码 PSF FWHM
PSF_FWHM_ARCSEC = {
    'JWST': 0.067,
    'SDSS': 1.3,
}


def _to_jsonable(obj: Any) -> Any:
    """递归把 numpy 类型转成 JSON 可序列化的 Python 类型 (NaN -> None)。"""
    if isinstance(obj, dict):
        return {k: _to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_to_jsonable(v) for v in obj]
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.floating):
        v = float(obj)
        return v if np.isfinite(v) else None
    if isinstance(obj, np.ndarray):
        return [_to_jsonable(v) for v in obj.tolist()]
    if isinstance(obj, bool):
        return obj
    if isinstance(obj, float):
        return obj if np.isfinite(obj) else None
    if isinstance(obj, (int, str)) or obj is None:
        return obj
    return str(obj)


def _round(v: Any, ndigits: int) -> Any:
    """标量四舍五入; None/NaN -> None (保持输出整洁)。"""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return round(f, ndigits) if np.isfinite(f) else None


def _read_image_and_mask(
    image_path: str,
    mask_path: Optional[str],
) -> tuple[np.ndarray, Optional[np.ndarray]]:
    """Read science image and optional bad-pixel mask.

    The pipeline convention is mask > 0 means bad pixel. Non-finite image pixels
    are always folded into the mask before fitting.
    """
    image = fits.getdata(image_path).astype(np.float64)
    nonfinite = ~np.isfinite(image)
    if mask_path and os.path.exists(mask_path):
        raw_mask = np.asarray(fits.getdata(mask_path)) > 0
        if raw_mask.shape != image.shape:
            raise ValueError(
                f"Mask shape {raw_mask.shape} does not match image shape {image.shape}"
            )
        mask = raw_mask | nonfinite
    elif nonfinite.any():
        mask = nonfinite
    else:
        mask = None
    return image, mask


def _apply_fit_region(
    image: np.ndarray,
    mask: Optional[np.ndarray],
    region: Optional[tuple[int, int, int, int]],
    *,
    one_indexed_inclusive: bool,
) -> tuple[np.ndarray, Optional[np.ndarray]]:
    """Crop image/mask to a GALFIT fitting region.

    ``parse_feedme`` returns GALFIT H) bounds as 1-indexed inclusive pixel
    coordinates. ``parse_lyric`` returns 0-indexed, exclusive Python slice
    bounds. This helper normalizes both forms before isophote fitting.
    """
    if region is None:
        return image, mask

    xmin, xmax, ymin, ymax = [int(v) for v in region]
    if one_indexed_inclusive:
        x0, x1 = xmin - 1, xmax
        y0, y1 = ymin - 1, ymax
    else:
        x0, x1 = xmin, xmax
        y0, y1 = ymin, ymax

    ny, nx = image.shape
    x0, x1 = max(0, x0), min(nx, x1)
    y0, y1 = max(0, y0), min(ny, y1)
    if x0 >= x1 or y0 >= y1:
        raise ValueError(f"Invalid fitting region after clipping: {region}")

    cropped_image = np.ascontiguousarray(image[y0:y1, x0:x1])
    cropped_mask = None if mask is None else np.ascontiguousarray(mask[y0:y1, x0:x1])
    return cropped_image, cropped_mask


def _empty_bar_result(reason: str) -> dict[str, Any]:
    return {
        'bar_detected': False, 'classification': '', 'e_max': np.nan,
        'bar_length_arcsec': np.nan, 'bar_pa_mean': np.nan,
        'bar_pa_var': np.nan, 'failure_reason': reason,
    }


def _empty_dolfi_result() -> dict[str, Any]:
    return {'lopsided_dolfi': False, 'A1_mean': np.nan,
            'phi1_mean': np.nan, 'r50': np.nan, 'r90': np.nan}


def _empty_center_result() -> dict[str, Any]:
    return {'lopsided_center': False, 'x_trend_r': np.nan,
            'y_trend_r': np.nan, 'dr_norm': np.nan}


def _classify_profiles(
    df_s2: pd.DataFrame,
    df_s3: pd.DataFrame,
    pixscale: float,
    survey_uc: str,
    band_or_survey: str,
) -> dict[str, Any]:
    """Run the current bar and lopsidedness criteria on Step 2/3 profiles."""
    if 'eps' in df_s3.columns and len(df_s3) >= 5:
        bar_result = detect_bar(df_s3, criteria=CGS_Z0_CRITERIA)
        dolfi = analyze_dolfi_a1(df_s3)
    else:
        bar_result = _empty_bar_result('too_few_isophotes')
        dolfi = _empty_dolfi_result()

    if 'x0_pix' in df_s2.columns and len(df_s2) >= 3:
        center = analyze_center_offset_v2(
            df_s2, pixscale, survey_uc.lower(), band_or_survey
        )
    else:
        center = _empty_center_result()

    return {
        'bar_result': bar_result,
        'dolfi': dolfi,
        'center': center,
        'is_lopsided': bool(dolfi['lopsided_dolfi'] and center['lopsided_center']),
    }


def _format_detection_result(
    classified: dict[str, Any],
    *,
    band: Optional[str] = None,
    delta_ang: Optional[float] = None,
    include_status: bool = True,
    include_diagnostics: bool = False,
) -> dict[str, Any]:
    """Format core classification output for the MCP-facing JSON response."""
    bar_result = classified['bar_result']
    dolfi = classified['dolfi']
    center = classified['center']
    is_lopsided = bool(classified['is_lopsided'])
    bar_detected = bool(bar_result['bar_detected'])

    result: dict[str, Any] = {}
    if include_status:
        result["status"] = "success"
    if band is not None:
        result["band"] = band
    result["bar"] = {"detected": bar_detected}
    result["lopsidedness"] = {"detected": is_lopsided}

    if bar_detected:
        e_max = bar_result.get('e_max')
        pa_deg = _round(bar_result.get('bar_pa_mean'), 2)
        if delta_ang is not None and pa_deg is not None:
            pa_deg = ((pa_deg + delta_ang + 90) % 360 + 180) % 360 - 180
        result["bar"]["pa_deg"] = pa_deg
        result["bar"]["b_over_a"] = _round(1.0 - e_max, 3) if e_max is not None else None

    if is_lopsided:
        result["lopsidedness"]["mag"] = _round(dolfi.get('A1_mean'), 4)
        phase_deg = _round(dolfi.get('phi1_mean'), 2)
        if delta_ang is not None and phase_deg is not None:
            phase_deg = ((phase_deg + delta_ang + 90) % 360 + 180) % 360 - 180
        result["lopsidedness"]["phase_deg"] = phase_deg

    if include_diagnostics:
        result["bar"]["failure_reason"] = bar_result.get('failure_reason')
        result["bar"]["e_max"] = _round(bar_result.get('e_max'), 4)
        result["lopsidedness"].update({
            "dolfi_detected": bool(dolfi.get('lopsided_dolfi', False)),
            "center_detected": bool(center.get('lopsided_center', False)),
            "dolfi_A1_mean": _round(dolfi.get('A1_mean'), 4),
            "dolfi_r50": _round(dolfi.get('r50'), 4),
            "dolfi_r90": _round(dolfi.get('r90'), 4),
            "center_dr_norm": _round(center.get('dr_norm'), 4),
            "center_x_trend_r": _round(center.get('x_trend_r'), 4),
            "center_y_trend_r": _round(center.get('y_trend_r'), 4),
        })

    return _to_jsonable(result)


def detect_bar_lopsidedness(
    feedme_file: Annotated[str, "Absolute path to a single-band GALFIT feedme file"],
    survey: Annotated[Literal["JWST", "SDSS"], "Data survey type"],
) -> dict[str, Any]:
    """Detect bar and lopsidedness from a single-band GALFIT feedme.

    Args:
        feedme_file: Absolute path to a single-band GALFIT feedme.
        survey: 'JWST' or 'SDSS' (selects PSF FWHM for center-offset method).

    Returns:
        dict with status, bar {detected}, lopsidedness {detected}.
        Only the detection conclusions are returned; detailed fit
        parameters (e_max, PA, A1, offsets, etc.) are filtered out.
    """
    feedme_file = os.path.abspath(feedme_file)
    if not os.path.exists(feedme_file):
        return {"status": "failure", "error": f"Feedme file not found: {feedme_file}"}

    survey_uc = str(survey).upper()

    # 1) 解析 feedme -> image(A) / mask(F) / fit_region(H)
    paths = parse_feedme(feedme_file)
    image_path = paths.get("input", "")
    mask_path = paths.get("mask", "")

    if not image_path or not os.path.exists(image_path):
        return {"status": "failure",
                "error": f"Input image (A) not found in feedme: {image_path}"}

    # 2) Read image + mask, then honor the GALFIT H) fitting box.  The
    # original pipeline fits already-prepared cutouts; running on a full
    # feedme image without this crop changes the Step 2/3 profiles directly.
    try:
        image, mask = _read_image_and_mask(image_path, mask_path)
        image, mask = _apply_fit_region(
            image, mask, paths.get("fit_region"), one_indexed_inclusive=True
        )
    except Exception as e:
        return {"status": "failure", "error": f"Failed to load/crop image: {e}"}

    # 3) 像素尺度从 WCS 读 (唯一接口适配)
    try:
        _meta = extract_fits_metadata(image_path)
        pixscale = float(_meta[1])
    except Exception as e:
        return {"status": "failure", "error": f"Failed to read WCS pixel scale: {e}"}

    # band_or_survey 决定 compute_mu 表面亮度零点: SDSS/'r' -> SDSS, 否则 JWST
    band_or_survey = 'r' if survey_uc == 'SDSS' else 'F200W'

    # 4) 三步等照度拟合 (纯函数, 原算法)
    try:
        _df_s1, df_s2, df_s3, _info = fit_isophotes(
            image, mask, pixscale, band_or_survey
        )
    except Exception as e:
        return {"status": "failure", "error": f"Isophote fitting failed: {e}"}

    classified = _classify_profiles(df_s2, df_s3, pixscale, survey_uc, band_or_survey)
    return _format_detection_result(classified)

def detect_galfits_bar_lopsidedness(
    lyric_file: Annotated[str, "Absolute path to a lyric file containing galfits configurations"],
    survey: Annotated[Literal["JWST", "SDSS"], "Data survey type"],
)-> dict[str, Any]:
    """Detect bar and lopsidedness from a lyric file containing galfits configurations.

    Args:
        lyric_file: Absolute path to a lyric file containing galfits configurations.
        survey: 'JWST' or 'SDSS' (selects PSF FWHM for center-offset method).
    Returns:
        dict with status, bar {detected}, lopsidedness {detected} by bands.
        Only the detection conclusions are returned; detailed fit
        parameters (e_max, PA, A1, offsets, etc.) are filtered out.
    """
    lyric_file = os.path.abspath(lyric_file)
    if not os.path.exists(lyric_file):
        return {"status": "failure", "error": f"Lyric file not found: {lyric_file}"}

    try:
        image_infos = parse_image_infos_from_lyric(lyric_file)
        region_info = parse_region_info_from_lyric(lyric_file)
    except Exception as e:
        return {"status": "failure", "error": f"Failed to parse lyric file: {e}"}

    results = []
    for info in image_infos:
        image_path = info.image[0]
        mask_path = info.mask[0]
        if not image_path or not os.path.exists(image_path):
            results.append({"band": info.band, "error": f"Image file not found: {image_path}"})
            continue

        _shape, _pixsc, _x0, _y0, _delta_ang, _wcs = extract_fits_metadata(
            image_path, ra=region_info.ra, dec=region_info.dec)
        try:
            image, mask = _read_image_and_mask(image_path, mask_path)
            image, mask = _apply_fit_region(
                image, mask, info.fitting_region, one_indexed_inclusive=False
            )
        except Exception as e:
            results.append({"band": info.band, "error": f"Failed to load/crop image: {e}"})
            continue
            
        band_or_survey = 'r' if survey.upper() == 'SDSS' else info.band    
        try:
            _df_s1, df_s2, df_s3, _info = fit_isophotes(
                image, mask, info.pixscale, band_or_survey
            )
        except Exception as e:
            results.append({"band": info.band, "error": f"Isophote fitting failed: {e}"})
            continue

        classified = _classify_profiles(
            df_s2, df_s3, info.pixscale, survey.upper(), band_or_survey
        )
        result = _format_detection_result(
            classified, band=info.band, delta_ang=_delta_ang, include_status=False
        )
        results.append(_to_jsonable(result))    
        
    return {"status": "success", "results": results}


def _infer_pixscale_from_profiles(
    df_s2: pd.DataFrame,
    df_s3: pd.DataFrame,
    survey_uc: str,
    band: Optional[str],
) -> float:
    """Infer pixel scale from saved isophote tables, with pipeline defaults."""
    for df in (df_s3, df_s2):
        if {'sma_arcsec', 'sma_pix'}.issubset(df.columns):
            ratio = df['sma_arcsec'] / df['sma_pix']
            ratio = ratio.replace([np.inf, -np.inf], np.nan).dropna()
            ratio = ratio[ratio > 0]
            if len(ratio) > 0:
                return float(np.median(ratio))

    if survey_uc == 'SDSS':
        return 0.396
    band_uc = (band or 'F200W').upper()
    if band_uc in {'F277W', 'F356W', 'F410M', 'F444W'}:
        return 0.04
    return 0.02


def detect_bar_lopsidedness_from_isophote_tables(
    step2_csv: Annotated[str, "Path to Step 2 free-center isophote CSV"],
    step3_csv: Annotated[str, "Path to Step 3 fixed-center isophote CSV"],
    survey: Annotated[Literal["JWST", "SDSS"], "Data survey type"],
    band: Annotated[Optional[str], "Band label, e.g. F200W or r"] = None,
    pixscale: Annotated[Optional[float], "Pixel scale override in arcsec/pixel"] = None,
) -> dict[str, Any]:
    """Reproduce bar/lopsidedness decisions from saved pipeline profiles.

    This is the strict regression path for comparing against the reference
    bar_lopsidedness pipeline outputs: Step 2 supplies the free-center offset
    profile and Step 3 supplies the fixed-center bar/Dolfi A1 profile.
    """
    if not os.path.exists(step2_csv):
        return {"status": "failure", "error": f"Step 2 CSV not found: {step2_csv}"}
    if not os.path.exists(step3_csv):
        return {"status": "failure", "error": f"Step 3 CSV not found: {step3_csv}"}

    survey_uc = str(survey).upper()
    band_or_survey = 'r' if survey_uc == 'SDSS' else (band or 'F200W')
    try:
        df_s2 = pd.read_csv(step2_csv)
    except pd.errors.EmptyDataError:
        df_s2 = pd.DataFrame()
    try:
        df_s3 = pd.read_csv(step3_csv)
    except pd.errors.EmptyDataError:
        df_s3 = pd.DataFrame()
    pix = pixscale
    if pix is None:
        pix = _infer_pixscale_from_profiles(df_s2, df_s3, survey_uc, band)

    classified = _classify_profiles(df_s2, df_s3, pix, survey_uc, band_or_survey)
    return _format_detection_result(classified, include_diagnostics=True)

def TEST_detect_galfits_bar_lopsidedness():
    lyric_file = "/home/jiangbo/jwst/1803/obj_1803.lyric"
    survey = "JWST"
    import time
    start_time = time.time()
    result = detect_galfits_bar_lopsidedness(lyric_file, survey)
    end_time = time.time()
    print(f"Execution time: {end_time - start_time} seconds")
    print(result)

if __name__ == '__main__':
    # 手动冒烟测试入口: python -m tools.bar_lopsidedness_detection <feedme> <survey>
    # import json as _json
    # _feedme = sys.argv[1] if len(sys.argv) > 1 else ""
    # _survey = sys.argv[2] if len(sys.argv) > 2 else "JWST"
    # _res = detect_bar_lopsidedness(_feedme, _survey)  # type: ignore[arg-type]
    # print(_json.dumps(_res, indent=2, ensure_ascii=False))
    TEST_detect_galfits_bar_lopsidedness()
