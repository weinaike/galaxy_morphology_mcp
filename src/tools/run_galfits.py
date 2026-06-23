import asyncio
import os
import re
import shlex
import shutil
import subprocess
import importlib.util
from datetime import datetime
from glob import glob

import numpy as np
import matplotlib
matplotlib.use('Agg')  
import matplotlib.pyplot as plt
from matplotlib.gridspec import GridSpec, GridSpecFromSubplotSpec
from astropy.io import fits
from typing import Any, Annotated, List, Dict, Tuple

from .render_original import render_asinh_panel
from .sb_profile import render_sb_profile
from .parse_lyric import (
    parse_image_infos_from_lyric,
    parse_region_info_from_lyric,
    extract_component_attributes,
    generate_subcomps
)

WORKFLOW_OUTPUT_DIR_RE = re.compile(r"^\d{8}_\d{6}_.+(?:_iter\d+)?$")


def _is_valid_workflow_output_dir(dirname: str) -> bool:
    """Return True only for strict timestamp-based workflow directory names."""
    return bool(WORKFLOW_OUTPUT_DIR_RE.fullmatch(dirname))


def _resolve_config_paths(config_file: str) -> str:
    """Create a temporary config with absolute paths for tools that need them.

    gsutils.read_config_file resolves relative paths against CWD, not the
    config file location. This helper rewrites ./  and ../ references to
    absolute paths so that callers don't need to care about CWD.
    """
    config_dir = os.path.dirname(os.path.abspath(config_file))
    parent_dir = os.path.dirname(config_dir)

    with open(config_file) as f:
        content = f.read()

    content = content.replace("../", parent_dir + "/")
    content = content.replace("./", config_dir + "/")

    abs_config = os.path.join(config_dir, f"_{os.path.basename(config_file)}.abs")
    with open(abs_config, "w") as f:
        f.write(content)
    return abs_config


def _build_galfits_command(config_file: str, workplace: str, saveimgs: bool) -> list[str]:
    """Build a robust command to run GalfitS.

    We avoid relying on shell aliases (common for GalfitS installs) by preferring:
    1) GALFITS_BIN (can be an executable, a .py file, or a full command string)
    2) python -m galfits.galfitS (if module is importable)
    3) fallback to `galfits` executable on PATH
    """

    galfits_bin = os.getenv("GALFITS_BIN")
    if galfits_bin:
        # Allow specifying a full command string, e.g. "python /path/to/galfitS.py"
        parts = shlex.split(galfits_bin)
        if len(parts) == 1 and parts[0].endswith(".py"):
            python_exec = os.getenv("GALFITS_PYTHON", os.getenv("PYTHON", "python3"))
            cmd = [python_exec, parts[0]]
        else:
            cmd = parts

        cmd += ["--config", config_file, "--workplace", workplace]
        if saveimgs:
            cmd.append("--saveimgs")
        return cmd

    # If GalfitS is installed as a Python package, this is the most reliable.
    # Guard against import-time failures (e.g. missing jax) during probing.
    try:
        module_ok = importlib.util.find_spec("galfits.galfitS") is not None
    except Exception:
        module_ok = False

    if module_ok:
        cmd = [os.getenv("GALFITS_PYTHON", os.getenv("PYTHON", "python3")), "-m", "galfits.galfitS"]
        cmd += ["--config", config_file, "--workplace", workplace]
        if saveimgs:
            cmd.append("--saveimgs")
        return cmd

    cmd = ["galfits", "--config", config_file, "--workplace", workplace]
    if saveimgs:
        cmd.append("--saveimgs")
    return cmd


def _parse_gssummary(summary_path: str) -> dict[str, Any]:
    """Parse a .gssummary file and extract key statistics.

    Returns a dict with reduced_chisq, bic, per_band_chisq, and parameter values.
    """
    if not summary_path or not os.path.exists(summary_path):
        return {}

    with open(summary_path) as f:
        content = f.read()

    result: dict[str, Any] = {
        "reduced_chisq": None,
        "bic": None,
        "per_band_chisq": {},
        "parameters": {},
    }

    # Global reduced chi-squared
    m = re.search(r"reduced\s+chi.*?[:\s]+([\d.]+)", content, re.IGNORECASE)
    if m:
        result["reduced_chisq"] = float(m.group(1))

    # BIC
    m = re.search(r"BIC\s*[:\s]+([\d.eE+-]+)", content, re.IGNORECASE)
    if m:
        result["bic"] = float(m.group(1))

    # Per-band chi-squared (look for patterns like "band_xxx chisq: 1.23" or in tables)
    for m in re.finditer(r"(band\s*\w+|f\d+w)\s*.*?(?:reduced\s+)?chi.*?[:\s]+([\d.]+)", content, re.IGNORECASE):
        band_name = m.group(1).strip()
        result["per_band_chisq"][band_name] = float(m.group(2))

    # Free parameters (tab-separated name-value lines)
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) == 2:
            name, value = parts
            try:
                result["parameters"][name] = float(value)
            except ValueError:
                pass

    return result

