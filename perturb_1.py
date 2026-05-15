import os
import re
import glob
import numpy as np
import random
import csv
import shutil

# ================= 1. 配置区 =================
# 动态获取当前脚本所在路径，不再写死绝对路径
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.join(BASE_DIR, "COS")
OUTPUT_DIR = os.path.join(BASE_DIR, "perturbed_i_feedme")
CSV_LOG_NAME = "all_galfit_perturbation_log.csv"

NUM_VARIANTS_PER_SOURCE = 5
BIAS_MODE_PROB = 0.5
NEIGHBOR_STRATEGY_PROB = 0.5 

LEVEL_PROBS = {"none": 0.20, "easy": 0.26, "medium": 0.27, "hard": 0.27}
DIFFICULTY_SETTINGS = {
    "none": { "re":0, "n":0, "pa":0, "q":0, "mag":0, "pos_sys":0, "pos_rand":0 },
    "easy": { "re": 0.2, "n": 0.5, "pa": 5.0, "q": 0.05, "mag": 0.1, "pos_sys": 1.0, "pos_rand": 0.5 },
    "medium": { "re": 0.4, "n": 1.0, "pa": 20.0, "q": 0.1, "mag": 0.3, "pos_sys": 3.0, "pos_rand": 0.75 },
    "hard": { "re": 0.8, "n": 2.0, "pa": 90.0, "q": 0.2, "mag": 0.8, "pos_sys": 5.0, "pos_rand": 1.0 }
}
BIAS_RE_MEAN = 0.15 
BIAS_MAG_MEAN = 0.2 

def ensure_dir(d):
    if os.path.exists(d): shutil.rmtree(d)
    os.makedirs(d)

def pick_level():
    return np.random.choice(list(LEVEL_PROBS.keys()), p=list(LEVEL_PROBS.values()))

# ================= 2. 路径清洗逻辑 (核心修改区域) =================
def rewrite_header_paths(line, object_name, suffix=""):
    stripped = line.strip()
    match = re.match(r'^([A-Z])\)\s+(.*?)(?:\s+#.*)?$', stripped)
    if match:
        label = match.group(1)
        original_path = match.group(2).strip()
        filename = os.path.basename(original_path)
        
        # --- 针对 B) 输出文件的修改 ---
        # 存到 ../galfit_tmp/ 下，并加上版本后缀
        if label == 'B':
            base, ext = os.path.splitext(filename)
            # 如果文件名里还没有版本号，才加后缀 (防止重复加)
            if suffix and suffix not in base:
                new_filename = f"{base}_{suffix}{ext}" # 例如 cosmos_68_v1.fits
            else:
                new_filename = filename
            
            # 使用 ../galfit_tmp 指向根目录下的临时文件夹
            new_path = f"../galfit_tmp/{new_filename}" 
            return f"{label}) {new_path}  # Output data image block\n"

        # --- 针对 D) PSF 文件：强制指向公共文件 ---
        if label == 'D':
            # 你的公共 PSF 在 COS 根目录下
            new_path = "../COS/f160w_psf.fits"
            return f"D) {new_path}  # Input PSF image\n"

        # --- 针对 G) 约束文件：强制指向公共文件 ---
        if label == 'G':
            # 你的公共约束文件在 COS 根目录下
            new_path = "../COS/GALFIT.con"
            return f"G) {new_path}  # Parameter constraints\n"

        # --- 针对 A, C, F (数据, 误差图, 掩膜) ---
        # 这些文件都在各自星系的子文件夹里
        if label in ['A', 'C', 'F']:
            new_path = f"../COS/{object_name}/{filename}"
            
            comment = ""
            if "#" in line:
                comment = "  # " + line.split("#", 1)[1].strip()
            return f"{label}) {new_path}{comment}\n"
            
    return line

