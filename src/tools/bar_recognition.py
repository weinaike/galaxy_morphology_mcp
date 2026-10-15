"""bar_recognition — 神经-符号棒结构识别 MCP 工具。

在工作流中于**首轮星系拟合之后**调用：基于首轮拟合产物（GALFIT 输出 FITS，
含原图/模型/残差 HDU；也兼容 GalfitS 每波段 *_result.fits）执行与
bar_recognize 仓库一致的三层判决，输出 Tier 1/2/3：

  - Tier 1 = contains_bar True   → 80% 概率存在 bar，附 bar_pa_deg 与投影轴比 raw_ab
  - Tier 2 = "Uncertain"         → 不确定
  - Tier 3 = contains_bar False  → 80% 概率无 bar

判决以 `note_section`（可直接追加的 markdown 小节）随结果返回；写入
working_note.md 的动作由 workflow 提示驱动 Agent 完成（本工具不写 note）。
该工具取代阶段一的原图分析职责。

实现纪律（与 bar_lopsidedness_detection 相同）：
  - 判决引擎与测光：bar_recognition_core.py（参考库逐字移植，零逻辑改动）
  - 视图渲染/zoom 几何：移植 bar_recognize/render/render_lib.py 的
    _zoom_context / zoom_geometry / snap_to_peak / companion_mask /
    render_global_original / render_center_zoom / render_residual_zoom，
    唯一适配是数据源从「galaxy_dir 里的 feedme + 归档 galfit.fits」换成
    「调用方直接给的拟合输出 FITS + 同目录参数文件」。
"""

import os
import glob
import json
import datetime
from typing import Annotated, Any, Optional

import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
from astropy.io import fits
from astropy.stats import sigma_clipped_stats
from scipy import ndimage
from scipy.ndimage import gaussian_filter
from PIL import Image

from . import prompt
from .bar_recognition_core import (
    evaluate_galaxy_bar,
    extract_photometrics_from_arrays,
    extract_guarded_features,
    extract_json_from_text,
)
from .analyze_image import create_vlm_client, encode_image_to_base64, call_vlm_api
from .run_galfit import observed_reff
from .parse_feedme import parse_feedme, parse_components
from .render_original import render_asinh_panel, effective_re

# ── zoom/渲染约定（值复制自 bar_recognize/render/render_lib.py:31-51）──
ZOOM_HALF_MIN_PX = 20       # 放大框半宽下限：全宽至少 40×40 px，防止 Re 过小时框退化
ZOOM_RE_FACTOR = 2.5        # 半宽 = 2.5×Re，即全宽 5×Re
CENTER_CLUSTER_PX = 3.0     # 距场心同一目标簇的容差：同心多成分(盘+核+棒)归为一簇
RESID_SIGMA_RANGE = 10      # 残差色标 ±10σ
SNAP_RADIUS_PX = 8          # 中心峰吸附搜索窗半径 [pix]
SNAP_MIN_SNR = 3.0          # 峰需超过 median + 3σ 才吸附
COMPANION_THRESH_SIGMA = 2.0
COMPANION_MIN_SNR = 5.0
COMPANION_DILATE_PX = 8

VLM_MAX_ATTEMPTS = 3        # VLM 特征提取尝试次数（解析失败重试，参考 run_eval.py）


# ════════════════════════════════════════════════════════════════════════════
# zoom 几何（render_lib.py 逐字移植）
# ════════════════════════════════════════════════════════════════════════════

def snap_to_peak(sci: np.ndarray, mask: np.ndarray, cx: float, cy: float,
                 radius: float = SNAP_RADIUS_PX, min_snr: float = SNAP_MIN_SNR):
    """把中心吸附到平滑去天光图的真实峰（数据驱动定心）。"""
    good = np.isfinite(sci) & (mask == 0)
    if int(good.sum()) < 50:
        return cx, cy, False
    median, std = sigma_clipped_stats(sci[good])[1:]
    smoothed = gaussian_filter(np.where(good, sci - median, 0.0), sigma=3)
    ny, nx = sci.shape
    x0 = max(0, int(cx - radius)); x1 = min(nx - 1, int(cx + radius))
    y0 = max(0, int(cy - radius)); y1 = min(ny - 1, int(cy + radius))
    if x1 <= x0 or y1 <= y0:
        return cx, cy, False
    win = np.where(good[y0:y1 + 1, x0:x1 + 1], smoothed[y0:y1 + 1, x0:x1 + 1], -np.inf)
    k = np.unravel_index(np.argmax(win), win.shape)
    if win[k] < min_snr * std or not np.isfinite(win[k]):
        return cx, cy, False
    return x0 + k[1], y0 + k[0], True


