import os
import json
from collections import Counter

def scan_expert_jsons(base_dir: str):
    """
    扫描目录下的所有 Gadotti_params.json，并进行全局统计分析
    """
    print(f"🔍 正在扫描目录: {base_dir}")
    json_files = []
    
    # 遍历所有子目录寻找目标 JSON
    for root, dirs, files in os.walk(base_dir):
        if "Gadotti_params.json" in files:
            json_files.append(os.path.join(root, "Gadotti_params.json"))
            
    total_files = len(json_files)
    if total_files == 0:
        print("⚠️ 未找到任何 Gadotti_params.json 文件，请检查路径。")
        return

    print(f"✅ 共找到 {total_files} 个星系专家标注数据！\n")

    # 统计容器
    mtype_counter = Counter()
    comp_count_counter = Counter()
    
    # 物理参数收集器 (用于计算均值)
    stats = {
        "bulge_n": [],
        "disk_n": [],
        "re_ratio": [] # re_bulge / re_disk
    }

    for path in json_files:
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                
            # 1. 统计星系主要形态分类
            mtype = data.get("MType", "unknown")
            mtype_counter[mtype] += 1
            
            # 2. 判定成分数量
            # 默认大多数系统都有核球 (即使是很小的 pseudo-bulge)，用 re_bulge > 0 判定最稳妥
            comp_count = 0
            if data.get("re_bulge", 0.0) > 0: comp_count += 1
            if data.get("re_disk", 0.0) > 0: comp_count += 1
            if data.get("re_bar", 0.0) > 0: comp_count += 1
            
            comp_count_counter[comp_count] += 1
            
            # 3. 收集物理参数规律
            if data.get("sersic_bulge", 0) > 0:
                stats["bulge_n"].append(data["sersic_bulge"])
                
            if data.get("sersic_disk", 0) > 0:
                stats["disk_n"].append(data["sersic_disk"])
                
            if data.get("re_disk", 0) > 0 and data.get("re_bulge", 0) > 0:
                # 收集核球与盘的尺寸比例
                stats["re_ratio"].append(data["re_bulge"] / data["re_disk"])
                
        except Exception as e:
            print(f"读取文件失败 {path}: {e}")

    # ==========================================
    # 打印最终的大盘战报
    # ==========================================
    print("🌟🌟🌟🌟🌟 [专家数据集全景大盘] 🌟🌟🌟🌟🌟")
    
    print("\n🎯 1. 成分数量分布 (Agent 将面临的拟合复杂度):")
    for count in sorted(comp_count_counter.keys()):
        num = comp_count_counter[count]
        ratio = (num / total_files) * 100
        print(f"   - {count} 成分星系: {num} 个 ({ratio:.1f}%)")
        
    print("\n🧬 2. 专家形态分类 (MType 分布):")
    for mtype, count in mtype_counter.most_common():
        ratio = (count / total_files) * 100
        print(f"   - {mtype}: {count} 个 ({ratio:.1f}%)")
        
    print("\n📈 3. 物理参数先验提炼 (Proposal Distribution 的核心基石):")
    if stats["bulge_n"]:
        avg_bulge_n = sum(stats["bulge_n"]) / len(stats["bulge_n"])
        print(f"   - 核球 Sersic 指数 (n) 平均值: {avg_bulge_n:.3f}")
        
    if stats["disk_n"]:
        avg_disk_n = sum(stats["disk_n"]) / len(stats["disk_n"])
        print(f"   - 星系盘 Sersic 指数 (n) 平均值: {avg_disk_n:.3f}")
        
    if stats["re_ratio"]:
        avg_ratio = sum(stats["re_ratio"]) / len(stats["re_ratio"])
        print(f"   - 尺度比例 (Re_bulge / Re_disk) 平均值: {avg_ratio:.3f}")

# inspect_json_keys.py
import os
import json

def inspect_all_json_keys(base_dir: str):
    """
    遍历整个数据集目录，提取并去重 Gadotti_params.json 中的所有键名
    """
    print(f"🔍 开始全局扫描目录: {base_dir}")
    all_keys = {}
    total_files = 0
    
    for root, dirs, files in os.walk(base_dir):
        if "Gadotti_params.json" in files:
            total_files += 1
            json_path = os.path.join(root, "Gadotti_params.json")
            try:
                with open(json_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    for key, value in data.items():
                        if key not in all_keys:
                            # 记录键名及其对应的数据类型名
                            all_keys[key] = type(value).__name__
            except Exception as e:
                print(f"❌ 读取失败 {json_path}: {e}")
                
    print(f"\n📊 扫描完毕！共成功审计 {total_files} 个专家文件。")
    print("📋 Gadotti_params.json 全量键名对账星表如下：")
    print("=" * 60)
    for k, v in sorted(all_keys.items()):
        print(f"  \"{k}\": {v}")
    print("=" * 60)


if __name__ == "__main__":
    # 请将这里的路径替换为你存放 gadotti_data 的绝对路径或相对路径
    # 例如：BASE_DATA_DIR = "./gadotti_data" 
    BASE_DATA_DIR = "/media/zhongling/wyh/GalDecomp_Gen/gadotti_data"
    
    # scan_expert_jsons(BASE_DATA_DIR)

    inspect_all_json_keys(BASE_DATA_DIR)