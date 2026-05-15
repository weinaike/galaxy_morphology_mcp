import os
import re
import glob
import numpy as np
import random
import csv
import shutil
import argparse

# ================= 1. 配置区 =================
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
ROOT_DIR_LIST = [
    os.path.join(DATA_DIR, "galfit_sample_0204", "COS"), 
    os.path.join(DATA_DIR, "galfit_sample_0204", "EGS"),
    os.path.join(DATA_DIR, "galfit_sample_0204", "GOODSN"),
    os.path.join(DATA_DIR, "galfit_sample_0204", "UDS")
]
CSV_LOG_NAME = "all_galfit_perturbation_log.csv"
BIAS_MODE_PROB = 0.5

# [新增] FIX 状态改变全局开关
# 注意：标准 GALFIT 中，0 代表 Free(释放)，1 代表 Fixed(固定)。这与 GalfitS 的 .lyric 刚好相反！
# False: 只扰动数值，严格保持原始的 Fix 状态不变。
# True: 启用针对邻近源的 Fix 状态转换逻辑 (base/real/fixed)。
ENABLE_FIX_STATE_CHANGES = False 

# 邻近源三种整体策略概率（总和为1）
NEIGHBOR_STRATEGY_PROBS = {
    "base": 0.4,       # 不扰动 + 固定 (Fix=1)
    "real": 0.3,       # 扰动 + 释放 (Fix=0)
    "fixed": 0.3,      # 扰动 + 固定 (Fix=1)
    "free_true": 0.0   # 不扰动 + 释放（预留，暂不启用）
}

LEVEL_PROBS = {"none": 0.20, "easy": 0.26, "medium": 0.27, "hard": 0.27}

# 难度参数（天空背景 sigma 为相对百分比的小数）
DIFFICULTY_SETTINGS = {
    "none":   {"re":0, "n":0, "pa":0, "q":0, "mag":0, "pos_rand":0, "sky":0},
    "easy":   {"re":0.2, "n":0.5, "pa":5.0, "q":0.05, "mag":0.1, "pos_rand":1.0, "sky":0.1},
    "medium": {"re":0.4, "n":1.0, "pa":20.0, "q":0.1, "mag":0.3, "pos_rand":3.0, "sky":0.3},
    "hard":   {"re":0.45, "n":2.0, "pa":90.0, "q":0.2, "mag":0.8, "pos_rand":5.0, "sky":0.5}
}

# 偏差模式方向修正：Re偏小（负均值），Mag偏亮（负均值）
BIAS_RE_MEAN = -0.15
BIAS_MAG_MEAN = -0.2

def ensure_dir(d):
    if os.path.exists(d): shutil.rmtree(d)
    os.makedirs(d)

def pick_level():
    return np.random.choice(list(LEVEL_PROBS.keys()), p=list(LEVEL_PROBS.values()))

# ================= 2. 路径清洗逻辑（保留但未使用） =================
def rewrite_header_paths(line, object_name, suffix=""):
    stripped = line.strip()
    match = re.match(r'^([A-Z])\)\s+(.*?)(?:\s+#.*)?$', stripped)
    if match:
        label = match.group(1)
        original_path = match.group(2).strip()
        filename = os.path.basename(original_path)

        if label == 'B':
            base, ext = os.path.splitext(filename)
            if suffix and suffix not in base:
                new_filename = f"{base}_{suffix}{ext}"
            else:
                new_filename = filename
            new_path = f"../galfit_tmp/{new_filename}"
            return f"{label}) {new_path}  # Output data image block\n"

        if label == 'D':
            new_path = "../f160w_psf.fits"
            return f"D) {new_path}  # Input PSF image\n"

        if label == 'G':
            return "# G) constraints omitted (no constraints used)\n"

        if label in ['A', 'C', 'F']:
            new_path = f"../galfit_sample_0204/**/{object_name}/{filename}"
            comment = ""
            if "#" in line:
                comment = "  # " + line.split("#", 1)[1].strip()
            return f"{label}) {new_path}{comment}\n"
    return line

