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
from typing import Annotated, Any, Literal

import numpy as np
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

    # 2) 读 image + mask (mask>0 = bad pixel); NaN/inf 像素并入 mask
    #    管线 run_isophote 假设数据已无 NaN (如 *_nonan.fits); 真实 drizzle
    #    图像常含 NaN, 此处把非有限值并入 mask 以适配, 不改变检测算法。
    image = fits.getdata(image_path).astype(np.float64)
    nonfinite = ~np.isfinite(image)
    if mask_path and os.path.exists(mask_path):
        mask = (np.asarray(fits.getdata(mask_path)) > 0) | nonfinite
    elif nonfinite.any():
        mask = nonfinite
    else:
        mask = None

    # 3) 像素尺度从 WCS 读 (唯一接口适配)
    try:
        _meta = extract_fits_metadata(image_path)
        pixscale = float(_meta[1])
    except Exception as e:
        return {"status": "failure", "error": f"Failed to read WCS pixel scale: {e}"}

    # band_or_survey 决定 compute_mu 表面亮度零点: SDSS/'r' -> SDSS, 否则 JWST
    band_or_survey = 'r' if survey_uc == 'SDSS' else 'F200W'

    # 4) PSF FWHM (原硬编码)
    psf_fwhm = PSF_FWHM_ARCSEC.get(survey_uc, 0.067)

    # 5) 三步等照度拟合 (纯函数, 原算法)
    try:
        _df_s1, df_s2, df_s3, _info = fit_isophotes(
            image, mask, pixscale, band_or_survey
        )
    except Exception as e:
        return {"status": "failure", "error": f"Isophote fitting failed: {e}"}

    # 6-8) 棒/偏侧检测 (原函数, 原阈值)。等照度表为空时跳过检测 (防 detect_bar
    #      对空 DataFrame 取列时 KeyError), 返回未检出 + failure 标记。
    if 'eps' in df_s3.columns and len(df_s3) >= 5:
        bar_result = detect_bar(df_s3, criteria=CGS_Z0_CRITERIA)
        dolfi = analyze_dolfi_a1(df_s3)
    else:
        bar_result = {
            'bar_detected': False, 'classification': '', 'e_max': np.nan,
            'bar_length_arcsec': np.nan, 'bar_pa_mean': np.nan,
            'bar_pa_var': np.nan, 'failure_reason': 'too_few_isophotes',
        }
        dolfi = {'lopsided_dolfi': False, 'A1_mean': np.nan,
                 'phi1_mean': np.nan, 'r50': np.nan, 'r90': np.nan}

    if 'x0_pix' in df_s2.columns and len(df_s2) >= 3:
        center = analyze_center_offset_v2(
            df_s2, pixscale, survey_uc.lower(), band_or_survey
        )
    else:
        center = {'lopsided_center': False, 'x_trend_r': np.nan,
                  'y_trend_r': np.nan, 'dr_norm': np.nan}

    is_lopsided = bool(dolfi['lopsided_dolfi'] and center['lopsided_center'])

    # ---- 组装 JSON (仅检测结论; 命中时附带关键量) ----
    bar_detected = bool(bar_result['bar_detected'])
    result = {
        "status": "success",
        "bar": {"detected": bar_detected},
        "lopsidedness": {"detected": bool(is_lopsided)},
    }

    # 命中 bar: 附带位置角 PA 与轴比 b/a (= 1 - e_max)
    if bar_detected:
        e_max = bar_result.get('e_max')
        result["bar"]["pa_deg"] = _round(bar_result.get('bar_pa_mean'), 2)
        result["bar"]["b_over_a"] = _round(1.0 - e_max, 3) if e_max is not None else None

    # 命中 lopsidedness: 附带 m=1 振幅 mag (≈A1) 与相位 (deg)
    if is_lopsided:
        result["lopsidedness"]["mag"] = _round(dolfi.get('A1_mean'), 4)
        result["lopsidedness"]["phase_deg"] = _round(dolfi.get('phi1_mean'), 2)

    return _to_jsonable(result)

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
        image = fits.getdata(image_path).astype(np.float64)
        nonfinite = ~np.isfinite(image)
        if mask_path and os.path.exists(mask_path):
            mask = (np.asarray(fits.getdata(mask_path)) > 0) | nonfinite
        elif nonfinite.any():
            mask = nonfinite
        else:
            mask = None
            
        band_or_survey = 'r' if survey.upper() == 'SDSS' else info.band    
        psf_fwhw = PSF_FWHM_ARCSEC.get(survey.upper(), 0.067)
        try:
            _df_s1, df_s2, df_s3, _info = fit_isophotes(
                image, mask, info.pixscale, band_or_survey
            )
        except Exception as e:
            results.append({"band": info.band, "error": f"Isophote fitting failed: {e}"})
            continue
        
        if 'eps' in df_s3.columns and len(df_s3) >= 5:
            bar_result = detect_bar(df_s3, criteria=CGS_Z0_CRITERIA)
            dolfi = analyze_dolfi_a1(df_s3)
        else:
            bar_result = {
                'bar_detected': False, 'classification': '', 'e_max': np.nan,
                'bar_length_arcsec': np.nan, 'bar_pa_mean': np.nan,
                'bar_pa_var': np.nan, 'failure_reason': 'too_few_isophotes',
            }
            dolfi = {'lopsided_dolfi': False, 'A1_mean': np.nan,
                     'phi1_mean': np.nan, 'r50': np.nan, 'r90': np.nan}
        
        if 'x0_pix' in df_s2.columns and len(df_s2) >= 3:
            center = analyze_center_offset_v2(
                df_s2, info.pixscale, survey.lower(), band_or_survey
            )
        else:
            center = {'lopsided_center': False, 'x_trend_r': np.nan,
                      'y_trend_r': np.nan, 'dr_norm': np.nan}
        is_lopsided = bool(dolfi['lopsided_dolfi'] and center['lopsided_center'])
        bar_detected = bool(bar_result['bar_detected'])
        result = {
            "band": info.band,
            "bar": {"detected": bar_detected},
            "lopsidedness": {"detected": bool(is_lopsided)},
        }
        if bar_detected:
            e_max = bar_result.get('e_max')
            pa_deg = _round(bar_result.get('bar_pa_mean'), 2)
            # NOTE: galfit的0度是y轴正方向, 逆时针为正; galfits的0度由fits文件中指定，此处需要转换
            pa_deg = ((pa_deg + _delta_ang + 90) % 360 + 180) % 360 - 180
            result["bar"]["pa_deg"] = pa_deg
            result["bar"]["b_over_a"] = _round(1.0 - e_max, 3) if e_max is not None else None
        if is_lopsided:
            result["lopsidedness"]["mag"] = _round(dolfi.get('A1_mean'), 4)
            phase_deg = _round(dolfi.get('phi1_mean'), 2)
            # NOTE: galfit的0度是y轴正方向, 逆时针为正; galfits的0度由fits文件中指定，此处需要转换
            phase_deg = ((phase_deg + _delta_ang + 90) % 360 + 180) % 360 - 180
            result["lopsidedness"]["phase_deg"] = phase_deg
        results.append(_to_jsonable(result))    
        
    return {"status": "success", "results": results}

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
