import os
import json
import time
from pathlib import Path
from typing import Any, Dict, Optional, List

from reward import calculate_reward_model_with_param


ROOT_DIR = "/media/zhongling/wyh/GalDecomp_Gen/test_image/done_pinsong/done"

OUTPUT_JSON = "/media/zhongling/wyh/GalDecomp_Gen/test_image/done_pinsong/done/galfit_reward_param_result.json"

IMAGE_NAME = "galfit_comparison_cutoff.png"
SUMMARY_NAME = "galfit_summary.md"

TIMEOUT = 300
MAX_RETRIES = 2
RETRY_SLEEP = 5
MODEL_NAME = "gemini-3.1-pro-preview"


def run_one_pair(
    prev_image: str,
    next_image: str,
    prev_summary_path: str,
    new_summary_path: str,
) -> Dict[str, Any]:

    last_error = None

    for attempt in range(1, MAX_RETRIES + 2):

        try:

            result = calculate_reward_model_with_param(
                prev_residual_image_path=prev_image,
                next_residual_image_path=next_image,
                prev_summary_path=prev_summary_path,
                new_summary_path=new_summary_path,

                model_name=MODEL_NAME,
                temperature=0.1,
                max_tokens=2560,
                timeout=TIMEOUT,
                confidence_threshold=0.6,
            )

            pred = result.get("final_improvement")

            if pred in [0, 1]:
                return {
                    "status": "success",
                    "attempt": attempt,
                    "prediction": pred,
                    "raw_result": result,
                }

            last_error = {
                "parsed_result": result
            }

        except Exception as e:

            last_error = {
                "error_type": type(e).__name__,
                "message": repr(e)
            }

        if attempt <= MAX_RETRIES:
            print(
                f"      [Retry] "
                f"attempt {attempt} failed "
                f"sleep {RETRY_SLEEP}s..."
            )

            time.sleep(RETRY_SLEEP)

    return {
        "status": "failed",
        "prediction": None,
        "error": last_error
    }


def collect_plate_dirs(root_dir: Path):

    return sorted(
        [
            p for p in root_dir.iterdir()
            if p.is_dir() and p.name.startswith("Plate")
        ]
    )


def collect_archive_dirs(plate_dir: Path):

    archives_dir = plate_dir / "archives"

    if not archives_dir.exists():
        return []

    return sorted(
        [
            p for p in archives_dir.iterdir()
            if p.is_dir()
        ]
    )


def calculate_metrics(records):

    valid_records = [

        r for r in records

        if r.get("status")=="success"
        and r.get("prediction") in [0,1]

    ]

    tp = sum(
        1 for r in valid_records
        if r["prediction"]==1
    )

    fn = sum(
        1 for r in valid_records
        if r["prediction"]==0
    )

    recall = tp/(tp+fn) if (tp+fn)>0 else None

    return {

        "total_pairs":len(records),
        "valid_pairs":len(valid_records),

        "true_positive":tp,
        "false_negative":fn,

        "recall":recall
    }


def save_results(records):

    metrics = calculate_metrics(records)

    output = {

        "root_dir":ROOT_DIR,
        "metrics":metrics,
        "records":records
    }

    with open(
        OUTPUT_JSON,
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            output,
            f,
            ensure_ascii=False,
            indent=2
        )

    return metrics


def main():

    root_dir=Path(ROOT_DIR)

    records=[]

    plate_dirs=collect_plate_dirs(root_dir)

    print(
        f"[Info] Found "
        f"{len(plate_dirs)} plates"
    )

    for plate_idx,plate_dir in enumerate(
        plate_dirs,
        start=1
    ):

        print(
            f"\n[Plate]"
            f"({plate_idx}/"
            f"{len(plate_dirs)}) "
            f"{plate_dir.name}"
        )

        archive_dirs=collect_archive_dirs(
            plate_dir
        )

        if len(archive_dirs)<2:

            print(
                "  [Skip] "
                "less than 2 archives"
            )
            continue


        next_archive=archive_dirs[-1]
        prev_archives=archive_dirs[:-1]

        next_image=(
            next_archive/IMAGE_NAME
        )

        prev_summary_path=(
            prev_archive/SUMMARY_NAME
        )
        new_summary_path=(
            next_archive/SUMMARY_NAME
        )

        for idx,prev_archive in enumerate(
            prev_archives,
            start=1
        ):

            prev_image=(
                prev_archive/IMAGE_NAME
            )

            print(
                f"    [Run]"
                f"({idx}/"
                f"{len(prev_archives)}) "
                f"{prev_archive.name}"
                f" -> "
                f"{next_archive.name}"
            )

            run_result=run_one_pair(
                prev_image=str(prev_image),
                next_image=str(next_image),
                prev_summary_path=str(prev_summary_path),
                new_summary_path=str(new_summary_path)

            )

            pred=run_result.get(
                "prediction"
            )

            record={

                "plate":
                    plate_dir.name,

                "prev_archive":
                    prev_archive.name,

                "next_archive":
                    next_archive.name,

                "prediction":
                    pred,

                "status":
                    run_result["status"],

                "detail":
                    run_result
            }

            records.append(record)

            metrics=save_results(
                records
            )

            print(
                f"      [Pred] "
                f"{pred}"
            )

            print(
                f"      [Recall]"
                f"{metrics['recall']}"
            )


    print("\n[Done]")



if __name__=="__main__":
    main()