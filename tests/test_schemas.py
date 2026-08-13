"""Unit tests for the frozen artifact schemas (v1.0).

Each schema gets: a minimal valid fixture, plus invalid variants covering the
contract rules that layer boundaries depend on (missing required fields,
illegal enum labels, forbidden action/inference fields, conditional
requirements).
"""

import copy

import jsonschema
import pytest

from schemas import SCHEMA_NAMES, iter_errors, load_schema, validate

# ---------------------------------------------------------------------------
# Valid fixtures
# ---------------------------------------------------------------------------

VALID_MANIFEST = {
    "schema_version": "1.0",
    "round_id": "20260813_obj195_iter3",
    "lyric_file": "/data/obj195/obj_195_iter3.lyric",
    "summary_file": "/data/obj195/output/round3/obj_195.gssummary",
    "bands": [
        {
            "band": "nircam_f200w",
            "science_fits": "/data/obj195/f200w_sci.fits",
            "science_hdu": 0,
            "result_fits": "/data/obj195/output/round3/result_f200w.fits",
            "residual_hdu": 0,
            "mask_hdu": 1,
            "sigma_hdu": 2,
            "model_hdu": 3,
            "original_hdu": 4,
            "psf_fits": "/data/obj195/psf_f200w.fits",
            "psf_hdu": 0,
            "pixscale_arcsec": 0.031,
            "fit_region": [0, 200, 0, 200],
            "validation": {
                "paths_exist": True,
                "hdu_layout_valid": True,
                "shape_consistent": True,
                "wcs_valid": True,
                "unit": "MJy/sr",
                "finite_pixel_fraction": 0.99,
            },
        }
    ],
    "catalog": {"path": None, "format": None, "available": False},
}

VALID_NUMERIC = {
    "schema_version": "1.0",
    "round_id": "20260813_obj195_iter3",
    "manifest_ref": "/data/obj195/output/round3/manifest.json",
    "algorithm_versions": {"isophote": "photutils-1.8.0"},
    "features": [
        {
            "feature_id": "f200w_central_fwhm_obs",
            "name": "central_fwhm_obs",
            "status": "AVAILABLE",
            "value": 2.7,
            "uncertainty": {"type": "bootstrap_ci", "value": [2.5, 2.9], "confidence_level": 0.68},
            "unit": "pixel",
            "source": {
                "band": "nircam_f200w",
                "file": "/data/obj195/output/round3/result_f200w.fits",
                "hdu": 4,
                "frame": "pixel",
                "region": "central_r5px",
            },
            "quality_flags": [],
        },
        {
            "feature_id": "local_peaks_all",
            "name": "residual_local_peaks",
            "status": "AVAILABLE",
            "value": 1,
            "source": {"band": None},
            "candidate_regions": [
                {
                    "region_id": "candidate_1",
                    "band": "nircam_f200w",
                    "x_pix": 143.2,
                    "y_pix": 88.9,
                    "radius_pix": 6.0,
                    "ra_deg": 150.1,
                    "dec_deg": 2.2,
                    "local_snr": 12.4,
                    "detected_in_bands": ["nircam_f200w", "nircam_f444w"],
                }
            ],
        },
        {
            "feature_id": "f090w_central_fwhm_obs",
            "name": "central_fwhm_obs",
            "status": "UNAVAILABLE",
            "value": None,
            "source": {"band": "nircam_f090w"},
            "quality_flags": ["low_snr"],
        },
    ],
    "band_quality": [
        {
            "band": "nircam_f200w",
            "passed": True,
            "psf_available": True,
            "psf_undersampled": False,
            "psf_fwhm_pix": 2.2,
            "central_snr": 45.0,
            "valid_pixel_fraction": 0.99,
            "mask_fraction": 0.05,
            "wcs_valid": True,
            "fit_succeeded": True,
            "reasons": [],
        },
        {
            "band": "nircam_f090w",
            "passed": False,
            "reasons": ["central_snr < 10"],
        },
    ],
}

