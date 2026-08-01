"""bar_lopsidedness_core — 自包含的棒/偏侧性检测核心算法

迁移自 bar_lopsidedness_pipeline_scripts_20260609_structured/src/ (config.py /
run_isophote.py / run_bar_detection.py / run_lopsidedness.py), 算法逐字保持一致,
仅剥离文件 IO、目录约定与脚本入口, 使 MCP 工具不依赖那个带日期戳的外部脚本包。

公开 API:
  - fit_isophotes(image, mask, pixscale, band_or_survey) -> (df_s1, df_s2, df_s3, info)
  - detect_bar(df_isophote, criteria=None) -> dict
  - analyze_dolfi_a1(df_s3, a1_threshold=0.1) -> dict
  - analyze_center_offset_v2(df_s2, pixscl, survey, band_label) -> dict

所有阈值/常量/PSF 处理与原管线一致 (CGS z=0 棒阈值、Dolfi A1>0.1、
center-offset |r|>0.5 & p<0.05 & dr_norm>0.01、PSF FWHM jwst=0.067/sdss=1.3)。
"""

import numpy as np
import pandas as pd
from astropy.stats import sigma_clipped_stats
from photutils.isophote import Ellipse, EllipseGeometry
from scipy.signal import find_peaks
from scipy.stats import linregress

import warnings
warnings.filterwarnings('ignore', category=UserWarning)


# ============================================================
# 常量 (迁移自 config.py)
# ============================================================

# --- 表面亮度零点 ---
PIXSCL_SDSS = 0.396                              # SDSS r-band
SB_ZP_JWST = 20.472                              # MJy/sr -> mag/arcsec^2
SB_ZP_SDSS_OFFSET = 2.5 * np.log10(PIXSCL_SDSS**2) + 22.5  # ≈ 20.496


def compute_mu(intensity, band_or_survey):
    """计算表面亮度 mag/arcsec^2 (迁移自 config.compute_mu)。

    band_or_survey 为 'SDSS'/'R' 时用 SDSS 零点, 否则按 JWST (MJy/sr) 处理。
    """
    band_upper = band_or_survey.upper()
    if band_upper == 'SDSS' or band_upper == 'R':
        return -2.5 * np.log10(intensity) + SB_ZP_SDSS_OFFSET
    return -2.5 * np.log10(intensity) + SB_ZP_JWST


# --- isophote 拟合参数 ---
FIT_STEP = 0.2          # SMA 步长 (pixels)
FIT_MAXGERR = 0.5       # 最大谐波振幅误差
FIT_MINSMA = 1          # 最小 SMA (pixels)
SIGMA_THRESHOLD = 1.0   # 背景阈值 (sigma 倍数) 用于确定外边界
BG_EDGE_FRAC = 0.10     # 背景估计: 边缘区域比例
CENTER_OFFSET_MAX = 3   # 中心偏移阈值 (pixels)
SMA0_BASE_FACTOR = 0.5  # sma0 = max(3, round(A_IMAGE * factor))

# --- 棒检测参数 ---
BAR_PEAK_PROMINENCE = 0.05
BAR_PEAK_DISTANCE = 3      # indices
BAR_PEAK_WIDTH = 1         # indices
BAR_MAX_PEAKS_TEST = 2
BAR_PA_STABILITY_RANGE = 5  # indices before peak

# --- 偏侧性参数 ---
A1_THRESHOLD = 0.1        # A1 = I1/I0 阈值 (Dolfi 环带均值)
CO_MIN_DR_NORM = 0.01     # 归一化径向偏移变化下限

# 棒检测默认判据 (z-independent 保守值; 实际调用方传 CGS z=0)
DEFAULT_CRITERIA = {
    'e_max_threshold': 0.2,
    'pa_stability': 20.0,
    'e_drop': 0.02,
    'pa_change_outer': 15.0,
}