# ================= 3. 解析 _summary.md 中的真值（修正正则）=================
def parse_summary(summary_path):
    true_params = {}
    with open(summary_path, 'r') as f:
        content = f.read()

    fit_log_match = re.search(r'## Fit log Content\n(.*?)(?=\n---|\Z)', content, re.DOTALL)
    if not fit_log_match:
        raise ValueError(f"Could not find Fit log Content in {summary_path}")
    fit_log = fit_log_match.group(1)

    sersic_lines = re.findall(r'^\s*sersic\s*:\s*\(([^)]+)\)\s+([\d\.\-eE]+)\s+([\d\.\-eE]+)\s+([\d\.\-eE]+)\s+([\d\.\-eE]+)\s+([\d\.\-eE]+)', fit_log, re.MULTILINE)
    for idx, match in enumerate(sersic_lines):
        xy_str, mag, re_, n, q, pa = match
        xy_parts = xy_str.split(',')
        x = float(xy_parts[0].strip())
        y = float(xy_parts[1].strip())
        true_params[f"sersic_{idx}"] = {
            "x": x, "y": y,
            "mag": float(mag),
            "re": float(re_),
            "n": float(n),
            "q": float(q),
            "pa": float(pa)
        }

    sky_line = re.search(r'^ sky\s*:\s*\[[^\]]+\]\s+([\d\.\-eE]+)', fit_log, re.MULTILINE)
    if sky_line:
        true_params["sky"] = {"sky": float(sky_line.group(1))}
    else:
        true_params["sky"] = {"sky": 0.0}

    return true_params

# ================= 4. 核心扰动逻辑 =================
def perturb_val(val, p_type, level, is_bias):
    val = float(val)
    if level == "none": return val, "None"
    sigma = DIFFICULTY_SETTINGS[level]
    noise, note = 0.0, "0.0"

    if p_type == "re":
        if is_bias:
            noise = np.random.normal(BIAS_RE_MEAN, sigma["re"])
        else:
            noise = np.random.normal(0, sigma["re"])
        f = np.exp(noise)
        if f > 2.5:
            f = 2.5
            note = f"*{f:.4f} (clipped)"
        else:
            note = f"*{f:.4f}"
        new_val = val * f

    elif p_type == "mag":
        if is_bias:
            noise = np.random.normal(BIAS_MAG_MEAN, sigma["mag"])
        else:
            noise = np.random.normal(0, sigma["mag"])
        new_val = val + noise
        note = f"{noise:+.4f}"

    elif p_type == "n":
        noise = np.random.normal(0, sigma["n"])
        new_val = np.clip(val + noise, 0.2, 8.0)
        note = f"{new_val-val:+.4f}"

    elif p_type == "q":
        if is_bias:
            noise = np.abs(np.random.normal(0, sigma["q"]))
            new_val = min(1.0, val + noise)
        else:
            noise = np.random.normal(0, sigma["q"])
            new_val = val + noise
        new_val = np.clip(new_val, 0.05, 1.0)
        note = f"{new_val-val:+.4f}"

    elif p_type == "pa":
        if level == "hard" and random.random() < 0.5:
            noise = 90.0
            new_val = (val + noise) % 180 - 90
            return new_val, "FLIP"
        else:
            noise = np.random.uniform(-sigma["pa"], sigma["pa"])
            new_val = (val + noise) % 180 - 90
            note = f"{noise:+.4f}"
            return new_val, note

    elif p_type == "pos":
        noise = np.random.normal(0, sigma["pos_rand"])
        new_val = val + noise
        new_val = np.clip(new_val, val - 5.0, val + 5.0)
        note = f"{noise:+.4f}"

    elif p_type == "sky":
        sky_sigma = sigma["sky"]
        noise = np.random.normal(0, sky_sigma)
        new_val = val + noise
        note = f"{noise:+.2f} (abs)"
    
    return new_val, note