def companion_mask(sci: np.ndarray, mask: np.ndarray, cx: float, cy: float,
                   thresh_sigma: float = COMPANION_THRESH_SIGMA,
                   min_snr: float = COMPANION_MIN_SNR,
                   dilate_px: int = COMPANION_DILATE_PX):
    """生成非目标源（伴星系/前景星）掩膜：凡与目标星系不连通的亮源全部遮蔽。"""
    good = np.isfinite(sci) & (mask == 0)
    out = np.zeros(sci.shape, dtype=bool)
    if int(good.sum()) < 50:
        return out, 0
    try:
        median, std = sigma_clipped_stats(sci[good])[1:]
    except Exception:
        return out, 0
    if not np.isfinite(std) or std <= 0:
        return out, 0

    smoothed = gaussian_filter(np.where(good, sci - median, 0.0), sigma=3)
    det = good & (smoothed > thresh_sigma * std)
    if not det.any():
        return out, 0

    labels, nlab = ndimage.label(det)
    target = labels[int(round(cy)), int(round(cx))]
    if target == 0 and nlab > 0:
        yy, xx = np.indices(sci.shape)
        objs = ndimage.find_objects(labels)
        dists = [np.hypot((sl[1].start + sl[1].stop - 1) / 2 - cx,
                          (sl[0].start + sl[0].stop - 1) / 2 - cy)
                 for sl in objs if sl is not None]
        target = int(np.argmin(dists)) + 1

    n_src = 0
    for lab in range(1, nlab + 1):
        if lab == target:
            continue
        sel = labels == lab
        peak = smoothed[sel].max()
        if peak < min_snr * std:
            continue  # 噪声斑，不遮
        out |= sel
        n_src += 1
    if n_src and dilate_px > 0:
        out = ndimage.binary_dilation(out, iterations=dilate_px)
    return out, n_src


def zoom_geometry(components, fit_region, sci, mask):
    """计算中心 ZOOM 框几何（约定与 render_lib.zoom_geometry 一致）。

    选源：目标星系 = 距拟合区域几何中心最近者；同心多成分归为同一目标簇，
    取最亮为代表。定心：簇中心 ±SNAP_RADIUS 小窗内吸附到平滑图真实峰。
    尺寸：半宽 = max(2.5×R_e,obs, 下限)，上限拟合区域短边 1/4。

    Returns:
        (cx, cy, half, re_obs, re_fit)：中心（1-indexed 全图坐标）、半宽、
        实测/拟合半光半径。
    """
    if fit_region is not None:
        xmin, xmax, ymin, ymax = fit_region
    else:
        ny, nx = sci.shape
        xmin, xmax, ymin, ymax = 1, nx, 1, ny

    ctr_x, ctr_y = (xmin + xmax) / 2, (ymin + ymax) / 2
    cands = [c for c in components or [] if c.get("type") != "sky"]
    if cands:
        d2 = lambda c: (c["x"] - ctr_x) ** 2 + (c["y"] - ctr_y) ** 2
        dmin = min(d2(c) for c in cands)
        central = [c for c in cands if d2(c) <= dmin + CENTER_CLUSTER_PX ** 2]
        main = min(central, key=lambda c: c.get("mag", 99))
        cx, cy = main["x"], main["y"]
        re_fit = effective_re(main)
    else:
        cx, cy = ctr_x, ctr_y
        re_fit = 0.0

    # 数据驱动定心：吸附到目标星系真实核球峰（仅小窗内微调，迭代至不动点）
    px, py = cx - xmin, cy - ymin
    for _ in range(5):
        nx_c, ny_c, _ = snap_to_peak(sci, mask, px, py)
        if np.hypot(nx_c - px, ny_c - py) < 0.6:
            px, py = nx_c, ny_c
            break
        px, py = nx_c, ny_c
    cx, cy = px + xmin, py + ymin

    re_obs = observed_reff(sci, mask, cx - xmin, cy - ymin) or re_fit
    half = max(ZOOM_RE_FACTOR * re_obs, ZOOM_HALF_MIN_PX)
    half = min(half, (xmax - xmin) / 4, (ymax - ymin) / 4)
    return cx, cy, half, re_obs, re_fit


# ════════════════════════════════════════════════════════════════════════════
# 数据加载（接口适配层：本目录新增）
# ════════════════════════════════════════════════════════════════════════════

