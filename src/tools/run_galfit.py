import os
import re
import shutil
import subprocess
import hashlib
import tempfile
from typing import Any, Annotated, List
import numpy as np
import matplotlib
matplotlib.use('Agg')  # Use non-interactive backend
import matplotlib.pyplot as plt
from matplotlib.gridspec import GridSpec, GridSpecFromSubplotSpec
from matplotlib.patches import Rectangle
from mpl_toolkits.axes_grid1 import make_axes_locatable
from astropy.io import fits
from astropy.stats import sigma_clipped_stats
import datetime
import glob

from .extract_summary_galfit import extract_summary_from_galfit
from .parse_feedme import parse_feedme, parse_components
from .render_original import render_asinh_panel
from .sb_profile import render_sb_profile

# Residual-zoom panel geometry (mirrors v2 layout in rerender_comparisons.py)
ZOOM_HALF_MIN_PX = 12       # 放大框半宽下限，防止 Re 过小时框退化
ZOOM_RE_FACTOR = 2.5        # 半宽 = 2.5×Re，即全宽 5×Re
ZOOM_SIGMA_RANGE = 10       # 放大图色标 ±10σ，与主残差图一致
CENTER_CLUSTER_PX = 3.0     # 距场心同一目标簇的容差：同心多成分(盘+核+棒)归为一簇


def observed_reff(data: np.ndarray, mask: np.ndarray,
                  ixc: float, iyc: float) -> float:
    """原图实测圆形半光半径 R_e,obs [pix]（掩膜内、去天光）。

    作为放大框尺寸的稳健基准：不依赖任一成分的拟合 Re，规避
    PSF(Re=0)/坍缩 bulge 把框压到下限，也与成分标签解耦。
    半光半径由「以拟合中心为圆心的圆形通量增长曲线」取半光得到；
    返回 0.0 表示无法测定（像素不足/总通量非正），调用方据此回落。
    """
    good = np.isfinite(data) & (mask == 0)
    if int(good.sum()) < 50:
        return 0.0
    try:
        sky = float(sigma_clipped_stats(data[good])[1])  # (mean, median, std)
    except Exception:
        return 0.0
    sci = np.where(good, data - sky, 0.0)
    yy, xx = np.indices(sci.shape)
    r = np.sqrt((xx - ixc) ** 2 + (yy - iyc) ** 2)
    rg, sg = r[good], sci[good]
    order = np.argsort(rg)
    rg, sg = rg[order], sg[order]
    cum = np.cumsum(sg)
    total = cum[-1]
    if not np.isfinite(total) or total <= 0:
        return 0.0
    return float(np.interp(total / 2.0, cum, rg))


def _crop_to_fit_region(full_data: np.ndarray, fit_region: tuple[int, int, int, int] | None,
                        target_shape: tuple[int, ...]) -> np.ndarray:
    """Crop full-frame data to match GALFIT output using the fitting region from feedme.

    Args:
        full_data: Full-frame image array.
        fit_region: (xmin, xmax, ymin, ymax) in 1-indexed pixels from feedme H) parameter,
                    or None to fall back to center crop.
        target_shape: Expected output shape (ny, nx) to crop to.

    Returns:
        Cropped array matching target_shape.
    """
    if fit_region is not None:
        xmin, xmax, ymin, ymax = fit_region
        # Convert 1-indexed feedme coords to 0-indexed Python slice
        cropped = full_data[ymin - 1:ymax, xmin - 1:xmax]
        if cropped.shape == target_shape:
            return cropped
        # Fall through to center crop if shape doesn't match (shouldn't happen)
    # Fallback: center crop
    dy, dx = (full_data.shape[0] - target_shape[0]) // 2, \
             (full_data.shape[1] - target_shape[1]) // 2
    return full_data[dy:dy + target_shape[0], dx:dx + target_shape[1]]


