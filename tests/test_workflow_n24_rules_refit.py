"""N24 tests: multi-target traversal, evidence-state separation, composite
ranking, comparator fail-closed band matching, fit_quality_ok, boundary
comparison semantics, and the revised multi-indicator refit matrix."""

from __future__ import annotations

import pytest

from component_analysis.policy import PolicyState, decide_proposal_with_policy
from component_analysis.rules import RuleThresholds, decide_proposal, evaluate_refit


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


def decide(features, observations=(), components=(), state=None):
    return decide_proposal_with_policy(
        round_id="r1",
        numeric_evidence=numeric_fixture(features),
        vlm_evidence=vlm_fixture(observations),
        current_components=components,
        state=state if state is not None else PolicyState(),
    )


def peaks(*regions):
    # 2026-10-28 ruling: eligible companion targets need >= 2 bands.
    return feat(
        "peaks",
        "residual_local_peaks",
        1,
        candidate_regions=[
            {"region_id": rid, "band": band, "x_pix": 40.0, "y_pix": 41.0, "local_snr": 8.0}
            for rid in regions
            for band in ("f150w", "f200w", "f444w")
        ],
    )


def gates(converged="yes", residual="improved", physical="yes", **extra):
    evaluation = {
        "fit_converged": converged,
        "residual_outcome": residual,
        "parameters_physical": physical,
    }
    evaluation.update(extra)
    return evaluation


def reduced(delta=-0.01, baseline=0.56, comparable=True):
    return {
        "baseline": baseline,
        "candidate": baseline + delta,
        "delta": delta,
        "comparable": comparable,
    }


def bic_gain(gain, comparable=True):
    return {
        "bic_simple": 1000.0,
        "bic_complex": 1000.0 - gain,
        "bic_gain": gain,
        "comparable": comparable,
    }


# ---------------------------------------------------------------------------
# Multi-target traversal (companion)
# ---------------------------------------------------------------------------


def test_companion_rule_traverses_all_confirmed_targets():
    features = [
        peaks("candidate_1", "candidate_2", "candidate_6"),
        feat("match", "original_source_matches", {
            "candidate_1": True, "candidate_2": True, "candidate_6": True,
        }),
    ]
    observations = [
        obs("independent_source", target_id="candidate_1"),
        obs("independent_source", target_id="candidate_6"),
        obs("uncertain", target_id="candidate_2"),
    ]
    decision = decide_proposal(
        round_id="r1",
        numeric_evidence=numeric_fixture(features),
        vlm_evidence=vlm_fixture(observations),
        current_components=["disk", "bulge"],
    )
    companion_candidates = [
        item for item in decision["candidate_actions"]
        if item["rule_id"] == "COMPANION_NUMERIC_VLM_V1"
    ]
    targets = {
        item["action"].get("target_model_label")
        for item in companion_candidates
    }
    # Both confirmed targets become candidates; the uncertain one does not.
    assert targets == {"candidate_1", "candidate_6"}
    inconclusive = [
        trace for trace in decision["rule_trace"]
        if trace["rule_id"] == "COMPANION_NUMERIC_VLM_V1"
        and trace["outcome"] == "INCONCLUSIVE"
    ]
    assert inconclusive and inconclusive[0]["blocking"] is False


def test_companion_uncertain_is_never_blocking():
    # 2026-10-28 ruling: companion is an optional off-axis component; its
    # evidence gap never blocks convergence and drives no collector.
    features = [
        peaks("candidate_1", "candidate_2"),
        feat("match", "original_source_matches", {"candidate_1": True, "candidate_2": True}),
    ]
    decision = decide_proposal(
        round_id="r1",
        numeric_evidence=numeric_fixture(features),
        vlm_evidence=vlm_fixture([obs("uncertain", target_id="candidate_1")]),
        current_components=["disk", "bulge"],
    )
    trace = next(
        trace for trace in decision["rule_trace"]
        if trace["rule_id"] == "COMPANION_NUMERIC_VLM_V1"
        and trace["outcome"] == "INCONCLUSIVE"
    )
    assert trace["blocking"] is False
    assert set(trace["inputs"]) == {"candidate_1", "candidate_2"}


