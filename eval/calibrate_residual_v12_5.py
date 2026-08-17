"""Calibrate V12.5 parent/child residual features on Val and lock on Test."""

from __future__ import annotations

import argparse
import glob
import json
import math
import os
import random
import re
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

from data_gen.dataset_utils import _to_physical_id
from eval.residual_delta_features import (
    FEATURE_NAMES,
    compute_residual_badness_features,
    compute_residual_feature_deltas,
    load_residual_inputs,
)
from eval.reward_for_rl_v12_2 import check_targeted_fitted_structure
from eval.reward_for_rl_v12_4 import compute_rl_reward_v12_4
from eval.validate_reward_alignment import _parse_fitted_components


def _existing(path):
    if not path:
        return None
    path = os.path.abspath(os.path.expanduser(str(path)))
    return path if os.path.isfile(path) else None


def _resolve_from(base_dir, value):
    if not value or str(value).strip().lower() in {"none", "null"}:
        return None
    value = str(value).strip().strip("`\"'")
    candidates = [value]
    if not os.path.isabs(value):
        candidates.insert(0, os.path.join(base_dir, value))
    marker = value.find("/mnt/")
    if marker >= 0:
        candidates.append(value[marker:])
    for candidate in candidates:
        found = _existing(candidate)
        if found:
            return found
    return None


def _control_paths(feedme_path):
    result = {}
    if not _existing(feedme_path):
        return result
    base = os.path.dirname(os.path.abspath(feedme_path))
    with open(feedme_path, encoding="utf-8", errors="replace") as handle:
        for line in handle:
            match = re.match(r"^\s*([ABCDFG])\)\s+([^#\s]+)", line)
            if match:
                result[match.group(1)] = _resolve_from(base, match.group(2))
    return result


def _summary_output_reference(summary_path):
    if not _existing(summary_path):
        return None
    text = Path(summary_path).read_text(encoding="utf-8", errors="replace")
    patterns = (
        r"\*\*Output File:\*\*\s*(.+)$",
        r"^Output File:\s*(.+)$",
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.MULTILINE)
        if match:
            return (
                match.group(1).strip().strip(chr(96)).strip('"').strip("'")
            )
    return None


def _looks_like_galfit_output(path):
    try:
        from astropy.io import fits
        with fits.open(path, memmap=False) as hdul:
            return len(hdul) > 3 and hdul[3].data is not None
    except Exception:
        return False


def _local_output_candidate(owner_path, output_reference):
    """Resolve the archived GALFIT output beside its owning node artifact."""

    owner_path = _existing(owner_path)
    if not owner_path or not output_reference:
        return None
    candidate = os.path.join(
        os.path.dirname(owner_path), os.path.basename(str(output_reference))
    )
    candidate = _existing(candidate)
    return candidate if candidate and _looks_like_galfit_output(candidate) else None


def _control_reference(feedme_path, key):
    if not _existing(feedme_path):
        return None
    with open(feedme_path, encoding="utf-8", errors="replace") as handle:
        for line in handle:
            match = re.match(r"^\s*([ABCDFG])\)\s+([^#\s]+)", line)
            if match and match.group(1) == key:
                return (
                    match.group(2).strip().strip(chr(96)).strip('"').strip("'")
                )
    return None


def _resolve_output_fits(node):
    # Newer trajectories may store the exact archived output explicitly.
    for key in ("output_fits_file", "optimized_fits_file", "model_output_fits_path"):
        found = _existing(node.get(key))
        if found and _looks_like_galfit_output(found):
            return found

    # Historical trajectories store only summary/feedme paths. run_galfit
    # archives the summary, feedme and output FITS together. Resolve beside the
    # node artifact before considering the stale pre-archive path it contains.
    summary_path = node.get("summary_path")
    summary_reference = _summary_output_reference(summary_path)
    found = _local_output_candidate(summary_path, summary_reference)
    if found:
        return found

    feedme_path = node.get("feedme_path")
    output_reference = _control_reference(feedme_path, "B")
    found = _local_output_candidate(feedme_path, output_reference)
    if found:
        return found

    # Do not recursively choose the newest FITS. That dangerous fallback bound
    # many unrelated nodes to one file and silently made all deltas zero.
    for owner_path, reference in (
        (summary_path, summary_reference),
        (feedme_path, output_reference),
    ):
        owner = _existing(owner_path)
        if owner:
            found = _resolve_from(os.path.dirname(owner), reference)
            if found and _looks_like_galfit_output(found):
                return found
    return None


