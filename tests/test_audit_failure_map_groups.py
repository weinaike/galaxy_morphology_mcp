from eval.audit_failure_map_groups import (
    _gate,
    _is_candidate_artifact_failure,
)


def test_positive_anchor_rejects_minus_one_zero_group():
    group = [
        {"coarse_reward": -1.0},
        {"coarse_reward": 0.0},
    ]
    result = _gate(group, simulate_failure_map=False)
    assert result.legacy_trainable is True
    assert result.positive_anchored_trainable is False


def test_positive_anchor_keeps_group_with_improvement():
    group = [
        {"coarse_reward": -1.0},
        {"coarse_reward": 1.0},
    ]
    result = _gate(group, simulate_failure_map=False)
    assert result.legacy_trainable is True
    assert result.positive_anchored_trainable is True


def test_failure_map_only_reclassifies_artifact_failure():
    artifact = {
        "outcome": "evaluator_failure",
        "failure_stage": "galfit_artifact_validation",
        "failure_reason": "incomplete GALFIT evaluator artifacts: metric_missing:bic",
    }
    infrastructure = {
        "outcome": "evaluator_failure",
        "failure_stage": "galfit_evaluator",
        "failure_reason": "OSError: Disk quota exceeded",
    }
    assert _is_candidate_artifact_failure(artifact) is True
    assert _is_candidate_artifact_failure(infrastructure) is False