# ============================================================
# Isophote 拟合 (迁移自 run_isophote.py, 剥离 load/save)
# ============================================================

def estimate_background(image, mask=None):
    """从图像边缘估计背景。"""
    ny, nx = image.shape
    edge = int(min(nx, ny) * BG_EDGE_FRAC)

    edge_pixels = np.concatenate([
        image[:edge, :].ravel(),
        image[-edge:, :].ravel(),
        image[:, :edge].ravel(),
        image[:, -edge:].ravel(),
    ])

    if mask is not None:
        edge_mask = np.concatenate([
            mask[:edge, :].ravel(),
            mask[-edge:, :].ravel(),
            mask[:, :edge].ravel(),
            mask[:, -edge:].ravel(),
        ])
        edge_pixels = edge_pixels[~edge_mask]

    mean, median, std = sigma_clipped_stats(edge_pixels, sigma=3.0)
    return mean, std


def determine_center(image, mask=None):
    """确定拟合中心: 正像素 flux-weighted centroid; 偏离图像中心 > CENTER_OFFSET_MAX 则用图像中心。"""
    ny, nx = image.shape
    img_cx, img_cy = (nx - 1) / 2.0, (ny - 1) / 2.0

    positive = image > 0
    if mask is not None:
        positive &= ~mask

    if not np.any(positive):
        return img_cx, img_cy

    values = np.where(positive, image, 0)
    total = values.sum()
    if total <= 0:
        return img_cx, img_cy

    yy, xx = np.indices(image.shape)
    cx = (xx * values).sum() / total
    cy = (yy * values).sum() / total

    offset = np.sqrt((cx - img_cx)**2 + (cy - img_cy)**2)
    if offset > CENTER_OFFSET_MAX:
        return img_cx, img_cy
    return cx, cy


