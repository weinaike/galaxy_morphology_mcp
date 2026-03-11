#!/usr/bin/env python3
"""
Multi-threshold visualization tool for astronomical images.

Generates a grid of image plots with different sigma thresholds
to help visualize structures at various intensity levels.
"""

import os
from typing import Annotated, Literal

import matplotlib.pyplot as plt
import numpy as np
from astropy.io import fits
from astropy.stats import sigma_clipped_stats
from astropy.visualization import simple_norm
from matplotlib.gridspec import GridSpec


def show_image(
    img: np.ndarray,
    mask: np.ndarray | None = None,
    fig: plt.Figure | None = None,
    gridspec: GridSpec | None = None,
    nmin: float = 3,
    nmax: float = 10,
    type: Literal["mask", "sci"] = "sci",
    stretch: Literal['linear', 'sqrt', 'power', 'log', 'asinh', 'sinh'] = "asinh",
    position: tuple[int, int] = (0, 0),
    title: str | None = None,
    cmap: str = "grey",
    single_size: tuple[float, float] = (6, 6),
    colorbar: bool = False,
    show_mask: bool = False
) -> plt.Axes:
    """
    Display an astronomical image with optional stretching and normalization.

    Args:
        img: Image data array
        mask: Optional mask array for sigma-clipped statistics
        fig: Matplotlib figure object
        gridspec: GridSpec for subplot positioning
        nmin: Lower threshold in standard deviations
        nmax: Upper threshold in standard deviations
        type: Display type ('mask' or 'sci')
        stretch: Stretch type for visualization
        position: Grid position (row, col)
        title: Plot title
        cmap: Colormap name
        single_size: Figure size if creating new figure
        colorbar: Whether to add colorbar

    Returns:
        Matplotlib axes object
    """
    assert type in ["mask", "sci"], "wrong type"
    assert stretch in ['linear', 'sqrt', 'power', 'log', 'asinh', 'sinh'], "wrong stretch"

    if fig is None or gridspec is None:
        fig = plt.figure(figsize=single_size)
        gridspec = GridSpec(1, 1, figure=fig)

    ax = fig.add_subplot(gridspec[position[0], position[1]])

    if type == "sci":
        if stretch == "log":
            im = ax.imshow(np.log10(img), origin="lower", cmap=cmap)
        else:
            mean, median, std = sigma_clipped_stats(img, mask=mask)
            norm = simple_norm(img, stretch, vmin=median - nmin * std, vmax=median + nmax * std)
            im = ax.imshow(img, norm=norm, origin="lower", cmap=cmap)

        if show_mask and mask is not None:
            # Create a red overlay for the mask
            mask_overlay = np.zeros(img.shape + (4,)) # RGBA
            mask_overlay[mask > 0] = [1, 0, 0, 0.3]   # Red with 0.3 alpha
            ax.imshow(mask_overlay, origin="lower")

    else:
        im = ax.imshow(img, origin="lower", cmap=cmap)

    ax.set_facecolor("#f8f9fb")
    for spine in ax.spines.values():
        spine.set_linewidth(1.2)
        spine.set_color("#212121")

    ax.tick_params(axis="both", which="major", direction="in", top=True, right=True,
                   labelsize=10, length=6, width=1)
    ax.tick_params(axis="both", which="minor", direction="in", top=True, right=True,
                   labelsize=8, length=3, width=0.8)
    ax.minorticks_on()

    if colorbar:
        cbar = plt.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
        cbar.ax.tick_params(labelsize=9, width=0.8, length=4)

    ax.set_title(f"{title}" if title is not None else "", fontsize=14, fontweight="semibold", pad=8)

    return ax