def test_single_band_peak_is_audited_but_never_eligible():
    features = [
        feat(
            "peaks",
            "residual_local_peaks",
            1,
            candidate_regions=[
                {"region_id": "candidate_noise", "band": "f200w", "x_pix": 40.0, "y_pix": 41.0, "local_snr": 8.0}
            ],
        ),
        feat("match", "original_source_matches", {"candidate_noise": True}),
    ]
    decision = decide_proposal(
        round_id="r1",
        numeric_evidence=numeric_fixture(features),
        vlm_evidence=vlm_fixture([obs("independent_source", target_id="candidate_noise")]),
        current_components=["disk", "bulge"],
    )
    # Even with a VLM confirmation, a single-band target yields no proposal.
    companion_candidates = [
        item for item in decision["candidate_actions"]
        if item["rule_id"] == "COMPANION_NUMERIC_VLM_V1"
    ]
    assert companion_candidates == []
    audit = next(
        trace for trace in decision["rule_trace"]
        if trace["rule_id"] == "SINGLE_BAND_CANDIDATE_V1"
    )
    assert audit["blocking"] is False


def test_allowed_targets_require_three_bands():
    from component_analysis.vlm import allowed_target_ids

    features = [
        feat(
            "peaks",
            "residual_local_peaks",
            1,
            candidate_regions=[
                {"region_id": "one_band", "band": "f200w", "x_pix": 40.0, "y_pix": 41.0, "local_snr": 8.0},
            ] + [
                # 2 bands is below the 2026-10-28 second ruling (>= 3).
                {"region_id": "two_band", "band": band, "x_pix": 44.0, "y_pix": 39.0, "local_snr": 8.0}
                for band in ("f150w", "f444w")
            ] + [
                {"region_id": "three_band", "band": band, "x_pix": 48.0, "y_pix": 37.0, "local_snr": 8.0}
                for band in ("f150w", "f200w", "f444w")
            ],
        ),
    ]
    assert allowed_target_ids(numeric_fixture(features)) == ("central", "three_band")


# ---------------------------------------------------------------------------
# Evidence-state separation (missing inputs are not negative)
# ---------------------------------------------------------------------------


def test_missing_outer_axis_ratio_is_inconclusive_not_negative():
    decision = decide_proposal(
        round_id="r1",
        numeric_evidence=numeric_fixture([feat("extent", "source_extent_psf_ratio", 4.0)]),
        vlm_evidence=vlm_fixture([]),
        current_components=["disk", "bulge"],
    )
    trace = next(
        trace for trace in decision["rule_trace"] if trace["rule_id"] == "EDGE_ON_LOW_Q_V1"
    )
    assert trace["outcome"] == "INCONCLUSIVE"
    assert trace["blocking"] is False


def test_valid_single_sersic_can_converge_without_confirmed_components():
    features = [
        feat("fit", "fit_convergence_summary", {"fit_succeeded": True}, source={"band": None}),
        feat("residual", "absolute_residual_quality", {
            "one_d_clean": True, "central_elongation": False,
        }, source={"band": None}),
        feat("health", "component_parameter_health", [{
            "model_label": "obj0",
            "component": "single_sersic",
            "parameters": [{"parameter": "re", "value": 5.0, "at_boundary": False}],
        }], source={"band": None}),
        feat("center", "center_constraint_health", [{
            "model_label": "obj0",
            "component": "single_sersic",
            "is_reference": True,
            "constraint_present": True,
            "offset_from_reference": 0.0,
        }], source={"band": None}),
    ]
    decision = decide_proposal(
        round_id="r1",
        numeric_evidence=numeric_fixture(features),
        vlm_evidence=vlm_fixture([]),
        current_components=[],
    )
    checks = {
        item["check_id"]: item["status"] for item in decision["termination_checks"]
    }
    assert checks["REQUIRED_FIXED_PARAMETERS"] == "NOT_APPLICABLE"
    assert decision["action"]["action_type"] == "CONVERGED"


# ---------------------------------------------------------------------------
# Composite candidate ranking
# ---------------------------------------------------------------------------


