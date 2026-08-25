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
) -> dict[str, Any]:
    return {
        "rule_id": rule_id,
        "outcome": outcome,
        "inputs": list(inputs),
        "unmet_conditions": list(unmet),
        "detail": detail,
    }


def _decision(
    *,
    round_id: str,
    state: str,
    action: dict[str, Any],
    traces: list[dict[str, Any]],
    evidence_refs: dict[str, Any] | None,
    thresholds: RuleThresholds,
    refit_evaluation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    artifact: dict[str, Any] = {
        "schema_version": "1.0",
        "round_id": round_id,
        "rules_version": "component-rules@v1",
        "thresholds_version": thresholds.version,
        "state": state,
        "action": action,
        "rule_trace": traces,
        "evidence_refs": {
            "numeric_evidence": None,
            "vlm_evidence": None,
            **(evidence_refs or {}),
        },
    }
    if refit_evaluation is not None:
        artifact["refit_evaluation"] = refit_evaluation
    validate(artifact, "decision_artifact")
    return artifact


def _disk_rule(
    numeric: dict[str, Any],
    vlm: dict[str, Any],
    thresholds: RuleThresholds,
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
        return {"action_type": "PROPOSE_ADD", "component": "disk"}, _trace(
            "DISK_N1_N2_N3_V1",
            "SATISFIED",
            inputs=(
                "source_extent_psf_ratio",
                "outer_isophote_geometry",
                "single_sersic_n",
                "outer_residual_systematic",
                "central",
            ),
    )
    if n1 and not n2 and not n3 and n_unbound and n_value >= thresholds.spheroid_n_min:
        return {"action_type": "KEEP_AND_CONTINUE"}, _trace(
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


def decide_proposal(
    *,
    round_id: str,
    numeric_evidence: dict[str, Any],
    vlm_evidence: dict[str, Any],
    current_components: Iterable[str],
    evidence_refs: dict[str, Any] | None = None,
    thresholds: RuleThresholds | None = None,
) -> dict[str, Any]:
    """Return exactly one deterministic proposal without executing it."""

    thresholds = thresholds or RuleThresholds()
    validate(numeric_evidence, "numeric_evidence")
    validate(vlm_evidence, "vlm_evidence")
    if numeric_evidence["round_id"] != round_id or vlm_evidence["round_id"] != round_id:
        raise ValueError("round_id must match both evidence artifacts")
    if vlm_evidence["parse_status"] != "OK":
        return _decision(
            round_id=round_id,
            state="PROPOSE",
            action={"action_type": "INCONCLUSIVE"},
            traces=[
                _trace(
                    "VLM_UNAVAILABLE_V1",
                    "INCONCLUSIVE",
                    detail=f"VLM parse status: {vlm_evidence['parse_status']}",
                )
            ],
            evidence_refs=evidence_refs,
            thresholds=thresholds,
        )

    components = set(current_components)
    ordered_rules: list[tuple[bool, Any]] = [
        (not ({"disk", "edge_on_disk"} & components), _disk_rule),
        ("disk" in components and "edge_on_disk" not in components, _edge_on_rule),
        (
            not ({"bulge", "agn", "compact_central_source_candidate"} & components),
            _central_source_rule,
        ),
        ("bar" not in components and bool({"disk", "edge_on_disk"} & components), _bar_rule),
        ("lens" not in components and "bar" in components, _lens_rule),
        ("fourier_m1" not in components and bool({"disk", "edge_on_disk"} & components), _m1_rule),
        ("companion" not in components, _companion_rule),
    ]
    traces: list[dict[str, Any]] = []
    for applicable, rule in ordered_rules:
        if not applicable:
            continue
        if rule is _m1_rule:
            action, trace = rule(numeric_evidence, thresholds)
        else:
            action, trace = rule(numeric_evidence, vlm_evidence, thresholds)
        traces.append(trace)
        if action is not None:
            return _decision(
                round_id=round_id,
                state="PROPOSE",
                action=action,
                traces=traces,
                evidence_refs=evidence_refs,
                thresholds=thresholds,
            )

    return _decision(
        round_id=round_id,
        state="PROPOSE",
        action={"action_type": "KEEP_AND_CONTINUE"},
        traces=traces or [_trace("NO_APPLICABLE_RULE_V1", "NOT_APPLICABLE")],
        evidence_refs=evidence_refs,
        thresholds=thresholds,
    )


def evaluate_refit(
    *,
    round_id: str,
    component: str,
    refit_evaluation: dict[str, Any],
    evidence_refs: dict[str, Any] | None = None,
    thresholds: RuleThresholds | None = None,
) -> dict[str, Any]:
    """Accept, reject or defer one completed refit using deterministic gates."""

    thresholds = thresholds or RuleThresholds()
    required = ("fit_converged", "residual_improved", "parameters_physical")
    missing = [name for name in required if name not in refit_evaluation]
    if missing:
        raise ValueError(f"refit_evaluation missing fields: {', '.join(missing)}")

    traces: list[dict[str, Any]] = []
    gate_values = [refit_evaluation[name] for name in required]
    if "inconclusive" in gate_values:
        traces.append(_trace("REFIT_PRIMARY_GATES_V1", "INCONCLUSIVE", inputs=required))
        action = {"action_type": "INCONCLUSIVE"}
    elif "no" in gate_values:
        failed = [name for name in required if refit_evaluation[name] == "no"]
        traces.append(
            _trace("REFIT_PRIMARY_GATES_V1", "NOT_SATISFIED", inputs=required, unmet=failed)
        )
        action = {"action_type": "REJECT_REFIT", "component": component}
    elif refit_evaluation.get("boundary_hits"):
        traces.append(
            _trace(
                "REFIT_BOUNDARY_GATE_V1",
                "NOT_SATISFIED",
                inputs=tuple(refit_evaluation["boundary_hits"]),
            )
        )
        action = {"action_type": "REJECT_REFIT", "component": component}
    else:
        traces.append(_trace("REFIT_PRIMARY_GATES_V1", "SATISFIED", inputs=required))
        optional_components = {"agn", "compact_central_source_candidate", "companion", "lens"}
        if component in optional_components:
            bic = refit_evaluation.get("bic")
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
            bic = refit_evaluation.get("bic")
            detail = None
            if bic and bic.get("comparable") and bic.get("bic_gain", 0) < 0:
                detail = "Primary structure accepted on physical and residual gates despite BIC loss."
            traces.append(_trace("PRIMARY_COMPONENT_PHYSICAL_GATE_V1", "SATISFIED", detail=detail))
            action = {"action_type": "ACCEPT_REFIT", "component": component}

    return _decision(
        round_id=round_id,
        state="EVALUATE_REFIT",
        action=action,
        traces=traces,
        evidence_refs=evidence_refs,
        thresholds=thresholds,
        refit_evaluation=refit_evaluation,
    )
