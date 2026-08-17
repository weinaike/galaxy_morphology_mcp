"""Convert existing trajectory trees into normalized GRPO replay JSONL."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from data_gen.extract_training_data import load_trajectories
from eval.validate_reward_alignment import (
    compute_rule_reward_for_pair,
    extract_step_pairs,
)


def convert_trajectory_dirs(input_dirs: list[str], reward_version: str) -> list[dict]:
    trajectories = []
    for input_dir in input_dirs:
        trajectories.extend(load_trajectories(input_dir))
    pairs = extract_step_pairs(trajectories)
    records = []
    for pair in pairs:
        # V12.5's calibrated residual gate is disabled (threshold=0), so its
        # production decision reward is intentionally identical to V12.4.
        base_version = "v12.4" if reward_version == "v12.5" else reward_version
        rule = compute_rule_reward_for_pair(pair, reward_version=base_version)
        records.append(
            {
                "group_id": f"{pair['galaxy_id']}::{pair['parent_id']}",
                "parent_id": pair["parent_id"],
                "step_id": pair.get("depth", -1),
                "galaxy_id": pair["galaxy_id"],
                "candidate_id": pair["node_id"],
                "reward_version": reward_version,
                "action_type": pair.get("coarse_label", "unknown"),
                "outcome": "success",
                "raw_reward": rule["reward"],
                "vlm_improvement": pair["vlm_improvement"],
                "bounds_ok": rule["bounds_ok"],
                "fitted_bounds_ok": rule.get("fitted_bounds_ok", True),
                "chi2_vetoed": rule["chi2_vetoed"],
                "r_chi2": rule["r_chi2"],
                "r_bic": rule["r_bic"],
                "r_noise": rule["r_noise"],
                "structure_vetoed": rule.get("structure_vetoed", False),
                "structure_violations": rule.get("structure_violations", []),
                "structure_warnings": rule.get("structure_warnings", []),
            }
        )
    return records


def convert_trajectory_dir(input_dir: str) -> list[dict]:
    # Backward-compatible single-directory V11 entry point.
    return convert_trajectory_dirs([input_dir], "v11")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--input-dir",
        action="append",
        required=True,
        help="trajectory directory; repeat to combine E1-E6",
    )
    parser.add_argument(
        "--reward-version",
        choices=("v11", "v12.4", "v12.5"),
        default="v11",
    )
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    records = convert_trajectory_dirs(args.input_dir, args.reward_version)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    print(f"wrote {len(records)} replay records to {output}")


if __name__ == "__main__":
    main()
