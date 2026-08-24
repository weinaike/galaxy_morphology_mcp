"""Audit how failure mapping changes same-parent GRPO groups.

The script reads ``reward_call_*.jsonl`` files written by the Galaxy reward
manager.  It reports the observed coarse-level composition and a
counterfactual in which candidate-caused artifact-validation failures are
mapped from evaluator-mask to coarse ``-1``.

This isolates the GroupGate effect of failure-map changes without retraining.
Infrastructure/evaluator failures remain masked.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping


CALL_RE = re.compile(r"reward_call_(\d+)\.jsonl$")


@dataclass(frozen=True)
class GateResult:
    levels: tuple[int, ...]
    valid_candidates: int
    legacy_trainable: bool
    positive_anchored_trainable: bool


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--audit-dir",
        type=Path,
        required=True,
        help="Directory containing reward_call_*.jsonl files.",
    )
    parser.add_argument(
        "--block-size",
        type=int,
        default=100,
        help="Number of reward calls per reporting block (default: 100).",
    )
    parser.add_argument(
        "--simulate-failure-map",
        action="store_true",
        help=(
            "Also report a counterfactual that maps candidate-caused "
            "artifact-validation failures from mask to coarse -1."
        ),
    )
    parser.add_argument(
        "--output-json",
        type=Path,
        help="Optional path for the machine-readable report.",
    )
    return parser.parse_args()


def _iter_calls(audit_dir: Path) -> Iterable[tuple[int, Path]]:
    matches: list[tuple[int, Path]] = []
    for path in audit_dir.glob("reward_call_*.jsonl"):
        match = CALL_RE.search(path.name)
        if match:
            matches.append((int(match.group(1)), path))
    if not matches:
        raise FileNotFoundError(f"no reward_call_*.jsonl under {audit_dir}")
    yield from sorted(matches)


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSON at {path}:{line_number}") from exc
    return rows


def _is_candidate_artifact_failure(row: Mapping[str, Any]) -> bool:
    """Return whether failure-map should assign this row to policy ``-1``.

    The check intentionally excludes generic evaluator exceptions such as
    disk quota, dependency, permission and filesystem failures.  It targets
    completed candidate evaluation that lacks required end-to-end artifacts.
    """

    if row.get("outcome") != "evaluator_failure":
        return False
    if row.get("failure_stage") != "galfit_artifact_validation":
        return False
    reason = str(row.get("failure_reason") or "").lower()
    infrastructure_markers = (
        "disk quota",
        "no space left",
        "permission denied",
        "read-only file system",
        "missing shared librar",
        "module not found",
        "dependency",
    )
    return not any(marker in reason for marker in infrastructure_markers)


def _coarse_value(
    row: Mapping[str, Any], *, simulate_failure_map: bool
) -> int | None:
    value = row.get("coarse_reward")
    if value is not None:
        return int(float(value))
    if simulate_failure_map and _is_candidate_artifact_failure(row):
        return -1
    return None


def _gate(group: list[Mapping[str, Any]], *, simulate_failure_map: bool) -> GateResult:
    coarse = [
        value
        for row in group
        if (value := _coarse_value(row, simulate_failure_map=simulate_failure_map))
        is not None
    ]
    levels = tuple(sorted(set(coarse)))
    enough = len(coarse) >= 2
    legacy = enough and len(levels) >= 2
    positive_anchored = legacy and 1 in levels
    return GateResult(
        levels=levels,
        valid_candidates=len(coarse),
        legacy_trainable=legacy,
        positive_anchored_trainable=positive_anchored,
    )


def _signature(levels: tuple[int, ...]) -> str:
    if not levels:
        return "masked"
    return "{" + ",".join(str(level) for level in levels) + "}"


def _summarize(
    calls: list[tuple[int, list[dict[str, Any]]]],
    *,
    simulate_failure_map: bool,
) -> dict[str, Any]:
    groups: dict[tuple[int, str], list[dict[str, Any]]] = defaultdict(list)
    for call_index, rows in calls:
        for row in rows:
            groups[(call_index, str(row.get("group_id")))].append(row)

    signatures: Counter[str] = Counter()
    legacy_groups = 0
    anchored_groups = 0
    legacy_candidates = 0
    anchored_candidates = 0
    reclassified_candidates = 0
    for group in groups.values():
        result = _gate(group, simulate_failure_map=simulate_failure_map)
        signatures[_signature(result.levels)] += 1
        if result.legacy_trainable:
            legacy_groups += 1
            legacy_candidates += result.valid_candidates
        if result.positive_anchored_trainable:
            anchored_groups += 1
            anchored_candidates += result.valid_candidates
        if simulate_failure_map:
            reclassified_candidates += sum(
                _is_candidate_artifact_failure(row) for row in group
            )

    minus_one_zero_groups = signatures.get("{-1,0}", 0)
    return {
        "calls": len(calls),
        "candidates": sum(len(rows) for _, rows in calls),
        "groups": len(groups),
        "coarse_level_signatures": dict(sorted(signatures.items())),
        "minus_one_zero_groups": minus_one_zero_groups,
        "minus_one_zero_group_rate": (
            minus_one_zero_groups / len(groups) if groups else 0.0
        ),
        "legacy_trainable_groups": legacy_groups,
        "legacy_trainable_group_rate": legacy_groups / len(groups) if groups else 0.0,
        "positive_anchored_trainable_groups": anchored_groups,
        "positive_anchored_trainable_group_rate": (
            anchored_groups / len(groups) if groups else 0.0
        ),
        "groups_removed_by_positive_anchor": legacy_groups - anchored_groups,
        "legacy_trainable_candidates": legacy_candidates,
        "positive_anchored_trainable_candidates": anchored_candidates,
        "candidates_removed_by_positive_anchor": legacy_candidates - anchored_candidates,
        "failure_map_reclassified_candidates": reclassified_candidates,
    }


def _blocks(
    loaded_calls: list[tuple[int, list[dict[str, Any]]]], block_size: int
) -> dict[str, list[tuple[int, list[dict[str, Any]]]]]:
    output: dict[str, list[tuple[int, list[dict[str, Any]]]]] = defaultdict(list)
    for call_index, rows in loaded_calls:
        start = ((call_index - 1) // block_size) * block_size + 1
        end = start + block_size - 1
        output[f"{start}-{end}"].append((call_index, rows))
    return dict(output)


def _print_summary(title: str, summary: Mapping[str, Any]) -> None:
    print(f"\n========== {title} ==========")
    print(
        f"calls={summary['calls']} candidates={summary['candidates']} "
        f"groups={summary['groups']}"
    )
    print("coarse signatures:", summary["coarse_level_signatures"])
    print(
        "{-1,0} groups: "
        f"{summary['minus_one_zero_groups']}/{summary['groups']} "
        f"({summary['minus_one_zero_group_rate']:.1%})"
    )
    print(
        "legacy GroupGate: "
        f"{summary['legacy_trainable_groups']}/{summary['groups']} "
        f"({summary['legacy_trainable_group_rate']:.1%}), "
        f"candidates={summary['legacy_trainable_candidates']}"
    )
    print(
        "positive-anchored GroupGate: "
        f"{summary['positive_anchored_trainable_groups']}/{summary['groups']} "
        f"({summary['positive_anchored_trainable_group_rate']:.1%}), "
        f"candidates={summary['positive_anchored_trainable_candidates']}"
    )
    print(
        "removed by positive anchor: "
        f"groups={summary['groups_removed_by_positive_anchor']} "
        f"candidates={summary['candidates_removed_by_positive_anchor']}"
    )
    if summary["failure_map_reclassified_candidates"]:
        print(
            "counterfactual failure-map reclassified candidates: "
            f"{summary['failure_map_reclassified_candidates']}"
        )


def main() -> None:
    args = _parse_args()
    if args.block_size < 1:
        raise ValueError("--block-size must be >= 1")

    loaded_calls = [
        (call_index, _load_jsonl(path))
        for call_index, path in _iter_calls(args.audit_dir)
    ]
    report: dict[str, Any] = {
        "audit_dir": str(args.audit_dir.resolve()),
        "block_size": args.block_size,
        "blocks": {},
    }
    for label, calls in _blocks(loaded_calls, args.block_size).items():
        observed = _summarize(calls, simulate_failure_map=False)
        block_report: dict[str, Any] = {"observed": observed}
        _print_summary(f"{label} OBSERVED", observed)
        if args.simulate_failure_map:
            counterfactual = _summarize(calls, simulate_failure_map=True)
            block_report["counterfactual_failure_map"] = counterfactual
            _print_summary(f"{label} COUNTERFACTUAL FAILURE_MAP", counterfactual)
        report["blocks"][label] = block_report

    if args.output_json:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"\noutput: {args.output_json}")


if __name__ == "__main__":
    main()
