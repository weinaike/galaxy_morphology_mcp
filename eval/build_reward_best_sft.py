"""Build a small SFT dataset from the highest-raw-reward rollout per parent.

This is an explicit *learnability upper-bound* experiment.  It does not claim
that the selected action is ground truth: it distils the current deterministic
rule reward into the policy and then asks whether the policy can reproduce
higher-reward actions on the same and held-out parent states.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from eval.run_grpo_rollout import _load_parent_context, load_jsonl


def write_jsonl(path: str | Path, rows: Iterable[Mapping[str, Any]]) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(dict(row), ensure_ascii=False) + "\n")


def remap_path(path: str, old: str | None, new: str | None) -> str:
    if not old or not new:
        return path
    old = old.rstrip("/\\")
    new = new.rstrip("/\\")
    if path == old or path.startswith(old + "/") or path.startswith(old + "\\"):
        return new + path[len(old) :]
    return path


def select_best_rollouts(
    rollouts: Sequence[Mapping[str, Any]],
    *,
    min_raw_reward: float | None = None,
    min_reward_gap: float = 0.0,
    min_successful_candidates: int = 2,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Select one successful, parseable, highest-reward response per group."""

    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rollouts:
        grouped[str(row.get("group_id"))].append(row)

    selected: list[dict[str, Any]] = []
    stats: Counter[str] = Counter()
    stats["groups_total"] = len(grouped)
    for group_id, rows in grouped.items():
        eligible = []
        for row in rows:
            if row.get("outcome") != "success":
                continue
            prediction = str(row.get("prediction") or "").strip()
            reward = row.get("raw_reward")
            try:
                reward = float(reward)
            except (TypeError, ValueError):
                continue
            if not prediction or not math.isfinite(reward):
                continue
            eligible.append((reward, int(row.get("candidate_index") or 0), row))

        if not eligible:
            stats["skip_no_successful_candidate"] += 1
            continue
        if len(eligible) < min_successful_candidates:
            stats["skip_too_few_successful_candidates"] += 1
            continue
        eligible.sort(key=lambda item: (-item[0], item[1]))
        best_reward, _, best = eligible[0]
        runner_up_reward = eligible[1][0] if len(eligible) > 1 else None
        gap = (
            best_reward - runner_up_reward
            if runner_up_reward is not None
            else None
        )
        if min_raw_reward is not None and best_reward < min_raw_reward:
            stats["skip_below_min_raw_reward"] += 1
            continue
        if gap is not None and gap < min_reward_gap:
            stats["skip_below_min_reward_gap"] += 1
            continue

        record = dict(best)
        record.update(
            selection_rank=1,
            eligible_candidates=len(eligible),
            runner_up_raw_reward=runner_up_reward,
            reward_gap=gap,
        )
        selected.append(record)
        stats["selected"] += 1
        stats[f"selected_action_{best.get('action_type', 'unknown')}"] += 1
    return selected, dict(stats)


def split_group_ids_by_physical_id(
    parents: Sequence[Mapping[str, Any]],
    selected_group_ids: set[str],
    *,
    val_ratio: float,
    seed: int,
) -> dict[str, str]:
    if not 0.0 <= val_ratio < 1.0:
        raise ValueError("--val-ratio must be in [0, 1)")
    group_to_pid = {
        str(row["group_id"]): str(row.get("physical_id") or row["group_id"])
        for row in parents
        if str(row.get("group_id")) in selected_group_ids
    }
    pids = sorted(set(group_to_pid.values()))
    rng = random.Random(seed)
    rng.shuffle(pids)
    n_val = int(round(len(pids) * val_ratio))
    if val_ratio > 0 and len(pids) >= 2:
        n_val = max(1, min(n_val, len(pids) - 1))
    val_pids = set(pids[:n_val])
    return {
        group_id: ("val" if pid in val_pids else "train")
        for group_id, pid in group_to_pid.items()
    }


def build_sft_row(
    parent: Mapping[str, Any],
    selected: Mapping[str, Any],
    *,
    tree_cache: dict[str, dict[str, Any]],
    max_steps: int,
    image_root_from: str | None,
    image_root_to: str | None,
) -> dict[str, Any]:
    from eval.run_exec_eval import build_step_prompt

    tree, parent_node, synthetic_child = _load_parent_context(parent, tree_cache)
    system_prompt, user_text, image_path = build_step_prompt(
        parent_node, synthetic_child, tree, max_steps
    )
    if not user_text.startswith("<image>"):
        user_text = "<image>\n" + user_text
    mapped_image = remap_path(
        os.path.abspath(str(image_path)), image_root_from, image_root_to
    )
    return {
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_text},
            {"role": "assistant", "content": str(selected["prediction"]).strip()},
        ],
        "images": [mapped_image],
    }


