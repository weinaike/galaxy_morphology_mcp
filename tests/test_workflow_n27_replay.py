"""N27 read-only replay over the persisted 104 canary (jwst0831-20260910T095811Z).

Reproduces the audited N23–N26 gaps from the historical artifacts (dual
arbitration, verdict-as-action rejection keys with empty identity fields) and
verifies the fixed logic against the same inputs WITHOUT rewriting any
historical lifecycle, PolicyState, best round or scientific decision.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from component_analysis.policy import (
    PolicyState,
    build_candidate_ref,
    compute_candidate_key,
)
from component_analysis.workflow_bridge import (
    complete_workflow_candidate,
    evaluate_workflow_refit,
)

CANARY_ROOT = Path(
    "/home/www/2026/GALFITS_examples/jwst0831/104/output/workflow/"
    "jwst0831-20260910T095811Z/artifacts"
)


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture()
def round_001() -> Path:
    round_dir = CANARY_ROOT / "round_001"
    if not round_dir.is_dir():
        pytest.skip("persisted 104 canary artifacts are not available")
    return round_dir


def test_replay_reproduces_dual_arbitration_gap(round_001, tmp_path):
    """Old gap C1: one candidate produced two full arbitration outputs."""
    completion = _load(round_001 / "candidate_completion.json")
    refit = _load(round_001 / "refit_decision.json")
    completion_verdict = (completion.get("action_summary") or {}).get("refit_verdict")
    refit_verdict = (refit.get("action_summary") or {}).get("refit_verdict")
    assert completion_verdict == "REJECTED" and refit_verdict == "REJECTED"
    assert (completion.get("decision") or {}).get("action", {}).get("action_type") == "REJECT_REFIT"
    assert (refit.get("decision") or {}).get("action", {}).get("action_type") == "REJECT_REFIT"
    # Derived audit note only; historical files untouched.
    report = tmp_path / "reproduced_gaps.json"
    report.write_text(
        json.dumps(
            {
                "round": str(round_001),
                "gaps": [
                    "dual arbitration: candidate_completion.json and refit_decision.json "
                    "each carry a full REJECT_REFIT verdict",
                    "empty-identity rejection key (see companion test)",
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def test_replay_reproduces_empty_identity_rejection_key(round_001):
    """Old gaps C2/C3: the rejection key is a verdict action with null identity."""
    refit = _load(round_001 / "refit_decision.json")
    keys = (refit.get("policy_state") or {}).get("rejected_candidate_keys") or []
    assert len(keys) == 1
    key = json.loads(keys[0])
    assert key["action_type"] == "REJECT_REFIT"
    assert key["evidence_fingerprint"] == ""
    assert key["baseline_config_checksum"] is None
    assert key["target_model_label"] is None


def test_replay_new_logic_single_arbitration_idempotent_next_decision(round_001, tmp_path):
    """Fixed behavior replayed on the same inputs, into a fresh state."""
    resolution = _load(round_001 / "resolution" / "resolution.json")
    manifest = _load(round_001 / "workflow_manifest.json")
    candidate_manifest = _load(round_001 / "candidate_manifest.json")
    completion = _load(round_001 / "candidate_completion.json")
    original_action = (resolution.get("decision") or {}).get("action")
    assert original_action["action_type"] == "PROPOSE_ADD"

    # Comparator adapter: evidence only, no verdict / lifecycle / state.
    adapter = complete_workflow_candidate(
        baseline_manifest=manifest,
        candidate_manifest=candidate_manifest,
        baseline_fit_result=None,
        candidate_fit_result=_load(round_001 / "candidate_manifest.json"),
        component="companion",
        candidate_action_type="PROPOSE_ADD",
    )
    assert "decision" not in adapter
    assert "action_summary" not in adapter
    assert "lifecycle" not in adapter

    evaluation = completion["evaluation"]
    candidate_ref = build_candidate_ref(
        action=original_action,
        rule_id="COMPANION_NUMERIC_VLM_V1",
        baseline_artifact_fingerprint="replay-baseline-fp",
        evidence_fingerprint="replay-evidence-fp",
        target=original_action.get("target_model_label"),
    )
    state = PolicyState(object_id="104")
    state.pending_candidate_queue = [
        {
            "candidate_id": "cq-replay",
            "rule_id": "COMPANION_NUMERIC_VLM_V1",
            "action": dict(original_action),
            "priority": 4,
            "safety_rank": 3,
            "status": "SELECTED",
            "barrier": None,
        }
    ]
    kwargs = dict(
        state=state,
        round_id=str(candidate_manifest["round_id"]),
        component="companion",
        refit_evaluation=evaluation,
        candidate_action_type="PROPOSE_ADD",
        candidate_ref=candidate_ref,
        lifecycle_file=tmp_path / "candidate_lifecycle.json",
        state_file=tmp_path / "policy_state.json",
    )
    first = evaluate_workflow_refit(**kwargs)
    assert first["duplicate_submission"] is False
    # One arbitration: the rejection is supported by the recorded evidence
    # (obj0.re boundary + BIC_gain=-163 + worsened reduced chisq).
    assert first["refit_decision"]["verdict"] == "REJECT_REFIT"
    # The verdict is NOT the next machine action; with an exhausted queue the
    # transition is an explicit stop, never action_type=REJECT_REFIT.
    assert first["next_decision"]["next_transition"] == "STOPPED_NEEDS_REVIEW"
    assert first["next_decision"]["decision"] is None
    # Rejection memory now carries the full candidate identity.
    assert compute_candidate_key(candidate_ref) in state.rejected_candidate_keys
    assert state.pending_candidate_queue[0]["status"] == "REJECTED"

    # Duplicate submission: same verdict, no further consumption.
    second = evaluate_workflow_refit(**kwargs)
    assert second["duplicate_submission"] is True
    assert second["refit_decision"]["verdict"] == "REJECT_REFIT"
    assert len(state.pending_candidate_queue) == 1
