"""Batch-run GALFIT for normal or reduction S4G feedme collections."""

import argparse
import asyncio
import glob
import os
import shutil
import sys

from tqdm.asyncio import tqdm

try:
    from dotenv import load_dotenv
except ModuleNotFoundError:
    def load_dotenv(*args, **kwargs):
        return False


BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(BASE_DIR)
from src.tools.run_galfit import run_galfit

load_dotenv(os.path.join(BASE_DIR, ".env"))

MAX_CONCURRENT_TASKS = 8
semaphore = asyncio.Semaphore(MAX_CONCURRENT_TASKS)


def output_dir_for_feedme(feedme_path, output_root, reduction):
    galaxy_name = os.path.splitext(os.path.basename(feedme_path))[0]
    galaxy_id = galaxy_name.split("_")[0]
    if reduction:
        return os.path.join(output_root, galaxy_id, f"{galaxy_id}_reduction", galaxy_name)
    return os.path.join(output_root, galaxy_id, galaxy_name)


def move_artifacts(result, output_dir):
    for source in (
        result.get("optimized_fits_file"),
        result.get("image_file"),
        result.get("summary_file"),
        result.get("round_status_file"),
    ):
        if not source or not os.path.exists(source):
            continue
        target = os.path.join(output_dir, os.path.basename(source))
        if os.path.abspath(source) == os.path.abspath(target):
            continue
        if os.path.exists(target):
            os.remove(target)
        shutil.move(source, target)


async def process_galaxy(feedme_path, output_root, step, reduction):
    galaxy_name = os.path.splitext(os.path.basename(feedme_path))[0]
    output_dir = output_dir_for_feedme(feedme_path, output_root, reduction)
    os.makedirs(output_dir, exist_ok=True)
    expected_output = os.path.join(output_dir, f"{galaxy_name}_comparison.png")
    if os.path.exists(expected_output):
        return False

    result = await run_galfit(os.path.abspath(feedme_path), ["-imax", str(step)])
    if result.get("status") == "success":
        move_artifacts(result, output_dir)
        print(f"✅ {galaxy_name} 完成: {output_dir}")
    else:
        print(f"❌ {galaxy_name} 失败: {result.get('error')}")
        if result.get("log"):
            print(result["log"][-1000:])
    return True


async def bounded_process(feedme, output_root, step, pbar, reduction):
    async with semaphore:
        did_run = await process_galaxy(feedme, output_root, step, reduction)
        pbar.update(1)
        return did_run


def resolve_roots(feedme_name):
    path = os.path.abspath(feedme_name)
    if feedme_name.endswith(".feedme"):
        input_dir = os.path.dirname(os.path.dirname(path))
        output_dir = os.path.join(os.path.dirname(input_dir), "results")
    elif feedme_name.endswith("Galaxies"):
        input_dir = path
        output_dir = os.path.join(os.path.dirname(input_dir), "results")
    else:
        input_dir = path
        output_dir = os.path.join(os.path.dirname(os.path.dirname(path)), "results")
    return input_dir, output_dir


async def main(step, feedme_name, reduction):
    input_dir, output_dir = resolve_roots(feedme_name)
    os.makedirs(output_dir, exist_ok=True)
    all_feedmes = glob.glob(os.path.join(input_dir, "**", "*.feedme"), recursive=True)
    if reduction:
        feedmes = [path for path in all_feedmes if "_reduction" in path]
    else:
        feedmes = [path for path in all_feedmes if "_reduction" not in path]
    if not feedmes:
        print(f"未在 {input_dir} 找到符合 reduction={reduction} 的 feedme")
        return
    print(f"发现 {len(feedmes)} 个任务，并发数 {MAX_CONCURRENT_TASKS}")
    with tqdm(total=len(feedmes), desc=f"Step {step} 拟合进度") as pbar:
        await asyncio.gather(*[
            bounded_process(feedme, output_dir, step, pbar, reduction) for feedme in feedmes
        ])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Batch-run GALFIT for S4G")
    parser.add_argument("--step", type=int, required=True, help="GALFIT maximum iterations")
    parser.add_argument("--feedme_name", required=True, help="Feedme file or input directory")
    parser.add_argument("--reduction", action="store_true")
    args = parser.parse_args()
    if not os.getenv("GALFIT_BIN"):
        parser.error("GALFIT_BIN is not configured")
    asyncio.run(main(args.step, args.feedme_name, args.reduction))
