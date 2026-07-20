"""
RL Reward：对齐 pipeline VLM reward (calculate_reward_model_with_param) 的 rule-based 替代。

设计哲学：
  - 不依赖与 GT 的参数距离（简并性：多组参数都能给好拟合）
  - 评估"这步操作的结果好不好"：参数边界 + chi2 改善 + BIC 改善 + 残差噪声质量

对齐 calculate_reward_model_with_param 的判断维度：
  1. 参数边界 → param_plausible
  2. chi2 gain → chisq/chisq_nu trend
  3. BIC gain  → metric_consistent (BIC 部分)
  4. 噪声质量 → residual_improved

组合方式：
  - 边界违规 → R=0
  - chi2/nu 明显恶化 → R=0（即使 BIC 改善）
  - 正常 → R = w_chi2 * r_chi2 + w_bic * r_bic + w_noise * r_noise

噪声质量评估借鉴同事 batch_eval_residual_quality_hierarchical.py：
  - robust_std_mad(residual/sigma) → 理想值 ~1
  - PSD 频段功率比 → 理想值 ~1（白噪声平坦谱）

权重通过 validate_reward_alignment.py 在 GT 轨迹 val 集上校准。
"""

import math
import numpy as np
from typing import Optional, Dict, Any, Tuple


# ============================================================
# 参数边界检查
# ============================================================

PARAM_BOUNDS = {
    "n": (0.1, 8.0),
    "re": (0.2, 300.0),   # 加上限：Re > 300px 几乎必是参数跑飞
    "q": (0.05, 1.0),
    "mag": (0.0, None),
}


def check_param_bounds(spec: dict) -> Tuple[bool, list]:
    """
    检查 spec 中所有成分的参数是否在物理合法范围内。

    Returns:
        (all_ok, violations): violations 是字符串列表描述违规参数
    """
    violations = []
    for i, comp in enumerate(spec.get("components", [])):
        model = (comp.get("model") or "").lower()
        for param, (lo, hi) in PARAM_BOUNDS.items():
            val = comp.get(param)
            if val is None:
                continue
            if model == "psf" and param in ("re", "n", "q"):
                continue
            try:
                val = float(val)
            except (ValueError, TypeError):
                violations.append(f"comp[{i}].{param}: non-numeric '{val}'")
                continue
            if lo is not None and val < lo:
                violations.append(f"comp[{i}].{param}={val:.4f} < {lo}")
            if hi is not None and val > hi:
                violations.append(f"comp[{i}].{param}={val:.4f} > {hi}")

    return len(violations) == 0, violations


# ============================================================
# chi2 改善
# ============================================================

def compute_chi2_gain(old_metrics: dict, new_metrics: dict) -> float:
    """
    计算 chi2 对数增益（沿用 pipeline calculate_reward_rule）。
    改善为正值，恶化为负值。

    r_chi2 = log10(old_chi2_nu / new_chi2_nu)
    """
    old_chi2 = old_metrics.get("chi2_nu", 999.0)
    new_chi2 = new_metrics.get("chi2_nu", 999.0)

    if old_chi2 > 0 and new_chi2 > 0:
        r_chi2 = math.log10(old_chi2 / new_chi2)
    else:
        r_chi2 = 0.0

    if new_chi2 >= 9999.0:
        r_chi2 = -10.0

    return r_chi2


def compute_bic_gain(old_metrics: dict, new_metrics: dict) -> float:
    """BIC 变化（对数归一化）：降低为正（更好），升高为负。

    原始 BIC 差值动辄上千~上万，与 r_chi2（±0.1 量级）不可比。
    用 sign(delta) * log10(1 + |delta|) 压缩到 ±4 范围。
    """
    old_bic = old_metrics.get("bic")
    new_bic = new_metrics.get("bic")

    if old_bic is None or new_bic is None:
        return 0.0

    delta = old_bic - new_bic  # 正 = BIC 降低（改善）
    if abs(delta) < 1e-6:
        return 0.0
    sign = 1.0 if delta > 0 else -1.0
    return sign * math.log10(1.0 + abs(delta))


# ============================================================
# 噪声质量（借鉴同事 batch_eval_residual_quality_hierarchical.py）
# ============================================================

def robust_std_mad(x: np.ndarray) -> float:
    """MAD-based robust standard deviation。对高斯噪声，1.4826 × MAD ≈ std。"""
    x = np.asarray(x, dtype=float)
    x = x[np.isfinite(x)]
    if x.size == 0:
        return float("nan")
    med = np.nanmedian(x)
    mad = np.nanmedian(np.abs(x - med))
    return float(1.4826 * mad)


