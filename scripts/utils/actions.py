import asyncio
import os
import re
import shutil
import random
import numpy as np

# 使用 skimage 替代 cv2，并引入完整预处理工具
from skimage import io, color, transform
from skimage.metrics import structural_similarity as ssim

import sys
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)
from src.tools.run_galfit import run_galfit


# ================= 1. 物理滤渣：图像相似度计算 =================
def calculate_ssim(img1_path, img2_path):
    if not os.path.exists(img1_path) or not os.path.exists(img2_path): 
        return 0.0
    try:
        img1 = io.imread(img1_path)
        img2 = io.imread(img2_path)
        if img1.ndim == 3 and img1.shape[2] == 4: img1 = img1[..., :3]
        if img2.ndim == 3 and img2.shape[2] == 4: img2 = img2[..., :3]
        if img1.ndim == 3: img1 = color.rgb2gray(img1)
        if img2.ndim == 3: img2 = color.rgb2gray(img2)
        if img1.shape != img2.shape:
            img2 = transform.resize(img2, img1.shape, anti_aliasing=False)
        score, _ = ssim(img1, img2, full=True, data_range=1.0)
        return score
    except Exception as e:
        print(f"🗑️ SSIM 计算失败: {e}")
        return 0.0


# ================= 2. 状态机：解析 summary.md 真值 =================
def parse_summary(summary_path):
    true_params = {}
    if not os.path.exists(summary_path): return true_params 
    with open(summary_path, 'r') as f: content = f.read()

    fit_log_match = re.search(r'## Fit log Content\n(.*?)(?=\n---|\Z)', content, re.DOTALL)
    if not fit_log_match: return true_params
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
    if sky_line: true_params["sky"] = {"sky": float(sky_line.group(1))}
    return true_params


# ================= 3. 核心：构造带有扰动的新 Feedme =================
def create_perturbed_feedme(input_feedme, output_feedme, summary_path, target_sersic_idx, delta_re_factor, delta_n_val):
    true_params = parse_summary(summary_path)
    with open(input_feedme, 'r') as f: lines = f.readlines()

    out_lines = []
    current_comp_idx = -1
    inside_sersic = False
    inside_sky = False
    actual_deltas = {}

    curr_feedme_dir = os.path.abspath(os.path.dirname(input_feedme))
    var_dir = os.path.abspath(os.path.dirname(output_feedme))

    for line in lines:
        stripped = line.strip()

        # ---------------- 智能路径转换 (核心修复区) ----------------
        if stripped.startswith(("A)", "C)", "D)", "F)", "G)")):
            parts = stripped.split(None, 1)
            if len(parts) > 1:
                path_str = parts[1].split('#')[0].strip()
                if path_str.lower() not in ["none", ""]:
                    # 1. 先定位该文件真实的绝对位置
                    if os.path.isabs(path_str):
                        abs_asset = path_str
                    elif os.path.exists(os.path.join(BASE_DIR, path_str)):
                        abs_asset = os.path.join(BASE_DIR, path_str)
                    else:
                        abs_asset = os.path.join(curr_feedme_dir, path_str)
                    
                    # 2. 计算从沙盒 (var_dir) 走向该文件的短相对路径
                    rel_to_var = os.path.relpath(abs_asset, var_dir)
                    line = line.replace(path_str, rel_to_var, 1)
            out_lines.append(line)
            continue

        if stripped.startswith("B)"):
            out_name = os.path.splitext(os.path.basename(output_feedme))[0]
            # 直接写文件名，GALFIT 跑在沙盒里，产物自然落在沙盒里
            out_lines.append(f"B) {out_name}.fits      # Output data image block\n")
            continue

        # ---------------- 结构跟踪 ----------------
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

        # ---------------- 处理 Sersic 成分 ----------------
        if inside_sersic:
            comp_true = true_params.get(f"sersic_{current_comp_idx}")
            if not comp_true:
                out_lines.append(line)
                continue

            if stripped.startswith("1)"):
                orig_fix_x, orig_fix_y = stripped.split()[3], stripped.split()[4]
                out_lines.append(f" 1) {comp_true['x']:.4f}  {comp_true['y']:.4f}  {orig_fix_x}  {orig_fix_y}  # Position x, y\n")
                continue
            if stripped.startswith("3)"):
                orig_fix = stripped.split()[2]
                out_lines.append(f" 3) {comp_true['mag']:.4f}     {orig_fix}       # Integrated magnitude\n")
                continue
            if stripped.startswith("9)"):
                orig_fix = stripped.split()[2]
                out_lines.append(f" 9) {comp_true['q']:.4f}       {orig_fix}       # Axis ratio (b/a)\n")
                continue
            if stripped.startswith("10)"):
                orig_fix = stripped.split()[2]
                out_lines.append(f"10) {comp_true['pa']:.4f}       {orig_fix}       # Position angle (PA) [deg]\n")
                continue

            if stripped.startswith("4)"):
                orig_fix = stripped.split()[2]
                orig_val = comp_true['re']
                if current_comp_idx == target_sersic_idx:
                    new_val = orig_val * delta_re_factor
                    if delta_re_factor > 2.5: new_val = orig_val * 2.5
                    actual_deltas["re"] = f"{'+' if new_val > orig_val else ''}{(new_val - orig_val) / orig_val * 100:.1f}%"
                else:
                    new_val = orig_val
                out_lines.append(f" 4) {new_val:.4f}      {orig_fix}       # R_e (effective radius)   [pix]\n")
                continue

            if stripped.startswith("5)"):
                orig_fix = stripped.split()[2]
                orig_val = comp_true['n']
                if current_comp_idx == target_sersic_idx:
                    new_val = np.clip(orig_val + delta_n_val, 0.2, 8.0)
                    actual_deltas["n"] = f"{'+' if new_val > orig_val else ''}{new_val - orig_val:.2f}"
                else:
                    new_val = orig_val
                out_lines.append(f" 5) {new_val:.4f}       {orig_fix}       # Sersic index n\n")
                continue

        # ---------------- 处理 Sky 背景 ----------------
        if inside_sky and "sky" in true_params and stripped.startswith("1)"):
            orig_fix = stripped.split()[2]
            out_lines.append(f" 1) {true_params['sky']['sky']:.4f}   {orig_fix}       # Sky background at center of fitting region [ADU]\n")
            continue

        out_lines.append(line)

    with open(output_feedme, 'w') as f: f.writelines(out_lines)
    return actual_deltas


