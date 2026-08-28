"""Calibrate fixed-region residual noise-likeness features offline."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path

import numpy as np

from eval import calibrate_residual_v12_5 as base
from eval.residual_noise_likeness import (
    FEATURE_NAMES,
    RegionConfig,
    compute_noise_likeness_badness,
    compute_noise_likeness_deltas,
)


def _configure_extractor():
    # Reuse path resolution, labels, fixed galaxy split and metric code exactly.
    base.FEATURE_NAMES = FEATURE_NAMES
    base.compute_residual_badness_features = compute_noise_likeness_badness
    base.compute_residual_feature_deltas = compute_noise_likeness_deltas


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--val-ratio", type=float, default=0.7)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--min-precision", type=float, default=0.85)
    args = parser.parse_args()

    _configure_extractor()
    os.makedirs(args.out_dir, exist_ok=True)
    paths, rows, failures, total = base._build_rows(args.input_dir)
    print("========== COVERAGE ==========")
    print("trajectory files:", len(paths))
    print("total pairs:", total)
    print("scored pairs:", len(rows))
    print("coverage:", len(rows) / max(total, 1))
    print("failures:", dict(failures))
    if not rows:
        raise RuntimeError("no residual pairs were scored")

    val_rows, test_rows = base._split(rows, args.val_ratio, args.seed)
    x_val, y_val = base._matrix(val_rows)
    mean, std, weights, intercept = base._fit_nonnegative_logistic(x_val, y_val)
    model = {"mean": mean, "std": std, "weights": weights, "intercept": intercept}
    val_scores = base._predict(val_rows, model)
    test_scores = base._predict(test_rows, model)
    threshold, val_metrics = base._choose_threshold(
        y_val, val_scores, args.min_precision
    )
    test_labels = np.asarray(
        [row["vlm_residual_improved"] for row in test_rows], dtype=int
    )
    test_metrics = base._metrics(test_labels, test_scores >= threshold)

    print("\n========== NOISE-LIKENESS MODEL ==========")
    for name, weight in sorted(
        zip(FEATURE_NAMES, weights), key=lambda item: item[1], reverse=True
    ):
        print(f"{name}: weight={weight:.6f}")
    print("threshold:", threshold)
    base._print_metrics("VAL", val_metrics)
    base._print_metrics("TEST", test_metrics)

    for split_name, split_rows, scores in (
        ("val", val_rows, val_scores),
        ("test", test_rows, test_scores),
    ):
        for row, score in zip(split_rows, scores):
            row["noise_likeness_probability"] = float(score)
            row["split"] = split_name

    with open(
        os.path.join(args.out_dir, "noise_likeness_pairs.jsonl"),
        "w",
        encoding="utf-8",
    ) as handle:
        for row in val_rows + test_rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    config = {
        "feature_names": list(FEATURE_NAMES),
        "feature_definition": "parent_badness_minus_child_badness",
        "region_config": asdict(RegionConfig()),
        "mean": mean.tolist(),
        "std": std.tolist(),
        "weights": weights.tolist(),
        "intercept": float(intercept),
        "threshold": float(threshold),
        "val_metrics": val_metrics,
        "test_metrics": test_metrics,
        "val_ratio": args.val_ratio,
        "seed": args.seed,
        "coverage": len(rows) / max(total, 1),
        "failures": dict(failures),
    }
    Path(os.path.join(args.out_dir, "noise_likeness_model_config.json")).write_text(
        json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("\noutput:", args.out_dir)


if __name__ == "__main__":
    main()