def dataset_info() -> dict[str, Any]:
    def entry(file_name: str) -> dict[str, Any]:
        return {
            "file_name": file_name,
            "formatting": "sharegpt",
            "columns": {"messages": "messages", "images": "images"},
            "tags": {
                "role_tag": "role",
                "content_tag": "content",
                "user_tag": "user",
                "assistant_tag": "assistant",
                "system_tag": "system",
            },
        }

    return {
        "galaxy_reward_best_train": entry("reward_best_sft_train.jsonl"),
        "galaxy_reward_best_val": entry("reward_best_sft_val.jsonl"),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parents", required=True)
    parser.add_argument("--rollouts", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--max-steps", type=int, default=15)
    parser.add_argument("--min-raw-reward", type=float)
    parser.add_argument("--min-reward-gap", type=float, default=0.0)
    parser.add_argument("--min-successful-candidates", type=int, default=2)
    parser.add_argument("--val-ratio", type=float, default=0.1)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--image-root-from")
    parser.add_argument("--image-root-to")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    outputs = [
        output_dir / "reward_best_sft_train.jsonl",
        output_dir / "reward_best_sft_val.jsonl",
        output_dir / "reward_best_selections.jsonl",
        output_dir / "reward_best_report.json",
        output_dir / "dataset_info.json",
    ]
    existing = [str(path) for path in outputs if path.exists()]
    if existing and not args.overwrite:
        raise FileExistsError(
            "outputs already exist; use a new directory or pass --overwrite: "
            + ", ".join(existing)
        )
    output_dir.mkdir(parents=True, exist_ok=True)

    parents = load_jsonl(args.parents)
    rollouts = load_jsonl(args.rollouts)
    parent_by_group = {str(row["group_id"]): row for row in parents}
    if len(parent_by_group) != len(parents):
        raise ValueError("parents contains duplicate group_id values")

    selected, selection_stats = select_best_rollouts(
        rollouts,
        min_raw_reward=args.min_raw_reward,
        min_reward_gap=args.min_reward_gap,
        min_successful_candidates=args.min_successful_candidates,
    )
    missing = sorted(
        {str(row["group_id"]) for row in selected} - set(parent_by_group)
    )
    if missing:
        raise KeyError(f"selected groups missing from parents: {missing[:5]}")

    split_by_group = split_group_ids_by_physical_id(
        parents,
        {str(row["group_id"]) for row in selected},
        val_ratio=args.val_ratio,
        seed=args.seed,
    )
    tree_cache: dict[str, dict[str, Any]] = {}
    train_rows: list[dict[str, Any]] = []
    val_rows: list[dict[str, Any]] = []
    audit_rows: list[dict[str, Any]] = []
    for row in selected:
        group_id = str(row["group_id"])
        split = split_by_group[group_id]
        sample = build_sft_row(
            parent_by_group[group_id],
            row,
            tree_cache=tree_cache,
            max_steps=args.max_steps,
            image_root_from=args.image_root_from,
            image_root_to=args.image_root_to,
        )
        (val_rows if split == "val" else train_rows).append(sample)
        audit_rows.append(
            {
                "group_id": group_id,
                "physical_id": parent_by_group[group_id].get("physical_id"),
                "split": split,
                "candidate_id": row.get("candidate_id"),
                "candidate_index": row.get("candidate_index"),
                "action_type": row.get("action_type"),
                "raw_reward": row.get("raw_reward"),
                "runner_up_raw_reward": row.get("runner_up_raw_reward"),
                "reward_gap": row.get("reward_gap"),
                "eligible_candidates": row.get("eligible_candidates"),
                "prediction": row.get("prediction"),
            }
        )

    write_jsonl(outputs[0], train_rows)
    write_jsonl(outputs[1], val_rows)
    write_jsonl(outputs[2], audit_rows)
    outputs[4].write_text(
        json.dumps(dataset_info(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    rewards = [float(row["raw_reward"]) for row in selected]
    gaps = [
        float(row["reward_gap"])
        for row in selected
        if row.get("reward_gap") is not None
    ]
    report = {
        **selection_stats,
        "parents": len(parents),
        "rollout_candidates": len(rollouts),
        "train_samples": len(train_rows),
        "val_samples": len(val_rows),
        "mean_selected_raw_reward": sum(rewards) / len(rewards) if rewards else None,
        "mean_top2_reward_gap": sum(gaps) / len(gaps) if gaps else None,
        "min_raw_reward": args.min_raw_reward,
        "min_reward_gap": args.min_reward_gap,
        "min_successful_candidates": args.min_successful_candidates,
        "val_ratio": args.val_ratio,
        "seed": args.seed,
        "max_steps": args.max_steps,
        "source_parents": os.path.abspath(args.parents),
        "source_rollouts": os.path.abspath(args.rollouts),
    }
    outputs[3].write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"SFT data: {output_dir}")


if __name__ == "__main__":
    main()
