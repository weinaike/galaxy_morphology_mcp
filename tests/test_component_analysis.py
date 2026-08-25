"""Unit tests for the shadow-mode component-analysis package.

Numeric layer: synthetic images with analytically known moments, Fourier
modes, FWHM, aperture SNR and local peaks.  Rules layer: pure JSON fixtures
covering PROPOSE / ACCEPT / REJECT / INCONCLUSIVE branches of every rule.
"""

import numpy as np
import pytest

from component_analysis import (
    BandArrays,
    RuleThresholds,
    decide_proposal,
    deconvolve_fwhm,
    detect_local_peaks,
    evaluate_refit,
    extract_numeric_evidence,
    measure_aperture_snr,
    measure_azimuthal_modes,
    measure_directional_harmonic_alignment,
    measure_fwhm,
    measure_psf_fwhm,
    measure_weighted_moments,
)

FWHM_FACTOR = 2.0 * np.sqrt(2.0 * np.log(2.0))


def gaussian_image(shape=(101, 101), sigma_x=4.0, sigma_y=None, amplitude=100.0):
    sigma_y = sigma_x if sigma_y is None else sigma_y
    ny, nx = shape
    xcen, ycen = (nx - 1) / 2.0, (ny - 1) / 2.0
    yy, xx = np.indices(shape, dtype=float)
    return amplitude * np.exp(
        -0.5 * (((xx - xcen) / sigma_x) ** 2 + ((yy - ycen) / sigma_y) ** 2)
    )


# ---------------------------------------------------------------------------
# Numeric layer
# ---------------------------------------------------------------------------


def test_weighted_moments_recovers_elliptical_gaussian():
    image = gaussian_image(sigma_x=4.0, sigma_y=2.0)
    result = measure_weighted_moments(image)
    assert result["status"] == "AVAILABLE"
    value = result["value"]
    assert value["x_centroid_pix"] == pytest.approx(50.0, abs=0.05)
    assert value["y_centroid_pix"] == pytest.approx(50.0, abs=0.05)
    assert np.sqrt(value["variance_major_pix2"]) == pytest.approx(4.0, rel=0.03)
    assert value["axis_ratio"] == pytest.approx(0.5, abs=0.03)
    assert min(value["pa_deg"], 180.0 - value["pa_deg"]) == pytest.approx(0.0, abs=2.0)


def test_weighted_moments_fully_masked_unavailable():
    image = gaussian_image()
    result = measure_weighted_moments(image, mask=np.ones_like(image, dtype=bool))
    assert result["status"] == "UNAVAILABLE"
    assert result["value"] is None


def test_measure_fwhm_matches_gaussian_sigma():
    sigma = 3.0
    image = gaussian_image(sigma_x=sigma)
    result = measure_fwhm(image, center=(50.0, 50.0))
    assert result["status"] == "AVAILABLE"
    expected = FWHM_FACTOR * sigma
    assert result["value"]["fwhm_geometric_pix"] == pytest.approx(expected, rel=0.03)


def test_measure_psf_fwhm_uses_core_half_maximum():
    sigma = 1.5
    image = gaussian_image(shape=(101, 101), sigma_x=sigma, amplitude=1.0)
    result = measure_psf_fwhm(image)
    assert result["status"] == "AVAILABLE"
    assert result["value"]["fwhm_geometric_pix"] == pytest.approx(
        FWHM_FACTOR * sigma, rel=0.05
    )


def test_deconvolve_fwhm():
    assert deconvolve_fwhm(5.0, 3.0) == pytest.approx(4.0)
    assert deconvolve_fwhm(2.0, 3.0) == 0.0
    with pytest.raises(ValueError):
        deconvolve_fwhm(-1.0, 3.0)
    with pytest.raises(ValueError):
        deconvolve_fwhm(5.0, 0.0)