def create_perband_comparison_png(
    lyric_file: str,
    gssummary_file: str,
    result_fits_file_list: List[str],
) -> Dict[str, str] | None:
    region_info = parse_region_info_from_lyric(lyric_file)
    image_infos = parse_image_infos_from_lyric(lyric_file)
    pngs = {}
    for image_info in image_infos:
        band = image_info.band
        result_fits_file = [
            fits_file for fits_file in result_fits_file_list if fits_file.find(band) != -1
        ]
        if result_fits_file is None or len(result_fits_file) != 1:
            pngs[band] = "comparison png not created: no unique result fits file found for band %s" % band
            continue
        result_fits_file = result_fits_file[0]

        with fits.open(result_fits_file) as hdul:
            if len(hdul) != 5:
                pngs[band] = "comparison png not created: expected 5 HDUs in result fits file, found %d" % len(hdul)
                continue
            original_data = hdul[4].data    
            model_data = hdul[3].data
            sigma_data = hdul[2].data
            residual_data = hdul[0].data
            mask_data = hdul[1].data
            if mask_data is None:
                mask_data = np.zeros_like(original_data, dtype=np.float)
            mask = np.where(mask_data > 0, 1, 0)

        region = image_info.fitting_region
        components = extract_component_attributes(
            summary_file=gssummary_file,
            config_file=lyric_file,
            fits_file=image_info.image[0],
            band=image_info.band,
            ra=region_info.ra,
            dec=region_info.dec
        )
        comp_imgs, comp_types = generate_subcomps(image_info, components)

        fig = plt.figure(figsize=(40, 8))
        gs = GridSpec(1, 5, figure=fig, wspace=0.18,
                      width_ratios=[1, 1, 1, 1, 0.8])
        fig.subplots_adjust(left=0.03, right=0.97, top=0.85, bottom=0.08)

        # === Col 0: Original Image (99.5th percentile) ===
        ax1 = fig.add_subplot(gs[0, 0])
        orig_info = render_asinh_panel(ax1, original_data, mask, region=region, show_isophotes=True)
        title_orig = (
            f"Original Data (vmax=99.5th pctl)\n"
            f"asinh: a={orig_info['asinh_a']:.4f}, vmin={orig_info['vmin_sigma']:.1f}$\\sigma$\n"
            f"Isophotes: 5$\\sigma$ [lime], vmax [red]"
        )
        ax1.set_title(title_orig, fontsize=9, pad=8)
        ax1.set_xlabel('X (pixels)', fontsize=10)
        ax1.set_ylabel('Y (pixels)', fontsize=10)

        # === Col 1: Original Image (99.99th percentile) ===
        ax1b = fig.add_subplot(gs[0, 1])
        orig_info_9999 = render_asinh_panel(ax1b, original_data, mask, region=region, 
                                            show_isophotes=True, vmax_percentile=99.99)
        title_orig_9999 = (
            f"Original Data (vmax=99.99th pctl)\n"
            f"asinh: a={orig_info_9999['asinh_a']:.4f}, vmin={orig_info_9999['vmin_sigma']:.1f}$\\sigma$\n"
            f"Isophotes: 5$\\sigma$ [lime], vmax [red]"
        )
        ax1b.set_title(title_orig_9999, fontsize=9, pad=8)
        ax1b.set_xlabel('X (pixels)', fontsize=10)
        ax1b.tick_params(labelleft=False)

        # === Col 2: Model Image (same asinh stretch as original 99.5) ===
        ax2 = fig.add_subplot(gs[0, 2])
        if model_data is not None:
            render_asinh_panel(ax2, model_data, mask, region=region,
                               show_isophotes=False, show_mask=False,
                               norm_params=orig_info,
                               components=components,
                               fit_region=region)
        else:
            ax2.text(0.5, 0.5, 'No Model', ha='center', va='center', transform=ax2.transAxes)

        title_model = (
            f"GALFIT Model\n"
            f"Same asinh stretch as original (99.5th pctl)\n"
            f"2*$R_e$ contours of component [cyan]"
        )
        ax2.set_title(title_model, fontsize=9, pad=8)
        ax2.set_xlabel('X (pixels)', fontsize=10)
        ax2.tick_params(labelleft=False)

        # === Col 3: Residual Image (significance map, ±10σ range, seismic) ===
        ax3 = fig.add_subplot(gs[0, 3])
        im3 = None
        if residual_data is not None:
            resid_display = residual_data.copy()
            resid_display[~np.isfinite(resid_display)] = 0

            # Normalize by background std from original image (significance map)
            bg_std = orig_info.get("std", 1.0)
            if bg_std > 0:
                resid_norm = resid_display / bg_std
            else:
                resid_norm = resid_display
            # Set masked pixels to 0 before normalization
            if mask is not None:
                resid_norm[mask > 0] = 0
            # Compute extent for real pixel coordinates from fit_region
            if region is not None:
                xmin, xmax, ymin, ymax = region
                plot_extent = [xmin - 0.5, xmax + 0.5, ymin - 0.5, ymax + 0.5]
            else:
                plot_extent = None

            im3 = ax3.imshow(resid_norm, cmap='seismic', vmin=-10, vmax=10,
                           origin='lower', extent=plot_extent, interpolation='nearest',
                           aspect='auto')

            # Overlay mask on residual (Opaque White)
            if mask is not None:
                mask_overlay = np.zeros((*mask.shape, 4))
                mask_overlay[mask > 0] = [1, 1, 1, 0.7]
                ax3.imshow(mask_overlay, origin="lower", extent=plot_extent, interpolation='nearest')

        else:
            ax3.text(0.5, 0.5, 'No Residual', ha='center', va='center', transform=ax3.transAxes)

        title_resid = (
            f"Residual/$\\sigma$\n"
            f"Normalized by bg $\\sigma$ of original image\n"
            f"Range: $\\pm$10$\\sigma$, white=masked"
        )
        ax3.set_title(title_resid, fontsize=9, pad=8)
        ax3.set_xlabel('X (pixels)', fontsize=10)
        ax3.tick_params(labelleft=False)

        # Add colorbar for residual (right side)
        if im3 is not None:
            from mpl_toolkits.axes_grid1 import make_axes_locatable
            divider = make_axes_locatable(ax3)
            cax = divider.append_axes("right", size="5%", pad=0.05)
            cbar = plt.colorbar(im3, cax=cax, orientation='vertical')
            cbar.ax.tick_params(labelsize=8)
            cbar.set_label('Residual (σ)', fontsize=8)

        # === Col 4: 1D Surface Brightness Profile ===
        gs_sb = GridSpecFromSubplotSpec(2, 1, subplot_spec=gs[0, 4],
                                         height_ratios=[3, 1], hspace=0.05)
        ax_sb = fig.add_subplot(gs_sb[0])
        ax_sb_resid = fig.add_subplot(gs_sb[1], sharex=ax_sb)
        render_sb_profile(
            ax_sb, ax_sb_resid, original_data, sigma_data, model_data,
            None, components, region,
            comp_images=comp_imgs, comp_types=comp_types,
            mask=mask, zeropoint=image_info.magzp, pixscale=image_info.pixscale
        )

        # Save figure
        fits_dir = os.path.dirname(result_fits_file)
        base_name = os.path.splitext(os.path.basename(result_fits_file))[0]
        png_filename = os.path.join(fits_dir, f"{base_name}_comparison.png")
        target_dpi = 1024 / 15
        plt.savefig(png_filename, dpi=target_dpi)
        plt.close(fig)

        pngs[band] = png_filename
    return pngs
            