def _generate_subcomps(param_file: str, working_dir: str) -> tuple[list, list] | None:
    """Generate individual component images via GALFIT subcomps mode (P=3).

    Returns (comp_images, comp_types) where comp_types are raw GALFIT type
    strings (e.g. "sersic", "expdisk"), or None on failure.
    """
    tmpdir = tempfile.mkdtemp(prefix="galfit_sub_", dir=working_dir)
    try:
        subcomps_feedme = os.path.join(tmpdir, "subcomps.feedme")
        # GALFIT P=3 always writes "subcomps.fits" to CWD, ignoring B)
        subcomps_path = os.path.join(working_dir, "subcomps.fits")

        with open(param_file) as f:
            lines = f.readlines()

        with open(subcomps_feedme, 'w') as f:
            for line in lines:
                s = line.strip()
                if re.match(r'^P\)', s):
                    f.write("P) 3                   # subcomps mode\n")
                else:
                    f.write(line)

        # Remove stale subcomps output if present
        if os.path.exists(subcomps_path):
            os.remove(subcomps_path)

        galfit_bin = os.getenv("GALFIT_BIN", "galfit")
        subprocess.run(
            [galfit_bin, subcomps_feedme],
            cwd=working_dir,
            capture_output=True, text=True, timeout=300,
        )

        if not os.path.exists(subcomps_path):
            return None

        comp_images = []
        comp_types = []
        known_components = {"sersic", "expdisk", "edgedisk", "devauc", "king",
                            "nuker", "psf", "gaussian", "moffat", "ferrer", "sky"}
        with fits.open(subcomps_path) as hdul:
            for i in range(1, len(hdul)):
                obj = hdul[i].header.get("OBJECT", f"Component {i-1}")
                if obj.lower() not in known_components:
                    continue
                comp_images.append(hdul[i].data.astype(np.float64))
                comp_types.append(obj.lower())

        return (comp_images, comp_types) if comp_images else None

    except Exception:
        return None
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def create_comparison_png(
    fits_file: str,
    sigma_file: str | None = None,
    mask_file: str | None = None,
    fit_region: tuple[int, int, int, int] | None = None,
    param_file: str | None = None,
    comp_images: list | None = None,
    comp_types: list | None = None,
) -> tuple[str | None, dict | None]:
    """Create a scientific comparison plot (2×3 layout).

    Layout:
    - Row 0: DATA (LOW DR) | DATA (HIGH DR) | MODEL
    - Row 1: RESIDUAL (full field) | RESIDUAL ZOOM | 1D SB Profile

    Rendering style:
    - DATA: asinh stretch (Greys_r) at 99.5th / 99.99th vmax, with isophote contours
    - MODEL: same asinh stretch as DATA (99.5th), plus 2*Re component ellipses (cyan)
    - RESIDUAL: normalized by bg σ (seismic, ±10σ); lime dashed box marks the ZOOM region
    - RESIDUAL ZOOM: centered on the brightest component, box width 5×Re (capped at 1/2
      field), same ±10σ scale; black contours added inside saturated cores
    - Mask overlaid as a semi-transparent layer

    Args:
        fits_file: Path to GALFIT output FITS file (contains original, model, residual)
        sigma_file: Path to sigma image for residual normalization
        mask_file: Path to mask image (0=good, non-zero=masked/bad)
        fit_region: (xmin, xmax, ymin, ymax) in 1-indexed pixels from feedme H) parameter.
        param_file: Path to GALFIT parameter file (feedme or galfit.01) to extract components.

    Returns:
        Tuple of (png_path, statistics_1d), both None if failed.
    """
    # ── Phase 1: Load core data (failure = fatal) ──────────────────
    try:
        with fits.open(fits_file) as hdul:
            original_data = None
            model_data = None
            residual_data = None

            for hdu in hdul:
                object_type = hdu.header.get("OBJECT", "")
                if object_type.find("[") != -1 and original_data is None:
                    original_data = hdu.data
                elif object_type.find("model") != -1:
                    model_data = hdu.data
                elif object_type.find("residual") != -1:
                    residual_data = hdu.data

            if original_data is None:
                print(f"[create_comparison_png] No original data HDU found in {fits_file}")
                return None, None
    except Exception as e:
        print(f"[create_comparison_png] Failed to read FITS file {fits_file}: {e}")
        return None, None

    # ── Phase 2: Load optional data (failure = degrade gracefully) ──
    mask = np.zeros(original_data.shape, dtype=float)
    if mask_file and os.path.exists(mask_file):
        try:
            mask_full = fits.getdata(mask_file)
            if mask_full.shape != original_data.shape:
                mask = _crop_to_fit_region(mask_full, fit_region, original_data.shape)
            else:
                mask = mask_full
            mask = np.where(np.array(mask, dtype=float) > 0, 1.0, 0.0)
        except Exception as e:
            print(f"[create_comparison_png] Failed to load mask {mask_file}, degrading to no-mask: {e}")
            mask = np.zeros(original_data.shape, dtype=float)

    sigma_data = None
    if sigma_file and os.path.exists(sigma_file):
        try:
            sigma_full = fits.getdata(sigma_file)
            if sigma_full.shape != original_data.shape:
                sigma_data = _crop_to_fit_region(sigma_full, fit_region, original_data.shape)
            else:
                sigma_data = sigma_full
        except Exception as e:
            print(f"[create_comparison_png] Failed to load sigma {sigma_file}, degrading to no-sigma: {e}")

    # ── Phase 3: Render ────────────────────────────────────────────
    fig = plt.figure(figsize=(24, 16))
    try:
        region = list(fit_region) if fit_region is not None else None

        components = None
        if param_file and os.path.exists(param_file):
            components = parse_components(param_file)

        # Layout 2×3:
        # Row 0 = DATA LOW DR | DATA HIGH DR | MODEL
        # Row 1 = RESIDUAL    | RESIDUAL ZOOM | 1D SB Profile
        gs = GridSpec(2, 3, figure=fig, wspace=0.18, hspace=0.28)
        fig.subplots_adjust(left=0.05, right=0.97, top=0.88, bottom=0.05)

        def stamp(ax, label):
            ax.text(0.97, 0.97, label, transform=ax.transAxes, fontsize=14,
                    fontweight='bold', color='lime', va='top', ha='right',
                    bbox=dict(boxstyle='round,pad=0.2', fc='black', alpha=0.6))

        # === Row 0, Col 0: Original Image (99.5th percentile, LOW DR) ===
        ax1 = fig.add_subplot(gs[0, 0])
        orig_info = render_asinh_panel(ax1, original_data, mask, region=region,
                                       show_isophotes=True)
        ax1.set_title(
            f"Original Data (vmax=99.5th pctl, LOW Dynamic Range)\n"
            f"asinh: a={orig_info['asinh_a']:.4f}, vmin={orig_info['vmin_sigma']:.1f}$\\sigma$\n"
            f"Isophotes: 5$\\sigma$ [lime], vmax [red]\n"
            f"Shaded: Masked; Focus: Central Galaxy", fontsize=10, pad=10)
        stamp(ax1, 'DATA (LOW DR)')
        ax1.set_xlabel('X (pixels)', fontsize=12)
        ax1.set_ylabel('Y (pixels)', fontsize=12)

        # === Row 0, Col 1: Original Image (99.99th percentile, HIGH DR) ===
        ax1b = fig.add_subplot(gs[0, 1])
        orig_info_9999 = render_asinh_panel(ax1b, original_data, mask, region=region,
                                            show_isophotes=True, vmax_percentile=99.99)
        ax1b.set_title(
            f"Original Data (vmax=99.99th pctl, HIGH Dynamic Range)\n"
            f"asinh: a={orig_info_9999['asinh_a']:.4f}, vmin={orig_info_9999['vmin_sigma']:.1f}$\\sigma$\n"
            f"Isophotes: 5$\\sigma$ [lime], vmax [red]\n"
            f"Shaded: Masked; Focus: Central Galaxy", fontsize=10, pad=10)
        stamp(ax1b, 'DATA (HIGH DR)')
        ax1b.set_xlabel('X (pixels)', fontsize=12)
        ax1b.tick_params(labelleft=False)

        # === Row 0, Col 2: Model Image (same asinh stretch as original 99.5) ===
        ax2 = fig.add_subplot(gs[0, 2])
        if model_data is not None:
            render_asinh_panel(ax2, model_data, mask, region=region,
                               show_isophotes=False, show_mask=False,
                               norm_params=orig_info,
                               components=components,
                               fit_region=fit_region)
        else:
            ax2.text(0.5, 0.5, 'No Model', ha='center', va='center', transform=ax2.transAxes)
        ax2.set_title(
            "GALFIT Model\n"
            "Same asinh stretch as original (99.5th pctl)\n"
            "2*$R_e$ contours of component [cyan]", fontsize=10, pad=10)
        stamp(ax2, 'MODEL')
        ax2.set_xlabel('X (pixels)', fontsize=12)
        ax2.tick_params(labelleft=False)

        # ── Residual normalization (shared by FULL FIELD + ZOOM panels) ──
        if fit_region is not None:
            xmin, xmax, ymin, ymax = fit_region
            extent = [xmin - 0.5, xmax + 0.5, ymin - 0.5, ymax + 0.5]
        else:
            xmin, ymin = 1, 1
            ymax, xmax = original_data.shape[0], original_data.shape[1]
            extent = [0.5, xmax + 0.5, 0.5, ymax + 0.5]

        resid_norm = None
        if residual_data is not None:
            resid_display = residual_data.copy()
            resid_display[~np.isfinite(resid_display)] = 0
            bg_std = orig_info.get("std", 1.0) or 1.0
            resid_norm = resid_display / bg_std
            resid_norm[mask > 0] = 0

        mask_overlay = np.zeros((*mask.shape, 4))
        mask_overlay[mask > 0] = [1, 1, 1, 0.7]

        # === Row 1, Col 0: Residual (FULL FIELD, ±10σ) ===
        ax3 = fig.add_subplot(gs[1, 0])
        im3 = None
        if resid_norm is not None:
            im3 = ax3.imshow(resid_norm, cmap='seismic', vmin=-10, vmax=10,
                             origin='lower', extent=extent, interpolation='nearest',
                             aspect='auto')
            ax3.imshow(mask_overlay, origin='lower', extent=extent, interpolation='nearest')
            ax3.set_title(
                "Residual/$\\sigma$ (FULL FIELD)\n"
                "Normalized by bg $\\sigma$ of original image\n"
                "Range: $\\pm$10$\\sigma$, white=masked; lime dashed box = ZOOM region",
                fontsize=10, pad=10)
        else:
            ax3.text(0.5, 0.5, 'No Residual', ha='center', va='center', transform=ax3.transAxes)
            ax3.set_title("Residual/$\\sigma$\nNo residual data", fontsize=10, pad=10)
        stamp(ax3, 'RESIDUAL')
        ax3.set_xlabel('X (pixels)', fontsize=12)
        ax3.set_ylabel('Y (pixels)', fontsize=12)
        if im3 is not None:
            divider = make_axes_locatable(ax3)
            cax = divider.append_axes("right", size="5%", pad=0.05)
            cbar = plt.colorbar(im3, cax=cax, orientation='vertical')
            cbar.ax.tick_params(labelsize=9)
            cbar.set_label('Residual (σ)', fontsize=9)

        # ── 放大框几何 ──
        # 定心：目标星系 = 距拟合区域几何中心最近者（伴星系虽亮但偏置，会被排除）；
        #       同心多成分（盘+核+棒，位置几乎重合）归为同一目标簇，取最亮为代表。
        # 尺寸：原图实测半光半径 R_e,obs（掩膜内、去天光、圆形增长曲线），规避
        #       PSF(Re=0)/坍缩 bulge 框过小，并与成分标签解耦。半宽 = max(2.5×R_e,obs, 下限)。
        ctr_x, ctr_y = (xmin + xmax) / 2, (ymin + ymax) / 2
        cands = [c for c in components or [] if c.get("type") != "sky"]
        if cands:
            d2 = lambda c: (c["x"] - ctr_x) ** 2 + (c["y"] - ctr_y) ** 2
            dmin = min(d2(c) for c in cands)
            central = [c for c in cands if d2(c) <= dmin + CENTER_CLUSTER_PX ** 2]
            main = min(central, key=lambda c: c.get("mag", 99))
            cx, cy, re_fit = main["x"], main["y"], max(main.get("re") or 0, 0)
        else:
            cx, cy = ctr_x, ctr_y
            re_fit = 0
        re_obs = observed_reff(original_data, mask, cx - xmin, cy - ymin) or re_fit
        half = max(ZOOM_RE_FACTOR * re_obs, ZOOM_HALF_MIN_PX)
        # 上限：拟合区域短边的 1/4，保证放大图至少有 2 倍放大率
        # （R_e 很大时 5×R_e 会接近全幅，放大失去意义）
        half = min(half, (xmax - xmin) / 4, (ymax - ymin) / 4)
        zx0 = max(xmin, cx - half); zx1 = min(xmax, cx + half)
        zy0 = max(ymin, cy - half); zy1 = min(ymax, cy + half)
        if im3 is not None:
            ax3.add_patch(Rectangle((zx0, zy0), zx1 - zx0, zy1 - zy0,
                                    fill=False, ec="lime", ls="--", lw=1.8))

        # === Row 1, Col 1: Residual Zoom (±10σ, independent axes) ===
        ax_zoom = fig.add_subplot(gs[1, 1])
        imz = None
        if resid_norm is not None:
            ix0, ix1 = int(round(zx0 - xmin)), int(round(zx1 - xmin))
            iy0, iy1 = int(round(zy0 - ymin)), int(round(zy1 - ymin))
            zoom = resid_norm[iy0:iy1 + 1, ix0:ix1 + 1]
            zoom_extent = [zx0 - 0.5, zx1 + 0.5, zy0 - 0.5, zy1 + 0.5]
            imz = ax_zoom.imshow(zoom, cmap='seismic',
                                 vmin=-ZOOM_SIGMA_RANGE, vmax=ZOOM_SIGMA_RANGE,
                                 origin='lower', extent=zoom_extent,
                                 interpolation='nearest', aspect='equal')
            ax_zoom.imshow(mask_overlay[iy0:iy1 + 1, ix0:ix1 + 1],
                           origin='lower', extent=zoom_extent, interpolation='nearest')

            # 中心饱和补救：色标外（>±10σ）的纯色块会吞掉几何信息，
            # 叠加对数间隔的高 σ 等值线还原饱和区内部形态。
            contour_note = ""
            zoom_abs = np.abs(zoom[np.isfinite(zoom)])
            maxabs = zoom_abs.max() if zoom_abs.size else 0
            n_sat = int((zoom_abs >= ZOOM_SIGMA_RANGE).sum())
            if maxabs > 3 * ZOOM_SIGMA_RANGE and n_sat >= 15:
                ratio = maxabs / ZOOM_SIGMA_RANGE
                lv1 = ZOOM_SIGMA_RANGE * ratio ** (1 / 3)
                lv2 = ZOOM_SIGMA_RANGE * ratio ** (2 / 3)
                xs = np.linspace(zx0, zx1, zoom.shape[1])
                ys = np.linspace(zy0, zy1, zoom.shape[0])
                ax_zoom.contour(xs, ys, zoom, levels=[lv1, lv2],
                                colors="yellow", linewidths=0.9)
                ax_zoom.contour(xs, ys, zoom, levels=[-lv2, -lv1],
                                colors="cyan", linewidths=0.9)
                contour_note = (f"; contours $\\pm${lv1:.0f},$\\pm${lv2:.0f}$\\sigma$"
                                f" (yellow=+, cyan=$-$)")

            re_obs_txt = f"{re_obs:.1f}" if re_obs else "n/a"
            re_fit_txt = f"{re_fit:.1f}" if re_fit else "n/a"
            ax_zoom.set_title(
                f"Residual/$\\sigma$ ZOOM (center, box width = "
                f"{2 * ZOOM_RE_FACTOR:.0f}$\\times R_e^{{obs}}$, capped at 1/2 field)\n"
                f"$R_e^{{obs}}$={re_obs_txt} px (data half-light, mask+sky-sub); "
                f"$R_e^{{fit}}$={re_fit_txt} px for ref\n"
                f"center=({cx:.0f},{cy:.0f}), box=[{zx0:.0f}:{zx1:.0f}, {zy0:.0f}:{zy1:.0f}]\n"
                f"Range: $\\pm${ZOOM_SIGMA_RANGE}$\\sigma$ (same as FULL FIELD), "
                f"white=masked{contour_note}", fontsize=10, pad=10)
            divider = make_axes_locatable(ax_zoom)
            cax = divider.append_axes("right", size="5%", pad=0.05)
            cbar = plt.colorbar(imz, cax=cax, orientation='vertical')
            cbar.ax.tick_params(labelsize=9)
            cbar.set_label('Residual (σ)', fontsize=9)
        else:
            ax_zoom.text(0.5, 0.5, 'No Residual', ha='center', va='center',
                         transform=ax_zoom.transAxes)
            ax_zoom.set_title("Residual/$\\sigma$ ZOOM\nNo residual data", fontsize=10, pad=10)
        stamp(ax_zoom, 'RESIDUAL ZOOM')
        ax_zoom.set_xlabel('X (pixels)', fontsize=12)
        ax_zoom.set_ylabel('Y (pixels)', fontsize=12)

        # === Row 1, Col 2: 1D Surface Brightness Profile ===
        statistics_1d = None
        try:
            gs_sb = GridSpecFromSubplotSpec(2, 1, subplot_spec=gs[1, 2],
                                            height_ratios=[3, 1], hspace=0.05)
            ax_sb = fig.add_subplot(gs_sb[0])
            stamp(ax_sb, '1D SB PROFILE')
            ax_sb_resid = fig.add_subplot(gs_sb[1], sharex=ax_sb)
            _, statistics_1d = render_sb_profile(
                ax_sb, ax_sb_resid, original_data, sigma_data, model_data,
                param_file, components, fit_region,
                comp_images=comp_images, comp_types=comp_types,
                mask=mask, auto_sky=True)
        except Exception as e:
            print(f"[create_comparison_png] 1D SB profile failed, degrading: {e}")

        # Save figure
        fits_dir = os.path.dirname(fits_file)
        base_name = os.path.splitext(os.path.basename(fits_file))[0]
        png_filename = os.path.join(fits_dir, f"{base_name}_comparison.png")
        target_dpi = 1024 / 15
        plt.savefig(png_filename, dpi=target_dpi)

        return png_filename, statistics_1d
    except Exception as e:
        print(f"[create_comparison_png] Rendering failed: {e}")
        return None, None
    finally:
        plt.close(fig)