# ================= 5. 处理单个源（.feedme + _summary.md） =================
def process_single_source(feedme_path, summary_path, all_logs, num_variants, output_dir):
    parent_dir = os.path.dirname(feedme_path)
    object_name = os.path.basename(parent_dir)
    filename = os.path.basename(feedme_path)
    base_name = os.path.splitext(filename)[0]
    object_id = base_name if "cosmos" in base_name else object_name

    with open(feedme_path, 'r') as f:
        feedme_lines = f.readlines()

    true_params = parse_summary(summary_path)
    num_sersic = sum(1 for line in feedme_lines if line.strip().startswith("0) sersic"))

    for v_idx in range(num_variants):
        suffix = f"v{v_idx}"
        is_bias = random.random() < BIAS_MODE_PROB
        mode_name = "Bias" if is_bias else "Random"

        comp_levels = {}
        for comp_idx in range(num_sersic):
            comp_levels[comp_idx] = {
                "GroupA": pick_level(),
                "GroupB": pick_level(),
                "GroupC_X": pick_level(),
                "GroupC_Y": pick_level()
            }
        sky_level = pick_level() if "sky" in true_params else "none"

        neighbor_strategy = {}
        for comp_idx in range(1, num_sersic):
            strat = np.random.choice(list(NEIGHBOR_STRATEGY_PROBS.keys()), p=list(NEIGHBOR_STRATEGY_PROBS.values()))
            neighbor_strategy[comp_idx] = strat

        out_lines = []
        current_comp_idx = -1
        inside_sersic = False
        inside_sky = False

        for line in feedme_lines:
            stripped = line.strip()

            # if re.match(r'^[A-Z]\)', stripped):
            #     out_lines.append(line)
            #     continue
            
            if re.match(r'^[A-Z]\)', stripped):
                # =========================================================
                # 为新生成的 feedme 写入基于 data/perturbed/xxx/ 的完美相对路径
                # =========================================================
                if line.startswith("A)") or line.startswith("C)") or line.startswith("F)"):
                    # 匹配原图、Sigma图、Mask图：提取 COS/cosmos_123/cosmos_123.fits
                    match = re.search(r'(COS|EGS|GOODSN|UDS)/([^/]+)/([^/\s#]+)', line)
                    if match:
                        out_lines.append(f"{line[0]}) ../../galfit_sample_0204/{match.group(0)}  # Input image\n")
                    else:
                        out_lines.append(line)
                elif line.startswith("B)"):
                    # 输出目标文件：扔到往上退 3 层的根目录 galfit_tmp/ 中
                    # 这里极其巧妙：使用 base_name_suffix 赋予每个变体独立名字，极大降低文件冲突率！
                    out_lines.append(f"B) ../../../galfit_tmp/{base_name}_{suffix}.fits  # Output FITS\n")
                elif line.startswith("D)"):
                    # PSF 星芒卷积核
                    out_lines.append("D) ../../galfit_sample_0204/f160w_psf.fits  # PSF\n")
                elif line.startswith("G)"):
                    # 约束文件
                    out_lines.append("G) ../../galfit_sample_0204/GALFIT.con  # Constraints\n")
                else:
                    out_lines.append(line)
                continue

            if re.match(r'^0\)\s+\w+', stripped):
                parts = stripped.split()
                if parts[1] == "sersic":
                    current_comp_idx += 1
                    inside_sersic = True
                    inside_sky = False
                elif parts[1] == "sky":
                    inside_sky = True
                    inside_sersic = False
                else:
                    inside_sersic = False
                    inside_sky = False
                out_lines.append(line)
                continue

            if inside_sersic:
                comp_true = true_params.get(f"sersic_{current_comp_idx}")
                if comp_true is None:
                    out_lines.append(line)
                    continue

                is_target = (current_comp_idx == 0)
                if not is_target:
                    strategy = neighbor_strategy.get(current_comp_idx, "base")

                # 1) 位置行
                if stripped.startswith("1)"):
                    parts = stripped.split()
                    orig_fix_x = int(parts[3])
                    orig_fix_y = int(parts[4])
                    x_true = comp_true["x"]
                    y_true = comp_true["y"]

                    if is_target:
                        eff_lvl_x = comp_levels[current_comp_idx]["GroupC_X"]
                        eff_lvl_y = comp_levels[current_comp_idx]["GroupC_Y"]
                        new_x, nx = perturb_val(x_true, "pos", eff_lvl_x, is_bias)
                        new_y, ny = perturb_val(y_true, "pos", eff_lvl_y, is_bias)
                        new_fix_x, new_fix_y = orig_fix_x, orig_fix_y
                    else:
                        if strategy == "base":  
                            new_x, nx = x_true, "None"
                            new_y, ny = y_true, "None"
                            new_fix_x, new_fix_y = 1, 1 # 固定
                        elif strategy == "real":  
                            eff_lvl_x = comp_levels[current_comp_idx]["GroupC_X"]
                            eff_lvl_y = comp_levels[current_comp_idx]["GroupC_Y"]
                            new_x, nx = perturb_val(x_true, "pos", eff_lvl_x, False)
                            new_y, ny = perturb_val(y_true, "pos", eff_lvl_y, False)
                            new_fix_x, new_fix_y = 0, 0 # 释放
                        else:  # fixed
                            eff_lvl_x = comp_levels[current_comp_idx]["GroupC_X"]
                            eff_lvl_y = comp_levels[current_comp_idx]["GroupC_Y"]
                            new_x, nx = perturb_val(x_true, "pos", eff_lvl_x, False)
                            new_y, ny = perturb_val(y_true, "pos", eff_lvl_y, False)
                            new_fix_x, new_fix_y = 1, 1 # 固定

                    if not ENABLE_FIX_STATE_CHANGES:
                        new_fix_x, new_fix_y = orig_fix_x, orig_fix_y

                    out_lines.append(f" 1) {new_x:.4f}  {new_y:.4f}  {new_fix_x}  {new_fix_y}  # Position x, y\n")
                    all_logs.append({"File":f"{base_name}_{suffix}", "Comp":f"obj{current_comp_idx}", "Param":"PosX", "Orig":f"{x_true:.4f}", "New":f"{new_x:.4f}", "Diff":nx, "Mode":mode_name})
                    all_logs.append({"File":f"{base_name}_{suffix}", "Comp":f"obj{current_comp_idx}", "Param":"PosY", "Orig":f"{y_true:.4f}", "New":f"{new_y:.4f}", "Diff":ny, "Mode":mode_name})
                    continue

                # 3) Mag
                if stripped.startswith("3)"):
                    parts = stripped.split()
                    val_true = comp_true["mag"]
                    orig_fix = int(parts[2])

                    if is_target:
                        eff_level = comp_levels[current_comp_idx]["GroupA"]
                        new_val, note = perturb_val(val_true, "mag", eff_level, is_bias)
                        new_fix = orig_fix
                    else:
                        if strategy == "base":
                            new_val, note = val_true, "None"
                            new_fix = 1 # 固定
                        elif strategy == "real":
                            eff_level = comp_levels[current_comp_idx]["GroupA"]
                            new_val, note = perturb_val(val_true, "mag", eff_level, False)
                            new_fix = 0 # 释放
                        else:  # fixed
                            eff_level = comp_levels[current_comp_idx]["GroupA"]
                            new_val, note = perturb_val(val_true, "mag", eff_level, False)
                            new_fix = 1 # 固定

                    if not ENABLE_FIX_STATE_CHANGES:
                        new_fix = orig_fix

                    out_lines.append(f" 3) {new_val:.4f}     {new_fix}       # Integrated magnitude\n")
                    all_logs.append({"File":f"{base_name}_{suffix}", "Comp":f"obj{current_comp_idx}", "Param":"Mag", "Orig":val_true, "New":f"{new_val:.4f}", "Diff":note, "Mode":mode_name})
                    continue

                # 4) Re
                if stripped.startswith("4)"):
                    parts = stripped.split()
                    val_true = comp_true["re"]
                    orig_fix = int(parts[2])

                    if is_target:
                        eff_level = comp_levels[current_comp_idx]["GroupA"]
                        new_val, note = perturb_val(val_true, "re", eff_level, is_bias)
                        new_fix = orig_fix
                    else:
                        if strategy == "base":
                            new_val, note = val_true, "None"
                            new_fix = 1
                        elif strategy == "real":
                            eff_level = comp_levels[current_comp_idx]["GroupA"]
                            new_val, note = perturb_val(val_true, "re", eff_level, False)
                            new_fix = 0
                        else:
                            eff_level = comp_levels[current_comp_idx]["GroupA"]
                            new_val, note = perturb_val(val_true, "re", eff_level, False)
                            new_fix = 1

                    if not ENABLE_FIX_STATE_CHANGES:
                        new_fix = orig_fix

                    out_lines.append(f" 4) {new_val:.4f}      {new_fix}       # R_e (effective radius)   [pix]\n")
                    all_logs.append({"File":f"{base_name}_{suffix}", "Comp":f"obj{current_comp_idx}", "Param":"Re", "Orig":val_true, "New":f"{new_val:.4f}", "Diff":note, "Mode":mode_name})
                    continue

                # 5) n
                if stripped.startswith("5)"):
                    parts = stripped.split()
                    val_true = comp_true["n"]
                    orig_fix = int(parts[2])

                    if is_target:
                        eff_level = comp_levels[current_comp_idx]["GroupA"]
                        new_val, note = perturb_val(val_true, "n", eff_level, is_bias)
                        new_fix = orig_fix
                    else:
                        if strategy == "base":
                            new_val, note = val_true, "None"
                            new_fix = 1
                        elif strategy == "real":
                            eff_level = comp_levels[current_comp_idx]["GroupA"]
                            new_val, note = perturb_val(val_true, "n", eff_level, False)
                            new_fix = 0
                        else:
                            eff_level = comp_levels[current_comp_idx]["GroupA"]
                            new_val, note = perturb_val(val_true, "n", eff_level, False)
                            new_fix = 1

                    if not ENABLE_FIX_STATE_CHANGES:
                        new_fix = orig_fix

                    out_lines.append(f" 5) {new_val:.4f}       {new_fix}       # Sersic index n\n")
                    all_logs.append({"File":f"{base_name}_{suffix}", "Comp":f"obj{current_comp_idx}", "Param":"n", "Orig":val_true, "New":f"{new_val:.4f}", "Diff":note, "Mode":mode_name})
                    continue

                # 9) q
                if stripped.startswith("9)"):
                    parts = stripped.split()
                    val_true = comp_true["q"]
                    orig_fix = int(parts[2])

                    if is_target:
                        eff_level = comp_levels[current_comp_idx]["GroupB"]
                        new_val, note = perturb_val(val_true, "q", eff_level, is_bias)
                        new_fix = orig_fix
                    else:
                        if strategy == "base":
                            new_val, note = val_true, "None"
                            new_fix = 1
                        elif strategy == "real":
                            eff_level = comp_levels[current_comp_idx]["GroupB"]
                            new_val, note = perturb_val(val_true, "q", eff_level, False)
                            new_fix = 0
                        else:
                            eff_level = comp_levels[current_comp_idx]["GroupB"]
                            new_val, note = perturb_val(val_true, "q", eff_level, False)
                            new_fix = 1

                    if not ENABLE_FIX_STATE_CHANGES:
                        new_fix = orig_fix

                    out_lines.append(f" 9) {new_val:.4f}       {new_fix}       # Axis ratio (b/a)\n")
                    all_logs.append({"File":f"{base_name}_{suffix}", "Comp":f"obj{current_comp_idx}", "Param":"q", "Orig":val_true, "New":f"{new_val:.4f}", "Diff":note, "Mode":mode_name})
                    continue

                # 10) PA
                if stripped.startswith("10)"):
                    parts = stripped.split()
                    val_true = comp_true["pa"]
                    orig_fix = int(parts[2])

                    if is_target:
                        eff_level = comp_levels[current_comp_idx]["GroupB"]
                        new_val, note = perturb_val(val_true, "pa", eff_level, is_bias)
                        new_fix = orig_fix
                    else:
                        if strategy == "base":
                            new_val, note = val_true, "None"
                            new_fix = 1
                        elif strategy == "real":
                            eff_level = comp_levels[current_comp_idx]["GroupB"]
                            new_val, note = perturb_val(val_true, "pa", eff_level, False)
                            new_fix = 0
                        else:
                            eff_level = comp_levels[current_comp_idx]["GroupB"]
                            new_val, note = perturb_val(val_true, "pa", eff_level, False)
                            new_fix = 1

                    if not ENABLE_FIX_STATE_CHANGES:
                        new_fix = orig_fix

                    out_lines.append(f"10) {new_val:.4f}       {new_fix}       # Position angle (PA) [deg]\n")
                    all_logs.append({"File":f"{base_name}_{suffix}", "Comp":f"obj{current_comp_idx}", "Param":"PA", "Orig":val_true, "New":f"{new_val:.4f}", "Diff":note, "Mode":mode_name})
                    continue

                out_lines.append(line)
                continue

            if inside_sky:
                if stripped.startswith("1)"):
                    parts = stripped.split()
                    sky_true = true_params["sky"]["sky"]
                    orig_fix = int(parts[2])

                    if sky_level != "none":
                        new_val, note = perturb_val(sky_true, "sky", sky_level, is_bias)
                    else:
                        new_val, note = sky_true, "None"

                    # 天空背景由于不涉及邻近源策略跳变，所以直接保持原fix
                    new_fix = orig_fix

                    out_lines.append(f" 1) {new_val:.4f}   {new_fix}       # Sky background at center of fitting region [ADU]\n")
                    all_logs.append({"File":f"{base_name}_{suffix}", "Comp":"sky", "Param":"Sky", "Orig":sky_true, "New":f"{new_val:.4f}", "Diff":note, "Mode":mode_name})
                    continue
                out_lines.append(line)
                continue

            out_lines.append(line)

        out_name = os.path.join(output_dir, f"{base_name}_{suffix}.feedme")
        with open(out_name, 'w') as f:
            f.writelines(out_lines)

