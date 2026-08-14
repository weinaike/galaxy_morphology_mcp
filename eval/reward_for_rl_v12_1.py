"""V12.1 reward: keep uncertain structure checks diagnostic-only."""

from __future__ import annotations

from typing import Any

from eval.reward_for_rl import compute_rl_reward as compute_v11_reward
from eval.reward_for_rl_v12 import check_fitted_structure


HARD_STRUCTURE_RULES = {
    "main_center_offset",
    "near_duplicate_components",
}


def _violation_rule(violation: str) -> str:
    return violation.split(":", 1)[0]


def compute_rl_reward_v12_1(
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
    """Apply hard vetoes only for high-confidence structural failures.

    Faint components and bulge/disk size ordering remain visible in diagnostics,
    but do not override statistically supported improvements.
    """

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
    _, all_violations, detail = check_fitted_structure(
        action_spec,
        fitted_components,
        thresholds=structure_thresholds,
    )
    hard_violations = [
        violation
        for violation in all_violations
        if _violation_rule(violation) in HARD_STRUCTURE_RULES
    ]
    warnings = [
        violation
        for violation in all_violations
        if _violation_rule(violation) not in HARD_STRUCTURE_RULES
    ]
    v11_reward = float(result.get("reward", 0.0))
    result.update(
        reward_version="v12.1",
        structure_ok=not hard_violations,
        structure_vetoed=bool(hard_violations),
        structure_violations=hard_violations,
        structure_warnings=warnings,
        all_structure_findings=all_violations,
        structure_detail=detail,
        v11_reward=v11_reward,
    )
    if hard_violations:
        result["reward"] = min(v11_reward, 0.0)
    return result


compute_rl_reward = compute_rl_reward_v12_1