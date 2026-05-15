import os
import pandas as pd
import shutil
from tqdm import tqdm

# ================= 配置区 =================
PROJECT_ROOT = "/mnt/data/wyh/galaxy_morphology_mcp"
FEEDME_NAME = "perturbed_ii_feedme_wo_fix"
TARGET_ROOT = os.path.join(PROJECT_ROOT, "transfer_A100")

def transfer_with_gt():
    # 1. 初始化 A100 目录结构 (增加 GT 相关目录)
    dirs = {
        "images": os.path.join(TARGET_ROOT, "images"),           # 输入：扰动残差图
        "summaries": os.path.join(TARGET_ROOT, "summaries"),     # 输入：扰动汇总
        "configs": os.path.join(TARGET_ROOT, "configs"),         # 输入：Feedme
        "gt_images": os.path.join(TARGET_ROOT, "gt_images"),     # 输出：GT 残差图
        "gt_summaries": os.path.join(TARGET_ROOT, "gt_summaries"),# 输出：GT 汇总
        "datasets": os.path.join(TARGET_ROOT, "datasets")        # 索引表
    }
    for d in dirs.values():
        if os.path.exists(d): shutil.rmtree(d)
        os.makedirs(d)

    # 2. 读取划分好的清单
    source_dir = os.path.join(PROJECT_ROOT, "data", "dataset", FEEDME_NAME)
    split_csvs = ["train_samples.csv", "test_samples.csv"]

    # 定义双向映射
    # '字段名': '目标子目录'
    mapping = {
        # 输入部分
        'perturb_residual_path': 'images',
        'perturb_summary_path': 'summaries',
        'perturb_feedme_path': 'configs',
        # 真值部分 (GT)
        'true_residual_path': 'gt_images',
        'true_summary_path': 'gt_summaries'
    }

    print("🚀 开始全量迁移 (包含输入与 GT 真值文件)...")

    for csv_name in split_csvs:
        src_csv_path = os.path.join(source_dir, csv_name)
        if not os.path.exists(src_csv_path): continue

        df = pd.read_csv(src_csv_path)
        
        for idx, row in tqdm(df.iterrows(), total=len(df), desc=f"Processing {csv_name}"):
            for col, subdir in mapping.items():
                if col not in df.columns or pd.isna(row[col]): continue
                
                src_path = row[col]
                # 补全绝对路径
                full_src_path = src_path if os.path.isabs(src_path) else os.path.join(PROJECT_ROOT, src_path)

                if os.path.exists(full_src_path):
                    # 为了防止 GT 文件名重复，我们保留原始的 obj_id 命名
                    filename = os.path.basename(full_src_path)
                    
                    # 如果是 GT 文件，为了防止和扰动文件重名，可以加前缀，或者靠子目录区分
                    # 这里我们靠子目录区分即可：gt_images/obj1.png vs images/obj1_v0.png
                    dst_path = os.path.join(dirs[subdir], filename)
                    
                    if not os.path.exists(dst_path):
                        shutil.copy2(full_src_path, dst_path)
                    
                    # 【核心】：重写 CSV 路径，让它指向 A100 的新位置
                    df.at[idx, col] = os.path.join(subdir, filename)

        # 保存修改后的 CSV，现在这个 CSV 里的所有路径在 A100 上都能找到了
        df.to_csv(os.path.join(dirs["datasets"], csv_name), index=False)

    print(f"✅ 全量迁移完成！")
    print(f"📍 A100 目录已准备好: {TARGET_ROOT}")
    print(f"📁 已包含 gt_images 和 gt_summaries 文件夹。")

if __name__ == "__main__":
    transfer_with_gt()