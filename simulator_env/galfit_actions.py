# simulator_env/galfit_actions.py
import os
import re
from pathlib import Path
import numpy as np
from skimage import io, color, transform
from skimage.metrics import structural_similarity as ssim

# 导入底层 MCP 工具
from simulator_env.modify_feedme import add_components, delete_components, _split_prefix_and_blocks

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

# ================= 3. 路径修复工具 =================
def _fix_feedme_paths(new_text: str, current_feedme_path: str, new_feedme_path: str) -> str:
    """
    修复 feedme 文本中的相对路径引用和输出文件名。
    将 A/C/D/F/G 行中的路径从 current_feedme 目录重新映射到 new_feedme 目录，
    B 行的输出文件名更新为 new_feedme 的 stem。
    """
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

                if path_str.lower() not in ("none", ""):
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

    return "\n".join(fixed_lines) + "\n"


# ================= 4. 核心执行：将 Action 应用到 Feedme =================
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
    # 🌍 路径计算与头部重写层
    # ==========================================
    if not new_text:
        try:
            with open(current_feedme_path, "r", encoding="utf-8") as f:
                new_text = f.read()
        except OSError as e:
            print(f"❌ [I/O 错误] 无法回退读取原始 feedme: {e}")
            return False

    new_text = _fix_feedme_paths(new_text, current_feedme_path, new_feedme_path)

    # ==========================================
    # 💾 最终落盘 I/O 层 (仅在此处处理系统权限或写入错误)
    # ==========================================
    try:
        os.makedirs(os.path.dirname(new_feedme_path), exist_ok=True)
        with open(new_feedme_path, "w", encoding="utf-8") as f:
            f.write(new_text)
        return True
    except OSError as e:
        print(f"❌ [I/O 错误] 无法将变体写入硬盘 ({new_feedme_path}): {e}")
        return False


