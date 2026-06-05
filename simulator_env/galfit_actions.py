# simulator_env/galfit_actions.py
import os
import re
from pathlib import Path
import numpy as np
from skimage import io, color, transform
from skimage.metrics import structural_similarity as ssim

# 导入底层 MCP 工具
from simulator_env.modify_feedme import add_components, delete_components

# ================= 1. 物理滤渣：图像相似度计算 =================
def calculate_ssim(img1_path: str, img2_path: str) -> float:
    if not img1_path or not img2_path or not os.path.exists(img1_path) or not os.path.exists(img2_path): 
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

# ================= 2. 状态提取：解析 summary.md (修正版) =================
def parse_summary(summary_path: str) -> dict:
    true_params = {}
    if not summary_path or not os.path.exists(summary_path): 
        return true_params 
        
    with open(summary_path, 'r', encoding='utf-8') as f: 
        content = f.read()

    fit_log_match = re.search(r'## Fit log Content\n(.*?)(?=\n---|\Z)', content, re.DOTALL)
    if not fit_log_match: 
        return true_params
    fit_log = fit_log_match.group(1)

    # 🚀 完美修正版：1 个括号坐标 + 5 个 \S+ 参数 (mag, re, n, q, pa)
    sersic_pattern = r'^\s*sersic\s*:\s*\(([^)]+)\)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)'
    sersic_lines = re.findall(sersic_pattern, fit_log, re.MULTILINE)
    
    def clean_val(val_str: str) -> float:
        cleaned = val_str.strip().replace('[', '').replace(']', '').replace('*', '')
        if '+/-' in cleaned:
            cleaned = cleaned.split('+/-')[0].strip()
        return float(cleaned)

    for idx, match in enumerate(sersic_lines):
        xy_str, mag, re_, n, q, pa = match
        xy_parts = xy_str.split(',')
        
        true_params[f"sersic_{idx}"] = {
            "x": clean_val(xy_parts[0]), 
            "y": clean_val(xy_parts[1]),
            "mag": clean_val(mag), 
            "re": clean_val(re_), 
            "n": clean_val(n),
            "q": clean_val(q), 
            "pa": clean_val(pa)
        }

    sky_line = re.search(r'^ sky\s*:\s*\[[^\]]+\]\s+(\S+)', fit_log, re.MULTILINE)
    if sky_line: 
        try:
            true_params["sky"] = {"sky": clean_val(sky_line.group(1))}
        except Exception:
            pass
            
    return true_params