def psd_band_metrics(image_2d: np.ndarray, valid_mask: np.ndarray) -> dict:
    """
    计算低/中/高频 PSD 功率比。

    白噪声的 PSD 是平坦的，low_to_high ≈ mid_to_high ≈ 1。
    残差中有大尺度结构 → low_to_high > 1。
    """
    img = np.array(image_2d, dtype=float)
    valid = valid_mask & np.isfinite(img)

    if np.sum(valid) == 0:
        return {
            "psd_low_power": None, "psd_mid_power": None, "psd_high_power": None,
            "psd_low_to_high": None, "psd_mid_to_high": None,
        }

    fill_value = np.nanmedian(img[valid])
    img[~valid] = fill_value
    img = img - np.nanmedian(img[valid])

    ny, nx = img.shape
    wy = np.hanning(ny)
    wx = np.hanning(nx)
    window = np.outer(wy, wx)

    fft = np.fft.fftshift(np.fft.fft2(img * window))
    psd2d = np.abs(fft) ** 2

    yy, xx = np.indices(img.shape)
    cy, cx = ny // 2, nx // 2
    rr = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2)
    rmax = rr.max()

    low = (rr > 0) & (rr <= 0.15 * rmax)
    mid = (rr > 0.15 * rmax) & (rr <= 0.40 * rmax)
    high = (rr > 0.40 * rmax) & (rr <= 0.80 * rmax)

    low_power = float(np.nanmean(psd2d[low]))
    mid_power = float(np.nanmean(psd2d[mid]))
    high_power = float(np.nanmean(psd2d[high]))

    eps = 1e-12
    return {
        "psd_low_power": low_power,
        "psd_mid_power": mid_power,
        "psd_high_power": high_power,
        "psd_low_to_high": float(low_power / max(high_power, eps)),
        "psd_mid_to_high": float(mid_power / max(high_power, eps)),
    }


# 噪声质量阈值（沿用同事默认值，后续可调）
NOISE_THRESHOLDS = {
    "std_low": 0.6,
    "std_high": 1.5,
    "std_soft_low": 0.80,
    "std_soft_high": 1.20,
    "psd_low_tol": 2.0,
    "psd_mid_tol": 1.5,
}


def compute_noise_score(
    residual: np.ndarray,
    sigma: np.ndarray,
    mask: np.ndarray,
    thresholds: dict = None,
) -> Dict[str, Any]:
    """
    计算残差噪声质量得分。

    Args:
        residual: 2D 残差图（data - model）
        sigma: 2D 噪声 sigma 图
        mask: 2D 掩膜（0=有效，非0=掩掉）

    Returns:
        {
            "std_ratio": float,  # robust_std(res/sigma)，理想 ~1
            "std_score": float,  # 连续得分 0~1
            "psd_low_to_high": float,
            "psd_mid_to_high": float,
            "psd_score": float,  # 连续得分 0~1
            "noise_score": float,  # 综合得分 0~1
            "std_hard_pass": bool,
            "psd_pass": bool,
        }
    """
    thr = {**NOISE_THRESHOLDS, **(thresholds or {})}

    valid = (
        np.isfinite(residual)
        & np.isfinite(sigma)
        & (sigma > 0)
        & (mask == 0)
    )

    result = {
        "std_ratio": float("nan"),
        "std_score": 0.0,
        "psd_low_to_high": None,
        "psd_mid_to_high": None,
        "psd_score": 0.0,
        "noise_score": 0.0,
        "std_hard_pass": False,
        "psd_pass": False,
        "n_valid_pixels": int(np.sum(valid)),
    }

    if np.sum(valid) < 100:
        return result

    z = residual / sigma
    z_valid = z[valid]
    std_ratio = robust_std_mad(z_valid)
    result["std_ratio"] = std_ratio

    std_hard_pass = thr["std_low"] <= std_ratio <= thr["std_high"]
    result["std_hard_pass"] = std_hard_pass

    if std_hard_pass:
        log_dev = abs(math.log(max(std_ratio, 1e-12)))
        max_log_dev = max(
            abs(math.log(thr["std_soft_low"])),
            abs(math.log(thr["std_soft_high"])),
        )
        result["std_score"] = max(0.0, 1.0 - log_dev / max_log_dev)
    else:
        result["std_score"] = 0.0

    psd = psd_band_metrics(z, valid)
    result["psd_low_to_high"] = psd["psd_low_to_high"]
    result["psd_mid_to_high"] = psd["psd_mid_to_high"]

    low_ok = psd["psd_low_to_high"] is not None and psd["psd_low_to_high"] <= thr["psd_low_tol"]
    mid_ok = psd["psd_mid_to_high"] is not None and psd["psd_mid_to_high"] <= thr["psd_mid_tol"]
    result["psd_pass"] = low_ok and mid_ok

    if psd["psd_low_to_high"] is not None and psd["psd_mid_to_high"] is not None:
        low_excess = max(0.0, math.log10(max(psd["psd_low_to_high"], 1e-12)))
        mid_excess = max(0.0, math.log10(max(psd["psd_mid_to_high"], 1e-12)))
        max_low = math.log10(max(thr["psd_low_tol"], 1e-12))
        max_mid = math.log10(max(thr["psd_mid_tol"], 1e-12))
        psd_metric = low_excess + 0.5 * mid_excess
        max_psd = max_low + 0.5 * max_mid
        result["psd_score"] = max(0.0, 1.0 - psd_metric / max(max_psd, 1e-12))
    else:
        result["psd_score"] = 0.0

    result["noise_score"] = 0.5 * result["std_score"] + 0.5 * result["psd_score"]

    return result