def create_multiband_comparison_png(
    lyric_file: str,
    gssummary_file: str,
    result_fits_file_list: List[str],
) -> Tuple[str, str]:
    """
    Create a single multi-band comparison PNG with all bands stacked vertically.

    Each band occupies 1 row (5 panels in 1x5 layout):
      Original (99.5) | Original (99.99) | Model | Residual / sigma | SB Profile

    Band name header above each row, horizontal separator between bands.
    Returns the path to the saved PNG and component attributes file, or None if no valid bands found.
    """
    region_info = parse_region_info_from_lyric(lyric_file)
    image_infos = parse_image_infos_from_lyric(lyric_file)

    # --- Collect valid band data ---
    band_data = []
    for image_info in image_infos:
        band = image_info.band
        matched = [f for f in result_fits_file_list if band in f]
        if len(matched) != 1:
            continue
        result_fits_file = matched[0]

        with fits.open(result_fits_file) as hdul:
            if len(hdul) != 5:
                continue
            original_data = hdul[4].data
            model_data = hdul[3].data
            sigma_data = hdul[2].data
            residual_data = hdul[0].data
            mask_data = hdul[1].data
            if mask_data is None:
                mask_data = np.zeros_like(original_data, dtype=float)
            mask = np.where(mask_data > 0, 1, 0)

        components = extract_component_attributes(
            summary_file=gssummary_file,
            config_file=lyric_file,
            fits_file=image_info.image[0],
            band=image_info.band,
            ra=region_info.ra,
            dec=region_info.dec,
        )

        comp_imgs, comp_types = generate_subcomps(image_info, components)

        band_data.append({
            'band': band,
            'image_info': image_info,
            'result_fits_file': result_fits_file,
            'original_data': original_data,
            'model_data': model_data,
            'sigma_data': sigma_data,
            'residual_data': residual_data,
            'mask': mask,
            'components': components,
            'comp_imgs': comp_imgs,
            'comp_types': comp_types,
        })

    if not band_data:
        return None, None

    n_bands = len(band_data)

    # --- Build GridSpec height_ratios ---
    # Per band: header(0.06) + plot row(1); between bands: separator(0.03)
    height_ratios = []
    for i in range(n_bands):
        height_ratios.append(0.06)   # header
        height_ratios.append(1.0)    # plot row (1x5)
        if i < n_bands - 1:
            height_ratios.append(0.03)  # separator
    n_rows = len(height_ratios)

    fig_height = 8 * n_bands
    fig = plt.figure(figsize=(40, fig_height))
    gs = GridSpec(n_rows, 5, figure=fig,
                  wspace=0.18, hspace=0.30,
                  width_ratios=[1, 1, 1, 1, 0.8],
                  height_ratios=height_ratios)
    fig.subplots_adjust(left=0.03, right=0.97, top=0.97, bottom=0.03)

    # --- Render each band ---
    current_row = 0
    for band_idx, bdata in enumerate(band_data):
        image_info = bdata['image_info']
        original_data = bdata['original_data']
        sigma_data = bdata['sigma_data']
        model_data = bdata['model_data']
        residual_data = bdata['residual_data']
        mask = bdata['mask']
        components = bdata['components']
        comp_imgs = bdata['comp_imgs']
        comp_types = bdata['comp_types']
        region = image_info.fitting_region

        # ---- Header row (band name) ----
        ax_header = fig.add_subplot(gs[current_row, :])
        ax_header.set_axis_off()
        ax_header.text(
            0.5, 0.3, f"Band: {bdata['band']}",
            transform=ax_header.transAxes,
            fontsize=14, fontweight='bold',
            ha='center', va='center',
        )
        current_row += 1

        r0 = current_row       # single plot row (1x5)

        # ---- Col 0: Original (99.5th percentile) ----
        ax1 = fig.add_subplot(gs[r0, 0])
        orig_info = render_asinh_panel(
            ax1, original_data, mask, region=region, components=components, show_isophotes=True)
        ax1.set_title(
            f"Original Data (vmax=99.5th pctl)\n"
            f"asinh: a={orig_info['asinh_a']:.4f}, "
            f"vmin={orig_info['vmin_sigma']:.1f}$\\sigma$\n"
            f"Isophotes: 5$\\sigma$ [lime], vmax [red]",
            fontsize=9, pad=8)
        ax1.set_xlabel('X (pixels)', fontsize=10)
        ax1.set_ylabel('Y (pixels)', fontsize=10)

        # ---- Col 1: Original (99.99th percentile) ----
        ax1b = fig.add_subplot(gs[r0, 1])
        orig_info_9999 = render_asinh_panel(
            ax1b, original_data, mask, region=region, components=components,
            show_isophotes=True, vmax_percentile=99.99)
        ax1b.set_title(
            f"Original Data (vmax=99.99th pctl)\n"
            f"asinh: a={orig_info_9999['asinh_a']:.4f}, "
            f"vmin={orig_info_9999['vmin_sigma']:.1f}$\\sigma$\n"
            f"Isophotes: 5$\\sigma$ [lime], vmax [red]",
            fontsize=9, pad=8)
        ax1b.set_xlabel('X (pixels)', fontsize=10)
        ax1b.tick_params(labelleft=False)

        # ---- Col 2: Model ----
        ax2 = fig.add_subplot(gs[r0, 2])
        if model_data is not None:
            render_asinh_panel(
                ax2, model_data, mask, region=region,
                show_isophotes=False, show_mask=False,
                norm_params=orig_info,
                components=components,
                fit_region=region)
        else:
            ax2.text(0.5, 0.5, 'No Model',
                     ha='center', va='center', transform=ax2.transAxes)
        ax2.set_title(
            f"GALFITS Model\n"
            f"Same asinh stretch as original (99.5th pctl)\n"
            f"2*$R_e$ contours of component [cyan]",
            fontsize=9, pad=8)
        ax2.set_xlabel('X (pixels)', fontsize=10)
        ax2.tick_params(labelleft=False)

        # ---- Col 3: Residual / sigma ----
        ax3 = fig.add_subplot(gs[r0, 3])
        im3 = None
        if residual_data is not None:
            resid_display = residual_data.copy()
            resid_display[~np.isfinite(resid_display)] = 0

            bg_std = orig_info.get("std", 1.0)
            resid_norm = resid_display / bg_std if bg_std > 0 else resid_display
            if mask is not None:
                resid_norm[mask > 0] = 0
            plot_extent = None
            if region is not None:
                xmin, xmax, ymin, ymax = region
                plot_extent = [xmin - 0.5, xmax + 0.5, ymin - 0.5, ymax + 0.5]

            im3 = ax3.imshow(
                resid_norm, cmap='seismic', vmin=-10, vmax=10,
                origin='lower', extent=plot_extent,
                interpolation='nearest', aspect='auto')

            if mask is not None:
                mask_overlay = np.zeros((*mask.shape, 4))
                mask_overlay[mask > 0] = [1, 1, 1, 0.7]
                ax3.imshow(mask_overlay, origin='lower',
                           extent=plot_extent, interpolation='nearest')
        else:
            ax3.text(0.5, 0.5, 'No Residual',
                     ha='center', va='center', transform=ax3.transAxes)

        ax3.set_title(
            f"Residual/$\\sigma$\n"
            f"Normalized by bg $\\sigma$ of original image\n"
            f"Range: $\\pm$10$\\sigma$, white=masked",
            fontsize=9, pad=8)
        ax3.set_xlabel('X (pixels)', fontsize=10)
        ax3.tick_params(labelleft=False)

        if im3 is not None:
            from mpl_toolkits.axes_grid1 import make_axes_locatable
            divider = make_axes_locatable(ax3)
            cax = divider.append_axes("right", size="5%", pad=0.05)
            cbar = plt.colorbar(im3, cax=cax, orientation='vertical')
            cbar.ax.tick_params(labelsize=8)
            cbar.set_label('Residual (σ)', fontsize=8)

        # ---- Col 4: 1D SB Profile ----
        gs_sb = GridSpecFromSubplotSpec(
            2, 1, subplot_spec=gs[r0, 4],
            height_ratios=[3, 1], hspace=0.05)
        ax_sb = fig.add_subplot(gs_sb[0])
        ax_sb_resid = fig.add_subplot(gs_sb[1], sharex=ax_sb)
        render_sb_profile(
            ax_sb, ax_sb_resid, original_data, sigma_data, model_data,
            None, components, region,
            comp_images=comp_imgs, comp_types=comp_types,
            mask=mask, auto_sky=True,
            zeropoint=image_info.magzp,
            pixscale=image_info.pixscale,
        )

        current_row = r0 + 1

        # ---- Separator row (between bands) ----
        if band_idx < n_bands - 1:
            ax_sep = fig.add_subplot(gs[current_row, :])
            ax_sep.set_axis_off()
            ax_sep.axhline(y=0.5, color='gray', linewidth=2)
            current_row += 1

    # --- Save ---
    output_dir = os.path.dirname(band_data[0]['result_fits_file'])
    png_filename = os.path.join(output_dir, "all_bands_comparison.png")
    target_dpi = 1024 / 15
    plt.savefig(png_filename, dpi=target_dpi)
    plt.close(fig)

    component_attr_file = os.path.join(output_dir, "component_attributes.txt")
    with open(component_attr_file, "w") as f:
        f.write("# NOTE that the units of x, y, Re are transformed from arcsec to pixel.\n")
        f.write("Component Attributes by Band\n")
        for bdata in band_data:
            f.write(f"- Band: {bdata['band']}\n")
            for comp in bdata['components']:
                f.write(f"    - Component {comp['name']}:\n")
                for attr, val in comp.items():
                    if attr not in ['name']:
                        f.write(f"        {attr}: {val}\n")
            f.write("\n")

    return png_filename, component_attr_file

