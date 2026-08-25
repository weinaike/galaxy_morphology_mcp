"""Generate a numeric-layer candidate-ID image for the VLM input."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from astropy.io import fits

from schemas import validate


_COLORS = ("#00e5ff", "#ffcc00", "#ff4d6d", "#7cff6b", "#c084fc", "#ff9f43")


def _regions_by_band(numeric_evidence: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    regions: dict[str, list[dict[str, Any]]] = {}
    for feature in numeric_evidence.get("features", []):
        for region in feature.get("candidate_regions", []):
            band = region.get("band")
            if not isinstance(band, str):
                continue
            if not all(isinstance(region.get(key), (int, float)) for key in ("x_pix", "y_pix")):
                continue
            regions.setdefault(band, []).append(region)
    return regions


def _display_limits(data: np.ndarray, *, residual: bool) -> tuple[float, float]:
    finite = np.asarray(data, dtype=float)[np.isfinite(data)]
    if finite.size == 0:
        return (-1.0, 1.0)
    if residual:
        scale = float(np.percentile(np.abs(finite), 99.5)) or 1.0
        return -scale, scale
    return float(np.percentile(finite, 1.0)), float(np.percentile(finite, 99.5))


def _load_result_band(band: dict[str, Any]) -> tuple[np.ndarray, np.ndarray]:
    with fits.open(band["result_fits"]) as hdul:
        original = np.asarray(hdul[band["original_hdu"]].data, dtype=float)
        residual = np.asarray(hdul[band["residual_hdu"]].data, dtype=float)
    if original.ndim != 2 or residual.ndim != 2:
        raise ValueError(f"overlay requires 2-D original/residual arrays for {band['band']}")
    if original.shape != residual.shape:
        raise ValueError(f"original/residual shape mismatch for {band['band']}")
    return original, residual


def create_candidate_overlay(
    manifest: dict[str, Any],
    numeric_evidence: dict[str, Any],
    output_path: str | Path,
) -> str:
    """Render original/residual panels with numeric-issued candidate IDs.

    Coordinates are used only inside this renderer. The VLM receives the
    resulting image and candidate IDs in the prompt, never coordinate text.
    The source comparison PNG is not modified.
    """
    validate(manifest, "artifact_manifest")
    validate(numeric_evidence, "numeric_evidence")
    if manifest["round_id"] != numeric_evidence["round_id"]:
        raise ValueError("manifest and numeric evidence round_id do not match")

    bands = manifest["bands"]
    regions_by_band = _regions_by_band(numeric_evidence)
    output = Path(output_path).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)

    fig, axes = plt.subplots(
        max(len(bands), 1),
        2,
        squeeze=False,
        figsize=(12, max(3.5 * len(bands), 4.0)),
        constrained_layout=True,
    )
    fig.suptitle(
        "Numeric candidate overlay: IDs and marker positions are issued by the numeric layer",
        fontsize=13,
        fontweight="bold",
    )

    for row, band in enumerate(bands):
        original, residual = _load_result_band(band)
        xmin, xmax, ymin, ymax = band["fit_region"]
        extent = [xmin - 0.5, xmax + 0.5, ymin - 0.5, ymax + 0.5]
        band_regions = regions_by_band.get(band["band"], [])
        for column, (data, title, residual_panel) in enumerate(
            ((original, "Original", False), (residual, "Residual", True))
        ):
            ax = axes[row][column]
            vmin, vmax = _display_limits(data, residual=residual_panel)
            cmap = "seismic" if residual_panel else "gray"
            ax.imshow(
                data,
                origin="lower",
                extent=extent,
                cmap=cmap,
                vmin=vmin,
                vmax=vmax,
                interpolation="nearest",
                aspect="auto",
            )
            for region in band_regions:
                candidate_id = str(region["region_id"])
                color = _COLORS[sum(ord(char) for char in candidate_id) % len(_COLORS)]
                x_pix = float(region["x_pix"])
                y_pix = float(region["y_pix"])
                ax.scatter(
                    [x_pix],
                    [y_pix],
                    s=90,
                    facecolors="none",
                    edgecolors=color,
                    linewidths=1.8,
                    zorder=4,
                )
                ax.annotate(
                    candidate_id,
                    (x_pix, y_pix),
                    xytext=(5, 5),
                    textcoords="offset points",
                    color=color,
                    fontsize=9,
                    fontweight="bold",
                    bbox={"facecolor": "black", "alpha": 0.65, "pad": 1.5},
                    zorder=5,
                )
            ax.set_title(f"{band['band']} — {title}")
            ax.set_xlabel("X (pixels)")
            if column == 0:
                ax.set_ylabel("Y (pixels)")

    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, dpi=150, facecolor="white")
    plt.close(fig)
    return str(output)
