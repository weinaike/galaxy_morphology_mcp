import os
import re
import csv
import json
import numpy as np
import argparse
from collections import defaultdict
from sklearn.model_selection import train_test_split
from skimage import io, color, transform
from skimage.metrics import structural_similarity as ssim
from tqdm import tqdm
from concurrent.futures import ProcessPoolExecutor
import multiprocessing

# ================= 0. 命令行参数解析 =================
parser = argparse.ArgumentParser()
parser.add_argument("--step", type=int, required=True, help="The GALFIT iteration step to process")
parser.add_argument("--feedme_name", type=str, required=True, help="Feedme name")
args = parser.parse_args()
STEP = args.step

# ================= 1. 配置区 =================
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")

PERTURB_FEEDME_DIR = os.path.join(DATA_DIR, "perturbed", args.feedme_name)
PERTURB_LOG_CSV = os.path.join(PERTURB_FEEDME_DIR, "all_galfit_perturbation_log.csv")
ORIGINAL_ROOT = os.path.join(DATA_DIR, "galfit_sample_0204")
RESIDUAL_DIR = os.path.join(DATA_DIR, "galfit_results", f"{args.feedme_name}_step_{STEP}_results")

OUT_DIR = os.path.join(DATA_DIR, "dataset", f"{args.feedme_name}")
OUTPUT_CSV = os.path.join(OUT_DIR, f"dataset_all_step_{STEP}.csv")
TRAIN_CSV = os.path.join(OUT_DIR, f"train_samples_step_{STEP}.csv")
TEST_CSV = os.path.join(OUT_DIR, f"test_samples_step_{STEP}.csv")

SPLIT_TRAIN_TEST = True
TEST_SIZE = 0.2
RANDOM_SEED = 42
SSIM_THRESHOLD = 0.98

# 【极速优化 1】全局共享缓存，彻底消除 IPC 进程间通信的 Pickling 开销！
GLOBAL_SOURCE_CACHE = {}

# ================= 2. 辅助函数与极速缓存 =================
def extract_source(sample_id):
    return re.sub(r'_v\d+$', '', sample_id)

def build_source_dir_cache():
    cache = {}
    print("[*] 正在构建全局目录缓存...")
    for root, dirs, files in os.walk(ORIGINAL_ROOT):
        for d in dirs:
            cache[d] = os.path.join(root, d)
    print(f"[*] 缓存构建完成，包含 {len(cache)} 个源星系.")
    return cache

def find_original_files(source):
    # 直接读取全局缓存
    source_dir = GLOBAL_SOURCE_CACHE.get(source)
    if not source_dir:
        return None, None
    
    summary_path = None
    for cand in [f"{source}_summary.md", f"{source}_re_summary.md", "summary.md"]:
        p = os.path.join(source_dir, cand)
        if os.path.exists(p):
            summary_path = p
            break
            
    png_path = os.path.join(source_dir, f"{source}_comparison.png")
    if not os.path.exists(png_path):
        png_path = None
            
    return summary_path, png_path

def find_perturbed_png(sample_id, source):
    png_path = os.path.join(RESIDUAL_DIR, sample_id, f"{sample_id}_comparison.png")
    return png_path if os.path.exists(png_path) else None

