"""Compare pre/post-SFT rollouts on the same frozen Galaxy parent groups."""

from __future__ import annotations

import argparse
import json
import math
import random
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean
from typing import Any, Mapping, Sequence

from eval.run_grpo_rollout import load_jsonl


def candidate_reward(row: Mapping[str, Any], failure_reward: float) -> float | None:
    outcome = row.get("outcome")
    if outcome == "evaluator_failure":
        return None
    if outcome == "success":
        try:
            value = float(row["raw_reward"])
        except (KeyError, TypeError, ValueError):
            return None
        return value if math.isfinite(value) else None
    return failure_reward


def group_metrics(
    rows: Sequence[Mapping[str, Any]], threshold: float, failure_reward: float
) -> dict[str, dict[str, float]]:
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get("group_id"))].append(row)
    result = {}
    for group_id, values in grouped.items():
        accountable_rows = [row for row in values if row.get("outcome") != "evaluator_failure"]
        accountable = [candidate_reward(row, failure_reward) for row in accountable_rows]
        accountable = [reward for reward in accountable if reward is not None]
        successes = [
            float(row["raw_reward"])
            for row in values
            if row.get("outcome") == "success" and row.get("raw_reward") is not None
        ]
        if not accountable:
            continue
        result[group_id] = {
            "candidate_mean": mean(accountable),
            "best_of_k": max(accountable),
            "positive_rate": mean(float(value > threshold) for value in accountable),
            "success_rate": mean(
                float(row.get("outcome") == "success") for row in accountable_rows
            ),
            "success_raw_mean": mean(successes) if successes else float("nan"),
            "accountable": float(len(accountable)),
        }
    return result


def bootstrap_ci(values: Sequence[float], seed: int, samples: int) -> list[float] | None:
    if not values:
        return None
    rng = random.Random(seed)
    estimates = []
    for _ in range(samples):
        estimates.append(mean(rng.choice(values) for _ in values))
    estimates.sort()
    lo = estimates[int(0.025 * (samples - 1))]
    hi = estimates[int(0.975 * (samples - 1))]
    return [lo, hi]


def compare(
    before_rows: Sequence[Mapping[str, Any]],
    after_rows: Sequence[Mapping[str, Any]],
    *,
    threshold: float,
    failure_reward: float,
    split_by_group: Mapping[str, str] | None = None,
    seed: int = 42,
    bootstrap_samples: int = 5000,
) -> dict[str, Any]:
    before = group_metrics(before_rows, threshold, failure_reward)
    after = group_metrics(after_rows, threshold, failure_reward)
    common = sorted(set(before) & set(after))
    metrics = (
        "candidate_mean",
        "best_of_k",
        "positive_rate",
        "success_rate",
        "success_raw_mean",
    )

    def one_subset(name: str, group_ids: list[str]) -> dict[str, Any]:
        report: dict[str, Any] = {"groups": len(group_ids)}
        for metric in metrics:
            usable = [
                group_id
                for group_id in group_ids
                if math.isfinite(before[group_id][metric])
                and math.isfinite(after[group_id][metric])
            ]
            deltas = [after[group_id][metric] - before[group_id][metric] for group_id in usable]
            report[metric] = {
                "groups": len(usable),
                "before": mean(before[group_id][metric] for group_id in usable) if usable else None,
                "after": mean(after[group_id][metric] for group_id in usable) if usable else None,
                "delta": mean(deltas) if deltas else None,
                "delta_95ci": bootstrap_ci(deltas, seed, bootstrap_samples),
                "groups_improved": sum(delta > 0 for delta in deltas),
                "groups_tied": sum(delta == 0 for delta in deltas),
            }
        return report

    subsets = {"all": common}
    if split_by_group:
        for split in sorted(set(split_by_group.values())):
            subsets[split] = [gid for gid in common if split_by_group.get(gid) == split]
    return {
        "threshold": threshold,
        "failure_reward": failure_reward,
        "before_candidates": len(before_rows),
        "after_candidates": len(after_rows),
        "before_groups": len(before),
        "after_groups": len(after),
        "common_groups": len(common),
        "missing_after": sorted(set(before) - set(after)),
        "new_after": sorted(set(after) - set(before)),
        "subsets": {name: one_subset(name, ids) for name, ids in subsets.items()},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before", required=True)
    parser.add_argument("--after", required=True)
    parser.add_argument("--selections", help="reward_best_selections.jsonl, for train/val subsets")
    parser.add_argument("--output", required=True)
    parser.add_argument("--threshold", type=float, default=0.05139489475137804)
    parser.add_argument("--failure-reward", type=float, default=-1.0)
    parser.add_argument("--bootstrap-samples", type=int, default=5000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    output = Path(args.output)
    if output.exists() and not args.overwrite:
        raise FileExistsError(f"{output} exists; pass --overwrite")
    split_by_group = None
    if args.selections:
        split_by_group = {
            str(row["group_id"]): str(row["split"])
            for row in load_jsonl(args.selections)
        }
    report = compare(
        load_jsonl(args.before),
        load_jsonl(args.after),
        threshold=args.threshold,
        failure_reward=args.failure_reward,
        split_by_group=split_by_group,
        seed=args.seed,
        bootstrap_samples=args.bootstrap_samples,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"comparison: {output}")


if __name__ == "__main__":
    main()
