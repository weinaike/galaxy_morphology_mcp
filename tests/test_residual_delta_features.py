import numpy as np

from eval.residual_delta_features import (
    FEATURE_NAMES,
    compute_residual_badness_features,
    compute_residual_feature_deltas,
)


def test_parent_to_child_central_cleanup_is_positive():
    sigma = np.ones((64, 64), dtype=float)
    mask = np.zeros((64, 64), dtype=int)
    parent = np.zeros((64, 64), dtype=float)
    child = np.zeros((64, 64), dtype=float)
    parent[27:37, 27:37] = 6.0
    child[27:37, 27:37] = 1.0

    old = compute_residual_badness_features(parent, sigma, mask)
    new = compute_residual_badness_features(child, sigma, mask)
    delta = compute_residual_feature_deltas(old, new)

    assert tuple(delta) == FEATURE_NAMES
    assert delta["central_abs"] > 0
    assert delta["central_tail3_fraction"] > 0
    assert delta["tail3_fraction"] > 0


def test_structured_child_is_worse_than_white_parent():
    rng = np.random.default_rng(42)
    sigma = np.ones((64, 64), dtype=float)
    mask = np.zeros((64, 64), dtype=int)
    parent = rng.normal(0.0, 1.0, size=(64, 64))
    child = parent.copy()
    child[:, :32] += 3.0
    child[:, 32:] -= 3.0

    old = compute_residual_badness_features(parent, sigma, mask)
    new = compute_residual_badness_features(child, sigma, mask)
    delta = compute_residual_feature_deltas(old, new)

    assert delta["median_abs"] < 0
    assert delta["neighbor_correlation"] < 0
    assert delta["low_frequency_excess"] < 0


def test_all_features_are_finite():
    rng = np.random.default_rng(7)
    residual = rng.normal(size=(32, 32))
    features = compute_residual_badness_features(
        residual,
        np.ones_like(residual),
        np.zeros_like(residual, dtype=int),
    )
    assert tuple(features) == FEATURE_NAMES
    assert all(np.isfinite(value) for value in features.values())


def test_v12_5_gate_is_selected_only_when_final_alignment_improves():
    from eval.calibrate_residual_v12_5 import _choose_v12_5_gate

    rows = [
        {"vlm_improvement": 1, "v12_4_reward": 1.0, "disk_like_degeneracy": True},
        {"vlm_improvement": 0, "v12_4_reward": 1.0, "disk_like_degeneracy": True},
        {"vlm_improvement": 1, "v12_4_reward": 1.0, "disk_like_degeneracy": False},
        {"vlm_improvement": 0, "v12_4_reward": 1.0, "disk_like_degeneracy": False},
    ]
    threshold, baseline, candidate = _choose_v12_5_gate(
        rows, np.asarray([0.9, 0.1, 0.1, 0.9])
    )

    assert threshold == 0.9
    assert candidate["fp"] == baseline["fp"] - 1
    assert candidate["fn"] == baseline["fn"]
    assert candidate["f1"] > baseline["f1"]