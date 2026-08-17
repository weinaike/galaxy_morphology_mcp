"""Audit V12.5 residual scores on grouped, VLM-labelled on-policy rollouts."""

from __future__ import annotations

import argparse
import json
import os
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

from eval.calibrate_residual_v12_5 import (
    _resolve_output_fits,
    _same_file,
    _sigma_mask,
)
from eval.residual_delta_features import (
    FEATURE_NAMES,
    compute_residual_badness_features,
    compute_residual_feature_deltas,
    load_residual_inputs,
)


def _read_jsonl(path):
    with open(path, encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _candidate_node(row):
    return {
        "summary_path": row.get("model_summary_path") or row.get("summary_path"),
        "feedme_path": row.get("model_feedme_path") or row.get("feedme_path"),
        "output_fits_file": row.get("model_output_fits_path")
        or row.get("output_fits_file"),
        "optimized_fits_file": row.get("optimized_fits_file"),
    }


def _label(row, name):
    detail = row.get("vlm_detail") or {}
    if name == "residual":
        value = detail.get("residual_improved")
        if value is None:
            value = row.get("residual_improved")
    else:
        value = row.get("vlm_improvement")
        if value is None:
            value = detail.get("improvement")
    return None if value is None else int(bool(value))


def _score(delta, model):
    x = np.asarray([delta[name] for name in FEATURE_NAMES], dtype=float)
    mean = np.asarray(model["mean"], dtype=float)
    std = np.asarray(model["std"], dtype=float)
    weights = np.asarray(model["weights"], dtype=float)
    z = np.clip((x - mean) / std, -10.0, 10.0)
    logit = float(z @ weights + float(model["intercept"]))
    return float(1.0 / (1.0 + np.exp(-np.clip(logit, -30.0, 30.0))))


def _group_metrics(rows, label_name, threshold):
    groups = defaultdict(list)
    for row in rows:
        label = row[f"{label_name}_label"]
        if label is not None:
            groups[row["group_id"]].append(row)

    complete = sum(len(group) == 8 for group in groups.values())
    mixed = []
    pairwise = []
    macro_pairwise = []
    top1 = []

    for group in groups.values():
        positives = [row for row in group if row[f"{label_name}_label"] == 1]
        negatives = [row for row in group if row[f"{label_name}_label"] == 0]
        if not positives or not negatives:
            continue
        mixed.append(group)
        local = []
        for positive in positives:
            for negative in negatives:
                left = positive["residual_score"]
                right = negative["residual_score"]
                value = 1.0 if left > right else (0.5 if left == right else 0.0)
                local.append(value)
                pairwise.append(value)
        macro_pairwise.append(float(np.mean(local)))
        maximum = max(row["residual_score"] for row in group)
        tied = [row for row in group if row["residual_score"] == maximum]
        top1.append(float(np.mean([row[f"{label_name}_label"] for row in tied])))

    selected = [row for row in rows if row["residual_score"] >= threshold]
    selected_groups = {row["group_id"] for row in selected}
    selected_labels = [
        row[f"{label_name}_label"]
        for row in selected
        if row[f"{label_name}_label"] is not None
    ]

    return {
        "groups": len(groups),
        "complete_groups": complete,
        "mixed_groups": len(mixed),
        "pairwise_ranking_accuracy": (
            float(np.mean(pairwise)) if pairwise else None
        ),
        "macro_group_ranking_accuracy": (
            float(np.mean(macro_pairwise)) if macro_pairwise else None
        ),
        "top1_positive_rate": float(np.mean(top1)) if top1 else None,
        "high_confidence_candidates": len(selected),
        "high_confidence_precision": (
            float(np.mean(selected_labels)) if selected_labels else None
        ),
        "high_confidence_group_coverage": len(selected_groups) / max(len(groups), 1),
    }


def _print_metrics(title, metrics):
    print(f"\n========== {title} ==========")
    print("groups:", metrics["groups"])
    print("complete groups:", metrics["complete_groups"])
    print("mixed groups:", metrics["mixed_groups"])
    for key in (
        "pairwise_ranking_accuracy",
        "macro_group_ranking_accuracy",
        "top1_positive_rate",
        "high_confidence_precision",
        "high_confidence_group_coverage",
    ):
        value = metrics[key]
        print(key + ":", "N/A" if value is None else f"{value:.1%}")
    print("high_confidence_candidates:", metrics["high_confidence_candidates"])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--parents", required=True)
    parser.add_argument("--rollouts", required=True)
    parser.add_argument("--model-config", required=True)
    parser.add_argument("--out-dir", required=True)
    args = parser.parse_args()

    parents = {
        row["group_id"]: row
        for row in _read_jsonl(args.parents)
    }
    rollouts = _read_jsonl(args.rollouts)
    model = json.loads(Path(args.model_config).read_text(encoding="utf-8"))
    threshold = float(model["threshold"])
    cache = {}
    scored = []
    failures = Counter()

    for row in rollouts:
        try:
            parent = parents[row["group_id"]]
            child = _candidate_node(row)
            parent_fits = _resolve_output_fits(parent)
            child_fits = _resolve_output_fits(child)
            if not parent_fits or not child_fits:
                raise FileNotFoundError("parent_or_child_output_fits")
            if _same_file(parent_fits, child_fits):
                raise ValueError("parent_child_output_fits_identical")
            sigma_path, mask_path = _sigma_mask(child, parent)
            if not sigma_path or not mask_path:
                raise FileNotFoundError("sigma_or_mask")

            def features(fits_path):
                key = (fits_path, sigma_path, mask_path)
                if key not in cache:
                    arrays = load_residual_inputs(fits_path, sigma_path, mask_path)
                    cache[key] = compute_residual_badness_features(*arrays)
                return cache[key]

            delta = compute_residual_feature_deltas(
                features(parent_fits),
                features(child_fits),
            )
            output = dict(row)
            output["parent_output_fits"] = parent_fits
            output["child_output_fits"] = child_fits
            output["residual_deltas"] = delta
            output["residual_score"] = _score(delta, model)
            output["residual_label"] = _label(row, "residual")
            output["overall_label"] = _label(row, "overall")
            scored.append(output)
        except Exception as exc:
            failures[f"{type(exc).__name__}: {exc}"] += 1

    print("========== COVERAGE ==========")
    print("rollouts:", len(rollouts))
    print("scored:", len(scored))
    print("coverage:", len(scored) / max(len(rollouts), 1))
    print("failures:", dict(failures))
    print("model threshold:", threshold)

    residual_metrics = _group_metrics(scored, "residual", threshold)
    overall_metrics = _group_metrics(scored, "overall", threshold)
    _print_metrics("VLM RESIDUAL_IMPROVED", residual_metrics)
    _print_metrics("VLM OVERALL IMPROVEMENT", overall_metrics)

    os.makedirs(args.out_dir, exist_ok=True)
    output_path = os.path.join(args.out_dir, "onpolicy_residual_scores.jsonl")
    with open(output_path, "w", encoding="utf-8") as handle:
        for row in scored:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    report = {
        "rollouts": len(rollouts),
        "scored": len(scored),
        "coverage": len(scored) / max(len(rollouts), 1),
        "failures": dict(failures),
        "threshold": threshold,
        "residual_metrics": residual_metrics,
        "overall_metrics": overall_metrics,
    }
    Path(os.path.join(args.out_dir, "onpolicy_residual_report.json")).write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print("\noutput:", args.out_dir)


if __name__ == "__main__":
    main()