def test_spec_compliance_refit_outranks_structural_add_regardless_of_order():
    features = [
        peaks("candidate_1"),
        feat("match", "original_source_matches", {"candidate_1": True}),
        feat("fixed", "required_fixed_parameter_health", [{
            "component": "disk", "model_label": "obj0", "parameter": "n",
            "satisfied": False, "expected_value": 1.0,
        }], source={"band": None}),
    ]
    observations = [obs("independent_source", target_id="candidate_1")]
    decision = decide(features, observations, components={"disk", "bulge"})
    assert decision["action"]["action_type"] == "REFIT_PARAMETERS"
    assert decision["action"]["reason_code"] == "DISK_N_NOT_FIXED"
    ranked = [
        item for item in decision["candidate_actions"] if item.get("ranking")
    ]
    assert ranked, "candidate ranking blocks must be persisted"
    selected = next(item for item in ranked if item["status"] == "SELECTED")
    assert selected["ranking"]["safety_rank"] == 0


# ---------------------------------------------------------------------------
# Refit matrix (Q1/Q2/Q3 ruling)
# ---------------------------------------------------------------------------


def test_fit_quality_no_rejects_even_with_improved_residual():
    decision = evaluate_refit(
        round_id="r2",
        component="bar",
        refit_evaluation=gates(**{"fit_quality_ok": "no"}),
    )
    assert decision["action"]["action_type"] == "REJECT_REFIT"


def test_fit_quality_inconclusive_is_inconclusive_not_reject():
    decision = evaluate_refit(
        round_id="r2",
        component="bar",
        refit_evaluation=gates(**{"fit_quality_ok": "inconclusive"}),
    )
    assert decision["action"]["action_type"] == "INCONCLUSIVE"


def test_baseline_existing_boundary_does_not_reject():
    decision = evaluate_refit(
        round_id="r2",
        component="bar",
        refit_evaluation=gates(
            boundary_hits=["obj0.re"],
            boundary_hits_baseline=["obj0.re"],
            boundary_hits_new_or_unresolved=[],
            bic=bic_gain(32.0),
            reduced_chisq=reduced(),
        ),
    )
    assert decision["action"]["action_type"] == "ACCEPT_REFIT"


def test_new_boundary_rejects():
    decision = evaluate_refit(
        round_id="r2",
        component="bar",
        refit_evaluation=gates(
            boundary_hits=["obj1.re"],
            boundary_hits_baseline=["obj0.re"],
            boundary_hits_new_or_unresolved=["obj1.re"],
        ),
    )
    assert decision["action"]["action_type"] == "REJECT_REFIT"


def test_propose_add_requires_improved_residual():
    decision = evaluate_refit(
        round_id="r2",
        component="bar",
        refit_evaluation=gates(residual="equivalent", reduced_chisq=reduced()),
    )
    assert decision["action"]["action_type"] == "REJECT_REFIT"


def test_propose_add_reduced_chisq_worsening_rejects():
    decision = evaluate_refit(
        round_id="r2",
        component="companion",
        refit_evaluation=gates(
            bic=bic_gain(32.0), reduced_chisq=reduced(delta=0.05),
        ),
    )
    assert decision["action"]["action_type"] == "REJECT_REFIT"


def test_refit_parameters_equivalent_residual_accepted():
    decision = evaluate_refit(
        round_id="r2",
        component="disk",
        candidate_action_type="REFIT_PARAMETERS",
        refit_evaluation=gates(
            residual="equivalent",
            reduced_chisq=reduced(delta=0.0),
            bic=bic_gain(-5.0),
        ),
    )
    assert decision["action"]["action_type"] == "ACCEPT_REFIT"


def test_replace_both_statistics_worsened_rejects():
    decision = evaluate_refit(
        round_id="r2",
        component="disk",
        candidate_action_type="PROPOSE_REPLACE",
        refit_evaluation=gates(
            residual="equivalent",
            reduced_chisq=reduced(delta=0.05),
            bic=bic_gain(-3.0),
        ),
    )
    assert decision["action"]["action_type"] == "REJECT_REFIT"


def test_replace_conflicting_evidence_is_inconclusive():
    decision = evaluate_refit(
        round_id="r2",
        component="disk",
        candidate_action_type="PROPOSE_REPLACE",
        refit_evaluation=gates(
            residual="worse",
            reduced_chisq=reduced(delta=-0.05),
        ),
    )
    assert decision["action"]["action_type"] == "INCONCLUSIVE"