VALID_VLM = {
    "schema_version": "1.0",
    "round_id": "20260813_obj195_iter3",
    "model_id": "glm-4v-20260601",
    "prompt_version": "residual_analysis_prompt@v1",
    "parse_status": "OK",
    "observations": [
        {
            "target_id": "central",
            "label": "central_compact_excess",
            "confidence": 0.8,
            "evidence_regions": ["nircam_f200w:residual:central_r5px"],
            "quality_flags": [],
            "notes": None,
        },
        {
            "target_id": "candidate_1",
            "label": "uncertain",
            "confidence": 0.3,
            "evidence_regions": [],
            "quality_flags": ["low_image_quality"],
            "notes": "faint, could be clump or PSF artifact",
        },
    ],
}

VALID_DECISION_PROPOSE = {
    "schema_version": "1.0",
    "round_id": "20260813_obj195_iter3",
    "generated_at": "2026-08-13T03:00:00Z",
    "rules_version": "rules@v1",
    "thresholds_version": "thresholds@v1",
    "state": "PROPOSE",
    "action": {
        "action_type": "PROPOSE_ADD",
        "component": "compact_central_source_candidate",
        "physical_identity": "unconfirmed",
        "resolved_state": "unresolved",
    },
    "rule_trace": [
        {
            "rule_id": "CENTRAL_UNRESOLVED_V1",
            "outcome": "SATISFIED",
            "inputs": ["f200w_central_fwhm_obs", "central"],
            "unmet_conditions": [],
            "detail": "FWHM_int < 0.5*FWHM_psf in all quality-gated bands",
        }
    ],
    "evidence_refs": {
        "numeric_evidence": "/data/obj195/output/round3/numeric_evidence.json",
        "vlm_evidence": "/data/obj195/output/round3/vlm_evidence.json",
        "manifest": "/data/obj195/output/round3/manifest.json",
        "previous_round": None,
    },
}

VALID_DECISION_EVALUATE = {
    "schema_version": "1.0",
    "round_id": "20260813_obj195_iter4",
    "rules_version": "rules@v1",
    "thresholds_version": "thresholds@v1",
    "state": "EVALUATE_REFIT",
    "action": {
        "action_type": "ACCEPT_REFIT",
        "component": "compact_central_source_candidate",
    },
    "rule_trace": [
        {"rule_id": "OPTIONAL_COMPONENT_BIC_GATE_V1", "outcome": "SATISFIED"},
        {"rule_id": "CENTRAL_RESIDUAL_IMPROVED_V1", "outcome": "SATISFIED"},
    ],
    "evidence_refs": {
        "numeric_evidence": "/data/obj195/output/round4/numeric_evidence.json",
        "vlm_evidence": None,
        "previous_round": "20260813_obj195_iter3",
    },
    "refit_evaluation": {
        "fit_converged": "yes",
        "residual_improved": "yes",
        "parameters_physical": "yes",
        "boundary_hits": [],
        "degeneracy_warnings": [],
        "bic": {
            "bic_simple": 15234.2,
            "bic_complex": 15201.8,
            "bic_gain": 32.4,
            "comparable": True,
            "formula": "chi2 + k*ln(n)",
            "n_data_points": 40000,
            "n_free_parameters_simple": 12,
            "n_free_parameters_complex": 16,
        },
    },
}


# ---------------------------------------------------------------------------
# Schema loading
# ---------------------------------------------------------------------------


def test_all_schemas_load_and_are_valid_draft202012():
    for name in SCHEMA_NAMES:
        schema = load_schema(name)
        jsonschema.Draft202012Validator.check_schema(schema)
        assert schema["properties"]["schema_version"]["const"] == "1.0"


def test_load_unknown_schema_raises():
    with pytest.raises(ValueError):
        load_schema("nonexistent")


# ---------------------------------------------------------------------------
# Valid fixtures pass
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "instance,name",
    [
        (VALID_MANIFEST, "artifact_manifest"),
        (VALID_NUMERIC, "numeric_evidence"),
        (VALID_VLM, "vlm_evidence"),
        (VALID_DECISION_PROPOSE, "decision_artifact"),
        (VALID_DECISION_EVALUATE, "decision_artifact"),
    ],
)
def test_valid_fixture_passes(instance, name):
    validate(instance, name)
    assert iter_errors(instance, name) == []


# ---------------------------------------------------------------------------
# artifact_manifest invalid cases
# ---------------------------------------------------------------------------


