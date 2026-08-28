"""Fixed-region residual noise-likeness features for offline reward audits."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import math

import numpy as np

FEATURE_NAMES = (
    "sky_acf_excess",
    "sky_blob_area_excess",
    "sky_blob_significance_excess",
)


@dataclass(frozen=True)
class RegionConfig:
    roi_radius_fraction: float = 0.28
    sky_inner_fraction: float = 0.36
    sky_outer_fraction: float = 0.48
    blob_sigma_threshold: float = 3.0
    sky_abs_quantile: float = 0.997
    acf_lags: tuple[int, ...] = (1, 2, 4, 8)
    min_region_pixels: int = 100

    def validate(self) -> None:
        if not 0 < self.roi_radius_fraction < self.sky_inner_fraction:
            raise ValueError("ROI must lie inside the sky annulus")
        if not self.sky_inner_fraction < self.sky_outer_fraction <= 0.5:
            raise ValueError("invalid sky annulus")
        if not 0.5 < self.sky_abs_quantile < 1.0:
            raise ValueError("invalid sky_abs_quantile")
        if self.blob_sigma_threshold <= 0 or self.min_region_pixels < 2:
            raise ValueError("invalid threshold or minimum region size")
        if not self.acf_lags or any(lag <= 0 for lag in self.acf_lags):
            raise ValueError("acf_lags must contain positive integers")


def fixed_regions(shape: tuple[int, int], config: RegionConfig | None = None):
    """Return child-independent circular ROI and sky-annulus masks."""

    config = config or RegionConfig()
    config.validate()
    if len(shape) != 2 or min(shape) < 8:
        raise ValueError(f"invalid image shape: {shape}")
    ny, nx = shape
    yy, xx = np.indices(shape)
    radius = np.sqrt((xx - (nx - 1) / 2) ** 2 + (yy - (ny - 1) / 2) ** 2)
    scale = float(min(shape))
    roi = radius <= config.roi_radius_fraction * scale
    sky = (radius >= config.sky_inner_fraction * scale) & (
        radius <= config.sky_outer_fraction * scale
    )
    return roi, sky


def _safe_float(value: float) -> float:
    value = float(value)
    return value if math.isfinite(value) else 0.0


def _lag_correlation(z: np.ndarray, valid: np.ndarray, lag: int) -> float:
    values = []
    for left, right, keep in (
        (z[:, :-lag], z[:, lag:], valid[:, :-lag] & valid[:, lag:]),
        (z[:-lag, :], z[lag:, :], valid[:-lag, :] & valid[lag:, :]),
    ):
        if np.count_nonzero(keep) < 20:
            continue
        x = left[keep] - np.mean(left[keep])
        y = right[keep] - np.mean(right[keep])
        denom = float(np.sqrt(np.sum(x * x) * np.sum(y * y)))
        if denom > 0:
            values.append(float(np.sum(x * y) / denom))
    return float(np.mean(values)) if values else 0.0


def _acf_profile(z: np.ndarray, region: np.ndarray, lags: tuple[int, ...]):
    return np.asarray(
        [_lag_correlation(z, region, lag) for lag in lags], dtype=float
    )


def _largest_blob(binary: np.ndarray, weights: np.ndarray) -> tuple[int, float]:
    """Return area and summed excess significance of the largest 8-neighbour blob."""

    seen = np.zeros(binary.shape, dtype=bool)
    best_area = 0
    best_weight = 0.0
    ny, nx = binary.shape
    for y, x in zip(*np.nonzero(binary)):
        if seen[y, x]:
            continue
        queue = deque([(int(y), int(x))])
        seen[y, x] = True
        area = 0
        weight = 0.0
        while queue:
            cy, cx = queue.popleft()
            area += 1
            weight += float(weights[cy, cx])
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    if not (dx or dy):
                        continue
                    yy, xx = cy + dy, cx + dx
                    if (
                        0 <= yy < ny
                        and 0 <= xx < nx
                        and binary[yy, xx]
                        and not seen[yy, xx]
                    ):
                        seen[yy, xx] = True
                        queue.append((yy, xx))
        if area > best_area or (area == best_area and weight > best_weight):
            best_area, best_weight = area, weight
    return best_area, best_weight


def compute_noise_likeness_badness(
    residual: np.ndarray,
    sigma: np.ndarray,
    mask: np.ndarray,
    config: RegionConfig | None = None,
) -> dict[str, float]:
    """Compute fixed-region residual badness; lower values are better."""

    config = config or RegionConfig()
    config.validate()
    residual = np.asarray(residual, dtype=float)
    sigma = np.asarray(sigma, dtype=float)
    mask = np.asarray(mask)
    if residual.shape != sigma.shape or residual.shape != mask.shape:
        raise ValueError("residual/sigma/mask shape mismatch")

    roi, sky = fixed_regions(residual.shape, config)
    valid = np.isfinite(residual) & np.isfinite(sigma) & (sigma > 0) & (mask == 0)
    roi &= valid
    sky &= valid
    if min(np.count_nonzero(roi), np.count_nonzero(sky)) < config.min_region_pixels:
        raise ValueError("too few valid pixels in fixed ROI or sky region")

    z = residual / sigma
    sky_center = float(np.median(z[sky]))
    centered = z - sky_center
    roi_acf = _acf_profile(centered, roi, config.acf_lags)
    sky_acf = _acf_profile(centered, sky, config.acf_lags)
    acf_excess = float(
        np.mean(np.maximum(np.abs(roi_acf) - np.abs(sky_acf), 0.0))
    )

    threshold = max(
        config.blob_sigma_threshold,
        float(np.quantile(np.abs(centered[sky]), config.sky_abs_quantile)),
    )
    excess = np.maximum(np.abs(centered) - threshold, 0.0)
    roi_area, roi_weight = _largest_blob((excess > 0) & roi, excess)
    sky_area, sky_weight = _largest_blob((excess > 0) & sky, excess)
    roi_n = float(np.count_nonzero(roi))
    sky_n = float(np.count_nonzero(sky))
    return {
        "sky_acf_excess": _safe_float(acf_excess),
        "sky_blob_area_excess": _safe_float(
            max(roi_area / roi_n - sky_area / sky_n, 0.0)
        ),
        "sky_blob_significance_excess": _safe_float(
            max(roi_weight / roi_n - sky_weight / sky_n, 0.0)
        ),
    }


def compute_noise_likeness_deltas(
    parent: dict[str, float], child: dict[str, float]
) -> dict[str, float]:
    """Return parent badness minus child badness; positive means improvement."""

    return {
        name: _safe_float(parent.get(name, 0.0) - child.get(name, 0.0))
        for name in FEATURE_NAMES
    }
