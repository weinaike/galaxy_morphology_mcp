"""N25 tests: persisted candidate queue, verdict-driven advancement, scoped
termination memory, and uncontaminated evidence views."""

from __future__ import annotations

from component_analysis.policy import (
    PolicyState,
    build_candidate_ref,
    compute_candidate_key,
    decide_proposal_with_policy,
    evaluate_refit_with_policy,
    rebuild_candidate_queue,
)
from component_analysis.rules import decide_proposal
from component_analysis.workflow_bridge import evaluate_workflow_refit


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


def numeric_fixture(features, round_id="r1"):
    return {
        "schema_version": "1.0",
        "round_id": round_id,
        "manifest_ref": "manifest.json",
        "features": features,
        "band_quality": [{"band": "f200w", "passed": True}],
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


def companion_proposal(*targets):
    features = [
        feat(
            "peaks",
            "residual_local_peaks",
            1,
            candidate_regions=[
                {"region_id": rid, "band": band, "x_pix": 40.0, "y_pix": 41.0, "local_snr": 8.0}
                for rid in targets
                for band in ("f150w", "f200w", "f444w")
            ],
        ),
        feat("match", "original_source_matches", {rid: True for rid in targets}),
    ]
    observations = [obs("independent_source", target_id=rid) for rid in targets]
    return decide_proposal(
        round_id="r1",
        numeric_evidence=numeric_fixture(features),
        vlm_evidence=vlm_fixture(observations),
        current_components=["disk", "bulge"],
    )


def gates(converged="yes", residual="improved", physical="yes", **extra):
    evaluation = {
        "fit_converged": converged,
        "residual_outcome": residual,
        "parameters_physical": physical,
    }
    evaluation.update(extra)
    return evaluation


def _ref_for(item, **overrides):
    payload = {
        "action": item["action"],
        "rule_id": item["rule_id"],
        "baseline_artifact_fingerprint": "baseline-fp",
        "evidence_fingerprint": "evidence-fp",
        "target": (item["action"]).get("target_model_label"),
    }
    payload.update(overrides)
    return build_candidate_ref(**payload)


def _queued_companion_state(*targets):
    decision = companion_proposal(*targets)
    state = PolicyState(object_id="obj1")
    count = rebuild_candidate_queue(decision, state)
    return decision, state, count


def test_reject_advances_to_next_queue_candidate():
    _, state, count = _queued_companion_state("candidate_1", "candidate_6")
    assert count == 2
    first, second = state.pending_candidate_queue

    result = evaluate_workflow_refit(
        state=state,
        round_id="r2",
        component="companion",
        refit_evaluation=gates(
            bic={"bic_simple": 100.0, "bic_complex": 120.0, "bic_gain": -20.0, "comparable": True},
            reduced_chisq={"baseline": 0.5, "candidate": 0.7, "delta": 0.2, "comparable": True},
            residual="worse",
        ),
        candidate_action_type="PROPOSE_ADD",
        candidate_ref=_ref_for(first),
    )
    assert result["refit_decision"]["verdict"] == "REJECT_REFIT"
    assert first["status"] == "REJECTED"
    assert compute_candidate_key(_ref_for(first)) in state.rejected_candidate_keys
    # The queue supplies the next executable decision, not the verdict action.
    next_decision = result["next_decision"]
    assert next_decision["next_transition"] == "RUN_FIT"
    assert next_decision["decision"]["action"]["action_type"] == "PROPOSE_ADD"
    assert next_decision["decision"]["action"]["target_model_label"] == "candidate_6"
    assert second["status"] == "SELECTED"


def test_accept_invalidates_remaining_queue():
    _, state, count = _queued_companion_state("candidate_1", "candidate_6")
    assert count == 2
    first, second = state.pending_candidate_queue

    result = evaluate_workflow_refit(
        state=state,
        round_id="r2",
        component="companion",
        refit_evaluation=gates(
            bic={"bic_simple": 100.0, "bic_complex": 60.0, "bic_gain": 40.0, "comparable": True},
            reduced_chisq={"baseline": 0.5, "candidate": 0.45, "delta": -0.05, "comparable": True},
        ),
        candidate_action_type="PROPOSE_ADD",
        candidate_ref=_ref_for(first),
    )
    assert result["refit_decision"]["verdict"] == "ACCEPT_REFIT"
    assert first["status"] == "ACCEPTED"
    assert second["status"] == "INVALIDATED"
    assert result["next_decision"]["next_transition"] == "ANALYZE_NEW_BASELINE"
    assert result["next_decision"]["decision"] is None


def test_exhausted_queue_stops_with_review():
    _, state, count = _queued_companion_state("candidate_1")
    assert count == 1
    only = state.pending_candidate_queue[0]
    result = evaluate_workflow_refit(
        state=state,
        round_id="r2",
        component="companion",
        refit_evaluation=gates(residual="worse"),
        candidate_action_type="PROPOSE_ADD",
        candidate_ref=_ref_for(only),
    )
    assert result["refit_decision"]["verdict"] == "REJECT_REFIT"
    assert result["next_decision"]["next_transition"] == "STOPPED_NEEDS_REVIEW"
    assert result["next_decision"]["decision"] is None


def test_duplicate_verdict_submission_is_idempotent():
    _, state, _ = _queued_companion_state("candidate_1", "candidate_6")
    first = state.pending_candidate_queue[0]
    evaluation = gates(residual="worse")
    args = dict(
        state=state,
        round_id="r2",
        component="companion",
        refit_evaluation=evaluation,
        candidate_action_type="PROPOSE_ADD",
        candidate_ref=_ref_for(first),
    )
    first_result = evaluate_workflow_refit(**args)
    keys_after_first = set(state.rejected_candidate_keys)
    # Re-submitting the same candidate must not consume another queue item.
    state.pending_candidate_queue[1]["status"] = "PENDING"
    second_result = evaluate_workflow_refit(**args)
    assert second_result["refit_decision"]["verdict"] == "REJECT_REFIT"
    assert set(state.rejected_candidate_keys) == keys_after_first


def test_target_scoped_termination_does_not_block_other_targets():
    decision = decide_proposal(
        round_id="r1",
        numeric_evidence=numeric_fixture([
            feat(
                "peaks",
                "residual_local_peaks",
                1,
                candidate_regions=[
                    {"region_id": "candidate_1", "band": band, "x_pix": 40.0, "y_pix": 41.0, "local_snr": 8.0}
                    for band in ("f150w", "f200w", "f444w")
                ] + [
                    {"region_id": "candidate_2", "band": band, "x_pix": 44.0, "y_pix": 39.0, "local_snr": 8.0}
                    for band in ("f150w", "f200w", "f444w")
                ],
            ),
            feat("match", "original_source_matches", {"candidate_1": True, "candidate_2": True}),
        ]),
        vlm_evidence=vlm_fixture([obs("uncertain", target_id="candidate_1")]),
        current_components=["disk", "bulge"],
    )
    state = PolicyState()
    # Rule is INCONCLUSIVE for candidate_1 (blocking) and candidate_2 has no
    # observation; terminating candidate_1 must not terminate candidate_2.
    trace = next(
        t for t in decision["rule_trace"]
        if t["rule_id"] == "COMPANION_NUMERIC_VLM_V1" and t["outcome"] == "INCONCLUSIVE"
    )
    assert set(trace["inputs"]) == {"candidate_1", "candidate_2"}
    memory_key = "COMPANION_NUMERIC_VLM_V1|candidate_1,candidate_2"
    state.inconclusive_seen[memory_key] = "fp1|"
    state.terminated_rules = {memory_key}
    # A NEW evidence context (different fingerprint) is not "repeated".
    decision2 = decide_proposal_with_policy(
        round_id="r3",
        numeric_evidence=numeric_fixture([
            feat(
                "peaks",
                "residual_local_peaks",
                1,
                candidate_regions=[
                    {"region_id": "candidate_2", "band": band, "x_pix": 44.0, "y_pix": 39.0, "local_snr": 8.0}
                    for band in ("f150w", "f200w", "f444w")
                ],
            ),
            feat("match", "original_source_matches", {"candidate_2": True}),
        ], round_id="r3"),
        vlm_evidence=vlm_fixture([obs("independent_source", target_id="candidate_2")], round_id="r3"),
        current_components=["disk", "bulge"],
        state=state,
        evidence_fingerprint="fp2",
    )
    # candidate_2 now confirmed: a companion action is available despite the
    # earlier termination scoped to candidate_1.
    assert decision2["action"]["action_type"] == "PROPOSE_ADD"
    assert decision2["action"]["target_model_label"] == "candidate_2"


def test_numeric_plus_vlm_view_not_polluted_by_fallback():
    decision = decide_proposal_with_policy(
        round_id="r1",
        numeric_evidence=numeric_fixture([
            feat("extent", "source_extent_psf_ratio", 4.0),
            feat("geom", "outer_isophote_geometry", {"pa_scatter_deg": 5.0, "q_range": 0.05}),
        ]),
        vlm_evidence=vlm_fixture([], parse_status="PARSE_FAILED"),
        current_components=[],
        state=PolicyState(),
        evidence_fingerprint="fp",
    )
    views = decision["evidence_views"]
    assert "vlm" not in views
    assert views["numeric_plus_vlm"]["parse_status"] == "PARSE_FAILED"
    # The original view keeps its own rule output; the numeric-only retry does
    # not overwrite it.
    assert views["numeric_only"]["parse_status"] == "OK"
    assert isinstance(views["numeric_plus_vlm"]["rule_trace"], list)
    assert isinstance(views["numeric_plus_vlm"]["candidate_actions"], list)


def test_resolve_persists_candidate_queue(tmp_path):
    from component_analysis.decision_service import resolve_workflow_proposal

    proposal = {
        "schema_version": "1.0",
        "workflow_mode": "multi-band",
        "object_id": "obj1",
        "round_id": "r1",
        "manifest_ref": "manifest.json",
        "numeric_evidence": numeric_fixture([
            feat(
                "peaks",
                "residual_local_peaks",
                1,
                candidate_regions=[
                    {"region_id": "candidate_1", "band": band, "x_pix": 40.0, "y_pix": 41.0, "local_snr": 8.0}
                    for band in ("f150w", "f200w", "f444w")
                ],
            ),
            feat("match", "original_source_matches", {"candidate_1": True}),
        ]),
        "vlm_evidence": vlm_fixture([obs("independent_source", target_id="candidate_1")]),
        "evidence_fingerprint": "fp1",
        "rule_decision": companion_proposal("candidate_1"),
        "raw_decision": {},
        "candidate_actions": [],
        "rule_trace": [],
        "termination_checks": [],
        "current_components": ["disk", "bulge"],
        "provider": {"status": "DISABLED", "attempts": []},
    }
    state = PolicyState(object_id="obj1")
    result = resolve_workflow_proposal(proposal, state=state, output_dir=tmp_path)
    assert result["decision"]["action"]["action_type"] == "PROPOSE_ADD"
    queue = state.pending_candidate_queue
    assert len(queue) == 1
    assert queue[0]["status"] in {"PENDING", "SELECTED"}
    assert queue[0]["rule_id"] == "COMPANION_NUMERIC_VLM_V1"
    # Duplicate resolve must not append a second copy of the queue.
    resolve_workflow_proposal(proposal, state=state, output_dir=tmp_path)
    assert len(state.pending_candidate_queue) == 1


def test_stable_rejection_key_blocks_reprisal_despite_evidence_drift():
    """2026-10-28 ruling 3: an action rejected on a baseline is never
    re-proposed on that baseline, even when the evidence fingerprint drifts
    (VLM responses vary between rounds)."""
    from component_analysis.policy import (
        compute_stable_candidate_key,
        rebuild_candidate_queue,
    )

    from component_analysis.policy import compute_baseline_artifact_fingerprint

    _, state, _ = _queued_companion_state("candidate_1")
    state.current_config_ref = "cfg"
    state.current_result_refs = {"result_files.0": "r.fits", "summary_file": "s.txt"}
    baseline_fp = compute_baseline_artifact_fingerprint("cfg", ["r.fits"], "s.txt")
    item = state.pending_candidate_queue[0]
    candidate_ref = _ref_for(item, baseline_artifact_fingerprint=baseline_fp)
    evaluate_refit_with_policy(
        round_id="r9",
        component="companion",
        refit_evaluation=gates(residual="worse"),
        state=state,
        candidate_action_type="PROPOSE_ADD",
        candidate_ref=candidate_ref,
    )
    assert compute_stable_candidate_key(candidate_ref) in state.rejected_stable_keys

    # A later proposal rebuilds the queue on the SAME baseline with a DIFFERENT
    # evidence fingerprint; the identical action must not be selectable.
    later = companion_proposal("candidate_1")
    rebuild_candidate_queue(later, state)
    drifted = state.pending_candidate_queue[0]
    assert drifted["action"]["target_model_label"] == "candidate_1"
    decision = decide_proposal_with_policy(
        round_id="r10",
        numeric_evidence=numeric_fixture([
            feat(
                "peaks",
                "residual_local_peaks",
                1,
                candidate_regions=[
                    {"region_id": "candidate_1", "band": b, "x_pix": 40.0, "y_pix": 41.0, "local_snr": 8.0}
                    for b in ("f150w", "f200w", "f444w")
                ],
            ),
            feat("match", "original_source_matches", {"candidate_1": True}),
        ], round_id="r10"),
        vlm_evidence=vlm_fixture([obs("independent_source", target_id="candidate_1")], round_id="r10"),
        current_components=["disk", "bulge"],
        state=state,
        evidence_fingerprint="totally-different-fingerprint",
    )
    assert drifted["status"] == "REJECTED"
    assert drifted.get("rejection_source") == "rejected_stable_keys"
    action = decision.get("action") or {}
    assert action.get("action_type") != "PROPOSE_ADD"


def test_evaluate_does_not_pollute_resolve_duplicate_cache():
    """N27-run3 finding: evaluate used to overwrite last_resolved_decision,
    so a later resolve hitting the duplicate branch (same evidence
    fingerprint, e.g. two REFUSED VLM rounds) replayed an EVALUATE_REFIT
    verdict as a proposal resolution."""
    from component_analysis.decision_service import resolve_workflow_proposal
    from component_analysis.policy import evaluate_refit_with_policy

    state = PolicyState(object_id="obj1")
    # First resolve: a real PROPOSE decision cached.
    decision = companion_proposal("candidate_1")
    proposal = {
        "schema_version": "1.0", "workflow_mode": "multi-band", "object_id": "obj1",
        "round_id": "r1", "manifest_ref": "manifest.json",
        "numeric_evidence": numeric_fixture([
            feat("peaks", "residual_local_peaks", 1, candidate_regions=[
                {"region_id": "candidate_1", "band": b, "x_pix": 40.0, "y_pix": 41.0, "local_snr": 8.0}
                for b in ("f150w", "f200w", "f444w")
            ]),
            feat("match", "original_source_matches", {"candidate_1": True}),
        ]),
        "vlm_evidence": vlm_fixture([obs("independent_source", target_id="candidate_1")]),
        "evidence_fingerprint": "fp-stable",
        "rule_decision": decision, "raw_decision": {}, "candidate_actions": [],
        "rule_trace": [], "termination_checks": [], "current_components": ["disk", "bulge"],
        "provider": {"status": "DISABLED", "attempts": []},
    }
    first = resolve_workflow_proposal(proposal, state=state)
    assert first["decision"]["state"] == "PROPOSE"
    # An evaluation records its verdict without touching the cache.
    evaluate_refit_with_policy(
        round_id="r2", component="companion",
        refit_evaluation=gates(residual="worse"), state=state,
        candidate_action_type="PROPOSE_ADD",
        candidate_ref=build_candidate_ref(
            action={"action_type": "PROPOSE_ADD", "component": "companion", "target_model_label": "candidate_1"},
            rule_id="COMPANION_NUMERIC_VLM_V1",
            baseline_artifact_fingerprint="cache-fp",
            evidence_fingerprint="fp-stable",
            target="candidate_1",
        ),
    )
    # Same proposal again (duplicate fingerprint): must still be a PROPOSE
    # decision, never the REJECT_REFIT verdict.
    second = resolve_workflow_proposal(proposal, state=state)
    assert second["decision"]["state"] == "PROPOSE"
    assert second["decision"]["action"]["action_type"] == "PROPOSE_ADD"


def test_queue_advance_skips_stable_rejected_candidate():
    """Queue advancement (build_next_candidate_decision) must not serve a
    candidate whose stable key was rejected on this baseline (1071 run4 r09)."""
    from component_analysis.policy import (
        build_candidate_ref,
        build_next_candidate_decision,
        evaluate_refit_with_policy,
        rebuild_candidate_queue,
    )

    _, state, _ = _queued_companion_state("candidate_1", "candidate_6")
    state.current_config_ref = "cfg"
    state.current_result_refs = {"result_files.0": "r.fits", "summary_file": "s.txt"}
    from component_analysis.policy import compute_baseline_artifact_fingerprint

    baseline_fp = compute_baseline_artifact_fingerprint("cfg", ["r.fits"], "s.txt")
    first = state.pending_candidate_queue[0]
    evaluate_refit_with_policy(
        round_id="r9", component="companion",
        refit_evaluation=gates(residual="worse"), state=state,
        candidate_action_type="PROPOSE_ADD",
        candidate_ref=_ref_for(first, baseline_artifact_fingerprint=baseline_fp),
    )
    # The queues items carry the state-derived fingerprint; advancing must
    # skip the rejected first candidate and serve the second.
    next_decision = build_next_candidate_decision(
        state, round_id="r10-next", previous_action_type="REJECT_REFIT"
    )
    assert next_decision is not None
    assert next_decision["action"]["target_model_label"] != first["action"].get("target_model_label")
    assert first["status"] == "REJECTED"