async def run_galfits(
    config_file: Annotated[str, "the path to the GalfitS (.lyric) configuration file"],
    timeout_sec: Annotated[int, "timeout in seconds"] = 3600,
    extra_args: Annotated[list[str] | None, "extra GalfitS CLI args (e.g. ['--fit_method','optimizer','--num_steps','200'])"] = None,
    read_summary: Annotated[str | None, "path to previous .gssummary to carry forward best-fit parameters"] = None,
    prior_file: Annotated[str | None, "path to .prior file for mass/size constraints"] = None,
) -> dict[str, Any]:
    """Execute GalfitS (multi-band) with the given config file.

    Runs GalfitS as a subprocess and returns discovered artifacts (summary + PNGs) and logs.
    """

    if not config_file or not os.path.exists(config_file):
        return {"status": "failure", "error": f"Config file not found: {config_file}"}

    # Validate optional inputs
    if read_summary and not os.path.exists(read_summary):
        return {"status": "failure", "error": f"Summary file not found: {read_summary}"}
    if prior_file and not os.path.exists(prior_file):
        return {"status": "failure", "error": f"Prior file not found: {prior_file}"}

    # Find galaxy root and determine workplace
    config_dir = os.path.dirname(os.path.abspath(config_file))
    config_basename = os.path.splitext(os.path.basename(config_file))[0]
    output_parent_dir = os.path.dirname(config_dir)

    if os.path.basename(output_parent_dir) == "output":
        galaxy_dir = os.path.dirname(output_parent_dir)
        subdir = os.path.basename(config_dir)
        if _is_valid_workflow_output_dir(subdir):
            workplace_dir = config_dir
            os.makedirs(workplace_dir, exist_ok=True)
            work_cwd = galaxy_dir
        else:
            return {
                "status": "failure",
                "error": (
                    f"Invalid workflow output directory name: {subdir}. "
                    "Expected YYYYMMDD_HHMMSS_<basename> or "
                    "YYYYMMDD_HHMMSS_<basename>_iterN. "
                    "Legacy roundN naming is not supported."
                ),
            }
    else:
        galaxy_dir = config_dir
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        workplace_dir = os.path.join(galaxy_dir, "output", f"{timestamp}_{config_basename}")
        os.makedirs(workplace_dir, exist_ok=True)
        shutil.copy(config_file, workplace_dir)
        work_cwd = galaxy_dir

    cmd = _build_galfits_command(config_file=config_file, workplace=workplace_dir, saveimgs=True)

    # Pass --readsummary through as-is. Caveat: GalfitS uses astropy.ascii.read
    # which only parses the `# free parameters:` section, so parameters that
    # were vary=0 in the previous round will NOT be inherited even if flipped
    # to vary=1 in the new config. Per CLAUDE.md Core Principle 6, prefer
    # manually writing fitted values into the new .lyric instead.
    if read_summary:
        cmd.extend(["--readsummary", os.path.abspath(read_summary)])

    if extra_args:
        cmd.extend([str(x) for x in extra_args])

    # Add --prior for mass/size constraints
    if prior_file:
        cmd.extend(["--prior", os.path.abspath(prior_file)])

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_sec,
            cwd=work_cwd,
        )
    except subprocess.TimeoutExpired:
        return {
            "status": "failure",
            "error": f"GalfitS execution timed out after {timeout_sec} seconds",
        }
    except FileNotFoundError:
        return {
            "status": "failure",
            "error": "GalfitS executable not found. Set GALFITS_BIN (or install GalfitS as a Python package).",
            "command": cmd,
        }

    log = (proc.stdout or "") + (proc.stderr or "")

    # Save log to workplace (both success and failure)
    log_path = os.path.join(workplace_dir, "run.log")
    with open(log_path, "w", encoding="utf-8") as f:
        f.write(log)

    # Discover common outputs (even on non-zero returncode,
    # GalfitS may have produced valid result files before exiting)
    summary_files = sorted(glob(os.path.join(workplace_dir, "*.gssummary")))
    summary_files = [f for f in summary_files if f != '_filtered_summary.gssummary']

    # GalfitS output filenames vary between versions; support common patterns.
    imagefit_pngs = sorted(
        set(
            glob(os.path.join(workplace_dir, "*.imagefit.png"))
            + glob(os.path.join(workplace_dir, "*image_fit.png"))
            + glob(os.path.join(workplace_dir, "*imagefit*.png"))
        )
    )
    sedmodel_pngs = sorted(
        set(
            glob(os.path.join(workplace_dir, "*.sedmodel.png"))
            + glob(os.path.join(workplace_dir, "*SED_model.png"))
            + glob(os.path.join(workplace_dir, "*sed*model*.png"))
        )
    )

    result_fits = sorted(set(glob(os.path.join(workplace_dir, "*_result.fits"))))

    comparison_png = None
    if result_fits and summary_files:
        comparison_png, component_attr_file = create_multiband_comparison_png(
            lyric_file=os.path.join(workplace_dir, os.path.basename(config_file)),
            gssummary_file=summary_files[0],
            result_fits_file_list=result_fits,
        )

    if proc.returncode != 0:
        has_results = bool(summary_files and result_fits)
        result = {
            "status": "failure",
            "error": f"GalfitS failed with return code {proc.returncode}",
            "workplace": workplace_dir,
            "command": cmd,
            "log": log,
            "log_path": log_path,
        }
        if has_results:
            result["summary_files"] = summary_files
            result["result_fits"] = result_fits
            result["comparison_png"] = comparison_png
            result["component_attr_file"] = component_attr_file
            result["reduced_chisq"] = _parse_gssummary(summary_files[0]).get("reduced_chisq") if summary_files else None
        return result

    # Additional output files from GalfitS
    constrain_files = sorted(glob(os.path.join(workplace_dir, "*.constrain")))
    params_files = sorted(glob(os.path.join(workplace_dir, "*.params")))

    # Parse gssummary for structured statistics
    summary_stats = _parse_gssummary(summary_files[0]) if summary_files else {}

    return {
        "status": "success",
        "message": f"GalfitS completed successfully for {config_file}. Output files:\n"
        f"- summary_files : .gssummary files contain fitting parameters, χ² statistics, and model components for all bands\n"
        f"- imagefit_pngs : PNG visualizations showing observed data, model fits, and residuals for all image bands\n"
        f"- comparison_png : A single PNG file with multi-band comparison panels (Original, Model, Residual, SB Profile) stacked vertically for all bands\n"
        f"- sedmodel_pngs : PNG plots of Spectral Energy Distribution (SED) models showing multi-band flux fitting across wavelengths\n"
        f"- result_fits : FITS files containing the best-fit model results\n"
        f"- constrain_files : Constraint files used during fitting\n"
        f"- params_files : Parameter files with initial and fitted values",
        "workplace": workplace_dir,
        "summary_files": summary_files,
        #"imagefit_pngs": imagefit_pngs,
        "imagefit_pngs": comparison_png,
        "comparison_png": comparison_png,
        "sedmodel_pngs": sedmodel_pngs,
        "result_fits": result_fits,
        "constrain_files": constrain_files,
        "params_files": params_files,
        "log_path": log_path,
        "reduced_chisq": summary_stats.get("reduced_chisq"),
        "bic": summary_stats.get("bic"),
        "per_band_chisq": summary_stats.get("per_band_chisq", {}),
        "parameters": summary_stats.get("parameters", {}),
    }

