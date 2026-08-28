"""Measure complementarity between V11 reward and residual noise-likeness."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
import math
import os
from pathlib import Path

import numpy as np


DEFAULT_ALPHAS = (-0.50, -0.20, -0.10, -0.05, 0.0, 0.02, 0.05, 0.10, 0.20, 0.50)
V11_SCORE_PATHS = (
    "training_score",
    "reward_detail.training_score",
    "shaped_reward",
    "reward_detail.shaped_reward",
    "raw_reward",
    "reward_detail.raw_reward",
    "reward",
)


def _read_jsonl(path):
    with open(path, encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _nested(row, dotted_path):
    value = row
    for part in dotted_path.split("."):
        if not isinstance(value, dict) or part not in value:
            return None
        value = value[part]
    return value


def _finite_number(value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _v11_score(row, field=None):
    paths = (field,) if field else V11_SCORE_PATHS
    for path in paths:
        value = _finite_number(_nested(row, path))
        if value is not None:
            return value, path
    raise KeyError("no supported V11 score field")


def _key(row):
    group_id = row.get("group_id")
    candidate_id = row.get("candidate_id")
    if candidate_id is None:
        candidate_id = row.get("candidate_index")
    if group_id is None or candidate_id is None:
        raise KeyError("group_id/candidate_id")
    return str(group_id), str(candidate_id)


def _overall_label(noise_row, v11_row):
    for value in (
        noise_row.get("overall_label"),
        noise_row.get("vlm_improvement"),
        (noise_row.get("vlm_detail") or {}).get("improvement"),
        v11_row.get("vlm_improvement"),
        (v11_row.get("vlm_detail") or {}).get("improvement"),
    ):
        if value is not None:
            return int(bool(value))
    raise KeyError("VLM overall-improvement label")


def _zscore(values):
    values = np.asarray(values, dtype=float)
    std = float(np.std(values))
    if std < 1e-12:
        return np.zeros_like(values)
    return (values - float(np.mean(values))) / std


def _prepare(v11_rows, noise_rows, score_field=None):
    v11_by_key = {_key(row): row for row in v11_rows}
    merged = []
    missing = Counter()
    used_fields = Counter()
    for noise in noise_rows:
        try:
            key = _key(noise)
            v11 = v11_by_key.get(key)
            if v11 is None:
                missing["missing_v11_candidate"] += 1
                continue
            v11_score, used_field = _v11_score(v11, score_field)
            noise_score = _finite_number(noise.get("residual_score"))
            if noise_score is None:
                raise KeyError("residual_score")
            used_fields[used_field] += 1
            merged.append(
                {
                    "group_id": key[0],
                    "candidate_id": key[1],
                    "label": _overall_label(noise, v11),
                    "v11_score": v11_score,
                    "noise_score": noise_score,
                }
            )
        except Exception as exc:
            missing[f"{type(exc).__name__}: {exc}"] += 1

    groups = defaultdict(list)
    for row in merged:
        groups[row["group_id"]].append(row)
    for group in groups.values():
        v11_z = _zscore([row["v11_score"] for row in group])
        noise_z = _zscore([row["noise_score"] for row in group])
        for row, left, right in zip(group, v11_z, noise_z):
            row["v11_z"] = float(left)
            row["noise_z"] = float(right)
    return merged, missing, used_fields


def _ranking_metrics(rows, score_name):
    groups = defaultdict(list)
    for row in rows:
        groups[row["group_id"]].append(row)
    pairwise = []
    macro = []
    top1 = []
    mixed = 0
    for group in groups.values():
        positives = [row for row in group if row["label"] == 1]
        negatives = [row for row in group if row["label"] == 0]
        if not positives or not negatives:
            continue
        mixed += 1
        local = []
        for positive in positives:
            for negative in negatives:
                delta = positive[score_name] - negative[score_name]
                value = 1.0 if delta > 0 else (0.5 if delta == 0 else 0.0)
                local.append(value)
                pairwise.append(value)
        macro.append(float(np.mean(local)))
        maximum = max(row[score_name] for row in group)
        tied = [row["label"] for row in group if row[score_name] == maximum]
        top1.append(float(np.mean(tied)))
    return {
        "groups": len(groups),
        "mixed_groups": mixed,
        "pairwise": float(np.mean(pairwise)) if pairwise else None,
        "macro": float(np.mean(macro)) if macro else None,
        "top1": float(np.mean(top1)) if top1 else None,
        "pairs": len(pairwise),
    }


def _pair_complementarity(rows):
    groups = defaultdict(list)
    for row in rows:
        groups[row["group_id"]].append(row)
    counts = Counter()
    examples = []
    for group_id, group in groups.items():
        positives = [row for row in group if row["label"] == 1]
        negatives = [row for row in group if row["label"] == 0]
        for positive in positives:
            for negative in negatives:
                v11 = np.sign(positive["v11_score"] - negative["v11_score"])
                noise = np.sign(positive["noise_score"] - negative["noise_score"])
                v11_state = "correct" if v11 > 0 else ("tie" if v11 == 0 else "wrong")
                noise_state = "correct" if noise > 0 else ("tie" if noise == 0 else "wrong")
                counts[f"v11_{v11_state}__noise_{noise_state}"] += 1
                if v11 <= 0 < noise and len(examples) < 20:
                    examples.append(
                        {
                            "group_id": group_id,
                            "positive_candidate": positive["candidate_id"],
                            "negative_candidate": negative["candidate_id"],
                            "v11_margin": positive["v11_score"] - negative["v11_score"],
                            "noise_margin": positive["noise_score"] - negative["noise_score"],
                        }
                    )
    return dict(counts), examples


def _format(value):
    return "N/A" if value is None else f"{value:.1%}"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--v11-rollouts", required=True)
    parser.add_argument("--noise-scores", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--v11-score-field")
    parser.add_argument("--alphas", nargs="+", type=float, default=DEFAULT_ALPHAS)
    args = parser.parse_args()

    rows, missing, used_fields = _prepare(
        _read_jsonl(args.v11_rollouts),
        _read_jsonl(args.noise_scores),
        args.v11_score_field,
    )
    if not rows:
        raise RuntimeError(f"no candidates merged: {dict(missing)}")
    print("========== MERGE ==========")
    print("merged candidates:", len(rows))
    print("missing:", dict(missing))
    print("V11 score fields:", dict(used_fields))

    baseline_v11 = _ranking_metrics(rows, "v11_score")
    baseline_noise = _ranking_metrics(rows, "noise_score")
    complementarity, repair_examples = _pair_complementarity(rows)
    print("\n========== BASELINES ==========")
    for name, metrics in (("V11", baseline_v11), ("NOISE", baseline_noise)):
        print(
            f"{name}: mixed={metrics['mixed_groups']} "
            f"pairwise={_format(metrics['pairwise'])} "
            f"macro={_format(metrics['macro'])} top1={_format(metrics['top1'])}"
        )
    print("\n========== PAIR COMPLEMENTARITY ==========")
    for key, value in sorted(complementarity.items()):
        print(f"{key}: {value}")

    grid = []
    print("\n========== GROUP-NORMALIZED FUSION ==========")
    print("alpha   pairwise   macro   top1")
    for alpha in args.alphas:
        for row in rows:
            row["combined_score"] = row["v11_z"] + alpha * row["noise_z"]
        metrics = _ranking_metrics(rows, "combined_score")
        grid.append({"alpha": alpha, **metrics})
        print(
            f"{alpha:>5.2f}   {_format(metrics['pairwise']):>8} "
            f"{_format(metrics['macro']):>8} {_format(metrics['top1']):>7}"
        )

    os.makedirs(args.out_dir, exist_ok=True)
    report = {
        "merged_candidates": len(rows),
        "missing": dict(missing),
        "v11_score_fields": dict(used_fields),
        "v11": baseline_v11,
        "noise": baseline_noise,
        "pair_complementarity": complementarity,
        "repair_examples": repair_examples,
        "fusion_grid": grid,
        "interpretation_guardrail": (
            "Do not select alpha on one audit set. Require the same positive alpha "
            "to improve both full_clean and n8 before considering online use."
        ),
    }
    Path(os.path.join(args.out_dir, "fusion_report.json")).write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("\noutput:", args.out_dir)


if __name__ == "__main__":
    main()
