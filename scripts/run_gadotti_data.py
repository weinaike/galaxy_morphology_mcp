"""Batch-run GALFIT for the Gadotti SDSS r-band dataset."""

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
    # modified by zl: keep the batch script usable without python-dotenv.
    def load_dotenv(*args, **kwargs):
        return False


BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(BASE_DIR)
from src.tools.run_galfit import run_galfit

load_dotenv(os.path.join(BASE_DIR, ".env"))

MAX_CONCURRENT_TASKS = 8
semaphore = asyncio.Semaphore(MAX_CONCURRENT_TASKS)
DEFAULT_INPUT_DIR = "/mnt/data/galaxy_decomposition_evaluation/data/Gadotti/no-1D/Plate0349_MJD51699_Fiber620_r/archives"


# modified by zl: collect fitting artifacts for batch output.
def move_artifacts(result, output_dir, overwrite):
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
            if not overwrite:
                continue
            os.remove(target)
        shutil.move(source, target)


# modified by zl: run one Gadotti galaxy fitting task.
async def process_galaxy(feedme_path, step, overwrite=False):
    galaxy_dir = os.path.dirname(feedme_path)
    galaxy_name = os.path.basename(galaxy_dir)
    output_dir = os.path.join(galaxy_dir, "result")
    os.makedirs(output_dir, exist_ok=True)
    expected = os.path.join(output_dir, f"{galaxy_name}_comparison.png")
    if os.path.exists(expected) and not overwrite:
        return False

    result = await run_galfit(os.path.abspath(feedme_path), ["-imax", str(step)])
    if result.get("status") == "success":
        move_artifacts(result, output_dir, overwrite)
        print(f"✅ {galaxy_name} 完成")
    else:
        print(f"❌ {galaxy_name} 失败: {result.get('error')}")
    return True


# modified by zl: limit concurrent Gadotti batch work.
async def bounded_process(feedme, step, overwrite, pbar):
    async with semaphore:
        await process_galaxy(feedme, step, overwrite)
        pbar.update(1)


# modified by zl: orchestrate the Gadotti batch runner.
async def main(step, input_dir, overwrite):
    feedmes = sorted(glob.glob(os.path.join(os.path.abspath(input_dir), "**", "galfit.feedme"), recursive=True))
    if not feedmes:
        print(f"未在 {input_dir} 找到 galfit.feedme")
        return
    print(f"发现 {len(feedmes)} 个任务，最大并发数 {MAX_CONCURRENT_TASKS}")
    with tqdm(total=len(feedmes), desc="GALFIT") as pbar:
        await asyncio.gather(*[
            bounded_process(feedme, step, overwrite, pbar) for feedme in feedmes
        ])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Batch-run the Gadotti dataset")
    parser.add_argument("--step", type=int, default=20000)
    parser.add_argument("--input_dir", default=DEFAULT_INPUT_DIR)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    if not os.getenv("GALFIT_BIN"):
        parser.error("GALFIT_BIN is not configured")
    asyncio.run(main(args.step, args.input_dir, args.overwrite))