def load_noise_inputs(residual_fits_path: str, sigma_fits_path: str, mask_fits_path: str,
                      residual_ext: int = 3):
    """
    从 FITS 文件加载残差、sigma、mask。

    Args:
        residual_fits_path: GALFIT 输出 galfit.fit 路径（HDU[3] = residual）
        sigma_fits_path: sigma.fit 路径
        mask_fits_path: mask.fit 路径
        residual_ext: 残差在哪个 HDU extension
    """
    from astropy.io import fits

    with fits.open(residual_fits_path) as hdul:
        residual = hdul[residual_ext].data.astype(float)

    sigma = fits.getdata(sigma_fits_path).astype(float)
    mask = fits.getdata(mask_fits_path)

    return residual, sigma, mask


# ============================================================
# 综合 RL Reward
# ============================================================

# 权重：跑 validate_reward_alignment.py 在 val 集上校准
W_CHI2 = 10.0
W_BIC = 2.0   # 降低：BIC 改善不可靠（参数跑飞也能降 BIC），给低权重
W_NOISE = 5.0

# chi2/nu 明显恶化阈值：对齐 calculate_reward_model_with_param 中
# "If chisq/nu clearly worsens, be conservative" 的逻辑
CHI2_VETO_THRESHOLD = -0.1  # log10(old/new) < -0.1 即 chi2_nu 恶化 >26%


def compute_rl_reward(
    old_metrics: dict,
    new_metrics: dict,
    action_spec: dict,
    residual: Optional[np.ndarray] = None,
    sigma: Optional[np.ndarray] = None,
    mask: Optional[np.ndarray] = None,
    noise_thresholds: dict = None,
) -> Dict[str, Any]:
    """
    RL reward，对齐 calculate_reward_model_with_param 的判断逻辑。

    判断维度（与 VLM reward 一一对应）：
      1. 参数边界检查 → 对应 VLM 的 param_plausible
      2. chi2 gain     → 对应 VLM 的 chisq/chisq_nu trend
      3. BIC gain      → 对应 VLM 的 metric_consistent (BIC 部分)
      4. 噪声质量      → 对应 VLM 的 residual_improved

    组合方式（对齐 calculate_reward_model_with_param）：
      - 边界违规 → R=0
      - chi2/nu 明显恶化 → R=0（即使 BIC 改善，对应 VLM Part 4 Rule 3）
      - 正常情况 → R = w_chi2 * r_chi2 + w_bic * r_bic + w_noise * r_noise

    Args:
        old_metrics: 父节点 metrics (chi2_nu, bic, ...)
        new_metrics: 当前节点 metrics
        action_spec: 模型输出的 action spec dict（含 components）
        residual: 2D 残差图（可选，有则计算噪声质量）
        sigma: 2D sigma 图（可选）
        mask: 2D 掩膜（可选）
    """
    bounds_ok, violations = check_param_bounds(action_spec)

    result = {
        "reward": 0.0,
        "bounds_ok": bounds_ok,
        "bounds_violations": violations,
        "r_chi2": 0.0,
        "r_bic": 0.0,
        "chi2_vetoed": False,
        "noise_detail": None,
        "r_noise": 0.0,
    }

    if not bounds_ok:
        return result

    r_chi2 = compute_chi2_gain(old_metrics, new_metrics)
    r_bic = compute_bic_gain(old_metrics, new_metrics)
    result["r_chi2"] = r_chi2
    result["r_bic"] = r_bic

    # chi2/nu 明显恶化 → 一票否决（对齐 VLM Part 4 Rule 3:
    # "If chisq/nu clearly worsens, even if BIC decreases, do not accept"）
    if r_chi2 < CHI2_VETO_THRESHOLD:
        result["chi2_vetoed"] = True
        return result

    # BIC-chi2 联动门控：chi2 改善不显著时不奖励 BIC 改善
    # 对齐 VLM 逻辑：metric-driven improvement 需要 chi2 有可感知的改善
    # r_chi2 > 0.003 ≈ chi2_nu 改善 > 0.7%，防止噪声级改善触发 BIC 奖励
    BIC_CHI2_GATE = 0.003
    BIC_CAP = 2.0  # r_bic 上限，防止极端 BIC 改善独占 reward
    if r_chi2 >= BIC_CHI2_GATE:
        effective_r_bic = min(r_bic, BIC_CAP)
    else:
        effective_r_bic = min(r_bic, 0.0)  # chi2 没显著改善时只允许 BIC 负贡献
    result["effective_r_bic"] = effective_r_bic

    r_noise = 0.0
    if residual is not None and sigma is not None and mask is not None:
        noise_detail = compute_noise_score(residual, sigma, mask, noise_thresholds)
        result["noise_detail"] = noise_detail
        r_noise = noise_detail["noise_score"]
    result["r_noise"] = r_noise

    reward = W_CHI2 * r_chi2 + W_BIC * effective_r_bic + W_NOISE * r_noise
    result["reward"] = reward

    return result
