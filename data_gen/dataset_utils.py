# data_gen/dataset_utils.py
import os
import glob
import random

def get_train_test_split(data_dir: str, num_test: int = 100, seed: int = 42) -> tuple:
    """
    扫描 Gadotti 数据目录 (例如 gadotti_data/SDSS_iband)，
    寻找所有的初始 galfit.feedme 文件，并划分测试集和训练集。
    """
    # 明确锁定 Gadotti 的初始配置文件名
    search_pattern = os.path.join(data_dir, "**", "galfit.feedme")
    all_files = glob.glob(search_pattern, recursive=True)
    
    # 物理防撞：剔除那些可能被错误放置在档案(archives)或输出目录下的污染文件
    clean_files = [f for f in all_files if "archives" not in f and "output" not in f]

    galaxies = []
    for f in clean_files:
        # 路径形如: .../SDSS_iband/Plate0350_MJD51691_Fiber164_i/galfit.feedme
        parent_dir = os.path.dirname(f)
        obj_name = os.path.basename(parent_dir)  # 例如: Plate0350_MJD51691_Fiber164_i
        band_name = os.path.basename(os.path.dirname(parent_dir)) # 例如: SDSS_iband
        
        # 组装唯一 ID，格式如：SDSS_iband_Plate0350_...
        # 这保证了当系统后续同时处理 g/r/i 波段时，输出结果绝对不会互相覆盖
        gal_id = f"{band_name}_{obj_name}"
        
        galaxies.append({
            "id": gal_id, 
            "init_feedme": os.path.abspath(f) # 绝对路径，保证后续跨目录不迷路
        })

    # 固定随机种子，保证每次运行划分出来的训练集/测试集是一模一样的
    galaxies.sort(key=lambda x: x["id"]) 
    random.seed(seed)
    random.shuffle(galaxies)
    
    total_count = len(galaxies)
    if total_count == 0:
        return [], []
        
    if total_count < num_test:
        print(f"⚠️ 警告: 该波段下找到的星系总数量 ({total_count}) 小于设定的测试集要求 ({num_test})。将全部作为训练集。")
        return galaxies, []

    test_set = galaxies[:num_test]
    train_set = galaxies[num_test:]
    
    return train_set, test_set