async def run_galfits_image_fitting(
    config_file: Annotated[str, "the path to the GalfitS (.lyric) configuration file"],
    timeout_sec: Annotated[int, "timeout in seconds"] = 3600,
    extra_args: Annotated[list[str] | None, "extra GalfitS CLI args (e.g. ['--fit_method','optimizer','--num_steps','200'])"] = None,
) -> dict[str, Any]:
    """Execute GalfitS (multi-band) with the given config file for image fitting.

    It runs GalfitS as a subprocess and returns discovered artifacts (summary + PNGs) and logs.
    """
    return await run_galfits(config_file=config_file, timeout_sec=timeout_sec, extra_args=extra_args)

async def run_galfits_sed_fitting(
    config_file: Annotated[str, "the path to the GalfitS (.lyric) configuration file"],
    image_fitting_workplace: Annotated[str, "the workplace directory containing results from image fitting, required for sed fitting"],
    timeout_sec: Annotated[int, "timeout in seconds"] = 3600,
    extra_args: Annotated[list[str] | None, "extra GalfitS CLI args (e.g. ['--fit_method','optimizer','--num_steps','200'])"] = None,
) -> dict[str, Any]:
    """
    Execute GalfitS (multi-band) with the given config file for sed fitting.
    It runs GalfitS as a subprocess and returns a new lyric file that will be used for combined image and sed fitting.
    The image_fitting_workplace is used to provide the necessary image fitting results for the sed fitting step.
    """
    from .galfits_fitting import PureSEDFitting
    #from src.tools.galfits_fitting import PureSEDFitting

    config_dir = os.path.dirname(os.path.abspath(config_file))
    config_basename, config_ext = os.path.splitext(os.path.basename(config_file))
    # Find galaxy root dir: if config is already inside output/, go up two levels
    output_parent = os.path.dirname(config_dir)
    if os.path.basename(output_parent) == "output" and _is_valid_workflow_output_dir(os.path.basename(config_dir)):
        galaxy_dir = os.path.dirname(output_parent)
    else:
        galaxy_dir = config_dir
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_base = os.path.join(galaxy_dir, "output")
    os.makedirs(output_base, exist_ok=True)
    workplace_dir = os.path.join(output_base, f"{timestamp}_{config_basename}_sed")
    os.makedirs(workplace_dir, exist_ok=True)
    shutil.copy(config_file, workplace_dir)
    new_lyric_file = os.path.join(workplace_dir, f"{config_basename}_for_image_sed_fitting" + (f"{config_ext}" if config_ext else ""))

    # gsutils.read_config_file resolves relative paths against CWD, not the
    # config file location. Create an absolute-path copy to avoid FileNotFoundError.
    abs_config = _resolve_config_paths(config_file)

    res = PureSEDFitting(lyric_file=abs_config, workplace=image_fitting_workplace, new_lyric_file=new_lyric_file, mock_root=workplace_dir, args=extra_args)
    if res.get("status") != "success":
        return {
            "status": "failure",
            "message": f"SED fitting in the workplace {workplace_dir} failed: {res.get('message', 'Unknown error')}"
        }
    return {
        "status": "success",
        "message": f"SED fitting completed successfully. New lyric file for image-sed fitting generated: {new_lyric_file}"
    }    

