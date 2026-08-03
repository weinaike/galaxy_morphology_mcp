"""Batch-run over-parameterized GALFIT feedmes and append fit metrics to CSV."""

import argparse
import asyncio
import csv
import datetime
import glob
import json
import os
import re
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

load_dotenv(os.path.join(BASE_DIR, ".env"))

MAX_CONCURRENT_TASKS = 8
semaphore = asyncio.Semaphore(MAX_CONCURRENT_TASKS)
log_lock = asyncio.Lock()

DEFAULT_INPUT_DIR = "/mnt/data/galaxy_decomposition_evaluation/data/GALFIT_DATA/COS"
DEFAULT_LOG_FILE = "/mnt/data/galaxy_decomposition_evaluation/results/overfit_galfit_run_log.csv"
DEFAULT_LIMIT = 2
OVERFIT_FEEDME_RE = re.compile(r"_(\d{2})(?:_(sersic|psf|bar))?$")
TEMPLATE_ALIASES = {
    "01": "01", "02": "02", "03": "03", "sersic": "03", "04": "04", "psf": "04",
    "05": "05", "bar": "05",
}

LOG_COLUMNS = [
    "timestamp", "source_id", "galaxy_name", "method", "feedme_path", "output_dir",
    "status", "chi2", "chi2_nu", "bic", "chisq1d", "chisq1d_nu", "bic1d",
    "sky_value", "optimized_fits_file", "image_file", "summary_file",
    "console_log_file", "round_status_file", "error",
]


def is_source_feedme(feedme_path):
    parts = os.path.abspath(feedme_path).split(os.sep)
    return "archives" not in parts and "test" not in parts


def output_subdir_for_feedme(galaxy_name):
    match = OVERFIT_FEEDME_RE.search(galaxy_name)
    return match.group(1) if match else "test"


def overfit_template_for_feedme(feedme_path):
    match = OVERFIT_FEEDME_RE.search(os.path.splitext(os.path.basename(feedme_path))[0])
    return match.group(1) if match else None


def galaxy_example_key(feedme_path):
    parent = os.path.dirname(os.path.abspath(feedme_path))
    return os.path.dirname(parent) if os.path.basename(parent) == "overparameterized" else parent


def source_id_for_feedme(feedme_path):
    parent = os.path.dirname(os.path.abspath(feedme_path))
    return os.path.basename(os.path.dirname(parent)) if os.path.basename(parent) == "overparameterized" else os.path.basename(parent)


def result_row(feedme_path, galaxy_name, output_dir, status, result=None, error=""):
    result = result or {}
    stats = result.get("fit_statistics") or {}
    return {
        "timestamp": datetime.datetime.now().isoformat(timespec="seconds"),
        "source_id": source_id_for_feedme(feedme_path),
        "galaxy_name": galaxy_name,
        "method": output_subdir_for_feedme(galaxy_name),
        "feedme_path": os.path.abspath(feedme_path),
        "output_dir": output_dir,
        "status": status,
        "chi2": stats.get("chi2", ""),
        "chi2_nu": stats.get("chi2_nu", ""),
        "bic": stats.get("bic", ""),
        "chisq1d": stats.get("chisq1d", ""),
        "chisq1d_nu": stats.get("chisq1d_nu", ""),
        "bic1d": stats.get("bic1d", ""),
        "sky_value": stats.get("sky_value", ""),
        "optimized_fits_file": result.get("optimized_fits_file", ""),
        "image_file": result.get("image_file", ""),
        "summary_file": result.get("summary_file", ""),
        "console_log_file": result.get("console_log_file", ""),
        "round_status_file": result.get("round_status_file", ""),
        "error": error or result.get("error", ""),
    }


async def append_log_row(log_file, row):
    if not log_file:
        return
    async with log_lock:
        os.makedirs(os.path.dirname(os.path.abspath(log_file)), exist_ok=True)
        exists = os.path.exists(log_file)
        with open(log_file, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=LOG_COLUMNS)
            if not exists:
                writer.writeheader()
            writer.writerow(row)


def select_feedmes_by_example(feedmes, limit):
    if not limit:
        return feedmes
    selected, seen = [], set()
    for feedme in feedmes:
        key = galaxy_example_key(feedme)
        if key not in seen:
            if len(seen) >= limit:
                break
            seen.add(key)
        selected.append(feedme)
    return selected


def filter_feedmes_by_template(feedmes, template):
    if template == "all":
        return [path for path in feedmes if overfit_template_for_feedme(path)]
    code = TEMPLATE_ALIASES[template]
    return [path for path in feedmes if overfit_template_for_feedme(path) == code]


def load_input_dirs(input_json, input_dir):
    """Load and validate galaxy directories from a JSON array."""
    with open(input_json, encoding="utf-8") as f:
        paths = json.load(f)
    if not isinstance(paths, list) or not all(isinstance(path, str) for path in paths):
        raise ValueError(f"{input_json} 必须是仅包含目录路径字符串的 JSON 数组")

    input_dir = os.path.realpath(input_dir)
    selected = []
    seen = set()
    for path in paths:
        resolved = os.path.realpath(
            path if os.path.isabs(path) else os.path.join(os.path.dirname(input_json), path)
        )
        if os.path.commonpath([input_dir, resolved]) != input_dir:
            raise ValueError(f"JSON 中的路径不在 input_dir 下: {path}")
        if not os.path.isdir(resolved):
            raise ValueError(f"JSON 中的目录不存在: {path}")
        if resolved not in seen:
            seen.add(resolved)
            selected.append(resolved)
    return selected