def _same_file(left, right):
    if not left or not right:
        return False
    try:
        return os.path.samefile(left, right)
    except OSError:
        return os.path.normcase(os.path.realpath(left)) == os.path.normcase(
            os.path.realpath(right)
        )


def _sigma_mask(node, fallback_node):
    for source in (node, fallback_node):
        controls = _control_paths(source.get("feedme_path"))
        sigma, mask = controls.get("C"), controls.get("F")
        if sigma and mask:
            return sigma, mask
    return None, None


def _load_trees(input_dir):
    paths = sorted(glob.glob(os.path.join(input_dir, "**", "*_trajectory.json"), recursive=True))
    trees = []
    for path in paths:
        try:
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
            if isinstance(payload, list):
                trees.extend(payload)
            else:
                trees.append(payload)
        except Exception as exc:
            print(f"skip trajectory {path}: {type(exc).__name__}: {exc}")
    return paths, trees


def _build_rows(input_dir):
    paths, trees = _load_trees(input_dir)
    feature_cache = {}
    rows, failures = [], Counter()
    total = 0
    for tree_index, tree in enumerate(trees, 1):
        galaxy_id = tree.get("galaxy_id", "unknown")
        node_map = {node.get("node_id"): node for node in tree.get("nodes", [])}
        for child in tree.get("nodes", []):
            parent = node_map.get(child.get("parent_id"))
            detail = (child.get("reward_detail") or {}).get("vlm_detail") or {}
            if parent is None or "improvement" not in detail or "residual_improved" not in detail:
                continue
            total += 1
            try:
                parent_fits = _resolve_output_fits(parent)
                child_fits = _resolve_output_fits(child)
                if not parent_fits or not child_fits:
                    raise FileNotFoundError("parent_or_child_output_fits")
                if _same_file(parent_fits, child_fits):
                    raise ValueError(
                        "parent_child_output_fits_identical: "
                        f"parent={parent_fits}, child={child_fits}"
                    )
                sigma_path, mask_path = _sigma_mask(child, parent)
                if not sigma_path or not mask_path:
                    raise FileNotFoundError("sigma_or_mask")

                def features(path):
                    key = (path, sigma_path, mask_path)
                    if key not in feature_cache:
                        arrays = load_residual_inputs(path, sigma_path, mask_path)
                        feature_cache[key] = compute_residual_badness_features(*arrays)
                    return feature_cache[key]

                parent_features = features(parent_fits)
                child_features = features(child_fits)
                deltas = compute_residual_feature_deltas(parent_features, child_features)
                spec = (child.get("action_from_parent") or {}).get("spec") or {}
                fitted = _parse_fitted_components(child.get("summary_path"))
                _, findings, _, _ = check_targeted_fitted_structure(spec, fitted)
                base = compute_rl_reward_v12_4(
                    old_metrics=parent.get("metrics") or {},
                    new_metrics=child.get("metrics") or {},
                    action_spec=spec,
                    fitted_components=fitted,
                )
                rows.append({
                    "galaxy_id": galaxy_id,
                    "physical_id": _to_physical_id(galaxy_id),
                    "node_id": child.get("node_id"),
                    "parent_id": child.get("parent_id"),
                    "action_type": (child.get("action_from_parent") or {}).get("coarse_label"),
                    "vlm_improvement": int(detail.get("improvement", 0)),
                    "vlm_residual_improved": int(bool(detail.get("residual_improved"))),
                    "v12_4_reward": float(base["reward"]),
                    "disk_like_degeneracy": any(
                        finding.startswith("disk_like_component_degeneracy:") for finding in findings
                    ),
                    "parent_output_fits": parent_fits,
                    "child_output_fits": child_fits,
                    "residual_deltas": deltas,
                })
            except Exception as exc:
                failures[f"{type(exc).__name__}: {exc}"] += 1
        if tree_index % 20 == 0 or tree_index == len(trees):
            print(f"progress: {tree_index}/{len(trees)}, scored={len(rows)}", flush=True)
    return paths, rows, failures, total


def _split(rows, val_ratio, seed):
    ids = sorted({row["physical_id"] for row in rows})
    rng = random.Random(seed)
    rng.shuffle(ids)
    cut = int(len(ids) * val_ratio)
    val_ids = set(ids[:cut])
    return (
        [row for row in rows if row["physical_id"] in val_ids],
        [row for row in rows if row["physical_id"] not in val_ids],
    )


def _matrix(rows):
    return np.asarray([[row["residual_deltas"][name] for name in FEATURE_NAMES] for row in rows]), np.asarray(
        [row["vlm_residual_improved"] for row in rows], dtype=float
    )


