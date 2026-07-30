"""Add independent VLM labels to already executed GRPO rollouts.

This command never samples the policy or reruns GALFIT.  It compares each
successful child state with its parent, checkpoints the API results, and
reports v11/VLM alignment at candidate, same-parent pair, and group levels.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from eval.reward_for_grpo import (
    DEFAULT_MARGIN_SCALE,
    DEFAULT_MARGIN_WEIGHT,
    V11_THRESHOLD,
)
from eval.validate_grpo_reward import build_replay_report, load_jsonl


def _key(row: Mapping[str, Any]) -> tuple[str, int]:
    return str(row.get("group_id")), int(row.get("candidate_index") or 0)


def _atomic_write_jsonl(path: str | Path, rows: Iterable[Mapping[str, Any]]) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(dict(row), ensure_ascii=False) + "\n")
    os.replace(temporary, output)


def _write_json(path: str | Path, value: Mapping[str, Any]) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
    os.replace(temporary, output)


def _existing_path(value: Any, field: str) -> str:
    if not value:
        raise FileNotFoundError(f"{field} is missing")
    path = Path(str(value))
    if not path.is_file():
        raise FileNotFoundError(f"{field} does not exist: {path}")
    return str(path)


async def label_rollout_with_vlm(
    row: Mapping[str, Any],
    parent: Mapping[str, Any],
    *,
    model_name: str,
    api_key: str | None,
) -> dict[str, Any]:
    """Label one executed child; non-successful children consume no API call."""

    result = dict(row)
    result["vlm_label_model"] = model_name
    if result.get("outcome") != "success":
        result.update(
            vlm_improvement=None,
            vlm_label_status="skipped_non_success",
        )
        result.pop("vlm_error", None)
        return result

    try:
        parent_residual = _existing_path(parent.get("residual_path"), "parent residual")
        parent_summary = _existing_path(parent.get("summary_path"), "parent summary")
        child_residual = _existing_path(
            result.get("model_residual_path"), "child residual"
        )
        child_summary = _existing_path(
            result.get("model_summary_path"), "child summary"
        )
        from eval.vlm_reward import vlm_reward_for_step

        detail = await asyncio.to_thread(
            vlm_reward_for_step,
            parent_residual_image_path=parent_residual,
            parent_summary_path=parent_summary,
            model_new_residual_image_path=child_residual,
            model_new_summary_path=child_summary,
            model_name=model_name,
            api_key=api_key,
        )
        improvement = detail.get("improvement")
        if improvement not in (0, 1, False, True):
            raise ValueError(f"invalid VLM improvement label: {improvement!r}")
        result.update(
            vlm_improvement=int(improvement),
            vlm_detail=detail,
            vlm_label_status="labeled",
        )
        result.pop("vlm_error", None)
    except Exception as exc:
        result.update(
            vlm_improvement=None,
            vlm_label_status="error",
            vlm_error=f"{type(exc).__name__}: {exc}",
        )
    return result


def _safe_div(numerator: int | float, denominator: int | float) -> float | None:
    return numerator / denominator if denominator else None


def _binary_metrics(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    labeled = [
        row
        for row in rows
        if row.get("vlm_improvement") in (0, 1, False, True)
        and row.get("coarse_reward") in (0.0, 1.0)
    ]
    tp = sum(
        int(int(row["coarse_reward"]) == 1 and int(row["vlm_improvement"]) == 1)
        for row in labeled
    )
    fp = sum(
        int(int(row["coarse_reward"]) == 1 and int(row["vlm_improvement"]) == 0)
        for row in labeled
    )
    tn = sum(
        int(int(row["coarse_reward"]) == 0 and int(row["vlm_improvement"]) == 0)
        for row in labeled
    )
    fn = sum(
        int(int(row["coarse_reward"]) == 0 and int(row["vlm_improvement"]) == 1)
        for row in labeled
    )
    return {
        "n": len(labeled),
        "tp": tp,
        "fp": fp,
        "tn": tn,
        "fn": fn,
        "accuracy": _safe_div(tp + tn, len(labeled)),
        "precision": _safe_div(tp, tp + fp),
        "recall": _safe_div(tp, tp + fn),
        "specificity": _safe_div(tn, tn + fp),
        "f1": _safe_div(2 * tp, 2 * tp + fp + fn),
        "vlm_positive_rate": _safe_div(tp + fn, len(labeled)),
        "v11_positive_rate": _safe_div(tp + fp, len(labeled)),
    }


def _pairwise_metrics(
    rows: Sequence[Mapping[str, Any]],
    *,
    score_key: str,
) -> dict[str, Any]:
    groups: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[str(row.get("group_id"))].append(row)

    wins = ties = losses = pairs = mixed_groups = 0
    for group_rows in groups.values():
        positives = [
            row
            for row in group_rows
            if row.get("vlm_improvement") in (1, True)
            and isinstance(row.get(score_key), (int, float))
        ]
        negatives = [
            row
            for row in group_rows
            if row.get("vlm_improvement") in (0, False)
            and isinstance(row.get(score_key), (int, float))
        ]
        mixed_groups += int(bool(positives and negatives))
        for positive in positives:
            for negative in negatives:
                pairs += 1
                positive_score = float(positive[score_key])
                negative_score = float(negative[score_key])
                if positive_score > negative_score:
                    wins += 1
                elif positive_score == negative_score:
                    ties += 1
                else:
                    losses += 1
    return {
        "mixed_labeled_groups": mixed_groups,
        "pairs": pairs,
        "wins": wins,
        "ties": ties,
        "losses": losses,
        "strict_accuracy": _safe_div(wins, pairs),
        "half_tie_accuracy": _safe_div(wins + 0.5 * ties, pairs),
    }


def _split_metrics(
    rows: Sequence[Mapping[str, Any]], field: str
) -> dict[str, dict[str, Any]]:
    values: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        values[str(row.get(field, "unknown"))].append(row)
    return {name: _binary_metrics(part) for name, part in sorted(values.items())}


def build_vlm_alignment_report(
    shaped_rows: Sequence[Mapping[str, Any]],
    replay_report: Mapping[str, Any],
    *,
    vlm_model: str,
) -> dict[str, Any]:
    successful = [row for row in shaped_rows if row.get("outcome") == "success"]
    labeled_successful = [
        row
        for row in successful
        if row.get("vlm_improvement") in (0, 1, False, True)
    ]
    groups: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in shaped_rows:
        groups[str(row.get("group_id"))].append(row)

    trainable = {
        group_id: rows
        for group_id, rows in groups.items()
        if any(bool(row.get("group_train_mask")) for row in rows)
    }
    complete: dict[str, list[Mapping[str, Any]]] = {}
    for group_id, rows in trainable.items():
        success_rows = [row for row in rows if row.get("outcome") == "success"]
        if success_rows and all(
            row.get("vlm_improvement") in (0, 1, False, True)
            for row in success_rows
        ):
            complete[group_id] = rows

    composition = Counter()
    top_has_vlm_positive = 0
    for rows in complete.values():
        labels = {
            int(row["vlm_improvement"])
            for row in rows
            if row.get("vlm_improvement") in (0, 1, False, True)
        }
        if labels == {0, 1}:
            composition["mixed_vlm_labels"] += 1
        elif labels == {1}:
            composition["all_vlm_positive"] += 1
        elif labels == {0}:
            composition["all_vlm_negative"] += 1

        candidates = [
            row
            for row in rows
            if bool(row.get("sample_train_mask"))
            and isinstance(row.get("shaped_reward"), (int, float))
        ]
        if candidates:
            best = max(float(row["shaped_reward"]) for row in candidates)
            top_rows = [
                row for row in candidates if float(row["shaped_reward"]) == best
            ]
            top_has_vlm_positive += int(
                any(row.get("vlm_improvement") in (1, True) for row in top_rows)
            )

    trainable_rows = [
        row
        for rows in trainable.values()
        for row in rows
        if bool(row.get("group_train_mask"))
    ]
    return {
        **dict(replay_report),
        "vlm_model": vlm_model,
        "vlm_label_coverage": {
            "successful_candidates": len(successful),
            "labeled_successful_candidates": len(labeled_successful),
            "unlabeled_successful_candidates": len(successful)
            - len(labeled_successful),
            "coverage": _safe_div(len(labeled_successful), len(successful)),
            "label_errors": sum(
                row.get("vlm_label_status") == "error" for row in shaped_rows
            ),
            "skipped_non_success": sum(
                row.get("vlm_label_status") == "skipped_non_success"
                for row in shaped_rows
            ),
        },
        "candidate_binary_alignment": _binary_metrics(shaped_rows),
        "same_parent_pairwise_raw_v11": _pairwise_metrics(
            shaped_rows, score_key="raw_reward"
        ),
        "same_parent_pairwise_shaped_reward": _pairwise_metrics(
            shaped_rows, score_key="shaped_reward"
        ),
        "trainable_groups_only_pairwise_shaped_reward": _pairwise_metrics(
            trainable_rows, score_key="shaped_reward"
        ),
        "trainable_group_alignment": {
            "groups": len(trainable),
            "fully_labeled_groups": len(complete),
            "incomplete_groups": len(trainable) - len(complete),
            **dict(composition),
            "top_reward_has_vlm_positive_groups": top_has_vlm_positive,
            "top_reward_has_vlm_positive_rate": _safe_div(
                top_has_vlm_positive, len(complete)
            ),
        },
        "candidate_alignment_by_action_type": _split_metrics(
            shaped_rows, "action_type"
        ),
        "candidate_alignment_by_parent_kind": _split_metrics(
            shaped_rows, "parent_kind"
        ),
    }


async def label_rollouts(args: argparse.Namespace) -> dict[str, Any]:
    parents = {_key(row)[0]: row for row in load_jsonl(args.parents)}
    source_rows = load_jsonl(args.rollouts)
    output = Path(args.output)
    if output.exists() and not (args.resume or args.overwrite):
        raise FileExistsError(
            f"{output} already exists; pass --resume or explicitly pass --overwrite"
        )

    rows_by_key = {_key(row): dict(row) for row in source_rows}
    if args.resume and output.exists():
        for row in load_jsonl(output):
            if _key(row) in rows_by_key:
                rows_by_key[_key(row)] = row

    ordered_keys = [_key(row) for row in source_rows]
    pending = [
        key
        for key in ordered_keys
        if rows_by_key[key].get("outcome") != "success"
        or rows_by_key[key].get("vlm_improvement") not in (0, 1, False, True)
    ]
    if args.max_candidates:
        pending = pending[: args.max_candidates]

    semaphore = asyncio.Semaphore(args.vlm_concurrency)

    async def run_one(key: tuple[str, int]) -> tuple[tuple[str, int], dict[str, Any]]:
        row = rows_by_key[key]
        parent = parents.get(key[0])
        if parent is None:
            result = dict(row)
            result.update(
                vlm_improvement=None,
                vlm_label_status="error",
                vlm_error=f"parent manifest row not found: {key[0]}",
            )
            return key, result
        async with semaphore:
            return key, await label_rollout_with_vlm(
                row,
                parent,
                model_name=args.vlm_model,
                api_key=args.api_key,
            )

    completed = 0
    tasks = [asyncio.create_task(run_one(key)) for key in pending]
    for future in asyncio.as_completed(tasks):
        key, result = await future
        rows_by_key[key] = result
        completed += 1
        print(
            f"[{completed}/{len(tasks)}] {key[0]} candidate={key[1]} "
            f"status={result.get('vlm_label_status')}",
            flush=True,
        )
        if completed % args.checkpoint_every == 0:
            _atomic_write_jsonl(
                output, (rows_by_key[key] for key in ordered_keys)
            )

    ordered_rows = [rows_by_key[key] for key in ordered_keys]
    _atomic_write_jsonl(output, ordered_rows)
    replay_report, shaped_rows = build_replay_report(
        ordered_rows,
        threshold=args.threshold,
        margin_scale=args.margin_scale,
        margin_weight=args.margin_weight,
    )
    report = build_vlm_alignment_report(
        shaped_rows, replay_report, vlm_model=args.vlm_model
    )
    _atomic_write_jsonl(args.shaped_output, shaped_rows)
    _write_json(args.report, report)
    errors = [
        {
            **dict(row),
            "alignment_error_type": (
                "false_positive"
                if int(row["coarse_reward"]) == 1
                else "false_negative"
            ),
        }
        for row in shaped_rows
        if row.get("vlm_improvement") in (0, 1, False, True)
        and row.get("coarse_reward") in (0.0, 1.0)
        and int(row["coarse_reward"]) != int(row["vlm_improvement"])
    ]
    _atomic_write_jsonl(args.errors, errors)
    return report


def _derived_path(output: str, suffix: str) -> str:
    path = Path(output)
    stem = path.name[:-6] if path.name.endswith(".jsonl") else path.name
    return str(path.with_name(stem + suffix))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Label existing GRPO rollouts with VLM and report alignment"
    )
    parser.add_argument("--parents", required=True)
    parser.add_argument("--rollouts", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--shaped-output")
    parser.add_argument("--report")
    parser.add_argument("--errors")
    parser.add_argument("--vlm-model", default="gemini-3.1-pro-preview")
    parser.add_argument("--api-key", default=None)
    parser.add_argument("--vlm-concurrency", type=int, default=2)
    parser.add_argument("--checkpoint-every", type=int, default=8)
    parser.add_argument("--max-candidates", type=int, default=0)
    parser.add_argument("--threshold", type=float, default=V11_THRESHOLD)
    parser.add_argument("--margin-scale", type=float, default=DEFAULT_MARGIN_SCALE)
    parser.add_argument("--margin-weight", type=float, default=DEFAULT_MARGIN_WEIGHT)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.vlm_concurrency < 1:
        raise ValueError("--vlm-concurrency must be >= 1")
    if args.checkpoint_every < 1:
        raise ValueError("--checkpoint-every must be >= 1")
    if args.max_candidates < 0:
        raise ValueError("--max-candidates must be >= 0")
    if args.resume and args.overwrite:
        raise ValueError("--resume and --overwrite are mutually exclusive")
    args.shaped_output = args.shaped_output or _derived_path(
        args.output, "_shaped.jsonl"
    )
    args.report = args.report or _derived_path(args.output, "_report.json")
    args.errors = args.errors or _derived_path(
        args.output, "_alignment_errors.jsonl"
    )
    report = asyncio.run(label_rollouts(args))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"labeled rollouts: {args.output}")
    print(f"shaped details: {args.shaped_output}")
    print(f"alignment report: {args.report}")
    print(f"alignment errors: {args.errors}")


if __name__ == "__main__":
    main()
