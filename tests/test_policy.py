"""Unit tests for the automation policy layer (automation-policy@v1).

Covers the three resolution modes from docs/component-analysis/redesign.md
section 4 -- trial-fit arbitration, conservative fallback, numeric-only
degradation when the VLM is unavailable -- plus the convergence guards:
trial budget, rejected-candidate suppression and repeated-INCONCLUSIVE
termination.
"""

import pytest

from component_analysis import (
    POLICY_VERSION,
    PolicyState,
    decide_proposal_with_policy,
    evaluate_refit_with_policy,
)


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


EXTENT = feat("extent", "source_extent_psf_ratio", 4.0)
GEOMETRY = feat("geom", "outer_isophote_geometry", {"pa_scatter_deg": 5.0, "q_range": 0.05})
RESIDUAL_OUTER = feat("resid", "outer_residual_systematic", True)
CENTRAL_EXCESS = feat("excess", "central_excess_multiband", True)
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
PEAKS = feat(
    "peaks",
    "residual_local_peaks",
    1,
    candidate_regions=[
        {"region_id": "candidate_1", "band": "f200w", "x_pix": 40.0, "y_pix": 41.0, "local_snr": 8.0}
    ],
)
ORIGINAL_MATCH = feat("match", "original_source_matches", {"candidate_1": True})


def resolution_feat(feature_id, band, fwhm_obs, fwhm_psf, snr):
    return feat(
        feature_id,
        "central_resolution_measurement",
        {"fwhm_obs_pix": fwhm_obs, "fwhm_psf_pix": fwhm_psf, "snr": snr},
        band=band,
    )


def decide(features, observations=(), components=(), state=None, fingerprint="", parse_status="OK"):
    return decide_proposal_with_policy(
        round_id="r1",
        numeric_evidence=numeric_fixture(features),
        vlm_evidence=vlm_fixture(observations, parse_status=parse_status),
        current_components=components,
        state=state if state is not None else PolicyState(),
        evidence_fingerprint=fingerprint,
    )


def gates(converged="yes", residual="yes", physical="yes", **extra):
    evaluation = {
        "fit_converged": converged,
        "residual_improved": residual,
        "parameters_physical": physical,
    }
    evaluation.update(extra)
    return evaluation


# ---------------------------------------------------------------------------
# Trial-fit arbitration
# ---------------------------------------------------------------------------


def test_disk_ambiguous_resolved_by_trial_fit():
    state = PolicyState()
    decision = decide([EXTENT, GEOMETRY], state=state)
    assert decision["action"] == {"action_type": "PROPOSE_ADD", "component": "disk"}
    automation = decision["automation"]
    assert automation["policy_version"] == POLICY_VERSION
    assert automation["resolution"] == "trial_fit"
    assert automation["original_action_type"] == "INCONCLUSIVE"
    assert automation["resolved_rule_id"] == "DISK_AMBIGUOUS_EVIDENCE_V1"
    assert automation["needs_review"] is True
    assert state.trials_used == 1


def test_edge_on_low_q_trial_replaces_disk():
    low_q = feat("q", "outer_axis_ratio", 0.12)
    state = PolicyState()
    decision = decide([EXTENT, low_q], components={"disk"}, state=state)
    assert decision["action"] == {
        "action_type": "PROPOSE_REPLACE",
        "replace_from": "disk",
        "replace_to": "edge_on_disk",
    }
    assert decision["automation"]["resolution"] == "trial_fit"


def test_central_resolution_conflict_trials_bulge_path():
    features = [
        CENTRAL_EXCESS,
        resolution_feat("res1", "f200w", 4.0, 2.5, 30.0),
        resolution_feat("res2", "f150w", 2.7, 2.6, 30.0),
    ]
    state = PolicyState()
    decision = decide(features, components={"disk"}, state=state)
    assert decision["action"]["action_type"] == "PROPOSE_ADD"
    assert decision["action"]["component"] == "bulge"
    assert decision["action"]["resolved_state"] == "inconclusive"
    assert decision["automation"]["resolved_rule_id"] == "CENTRAL_RESOLUTION_CONFLICT_V1"


def test_companion_uncertain_trial_with_target_label():
    state = PolicyState()
    decision = decide(
        [PEAKS, ORIGINAL_MATCH],
        [obs("uncertain", target_id="candidate_1")],
        components={"disk"},
        state=state,
    )
    assert decision["action"]["component"] == "companion"
    assert decision["action"]["target_model_label"] == "candidate_1"
    assert decision["automation"]["resolution"] == "trial_fit"


# ---------------------------------------------------------------------------
# Conservative fallback and convergence guards
# ---------------------------------------------------------------------------


