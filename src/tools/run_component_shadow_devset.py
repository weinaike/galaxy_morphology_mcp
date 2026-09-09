"""Run component-analysis shadow over a round-level dev set."""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from component_analysis import OpenAICompatibleVLM, PolicyState, build_manifest, run_shadow_round
from component_analysis.shadow import _components_from_lyric


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--input-root", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--summary-file", required=True, type=Path)
    parser.add_argument("--review-table", type=Path)
    parser.add_argument("--coverage-report", type=Path)
    parser.add_argument("--numeric-only", action="store_true", help="skip the VLM callback and explicitly run the numeric degradation path")
    parser.add_argument("--vlm-timeout", type=float, default=360.0, help="per-request VLM timeout in seconds (default: 360)")
    parser.add_argument("--shard-index", type=int, default=0)
    parser.add_argument("--shard-count", type=int, default=1)
    return parser.parse_args()


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _round_paths(input_root: Path, entry: dict[str, Any]) -> dict[str, Path]:
    round_dir = (input_root / entry["round_dir"]).expanduser().resolve()
    return {
        "round_dir": round_dir,
        "lyric": (round_dir / entry["lyric_file"]).resolve(),
        "summary": (round_dir / entry["summary_file"]).resolve(),
        "comparison": (round_dir / entry["comparison_png"]).resolve(),
    }


def _run_one(
    entry: dict[str, Any],
    *,
    input_root: Path,
    output_dir: Path,
    vlm_callback: Any | None = None,
    isophote_cache: dict[str, Any] | None = None,
    policy_state: PolicyState | None = None,
) -> dict[str, Any]:
    paths = _round_paths(input_root, entry)
    current_components = sorted(_components_from_lyric(str(paths["lyric"])))
    expected_components = sorted(entry["source_components"])
    if current_components != expected_components:
        raise ValueError(
            f"normalized lyric components {current_components} do not match "
            f"dataset source_components {expected_components} for {entry['round_id']}"
        )
    manifest = build_manifest(
        round_dir=paths["round_dir"],
        lyric_file=paths["lyric"],
        summary_file=paths["summary"],
        comparison_png=paths["comparison"],
        round_id=entry["round_id"],
    )
    artifact_dir = output_dir / f"{entry['object_id']}_{entry['round_id']}"
    result = run_shadow_round(
        manifest,
        output_dir=artifact_dir,
        vlm_callback=vlm_callback,
        current_components=current_components,
        isophote_cache=isophote_cache,
        policy_state=policy_state,
    )
    decision = result["decision_artifact"]
    action = decision.get("action")
    raw_decision = decision.get("raw_decision", {})
    raw_action = raw_decision.get("action") if isinstance(raw_decision, dict) else None
    automation = decision.get("automation", {})
    quality_statuses = Counter(
        "passed" if band.get("passed") else "failed"
        for band in result["numeric_evidence"].get("band_quality", [])
    )
    return {
        "object_id": entry["object_id"],
        "round_id": entry["round_id"],
        "round_dir": str(paths["round_dir"]),
        "source_components": entry["source_components"],
        "normalized_current_components": current_components,
        "expert_final_components": entry["expert_final_components"],
        "relation_to_expert_final": entry["relation_to_expert_final"],
        "missing_components": entry["missing_components"],
        "extra_components": entry["extra_components"],
        "band_count": len(manifest["bands"]),
        "gssummary": entry["gssummary"],
        "numeric_feature_count": len(result["numeric_evidence"].get("features", [])),
        "band_quality": dict(quality_statuses),
        "shadow_mode": result.get("shadow_mode", "proposal_only"),
        "decision_state": decision["state"],
        "action": action,
        "action_type": action.get("action_type") if isinstance(action, dict) else None,
        "raw_action_type": raw_action.get("action_type") if isinstance(raw_action, dict) else None,
        "raw_decision": raw_decision,
        "candidate_actions": decision.get("candidate_actions", []),
        "workflow_status": decision.get("workflow_status"),
        "termination_checks": decision.get("termination_checks", []),
        "vlm_parse_status": result["vlm_evidence"]["parse_status"],
        "vlm_model_id": result["vlm_evidence"].get("model_id"),
        "vlm_error": result["vlm_error"],
        "vlm_attempts": result.get("vlm_attempts", []),
        "artifact_refs": result.get("artifact_refs", {}),
        "rule_trace": [
            {
                "rule_id": item["rule_id"],
                "outcome": item["outcome"],
                "detail": item.get("detail"),
            }
            for item in decision["rule_trace"]
        ],
        "automation": automation,
        "policy_state": result.get("policy_state", {}),
        "artifact_dir": str(artifact_dir.resolve()),
    }