def _load_fit_fits(fits_file: str) -> dict[str, Any]:
    """读取拟合输出 FITS 的 原图/模型/残差 数组。

    支持两种布局：
      1. GALFIT 输出（OBJECT 头启发式，同 run_galfit.create_comparison_png）：
         HDU '...[...]'=original / 'model' / 'residual map'，无 mask HDU
      2. GalfitS 每波段 *_result.fits（5 HDU）：
         [0]=resid [1]=mask [2]=sigma [3]=model [4]=original

    Returns:
        dict: sci/model/resid (float64)、mask_hdul (bool 或 None)、format
    Raises:
        ValueError: 找不到必要的 HDU
    """
    with fits.open(fits_file) as hdul:
        original = model = residual = None
        for hdu in hdul:
            object_type = hdu.header.get("OBJECT", "")
            if object_type.find("[") != -1 and original is None:
                original = hdu.data
            elif object_type.find("model") != -1:
                model = hdu.data
            elif object_type.find("residual") != -1:
                residual = hdu.data
        if original is not None:
            return {
                "sci": np.asarray(original, dtype=np.float64),
                "model": np.asarray(model, dtype=np.float64) if model is not None else None,
                "resid": np.asarray(residual, dtype=np.float64) if residual is not None else None,
                "mask_hdul": None,
                "format": "galfit",
            }

        # GalfitS 5-HDU 布局
        if len(hdul) == 5 and hdul[4].data is not None:
            mask_arr = hdul[1].data
            return {
                "sci": np.asarray(hdul[4].data, dtype=np.float64),
                "model": np.asarray(hdul[3].data, dtype=np.float64) if hdul[3].data is not None else None,
                "resid": np.asarray(hdul[0].data, dtype=np.float64) if hdul[0].data is not None else None,
                "mask_hdul": np.asarray(mask_arr) > 0 if mask_arr is not None else None,
                "format": "galfits_band",
            }

    raise ValueError(
        f"Could not locate original/model/residual HDUs in {fits_file} "
        "(neither GALFIT OBJECT headers nor a 5-HDU GalfitS layout)"
    )


def _find_param_file(fits_dir: str, param_file: str) -> Optional[str]:
    """定位首轮参数文件（galfit.NN 拟合值优先于 feedme 初始值；两者 F)/H) 相同）。

    顺序：显式 param_file（存在才用，否则告警并转入自动发现）→ FITS 同目录
    galfit.NN（编号最大者）→ 同目录 *.feedme → 向上最多 4 层父目录（覆盖
    archives/ 归档目录之上的星系主目录）重复同两步。
    全部落空返回 None —— 调用方按降级路径继续执行并在结果中给出警告。
    """
    if param_file:
        param_file = os.path.abspath(param_file)
        if os.path.exists(param_file):
            return param_file
        print(f"[bar_recognition] explicit param_file not found: {param_file}; "
              f"falling back to auto-discovery")

    def _probe(directory: str) -> Optional[str]:
        galfit_files = glob.glob(os.path.join(directory, "galfit.[0-9]*"))
        if galfit_files:
            return max(galfit_files, key=lambda f: int(f.rsplit(".", 1)[-1]))
        feedmes = sorted(glob.glob(os.path.join(directory, "*.feedme")))
        return feedmes[0] if feedmes else None

    p = fits_dir
    for _ in range(4):
        found = _probe(p)
        if found:
            return found
        parent = os.path.dirname(p)
        if parent == p:
            break
        p = parent
    return None


def _locate_mask_file(mask_path: str, fits_dir: str) -> Optional[str]:
    """定位掩膜文件。

    parse_feedme 把相对路径按参数文件所在目录解析；归档副本随 archives/
    移动后相对路径会失效，此时向上层目录（星系主目录）按文件名回找。
    """
    if not mask_path:
        return None
    if os.path.exists(mask_path):
        return mask_path
    base = os.path.basename(mask_path)
    p = fits_dir
    for _ in range(4):
        cand = os.path.join(p, base)
        if os.path.exists(cand):
            return cand
        parent = os.path.dirname(p)
        if parent == p:
            break
        p = parent
    return None