def test_azimuthal_modes_recovers_m2_amplitude():
    shape = (101, 101)
    yy, xx = np.indices(shape, dtype=float)
    theta = np.arctan2(yy - 50.0, xx - 50.0)
    image = 1.0 + 0.4 * np.cos(2.0 * theta)
    result = measure_azimuthal_modes(image, r_min=3.0, r_max=45.0)
    assert result["status"] == "AVAILABLE"
    assert result["value"]["m2"]["amplitude"] == pytest.approx(0.4, abs=0.03)
    assert result["value"]["m1"]["amplitude"] == pytest.approx(0.0, abs=0.03)


def test_azimuthal_modes_recovers_m1_phase():
    shape = (101, 101)
    yy, xx = np.indices(shape, dtype=float)
    theta = np.arctan2(yy - 50.0, xx - 50.0)
    image = 1.0 + 0.3 * np.cos(theta - np.radians(30.0))
    result = measure_azimuthal_modes(image, orders=(1,), r_min=3.0, r_max=45.0)
    assert result["value"]["m1"]["amplitude"] == pytest.approx(0.3, abs=0.03)
    assert result["value"]["m1"]["phase_deg"] == pytest.approx(30.0, abs=2.0)


def test_azimuthal_modes_zero_field_unavailable():
    result = measure_azimuthal_modes(np.zeros((64, 64)))
    assert result["status"] == "UNAVAILABLE"


def _sixfold_pattern(*, phase_deg=0.0, amplitude=1.0, positive=False):
    shape = (81, 81)
    yy, xx = np.indices(shape, dtype=float)
    radius = np.hypot(xx - 40.0, yy - 40.0)
    theta = np.arctan2(yy - 40.0, xx - 40.0)
    radial = np.exp(-0.5 * ((radius - 20.0) / 7.0) ** 2)
    angular = np.cos(6.0 * (theta - np.radians(phase_deg)))
    if positive:
        angular = 1.0 + 0.8 * angular
    return amplitude * radial * angular


def test_directional_harmonic_alignment_accepts_signed_axis_inversion():
    template = _sixfold_pattern(phase_deg=3.0, positive=True)
    image = -_sixfold_pattern(phase_deg=3.0, amplitude=10.0)
    result = measure_directional_harmonic_alignment(
        image,
        np.ones_like(image),
        template,
        image_center=(40.0, 40.0),
        template_center=(40.0, 40.0),
        candidate_radius=20.0,
    )
    assert result["status"] == "AVAILABLE"
    assert result["value"]["evaluated"] is True
    assert result["value"]["aligned"] is True
    assert result["value"]["phase_delta_deg"] == pytest.approx(0.0, abs=1.0)


def test_directional_harmonic_alignment_rejects_wrong_direction():
    template = _sixfold_pattern(phase_deg=0.0, positive=True)
    image = _sixfold_pattern(phase_deg=12.0, amplitude=10.0)
    result = measure_directional_harmonic_alignment(
        image,
        np.ones_like(image),
        template,
        image_center=(40.0, 40.0),
        template_center=(40.0, 40.0),
        candidate_radius=20.0,
    )
    assert result["value"]["evaluated"] is True
    assert result["value"]["aligned"] is False
    assert result["value"]["phase_delta_deg"] == pytest.approx(12.0, abs=1.0)


def test_directional_harmonic_alignment_keeps_low_snr_unknown():
    template = _sixfold_pattern(positive=True)
    image = _sixfold_pattern(amplitude=0.01)
    result = measure_directional_harmonic_alignment(
        image,
        np.ones_like(image),
        template,
        image_center=(40.0, 40.0),
        template_center=(40.0, 40.0),
        candidate_radius=20.0,
    )
    assert result["status"] == "AVAILABLE"
    assert result["value"]["evaluated"] is False
    assert result["value"]["aligned"] is None
    assert "low_snr" in result["quality_flags"]


def test_aperture_snr_uniform_field():
    image = np.ones((101, 101))
    sigma = np.ones((101, 101))
    result = measure_aperture_snr(image, sigma, center=(50.0, 50.0), radius=5.0)
    assert result["status"] == "AVAILABLE"
    value = result["value"]
    assert value["snr"] == pytest.approx(np.sqrt(value["valid_pixels"]))