def test_mandatory_disk_n_uses_new_boundary_semantics():
    decision = evaluate_refit(
        round_id="r2",
        component="disk",
        candidate_action_type="PROMOTE_SINGLE_SERSIC_TO_DISK",
        candidate_reason_code="DISK_CONFIRMED_SINGLE_SERSIC_PROMOTION",
        refit_evaluation=gates(
            residual="worse",
            boundary_hits=["obj0.re"],
            boundary_hits_baseline=["obj0.re"],
            boundary_hits_new_or_unresolved=[],
        ),
    )
    assert decision["action"]["action_type"] == "ACCEPT_REFIT"


# ---------------------------------------------------------------------------
# Comparator: fail-closed band matching and fit_quality_ok
# ---------------------------------------------------------------------------


def test_band_result_matching_refuses_positional_fallback():
    from component_analysis.refit_comparator import _band_result_files

    with pytest.raises(ValueError, match="refusing positional fallback"):
        _band_result_files(
            [{"band": "f200w", "result_fits": "/tmp/a.fits"}],
            ["/tmp/unrelated_name.fits"],
        )


def test_band_result_matching_allows_empty_result_list():
    from component_analysis.refit_comparator import _band_result_files

    aligned = _band_result_files(
        [{"band": "f200w", "result_fits": "/tmp/a.fits"}], []
    )
    assert aligned == [None]


def test_fit_quality_ok_requires_generations_for_multiband(tmp_path):
    from component_analysis.refit_comparator import _fit_quality_ok

    manifest = {"workflow_mode": "multi-band", "summary_file": str(tmp_path / "s.txt")}
    (tmp_path / "s.txt").write_text("# reduced chisq: 1.0\n", encoding="utf-8")
    assert _fit_quality_ok(manifest) == "inconclusive"
    (tmp_path / "s.txt").write_text(
        "# reduced chisq: 1.0\nnum_generations: 5000  popsize: 10\n", encoding="utf-8"
    )
    assert _fit_quality_ok(manifest) == "yes"
    (tmp_path / "s.txt").write_text(
        "# reduced chisq: -1.0\nnum_generations: 5000  popsize: 10\n", encoding="utf-8"
    )
    assert _fit_quality_ok(manifest) == "no"


def _single_band_fit_fixture(tmp_path, name, residual_value, bic, round_id):
    from astropy.io import fits
    import numpy as np

    science = tmp_path / f"{name}.science.fits"
    result = tmp_path / f"{name}.result.fits"
    config = tmp_path / f"{name}.feedme"
    summary = tmp_path / f"{name}.summary.md"
    comparison = tmp_path / f"{name}.comparison.png"
    original = np.ones((20, 20), dtype=float)
    residual = np.full_like(original, residual_value)
    model = original - residual
    fits.PrimaryHDU(original).writeto(science)
    fits.HDUList([
        fits.PrimaryHDU(original),
        fits.ImageHDU(model, name="MODEL"),
        fits.ImageHDU(residual, name="RESIDUAL"),
    ]).writeto(result)
    config.write_text(
        "A) input.fits\nB) output.fits\nC) none\nD) none\nF) none\nG) none\nH) 1 20 1 20\n"
        "K) 0.1 0.1\n\n# Object number: 1\n0) sersic\n1) 10 10 1 1\n3) 20 1\n4) 2 1\n5) 2 1\n"
        "9) 0.5\n10) 0.8\nZ) 0\n",
        encoding="utf-8",
    )
    summary.write_text(
        f"# reduced chisq: {residual_value}\n# BIC: {bic}\n"
        f"# image number: 0  band: single  chisq: [{residual_value}]  dof: [398.]  reduced chisq: [{residual_value}]\n",
        encoding="utf-8",
    )
    comparison.write_bytes(b"comparison")
    manifest = {
        "schema_version": "1.0",
        "workflow_mode": "single-band",
        "round_id": round_id,
        "object_id": "object-1",
        "config_file": str(config),
        "result_files": [str(result)],
        "summary_file": str(summary),
        "comparison_png": str(comparison),
        "working_note_file": None,
        "constraint_files": [],
        "bands": [{
            "band": "single",
            "science_fits": str(science),
            "science_hdu": 0,
            "sigma_fits": None,
            "sigma_hdu": None,
            "mask_fits": None,
            "mask_hdu": None,
            "psf_fits": None,
            "psf_hdu": None,
            "result_fits": str(result),
            "result_hdus": {"original_hdu": 0, "model_hdu": 1, "residual_hdu": 2},
            "pixscale_arcsec": 0.1,
            "fit_region": [0, 10, 0, 10],
            "validation": {},
        }],
    }
    fit_result = {
        "status": "success",
        "input_param_file": str(config),
        "optimized_fits_file": str(result),
        "summary_file": str(summary),
        "image_file": str(comparison),
        "output_param_file": str(config),
    }
    return manifest, fit_result


