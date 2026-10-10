"""Compare each fixed-horizon rollout state with the expert final state."""

import argparse
import hashlib
import json
import os
from collections import Counter, defaultdict
from pathlib import Path

from eval.expert_benchmark import read_jsonl, write_jsonl


def _sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _task_ids_sha256(rows):
    payload = "\n".join(sorted(str(row["task_id"]) for row in rows)) + "\n"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _binary_improvement(result):
    value = result.get("final_improvement")
    if value is None:
        value = result.get("improvement")
    if value not in (0, 1, "0", "1"):
        raise ValueError(f"Legacy comparator returned invalid improvement: {value!r}")
    return int(value)


def _compare_legacy_bidirectional(
    compare, *, model_image, expert_image, model_summary, expert_summary,
    model_name, api_key,
):
    """Original directional prompt, with expert→model defined as forward.

    (1,0) means the model beats the expert; (0,1) means the expert wins.
    The binary prompt cannot establish a genuine tie, so (0,0) is not success.
    """
    forward = compare(
        prev_residual_image_path=str(expert_image),
        next_residual_image_path=str(model_image),
        prev_summary_path=str(expert_summary),
        new_summary_path=str(model_summary),
        model_name=model_name,
        api_key=api_key,
    )
    reverse = compare(
        prev_residual_image_path=str(model_image),
        next_residual_image_path=str(expert_image),
        prev_summary_path=str(model_summary),
        new_summary_path=str(expert_summary),
        model_name=model_name,
        api_key=api_key,
    )
    pair = (_binary_improvement(forward), _binary_improvement(reverse))
    outcome = {
        (1, 0): "model_better",
        (0, 1): "expert_better",
        (0, 0): "neither_improves",
        (1, 1): "both_improve_conflict",
    }[pair]
    return {
        "combination": f"({pair[0]},{pair[1]})",
        "outcome": outcome,
        "success": pair == (1, 0),
        "strict_directional": pair in ((1, 0), (0, 1)),
        "comparison": {
            "forward_expert_to_model": forward,
            "reverse_model_to_expert": reverse,
        },
    }


def _summarize(rows, task_count, max_steps):
    by_step = defaultdict(list)
    for row in rows:
        by_step[int(row["step"])].append(row)
    report = {}
    for step in range(1, max_steps + 1):
        selected = by_step.get(step, [])
        outcomes = Counter(row["outcome"] for row in selected)
        robust = [row for row in selected if row.get("robust")]
        successes = [
            row for row in robust
            if row.get("verdict") in {"A_better", "tie"}
        ]
        report[str(step)] = {
            "tasks": task_count,
            "available_model_states": sum(row["outcome"] != "missing_state" for row in selected),
            "robust_comparisons": len(robust),
            "successes": len(successes),
            "task_success_at_k": len(successes) / task_count,
            "conditional_success_on_robust_comparison": (
                len(successes) / len(robust) if robust else None
            ),
            "outcomes": dict(outcomes),
        }
    return report