def test_detect_local_peaks_mask_threshold_and_center_exclusion():
    image = np.zeros((64, 64))
    sigma = np.ones((64, 64))
    image[10, 10] = 10.0   # detectable
    image[30, 30] = 3.0    # below SNR threshold
    image[50, 50] = 20.0   # masked out
    image[33, 33] = 8.0    # inside center exclusion radius
    mask = np.zeros((64, 64), dtype=bool)
    mask[50, 50] = True
    peaks = detect_local_peaks(
        image,
        sigma,
        band="f200w",
        mask=mask,
        threshold_snr=5.0,
        center=(32.0, 32.0),
        center_exclusion_radius=5.0,
    )
    assert len(peaks) == 1
    assert peaks[0]["region_id"] == "candidate_1"
    assert (peaks[0]["x_pix"], peaks[0]["y_pix"]) == (10.0, 10.0)
    assert peaks[0]["local_snr"] == pytest.approx(10.0)


def test_extract_numeric_evidence_schema_valid_and_quality_gated():
    shape = (64, 64)
    original = gaussian_image(shape=shape, sigma_x=4.0)
    residual = np.zeros(shape)
    sigma = np.ones(shape)
    psf = gaussian_image(shape=(33, 33), sigma_x=1.5, amplitude=1.0)
    good = BandArrays(
        band="f200w", original=original, residual=residual, sigma=sigma, psf=psf
    )
    no_psf = BandArrays(
        band="f090w", original=original, residual=residual, sigma=sigma, psf=None
    )
    evidence = extract_numeric_evidence(
        round_id="r1", manifest_ref="manifest.json", bands=[good, no_psf]
    )
    quality = {item["band"]: item for item in evidence["band_quality"]}
    assert quality["f200w"]["passed"] is True
    assert quality["f200w"]["psf_fwhm_pix"] == pytest.approx(FWHM_FACTOR * 1.5, rel=0.05)
    assert quality["f090w"]["passed"] is False
    assert "PSF unavailable" in quality["f090w"]["reasons"]
    names = {feature["name"] for feature in evidence["features"]}
    assert {"source_fwhm", "psf_fwhm", "residual_fourier_modes", "residual_local_peaks"} <= names


def test_extract_numeric_evidence_qualifies_candidate_ids_by_band():
    shape = (32, 32)
    original = gaussian_image(shape=shape, sigma_x=3.0)
    residual = np.zeros(shape)
    residual[4, 5] = 10.0
    sigma = np.ones(shape)
    bands = [
        BandArrays("f200w", original, residual, sigma, psf=None),
        BandArrays("f444w", original, residual, sigma, psf=None),
    ]
    evidence = extract_numeric_evidence(
        round_id="r1", manifest_ref="manifest.json", bands=bands
    )
    regions = [
        region
        for feature in evidence["features"]
        if feature["name"] == "residual_local_peaks"
        for region in feature.get("candidate_regions", [])
    ]
    assert {region["region_id"] for region in regions} == {
        "f200w:candidate_1",
        "f444w:candidate_1",
    }


# ---------------------------------------------------------------------------
# Rules layer fixtures
# ---------------------------------------------------------------------------


def feat(feature_id, name, value, band="f200w", status="AVAILABLE", **extra):
    feature = {
        "feature_id": feature_id,
        "name": name,
        "status": status,
        "value": value,
        "source": {"band": band},
    }
    feature.update(extra)
    return feature


def numeric_fixture(features, band_quality=None, round_id="r1"):
    return {
        "schema_version": "1.0",
        "round_id": round_id,
        "manifest_ref": "manifest.json",
        "features": features,
        "band_quality": band_quality or [{"band": "f200w", "passed": True}],
    }


def vlm_fixture(observations=(), round_id="r1", parse_status="OK"):
    return {
        "schema_version": "1.0",
        "round_id": round_id,
        "parse_status": parse_status,
        "observations": list(observations),
    }


def obs(label, target_id="central", confidence=0.9):
    return {"target_id": target_id, "label": label, "confidence": confidence}