def test_manifest_missing_catalog_rejected():
    bad = copy.deepcopy(VALID_MANIFEST)
    del bad["catalog"]
    with pytest.raises(jsonschema.ValidationError):
        validate(bad, "artifact_manifest")


def test_manifest_empty_bands_rejected():
    bad = copy.deepcopy(VALID_MANIFEST)
    bad["bands"] = []
    with pytest.raises(jsonschema.ValidationError):
        validate(bad, "artifact_manifest")


def test_manifest_band_missing_pixscale_rejected():
    bad = copy.deepcopy(VALID_MANIFEST)
    del bad["bands"][0]["pixscale_arcsec"]
    with pytest.raises(jsonschema.ValidationError):
        validate(bad, "artifact_manifest")


def test_manifest_nonpositive_pixscale_rejected():
    bad = copy.deepcopy(VALID_MANIFEST)
    bad["bands"][0]["pixscale_arcsec"] = 0
    with pytest.raises(jsonschema.ValidationError):
        validate(bad, "artifact_manifest")


def test_manifest_fit_region_wrong_length_rejected():
    bad = copy.deepcopy(VALID_MANIFEST)
    bad["bands"][0]["fit_region"] = [0, 200, 0]
    with pytest.raises(jsonschema.ValidationError):
        validate(bad, "artifact_manifest")


def test_manifest_unknown_top_level_field_rejected():
    bad = copy.deepcopy(VALID_MANIFEST)
    bad["extra_field"] = 1
    with pytest.raises(jsonschema.ValidationError):
        validate(bad, "artifact_manifest")


# ---------------------------------------------------------------------------
# numeric_evidence invalid cases
# ---------------------------------------------------------------------------


def test_numeric_bad_status_rejected():
    bad = copy.deepcopy(VALID_NUMERIC)
    bad["features"][0]["status"] = "MAYBE"
    with pytest.raises(jsonschema.ValidationError):
        validate(bad, "numeric_evidence")


@pytest.mark.parametrize("name", ["add_bar", "bar_candidate", "remove_bulge", "replace_disk"])
def test_numeric_action_or_inference_feature_name_rejected(name):
    """Layer 1 must not emit action/inference fields."""
    bad = copy.deepcopy(VALID_NUMERIC)
    bad["features"][0]["name"] = name
    with pytest.raises(jsonschema.ValidationError):
        validate(bad, "numeric_evidence")


def test_numeric_missing_source_rejected():
    bad = copy.deepcopy(VALID_NUMERIC)
    del bad["features"][0]["source"]
    with pytest.raises(jsonschema.ValidationError):
        validate(bad, "numeric_evidence")


def test_numeric_candidate_region_missing_coords_rejected():
    bad = copy.deepcopy(VALID_NUMERIC)
    del bad["features"][1]["candidate_regions"][0]["x_pix"]
    with pytest.raises(jsonschema.ValidationError):
        validate(bad, "numeric_evidence")


def test_numeric_unknown_quality_flag_rejected():
    bad = copy.deepcopy(VALID_NUMERIC)
    bad["features"][0]["quality_flags"] = ["looks_weird"]
    with pytest.raises(jsonschema.ValidationError):
        validate(bad, "numeric_evidence")


def test_numeric_missing_band_quality_rejected():
    bad = copy.deepcopy(VALID_NUMERIC)
    del bad["band_quality"]
    with pytest.raises(jsonschema.ValidationError):
        validate(bad, "numeric_evidence")


# ---------------------------------------------------------------------------
# vlm_evidence invalid cases
# ---------------------------------------------------------------------------


def test_vlm_unknown_label_rejected():
    bad = copy.deepcopy(VALID_VLM)
    bad["observations"][0]["label"] = "agn"
    with pytest.raises(jsonschema.ValidationError):
        validate(bad, "vlm_evidence")


def test_vlm_confidence_out_of_range_rejected():
    bad = copy.deepcopy(VALID_VLM)
    bad["observations"][0]["confidence"] = 1.5
    with pytest.raises(jsonschema.ValidationError):
        validate(bad, "vlm_evidence")


