import pytest

from eval.audit_v11_noise_likeness_fusion import (
    _pair_complementarity,
    _prepare,
    _ranking_metrics,
)


def test_merge_and_complementarity_detect_residual_repair():
    v11 = [
        {"group_id": "g", "candidate_id": "p", "training_score": 0.0},
        {"group_id": "g", "candidate_id": "n", "training_score": 1.0},
    ]
    noise = [
        {
            "group_id": "g",
            "candidate_id": "p",
            "residual_score": 0.9,
            "overall_label": 1,
        },
        {
            "group_id": "g",
            "candidate_id": "n",
            "residual_score": 0.1,
            "overall_label": 0,
        },
    ]
    rows, missing, fields = _prepare(v11, noise)
    assert not missing
    assert fields == {"training_score": 2}
    assert _ranking_metrics(rows, "v11_score")["pairwise"] == 0.0
    assert _ranking_metrics(rows, "noise_score")["pairwise"] == 1.0
    counts, examples = _pair_complementarity(rows)
    assert counts["v11_wrong__noise_correct"] == 1
    assert examples[0]["group_id"] == "g"


def test_group_standardization_preserves_v11_order():
    v11 = [
        {"group_id": "g", "candidate_id": "a", "raw_reward": 3.0},
        {"group_id": "g", "candidate_id": "b", "raw_reward": -2.0},
    ]
    noise = [
        {"group_id": "g", "candidate_id": "a", "residual_score": 0.2, "overall_label": 1},
        {"group_id": "g", "candidate_id": "b", "residual_score": 0.2, "overall_label": 0},
    ]
    rows, _, _ = _prepare(v11, noise)
    assert _ranking_metrics(rows, "v11_z")["pairwise"] == pytest.approx(1.0)