# ================= 6. 主程序 =================
def main(num_variants, output_dir):
    ensure_dir(output_dir)
    print(f"[INFO] Scanning directory: {ROOT_DIR_LIST}")

    feedme_files = []
    for root_dir in ROOT_DIR_LIST:
        feedme_files.extend(glob.glob(os.path.join(root_dir, "**", "*.feedme"), recursive=True))
    feedme_files = [f for f in feedme_files if "perturbed_ii_feedme_wo_fix" not in f]
    print(f"[INFO] Processing {len(feedme_files)} feedme files with {num_variants} variants each.")

    all_logs = []

    for f_path in feedme_files:
        parent_dir = os.path.dirname(f_path)
        base_name = os.path.splitext(os.path.basename(f_path))[0]
        summary_candidates = [
            os.path.join(parent_dir, f"{base_name}_re_summary.md"), # 匹配 _re 后缀
            os.path.join(parent_dir, f"{base_name}_summary.md"),
            os.path.join(parent_dir, f"{base_name}.md"),
            os.path.join(parent_dir, "summary.md")
        ]
        # 兜底：抓取目录下所有以 summary.md 结尾的文件
        fallback = glob.glob(os.path.join(parent_dir, "*summary.md"))
        summary_candidates.extend(fallback)
        summary_path = None
        for cand in summary_candidates:
            if os.path.exists(cand):
                summary_path = cand
                break

        if summary_path is None:
            print(f"[WARN] Summary file not found for {f_path}, skipping.")
            continue

        process_single_source(f_path, summary_path, all_logs, num_variants, output_dir)

    if all_logs:
        keys = ["File", "Comp", "Param", "Orig", "New", "Diff", "Mode"]
        with open(os.path.join(output_dir, CSV_LOG_NAME), 'w', newline='') as f:
            writer = csv.DictWriter(f, keys)
            writer.writeheader()
            writer.writerows(all_logs)
    print(f"[SUCCESS] Done. Output dir: {output_dir}")

if __name__ == "__main__":
    # 新增：解析命令行 --num_variants 参数
    parser = argparse.ArgumentParser(description="Run GALFIT with varying steps.")
    parser.add_argument("--num_variants", type=int, required=True, help="Number of variants per source")
    parser.add_argument("--feedme_name", type=str, required=True, help="Feedme name")
    args = parser.parse_args()
    args.output_dir = os.path.join(DATA_DIR, "perturbed", args.feedme_name)
    print(f"[INFO] Output directory: {args.output_dir}")
    main(args.num_variants, args.output_dir)