def discover_feedmes(input_dir, input_json=None):
    search_dirs = load_input_dirs(input_json, input_dir) if input_json else [input_dir]
    feedmes = set()
    for search_dir in search_dirs:
        feedmes.update(
            path for path in glob.glob(os.path.join(search_dir, "**", "*.feedme"), recursive=True)
            if is_source_feedme(path)
        )
    return sorted(feedmes)


def move_result_artifacts(result, output_dir, overwrite):
    fields = {
        "optimized_fits_file": result.get("optimized_fits_file"),
        "image_file": result.get("image_file"),
        "summary_file": result.get("summary_file"),
        "round_status_file": result.get("round_status_file"),
    }
    for field, source in fields.items():
        if not source or not os.path.exists(source):
            continue
        target = os.path.join(output_dir, os.path.basename(source))
        if os.path.abspath(source) == os.path.abspath(target):
            result[field] = target
            continue
        if os.path.exists(target):
            if not overwrite:
                result[field] = target
                continue
            os.remove(target)
        shutil.move(source, target)
        result[field] = target


async def process_galaxy(feedme_path, step, overwrite=False, log_file=None):
    # Keep the heavy project import out of --help and --dry_run. The GALFIT
    # toolchain requires the project's Python 3.10+ runtime.
    from src.tools.run_galfit import run_galfit

    galaxy_name = os.path.splitext(os.path.basename(feedme_path))[0]
    output_dir = os.path.join(os.path.dirname(feedme_path), output_subdir_for_feedme(galaxy_name))
    os.makedirs(output_dir, exist_ok=True)
    expected = os.path.join(output_dir, f"{galaxy_name}_comparison.png")
    if os.path.exists(expected) and not overwrite:
        await append_log_row(log_file, result_row(feedme_path, galaxy_name, output_dir, "skipped_existing"))
        return False

    tmp_fits = os.path.join(BASE_DIR, "galfit_tmp", f"{galaxy_name}.fits")
    if os.path.exists(tmp_fits):
        try:
            os.remove(tmp_fits)
        except FileNotFoundError:
            pass

    result = await run_galfit(os.path.abspath(feedme_path), ["-imax", str(step)])
    if result.get("status") == "success":
        move_result_artifacts(result, output_dir, overwrite)
        await append_log_row(log_file, result_row(feedme_path, galaxy_name, output_dir, "success", result))
        print(f"✅ {galaxy_name} 完成: {output_dir}")
    else:
        await append_log_row(log_file, result_row(feedme_path, galaxy_name, output_dir, "failure", result))
        print(f"❌ {galaxy_name} 失败: {result.get('error')}")
    return True


async def bounded_process(feedme, step, overwrite, log_file, pbar):
    async with semaphore:
        did_run = await process_galaxy(feedme, step, overwrite, log_file)
        pbar.update(1)
        return did_run


async def main(step, input_dir, input_json, limit, template, overwrite, log_file, dry_run):
    input_dir = os.path.abspath(input_dir)
    if input_json:
        input_json = os.path.abspath(input_json)
    all_feedmes = discover_feedmes(input_dir, input_json)
    matching = filter_feedmes_by_template(all_feedmes, template)
    effective_limit = (0 if input_json else DEFAULT_LIMIT) if limit is None else limit
    feedmes = select_feedmes_by_example(matching, effective_limit)
    if not feedmes:
        print(f"未在 {input_dir} 找到 template={template} 的 feedme")
        return
    print(f"发现 {len(matching)} 个匹配配置，本次运行 {len(feedmes)} 个，并发数 {MAX_CONCURRENT_TASKS}")
    if dry_run:
        print("DRY RUN：以下 feedme 不会实际执行：")
        for feedme in feedmes:
            print(feedme)
        return
    with tqdm(total=len(feedmes), desc=f"Step {step} 拟合进度") as pbar:
        await asyncio.gather(*[
            bounded_process(feedme, step, overwrite, log_file, pbar) for feedme in feedmes
        ])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Batch-run GALFIT overfit templates")
    parser.add_argument("--step", type=int, required=True, help="GALFIT maximum iterations")
    parser.add_argument("--input_dir", default=DEFAULT_INPUT_DIR)
    parser.add_argument("--input_json", help="JSON array of galaxy directories to scan")
    parser.add_argument(
        "--limit", type=int, default=None,
        help=f"maximum examples; 0 runs all (default: all with --input_json, otherwise {DEFAULT_LIMIT})",
    )
    parser.add_argument("--template", choices=["all", "01", "02", "03", "04", "05", "sersic", "psf", "bar"], default="all")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--log_file", default=DEFAULT_LOG_FILE)
    parser.add_argument("--dry_run", action="store_true", help="list selected feedmes without running GALFIT")
    args = parser.parse_args()
    if args.limit is not None and args.limit < 0:
        parser.error("--limit must be >= 0")
    if not args.dry_run and not os.getenv("GALFIT_BIN"):
        parser.error("GALFIT_BIN is not configured")
    try:
        asyncio.run(main(
            args.step, args.input_dir, args.input_json, args.limit, args.template,
            args.overwrite, args.log_file, args.dry_run,
        ))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