def test_bar_diffraction_conflict_conservative_keep():
    state = PolicyState()
    decision = decide(
        [STRONG_BAR], [obs("diffraction_psf", confidence=0.9)], components={"disk"}, state=state
    )
    assert decision["action"] == {"action_type": "KEEP_AND_CONTINUE"}
    assert decision["automation"]["resolution"] == "conservative_keep"
    assert state.trials_used == 0


def test_trial_budget_exhausted_falls_back_conservative():
    state = PolicyState(trial_budget=0)
    decision = decide([EXTENT, GEOMETRY], state=state)
    assert decision["action"]["action_type"] == "KEEP_AND_CONTINUE"
    assert decision["automation"]["resolution"] == "conservative_keep"
    assert state.trials_used == 0


def test_rejected_component_not_retried():
    state = PolicyState()
    state.rejected_components.add("disk")
    decision = decide([EXTENT, GEOMETRY], state=state)
    assert decision["action"]["action_type"] == "KEEP_AND_CONTINUE"
    assert state.trials_used == 0


def test_repeated_inconclusive_terminates_question():
    state = PolicyState()
    first = decide([EXTENT, GEOMETRY], state=state, fingerprint="fp1")
    assert first["automation"]["resolution"] == "trial_fit"
    second = decide([EXTENT, GEOMETRY], state=state, fingerprint="fp1")
    assert second["automation"]["resolution"] == "conservative_keep"
    assert "DISK_AMBIGUOUS_EVIDENCE_V1" in state.terminated_rules
    third = decide([EXTENT, GEOMETRY], state=state, fingerprint="fp2")
    assert third["automation"]["resolution"] == "conservative_keep"


# ---------------------------------------------------------------------------
# Numeric-only degradation when VLM is unavailable
# ---------------------------------------------------------------------------


def test_vlm_unavailable_numeric_only_retry_proposes_disk():
    state = PolicyState()
    decision = decide(
        [EXTENT, GEOMETRY, RESIDUAL_OUTER], state=state, parse_status="PARSE_FAILED"
    )
    assert decision["action"] == {"action_type": "PROPOSE_ADD", "component": "disk"}
    assert decision["automation"]["resolution"] == "numeric_only_retry"
    assert decision["automation"]["needs_review"] is True


def test_vlm_unavailable_weak_evidence_still_resolved():
    state = PolicyState()
    decision = decide([EXTENT, GEOMETRY], state=state, parse_status="TIMEOUT")
    assert decision["action"]["action_type"] == "PROPOSE_ADD"
    assert decision["automation"]["resolution"] == "trial_fit"


# ---------------------------------------------------------------------------
# EVALUATE_REFIT fallback and pass-through
# ---------------------------------------------------------------------------


def test_refit_inconclusive_falls_back_to_reject():
    state = PolicyState()
    decision = evaluate_refit_with_policy(
        round_id="r2",
        component="bar",
        refit_evaluation=gates(converged="inconclusive"),
        state=state,
    )
    assert decision["action"] == {"action_type": "REJECT_REFIT", "component": "bar"}
    assert decision["automation"]["resolution"] == "reject_fallback"
    assert "bar" in state.rejected_components


def test_refit_incomparable_bic_falls_back_to_reject():
    state = PolicyState()
    decision = evaluate_refit_with_policy(
        round_id="r2",
        component="lens",
        refit_evaluation=gates(
            bic={"bic_simple": 1.0, "bic_complex": 0.0, "bic_gain": 1.0, "comparable": False}
        ),
        state=state,
    )
    assert decision["action"]["action_type"] == "REJECT_REFIT"
    assert "lens" in state.rejected_components


def test_refit_reject_recorded_without_automation():
    state = PolicyState()
    decision = evaluate_refit_with_policy(
        round_id="r2",
        component="companion",
        refit_evaluation=gates(residual="no"),
        state=state,
    )
    assert decision["action"]["action_type"] == "REJECT_REFIT"
    assert "automation" not in decision
    assert "companion" in state.rejected_components


def test_accept_passes_through_unchanged():
    state = PolicyState()
    decision = evaluate_refit_with_policy(
        round_id="r2", component="bulge", refit_evaluation=gates(), state=state
    )
    assert decision["action"]["action_type"] == "ACCEPT_REFIT"
    assert "automation" not in decision


def test_clear_proposal_passes_through():
    state = PolicyState()
    decision = decide([EXTENT, GEOMETRY, RESIDUAL_OUTER], [obs("disk_like")], state=state)
    assert decision["action"] == {"action_type": "PROPOSE_ADD", "component": "disk"}
    assert "automation" not in decision
    assert state.trials_used == 0
