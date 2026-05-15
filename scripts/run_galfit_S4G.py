import asyncio
import os
import sys
import shutil
import glob
import argparse
from tqdm.asyncio import tqdm  # 注意这里：为了兼容异步的进度条

try:
    from dotenv import load_dotenv
except ModuleNotFoundError:
    def load_dotenv(*args, **kwargs):
        return False

# ================= 路径寻址与包导入修复 =================
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# print(f"base:{BASE_DIR}")
sys.path.append(BASE_DIR)
from src.tools.run_galfit import run_galfit

load_dotenv(os.path.join(BASE_DIR, ".env"))

# ================= 并发控制区 =================
# 🚀 根据你服务器的 CPU 核心数来调整。你的服务器很强，可以先设为 16 或 20 试试水
MAX_CONCURRENT_TASKS = 8
semaphore = asyncio.Semaphore(MAX_CONCURRENT_TASKS)


async def process_galaxy(feedme_path, target_output_root, step, reduction):
    # 1. 获取星系名字
    galaxy_name = os.path.splitext(os.path.basename(feedme_path))[0]
    galaxy_id = galaxy_name.split('_')[0]
    if reduction:
        final_output_dir = os.path.join(target_output_root, galaxy_id, f"{galaxy_id}_reduction", galaxy_name)
    else:
        final_output_dir = os.path.join(target_output_root, galaxy_id, galaxy_name)
    os.makedirs(final_output_dir, exist_ok=True)
    # final_output_dir = target_output_root
    # =======================================================
    # 中断重启：微观防线，秒速跳过已完成的任务
    # =======================================================
    expected_output = os.path.join(final_output_dir, f"{galaxy_name}_comparison.png")

    if os.path.exists(expected_output):
        # print(f"✅ [跳过] {expected_output} 已存在，跳过拟合")
        return False
    # =======================================================

    # [扫雷逻辑，清理临时垃圾]
    tmp_fits_path = os.path.join(BASE_DIR, "galfit_tmp", f"{galaxy_name}.fits")
    if os.path.exists(tmp_fits_path):
        try:
            os.remove(tmp_fits_path)
        except FileNotFoundError:
            pass  # 并发时可能别人已经删了，忽略即可

    # 2. 调用 GALFIT (现在底层已经有 UUID 保护，放手去跑！)
    abs_feedme_path = os.path.abspath(feedme_path)
    # 注意：并发时切换工作目录 (os.chdir) 是极度危险的！
    # 但如果 run_galfit 底层已经处理好了工作目录，这里勉强保留。最好确认底层不会因为并发 chdir 而混乱。
    # 如果底层能接受绝对路径且不需要外部 chdir，建议删掉这行 os.chdir(BASE_DIR)。
    # os.chdir(BASE_DIR)

    result = await run_galfit(abs_feedme_path, ["-imax", f"{step}"])

    if result["status"] == "success":
        print(f"   ✅ 拟合成功，正在将结果移动到: {final_output_dir}")

        files_to_move = {
            "FITS": result.get("optimized_fits_file"),
            "PNG": result.get("image_file"),
            "Summary": result.get("summary_file")
        }

        for file_type, source_path in files_to_move.items():
            if source_path and os.path.exists(source_path):
                file_name = os.path.basename(source_path)
                target_path = os.path.join(final_output_dir, file_name)
                if os.path.exists(target_path):
                    print(f"⚠️ 删除已有输出文件: {target_path}")
                    os.remove(target_path)
                shutil.move(source_path, target_path)
    else:
        print(f"\n❌ [失败] {galaxy_name}: {result.get('error')}")
        if 'log' in result:
            print(f"      日志片段: {result['log'][-1000:]}")  # 打印最后200字符日志

    return True


# 包装函数：用 Semaphore 控制并发量，并更新进度条
async def bounded_process(feedme_path, target_output_root, step, pbar, reduction):
    async with semaphore:  # 获取并发锁，拿到锁的才能往下走
        did_run = await process_galaxy(feedme_path, target_output_root, step, reduction)
        pbar.update(1)  # 跑完一个，进度条加一
        return did_run


async def main(step, feedme_name, reduction):
    if feedme_name.endswith(".feedme"):
        INPUT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(feedme_name)))
        OUTPUT_DIR = os.path.join(os.path.dirname(INPUT_DIR), "results")
        os.makedirs(OUTPUT_DIR, exist_ok=True)

    elif feedme_name.endswith("Galaxies"):
        INPUT_DIR = feedme_name
        OUTPUT_DIR = os.path.join(os.path.dirname(INPUT_DIR), "results")
        os.makedirs(OUTPUT_DIR, exist_ok=True)

    else:
        INPUT_DIR = feedme_name
        OUTPUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(feedme_name))), "results")
        os.makedirs(OUTPUT_DIR, exist_ok=True)

    # 1. 原始匹配：依然获取所有 feedme
    all_feedme_files = glob.glob(os.path.join(INPUT_DIR, "**/*.feedme"), recursive=True)

    # 2. 根据 reduction 标志位进行精准过滤
    if reduction:
        # 如果是 reduction 模式，只跑路径里包含 "_reduction" 的文件
        feedme_files = [f for f in all_feedme_files if "_reduction" in f]
    else:
        # 如果不是 reduction 模式，排除掉所有路径里包含 "_reduction" 的文件
        feedme_files = [f for f in all_feedme_files if "_reduction" not in f]

    if not feedme_files:
        mode_str = "Reduction" if reduction else "Normal"
        print(f"❌ 错误: 在 {INPUT_DIR} 下没找到任何符合条件的 {mode_str} 拟合配置文件！")
        return

    print(f"找到 {len(feedme_files)} 个任务...")
    print(f"启动高并发模式 (最大并发数: {MAX_CONCURRENT_TASKS}) ...\n")

    # 核心：使用 asyncio.gather 进行并发派发
    with tqdm(total=len(feedme_files), desc=f"Step {step} 拟合进度") as pbar:
        # 创建所有任务的列表
        tasks = [bounded_process(feedme, OUTPUT_DIR, step, pbar, reduction) for feedme in feedme_files]
        # 并发执行所有任务！
        await asyncio.gather(*tasks)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run GALFIT with varying steps.")
    parser.add_argument("--step", type=int, required=True, help="Max iterations for GALFIT")
    parser.add_argument("--feedme_name", type=str, required=True, help="Feedme name")
    parser.add_argument("--reduction", action='store_true', help="Reduction flag")
    args = parser.parse_args()

    if not os.getenv("GALFIT_BIN"):
        print("❌ 错误: 未找到 GALFIT_BIN 环境变量。请确保已创建 .env 文件。")
    else:
        asyncio.run(main(args.step, args.feedme_name, args.reduction))