def _build_zoom_context(fits_file: str, data: dict[str, Any], param_file: Optional[str],
                        re_obs_override: Optional[float]) -> dict[str, Any]:
    """按 render_lib._zoom_context 的流程完成定心/裁剪/掩蔽（数据源为内存数组）。

    GALFIT 输出 FITS 的 sci 已裁剪到 H) 拟合区域；feedme 的 H) 给出全图坐标
    框、成分 x,y 为全图坐标 —— 与参考 _zoom_context 的坐标约定一致。
    """
    sci = data["sci"]
    resid = data["resid"]

    components: list[dict[str, Any]] = []
    mask = (~np.isfinite(sci)).astype(int) if data["mask_hdul"] is None \
        else data["mask_hdul"].astype(int)
    fit_region = None
    region = None

    if param_file:
        params = parse_feedme(param_file)
        fit_region = params.get("fit_region")
        components = parse_components(param_file)
        mask_full_path = _locate_mask_file(params.get("mask", ""), os.path.dirname(os.path.abspath(fits_file)))
        if mask_full_path:
            try:
                mask_full = fits.getdata(mask_full_path)
                if fit_region is not None and mask_full.shape != sci.shape:
                    xmin, xmax, ymin, ymax = fit_region
                    mask_crop = mask_full[ymin - 1:ymax, xmin - 1:xmax]
                else:
                    mask_crop = mask_full
                if mask_crop.shape == sci.shape:
                    mask = (np.asarray(mask_crop, dtype=float) > 0).astype(int) | mask
            except Exception as e:
                print(f"[bar_recognition] mask load failed ({mask_full_path}): {e}")

    if fit_region is not None:
        region = list(fit_region)
        xmin, xmax, ymin, ymax = fit_region
    else:
        xmin, xmax, ymin, ymax = 1, sci.shape[1], 1, sci.shape[0]

    # 背景 σ（伴星系遮蔽前、掩膜透传），残差归一化与测光共用
    good_all = np.isfinite(sci) & (mask == 0)
    bg_std = float(sigma_clipped_stats(sci[good_all])[2]) \
        if int(good_all.sum()) > 50 else 1.0

    cx, cy, half, re_obs, re_fit = zoom_geometry(components, fit_region, sci, mask)
    if re_obs_override is not None and re_obs_override > 0:
        re_obs = float(re_obs_override)

    # 先遮非目标源：伴星系/前景星全掩蔽，目标星系成为画面唯一主体
    companion, n_comp = companion_mask(sci, mask, cx - xmin, cy - ymin)
    mask = (mask > 0) | companion

    # Clip zoom box to the fit region (1-indexed inclusive)
    zx0, zx1 = max(xmin, cx - half), min(xmax, cx + half)
    zy0, zy1 = max(ymin, cy - half), min(ymax, cy + half)
    ix0, ix1 = int(round(zx0 - xmin)), int(round(zx1 - xmin))
    iy0, iy1 = int(round(zy0 - ymin)), int(round(zy1 - ymin))
    zoom = sci[iy0:iy1 + 1, ix0:ix1 + 1]
    zoom_mask = mask[iy0:iy1 + 1, ix0:ix1 + 1]
    zoom_region = [zx0, zx1, zy0, zy1]
    resid_zoom = resid[iy0:iy1 + 1, ix0:ix1 + 1] if resid is not None else None

    return {
        "sci": sci, "mask": mask, "region": region,
        "zoom": zoom, "zoom_mask": zoom_mask, "zoom_region": zoom_region,
        "resid_zoom": resid_zoom, "bg_std": bg_std,
        "center": (cx, cy),
        "half_width_px": half, "re_obs": re_obs, "re_fit": re_fit,
        "companions_masked": n_comp,
        "components": components,
    }


# ════════════════════════════════════════════════════════════════════════════
# 三视图渲染（render_lib.py render_*_zoom 逐字移植，ctx 为内存字典）
# ════════════════════════════════════════════════════════════════════════════

def _render_global_view(output_path: str, ctx: dict[str, Any]) -> None:
    """全景原图（整体视场 + 品红 zoom 框），384×384。"""
    sci, mask, region = ctx["sci"], ctx["mask"], ctx["region"]
    fig, ax = plt.subplots(figsize=(3.84, 3.84))
    render_asinh_panel(ax, sci, mask, region=region, vmax_percentile=99.99)

    zx0, zx1, zy0, zy1 = ctx["zoom_region"]
    rect = Rectangle((zx0 - 0.5, zy0 - 0.5), zx1 - zx0 + 1, zy1 - zy0 + 1,
                     linewidth=1.2, edgecolor="magenta", facecolor="none", linestyle="--")
    ax.add_patch(rect)

    ax.set_title("Global View (entire disk & inclination)\nMagenta dashed box = center zoom region",
                 fontsize=6, pad=3)
    fig.tight_layout()
    fig.savefig(output_path, dpi=100)
    plt.close(fig)


def _render_core_zoom(output_path: str, ctx: dict[str, Any]) -> None:
    """原图中心 ZOOM（asinh 99.99 + 5σ lime / vmax red 等照度线），384×384。"""
    zoom, zoom_mask = ctx["zoom"], ctx["zoom_mask"]
    cx, cy = ctx["center"]
    half, re_obs, re_fit = ctx["half_width_px"], ctx["re_obs"], ctx["re_fit"]
    n_comp = ctx["companions_masked"]

    fig, ax = plt.subplots(figsize=(3.84, 3.84))
    info = render_asinh_panel(ax, zoom, zoom_mask, region=ctx["zoom_region"],
                              vmax_percentile=99.99)
    re_obs_txt = f"{re_obs:.1f}" if re_obs else "n/a"
    re_fit_txt = f"{re_fit:.1f}" if re_fit else "n/a"
    masked_txt = f"; companions masked: {n_comp}" if n_comp else ""
    ax.set_title(
        f"vmax=99.99th pctl (center zoom, box width = "
        f"{2 * ZOOM_RE_FACTOR:.0f}$\\times R_e^{{obs}}$, capped at 1/2 field)\n"
        f"center=({cx:.1f}, {cy:.1f}); $R_e^{{obs}}$={re_obs_txt} px; "
        f"$R_e^{{fit}}$={re_fit_txt} px\n"
        f"asinh_a={info['asinh_a']:.4f}; vmin={info['vmin_sigma']:.1f}$\\sigma$"
        f"\nIsophotes: 5.0$\\sigma$ [lime]; vmax[red]; Shaded: Masked{masked_txt}",
        fontsize=6, pad=3)
    fig.tight_layout()
    fig.savefig(output_path, dpi=100)
    plt.close(fig)


