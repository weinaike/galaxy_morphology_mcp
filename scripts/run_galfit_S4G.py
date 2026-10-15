"""Batch-run GALFIT for S4G feedmes outside reduction directories."""

import argparse
import asyncio
import os
import shutil
import sys

try:
    from tqdm.asyncio import tqdm
except ModuleNotFoundError:
    class tqdm:  # Minimal fallback for environments without tqdm.
        # modified by zl: provide a minimal progress implementation when tqdm is absent.
        def __init__(self, total=None, desc=None):
            self.total = total
            self.desc = desc
            self.current = 0

        # modified by zl: support progress context management.
        def __enter__(self):
            return self

        # modified by zl: support progress context cleanup.
        def __exit__(self, exc_type, exc_value, traceback):
            return False

        # modified by zl: track completed batch items in fallback mode.
        def update(self, amount=1):
            self.current += amount

try:
    from dotenv import load_dotenv
except ModuleNotFoundError:
    # modified by zl: keep the batch script usable without python-dotenv.
    def load_dotenv(*args, **kwargs):
        return False


BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(BASE_DIR)

load_dotenv(os.path.join(BASE_DIR, ".env"))

MAX_CONCURRENT_TASKS = 8
semaphore = asyncio.Semaphore(MAX_CONCURRENT_TASKS)

S4G_DIR = "/mnt/data/galaxy_decomposition_evaluation/data/S4G"
DEFAULT_INPUT_DIR = os.path.join(S4G_DIR, "Galaxies")
DEFAULT_OUTPUT_DIR = os.path.join(S4G_DIR, "results")


# modified by zl: derive deterministic S4G output directories.
def output_dir_for_feedme(feedme_path, output_root):
    galaxy_name = os.path.splitext(os.path.basename(feedme_path))[0]
    galaxy_id = galaxy_name.split("_")[0]
    return os.path.join(output_root, galaxy_id, galaxy_name)


# modified by zl: identify reduction directories excluded from S4G discovery.
def is_reduction_dir(dirname):
    """Return True for S4G reduction collection directories."""
    return dirname == "reduction" or dirname.endswith("_reduction")


# modified by zl: discover eligible S4G feedme inputs.
def discover_feedmes(input_path):
    """Find feedmes while pruning every reduction directory from traversal."""
    input_path = os.path.abspath(input_path)
    if os.path.isfile(input_path):
        if not input_path.endswith(".feedme"):
            raise ValueError(f"输入文件不是 .feedme: {input_path}")
        if any(is_reduction_dir(part) for part in input_path.split(os.sep)):
            return []
        return [input_path]

    if not os.path.isdir(input_path):
        raise FileNotFoundError(f"输入路径不存在: {input_path}")

    feedmes = []
    for current_dir, dirnames, filenames in os.walk(input_path):
        # Prune reduction trees before os.walk descends into them.
        dirnames[:] = sorted(
            dirname for dirname in dirnames if not is_reduction_dir(dirname)
        )
        feedmes.extend(
            os.path.join(current_dir, filename)
            for filename in sorted(filenames)
            if filename.endswith(".feedme")
        )
    return sorted(feedmes)


# modified by zl: collect S4G fitting artifacts.
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


# modified by zl: execute one S4G galaxy fitting task.
async def process_galaxy(feedme_path, output_root, step, overwrite=False):
    # Keep the heavy GALFIT stack out of discovery/dry-run mode.
    from src.tools.run_galfit import run_galfit

    galaxy_name = os.path.splitext(os.path.basename(feedme_path))[0]
    output_dir = output_dir_for_feedme(feedme_path, output_root)
    os.makedirs(output_dir, exist_ok=True)
    expected_output = os.path.join(output_dir, f"{galaxy_name}_comparison.png")
    if os.path.exists(expected_output) and not overwrite:
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


# modified by zl: limit concurrent S4G batch work.
async def bounded_process(feedme, output_root, step, pbar, overwrite=False):
    async with semaphore:
        did_run = await process_galaxy(feedme, output_root, step, overwrite)
        pbar.update(1)
        return did_run


# modified by zl: orchestrate the S4G batch runner.
async def main(
    step,
    feedme_name,
    output_root,
    dry_run=False,
    overwrite=False,
    limit=None,
):
    input_dir = os.path.abspath(feedme_name)
    output_dir = os.path.abspath(output_root)
    os.makedirs(output_dir, exist_ok=True)
    discovered_feedmes = discover_feedmes(input_dir)
    if not discovered_feedmes:
        print(f"未在 {input_dir} 的非 reduction 目录中找到 .feedme")
        return

    if overwrite:
        pending_feedmes = discovered_feedmes
    else:
        pending_feedmes = []
        for feedme in discovered_feedmes:
            galaxy_name = os.path.splitext(os.path.basename(feedme))[0]
            output_model_dir = output_dir_for_feedme(feedme, output_dir)
            comparison_png = os.path.join(
                output_model_dir, f"{galaxy_name}_comparison.png"
            )
            if not os.path.exists(comparison_png):
                pending_feedmes.append(feedme)

    feedmes = pending_feedmes[:limit] if limit is not None else pending_feedmes
    print(f"输入目录: {input_dir}")
    print(f"输出目录: {output_dir}")
    print(f"发现非 reduction .feedme: {len(discovered_feedmes)}")
    print(f"跳过已有结果: {len(discovered_feedmes) - len(pending_feedmes)}")
    print(f"本次选择运行: {len(feedmes)}，并发数 {MAX_CONCURRENT_TASKS}")
    print(f"覆盖已有结果: {'是' if overwrite else '否'}")
    if not feedmes:
        print("没有需要运行的任务")
        return
    if dry_run:
        for feedme in feedmes:
            print(f"{feedme} -> {output_dir_for_feedme(feedme, output_dir)}")
        return
    with tqdm(total=len(feedmes), desc=f"Step {step} 拟合进度") as pbar:
        await asyncio.gather(*[
            bounded_process(feedme, output_dir, step, pbar, overwrite) for feedme in feedmes
        ])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Batch-run GALFIT for S4G")
    parser.add_argument("--step", type=int, required=True, help="GALFIT maximum iterations")
    parser.add_argument(
        "--feedme_name",
        default=DEFAULT_INPUT_DIR,
        help=f"Feedme file or input directory (default: {DEFAULT_INPUT_DIR})",
    )
    parser.add_argument(
        "--output_root",
        default=DEFAULT_OUTPUT_DIR,
        help=f"Result root directory (default: {DEFAULT_OUTPUT_DIR})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List selected feedmes and output directories without running GALFIT",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Re-run feedmes even when <model>_comparison.png already exists",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Run at most N selected feedmes (default: all pending feedmes)",
    )
    args = parser.parse_args()
    if args.limit is not None and args.limit <= 0:
        parser.error("--limit must be a positive integer")
    if not args.dry_run and not os.getenv("GALFIT_BIN"):
        parser.error("GALFIT_BIN is not configured")
    asyncio.run(main(
        args.step,
        args.feedme_name,
        args.output_root,
        args.dry_run,
        args.overwrite,
        args.limit,
    ))