async def run_galfits_image_sed_fitting(
    config_file: Annotated[str, "the path to the GalfitS (.lyric) configuration file"],
    timeout_sec: Annotated[int, "timeout in seconds"] = 3600,
    extra_args: Annotated[list[str] | None, "extra GalfitS CLI args (e.g. ['--fit_method','optimizer','--num_steps','200'])"] = None,
) -> dict[str, Any]:
    """Execute GalfitS (multi-band) with the given config file for combined image and sed fitting.

    It runs GalfitS as a subprocess and returns discovered artifacts (summary + PNGs) and logs.
    """
    return await run_galfits(config_file=config_file, timeout_sec=timeout_sec, extra_args=extra_args)

def TEST_create_multiband_comparison_png():
    lyric_file = "/home/jiangbo/jwst/216/output/20260621_134739_obj_216/obj_216.lyric"
    gssummary_file = "/home/jiangbo/jwst/216/output/20260621_134739_obj_216/obj216.gssummary"
    result_fits_file_list = glob("/home/jiangbo/jwst/216/output/20260621_134739_obj_216/*_result.fits")
    png_path, component_attr_file = create_multiband_comparison_png(lyric_file, gssummary_file, result_fits_file_list)
    print(f"Generated comparison PNG: {png_path}")
    print(f"Generated component attributes file: {component_attr_file}")