def test_comparator_reports_boundary_comparison_semantics(tmp_path):
    from component_analysis.refit_comparator import compare_refit_artifacts

    baseline_manifest, baseline_fit = _single_band_fit_fixture(
        tmp_path, "baseline", 0.1, 100.0, "round-1"
    )
    candidate_manifest, candidate_fit = _single_band_fit_fixture(
        tmp_path, "candidate", 0.05, 90.0, "round-2"
    )
    candidate_manifest["bands"][0]["science_fits"] = baseline_manifest["bands"][0]["science_fits"]
    result = compare_refit_artifacts(
        baseline_manifest=baseline_manifest,
        candidate_manifest=candidate_manifest,
        baseline_fit=baseline_fit,
        candidate_fit=candidate_fit,
        candidate_action_type="REFIT_PARAMETERS",
    )
    evaluation = result["evaluation"]
    assert evaluation["optimizer_converged"] == "NOT_APPLICABLE"
    assert evaluation["one_d_residual_outcome"] == "NOT_APPLICABLE"
    assert evaluation["fit_quality_ok"] == "yes"
    assert evaluation["boundary_hits_baseline"] == []
    assert evaluation["boundary_hits_new_or_unresolved"] == []


def test_center_constraint_exempts_off_axis_companion():
    from component_analysis.rules import decide_proposal

    features = [
        feat("center", "center_constraint_health", [
            {"model_label": "obj0", "component": "disk", "x": 0.0, "y": 0.0,
             "x_vary": True, "y_vary": True, "is_reference": True,
             "constraint_present": True, "offset_from_reference": 0.0},
            {"model_label": "companion", "component": "companion", "x": 0.23, "y": 0.03,
             "x_vary": True, "y_vary": True, "is_reference": False,
             "constraint_present": False, "offset_from_reference": 0.29},
        ], source={"band": None}),
    ]
    decision = decide_proposal(
        round_id="r1",
        numeric_evidence=numeric_fixture(features),
        vlm_evidence=vlm_fixture([]),
        current_components=["disk", "companion"],
    )
    center_candidates = [
        c for c in decision["candidate_actions"]
        if c.get("rule_id") == "CENTER_CONSTRAINT_V1"
    ]
    assert center_candidates == []
    audit = [t for t in decision["rule_trace"] if t["rule_id"] == "CENTER_CONSTRAINT_V1" and t["outcome"] == "NOT_APPLICABLE"]
    assert audit and "companion" in audit[0]["inputs"]


def test_mandatory_center_constraint_ignores_fit_quality():
    """2026-10-29 ruling 4: host components must be concentric regardless of
    fit quality — the LINK_CENTER refit accepts on convergence+physicality
    even when residuals and BIC worsen (audit-only), but still rejects on
    unresolved new boundaries or non-convergence."""
    # Worsened residuals/BIC still accepted.
    decision = evaluate_refit(
        round_id="r2",
        component="bar",
        candidate_action_type="REFIT_PARAMETERS",
        candidate_reason_code="CENTER_CONSTRAINT_MISSING",
        refit_evaluation=gates(
            residual="worse",
            reduced_chisq=reduced(delta=0.2),
            bic=bic_gain(gain=-180.0),
        ),
    )
    assert decision["action"]["action_type"] == "ACCEPT_REFIT"
    trace = decision["rule_trace"][-1]
    assert trace["rule_id"] == "MANDATORY_CENTER_CONSTRAINT_SPEC_V1"
    assert "concentric" in trace["detail"]
    # Unresolved new boundary still rejects.
    decision = evaluate_refit(
        round_id="r2",
        component="bar",
        candidate_action_type="REFIT_PARAMETERS",
        candidate_reason_code="CENTER_CONSTRAINT_MISSING",
        refit_evaluation=gates(
            residual="worse",
            boundary_hits=["bar.q"],
            boundary_hits_new_or_unresolved=["bar.q"],
        ),
    )
    assert decision["action"]["action_type"] == "REJECT_REFIT"
    # Non-convergence still rejects.
    decision = evaluate_refit(
        round_id="r2",
        component="bar",
        candidate_action_type="REFIT_PARAMETERS",
        candidate_reason_code="CENTER_CONSTRAINT_MISSING",
        refit_evaluation=gates(**{"fit_quality_ok": "no"}),
    )
    assert decision["action"]["action_type"] == "REJECT_REFIT"


