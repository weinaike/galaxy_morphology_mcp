import os
import json
import time
import traceback
from typing import Dict, Any, List

from reward import is_good_fit


# ROOT_DIR = "/media/zhongling/wyh/GalDecomp_Gen/test_image/limin_gadotti_done"
ROOT_DIR = "/media/zhongling/wyh/GalDecomp_Gen/test_image/done_pinsong/done"

# OUTPUT_JSON = "/media/zhongling/wyh/GalDecomp_Gen/test_image/limin_gadotti_done/is_good_fit_batch_results_gemini.json"
OUTPUT_JSON = "/media/zhongling/wyh/GalDecomp_Gen/test_image/done_pinsong/done/is_good_fit_batch_results_gemini.json"
# MODEL_NAME = "gpt-5.5"
MODEL_NAME = "gemini-3.1-pro-preview"
API_KEY = os.getenv("OPENAI_API_KEY")

TEMPERATURE = 0.7
TIMEOUT = 300
MAX_TOKENS = 2048
CONFIDENCE_THRESHOLD = 0.6
MAX_RETRIES = 3
RETRY_SLEEP = 10


def find_galfit_comparison_images(root_dir: str) -> List[Dict[str, str]]:
    """
    遍历 limin_gadotti_done 下所有 Plate*/archives/*/galfit_comparison.png 文件。

    返回:
        [
            {
                "plate_dir": "...",
                "archive_id": "...",
                "image_path": "..."
            },
            ...
        ]
    """
    samples = []

    if not os.path.exists(root_dir):
        raise FileNotFoundError(f"ROOT_DIR does not exist: {root_dir}")

    for plate_name in sorted(os.listdir(root_dir)):
        plate_dir = os.path.join(root_dir, plate_name)

        if not os.path.isdir(plate_dir):
            continue

        archives_dir = os.path.join(plate_dir, "archives")
        if not os.path.isdir(archives_dir):
            continue

        for archive_id in sorted(os.listdir(archives_dir)):
            archive_dir = os.path.join(archives_dir, archive_id)

            if not os.path.isdir(archive_dir):
                continue

            image_path = os.path.join(
                archive_dir,
                "galfit_comparison.png"
            )

            if os.path.exists(image_path):
                samples.append({
                    "plate_name": plate_name,
                    "plate_dir": plate_dir,
                    "archive_id": archive_id,
                    "archive_dir": archive_dir,
                    "image_path": image_path,
                })

    return samples


def load_existing_results(output_json: str) -> List[Dict[str, Any]]:
    """
    如果之前已经跑过一部分，则读取已有结果，支持断点续跑。
    """
    if not os.path.exists(output_json):
        return []

    try:
        with open(output_json, "r", encoding="utf-8") as f:
            data = json.load(f)

        if isinstance(data, list):
            return data

        print(f"[Warning] Existing output is not a list, ignore: {output_json}")
        return []

    except Exception as e:
        print(f"[Warning] Failed to load existing results: {e}")
        return []


def save_results(results: List[Dict[str, Any]], output_json: str):
    """
    实时保存结果。
    """
    os.makedirs(os.path.dirname(output_json), exist_ok=True)

    with open(output_json, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)


def main():
    print(f"[Info] Root dir: {ROOT_DIR}")
    print(f"[Info] Output json: {OUTPUT_JSON}")

    samples = find_galfit_comparison_images(ROOT_DIR)
    print(f"[Info] Found {len(samples)} galfit_comparison.png files.")

    if len(samples) == 0:
        print("[Warning] No images found. Please check whether the path pattern is:")
        print("Plate*/archives/*/7/galfit_comparison.png")
        return

    results = load_existing_results(OUTPUT_JSON)

    processed_paths = set()
    for item in results:
        if "image_path" in item:
            processed_paths.add(item["image_path"])

    print(f"[Info] Existing processed samples: {len(processed_paths)}")

    for idx, sample in enumerate(samples, start=1):
        image_path = sample["image_path"]

        if image_path in processed_paths:
            print(f"[Skip] ({idx}/{len(samples)}) Already processed: {image_path}")
            continue

        print("\n" + "=" * 80)
        print(f"[Run] ({idx}/{len(samples)})")
        print(f"[Plate] {sample['plate_name']}")
        print(f"[Archive] {sample['archive_id']}")
        print(f"[Image] {image_path}")

        start_time = time.time()

        record = {
            "plate_name": sample["plate_name"],
            "plate_dir": sample["plate_dir"],
            "archive_id": sample["archive_id"],
            "archive_dir": sample["archive_dir"],
            "image_path": image_path,
            "model_name": MODEL_NAME,
            "status": "running",
        }

        try:
            result = is_good_fit(
                residual_image_path=image_path,
                param=True,
                summary_md_path=None,
                model_name=MODEL_NAME,
                api_key=API_KEY,
                temperature=TEMPERATURE,
                max_tokens=MAX_TOKENS,
                timeout=TIMEOUT,
                confidence_threshold=CONFIDENCE_THRESHOLD,
                max_retries=MAX_RETRIES,
                retry_sleep=RETRY_SLEEP,
            )

            elapsed = time.time() - start_time

            record.update({
                "status": "success",
                "elapsed_seconds": round(elapsed, 3),
                "result": result,
            })

            print("[Success]")
            print(json.dumps(result, ensure_ascii=False, indent=2))

        except Exception as e:
            elapsed = time.time() - start_time

            record.update({
                "status": "failed",
                "elapsed_seconds": round(elapsed, 3),
                "error": repr(e),
                "traceback": traceback.format_exc(),
            })

            print("[Failed]")
            print(repr(e))

        results.append(record)
        save_results(results, OUTPUT_JSON)

        print(f"[Saved] Current results saved to: {OUTPUT_JSON}")

    print("\n" + "=" * 80)
    print(f"[Done] Total found: {len(samples)}")
    print(f"[Done] Total records in json: {len(results)}")
    print(f"[Done] Result saved to: {OUTPUT_JSON}")


if __name__ == "__main__":
    main()