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
CSV_LOG_NAME = "simplify_galfit_perturbation_log.csv"
BIAS_MODE_PROB = 0.5

# 极简策略：只扰动 Re 和 n。冻结 Fix 状态及其他所有参数。
LEVEL_PROBS = {"none": 0.20, "easy": 0.26, "medium": 0.27, "hard": 0.27}

# 难度参数 (严格遵循专家建议，移除 Pos/Sky/Mag/PA/q)
DIFFICULTY_SETTINGS = {
    "none":   {"re":0, "n":0},
    "easy":   {"re":0.2, "n":0.5},
    "medium": {"re":0.4, "n":1.0},
    "hard":   {"re":0.45, "n":2.0}
}

# 偏差模式方向修正：暗源倾向于 Re 偏小 (陈竹组设定)
BIAS_RE_MEAN = -0.15

def ensure_dir(d):
    if os.path.exists(d): shutil.rmtree(d)
    os.makedirs(d)

def pick_level():
    return np.random.choice(list(LEVEL_PROBS.keys()), p=list(LEVEL_PROBS.values()))

# ================= 2. 解析 _summary.md 中的真值 =================
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
        true_params[f"sersic_{idx}"] = {
            "x": float(xy_parts[0].strip()), "y": float(xy_parts[1].strip()),
            "mag": float(mag), "re": float(re_), "n": float(n),
            "q": float(q), "pa": float(pa)
        }

    sky_line = re.search(r'^ sky\s*:\s*\[[^\]]+\]\s+([\d\.\-eE]+)', fit_log, re.MULTILINE)
    true_params["sky"] = {"sky": float(sky_line.group(1)) if sky_line else 0.0}

    return true_params

# ================= 3. 核心扰动逻辑 (仅 Re 和 n) =================
def perturb_val(val, p_type, level, is_bias):
    val = float(val)
    if level == "none": return val, "None"
    sigma = DIFFICULTY_SETTINGS[level]
    
    if p_type == "re":
        noise = np.random.normal(BIAS_RE_MEAN if is_bias else 0, sigma["re"])
        f = np.exp(noise)
        # 陈竹老师修正：上限控制在 2.5 倍
        if f > 2.5:
            f = 2.5
            note = f"*{f:.4f} (clipped)"
        else:
            note = f"*{f:.4f}"
        new_val = val * f

    elif p_type == "n":
        noise = np.random.normal(0, sigma["n"])
        new_val = np.clip(val + noise, 0.2, 8.0)
        note = f"{new_val-val:+.4f}"

    return new_val, note