def _summarize_legacy(rows, task_count, max_steps):
    by_step = defaultdict(list)
    for row in rows:
        by_step[int(row["step"])].append(row)
    report = {}
    for step in range(1, max_steps + 1):
        selected = by_step.get(step, [])
        evaluated = [row for row in selected if row.get("combination")]
        successes = sum(row.get("success") is True for row in selected)
        report[str(step)] = {
            "tasks": task_count,
            "available_model_states": sum(
                row["outcome"] != "missing_state" for row in selected
            ),
            "evaluated_bidirectional_pairs": len(evaluated),
            "strict_directional_pairs": sum(
                row.get("strict_directional") is True for row in evaluated
            ),
            "successes": successes,
            "task_success_at_k": successes / task_count,
            "conditional_success_on_evaluated_pairs": (
                successes / len(evaluated) if evaluated else None
            ),
            "combination_counts": dict(Counter(
                row["combination"] for row in evaluated
            )),
            "outcomes": dict(Counter(row["outcome"] for row in selected)),
        }
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rollout-dir", required=True)
    parser.add_argument("--task-manifest", required=True)
    parser.add_argument("--split", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--vlm-model", default="gemini-3.1-pro-preview")
    parser.add_argument(
        "--judge", choices=("symmetric_v1", "legacy_bidirectional_v1"),
        default="symmetric_v1",
        help="Use a new output directory when changing judge or success policy",
    )
    parser.add_argument("--max-tasks", type=int, default=0, help="Smoke test only")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()

    if args.judge == "legacy_bidirectional_v1":
        from data_gen.reward import calculate_reward_model_with_param as compare
    else:
        from data_gen.reward import compare_two_fits_symmetric as compare

    api_key = os.environ.get("VLM_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("Set VLM_API_KEY or OPENAI_API_KEY")
    split = json.loads(Path(args.split).read_text(encoding="utf-8"))
    rollout_dir = Path(args.rollout_dir).resolve()
    meta = json.loads((rollout_dir / "rollout_meta.json").read_text(encoding="utf-8"))
    if meta["benchmark_sha256"] != split["benchmark_sha256"]:
        raise ValueError("Rollout and benchmark hashes differ")
    if not meta.get("full_frozen_test") and not args.max_tasks:
        raise ValueError("Smoke rollout cannot produce a full report")

    tasks = list(read_jsonl(args.task_manifest))
    if len(tasks) != split["n_task_success_tasks"]:
        raise ValueError("Task manifest count disagrees with split")
    if args.max_tasks:
        tasks = tasks[:args.max_tasks]
    if meta.get("task_ids_sha256"):
        if _task_ids_sha256(tasks) != meta["task_ids_sha256"]:
            raise ValueError("Task identities differ between rollout and scoring manifest")
    elif _sha256(args.task_manifest) != meta["task_manifest_sha256"]:
        raise ValueError("Task execution manifest differs from rollout manifest")
    task_by_id = {row["task_id"]: row for row in tasks}
    rollouts = [
        row for row in read_jsonl(rollout_dir / "task_rollouts.jsonl")
        if row["task_id"] in task_by_id
    ]
    rollout_by_id = {row["task_id"]: row for row in rollouts}
    if len(rollout_by_id) != len(tasks):
        raise ValueError("Incomplete or duplicate rollout tasks")

    for task in tasks:
        for key in ("expert_final_image", "expert_final_summary"):
            path = Path(task[key])
            if not path.is_file():
                raise FileNotFoundError(path)
            if _sha256(path) != task["artifact_sha256"][key]:
                raise ValueError(f"Expert artifact changed: {task['task_id']} {key}")

    out = Path(args.out_dir)
    details_path = out / "success_at_k_details.jsonl"
    scoring_meta = {
        "judge": args.judge,
        "benchmark_sha256": split["benchmark_sha256"],
        "rollout_meta_sha256": _sha256(rollout_dir / "rollout_meta.json"),
        "task_manifest_sha256": _sha256(args.task_manifest),
        "vlm_model": args.vlm_model,
        "reward_source_sha256": _sha256(
            Path(__file__).resolve().parents[1] / "data_gen" / "reward.py"
        ),
    }
    if out.exists() and not args.resume:
        raise FileExistsError(f"Refusing to overwrite without --resume: {out}")
    if out.exists():
        meta_path = out / "scoring_meta.json"
        if not meta_path.is_file() and args.judge != "symmetric_v1":
            raise ValueError(f"Cannot verify existing scoring policy: {out}")
        if meta_path.is_file() and json.loads(
            meta_path.read_text(encoding="utf-8")
        ) != scoring_meta:
            raise ValueError(f"Scoring policy or inputs changed: {out}")
    out.mkdir(parents=True, exist_ok=True)
    (out / "scoring_meta.json").write_text(
        json.dumps(scoring_meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    results = list(read_jsonl(details_path)) if details_path.is_file() else []
    done = {(row["task_id"], int(row["step"])) for row in results}

    max_steps = int(meta["max_steps"])
    total = len(tasks) * max_steps
    for task in tasks:
        rollout = rollout_by_id[task["task_id"]]
        steps = {int(row["step"]): row for row in rollout.get("steps", [])}
        for step in range(1, max_steps + 1):
            key = (task["task_id"], step)
            if key in done:
                continue
            state = steps.get(step)
            row = {
                "task_id": task["task_id"],
                "galaxy_id": task["galaxy_id"],
                "step": step,
                "verdict": None,
                "robust": False,
                "judge": args.judge,
                "success": False,
            }
            if not state or state.get("status") != "success":
                row["outcome"] = "missing_state"
            else:
                model_image = rollout_dir / state["image_relpath"]
                model_summary = rollout_dir / state["summary_relpath"]
                if args.judge == "legacy_bidirectional_v1":
                    row.update(_compare_legacy_bidirectional(
                        compare,
                        model_image=model_image,
                        expert_image=task["expert_final_image"],
                        model_summary=model_summary,
                        expert_summary=task["expert_final_summary"],
                        model_name=args.vlm_model,
                        api_key=api_key,
                    ))
                else:
                    comparison = compare(
                        image_a_path=str(model_image),
                        image_b_path=task["expert_final_image"],
                        summary_a_path=str(model_summary),
                        summary_b_path=task["expert_final_summary"],
                        model_name=args.vlm_model,
                        api_key=api_key,
                    )
                    row.update({
                        "verdict": comparison["verdict"],
                        "robust": bool(comparison["robust"]),
                        "outcome": (
                            comparison["verdict"]
                            if comparison["robust"]
                            else "position_inconsistent"
                        ),
                        "comparison": comparison,
                        "success": bool(comparison["robust"]) and
                        comparison["verdict"] in {"A_better", "tie"},
                    })
            results.append(row)
            done.add(key)
            write_jsonl(details_path, results)
            print(
                f"[{len(done)}/{total}] {task['task_id']} K={step} "
                f"outcome={row['outcome']}",
                flush=True,
            )

    report = {
        "benchmark_sha256": split["benchmark_sha256"],
        "model_path": meta["model_path"],
        "adapter_path": meta["adapter_path"],
        "tasks": len(tasks),
        "max_steps": max_steps,
        "judge": args.judge,
        "success_definition": (
            "At exactly K actions, original directional comparator returns "
            "(1,0) for expert-final→model and model→expert; ties do not count."
            if args.judge == "legacy_bidirectional_v1" else
            "At exactly K actions, a robust symmetric comparison judges the "
            "model state A_better than or tied with the expert final state."
        ),
        "inconsistent_position_judgments_count_as_success": False,
        "success_at_k": (
            _summarize_legacy(results, len(tasks), max_steps)
            if args.judge == "legacy_bidirectional_v1" else
            _summarize(results, len(tasks), max_steps)
        ),
        "full_frozen_test": not bool(args.max_tasks),
    }
    (out / "success_at_k_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
