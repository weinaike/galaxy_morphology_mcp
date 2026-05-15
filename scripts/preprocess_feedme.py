#!/usr/bin/env python3
"""
fix_feedme_paths.py

遍历 COS 目录下所有原始 .feedme 文件，将绝对路径转换为相对路径，
使得路径指向当前项目结构中的正确位置（如 COS/<source>/... 和 galfit_tmp/）。
"""

import os
import re
import shutil
import glob

# ================= 配置 =================
BASE_DIR = "/mnt/data/wyh/galaxy_morphology_mcp/galfit_sample_0204"
ROOT_LIST = ["COS","EGS","GOODSN","GOODSS","UDS"]                # 原始数据根目录
TMP_OUTPUT_DIR = "/mnt/data/wyh/galaxy_morphology_mcp/galfit_tmp"   # 临时输出目录（将用于 B 行）
PSF_PATH = "/mnt/data/wyh/galaxy_morphology_mcp/f160w_psf.fits" # PSF 文件的统一相对路径（相对于 feedme 所在目录）

# 确保临时目录存在
os.makedirs(TMP_OUTPUT_DIR, exist_ok=True)

# 正则匹配 feedme 中的行首字母和路径
LINE_PATTERN = re.compile(r'^([A-Z])\)\s+(.*?)(?:\s+#.*)?$')

def fix_feedme_file(feedme_path, root_dir):
    """
    处理单个 feedme 文件，将其中的绝对路径改为相对路径。
    备份原文件为 .bak。
    """
    print(f"Processing: {feedme_path}")

    # 获取源名称（即父目录名，如 cosmos_38）
    source_dir = os.path.basename(os.path.dirname(feedme_path))
    base_name = os.path.splitext(os.path.basename(feedme_path))[0]  # 如 cosmos_38_v0 或 cosmos_38

    with open(feedme_path, 'r') as f:
        lines = f.readlines()

    new_lines = []
    modified = False

    for line in lines:
        stripped = line.strip()
        m = LINE_PATTERN.match(stripped)
        if m:
            label = m.group(1)
            path = m.group(2).strip()
            # 如果路径看起来是绝对路径（以 / 开头），或者包含 /data2/ 等，我们将其转换
            if path.startswith('/') or '/data2/' in path:
                # 提取文件名
                filename = os.path.basename(path)
                # 根据标签构造新路径
                new_path = None
                if label == 'A':  # 输入图像
                    # 假设输入图像命名为 <source>_drz.fits 或类似，但实际可能为 cosmos_38_drz.fits
                    # 我们尝试用 source_dir 和原始文件名构建相对路径
                    new_path = f"/mnt/data/wyh/galaxy_morphology_mcp/galfit_sample_0204/{root_dir}/{source_dir}/{filename}"
                elif label == 'B':  # 输出图像
                    # 输出到 galfit_tmp 下，保留原文件名
                    new_path = f"/mnt/data/wyh/galaxy_morphology_mcp/galfit_tmp/{filename}"
                elif label == 'C':  # sigma 图像
                    new_path = f"/mnt/data/wyh/galaxy_morphology_mcp/galfit_sample_0204/{root_dir}/{source_dir}/{filename}"
                elif label == 'D':  # PSF 图像
                    new_path = PSF_PATH  # 统一指向 ../COS/f160w_psf.fits
                elif label == 'F':  # mask 图像
                    new_path = f"/mnt/data/wyh/galaxy_morphology_mcp/galfit_sample_0204/{root_dir}/{source_dir}/{filename}"
                elif label == 'G':
                    # G 行是注释，通常没有路径，保持不变
                    new_path = f"/mnt/data/wyh/galaxy_morphology_mcp/galfit_sample_0204/GALFIT.con"
                else:
                    # 其他行（如 H,I,J,K,O,P）不是路径，保持不变
                    new_path = None

                if new_path:
                    # 替换行，保留注释（如果有）
                    comment = ""
                    if "#" in line:
                        comment = "  # " + line.split("#", 1)[1].strip()
                    new_line = f"{label}) {new_path}{comment}\n"
                    new_lines.append(new_line)
                    modified = True
                    print(f"  {label}: {path} -> {new_path}")
                    continue
        # 如果不是需要修改的行，保留原行
        new_lines.append(line)

    if modified:
        # 备份原文件
        backup_path = feedme_path + ".bak"
        shutil.copy2(feedme_path, backup_path)
        print(f"  Backup created: {backup_path}")
        # 写入新文件
        with open(feedme_path, 'w') as f:
            f.writelines(new_lines)
        print(f"  Updated: {feedme_path}")
    else:
        print(f"  No changes needed.")

def main():
    # 查找所有 feedme 文件
    for root_dir in ROOT_LIST:
        feedme_files = glob.glob(os.path.join(BASE_DIR, root_dir, "*", "*.feedme"))
        print(f"Found {len(feedme_files)} feedme files.")

        for fpath in feedme_files:
            print(f"Processing: {fpath}")
            fix_feedme_file(fpath, root_dir)

        print(f"Processed {len(feedme_files)} feedme files in {root_dir}.")

    print("Done.")

if __name__ == "__main__":
    main()