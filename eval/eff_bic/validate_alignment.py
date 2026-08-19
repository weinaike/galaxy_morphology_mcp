"""Validate parent-to-child effective-BIC direction against VLM improvement.

The classifier is fixed by definition: a child is predicted good iff
``BIC_eff(child) < BIC_eff(parent)``.  No threshold is fitted on Test.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

import numpy as np

from data_gen.dataset_utils import _to_physical_id
from eval.calibrate_residual_v12_5 import (
    _control_paths,
    _load_trees,
    _resolve_output_fits,
    _same_file,
)
from eval.eff_bic.metrics import compute_effective_bic_from_files


def _safe_div(numerator: int | float, denominator: int | float) -> float:
    return float(numerator / denominator) if denominator else 0.0


def _binary_metrics(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    rows = list(rows)
    tp = sum(row["eff_bic_prediction"] == 1 and row["vlm_improvement"] == 1 for row in rows)
    fp = sum(row["eff_bic_prediction"] == 1 and row["vlm_improvement"] == 0 for row in rows)
    tn = sum(row["eff_bic_prediction"] == 0 and row["vlm_improvement"] == 0 for row in rows)
    fn = sum(row["eff_bic_prediction"] == 0 and row["vlm_improvement"] == 1 for row in rows)
    return {
        "n": len(rows),
        "tp": tp,
        "fp": fp,
        "tn": tn,
        "fn": fn,
        "accuracy": _safe_div(tp + tn, len(rows)),
        "precision": _safe_div(tp, tp + fp),
        "recall": _safe_div(tp, tp + fn),
        "specificity": _safe_div(tn, tn + fp),
        "f1": _safe_div(2 * tp, 2 * tp + fp + fn),
        "predicted_positive_rate": _safe_div(tp + fp, len(rows)),
        "vlm_positive_rate": _safe_div(tp + fn, len(rows)),
    }


def _psf_path(child: dict[str, Any], parent: dict[str, Any]) -> str | None:
    for node in (child, parent):
        psf = _control_paths(node.get("feedme_path")).get("D")
        if psf and os.path.isfile(psf):
            return psf
    return None


def build_rows(input_dir: str, method: str) -> tuple[list[dict[str, Any]], Counter, int, int]:
    paths, trees = _load_trees(input_dir)
    cache: dict[tuple[str, str, str], dict[str, Any]] = {}
    rows: list[dict[str, Any]] = []
    failures: Counter = Counter()
    eligible = 0
    for tree in trees:
        galaxy_id = tree.get("galaxy_id", "unknown")
        node_map = {node.get("node_id"): node for node in tree.get("nodes", [])}
        for child in tree.get("nodes", []):
            parent = node_map.get(child.get("parent_id"))
            vlm = (child.get("reward_detail") or {}).get("vlm_detail") or {}
            if parent is None or vlm.get("improvement") not in (0, 1, False, True):
                continue
            eligible += 1
            try:
                parent_fits = _resolve_output_fits(parent)
                child_fits = _resolve_output_fits(child)
                if not parent_fits or not child_fits:
                    raise FileNotFoundError("parent_or_child_output_fits")
                if _same_file(parent_fits, child_fits):
                    raise ValueError("parent_child_output_fits_identical")
                psf = _psf_path(child, parent)
                if not psf:
                    raise FileNotFoundError("psf")

                def calculate(path: str) -> dict[str, Any]:
                    key = (path, psf, method)
                    if key not in cache:
                        cache[key] = compute_effective_bic_from_files(path, psf, method=method)
                    return cache[key]

                parent_metrics = calculate(parent_fits)
                child_metrics = calculate(child_fits)
                delta = float(parent_metrics["bic_effective"] - child_metrics["bic_effective"])
                rows.append({
                    "galaxy_id": galaxy_id,
                    "physical_id": _to_physical_id(galaxy_id),
                    "node_id": child.get("node_id"),
                    "parent_id": child.get("parent_id"),
                    "depth": child.get("depth"),
                    "action_type": (child.get("action_from_parent") or {}).get("coarse_label", "unknown"),
                    "vlm_improvement": int(vlm["improvement"]),
                    "eff_bic_prediction": int(delta > 0.0),
                    "delta_eff_bic_parent_minus_child": delta,
                    "parent_eff_bic": parent_metrics["bic_effective"],
                    "child_eff_bic": child_metrics["bic_effective"],
                    "parent_bic_2d": parent_metrics["bic_2d"],
                    "child_bic_2d": child_metrics["bic_2d"],
                    "psf_area": child_metrics["psf_area"],
                    "psf_area_method": method,
                    "parent_output_fits": parent_fits,
                    "child_output_fits": child_fits,
                    "psf_fits": psf,
                })
            except Exception as exc:
                failures[f"{type(exc).__name__}: {exc}"] += 1
    return rows, failures, eligible, len(paths)


def _split(rows: list[dict[str, Any]], val_ratio: float, seed: int):
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[row["physical_id"]].append(row)
    ids = sorted(grouped)
    random.Random(seed).shuffle(ids)
    n_val = int(len(ids) * val_ratio)
    val_ids = set(ids[:n_val])
    return (
        [row for row in rows if row["physical_id"] in val_ids],
        [row for row in rows if row["physical_id"] not in val_ids],
    )


def _per_type(rows: list[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[row["action_type"]].append(row)
    return {key: _binary_metrics(value) for key, value in sorted(grouped.items())}


def _score_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"n": 0}
    values = np.asarray([row["delta_eff_bic_parent_minus_child"] for row in rows])
    return {
        "n": len(values),
        "mean": float(values.mean()),
        "std": float(values.std()),
        "min": float(values.min()),
        "p5": float(np.percentile(values, 5)),
        "median": float(np.median(values)),
        "p95": float(np.percentile(values, 95)),
        "max": float(values.max()),
    }


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument(
        "--psf-area-method",
        choices=("noise_equivalent", "gaussian_fwhm"),
        default="noise_equivalent",
    )
    parser.add_argument("--val-ratio", type=float, default=0.7)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    rows, failures, eligible, trajectory_files = build_rows(
        args.input_dir, args.psf_area_method
    )
    val_rows, test_rows = _split(rows, args.val_ratio, args.seed)
    report = {
        "definition": "good iff BIC_eff(child) < BIC_eff(parent)",
        "psf_area_method": args.psf_area_method,
        "trajectory_files": trajectory_files,
        "eligible_pairs": eligible,
        "scored_pairs": len(rows),
        "coverage": _safe_div(len(rows), eligible),
        "failures": dict(failures),
        "val": {
            "metrics": _binary_metrics(val_rows),
            "per_type": _per_type(val_rows),
            "delta_eff_bic": _score_summary(val_rows),
        },
        "test": {
            "metrics": _binary_metrics(test_rows),
            "per_type": _per_type(test_rows),
            "delta_eff_bic": _score_summary(test_rows),
        },
    }
    output = Path(args.out_dir)
    output.mkdir(parents=True, exist_ok=True)
    _write_json(output / "eff_bic_alignment_report.json", report)
    _write_jsonl(output / "eff_bic_pairs.jsonl", rows)
    _write_jsonl(
        output / "eff_bic_disagreements.jsonl",
        [row for row in rows if row["eff_bic_prediction"] != row["vlm_improvement"]],
    )

    print("=" * 60)
    print("Effective BIC alignment")
    print("=" * 60)
    print(f"method: {args.psf_area_method}")
    print(f"trajectory files: {trajectory_files}")
    print(f"coverage: {len(rows)}/{eligible} ({report['coverage']:.1%})")
    print(f"failures: {dict(failures)}")
    for name, subset in (("VAL", report["val"]), ("TEST", report["test"])):
        metrics = subset["metrics"]
        print(
            f"{name}: n={metrics['n']} acc={metrics['accuracy']:.1%} "
            f"P={metrics['precision']:.1%} R={metrics['recall']:.1%} "
            f"F1={metrics['f1']:.1%} TP={metrics['tp']} FP={metrics['fp']} "
            f"TN={metrics['tn']} FN={metrics['fn']}"
        )
        for action, action_metrics in subset["per_type"].items():
            print(
                f"  {action}: n={action_metrics['n']} "
                f"acc={action_metrics['accuracy']:.1%} "
                f"F1={action_metrics['f1']:.1%}"
            )
    print(f"output: {output}")


if __name__ == "__main__":
    main()
