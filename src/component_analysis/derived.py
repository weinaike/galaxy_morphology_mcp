"""Rule-facing numeric facts derived from images, WCS and fit parameters."""

from __future__ import annotations

import hashlib
import warnings
from copy import deepcopy
from typing import Any, Mapping, MutableMapping, Sequence

import numpy as np

from schemas import validate

from .numeric import (
    BandArrays,
    measure_aperture_snr,
    measure_azimuthal_modes,
    measure_directional_harmonic_alignment,
    measure_fwhm,
    measure_psf_fwhm,
    measure_weighted_moments,
)


def _measurement(
    value: Any = None,
    *,
    status: str = "AVAILABLE",
    quality_flags: Sequence[str] = (),
) -> dict[str, Any]:
    return {
        "status": status,
        "value": value if status == "AVAILABLE" else None,
        "quality_flags": list(quality_flags),
    }


def _column(table: Any, name: str) -> np.ndarray:
    try:
        values = table[name]
    except (KeyError, TypeError) as exc:
        raise ValueError(f"isophote table is missing {name}") from exc
    return np.asarray(values, dtype=float)


def _pa_difference(first: float, second: float) -> float:
    return float(abs((first - second + 90.0) % 180.0 - 90.0))


def _pa_scatter(values: np.ndarray) -> float:
    doubled = np.unwrap(np.radians(2.0 * values))
    return float(np.degrees(np.std(doubled)) / 2.0)


