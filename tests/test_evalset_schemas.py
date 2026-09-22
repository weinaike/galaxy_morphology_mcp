"""Schema and checksum contracts for the component evalset S0-S2 boundary."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import ValidationError

from component_analysis.evalset.config import build_run_config
from schemas import SCHEMA_NAMES, load_schema, validate


def test_all_evalset_schemas_are_registered_and_valid() -> None:
    names = [name for name in SCHEMA_NAMES if name.startswith("evaluation_")]
    assert len(names) == 14
    for name in names:
        schema = load_schema(name)
        assert schema["additionalProperties"] is False


def test_run_config_binds_design_checksum_and_rule_version(tmp_path: Path) -> None:
    config = build_run_config(
        "inventory",
        output_root=tmp_path / "artifacts",
        single_band_root=tmp_path / "single",
        multi_band_root=tmp_path / "multi",
        run_id="evalset-20260910T000000Z",
    ).as_dict()
    validate(config, "evaluation_run_config")
    assert len(config["design_sha256"]) == 64
    assert config["adjudication_rule_version"] == "component-evalset-v2"

    invalid = dict(config)
    invalid["adjudication_rule_version"] = "component-evalset-v0"
    with pytest.raises(ValidationError):
        validate(invalid, "evaluation_run_config")


def test_proposal_rejects_extra_fields() -> None:
    proposal = {
        "schema_version": "evaluation-pilot-selection-proposal@v1",
        "status": "PROPOSAL_REQUIRES_USER_CONFIRMATION",
        "target_object_count": 0,
        "selection_policy": "fixture",
        "required_review": [],
        "objects": [],
        "unexpected": True,
    }
    with pytest.raises(ValidationError):
        validate(proposal, "evaluation_pilot_selection_proposal")
