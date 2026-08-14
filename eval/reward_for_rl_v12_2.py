"""V12.2 reward: targeted fitted-structure checks from GRPO Rule-only cases.

This candidate keeps V11 unchanged and adds only role-aware, high-severity
checks.  It must pass locked Val/Test alignment before it is connected to the
online rollout reward.
"""

from __future__ import annotations

from typing import Any, Iterable

from eval.reward_for_rl import compute_rl_reward as compute_v11_reward
from eval.reward_for_rl_v12 import (
    _annotate_fitted_components,
    _model,
    _number,
    _role,
    check_fitted_structure,
)
from eval.reward_for_rl_v12_1 import HARD_STRUCTURE_RULES, _violation_rule


V12_2_STRUCTURE_DEFAULTS = {
    # Role-aware limits.  A low-q disk may be a real edge-on disk, so the
    # stricter limits apply only to bulge/bar roles implicated by the audited
    # GRPO Rule-only cases.
    "bulge_q_min": 0.20,
    "bar_q_min": 0.10,
    # V12 used 1.5 and rejected many VLM-positive decompositions.  V12.2 only
    # catches runaway scale inversions such as Re_bulge/Re_disk_eff ~= 10.
    "extreme_bulge_to_disk_re_max": 5.0,
    # A bulge, bar and disk all becoming disk-like is treated as component
    # identity collapse.  Two-component pseudo-bulge+disk models are not
    # rejected by this rule.
    "disk_like_sersic_n_max": 0.60,
}


def check_targeted_fitted_structure(
    action_spec: dict[str, Any],
    fitted_components: Iterable[dict[str, Any]] | None,
    *,
    thresholds: dict[str, float] | None = None,
) -> tuple[bool, list[str], list[str], dict[str, Any]]:
    """Return V12.2 hard violations, diagnostic warnings and details."""

    cfg = dict(V12_2_STRUCTURE_DEFAULTS)
    if thresholds:
        cfg.update(thresholds)

    components = _annotate_fitted_components(action_spec, fitted_components)
    violations: list[str] = []

    for component in components:
        role = _role(component)
        q = _number(component.get("q"))
        index = component["component_index"]
        if q is None:
            continue
        if role == "bulge" and q < cfg["bulge_q_min"]:
            violations.append(
                "extreme_role_axis_ratio: "
                f"bulge[{index}].q={q:.3f}<{cfg['bulge_q_min']:.3f}"
            )
        elif role == "bar" and q < cfg["bar_q_min"]:
            violations.append(
                "extreme_role_axis_ratio: "
                f"bar[{index}].q={q:.3f}<{cfg['bar_q_min']:.3f}"
            )

    bulges = [component for component in components if _role(component) == "bulge"]
    disks = [component for component in components if _role(component) == "disk"]
    for bulge in bulges:
        bulge_re = _number(bulge.get("re"))
        if bulge_re is None:
            continue
        for disk in disks:
            disk_size = _number(disk.get("re"))
            if disk_size is None or disk_size <= 0:
                continue
            disk_effective_re = 1.678 * disk_size if _model(disk) == "expdisk" else disk_size
            ratio = bulge_re / disk_effective_re
            if ratio > cfg["extreme_bulge_to_disk_re_max"]:
                violations.append(
                    "extreme_size_hierarchy: "
                    f"bulge[{bulge['component_index']}].Re/"
                    f"disk[{disk['component_index']}].Re_eff="
                    f"{ratio:.3f}>{cfg['extreme_bulge_to_disk_re_max']:.3f}"
                )

    role_map = {_role(component): component for component in components}
    if {"bulge", "bar", "disk"}.issubset(role_map):
        bulge_n = _number(role_map["bulge"].get("n"))
        bar_n = _number(role_map["bar"].get("n"))
        disk_is_disk_like = (
            _model(role_map["disk"]) == "expdisk"
            or (
                _number(role_map["disk"].get("n")) is not None
                and _number(role_map["disk"].get("n")) <= cfg["disk_like_sersic_n_max"]
            )
        )
        if (
            bulge_n is not None
            and bar_n is not None
            and bulge_n <= cfg["disk_like_sersic_n_max"]
            and bar_n <= cfg["disk_like_sersic_n_max"]
            and disk_is_disk_like
        ):
            violations.append(
                "disk_like_component_degeneracy: "
                f"bulge.n={bulge_n:.3f}, bar.n={bar_n:.3f}, disk_like=true"
            )

    # Preserve the two high-confidence V12.1 checks and retain the broader V12
    # findings as diagnostics only.
    _, all_v12_findings, v12_detail = check_fitted_structure(
        action_spec,
        fitted_components,
        thresholds=thresholds,
    )
    inherited_hard = [
        finding
        for finding in all_v12_findings
        if _violation_rule(finding) in HARD_STRUCTURE_RULES
    ]
    warnings = [
        finding
        for finding in all_v12_findings
        if _violation_rule(finding) not in HARD_STRUCTURE_RULES
    ]
    violations = inherited_hard + violations

    detail = {
        "thresholds": cfg,
        "annotated_fitted_components": components,
        "v12_diagnostic_detail": v12_detail,
        "n_structure_violations": len(violations),
        "n_structure_warnings": len(warnings),
    }
    return not violations, violations, warnings, detail


def compute_rl_reward_v12_2(
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
    """Compute V11, then apply the targeted V12.2 structure vetoes."""

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
    structure_ok, violations, warnings, detail = check_targeted_fitted_structure(
        action_spec,
        fitted_components,
        thresholds=structure_thresholds,
    )
    v11_reward = float(result.get("reward", 0.0))
    result.update(
        reward_version="v12.2",
        structure_ok=structure_ok,
        structure_vetoed=not structure_ok,
        structure_violations=violations,
        structure_warnings=warnings,
        structure_detail=detail,
        v11_reward=v11_reward,
    )
    if not structure_ok:
        result["reward"] = min(v11_reward, 0.0)
    return result


compute_rl_reward = compute_rl_reward_v12_2
