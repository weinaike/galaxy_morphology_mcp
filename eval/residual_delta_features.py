"""Parent/child residual morphology features for the V12.5 reward candidate."""

from __future__ import annotations

import math
from typing import Any

import numpy as np


FEATURE_NAMES = (
    "mad_distance",
    "median_abs",
    "p90_abs",
    "p99_abs",
    "tail3_fraction",
    "tail5_fraction",
    "central_abs",
    "inner_abs",
    "outer_abs",
    "central_tail3_fraction",
    "low_frequency_excess",
    "neighbor_correlation",
    "signed_imbalance",
)


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return default
    return value if math.isfinite(value) else default


def _mean_abs(values: np.ndarray) -> float:
    return float(np.mean(np.abs(values))) if values.size else 0.0


def _neighbor_correlation(z: np.ndarray, valid: np.ndarray) -> float:
    values = []
    for left, right, mask in (
        (z[:, :-1], z[:, 1:], valid[:, :-1] & valid[:, 1:]),
        (z[:-1, :], z[1:, :], valid[:-1, :] & valid[1:, :]),
    ):
        if np.count_nonzero(mask) < 100:
            continue
        x = left[mask]
        y = right[mask]
        x = x - np.mean(x)
        y = y - np.mean(y)
        denom = float(np.sqrt(np.sum(x * x) * np.sum(y * y)))
        if denom > 0:
            values.append(float(np.sum(x * y) / denom))
    return float(np.mean(values)) if values else 0.0


def _low_frequency_excess(z: np.ndarray, valid: np.ndarray) -> float:
    image = np.asarray(z, dtype=float).copy()
    fill = float(np.median(image[valid]))
    image[~valid] = fill
    image -= fill
    ny, nx = image.shape
    image *= np.outer(np.hanning(ny), np.hanning(nx))
    power = np.abs(np.fft.fftshift(np.fft.fft2(image))) ** 2
    yy, xx = np.indices(image.shape)
    radius = np.sqrt((xx - (nx - 1) / 2) ** 2 + (yy - (ny - 1) / 2) ** 2)
    rmax = float(radius.max())
    low = (radius > 0) & (radius <= 0.15 * rmax)
    high = (radius > 0.40 * rmax) & (radius <= 0.80 * rmax)
    low_power = float(np.mean(power[low]))
    high_power = float(np.mean(power[high]))
    return float(math.log1p(low_power / max(high_power, 1e-12)))


def compute_residual_badness_features(
    residual: np.ndarray,
    sigma: np.ndarray,
    mask: np.ndarray,
) -> dict[str, float]:
    """Return morphology-sensitive badness features; lower is better."""

    residual = np.asarray(residual, dtype=float)
    sigma = np.asarray(sigma, dtype=float)
    mask = np.asarray(mask)
    if residual.shape != sigma.shape or residual.shape != mask.shape:
        raise ValueError(
            f"residual/sigma/mask shape mismatch: {residual.shape}, {sigma.shape}, {mask.shape}"
        )

    valid = np.isfinite(residual) & np.isfinite(sigma) & (sigma > 0) & (mask == 0)
    if np.count_nonzero(valid) < 100:
        raise ValueError(f"too few valid residual pixels: {np.count_nonzero(valid)}")

    z = residual / sigma
    zv = z[valid]
    abs_z = np.abs(zv)
    median = float(np.median(zv))
    mad_std = 1.4826 * float(np.median(np.abs(zv - median)))

    ny, nx = z.shape
    yy, xx = np.indices(z.shape)
    radius = np.sqrt((xx - (nx - 1) / 2) ** 2 + (yy - (ny - 1) / 2) ** 2)
    valid_radius = radius[valid]
    scale = float(np.percentile(valid_radius, 95))
    central = valid & (radius <= 0.10 * scale)
    inner = valid & (radius <= 0.25 * scale)
    outer = valid & (radius >= 0.50 * scale)

    central_values = z[central]
    features = {
        "mad_distance": abs(math.log(max(mad_std, 1e-12))),
        "median_abs": float(np.median(abs_z)),
        "p90_abs": float(np.percentile(abs_z, 90)),
        "p99_abs": float(np.percentile(abs_z, 99)),
        "tail3_fraction": float(np.mean(abs_z > 3.0)),
        "tail5_fraction": float(np.mean(abs_z > 5.0)),
        "central_abs": _mean_abs(central_values),
        "inner_abs": _mean_abs(z[inner]),
        "outer_abs": _mean_abs(z[outer]),
        "central_tail3_fraction": (
            float(np.mean(np.abs(central_values) > 3.0)) if central_values.size else 0.0
        ),
        "low_frequency_excess": _low_frequency_excess(z, valid),
        "neighbor_correlation": abs(_neighbor_correlation(z, valid)),
        "signed_imbalance": abs(float(np.mean(zv))) / max(float(np.mean(abs_z)), 1e-12),
    }
    return {name: _safe_float(features[name]) for name in FEATURE_NAMES}


def compute_residual_feature_deltas(
    parent_features: dict[str, float],
    child_features: dict[str, float],
) -> dict[str, float]:
    """Return parent badness minus child badness; positive means improvement."""

    return {
        name: _safe_float(parent_features.get(name)) - _safe_float(child_features.get(name))
        for name in FEATURE_NAMES
    }


def load_residual_inputs(
    output_fits_path: str,
    sigma_path: str,
    mask_path: str,
    residual_ext: int = 3,
):
    from astropy.io import fits

    with fits.open(output_fits_path, memmap=False) as hdul:
        residual = np.asarray(hdul[residual_ext].data, dtype=float)
    sigma = np.asarray(fits.getdata(sigma_path), dtype=float)
    mask = np.asarray(fits.getdata(mask_path))
    return residual, sigma, mask
