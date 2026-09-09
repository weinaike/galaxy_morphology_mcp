"""Deterministic, read-only Markdown rendering for multi-band workflow rounds."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from jsonschema import ValidationError

from schemas import validate


RENDERER_VERSION = "workflow-summary-renderer@v1"


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


def _value(value: Any, *, fallback: str = "unavailable") -> str:
    if value is None or value == "":
        return fallback
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
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
        lines.append(f"- parameter_changes：{_value(changes)}")
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
        return _bullet_lines([
            f"{_value(item.get('name'))}：{_value(item.get('value'))}（{_value(item.get('status'))}）"
            for item in features[:12]
            if isinstance(item, Mapping)
        ])
    observations = evidence.get("observations")
    if not isinstance(observations, list):
        return ["- unavailable"]
    return _bullet_lines([
        f"{_value(item.get('target_id'))}：{_value(item.get('label'))}，confidence={_value(item.get('confidence'))}"
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
        f"- attempts：{_value(attempt_statuses, fallback='未采集')}",
        f"- response_bytes：{_value(provider.get('response_bytes'), fallback='unavailable')}",
        f"- finish_reason：{_value(provider.get('finish_reason'), fallback='unavailable')}",
        f"- token_usage：{_value(provider.get('token_usage'), fallback='unavailable')}",
        f"- timing：{_value(timing_text, fallback='未采集')}",
        f"- error：{_value(provider.get('error'), fallback='unavailable')}",
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
    return [
        f"- raw VLM decision view：{_value(_compact_evidence_view(views.get('vlm')), fallback='未采集')}",
        f"- numeric-only decision view：{_value(_compact_evidence_view(views.get('numeric_only')), fallback='未采集')}",
        f"- resolved decision view：{_value(resolved)}",
    ]


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
    candidate, _ = _read_validated(round_dir / "candidate_lifecycle.json", "workflow_lifecycle")
    if not manifest and not proposal and not lifecycle:
        return f"- `{round_dir.name}`：结构化 artifact unavailable"
    decision = lifecycle.get("resolved_decision") if lifecycle else None
    action = decision.get("action") if isinstance(decision, Mapping) else None
    action_type = action.get("action_type") if isinstance(action, Mapping) else None
    summary = (candidate or lifecycle or {}).get("action_summary", {})
    verdict = summary.get("refit_verdict") if isinstance(summary, Mapping) else None
    next_step = (lifecycle or {}).get("next_step")
    return (
        f"- `{(manifest or {}).get('round_id', round_dir.name)}`："
        f"action={_value(action_type, fallback='unavailable')}；"
        f"refit={_value(verdict, fallback='未采集')}；"
        f"next={_value(next_step, fallback='unavailable')}"
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
        f"- detect_bar_lopsidedness：{_value(detection, fallback='unavailable')}",
        f"- detected_features：{_value(_detected_features(detection), fallback='unavailable')}",
        f"- 高概率存在成分：{_value((proposal or {}).get('predicted_components'), fallback='unavailable')}",
        "",
        "## 当前模型与已确认成分",
        f"- 当前 profile：{_value(profile_value, fallback='unavailable')}",
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
        "### baseline",
        *_baseline_fit_lines(proposal),
        "### candidate",
        *_fit_assessment_lines(candidate_decision if isinstance(candidate_decision, Mapping) else decision if isinstance(decision, Mapping) else None),
        f"- refit verdict：{_value((action_summary or {}).get('refit_verdict'), fallback='未采集')}",
        "",
        "## 状态与下一步",
        f"- PolicyState：{_value((lifecycle or {}).get('policy_state'), fallback='unavailable')}",
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