def _sigmoid(value):
    value = np.clip(value, -30.0, 30.0)
    return 1.0 / (1.0 + np.exp(-value))


def _fit_nonnegative_logistic(x, y, steps=5000, learning_rate=0.03, l2=0.05):
    mean = np.mean(x, axis=0)
    std = np.std(x, axis=0)
    std[std < 1e-8] = 1.0
    z = np.clip((x - mean) / std, -10.0, 10.0)
    weights = np.zeros(z.shape[1], dtype=float)
    positive_rate = float(np.mean(y))
    intercept = math.log(max(positive_rate, 1e-6) / max(1.0 - positive_rate, 1e-6))
    class_weight = np.where(y > 0.5, 0.5 / max(positive_rate, 1e-6), 0.5 / max(1.0 - positive_rate, 1e-6))
    for _ in range(steps):
        probability = _sigmoid(z @ weights + intercept)
        error = (probability - y) * class_weight
        weights -= learning_rate * ((z.T @ error) / len(y) + l2 * weights)
        weights = np.maximum(weights, 0.0)
        intercept -= learning_rate * float(np.mean(error))
    return mean, std, weights, intercept


def _predict(rows, model):
    x, _ = _matrix(rows)
    z = np.clip((x - model["mean"]) / model["std"], -10.0, 10.0)
    return _sigmoid(z @ model["weights"] + model["intercept"])


def _metrics(labels, predictions):
    labels = np.asarray(labels, dtype=int)
    predictions = np.asarray(predictions, dtype=int)
    tp = int(np.sum((labels == 1) & (predictions == 1)))
    fp = int(np.sum((labels == 0) & (predictions == 1)))
    tn = int(np.sum((labels == 0) & (predictions == 0)))
    fn = int(np.sum((labels == 1) & (predictions == 0)))
    precision = tp / max(tp + fp, 1)
    recall = tp / max(tp + fn, 1)
    return {
        "n": len(labels), "accuracy": (tp + tn) / max(len(labels), 1),
        "precision": precision, "recall": recall,
        "f1": 2 * precision * recall / max(precision + recall, 1e-12),
        "tp": tp, "fp": fp, "tn": tn, "fn": fn,
    }


def _choose_threshold(labels, scores, min_precision=0.85):
    candidates = np.unique(np.concatenate(([0.0, 1.0], scores)))
    best = None
    for threshold in candidates:
        metrics = _metrics(labels, scores >= threshold)
        if metrics["precision"] < min_precision:
            continue
        key = (metrics["f1"], metrics["recall"], metrics["accuracy"])
        if best is None or key > best[0]:
            best = (key, float(threshold), metrics)
    if best is None:
        for threshold in candidates:
            metrics = _metrics(labels, scores >= threshold)
            key = (metrics["precision"], metrics["f1"], metrics["recall"])
            if best is None or key > best[0]:
                best = (key, float(threshold), metrics)
    return best[1], best[2]


RULE_THRESHOLD = 0.05139489475137804


def _v12_5_metrics(rows, residual_scores, gate_threshold):
    labels = np.asarray([row["vlm_improvement"] for row in rows], dtype=int)
    base_positive = np.asarray(
        [row["v12_4_reward"] > RULE_THRESHOLD for row in rows], dtype=bool
    )
    gated = np.asarray(
        [
            row["disk_like_degeneracy"] and score < gate_threshold
            for row, score in zip(rows, residual_scores)
        ],
        dtype=bool,
    )
    return _metrics(labels, base_positive & ~gated)


def _choose_v12_5_gate(rows, residual_scores):
    baseline = _v12_5_metrics(rows, residual_scores, 0.0)
    candidates = [0.0]
    candidates.extend(
        float(score)
        for row, score in zip(rows, residual_scores)
        if row["disk_like_degeneracy"]
    )
    best = ((baseline["f1"], baseline["accuracy"], baseline["precision"], baseline["recall"]), 0.0, baseline)
    for threshold in sorted(set(candidates)):
        metrics = _v12_5_metrics(rows, residual_scores, threshold)
        if metrics["precision"] + 1e-12 < baseline["precision"]:
            continue
        key = (metrics["f1"], metrics["accuracy"], metrics["precision"], metrics["recall"])
        if key > best[0]:
            best = (key, threshold, metrics)
    return best[1], baseline, best[2]