def get_initial_params(image, mask=None):
    """从图像获取初始拟合参数 (flux-weighted centroid + 二阶矩)。"""
    ny, nx = image.shape
    cx, cy = determine_center(image, mask)

    positive = image > 0
    if mask is not None:
        positive &= ~mask

    if np.sum(positive) < 10:
        return cx, cy, 0.2, 0.0, max(3, min(nx, ny) // 6)

    values = np.where(positive, image, 0)
    total = values.sum()
    if total <= 0:
        return cx, cy, 0.2, 0.0, max(3, min(nx, ny) // 6)

    yy, xx = np.indices(image.shape)
    dx = xx - cx
    dy = yy - cy
    w = values / total

    x2 = np.sum(w * dx * dx)
    y2 = np.sum(w * dy * dy)
    xy = np.sum(w * dx * dy)

    theta = 0.5 * np.arctan2(2 * xy, x2 - y2)
    Ixx = x2 * np.cos(theta)**2 + 2 * xy * np.cos(theta) * np.sin(theta) + y2 * np.sin(theta)**2
    Iyy = x2 * np.sin(theta)**2 - 2 * xy * np.cos(theta) * np.sin(theta) + y2 * np.cos(theta)**2
    eps = 1.0 - np.sqrt(Iyy / max(Ixx, 1e-30))
    eps = np.clip(eps, 0.01, 0.9)

    sma0 = max(3, int(round(np.sqrt(Ixx) * SMA0_BASE_FACTOR)))
    return cx, cy, eps, theta, sma0


def make_sma0_list(base_sma, cutout_half):
    """生成多个 sma0 候选。"""
    cap = cutout_half - 2
    return sorted(set(min(s, cap) for s in [base_sma, base_sma + 5, base_sma + 10]))


def step1_free_fit(masked_image, geometry, maxsma):
    """Step 1: 自由拟合 (无外边界)。"""
    ellipse = Ellipse(masked_image, geometry)
    try:
        return ellipse.fit_image(
            fix_center=False, fix_pa=False, fix_eps=False,
            minsma=FIT_MINSMA, maxsma=maxsma, step=FIT_STEP,
            maxgerr=FIT_MAXGERR,
        )
    except Exception:
        return None


def find_maxsma(iso_result, bg_std):
    """从 Step 1 结果确定外边界。"""
    if iso_result is None or len(iso_result) < 3:
        return None

    sma = np.array([i.sma for i in iso_result])
    intensity = np.array([i.intens for i in iso_result])

    threshold = bg_std * SIGMA_THRESHOLD
    below = np.where(intensity < threshold)[0]
    if len(below) > 0:
        return sma[below[0]]
    return sma[-1]


def step2_bounded_fit(masked_image, geometry, maxsma):
    """Step 2: 有界重拟合 (自由中心)。"""
    ellipse = Ellipse(masked_image, geometry)
    try:
        return ellipse.fit_image(
            fix_center=False, fix_pa=False, fix_eps=False,
            minsma=FIT_MINSMA, maxsma=maxsma, step=FIT_STEP,
            maxgerr=FIT_MAXGERR,
        )
    except Exception:
        return None


def calculate_fixed_center(iso_result):
    """从 Step 2 结果计算固定中心 (knee-point detection)。返回 ((x0,y0), ok)。"""
    if iso_result is None or len(iso_result) < 5:
        return None, False

    sma = np.array([i.sma for i in iso_result])
    x0 = np.array([i.x0 for i in iso_result])
    y0 = np.array([i.y0 for i in iso_result])

    order = np.argsort(sma)
    sma, x0, y0 = sma[order], x0[order], y0[order]

    stability = np.array([
        (np.var(x0[:i+1]) + np.var(y0[:i+1])) / 2.0
        for i in range(len(sma))
    ])

    grad = np.diff(stability)
    n_baseline = min(max(3, len(grad) // 10), 10)
    baseline_mean = np.mean(grad[:n_baseline])
    baseline_std = np.std(grad[:n_baseline]) if n_baseline > 1 else 0

    threshold = baseline_mean + 5.0 * max(baseline_std, 1e-10)
    knee_idx = np.where(grad > threshold)[0]

    end = knee_idx[0] + 1 if len(knee_idx) > 0 else len(sma)

    cx = np.mean(x0[:end])
    cy = np.mean(y0[:end])
    return (cx, cy), True


def step3_fixed_center_fit(masked_image, geometry, maxsma):
    """Step 3: 固定中心拟合。"""
    ellipse = Ellipse(masked_image, geometry)
    try:
        return ellipse.fit_image(
            fix_center=True, fix_pa=False, fix_eps=False,
            minsma=FIT_MINSMA, maxsma=maxsma, step=FIT_STEP,
            maxgerr=FIT_MAXGERR,
        )
    except Exception:
        return None


def extract_isophote_table(iso_result, pixscl, band_or_survey):
    """从 IsophoteList 提取结果为 DataFrame。"""
    if iso_result is None or len(iso_result) == 0:
        return pd.DataFrame()

    records = []
    for iso in iso_result:
        if iso.intens <= 0 or not iso.valid:
            continue

        # m=1 Fourier 振幅 (用于 lopsidedness)
        a1, b1 = np.nan, np.nan
        try:
            angles = iso.sample.values[0]    # azimuthal angles
            intens = iso.sample.values[2]    # intensity values
            a1 = 2.0 * np.mean(intens * np.cos(angles))
            b1 = 2.0 * np.mean(intens * np.sin(angles))
        except Exception:
            pass

        # m=2 Fourier 振幅
        a2, b2 = np.nan, np.nan
        try:
            angles = iso.sample.values[0]
            intens = iso.sample.values[2]
            a2 = 2.0 * np.mean(intens * np.cos(2 * angles))
            b2 = 2.0 * np.mean(intens * np.sin(2 * angles))
        except Exception:
            pass

        records.append({
            'sma_pix': iso.sma,
            'sma_arcsec': iso.sma * pixscl,
            'intensity': iso.intens,
            'int_err': iso.int_err,
            'eps': iso.eps,
            'eps_err': iso.ellip_err if hasattr(iso, 'ellip_err') else np.nan,
            'pa_deg': np.degrees(iso.pa) - 90,   # 天文 PA: CCW from North
            'pa_err_deg': np.degrees(iso.pa_err) if hasattr(iso, 'pa_err') else np.nan,
            'x0_pix': iso.x0,
            'y0_pix': iso.y0,
            'x0_err': iso.x0_err if hasattr(iso, 'x0_err') else np.nan,
            'y0_err': iso.y0_err if hasattr(iso, 'y0_err') else np.nan,
            'grad': iso.grad if hasattr(iso, 'grad') else np.nan,
            'grad_err': iso.grad_error if hasattr(iso, 'grad_error') else np.nan,
            'mu_mag_arcsec2': compute_mu(iso.intens, band_or_survey),
            'a1': a1, 'b1': b1, 'a2': a2, 'b2': b2,
            'a3': iso.a3 if hasattr(iso, 'a3') else np.nan,
            'b3': iso.b3 if hasattr(iso, 'b3') else np.nan,
            'a3_err': iso.a3_err if hasattr(iso, 'a3_err') else np.nan,
            'b3_err': iso.b3_err if hasattr(iso, 'b3_err') else np.nan,
            'a4': iso.a4 if hasattr(iso, 'a4') else np.nan,
            'b4': iso.b4 if hasattr(iso, 'b4') else np.nan,
            'a4_err': iso.a4_err if hasattr(iso, 'a4_err') else np.nan,
            'b4_err': iso.b4_err if hasattr(iso, 'b4_err') else np.nan,
        })

    df = pd.DataFrame(records)
    if len(df) > 0:
        # 规范化 PA 到 [-90, 90]
        df['pa_deg'] = ((df['pa_deg'] + 90) % 180) - 90
    return df


def fit_isophotes(image, mask, pixscale, band_or_survey='F200W'):
    """三步 isophote 拟合的纯函数 (无文件 IO)。

    Parameters
    ----------
    image : 2D array
        科学图像 (原始数据单位)。
    mask : 2D bool array or None
        True = 坏像素。
    pixscale : float
        像素尺度 (arcsec/pixel)。
    band_or_survey : str
        传给 compute_mu() 决定表面亮度零点 ('SDSS'/'R' → SDSS, 否则 JWST)。

    Returns
    -------
    (df_s1, df_s2, df_s3, info) : tuple
        三步等照度表 + 诊断 dict。
    """
    ny, nx = image.shape
    dim = min(nx, ny)

    # 背景估计与扣除
    bg_mean, bg_std = estimate_background(image, mask)
    image_bgsub = image - bg_mean

    # MaskedArray
    if mask is not None and np.any(mask):
        masked_image = np.ma.MaskedArray(image_bgsub, mask=mask)
    else:
        masked_image = image_bgsub

    # 初始参数
    cx, cy, eps0, pa0_rad, sma0_base = get_initial_params(image_bgsub, mask)
    sma0_list = make_sma0_list(sma0_base, dim // 2)

    # ---- Step 1: Free fit ----
    geometry = EllipseGeometry(cx, cy, sma=sma0_list[0], eps=eps0, pa=pa0_rad)
    maxsma_s1 = dim / 2 * 1.35
    iso_step1 = None
    for sma0 in sma0_list:
        geometry.sma = sma0
        iso_step1 = step1_free_fit(masked_image, geometry, maxsma_s1)
        if iso_step1 is not None and len(iso_step1) >= 3:
            break

    maxsma_free = find_maxsma(iso_step1, bg_std)
    if maxsma_free is None:
        maxsma_free = dim / 2 * 0.9

    # ---- Step 2: Bounded re-fit (free center) ----
    geometry2 = EllipseGeometry(cx, cy, sma=sma0_list[0], eps=eps0, pa=pa0_rad)
    iso_step2 = None
    for sma0 in sma0_list:
        geometry2.sma = sma0
        iso_step2 = step2_bounded_fit(masked_image, geometry2, maxsma_free)
        if iso_step2 is not None and len(iso_step2) >= 3:
            break

    # 如果 step2 失败, 尝试增大椭率
    if iso_step2 is None or len(iso_step2) < 3:
        eps_retry = min(eps0 + 0.1, 0.9)
        geometry_retry = EllipseGeometry(cx, cy, sma=sma0_list[0], eps=eps_retry, pa=pa0_rad)
        for sma0 in sma0_list:
            geometry_retry.sma = sma0
            iso_step2 = step2_bounded_fit(masked_image, geometry_retry, maxsma_free)
            if iso_step2 is not None and len(iso_step2) >= 3:
                break

    # ---- 确定固定中心 ----
    fixed_center, center_ok = calculate_fixed_center(iso_step2)
    if fixed_center is None:
        fixed_center = (cx, cy)
        center_ok = False

    # ---- Step 3: Fixed-center fit ----
    geometry3 = EllipseGeometry(
        fixed_center[0], fixed_center[1],
        sma=sma0_list[0], eps=eps0, pa=pa0_rad,
    )
    iso_step3 = None
    for sma0 in sma0_list:
        geometry3.sma = sma0
        iso_step3 = step3_fixed_center_fit(masked_image, geometry3, maxsma_free)
        if iso_step3 is not None and len(iso_step3) >= 3:
            break

    n_step3 = len(iso_step3) if iso_step3 else 0
    step3_ok = iso_step3 is not None and n_step3 >= 3
    iso_final = iso_step3 if step3_ok else iso_step2

    # ---- 提取结果 ----
    df_s1 = extract_isophote_table(iso_step1, pixscale, band_or_survey)
    df_s2 = extract_isophote_table(iso_step2, pixscale, band_or_survey)
    df_s3 = extract_isophote_table(iso_final, pixscale, band_or_survey)

    info = {
        'bg_mean': float(bg_mean),
        'bg_std': float(bg_std),
        'fixed_center': fixed_center,
        'center_ok': center_ok,
        'step3_ok': step3_ok,
        'n_step1': len(df_s1),
        'n_step2': len(df_s2),
        'n_step3': len(df_s3),
    }
    return df_s1, df_s2, df_s3, info


# ============================================================
# 棒检测 (迁移自 run_bar_detection.py)
# ============================================================

def normalize_pa(pa_deg):
    """Normalize PA to [0, 180)."""
    return pa_deg % 180


def unwrap_pa(pa_deg):
    """Unwrap PA for continuous variation calculation."""
    return np.unwrap(np.deg2rad(pa_deg), period=np.pi) * 180 / np.pi


def detect_bar(df_isophote, criteria=None):
    """棒检测 (ellipse method), 基于 Step 3 固定中心等照度。

    返回 dict: bar_detected, classification, peak_idx, peak_sma_arcsec/pix,
    e_max, inner_idx/sma, outer_idx/sma, bar_pa_mean, bar_pa_var,
    bar_length_arcsec, failure_reason。
    """
    if criteria is None:
        criteria = DEFAULT_CRITERIA

    result = {
        'bar_detected': False,
        'classification': '',
        'peak_idx': None,
        'peak_sma_arcsec': np.nan,
        'peak_sma_pix': np.nan,
        'e_max': np.nan,
        'inner_idx': None,
        'inner_sma_arcsec': np.nan,
        'outer_idx': None,
        'outer_sma_arcsec': np.nan,
        'bar_pa_mean': np.nan,
        'bar_pa_var': np.nan,
        'bar_length_arcsec': np.nan,
        'failure_reason': None,
    }

    eps = df_isophote['eps'].values
    pa_deg = normalize_pa(df_isophote['pa_deg'].values)
    pa_unwrap = unwrap_pa(pa_deg)
    sma_arcsec = df_isophote['sma_arcsec'].values
    sma_pix = df_isophote['sma_pix'].values

    n = len(eps)
    if n < 5:
        result['failure_reason'] = 'too_few_isophotes'
        return result

    # ---- Peak detection ----
    e_max_threshold = criteria['e_max_threshold']
    peaks, properties = find_peaks(
        eps,
        height=e_max_threshold,
        prominence=BAR_PEAK_PROMINENCE,
        distance=BAR_PEAK_DISTANCE,
        width=BAR_PEAK_WIDTH,
    )

    if len(peaks) == 0:
        result['failure_reason'] = 'no_eps_peak'
        return result

    # ---- Rank peaks by PA stability ----
    pa_variances = []
    for pk in peaks:
        start = max(0, pk - BAR_PA_STABILITY_RANGE)
        region = pa_unwrap[start:pk + 1]
        if len(region) > 0:
            pa_variances.append(np.var(region))
        else:
            pa_variances.append(np.inf)

    ranked = np.argsort(pa_variances)
    peaks_to_test = peaks[ranked[:BAR_MAX_PEAKS_TEST]]

    # ---- Test each candidate peak ----
    for peak_idx in peaks_to_test:
        e_max = eps[peak_idx]

        # -- Inner boundary --
        inner_cands = np.where(eps[:peak_idx] < e_max_threshold)[0]
        if len(inner_cands) > 0:
            inner_idx = inner_cands[-1]
            classification = 'standard'
        else:
            inner_idx = max(0, peak_idx - 5)
            classification = 'weak_bulge'

        # -- Bar region minimum size --
        n_bar_pts = peak_idx - inner_idx + 1
        if n_bar_pts < 3:
            continue

        # -- PA stability --
        bar_region = pa_unwrap[inner_idx:peak_idx + 1]
        pa_variation = bar_region.max() - bar_region.min() if len(bar_region) > 0 else 0.0
        if pa_variation > criteria['pa_stability']:
            continue

        bar_pa_mean = np.mean(pa_deg[inner_idx:peak_idx + 1])

        # -- Outer boundary --
        outer_idx = None
        if peak_idx < n - 1:
            after_peak_eps = eps[peak_idx + 1:]
            after_peak_pa = pa_deg[peak_idx + 1:]
            after_peak_indices = np.arange(peak_idx + 1, n)

            eps_drop_mask = after_peak_eps <= e_max - criteria['e_drop']
            pa_change_mask = np.abs(normalize_pa(after_peak_pa) - bar_pa_mean) >= criteria['pa_change_outer']
            standard_cands = after_peak_indices[eps_drop_mask & pa_change_mask]

            if len(standard_cands) > 0:
                outer_idx = standard_cands[0]
                classification = 'standard'
            else:
                relaxed_cands = after_peak_indices[eps_drop_mask]
                if len(relaxed_cands) > 0:
                    outer_idx = relaxed_cands[0]
                    classification = 'aligned'

        # -- Record result --
        result['bar_detected'] = True
        result['classification'] = classification
        result['peak_idx'] = int(peak_idx)
        result['peak_sma_arcsec'] = sma_arcsec[peak_idx]
        result['peak_sma_pix'] = sma_pix[peak_idx]
        result['e_max'] = e_max
        result['inner_idx'] = int(inner_idx)
        result['inner_sma_arcsec'] = sma_arcsec[inner_idx]
        result['outer_idx'] = int(outer_idx) if outer_idx is not None else None
        result['outer_sma_arcsec'] = sma_arcsec[outer_idx] if outer_idx is not None else np.nan
        result['bar_pa_mean'] = bar_pa_mean
        result['bar_pa_var'] = pa_variation
        result['bar_length_arcsec'] = sma_arcsec[peak_idx]
        result['failure_reason'] = None
        return result

    result['failure_reason'] = 'all_peaks_failed_verification'
    return result


# ============================================================
# 偏侧性 (迁移自 run_lopsidedness.py)
# ============================================================

def compute_fourier_amplitudes(df):
    """计算 A1 = I1/I0, A2 = I2/I0, 及 m=1 相位 phi1 (deg)。"""
    df = df.copy()
    df['I1_I0'] = np.sqrt(df['a1']**2 + df['b1']**2) / df['intensity']
    df['I2_I0'] = np.sqrt(df['a2']**2 + df['b2']**2) / df['intensity']
    df['phi1_deg'] = np.degrees(np.arctan2(df['b1'], df['a1']))
    return df


def compute_radii_from_sbp(df):
    """从累积光度剖面计算 R50, R90 (arcsec)。"""
    sma = df['sma_arcsec'].values
    intensity = df['intensity'].values

    if len(sma) < 3:
        return np.nan, np.nan

    cum_flux = np.zeros(len(sma))
    cum_flux[0] = intensity[0] * np.pi * sma[0]**2
    for i in range(1, len(sma)):
        annulus_area = np.pi * (sma[i]**2 - sma[i-1]**2)
        avg_int = (intensity[i] + intensity[i-1]) / 2
        cum_flux[i] = cum_flux[i-1] + avg_int * annulus_area

    total_flux = cum_flux[-1]
    if total_flux <= 0:
        return np.nan, np.nan

    frac = cum_flux / total_flux
    r50 = np.interp(0.5, frac, sma)
    r90 = np.interp(0.9, frac, sma)
    return r50, r90


def analyze_dolfi_a1(df_s3, a1_threshold=A1_THRESHOLD):
    """偏侧方法 1: Dolfi et al. (2025) Fourier A1。

    计算 R50 < r < 1.4*R90 环带内 A1 均值; > a1_threshold 则判为偏侧。
    同时返回该环带的 m=1 相位均值 phi1 (deg, 环形平均)。
    """
    df_s3 = compute_fourier_amplitudes(df_s3)
    A1 = df_s3['I1_I0'].values
    phi1 = df_s3['phi1_deg'].values
    sma = df_s3['sma_arcsec'].values

    r50, r90 = compute_radii_from_sbp(df_s3)
    if np.isnan(r50) or np.isnan(r90):
        return {
            'lopsided_dolfi': False,
            'r50': np.nan,
            'r90': np.nan,
            'r_inner': np.nan,
            'r_outer': np.nan,
            'A1_mean': np.nan,
            'phi1_mean': np.nan,
            'n_isophotes': 0,
            'A1_max': float(np.max(A1)),
            'A1_overall_mean': float(np.mean(A1)),
        }

    r_inner = r50
    r_outer = 1.4 * r90
    mask = (sma >= r_inner) & (sma <= r_outer)
    n_iso = mask.sum()

    a1_mean_annulus = float(np.mean(A1[mask])) if n_iso > 0 else np.nan
    if n_iso > 0:
        # 环形平均: 用单位向量的 atan2, 避免 ±180° 跳变
        phi_rad = np.radians(phi1[mask])
        phi1_mean_annulus = float(np.degrees(np.arctan2(
            np.mean(np.sin(phi_rad)), np.mean(np.cos(phi_rad))
        )))
    else:
        phi1_mean_annulus = np.nan
    lopsided = (not np.isnan(a1_mean_annulus)) and (a1_mean_annulus > a1_threshold)

    return {
        'lopsided_dolfi': lopsided,
        'r50': float(r50),
        'r90': float(r90),
        'r_inner': float(r_inner),
        'r_outer': float(r_outer),
        'A1_mean': float(a1_mean_annulus) if not np.isnan(a1_mean_annulus) else np.nan,
        'phi1_mean': float(phi1_mean_annulus) if not np.isnan(phi1_mean_annulus) else np.nan,
        'n_isophotes': int(n_iso),
        'A1_max': float(np.max(A1)),
        'A1_overall_mean': float(np.mean(A1)),
    }


def analyze_center_offset_v2(df_s2, pixscl, survey, band_label):
    """偏侧方法 2: 中心偏移线性趋势 (Step 2 自由中心)。

    从 PSF FWHM 起, x/y 中心 vs SMA 线性回归; 两轴均 |r|>0.5 且 p<0.05,
    且归一化径向偏移变化 > CO_MIN_DR_NORM, 则判为偏侧。
    band_label 参数保留以兼容原签名 (函数内未使用)。
    """
    x0 = df_s2['x0_pix'].values
    y0 = df_s2['y0_pix'].values
    sma = df_s2['sma_arcsec'].values

    if len(sma) < 3:
        return {
            'lopsided_center': False,
            'max_offset_arcsec': np.nan,
            'max_offset_norm': np.nan,
            'dr_norm': np.nan,
            'psf_start_idx': None,
            'ref_x': np.nan,
            'ref_y': np.nan,
            'x_trend_r': np.nan, 'x_trend_p': np.nan,
            'y_trend_r': np.nan, 'y_trend_p': np.nan,
        }

    # Reference center: mean of outer 1/3
    n_outer = max(1, len(x0) // 3)
    ref_x = np.mean(x0[-n_outer:])
    ref_y = np.mean(y0[-n_outer:])

    dx = x0 - ref_x
    dy = y0 - ref_y
    dr = np.sqrt(dx**2 + dy**2)
    r_out = sma[-1]

    # PSF FWHM in arcsec (原硬编码)
    if survey == 'jwst':
        psf_fwhm_arcsec = 0.067  # F200W
    else:
        psf_fwhm_arcsec = 1.3    # SDSS r-band

    psf_start_idx = np.searchsorted(sma, psf_fwhm_arcsec)
    if psf_start_idx >= len(sma) - 2:
        psf_start_idx = 0  # fallback

    sma_seg = sma[psf_start_idx:]
    x_seg = dx[psf_start_idx:]
    y_seg = dy[psf_start_idx:]

    if len(sma_seg) < 3:
        return {
            'lopsided_center': False,
            'max_offset_arcsec': float(np.max(dr * pixscl)),
            'max_offset_norm': float(np.max(dr / r_out)),
            'dr_norm': np.nan,
            'psf_start_idx': int(psf_start_idx),
            'ref_x': float(ref_x), 'ref_y': float(ref_y),
            'x_trend_r': np.nan, 'x_trend_p': np.nan,
            'y_trend_r': np.nan, 'y_trend_p': np.nan,
        }

    res_x = linregress(sma_seg, x_seg)
    res_y = linregress(sma_seg, y_seg)

    x_trend_r = abs(res_x.rvalue)
    x_trend_p = res_x.pvalue
    y_trend_r = abs(res_y.rvalue)
    y_trend_p = res_y.pvalue

    dr_norm = dr / r_out
    dr_change = abs(dr_norm[-1] - dr_norm[psf_start_idx])

    CO_TREND_R_MIN = 0.5
    CO_TREND_P_MAX = 0.05

    lopsided = (
        x_trend_r > CO_TREND_R_MIN and x_trend_p < CO_TREND_P_MAX and
        y_trend_r > CO_TREND_R_MIN and y_trend_p < CO_TREND_P_MAX and
        dr_change > CO_MIN_DR_NORM
    )

    return {
        'lopsided_center': lopsided,
        'max_offset_arcsec': float(np.max(dr * pixscl)),
        'max_offset_norm': float(np.max(dr_norm)),
        'dr_norm': float(dr_change),
        'psf_start_idx': int(psf_start_idx),
        'ref_x': float(ref_x),
        'ref_y': float(ref_y),
        'x_trend_r': float(x_trend_r), 'x_trend_p': float(x_trend_p),
        'y_trend_r': float(y_trend_r), 'y_trend_p': float(y_trend_p),
    }
