"""Pure deterministic rules for component proposals and refit arbitration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from schemas import validate


@dataclass(frozen=True)
class RuleThresholds:
    """Versioned v1 defaults; values remain subject to dev-set calibration."""

    version: str = "thresholds@v1"
    source_extent_psf_ratio: float = 3.0
    disk_pa_scatter_deg: float = 20.0
    disk_q_range: float = 0.15
    disk_n_max: float = 2.5
    spheroid_n_min: float = 3.0
    vlm_conflict_confidence: float = 0.8
    edge_on_axis_ratio: float = 0.17
    bar_ellipticity_peak: float = 0.2
    bar_pa_scatter_deg: float = 20.0
    bar_ellipticity_drop: float = 0.02
    bar_outer_pa_change_deg: float = 15.0
    bar_scale_psf_ratio: float = 2.0
    residual_m2_amplitude: float = 0.1
    resolved_fwhm_ratio: float = 0.5
    strong_snr: float = 20.0
    weak_snr: float = 10.0
    psf_min_fwhm_pix: float = 2.0
    m1_detection_amplitude: float = 0.1
    m1_keep_amplitude: float = 0.02
    companion_snr: float = 5.0
    lens_bar_re_ratio: float = 0.9
    lens_bar_q_max: float = 0.5
    optional_bic_gain: float = 10.0


def _features(numeric_evidence: dict[str, Any], name: str) -> list[dict[str, Any]]:
    return [
        feature
        for feature in numeric_evidence.get("features", [])
        if feature.get("name") == name and feature.get("status") == "AVAILABLE"
    ]


def _first_value(numeric_evidence: dict[str, Any], name: str) -> Any:
    matches = _features(numeric_evidence, name)
    return matches[0].get("value") if matches else None


def _band_passed(numeric_evidence: dict[str, Any], band: str | None) -> bool:
    if band is None:
        return True
    return any(
        item.get("band") == band and item.get("passed") is True
        for item in numeric_evidence.get("band_quality", [])
    )


def _observations(vlm_evidence: dict[str, Any], target_id: str = "central") -> list[dict[str, Any]]:
    return [
        item
        for item in vlm_evidence.get("observations", [])
        if item.get("target_id") == target_id
    ]


def _labels(vlm_evidence: dict[str, Any], target_id: str = "central") -> set[str]:
    return {item["label"] for item in _observations(vlm_evidence, target_id)}


def _high_confidence_label(
    vlm_evidence: dict[str, Any],
    label: str,
    confidence: float,
    target_id: str = "central",
) -> bool:
    return any(
        item.get("label") == label and item.get("confidence", 0.0) >= confidence
        for item in _observations(vlm_evidence, target_id)
    )


def _trace(
    rule_id: str,
    outcome: str,
    *,
    inputs: Iterable[str] = (),
    unmet: Iterable[str] = (),
    detail: str | None = None,
    blocking: bool | None = None,
    collector_id: str | None = None,
    evidence_targets: Iterable[str] = (),
    expected_new_fingerprint: str | None = None,
) -> dict[str, Any]:
    is_blocking = outcome == "INCONCLUSIVE" if blocking is None else blocking
    targets = list(evidence_targets)
    if outcome == "INCONCLUSIVE" and not targets:
        targets = list(inputs) or ["fit_convergence_summary", "residual_profile"]
    has_declared_vlm_gap = (
        "VLM" in (detail or "").upper()
        or any("VLM" in value.upper() for value in unmet)
        or rule_id == "VLM_UNAVAILABLE_V1"
    )
    return {
        "rule_id": rule_id,
        "outcome": outcome,
        "inputs": list(inputs),
        "unmet_conditions": list(unmet),
        "detail": detail,
        "blocking": is_blocking,
        "collector_id": (
            collector_id
            if collector_id is not None
            else "refresh_numeric_and_vlm_evidence"
            if outcome == "INCONCLUSIVE" and has_declared_vlm_gap
            else None
        ),
        "evidence_targets": targets,
        "expected_new_fingerprint": expected_new_fingerprint,
    }


def _complete_action(
    action: dict[str, Any] | None,
    *,
    traces: list[dict[str, Any]],
    termination_checks: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if action is None:
        return None
    completed = dict(action)
    action_type = completed.get("action_type")
    last_rule = traces[-1].get("rule_id", "decision") if traces else "decision"
    if action_type == "COLLECT_EVIDENCE":
        completed.setdefault(
            "continuation_reason",
            f"Rule evaluation {last_rule} requires another evidence-changing round.",
        )
        completed.setdefault("next_step", "Collect the missing fit or residual evidence and re-analyze.")
        completed.setdefault("next_transition", "COLLECT_EVIDENCE")
        completed.setdefault(
            "collector_id", "refresh_numeric_and_vlm_evidence"
        )
        completed.setdefault(
            "evidence_targets",
            ["fit_convergence_summary", "residual_profile", "parameter_health"],
        )
        completed.setdefault("expected_input_change", "new evidence artifact")
        completed.setdefault("expected_new_fingerprint", "unavailable")
    if action_type == "PROPOSE_REPLACE":
        completed.setdefault("target_model_label", completed.get("replace_from"))
    if action_type == "CONVERGED":
        completed.setdefault("termination_checks", termination_checks)
    return completed


def _decision(
    *,
    round_id: str,
    state: str,
    action: dict[str, Any] | None,
    traces: list[dict[str, Any]],
    evidence_refs: dict[str, Any] | None,
    thresholds: RuleThresholds,
    raw_action: dict[str, Any] | None = None,
    candidate_actions: list[dict[str, Any]] | None = None,
    workflow_status: str | None = None,
    termination_checks: list[dict[str, Any]] | None = None,
    refit_evaluation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    checks = termination_checks or []
    resolved_action = _complete_action(
        action,
        traces=traces,
        termination_checks=checks,
    )
    raw = _complete_action(
        raw_action if raw_action is not None else action,
        traces=traces,
        termination_checks=checks,
    )
    status = workflow_status or (
        "CONVERGED"
        if resolved_action and resolved_action.get("action_type") == "CONVERGED"
        else "STOPPED_NEEDS_REVIEW"
        if resolved_action and resolved_action.get("action_type") == "INCONCLUSIVE"
        else "CONTINUE"
    )
    normalized_candidates = []
    for candidate in candidate_actions or []:
        item = dict(candidate)
        item["action"] = _complete_action(
            item["action"],
            traces=traces,
            termination_checks=checks,
        )
        normalized_candidates.append(item)

    artifact: dict[str, Any] = {
        "schema_version": "1.1",
        "round_id": round_id,
        "rules_version": "component-rules@v1",
        "thresholds_version": thresholds.version,
        "state": state,
        "action": resolved_action,
        "raw_decision": {
            "state": state,
            "action": raw,
            "rule_trace": traces,
        },
        "candidate_actions": normalized_candidates,
        "workflow_status": status,
        "termination_checks": checks,
        "rule_trace": traces,
        "evidence_refs": {
            "numeric_evidence": None,
            "vlm_evidence": None,
            **(evidence_refs or {}),
        },
    }
    if status == "STOPPED_NEEDS_REVIEW":
        artifact["automation"] = {
            "policy_version": "rules@v1",
            "resolution": "rule_terminated",
            "original_action_type": "INCONCLUSIVE",
            "resolved_rule_id": traces[-1].get("rule_id") if traces else None,
            "reason": "No safe executable action was available from the current evidence.",
            "needs_review": True,
        }
    if refit_evaluation is not None:
        artifact["refit_evaluation"] = refit_evaluation
    validate(artifact, "decision_artifact")
    return artifact


def _disk_rule(
    numeric: dict[str, Any],
    vlm: dict[str, Any],
    thresholds: RuleThresholds,
    *,
    promotion_target: str | None = None,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    extent = _first_value(numeric, "source_extent_psf_ratio")
    geometry = _first_value(numeric, "outer_isophote_geometry") or {}
    sersic = _first_value(numeric, "single_sersic_n") or {}
    residual = _first_value(numeric, "outer_residual_systematic")
    n1 = isinstance(extent, (int, float)) and extent >= thresholds.source_extent_psf_ratio
    geometry_stable = (
        geometry.get("pa_scatter_deg", float("inf")) < thresholds.disk_pa_scatter_deg
        and geometry.get("q_range", float("inf")) < thresholds.disk_q_range
    )
    n_value = sersic.get("n")
    n_unbound = n_value is not None and not sersic.get("at_boundary", False)
    n2 = geometry_stable or (n_unbound and n_value <= thresholds.disk_n_max)
    n3 = residual is True
    labels = _labels(vlm)
    disk_label = bool(labels & {"disk_like", "spiral_arm", "edge_on_disk"})
    neutral_label = not labels or bool(labels & {"uncertain", "none"})
    numeric_disk_support = n1 and n2 and n3

    if (n1 and (n2 or n3) and disk_label) or (numeric_disk_support and neutral_label):
        action_type = (
            "PROMOTE_SINGLE_SERSIC_TO_DISK"
            if promotion_target
            else "PROPOSE_ADD"
        )
        action = {
            "action_type": action_type,
            "component": "disk",
        }
        if promotion_target:
            action.update(
                {
                    "target_model_label": promotion_target,
                    "semantic_transition": "single_sersic_to_disk",
                    "reason_code": "DISK_CONFIRMED_SINGLE_SERSIC_PROMOTION",
                }
            )
        return action, _trace(
            "DISK_N1_N2_N3_V1",
            "SATISFIED",
            inputs=(
                "source_extent_psf_ratio",
                "outer_isophote_geometry",
                "single_sersic_n",
                "outer_residual_systematic",
            "central",
            *(() if not promotion_target else ("single_sersic_profile",)),
        ),
    )
    if n1 and not n2 and not n3 and n_unbound and n_value >= thresholds.spheroid_n_min:
        return None, _trace(
            "SPHEROID_SINGLE_SERSIC_V1",
            "SATISFIED",
            inputs=("source_extent_psf_ratio", "single_sersic_n"),
            detail="Numeric evidence supports retaining a single Sersic spheroid; VLM morphology is not used.",
        )
    ambiguous_n = n_unbound and thresholds.disk_n_max < n_value < thresholds.spheroid_n_min
    if ambiguous_n or (n1 and neutral_label and n2 != n3):
        return {"action_type": "INCONCLUSIVE"}, _trace(
            "DISK_AMBIGUOUS_EVIDENCE_V1",
            "INCONCLUSIVE",
            inputs=("single_sersic_n", "outer_isophote_geometry", "outer_residual_systematic"),
        )
    return None, _trace(
        "DISK_N1_N2_N3_V1",
        "NOT_SATISFIED",
        unmet=("minimum Disk evidence combination not met",),
    )


def _edge_on_rule(
    numeric: dict[str, Any],
    vlm: dict[str, Any],
    thresholds: RuleThresholds,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    q = _first_value(numeric, "outer_axis_ratio")
    extent = _first_value(numeric, "source_extent_psf_ratio")
    low_q = isinstance(q, (int, float)) and q < thresholds.edge_on_axis_ratio
    extended = isinstance(extent, (int, float)) and extent >= thresholds.source_extent_psf_ratio
    if low_q and extended and "edge_on_disk" in _labels(vlm):
        return {
            "action_type": "PROPOSE_REPLACE",
            "replace_from": "disk",
            "replace_to": "edge_on_disk",
        }, _trace(
            "EDGE_ON_LOW_Q_V1",
            "SATISFIED",
            inputs=("outer_axis_ratio", "source_extent_psf_ratio", "central"),
        )
    if low_q and extended:
        return {"action_type": "INCONCLUSIVE"}, _trace(
            "EDGE_ON_LOW_Q_V1",
            "INCONCLUSIVE",
            inputs=("outer_axis_ratio", "central"),
            unmet=("VLM edge_on_disk confirmation missing",),
        )
    return None, _trace("EDGE_ON_LOW_Q_V1", "NOT_SATISFIED")


def _bar_rule(
    numeric: dict[str, Any],
    vlm: dict[str, Any],
    thresholds: RuleThresholds,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    strong_bands: list[str] = []
    for feature in _features(numeric, "bar_isophote_profile"):
        band = feature.get("source", {}).get("band")
        value = feature.get("value") or {}
        outer_change = (
            value.get("outer_ellipticity_drop", -float("inf"))
            >= thresholds.bar_ellipticity_drop
            or value.get("outer_pa_change_deg", -float("inf"))
            >= thresholds.bar_outer_pa_change_deg
        )
        if (
            _band_passed(numeric, band)
            and value.get("ellipticity_peak", -float("inf"))
            >= thresholds.bar_ellipticity_peak
            and value.get("pa_scatter_deg", float("inf")) < thresholds.bar_pa_scatter_deg
            and value.get("scale_psf_ratio", -float("inf"))
            >= thresholds.bar_scale_psf_ratio
            and outer_change
            and value.get("psf_veto") is False
        ):
            strong_bands.append(str(band))
    if strong_bands:
        if _high_confidence_label(
            vlm, "diffraction_psf", thresholds.vlm_conflict_confidence
        ):
            return {"action_type": "INCONCLUSIVE"}, _trace(
                "BAR_DIFFRACTION_CONFLICT_V1",
                "INCONCLUSIVE",
                inputs=strong_bands + ["central"],
            )
        return {"action_type": "PROPOSE_ADD", "component": "bar"}, _trace(
            "BAR_STRONG_ISOPHOTE_V1",
            "SATISFIED",
            inputs=strong_bands,
            detail="At least one quality-gated band has a strong isophote signature.",
        )

    m2 = _first_value(numeric, "residual_m2_amplitude")
    elongated = _first_value(numeric, "residual_central_elongation") is True
    if (
        isinstance(m2, (int, float))
        and m2 >= thresholds.residual_m2_amplitude
        and elongated
        and bool(_labels(vlm) & {"bar_like", "peanut_x"})
    ):
        return {"action_type": "PROPOSE_ADD", "component": "bar"}, _trace(
            "BAR_WEAK_COMBINED_V1",
            "SATISFIED",
            inputs=("residual_m2_amplitude", "residual_central_elongation", "central"),
        )
    return None, _trace("BAR_EVIDENCE_V1", "NOT_SATISFIED")


def _central_source_rule(
    numeric: dict[str, Any],
    vlm: dict[str, Any],
    thresholds: RuleThresholds,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    if _first_value(numeric, "central_excess_multiband") is not True:
        return None, _trace("CENTRAL_EXCESS_V1", "NOT_SATISFIED")
    if _labels(vlm) & {"dust_lane", "diffraction_psf"}:
        return {"action_type": "INCONCLUSIVE"}, _trace(
            "CENTRAL_MORPHOLOGY_CONFLICT_V1",
            "INCONCLUSIVE",
            inputs=("central_excess_multiband", "central"),
        )

    states: list[tuple[str | None, str, float]] = []
    for feature in _features(numeric, "central_resolution_measurement"):
        band = feature.get("source", {}).get("band")
        value = feature.get("value") or {}
        obs = value.get("fwhm_obs_pix")
        psf = value.get("fwhm_psf_pix")
        snr = value.get("snr")
        if not all(isinstance(item, (int, float)) for item in (obs, psf, snr)):
            continue
        if psf < thresholds.psf_min_fwhm_pix or snr < thresholds.weak_snr:
            continue
        intrinsic = max(obs**2 - psf**2, 0.0) ** 0.5
        if snr >= thresholds.strong_snr:
            state = (
                "resolved"
                if intrinsic >= thresholds.resolved_fwhm_ratio * psf
                else "unresolved"
            )
            states.append((band, state, psf))
        else:
            states.append((band, "weak", psf))

    strong_states = [item for item in states if item[1] != "weak"]
    for index, first in enumerate(strong_states):
        for second in strong_states[index + 1 :]:
            similar_resolution = abs(first[2] - second[2]) / min(first[2], second[2]) <= 0.2
            if similar_resolution and first[1] != second[1]:
                return {"action_type": "INCONCLUSIVE"}, _trace(
                    "CENTRAL_RESOLUTION_CONFLICT_V1",
                    "INCONCLUSIVE",
                    inputs=tuple(str(item[0]) for item in (first, second)),
                )
    if any(state == "resolved" for _, state, _ in strong_states):
        return {
            "action_type": "PROPOSE_ADD",
            "component": "bulge",
            "resolved_state": "resolved",
        }, _trace(
            "CENTRAL_RESOLVED_V1",
            "SATISFIED",
            inputs=tuple(str(band) for band, state, _ in strong_states if state == "resolved"),
        )
    if strong_states and all(state == "unresolved" for _, state, _ in strong_states):
        independent_agn = _first_value(numeric, "independent_agn_evidence") is True
        component = "agn" if independent_agn else "compact_central_source_candidate"
        action = {
            "action_type": "PROPOSE_ADD",
            "component": component,
            "physical_identity": "agn" if independent_agn else "unconfirmed",
            "resolved_state": "unresolved",
        }
        return action, _trace(
            "CENTRAL_UNRESOLVED_V1",
            "SATISFIED",
            inputs=tuple(str(band) for band, _, _ in strong_states),
        )
    return {"action_type": "INCONCLUSIVE"}, _trace(
        "CENTRAL_RESOLUTION_QUALITY_V1",
        "INCONCLUSIVE",
        unmet=("no high-SNR, adequately sampled band",),
    )


def _m1_rule(
    numeric: dict[str, Any],
    thresholds: RuleThresholds,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    amplitude = _first_value(numeric, "original_m1_amplitude")
    confused = _first_value(numeric, "m1_confusion_present") is True
    if isinstance(amplitude, (int, float)) and amplitude >= thresholds.m1_detection_amplitude:
        if confused:
            return {"action_type": "INCONCLUSIVE"}, _trace(
                "FOURIER_M1_CONFOUNDING_V1",
                "INCONCLUSIVE",
                inputs=("original_m1_amplitude", "m1_confusion_present"),
            )
        return {"action_type": "PROPOSE_ADD", "component": "fourier_m1"}, _trace(
            "FOURIER_M1_ORIGINAL_V1",
            "SATISFIED",
            inputs=("original_m1_amplitude",),
        )
    return None, _trace("FOURIER_M1_ORIGINAL_V1", "NOT_SATISFIED")


def _companion_rule(
    numeric: dict[str, Any],
    vlm: dict[str, Any],
    thresholds: RuleThresholds,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    original_matches = _first_value(numeric, "original_source_matches") or {}
    for feature in _features(numeric, "residual_local_peaks"):
        for region in feature.get("candidate_regions", []):
            target_id = region.get("region_id")
            if region.get("local_snr", -float("inf")) < thresholds.companion_snr:
                continue
            if original_matches.get(target_id) is not True:
                continue
            labels = _labels(vlm, target_id)
            if "independent_source" in labels:
                return {
                    "action_type": "PROPOSE_ADD",
                    "component": "companion",
                    "target_model_label": target_id,
                }, _trace(
                    "COMPANION_NUMERIC_VLM_V1",
                    "SATISFIED",
                    inputs=(feature["feature_id"], target_id),
                )
            if "uncertain" in labels or not labels:
                return {"action_type": "INCONCLUSIVE"}, _trace(
                    "COMPANION_NUMERIC_VLM_V1",
                    "INCONCLUSIVE",
                    inputs=(feature["feature_id"], target_id),
                    unmet=("VLM independent_source confirmation missing",),
                )
    return None, _trace("COMPANION_NUMERIC_VLM_V1", "NOT_SATISFIED")


def _lens_rule(
    numeric: dict[str, Any],
    vlm: dict[str, Any],
    thresholds: RuleThresholds,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    bar = _first_value(numeric, "bar_fit_parameters") or {}
    re_ratio = bar.get("re_bar_over_re_disk")
    q_bar = bar.get("q_bar")
    anomalous = (
        isinstance(re_ratio, (int, float)) and re_ratio >= thresholds.lens_bar_re_ratio
    ) or (isinstance(q_bar, (int, float)) and q_bar > thresholds.lens_bar_q_max)
    if not anomalous:
        return None, _trace("LENS_BAR_ANOMALY_V1", "NOT_SATISFIED")
    if _first_value(numeric, "extended_positive_residual") is not True:
        return {"action_type": "INCONCLUSIVE"}, _trace(
            "LENS_BAR_ANOMALY_V1",
            "INCONCLUSIVE",
            inputs=("bar_fit_parameters",),
            unmet=("extended positive residual evidence missing",),
            detail="Bar parameters are anomalous but may reflect degeneracy or label swap.",
        )
    if any(
        item.get("label") == "independent_source"
        and item.get("confidence", 0.0) >= thresholds.vlm_conflict_confidence
        for item in vlm.get("observations", [])
    ):
        return {"action_type": "INCONCLUSIVE"}, _trace(
            "LENS_COMPANION_CONFLICT_V1",
            "INCONCLUSIVE",
            inputs=("bar_fit_parameters", "extended_positive_residual"),
            detail="Extended residual may belong to an independent source.",
        )
    return {"action_type": "PROPOSE_ADD", "component": "lens"}, _trace(
        "LENS_BAR_SPLIT_V1",
        "SATISFIED",
        inputs=("bar_fit_parameters", "extended_positive_residual"),
    )


def _feature_record(numeric: dict[str, Any], name: str) -> dict[str, Any] | None:
    matches = [
        item for item in numeric.get("features", []) if item.get("name") == name
    ]
    return matches[0] if matches else None


def _candidate(
    rule_id: str,
    priority: int,
    action: dict[str, Any],
    *,
    status: str = "DEFERRED",
    detail: str | None = None,
) -> dict[str, Any]:
    return {
        "rule_id": rule_id,
        "priority": priority,
        "status": status,
        "action": action,
        "detail": detail,
    }


def _parameter_refit_candidates(
    numeric: dict[str, Any],
    components: set[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    candidates: list[dict[str, Any]] = []
    traces: list[dict[str, Any]] = []
    fixed = _first_value(numeric, "required_fixed_parameter_health")
    if isinstance(fixed, list):
        for item in fixed:
            role = item.get("component")
            if item.get("parameter") == "n" and role in components and item.get("satisfied") is False:
                target = item.get("model_label")
                expected = item.get("expected_value")
                if target and expected is not None:
                    action = {
                        "action_type": "REFIT_PARAMETERS",
                        "reason_code": f"{role.upper()}_N_NOT_FIXED",
                        "parameter_changes": [
                            {
                                "target_model_label": target,
                                "parameter": "n",
                                "operation": "FIX_VALUE",
                                "value": expected,
                                "reason": f"{role} n must satisfy the existing model specification",
                            }
                        ],
                    }
                    candidates.append(_candidate("REQUIRED_FIXED_PARAMETER_V1", 1, action))
                    traces.append(
                        _trace(
                            "REQUIRED_FIXED_PARAMETER_V1",
                            "SATISFIED",
                            inputs=(str(target), "n"),
                        )
                    )
    center = _first_value(numeric, "center_constraint_health")
    if isinstance(center, list):
        reference = next(
            (item for item in center if item.get("is_reference") is True),
            None,
        )
        reference_label = reference.get("model_label") if reference else None
        for item in center:
            if item.get("is_reference") is True:
                continue
            offset = item.get("offset_from_reference")
            invalid = item.get("constraint_present") is False or (
                isinstance(offset, (int, float)) and offset != 0
            )
            if invalid and item.get("model_label") and reference_label:
                action = {
                    "action_type": "REFIT_PARAMETERS",
                    "reason_code": "CENTER_CONSTRAINT_MISSING",
                    "parameter_changes": [
                        {
                            "target_model_label": item["model_label"],
                            "parameter": "x,y",
                            "operation": "LINK_CENTER",
                            "reference_model_label": reference_label,
                        }
                    ],
                }
                candidates.append(_candidate("CENTER_CONSTRAINT_V1", 1, action))
                traces.append(
                    _trace(
                        "CENTER_CONSTRAINT_V1",
                        "SATISFIED",
                        inputs=(str(item["model_label"]), str(reference_label)),
                    )
                )
    health = _first_value(numeric, "component_parameter_health")
    if isinstance(health, list):
        for component in health:
            role = component.get("component")
            if role not in components:
                continue
            for item in component.get("parameters", []):
                if item.get("at_boundary") is not True:
                    continue
                target = component.get("model_label")
                parameter = item.get("parameter")
                if not target or not parameter:
                    continue
                if item.get("vary") is False:
                    operation = "REMOVE_NONREQUIRED_CONSTRAINT"
                    change = {
                        "target_model_label": target,
                        "parameter": parameter,
                        "operation": operation,
                        "reason": "A non-required fixed parameter is at its configured boundary",
                    }
                else:
                    operation = "SET_BOUNDS"
                    change = {
                        "target_model_label": target,
                        "parameter": parameter,
                        "operation": operation,
                        "lower": item.get("lower"),
                        "upper": item.get("upper"),
                        "reason": "Parameter health reports an active boundary hit",
                    }
                action = {
                    "action_type": "REFIT_PARAMETERS",
                    "reason_code": "PARAMETER_BOUNDARY_HIT",
                    "parameter_changes": [change],
                }
                candidates.append(_candidate("PARAMETER_HEALTH_V1", 1, action))
                traces.append(
                    _trace(
                        "PARAMETER_HEALTH_V1",
                        "SATISFIED",
                        inputs=(str(target), str(parameter)),
                    )
                )
    if "fourier_m1" in components:
        targets = _first_value(numeric, "required_fixed_parameter_health")
        target_labels = [
            item.get("target_model_labels", [])
            for item in targets or []
            if item.get("parameter") == "fourier_m1_target"
        ]
        if target_labels and not target_labels[0]:
            action = {
                "action_type": "REFIT_PARAMETERS",
                "reason_code": "FOURIER_M1_TARGET_MISSING",
                "parameter_changes": [
                    {
                        "target_model_label": "disk",
                        "parameter": "profile",
                        "operation": "SET_INITIAL",
                        "value": "sersic_f",
                        "reason": "Fourier m=1 must be attached to the Disk profile",
                    }
                ],
            }
            candidates.append(_candidate("FOURIER_M1_TARGET_V1", 1, action))
            traces.append(_trace("FOURIER_M1_TARGET_V1", "SATISFIED", inputs=("disk",)))
    return candidates, traces


def _remove_candidates(
    numeric: dict[str, Any],
    components: set[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    candidates: list[dict[str, Any]] = []
    traces: list[dict[str, Any]] = []
    local = _first_value(numeric, "component_local_residual_facts")
    facts = local if isinstance(local, list) else []
    if not facts:
        return candidates, traces
    protected = {"disk", "edge_on_disk", "agn"}
    for fact in facts:
        role = fact.get("component")
        target = fact.get("model_label")
        if role not in components or not target:
            continue
        if role in protected:
            traces.append(
                _trace(
                    "REMOVE_PROTECTION_V1",
                    "NOT_APPLICABLE",
                    inputs=(role, target),
                )
            )
            continue
        if role == "bar" and fact.get("diffraction_psf_conflict") is True:
            traces.append(
                _trace(
                    "BAR_DIFFRACTION_CONFLICT_V1",
                    "INCONCLUSIVE",
                    inputs=(target,),
                    detail="diffraction_psf conflict is not a removal decision.",
                )
            )
            continue
        issue = any(
            fact.get(name) is True
            for name in (
                "parameter_boundary",
                "degenerate",
                "position_drift",
                "negative_residual",
                "overfit",
                "role_conflict",
            )
        )
        if fact.get("support_evidence") is False and fact.get("data_quality_ok") is True and issue:
            action = {
                "action_type": "PROPOSE_REMOVE",
                "component": role,
                "target_model_label": target,
                "reason_code": "COMPONENT_SUPPORT_FALSE",
            }
            candidates.append(_candidate("COMPONENT_REMOVE_V1", 3, action))
            traces.append(
                _trace(
                    "COMPONENT_REMOVE_V1",
                    "SATISFIED",
                    inputs=(role, target),
                )
            )
    return candidates, traces


def _termination_checks(
    numeric: dict[str, Any],
    components: set[str],
    traces: list[dict[str, Any]],
    has_structural_candidate: bool,
) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []

    fit = _first_value(numeric, "fit_convergence_summary")
    if isinstance(fit, dict) and fit.get("fit_succeeded") is True:
        checks.append({"check_id": "FIT_CONVERGENCE", "status": "PASS"})
    elif isinstance(fit, dict) and fit.get("fit_succeeded") is False:
        checks.append({"check_id": "FIT_CONVERGENCE", "status": "FAIL"})
    else:
        checks.append({"check_id": "FIT_CONVERGENCE", "status": "UNAVAILABLE"})

    residual = _first_value(numeric, "absolute_residual_quality")
    if isinstance(residual, dict):
        clean = residual.get("one_d_clean") is True and residual.get("central_elongation") is False
        checks.append(
            {
                "check_id": "ABSOLUTE_RESIDUAL",
                "status": "PASS" if clean else "FAIL",
                "detail": "Current absolute residual quality",
            }
        )
    else:
        checks.append({"check_id": "ABSOLUTE_RESIDUAL", "status": "UNAVAILABLE"})

    parameter_health = _first_value(numeric, "component_parameter_health")
    if isinstance(parameter_health, list) and parameter_health:
        healthy = all(
            isinstance(item.get("value"), (int, float))
            and item.get("at_boundary") is not True
            for component in parameter_health
            for item in component.get("parameters", [])
            if item.get("value") is not None
        )
        checks.append(
            {
                "check_id": "PARAMETER_HEALTH",
                "status": "PASS" if healthy else "FAIL",
            }
        )
    else:
        checks.append({"check_id": "PARAMETER_HEALTH", "status": "UNAVAILABLE"})

    center = _first_value(numeric, "center_constraint_health")
    if isinstance(center, list) and center:
        healthy = all(
            item.get("is_reference") is True
            or (
                item.get("constraint_present") is True
                and item.get("offset_from_reference") == 0
            )
            for item in center
        )
        checks.append(
            {
                "check_id": "CENTER_CONSTRAINT",
                "status": "PASS" if healthy else "FAIL",
            }
        )
    else:
        checks.append({"check_id": "CENTER_CONSTRAINT", "status": "UNAVAILABLE"})

    required = _first_value(numeric, "required_fixed_parameter_health")
    required_items = [
        item for item in required or []
        if item.get("parameter") != "fourier_m1_target"
        and item.get("component") in components
    ]
    if "fourier_m1" in components:
        required_items.extend(
            item for item in required or [] if item.get("parameter") == "fourier_m1_target"
        )
    if required_items and all(item.get("satisfied") is True for item in required_items):
        checks.append({"check_id": "REQUIRED_FIXED_PARAMETERS", "status": "PASS"})
    else:
        checks.append(
            {
                "check_id": "REQUIRED_FIXED_PARAMETERS",
                "status": "UNAVAILABLE" if not required_items else "FAIL",
            }
        )

    inconclusive = any(
        item.get("outcome") == "INCONCLUSIVE"
        and item.get("blocking", True)
        for item in traces
    )
    checks.append(
        {
            "check_id": "RULES_COMPLETE",
            "status": "PASS"
            if not has_structural_candidate and not inconclusive
            else "FAIL",
        }
    )
    checks.append(
        {
            "check_id": "NO_HIGH_PRIORITY_INCONCLUSIVE",
            "status": "PASS" if not inconclusive else "FAIL",
        }
    )
    return checks


def _select_candidates(
    candidates: list[dict[str, Any]],
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    if not candidates:
        return None, []
    ordered = sorted(
        enumerate(candidates),
        key=lambda item: (
            item[1]["priority"],
            1 if item[1]["action"].get("action_type") == "INCONCLUSIVE" else 0,
            item[0],
        ),
    )
    selected_index = ordered[0][0]
    result = []
    for index, candidate in enumerate(candidates):
        item = dict(candidate)
        item["status"] = "SELECTED" if index == selected_index else (
            "INCONCLUSIVE"
            if candidate["action"].get("action_type") == "INCONCLUSIVE"
            else "DEFERRED"
        )
        result.append(item)
    return candidates[selected_index]["action"], result


def decide_proposal(
    *,
    round_id: str,
    numeric_evidence: dict[str, Any],
    vlm_evidence: dict[str, Any],
    current_components: Iterable[str],
    current_profile: Iterable[Mapping[str, Any]] | None = None,
    evidence_refs: dict[str, Any] | None = None,
    thresholds: RuleThresholds | None = None,
) -> dict[str, Any]:
    """Evaluate every applicable rule, then select one proposal action."""

    thresholds = thresholds or RuleThresholds()
    validate(numeric_evidence, "numeric_evidence")
    validate(vlm_evidence, "vlm_evidence")
    if numeric_evidence["round_id"] != round_id or vlm_evidence["round_id"] != round_id:
        raise ValueError("round_id must match both evidence artifacts")
    traces: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    components = set(current_components)
    profile = [dict(item) for item in (current_profile or [])]
    unclassified_sersic = [
        item for item in profile
        if str(item.get("model_type", item.get("type", ""))).lower() in {"sersic", "sersic_f"}
        and str(item.get("semantic_label", "unclassified")).lower()
        in {"unclassified", "single_sersic", "none", ""}
    ]
    promotion_target = (
        str(unclassified_sersic[0].get("component_id") or unclassified_sersic[0].get("model_label"))
        if len(unclassified_sersic) == 1
        else None
    )
    if vlm_evidence["parse_status"] != "OK":
        traces.append(
            _trace(
                "VLM_UNAVAILABLE_V1",
                "INCONCLUSIVE",
                detail=f"VLM parse status: {vlm_evidence['parse_status']}; VLM-dependent rules remain inconclusive.",
            )
        )

    refit_candidates, refit_traces = _parameter_refit_candidates(numeric_evidence, components)
    candidates.extend(refit_candidates)
    traces.extend(refit_traces)
    remove_candidates, remove_traces = _remove_candidates(numeric_evidence, components)
    candidates.extend(remove_candidates)
    traces.extend(remove_traces)

    ordered_rules: list[tuple[int, bool, Any]] = [
        (4, not ({"disk", "edge_on_disk"} & components), _disk_rule),
        (3, "disk" in components and "edge_on_disk" not in components, _edge_on_rule),
        (
            4,
            not ({"bulge", "agn", "compact_central_source_candidate"} & components),
            _central_source_rule,
        ),
        (4, "bar" not in components and bool({"disk", "edge_on_disk"} & components), _bar_rule),
        (4, "lens" not in components and "bar" in components, _lens_rule),
        (4, "fourier_m1" not in components and bool({"disk", "edge_on_disk"} & components), _m1_rule),
        (4, "companion" not in components, _companion_rule),
    ]
    for priority, applicable, rule in ordered_rules:
        if not applicable:
            continue
        if rule is _m1_rule:
            rule_action, trace = rule(numeric_evidence, thresholds)
        else:
            if rule is _disk_rule:
                rule_action, trace = rule(
                    numeric_evidence,
                    vlm_evidence,
                    thresholds,
                    promotion_target=promotion_target,
                )
            else:
                rule_action, trace = rule(numeric_evidence, vlm_evidence, thresholds)
        traces.append(trace)
        if rule_action is not None:
            candidates.append(
                _candidate(
                    trace["rule_id"],
                    priority,
                    rule_action,
                )
            )

    structural_candidates = [
        item for item in candidates
        if item["action"].get("action_type") in {
            "PROPOSE_ADD", "PROPOSE_REMOVE", "PROPOSE_REPLACE",
            "PROMOTE_SINGLE_SERSIC_TO_DISK", "REFIT_PARAMETERS"
        }
    ]
    inconclusive = any(
        item.get("outcome") == "INCONCLUSIVE"
        and item.get("blocking", True)
        for item in traces
    )
    selected, candidate_artifact = _select_candidates(candidates)
    if selected is None:
        checks = _termination_checks(
            numeric_evidence,
            components,
            traces,
            bool(structural_candidates),
        )
        if all(item["status"] == "PASS" for item in checks):
            selected = {
                "action_type": "CONVERGED",
                "reason_code": "ALL_TERMINATION_GATES_PASS",
            }
            candidate_artifact = []
        elif inconclusive:
            selected = {"action_type": "INCONCLUSIVE"}
            candidate_artifact = []
        else:
            selected = None
            candidate_artifact = []
    else:
        checks = _termination_checks(
            numeric_evidence,
            components,
            traces,
            bool(structural_candidates),
        )
    return _decision(
        round_id=round_id,
        state="PROPOSE",
        action=selected,
        raw_action=selected,
        traces=traces or [_trace("NO_APPLICABLE_RULE_V1", "NOT_APPLICABLE")],
        candidate_actions=candidate_artifact,
        evidence_refs=evidence_refs,
        thresholds=thresholds,
        workflow_status=(
            "CONVERGED"
            if selected and selected.get("action_type") == "CONVERGED"
            else "STOPPED_NEEDS_REVIEW"
            if selected is None
            else "STOPPED_NEEDS_REVIEW"
            if selected.get("action_type") == "INCONCLUSIVE"
            else "CONTINUE"
        ),
        termination_checks=checks,
    )


def evaluate_refit(
    *,
    round_id: str,
    component: str,
    refit_evaluation: dict[str, Any],
    candidate_action_type: str | None = None,
    candidate_reason_code: str | None = None,
    evidence_refs: dict[str, Any] | None = None,
    thresholds: RuleThresholds | None = None,
) -> dict[str, Any]:
    """Evaluate one completed refit with operation-aware gates."""

    thresholds = thresholds or RuleThresholds()
    evaluation = dict(refit_evaluation)
    if "residual_outcome" not in evaluation:
        legacy = evaluation.get("residual_improved")
        evaluation["residual_outcome"] = {
            "yes": "improved",
            "no": "worse",
            "inconclusive": "inconclusive",
        }.get(legacy, "inconclusive")
    evaluation.pop("residual_improved", None)
    action_type = candidate_action_type or evaluation.get("candidate_action_type") or "PROPOSE_ADD"
    evaluation["candidate_action_type"] = action_type
    if candidate_reason_code:
        evaluation["reason_code"] = candidate_reason_code
    mandatory_disk_n = (
        candidate_reason_code in {
            "DISK_N_NOT_FIXED",
            "DISK_CONFIRMED_SINGLE_SERSIC_PROMOTION",
        }
        and action_type in {"REFIT_PARAMETERS", "PROMOTE_SINGLE_SERSIC_TO_DISK"}
    )
    required = ("fit_converged", "residual_outcome", "parameters_physical")
    missing = [name for name in required if name not in evaluation]
    if missing:
        raise ValueError(f"refit_evaluation missing fields: {', '.join(missing)}")

    traces: list[dict[str, Any]] = []
    gate_values = [evaluation[name] for name in required]
    if mandatory_disk_n:
        mandatory_gates = ("fit_converged", "parameters_physical")
        if any(evaluation[name] == "inconclusive" for name in mandatory_gates):
            traces.append(
                _trace(
                    "MANDATORY_DISK_N_SPEC_V1",
                    "INCONCLUSIVE",
                    inputs=mandatory_gates,
                    detail=(
                        "Disk n=1 is required after Disk confirmation; fit quality "
                        "is recorded but is not a rejection gate."
                    ),
                )
            )
            action = {"action_type": "INCONCLUSIVE"}
        elif any(evaluation[name] == "no" for name in mandatory_gates):
            failed = [name for name in mandatory_gates if evaluation[name] == "no"]
            traces.append(
                _trace(
                    "MANDATORY_DISK_N_SPEC_V1",
                    "NOT_SATISFIED",
                    inputs=mandatory_gates,
                    unmet=failed,
                    detail=(
                        "The mandatory Disk n=1 refit still requires a converged, "
                        "physical result."
                    ),
                )
            )
            action = {"action_type": "REJECT_REFIT", "component": component}
        elif evaluation.get("boundary_hits") or evaluation.get("degeneracy_warnings"):
            health_inputs = tuple(
                evaluation.get("boundary_hits", [])
                + evaluation.get("degeneracy_warnings", [])
            )
            traces.append(
                _trace(
                    "MANDATORY_DISK_N_SPEC_V1",
                    "NOT_SATISFIED",
                    inputs=health_inputs,
                    detail="The mandatory refit produced a parameter-health warning.",
                )
            )
            action = {"action_type": "REJECT_REFIT", "component": component}
        else:
            traces.append(
                _trace(
                    "MANDATORY_DISK_N_SPEC_V1",
                    "SATISFIED",
                    inputs=mandatory_gates,
                    detail=(
                        "Disk n=1 is a specification requirement; residual, BIC and "
                        "reduced-chisq changes are retained for audit only."
                    ),
                )
            )
            action = {"action_type": "ACCEPT_REFIT", "component": component}
    elif "inconclusive" in gate_values:
        traces.append(_trace("REFIT_PRIMARY_GATES_V1", "INCONCLUSIVE", inputs=required))
        action = {"action_type": "INCONCLUSIVE"}
    elif "no" in gate_values or evaluation["residual_outcome"] == "worse":
        failed = [
            name for name in required
            if evaluation[name] == "no"
        ]
        if evaluation["residual_outcome"] == "worse":
            failed.append("residual_outcome")
        traces.append(
            _trace("REFIT_PRIMARY_GATES_V1", "NOT_SATISFIED", inputs=required, unmet=failed)
        )
        action = {"action_type": "REJECT_REFIT", "component": component}
    elif evaluation.get("boundary_hits") or evaluation.get("degeneracy_warnings"):
        traces.append(
            _trace(
                "REFIT_HEALTH_GATE_V1",
                "NOT_SATISFIED",
                inputs=tuple(
                    evaluation.get("boundary_hits", [])
                    + evaluation.get("degeneracy_warnings", [])
                ),
            )
        )
        action = {"action_type": "REJECT_REFIT", "component": component}
    else:
        accepted_residual = (
            evaluation["residual_outcome"] in {"improved", "equivalent"}
            and (
                action_type != "PROPOSE_ADD"
                or evaluation["residual_outcome"] == "improved"
            )
        )
        if not accepted_residual:
            traces.append(
                _trace(
                    "REFIT_RESIDUAL_GATE_V1",
                    "NOT_SATISFIED",
                    inputs=("residual_outcome",),
                )
            )
            action = {"action_type": "REJECT_REFIT", "component": component}
        else:
            traces.append(_trace("REFIT_PRIMARY_GATES_V1", "SATISFIED", inputs=required))
            optional_components = {
                "agn", "compact_central_source_candidate", "companion", "lens"
            }
            if action_type == "PROPOSE_ADD" and component in optional_components:
                bic = evaluation.get("bic")
                if not bic or not bic.get("comparable", False):
                    traces.append(_trace("OPTIONAL_COMPONENT_BIC_GATE_V1", "INCONCLUSIVE"))
                    action = {"action_type": "INCONCLUSIVE"}
                elif bic["bic_gain"] < thresholds.optional_bic_gain:
                    traces.append(
                        _trace(
                            "OPTIONAL_COMPONENT_BIC_GATE_V1",
                            "NOT_SATISFIED",
                            detail=(
                                f"BIC_gain={bic['bic_gain']:.6g} < "
                                f"{thresholds.optional_bic_gain:.6g}"
                            ),
                        )
                    )
                    action = {"action_type": "REJECT_REFIT", "component": component}
                else:
                    traces.append(_trace("OPTIONAL_COMPONENT_BIC_GATE_V1", "SATISFIED"))
                    action = {"action_type": "ACCEPT_REFIT", "component": component}
            else:
                traces.append(
                    _trace(
                        "PRIMARY_COMPONENT_PHYSICAL_GATE_V1",
                        "SATISFIED",
                    )
                )
                action = {"action_type": "ACCEPT_REFIT", "component": component}

    candidate = {
        "rule_id": "REFIT_EVALUATION_V1",
        "priority": 1,
        "status": "SELECTED",
        "action": action,
        "detail": action_type,
    }
    return _decision(
        round_id=round_id,
        state="EVALUATE_REFIT",
        action=action,
        raw_action=action,
        candidate_actions=[candidate],
        traces=traces,
        evidence_refs=evidence_refs,
        thresholds=thresholds,
        workflow_status=(
            "STOPPED_NEEDS_REVIEW"
            if action.get("action_type") == "INCONCLUSIVE"
            else "CONTINUE"
        ),
        refit_evaluation=evaluation,
    )
