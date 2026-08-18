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
from scipy.ndimage import map_coordinates, maximum_filter

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
    pixscale_arcsec: float | None = None
    pa_v3_deg: float | None = None
    wcs: Any | None = None


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


def measure_directional_harmonic_alignment(
    image: np.ndarray,
    sigma: np.ndarray,
    template: np.ndarray,
    *,
    candidate_radius: float,
    image_center: tuple[float, float] | None = None,
    template_center: tuple[float, float] | None = None,
    mask: np.ndarray | None = None,
    order: int = 6,
    inner_radius_fraction: float = 0.6,
    outer_radius_fraction: float = 1.1,
    min_template_amplitude: float = 0.1,
    min_image_amplitude: float = 0.02,
    min_harmonic_snr: float = 5.0,
    min_coverage: float = 0.6,
    phase_tolerance_deg: float = 7.5,
) -> dict[str, Any]:
    """Compare one image harmonic with a same-pixel-frame template.

    The phase comparison treats positive and negative image residuals as the
    same physical axes.  A sign inversion therefore changes neither the
    alignment result nor the reported axis-phase separation.
    """

    values = _as_2d_float(image, "image")
    errors = _as_2d_float(sigma, "sigma")
    reference = _as_2d_float(template, "template")
    if errors.shape != values.shape:
        raise ValueError("sigma shape must match image")
    if candidate_radius <= 0 or order < 1:
        raise ValueError("candidate_radius and order must be positive")
    if not 0 < inner_radius_fraction < outer_radius_fraction:
        raise ValueError("invalid annulus radius fractions")
    if not 0 < min_coverage <= 1:
        raise ValueError("min_coverage must be in (0, 1]")

    inner_radius = inner_radius_fraction * candidate_radius
    outer_radius = outer_radius_fraction * candidate_radius
    x_image, y_image = image_center or _default_center(values.shape)

    finite_template = np.isfinite(reference)
    border = np.concatenate(
        (
            reference[0, finite_template[0]],
            reference[-1, finite_template[-1]],
            reference[finite_template[:, 0], 0],
            reference[finite_template[:, -1], -1],
        )
    )
    background = float(np.median(border)) if border.size else 0.0
    template_signal = np.where(
        finite_template,
        np.clip(reference - background, 0.0, None),
        0.0,
    )
    if template_center is None:
        y_template, x_template = np.unravel_index(
            int(np.argmax(template_signal)),
            template_signal.shape,
        )
        x_template, y_template = float(x_template), float(y_template)
    else:
        x_template, y_template = map(float, template_center)

    def annulus_geometry(
        shape: tuple[int, int],
        center: tuple[float, float],
    ) -> tuple[np.ndarray, np.ndarray]:
        yy, xx = np.indices(shape, dtype=float)
        radius = np.hypot(xx - center[0], yy - center[1])
        theta = np.arctan2(yy - center[1], xx - center[0])
        return (radius >= inner_radius) & (radius <= outer_radius), theta

    image_annulus, image_theta = annulus_geometry(
        values.shape,
        (x_image, y_image),
    )
    excluded = (
        _matching_mask(values.shape, mask)
        | ~np.isfinite(values)
        | ~np.isfinite(errors)
        | (errors <= 0)
    )
    image_selected = image_annulus & ~excluded
    coverage = float(
        np.count_nonzero(image_selected) / max(np.count_nonzero(image_annulus), 1)
    )

    template_annulus, template_theta = annulus_geometry(
        reference.shape,
        (x_template, y_template),
    )
    template_selected = template_annulus & finite_template

    def mode(
        signal: np.ndarray,
        theta: np.ndarray,
        selected: np.ndarray,
    ) -> tuple[float, float, complex] | None:
        selected_signal = signal[selected]
        denominator = float(np.sum(np.abs(selected_signal)))
        if selected_signal.size == 0 or denominator <= 0:
            return None
        coefficient = np.sum(
            selected_signal * np.exp(-1j * order * theta[selected])
        )
        amplitude = float(2.0 * np.abs(coefficient) / denominator)
        phase = float(
            (-np.degrees(np.angle(coefficient)) / order) % (360.0 / order)
        )
        return amplitude, phase, coefficient

    template_mode = mode(template_signal, template_theta, template_selected)
    image_mode = mode(values, image_theta, image_selected)
    if template_mode is None or image_mode is None:
        return {
            "status": "UNAVAILABLE",
            "value": None,
            "quality_flags": ["low_snr"],
        }

    template_amplitude, template_phase, _ = template_mode
    image_amplitude, image_phase, image_coefficient = image_mode
    harmonic_noise = float(np.sqrt(np.sum(np.square(errors[image_selected]))))
    harmonic_snr = (
        float(np.abs(image_coefficient) / harmonic_noise)
        if harmonic_noise > 0
        else 0.0
    )
    axis_period = 180.0 / order
    phase_delta = float(
        abs((image_phase - template_phase + axis_period / 2.0) % axis_period
            - axis_period / 2.0)
    )

    flags: list[str] = []
    if coverage < min_coverage:
        flags.append("high_mask_fraction")
    if (
        template_amplitude < min_template_amplitude
        or image_amplitude < min_image_amplitude
        or harmonic_snr < min_harmonic_snr
    ):
        flags.append("low_snr")
    evaluated = not flags
    return {
        "status": "AVAILABLE",
        "value": {
            "evaluated": evaluated,
            "aligned": phase_delta <= phase_tolerance_deg if evaluated else None,
            "order": order,
            "inner_radius_pix": float(inner_radius),
            "outer_radius_pix": float(outer_radius),
            "template_amplitude": template_amplitude,
            "image_amplitude": image_amplitude,
            "template_phase_deg": template_phase,
            "image_phase_deg": image_phase,
            "phase_delta_deg": phase_delta,
            "phase_tolerance_deg": float(phase_tolerance_deg),
            "harmonic_snr": harmonic_snr,
            "coverage_fraction": coverage,
        },
        "quality_flags": flags,
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


def measure_psf_fwhm(
    image: np.ndarray,
    *,
    angle_count: int = 64,
    radial_step: float = 0.05,
) -> dict[str, Any]:
    """Measure the PSF core FWHM from half-maximum radial crossings.

    A PSF's full-image second moment is dominated by diffraction wings and is
    therefore not a core-resolution measure.  This estimator subtracts a
    border background, samples the profile from the peak along multiple
    azimuths, and summarizes the first half-maximum crossing.
    """

    values = _as_2d_float(image, "image")
    finite = np.isfinite(values)
    if not np.any(finite):
        return {
            "status": "UNAVAILABLE",
            "value": None,
            "quality_flags": ["nan_in_region"],
        }
    border = np.concatenate(
        (
            values[0, finite[0]],
            values[-1, finite[-1]],
            values[finite[:, 0], 0],
            values[finite[:, -1], -1],
        )
    )
    background = float(np.median(border)) if border.size else 0.0
    signal = np.where(finite, values - background, 0.0)
    y_peak, x_peak = np.unravel_index(int(np.argmax(signal)), signal.shape)
    peak = float(signal[y_peak, x_peak])
    if peak <= 0 or angle_count < 8 or radial_step <= 0:
        return {
            "status": "UNAVAILABLE",
            "value": None,
            "quality_flags": ["low_snr"],
        }

    half = peak / 2.0
    max_radius = float(
        max(
            np.hypot(x_peak, y_peak),
            np.hypot(signal.shape[1] - 1 - x_peak, y_peak),
            np.hypot(x_peak, signal.shape[0] - 1 - y_peak),
            np.hypot(signal.shape[1] - 1 - x_peak, signal.shape[0] - 1 - y_peak),
        )
    )
    radii = np.arange(0.0, max_radius + radial_step, radial_step)
    crossings: list[float] = []
    for angle in np.linspace(0.0, np.pi, angle_count, endpoint=False):
        for direction in (1.0, -1.0):
            x = x_peak + direction * radii * np.cos(angle)
            y = y_peak + direction * radii * np.sin(angle)
            profile = map_coordinates(
                signal,
                [y, x],
                order=1,
                mode="constant",
                cval=0.0,
            )
            below = np.flatnonzero(profile <= half)
            if below.size == 0:
                continue
            index = int(below[0])
            if index == 0:
                crossings.append(0.0)
                continue
            previous = float(profile[index - 1])
            current = float(profile[index])
            denominator = previous - current
            fraction = (previous - half) / denominator if denominator > 0 else 0.0
            crossings.append(float(radii[index - 1] + fraction * radial_step))

    widths = 2.0 * np.asarray(crossings, dtype=float)
    widths = widths[np.isfinite(widths) & (widths > 0)]
    if widths.size < max(8, angle_count // 4):
        return {
            "status": "UNAVAILABLE",
            "value": None,
            "quality_flags": ["low_snr"],
        }
    minor, major = np.percentile(widths, [25.0, 75.0])
    return {
        "status": "AVAILABLE",
        "value": {
            "fwhm_major_pix": float(major),
            "fwhm_minor_pix": float(minor),
            "fwhm_geometric_pix": float(np.sqrt(major * minor)),
            "center_x_pix": float(x_peak),
            "center_y_pix": float(y_peak),
        },
        "quality_flags": [],
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
            psf_measurement = measure_psf_fwhm(band_data.psf)
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
        for peak in peaks:
            # IDs are global within one evidence artifact; band-local IDs would
            # make VLM targets ambiguous when several bands detect candidate_1.
            peak["region_id"] = f"{prefix}:{peak['region_id']}"
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
