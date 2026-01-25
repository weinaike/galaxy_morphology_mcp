import os
import re
import subprocess
from typing import Any, Annotated
import numpy as np
import matplotlib
matplotlib.use('Agg')  # Use non-interactive backend
import matplotlib.pyplot as plt
from matplotlib.gridspec import GridSpec
from astropy.io import fits

from .extract_summary_galfit import extract_summary_from_galfit


def _parse_galfit_config(config_file: str) -> dict[str, str]:
    """Parse GALFIT configuration file to extract file paths.

    Returns dict with keys: input, output, sigma, mask, psf, constraint
    """
    paths = {
        "input": "",
        "output": "",
        "sigma": "",
        "mask": "",
        "psf": "",
        "constraint": "",
    }

    with open(config_file) as f:
        content = f.read()

    # Parse each parameter line
    patterns = {
        "input": r"^A\)\s*(.+?)\s*#",
        "output": r"^B\)\s*(.+?)\s*#",
        "sigma": r"^C\)\s*(.+?)\s*#",
        "psf": r"^D\)\s*(.+?)\s*#",
        "mask": r"^F\)\s*(.+?)\s*#",
        "constraint": r"^G\)\s*(.+?)\s*#",
    }

    for key, pattern in patterns.items():
        match = re.search(pattern, content, re.MULTILINE)
        if match:
            value = match.group(1).strip()
            if value.lower() not in ("none", ""):
                paths[key] = value

    return paths


def _normimg_galfit(image: np.ndarray, immin: float, immax: float,
                    frac: float = 0.4) -> np.ndarray:
    """Normalize image with arcsinh stretch (GalfitS style).

    Applies arcsinh scaling for better dynamic range display. Handles both
    positive and negative pixel values.

    Args:
        image: Input image array (sky-subtracted).
        immin: Lower threshold for arcsinh stretch.
        immax: Upper threshold for arcsinh stretch.
        frac: Fraction parameter controlling stretch behavior near zero.

    Returns:
        Normalized image in range [-1, 1] suitable for display.
    """
    result = np.zeros_like(image, dtype=float)

    # GalfitS normimg logic:
    # For image > immin: arcsinh stretch from [immin, immax] to [frac, 1]
    # For image <= immin: linear scaling to [-frac, frac]
    # For image < -frac: arcsinh stretch (negative side)

    # Above threshold: arcsinh stretch
    mask_above = image > immin
    if np.any(mask_above):
        arcsinh_min = np.arcsinh(immin)
        arcsinh_max = np.arcsinh(immax)
        result[mask_above] = (1 - frac) * (np.arcsinh(image[mask_above]) - arcsinh_min) / \
                             (arcsinh_max - arcsinh_min) + frac

    # At or below threshold: linear scaling
    mask_at_below = image <= immin
    if np.any(mask_at_below):
        result[mask_at_below] = image[mask_at_below] * frac / immin

    # Negative values: mirror arcsinh stretch to [-1, -frac]
    mask_neg = image < -frac
    if np.any(mask_neg):
        arcsinh_frac = np.arcsinh(frac)
        # Note: GalfitS uses frac*max/min as upper bound for negative side
        arcsinh_negmax = np.arcsinh(frac * immax / immin)
        result[mask_neg] = -(1 - frac) * (np.arcsinh(-image[mask_neg]) - arcsinh_frac) / \
                           (arcsinh_negmax - arcsinh_frac) - frac

    return result