def _render_residual_zoom(output_path: str, ctx: dict[str, Any]) -> None:
    """首轮模型残差 ZOOM（seismic ±10σ / bg_std，掩膜涂黑），384×384。"""
    resid, zoom_mask = ctx["resid_zoom"], ctx["zoom_mask"]
    xmin, xmax, ymin, ymax = ctx["zoom_region"]
    ext = [xmin - 0.5, xmax + 0.5, ymin - 0.5, ymax + 0.5]

    has_resid = resid is not None
    rn = (np.clip(np.nan_to_num(resid, nan=0.0) / ctx["bg_std"],
                  -RESID_SIGMA_RANGE, RESID_SIGMA_RANGE)
          if has_resid else np.zeros_like(zoom_mask, dtype=float))

    fig, ax = plt.subplots(figsize=(3.84, 3.84))
    ax.imshow(rn, cmap='seismic', origin='lower', extent=ext,
              vmin=-RESID_SIGMA_RANGE, vmax=RESID_SIGMA_RANGE)
    mask_overlay = np.zeros((*zoom_mask.shape, 4))
    mask_overlay[zoom_mask > 0] = [0, 0, 0, 1.0]
    ax.imshow(mask_overlay, origin='lower', extent=ext)
    arch_note = ("data−model)/σ; red=excess, blue=deficit" if has_resid
                 else "NO RESIDUAL AVAILABLE")
    ax.set_title(
        f"First-round fit residual zoom ({arch_note}, seismic ±{RESID_SIGMA_RANGE}σ)\n"
        f"same zoom box & mask as original zoom (black = masked, not fitted)",
        fontsize=6, pad=3)
    ax.tick_params(axis="both", which="major", direction="out", top=True, right=True,
                   labelsize=8, length=4, width=0.5)
    ax.tick_params(axis="both", which="minor", direction="out", top=True, right=True,
                   labelsize=8, length=4, width=0.5)
    fig.tight_layout()
    fig.savefig(output_path, dpi=100)
    plt.close(fig)


# ════════════════════════════════════════════════════════════════════════════
# note 小节生成 / 结果落盘
# ════════════════════════════════════════════════════════════════════════════

def _tier_summary(verdict: dict[str, Any]) -> tuple[int, str]:
    contains_bar = verdict["contains_bar"]
    if contains_bar is True:
        return 1, "Tier 1 — 80% 概率存在 bar"
    if contains_bar is False:
        return 3, "Tier 3 — 80% 概率无 bar"
    return 2, "Tier 2 — 不确定（Uncertain）"


def _guidance_for(tier: int, verdict: dict[str, Any]) -> str:
    if tier == 1:
        pa = verdict.get("bar_pa_deg")
        raw_ab = verdict.get("evaluated_predicates", {}).get("raw_ab")
        pa_txt = f"PA≈{pa:.1f}°" if pa is not None else "PA 见 bar_pa_deg"
        q_txt = f"q≈{1.0 / raw_ab:.2f}" if raw_ab else "q 由残差估计"
        return (f"Tier 1：80% 概率存在 bar。后续拟合优先考虑添加 Bar 成分（sersic，n=0.5 固定），"
                f"初值建议 {pa_txt}（CCW from North/Up）、轴比 {q_txt}（b/a，对应投影轴比 a/b="
                f"{raw_ab}）；Re 初值与是否添加由 component_analysis 依残差证据最终裁决。")
    if tier == 3:
        return ("Tier 3：80% 概率无 bar。后续迭代不得主动添加 Bar 成分，"
                "除非 component_analysis 依据残差给出明确证据。")
    return ("Tier 2：棒存在性不确定。不要仅凭本判决加/减 Bar 成分，"
            "交由 component_analysis 依后续残差证据裁决。")