# ================= 3. 核心扰动逻辑 (保持不变) =================
def perturb_val(val, p_type, level, is_bias, sys_pos_offset=0.0):
    val = float(val)
    if level == "none": return val, "None"
    sigma = DIFFICULTY_SETTINGS[level]
    noise, note = 0.0, "0.0"

    if p_type == "re": 
        if is_bias: noise = np.random.normal(BIAS_RE_MEAN, sigma["re"])
        else: noise = np.random.normal(0, sigma["re"] if level!="hard" else 1.5)
        f = min(np.exp(noise), 5.0)
        new_val = val * f; note = f"*{f:.4f}"
    elif p_type == "mag": 
        if is_bias: noise = np.abs(np.random.normal(0, sigma["mag"])) + BIAS_MAG_MEAN
        else: noise = np.random.normal(0, sigma["mag"])
        new_val = val + noise; note = f"{noise:+.4f}"
    elif p_type == "n":
        noise = np.random.normal(0, sigma["n"])
        new_val = np.clip(val + noise, 0.2, 8.0); note = f"{new_val-val:+.4f}"
    elif p_type == "q": 
        if is_bias: noise = np.abs(np.random.normal(0, sigma["q"])); new_val = min(1.0, val + noise)
        else: noise = np.random.normal(0, sigma["q"]); new_val = val + noise
        new_val = np.clip(new_val, 0.05, 1.0); note = f"{new_val-val:+.4f}"
    elif p_type == "pa":
        if level == "hard" and random.random() < 0.5: noise = 90.0; note = "FLIP"
        else: noise = np.random.uniform(-sigma["pa"], sigma["pa"])
        new_val = (val + noise) % 180 - 90; note = f"{noise:+.4f}"
    elif p_type == "pos": 
        rand_noise = np.random.normal(0, sigma["pos_rand"])
        total_noise = sys_pos_offset + rand_noise
        new_val = val + total_noise; note = f"Sys:{sys_pos_offset:.2f}+Rnd:{rand_noise:.2f}"
    return new_val, note