EXTENT = feat("extent", "source_extent_psf_ratio", 4.0)
GEOMETRY = feat("geom", "outer_isophote_geometry", {"pa_scatter_deg": 5.0, "q_range": 0.05})
RESIDUAL_OUTER = feat("resid", "outer_residual_systematic", True)


def decide(features, observations=(), components=(), band_quality=None):
    return decide_proposal(
        round_id="r1",
        numeric_evidence=numeric_fixture(features, band_quality),
        vlm_evidence=vlm_fixture(observations),
        current_components=components,
    )


# ---------------------------------------------------------------------------
# Disk / spheroid
# ---------------------------------------------------------------------------


def test_disk_proposed_with_vlm_support_and_n3_only():
    """Regression for the operator-precedence fix: with disk-like VLM support,
    N2 or N3 alone must suffice alongside N1."""
    decision = decide([EXTENT, RESIDUAL_OUTER], [obs("disk_like")])
    assert decision["action"] == {"action_type": "PROPOSE_ADD", "component": "disk"}


def test_disk_proposed_with_neutral_vlm_requires_all_three():
    decision = decide([EXTENT, GEOMETRY, RESIDUAL_OUTER], [])
    assert decision["action"] == {"action_type": "PROPOSE_ADD", "component": "disk"}


def test_disk_neutral_with_partial_evidence_inconclusive():
    decision = decide([EXTENT, GEOMETRY], [])
    assert decision["action"]["action_type"] == "INCONCLUSIVE"


def test_legacy_spheroid_label_does_not_create_vlm_disk_conflict():
    decision = decide(
        [EXTENT, GEOMETRY, RESIDUAL_OUTER], [obs("spheroid_like", confidence=0.9)]
    )
    assert decision["rule_trace"][-1]["rule_id"] != "DISK_VLM_CONFLICT_V1"


def test_spheroid_kept_as_single_sersic():
    sersic = feat("n", "single_sersic_n", {"n": 4.0, "at_boundary": False})
    decision = decide([EXTENT, sersic])
    assert decision["action"]["action_type"] == "KEEP_AND_CONTINUE"
    assert decision["rule_trace"][-1]["rule_id"] == "SPHEROID_SINGLE_SERSIC_V1"


# ---------------------------------------------------------------------------
# Edge-on disk
# ---------------------------------------------------------------------------


def test_edge_on_replace_requires_vlm_confirmation():
    low_q = feat("q", "outer_axis_ratio", 0.12)
    decision = decide([EXTENT, low_q], [obs("edge_on_disk")], components={"disk"})
    assert decision["action"] == {
        "action_type": "PROPOSE_REPLACE",
        "replace_from": "disk",
        "replace_to": "edge_on_disk",
    }
    decision = decide([EXTENT, low_q], [], components={"disk"})
    assert decision["action"]["action_type"] == "INCONCLUSIVE"


# ---------------------------------------------------------------------------
# Bar
# ---------------------------------------------------------------------------

STRONG_BAR = feat(
    "bar",
    "bar_isophote_profile",
    {
        "ellipticity_peak": 0.35,
        "pa_scatter_deg": 5.0,
        "scale_psf_ratio": 3.0,
        "outer_ellipticity_drop": 0.1,
        "psf_veto": False,
    },
)


def test_bar_single_quality_band_strong_isophote_triggers():
    decision = decide([STRONG_BAR], [], components={"disk"})
    assert decision["action"] == {"action_type": "PROPOSE_ADD", "component": "bar"}
    assert decision["rule_trace"][-1]["rule_id"] == "BAR_STRONG_ISOPHOTE_V1"


def test_bar_diffraction_conflict_inconclusive():
    decision = decide(
        [STRONG_BAR], [obs("diffraction_psf", confidence=0.9)], components={"disk"}
    )
    assert decision["action"]["action_type"] == "INCONCLUSIVE"


def test_bar_band_failing_quality_gate_cannot_trigger():
    decision = decide(
        [STRONG_BAR],
        [],
        components={"disk"},
        band_quality=[{"band": "f200w", "passed": False}],
    )
    assert decision["action"]["action_type"] == "KEEP_AND_CONTINUE"


