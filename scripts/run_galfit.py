import asyncio
import os
import shutil
import glob
import time
from dotenv import load_dotenv

# 导入 run_galfit 函数
try:
    from src.mcp_server import run_galfit
except ImportError:
    try:
        from src.tools.galfit import run_galfit
    except ImportError:
        raise ImportError("无法导入 run_galfit，请检查 src 路径")

load_dotenv()

async def process_original_feedme(feedme_path, target_output_root):
    """
    运行单个原始 feedme，并将结果（包括 _summary.md）移动到指定目录
    返回状态码: "skipped", "success", "failed"
    """
    source_dir = os.path.dirname(feedme_path)
    source_name = os.path.basename(source_dir)          # 如 cosmos_38
    feedme_filename = os.path.basename(feedme_path)     # 如 cosmos_38.feedme
    base_name = os.path.splitext(feedme_filename)[0]

    final_output_dir = os.path.join(target_output_root, source_name)
    
    # ---------------------------------------------------------
    # 改进 1：检查是否已经存在 summary 文件（断点续传）
    # ---------------------------------------------------------
    possible_summaries = [
            os.path.join(final_output_dir, f"{base_name}_re_summary.md"), # 命中你截图里的情况
            os.path.join(final_output_dir, f"{base_name}_summary.md"),
            os.path.join(final_output_dir, f"{base_name}.md"),
            os.path.join(final_output_dir, "summary.md")
        ]
        
    # 或者更暴力的兜底找法（只要以 summary.md 结尾就抓过来）：
    possible_summaries.extend( glob.glob(os.path.join(final_output_dir, "*summary.md")))
    print(f"possible_summaries:{possible_summaries}")
    if any(os.path.exists(p) for p in possible_summaries):
        print(f"  ⏭️ 跳过: {source_name} (已存在 summary 文件)")
        return "skipped"

    print(f"🚀 正在处理: {source_name} (feedme: {feedme_path})")

    # 调用 GALFIT
    # 加上 try...except 捕获所有来自底层 run_galfit 的异常
    try:
        result = await run_galfit(feedme_path, ["-imax", "100"])
    except Exception as e:
        print(f"   ❌ 致命错误: 底层 run_galfit 崩溃 - {e}")
        return "failed"

    if result["status"] == "success":
        os.makedirs(final_output_dir, exist_ok=True)
        print(f"   ✅ 拟合成功，正在将结果移动到: {final_output_dir}")

        # 需要移动的文件
        files_to_move = {
            "FITS": result.get("optimized_fits_file"),
            "PNG": result.get("image_file"),
            "Summary": result.get("summary_file")
        }

        for file_type, src_path in files_to_move.items():
            if src_path and os.path.exists(src_path):
                dst_path = os.path.join(final_output_dir, os.path.basename(src_path))
                shutil.move(src_path, dst_path)
                print(f"      -> 已保存 {file_type}: {os.path.basename(src_path)}")
            else:
                print(f"      ⚠️ 警告: 未找到 {file_type} 文件")
        return "success"
    else:
        print(f"   ❌ 失败: {result.get('error')}")
        if 'log' in result:
            print(f"      日志片段: {result['log'][-200:]}")
        return "failed"

async def main():
    # === 配置区域 ===
    BASE_DIR = "/mnt/data/wyh/galaxy_morphology_mcp/galfit_sample_0204/"
    
    # 改进 2：补全了报错日志中遗漏的 UDS
    ROOT_LIST = [
        "COS",
        "EGS",
        "GOODSN",
        "GOODSS",
        "UDS"  
    ]

    os.makedirs("/mnt/data/wyh/galaxy_morphology_mcp/galfit_tmp", exist_ok=True)
    print("📁 临时输出目录: /mnt/data/wyh/galaxy_morphology_mcp/galfit_tmp")

    # 用于记录失败的任务
    failed_tasks = []

    for root in ROOT_LIST:
        search_pattern = os.path.join(BASE_DIR, root, "**", "*.feedme") # 使用 ** 支持多层级嵌套查找
        feedme_files = glob.glob(search_pattern, recursive=True)
        print(f"\n📦 在 {root} 中找到 {len(feedme_files)} 个原始 feedme 文件")
        
        OUTPUT_DIR = os.path.join(BASE_DIR, root)

        for feedme in feedme_files:
            status = await process_original_feedme(feedme, OUTPUT_DIR)
            
            if status == "success":
                print("⏳ 冷却中...")
                time.sleep(1.0)  # 只有真正跑了运算才冷却，跳过的直接进下一个
            elif status == "failed":
                failed_tasks.append(feedme)

    # ---------------------------------------------------------
    # 改进 3：统一导出错误日志
    # ---------------------------------------------------------
    if failed_tasks:
        fail_log_path = os.path.join(BASE_DIR, "failed_galfit_runs.txt")
        with open(fail_log_path, "w") as f:
            for task in failed_tasks:
                f.write(task + "\n")
        print(f"\n⚠️ 批处理结束。共有 {len(failed_tasks)} 个任务失败。")
        print(f"📄 失败名单已记录到: {fail_log_path}")
    else:
        print("\n🎉 批处理结束。所有文件均已成功生成 Summary！")

if __name__ == "__main__":
    if not os.getenv("GALFIT_BIN"):
        print("❌ 错误: 未找到 GALFIT_BIN 环境变量，请创建 .env 文件。")
    else:
        asyncio.run(main())