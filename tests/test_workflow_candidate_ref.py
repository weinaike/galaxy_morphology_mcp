"""N23 contract tests: candidate_ref identity, refit single-entry response, capability."""

from __future__ import annotations

import copy

import pytest
from jsonschema import ValidationError

from schemas import iter_errors, validate
from component_analysis.policy import (
    PolicyState,
    build_candidate_ref,
    compute_candidate_key,
    evaluate_refit_with_policy,
)
from tools.workflow_lifecycle import workflow_capabilities, workflow_evaluate_refit


def _candidate_ref(**overrides):
    payload = {
        "action": {
            "action_type": "PROPOSE_ADD",
            "component": "companion",
            "target_model_label": "candidate_12",
        },
        "rule_id": "COMPANION_NUMERIC_VLM_V1",
        "baseline_artifact_fingerprint": "baseline-fp-1",
        "evidence_fingerprint": "evidence-fp-1",
        "target": "candidate_12",
        "decision_ref": "decision-1",
    }
    payload.update(overrides)
    return build_candidate_ref(**payload)


def _reject_evaluation():
    return {
        "fit_converged": "no",
        "residual_improved": "yes",
        "parameters_physical": "yes",
    }


# ---------------------------------------------------------------------------
# candidate_ref schema
# ---------------------------------------------------------------------------


def test_candidate_ref_schema_accepts_original_action():
    candidate_ref = _candidate_ref()
    validate(candidate_ref, "candidate_ref")
    assert iter_errors(candidate_ref, "candidate_ref") == []


def test_candidate_ref_schema_rejects_verdict_action():
    with pytest.raises(ValidationError):
        _candidate_ref(
            action={
                "action_type": "REJECT_REFIT",
                "component": "companion",
            }
        )


def test_candidate_ref_schema_requires_real_fingerprints():
    with pytest.raises(ValidationError):
        _candidate_ref(evidence_fingerprint="")
    with pytest.raises(ValidationError):
        _candidate_ref(baseline_artifact_fingerprint="")


def test_candidate_ref_rejects_refit_parameters_without_plan():
    with pytest.raises(ValidationError):
        _candidate_ref(
            action={
                "action_type": "REFIT_PARAMETERS",
                "component": "disk",
                "parameter_changes": [],
            }
        )


# ---------------------------------------------------------------------------
# server-side candidate key
# ---------------------------------------------------------------------------


def test_candidate_key_is_deterministic_and_stable():
    assert compute_candidate_key(_candidate_ref()) == compute_candidate_key(_candidate_ref())


def test_candidate_key_separates_target_baseline_and_evidence():
    base = compute_candidate_key(_candidate_ref())
    other_target = compute_candidate_key(_candidate_ref(target="candidate_6"))
    other_baseline = compute_candidate_key(
        _candidate_ref(baseline_artifact_fingerprint="baseline-fp-2")
    )
    other_evidence = compute_candidate_key(_candidate_ref(evidence_fingerprint="evidence-fp-2"))
    assert len({base, other_target, other_baseline, other_evidence}) == 4


def test_build_candidate_ref_assigns_stable_derived_id():
    first = _candidate_ref()
    second = _candidate_ref()
    assert first["candidate_id"] == second["candidate_id"]
    assert first["candidate_id"].startswith("cand-")


# ---------------------------------------------------------------------------
# decision artifact v1.2 and v1.1 read compatibility
# ---------------------------------------------------------------------------


def test_evaluate_refit_with_candidate_ref_stamps_v1_2_and_server_key():
    state = PolicyState(object_id="obj1")
    candidate_ref = _candidate_ref()
    decision = evaluate_refit_with_policy(
        round_id="r2",
        component="companion",
        refit_evaluation=_reject_evaluation(),
        state=state,
        candidate_action_type="PROPOSE_ADD",
        candidate_ref=candidate_ref,
    )
    assert decision["schema_version"] == "1.2"
    assert decision["candidate_ref"]["candidate_id"] == candidate_ref["candidate_id"]
    validate(decision, "decision_artifact")
    assert compute_candidate_key(candidate_ref) in state.rejected_candidate_keys


