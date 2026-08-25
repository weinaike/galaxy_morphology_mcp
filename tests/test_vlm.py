"""Tests for the shadow-mode controlled VLM JSON adapter."""

import copy
import json

import pytest

from component_analysis import (
    CONTROLLED_LABELS,
    PROMPT_VERSION,
    allowed_target_ids,
    build_vlm_prompt,
    make_unavailable_vlm_evidence,
    parse_vlm_response,
)
from schemas import validate


def numeric_fixture(round_id="r1"):
    return {
        "schema_version": "1.0",
        "round_id": round_id,
        "manifest_ref": "manifest.json",
        "features": [
            {
                "feature_id": "peaks",
                "name": "residual_local_peaks",
                "status": "AVAILABLE",
                "value": 1,
                "source": {"band": "f200w"},
                "candidate_regions": [
                    {
                        "region_id": "candidate_1",
                        "band": "f200w",
                        "x_pix": 143.2,
                        "y_pix": 88.9,
                        "local_snr": 12.4,
                    }
                ],
            }
        ],
        "band_quality": [{"band": "f200w", "passed": True}],
    }


def response_fixture(observations=(), round_id="r1"):
    return {
        "schema_version": "1.0",
        "round_id": round_id,
        "parse_status": "OK",
        "observations": list(observations),
    }


def observation(label, target_id="central", confidence=0.9, **extra):
    item = {
        "target_id": target_id,
        "label": label,
        "confidence": confidence,
    }
    item.update(extra)
    return item


def parse(payload, numeric=None, **kwargs):
    return parse_vlm_response(
        json.dumps(payload),
        round_id="r1",
        numeric_evidence=numeric or numeric_fixture(),
        **kwargs,
    )


def assert_parse_failed(payload):
    evidence, error = parse(payload)
    validate(evidence, "vlm_evidence")
    assert evidence["parse_status"] == "PARSE_FAILED"
    assert evidence["observations"] == []
    assert error


def test_allowed_targets_come_only_from_numeric_candidate_regions():
    assert allowed_target_ids(numeric_fixture()) == ("central", "candidate_1")


def test_prompt_is_versioned_label_only_and_does_not_leak_coordinates():
    prompt = build_vlm_prompt(round_id="r1", numeric_evidence=numeric_fixture())
    assert PROMPT_VERSION in prompt
    assert "candidate_1" in prompt
    assert "143.2" not in prompt
    assert "88.9" not in prompt
    assert all(label in prompt for label in CONTROLLED_LABELS)
    assert "spheroid_like" not in prompt
    assert "中心 Bulge、单 Sérsic 光球和 Disk 的分流只由数值证据决定" in prompt
    assert "band:panel:region_id" in prompt
    assert "nircam_f200w:residual:central_r5px" in prompt
    assert "只输出一个 JSON 对象" in prompt


def test_valid_response_is_wrapped_with_controlled_metadata():
    payload = response_fixture(
        [
            observation("disk_like"),
            observation("spiral_arm"),
            observation("independent_source", target_id="candidate_1"),
        ]
    )
    evidence, error = parse(payload, model_id="test-vlm")
    assert error is None
    assert evidence["parse_status"] == "OK"
    assert evidence["model_id"] == "test-vlm"
    assert evidence["prompt_version"] == PROMPT_VERSION
    validate(evidence, "vlm_evidence")


def test_valid_evidence_region_contract_is_accepted():
    region = "nircam_f200w:residual:central_r5px"
    payload = response_fixture(
        [observation("disk_like", evidence_regions=[region])]
    )
    evidence, error = parse(payload)
    assert error is None
    assert evidence["observations"][0]["evidence_regions"] == [region]


def test_legacy_spheroid_label_is_rejected_by_the_v12_parser():
    payload = response_fixture([observation("spheroid_like")])
    evidence, error = parse(payload)
    assert evidence["parse_status"] == "PARSE_FAILED"
    assert "legacy VLM label" in error


def test_markdown_fenced_json_is_rejected_as_non_strict():
    fence = chr(96) * 3
    raw = f"{fence}json\n{json.dumps(response_fixture())}\n{fence}"
    evidence, error = parse_vlm_response(
        raw,
        round_id="r1",
        numeric_evidence=numeric_fixture(),
    )
    assert evidence["parse_status"] == "PARSE_FAILED"
    assert "strict JSON" in error


def test_unknown_label_is_downgraded():
    assert_parse_failed(response_fixture([observation("agn")]))


def test_missing_required_field_is_downgraded():
    item = observation("disk_like")
    del item["confidence"]
    assert_parse_failed(response_fixture([item]))


def test_coordinate_field_is_downgraded():
    assert_parse_failed(response_fixture([observation("disk_like", x_pix=42.0)]))


def test_target_not_issued_by_numeric_layer_is_downgraded():
    evidence, error = parse(
        response_fixture(
            [observation("independent_source", target_id="invented_candidate")]
        )
    )
    assert evidence["parse_status"] == "PARSE_FAILED"
    assert "not issued by the numeric layer" in error


@pytest.mark.parametrize(
    "labels",
    [
        ("independent_source", "clump"),
        ("bar_like", "diffraction_psf"),
        ("none", "disk_like"),
        ("uncertain", "spiral_arm"),
    ],
)
def test_conflicting_labels_are_downgraded(labels):
    payload = response_fixture([observation(label) for label in labels])
    evidence, error = parse(payload)
    assert evidence["parse_status"] == "PARSE_FAILED"
    assert "conflict" in error


def test_explicit_label_conflict_flag_is_downgraded():
    payload = response_fixture(
        [observation("uncertain", quality_flags=["label_conflict"])]
    )
    evidence, error = parse(payload)
    assert evidence["parse_status"] == "PARSE_FAILED"
    assert "label conflict" in error


def test_low_quality_requires_uncertain_label():
    invalid = response_fixture(
        [observation("bar_like", quality_flags=["low_image_quality"])]
    )
    assert_parse_failed(invalid)

    valid = response_fixture(
        [observation("uncertain", quality_flags=["low_image_quality"])]
    )
    evidence, error = parse(valid)
    assert error is None
    assert evidence["parse_status"] == "OK"


@pytest.mark.parametrize("status", ["PARSE_FAILED", "TIMEOUT", "REFUSED"])
def test_unavailable_status_builds_schema_valid_empty_artifact(status):
    evidence = make_unavailable_vlm_evidence(
        round_id="r1",
        status=status,
        model_id="test-vlm",
    )
    assert evidence["parse_status"] == status
    assert evidence["observations"] == []
    validate(evidence, "vlm_evidence")


def test_unavailable_builder_rejects_ok_status():
    with pytest.raises(ValueError):
        make_unavailable_vlm_evidence(round_id="r1", status="OK")


def test_numeric_round_mismatch_raises_before_prompt_or_parse():
    numeric = numeric_fixture(round_id="other")
    with pytest.raises(ValueError):
        build_vlm_prompt(round_id="r1", numeric_evidence=numeric)
    with pytest.raises(ValueError):
        parse_vlm_response(
            json.dumps(response_fixture()),
            round_id="r1",
            numeric_evidence=numeric,
        )


def test_model_cannot_override_adapter_metadata():
    payload = copy.deepcopy(response_fixture())
    payload["model_id"] = "invented-model"
    payload["prompt_version"] = "invented-prompt"
    evidence, error = parse(payload, model_id="actual-model")
    assert error is None
    assert evidence["model_id"] == "actual-model"
    assert evidence["prompt_version"] == PROMPT_VERSION
