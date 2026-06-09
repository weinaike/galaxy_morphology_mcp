import os
import json
import time
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Optional, List


ROOT_DIR = "/media/zhongling/wyh/GalDecomp_Gen/test_image/done_pinsong/done"
TEST_REWARD_PY = "/media/zhongling/wyh/GalDecomp_Gen/data_gen/test_reward.py"
OUTPUT_JSON = "/media/zhongling/wyh/GalDecomp_Gen/test_image/done_pinsong/done/galfit_comparison_cutoff_test_result.json"

IMAGE_NAME = "galfit_comparison_cutoff.png"

TIMEOUT = 300
MAX_RETRIES = 2
RETRY_SLEEP = 5
MODEL_NAME = "gemini-3.1-pro-preview"


def load_json_safely(json_path: str) -> Optional[Dict[str, Any]]:
    if not os.path.exists(json_path):
        return None

    try:
        with open(json_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def get_pred_improve(result: Optional[Dict[str, Any]]) -> Optional[int]:
    if not isinstance(result, dict):
        return None

    if "improvement" in result:
        try:
            return int(result["improvement"])
        except Exception:
            pass

    judgement = str(result.get("reward_judgement", "")).lower().strip()

    if judgement in ["improved", "improve", "1", "true"]:
        return 1

    if judgement in ["not_improved", "not improve", "not_improve", "0", "false"]:
        return 0

    return None


def run_one_pair(
    prev_image: str,
    next_image: str,
    pair_output_json: str,
) -> Dict[str, Any]:
    cmd = [
        sys.executable,
        TEST_REWARD_PY,
        "--prev_image",
        prev_image,
        "--next_image",
        next_image,
        "--output_json",
        pair_output_json,
        "--model_name",
        MODEL_NAME,
    ]

    last_error = None

    for attempt in range(1, MAX_RETRIES + 2):
        try:
            completed = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=TIMEOUT,
            )

            result = load_json_safely(pair_output_json)
            pred_improve = get_pred_improve(result)

            if completed.returncode == 0 and result is not None and pred_improve is not None:
                return {
                    "status": "success",
                    "attempt": attempt,
                    "prediction": pred_improve,
                    "raw_result": result,
                    "stdout": completed.stdout[-2000:],
                    "stderr": completed.stderr[-2000:],
                }

            last_error = {
                "returncode": completed.returncode,
                "stdout": completed.stdout[-2000:],
                "stderr": completed.stderr[-2000:],
                "parsed_result": result,
                "parsed_prediction": pred_improve,
            }

        except subprocess.TimeoutExpired as e:
            last_error = {
                "error_type": "TimeoutExpired",
                "message": str(e),
            }

        except Exception as e:
            last_error = {
                "error_type": type(e).__name__,
                "message": repr(e),
            }

        if attempt <= MAX_RETRIES:
            print(f"      [Retry] attempt {attempt} failed, sleep {RETRY_SLEEP}s...")
            time.sleep(RETRY_SLEEP)

    return {
        "status": "failed",
        "prediction": None,
        "error": last_error,
    }


def collect_plate_dirs(root_dir: Path) -> List[Path]:
    return sorted(
        [p for p in root_dir.iterdir() if p.is_dir() and p.name.startswith("Plate")],
        key=lambda x: x.name,
    )


def collect_archive_dirs(plate_dir: Path) -> List[Path]:
    archives_dir = plate_dir / "archives"

    if not archives_dir.exists():
        return []

    return sorted(
        [p for p in archives_dir.iterdir() if p.is_dir()],
        key=lambda x: x.name,
    )


