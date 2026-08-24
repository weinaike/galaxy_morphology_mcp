"""Audit on-policy reward changes for the same parent across epochs.

Per-step reward means mix different parent difficulties.  This script groups
``reward_call_*.jsonl`` records by ``group_id``, orders repeated appearances
by reward-call index, and compares the same parent between occurrences.
"""

from __future__ import annotations

import argparse
import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean, median
from typing import Any


CALL_RE = re.compile(r"reward_call_(\d+)\.jsonl$")


def _load_groups(audit_dir: Path) -> dict[str, list[tuple[int, list[dict[str, Any]]]]]:
    groups: dict[str, list[tuple[int, list[dict[str, Any]]]]] = defaultdict(list)
    files: list[tuple[int, Path]] = []
    for path in audit_dir.glob("reward_call_*.jsonl"):
        match = CALL_RE.search(path.name)
        if match:
            files.append((int(match.group(1)), path))
    if not files:
        raise FileNotFoundError(f"no reward_call_*.jsonl under {audit_dir}")

    for call_index, path in sorted(files):
        by_group: dict[str, list[dict[str, Any]]] = defaultdict(list)
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    row = json.loads(line)
                    by_group[str(row.get("group_id"))].append(row)
        for group_id, rows in by_group.items():
            groups[group_id].append((call_index, rows))
    return groups


def _finite(values: list[Any]) -> list[float]:
    output: list[float] = []
    for value in values:
        try:
            converted = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(converted):
            output.append(converted)
    return output


def _group_metrics(rows: list[dict[str, Any]]) -> dict[str, float]:
    raw = _finite([row.get("raw_reward") for row in rows])
    shaped = _finite(
        [
            row.get("shaped_reward")
            for row in rows
            if row.get("coarse_reward") is not None
        ]
    )
    coarse = _finite([row.get("coarse_reward") for row in rows])
    count = len(rows)
    return {
        "mean_raw": mean(raw) if raw else float("nan"),
        "max_raw": max(raw) if raw else float("nan"),
        "mean_shaped": mean(shaped) if shaped else float("nan"),
        "max_shaped": max(shaped) if shaped else float("nan"),
        "positive_fraction": sum(value == 1.0 for value in coarse) / count,
        "failure_fraction": sum(value == -1.0 for value in coarse) / count,
        "success_fraction": sum(row.get("outcome") == "success" for row in rows) / count,
        "trainable": float(any(bool(row.get("group_train_mask")) for row in rows)),
    }


def _transition_summary(deltas: list[float]) -> dict[str, Any]:
    finite = [value for value in deltas if math.isfinite(value)]
    if not finite:
        return {"n": 0}
    epsilon = 1e-12
    return {
        "n": len(finite),
        "mean_delta": mean(finite),
        "median_delta": median(finite),
        "improved": sum(value > epsilon for value in finite),
        "unchanged": sum(abs(value) <= epsilon for value in finite),
        "worsened": sum(value < -epsilon for value in finite),
        "improvement_rate": sum(value > epsilon for value in finite) / len(finite),
    }


def _audit(audit_dir: Path) -> dict[str, Any]:
    groups = _load_groups(audit_dir)
    occurrence_counts = Counter(len(occurrences) for occurrences in groups.values())
    metric_names = (
        "mean_raw",
        "max_raw",
        "mean_shaped",
        "max_shaped",
        "positive_fraction",
        "failure_fraction",
        "success_fraction",
        "trainable",
    )
    transitions: dict[str, dict[str, list[float]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for occurrences in groups.values():
        ordered = sorted(occurrences)
        metrics = [_group_metrics(rows) for _, rows in ordered]
        for index in range(len(metrics) - 1):
            label = f"occurrence_{index + 1}_to_{index + 2}"
            for metric in metric_names:
                transitions[label][metric].append(
                    metrics[index + 1][metric] - metrics[index][metric]
                )

    return {
        "audit_dir": str(audit_dir.resolve()),
        "unique_parents": len(groups),
        "occurrence_count_distribution": dict(sorted(occurrence_counts.items())),
        "transitions": {
            label: {
                metric: _transition_summary(values)
                for metric, values in metric_values.items()
            }
            for label, metric_values in transitions.items()
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit-dir", type=Path, required=True)
    parser.add_argument("--output-json", type=Path)
    args = parser.parse_args()
    report = _audit(args.audit_dir)

    print("========== COVERAGE ==========")
    print("unique parents:", report["unique_parents"])
    print("occurrences:", report["occurrence_count_distribution"])
    for label, metrics in report["transitions"].items():
        print(f"\n========== {label} ==========")
        for metric, summary in metrics.items():
            print(metric, json.dumps(summary, ensure_ascii=False))

    if args.output_json:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"\noutput: {args.output_json}")


if __name__ == "__main__":
    main()