def test_bar_unknown_psf_veto_cannot_trigger_strong_evidence():
    unknown_psf = feat(
        "bar_unknown_psf",
        "bar_isophote_profile",
        {**STRONG_BAR["value"], "psf_veto": None},
    )
    decision = decide([unknown_psf], [], components={"disk"})
    assert decision["action"]["action_type"] == "KEEP_AND_CONTINUE"


def test_bar_weak_candidate_needs_numeric_and_vlm():
    weak = [
        feat("m2", "residual_m2_amplitude", 0.2),
        feat("elong", "residual_central_elongation", True),
    ]
    decision = decide(weak, [obs("bar_like")], components={"disk"})
    assert decision["action"] == {"action_type": "PROPOSE_ADD", "component": "bar"}
    decision = decide(weak, [], components={"disk"})
    assert decision["action"]["action_type"] == "KEEP_AND_CONTINUE"


# ---------------------------------------------------------------------------
# Central source: bulge / candidate / AGN
# ---------------------------------------------------------------------------

CENTRAL_EXCESS = feat("excess", "central_excess_multiband", True)


def resolution(feature_id, band, fwhm_obs, fwhm_psf, snr):
    return feat(
        feature_id,
        "central_resolution_measurement",
        {"fwhm_obs_pix": fwhm_obs, "fwhm_psf_pix": fwhm_psf, "snr": snr},
        band=band,
    )


def test_central_resolved_proposes_bulge():
    features = [CENTRAL_EXCESS, resolution("res", "f200w", 4.0, 2.5, 30.0)]
    decision = decide(features, [obs("central_compact_excess")], components={"disk"})
    assert decision["action"]["action_type"] == "PROPOSE_ADD"
    assert decision["action"]["component"] == "bulge"
    assert decision["action"]["resolved_state"] == "resolved"


def test_central_unresolved_proposes_candidate_not_agn():
    features = [CENTRAL_EXCESS, resolution("res", "f200w", 2.6, 2.5, 30.0)]
    decision = decide(features, [obs("central_compact_excess")], components={"disk"})
    assert decision["action"]["component"] == "compact_central_source_candidate"
    assert decision["action"]["physical_identity"] == "unconfirmed"


def test_central_unresolved_with_independent_evidence_proposes_agn():
    features = [
        CENTRAL_EXCESS,
        resolution("res", "f200w", 2.6, 2.5, 30.0),
        feat("agn", "independent_agn_evidence", True),
    ]
    decision = decide(features, [obs("central_compact_excess")], components={"disk"})
    assert decision["action"]["component"] == "agn"
    assert decision["action"]["physical_identity"] == "agn"


def test_central_similar_resolution_conflict_inconclusive():
    features = [
        CENTRAL_EXCESS,
        resolution("res1", "f200w", 4.0, 2.5, 30.0),
        resolution("res2", "f150w", 2.7, 2.6, 30.0),
    ]
    decision = decide(features, [], components={"disk"})
    assert decision["action"]["action_type"] == "INCONCLUSIVE"
    assert decision["rule_trace"][-1]["rule_id"] == "CENTRAL_RESOLUTION_CONFLICT_V1"


def test_central_weak_snr_only_inconclusive():
    features = [CENTRAL_EXCESS, resolution("res", "f200w", 4.0, 2.5, 15.0)]
    decision = decide(features, [], components={"disk"})
    assert decision["action"]["action_type"] == "INCONCLUSIVE"
    assert decision["rule_trace"][-1]["rule_id"] == "CENTRAL_RESOLUTION_QUALITY_V1"


def test_central_dust_or_diffraction_pollution_inconclusive():
    features = [CENTRAL_EXCESS, resolution("res", "f200w", 4.0, 2.5, 30.0)]
    decision = decide(features, [obs("dust_lane")], components={"disk"})
    assert decision["action"]["action_type"] == "INCONCLUSIVE"


# ---------------------------------------------------------------------------
# Fourier m=1 and companion
# ---------------------------------------------------------------------------


