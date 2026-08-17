from eval.compare_grpo_reward_versions import build_unified_report


def _component(q=0.8):
    return {
        "model": "sersic",
        "x": 128,
        "y": 128,
        "mag": 16,
        "re": 3,
        "n": 2,
        "q": q,
        "pa": 10,
    }


def test_versions_use_same_candidates_and_recompute_groupgate():
    parents = [{
        "group_id": "g1",
        "parent_metrics": {"chi2_nu": 1.0, "bic": 200.0},
    }]
    predictions = [
        {
            "group_id": "g1",
            "candidate_index": 0,
            "pred_spec": {
                "components": [{"role": "bulge", "model": "sersic"}],
            },
        },
        {
            "group_id": "g1",
            "candidate_index": 1,
            "pred_spec": {
                "components": [{"role": "bulge", "model": "sersic"}],
            },
        },
    ]
    rollouts = [
        {
            "group_id": "g1",
            "candidate_index": 0,
            "outcome": "success",
            "model_metrics": {"chi2_nu": 0.8, "bic": 100.0},
            "fitted_components": [_component(q=0.17)],
            "vlm_improvement": 1,
        },
        {
            "group_id": "g1",
            "candidate_index": 1,
            "outcome": "success",
            "model_metrics": {"chi2_nu": 1.0, "bic": 200.0},
            "fitted_components": [_component(q=0.8)],
            "vlm_improvement": 0,
        },
    ]
    residual = [
        {"group_id": "g1", "candidate_index": 0, "residual_score": 0.9},
        {"group_id": "g1", "candidate_index": 1, "residual_score": 0.1},
    ]

    report, details = build_unified_report(
        parents,
        predictions,
        rollouts,
        residual_rows=residual,
        residual_gate_threshold=0.0,
    )

    assert report["input_counts"] == {
        "parents": 1,
        "predictions": 2,
        "rollouts": 2,
        "residual_scores": 2,
    }
    assert report["versions"]["v11"]["candidate_binary_alignment"]["accuracy"] == 1.0
    assert report["versions"]["v11"]["num_trainable_groups"] == 1

    v12 = report["versions"]["v12.4"]
    assert v12["candidate_binary_alignment"]["accuracy"] == 0.5
    assert v12["num_trainable_groups"] == 0
    assert details["v12.4"][0]["recomputed_structure_vetoed"]

    # The calibrated V12.5 degeneracy threshold is zero, so its final reward
    # must be exactly V12.4. Residual-only ranking remains a separate diagnostic.
    assert report["versions"]["v12.5"] == report["versions"]["v12.4"]
    residual_pair = report["v12.5_residual_only_diagnostic"]["pairwise"]
    assert residual_pair["strict_accuracy"] == 1.0
    assert residual_pair["top1_positive_rate"] == 1.0


def test_policy_failure_participates_in_groupgate_but_not_binary_alignment():
    parents = [{
        "group_id": "g1",
        "parent_metrics": {"chi2_nu": 1.0, "bic": 200.0},
    }]
    predictions = [{
        "group_id": "g1",
        "candidate_index": 0,
        "pred_spec": {"components": [{"role": "bulge", "model": "sersic"}]},
    }]
    rollouts = [
        {
            "group_id": "g1",
            "candidate_index": 0,
            "outcome": "success",
            "model_metrics": {"chi2_nu": 0.8, "bic": 100.0},
            "fitted_components": [_component()],
            "vlm_improvement": 1,
        },
        {
            "group_id": "g1",
            "candidate_index": 1,
            "outcome": "policy_execution_failure",
            "vlm_improvement": None,
        },
    ]

    report, _ = build_unified_report(parents, predictions, rollouts)
    v11 = report["versions"]["v11"]
    assert v11["candidate_binary_alignment"]["n"] == 1
    assert v11["num_trainable_groups"] == 1
    assert v11["coarse_counts"]["-1.0"] == 1
