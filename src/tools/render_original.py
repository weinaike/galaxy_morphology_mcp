import os
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Ellipse
from astropy.io import fits
from astropy.stats import sigma_clipped_stats
from astropy.visualization import simple_norm
from scipy.ndimage import gaussian_filter
from typing import Any, Annotated

from .parse_feedme import parse_feedme
from .parse_lyric import parse_image_infos_from_lyric


def render_asinh_panel(ax, sci, mask, region=None, nmin=1, show_isophotes=True,
                       show_mask=True, norm_params=None, components=None,
                       fit_region=None, vmax_percentile=99.5):
    """Render a single axes with asinh stretch, optional isophotes, and mask overlay.

    This is the shared rendering logic used by both render_original (single panel)
    and create_comparison_png (data/model panels in run_galfit).

    Args:
        ax: matplotlib Axes to draw on.
        sci: 2D science image array.
        mask: 2D mask array (0=good, >0=masked).
        region: [xmin, xmax, ymin, ymax] in 1-based pixels, or None.
        nmin: Number of std below median for vmin.
        show_isophotes: Whether to draw isophote contours.
        show_mask: Whether to overlay mask on the panel.
        norm_params: If provided, use these pre-computed values instead of computing
                     from sci. Dict with keys: vmin, vmax, asinh_a. Used to make
                     model panel share the same stretch as original.
        components: List of component dicts to draw 2*Re ellipses (model panel only).
        fit_region: (xmin, xmax, ymin, ymax) in 1-indexed pixels, for converting
                    component coords from full-image to cropped coords.
    """
    if norm_params is not None:
        vmin = norm_params["vmin"]
        vmax = norm_params["vmax"]
        asinh_a = norm_params["asinh_a"]
        std = norm_params.get("std", 1.0)
    else:
        mean, median, std = sigma_clipped_stats(sci, mask=mask)
        valid = sci[mask == 0]
        valid = valid[np.isfinite(valid)]
        vmin = median - nmin * std
        vmax = np.percentile(valid, vmax_percentile)
        data_range = vmax - vmin
        if data_range <= 0:
            data_range = 1e-10
        noise_fraction = std / data_range
        asinh_a = min(0.5, noise_fraction * 2.0)

    norm = simple_norm(sci, 'asinh', vmin=vmin, vmax=vmax, asinh_a=asinh_a)

    if region is not None:
        xmin_r, xmax_r, ymin_r, ymax_r = region
        ext = [xmin_r - 0.5, xmax_r + 0.5, ymin_r - 0.5, ymax_r + 0.5]
    else:
        ext = None
    ax.imshow(sci, norm=norm, origin="lower", cmap='Greys_r', extent=ext)

    if show_isophotes:
        sci_nomask = np.where(mask == 0, sci, 0.0)
        smoothed = gaussian_filter(sci_nomask, sigma=3)
        level_min = median + 5 * std
        level_max = vmax
        ax.contour(smoothed, levels=[level_min], origin="lower", extent=ext,
                   colors='lime', linewidths=0.2, alpha=0.8)
        ax.contour(smoothed, levels=[level_max], origin="lower", extent=ext,
                   colors='red', linewidths=0.2, alpha=0.8)

    # Mask overlay: semi-transparent black for masked regions
    if show_mask:
        mask_overlay = np.zeros((*mask.shape, 4))
        mask_overlay[mask > 0] = [0, 0, 0, 0.7]
        ax.imshow(mask_overlay, origin="lower", extent=ext)

    # Draw component 2*Re ellipses (for model panel)
    if components and fit_region is not None:
        # Note: components have coords in full image space, extent is also in full image space
        # So we can use component coords directly - no transformation needed
        for comp in components:
            cx = comp["x"]  # Full image x coordinate
            cy = comp["y"]  # Full image y coordinate
            re = comp["re"]
            ba = comp["ba"]
            # GALFIT PA: from +Y axis, counter-clockwise (Up=0, Left=90)
            # matplotlib angle: from +X axis, counter-clockwise
            # Conversion: mpl_angle = 90 + galfit_pa
            pa_mpl = comp["pa"] + 90.0
            # 2*Re contour: semi-major = 2*Re, semi-minor = 2*Re * b/a
            ellipse = Ellipse(
                xy=(cx, cy),
                width=4 * re,          # full diameter of 2*Re along major axis
                height=4 * re * ba,    # full diameter of 2*Re*b/a along minor axis
                angle=pa_mpl,
                edgecolor="cyan",
                facecolor="none",
                linewidth=1.2,
                alpha=0.9,
                linestyle="--",
                zorder=10,
            )
            ax.add_patch(ellipse)

    ax.tick_params(axis="both", which="major", direction="out", top=True, right=True,
                   labelsize=8, length=4, width=0.5)
    ax.tick_params(axis="both", which="minor", direction="out", top=True, right=True,
                   labelsize=8, length=4, width=0.5)

    return {"asinh_a": asinh_a, "vmin_sigma": vmin / std, "vmin": vmin, "vmax": vmax, "std": std}