def test_center_gate_exempts_companion():
    from component_analysis.rules import decide_proposal

    features = [
        feat("fit", "fit_convergence_summary", {"fit_succeeded": True}, source={"band": None}),
        feat("residual", "absolute_residual_quality", {
            "one_d_clean": True, "central_elongation": False,
        }, source={"band": None}),
        feat("health", "component_parameter_health", [{
            "model_label": "obj0", "component": "disk",
            "parameters": [{"parameter": "re", "value": 5.0, "at_boundary": False}],
        }], source={"band": None}),
        feat("center", "center_constraint_health", [
            {"model_label": "obj0", "component": "disk", "is_reference": True,
             "constraint_present": True, "offset_from_reference": 0.0},
            {"model_label": "companion", "component": "companion", "is_reference": False,
             "constraint_present": False, "offset_from_reference": 0.29},
        ], source={"band": None}),
    ]
    decision = decide_proposal(
        round_id="r1",
        numeric_evidence=numeric_fixture(features),
        vlm_evidence=vlm_fixture([]),
        current_components=["disk", "companion"],
    )
    checks = {c["check_id"]: c["status"] for c in decision["termination_checks"]}
    assert checks["CENTER_CONSTRAINT"] == "PASS"


def test_disk_bulge_split_priority_and_dust_conflict():
    """2026-10-29 ruling 6: with a confirmed Disk and no bulge, the split
    outranks bar/companion proposals; a VLM dust_lane conflict only audits."""
    features = [
        peaks("candidate_1"),
        feat("match", "original_source_matches", {"candidate_1": True}),
    ]
    # No dust conflict: split proposal present with top priority.
    decision = decide_proposal(
        round_id="r1",
        numeric_evidence=numeric_fixture(features),
        vlm_evidence=vlm_fixture([obs("independent_source", target_id="candidate_1")]),
        current_components=["disk"],
    )
    split = [
        c for c in decision["candidate_actions"]
        if c["rule_id"] == "DISK_BULGE_SPLIT_V1"
    ]
    assert split and split[0]["action"]["component"] == "bulge"
    assert split[0]["priority"] == 3
    # The queue ranking must place the split before the companion add.
    from component_analysis.policy import PolicyState, rebuild_candidate_queue

    state = PolicyState()
    rebuild_candidate_queue(decision, state)
    assert state.pending_candidate_queue[0]["action"]["component"] == "bulge"
    # Dust conflict: audited as non-blocking, no split proposal.
    decision = decide_proposal(
        round_id="r1",
        numeric_evidence=numeric_fixture(features),
        vlm_evidence=vlm_fixture(
            [obs("dust_lane"), obs("independent_source", target_id="candidate_1")]
        ),
        current_components=["disk"],
    )
    split = [
        c for c in decision["candidate_actions"]
        if c["rule_id"] == "DISK_BULGE_SPLIT_V1"
    ]
    assert split == []
    trace = next(t for t in decision["rule_trace"] if t["rule_id"] == "DISK_BULGE_SPLIT_V1")
    assert trace["outcome"] == "INCONCLUSIVE" and trace["blocking"] is False


def test_unavailable_extent_is_not_a_negative_observation():
    """H7 residue (1429/1825 finding): a feature emitted with
    status=UNAVAILABLE is a failed measurement, not negative evidence —
    without the ruling-8 alternative (neutral VLM label) the trace stays
    INCONCLUSIVE instead of NOT_SATISFIED."""
    features = [
        feat("extent", "source_extent_psf_ratio", None, status="UNAVAILABLE"),
        feat("n", "single_sersic_n", {"n": 0.32, "at_boundary": False}, source={"band": None}),
    ]
    decision = decide_proposal(
        round_id="r1",
        numeric_evidence=numeric_fixture(features),
        vlm_evidence=vlm_fixture([]),
        current_components=[],
    )
    trace = next(
        t for t in decision["rule_trace"] if t["rule_id"] == "DISK_N1_N2_N3_V1"
    )
    assert trace["outcome"] == "INCONCLUSIVE"
    assert trace["blocking"] is False