# ================= 4. 处理单个源 =================
def process_single_source(feedme_path, summary_path, all_logs, num_variants, output_dir):
    parent_dir = os.path.dirname(feedme_path)
    object_name = os.path.basename(parent_dir)
    filename = os.path.basename(feedme_path)
    base_name = os.path.splitext(filename)[0]

    with open(feedme_path, 'r') as f:
        feedme_lines = f.readlines()

    true_params = parse_summary(summary_path)
    num_sersic = sum(1 for line in feedme_lines if line.strip().startswith("0) sersic"))

    for v_idx in range(num_variants):
        suffix = f"v{v_idx}"
        is_bias = random.random() < BIAS_MODE_PROB
        mode_name = "Bias" if is_bias else "Random"

        comp_levels = {idx: pick_level() for idx in range(num_sersic)}

        out_lines = []
        current_comp_idx = -1
        inside_sersic = False
        inside_sky = False

        for line in feedme_lines:
            stripped = line.strip()

            if re.match(r'^[A-Z]\)', stripped):
                if line.startswith("A)") or line.startswith("C)") or line.startswith("F)"):
                    match = re.search(r'(COS|EGS|GOODSN|UDS)/([^/]+)/([^/\s#]+)', line)
                    if match: out_lines.append(f"{line[0]}) ../../galfit_sample_0204/{match.group(0)}  # Input image\n")
                    else: out_lines.append(line)
                elif line.startswith("B)"):
                    out_lines.append(f"B) ../../../galfit_tmp/{base_name}_{suffix}.fits  # Output FITS\n")
                elif line.startswith("D)"):
                    out_lines.append("D) ../../galfit_sample_0204/f160w_psf.fits  # PSF\n")
                elif line.startswith("G)"):
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
                    inside_sersic = inside_sky = False
                out_lines.append(line)
                continue

            if inside_sersic:
                comp_true = true_params.get(f"sersic_{current_comp_idx}")
                if not comp_true:
                    out_lines.append(line)
                    continue

                eff_level = comp_levels[current_comp_idx]

                # 1) 位置行 (严格保持真值与原始 Fix)
                if stripped.startswith("1)"):
                    orig_fix_x, orig_fix_y = stripped.split()[3], stripped.split()[4]
                    out_lines.append(f" 1) {comp_true['x']:.4f}  {comp_true['y']:.4f}  {orig_fix_x}  {orig_fix_y}  # Position x, y\n")
                    continue

                # 3) Mag (严格保持真值与原始 Fix)
                if stripped.startswith("3)"):
                    orig_fix = stripped.split()[2]
                    out_lines.append(f" 3) {comp_true['mag']:.4f}     {orig_fix}       # Integrated magnitude\n")
                    continue

                # 4) Re (执行扰动)
                if stripped.startswith("4)"):
                    orig_fix = stripped.split()[2]
                    new_val, note = perturb_val(comp_true["re"], "re", eff_level, is_bias)
                    out_lines.append(f" 4) {new_val:.4f}      {orig_fix}       # R_e (effective radius)   [pix]\n")
                    all_logs.append({"File":f"{base_name}_{suffix}", "Comp":f"obj{current_comp_idx}", "Param":"Re", "Orig":comp_true['re'], "New":f"{new_val:.4f}", "Diff":note, "Mode":mode_name})
                    continue

                # 5) n (执行扰动)
                if stripped.startswith("5)"):
                    orig_fix = stripped.split()[2]
                    new_val, note = perturb_val(comp_true["n"], "n", eff_level, is_bias)
                    out_lines.append(f" 5) {new_val:.4f}       {orig_fix}       # Sersic index n\n")
                    all_logs.append({"File":f"{base_name}_{suffix}", "Comp":f"obj{current_comp_idx}", "Param":"n", "Orig":comp_true['n'], "New":f"{new_val:.4f}", "Diff":note, "Mode":mode_name})
                    continue

                # 9) q (严格保持真值与原始 Fix)
                if stripped.startswith("9)"):
                    orig_fix = stripped.split()[2]
                    out_lines.append(f" 9) {comp_true['q']:.4f}       {orig_fix}       # Axis ratio (b/a)\n")
                    continue

                # 10) PA (严格保持真值与原始 Fix)
                if stripped.startswith("10)"):
                    orig_fix = stripped.split()[2]
                    out_lines.append(f"10) {comp_true['pa']:.4f}       {orig_fix}       # Position angle (PA) [deg]\n")
                    continue

                out_lines.append(line)
                continue

            if inside_sky:
                if stripped.startswith("1)"):
                    orig_fix = stripped.split()[2]
                    out_lines.append(f" 1) {true_params['sky']['sky']:.4f}   {orig_fix}       # Sky background at center of fitting region [ADU]\n")
                    continue
                out_lines.append(line)
                continue

            out_lines.append(line)

        out_name = os.path.join(output_dir, f"{base_name}_{suffix}.feedme")
        with open(out_name, 'w') as f:
            f.writelines(out_lines)

# ================= 5. 主程序 =================
def main(num_variants, output_dir):
    ensure_dir(output_dir)
    print(f"[INFO] Scanning directory: {ROOT_DIR_LIST}")

    feedme_files = []
    for root_dir in ROOT_DIR_LIST:
        feedme_files.extend(glob.glob(os.path.join(root_dir, "**", "*.feedme"), recursive=True))
    feedme_files = [f for f in feedme_files if "perturbed" not in f]
    print(f"[INFO] Processing {len(feedme_files)} feedme files with {num_variants} variants each.")

    all_logs = []

    for f_path in feedme_files:
        parent_dir = os.path.dirname(f_path)
        base_name = os.path.splitext(os.path.basename(f_path))[0]
        summary_candidates = [
            os.path.join(parent_dir, f"{base_name}_re_summary.md"),
            os.path.join(parent_dir, f"{base_name}_summary.md"),
            os.path.join(parent_dir, f"{base_name}.md"),
            os.path.join(parent_dir, "summary.md")
        ]
        fallback = glob.glob(os.path.join(parent_dir, "*summary.md"))
        summary_candidates.extend(fallback)
        
        summary_path = next((cand for cand in summary_candidates if os.path.exists(cand)), None)

        if not summary_path:
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
    parser = argparse.ArgumentParser(description="Run GALFIT with varying steps.")
    parser.add_argument("--num_variants", type=int, required=True, help="Number of variants per source")
    parser.add_argument("--feedme_name", type=str, required=True, help="Feedme name")
    args = parser.parse_args()
    args.output_dir = os.path.join(DATA_DIR, "perturbed", args.feedme_name)
    print(f"[INFO] Output directory: {args.output_dir}")
    main(args.num_variants, args.output_dir)