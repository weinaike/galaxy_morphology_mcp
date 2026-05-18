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
from astropy.io import fits
import datetime
import glob

from .extract_summary_galfit import extract_summary_from_galfit
from .parse_feedme import parse_feedme, parse_components
from .render_original import render_asinh_panel
from .sb_profile import render_sb_profile


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
) -> str | None:
    """Create a scientific comparison plot with original, model, and normalized residual.

    Rendering style:
    - Original/Model: asinh stretch with Greys_r colormap + isophote contours (shared with render_original)
    - Model: also shows 2*Re component ellipses in cyan dashed lines
    - Residual: normalized by sigma (significance map) with seismic colormap, ±10σ range
    - Mask overlaid as semi-transparent layer on all panels

    Args:
        fits_file: Path to GALFIT output FITS file (contains original, model, residual)
        sigma_file: Path to sigma image for residual normalization
        mask_file: Path to mask image (0=good, non-zero=masked/bad)
        fit_region: (xmin, xmax, ymin, ymax) in 1-indexed pixels from feedme H) parameter.
        param_file: Path to GALFIT parameter file (feedme or galfit.01) to extract components.

    Returns:
        Path to saved PNG file or None if failed.
    """
    try:
        # Read FITS data
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
                return None

        # Load mask if provided (GALFIT convention: 0=good, non-zero=masked/bad)
        mask = None
        if mask_file and os.path.exists(mask_file):
            mask_full = fits.getdata(mask_file)
            # Crop to matching region using fit_region from feedme
            if mask_full.shape != original_data.shape:
                mask = _crop_to_fit_region(mask_full, fit_region, original_data.shape)
            else:
                mask = mask_full
            # Normalize mask to 0-1 range for display
            mask = np.array(mask, dtype=float)
            mask = np.where(mask > 0, 1, 0)

        # Default: no mask (all pixels good)
        if mask is None:
            mask = np.zeros(original_data.shape, dtype=float)

        # Convert fit_region tuple to list for render_asinh_panel
        region = list(fit_region) if fit_region is not None else None

        # Parse components for model panel contour display
        components = None
        if param_file and os.path.exists(param_file):
            components = parse_components(param_file)

        # Create figure: Original | Model | Residual | Spacer | SB Profile | Isophotes
        fig = plt.figure(figsize=(32, 7))
        gs = GridSpec(1, 6, figure=fig, wspace=0.08,
                      width_ratios=[1, 1, 1, 0.15, 1.2, 1])
        fig.subplots_adjust(left=0.04, right=0.97, top=0.82)

        # === Original Image (asinh stretch, Greys_r + isophotes) ===
        ax1 = fig.add_subplot(gs[0, 0])
        orig_info = render_asinh_panel(ax1, original_data, mask, region=region, show_isophotes=True)
        title_orig = (
            f"Original Data\n"
            f"asinh: a={orig_info['asinh_a']:.4f}, vmin={orig_info['vmin_sigma']:.1f}$\\sigma$, vmax=99.5th pctl\n"
            f"Isophotes: 5$\\sigma$ [lime], vmax [red]"
        )
        ax1.set_title(title_orig, fontsize=10, pad=10)
        ax1.set_xlabel('X (pixels)', fontsize=12)
        ax1.set_ylabel('Y (pixels)', fontsize=12)

        # === Model Image (same asinh stretch as original, no isophotes, no mask) ===
        ax2 = fig.add_subplot(gs[0, 1])
        if model_data is not None:
            render_asinh_panel(ax2, model_data, mask, region=region,
                               show_isophotes=False, show_mask=False,
                               norm_params=orig_info,
                               components=components,
                               fit_region=fit_region)
        else:
            ax2.text(0.5, 0.5, 'No Model', ha='center', va='center', transform=ax2.transAxes)
        
        title_model = (
            f"GALFIT Model\n"
            f"Same asinh stretch as original\n"
            f"2*$R_e$ contours of component [cyan]"
        )
        ax2.set_title(title_model, fontsize=10, pad=10)
        ax2.set_xlabel('X (pixels)', fontsize=12)
        ax2.tick_params(labelleft=False)

        # === Residual Image (significance map, ±10σ range, seismic) ===
        ax3 = fig.add_subplot(gs[0, 2])
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
            if fit_region is not None:
                xmin, xmax, ymin, ymax = fit_region
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
        ax3.set_title(title_resid, fontsize=10, pad=10)
        ax3.set_xlabel('X (pixels)', fontsize=12)
        ax3.tick_params(labelleft=False)

        # Add colorbar for residual (right side)
        if im3 is not None:
            from mpl_toolkits.axes_grid1 import make_axes_locatable
            divider = make_axes_locatable(ax3)
            cax = divider.append_axes("right", size="5%", pad=0.05)
            cbar = plt.colorbar(im3, cax=cax, orientation='vertical')
            cbar.ax.tick_params(labelsize=9)
            cbar.set_label('Residual (σ)', fontsize=9)

        # === 1D Surface Brightness Profile (5th column) ===
        gs_sb = GridSpecFromSubplotSpec(2, 1, subplot_spec=gs[0, 4],
                                         height_ratios=[3, 1], hspace=0.05)
        ax_sb = fig.add_subplot(gs_sb[0])
        ax_sb_resid = fig.add_subplot(gs_sb[1], sharex=ax_sb)
        isolist = render_sb_profile(ax_sb, ax_sb_resid, original_data, model_data,
                                    param_file, components, fit_region,
                                    comp_images=comp_images, comp_types=comp_types,
                                    mask=mask)

        # === Isophote Ellipses (6th column) ===
        from .sb_profile import render_isophote_panel
        ax_iso = fig.add_subplot(gs[0, 5])
        render_isophote_panel(ax_iso, original_data, isolist=isolist,
                              mask=mask, norm_params=orig_info)

        # Save figure
        fits_dir = os.path.dirname(fits_file)
        base_name = os.path.splitext(os.path.basename(fits_file))[0]
        png_filename = os.path.join(fits_dir, f"{base_name}_comparison.png")
        target_dpi = 1024 / 15
        plt.savefig(png_filename, dpi=target_dpi)
        plt.close(fig)

        return png_filename
    except Exception:
        return None