def multi_thresh_plot(
    sci_image: Annotated[str, "Path to the science image FITS file"],
    mask_path: Annotated[str, "Path to the mask FITS file (same dimensions as sci_image)"],
    output_path: Annotated[str, "Output path for the generated multi-threshold plot PNG file"] = "./multi_threshold.png",
    stretch: Annotated[Literal['linear', 'sqrt', 'power', 'log', 'asinh', 'sinh'], "Stretch type for image visualization"] = "asinh",
    dpi: Annotated[int, "Resolution of the output image in dots per inch"] = 200,
    band_name: Annotated[str, "Band/filter name for display in plot title (e.g., 'F200W', 'F115W')"] = "F200W",
) -> Annotated[str, "Path to the generated multi-threshold plot PNG file"]:
    """
    Generate a multi-threshold visualization grid for astronomical images.

    This function creates a 2x5 grid of image plots, each displaying the same
    data with different sigma thresholds. This helps visualize structures at
    various intensity levels, from low-surface-brightness features to bright cores.

    Process:
    1. Load science image and mask from FITS files
    2. Calculate statistics (mean, median, std) with sigma clipping
    3. Determine upper threshold based on 99th percentile
    4. Generate 10 logarithmically-spaced threshold values
    5. Create a grid of plots showing the image at each threshold

    Args:
        sci_image: Path to the science image FITS file
        mask_path: Path to the mask FITS file (must have same dimensions as sci_image)
        output_path: Output path for the generated PNG plot (default: "./multi_threshold.png")
        stretch: Stretch type for visualization (default: "asinh")
        dpi: Resolution of output image in DPI (default: 200)
        band_name: Band/filter name for display in plot title (default: "F277W")

    Returns:
        Path to the generated multi-threshold plot PNG file

    Example:
        >>> output = multi_thresh_plot(
        ...     sci_image="data/galaxy_f277w.fits",
        ...     mask_path="data/mask.fits",
        ...     output_path="output/multi_thresh.png",
        ...     stretch="asinh",
        ...     band_name="F277W"
        ... )
        >>> print(f"Plot saved to: {output}")
    """
    # Load image and mask
    with fits.open(sci_image) as hdul, fits.open(mask_path) as hdul_mask:
        img = hdul[0].data
        mask = hdul_mask[0].data

    # Calculate statistics with sigma clipping
    mean, median, std = sigma_clipped_stats(img, mask=mask)

    # Determine upper threshold based on 99th percentile
    upper_value = np.percentile(img, 99)
    upper_thresh = (upper_value - median) / std

    # Generate 10 logarithmically-spaced thresholds
    # thresholds = np.logspace(np.log10(3), np.log10(upper_thresh), 10)
    thresholds = np.linspace(3, upper_thresh, 10)

    # Create figure with 2 rows and 5 columns
    size = 4
    n_thresh = len(thresholds)
    fig = plt.figure(figsize=(n_thresh / 2 * size, 2 * size))
    gridspec = GridSpec(2, int(n_thresh / 2), figure=fig)

    # Add overall title with band name
    fig.suptitle(f"Multi-Threshold Visualization - {band_name} Band",
                 fontsize=16, fontweight='bold', y=0.98)

    # Plot each threshold
    for i in range(n_thresh):
        if i < n_thresh / 2:
            show_image(
                img, mask=mask, fig=fig, gridspec=gridspec,
                position=(0, i), stretch=stretch, nmax=thresholds[i],
                title=f"{thresholds[i]:.1f}σ", show_mask = True
            )
        else:
            show_image(
                img, mask=mask, fig=fig, gridspec=gridspec,
                position=(1, i - int(n_thresh / 2)), stretch=stretch,
                nmax=thresholds[i], title=f"{thresholds[i]:.1f}σ", show_mask = True
            )

    plt.tight_layout(rect=[0, 0, 1, 0.96])  # Make room for suptitle
    plt.savefig(output_path, dpi=dpi, bbox_inches='tight')
    plt.close()

    return output_path


if __name__ == "__main__":
    # Example usage
    import sys

    if len(sys.argv) < 3:
        print("Usage: python multi_thresh_plot.py <sci_image> <mask_path> [output_path] [stretch]")
        print("Example: python multi_thresh_plot.py galaxy.fits mask.png output.png asinh")
        sys.exit(1)

    sci_img = sys.argv[1]
    mask_p = sys.argv[2]
    output = sys.argv[3] if len(sys.argv) > 3 else "./multi_threshold.png"
    str_type = sys.argv[4] if len(sys.argv) > 4 else "asinh"

    result = multi_thresh_plot(sci_img, mask_p, output, str_type)
    print(f"Multi-threshold plot saved to: {result}")