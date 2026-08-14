"""V12 reward: V11 formula plus conservative fitted-structure vetoes."""

from __future__ import annotations

import math
from itertools import combinations
from typing import Any, Iterable

from eval.reward_for_rl import compute_rl_reward as compute_v11_reward


V12_STRUCTURE_DEFAULTS = {
    "main_center_offset_px": 3.0,
    "bulge_to_disk_re_max": 1.5,
    "negligible_mag_delta": 5.0,
    "duplicate_center_offset_px": 1.0,
    "duplicate_size_ratio_max": 1.25,
    "duplicate_q_delta_max": 0.10,
    "duplicate_pa_delta_deg": 10.0,
}
_MAIN_ROLES = {"bulge", "disk", "bar", "lens", "nucleus", "core"}


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _role(component: dict[str, Any]) -> str:
    return str(component.get("role") or "").strip().lower()


def _model(component: dict[str, Any]) -> str:
    return str(component.get("model") or "").strip().lower()


def _pa_distance(left: float, right: float) -> float:
    delta = abs(left - right) % 180.0
    return min(delta, 180.0 - delta)


def _center_distance(left: dict[str, Any], right: dict[str, Any]) -> float | None:
    values = tuple(
        _number(value)
        for value in (left.get("x"), left.get("y"), right.get("x"), right.get("y"))
    )
    if any(value is None for value in values):
        return None
    lx, ly, rx, ry = values
    return math.hypot(lx - rx, ly - ry)


def _annotate_fitted_components(
    action_spec: dict[str, Any], fitted_components: Iterable[dict[str, Any]] | None
) -> list[dict[str, Any]]:
    """Attach proposal roles to fitted components using component order."""

    specs = [
        dict(component)
        for component in (action_spec or {}).get("components", [])
        if str(component.get("model") or "").lower() != "sky"
    ]
    annotated = []
    for index, fitted in enumerate(fitted_components or []):
        component = dict(fitted)
        if index < len(specs):
            component.setdefault("role", specs[index].get("role"))
            component.setdefault("proposal_model", specs[index].get("model"))
        component["component_index"] = index
        annotated.append(component)
    return annotated


def check_fitted_structure(
    action_spec: dict[str, Any],
    fitted_components: Iterable[dict[str, Any]] | None,
    *,
    thresholds: dict[str, float] | None = None,
) -> tuple[bool, list[str], dict[str, Any]]:
    """Implement qualitative fitted-structure checks from the VLM prompt."""

    cfg = dict(V12_STRUCTURE_DEFAULTS)
    if thresholds:
        cfg.update(thresholds)
    components = _annotate_fitted_components(action_spec, fitted_components)
    violations: list[str] = []

    main = [component for component in components if _role(component) in _MAIN_ROLES]
    for left, right in combinations(main, 2):
        distance = _center_distance(left, right)
        if distance is not None and distance > cfg["main_center_offset_px"]:
            violations.append(
                "main_center_offset: "
                f"{_role(left)}[{left['component_index']}] vs "
                f"{_role(right)}[{right['component_index']}]="
                f"{distance:.3f}px>{cfg['main_center_offset_px']:.3f}px"
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
            if ratio > cfg["bulge_to_disk_re_max"]:
                violations.append(
                    "implausible_size_hierarchy: "
                    f"bulge[{bulge['component_index']}].Re/"
                    f"disk[{disk['component_index']}].Re_eff="
                    f"{ratio:.3f}>{cfg['bulge_to_disk_re_max']:.3f}"
                )

    magnitudes = [(component, _number(component.get("mag"))) for component in components]
    valid_magnitudes = [value for _, value in magnitudes if value is not None]
    if valid_magnitudes:
        brightest = min(valid_magnitudes)
        for component, magnitude in magnitudes:
            if magnitude is not None and magnitude - brightest > cfg["negligible_mag_delta"]:
                violations.append(
                    "negligible_flux_component: "
                    f"component[{component['component_index']}].delta_mag="
                    f"{magnitude - brightest:.3f}>{cfg['negligible_mag_delta']:.3f}"
                )

    for left, right in combinations(components, 2):
        if not _role(left) or _role(left) != _role(right):
            continue
        if not _model(left) or _model(left) != _model(right):
            continue
        distance = _center_distance(left, right)
        left_re, right_re = _number(left.get("re")), _number(right.get("re"))
        left_q, right_q = _number(left.get("q")), _number(right.get("q"))
        left_pa, right_pa = _number(left.get("pa")), _number(right.get("pa"))
        values = (distance, left_re, right_re, left_q, right_q, left_pa, right_pa)
        if any(value is None for value in values) or min(left_re, right_re) <= 0:
            continue
        size_ratio = max(left_re, right_re) / min(left_re, right_re)
        if (
            distance <= cfg["duplicate_center_offset_px"]
            and size_ratio <= cfg["duplicate_size_ratio_max"]
            and abs(left_q - right_q) <= cfg["duplicate_q_delta_max"]
            and _pa_distance(left_pa, right_pa) <= cfg["duplicate_pa_delta_deg"]
        ):
            violations.append(
                "near_duplicate_components: "
                f"{_role(left)}[{left['component_index']}] and "
                f"{_role(right)}[{right['component_index']}]"
            )

    detail = {
        "thresholds": cfg,
        "annotated_fitted_components": components,
        "n_structure_violations": len(violations),
    }
    return not violations, violations, detail


def compute_rl_reward_v12(
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
    """Compute V11 unchanged, then veto structurally implausible fitted models."""

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
    structure_ok, violations, detail = check_fitted_structure(
        action_spec, fitted_components, thresholds=structure_thresholds
    )
    v11_reward = float(result.get("reward", 0.0))
    result.update(
        reward_version="v12",
        structure_ok=structure_ok,
        structure_violations=violations,
        structure_detail=detail,
        structure_vetoed=not structure_ok,
        v11_reward=v11_reward,
    )
    if not structure_ok:
        result["reward"] = min(v11_reward, 0.0)
    return result


compute_rl_reward = compute_rl_reward_v12