def test_vlm_coordinate_field_rejected():
    """VLM must not emit coordinates."""
    bad = copy.deepcopy(VALID_VLM)
    bad["observations"][0]["x_pix"] = 120.0
    with pytest.raises(jsonschema.ValidationError):
        validate(bad, "vlm_evidence")


def test_vlm_bad_evidence_region_format_rejected():
    bad = copy.deepcopy(VALID_VLM)
    bad["observations"][0]["evidence_regions"] = ["just-a-string"]
    with pytest.raises(jsonschema.ValidationError):
        validate(bad, "vlm_evidence")


def test_vlm_parse_failed_with_observations_rejected():
    bad = copy.deepcopy(VALID_VLM)
    bad["parse_status"] = "PARSE_FAILED"
    with pytest.raises(jsonschema.ValidationError):
        validate(bad, "vlm_evidence")


def test_vlm_parse_failed_without_observations_passes():
    ok = {
        "schema_version": "1.0",
        "round_id": "r1",
        "parse_status": "PARSE_FAILED",
        "observations": [],
    }
    validate(ok, "vlm_evidence")


# ---------------------------------------------------------------------------
# decision_artifact invalid cases
# ---------------------------------------------------------------------------


def test_decision_propose_add_without_component_rejected():
    bad = copy.deepcopy(VALID_DECISION_PROPOSE)
    del bad["action"]["component"]
    with pytest.raises(jsonschema.ValidationError):
        validate(bad, "decision_artifact")


def test_decision_replace_without_targets_rejected():
    bad = copy.deepcopy(VALID_DECISION_PROPOSE)
    bad["action"] = {"action_type": "PROPOSE_REPLACE", "replace_from": "disk"}
    with pytest.raises(jsonschema.ValidationError):
        validate(bad, "decision_artifact")


def test_decision_replace_with_targets_passes():
    ok = copy.deepcopy(VALID_DECISION_PROPOSE)
    ok["action"] = {
        "action_type": "PROPOSE_REPLACE",
        "replace_from": "disk",
        "replace_to": "edge_on_disk",
    }
    validate(ok, "decision_artifact")


def test_decision_unknown_component_rejected():
    bad = copy.deepcopy(VALID_DECISION_PROPOSE)
    bad["action"]["component"] = "spiral_arm"
    with pytest.raises(jsonschema.ValidationError):
        validate(bad, "decision_artifact")


def test_decision_empty_rule_trace_rejected():
    bad = copy.deepcopy(VALID_DECISION_PROPOSE)
    bad["rule_trace"] = []
    with pytest.raises(jsonschema.ValidationError):
        validate(bad, "decision_artifact")


def test_decision_evaluate_without_refit_evaluation_rejected():
    bad = copy.deepcopy(VALID_DECISION_EVALUATE)
    del bad["refit_evaluation"]
    with pytest.raises(jsonschema.ValidationError):
        validate(bad, "decision_artifact")


def test_decision_accept_refit_in_propose_state_rejected():
    bad = copy.deepcopy(VALID_DECISION_PROPOSE)
    bad["action"] = {"action_type": "ACCEPT_REFIT", "component": "bar"}
    with pytest.raises(jsonschema.ValidationError):
        validate(bad, "decision_artifact")


def test_decision_propose_add_in_evaluate_state_rejected():
    bad = copy.deepcopy(VALID_DECISION_EVALUATE)
    bad["action"] = {"action_type": "PROPOSE_ADD", "component": "bar"}
    with pytest.raises(jsonschema.ValidationError):
        validate(bad, "decision_artifact")


def test_decision_bic_missing_comparable_rejected():
    bad = copy.deepcopy(VALID_DECISION_EVALUATE)
    del bad["refit_evaluation"]["bic"]["comparable"]
    with pytest.raises(jsonschema.ValidationError):
        validate(bad, "decision_artifact")


def test_decision_missing_thresholds_version_rejected():
    bad = copy.deepcopy(VALID_DECISION_PROPOSE)
    del bad["thresholds_version"]
    with pytest.raises(jsonschema.ValidationError):
        validate(bad, "decision_artifact")


def test_decision_inconclusive_needs_no_component():
    ok = copy.deepcopy(VALID_DECISION_PROPOSE)
    ok["action"] = {"action_type": "INCONCLUSIVE"}
    validate(ok, "decision_artifact")