def TEST_sed_fitting():
    # config_file = "/home/jiangbo/GALFITS_examples_2/6978/obj6978_iter4.lyric"
    # image_fitting_workplace = "/home/jiangbo/GALFITS_examples_2/6978/output/20260603_112151_obj6978_iter4"
    # extra_args = ["--fit_method", "ES"]
    # res = asyncio.run(run_galfits_sed_fitting(config_file, image_fitting_workplace, extra_args=extra_args))
    # print(res)

    # config_file = "/home/jiangbo/GALFITS_examples_2/10766/obj10766.lyric"
    # extra_args = ["--fit_method", "ES"]
    # res = asyncio.run(run_galfits_image_sed_fitting(config_file, extra_args=extra_args))
    # print(res)

    # from .residual_analysis import component_analysis
    # component_analysis(
    #     image_file="/home/jiangbo/GALFITS_examples_2/6978/output/20260604_091751_obj6978_iter4_sed/all_bands_comparison.png", 
    #     summary_file="/home/jiangbo/GALFITS_examples_2/6978/output/20260604_091751_obj6978_iter4_sed/obj6978_s1_nosed.gssummary",
    #     mode="multi-band"
    # )

    # config_file = "/home/jiangbo/GALFITS_examples_2/13374/obj13374_iter2.lyric"
    # # extra_args = ["--fit_method","ES","--readsummary","/home/jiangbo/GALFITS_examples_2/13374/output/20260604_114058_obj13374/obj13374_s1.gssummary"]
    # extra_args = ["--fit_method","ES"]
    # res = asyncio.run(run_galfits_image_fitting(config_file, extra_args=extra_args))
    # print(res)

    config_file = "/home/jiangbo/GALFITS_examples_2/2114/obj2114_iter2.lyric"
    extra_args = ["--fit_method","ES"]
    res = asyncio.run(run_galfits_image_fitting(config_file, extra_args=extra_args))
    print(res)
    
if __name__ == "__main__":
    # TEST_sed_fitting()
    TEST_create_multiband_comparison_png()