def _build_note_section(source_id: str, fits_file: str, param_file: Optional[str],
                        first_round_components: list[str], verdict: dict[str, Any],
                        vlm_features: dict[str, Any], photometrics: dict[str, Any],
                        vlm_status: str, guidance: str) -> str:
    """生成供 Agent 追加进 working_note.md 的标准小节（本工具不写 note）。"""
    tier, tier_label = _tier_summary(verdict)
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    pred = verdict.get("evaluated_predicates", {})
    raw_ab = pred.get("raw_ab")
    lines = [
        f"## Bar 结构识别（neuro-symbolic，首轮拟合后）— {ts}",
        f"- 输入: {fits_file}" + (f"（参数文件: {param_file}）" if param_file else ""),
        f"- 首轮拟合成分: {', '.join(first_round_components) if first_round_components else '未知（未找到参数文件）'}",
        f"- VLM 特征提取: {vlm_status}",
    ]
    if param_file is None:
        lines.append("- ⚠ 参数文件未找到，已降级执行（无 re_fit 发散门 / 无外部坏像素 mask）；"
                     "建议传 param_file=本轮 output_param_file 重试")
    lines.append(f"- **{tier_label}**")
    if tier == 1:
        pa = verdict.get("bar_pa_deg")
        q = (1.0 / raw_ab) if raw_ab else None
        lines += [
            f"  - bar 方向角 bar_pa_deg: {pa:.1f}°（CCW from North/Up）" if pa is not None else "  - bar 方向角: n/a",
            f"  - 投影轴比 raw_ab (a/b): {raw_ab}  →  GALFIT 轴比 q = b/a ≈ {q:.2f}" if raw_ab else "  - 投影轴比: n/a",
            f"  - 去投影轴比: {verdict.get('deprojected_ab')}；核心-盘 ΔPA: {verdict.get('delta_pa_deg')}°",
        ]
    lines += [
        f"  - 判决路径: {verdict.get('decision_path')}",
        f"  - 命中判据: {'; '.join(verdict.get('matched_criteria', []))}",
        f"- 关键特征: is_spiral={vlm_features.get('is_spiral')}, inclination={pred.get('inc')}°, "
        f"disk_pa={vlm_features.get('disk_pa_deg')}°, host={pred.get('has_outer_disk')}, "
        f"arm={pred.get('arm_takeoff')}, m2_amp={pred.get('m2_amp')}, QC={pred.get('quadrupole_contrast')}",
        f"- 测光: core_ab_phot={photometrics.get('core_ab_phot')}, "
        f"m2_amp={photometrics.get('m2_amp')}, quadrupole_contrast={photometrics.get('quadrupole_contrast')}, "
        f"quad_alt_contrast={photometrics.get('quad_alt_contrast')}",
        f"- 理由: {verdict.get('reasoning')}",
        f"- 拟合指引: {guidance}",
        "",
    ]
    return "\n".join(lines)


# ════════════════════════════════════════════════════════════════════════════
# MCP 工具
# ════════════════════════════════════════════════════════════════════════════