def test_v1_2_evaluate_refit_without_candidate_ref_is_invalid():
    legacy = evaluate_refit_with_policy(
        round_id="r2",
        component="companion",
        refit_evaluation=_reject_evaluation(),
        state=PolicyState(),
    )
    assert legacy["schema_version"] == "1.1"
    validate(copy.deepcopy(legacy), "decision_artifact")

    upgraded = copy.deepcopy(legacy)
    upgraded["schema_version"] = "1.2"
    assert iter_errors(upgraded, "decision_artifact")
    upgraded["candidate_ref"] = _candidate_ref()
    validate(upgraded, "decision_artifact")


# ---------------------------------------------------------------------------
# PolicyState queue contract
# ---------------------------------------------------------------------------


def test_policy_state_queue_roundtrip_and_v1_compat():
    state = PolicyState(object_id="obj1")
    state.pending_candidate_queue.append(
        {
            "candidate_id": "cand-abc",
            "rule_id": "COMPANION_NUMERIC_VLM_V1",
            "status": "PENDING",
            "candidate_ref": _candidate_ref(),
        }
    )
    snapshot = state.to_dict()
    assert snapshot["state_version"] == "workflow-state@v2"
    assert snapshot["pending_candidate_queue"][0]["candidate_id"] == "cand-abc"

    restored = PolicyState.from_dict(snapshot)
    assert restored.pending_candidate_queue[0]["status"] == "PENDING"

    legacy = PolicyState.from_dict({"object_id": "old-obj"})
    assert legacy.pending_candidate_queue == []


# ---------------------------------------------------------------------------
# MCP wrapper contract
# ---------------------------------------------------------------------------


def test_workflow_capabilities_actions_exclude_legacy_keep_and_continue():
    caps = workflow_capabilities()
    assert "COLLECT_EVIDENCE" in caps["actions"]
    assert "KEEP_AND_CONTINUE" not in caps["actions"]
    assert caps["legacy_read_only_actions"] == ["KEEP_AND_CONTINUE"]


def test_workflow_evaluate_refit_requires_schema_valid_candidate_ref(tmp_path):
    state_file = tmp_path / "policy_state.json"
    with pytest.raises(ValueError, match="candidate_ref is required"):
        workflow_evaluate_refit(
            round_id="r2",
            component="companion",
            refit_evaluation=_reject_evaluation(),
            state_file=str(state_file),
        )
    with pytest.raises(ValidationError):
        workflow_evaluate_refit(
            round_id="r2",
            component="companion",
            refit_evaluation=_reject_evaluation(),
            state_file=str(state_file),
            candidate_ref={
                "schema_version": "candidate-ref@v1",
                "candidate_id": "cand-x",
                "rule_id": "RULE",
                "action": {"action_type": "REJECT_REFIT", "component": "companion"},
                "baseline_artifact_fingerprint": "b",
                "evidence_fingerprint": "e",
            },
        )


def test_workflow_evaluate_refit_returns_split_response(tmp_path):
    state_file = tmp_path / "policy_state.json"
    candidate_ref = _candidate_ref()
    result = workflow_evaluate_refit(
        round_id="r2",
        component="companion",
        refit_evaluation=_reject_evaluation(),
        state_file=str(state_file),
        candidate_action_type="PROPOSE_ADD",
        candidate_ref=candidate_ref,
    )
    assert result["refit_decision"]["verdict"] == "REJECT_REFIT"
    assert result["refit_decision"]["executable"] is False
    assert result["candidate_ref"]["candidate_id"] == candidate_ref["candidate_id"]
    # Until the N25 queue lands, a rejection has no executable next decision.
    assert result["next_decision"]["decision"] is None