async def run_galfit(
    config_file: Annotated[str, "absolute path to the GALFIT configuration file"],
    options: Annotated[List[str], "options that control how galfit runs"] = []
) -> dict[str, Any]:
    """Execute GALFIT single-band fitting with the given configuration file.

    **Execution Process:**
    1. Parses the GALFIT feedme configuration file to extract file paths and fitting region
    2. Executes GALFIT as a subprocess with 5-minute timeout protection
    3. Generates a 2×3 comparison image: Row 0 = DATA×2 | MODEL, Row 1 = RESIDUAL | RESIDUAL ZOOM | 1D SB Profile
    4. Extracts fitting parameters and statistics to JSON summary
    5. Archives all output files to a timestamped directory with config backup

    **Input Parameters:**
    - config_file (str): Absolute path to GALFIT feedme configuration file
      - Must contain standard GALFIT parameters (A-H sections)
      - Relative paths in config are resolved relative to config file location
    - options (List[str], optional): GALFIT command-line options
      - Example: ["-o"] for overwrite mode, ["-v"] for verbose output

    """
    galfit_bin = os.getenv("GALFIT_BIN", "galfit")
    config_file = os.path.abspath(config_file)
    options = options or []
    if not isinstance(options, list):
        options = [options]
    command = [galfit_bin] + options + [config_file]

    # Parse config file for additional paths
    config_paths = parse_feedme(config_file)

    # Use config file directory as working directory so fit.log is created there
    working_dir = os.path.dirname(os.path.abspath(config_file))

    try:
        proc = subprocess.run(
            command,
            cwd=working_dir,
            capture_output=True,
            text=True,
            check=False,
            timeout=300,  # 5 minute timeout
        )
    except subprocess.TimeoutExpired:
        return {
            "status": "failure",
            "error": "GALFIT execution timed out after 5 minutes",
        }
    except FileNotFoundError:
        return {
            "status": "failure",
            "error": "GALFIT executable not found. Please ensure GALFIT is installed.",
        }

    # Combine stdout and stderr
    full_output = proc.stdout + proc.stderr

    if proc.returncode != 0:
        return {
            "status": "failure",
            "error": f"GALFIT failed with return code {proc.returncode}",
            "log": full_output,
        }

    # Get output file path from parsed config
    output_file = config_paths.get("output", "")
    if not output_file:
        return {
            "status": "failure",
            "error": "Could not find output file path in config",
        }

    # Check if output file exists
    if not os.path.exists(output_file):
        return {
            "status": "failure",
            "error": f"GALFIT output file not created: {output_file}",
            "log": full_output,
        }

    # Identify the latest galfit.[0-9]* file for parameter extraction
    matched_galfit_files = glob.glob(os.path.join(working_dir, "galfit.[0-9]*"))
    latest_galfit = "galfit.01"
    param_file_for_plot = config_file # Fallback
    if matched_galfit_files:
        latest_galfit = max(matched_galfit_files, key=lambda f: int(f.rsplit(".", 1)[-1]))
        param_file_for_plot = latest_galfit

    # Create comparison PNG with sigma and mask if available
    sigma_file = config_paths.get("sigma") or None
    mask_file = config_paths.get("mask") or None
    fit_region = config_paths.get("fit_region")

    # Generate subcomps for SB profile component curves
    comp_data = _generate_subcomps(latest_galfit, working_dir) if matched_galfit_files else None
    comp_images = comp_data[0] if comp_data else None
    comp_types = comp_data[1] if comp_data else None

    # Use latest_galfit (fitted parameters) for component parameters in plot
    comparison_png_path, statistics_1d = create_comparison_png(output_file, sigma_file, mask_file, fit_region,
                                                param_file=param_file_for_plot,
                                                comp_images=comp_images, comp_types=comp_types)

    # Identify constraint file
    constraint_file = config_paths.get("constraint") or None

    # Extract summary information
    summary, fit_stats = extract_summary_from_galfit(output_file, config_file,
                                                     statistics_1d=statistics_1d,
                                                     constraint_file=constraint_file)

    # Cleanup the workspace
    ws_dir = os.path.dirname(output_file)
    ar_dir = os.path.join(ws_dir, "archives", "%s.%s" % (datetime.datetime.now().strftime("%Y%m%dT%H%M%S"), hashlib.md5(config_file.encode("utf-8")).hexdigest()[:8]))
    os.makedirs(ar_dir, exist_ok=True)
    # Save stdout+stderr to file for diagnose
    console_log_path = os.path.join(ar_dir, "console.log")
    with open(console_log_path, "w", encoding="utf-8") as f:
        f.write(full_output)
    fit_log_path = os.path.join(working_dir, "fit.log")
    if os.path.exists(fit_log_path):
        shutil.move(fit_log_path, ar_dir)
    if os.path.exists(output_file):        
        shutil.move(output_file, ar_dir)
        output_file = os.path.join(ar_dir, os.path.basename(output_file))
    if comparison_png_path:
        shutil.move(comparison_png_path, ar_dir)
        comparison_png_path = os.path.join(ar_dir, os.path.basename(comparison_png_path))
    if summary:
        shutil.move(summary, ar_dir)
        summary = os.path.join(ar_dir, os.path.basename(summary))    

    # Archive constraint file if referenced in config
    if constraint_file and os.path.exists(constraint_file):
        shutil.copy(constraint_file, ar_dir)

    if matched_galfit_files:
        shutil.copy(latest_galfit, ar_dir)
    shutil.copy(config_file, ar_dir)
    # Archive subcomps FITS if it was generated
    subcomps_file = os.path.join(working_dir, "subcomps.fits")
    if os.path.exists(subcomps_file):
        shutil.move(subcomps_file, ar_dir)

    stats_lines = ""

    chisq1d_nu = fit_stats.get("chisq1d_nu")
    bic1d = fit_stats.get("bic1d")
    chi2_nu = fit_stats.get("chi2_nu")
    bic = fit_stats.get("bic")
    sky_value = fit_stats.get("sky_value")

    if chisq1d_nu is not None:
        stats_lines += f"-2D χ²/ν (reduced chi-squared): {chi2_nu:.6f}\n"
        stats_lines += f"-1D χ²/ν (reduced chi-squared): {chisq1d_nu:.6f}\n"
    if bic1d is not None:
        stats_lines += f"-1D BIC: {bic1d:.4f}\n"
    if sky_value is not None:
        stats_lines += f"-1D Sky Background: {sky_value:.6f}\n"

    message = (
        "GALFIT completed successfully.\n"
        f"{stats_lines}"
        "- input_param_file: the input feedme configuration file used for this run.\n"
        "- output_param_file: the latest GALFIT output parameter file.\n"
        "- optimized_fits_file: FITS file with original, model, and residual image extensions.\n"
        "- image_file: 2×3 PNG (DATA LOW/HIGH DR | MODEL // RESIDUAL | RESIDUAL ZOOM | 1D SB profile).\n"
        "- summary_file: Markdown file containing fitted parameters, chi-squared statistics, BIC, and observation metadata.\n"
        "- console_log_file: GALFIT console log from this run.\n"
    )
    return {
        "status": "success",
        "message": message,
        "input_param_file": config_file,
        "output_param_file": latest_galfit,  
        "optimized_fits_file": output_file,      
        "image_file": comparison_png_path,
        "summary_file": summary,
        "console_log_file": console_log_path,
    }