def calculate_metrics(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    valid_records = [
        r for r in records
        if r.get("status") == "success" and r.get("prediction") in [0, 1]
    ]

    failed_records = [
        r for r in records
        if r.get("status") != "success" or r.get("prediction") not in [0, 1]
    ]

    tp = sum(1 for r in valid_records if r["prediction"] == 1)
    fn = sum(1 for r in valid_records if r["prediction"] == 0)

    recall = tp / (tp + fn) if (tp + fn) > 0 else None

    return {
        "note": "All samples are assumed to have ground_truth_improve = 1.",
        "total_pairs": len(records),
        "valid_pairs": len(valid_records),
        "failed_pairs": len(failed_records),
        "true_positive": tp,
        "false_negative": fn,
        "recall": recall,
        "false_positive": None,
        "false_positive_rate": None,
        "false_positive_note": "Cannot compute false positives because there are no ground_truth_improve = 0 samples.",
    }


def save_results(records: List[Dict[str, Any]]):
    output_json = Path(OUTPUT_JSON)
    output_json.parent.mkdir(parents=True, exist_ok=True)

    metrics = calculate_metrics(records)

    output = {
        "root_dir": ROOT_DIR,
        "test_reward_py": TEST_REWARD_PY,
        "image_name": IMAGE_NAME,
        "metrics": metrics,
        "records": records,
    }

    with open(output_json, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    return metrics


def load_existing_records() -> List[Dict[str, Any]]:
    if not os.path.exists(OUTPUT_JSON):
        return []

    try:
        with open(OUTPUT_JSON, "r", encoding="utf-8") as f:
            data = json.load(f)

        if isinstance(data, dict) and isinstance(data.get("records"), list):
            return data["records"]

        if isinstance(data, list):
            return data

    except Exception as e:
        print(f"[Warning] Failed to load existing results: {e}")

    return []


def main():
    root_dir = Path(ROOT_DIR)

    if not root_dir.exists():
        raise FileNotFoundError(f"ROOT_DIR does not exist: {ROOT_DIR}")

    pair_output_dir = Path(OUTPUT_JSON).parent / "test_reward_pair_outputs"
    pair_output_dir.mkdir(parents=True, exist_ok=True)

    records = load_existing_records()

    processed_pairs = set()
    for item in records:
        key = (
            item.get("plate"),
            item.get("prev_archive"),
            item.get("next_archive"),
        )
        if all(key):
            processed_pairs.add(key)

    plate_dirs = collect_plate_dirs(root_dir)

    print(f"[Info] Root dir: {ROOT_DIR}")
    print(f"[Info] Found {len(plate_dirs)} Plate folders.")
    print(f"[Info] Existing processed pairs: {len(processed_pairs)}")

    for plate_idx, plate_dir in enumerate(plate_dirs, start=1):
        print(f"\n[Plate] ({plate_idx}/{len(plate_dirs)}) {plate_dir.name}")

        archive_dirs = collect_archive_dirs(plate_dir)

        if len(archive_dirs) < 2:
            print("  [Skip] Less than 2 timestamp folders inside archives.")
            continue

        # 关键逻辑：
        # 每个 Plate/archives/ 下，最后一个时间戳文件夹作为 next_image
        # 前面所有时间戳文件夹作为 prev_image
        next_archive_dir = archive_dirs[-1]
        prev_archive_dirs = archive_dirs[:-1]

        next_image = next_archive_dir / IMAGE_NAME

        if not next_image.exists():
            print(f"  [Skip] next_image not found: {next_image}")
            records.append({
                "plate": plate_dir.name,
                "next_archive": next_archive_dir.name,
                "next_image": str(next_image),
                "status": "failed",
                "reason": "next_image_not_found",
                "prediction": None,
                "ground_truth_improve": 1,
            })
            save_results(records)
            continue

        print(f"  [Next archive] {next_archive_dir.name}")
        print(f"  [Prev archive count] {len(prev_archive_dirs)}")

        for prev_idx, prev_archive_dir in enumerate(prev_archive_dirs, start=1):
            pair_key = (
                plate_dir.name,
                prev_archive_dir.name,
                next_archive_dir.name,
            )

            if pair_key in processed_pairs:
                print(
                    f"    [Skip processed] "
                    f"{prev_archive_dir.name} -> {next_archive_dir.name}"
                )
                continue

            prev_image = prev_archive_dir / IMAGE_NAME

            record_base = {
                "plate": plate_dir.name,
                "prev_archive": prev_archive_dir.name,
                "next_archive": next_archive_dir.name,
                "prev_image": str(prev_image),
                "next_image": str(next_image),
                "ground_truth_improve": 1,
            }

            if not prev_image.exists():
                print(f"    [Skip] prev_image not found: {prev_image}")
                records.append({
                    **record_base,
                    "status": "failed",
                    "reason": "prev_image_not_found",
                    "prediction": None,
                })
                save_results(records)
                continue

            pair_id = (
                f"{plate_dir.name}"
                f"__prev_{prev_archive_dir.name}"
                f"__next_{next_archive_dir.name}"
            )
            safe_pair_id = pair_id.replace("/", "_").replace(":", "_")
            pair_output_json = pair_output_dir / f"{safe_pair_id}.json"

            print(
                f"    [Run] ({prev_idx}/{len(prev_archive_dirs)}) "
                f"{prev_archive_dir.name} -> {next_archive_dir.name}"
            )

            run_result = run_one_pair(
                prev_image=str(prev_image),
                next_image=str(next_image),
                pair_output_json=str(pair_output_json),
            )

            pred = run_result.get("prediction")

            if pred == 1:
                eval_type = "TP"
            elif pred == 0:
                eval_type = "FN"
            else:
                eval_type = "FAILED"

            record = {
                **record_base,
                "status": run_result.get("status"),
                "prediction": pred,
                "eval_type": eval_type,
                "pair_output_json": str(pair_output_json),
                "run_detail": run_result,
            }

            records.append(record)
            processed_pairs.add(pair_key)

            metrics = save_results(records)

            print(f"      [Pred] {pred}, [{eval_type}]")
            print(f"      [Current recall] {metrics['recall']}")
            print(f"      [Saved] {OUTPUT_JSON}")

    final_metrics = save_results(records)

    print("\n[Done]")
    print(f"[Saved] Final results saved to: {OUTPUT_JSON}")
    print(json.dumps(final_metrics, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()