# ================= 4. 处理单个 Feedme =================
def process_single_feedme(file_path, all_logs):
    parent_dir = os.path.dirname(file_path)
    object_name = os.path.basename(parent_dir)
    filename = os.path.basename(file_path)
    base_name = os.path.splitext(filename)[0]
    object_id = base_name if "cosmos" in base_name else object_name

    with open(file_path, 'r') as f: lines = f.readlines()

    comp_levels = {} 
    comp_idx = 0
    for line in lines:
        if line.strip().startswith("0) sersic"):
            comp_levels[comp_idx] = { "GroupA": pick_level(), "GroupB": pick_level(), "GroupC": pick_level() }
            comp_idx += 1
    
    for v_idx in range(NUM_VARIANTS_PER_SOURCE):
        suffix = f"v{v_idx}" # 定义版本后缀
        is_bias = random.random() < BIAS_MODE_PROB
        mode_name = "Bias" if is_bias else "Random"
        
        out_lines = []
        current_comp_idx = -1
        inside_sersic = False
        sys_sigma = DIFFICULTY_SETTINGS["medium"]["pos_sys"] 
        g_dx = np.random.normal(0, sys_sigma)
        g_dy = np.random.normal(0, sys_sigma)

        for line in lines:
            stripped = line.strip()
            
            # 传递 suffix 给路径清洗函数
            if re.match(r'^[A-Z]\)', stripped):
                new_line = rewrite_header_paths(line, object_id, suffix)
                out_lines.append(new_line)
                continue

            if re.match(r'^0\)\s+\w+', stripped):
                parts = stripped.split()
                if parts[1] == "sersic": current_comp_idx += 1; inside_sersic = True
                else: inside_sersic = False
                out_lines.append(line); continue
            
            if inside_sersic and current_comp_idx in comp_levels:
                levels = comp_levels[current_comp_idx]
                def check_neighbor_strategy(original_fix):
                    if original_fix == 0: return True, 0
                    return (True, 0) if random.random() < NEIGHBOR_STRATEGY_PROB else (False, 1)

                if stripped.startswith("1)"):
                    parts = stripped.split(); x_val, y_val = float(parts[1]), float(parts[2]); fix_x, fix_y = int(parts[3]), int(parts[4])
                    lvl = levels["GroupC"]
                    do_pert_x, final_fix_x = check_neighbor_strategy(fix_x)
                    new_x, nx = perturb_val(x_val, "pos", lvl, is_bias, g_dx) if do_pert_x else (x_val, "KeepFixed")
                    do_pert_y, final_fix_y = check_neighbor_strategy(fix_y)
                    new_y, ny = perturb_val(y_val, "pos", lvl, is_bias, g_dy) if do_pert_y else (y_val, "KeepFixed")
                    out_lines.append(f" 1) {new_x:.4f}  {new_y:.4f}  {final_fix_x}  {final_fix_y}  # Position x, y\n"); all_logs.append({"File":f"{base_name}_{suffix}", "Comp":f"obj{current_comp_idx}", "Param":"Pos", "Orig":f"{x_val:.1f}", "New":f"{new_x:.1f}", "Diff":nx, "Mode":mode_name}); continue
                elif stripped.startswith("3)"):
                    parts = stripped.split(); val, fix = float(parts[1]), int(parts[2]); lvl = levels["GroupA"]
                    do_pert, final_fix = check_neighbor_strategy(fix)
                    new_val, note = perturb_val(val, "mag", lvl, is_bias) if do_pert else (val, "KeepFixed")
                    out_lines.append(f" 3) {new_val:.4f}     {final_fix}       # Integrated magnitude\n"); all_logs.append({"File":f"{base_name}_{suffix}", "Comp":f"obj{current_comp_idx}", "Param":"Mag", "Orig":val, "New":f"{new_val:.4f}", "Diff":note, "Mode":mode_name}); continue
                elif stripped.startswith("4)"):
                    parts = stripped.split(); val, fix = float(parts[1]), int(parts[2]); lvl = levels["GroupA"]
                    do_pert, final_fix = check_neighbor_strategy(fix)
                    new_val, note = perturb_val(val, "re", lvl, is_bias) if do_pert else (val, "KeepFixed")
                    out_lines.append(f" 4) {new_val:.4f}      {final_fix}       # R_e (effective radius)   [pix]\n"); all_logs.append({"File":f"{base_name}_{suffix}", "Comp":f"obj{current_comp_idx}", "Param":"Re", "Orig":val, "New":f"{new_val:.4f}", "Diff":note, "Mode":mode_name}); continue
                elif stripped.startswith("5)"):
                    parts = stripped.split(); val, fix = float(parts[1]), int(parts[2]); lvl = levels["GroupA"]
                    do_pert, final_fix = check_neighbor_strategy(fix)
                    new_val, note = perturb_val(val, "n", lvl, is_bias) if do_pert else (val, "KeepFixed")
                    out_lines.append(f" 5) {new_val:.4f}       {final_fix}       # Sersic index n\n"); all_logs.append({"File":f"{base_name}_{suffix}", "Comp":f"obj{current_comp_idx}", "Param":"n", "Orig":val, "New":f"{new_val:.4f}", "Diff":note, "Mode":mode_name}); continue
                elif stripped.startswith("9)"):
                    parts = stripped.split(); val, fix = float(parts[1]), int(parts[2]); lvl = levels["GroupB"]
                    do_pert, final_fix = check_neighbor_strategy(fix)
                    new_val, note = perturb_val(val, "q", lvl, is_bias) if do_pert else (val, "KeepFixed")
                    out_lines.append(f" 9) {new_val:.4f}       {final_fix}       # Axis ratio (b/a)\n"); all_logs.append({"File":f"{base_name}_{suffix}", "Comp":f"obj{current_comp_idx}", "Param":"q", "Orig":val, "New":f"{new_val:.4f}", "Diff":note, "Mode":mode_name}); continue
                elif stripped.startswith("10)"):
                    parts = stripped.split(); val, fix = float(parts[1]), int(parts[2]); lvl = levels["GroupB"]
                    do_pert, final_fix = check_neighbor_strategy(fix)
                    new_val, note = perturb_val(val, "pa", lvl, is_bias) if do_pert else (val, "KeepFixed")
                    out_lines.append(f"10) {new_val:.4f}       {final_fix}       # Position angle (PA) [deg]\n"); all_logs.append({"File":f"{base_name}_{suffix}", "Comp":f"obj{current_comp_idx}", "Param":"PA", "Orig":val, "New":f"{new_val:.4f}", "Diff":note, "Mode":mode_name}); continue

            out_lines.append(line)
            
        out_name = os.path.join(OUTPUT_DIR, f"{base_name}_{suffix}.feedme")
        with open(out_name, 'w') as f:
            f.writelines(out_lines)

def main():
    ensure_dir(OUTPUT_DIR)
    print(f"[INFO] Scanning directory: {ROOT_DIR}")
    feedme_files = glob.glob(os.path.join(ROOT_DIR, "**", "*.feedme"), recursive=True)
    all_logs = []
    print(f"[INFO] Found {len(feedme_files)} feedme files.")
    for f_path in feedme_files:
        if "perturbed_i_feedme" in f_path: continue
        # 打印当前正在处理的文件，方便调试
        # print(f"Processing: {os.path.basename(f_path)}")
        process_single_feedme(f_path, all_logs)
    if all_logs:
        keys = ["File", "Comp", "Param", "Orig", "New", "Diff", "Mode"]
        with open(os.path.join(OUTPUT_DIR, CSV_LOG_NAME), 'w') as f:
            writer = csv.DictWriter(f, keys)
            writer.writeheader()
            writer.writerows(all_logs)
    print(f"[SUCCESS] Done. Output dir: {OUTPUT_DIR}")

if __name__ == "__main__":
    main()