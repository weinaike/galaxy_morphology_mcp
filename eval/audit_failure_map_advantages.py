"""Counterfactual audit of failure-map effects on GRPO advantages.

The Galaxy training config uses GRPO with
``norm_adv_by_std_in_grpo=false``.  For each trainable group the scalar
advantage is therefore ``score - group_mean``.  This script replays old
``reward_call_*.jsonl`` files and measures how mapping artifact-validation
failures from mask to ``-1`` changes every candidate's advantage.
"""

from __future__ import annotations

import argparse
import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Mapping


CALL_RE = re.compile(r"reward_call_(\d+)\.jsonl$")


def _artifact_failure(row: Mapping[str, Any]) -> bool:
    if row.get("outcome") != "evaluator_failure":
        return False
    if row.get("failure_stage") != "galfit_artifact_validation":
        return False
    reason = str(row.get("failure_reason") or "").lower()
    infrastructure = (
        "disk quota",
        "no space left",
        "permission denied",
        "read-only file system",
        "missing shared librar",
        "module not found",
        "dependency",
    )
    return not any(marker in reason for marker in infrastructure)


def _sign(value: float, epsilon: float = 1e-12) -> int:
    if value > epsilon:
        return 1
    if value < -epsilon:
        return -1
    return 0


def _group_state(
    rows: list[dict[str, Any]], *, failure_map: bool
) -> tuple[bool, dict[int, float]]:
    valid: list[tuple[int, float, int]] = []
    for index, row in enumerate(rows):
        coarse = row.get("coarse_reward")
        if coarse is None:
            if failure_map and _artifact_failure(row):
                valid.append((index, -1.0, -1))
            continue
        valid.append((index, float(row.get("shaped_reward", coarse)), int(float(coarse))))

    levels = {coarse for _, _, coarse in valid}
    trainable = len(valid) >= 2 and len(levels) >= 2
    if not trainable:
        return False, {}
    mean = sum(score for _, score, _ in valid) / len(valid)
    return True, {index: score - mean for index, score, _ in valid}


def _load_calls(audit_dir: Path) -> list[tuple[int, list[dict[str, Any]]]]:
    calls: list[tuple[int, list[dict[str, Any]]]] = []
    for path in audit_dir.glob("reward_call_*.jsonl"):
        match = CALL_RE.search(path.name)
        if not match:
            continue
        with path.open(encoding="utf-8") as handle:
            rows = [json.loads(line) for line in handle if line.strip()]
        calls.append((int(match.group(1)), rows))
    if not calls:
        raise FileNotFoundError(f"no reward_call_*.jsonl under {audit_dir}")
    return sorted(calls)


def _summarize(calls: list[tuple[int, list[dict[str, Any]]]]) -> dict[str, Any]:
    groups: dict[tuple[int, str], list[dict[str, Any]]] = defaultdict(list)
    for call_index, rows in calls:
        for row in rows:
            groups[(call_index, str(row.get("group_id")))].append(row)

    counts: Counter[str] = Counter()
    abs_deltas: list[float] = []
    for rows in groups.values():
        if not any(_artifact_failure(row) for row in rows):
            continue
        counts["affected_groups"] += 1
        counts["reclassified_failures"] += sum(_artifact_failure(row) for row in rows)
        old_trainable, old_adv = _group_state(rows, failure_map=False)
        new_trainable, new_adv = _group_state(rows, failure_map=True)
        counts["old_trainable_groups"] += old_trainable
        counts["new_trainable_groups"] += new_trainable
        counts["newly_trainable_groups"] += (not old_trainable and new_trainable)
        counts["remain_trainable_groups"] += old_trainable and new_trainable

        if not (old_trainable and new_trainable):
            continue
        for index in old_adv.keys() & new_adv.keys():
            counts["comparable_existing_candidates"] += 1
            old_sign = _sign(old_adv[index])
            new_sign = _sign(new_adv[index])
            if old_sign != new_sign:
                counts[f"sign_flip_{old_sign}_to_{new_sign}"] += 1
            coarse = rows[index].get("coarse_reward")
            if coarse is not None and int(float(coarse)) == 0:
                counts["comparable_zero_candidates"] += 1
                if old_sign <= 0 and new_sign > 0:
                    counts["zero_promoted_positive"] += 1
            abs_deltas.append(abs(new_adv[index] - old_adv[index]))

    counts_dict = dict(counts)
    comparable_zero = counts.get("comparable_zero_candidates", 0)
    counts_dict["zero_promotion_rate"] = (
        counts.get("zero_promoted_positive", 0) / comparable_zero
        if comparable_zero
        else 0.0
    )
    counts_dict["mean_abs_advantage_delta"] = (
        sum(abs_deltas) / len(abs_deltas) if abs_deltas else 0.0
    )
    counts_dict["max_abs_advantage_delta"] = max(abs_deltas, default=0.0)
    return counts_dict


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit-dir", type=Path, required=True)
    parser.add_argument("--block-size", type=int, default=100)
    parser.add_argument("--output-json", type=Path)
    args = parser.parse_args()
    if args.block_size < 1:
        raise ValueError("--block-size must be >= 1")

    blocks: dict[str, list[tuple[int, list[dict[str, Any]]]]] = defaultdict(list)
    for call_index, rows in _load_calls(args.audit_dir):
        start = ((call_index - 1) // args.block_size) * args.block_size + 1
        end = start + args.block_size - 1
        blocks[f"{start}-{end}"].append((call_index, rows))

    report = {"audit_dir": str(args.audit_dir.resolve()), "blocks": {}}
    for label, calls in blocks.items():
        summary = _summarize(calls)
        report["blocks"][label] = summary
        print(f"\n========== {label} ==========")
        for key, value in summary.items():
            if isinstance(value, float):
                if not math.isfinite(value):
                    raise ValueError(f"non-finite statistic {key}={value}")
                print(f"{key}: {value:.6f}")
            else:
                print(f"{key}: {value}")

    if args.output_json:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"\noutput: {args.output_json}")


if __name__ == "__main__":
    main()