def create_comparison_png(
    fits_file: str,
    sigma_file: str | None = None,
    mask_file: str | None = None,
) -> str | None:
    """Create a scientific comparison plot with original, model, and normalized residual.

    Uses GalfitS-style rendering:
    - Original/Model: arcsinh stretch with 'seismic' colormap
    - Residual: normalized by sigma (significance map) with 'seismic' colormap, ±10σ range
    - Mask overlaid as semi-transparent blue layer on all panels

    Args:
        fits_file: Path to GALFIT output FITS file (contains original, model, residual)
        sigma_file: Path to sigma image for residual normalization
        mask_file: Path to mask image (0=bad, 1=good)

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

        # Load mask if provided (GalfitS uses 0=bad, 1=good convention)
        mask = None
        if mask_file and os.path.exists(mask_file):
            mask_full = fits.getdata(mask_file)
            # Crop to matching region if needed
            if mask_full.shape != original_data.shape:
                dy, dx = (mask_full.shape[0] - original_data.shape[0]) // 2, \
                         (mask_full.shape[1] - original_data.shape[1]) // 2
                mask = mask_full[dy:dy+original_data.shape[0], dx:dx+original_data.shape[1]]
            else:
                mask = mask_full
            # Normalize mask to 0-1 range for display
            mask = np.array(mask, dtype=float)
            mask = np.where(mask > 0, 1, 0)

        # Load sigma if provided
        sigma = None
        if sigma_file and os.path.exists(sigma_file):
            sigma_full = fits.getdata(sigma_file)
            if sigma_full.shape != original_data.shape:
                dy, dx = (sigma_full.shape[0] - original_data.shape[0]) // 2, \
                         (sigma_full.shape[1] - original_data.shape[1]) // 2
                sigma = sigma_full[dy:dy+original_data.shape[0], dx:dx+original_data.shape[1]]
            else:
                sigma = sigma_full

        # Calculate sky statistics for normalization (GalfitS style)
        from astropy.stats import sigma_clipped_stats
        _, sky_median, sky_std = sigma_clipped_stats(original_data, sigma=3.0, maxiters=5)

        # Create figure with custom layout
        fig = plt.figure(figsize=(15, 6))
        gs = GridSpec(1, 3, figure=fig, wspace=0.05)
        fig.subplots_adjust(left=0.05, right=0.95, top=0.90)

        # === Original Image (GalfitS style: seismic, arcsinh stretch) ===
        ax1 = fig.add_subplot(gs[0, 0])
        # GalfitS uses: immin = 5*sky_std, immax = max(image), and subtracts sky
        orig_data_sky_sub = original_data - sky_median
        orig_display = np.flipud(orig_data_sky_sub)
        immin = 5 * sky_std
        immax = np.nanmax(orig_data_sky_sub)
        orig_norm = _normimg_galfit(orig_display, immin, immax, frac=0.4)
        ax1.imshow(orig_norm, cmap='seismic', vmin=-1, vmax=1, origin='lower', interpolation='nearest')
        ax1.set_title('Original Data', fontsize=14, fontweight='bold')
        ax1.set_xlabel('X (pixels)', fontsize=12)
        ax1.set_ylabel('Y (pixels)', fontsize=12)

        # === Model Image (GalfitS style) ===
        ax2 = fig.add_subplot(gs[0, 1])
        if model_data is not None:
            model_data_sky_sub = model_data - sky_median
            model_display = np.flipud(model_data_sky_sub)
            model_norm = _normimg_galfit(model_display, immin, immax, frac=0.4)
            ax2.imshow(model_norm, cmap='seismic', vmin=-1, vmax=1, origin='lower', interpolation='nearest')
        else:
            ax2.text(0.5, 0.5, 'No Model', ha='center', va='center', transform=ax2.transAxes)
        ax2.set_title('GALFIT Model', fontsize=14, fontweight='bold')
        ax2.set_xlabel('X (pixels)', fontsize=12)
        ax2.tick_params(labelleft=False)

        # === Residual Image (GalfitS style: significance map, ±10σ range) ===
        ax3 = fig.add_subplot(gs[0, 2])
        im3 = None
        if residual_data is not None:
            resid_display = np.flipud(residual_data.copy())

            # Normalize by sigma to get significance map (GalfitS style)
            if sigma is not None:
                sigma_safe = np.where((sigma > 0) & (sigma < 1e6), sigma, 1)
                resid_norm = resid_display / sigma_safe
            else:
                # Fallback: use RMS normalization
                rms = np.std(resid_display)
                resid_norm = resid_display / rms if rms > 0 else resid_display

            im3 = ax3.imshow(resid_norm, cmap='seismic', vmin=-10, vmax=10,
                           origin='lower', interpolation='nearest')

            # Add statistics text (GalfitS style)
            resid_valid = resid_norm[~np.isnan(resid_norm)]
            if len(resid_valid) > 0:
                rms_val = np.std(resid_valid)
                max_val = np.max(np.abs(resid_valid))
                stats_text = f'rms: {rms_val:.2f}\nmax: {max_val:.2f}'
                ax3.text(3, 3, stats_text, transform=ax3.transAxes,
                        va='bottom', ha='left', fontsize=11, color='blue',
                        bbox=dict(boxstyle='round', facecolor='white', alpha=0.7))
        else:
            ax3.text(0.5, 0.5, 'No Residual', ha='center', va='center', transform=ax3.transAxes)

        ax3.set_title('Residual/σ', fontsize=14, fontweight='bold')
        ax3.set_xlabel('X (pixels)', fontsize=12)
        ax3.tick_params(labelleft=False)

        # Add colorbar for residual (right side)
        if im3 is not None:
            from mpl_toolkits.axes_grid1 import make_axes_locatable
            divider = make_axes_locatable(ax3)
            cax = divider.append_axes("right", size="5%", pad=0.05)
            cbar = plt.colorbar(im3, cax=cax, orientation='vertical')
            cbar.ax.tick_params(labelsize=11)
            cbar.set_label('Residual (σ)', fontsize=12)

        # === Overlay mask on data and residual panels (not on model) ===
        if mask is not None:
            mask_display = np.flipud(mask)
            for ax in [ax1, ax3]:
                ax.imshow(mask_display, cmap='Blues', origin='lower',
                         alpha=0.5 * mask_display, interpolation='nearest')

        # Add mask legend/explanation at top-left of figure
        if mask is not None:
            fig.text(0.01, 0.99, 'Blue overlay: masked pixels',
                    ha='left', va='top', fontsize=11, color='blue',
                    bbox=dict(boxstyle='round', facecolor='white', alpha=0.8))

        # Add panel labels (GalfitS style)
        ax1.text(3, 3, "data", size=16, color='blue', weight="light",
                transform=ax1.transAxes, va='bottom', ha='left')
        ax2.text(3, 3, "model", size=16, color='blue', weight="light",
                transform=ax2.transAxes, va='bottom', ha='left')
        if im3 is not None:
            ny = orig_display.shape[0]
            ax3.text(3, ny - 3, "residual/σ", size=16, color='blue',
                    weight="light", transform=ax3.transAxes, va='top', ha='left')

        # Save figure (avoid bbox_inches='tight' as it causes issues with divider.append_axes)
        fits_dir = os.path.dirname(fits_file)
        base_name = os.path.splitext(os.path.basename(fits_file))[0]
        png_filename = os.path.join(fits_dir, f"{base_name}_comparison.png")
        # Calculate DPI to get ~1024px width (15 inches * dpi ≈ 1024)
        target_dpi = 1024 / 15
        plt.savefig(png_filename, dpi=target_dpi)
        plt.close(fig)

        return png_filename
    except Exception:
        return None


async def run_galfit(
    config_file: Annotated[str, "the path to the GALFIT configuration file"],
) -> dict[str, Any]:
    """Execute GALFIT with the given configuration file.

    Runs GALFIT as a subprocess and returns the results including
    the residual image as base64-encoded PNG.
    """
    galfit_bin = os.getenv("GALFIT_BIN", "galfit")
    command = [galfit_bin, config_file]

    # Parse config file for additional paths
    config_paths = _parse_galfit_config(config_file)

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

    # Create comparison PNG with sigma and mask if available
    sigma_file = config_paths.get("sigma") or None
    mask_file = config_paths.get("mask") or None
    comparison_png_path = create_comparison_png(output_file, sigma_file, mask_file)

    # Extract summary information
    summary = extract_summary_from_galfit(output_file, full_output)

    return {
        "status": "success",
        "message": "GALFIT completed successfully. optimized_fits_file contains the optimized FITS data with original, model, and residual extensions; image_file is a 1-row 3-column image showing original | model | residual for visual comparison; summary_file is a JSON file containing fitted parameters and their numerical values.",
        "optimized_fits_file": output_file,
        "image_file": comparison_png_path,
        "summary_file": summary,
    }
