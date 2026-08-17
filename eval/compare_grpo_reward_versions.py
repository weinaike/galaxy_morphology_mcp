"""Compare V11, V12.4 and V12.5 on exactly the same on-policy groups.

The command is label-only/reward-only: it never samples the model, reruns
GALFIT, or calls the VLM.  It recomputes each reward version from saved parent
metrics, candidate specs, fitted components and fixed VLM labels, then applies
the production GRPO shaping and GroupGate implementation.
"""

from __future__ import annotations

import argparse
import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from eval.evaluate_action import parse_json_spec
from eval.label_grpo_rollouts import _binary_metrics
from eval.reward_for_grpo import (
    DEFAULT_MARGIN_SCALE,
    DEFAULT_MARGIN_WEIGHT,
    OUTCOME_EVALUATOR_FAILURE,
    OUTCOME_SUCCESS,
    V11_THRESHOLD,
    gate_grpo_group,
    shape_grpo_reward,
)
from eval.reward_for_rl import compute_rl_reward as compute_v11_reward
from eval.reward_for_rl_v12_4 import compute_rl_reward_v12_4
from eval.validate_reward_alignment import _parse_fitted_components


def _read_jsonl(path: str | Path) -> list[dict[str, Any]]:
    rows = []
    with Path(path).open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"{path}:{line_number} must be a JSON object")
            rows.append(value)
    return rows


def _write_jsonl(path: str | Path, rows: Iterable[Mapping[str, Any]]) -> None:
    with Path(path).open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(dict(row), ensure_ascii=False) + "\n")


def _key(row: Mapping[str, Any]) -> tuple[str, int]:
    return str(row.get("group_id")), int(row.get("candidate_index") or 0)


def _finite(value: Any) -> float | None:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _label(row: Mapping[str, Any]) -> int | None:
    value = row.get("vlm_improvement")
    if value is None:
        value = (row.get("vlm_detail") or {}).get("improvement")
    return int(bool(value)) if value in (0, 1, False, True) else None


def _spec(prediction: Mapping[str, Any]) -> dict[str, Any] | None:
    value = prediction.get("pred_spec")
    if isinstance(value, dict):
        return value
    parsed = parse_json_spec(str(prediction.get("prediction") or ""))
    return parsed if isinstance(parsed, dict) else None


def _fitted(row: Mapping[str, Any]) -> list[dict[str, Any]]:
    value = row.get("fitted_components")
    if isinstance(value, list):
        return [dict(item) for item in value if isinstance(item, Mapping)]
    path = row.get("model_summary_path") or row.get("summary_path")
    return _parse_fitted_components(path) if path else []


def _pairwise_metrics(
    rows: Sequence[Mapping[str, Any]],
    *,
    score_key: str,
) -> dict[str, Any]:
    groups: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[str(row.get("group_id"))].append(row)

    wins = ties = losses = pairs = 0
    strict_by_group = []
    half_tie_by_group = []
    top1 = []
    mixed_groups = 0

    for group in groups.values():
        positives = [
            row for row in group
            if _label(row) == 1 and _finite(row.get(score_key)) is not None
        ]
        negatives = [
            row for row in group
            if _label(row) == 0 and _finite(row.get(score_key)) is not None
        ]
        if not positives or not negatives:
            continue
        mixed_groups += 1
        local_wins = local_ties = local_losses = 0
        for positive in positives:
            for negative in negatives:
                left = float(positive[score_key])
                right = float(negative[score_key])
                if left > right:
                    wins += 1
                    local_wins += 1
                elif left == right:
                    ties += 1
                    local_ties += 1
                else:
                    losses += 1
                    local_losses += 1
        local_pairs = local_wins + local_ties + local_losses
        pairs += local_pairs
        strict_by_group.append(local_wins / local_pairs)
        half_tie_by_group.append((local_wins + 0.5 * local_ties) / local_pairs)

        labeled = [
            row for row in group
            if _label(row) is not None and _finite(row.get(score_key)) is not None
        ]
        maximum = max(float(row[score_key]) for row in labeled)
        tied = [row for row in labeled if float(row[score_key]) == maximum]
        top1.append(sum(_label(row) for row in tied) / len(tied))

    return {
        "groups": len(groups),
        "mixed_vlm_groups": mixed_groups,
        "pairs": pairs,
        "wins": wins,
        "ties": ties,
        "losses": losses,
        "strict_accuracy": wins / pairs if pairs else None,
        "half_tie_accuracy": (wins + 0.5 * ties) / pairs if pairs else None,
        "macro_strict_accuracy": (
            sum(strict_by_group) / len(strict_by_group) if strict_by_group else None
        ),
        "macro_half_tie_accuracy": (
            sum(half_tie_by_group) / len(half_tie_by_group)
            if half_tie_by_group else None
        ),
        "top1_positive_rate": sum(top1) / len(top1) if top1 else None,
    }


