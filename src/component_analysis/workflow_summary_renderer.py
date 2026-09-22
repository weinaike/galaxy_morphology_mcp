"""Deterministic, read-only Markdown rendering for multi-band workflow rounds."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from jsonschema import ValidationError

from schemas import validate


RENDERER_VERSION = "workflow-summary-renderer@v2"


def _read_validated(path: Path, schema: str) -> tuple[dict[str, Any] | None, str]:
    """Read one JSON artifact only when it satisfies its frozen schema."""
    if not path.is_file():
        return None, "unavailable"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            return None, "schema validation failed"
        validate(value, schema)
    except (OSError, ValueError, ValidationError):
        return None, "schema validation failed"
    return value, "available"


def _profile_lines(value: Any) -> list[str]:
    """Render component semantics without serializing profile JSON."""
    if not isinstance(value, list) or not value:
        return ["- 当前 profile：unavailable"]
    if all(isinstance(item, str) for item in value):
        return [f"- 当前 profile：{json.dumps(value, ensure_ascii=False)}"]
    lines: list[str] = []
    for item in value:
        if not isinstance(item, Mapping):
            lines.append(f"- {_value(item)}")
            continue
        label = item.get("semantic_label") or item.get("model_label") or item.get("component_id")
        lines.append(
            f"- {_value(label)}：model={_value(item.get('model_type'))}；"
            f"classification={_value(item.get('classification'))}"
        )
    return lines or ["- 当前 profile：unavailable"]


def _detection_lines(value: Mapping[str, Any] | None) -> list[str]:
    if not isinstance(value, Mapping):
        return ["- detect_bar_lopsidedness：unavailable"]
    results = value.get("results")
    if not isinstance(results, list):
        return [f"- detect_bar_lopsidedness：status={_value(value.get('status'))}"]
    lines = [f"- detect_bar_lopsidedness：status={_value(value.get('status'), fallback='success')}"]
    for item in results:
        if not isinstance(item, Mapping):
            continue
        band = _value(item.get("band"))
        bar = item.get("bar") if isinstance(item.get("bar"), Mapping) else {}
        lop = item.get("lopsidedness") if isinstance(item.get("lopsidedness"), Mapping) else {}
        lines.append(
            f"  - {band}：bar={_value(bar.get('detected'))}；"
            f"lopsidedness={_value(lop.get('detected'))}"
        )
    return lines


def _policy_state_lines(value: Any) -> list[str]:
    if not isinstance(value, Mapping):
        return ["- PolicyState：unavailable"]
    rejected = value.get("rejected_candidate_keys")
    collectors = value.get("evidence_collector_keys")
    return [
        f"- trial budget：{_value(value.get('trials_used'), fallback='未采集')}/"
        f"{_value(value.get('trial_budget'), fallback='未采集')}",
        f"- rejected candidate keys：{len(rejected) if isinstance(rejected, list) else '未采集'}",
        f"- evidence collectors used：{len(collectors) if isinstance(collectors, list) else '未采集'}",
        f"- last evidence fingerprint：{_value(value.get('last_evidence_fingerprint'), fallback='未采集')}",
    ]


def _timing_lines(timing: Any) -> list[str]:
    if not isinstance(timing, Mapping):
        return ["- timing：未采集"]
    attempts = timing.get("attempts")
    attempt_count = len(attempts) if isinstance(attempts, list) else "未采集"
    return [
        f"- timing：started={_value(timing.get('started_at'), fallback='未采集')}；"
        f"ended={_value(timing.get('ended_at'), fallback='未采集')}；"
        f"duration_s={_value(timing.get('duration_s'), fallback='未采集')}；"
        f"attempts={attempt_count}",
        f"- timing log：{_value(timing.get('timing_log_ref'), fallback='未采集')}",
    ]


def _error_summary(value: Any) -> str:
    if not isinstance(value, str) or not value:
        return "unavailable"
    parts = [part.strip() for part in value.split(";") if part.strip()]
    if not parts:
        return "unavailable"
    counts: dict[str, int] = {}
    for part in parts:
        counts[part] = counts.get(part, 0) + 1
    return "；".join(
        f"{message}（{count}次）" if count > 1 else message
        for message, count in sorted(counts.items())
    )


def _coverage_lines(provider: Mapping[str, Any]) -> list[str]:
    coverage = provider.get("coverage")
    if not isinstance(coverage, Mapping):
        return ["- target coverage：unavailable"]
    requested = coverage.get("requested") if isinstance(coverage.get("requested"), list) else []
    covered = coverage.get("covered") if isinstance(coverage.get("covered"), list) else []
    missing = coverage.get("missing") if isinstance(coverage.get("missing"), list) else []
    return [
        f"- target coverage：{len(covered)}/{len(requested)}；complete={_value(coverage.get('complete'))}",
        f"- covered targets：{_value(covered, fallback='未采集')}",
        f"- missing targets：{_value(missing, fallback='无')}",
    ]


def _value(value: Any, *, fallback: str = "unavailable") -> str:
    if value is None or value == "":
        return fallback
    if isinstance(value, dict):
        parts = [
            f"{key}={_value(item, fallback='未采集')}"
            for key, item in value.items()
        ]
        return "；".join(parts) or fallback
    if isinstance(value, list):
        return "、".join(_value(item, fallback="未采集") for item in value) or fallback
    return str(value)


def _bullet_lines(values: list[str], *, fallback: str = "unavailable") -> list[str]:
    return [f"- {value}" for value in values] or [f"- {fallback}"]


def _action_lines(decision: Mapping[str, Any] | None) -> list[str]:
    if not decision:
        return ["- unavailable"]
    action = decision.get("action")
    if not isinstance(action, Mapping):
        return ["- unavailable"]
    lines = [f"- action_type：{_value(action.get('action_type'))}"]
    for key in (
        "component", "replace_from", "replace_to", "target_model_label",
        "reason_code", "continuation_reason", "next_step", "next_transition",
        "collector_id", "evidence_targets",
    ):
        if key in action:
            lines.append(f"- {key}：{_value(action.get(key))}")
    changes = action.get("parameter_changes")
    if isinstance(changes, list):
        lines.append("- parameter_changes：")
        for change in changes:
            if isinstance(change, Mapping):
                lines.append(
                    "  - "
                    + "；".join(
                        f"{key}={_value(value, fallback='未采集')}"
                        for key, value in change.items()
                    )
                )
            else:
                lines.append(f"  - {_value(change)}")
    return lines


def _fit_assessment_lines(decision: Mapping[str, Any] | None) -> list[str]:
    evaluation = decision.get("refit_evaluation") if decision else None
    if not isinstance(evaluation, Mapping):
        return ["- 收敛：未采集", "- 残差：未采集", "- reduced chi-square：未采集", "- BIC：未采集", "- 参数物理性：未采集"]
    return [
        f"- 收敛：{_value(evaluation.get('fit_converged'), fallback='未采集')}",
        f"- 残差：{_value(evaluation.get('residual_outcome'), fallback='未采集')}",
        f"- reduced chi-square：{_value(evaluation.get('reduced_chisq'), fallback='未采集')}",
        f"- BIC：{_value(evaluation.get('bic'), fallback='未采集')}",
        f"- 参数物理性：{_value(evaluation.get('parameters_physical'), fallback='未采集')}",
        f"- 参数边界：{_value(evaluation.get('boundary_hits'), fallback='未采集')}",
        f"- 参数退化：{_value(evaluation.get('degeneracy_warnings'), fallback='未采集')}",
    ]


def _evidence_lines(
    proposal: Mapping[str, Any] | None,
    *,
    kind: str,
) -> list[str]:
    if not proposal:
        return ["- unavailable"]
    evidence = proposal.get(kind)
    if not isinstance(evidence, Mapping):
        return ["- unavailable"]
    if kind == "numeric_evidence":
        features = evidence.get("features")
        if not isinstance(features, list):
            return ["- unavailable"]
        rows = []
        for item in features:
            if not isinstance(item, Mapping):
                continue
            scope = item.get("band") or item.get("target_id") or item.get("region")
            scope_text = f"[{_value(scope)}]" if scope is not None else ""
            rows.append(
                f"{scope_text}{_value(item.get('name'))}：{_value(item.get('value'))}"
                f"（{_value(item.get('status'))}）"
            )
        return _bullet_lines(rows[:24])
    observations = evidence.get("observations")
    if not isinstance(observations, list):
        return ["- unavailable"]
    return _bullet_lines([
        f"target={_value(item.get('target_id'))}：{_value(item.get('label'))}，confidence={_value(item.get('confidence'))}；"
        f"证据定位={_value(item.get('evidence_regions'), fallback='未采集')}；"
        f"备注={_value(item.get('notes'), fallback='未采集')}"
        for item in observations[:12]
        if isinstance(item, Mapping)
    ])


def _provider_lines(proposal: Mapping[str, Any] | None) -> list[str]:
    provider = proposal.get("provider") if proposal else None
    if not isinstance(provider, Mapping):
        return ["- status：unavailable", "- attempts：未采集", "- timing：未采集"]
    attempts = provider.get("attempts")
    attempt_statuses = [
        item.get("parse_status", "unavailable")
        for item in attempts
        if isinstance(item, Mapping)
    ] if isinstance(attempts, list) else []
    timing = proposal.get("timing")
    timing_text = timing if isinstance(timing, Mapping) else None
    return [
        f"- provider status：{_value(provider.get('status'))}",
        f"- model：{_value(provider.get('model_id'), fallback='unavailable')}",
        f"- prompt_version：{_value(provider.get('prompt_version'), fallback='unavailable')}",
        f"- attempts：{len(attempt_statuses)}；状态={_value(sorted(set(attempt_statuses)), fallback='未采集')}",
        *_coverage_lines(provider),
        f"- response_bytes：{_value(provider.get('response_bytes'), fallback='unavailable')}",
        f"- finish_reason：{_value(provider.get('finish_reason'), fallback='unavailable')}",
        f"- token_usage：{_value(provider.get('token_usage'), fallback='unavailable')}",
        *_timing_lines(timing_text),
        f"- error：{_error_summary(provider.get('error'))}",
    ]


def _compact_evidence_view(view: Any) -> dict[str, Any] | None:
    if not isinstance(view, Mapping):
        return None
    trace = view.get("rule_trace")
    trace_summary = []
    if isinstance(trace, list):
        trace_summary = [
            {
                "rule_id": item.get("rule_id"),
                "outcome": item.get("outcome"),
                "unmet_conditions": item.get("unmet_conditions", []),
            }
            for item in trace
            if isinstance(item, Mapping)
        ]
    observations = view.get("observations")
    observation_targets = [
        item.get("target_id")
        for item in observations
        if isinstance(item, Mapping) and item.get("target_id") is not None
    ] if isinstance(observations, list) else []
    candidates = view.get("candidate_actions")
    candidate_types = [
        (item.get("action") or {}).get("action_type")
        for item in candidates
        if isinstance(item, Mapping) and isinstance(item.get("action"), Mapping)
    ] if isinstance(candidates, list) else []
    return {
        "parse_status": view.get("parse_status", "unavailable"),
        "observations": observation_targets,
        "rule_trace": trace_summary,
        "action": view.get("action", "unavailable"),
        "candidate_action_types": candidate_types,
    }


def _evidence_view_lines(
    decision: Mapping[str, Any] | None,
    proposal: Mapping[str, Any] | None = None,
) -> list[str]:
    if not isinstance(decision, Mapping):
        return ["- raw VLM decision view：未采集", "- numeric-only decision view：未采集", "- resolved decision view：未采集"]
    views = decision.get("evidence_views")
    views = dict(views) if isinstance(views, Mapping) else {}
    # Older, schema-valid rounds predate the explicit evidence_views field. The
    # proposal and decision artifacts still contain enough structured data to
    # render the view without changing the historical decision.
    if "vlm" not in views and isinstance(proposal, Mapping):
        vlm = proposal.get("vlm_evidence")
        if isinstance(vlm, Mapping):
            views["vlm"] = {
                "parse_status": vlm.get("parse_status", "unavailable"),
                "observations": vlm.get("observations", []),
                "rule_trace": (decision.get("raw_decision") or {}).get("rule_trace", []),
                "action": (decision.get("raw_decision") or {}).get("action", "unavailable"),
                "candidate_actions": decision.get("candidate_actions", []),
            }
    views.setdefault("numeric_only", {"parse_status": "NOT_RUN", "rule_trace": "unavailable", "action": "unavailable", "candidate_actions": []})
    resolved = {
        "workflow_status": decision.get("workflow_status", "unavailable"),
        "action": decision.get("action", "unavailable"),
        "automation": decision.get("automation", "unavailable"),
    }
    lines: list[str] = []
    for label, view in (
        ("raw VLM", views.get("vlm")),
        ("numeric-only", views.get("numeric_only")),
    ):
        compact = _compact_evidence_view(view)
        if not compact:
            lines.append(f"- {label} decision view：未采集")
            continue
        lines.extend(
            [
                f"- {label} parse_status：{_value(compact.get('parse_status'))}",
                f"- {label} observations：{_value(compact.get('observations'), fallback='未采集')}",
                f"- {label} candidate actions：{_value(compact.get('candidate_action_types'), fallback='未采集')}",
            ]
        )
        traces = compact.get("rule_trace") or []
        lines.append(f"- {label} rule conclusions：")
        if traces:
            lines.extend(
                f"  - {item.get('rule_id')}：{item.get('outcome')}；未满足={_value(item.get('unmet_conditions'), fallback='无')}"
                for item in traces
            )
        else:
            lines.append("  - 未采集")
    lines.extend(
        [
            f"- resolved workflow_status：{_value(resolved.get('workflow_status'))}",
            f"- resolved action：{_value((resolved.get('action') or {}).get('action_type') if isinstance(resolved.get('action'), Mapping) else None)}",
            f"- automation reason：{_value((resolved.get('automation') or {}).get('reason') if isinstance(resolved.get('automation'), Mapping) else None)}",
        ]
    )
    return lines


def _band_paths(
    manifest: Mapping[str, Any] | None,
    *,
    label: str = "",
) -> list[str]:
    if not manifest:
        return [f"- {label}unavailable" if label else "- unavailable"]
    rows = []
    for band in manifest.get("bands", []):
        if not isinstance(band, Mapping):
            continue
        rows.append(
            "- {label}{band}：result FITS `{result}`；summary `{summary}`；comparison `{comparison}`".format(
                label=label,
                band=_value(band.get("band")),
                result=_value(band.get("result_fits")),
                summary=_value(band.get("summary_file") or manifest.get("summary_file")),
                comparison=_value(manifest.get("comparison_png")),
            )
        )
    return rows or [f"- {label}unavailable" if label else "- unavailable"]


def _rule_lines(decision: Mapping[str, Any] | None) -> list[str]:
    if not isinstance(decision, Mapping):
        return ["- 未采集"]
    traces = decision.get("rule_trace")
    if not isinstance(traces, list) or not traces:
        traces = (decision.get("raw_decision") or {}).get("rule_trace", [])
    if not isinstance(traces, list) or not traces:
        return ["- 未采集"]
    lines: list[str] = []
    for trace in traces:
        if not isinstance(trace, Mapping):
            continue
        status = _value(trace.get("outcome"), fallback="未采集")
        blocking = "blocking" if trace.get("blocking") is True else "non-blocking"
        unmet = _value(trace.get("unmet_conditions"), fallback="无")
        detail = _value(trace.get("detail"), fallback="未采集")
        lines.append(
            f"- {trace.get('rule_id', 'unavailable')}：{status}（{blocking}）；"
            f"未满足={unmet}；说明={detail}"
        )
    return lines or ["- 未采集"]


def _baseline_fit_lines(proposal: Mapping[str, Any] | None) -> list[str]:
    if not proposal:
        return ["- 收敛：未采集", "- 残差：未采集", "- reduced chi-square：未采集", "- BIC：未采集", "- 参数物理性：未采集"]
    features = (proposal.get("numeric_evidence") or {}).get("features", [])
    values: dict[str, Any] = {}
    for item in features if isinstance(features, list) else []:
        if isinstance(item, Mapping) and item.get("status") == "AVAILABLE":
            values.setdefault(str(item.get("name")), item.get("value"))
    convergence = values.get("fit_convergence_summary")
    convergence_value = convergence.get("fit_succeeded") if isinstance(convergence, Mapping) else None
    missing = "未采集"
    residual_value = values.get("residual_profile")
    reduced_value = values.get("reduced_chisq")
    bic_value = values.get("bic")
    physical_value = values.get("parameter_health")
    return [
        f"- 收敛：{_value(convergence_value, fallback=missing)}",
        f"- 残差：{_value(residual_value, fallback=missing)}",
        f"- reduced chi-square：{_value(reduced_value, fallback=missing)}",
        f"- BIC：{_value(bic_value, fallback=missing)}",
        f"- 参数物理性：{_value(physical_value, fallback=missing)}",
    ]


def _round_conclusion(round_dir: Path) -> str:
    manifest, _ = _read_validated(round_dir / "workflow_manifest.json", "workflow_round_manifest")
    proposal, _ = _read_validated(round_dir / "proposal" / "workflow_proposal.json", "workflow_proposal")
    lifecycle, _ = _read_validated(round_dir / "lifecycle.json", "workflow_lifecycle")
    terminal, _ = _read_validated(round_dir / "terminal_lifecycle.json", "workflow_lifecycle")
    candidate, _ = _read_validated(round_dir / "candidate_lifecycle.json", "workflow_lifecycle")
    lifecycle = terminal or lifecycle
    if not manifest and not proposal and not lifecycle:
        return f"- `{round_dir.name}`：结构化 artifact unavailable"
    decision = lifecycle.get("resolved_decision") if lifecycle else None
    action = decision.get("action") if isinstance(decision, Mapping) else None
    action_type = action.get("action_type") if isinstance(action, Mapping) else None
    summary = (candidate or lifecycle or {}).get("action_summary", {})
    verdict = summary.get("refit_verdict") if isinstance(summary, Mapping) else None
    next_step = (lifecycle or {}).get("next_step")
    workflow_status = (
        decision.get("workflow_status")
        if isinstance(decision, Mapping)
        else "unavailable"
    )
    terminal_review = workflow_status == "STOPPED_NEEDS_REVIEW"
    if terminal_review:
        verdict = None
    provider = proposal.get("provider") if isinstance(proposal, Mapping) else {}
    provider_status = provider.get("status") if isinstance(provider, Mapping) else "unavailable"
    rule_items = (
        [
            f"{item.get('rule_id')}={item.get('outcome')}"
            for item in (decision or {}).get("rule_trace", [])
            if isinstance(item, Mapping)
        ]
        if isinstance(decision, Mapping)
        else []
    )
    executed = summary.get("executed_action_type") if isinstance(summary, Mapping) else None
    if executed is None and isinstance(summary, Mapping):
        executed = summary.get("executed")
    action_text = "未执行模型动作" if terminal_review else (
        executed or (action_type if action_type not in {"REJECT_REFIT", "ACCEPT_REFIT"} else "未执行模型动作")
    )
    return "\n".join(
        [
            f"- {(manifest or {}).get('round_id', round_dir.name)}："
            f"workflow_status={_value(workflow_status)}；"
            f"VLM={_value(provider_status)}；"
            f"动作={_value(action_text)}；"
            f"refit={_value(verdict, fallback='未采集')}；"
            f"下一步={_value(next_step, fallback='unavailable')}",
            f"  - 规则结论：{_value(rule_items, fallback='未采集')}",
        ]
    )


def _detected_features(value: Mapping[str, Any] | None) -> list[str] | None:
    if not isinstance(value, Mapping):
        return None
    results = value.get("results")
    if not isinstance(results, list):
        return None
    features: list[str] = []
    for item in results:
        if not isinstance(item, Mapping):
            continue
        band = str(item.get("band", "unavailable"))
        bar = item.get("bar")
        lop = item.get("lopsidedness")
        if isinstance(bar, Mapping) and bar.get("detected") is True:
            features.append(f"{band}:bar")
        if isinstance(lop, Mapping) and lop.get("detected") is True:
            features.append(f"{band}:lopsidedness")
    return features


def render_image_round(
    *,
    round_dir: str | Path,
    summary_dir: str | Path,
    state: Mapping[str, Any] | None = None,
    detect_result: Mapping[str, Any] | None = None,
) -> Path:
    """Render one Image round from schema-validated JSON artifacts only."""
    round_path = Path(round_dir).expanduser().resolve()
    summaries = Path(summary_dir).expanduser().resolve()
    manifest, manifest_status = _read_validated(round_path / "workflow_manifest.json", "workflow_round_manifest")
    proposal, proposal_status = _read_validated(round_path / "proposal" / "workflow_proposal.json", "workflow_proposal")
    lifecycle, lifecycle_status = _read_validated(round_path / "lifecycle.json", "workflow_lifecycle")
    terminal_lifecycle, terminal_status = _read_validated(
        round_path / "terminal_lifecycle.json", "workflow_lifecycle"
    )
    if terminal_lifecycle is not None:
        lifecycle = terminal_lifecycle
        lifecycle_status = terminal_status
    candidate_lifecycle, candidate_lifecycle_status = _read_validated(
        round_path / "candidate_lifecycle.json", "workflow_lifecycle"
    )
    verifier, _ = _read_validated(round_path / "workflow_verifier.json", "workflow_verifier")
    decision = lifecycle.get("resolved_decision") if lifecycle else None
    candidate_decision = candidate_lifecycle.get("resolved_decision") if candidate_lifecycle else None
    if isinstance(decision, Mapping) and not isinstance(decision.get("action"), Mapping):
        decision = {**decision, "action": {}}
    object_id = _value((manifest or {}).get("object_id"), fallback=_value((state or {}).get("object_id")))
    round_id = _value((manifest or {}).get("round_id"), fallback=round_path.name)
    action_summary = (candidate_lifecycle or lifecycle or {}).get("action_summary", {})
    candidate_manifest, candidate_manifest_status = _read_validated(
        round_path / "candidate_manifest.json", "workflow_round_manifest"
    )
    baseline_constraints = (manifest or {}).get("constraint_files", [])
    candidate_constraints = (candidate_manifest or {}).get("constraint_files", [])
    profile_value = (proposal or {}).get("current_profile")
    if profile_value is None:
        profile_value = (proposal or {}).get("current_components")
    confirmed_value = (proposal or {}).get("confirmed_components")
    detection = detect_result or (proposal or {}).get("round0_detection")
    profile_lines = _profile_lines(profile_value)
    candidate_exists = candidate_manifest is not None
    executed_action = action_summary.get("executed_action_type") if isinstance(action_summary, Mapping) else None
    if executed_action is None and isinstance(action_summary, Mapping):
        executed_action = action_summary.get("executed")
    if not candidate_exists:
        fitting_conclusion = "- 本轮没有生成 candidate lyric，未执行新的 Image fitting。"
    elif executed_action:
        fitting_conclusion = f"- 本轮执行模型动作：{_value(executed_action)}。"
    else:
        fitting_conclusion = "- candidate 产物已记录；执行动作状态：未采集。"
    content = [
        f"# Image Round {round_id} Component Analysis",
        "",
        f"- renderer：{RENDERER_VERSION}",
        f"- object_id：{object_id}",
        f"- manifest：{manifest_status}",
        f"- proposal：{proposal_status}",
        f"- lifecycle：{lifecycle_status}",
        f"- candidate lifecycle：{candidate_lifecycle_status}",
        f"- fixture_only：{_value((state or {}).get('fixture_only'), fallback='unavailable')}",
        f"- historical_pre_fix：{_value((state or {}).get('historical_pre_fix'), fallback='unavailable')}",
        "",
        "## Round 0：原图成分预测",
        *_detection_lines(detection),
        f"- detected_features：{_value(_detected_features(detection), fallback='unavailable')}",
        f"- 高概率存在成分：{_value((proposal or {}).get('predicted_components'), fallback='unavailable')}",
        "",
        "## 当前模型与已确认成分",
        *profile_lines,
        f"- 已确认成分：{_value(confirmed_value, fallback='unavailable')}",
        "",
        "## Numeric Evidence",
        *_evidence_lines(proposal, kind="numeric_evidence"),
        "",
        "## VLM Evidence",
        *_provider_lines(proposal),
        *_evidence_lines(proposal, kind="vlm_evidence"),
        "",
        "## Evidence Views",
        *_evidence_view_lines(
            decision if isinstance(decision, Mapping) else None,
            proposal if isinstance(proposal, Mapping) else None,
        ),
        "",
        "## Rules 逐条结论",
        *_rule_lines(decision if isinstance(decision, Mapping) else None),
        "",
        "## 动作决策",
        "### Raw Action",
        *_action_lines((decision or {}).get("raw_decision") if isinstance(decision, Mapping) else None),
        "",
        "### Resolved Action",
        *_action_lines(decision if isinstance(decision, Mapping) else None),
        "",
        "## 新 lyric 与参数／约束差异",
        f"- baseline lyric：{_value((manifest or {}).get('config_file'))}",
        f"- candidate lyric：{_value((candidate_manifest or {}).get('config_file'), fallback='未采集')}",
        f"- baseline constraints：{_value(baseline_constraints, fallback='未采集')}",
        f"- candidate constraints：{_value(candidate_constraints, fallback='未采集')}",
        "",
        "## 波段产物路径",
        "### baseline",
        *_band_paths(manifest),
        "### candidate",
        f"- manifest：{candidate_manifest_status}",
        *_band_paths(candidate_manifest),
        f"- artifact index：{_value((state or {}).get('artifact_index_file'), fallback='未采集')}",
        "",
        "## 拟合评价",
        fitting_conclusion,
        "### baseline",
        *_baseline_fit_lines(proposal),
        "### candidate",
        *_fit_assessment_lines(candidate_decision if isinstance(candidate_decision, Mapping) else decision if isinstance(decision, Mapping) else None),
        f"- refit verdict：{_value((action_summary or {}).get('refit_verdict'), fallback='未采集')}",
        "",
        "## 状态与下一步",
        *_policy_state_lines((lifecycle or {}).get("policy_state")),
        f"- needs_review：{_value((lifecycle or {}).get('needs_review'), fallback='unavailable')}",
        f"- next_transition：{_value((decision or {}).get('action', {}).get('next_transition') if isinstance(decision, Mapping) else None, fallback=_value((lifecycle or {}).get('next_step'), fallback='unavailable'))}",
        f"- verifier：{_value((verifier or {}).get('verdict'), fallback='未采集')}",
        f"- 下一步动作或停止原因：{_value((state or {}).get('termination_reason'), fallback=_value((lifecycle or {}).get('next_step'), fallback='unavailable'))}",
        "",
        "本文件仅由已通过 schema 校验的结构化 artifact 渲染，不参与任何机器动作。",
    ]
    summaries.mkdir(parents=True, exist_ok=True)
    target = summaries / f"round_{round_id}_component_analysis.md"
    target.write_text("\n".join(content) + "\n", encoding="utf-8")
    return target


def render_working_note(
    *,
    summary_dir: str | Path,
    round_summary_files: list[str | Path],
    round_dirs: list[str | Path] | None = None,
) -> Path:
    """Create a deterministic object-level note from validated round artifacts."""
    summaries = Path(summary_dir).expanduser().resolve()
    rounds = [Path(value).expanduser().resolve() for value in round_summary_files]
    lines = ["# Working Note", "", f"- renderer：{RENDERER_VERSION}", "- Round 0：原图成分预测见首个 Image round summary。", "", "## Image Rounds"]
    if round_dirs:
        for round_dir in round_dirs:
            lines.append(_round_conclusion(Path(round_dir).expanduser().resolve()))
    lines.extend([f"- report：[{path.stem}]({path.name})" for path in rounds] or ["- unavailable"])
    target = summaries / "working_note.md"
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target


def render_final_report(
    *,
    object_id: str,
    summary_dir: str | Path,
    state: Mapping[str, Any],
    round_dirs: list[str | Path] | None = None,
) -> Path:
    """Render the terminal report without inferring scientific optimality."""
    summaries = Path(summary_dir).expanduser().resolve()
    downstream = state.get("downstream") if isinstance(state.get("downstream"), Mapping) else {}
    lines = [
        f"# Component Analysis Report：Object {object_id}",
        "",
        f"- fixture_only：{_value(state.get('fixture_only'), fallback='unavailable')}",
        f"- historical_pre_fix：{_value(state.get('historical_pre_fix'), fallback='unavailable')}",
        f"- renderer：{RENDERER_VERSION}",
        f"- workflow status：{_value(state.get('status'))}",
        f"- Image：{_value(downstream.get('image'), fallback='FIT_AVAILABLE')}",
        f"- SED：{_value(downstream.get('sed'), fallback='NOT_RUN')}",
        f"- Image-SED：{_value(downstream.get('image_sed'), fallback='NOT_RUN')}",
        f"- best round status：{_value(state.get('best_round_status'), fallback='UNLOCKED')}",
        f"- needs_review：{_value(state.get('needs_review'))}",
        f"- Image 停止原因：{_value(state.get('termination_reason'), fallback='unavailable')}",
        "",
        "## 工程闭环判定",
        f"- Image 迭代执行链：{_value(state.get('image_iteration_status'), fallback=_value(state.get('status'), fallback='未采集'))}",
        f"- 安全停止门：{_value(state.get('safe_stop_status'), fallback='未采集')}",
        f"- 完整 workflow 闭环：{_value(state.get('full_workflow_closure'), fallback='NOT_PASSED')}",
        "",
        "## 逐轮总结",
        "- 见 `working_note.md` 与各 `round_*_component_analysis.md`。",
        f"- run_index：{_value(state.get('run_index_file'), fallback='未采集')}",
    ]
    if round_dirs:
        lines.extend([
            "",
            "### 结构化逐轮结论",
            *[_round_conclusion(Path(value).expanduser().resolve()) for value in round_dirs],
        ])
    lines.extend([
        "",
        "本报告说明工程状态与可追溯证据，不表示科学上已经确认全局最优模型。",
    ])
    summaries.mkdir(parents=True, exist_ok=True)
    target = summaries / f"analysis_report_obj{object_id}.md"
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target