# ================= 5. 统一决策执行：将 VLM 统一决策应用到 Feedme =================
def apply_unified_action_to_feedme(
    decision: dict,
    current_feedme_path: str,
    new_feedme_path: str,
    summary_path: str = None,
) -> bool:
    """
    将 VLM 统一决策格式的 action 应用到 feedme 文件。

    决策格式:
      structural: "add_bar"|"add_disk"|...|"delete_N"|"none"
      new_component: {...} (仅 add_* 时有效)
      param_updates: [{target_idx, delta_re_factor, delta_n_val, delta_mag, ...}]

    执行顺序: 先参数更新 → 再结构变更
    """
    if not Path(current_feedme_path).is_file():
        raise FileNotFoundError(f"❌ [致命错误] feedme 文件不存在: {os.path.abspath(current_feedme_path)}")

    structural = decision.get("structural", "none")
    param_updates = decision.get("param_updates", [])

    # ---- Step 1: 参数更新 ----
    true_params = parse_summary(summary_path) if summary_path else {}

    try:
        with open(current_feedme_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
    except OSError as e:
        print(f"❌ [I/O 错误] 无法读取 feedme 文件: {e}")
        return False

    updates_by_idx = {}
    for pu in param_updates:
        idx = pu.get("target_idx", 0)
        updates_by_idx[idx] = pu

    out_lines = []
    current_comp_idx = -1
    inside_sersic = False
    inside_sky = False

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

        if inside_sersic:
            comp_true = true_params.get(f"sersic_{current_comp_idx}")
            if comp_true:
                update = updates_by_idx.get(current_comp_idx, {})

                if stripped.startswith("1)"):
                    ox, oy = stripped.split()[3], stripped.split()[4]
                    out_lines.append(f" 1) {comp_true['x']:.4f}  {comp_true['y']:.4f}  {ox}  {oy}  # Position x, y\n")
                    continue
                if stripped.startswith("3)"):
                    ofix = stripped.split()[2]
                    mag = comp_true['mag']
                    if "delta_mag" in update:
                        mag += update["delta_mag"]
                    out_lines.append(f" 3) {mag:.4f}     {ofix}       # Integrated magnitude\n")
                    continue
                if stripped.startswith("4)"):
                    ofix = stripped.split()[2]
                    re_val = comp_true['re']
                    if "delta_re_factor" in update:
                        re_val *= update["delta_re_factor"]
                        re_val = max(0.1, min(comp_true['re'] * 2.5, re_val))
                    out_lines.append(f" 4) {re_val:.4f}      {ofix}       # R_e (effective radius)   [pix]\n")
                    continue
                if stripped.startswith("5)"):
                    ofix = stripped.split()[2]
                    n_val = comp_true['n']
                    if "delta_n_val" in update:
                        n_val += update["delta_n_val"]
                        n_val = max(0.2, min(8.0, n_val))
                    out_lines.append(f" 5) {n_val:.4f}       {ofix}       # Sersic index n\n")
                    continue
                if stripped.startswith("9)"):
                    ofix = stripped.split()[2]
                    q_val = comp_true['q']
                    if "delta_q_factor" in update:
                        q_val *= update["delta_q_factor"]
                        q_val = max(0.05, min(1.0, q_val))
                    out_lines.append(f" 9) {q_val:.4f}       {ofix}       # Axis ratio (b/a)\n")
                    continue
                if stripped.startswith("10)"):
                    ofix = stripped.split()[2]
                    pa_val = comp_true['pa']
                    if "delta_pa_val" in update:
                        pa_val += update["delta_pa_val"]
                    out_lines.append(f"10) {pa_val:.4f}       {ofix}       # Position angle (PA) [deg]\n")
                    continue

        if inside_sky and "sky" in true_params and stripped.startswith("1)"):
            ofix = stripped.split()[2]
            out_lines.append(f" 1) {true_params['sky']['sky']:.4f}   {ofix}       # Sky background\n")
            continue

        out_lines.append(line)

    new_text = "".join(out_lines)

    # ---- Step 2: 结构变更 ----
    if structural.startswith("add_"):
        nc = decision.get("new_component", {})
        comp_type = nc.get("type", structural.split("_", 1)[1])

        insert_item = {"type": comp_type}
        patch = {}
        if nc.get("n") is not None:
            patch["preset_n"] = float(nc["n"])
        if nc.get("re") is not None:
            patch["preset_re"] = float(nc["re"])
        if nc.get("q") is not None:
            patch["preset_q"] = float(nc["q"])
        if nc.get("pa") is not None:
            patch["preset_pa"] = float(nc["pa"])
        patch["mag_offset"] = float(nc.get("mag_offset", 1.5))
        if patch:
            insert_item["patch_applied"] = patch

        new_text = add_components(new_text, [insert_item])

    elif structural.startswith("delete_"):
        try:
            del_idx = int(structural.split("_", 1)[1])
            new_text = delete_components(new_text, [del_idx])
        except (ValueError, IndexError) as e:
            print(f"    ⚠️ [统一决策] 删除解析失败: {e}，跳过删除")

    # ---- Step 3: 路径修复与落盘 ----
    new_text = _fix_feedme_paths(new_text, current_feedme_path, new_feedme_path)

    try:
        var_dir = os.path.dirname(new_feedme_path)
        os.makedirs(var_dir, exist_ok=True)
        with open(new_feedme_path, "w", encoding="utf-8") as f:
            f.write(new_text)
        return True
    except OSError as e:
        print(f"❌ [I/O 错误] 无法将变体写入硬盘 ({new_feedme_path}): {e}")
        return False


# ================= 6. 方案B：完整模型规格 → 写整份 feedme =================
def _spec_to_float(val_str):
    """从 feedme 参数 token 提取浮点数（去括号/星号/误差）。"""
    try:
        cleaned = str(val_str).strip().replace('[', '').replace(']', '').replace('*', '')
        if '+/-' in cleaned:
            cleaned = cleaned.split('+/-')[0].strip()
        return float(cleaned)
    except (ValueError, TypeError):
        return None


def parse_components_from_feedme(feedme_path: str) -> list:
    """
    解析父 feedme 的全部成分（支持 sersic/expdisk/psf），返回:
      [{"model":"sersic","x":..,"y":..,"mag":..,"re":..,"n":..,"q":..,"pa":..,
        "fix":{"x":1,"y":1,"mag":1,"re":1,"n":1,"q":1,"pa":1}}, ...]
    expdisk 的 re 字段存的是 Rs（标度长），psf 无 re/n。sky 不计入。
    仅用于 diff 与 ≤1 成分变更校验。
    """
    p = Path(feedme_path)
    if not p.is_file():
        return []
    try:
        text = p.read_text(encoding="utf-8", errors="ignore")
        _, blocks = _split_prefix_and_blocks(text)
    except (ValueError, OSError):
        return []

    comps = []
    for b in blocks:
        if b.comp_type == "sky":
            continue
        bt = b.text
        comp = {"model": b.comp_type, "fix": {}}

        m1 = re.search(r"(?m)^\s*1\)\s+(\S+)\s+(\S+)(?:\s+(\S+)\s+(\S+))?", bt)
        if m1:
            comp["x"] = _spec_to_float(m1.group(1))
            comp["y"] = _spec_to_float(m1.group(2))
            for grp, key in [(3, "x"), (4, "y")]:
                if m1.group(grp) is not None:
                    try:
                        comp["fix"][key] = int(float(m1.group(grp)))
                    except ValueError:
                        pass

        for par, key in [(3, "mag"), (4, "re"), (5, "n"), (9, "q"), (10, "pa")]:
            if key == "n" and comp["model"] != "sersic":
                continue  # expdisk/psf 无 n，第5行是隐藏占位
            if comp["model"] == "psf" and key in ("re", "q", "pa"):
                continue  # psf 无 re；q/pa 为 -1 占位，不计入
            mm = re.search(rf"(?m)^\s*{par}\)\s+(\S+)\s+(\S+)", bt)
            if mm:
                v = _spec_to_float(mm.group(1))
                if v is not None:
                    comp[key] = v
                try:
                    comp["fix"][key] = int(float(mm.group(2)))
                except ValueError:
                    pass
        comps.append(comp)
    return comps


def _get_parent_center(root_text: str):
    """取父 feedme 第一个成分的 x,y 作为同心成分的默认中心。"""
    m = re.search(r"(?m)^\s*1\)\s+([+-]?\d+(?:\.\d+)?)\s+([+-]?\d+(?:\.\d+)?)", root_text)
    if m:
        return float(m.group(1)), float(m.group(2))
    return None, None


def _get_parent_sky(root_text: str):
    """取父 feedme sky 块的 background 值作为继承默认。"""
    sky_match = re.search(
        r"(?ms)^\s*0\)\s*sky\b.*?^\s*1\)\s+([+-]?\d+(?:\.\d+(?:[eE][+-]?\d+)?)?)",
        root_text,
    )
    if sky_match:
        try:
            return float(sky_match.group(1))
        except ValueError:
            return 0.0
    return 0.0


def _fix_flag(comp_fix: dict, key: str, default: int = 1) -> int:
    """取某参数 fix 标志，缺省 free(1)；clamp 到 {0,1}。"""
    v = (comp_fix or {}).get(key, default)
    try:
        v = int(v)
    except (ValueError, TypeError):
        v = default
    return 1 if v == 1 else 0


def _build_sersic_block(c: dict) -> str:
    fx = _fix_flag(c.get("fix", {}), "x"); fy = _fix_flag(c.get("fix", {}), "y")
    return (
        "0) sersic                    #  Component type\n"
        f" 1) {c['x']:.4f}  {c['y']:.4f}  {fx}  {fy}  #  Position x, y\n"
        f" 3) {c['mag']:.4f}     {_fix_flag(c.get('fix', {}), 'mag')}       #  Integrated magnitude\n"
        f" 4) {c['re']:.4f}      {_fix_flag(c.get('fix', {}), 're')}       #  R_e (effective radius)   [pix]\n"
        f" 5) {c['n']:.4f}       {_fix_flag(c.get('fix', {}), 'n')}       #  Sersic index n\n"
        " 6) 0.0000        0           #     -----\n"
        " 7) 0.0000        0           #     -----\n"
        " 8) 0.0000        0           #     -----\n"
        f" 9) {c['q']:.4f}       {_fix_flag(c.get('fix', {}), 'q')}       #  Axis ratio (b/a)\n"
        f"10) {c['pa']:.4f}       {_fix_flag(c.get('fix', {}), 'pa')}       #  Position angle (PA) [deg]\n"
        "Z) 0                         #  Skip this model in output image?  (yes=1, no=0)\n"
    )


def _build_expdisk_block(c: dict) -> str:
    fx = _fix_flag(c.get("fix", {}), "x"); fy = _fix_flag(c.get("fix", {}), "y")
    rs = c["re"] / 1.678  # VLM 给 Re，expdisk 用 Rs
    return (
        "0) expdisk                   #  Component type\n"
        f" 1) {c['x']:.4f}  {c['y']:.4f}  {fx}  {fy}  #  Position x, y\n"
        f" 3) {c['mag']:.4f}     {_fix_flag(c.get('fix', {}), 'mag')}       #  Integrated magnitude\n"
        f" 4) {rs:.4f}      {_fix_flag(c.get('fix', {}), 're')}       #  R_s (disk scale-length)   [pix]\n"
        " 5) 0.0000        0           #     -----\n"
        " 6) 0.0000        0           #     -----\n"
        " 7) 0.0000        0           #     -----\n"
        " 8) 0.0000        0           #     -----\n"
        f" 9) {c['q']:.4f}       {_fix_flag(c.get('fix', {}), 'q')}       #  Axis ratio (b/a)\n"
        f"10) {c['pa']:.4f}       {_fix_flag(c.get('fix', {}), 'pa')}       #  Position angle (PA) [deg]\n"
        "Z) 0                         #  Skip this model in output image?  (yes=1, no=0)\n"
    )


def _build_psf_block(c: dict) -> str:
    # 对齐 MCP component_specification_galfit.md：psf 仅 x,y,mag，无形状参数(9/10)
    fx = _fix_flag(c.get("fix", {}), "x"); fy = _fix_flag(c.get("fix", {}), "y")
    return (
        "0) psf                       #  Component type\n"
        f" 1) {c['x']:.4f}  {c['y']:.4f}  {fx}  {fy}  #  Position x, y\n"
        f" 3) {c['mag']:.4f}     {_fix_flag(c.get('fix', {}), 'mag')}       #  Integrated magnitude\n"
        "Z) 0                         #  Skip this model in output image?  (yes=1, no=0)\n"
    )


def _build_sky_block(sky_value: float, fix: int = 0) -> str:
    return (
        "0) sky                       #  Component type\n"
        f" 1) {sky_value:.6f}   {1 if fix == 1 else 0}       #  Sky background at center of fitting region [ADUs]\n"
        " 2) 0.0000        0           #  dsky/dx (sky gradient in x)     [ADUs/pix]\n"
        " 3) 0.0000        0           #  dsky/dy (sky gradient in y)     [ADUs/pix]\n"
        "Z) 0                         #  Skip this model in output image?  (yes=1, no=0)\n"
    )


def write_feedme_from_spec(spec: dict, root_feedme_path: str, new_feedme_path: str) -> bool:
    """
    方案B：根据完整模型规格写出整份 feedme。
      spec = {"components":[{role,model,x,y,mag,re,n,q,pa,fix:{}}...], "sky":{value,fix}}
    复用 root feedme 的头部(A-P)，逐成分构建 block，sky 放最后，修路径后落盘。
    """
    p = Path(root_feedme_path)
    if not p.is_file():
        raise FileNotFoundError(f"❌ [致命错误] root feedme 不存在: {os.path.abspath(root_feedme_path)}")

    root_text = p.read_text(encoding="utf-8", errors="ignore")
    try:
        prefix, _ = _split_prefix_and_blocks(root_text)
    except ValueError as e:
        print(f"❌ [方案B] 无法解析 root feedme 头部: {e}")
        return False

    cx_default, cy_default = _get_parent_center(root_text)
    sky_default = _get_parent_sky(root_text)

    components = spec.get("components", [])
    if not components:
        print("⚠️ [方案B] spec 无 components，跳过")
        return False

    blocks = []
    concentric_idxs = []  # 1-based 成分号：非伴星系成分需 x,y 同心绑定
    for c in components:
        model = str(c.get("model", "sersic")).lower().strip()
        is_companion = str(c.get("role", "")).lower().strip() == "companion"
        if c.get("x") is None:
            c["x"] = cx_default if cx_default is not None else 0.0
        if c.get("y") is None:
            c["y"] = cy_default if cy_default is not None else 0.0

        if model == "expdisk":
            blocks.append(_build_expdisk_block(c))
        elif model == "psf":
            blocks.append(_build_psf_block(c))
        else:
            blocks.append(_build_sersic_block(c))

        if not is_companion:
            concentric_idxs.append(len(blocks))  # 当前成分的 1-based 号

    sky = spec.get("sky") or {}
    sky_val = sky.get("value")
    if sky_val is None:
        sky_val = sky_default
    blocks.append(_build_sky_block(float(sky_val), fix=int(sky.get("fix", 0) or 0)))

    parts = [prefix.rstrip() + "\n"]
    for i, bt in enumerate(blocks, start=1):
        parts.append(f"\n# Object number: {i}\n{bt}")
    new_text = "".join(parts)

    new_text = _fix_feedme_paths(new_text, root_feedme_path, new_feedme_path)

    # 同心绑定：≥2 个同心成分时，写 .cons 约束文件，把它们的 x,y offset 绑定（对齐 MCP）
    var_dir = os.path.dirname(new_feedme_path)
    cons_basename = None
    if len(concentric_idxs) >= 2:
        stem = os.path.splitext(os.path.basename(new_feedme_path))[0]
        cons_basename = stem + ".cons"
        idx_str = "_".join(str(i) for i in concentric_idxs)
        cons_text = (
            "# Component/    parameter   constraint    Comment\n"
            f"  {idx_str}        x          offset      # 同心：x 相对位置绑定\n"
            f"  {idx_str}        y          offset      # 同心：y 相对位置绑定\n"
        )
        # 把 G) 行指向该 .cons（GALFIT 以 cwd=feedme 目录运行，用 basename 即可）
        new_text = re.sub(
            r"(?m)^\s*G\)\s+\S+.*$",
            f"G) {cons_basename}                         # File with parameter constraints (ASCII file)",
            new_text,
        )

    try:
        os.makedirs(var_dir, exist_ok=True)
        if cons_basename:
            with open(os.path.join(var_dir, cons_basename), "w", encoding="utf-8") as f:
                f.write(cons_text)
        with open(new_feedme_path, "w", encoding="utf-8") as f:
            f.write(new_text)
        return True
    except OSError as e:
        print(f"❌ [I/O 错误] 无法写入 feedme ({new_feedme_path}): {e}")
        return False