def find_perturbed_summary(sample_id, source):
    candidates = [
        os.path.join(RESIDUAL_DIR, sample_id, f"{sample_id}_summary.md"), # 加上这一行
        os.path.join(RESIDUAL_DIR, sample_id, f"{source}_summary.md"),
        os.path.join(RESIDUAL_DIR, sample_id, "summary.md"),
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    return ""

def parse_image_path_from_feedme(feedme_path):
    try:
        with open(feedme_path, 'r') as f:
            for line in f:
                if line.strip().startswith('A)'):
                    parts = line.strip().split(None, 1)
                    if len(parts) >= 2:
                        path = parts[1].split('#')[0].strip()
                        abs_path = os.path.normpath(os.path.join(os.path.dirname(feedme_path), path))
                        return abs_path if os.path.exists(abs_path) else path
    except Exception:
        pass
    return ""

# ================= 3. 核心评估模块 (SSIM) =================
def check_perturbation_effect(true_img_path, perturb_img_path):
    try:
        img_true = io.imread(true_img_path)
        img_perturb = io.imread(perturb_img_path)
        
        if img_true.ndim == 3 and img_true.shape[2] == 4:
            img_true = img_true[..., :3]
        if img_perturb.ndim == 3 and img_perturb.shape[2] == 4:
            img_perturb = img_perturb[..., :3]

        if img_true.ndim == 3: img_true = color.rgb2gray(img_true)
        if img_perturb.ndim == 3: img_perturb = color.rgb2gray(img_perturb)
            
        if img_true.shape != img_perturb.shape:
            img_perturb = transform.resize(img_perturb, img_true.shape, anti_aliasing=False)

        score = ssim(img_true, img_perturb, data_range=1.0)
        return score, bool(score < SSIM_THRESHOLD)
    except Exception as e:
        return -1.0, False

# ================= 4. 多进程处理核心 worker =================
def process_single_variant_wrapper(args):
    """【极速优化 2】剥离了沉重的缓存字典，只传递最轻量的字符串参数"""
    sample_id, param_records = args
    source = extract_source(sample_id)
    
    result = {'status': 'missing', 'data': None}
    
    perturb_feedme_path = os.path.join(PERTURB_FEEDME_DIR, f"{sample_id}.feedme")
    true_summary_path, true_img_path = find_original_files(source) # 内部直接查全局字典
    perturb_img_path = find_perturbed_png(sample_id, source)
    
    if not (true_summary_path and true_img_path and perturb_img_path):
        return result

    image_path = parse_image_path_from_feedme(perturb_feedme_path)
    perturb_summary_path = find_perturbed_summary(sample_id, source)

    ssim_score, is_effective = check_perturbation_effect(true_img_path, perturb_img_path)
    result['status'] = 'effective' if is_effective else 'ineffective'

    overall_mode = param_records[0].get('Mode', 'unknown') if param_records else 'unknown'
    parameters = [{
        'comp': r['Comp'], 'param': r['Param'], 'orig': float(r['Orig']), 
        'perturb': float(r['New']), 'diff': r['Diff'], 'mode': r['Mode']
    } for r in param_records]

    delta_package = {'sample_id': sample_id, 'mode': overall_mode, 'parameters': parameters}

    result['data'] = {
        'sample_id': sample_id,
        'source': source,
        'image_path': image_path,                     
        'perturb_feedme_path': perturb_feedme_path,
        'true_summary_path': true_summary_path,
        'perturb_summary_path': perturb_summary_path, 
        'true_residual_path': true_img_path,         
        'perturb_residual_path': perturb_img_path,   
        'ssim_score': round(float(ssim_score), 4),
        'is_effective': int(is_effective),
        'delta_params': json.dumps(delta_package, ensure_ascii=False)
    }
    return result

# ================= 5. 主干流程 (多进程版) =================
def main():
    global GLOBAL_SOURCE_CACHE # 声明使用全局变量
    
    print(f"DEBUG: BASE_DIR is {BASE_DIR}")
    print(f"DEBUG: Looking for images in {RESIDUAL_DIR}")

    os.makedirs(OUT_DIR, exist_ok=True)

    print(f"[*] Loading perturbation log from {PERTURB_LOG_CSV}")
    if not os.path.exists(PERTURB_LOG_CSV):
        raise FileNotFoundError(f"Cannot find {PERTURB_LOG_CSV}")
        
    with open(PERTURB_LOG_CSV, 'r') as f:
        rows = list(csv.DictReader(f))

    variant_groups = defaultdict(list)
    for row in rows:
        v_file = row['File'].replace(".feedme", "").replace("_summary.md", "") 
        variant_groups[v_file].append(row)

    print(f"[*] Found {len(variant_groups)} unique perturbation variants.")

    # 构建全局缓存，供所有子进程零拷贝读取
    GLOBAL_SOURCE_CACHE = build_source_dir_cache()

    # ====== 启动多进程并发 ======
    # 留下 4 个核心，保护服务器网络和编辑器进程不卡死
    max_workers = max(6, multiprocessing.cpu_count() - 2)
    print(f"🚀 Firing up {max_workers} CPU cores for SSIM multiprocessing...")

    samples = []
    stats = {"total": 0, "effective": 0, "ineffective": 0, "missing": 0}

    # 只打包轻量级数据：ID 和 记录
    task_args = [(sid, records) for sid, records in variant_groups.items()]
    
    # 【极速优化 3】使用 chunksize=50 进行批处理分配，杜绝队列拥堵假死
    with ProcessPoolExecutor(max_workers=max_workers) as executor:
        # executor.map 配合 tqdm 完美展示进度，且比 submit 速度快非常多
        results = list(tqdm(executor.map(process_single_variant_wrapper, task_args, chunksize=50), 
                            total=len(task_args), desc="SSIM 计算与数据封装"))

    # 汇总结果
    for res in results:
        stats["total"] += 1
        status = res['status']
        if status == 'missing':
            stats["missing"] += 1
        else:
            if status == 'effective':
                stats["effective"] += 1
            else:
                stats["ineffective"] += 1
            samples.append(res['data'])

    # ================= 6. 输出与落盘 =================
    print("\n" + "="*40)
    print(f"📊 Processing Summary:")
    print(f"  - Total processed: {stats['total']}")
    print(f"  - Missing files: {stats['missing']}")
    print(f"  - ✅ Effective (SSIM < {SSIM_THRESHOLD}): {stats['effective']}")
    print(f"  - ❌ Ineffective (Filtered): {stats['ineffective']}")
    print("="*40 + "\n")

    fieldnames = ['sample_id', 'source', 'image_path', 'perturb_feedme_path', 'true_summary_path', 
                  'perturb_summary_path', 'true_residual_path', 'perturb_residual_path', 
                  'ssim_score', 'is_effective', 'delta_params']
                  
    with open(OUTPUT_CSV, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(samples)
    print(f"[*] All samples saved to -> {OUTPUT_CSV}")

    valid_samples = [s for s in samples if s['is_effective'] == 1]

    if SPLIT_TRAIN_TEST and valid_samples:
        sources = list({s['source'] for s in valid_samples})
        train_sources, test_sources = train_test_split(sources, test_size=TEST_SIZE, random_state=RANDOM_SEED)
        
        train_samples = [s for s in valid_samples if s['source'] in train_sources]
        test_samples = [s for s in valid_samples if s['source'] in test_sources]

        with open(TRAIN_CSV, 'w', newline='') as f:
            csv.DictWriter(f, fieldnames=fieldnames).writeheader()
            csv.DictWriter(f, fieldnames=fieldnames).writerows(train_samples)
        with open(TEST_CSV, 'w', newline='') as f:
            csv.DictWriter(f, fieldnames=fieldnames).writeheader()
            csv.DictWriter(f, fieldnames=fieldnames).writerows(test_samples)

        print(f"[*] Train set (Effective only): {len(train_samples)} rows -> {TRAIN_CSV}")
        print(f"[*] Test set (Effective only):  {len(test_samples)} rows -> {TEST_CSV}")

def test_image_info():
    from skimage import io
    img_path = "/mnt/data/wyh/galaxy_morphology_mcp/galfit_sample_0204/COS/cosmos_38/cosmos_38_comparison.png"
    img = io.imread(img_path)
    print(f"图像维度 (ndim): {img.ndim}") # 3
    print(f"图像形状 (shape): {img.shape}") # (409, 1024, 4)
    print(f"数据类型 (dtype): {img.dtype}") # uint8
    return

def test_alpha_channel():
    import matplotlib.pyplot as plt
    from skimage import io
    import numpy as np
    img_path = "/mnt/data/wyh/galaxy_morphology_mcp/galfit_sample_0204/COS/cosmos_38/cosmos_38_comparison.png"
    # 读取 4 通道 RGBA 图像
    img = io.imread(img_path)
    # 切片提取第 4 个通道（索引为 3）
    alpha_channel = img[:, :, 3]
    # 打印一下这层通道里到底有哪些数值？
    print(f"Alpha 通道里包含的数值种类有: {np.unique(alpha_channel)}")
    # 把它画出来看看
    plt.figure(figsize=(10, 4))
    plt.imshow(alpha_channel, cmap='gray')
    plt.colorbar(label='Transparency (0=透明, 255=完全不透明)')
    plt.title("The 4th Channel (Alpha Transparency Mask)")
    plt.show()
    save_path = "alpha_channel_check.png"
    plt.savefig(save_path, dpi=150, bbox_inches='tight')
    print(f"✅ 成功！请在当前目录下查看生成的验证图片: {os.path.abspath(save_path)}")


if __name__ == "__main__":
    # 强制设置多进程启动方式为 fork（在 Linux 下更快更安全）
    try:
        multiprocessing.set_start_method('fork')
    except RuntimeError:
        pass
    main()