"""Deterministic numeric measurements for component-analysis evidence.

The functions in this module report measurements and data-quality states only.
They do not name physical components or emit fitting actions.  FITS and
manifest I/O are deliberately left to a future adapter so this layer can run
in shadow mode without changing the existing workflow.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

import numpy as np
from scipy.ndimage import maximum_filter

from schemas import validate

FWHM_FACTOR = 2.0 * np.sqrt(2.0 * np.log(2.0))


@dataclass(frozen=True)
class BandArrays:
    """In-memory arrays and provenance for one band.

    ``original`` is used for source shape and central-profile measurements;
    ``residual`` is used for Fourier modes and local residual peaks.  All
    science-sized arrays must have the same two-dimensional shape.
    """

    band: str
    original: np.ndarray
    residual: np.ndarray
    sigma: np.ndarray
    psf: np.ndarray | None
    mask: np.ndarray | None = None
    center: tuple[float, float] | None = None
    source_file: str | None = None
    original_hdu: int | None = None
    residual_hdu: int | None = None
    psf_file: str | None = None
    psf_hdu: int | None = None


def _as_2d_float(array: np.ndarray, name: str) -> np.ndarray:
    result = np.asarray(array, dtype=float)
    if result.ndim != 2:
        raise ValueError(f"{name} must be a two-dimensional array")
    return result


def _matching_mask(shape: tuple[int, int], mask: np.ndarray | None) -> np.ndarray:
    if mask is None:
        return np.zeros(shape, dtype=bool)
    result = np.asarray(mask, dtype=bool)
    if result.shape != shape:
        raise ValueError("mask shape must match the science array")
    return result


def _default_center(shape: tuple[int, int]) -> tuple[float, float]:
    ny, nx = shape
    return (nx - 1) / 2.0, (ny - 1) / 2.0


def measure_weighted_moments(
    image: np.ndarray,
    *,
    mask: np.ndarray | None = None,
    center: tuple[float, float] | None = None,
) -> dict[str, Any]:
    """Measure a positive-flux centroid and second-moment ellipse.

    Negative pixels carry zero weight.  This makes the definition suitable for
    a background-subtracted source or positive residual feature and prevents a
    signed residual field from producing an invalid covariance matrix.
    """

    values = _as_2d_float(image, "image")
    excluded = _matching_mask(values.shape, mask) | ~np.isfinite(values)
    weights = np.where(excluded, 0.0, np.clip(values, 0.0, None))
    total = float(weights.sum())
    valid_pixels = int(np.count_nonzero(~excluded))
    if total <= 0 or valid_pixels == 0:
        return {
            "status": "UNAVAILABLE",
            "value": None,
            "quality_flags": ["nan_in_region"] if valid_pixels == 0 else ["low_snr"],
        }

    yy, xx = np.indices(values.shape, dtype=float)
    if center is None:
        xcen = float(np.sum(weights * xx) / total)
        ycen = float(np.sum(weights * yy) / total)
    else:
        xcen, ycen = map(float, center)

    dx = xx - xcen
    dy = yy - ycen
    covariance = np.array(
        [
            [np.sum(weights * dx * dx), np.sum(weights * dx * dy)],
            [np.sum(weights * dx * dy), np.sum(weights * dy * dy)],
        ],
        dtype=float,
    ) / total
    eigenvalues, eigenvectors = np.linalg.eigh(covariance)
    eigenvalues = np.clip(eigenvalues, 0.0, None)
    major_index = int(np.argmax(eigenvalues))
    minor_index = 1 - major_index
    major_variance = float(eigenvalues[major_index])
    minor_variance = float(eigenvalues[minor_index])
    major_vector = eigenvectors[:, major_index]
    pa_deg = float(np.degrees(np.arctan2(major_vector[1], major_vector[0])) % 180.0)
    axis_ratio = (
        float(np.sqrt(minor_variance / major_variance)) if major_variance > 0 else 1.0
    )

    return {
        "status": "AVAILABLE",
        "value": {
            "x_centroid_pix": xcen,
            "y_centroid_pix": ycen,
            "variance_major_pix2": major_variance,
            "variance_minor_pix2": minor_variance,
            "axis_ratio": axis_ratio,
            "pa_deg": pa_deg,
            "positive_flux": total,
            "valid_pixels": valid_pixels,
        },
        "quality_flags": [],
    }


def measure_azimuthal_modes(
    image: np.ndarray,
    *,
    center: tuple[float, float] | None = None,
    mask: np.ndarray | None = None,
    orders: Sequence[int] = (1, 2),
    r_min: float = 0.0,
    r_max: float | None = None,
) -> dict[str, Any]:
    """Measure normalized azimuthal Fourier amplitudes and phases.

    For a field ``I(theta) = I0 * (1 + a*cos(m*(theta-phi)))``, the returned
    amplitude approaches ``a`` for complete azimuthal coverage.  The
    normalization uses absolute flux so signed residual images remain defined.
    """

    values = _as_2d_float(image, "image")
    if any(order < 1 for order in orders):
        raise ValueError("Fourier orders must be positive integers")
    xcen, ycen = center or _default_center(values.shape)
    yy, xx = np.indices(values.shape, dtype=float)
    radius = np.hypot(xx - xcen, yy - ycen)
    theta = np.arctan2(yy - ycen, xx - xcen)
    excluded = _matching_mask(values.shape, mask) | ~np.isfinite(values)
    selected = (~excluded) & (radius >= r_min)
    if r_max is not None:
        selected &= radius <= r_max

    signal = values[selected]
    denominator = float(np.sum(np.abs(signal)))
    if denominator <= 0 or signal.size == 0:
        return {
            "status": "UNAVAILABLE",
            "value": None,
            "quality_flags": ["low_snr"],
        }

    result: dict[str, dict[str, float]] = {}
    selected_theta = theta[selected]
    for order in orders:
        coefficient = np.sum(signal * np.exp(-1j * order * selected_theta))
        amplitude = float(2.0 * np.abs(coefficient) / denominator)
        phase_deg = float((-np.degrees(np.angle(coefficient)) / order) % (360.0 / order))
        result[f"m{order}"] = {"amplitude": amplitude, "phase_deg": phase_deg}

    return {
        "status": "AVAILABLE",
        "value": result,
        "quality_flags": [],
    }


def measure_fwhm(
    image: np.ndarray,
    *,
    mask: np.ndarray | None = None,
    center: tuple[float, float] | None = None,
) -> dict[str, Any]:
    """Estimate major, minor and geometric-mean FWHM from second moments."""

    moments = measure_weighted_moments(image, mask=mask, center=center)
    if moments["status"] != "AVAILABLE":
        return moments
    value = moments["value"]
    sigma_major = np.sqrt(value["variance_major_pix2"])
    sigma_minor = np.sqrt(value["variance_minor_pix2"])
    return {
        "status": "AVAILABLE",
        "value": {
            "fwhm_major_pix": float(FWHM_FACTOR * sigma_major),
            "fwhm_minor_pix": float(FWHM_FACTOR * sigma_minor),
            "fwhm_geometric_pix": float(FWHM_FACTOR * np.sqrt(sigma_major * sigma_minor)),
        },
        "quality_flags": moments["quality_flags"],
    }


def deconvolve_fwhm(fwhm_obs: float, fwhm_psf: float) -> float:
    """Return ``sqrt(max(FWHM_obs**2 - FWHM_psf**2, 0))``."""

    if not np.isfinite(fwhm_obs) or not np.isfinite(fwhm_psf):
        raise ValueError("FWHM values must be finite")
    if fwhm_obs < 0 or fwhm_psf <= 0:
        raise ValueError("observed FWHM must be non-negative and PSF FWHM positive")
    return float(np.sqrt(max(fwhm_obs**2 - fwhm_psf**2, 0.0)))


def measure_aperture_snr(
    image: np.ndarray,
    sigma: np.ndarray,
    *,
    center: tuple[float, float] | None = None,
    radius: float,
    mask: np.ndarray | None = None,
) -> dict[str, Any]:
    """Measure summed aperture signal divided by quadrature sigma."""

    values = _as_2d_float(image, "image")
    errors = _as_2d_float(sigma, "sigma")
    if errors.shape != values.shape:
        raise ValueError("sigma shape must match image")
    if radius <= 0:
        raise ValueError("radius must be positive")
    xcen, ycen = center or _default_center(values.shape)
    yy, xx = np.indices(values.shape, dtype=float)
    aperture = np.hypot(xx - xcen, yy - ycen) <= radius
    excluded = (
        _matching_mask(values.shape, mask)
        | ~np.isfinite(values)
        | ~np.isfinite(errors)
        | (errors <= 0)
    )
    selected = aperture & ~excluded
    if not np.any(selected):
        return {
            "status": "UNAVAILABLE",
            "value": None,
            "quality_flags": ["nan_in_region"],
        }
    signal = float(np.sum(values[selected]))
    noise = float(np.sqrt(np.sum(np.square(errors[selected]))))
    return {
        "status": "AVAILABLE",
        "value": {
            "snr": signal / noise,
            "signal": signal,
            "noise": noise,
            "valid_pixels": int(np.count_nonzero(selected)),
        },
        "quality_flags": [],
    }


def detect_local_peaks(
    image: np.ndarray,
    sigma: np.ndarray,
    *,
    band: str,
    mask: np.ndarray | None = None,
    threshold_snr: float = 5.0,
    min_distance: int = 2,
    center: tuple[float, float] | None = None,
    center_exclusion_radius: float = 0.0,
    max_peaks: int = 50,
) -> list[dict[str, Any]]:
    """Detect deterministic mask-aware local maxima in a per-pixel SNR map."""

    values = _as_2d_float(image, "image")
    errors = _as_2d_float(sigma, "sigma")
    if errors.shape != values.shape:
        raise ValueError("sigma shape must match image")
    if min_distance < 1 or max_peaks < 1:
        raise ValueError("min_distance and max_peaks must be positive")

    excluded = (
        _matching_mask(values.shape, mask)
        | ~np.isfinite(values)
        | ~np.isfinite(errors)
        | (errors <= 0)
    )
    snr = np.full(values.shape, -np.inf, dtype=float)
    np.divide(values, errors, out=snr, where=~excluded)
    window = 2 * min_distance + 1
    local_max = maximum_filter(snr, size=window, mode="constant", cval=-np.inf)
    selected = (snr >= threshold_snr) & (snr == local_max) & ~excluded

    if center is not None and center_exclusion_radius > 0:
        yy, xx = np.indices(values.shape, dtype=float)
        xcen, ycen = center
        selected &= np.hypot(xx - xcen, yy - ycen) > center_exclusion_radius

    y_coords, x_coords = np.nonzero(selected)
    ranked = sorted(
        zip(x_coords, y_coords, strict=True),
        key=lambda point: (-snr[point[1], point[0]], point[1], point[0]),
    )[:max_peaks]
    return [
        {
            "region_id": f"candidate_{index}",
            "band": band,
            "x_pix": float(x),
            "y_pix": float(y),
            "radius_pix": float(min_distance),
            "ra_deg": None,
            "dec_deg": None,
            "local_snr": float(snr[y, x]),
            "detected_in_bands": [band],
        }
        for index, (x, y) in enumerate(ranked, start=1)
    ]


def _feature(
    feature_id: str,
    name: str,
    measurement: dict[str, Any],
    *,
    band: str,
    file: str | None,
    hdu: int | None,
    region: str,
) -> dict[str, Any]:
    return {
        "feature_id": feature_id,
        "name": name,
        "status": measurement["status"],
        "value": measurement.get("value"),
        "source": {
            "band": band,
            "file": file,
            "hdu": hdu,
            "frame": "pixel",
            "region": region,
        },
        "quality_flags": measurement.get("quality_flags", []),
    }


def extract_numeric_evidence(
    *,
    round_id: str,
    manifest_ref: str,
    bands: Sequence[BandArrays],
    central_aperture_scale: float = 1.0,
    peak_snr_threshold: float = 5.0,
) -> dict[str, Any]:
    """Build schema-valid numeric evidence from in-memory per-band arrays.

    This is the shadow-mode calculation entry point.  It intentionally does
    not read the manifest or FITS files; a later workflow adapter can construct
    ``BandArrays`` after performing the manifest validation already frozen in
    the artifact schema.
    """

    if not round_id or not manifest_ref:
        raise ValueError("round_id and manifest_ref must be non-empty")
    if not bands:
        raise ValueError("at least one band is required")

    features: list[dict[str, Any]] = []
    band_quality: list[dict[str, Any]] = []
    for band_data in bands:
        original = _as_2d_float(band_data.original, "original")
        residual = _as_2d_float(band_data.residual, "residual")
        sigma = _as_2d_float(band_data.sigma, "sigma")
        if residual.shape != original.shape or sigma.shape != original.shape:
            raise ValueError("original, residual and sigma shapes must match")
        mask = _matching_mask(original.shape, band_data.mask)
        center = band_data.center or _default_center(original.shape)
        finite = np.isfinite(original) & np.isfinite(residual) & np.isfinite(sigma)
        valid = finite & ~mask & (sigma > 0)
        valid_fraction = float(np.count_nonzero(valid) / valid.size)
        mask_fraction = float(np.count_nonzero(mask) / mask.size)

        psf_fwhm: float | None = None
        psf_measurement: dict[str, Any]
        if band_data.psf is None:
            psf_measurement = {
                "status": "UNAVAILABLE",
                "value": None,
                "quality_flags": ["psf_missing"],
            }
        else:
            psf_measurement = measure_fwhm(band_data.psf)
            if psf_measurement["status"] == "AVAILABLE":
                psf_fwhm = psf_measurement["value"]["fwhm_geometric_pix"]

        source_moments = measure_weighted_moments(original, mask=mask)
        source_fwhm = measure_fwhm(original, mask=mask, center=center)
        residual_modes = measure_azimuthal_modes(residual, mask=mask, center=center)
        aperture_radius = (
            central_aperture_scale * psf_fwhm if psf_fwhm is not None else 1.0
        )
        central_snr = measure_aperture_snr(
            residual,
            sigma,
            center=center,
            radius=aperture_radius,
            mask=mask,
        )

        prefix = band_data.band
        features.extend(
            [
                _feature(
                    f"{prefix}_source_moments",
                    "source_second_moments",
                    source_moments,
                    band=prefix,
                    file=band_data.source_file,
                    hdu=band_data.original_hdu,
                    region="source",
                ),
                _feature(
                    f"{prefix}_source_fwhm",
                    "source_fwhm",
                    source_fwhm,
                    band=prefix,
                    file=band_data.source_file,
                    hdu=band_data.original_hdu,
                    region="central",
                ),
                _feature(
                    f"{prefix}_psf_fwhm",
                    "psf_fwhm",
                    psf_measurement,
                    band=prefix,
                    file=band_data.psf_file,
                    hdu=band_data.psf_hdu,
                    region="psf",
                ),
                _feature(
                    f"{prefix}_residual_fourier_modes",
                    "residual_fourier_modes",
                    residual_modes,
                    band=prefix,
                    file=band_data.source_file,
                    hdu=band_data.residual_hdu,
                    region="fit_region",
                ),
                _feature(
                    f"{prefix}_central_aperture_snr",
                    "central_aperture_snr",
                    central_snr,
                    band=prefix,
                    file=band_data.source_file,
                    hdu=band_data.residual_hdu,
                    region="central_psf_aperture",
                ),
            ]
        )

        peaks = detect_local_peaks(
            residual,
            sigma,
            band=prefix,
            mask=mask,
            threshold_snr=peak_snr_threshold,
            center=center,
            center_exclusion_radius=aperture_radius,
        )
        peak_feature = {
            "feature_id": f"{prefix}_residual_local_peaks",
            "name": "residual_local_peaks",
            "status": "AVAILABLE",
            "value": len(peaks),
            "source": {
                "band": prefix,
                "file": band_data.source_file,
                "hdu": band_data.residual_hdu,
                "frame": "pixel",
                "region": "fit_region",
            },
            "quality_flags": [],
            "candidate_regions": peaks,
        }
        features.append(peak_feature)

        snr_value = (
            central_snr["value"]["snr"] if central_snr["status"] == "AVAILABLE" else None
        )
        undersampled = psf_fwhm is not None and psf_fwhm < 2.0
        reasons: list[str] = []
        if psf_fwhm is None:
            reasons.append("PSF unavailable")
        if undersampled:
            reasons.append("PSF FWHM < 2 px")
        if valid_fraction < 0.5:
            reasons.append("valid pixel fraction < 0.5")
        band_quality.append(
            {
                "band": prefix,
                "passed": not reasons,
                "psf_available": psf_fwhm is not None,
                "psf_undersampled": undersampled,
                "psf_fwhm_pix": psf_fwhm,
                "central_snr": snr_value,
                "valid_pixel_fraction": valid_fraction,
                "mask_fraction": mask_fraction,
                "wcs_valid": None,
                "fit_succeeded": None,
                "reasons": reasons,
            }
        )

    artifact = {
        "schema_version": "1.0",
        "round_id": round_id,
        "manifest_ref": manifest_ref,
        "algorithm_versions": {
            "moments": "weighted-moments@v1",
            "fourier": "azimuthal-dft@v1",
            "local_peaks": "maximum-filter@v1",
        },
        "features": features,
        "band_quality": band_quality,
    }
    validate(artifact, "numeric_evidence")
    return artifact