def _print_metrics(title, metrics):
    print(
        f"{title}: n={metrics['n']} acc={metrics['accuracy']:.1%} "
        f"P={metrics['precision']:.1%} R={metrics['recall']:.1%} F1={metrics['f1']:.1%} "
        f"TP={metrics['tp']} FP={metrics['fp']} TN={metrics['tn']} FN={metrics['fn']}"
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--val-ratio", type=float, default=0.7)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--min-precision", type=float, default=0.85)
    args = parser.parse_args()

    os.makedirs(args.out_dir, exist_ok=True)
    paths, rows, failures, total = _build_rows(args.input_dir)
    print("\n========== COVERAGE ==========")
    print("trajectory files:", len(paths))
    print("total pairs:", total)
    print("scored pairs:", len(rows))
    print("coverage:", len(rows) / max(total, 1))
    print("failures:", dict(failures))
    if not rows:
        raise RuntimeError("no residual pairs were scored")

    val_rows, test_rows = _split(rows, args.val_ratio, args.seed)
    x_val, y_val = _matrix(val_rows)
    mean, std, weights, intercept = _fit_nonnegative_logistic(x_val, y_val)
    model = {"mean": mean, "std": std, "weights": weights, "intercept": intercept}
    val_scores = _predict(val_rows, model)
    test_scores = _predict(test_rows, model)
    threshold, val_metrics = _choose_threshold(y_val, val_scores, args.min_precision)
    test_labels = np.asarray([row["vlm_residual_improved"] for row in test_rows])
    test_metrics = _metrics(test_labels, test_scores >= threshold)
    gate_threshold, v12_4_val_metrics, v12_5_val_metrics = _choose_v12_5_gate(
        val_rows, val_scores
    )
    v12_4_test_metrics = _v12_5_metrics(test_rows, test_scores, 0.0)
    v12_5_test_metrics = _v12_5_metrics(test_rows, test_scores, gate_threshold)

    print("\n========== RESIDUAL MODEL ==========")
    for name, weight in sorted(zip(FEATURE_NAMES, weights), key=lambda item: item[1], reverse=True):
        print(f"{name}: weight={weight:.6f}")
    print("threshold:", threshold)
    _print_metrics("VAL", val_metrics)
    _print_metrics("TEST", test_metrics)

    for split_name, split_rows, split_scores in (
        ("VAL", val_rows, val_scores), ("TEST", test_rows, test_scores)
    ):
        indexes = [i for i, row in enumerate(split_rows) if row["disk_like_degeneracy"]]
        if indexes:
            labels = [split_rows[i]["vlm_residual_improved"] for i in indexes]
            metrics = _metrics(labels, split_scores[indexes] >= threshold)
            _print_metrics(f"{split_name} degeneracy", metrics)

    print("\n========== V12.5 FINAL LABEL ALIGNMENT ==========")
    print("rule threshold:", RULE_THRESHOLD)
    print("degeneracy residual gate threshold:", gate_threshold)
    _print_metrics("V12.4 VAL", v12_4_val_metrics)
    _print_metrics("V12.5 VAL", v12_5_val_metrics)
    _print_metrics("V12.4 TEST", v12_4_test_metrics)
    _print_metrics("V12.5 TEST", v12_5_test_metrics)

    for row, score in zip(val_rows, val_scores):
        row["residual_improvement_probability"] = float(score)
        row["split"] = "val"
    for row, score in zip(test_rows, test_scores):
        row["residual_improvement_probability"] = float(score)
        row["split"] = "test"

    output_rows = val_rows + test_rows
    with open(os.path.join(args.out_dir, "residual_pairs.jsonl"), "w", encoding="utf-8") as handle:
        for row in output_rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    config = {
        "feature_names": list(FEATURE_NAMES),
        "mean": mean.tolist(), "std": std.tolist(), "weights": weights.tolist(),
        "intercept": float(intercept), "threshold": float(threshold),
        "val_metrics": val_metrics, "test_metrics": test_metrics,
        "rule_threshold": RULE_THRESHOLD,
        "degeneracy_gate_threshold": float(gate_threshold),
        "v12_4_val_metrics": v12_4_val_metrics,
        "v12_5_val_metrics": v12_5_val_metrics,
        "v12_4_test_metrics": v12_4_test_metrics,
        "v12_5_test_metrics": v12_5_test_metrics,
        "val_ratio": args.val_ratio, "seed": args.seed,
        "coverage": len(rows) / max(total, 1), "failures": dict(failures),
    }
    Path(os.path.join(args.out_dir, "residual_model_config.json")).write_text(
        json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("\noutput:", args.out_dir)


if __name__ == "__main__":
    main()