def _write_review_table(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "object_id", "round_id", "source_components", "expert_final_components",
        "relation_to_expert_final", "action_type", "raw_action_type",
        "workflow_status", "needs_review", "action_component", "artifact_dir",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, delimiter="\t", lineterminator="\n")
        writer.writeheader()
        for row in rows:
            action = row.get("action") or {}
            writer.writerow({
                "object_id": row["object_id"],
                "round_id": row["round_id"],
                "source_components": ",".join(row["source_components"]),
                "expert_final_components": ",".join(row["expert_final_components"]),
                "relation_to_expert_final": row["relation_to_expert_final"],
                "action_type": row.get("action_type") or "NONE",
                "raw_action_type": row.get("raw_action_type") or "NONE",
                "workflow_status": row.get("workflow_status") or "UNSET",
                "needs_review": row.get("automation", {}).get("needs_review", False),
                "action_component": action.get("component") or action.get("target_model_label") or action.get("replace_to") or "",
                "artifact_dir": row["artifact_dir"],
            })


def _coverage_details(summary: dict[str, Any]) -> dict[str, Counter[str]]:
    rows = summary["samples"]
    action_target_counts: Counter[str] = Counter()
    inconclusive_reason_counts: Counter[str] = Counter()
    inconclusive_resolution_counts: Counter[str] = Counter()
    inconclusive_resolved_action_counts: Counter[str] = Counter()
    keep_continuation_counts: Counter[str] = Counter()
    converged_check_counts: Counter[str] = Counter()
    object_round_counts: Counter[str] = Counter()

    for row in rows:
        action_type = row.get("action_type") or "NONE"
        action = row.get("action") or {}
        target_parts = []
        if action.get("component"):
            target_parts.append(str(action["component"]))
        if action.get("target_model_label"):
            target_parts.append(str(action["target_model_label"]))
        if action.get("replace_from") or action.get("replace_to"):
            target_parts.append(
                f"{action.get('replace_from', '?')}->{action.get('replace_to', '?')}"
            )
        parameter_changes = action.get("parameter_changes") or []
        target_parts.extend(
            f"{change.get('target_model_label', '?')}:{change.get('parameter', '?')}"
            for change in parameter_changes
        )
        action_target_counts[f"{action_type} | {'; '.join(target_parts) or 'none'}"] += 1

        if row.get("raw_action_type") == "INCONCLUSIVE":
            for trace in row.get("rule_trace", []):
                if trace.get("outcome") == "INCONCLUSIVE":
                    inconclusive_reason_counts[trace.get("rule_id", "UNKNOWN")] += 1
            automation = row.get("automation") or {}
            resolution = automation.get("resolution") or "UNRESOLVED"
            inconclusive_resolution_counts[resolution] += 1
            inconclusive_resolved_action_counts[
                f"{resolution} -> {action_type}"
            ] += 1

        if action_type == "KEEP_AND_CONTINUE":
            keep_continuation_counts[
                f"{action.get('continuation_reason', 'UNSET')} -> "
                f"{action.get('next_step', 'UNSET')}"
            ] += 1

        if action_type == "CONVERGED":
            for check in row.get("termination_checks", []):
                converged_check_counts[
                    f"{check.get('check_id', 'UNKNOWN')}={check.get('status', 'UNKNOWN')}"
                ] += 1

        object_round_counts[str(row["object_id"])] += 1

    state_rows = sum("policy_state" in row for row in rows)
    state_last_round_matches = sum(
        bool(row.get("policy_state", {}).get("last_round_id") == row.get("round_id"))
        for row in rows
    )
    return {
        "action_target_counts": action_target_counts,
        "inconclusive_reason_counts": inconclusive_reason_counts,
        "inconclusive_resolution_counts": inconclusive_resolution_counts,
        "inconclusive_resolved_action_counts": inconclusive_resolved_action_counts,
        "keep_continuation_counts": keep_continuation_counts,
        "converged_check_counts": converged_check_counts,
        "object_round_counts": object_round_counts,
        "state_rows": Counter({"rows": state_rows}),
        "state_last_round_matches": Counter({"rows": state_last_round_matches}),
    }


