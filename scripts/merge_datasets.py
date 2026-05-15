import os
import glob
import pandas as pd
import argparse

parser = argparse.ArgumentParser()
parser.add_argument("--feedme_name", type=str, required=True, help="Feedme name")
args = parser.parse_args()

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 完美指向 data/dataset/perturbed_ii_feedme_wo_fix
DATASET_DIR = os.path.join(BASE_DIR, "data", "dataset", args.feedme_name)

def merge_csvs(pattern, output_name):
    print(f"🔄 正在搜索合并: {pattern}")
    all_files = glob.glob(os.path.join(DATASET_DIR, pattern))
    
    if not all_files:
        print(f"⚠️ 未找到匹配的文件: {pattern} (搜索路径: {DATASET_DIR})")
        return
    else:
        print(f"搜索到匹配文件：{all_files}(搜索路径: {DATASET_DIR})")

    # 读取所有匹配的 CSV 并合并
    df_list = [pd.read_csv(f) for f in all_files]
    merged_df = pd.concat(df_list, ignore_index=True)
    
    # 保存终极版
    output_path = os.path.join(DATASET_DIR, output_name)
    merged_df.to_csv(output_path, index=False)
    print(f"✅ 成功合并 {len(all_files)} 个文件 -> {output_path} (共 {len(merged_df)} 条数据)")

if __name__ == "__main__":
    print("========================================")
    print("🌟 开始执行多阶梯数据集大合并")
    print("========================================")
    merge_csvs("train_samples_step_*.csv", "train_samples.csv")
    merge_csvs("test_samples_step_*.csv", "test_samples.csv")
    # merge_csvs("dataset_all_step_*.csv", "dataset_all.csv") # 顺手把总表也合并了！
    print("🎉 合并完成！可以直接喂给大模型了。")