def test_m1_confusion_no_longer_drives_inflow_trial_fit():
    """2026-11-02 ruling 9 supersedes ruling 7: m=1 no longer competes
    in-flow, so a confounded amplitude produces NO in-flow action (the
    closing attempt happens later, without asking the confusion flag)."""
    from component_analysis.policy import PolicyState, decide_proposal_with_policy

    features = [
        feat("m1", "original_m1_amplitude", 0.31),
        feat("conf", "m1_confusion_present", True),
    ]
    decision = decide_proposal_with_policy(
        round_id="r1",
        numeric_evidence=numeric_fixture(features),
        vlm_evidence=vlm_fixture([]),
        current_components=["disk", "bulge"],
        state=PolicyState(),
    )
    # The closing action fires only in the no-candidate branch; here the
    # confounded in-flow rule no longer exists, so the decision falls through
    # (closing fires because no structural candidate exists — confusion is
    # irrelevant, which is exactly the ruling-9 semantics).
    assert decision["action"]["action_type"] == "PROPOSE_ADD"
    assert decision["action"]["component"] == "fourier_m1"
    trace = next(t for t in decision["rule_trace"] if t["rule_id"] == "FOURIER_M1_CLOSING_V1")
    assert trace["outcome"] == "SATISFIED"


def test_fourier_m1_is_optional_for_bic_gate():
    """Ruling 7: m=1 must earn its place via BIC (gain >= 10), like other
    optional components."""
    decision = evaluate_refit(
        round_id="r2",
        component="fourier_m1",
        candidate_action_type="PROPOSE_ADD",
        refit_evaluation=gates(
            bic=bic_gain(5.0),
            reduced_chisq=reduced(),
        ),
    )
    assert decision["action"]["action_type"] == "REJECT_REFIT"
    decision = evaluate_refit(
        round_id="r2",
        component="fourier_m1",
        candidate_action_type="PROPOSE_ADD",
        refit_evaluation=gates(
            bic=bic_gain(32.0),
            reduced_chisq=reduced(),
        ),
    )
    assert decision["action"]["action_type"] == "ACCEPT_REFIT"


def test_disk_confirmed_via_n1_unavailable_alternative():
    """2026-10-30 ruling 8: N1 unmeasured + N2 + explicit VLM disk label
    confirms the disk; a neutral label does not."""
    features = [
        feat("extent", "source_extent_psf_ratio", None, status="UNAVAILABLE"),
        feat("n", "single_sersic_n", {"n": 0.32, "at_boundary": False}, source={"band": None}),
    ]
    decision = decide_proposal(
        round_id="r1",
        numeric_evidence=numeric_fixture(features),
        vlm_evidence=vlm_fixture([obs("disk_like")]),
        current_components=[],
        current_profile=[{"component_id": "obj0", "model_type": "sersic"}],
    )
    assert decision["action"]["action_type"] == "PROMOTE_SINGLE_SERSIC_TO_DISK"
    trace = next(t for t in decision["rule_trace"] if t["rule_id"] == "DISK_N1_N2_N3_V1")
    assert trace["outcome"] == "SATISFIED"
    assert "N1_UNAVAILABLE_ALTERNATIVE" in (trace.get("detail") or "")
    # Neutral label: confirmation stays open (INCONCLUSIVE, not negative).
    neutral = decide_proposal(
        round_id="r1",
        numeric_evidence=numeric_fixture(features),
        vlm_evidence=vlm_fixture([]),
        current_components=[],
        current_profile=[{"component_id": "obj0", "model_type": "sersic"}],
    )
    trace = next(t for t in neutral["rule_trace"] if t["rule_id"] == "DISK_N1_N2_N3_V1")
    assert trace["outcome"] == "INCONCLUSIVE"
    assert trace["blocking"] is False


