import pytest

from eval.reward_for_grpo import (
    OUTCOME_EVALUATOR_FAILURE,
    OUTCOME_POLICY_EXECUTION_FAILURE,
    OUTCOME_POLICY_INVALID,
    V11_THRESHOLD,
    calibrate_margin_scale,
    gate_grpo_group,
    shape_grpo_reward,
)


def test_v11_threshold_is_strict():
    at_threshold = shape_grpo_reward({"reward": V11_THRESHOLD})
    above_threshold = shape_grpo_reward({"reward": V11_THRESHOLD + 1e-6})

    assert at_threshold["coarse_reward"] == 0.0
    assert above_threshold["coarse_reward"] == 1.0


@pytest.mark.parametrize(
    "outcome", [OUTCOME_POLICY_INVALID, OUTCOME_POLICY_EXECUTION_FAILURE]
)
def test_policy_failures_are_negative(outcome):
    result = shape_grpo_reward({"reward": 100.0}, outcome=outcome)

    assert result["coarse_reward"] == -1.0
    assert result["margin"] == 0.0
    assert result["shaped_reward"] == -1.0
    assert result["sample_train_mask"] is True


def test_evaluator_failure_is_masked():
    result = shape_grpo_reward(
        None,
        outcome=OUTCOME_EVALUATOR_FAILURE,
        failure_reason="worker_crash",
    )

    assert result["sample_train_mask"] is False
    assert result["coarse_reward"] is None
    assert result["failure_reason"] == "worker_crash"


def test_missing_raw_reward_becomes_evaluator_failure():
    result = shape_grpo_reward({})

    assert result["outcome"] == OUTCOME_EVALUATOR_FAILURE
    assert result["sample_train_mask"] is False


def test_bounded_margin_cannot_reverse_binary_order():
    positive = shape_grpo_reward(
        {"reward": V11_THRESHOLD + 100.0},
        margin_weight=0.2,
    )
    negative = shape_grpo_reward(
        {"reward": V11_THRESHOLD - 100.0},
        margin_weight=0.2,
    )

    assert positive["shaped_reward"] == pytest.approx(1.2)
    assert negative["shaped_reward"] == pytest.approx(-0.2)
    assert positive["shaped_reward"] > negative["shaped_reward"]


def test_homogeneous_group_is_masked():
    results = [
        shape_grpo_reward({"reward": V11_THRESHOLD + 0.1}),
        shape_grpo_reward({"reward": V11_THRESHOLD + 0.2}),
    ]
    group = gate_grpo_group(results)

    assert group["group_train_mask"] is False
    assert group["gate_reason"] == "homogeneous_coarse_level"
    assert not any(r["group_train_mask"] for r in group["rollout_results"])


def test_mixed_group_is_trainable_and_ignores_evaluator_failure():
    results = [
        shape_grpo_reward({"reward": V11_THRESHOLD + 0.1}),
        shape_grpo_reward({"reward": V11_THRESHOLD - 0.1}),
        shape_grpo_reward(None, outcome=OUTCOME_EVALUATOR_FAILURE),
    ]
    group = gate_grpo_group(results)

    assert group["group_train_mask"] is True
    assert group["coarse_levels"] == [0.0, 1.0]
    assert [r["group_train_mask"] for r in group["rollout_results"]] == [
        True,
        True,
        False,
    ]


def test_margin_scale_calibration():
    assert calibrate_margin_scale([0.0, 1.0, 2.0, 3.0], method="iqr") == 1.5
    assert calibrate_margin_scale([1.0, 1.0, 1.0], method="mad") == 1e-6

