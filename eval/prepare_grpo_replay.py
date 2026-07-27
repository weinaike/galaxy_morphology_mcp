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


def convert_trajectory_dir(input_dir: str) -> list[dict]:
    trajectories = load_trajectories(input_dir)
    pairs = extract_step_pairs(trajectories)
    records = []
    for pair in pairs:
        rule = compute_rule_reward_for_pair(pair)
        records.append(
            {
                "group_id": f"{pair['galaxy_id']}::{pair['parent_id']}",
                "parent_id": pair["parent_id"],
                "step_id": pair.get("depth", -1),
                "galaxy_id": pair["galaxy_id"],
                "candidate_id": pair["node_id"],
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
            }
        )
    return records


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    records = convert_trajectory_dir(args.input_dir)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    print(f"wrote {len(records)} replay records to {output}")


if __name__ == "__main__":
    main()
