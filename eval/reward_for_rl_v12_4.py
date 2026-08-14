"""V12.4 candidate with tighter axis-ratio and size thresholds."""

from __future__ import annotations

from typing import Any

from eval.reward_for_rl_v12_3 import compute_rl_reward_v12_3


V12_4_STRUCTURE_DEFAULTS = {
    "bulge_q_min": 0.18,
    "bar_q_min": 0.08,
    "extreme_bulge_to_disk_re_max": 8.0,
}


def compute_rl_reward_v12_4(
    old_metrics: dict[str, Any],
    new_metrics: dict[str, Any],
    action_spec: dict[str, Any],
    residual=None,
    sigma=None,
    mask=None,
    noise_thresholds=None,
    fitted_components=None,
    *,
    structure_thresholds: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Compute V12.3 with thresholds tightened around audited failures."""

    thresholds = dict(V12_4_STRUCTURE_DEFAULTS)
    if structure_thresholds:
        thresholds.update(structure_thresholds)
    result = compute_rl_reward_v12_3(
        old_metrics=old_metrics,
        new_metrics=new_metrics,
        action_spec=action_spec,
        residual=residual,
        sigma=sigma,
        mask=mask,
        noise_thresholds=noise_thresholds,
        fitted_components=fitted_components,
        structure_thresholds=thresholds,
    )
    result["reward_version"] = "v12.4"
    return result


compute_rl_reward = compute_rl_reward_v12_4
