"""Audit whether newer rule rewards cover GRPO rule-only failure examples.

This is an offline replay over saved step evaluation outputs.  It does not run
model inference, GALFIT, or VLM evaluation.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Mapping

from data_gen.extract_training_data import load_trajectories
from eval.evaluate_action import parse_json_spec
from eval.validate_reward_alignment import compute_rule_reward_for_pair, extract_step_pairs


def _read_jsonl(path: str | Path) -> list[dict[str, Any]]:
    rows = []
    with Path(path).open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"{path}:{line_number} must be a JSON object")
            rows.append(value)
    return rows


def _write_jsonl(path: str | Path, rows: Iterable[Mapping[str, Any]]) -> None:
    with Path(path).open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(dict(row), ensure_ascii=False) + "\n")


def _key(row: Mapping[str, Any]) -> tuple[str, str]:
    return str(row.get("galaxy_id")), str(row.get("node_id"))


def _binary_metrics(rows: list[Mapping[str, Any]], field: str) -> dict[str, Any]:
    tp = fp = tn = fn = 0
    for row in rows:
        pred = int(row[field])
        label = int(row["vlm_improvement"])
        if pred and label:
            tp += 1
        elif pred:
            fp += 1
        elif label:
            fn += 1
        else:
            tn += 1
    n = tp + fp + tn + fn
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    return {
        "n": n,
        "accuracy": (tp + tn) / n if n else None,
        "precision": precision,
        "recall": recall,
        "f1": 2 * precision * recall / (precision + recall) if precision + recall else 0.0,
        "tp": tp,
        "fp": fp,
        "tn": tn,
        "fn": fn,
    }


def _find_summary(work_root: Path, galaxy_id: str, node_id: str) -> Path | None:
    node_dir = work_root / galaxy_id / node_id
    candidates = list(node_dir.rglob("*summary.md")) if node_dir.is_dir() else []
    if not candidates:
        return None
    return max(candidates, key=lambda path: (path.stat().st_mtime_ns, str(path)))


def build_audit(
    trajectory_dir: str,
    predictions_path: str,
    details_path: str,
    work_root: str,
    threshold: float,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    pairs = extract_step_pairs(load_trajectories(trajectory_dir))
    pair_map = {_key(pair): pair for pair in pairs}
    prediction_map = {_key(row): row for row in _read_jsonl(predictions_path)}
    details = _read_jsonl(details_path)
    root = Path(work_root)

    output = []
    failures = Counter()
    for detail in details:
        if detail.get("galfit_status") != "success":
            continue
        key = _key(detail)
        pair = pair_map.get(key)
        prediction = prediction_map.get(key)
        if pair is None or prediction is None:
            failures["missing_pair_or_prediction"] += 1
            continue
        spec = prediction.get("pred_spec")
        if not isinstance(spec, dict):
            spec = parse_json_spec(str(prediction.get("prediction") or ""))
        if not isinstance(spec, dict):
            failures["invalid_prediction_spec"] += 1
            continue
        summary_path = _find_summary(root, key[0], key[1])
        if summary_path is None:
            failures["missing_summary"] += 1
            continue

        replay_pair = {
            **pair,
            "action_spec": spec,
            "child_metrics": dict(detail.get("model_metrics") or {}),
            "summary_path": str(summary_path),
        }
        try:
            v11 = compute_rule_reward_for_pair(replay_pair, reward_version="v11")
            v124 = compute_rule_reward_for_pair(replay_pair, reward_version="v12.4")
        except Exception as exc:
            failures[f"{type(exc).__name__}: {exc}"] += 1
            continue

        label = int(bool(detail.get("vlm_improvement")))
        row = {
            "galaxy_id": key[0],
            "node_id": key[1],
            "vlm_improvement": label,
            "summary_path": str(summary_path),
            "v11_reward": float(v11["reward"]),
            "v11_binary": int(float(v11["reward"]) > threshold),
            "v12_4_reward": float(v124["reward"]),
            "v12_4_binary": int(float(v124["reward"]) > threshold),
            # The calibrated V12.5 degeneracy residual gate threshold is 0;
            # therefore its final decision reward is currently V12.4.
            "v12_5_reward": float(v124["reward"]),
            "v12_5_binary": int(float(v124["reward"]) > threshold),
            "structure_vetoed": bool(v124.get("structure_vetoed", False)),
            "structure_violations": list(v124.get("structure_violations", [])),
            "structure_warnings": list(v124.get("structure_warnings", [])),
        }
        output.append(row)

    baseline_rule_only = [
        row for row in output
        if row["v11_binary"] == 1 and row["vlm_improvement"] == 0
    ]
    report = {
        "inputs": {
            "trajectory_dir": trajectory_dir,
            "predictions": predictions_path,
            "details": details_path,
            "work_root": work_root,
        },
        "threshold": threshold,
        "detail_rows": len(details),
        "comparable_success_rows": len(output),
        "failures": dict(failures),
        "versions": {
            "v11": _binary_metrics(output, "v11_binary"),
            "v12.4": _binary_metrics(output, "v12_4_binary"),
            "v12.5": _binary_metrics(output, "v12_5_binary"),
        },
        "v11_rule_only_count": len(baseline_rule_only),
        "v11_rule_only_keys": [
            [row["galaxy_id"], row["node_id"]] for row in baseline_rule_only
        ],
        "coverage": {},
    }
    for version, field in (("v12.4", "v12_4_binary"), ("v12.5", "v12_5_binary")):
        corrected = [row for row in baseline_rule_only if row[field] == 0]
        newly_rejected_positive = [
            row for row in output
            if row["v11_binary"] == 1
            and row["vlm_improvement"] == 1
            and row[field] == 0
        ]
        report["coverage"][version] = {
            "corrected_rule_only": len(corrected),
            "coverage_rate": len(corrected) / len(baseline_rule_only) if baseline_rule_only else None,
            "remaining_rule_only": len(baseline_rule_only) - len(corrected),
            "newly_rejected_vlm_positive": len(newly_rejected_positive),
            "corrected_keys": [[row["galaxy_id"], row["node_id"]] for row in corrected],
        }
    return report, output


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trajectory-dir", required=True)
    parser.add_argument("--predictions", required=True)
    parser.add_argument("--exec-details", required=True)
    parser.add_argument("--work-root", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--threshold", type=float, default=0.05139489475137804)
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=False)
    report, rows = build_audit(
        args.trajectory_dir,
        args.predictions,
        args.exec_details,
        args.work_root,
        args.threshold,
    )
    _write_jsonl(out_dir / "details.jsonl", rows)
    with (out_dir / "report.json").open("w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"output: {out_dir}")


if __name__ == "__main__":
    main()