async def run_galfit(
    config_file: Annotated[str, "absolute path to the GALFIT configuration file"],
    options: Annotated[List[str], "options that control how galfit runs"] = []
) -> dict[str, Any]:
    """Execute GALFIT single-band fitting with the given configuration file.

    **Execution Process:**
    1. Parses the GALFIT feedme configuration file to extract file paths and fitting region
    2. Executes GALFIT as a subprocess with 5-minute timeout protection
    3. Generates a 1×4 comparison image: Original | Model | Residual | 1D SB Profile
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
    comparison_png_path = create_comparison_png(output_file, sigma_file, mask_file, fit_region,
                                                param_file=param_file_for_plot,
                                                comp_images=comp_images, comp_types=comp_types)

    # Extract summary information
    summary, fit_stats = extract_summary_from_galfit(output_file, config_file)

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

    if matched_galfit_files:
        shutil.copy(latest_galfit, ar_dir)
    shutil.copy(config_file, ar_dir)
    # Archive subcomps FITS if it was generated
    subcomps_file = os.path.join(working_dir, "subcomps.fits")
    if os.path.exists(subcomps_file):
        shutil.move(subcomps_file, ar_dir)

    stats_lines = ""
    chi2_nu = fit_stats.get("chi2_nu")
    bic = fit_stats.get("bic")
    if chi2_nu is not None:
        stats_lines += f"- χ²/ν (reduced chi-squared): {chi2_nu:.6f}\n"
    if bic is not None:
        stats_lines += f"- BIC: {bic:.4f}\n"

    message = (
        "GALFIT completed successfully.\n"
        f"{stats_lines}"
        "- input_param_file: the input feedme configuration file used for this run.\n"
        "- output_param_file: the latest GALFIT output parameter file.\n"
        "- optimized_fits_file: FITS file with original, model, and residual image extensions.\n"
        "- image_file: 1×4 PNG (original | model | residual | 1D SB profile with residual panel).\n"
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