def summarize_isophote_profile(
    table: Any,
    *,
    psf_fwhm: float,
    bar_result: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Summarize an isophote table without making a component decision."""

    if not np.isfinite(psf_fwhm) or psf_fwhm <= 0:
        raise ValueError("psf_fwhm must be finite and positive")
    sma = _column(table, "sma_pix")
    eps = _column(table, "eps")
    pa = _column(table, "pa_deg")
    x0 = _column(table, "x0_pix")
    y0 = _column(table, "y0_pix")
    valid = (
        np.isfinite(sma)
        & np.isfinite(eps)
        & np.isfinite(pa)
        & np.isfinite(x0)
        & np.isfinite(y0)
    )
    sma, eps, pa, x0, y0 = (
        values[valid] for values in (sma, eps, pa, x0, y0)
    )
    if sma.size < 5:
        return _measurement(status="UNAVAILABLE", quality_flags=("other",))

    order = np.argsort(sma)
    sma, eps, pa, x0, y0 = (
        values[order] for values in (sma, eps, pa, x0, y0)
    )
    outer_start = max(0, int(np.floor(2.0 * sma.size / 3.0)))
    outer_q = 1.0 - eps[outer_start:]
    outer_pa = pa[outer_start:]
    outer_geometry = {
        "pa_scatter_deg": _pa_scatter(outer_pa),
        "q_range": float(np.ptp(outer_q)),
        "center_scatter_pix": float(
            np.hypot(np.std(x0[outer_start:]), np.std(y0[outer_start:]))
        ),
    }

    bar_profile = None
    if bar_result and bar_result.get("bar_detected") is True:
        peak_index = bar_result.get("peak_idx")
        outer_index = bar_result.get("outer_idx")
        if isinstance(peak_index, (int, np.integer)) and 0 <= peak_index < eps.size:
            peak_sma = float(bar_result.get("peak_sma_pix", sma[peak_index]))
            peak_eps = float(bar_result.get("e_max", eps[peak_index]))
            ellipticity_drop = 0.0
            outer_pa_change = 0.0
            if isinstance(outer_index, (int, np.integer)) and 0 <= outer_index < eps.size:
                ellipticity_drop = float(peak_eps - eps[outer_index])
                outer_pa_change = _pa_difference(
                    float(bar_result.get("bar_pa_mean", pa[peak_index])),
                    float(pa[outer_index]),
                )
            bar_profile = {
                "ellipticity_peak": peak_eps,
                "pa_scatter_deg": float(bar_result.get("bar_pa_var", np.inf)),
                "bar_pa_deg": float(bar_result.get("bar_pa_mean", pa[peak_index])),
                "scale_pix": peak_sma,
                "scale_psf_ratio": peak_sma / psf_fwhm,
                "outer_ellipticity_drop": ellipticity_drop,
                "outer_pa_change_deg": outer_pa_change,
                "psf_veto": True if peak_sma < 2.0 * psf_fwhm else None,
                "psf_veto_reason": (
                    "isophote scale is below 2 PSF FWHM"
                    if peak_sma < 2.0 * psf_fwhm
                    else "PSF diffraction direction was not evaluated"
                ),
            }

    return {
        "status": "AVAILABLE",
        "source_extent_psf_ratio": float(np.max(sma) / psf_fwhm),
        "outer_axis_ratio": float(np.median(outer_q)),
        "outer_geometry": outer_geometry,
        "bar_profile": bar_profile,
        "quality_flags": [],
    }


def evaluate_bar_psf_veto(
    band: BandArrays,
    *,
    psf_fwhm: float,
    candidate_radius: float,
) -> dict[str, Any]:
    """Evaluate a Bar candidate against its fitted convolution PSF axes."""

    diagnostics: dict[str, Any] = {
        "version": "psf-direction@v1",
        "orientation_source": "fitted_convolution_psf_pixel_frame",
        "psf_file": band.psf_file,
        "pa_v3_deg": band.pa_v3_deg,
        "measurements": [],
    }
    if candidate_radius < 2.0 * psf_fwhm:
        return {
            "psf_veto": True,
            "psf_veto_reason": "isophote scale is below 2 PSF FWHM",
            "psf_veto_diagnostics": diagnostics,
        }
    if band.psf is None:
        return {
            "psf_veto": None,
            "psf_veto_reason": "fitted convolution PSF is unavailable",
            "psf_veto_diagnostics": diagnostics,
        }

    center = band.center or (
        (band.original.shape[1] - 1) / 2.0,
        (band.original.shape[0] - 1) / 2.0,
    )
    evaluated: list[dict[str, Any]] = []
    for source_name, image in (
        ("original", band.original),
        ("residual", band.residual),
    ):
        measurement = measure_directional_harmonic_alignment(
            image,
            band.sigma,
            band.psf,
            candidate_radius=candidate_radius,
            image_center=center,
            mask=band.mask,
        )
        entry = {"image_source": source_name, **measurement}
        diagnostics["measurements"].append(entry)
        value = measurement.get("value") or {}
        if measurement.get("status") == "AVAILABLE" and value.get("evaluated") is True:
            evaluated.append(value)

    if not evaluated:
        return {
            "psf_veto": None,
            "psf_veto_reason": "PSF direction measurement did not pass quality gates",
            "psf_veto_diagnostics": diagnostics,
        }
    if any(item.get("aligned") is True for item in evaluated):
        return {
            "psf_veto": True,
            "psf_veto_reason": "m=6 axes align with the fitted convolution PSF",
            "psf_veto_diagnostics": diagnostics,
        }
    return {
        "psf_veto": False,
        "psf_veto_reason": "measured m=6 axes do not align with the fitted convolution PSF",
        "psf_veto_diagnostics": diagnostics,
    }


def measure_radial_residual_systematic(
    residual: np.ndarray,
    sigma: np.ndarray,
    *,
    center: tuple[float, float],
    inner_radius: float,
    outer_radius: float,
    mask: np.ndarray | None = None,
    bins: int = 6,
    min_bin_snr: float = 3.0,
    min_consecutive: int = 3,
) -> dict[str, Any]:
    """Measure whether adjacent outer annuli have significant equal-sign residuals."""

    values = np.asarray(residual, dtype=float)
    errors = np.asarray(sigma, dtype=float)
    if values.ndim != 2 or errors.shape != values.shape:
        raise ValueError("residual and sigma must be matching two-dimensional arrays")
    if not 0 <= inner_radius < outer_radius or bins < min_consecutive:
        raise ValueError("invalid radial range or bin count")
    excluded = np.zeros(values.shape, dtype=bool)
    if mask is not None:
        excluded = np.asarray(mask, dtype=bool)
        if excluded.shape != values.shape:
            raise ValueError("mask shape must match residual")
    excluded |= ~np.isfinite(values) | ~np.isfinite(errors) | (errors <= 0)
    yy, xx = np.indices(values.shape, dtype=float)
    radius = np.hypot(xx - center[0], yy - center[1])
    edges = np.linspace(inner_radius, outer_radius, bins + 1)

    bin_snr: list[float | None] = []
    signs: list[int] = []
    for lower, upper in zip(edges[:-1], edges[1:], strict=True):
        selected = (radius >= lower) & (radius < upper) & ~excluded
        if not np.any(selected):
            bin_snr.append(None)
            signs.append(0)
            continue
        signal = float(np.sum(values[selected]))
        noise = float(np.sqrt(np.sum(np.square(errors[selected]))))
        snr = signal / noise
        bin_snr.append(snr)
        signs.append(1 if snr >= min_bin_snr else -1 if snr <= -min_bin_snr else 0)

    best_sign = 0
    best_run = 0
    current_sign = 0
    current_run = 0
    for sign in signs:
        if sign != 0 and sign == current_sign:
            current_run += 1
        elif sign != 0:
            current_sign = sign
            current_run = 1
        else:
            current_sign = 0
            current_run = 0
        if current_run > best_run:
            best_sign = current_sign
            best_run = current_run

    return _measurement(
        {
            "systematic": best_run >= min_consecutive,
            "sign": "positive" if best_sign > 0 else "negative" if best_sign < 0 else "none",
            "max_consecutive": best_run,
            "bin_snr": bin_snr,
            "inner_radius_pix": float(inner_radius),
            "outer_radius_pix": float(outer_radius),
        }
    )


def derive_fit_features(components: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """Derive rule-facing measurements from parsed GalfitS component parameters."""

    physical = [item for item in components if item.get("type") != "const"]
    single = _measurement(status="UNAVAILABLE", quality_flags=("other",))
    if len(physical) == 1:
        component = physical[0]
        n_value = component.get("n")
        if str(component.get("type", "")).lower().startswith("sersic") and isinstance(
            n_value, (int, float)
        ):
            single = _measurement(
                {
                    "n": float(n_value),
                    "at_boundary": bool(component.get("n_at_boundary", False)),
                }
            )

    by_name = {str(item.get("name", "")).lower(): item for item in physical}
    disk = by_name.get("disk")
    bar = by_name.get("bar")
    bar_parameters = _measurement(status="UNAVAILABLE", quality_flags=("other",))
    if disk and bar:
        disk_re = disk.get("re")
        bar_re = bar.get("re")
        q_bar = bar.get("ba")
        if all(isinstance(value, (int, float)) for value in (disk_re, bar_re, q_bar)):
            if disk_re > 0:
                bar_parameters = _measurement(
                    {
                        "re_bar_over_re_disk": float(bar_re / disk_re),
                        "q_bar": float(q_bar),
                    }
                )

    return {
        "single_sersic_n": single,
        "bar_fit_parameters": bar_parameters,
    }


def _sky_separation_arcsec(
    first: tuple[float, float],
    second: tuple[float, float],
) -> float:
    mean_dec = np.radians((first[1] + second[1]) / 2.0)
    delta_ra = (first[0] - second[0]) * np.cos(mean_dec)
    delta_dec = first[1] - second[1]
    return float(3600.0 * np.hypot(delta_ra, delta_dec))


def merge_wcs_candidate_regions(
    numeric_evidence: dict[str, Any],
    bands: Sequence[BandArrays],
    *,
    match_radius_arcsec: float = 0.1,
) -> dict[str, Any]:
    """Assign sky positions and stable cross-band IDs to local-peak regions."""

    if match_radius_arcsec <= 0:
        raise ValueError("match_radius_arcsec must be positive")
    result = deepcopy(numeric_evidence)
    wcs_by_band = {band.band: band.wcs for band in bands if band.wcs is not None}
    entries: list[dict[str, Any]] = []
    for feature in result.get("features", []):
        for region in feature.get("candidate_regions", []):
            wcs = wcs_by_band.get(region["band"])
            if wcs is None:
                continue
            try:
                ra, dec = wcs.all_pix2world(region["x_pix"], region["y_pix"], 0)
                sky = (float(ra), float(dec))
            except (TypeError, ValueError, RuntimeError):
                continue
            if not all(np.isfinite(sky)):
                continue
            region["ra_deg"], region["dec_deg"] = sky
            entries.append({"region": region, "sky": sky})

    clusters: list[dict[str, Any]] = []
    for entry in sorted(entries, key=lambda item: (*item["sky"], item["region"]["band"])):
        nearest = None
        nearest_distance = float("inf")
        for cluster in clusters:
            if entry["region"]["band"] in cluster["bands"]:
                continue
            distance = _sky_separation_arcsec(entry["sky"], cluster["center"])
            if distance <= match_radius_arcsec and distance < nearest_distance:
                nearest = cluster
                nearest_distance = distance
        if nearest is None:
            clusters.append(
                {
                    "members": [entry],
                    "bands": {entry["region"]["band"]},
                    "center": entry["sky"],
                }
            )
            continue
        nearest["members"].append(entry)
        nearest["bands"].add(entry["region"]["band"])
        coordinates = np.asarray([item["sky"] for item in nearest["members"]])
        nearest["center"] = tuple(np.mean(coordinates, axis=0))

    clusters.sort(key=lambda item: item["center"])
    for index, cluster in enumerate(clusters, start=1):
        region_id = f"candidate_{index}"
        detected_in = sorted(cluster["bands"])
        for entry in cluster["members"]:
            entry["region"]["region_id"] = region_id
            entry["region"]["detected_in_bands"] = detected_in

    validate(result, "numeric_evidence")
    return result


def _derived_feature(
    feature_id: str,
    name: str,
    measurement: Mapping[str, Any],
    *,
    band: str | None = None,
    file: str | None = None,
    hdu: int | None = None,
    region: str | None = None,
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
            "frame": "pixel" if band is not None else "none",
            "region": region,
        },
        "quality_flags": list(measurement.get("quality_flags", [])),
    }


def _isophote_cache_key(band: BandArrays) -> str:
    digest = hashlib.sha256()
    for array in (band.original, band.mask):
        contiguous = np.ascontiguousarray(array)
        digest.update(contiguous.view(np.uint8))
    digest.update(str(band.band).encode())
    digest.update(str(band.pixscale_arcsec).encode())
    return digest.hexdigest()


def _isophote_measurements(
    band: BandArrays,
    psf_fwhm: float,
    *,
    isophote_cache: MutableMapping[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    from tools.bar_lopsidedness_core import (
        analyze_dolfi_a1,
        detect_bar,
        fit_isophotes,
    )

    if band.pixscale_arcsec is None or band.pixscale_arcsec <= 0:
        unavailable = _measurement(status="UNAVAILABLE", quality_flags=("other",))
        return unavailable, unavailable
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message="Mean of empty slice", category=RuntimeWarning)
        warnings.filterwarnings(
            "ignore",
            message="Degrees of freedom <= 0 for slice",
            category=RuntimeWarning,
        )
        warnings.filterwarnings(
            "ignore",
            message="invalid value encountered in",
            category=RuntimeWarning,
        )
        table = None
        cache_key = _isophote_cache_key(band) if isophote_cache is not None else None
        if cache_key is not None:
            table = isophote_cache.get(cache_key)
        if table is None:
            _, _, table, _ = fit_isophotes(
                band.original,
                band.mask,
                band.pixscale_arcsec,
                band.band,
            )
            if cache_key is not None:
                isophote_cache[cache_key] = table
    if len(table) < 5:
        unavailable = _measurement(status="UNAVAILABLE", quality_flags=("other",))
        return unavailable, unavailable
    summary = summarize_isophote_profile(
        table,
        psf_fwhm=psf_fwhm,
        bar_result=detect_bar(table),
    )
    if summary["status"] == "AVAILABLE" and summary.get("bar_profile") is not None:
        summary["bar_profile"].update(
            evaluate_bar_psf_veto(
                band,
                psf_fwhm=psf_fwhm,
                candidate_radius=summary["bar_profile"]["scale_pix"],
            )
        )
    m1_result = analyze_dolfi_a1(table)
    amplitude = m1_result.get("A1_mean")
    m1 = (
        _measurement(float(amplitude))
        if isinstance(amplitude, (int, float)) and np.isfinite(amplitude)
        else _measurement(status="UNAVAILABLE", quality_flags=("other",))
    )
    return summary, m1


def _central_measurements(
    band: BandArrays,
    psf_fwhm: float,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    center = band.center or (
        (band.original.shape[1] - 1) / 2.0,
        (band.original.shape[0] - 1) / 2.0,
    )
    mask = (
        np.zeros(band.original.shape, dtype=bool)
        if band.mask is None
        else np.asarray(band.mask, dtype=bool)
    )
    snr = measure_aperture_snr(
        band.residual,
        band.sigma,
        center=center,
        radius=psf_fwhm,
        mask=mask,
    )
    yy, xx = np.indices(band.residual.shape, dtype=float)
    local_mask = mask | (np.hypot(xx - center[0], yy - center[1]) > 3.0 * psf_fwhm)
    fwhm = measure_fwhm(
        np.clip(band.residual, 0.0, None),
        mask=local_mask,
        center=center,
    )
    moments = measure_weighted_moments(
        np.clip(band.residual, 0.0, None),
        mask=local_mask,
        center=center,
    )
    modes = measure_azimuthal_modes(
        band.residual,
        center=center,
        mask=mask,
        orders=(2,),
        r_min=psf_fwhm,
        r_max=4.0 * psf_fwhm,
    )

    if snr["status"] == "AVAILABLE" and fwhm["status"] == "AVAILABLE":
        resolution = _measurement(
            {
                "fwhm_obs_pix": fwhm["value"]["fwhm_geometric_pix"],
                "fwhm_psf_pix": psf_fwhm,
                "snr": snr["value"]["snr"],
            }
        )
    else:
        flags = set(snr.get("quality_flags", [])) | set(fwhm.get("quality_flags", []))
        resolution = _measurement(
            status="UNAVAILABLE",
            quality_flags=tuple(flags or {"other"}),
        )
    axis_ratio = (
        _measurement(float(moments["value"]["axis_ratio"]))
        if moments["status"] == "AVAILABLE"
        else _measurement(
            status="UNAVAILABLE",
            quality_flags=moments.get("quality_flags", ("other",)),
        )
    )
    m2 = (
        _measurement(float(modes["value"]["m2"]["amplitude"]))
        if modes["status"] == "AVAILABLE"
        else _measurement(
            status="UNAVAILABLE",
            quality_flags=modes.get("quality_flags", ("other",)),
        )
    )
    return resolution, axis_ratio, m2


def _local_original_contrast_snr(band: BandArrays, region: Mapping[str, Any]) -> float | None:
    x = float(region["x_pix"])
    y = float(region["y_pix"])
    radius = max(float(region.get("radius_pix") or 1.0), 1.0)
    yy, xx = np.indices(band.original.shape, dtype=float)
    distance = np.hypot(xx - x, yy - y)
    core = distance <= radius
    annulus = (distance >= 2.0 * radius) & (distance <= 4.0 * radius)
    excluded = ~np.isfinite(band.original) | ~np.isfinite(band.sigma) | (band.sigma <= 0)
    if band.mask is not None:
        excluded |= np.asarray(band.mask, dtype=bool)
    core &= ~excluded
    annulus &= ~excluded
    if not np.any(core) or not np.any(annulus):
        return None
    contrast = float(np.max(band.original[core]) - np.median(band.original[annulus]))
    noise = float(np.median(band.sigma[core]))
    return contrast / noise if noise > 0 else None


def _candidate_facts(
    numeric_evidence: dict[str, Any],
    bands: Sequence[BandArrays],
) -> tuple[dict[str, bool], dict[str, Any]]:
    by_band = {band.band: band for band in bands}
    matches: dict[str, bool] = {}
    positions: dict[str, list[tuple[float, float]]] = {}
    detected: dict[str, set[str]] = {}
    for feature in numeric_evidence.get("features", []):
        for region in feature.get("candidate_regions", []):
            region_id = region["region_id"]
            band = by_band.get(region["band"])
            contrast = _local_original_contrast_snr(band, region) if band else None
            matches[region_id] = matches.get(region_id, False) or bool(
                contrast is not None and contrast >= 5.0
            )
            detected.setdefault(region_id, set()).add(region["band"])
            if region.get("ra_deg") is not None and region.get("dec_deg") is not None:
                positions.setdefault(region_id, []).append(
                    (float(region["ra_deg"]), float(region["dec_deg"]))
                )

    summary: dict[str, Any] = {}
    for region_id in sorted(detected):
        coords = positions.get(region_id, [])
        scatter = None
        if coords:
            center = tuple(np.mean(np.asarray(coords), axis=0))
            scatter = max(_sky_separation_arcsec(coord, center) for coord in coords)
        summary[region_id] = {
            "detected_in_bands": sorted(detected[region_id]),
            "position_scatter_arcsec": scatter,
        }
    return matches, summary


def _mask_asymmetric(bands: Sequence[BandArrays], threshold: float = 0.1) -> bool:
    for band in bands:
        if band.mask is None:
            continue
        mask = np.asarray(band.mask, dtype=bool)
        y_mid, x_mid = mask.shape[0] // 2, mask.shape[1] // 2
        left, right = np.mean(mask[:, :x_mid]), np.mean(mask[:, -x_mid:])
        lower, upper = np.mean(mask[:y_mid, :]), np.mean(mask[-y_mid:, :])
        if abs(left - right) > threshold or abs(lower - upper) > threshold:
            return True
    return False


def derive_rule_features(
    numeric_evidence: dict[str, Any],
    bands: Sequence[BandArrays],
    *,
    fit_components: Sequence[Mapping[str, Any]] = (),
    candidate_match_arcsec: float = 0.1,
    isophote_cache: MutableMapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Append all v1 rule-facing numeric facts to primitive evidence."""

    result = merge_wcs_candidate_regions(
        numeric_evidence,
        bands,
        match_radius_arcsec=candidate_match_arcsec,
    )
    passed_bands = {
        item["band"] for item in result["band_quality"] if item.get("passed") is True
    }
    per_band_features: list[dict[str, Any]] = []
    isophote_values: list[tuple[str, dict[str, Any]]] = []
    m1_values: list[float] = []
    m2_values: list[float] = []
    central_axis_ratios: list[float] = []
    central_excess_bands: list[str] = []
    radial_values: list[dict[str, Any]] = []

    for band in bands:
        psf = (
            measure_psf_fwhm(band.psf)
            if band.psf is not None
            else _measurement(status="UNAVAILABLE", quality_flags=("psf_missing",))
        )
        if psf["status"] != "AVAILABLE":
            continue
        psf_fwhm = float(psf["value"]["fwhm_geometric_pix"])
        isophote, m1 = _isophote_measurements(
            band,
            psf_fwhm,
            isophote_cache=isophote_cache,
        )
        isophote_values.append((band.band, isophote))
        if m1["status"] == "AVAILABLE" and band.band in passed_bands:
            m1_values.append(float(m1["value"]))

        resolution, axis_ratio, m2 = _central_measurements(band, psf_fwhm)
        if resolution["status"] == "AVAILABLE":
            per_band_features.append(
                _derived_feature(
                    f"{band.band}_central_resolution_measurement",
                    "central_resolution_measurement",
                    resolution,
                    band=band.band,
                    file=band.source_file,
                    hdu=band.residual_hdu,
                    region="central_3psf",
                )
            )
            if (
                band.band in passed_bands
                and resolution["value"]["snr"] >= 10.0
            ):
                central_excess_bands.append(band.band)
        else:
            per_band_features.append(
                _derived_feature(
                    f"{band.band}_central_resolution_measurement",
                    "central_resolution_measurement",
                    resolution,
                    band=band.band,
                    file=band.source_file,
                    hdu=band.residual_hdu,
                    region="central_3psf",
                )
            )
        if axis_ratio["status"] == "AVAILABLE" and band.band in passed_bands:
            central_axis_ratios.append(float(axis_ratio["value"]))
        if m2["status"] == "AVAILABLE" and band.band in passed_bands:
            m2_values.append(float(m2["value"]))

        center = band.center or (
            (band.residual.shape[1] - 1) / 2.0,
            (band.residual.shape[0] - 1) / 2.0,
        )
        outer_radius = 0.45 * min(band.residual.shape)
        inner_radius = max(3.0 * psf_fwhm, 0.2 * outer_radius)
        radial = (
            measure_radial_residual_systematic(
                band.residual,
                band.sigma,
                center=center,
                inner_radius=inner_radius,
                outer_radius=outer_radius,
                mask=band.mask,
            )
            if inner_radius < outer_radius
            else _measurement(status="UNAVAILABLE", quality_flags=("other",))
        )
        if band.band in passed_bands and radial["status"] == "AVAILABLE":
            radial_values.append(radial["value"])
        per_band_features.append(
            _derived_feature(
                f"{band.band}_residual_outer_profile",
                "residual_outer_profile",
                radial,
                band=band.band,
                file=band.source_file,
                hdu=band.residual_hdu,
                region="outer_annuli",
            )
        )
        bar_measurement = (
            _measurement(isophote["bar_profile"])
            if isophote["status"] == "AVAILABLE"
            and isophote.get("bar_profile") is not None
            else _measurement(status="UNAVAILABLE", quality_flags=("other",))
        )
        per_band_features.append(
            _derived_feature(
                f"{band.band}_bar_isophote_profile",
                "bar_isophote_profile",
                bar_measurement,
                band=band.band,
                file=band.source_file,
                hdu=band.original_hdu,
                region="isophote_profile",
            )
        )

    available_isophotes = [
        value
        for band, value in isophote_values
        if band in passed_bands and value["status"] == "AVAILABLE"
    ]
    if available_isophotes:
        extent = _measurement(
            float(np.median([item["source_extent_psf_ratio"] for item in available_isophotes]))
        )
        geometry = _measurement(
            {
                "pa_scatter_deg": float(
                    np.median(
                        [item["outer_geometry"]["pa_scatter_deg"] for item in available_isophotes]
                    )
                ),
                "q_range": float(
                    np.median([item["outer_geometry"]["q_range"] for item in available_isophotes])
                ),
                "center_scatter_pix": float(
                    np.median(
                        [
                            item["outer_geometry"]["center_scatter_pix"]
                            for item in available_isophotes
                        ]
                    )
                ),
            }
        )
        outer_q = _measurement(
            float(np.median([item["outer_axis_ratio"] for item in available_isophotes]))
        )
    else:
        extent = geometry = outer_q = _measurement(
            status="UNAVAILABLE",
            quality_flags=("other",),
        )

    support_needed = min(2, len(passed_bands)) if passed_bands else 1
    sign_counts = {
        sign: sum(
            item["systematic"] is True and item["sign"] == sign
            for item in radial_values
        )
        for sign in ("positive", "negative")
    }
    outer_systematic = _measurement(max(sign_counts.values(), default=0) >= support_needed)
    extended_positive = _measurement(sign_counts["positive"] >= support_needed)
    central_excess = _measurement(len(set(central_excess_bands)) >= support_needed)
    residual_m2 = (
        _measurement(float(max(m2_values)))
        if m2_values
        else _measurement(status="UNAVAILABLE", quality_flags=("low_snr",))
    )
    central_elongation = (
        _measurement(min(central_axis_ratios) <= 0.7)
        if central_axis_ratios
        else _measurement(status="UNAVAILABLE", quality_flags=("low_snr",))
    )
    original_m1 = (
        _measurement(float(np.median(m1_values)))
        if m1_values
        else _measurement(status="UNAVAILABLE", quality_flags=("other",))
    )
    original_matches, match_summary = _candidate_facts(result, bands)
    m1_confusion = _measurement(any(original_matches.values()) or _mask_asymmetric(bands))
    fit_features = derive_fit_features(fit_components)

    aggregate_features = [
        _derived_feature("aggregate_source_extent", "source_extent_psf_ratio", extent),
        _derived_feature("aggregate_outer_geometry", "outer_isophote_geometry", geometry),
        _derived_feature("aggregate_outer_axis_ratio", "outer_axis_ratio", outer_q),
        _derived_feature(
            "aggregate_outer_residual",
            "outer_residual_systematic",
            outer_systematic,
        ),
        _derived_feature(
            "aggregate_central_excess",
            "central_excess_multiband",
            central_excess,
        ),
        _derived_feature("aggregate_residual_m2", "residual_m2_amplitude", residual_m2),
        _derived_feature(
            "aggregate_residual_elongation",
            "residual_central_elongation",
            central_elongation,
        ),
        _derived_feature("aggregate_original_m1", "original_m1_amplitude", original_m1),
        _derived_feature("aggregate_m1_confusion", "m1_confusion_present", m1_confusion),
        _derived_feature(
            "aggregate_original_source_matches",
            "original_source_matches",
            _measurement(original_matches),
        ),
        _derived_feature(
            "aggregate_candidate_wcs_matches",
            "candidate_wcs_match_summary",
            _measurement(match_summary),
        ),
        _derived_feature(
            "aggregate_bar_fit_parameters",
            "bar_fit_parameters",
            fit_features["bar_fit_parameters"],
        ),
        _derived_feature(
            "aggregate_single_sersic_n",
            "single_sersic_n",
            fit_features["single_sersic_n"],
        ),
        _derived_feature(
            "aggregate_extended_positive_residual",
            "extended_positive_residual",
            extended_positive,
        ),
    ]
    result["features"].extend(aggregate_features + per_band_features)
    result.setdefault("algorithm_versions", {}).update(
        {
            "isophote_facts": "photutils-isophote-summary@v1",
            "radial_residual": "consecutive-annular-snr@v1",
            "candidate_wcs_merge": "sky-nearest-0.1arcsec@v1",
            "fit_parameter_facts": "gssummary-components@v1",
        }
    )
    validate(result, "numeric_evidence")
    return result