def test_vlm_residual_verdict_gate():
    """2026-11-02 ruling 12: 'dirtier' vetoes an otherwise perfect numeric
    candidate; 'cleaner' passes; unavailable is fail-open; mandatory-spec
    branches (center constraint) stay exempt."""
    good = gates(bic=bic_gain(32.0), reduced_chisq=reduced())
    # dirtier vetoes despite every numeric gate passing.
    decision = evaluate_refit(
        round_id="r2", component="bar",
        candidate_action_type="PROPOSE_ADD",
        refit_evaluation=dict(good, vlm_residual_verdict="dirtier"),
    )
    assert decision["action"]["action_type"] == "REJECT_REFIT"
    veto = next(t for t in decision["rule_trace"] if t["rule_id"] == "VLM_RESIDUAL_GATE_V1")
    assert veto["outcome"] == "NOT_SATISFIED"
    # cleaner passes with an audit trace.
    decision = evaluate_refit(
        round_id="r2", component="bar",
        candidate_action_type="PROPOSE_ADD",
        refit_evaluation=dict(good, vlm_residual_verdict="cleaner"),
    )
    assert decision["action"]["action_type"] == "ACCEPT_REFIT"
    audit = next(t for t in decision["rule_trace"] if t["rule_id"] == "VLM_RESIDUAL_GATE_V1")
    assert audit["outcome"] == "SATISFIED"
    # unavailable: fail-open, numeric gates decide.
    decision = evaluate_refit(
        round_id="r2", component="bar",
        candidate_action_type="PROPOSE_ADD",
        refit_evaluation=dict(good, vlm_residual_verdict="unavailable"),
    )
    assert decision["action"]["action_type"] == "ACCEPT_REFIT"
    # mandatory center-constraint branch stays exempt from the veto.
    decision = evaluate_refit(
        round_id="r2", component="bar",
        candidate_action_type="REFIT_PARAMETERS",
        candidate_reason_code="CENTER_CONSTRAINT_MISSING",
        refit_evaluation=gates(
            residual="worse", vlm_residual_verdict="dirtier",
            reduced_chisq=reduced(delta=0.2), bic=bic_gain(gain=-180.0),
        ),
    )
    assert decision["action"]["action_type"] == "ACCEPT_REFIT"


def test_early_split_entry_before_disk_confirmation():
    """2026-11-03 ruling 13: a 'central bright core + extended outer'
    object (VLM central_compact_excess, no disk label, disk unconfirmed)
    gets the split as its FIRST action; without the central-core label it
    stays stalled; the trace marks the early entry."""
    # Early entry fires: disk unconfirmed, VLM sees a central compact core.
    decision = decide_proposal(
        round_id="r1",
        numeric_evidence=numeric_fixture([
            feat("extent", "source_extent_psf_ratio", None, status="UNAVAILABLE"),
            feat("n", "single_sersic_n", {"n": 3.2, "at_boundary": False}, source={"band": None}),
        ]),
        vlm_evidence=vlm_fixture([obs("central_compact_excess")]),
        current_components=[],
    )
    split = [
        c for c in decision["candidate_actions"]
        if c["rule_id"] == "DISK_BULGE_SPLIT_V1"
    ]
    assert split and split[0]["action"]["component"] == "bulge"
    trace = next(t for t in decision["rule_trace"] if t["rule_id"] == "DISK_BULGE_SPLIT_V1")
    assert trace["outcome"] == "SATISFIED"
    assert "Early split entry" in trace["detail"]

    # No central-core label and no disk: still stalled (unchanged behavior).
    quiet = decide_proposal(
        round_id="r1",
        numeric_evidence=numeric_fixture([
            feat("extent", "source_extent_psf_ratio", None, status="UNAVAILABLE"),
            feat("n", "single_sersic_n", {"n": 3.2, "at_boundary": False}, source={"band": None}),
        ]),
        vlm_evidence=vlm_fixture([]),
        current_components=[],
    )
    assert not [
        c for c in quiet["candidate_actions"]
        if c["rule_id"] == "DISK_BULGE_SPLIT_V1"
    ]

    # Main entry (disk confirmed) keeps its original trace wording.
    main = decide_proposal(
        round_id="r1",
        numeric_evidence=numeric_fixture([]),
        vlm_evidence=vlm_fixture([]),
        current_components=["disk"],
    )
    trace = next(t for t in main["rule_trace"] if t["rule_id"] == "DISK_BULGE_SPLIT_V1")
    assert "Confirmed Disk requires" in trace["detail"]