def render_original(
    config_file: Annotated[str, "absolute path to the GALFIT feedme or GALFITS lyric configuration file"],
) -> dict[str, Any]:
    """Render the original science image from a GALFIT feedme or GALFITS lyric configuration.

    Parses the feedme/lyric to locate the input image, mask, and fitting region (H),
    crops to the ROI, and renders with asinh stretch, isophote contours, and
    mask overlay. Saves the PNG file(s) next to the configuration file.

    Args:
        config_file: Absolute path to the configuration file.

    Returns:
        dict with status and image_file(s) (path to the saved PNG), or status=failure.
    """
    config_file = os.path.abspath(config_file)
    if not os.path.exists(config_file):
        return {"status": "failure", "error": f"Configuration file not found: {config_file}"}

    # parse as lyric format
    image_infos = parse_image_infos_from_lyric(config_file)
    if image_infos:
        rendered_images = {}
        for image_info in image_infos:
            sci_full = fits.getdata(*image_info.image)
            mask_full = np.zeros_like(sci_full, dtype=int)
            if image_info.mask:
                mask_full = fits.getdata(*image_info.mask).astype(int)

            fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(7.68, 3.84))
            info1 = render_asinh_panel(ax1, sci_full, mask_full, region=None,
                                       vmax_percentile=99.5)
            info2 = render_asinh_panel(ax2, sci_full, mask_full, region=None,
                                       vmax_percentile=99.99)
            ax1.set_title(
                f"band: {image_info.band} vmax=99.5th pctl"
                f"\nasinh_a={info1['asinh_a']:.4f}; vmin={info1['vmin_sigma']:.1f}$\\sigma$"
                f"\nIsophotes: 5.0$\\sigma$ [lime]; vmax[red]"
                f"\nShaded: Masked; Focus: Central Galaxy",
                fontsize=6, pad=3)
            ax2.set_title(
                f"band: {image_info.band} vmax=99.99th pctl"
                f"\nasinh_a={info2['asinh_a']:.4f}; vmin={info2['vmin_sigma']:.1f}$\\sigma$"
                f"\nIsophotes: 5.0$\\sigma$ [lime]; vmax[red]"
                f"\nShaded: Masked; Focus: Central Galaxy",
                fontsize=6, pad=3)
            plt.tight_layout()

            lyric_dir = os.path.dirname(config_file)
            base_name = os.path.splitext(os.path.basename(config_file))[0]
            output_path = os.path.join(lyric_dir,
                                       f"{base_name}_{image_info.band}_original.png")
            fig.savefig(output_path, dpi=100)
            plt.close(fig)
            rendered_images[image_info.band] = output_path
        return {
            "status": "success",
            "message": f"The original image has been successfully rendered across {len(image_infos)} observation bands.",
            "image files": rendered_images
        }
    
    params = parse_feedme(config_file)

    if not params["input"]:
        return {"status": "failure", "error": "No input image (A) found in feedme"}
    if not os.path.exists(params["input"]):
        return {"status": "failure", "error": f"Input image not found: {params['input']}"}

    # Read full images
    sci_full = fits.getdata(params["input"])

    mask_full = np.zeros_like(sci_full, dtype=int)
    if params["mask"] and os.path.exists(params["mask"]):
        mask_full = fits.getdata(params["mask"]).astype(int)

    # Crop to ROI
    if params["fit_region"] is not None:
        xmin, xmax, ymin, ymax = params["fit_region"]
        sci = sci_full[ymin - 1:ymax, xmin - 1:xmax]
        mask = mask_full[ymin - 1:ymax, xmin - 1:xmax]
        region = [xmin, xmax, ymin, ymax]
    else:
        sci = sci_full
        mask = mask_full
        region = None

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(7.68, 3.84))
    info1 = render_asinh_panel(ax1, sci, mask, region=region, vmax_percentile=99.5)
    info2 = render_asinh_panel(ax2, sci, mask, region=region, vmax_percentile=99.99)

    ax1.set_title(
        f"vmax=99.5th pctl"
        f"\nasinh_a={info1['asinh_a']:.4f}; vmin={info1['vmin_sigma']:.1f}$\\sigma$"
        f"\nIsophotes: 5.0$\\sigma$ [lime]; vmax[red]"
        f"\nShaded: Masked; Focus: Central Galaxy",
        fontsize=6, pad=3)
    ax2.set_title(
        f"vmax=99.99th pctl"
        f"\nasinh_a={info2['asinh_a']:.4f}; vmin={info2['vmin_sigma']:.1f}$\\sigma$"
        f"\nIsophotes: 5.0$\\sigma$ [lime]; vmax[red]"
        f"\nShaded: Masked; Focus: Central Galaxy",
        fontsize=6, pad=3)
    plt.tight_layout()

    feedme_dir = os.path.dirname(config_file)
    base_name = os.path.splitext(os.path.basename(config_file))[0]
    output_path = os.path.join(feedme_dir, f"{base_name}_original.png")
    fig.savefig(output_path, dpi=100)
    plt.close(fig)

    return {
        "status": "success",
        "message": f"Original image rendered to {output_path}",
        "image_file": output_path,
    }

if __name__ == '__main__':
    config_file = "/home/jiangbo/GALFITS_examples/40/obj40.lyric"
    res = render_original(config_file)
    print(res)