def test_m1_from_original_detection():
    decision = decide(
        [feat("m1", "original_m1_amplitude", 0.15)], [], components={"disk"}
    )
    assert decision["action"] == {"action_type": "PROPOSE_ADD", "component": "fourier_m1"}


def test_m1_with_confusion_inconclusive():
    features = [
        feat("m1", "original_m1_amplitude", 0.15),
        feat("conf", "m1_confusion_present", True),
    ]
    decision = decide(features, [], components={"disk"})
    assert decision["action"]["action_type"] == "INCONCLUSIVE"


PEAKS = feat(
    "peaks",
    "residual_local_peaks",
    1,
    candidate_regions=[
        {"region_id": "candidate_1", "band": "f200w", "x_pix": 40.0, "y_pix": 41.0, "local_snr": 8.0}
    ],
)
ORIGINAL_MATCH = feat("match", "original_source_matches", {"candidate_1": True})


def test_companion_requires_numeric_candidate_original_match_and_vlm():
    decision = decide(
        [PEAKS, ORIGINAL_MATCH],
        [obs("independent_source", target_id="candidate_1")],
        components={"disk"},
    )
    assert decision["action"]["action_type"] == "PROPOSE_ADD"
    assert decision["action"]["component"] == "companion"
    assert decision["action"]["target_model_label"] == "candidate_1"


def test_companion_vlm_uncertain_inconclusive():
    decision = decide(
        [PEAKS, ORIGINAL_MATCH],
        [obs("uncertain", target_id="candidate_1")],
        components={"disk"},
    )
    assert decision["action"]["action_type"] == "INCONCLUSIVE"


def test_companion_without_original_match_not_proposed():
    decision = decide(
        [PEAKS],
        [obs("independent_source", target_id="candidate_1")],
        components={"disk"},
    )
    assert decision["action"]["action_type"] == "KEEP_AND_CONTINUE"


# ---------------------------------------------------------------------------
# Lens
# ---------------------------------------------------------------------------

BAR_ANOMALY = feat("barpar", "bar_fit_parameters", {"re_bar_over_re_disk": 1.1, "q_bar": 0.6})
EXTENDED_RESIDUAL = feat("ext", "extended_positive_residual", True)


def test_lens_proposed_on_bar_anomaly_with_extended_residual():
    decision = decide([BAR_ANOMALY, EXTENDED_RESIDUAL], [], components={"disk", "bar"})
    assert decision["action"] == {"action_type": "PROPOSE_ADD", "component": "lens"}
    assert decision["rule_trace"][-1]["rule_id"] == "LENS_BAR_SPLIT_V1"


def test_lens_anomaly_without_extended_residual_inconclusive():
    decision = decide([BAR_ANOMALY], [], components={"disk", "bar"})
    assert decision["action"]["action_type"] == "INCONCLUSIVE"
    assert decision["rule_trace"][-1]["rule_id"] == "LENS_BAR_ANOMALY_V1"


def test_lens_companion_conflict_inconclusive():
    decision = decide(
        [BAR_ANOMALY, EXTENDED_RESIDUAL],
        [obs("independent_source", target_id="candidate_1")],
        components={"disk", "bar"},
    )
    assert decision["action"]["action_type"] == "INCONCLUSIVE"
    assert decision["rule_trace"][-1]["rule_id"] == "LENS_COMPANION_CONFLICT_V1"


def test_lens_not_proposed_without_bar():
    decision = decide([BAR_ANOMALY, EXTENDED_RESIDUAL], [], components={"disk"})
    assert decision["action"]["action_type"] == "KEEP_AND_CONTINUE"


def test_lens_normal_bar_parameters_not_triggered():
    normal = feat("barpar", "bar_fit_parameters", {"re_bar_over_re_disk": 0.5, "q_bar": 0.3})
    decision = decide([normal, EXTENDED_RESIDUAL], [], components={"disk", "bar"})
    assert decision["action"]["action_type"] == "KEEP_AND_CONTINUE"


# ---------------------------------------------------------------------------
# Proposal control flow
# ---------------------------------------------------------------------------