def bar_recognition(
    fits_file: Annotated[str, "Absolute path to the FIRST-round fitting output FITS (run_galfit's optimized_fits_file, containing original/model/residual HDUs; GalfitS per-band *_result.fits also accepted)"],
    band: Annotated[str, "Band label, used only for output file naming (e.g. F200W or r)"] = "",
    source_id: Annotated[str, "Identifier for the source/galaxy"] = "",
    re_obs_px: Annotated[Optional[float], "Optional override of the observed half-light radius [px] used for zoom geometry and photometry"] = None,
    param_file: Annotated[str, "First-round parameter file (galfit.NN preferred, feedme accepted). Recommended backup input: run_galfit's returned output_param_file. Empty = auto-discover next to the FITS then in parent dirs; when missing the tool degrades gracefully but reports a warning"] = "",
    custom_instructions: Annotated[str, "Optional context, e.g. scientific objective of this fitting task"] = "",
) -> dict[str, Any]:
    """Neuro-symbolic bar recognition on the FIRST-round fitting result.

    Called in the single-band workflows right after the first run_galfit round
    (this replaces the former original-image analysis step). Pipeline (identical
    to the reference bar_recognize neuro-symbolic engine):
      1. Load original/model/residual arrays from the first-round output FITS;
      2. Render the three calibrated views (global / core-zoom with 5-sigma
         contour / residual-zoom seismic) with the reference zoom geometry;
      3. Extract objective photometrics (asinh second moments, azimuthal m=2
         Fourier mode, quadrupole contrast) from the float arrays;
      4. A VLM extracts the 11 standardized morphological features from the
         three views;
      5. The deterministic rule engine (v2.4.1) fuses everything into a verdict.

    Tier semantics:
      - Tier 1: contains_bar=True  -> "80% probability bar exists"; returns the
        bar position angle (bar_pa_deg, CCW from North/Up) and the projected
        axis ratio (raw_ab, a/b>=1, VLM track after photometric anchoring).
      - Tier 2: contains_bar="Uncertain" -> inconclusive.
      - Tier 3: contains_bar=False -> "80% probability no bar".

    The verdict (tier, bar PA, raw_ab, decision trace, fitting guidance) is
    saved as <fits_stem>_bar_recognition.json / .md next to the FITS file, and
    a ready-to-append markdown section is returned as `note_section` — the
    workflow Agent (not this tool) writes it into the galaxy's working_note.md.

    Returns:
        dict: status / tier / tier_label / contains_bar / bar (Tier 1) /
        decision_path / matched_criteria / reasoning / vlm_features /
        photometrics / images / guidance / note_section / analysis_file
    """
    fits_file = os.path.abspath(fits_file)
    if not os.path.exists(fits_file):
        return {"status": "failure", "error": f"FITS file not found: {fits_file}"}
    if re_obs_px is not None and re_obs_px <= 0:
        return {"status": "failure", "error": f"re_obs_px must be positive, got {re_obs_px}"}

    fits_dir = os.path.dirname(fits_file)
    base_name = os.path.splitext(os.path.basename(fits_file))[0]
    if band:
        base_name = f"{base_name}_{band}"

    # 1) 加载数据与辅助文件
    try:
        data = _load_fit_fits(fits_file)
    except Exception as e:
        return {"status": "failure", "error": f"Failed to load FITS: {e}"}

    param_file_resolved = _find_param_file(fits_dir, param_file)

    # 2) zoom 几何（定心/半宽/掩蔽）
    try:
        ctx = _build_zoom_context(fits_file, data, param_file_resolved, re_obs_px)
    except Exception as e:
        return {"status": "failure", "error": f"Failed to build zoom context: {e}"}

    first_round_components = [c["type"] for c in ctx["components"]]

    # 3) 渲染三视图
    png_global = os.path.join(fits_dir, f"{base_name}_global.png")
    png_core = os.path.join(fits_dir, f"{base_name}_core_zoom.png")
    png_resid = os.path.join(fits_dir, f"{base_name}_residual_zoom.png")
    try:
        _render_global_view(png_global, ctx)
        _render_core_zoom(png_core, ctx)
        _render_residual_zoom(png_resid, ctx)
    except Exception as e:
        return {"status": "failure", "error": f"Failed to render views: {e}"}

    # 4) 客观测光（npz 浮点轨：zoom 裁剪 + 以数组几何中心为心）
    photometrics = extract_photometrics_from_arrays(
        ctx["zoom"], ctx["resid_zoom"] if ctx["resid_zoom"] is not None
        else np.zeros_like(ctx["zoom"]),
        ctx["zoom_mask"] > 0, ctx["bg_std"], ctx["half_width_px"],
        re_obs_px=ctx["re_obs"],
        has_resid=ctx["resid_zoom"] is not None,
    ) or {}

    # 5) VLM 特征提取（参考顺序 Image1=global, Image2=core_zoom, Image3=residual_zoom）
    vlm_features: dict[str, Any] = {}
    vlm_status = "ok"
    vlm_error = None
    vlm_raw = None
    client, error = create_vlm_client()
    if error:
        vlm_status, vlm_error = "failed", error
    else:
        system_message = prompt.get_bar_extractor_system_message()
        user_text = prompt.get_bar_extractor_user_prompt()
        if source_id:
            user_text = f"source_id: {source_id}\n\n{user_text}"
        if custom_instructions:
            user_text += f"\n\n--- Additional requirements ---\n{custom_instructions}"

        b64_global = encode_image_to_base64(png_global)
        b64_core = encode_image_to_base64(png_core)
        b64_resid = encode_image_to_base64(png_resid)
        if not (b64_global and b64_core and b64_resid):
            vlm_status, vlm_error = "failed", "Failed to encode rendered views"
        else:
            additional_images = [
                {"base64": b64_core,
                 "description": "Image 2: Center Optical Zoom with 5-sigma green isophotal contour"},
                {"base64": b64_resid,
                 "description": "Image 3: First-Round Model Residual Zoom, seismic colormap (red=positive excess, blue=negative deficit)"},
            ]
            last_err = None
            for _attempt in range(1, VLM_MAX_ATTEMPTS + 1):
                analysis, error = call_vlm_api(
                    client=client,  # type: ignore[arg-type]
                    base64_image=b64_global,
                    additional_content=[{"type": "text", "text": user_text}],
                    system_message=system_message,
                    max_tokens=9600,
                    temperature=0.2,
                    additional_images=additional_images,
                )
                if error or not analysis:
                    last_err = error or "empty response"
                    continue
                vlm_raw = analysis
                parsed = extract_json_from_text(analysis)
                if parsed and isinstance(parsed, dict):
                    vlm_features = parsed
                    break
                last_err = "Empty or unparseable JSON response"
            if not vlm_features:
                vlm_status = "failed"
                vlm_error = f"VLM feature extraction failed after {VLM_MAX_ATTEMPTS} attempts: {last_err}"

    # 6) 守卫轨测光（需 VLM 的倾角/盘PA/旋涡标志）+ 符号判决
    if vlm_status == "ok":
        inc_val = float(vlm_features.get("inclination_deg", 0.0)) \
            if vlm_features.get("inclination_deg") is not None else 0.0
        dpa_val = float(vlm_features.get("disk_pa_deg", 0.0)) \
            if vlm_features.get("disk_pa_deg") is not None else 0.0
        sp_val = bool(vlm_features.get("is_spiral", True))
        try:
            orig_gray = np.array(Image.open(png_core).convert("L"), dtype=float)
            res_rgb = np.array(Image.open(png_resid).convert("RGB"), dtype=float)
            photometrics.update(extract_guarded_features(
                orig_gray, res_rgb, ctx["re_obs"], inc_val, dpa_val, sp_val))
        except Exception as e:
            print(f"[bar_recognition] guarded features failed: {e}")

    if vlm_status == "failed":
        # 参考语义（run_eval.py）：VLM 彻底失败回退 Uncertain → Tier 2
        verdict = {
            "contains_bar": "Uncertain",
            "bar_pa_deg": None,
            "deprojected_ab": None,
            "delta_pa_deg": None,
            "decision_path": "VLM feature extraction failed -> Uncertain (Tier 2)",
            "reasoning": f"VLM feature extraction failed: {vlm_error}",
            "decision_trace": [],
            "matched_criteria": [],
            "evaluated_predicates": {},
        }
    else:
        verdict = evaluate_galaxy_bar(
            vlm_features,
            metadata={"re_obs_px": ctx["re_obs"], "re_fit_px": ctx["re_fit"]},
            photometrics=photometrics,
        )

    tier, tier_label = _tier_summary(verdict)
    guidance = _guidance_for(tier, verdict)

    result: dict[str, Any] = {
        "status": "success",
        "tier": tier,
        "tier_label": tier_label,
        "param_file": param_file_resolved,
        "contains_bar": verdict["contains_bar"],
        "vlm_status": vlm_status,
        "decision_path": verdict.get("decision_path"),
        "matched_criteria": verdict.get("matched_criteria", []),
        "reasoning": verdict.get("reasoning"),
        "vlm_features": vlm_features,
        "photometrics": photometrics,
        "geometry": {
            "center_px": [float(c) for c in ctx["center"]],
            "half_width_px": round(float(ctx["half_width_px"]), 2),
            "re_obs_px": round(float(ctx["re_obs"]), 2),
            "re_fit_px": round(float(ctx["re_fit"]), 2),
            "companions_masked": ctx["companions_masked"],
            "bg_std": ctx["bg_std"],
        },
        "first_round_components": first_round_components,
        "images": {"global": png_global, "core_zoom": png_core, "residual_zoom": png_resid},
        "guidance": guidance,
    }
    if tier == 1:
        raw_ab = verdict.get("evaluated_predicates", {}).get("raw_ab")
        result["bar"] = {
            "pa_deg": verdict.get("bar_pa_deg"),
            "projected_axis_ratio": raw_ab,
            "galfit_q": round(1.0 / raw_ab, 3) if raw_ab else None,
            "deprojected_axis_ratio": verdict.get("deprojected_ab"),
        }
    if vlm_status == "failed":
        result["error"] = vlm_error
    if param_file_resolved is None:
        # 静默降级是不可接受的：明确告知质量损失与补救方式
        result["warnings"] = result.get("warnings", []) + [
            "未找到首轮参数文件（galfit.NN / feedme，已查找 FITS 同目录及其上层目录）。"
            "已降级执行：zoom 定心改为纯数据驱动吸附；re_fit=0（Re_fit/Re_obs 尺度发散门失效）；"
            "无外部坏像素 mask（仅非有限像素 + 伴星掩蔽）。"
            "建议以 param_file = 本轮 run_galfit 返回的 output_param_file 重新调用以恢复完整精度。"
        ]

    # 自含的一行结论/行动摘要：workflow 无需复述任何降级或处理规则
    result["message"] = (
        f"{tier_label}；拟合指引见 guidance，note_section 供追加到星系 working_note.md。"
        + ("存在 warnings（已降级执行），请按其建议处理后重调。" if result.get("warnings") else "")
    )

    # 7) 落盘：JSON + MD 工具产物；working_note 的写入由 workflow 中的 Agent
    #    用返回的 note_section 完成（本工具不写 working_note.md）
    note_section = _build_note_section(
        source_id, fits_file, param_file_resolved, first_round_components,
        verdict, vlm_features, photometrics, vlm_status, guidance)
    result["note_section"] = note_section

    json_file = os.path.join(fits_dir, f"{base_name}_bar_recognition.json")
    md_file = os.path.join(fits_dir, f"{base_name}_bar_recognition.md")
    payload = dict(result)
    payload["verdict"] = verdict
    payload["source_id"] = source_id
    payload["fits_file"] = fits_file
    payload["param_file"] = param_file_resolved
    payload["created_at"] = datetime.datetime.now().isoformat(timespec="seconds")
    payload["vlm_raw"] = vlm_raw
    try:
        with open(json_file, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        result["analysis_file"] = json_file
    except Exception as e:
        print(f"Warning: failed to save {json_file}: {e}")

    try:
        with open(md_file, "w", encoding="utf-8") as f:
            f.write(note_section)
        result["analysis_md_file"] = md_file
    except Exception as e:
        print(f"Warning: failed to save {md_file}: {e}")

    return result
