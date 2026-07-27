"""Offline replay report for v11 -> GRPO reward records."""

from __future__ import annotations

import argparse
import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple

from eval.reward_for_grpo import (
    DEFAULT_MARGIN_SCALE,
    DEFAULT_MARGIN_WEIGHT,
    OUTCOME_EVALUATOR_FAILURE,
    OUTCOME_SUCCESS,
    SUPPORTED_OUTCOMES,
    V11_THRESHOLD,
    calibrate_margin_scale,
    gate_grpo_group,
    shape_grpo_reward,
)


def _finite_float(value: Any) -> float | None:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def load_jsonl(path: str | Path) -> List[Dict[str, Any]]:
    records = []
    with Path(path).open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSON at line {line_number}: {exc}") from exc
            if not isinstance(row, dict):
                raise ValueError(f"line {line_number} must contain a JSON object")
            records.append(row)
    return records


def normalize_record(
    row: Mapping[str, Any],
    *,
    threshold: float,
    margin_scale: float,
    margin_weight: float,
) -> Dict[str, Any]:
    raw_reward = _finite_float(row.get("raw_reward", row.get("rule_reward")))
    outcome = row.get("outcome")
    if outcome is None:
        outcome = OUTCOME_SUCCESS if raw_reward is not None else OUTCOME_EVALUATOR_FAILURE
    if outcome not in SUPPORTED_OUTCOMES:
        raise ValueError(f"unsupported outcome {outcome!r}")

    shaped = shape_grpo_reward(
        {"reward": raw_reward} if raw_reward is not None else None,
        outcome=outcome,
        threshold=threshold,
        margin_scale=margin_scale,
        margin_weight=margin_weight,
        failure_reason=row.get("failure_reason"),
    )
    result = dict(row)
    result.update(shaped)
    result["group_id"] = str(
        row.get("group_id")
        or f"{row.get('parent_id', 'unknown')}::{row.get('step_id', 0)}"
    )
    return result


def _pairwise_counts(groups: Mapping[str, Sequence[Mapping[str, Any]]]) -> Dict[str, Any]:
    wins = ties = losses = pairs = mixed_groups = 0
    for rows in groups.values():
        positives = [
            row for row in rows
            if row.get("vlm_improvement") in (1, True)
            and row.get("raw_reward") is not None
        ]
        negatives = [
            row for row in rows
            if row.get("vlm_improvement") in (0, False)
            and row.get("raw_reward") is not None
        ]
        mixed_groups += int(bool(positives and negatives))
        for positive in positives:
            for negative in negatives:
                pairs += 1
                pos_score = float(positive["raw_reward"])
                neg_score = float(negative["raw_reward"])
                if pos_score > neg_score:
                    wins += 1
                elif pos_score == neg_score:
                    ties += 1
                else:
                    losses += 1

    return {
        "mixed_labeled_groups": mixed_groups,
        "pairs": pairs,
        "wins": wins,
        "ties": ties,
        "losses": losses,
        "strict_accuracy": wins / pairs if pairs else None,
        "half_tie_accuracy": (wins + 0.5 * ties) / pairs if pairs else None,
    }


def build_replay_report(
    records: Sequence[Mapping[str, Any]],
    *,
    threshold: float = V11_THRESHOLD,
    margin_scale: float = DEFAULT_MARGIN_SCALE,
    margin_weight: float = DEFAULT_MARGIN_WEIGHT,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    normalized = [
        normalize_record(
            row,
            threshold=threshold,
            margin_scale=margin_scale,
            margin_weight=margin_weight,
        )
        for row in records
    ]
    groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in normalized:
        groups[row["group_id"]].append(row)

    gate_counts = Counter()
    trainable_groups = 0
    output_rows: List[Dict[str, Any]] = []
    for group_id, rows in groups.items():
        gated = gate_grpo_group(rows)
        gate_counts[gated["gate_reason"]] += 1
        trainable_groups += int(gated["group_train_mask"])
        for row in gated["rollout_results"]:
            row["group_id"] = group_id
            row["group_gate_reason"] = gated["gate_reason"]
            output_rows.append(row)

    outcome_counts = Counter(row["outcome"] for row in output_rows)
    coarse_counts = Counter(
        str(row["coarse_reward"])
        for row in output_rows
        if row["coarse_reward"] is not None
    )
    labeled = [
        row for row in output_rows
        if row.get("vlm_improvement") in (0, 1, False, True)
        and row.get("coarse_reward") in (0.0, 1.0)
    ]
    label_correct = sum(
        int(int(row["coarse_reward"]) == int(row["vlm_improvement"]))
        for row in labeled
    )
    report = {
        "reward_version": "v11",
        "threshold": threshold,
        "margin_scale": margin_scale,
        "margin_weight": margin_weight,
        "num_candidates": len(output_rows),
        "num_groups": len(groups),
        "num_trainable_groups": trainable_groups,
        "trainable_group_rate": trainable_groups / len(groups) if groups else 0.0,
        "gate_reason_counts": dict(gate_counts),
        "outcome_counts": dict(outcome_counts),
        "coarse_counts": dict(coarse_counts),
        "labeled_binary_n": len(labeled),
        "labeled_binary_accuracy": label_correct / len(labeled) if labeled else None,
        "same_parent_pairwise": _pairwise_counts(groups),
    }
    return report, output_rows


def _write_jsonl(path: str | Path, rows: Iterable[Mapping[str, Any]]) -> None:
    with Path(path).open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--details")
    parser.add_argument("--threshold", type=float, default=V11_THRESHOLD)
    parser.add_argument("--margin-weight", type=float, default=DEFAULT_MARGIN_WEIGHT)
    parser.add_argument("--margin-scale", type=float, default=DEFAULT_MARGIN_SCALE)
    parser.add_argument(
        "--calibrate-scale",
        choices=("iqr", "mad"),
        help="use only when the input is a fixed validation split",
    )
    args = parser.parse_args()

    records = load_jsonl(args.input)
    margin_scale = args.margin_scale
    if args.calibrate_scale:
        raw_rewards = [
            value for row in records
            if (value := _finite_float(row.get("raw_reward", row.get("rule_reward"))))
            is not None
        ]
        margin_scale = calibrate_margin_scale(raw_rewards, method=args.calibrate_scale)

    report, details = build_replay_report(
        records,
        threshold=args.threshold,
        margin_scale=margin_scale,
        margin_weight=args.margin_weight,
    )
    with Path(args.report).open("w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
    if args.details:
        _write_jsonl(args.details, details)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