def _write_coverage_report(path: Path, summary: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    details = _coverage_details(summary)
    lines = [
        f"# JWST0716 {summary.get('runner', 'component-shadow-devset@v2').rsplit('@', 1)[-1]} Action Coverage",
        "",
        "This report is generated from the proposal-only shadow summary. It does not execute or lock a formal workflow.",
        f"- Completion status: {summary.get('completion_status', 'complete')}",
        f"- Validation note: {summary.get('validation_note', '')}",
        "",
        f"- Successful rounds: {summary['successful_entries']}/{summary['total_entries']}",
        f"- Failed rounds: {summary['failed_entries']}",
        f"- Numeric-only: {summary['numeric_only']}",
        "",
        "## Resolved Actions",
        "",
    ]
    for action, count in sorted(summary["action_counts"].items()):
        lines.append(f"- `{action}`: {count}")
    lines.extend(["", "## Raw Actions", ""])
    for action, count in sorted(summary["raw_action_counts"].items()):
        lines.append(f"- `{action}`: {count}")
    lines.extend(["", "## Candidate Action Reachability", ""])
    for action, count in sorted(summary["candidate_action_counts"].items()):
        lines.append(f"- `{action}`: {count}")
    lines.extend(["", "## Action Component / Target Breakdown", ""])
    for target, count in sorted(details["action_target_counts"].items()):
        lines.append(f"- `{target}`: {count}")
    lines.extend(["", "## INCONCLUSIVE Reasons and Resolution", ""])
    lines.append("### Rule Reasons")
    lines.append("")
    for reason, count in sorted(details["inconclusive_reason_counts"].items()):
        lines.append(f"- `{reason}`: {count}")
    lines.extend(["", "### Policy Resolution", ""])
    for resolution, count in sorted(details["inconclusive_resolution_counts"].items()):
        lines.append(f"- `{resolution}`: {count}")
    lines.extend(["", "### Resolved Action", ""])
    for resolution, count in sorted(details["inconclusive_resolved_action_counts"].items()):
        lines.append(f"- `{resolution}`: {count}")
    lines.extend(["", "## KEEP_AND_CONTINUE Follow-up", ""])
    for continuation, count in sorted(details["keep_continuation_counts"].items()):
        lines.append(f"- `{continuation}`: {count}")
    lines.extend(["", "## CONVERGED Termination Gates", ""])
    for check, count in sorted(details["converged_check_counts"].items()):
        lines.append(f"- `{check}`: {count}")
    lines.extend(["", "## Workflow Status", ""])
    for status, count in sorted(summary["workflow_status_counts"].items()):
        lines.append(f"- `{status}`: {count}")
    object_round_counts = details["object_round_counts"]
    multi_round_objects = sum(count > 1 for count in object_round_counts.values())
    lines.extend([
        "",
        "## Object PolicyState Continuity",
        "",
        f"- Objects: {len(object_round_counts)}",
        f"- Objects with multiple rounds: {multi_round_objects}",
        f"- Rows with PolicyState: {details['state_rows']['rows']}/{len(summary['samples'])}",
        f"- Rows whose state last_round_id matches the row: {details['state_last_round_matches']['rows']}/{len(summary['samples'])}",
        f"- Maximum rounds per object: {max(object_round_counts.values(), default=0)}",
        "",
        "## Legacy residual_analysis Direction Comparison",
        "",
        f"- V2 rows with a machine-readable legacy atomic-action direction: 0/{len(summary['samples'])}; the v2 manifest does not carry this field.",
        "- Existing source comparison remains in `docs/component-analysis/shadow-dev-action-direction-comparison-jwst0716.md`; it is kept separate and is not treated as v2 action truth.",
    ])
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")

def main() -> int:
    args = _parse_args()
    dataset_path = args.dataset.expanduser().resolve()
    input_root = args.input_root.expanduser().resolve()
    output_dir = args.output_dir.expanduser().resolve()
    dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
    samples = dataset.get("samples")
    if not isinstance(samples, list) or not samples:
        raise ValueError("dataset.samples must be a non-empty list")
    if not (0 <= args.shard_index < args.shard_count):
        raise ValueError("shard-index must be within shard-count")
    object_ids = sorted({str(entry["object_id"]) for entry in samples})
    shard_objects = {
        object_id
        for index, object_id in enumerate(object_ids)
        if index % args.shard_count == args.shard_index
    }
    samples = sorted(
        (entry for entry in samples if str(entry["object_id"]) in shard_objects),
        key=lambda entry: (str(entry["object_id"]), str(entry["round_id"])),
    )

    vlm_callback = None
    if not args.numeric_only:
        vlm_callback = OpenAICompatibleVLM(timeout=args.vlm_timeout)

    output_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    isophote_cache: dict[str, Any] = {}
    policy_states = {
        object_id: PolicyState(object_id=object_id)
        for object_id in shard_objects
    }
    for entry in samples:
        try:
            rows.append(
                _run_one(
                    entry,
                    input_root=input_root,
                    output_dir=output_dir,
                    vlm_callback=vlm_callback,
                    isophote_cache=isophote_cache,
                    policy_state=policy_states[str(entry["object_id"])],
                )
            )
        except Exception as exc:  # keep the summary useful for batch diagnostics
            failures.append(
                {
                    "object_id": str(entry.get("object_id")),
                    "round_id": str(entry.get("round_id")),
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )

    summary = {
        "schema_version": "3.0",
        "runner": "component-shadow-devset@v3",
        "shadow_mode": "proposal_only",
        "dataset": str(dataset_path),
        "input_root": str(input_root),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "numeric_only": args.numeric_only,
        "vlm_callback": None if vlm_callback is None else {"provider": "OpenAICompatibleVLM", "model_id": vlm_callback.model_id},
        "shard_index": args.shard_index,
        "shard_count": args.shard_count,
        "shard_object_ids": sorted(shard_objects),
        "total_entries": len(samples),
        "successful_entries": len(rows),
        "failed_entries": len(failures),
        "action_counts": dict(Counter(row["action_type"] or "NONE" for row in rows)),
        "raw_action_counts": dict(Counter(row["raw_action_type"] or "NONE" for row in rows)),
        "workflow_status_counts": dict(Counter(row["workflow_status"] or "UNSET" for row in rows)),
        "candidate_action_counts": dict(Counter(
            candidate.get("action", {}).get("action_type", "NONE")
            for row in rows
            for candidate in row.get("candidate_actions", [])
        )),
        "inconclusive_rule_counts": dict(Counter(
            trace["rule_id"]
            for row in rows
            for trace in row.get("rule_trace", [])
            if trace.get("outcome") == "INCONCLUSIVE"
        )),
        "relation_counts": dict(Counter(row["relation_to_expert_final"] for row in rows)),
        "vlm_status_counts": dict(Counter(row["vlm_parse_status"] for row in rows)),
        "vlm_attempt_count": sum(len(row.get("vlm_attempts", [])) for row in rows),
        "vlm_retry_count": sum(
            max(len(row.get("vlm_attempts", [])) - 1, 0) for row in rows
        ),
        "vlm_error_count": sum(bool(row["vlm_error"]) for row in rows),
        "full_vlm_successful_entries": sum(row["vlm_parse_status"] == "OK" for row in rows),
        "needs_review_count": sum(
            bool(row["automation"].get("needs_review")) for row in rows
        ),
        "failures": failures,
        "samples": rows,
    }
    summary_path = args.summary_file.expanduser().resolve()
    _write_json(summary_path, summary)
    if args.review_table:
        _write_review_table(args.review_table.expanduser().resolve(), rows)
    if args.coverage_report:
        _write_coverage_report(args.coverage_report.expanduser().resolve(), summary)
    print(
        f"shadowed {len(rows)}/{len(samples)} rounds; "
        f"needs_review={summary['needs_review_count']}; "
        f"failures={len(failures)}"
    )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