# ================= 3. 核心执行：将 Action 应用到 Feedme =================
def apply_action_to_feedme(action: dict, current_feedme_path: str, new_feedme_path: str, summary_path: str = None) -> bool:
    """
    根据给定的动作修改 feedme，并处理不同目录层级间的相对路径映射。
    注意: summary_path 仅在执行 Action C (微调) 时需要。
    """
    action_type = action["type"]
    new_text = ""

    # ==========================================
    # 🚨 前置 I/O 检查 (Fail Fast)
    # ==========================================
    if action_type in ["A", "B", "C"]:
        if not Path(current_feedme_path).is_file():
            # 这是业务逻辑要求的硬性条件，找不到直接抛出，拒绝用 try 掩盖
            raise FileNotFoundError(
                f"❌ [致命错误] Action {action_type} 依赖的文件不存在: {os.path.abspath(current_feedme_path)}"
            )

    if action_type == "C" and summary_path:
        if not Path(summary_path).is_file():
            raise FileNotFoundError(f"❌ [致命错误] Action C 依赖的 summary 不存在: {os.path.abspath(summary_path)}")


    # ==========================================
    # 🧠 业务逻辑层 (纯内存计算，不加 Try-Except)
    # 这里的任何崩溃都说明代码有 Bug，必须让 Python 抛出真实堆栈！
    # ==========================================
    
    # ---------------------------------------------------------
    # Action A: 添加组件
    # ---------------------------------------------------------
    if action_type == "A":
        # 这里的 add_components 内部如果不涉及 I/O，也不应该包 try
        new_text = add_components(current_feedme_path, [action]) 
        
    # ---------------------------------------------------------
    # Action B: 删除组件
    # ---------------------------------------------------------
    elif action_type == "B":
        new_text = delete_components(current_feedme_path, [action["target_index"]])
        
    # ---------------------------------------------------------
    # Action C: 扰动/微调组件
    # ---------------------------------------------------------
    elif action_type == "C":
        true_params = parse_summary(summary_path) if summary_path else {}
        
        # 仅对读取文件操作包裹极其克制的 try，防范操作系统的锁或者并发冲突
        try:
            with open(current_feedme_path, 'r', encoding='utf-8') as f:
                lines = f.readlines()
        except OSError as e:
            print(f"❌ [I/O 错误] 无法读取 feedme 文件: {e}")
            return False
        
        out_lines = []
        current_comp_idx = -1
        inside_sersic = False
        inside_sky = False
        target_sersic_idx = action.get("target_sersic_idx", 0)
        delta_re = action.get("delta_re_factor", 1.0)
        delta_n = action.get("delta_n_val", 0.0)

        for line in lines:
            stripped = line.strip()
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
            
            # Sersic 参数替换 (纯字符串操作，不应报错)
            if inside_sersic:
                comp_true = true_params.get(f"sersic_{current_comp_idx}")
                if comp_true:
                    if stripped.startswith("1)"):
                        ox, oy = stripped.split()[3], stripped.split()[4]
                        out_lines.append(f" 1) {comp_true['x']:.4f}  {comp_true['y']:.4f}  {ox}  {oy}  # Position x, y\n")
                        continue
                    if stripped.startswith("3)"):
                        ofix = stripped.split()[2]
                        out_lines.append(f" 3) {comp_true['mag']:.4f}     {ofix}       # Integrated magnitude\n")
                        continue
                    if stripped.startswith("9)"):
                        ofix = stripped.split()[2]
                        out_lines.append(f" 9) {comp_true['q']:.4f}       {ofix}       # Axis ratio (b/a)\n")
                        continue
                    if stripped.startswith("10)"):
                        ofix = stripped.split()[2]
                        out_lines.append(f"10) {comp_true['pa']:.4f}       {ofix}       # Position angle (PA) [deg]\n")
                        continue
                    
                    if stripped.startswith("4)"):
                        ofix = stripped.split()[2]
                        if current_comp_idx == target_sersic_idx:
                            new_val = comp_true['re'] * delta_re
                            new_val = max(0.1, min(comp_true['re'] * 2.5, new_val))
                        else:
                            new_val = comp_true['re']
                        out_lines.append(f" 4) {new_val:.4f}      {ofix}       # R_e (effective radius)   [pix]\n")
                        continue
                        
                    if stripped.startswith("5)"):
                        ofix = stripped.split()[2]
                        if current_comp_idx == target_sersic_idx:
                            new_val = comp_true['n'] + delta_n
                            new_val = max(0.2, min(8.0, new_val))
                        else:
                            new_val = comp_true['n']
                        out_lines.append(f" 5) {new_val:.4f}       {ofix}       # Sersic index n\n")
                        continue
            
            # Sky 参数替换
            if inside_sky and "sky" in true_params and stripped.startswith("1)"):
                ofix = stripped.split()[2]
                out_lines.append(f" 1) {true_params['sky']['sky']:.4f}   {ofix}       # Sky background\n")
                continue
            
            out_lines.append(line)
        new_text = "".join(out_lines)

    # ---------------------------------------------------------
    # Action D: 停止动作
    # ---------------------------------------------------------
    elif action_type == "D":
        return True

    # ==========================================
    # 🌍 路径计算与头部重写层 (纯字符串计算)
    # ==========================================
    if not new_text:
        # 如果新文本为空（比如没做什么修改），仅克制地捕获 I/O
        try:
            with open(current_feedme_path, "r", encoding="utf-8") as f:
                new_text = f.read()
        except OSError as e:
            print(f"❌ [I/O 错误] 无法回退读取原始 feedme: {e}")
            return False

    curr_feedme_dir = os.path.abspath(os.path.dirname(current_feedme_path))
    var_dir = os.path.abspath(os.path.dirname(new_feedme_path))

    lines = new_text.splitlines()
    fixed_lines = []
    
    for line in lines:
        stripped = line.strip()
        if stripped.startswith(("A)", "C)", "D)", "F)", "G)")):
            parts = stripped.split(None, 1)
            if len(parts) > 1:
                path_str = parts[1].split('#')[0].strip()
                comment = "#" + parts[1].split('#', 1)[1] if '#' in parts[1] else ""
                
                if path_str.lower() not in ["none", ""]:
                    if os.path.isabs(path_str):
                        abs_asset = path_str
                    else:
                        abs_asset = os.path.normpath(os.path.join(curr_feedme_dir, path_str))
                        
                    rel_to_var = os.path.relpath(abs_asset, start=var_dir)
                    line = f"{parts[0]} {rel_to_var}    {comment}"
                    
        elif stripped.startswith("B)"):
            out_name = os.path.splitext(os.path.basename(new_feedme_path))[0]
            line = f"B) {out_name}.fits      # Output data image block"
            
        fixed_lines.append(line)

    new_text = "\n".join(fixed_lines) + "\n"

    # ==========================================
    # 💾 最终落盘 I/O 层 (仅在此处处理系统权限或写入错误)
    # ==========================================
    try:
        os.makedirs(var_dir, exist_ok=True)
        with open(new_feedme_path, "w", encoding="utf-8") as f:
            f.write(new_text)
        return True
    except OSError as e:
        print(f"❌ [I/O 错误] 无法将变体写入硬盘 ({new_feedme_path}): {e}")
        return False