def _recompute_rows(
    *,
    version: str,
    parents: Mapping[str, Mapping[str, Any]],
    predictions: Mapping[tuple[str, int], Mapping[str, Any]],
    rollouts: Sequence[Mapping[str, Any]],
    residual_scores: Mapping[tuple[str, int], float],
    residual_gate_threshold: float,
    threshold: float,
    margin_scale: float,
    margin_weight: float,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    if version not in {"v11", "v12.4", "v12.5"}:
        raise ValueError(f"unsupported version: {version}")

    output = []
    failures = Counter()
    for source in rollouts:
        row = dict(source)
        key = _key(row)
        parent = parents.get(key[0])
        prediction = predictions.get(key)
        row["vlm_improvement"] = _label(row)
        row["evaluated_reward_version"] = version

        outcome = str(row.get("outcome") or OUTCOME_EVALUATOR_FAILURE)
        raw_result = None
        if outcome == OUTCOME_SUCCESS:
            try:
                if parent is None:
                    raise KeyError(f"missing parent {key[0]}")
                if prediction is None:
                    raise KeyError(f"missing prediction {key}")
                action_spec = _spec(prediction)
                if action_spec is None:
                    raise ValueError("missing candidate action spec")
                old_metrics = dict(parent.get("parent_metrics") or parent.get("metrics") or {})
                new_metrics = dict(row.get("model_metrics") or {})
                if not old_metrics or not new_metrics:
                    raise ValueError("missing parent or child metrics")
                fitted_components = _fitted(row)
                if version == "v11":
                    raw_result = compute_v11_reward(
                        old_metrics=old_metrics,
                        new_metrics=new_metrics,
                        action_spec=action_spec,
                        fitted_components=fitted_components,
                    )
                else:
                    raw_result = compute_rl_reward_v12_4(
                        old_metrics=old_metrics,
                        new_metrics=new_metrics,
                        action_spec=action_spec,
                        fitted_components=fitted_components,
                    )
                    if version == "v12.5":
                        residual_score = residual_scores.get(key)
                        degeneracy = any(
                            str(value).startswith("disk_like_component_degeneracy:")
                            for value in raw_result.get("structure_warnings", [])
                        )
                        row["residual_score"] = residual_score
                        row["v12_5_degeneracy_gate_threshold"] = residual_gate_threshold
                        row["v12_5_residual_gate_applied"] = bool(
                            degeneracy
                            and residual_score is not None
                            and residual_score < residual_gate_threshold
                        )
                        if row["v12_5_residual_gate_applied"]:
                            raw_result = dict(raw_result)
                            raw_result["reward"] = min(
                                float(raw_result.get("reward", 0.0)), 0.0
                            )
                row["recomputed_raw_reward"] = float(raw_result["reward"])
                row["recomputed_structure_vetoed"] = bool(
                    raw_result.get("structure_vetoed", False)
                )
                row["recomputed_structure_violations"] = list(
                    raw_result.get("structure_violations", [])
                )
            except Exception as exc:
                failures[f"{type(exc).__name__}: {exc}"] += 1
                outcome = OUTCOME_EVALUATOR_FAILURE
                row["recompute_error"] = f"{type(exc).__name__}: {exc}"

        shaped = shape_grpo_reward(
            raw_result,
            outcome=outcome,
            threshold=threshold,
            margin_scale=margin_scale,
            margin_weight=margin_weight,
            failure_reason=row.get("failure_reason") or row.get("recompute_error"),
        )
        row.update(shaped)
        row["reward_version"] = version
        output.append(row)

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in output:
        grouped[str(row["group_id"])].append(row)

    gated_rows = []
    for group_id, group in grouped.items():
        gated = gate_grpo_group(group)
        for row in gated["rollout_results"]:
            row["group_id"] = group_id
            row["group_gate_reason"] = gated["gate_reason"]
            gated_rows.append(row)
    return gated_rows, dict(failures)


def _version_report(rows: Sequence[Mapping[str, Any]], failures: Mapping[str, int]):
    groups: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[str(row["group_id"])].append(row)
    trainable_ids = {
        group_id for group_id, group in groups.items()
        if any(bool(row.get("group_train_mask")) for row in group)
    }
    trainable_rows = [
        row for row in rows if str(row["group_id"]) in trainable_ids
    ]
    labeled_rows = [row for row in rows if _label(row) is not None]
    return {
        "num_candidates": len(rows),
        "num_groups": len(groups),
        "recompute_failures": dict(failures),
        "outcomes": dict(Counter(str(row.get("outcome")) for row in rows)),
        "coarse_counts": dict(
            Counter(str(row.get("coarse_reward")) for row in rows)
        ),
        "candidate_binary_alignment": _binary_metrics(rows),
        "num_trainable_groups": len(trainable_ids),
        "trainable_group_rate": len(trainable_ids) / len(groups) if groups else 0.0,
        "gate_reason_counts": dict(Counter(
            str(group[0].get("group_gate_reason")) for group in groups.values()
        )),
        "all_groups_pairwise_raw": _pairwise_metrics(
            labeled_rows, score_key="raw_reward"
        ),
        "all_groups_pairwise_shaped": _pairwise_metrics(
            labeled_rows, score_key="shaped_reward"
        ),
        "groupgate_pairwise_shaped": _pairwise_metrics(
            trainable_rows, score_key="shaped_reward"
        ),
    }


def _residual_only_report(
    rollouts: Sequence[Mapping[str, Any]],
    residual_scores: Mapping[tuple[str, int], float],
) -> dict[str, Any]:
    rows = []
    for source in rollouts:
        score = residual_scores.get(_key(source))
        if score is None:
            continue
        row = dict(source)
        row["vlm_improvement"] = _label(row)
        row["residual_score"] = score
        rows.append(row)
    return {
        "scored_candidates": len(rows),
        "coverage": len(rows) / len(rollouts) if rollouts else 0.0,
        "pairwise": _pairwise_metrics(rows, score_key="residual_score"),
    }


def build_unified_report(
    parents: Sequence[Mapping[str, Any]],
    predictions: Sequence[Mapping[str, Any]],
    rollouts: Sequence[Mapping[str, Any]],
    *,
    residual_rows: Sequence[Mapping[str, Any]] = (),
    residual_gate_threshold: float = 0.0,
    threshold: float = V11_THRESHOLD,
    margin_scale: float = DEFAULT_MARGIN_SCALE,
    margin_weight: float = DEFAULT_MARGIN_WEIGHT,
) -> tuple[dict[str, Any], dict[str, list[dict[str, Any]]]]:
    parent_map = {str(row["group_id"]): row for row in parents}
    prediction_map = {_key(row): row for row in predictions}
    residual_scores = {
        _key(row): float(row["residual_score"])
        for row in residual_rows
        if _finite(row.get("residual_score")) is not None
    }

    reports = {}
    details = {}
    for version in ("v11", "v12.4", "v12.5"):
        rows, failures = _recompute_rows(
            version=version,
            parents=parent_map,
            predictions=prediction_map,
            rollouts=rollouts,
            residual_scores=residual_scores,
            residual_gate_threshold=residual_gate_threshold,
            threshold=threshold,
            margin_scale=margin_scale,
            margin_weight=margin_weight,
        )
        details[version] = rows
        reports[version] = _version_report(rows, failures)

    report = {
        "protocol": {
            "same_parents_predictions_rollouts": True,
            "fixed_vlm_labels": True,
            "reruns_galfit": False,
            "calls_vlm": False,
            "threshold": threshold,
            "margin_scale": margin_scale,
            "margin_weight": margin_weight,
            "residual_gate_threshold": residual_gate_threshold,
        },
        "input_counts": {
            "parents": len(parents),
            "predictions": len(predictions),
            "rollouts": len(rollouts),
            "residual_scores": len(residual_scores),
        },
        "versions": reports,
        "v12.5_residual_only_diagnostic": _residual_only_report(
            rollouts, residual_scores
        ),
    }
    return report, details


def _pct(value: Any) -> str:
    return "N/A" if value is None else f"{float(value):.1%}"


def _print_report(report: Mapping[str, Any]) -> None:
    print("========== INPUT ==========")
    for key, value in report["input_counts"].items():
        print(f"{key}: {value}")
    for version, result in report["versions"].items():
        binary = result["candidate_binary_alignment"]
        all_pair = result["all_groups_pairwise_shaped"]
        gated = result["groupgate_pairwise_shaped"]
        print(f"\n========== {version.upper()} ==========")
        print(
            "binary: "
            f"n={binary['n']} acc={_pct(binary['accuracy'])} "
            f"P={_pct(binary['precision'])} R={_pct(binary['recall'])} "
            f"F1={_pct(binary['f1'])}"
        )
        print(
            "GroupGate: "
            f"{result['num_trainable_groups']}/{result['num_groups']} "
            f"({_pct(result['trainable_group_rate'])})"
        )
        print(
            "all-group shaped pairwise: "
            f"strict={_pct(all_pair['strict_accuracy'])} "
            f"half-tie={_pct(all_pair['half_tie_accuracy'])} "
            f"macro-half={_pct(all_pair['macro_half_tie_accuracy'])} "
            f"top1={_pct(all_pair['top1_positive_rate'])}"
        )
        print(
            "GroupGate shaped pairwise: "
            f"strict={_pct(gated['strict_accuracy'])} "
            f"half-tie={_pct(gated['half_tie_accuracy'])} "
            f"macro-half={_pct(gated['macro_half_tie_accuracy'])} "
            f"top1={_pct(gated['top1_positive_rate'])}"
        )
        if result["recompute_failures"]:
            print("recompute failures:", result["recompute_failures"])

    residual = report["v12.5_residual_only_diagnostic"]
    pair = residual["pairwise"]
    print("\n========== V12.5 RESIDUAL-ONLY DIAGNOSTIC ==========")
    print(
        f"coverage={_pct(residual['coverage'])} "
        f"strict={_pct(pair['strict_accuracy'])} "
        f"half-tie={_pct(pair['half_tie_accuracy'])} "
        f"macro-half={_pct(pair['macro_half_tie_accuracy'])} "
        f"top1={_pct(pair['top1_positive_rate'])}"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--parents", required=True)
    parser.add_argument("--predictions", required=True)
    parser.add_argument("--rollouts-vlm", required=True)
    parser.add_argument("--residual-scores")
    parser.add_argument("--residual-model-config")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--threshold", type=float, default=V11_THRESHOLD)
    parser.add_argument("--margin-scale", type=float, default=DEFAULT_MARGIN_SCALE)
    parser.add_argument("--margin-weight", type=float, default=DEFAULT_MARGIN_WEIGHT)
    args = parser.parse_args()

    residual_rows = (
        _read_jsonl(args.residual_scores) if args.residual_scores else []
    )
    residual_gate_threshold = 0.0
    if args.residual_model_config:
        config = json.loads(
            Path(args.residual_model_config).read_text(encoding="utf-8")
        )
        residual_gate_threshold = float(
            config.get("degeneracy_gate_threshold", 0.0)
        )

    report, details = build_unified_report(
        _read_jsonl(args.parents),
        _read_jsonl(args.predictions),
        _read_jsonl(args.rollouts_vlm),
        residual_rows=residual_rows,
        residual_gate_threshold=residual_gate_threshold,
        threshold=args.threshold,
        margin_scale=args.margin_scale,
        margin_weight=args.margin_weight,
    )

    output = Path(args.out_dir)
    output.mkdir(parents=True, exist_ok=False)
    (output / "unified_reward_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    for version, rows in details.items():
        _write_jsonl(output / f"{version.replace('.', '_')}_details.jsonl", rows)
    _print_report(report)
    print("\noutput:", output)


if __name__ == "__main__":
    main()