def test_vlm_parse_failure_forces_inconclusive():
    decision = decide_proposal(
        round_id="r1",
        numeric_evidence=numeric_fixture([EXTENT, GEOMETRY, RESIDUAL_OUTER]),
        vlm_evidence=vlm_fixture(parse_status="PARSE_FAILED"),
        current_components=[],
    )
    assert decision["action"]["action_type"] == "INCONCLUSIVE"
    assert decision["rule_trace"][0]["rule_id"] == "VLM_UNAVAILABLE_V1"


def test_round_id_mismatch_raises():
    with pytest.raises(ValueError):
        decide_proposal(
            round_id="r2",
            numeric_evidence=numeric_fixture([]),
            vlm_evidence=vlm_fixture(),
            current_components=[],
        )


def test_all_components_present_keeps_and_continues():
    decision = decide(
        [], [], components={"disk", "bulge", "bar", "fourier_m1", "companion"}
    )
    assert decision["action"]["action_type"] == "KEEP_AND_CONTINUE"


# ---------------------------------------------------------------------------
# Refit arbitration
# ---------------------------------------------------------------------------


def gates(converged="yes", residual="yes", physical="yes", **extra):
    evaluation = {
        "fit_converged": converged,
        "residual_improved": residual,
        "parameters_physical": physical,
    }
    evaluation.update(extra)
    return evaluation


def bic(gain, comparable=True):
    return {
        "bic_simple": 1000.0,
        "bic_complex": 1000.0 - gain,
        "bic_gain": gain,
        "comparable": comparable,
    }


def test_optional_component_accepted_above_bic_gate():
    decision = evaluate_refit(
        round_id="r2",
        component="compact_central_source_candidate",
        refit_evaluation=gates(bic=bic(32.0)),
    )
    assert decision["action"]["action_type"] == "ACCEPT_REFIT"


def test_optional_component_rejected_below_bic_gate():
    decision = evaluate_refit(
        round_id="r2", component="companion", refit_evaluation=gates(bic=bic(5.0))
    )
    assert decision["action"]["action_type"] == "REJECT_REFIT"


def test_optional_component_incomparable_bic_inconclusive():
    decision = evaluate_refit(
        round_id="r2",
        component="agn",
        refit_evaluation=gates(bic=bic(32.0, comparable=False)),
    )
    assert decision["action"]["action_type"] == "INCONCLUSIVE"


def test_primary_component_accepted_despite_bic_loss():
    decision = evaluate_refit(
        round_id="r2", component="bar", refit_evaluation=gates(bic=bic(-8.0))
    )
    assert decision["action"]["action_type"] == "ACCEPT_REFIT"


def test_failed_primary_gate_rejects():
    decision = evaluate_refit(
        round_id="r2", component="bar", refit_evaluation=gates(residual="no")
    )
    assert decision["action"]["action_type"] == "REJECT_REFIT"


def test_inconclusive_primary_gate_defers():
    decision = evaluate_refit(
        round_id="r2", component="bar", refit_evaluation=gates(converged="inconclusive")
    )
    assert decision["action"]["action_type"] == "INCONCLUSIVE"


def test_boundary_hits_reject():
    decision = evaluate_refit(
        round_id="r2",
        component="bulge",
        refit_evaluation=gates(boundary_hits=["bulge_Re at lower bound"]),
    )
    assert decision["action"]["action_type"] == "REJECT_REFIT"


def test_lens_rejected_below_bic_gate():
    decision = evaluate_refit(
        round_id="r2", component="lens", refit_evaluation=gates(bic=bic(5.0))
    )
    assert decision["action"]["action_type"] == "REJECT_REFIT"


def test_missing_gate_field_raises():
    with pytest.raises(ValueError):
        evaluate_refit(
            round_id="r2",
            component="bar",
            refit_evaluation={"fit_converged": "yes", "residual_improved": "yes"},
        )


def test_thresholds_are_versioned_in_artifact():
    decision = decide([], [], components={"disk", "bulge", "bar", "fourier_m1", "companion"})
    assert decision["thresholds_version"] == RuleThresholds().version
    assert decision["rules_version"] == "component-rules@v1"