# ================= 4. Action C 调度器：并发跑流与垃圾回收 =================
async def perform_action_perturb(current_feedme, galaxy_name, step_i, base_out_dir, num_variants=4, max_iter=100):
    valid_variants = []
    tasks = []
    variant_records = []

    curr_dir = os.path.dirname(current_feedme)
    curr_feedme_name = os.path.splitext(os.path.basename(current_feedme))[0]
    curr_summary_path = os.path.join(curr_dir, f"{curr_feedme_name}_summary.md")
    curr_png_path = os.path.join(curr_dir, f"{curr_feedme_name}_comparison.png")

    with open(current_feedme, 'r') as f:
        num_sersic = sum(1 for line in f if line.strip().startswith("0) sersic"))
    if num_sersic == 0: return []

    for v_idx in range(1, num_variants + 1):
        variant_name = f"{galaxy_name}_step_{step_i}_action_c_v_{v_idx}"
        var_dir = os.path.join(base_out_dir, variant_name)
        
        if os.path.exists(var_dir): shutil.rmtree(var_dir)
        os.makedirs(var_dir, exist_ok=True)
        new_feedme_path = os.path.join(var_dir, f"{variant_name}.feedme")

        target_sersic = random.randint(0, num_sersic - 1)
        re_factor = np.exp(np.random.normal(0, 0.3)) 
        n_delta = np.random.normal(0, 0.5)

        actual_deltas = create_perturbed_feedme(
            current_feedme, new_feedme_path, curr_summary_path,
            target_sersic_idx=target_sersic, delta_re_factor=re_factor, delta_n_val=n_delta
        )

        if actual_deltas:
            variant_records.append({
                "variant_name": variant_name, "sersic_id": target_sersic,
                "delta_params": actual_deltas, "feedme_path": new_feedme_path, "var_dir": var_dir
            })
            tasks.append(run_galfit(os.path.abspath(new_feedme_path), ["-imax", f"{max_iter}"]))

    if not tasks: return []

    results = await asyncio.gather(*tasks)

    for record, res in zip(variant_records, results):
        if res["status"] == "success":
            files_to_move = {
                "FITS": res.get("optimized_fits_file"),
                "PNG": res.get("image_file"),
                "Summary": res.get("summary_file")
            }
            
            for file_type, source_path in files_to_move.items():
                if source_path and os.path.exists(source_path):
                    file_name = os.path.basename(source_path)
                    target_path = os.path.join(record["var_dir"], file_name)
                    # 因为它直接跑在沙盒里，大概率不需要 move，加上这层判定做防御
                    if os.path.abspath(source_path) != os.path.abspath(target_path):
                        shutil.move(source_path, target_path)
                    if file_type == "PNG": res["image_file"] = target_path

            new_png = res.get("image_file")
            ssim_score = calculate_ssim(curr_png_path, new_png)
            
            if ssim_score > 0.995:
                print(f"🗑️ [SSIM 过滤] 变体 {record['variant_name']} 无实质改变，已丢弃。")
                shutil.rmtree(record["var_dir"], ignore_errors=True)
            else:
                valid_variants.append({
                    "variant_name": record["variant_name"],
                    "sersic_id": record["sersic_id"],
                    "delta_params": record["delta_params"]
                })
        else:
            print(f"❌ [GALFIT 失败] 变体 {record['variant_name']} 运行崩溃！")
            print(f"   原因: {res.get('error', '未知错误')}")
            if 'log' in res and res['log']:
                print(f"   日志片段: {res['log'][-300:]}")
            # 现场保留开关：若需调试底层错误，可注释下行。
            shutil.rmtree(record["var_dir"], ignore_errors=True)

    return valid_variants

# ================= 5. 占位区：增删成分模块 =================
async def perform_action_add(current_feedme, galaxy_name, step_i, base_out_dir):
    return []

async def perform_action_delete(current_feedme, galaxy_name, step_i, base_out_dir):
    return []