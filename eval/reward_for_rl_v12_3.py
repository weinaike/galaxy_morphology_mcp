"""V12.3 candidate: hard-veto axis/size failures, diagnose degeneracy."""

from __future__ import annotations

from typing import Any

from eval.reward_for_rl import compute_rl_reward as compute_v11_reward
from eval.reward_for_rl_v12_2 import check_targeted_fitted_structure


HARD_RULES = {"extreme_role_axis_ratio", "extreme_size_hierarchy"}


def _rule(finding: str) -> str:
    return finding.split(":", 1)[0]


def compute_rl_reward_v12_3(
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
    """Compute V11 with axis/size vetoes and diagnostic-only degeneracy."""

    result = compute_v11_reward(
        old_metrics=old_metrics,
        new_metrics=new_metrics,
        action_spec=action_spec,
        residual=residual,
        sigma=sigma,
        mask=mask,
        noise_thresholds=noise_thresholds,
        fitted_components=fitted_components,
    )
    _, findings, warnings, detail = check_targeted_fitted_structure(
        action_spec,
        fitted_components,
        thresholds=structure_thresholds,
    )
    hard = [finding for finding in findings if _rule(finding) in HARD_RULES]
    diagnostic = [finding for finding in findings if _rule(finding) not in HARD_RULES]
    v11_reward = float(result.get("reward", 0.0))
    result.update(
        reward_version="v12.3",
        structure_ok=not hard,
        structure_vetoed=bool(hard),
        structure_violations=hard,
        structure_warnings=warnings + diagnostic,
        all_structure_findings=findings,
        structure_detail=detail,
        v11_reward=v11_reward,
    )
    if hard:
        result["reward"] = min(v11_reward, 0.0)
    return result


compute_rl_reward = compute_rl_reward_v12_3
