# data_gen/dataset_utils.py
import os
import re
import glob
import random


def _to_physical_id(galaxy_id_or_obj_name: str) -> str:
    """从带波段的 galaxy_id 或目录名提取物理星系 ID。

    SDSS_rband_Plate0270_MJD51909_Fiber095_r → Plate0270_MJD51909_Fiber095
    Plate0270_MJD51909_Fiber095_r             → Plate0270_MJD51909_Fiber095
    """
    s = re.sub(r"^SDSS_[gri]band_", "", galaxy_id_or_obj_name)
    s = re.sub(r"_[gri]$", "", s)
    return s


def get_train_test_split(data_dir: str, num_test: int = 100, seed: int = 42) -> tuple:
    """
    扫描 Gadotti 数据目录 (例如 gadotti_data/SDSS_iband)，
    寻找所有的初始 galfit.feedme 文件，并划分测试集和训练集。

    注意：单波段调用时切分是独立的。多波段场景请用
    get_cross_band_train_test_split 保证同一物理星系在各波段归属一致。
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


def get_cross_band_train_test_split(gadotti_root: str, bands: list,
                                    num_test: int = 10, seed: int = 42) -> tuple:
    """跨波段一致切分：按物理星系 ID 统一划 train/test，再映射回各波段。

    保证同一颗物理星系在 g/r/i 三波段归属完全一致，杜绝跨波段数据泄露。

    Returns: (all_train, all_test)  每个元素是 {"id": "SDSS_rband_...", "init_feedme": ...}
    """
    # 1. 收集所有波段的星系
    band_galaxies = {}  # {band: [galaxy_dict, ...]}
    phys_to_bands = {}  # {physical_id: {band: galaxy_dict}}

    for band in bands:
        band_dir = os.path.join(gadotti_root, band)
        if not os.path.isdir(band_dir):
            print(f"  [WARN] 波段目录不存在，跳过: {band_dir}")
            continue
        train, test = get_train_test_split(band_dir, num_test=0, seed=seed)
        all_gals = train + test  # num_test=0 → all in train
        band_galaxies[band] = {g["id"]: g for g in all_gals}
        for g in all_gals:
            pid = _to_physical_id(g["id"])
            phys_to_bands.setdefault(pid, {})[band] = g

    # 2. 按物理 ID 统一切分
    all_phys_ids = sorted(phys_to_bands.keys())
    random.seed(seed)
    random.shuffle(all_phys_ids)

    if len(all_phys_ids) <= num_test:
        print(f"  [WARN] 物理星系总数 ({len(all_phys_ids)}) <= num_test ({num_test})，全部归为训练集")
        test_phys = set()
    else:
        test_phys = set(all_phys_ids[:num_test])

    # 3. 映射回各波段
    all_train, all_test = [], []
    for pid, band_map in sorted(phys_to_bands.items()):
        for band, gal in band_map.items():
            if pid in test_phys:
                all_test.append(gal)
            else:
                all_train.append(gal)

    band_counts = {b: sum(1 for g in all_train if g["id"].startswith(b))
                   for b in bands}
    test_band_counts = {b: sum(1 for g in all_test if g["id"].startswith(b))
                        for b in bands}
    print(f"  跨波段统一切分完成: 物理星系 {len(all_phys_ids)} 颗, "
          f"test {len(test_phys)} 颗(×{len(bands)}波段)")
    print(f"    训练集: {len(all_train)} (各波段: {band_counts})")
    print(f"    测评集: {len(all_test)} (各波段: {test_band_counts})")

    return all_train, all_test