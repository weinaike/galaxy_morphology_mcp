# data_gen/vlm_proposal.py
"""
VLM-based proposal generation for galaxy morphology fitting.

Uses a multimodal VLM to analyze GALFIT comparison images and fitting parameter
summaries, then outputs unified decision dicts (structural + param_updates)
compatible with apply_unified_action_to_feedme().

Prompt design follows the MCP component_analysis Phase 4 output format.
"""

import asyncio
import os
import re
import json
from typing import List, Dict, Tuple, Optional

from data_gen.reward import (
    get_openAI_response_one_image,
    read_summary_md,
)


# ============================================================
# System Prompt
# ============================================================

SYSTEM_PROMPT = """你是一位专业的星系形态学成分分析专家，负责分析 GALFIT 拟合结果，并输出**下一轮完整的模型规格**（所有成分的完整参数表 + 逐参数 fix/free + 模型类型 + sky）。

## 工作原则
- 先观察后判断：仔细审视残差图和原始图像，再给出诊断结论
- 先物理成分后模型类型：先分析星系含哪些物理成分，再给参数
- 增量演进：基于上一轮拟合结果微调，不要推倒重来
- 一次只做一件事：相对上一轮，成分数量最多 ±1 个

## 残差图诊断树（按严重程度）
1. 全局系统性异常：整体大面积正/负残差 → sky 漂移（须把 sky fix 到背景值）；中心+边缘对称光晕 → PSF 问题
2. 数据污染：偏离中心的独立亮斑 → 前景星/伴星系，需额外 sersic/psf 同时拟合（伴星系须满足认定条件）
3. 星系成分（逐成分递增）：
   - Disk+Bulge：大尺度对称亮区（中心过亮外围过暗）→ 双成分
   - Bar：中心"一字型"/"X型"亮区 → 低 n(0.5) 高椭率 sersic
   - Nucleus/AGN：1D profile 左侧(<5px) 尖峰且调参无法解决 → psf
4. 可接受终态：残差呈"电视雪花"随机分布 → 维持当前成分

## 成分→模型→参数规范（务必遵循）
- **Disk**：用 **expdisk** 模型（不是 sersic）；Re ≈ 测量有效半径；q 面朝 0.7-1.0、侧向 <0.5
- **Bulge**：用 sersic；n 先固定 4（fix）分配通量，正常后再 free n；n 物理范围 0.1-8；Re 约为总光度半径的 1/5~1/3
- **Bar**：用 sersic，**n 固定 0.5（fix）**；q 0.2-0.4（高椭率）；PA 取自图像棒方向；Re 介于 Bulge 与 Disk 之间
- **Nucleus/AGN**：Re<0.2px 时用 **psf**（无 Re/n），否则用 sersic
- **典型尺寸层次**：Re_disk > Re_bar > Re_bulge

## fix/free（每个参数的 fix 标志）
- fix 取值：**1=free（参与拟合），0=fixed（冻结）**
- Bar 的 n 必须 fixed=0（锁 0.5）；Bulge 的 n 初期可 fixed=0（锁 4）待通量分配后再 free=1
- sky 必须 fixed（fix=0）到背景值，防止整体漂移
- 其余参数一般 free（fix=1）

## 参数物理判据
- 同一源的 Bulge+Disk 若 Re_bulge > Re_disk → 标签反了，交换
- 某成分 n<1 且 q<0.5 → 可能是 Bar 或 edge-on disk
- 某 sersic Re<0.2px → 应换 psf
- 同心成分（Disk/Bulge/Bar）x,y 基本一致
- 拆单成分为双成分时，按 3:7 或 4:6 分配通量（mag）

## 奥卡姆剃刀（仅用于 Nucleus/伴星系）
- 增加 Disk/Bulge/Bar 依据残差结构特征，不受 BIC 约束
- 增加 Nucleus/伴星系：ΔBIC>10 才值得接受，否则视为过拟合"""


def build_proposal_prompt(
    summary_content: str,
    step: int,
    max_steps: int,
    num_sersic: int,
    expert_gt: dict = None,
    current_components: list = None,
    history_summary: str = None,
) -> str:
    # 渲染当前模型成分清单（方案B：VLM 需在此基础上输出下一轮完整规格）
    cur_lines = []
    if current_components:
        for i, c in enumerate(current_components):
            parts = [f"  [{i}] model={c.get('model','?')}"]
            for k in ("mag", "re", "n", "q", "pa"):
                if c.get(k) is not None:
                    parts.append(f"{k}={c[k]:.3f}")
            fixed = [k for k, v in (c.get("fix") or {}).items() if v == 0 and k in ("mag", "re", "n", "q", "pa")]
            if fixed:
                parts.append(f"fixed={fixed}")
            cur_lines.append(" ".join(parts))
    current_model_desc = "\n".join(cur_lines) if cur_lines else "  (单 Sersic 基线，待分解)"

    # 历史轮次摘要（可选）：沿父链路记录每步采纳动作/指标/同层被拒尝试，避免重复踩坑
    history_block = ""
    if history_summary:
        history_block = f"\n\n**历史轮次摘要（请参考，避免重复已失败的尝试，并在此基础上逐步改善）**：\n{history_summary}\n"

    prompt = f"""请分析以下 GALFIT 拟合结果图像（包含原图、模型图、2D残差图、1D表面亮度轮廓图），并输出**下一轮完整的模型规格**。

当前状态：第 {step}/{max_steps} 步。当前模型成分：
{current_model_desc}{history_block}

**重要：阶段一~三的分析务必精炼，每个阶段最多 2-3 句话、抓要点即可，不要长篇展开。必须为阶段四的 JSON 输出预留足够篇幅，确保 JSON 完整闭合、不被截断。**

**阶段一：多模态视觉特征提取（仅客观描述，不做判断）**
1. 描述原图中心星系的特征，推测高概率存在的星系成分（需提供强特征证据支持）
2. 对比原图与模型图的**总体骨架轮廓**：两者是否一致？差异点在哪里？（如外围轮廓是否匹配、中心亮度分布是否吻合）
3. 描述 2D 残差图核心区的空间分布形态（如偶极、环状、一字型等）
4. 描述 2D 残差图外围区的分布特征（如同心环、条带、随机噪声等）
5. 描述 1D 亮度曲线中 Data 与 Model 之间的差异区域，特别关注：
   - 如果存在 sky 成分，sky 成分线是否与 Sky Background 虚线齐平？（偏高或偏低说明 sky 漂移）
   - 各成分的 Re 位置与残差曲线峰值是否对应？（残差峰值出现在某成分 Re 附近说明该成分参数需要调整）

**阶段二：参数审查**
结合以下拟合参数摘要，审查当前各成分的参数收敛情况：
{summary_content}

逐一检查：
1. 各成分的 Re、n、mag、q、PA 当前值是多少？收敛是否合理？
2. 是否有参数触碰边界（n>8, Re<0.2px, q<0.05）？是否存在异常参数值？
3. **重要：遇到参数异常时，应优先分析原因并尝试调参方案（如调整 Re/mag 分配），不要急于添加新成分。**

**阶段三：专家推理**

首先，基于残差诊断树做**结构分析**：
- 是否存在全局系统性异常（整体正/负大面积残差 → sky 问题；对称光晕残差 → PSF 问题）？
- 当前成分是否缺失 Disk/Bulge/Bar/Nucleus？判断依据：
  · Disk+Bulge：大尺度对称亮区（中心过亮外围过暗）→ 需要双成分
  · Bar：中心区域"一字型"或"X型"亮区 → 添加低 n(~0.5) 高椭率 Sersic
  · Nucleus/AGN：1D profile 左侧(<5pix) 明显尖峰且无法通过调参解决 → PSF
- 残差是否已接近"电视机雪花"般的纯随机分布（可终止拟合）？
- 分析顺序：**先总体后细节**——先保障原图与模型的总体轮廓相近（如 Disk 亮度区域、Bar 方向大小），总体轮廓匹配后再处理中心细节（Bulge/Nucleus）。

然后，做**参数分析**（尤其当成分结构已基本完整，即已有 {num_sersic} 个成分时，应优先考虑参数微调而非继续添加成分）：
- 各成分 Re 的大小关系是否合理？通常应满足 Re_disk > Re_bar > Re_bulge。如果 Bulge 的 Re 大于 Disk 的 Re，可能成分角色互换了，需要交换标签或重新分配参数。
- mag 通量分配是否均衡？将单成分拆为双成分时，按 3:7 或 4:6 比例分配通量。如果某成分通量占比极低（远暗于主成分 3+ mag），需要重新分配。
- n 值是否符合物理预期？Bulge 的 n 一般在 0.1~8 之间；如果 n<1 且 q<0.5，该成分可能实际上是 Bar 而非 Bulge。Disk 的 n 不一定等于 1，也可以 <1（平缓盘）。
- 如果星系边缘仍存在明显正残差，说明 Disk 的 Re 或 Mag 设置过低，同时可能挤压了 Bulge 的 Re，需要重新分配 Disk 和 Bulge 的参数初始值。
"""

    if expert_gt:
        hint_lines = []
        mtype = expert_gt.get("MType", "unknown")
        mtype2 = expert_gt.get("MType2", "")
        type_desc = f"{mtype}" + (f" ({mtype2})" if mtype2 else "")
        hint_lines.append(f"该星系形态类型: {type_desc}，专家标注包含以下成分：")

        if expert_gt.get("re_disk", 0) > 0:
            hint_lines.append(f"- Disk: Re={expert_gt['re_disk']:.2f}px, mag={expert_gt.get('mag_disk', 'N/A')}, n={expert_gt.get('sersic_disk', 1)}, q={expert_gt.get('axis_ratio_disk', 'N/A')}")
        if expert_gt.get("re_bulge", 0) > 0:
            hint_lines.append(f"- Bulge: Re={expert_gt['re_bulge']:.2f}px, mag={expert_gt.get('mag_bulge', 'N/A')}, n={expert_gt.get('sersic_bulge', 'N/A')}, q={expert_gt.get('axis_ratio_bulge', 'N/A')}")
        if expert_gt.get("re_bar", 0) > 0:
            hint_lines.append(f"- Bar: Re={expert_gt['re_bar']:.2f}px, mag={expert_gt.get('mag_bar', 'N/A')}, n={expert_gt.get('sersic_bar', 'N/A')}, q={expert_gt.get('axis_ratio_bar', 'N/A')}")

        expert_section = "\n".join(hint_lines)
        prompt += f"""

**参考信息（专家标注）**：
{expert_section}
请以此作为参数初值参考，但仍需根据残差图独立判断结构决策。
"""

    prompt += f"""
**阶段四：输出下一轮完整模型规格**
基于以上分析，输出**一个** JSON 对象，给出下一轮**全部成分**的完整参数表（在当前模型基础上演进，成分数量相对上一轮最多 ±1 个）。

决策优先级：**结构缺陷 > 参数分配不合理 > 细节微调**。即使不增减成分，也应给出参数/ fix 的合理调整（如释放 Bulge n、把 Bar 的 n 固定 0.5、重分配通量）。

**输出格式**：用 ```json``` 代码块输出，格式如下：

{{
  "components": [
    {{"role":"bulge|disk|bar|nucleus|companion","model":"sersic|expdisk|psf",
      "x":<float或null>,"y":<float或null>,"mag":<float>,
      "re":<float>,"n":<float>,"q":<float>,"pa":<float>,
      "fix":{{"n":0}}}}
  ],
  "sky": {{"value": <float或null>, "fix": 0}},
  "target": "<本轮物理目标，一句话>",
  "confidence": <0-1>,
  "reasoning": "<简要原因>"
}}

字段说明：
- "components": 下一轮**全部**成分（含从上一轮继承、可修改的成分 + 至多新增 1 个）。每个成分：
  - "role": 物理角色 bulge/disk/bar/nucleus/companion
  - "model": **disk 用 expdisk；bulge/bar 用 sersic；nucleus/AGN（Re<0.2px）用 psf**
  - "x","y": 像素坐标；同心成分填 null（自动继承中心），仅伴星系需给绝对坐标
  - "mag": 积分星等（拆分时按 3:7 或 4:6 分配通量）
  - "re": 有效半径(pix)；expdisk 也填 Re（系统会自动转 Rs）；psf 不需要
  - "n": Sersic 指数；expdisk/psf 省略；**bar 固定 0.5**
  - "q","pa": 轴比、位置角；bar 的 q 取 0.2-0.4
  - "fix": 逐参数冻结标志，**1=free（拟合），0=fixed（冻结）**；只列需要冻结的参数（如 {{"n":0}} 锁住 n），未列默认 free
- "sky": value 为 null 表示沿用当前 sky 值；fix 恒为 0（冻结到背景）
- "confidence": 0-1；"target"/"reasoning": 简述本轮目标与依据

示例 — 把单 Sersic 拆为 Bulge(n=4 锁定) + Disk(expdisk)：
```json
{{
  "components": [
    {{"role":"bulge","model":"sersic","x":null,"y":null,"mag":16.5,"re":3.0,"n":4.0,"q":0.8,"pa":30.0,"fix":{{"n":0}}}},
    {{"role":"disk","model":"expdisk","x":null,"y":null,"mag":16.2,"re":12.0,"q":0.7,"pa":30.0,"fix":{{}}}}
  ],
  "sky": {{"value": null, "fix": 0}},
  "target": "拆为 Bulge(n=4 fix)+Disk(expdisk)，按 4:6 分配通量",
  "confidence": 0.85,
  "reasoning": "中心过亮外围过暗，单成分不足，需双成分；先锁 Bulge n=4 分配通量"
}}
```

示例 — 在双成分基础上释放 Bulge 的 n（不增减成分，仅改 fix）：
```json
{{
  "components": [
    {{"role":"bulge","model":"sersic","x":null,"y":null,"mag":16.5,"re":3.0,"n":4.0,"q":0.8,"pa":30.0,"fix":{{}}}},
    {{"role":"disk","model":"expdisk","x":null,"y":null,"mag":16.2,"re":12.0,"q":0.7,"pa":30.0,"fix":{{}}}}
  ],
  "sky": {{"value": null, "fix": 0}},
  "target": "Bulge 通量已分配，释放 n 重新拟合",
  "confidence": 0.8,
  "reasoning": "上一轮 Bulge n 锁 4 已稳定，现释放 n 看真实集中度"
}}
```

请在阶段一到三完成精炼分析后，用 ```json``` 代码块输出完整模型规格 JSON。"""

    return prompt


def _derive_full_comparison_path(cropped_path: str) -> Optional[str]:
    if not cropped_path:
        return None
    stem, ext = os.path.splitext(cropped_path)
    if stem.endswith("_cutoff"):
        full_path = stem[:-len("_cutoff")] + ext
        if os.path.exists(full_path):
            return full_path
    if os.path.exists(cropped_path):
        return cropped_path
    return None


def call_vlm_proposal(
    image_path: str,
    prompt: str,
    model_name: str,
    api_key: str = None,
    temperature: float = 0.7,
    max_tokens: int = 8192,
    timeout: int = 300,
) -> Tuple[str, Dict]:
    return get_openAI_response_one_image(
        api_key=api_key,
        model_name=model_name,
        prompt=prompt,
        image_path=image_path,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=timeout,
    )


# ============================================================
# JSON 提取
# ============================================================

def _extract_json_object_from_text(text: str) -> Optional[dict]:
    text = text.strip()

    try:
        result = json.loads(text)
        if isinstance(result, dict):
            return result
    except json.JSONDecodeError:
        pass

    code_block_match = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', text, flags=re.DOTALL)
    if code_block_match:
        try:
            result = json.loads(code_block_match.group(1).strip())
            if isinstance(result, dict):
                return result
            if isinstance(result, list) and len(result) > 0 and isinstance(result[0], dict):
                return result[0]
        except json.JSONDecodeError:
            pass

    obj_match = re.search(r'\{.*\}', text, flags=re.DOTALL)
    if obj_match:
        try:
            result = json.loads(obj_match.group(0))
            if isinstance(result, dict):
                return result
        except json.JSONDecodeError:
            pass

    return None


# ============================================================
# 方案B：完整模型规格 解析 / 校验 / diff / 去重
# ============================================================

VALID_MODELS = {"sersic", "expdisk", "psf"}


def _infer_model(role: str, given_model: str, re_val) -> str:
    role = (role or "").lower().strip()
    m = (given_model or "").lower().strip()
    if role == "disk":
        return "expdisk"
    if role in ("bulge", "bar"):
        return "sersic"
    if role in ("nucleus", "agn"):
        return "psf" if (re_val is None or (re_val is not None and re_val < 0.2)) else "sersic"
    if m in VALID_MODELS:
        return m
    return "sersic"


def _normalize_component(c: dict, expert_gt: Optional[dict] = None) -> Optional[dict]:
    """规范化单个成分；mag 缺失则返回 None（视为非法规格）。

    bar 参数处理原则（2026-06 修订）：
      - VLM 输出永远是基础事实
      - 有 Gadotti 真值：以真值为中心 ±20% 作合理范围，VLM 输出超界才 clamp；n 释放为 free
      - 无 Gadotti 真值：用教科书边界（q∈[0.2,0.4], n=0.5 fix），让 VLM 误提的 bar
        在 unbarred 星系上自然失败（→ DPO 负样本）
    """
    if not isinstance(c, dict):
        return None
    role = str(c.get("role", "")).lower().strip()
    try:
        mag = float(c.get("mag"))
    except (TypeError, ValueError):
        return None

    re_in = c.get("re")
    try:
        re_in = float(re_in) if re_in is not None else None
    except (TypeError, ValueError):
        re_in = None

    model = _infer_model(role, c.get("model"), re_in)

    raw_fix = c.get("fix") or {}
    fix = {}
    for k, v in raw_fix.items():
        try:
            fix[str(k).lower()] = 1 if int(v) == 1 else 0
        except (TypeError, ValueError):
            continue

    comp = {"role": role or model, "model": model, "mag": mag, "fix": fix}

    # x,y：仅 companion 允许 VLM 给绝对坐标，其余同心(None→继承父中心)
    if role == "companion":
        for k in ("x", "y"):
            try:
                comp[k] = float(c[k]) if c.get(k) is not None else None
            except (TypeError, ValueError):
                comp[k] = None
    else:
        comp["x"] = None
        comp["y"] = None

    if model != "psf":
        re_val = re_in if re_in is not None else 5.0
        comp["re"] = max(0.2, re_val)
        q = c.get("q")
        try:
            q = float(q) if q is not None else 0.5
        except (TypeError, ValueError):
            q = 0.5
        if role == "bar":
            expert_q = (expert_gt or {}).get("axis_ratio_bar", 0) if expert_gt else 0
            try:
                expert_q = float(expert_q)
            except (TypeError, ValueError):
                expert_q = 0
            if expert_q and expert_q > 0:
                # 有 Gadotti: 真值 ±20%
                lo = max(0.05, expert_q * 0.8)
                hi = min(1.0, expert_q * 1.2)
            else:
                # 无 Gadotti: 教科书,让 unbarred 误提自然失败
                lo, hi = 0.2, 0.4
            q = max(lo, min(hi, q))
        comp["q"] = max(0.05, min(1.0, q))
        pa = c.get("pa")
        try:
            pa = float(pa) if pa is not None else 0.0
        except (TypeError, ValueError):
            pa = 0.0
        comp["pa"] = max(-180.0, min(180.0, pa))

    if model == "sersic":
        n_in = c.get("n")
        try:
            n_in = float(n_in) if n_in is not None else None
        except (TypeError, ValueError):
            n_in = None
        if role == "bar":
            expert_n = (expert_gt or {}).get("sersic_bar", 0) if expert_gt else 0
            try:
                expert_n = float(expert_n)
            except (TypeError, ValueError):
                expert_n = 0
            if expert_n and expert_n > 0:
                # 有 Gadotti: 真值 ±20%, free 让 GALFIT 微调
                lo, hi = max(0.1, expert_n * 0.8), min(8.0, expert_n * 1.2)
                default_n = expert_n
                comp["n"] = max(lo, min(hi, n_in if n_in is not None else default_n))
                comp["fix"]["n"] = 1
            else:
                # 无 Gadotti: 教科书 0.5 fix, 让 unbarred 误提自然失败
                comp["n"] = 0.5
                comp["fix"]["n"] = 0
        else:
            comp["n"] = max(0.1, min(8.0, n_in if n_in is not None else 1.0))
    return comp


def parse_model_spec(vlm_response: str, parent_components: list, expert_gt: Optional[dict] = None) -> Optional[dict]:
    """
    解析 VLM 输出的完整模型规格；校验 ≤1 成分增减；返回携带 spec/coarse_label/diff 的 action。
    expert_gt: Gadotti 专家真值（如有），用于指导 bar 等成分的 clamp 范围。
    """
    raw = _extract_json_object_from_text(vlm_response)
    if not raw:
        tail = (vlm_response or "").strip()[-200:]
        print(f"    ⚠️ [VLM Proposal] JSON 解析失败 | 响应长度={len(vlm_response or '')} 末尾200字符: ...{tail!r}")
        return None

    raw_comps = raw.get("components")
    if not isinstance(raw_comps, list) or not raw_comps:
        print("    ⚠️ [VLM Proposal] 规格无 components，跳过")
        return None

    comps = []
    for c in raw_comps:
        nc = _normalize_component(c, expert_gt=expert_gt)
        if nc is None:
            print("    ⚠️ [VLM Proposal] 成分缺 mag/非法，整条规格丢弃")
            return None
        comps.append(nc)

    # ≤1 成分增减校验
    parent_n = len(parent_components or [])
    if abs(len(comps) - parent_n) > 1:
        print(f"    ⚠️ [VLM Proposal] 成分数变化 {parent_n}->{len(comps)} 超过 ±1，丢弃该规格")
        return None

    # sky
    raw_sky = raw.get("sky") or {}
    sky = {"value": raw_sky.get("value"), "fix": 0}  # sky 恒冻结
    try:
        if sky["value"] is not None:
            sky["value"] = float(sky["value"])
    except (TypeError, ValueError):
        sky["value"] = None

    spec = {"components": comps, "sky": sky}
    diff, coarse = diff_spec_vs_parent(parent_components or [], comps)

    return {
        "spec": spec,
        "components": comps,
        "coarse_label": coarse,
        "diff": diff,
        "confidence": max(0.0, min(1.0, float(raw.get("confidence", 0.5)))),
        "reasoning": str(raw.get("reasoning", "")),
        "target": str(raw.get("target", "")),
        "source": "vlm_proposal",
    }


def _coarse_from_added(comp: dict) -> str:
    model = comp.get("model")
    role = comp.get("role", "")
    if model == "psf":
        return "add_psf"
    if model == "expdisk" or role == "disk":
        return "add_disk"
    if role == "bulge":
        return "add_bulge"
    if role == "bar":
        return "add_bar"
    return "add_psf" if role in ("nucleus", "agn", "companion") else "add_bulge"


def diff_spec_vs_parent(parent_components: list, child_components: list) -> Tuple[dict, str]:
    """对比父子成分，产出训练用动作标签 + 统计用粗标签。"""
    P, C = len(parent_components), len(child_components)
    if C == P + 1:
        added = child_components[-1]  # VLM 通常把新成分追加在末尾
        return ({"kind": "add", "role": added.get("role"), "model": added.get("model")},
                _coarse_from_added(added))
    if C == P - 1:
        return ({"kind": "delete"}, "delete")

    # 同数量 → modify：逐 index 比对参数与 fix 变化
    param_changes, fix_changes = {}, {}
    for i in range(min(P, C)):
        pc, cc = parent_components[i], child_components[i]
        for k in ("mag", "re", "n", "q", "pa"):
            pv, cv = pc.get(k), cc.get(k)
            if pv is not None and cv is not None and abs(pv - cv) > max(abs(pv) * 0.02, 1e-3):
                param_changes[f"{i}.{k}"] = [round(pv, 4), round(cv, 4)]
        pf, cf = pc.get("fix", {}) or {}, cc.get("fix", {}) or {}
        for k in set(pf) | set(cf):
            if pf.get(k, 1) != cf.get(k, 1):
                fix_changes[f"{i}.{k}"] = [pf.get(k, 1), cf.get(k, 1)]
    return ({"kind": "modify", "param_changes": param_changes, "fix_changes": fix_changes}, "modify")


def _spec_fingerprint(decision: dict) -> str:
    comps = decision.get("components", [])
    items = []
    for c in comps:
        fix_t = tuple(sorted((c.get("fix") or {}).items()))
        items.append((
            c.get("model"),
            round(c.get("mag", 0.0), 1),
            round(c.get("re", 0.0) or 0.0, 1),
            round(c.get("n", 0.0) or 0.0, 1),
            round(c.get("q", 0.0) or 0.0, 2),
            round(c.get("pa", 0.0) or 0.0, 0),
            fix_t,
        ))
    items.sort()
    sky = decision.get("spec", {}).get("sky", {})
    return f"{items}|sky_fix={sky.get('fix')}"


def _deduplicate_decisions(decisions: List[dict]) -> List[dict]:
    seen, unique = set(), []
    for d in decisions:
        fp = _spec_fingerprint(d)
        if fp not in seen:
            seen.add(fp)
            unique.append(d)
    return unique


# ============================================================
# 并发调用
# ============================================================

async def _call_vlm_single(
    image_path: str,
    prompt_text: str,
    model_name: str,
    temperature: float,
    parent_components: list,
    call_idx: int,
    expert_gt: Optional[dict] = None,
) -> Tuple[Optional[dict], Optional[dict]]:
    try:
        print(f"      🧠 [VLM Call {call_idx}] temp={temperature:.2f} 调用中...")
        response_text, usage = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: call_vlm_proposal(
                image_path=image_path,
                prompt=prompt_text,
                model_name=model_name,
                temperature=temperature,
            )
        )
        decision = parse_model_spec(response_text, parent_components, expert_gt=expert_gt)
        if decision:
            decision["full_response"] = response_text
            print(f"      ✅ [VLM Call {call_idx}] 解析成功: {decision['coarse_label']}, "
                  f"成分数={len(decision['components'])}, conf={decision['confidence']:.2f}")
        else:
            print(f"      ⚠️ [VLM Call {call_idx}] 解析失败/规格非法，跳过")
        return decision, usage
    except Exception as e:
        print(f"      ❌ [VLM Call {call_idx}] 调用失败: {e}")
        return None, None


async def _call_vlm_with_retry(
    image_path: str,
    prompt_text: str,
    model_name: str,
    temperature: float,
    parent_components: list,
    call_idx: int,
    expert_gt: Optional[dict] = None,
) -> Tuple[Optional[dict], Optional[dict]]:
    decision, usage = await _call_vlm_single(
        image_path, prompt_text, model_name, temperature, parent_components, call_idx,
        expert_gt=expert_gt,
    )
    if decision is not None:
        return decision, usage

    retry_temp = min(temperature + 0.15, 1.0)
    print(f"      🔄 [VLM Call {call_idx}] 重试 (temp={retry_temp:.2f})...")
    decision, usage = await _call_vlm_single(
        image_path, prompt_text, model_name, retry_temp, parent_components, call_idx,
        expert_gt=expert_gt,
    )
    return decision, usage


async def generate_vlm_proposals(
    state_context: dict,
    current_step: int,
    num_calls: int = 4,
    model_name: str = "gemini-3.1-pro-preview",
    max_steps: int = 10,
) -> Tuple[List[dict], Optional[dict]]:
    """
    方案B：VLM 完整模型规格提议生成入口（异步）。
    并发调用 VLM num_calls 次，每次输出 1 份完整模型规格，去重后返回。
    Returns: (unique_decisions_list, aggregated_usage_or_None)
    """
    num_sersic = state_context.get("num_sersic", 1)
    parent_components = state_context.get("parent_components") or []
    residual_path = state_context.get("residual_path")
    summary_path = state_context.get("summary_path")

    summary_content = ""
    if summary_path and os.path.exists(summary_path):
        try:
            summary_content = read_summary_md(summary_path)
        except Exception as e:
            print(f"    ⚠️ [VLM Proposal] summary 读取失败: {e}")
            summary_content = "(参数摘要不可用)"
    else:
        summary_content = "(参数摘要不可用)"

    expert_gt = state_context.get("expert_gt")
    history_summary = state_context.get("history_summary")

    prompt = build_proposal_prompt(
        summary_content, current_step, max_steps, num_sersic,
        expert_gt=expert_gt, current_components=parent_components,
        history_summary=history_summary,
    )
    prompt_text = SYSTEM_PROMPT + "\n\n" + prompt

    image_path = _derive_full_comparison_path(residual_path)
    if not image_path or not os.path.exists(image_path):
        print(f"    ⚠️ [VLM Proposal] 对比图不存在: {image_path}，返回空提议")
        return [], None

    if num_calls <= 1:
        temperatures = [0.35]
    else:
        # 2026-06 修订: 温度收窄到 [0.2, 0.5]，更聚焦专家 hint，减少高温乱猜
        # 原: [0.3, 0.5, 0.7, 0.9] → 现: [0.2, 0.3, 0.4, 0.5]
        temperatures = [0.2 + 0.3 * i / (num_calls - 1) for i in range(num_calls)]

    print(f"    🧠 [VLM Proposal] 并发 {num_calls} 次调用 ({model_name})，图片: {os.path.basename(image_path)}")
    if expert_gt:
        print(f"    🧠 [VLM Proposal] 已注入专家参数提示 (MType: {expert_gt.get('MType', '?')})")

    tasks = [
        _call_vlm_with_retry(image_path, prompt_text, model_name, temp, parent_components, i,
                              expert_gt=expert_gt)
        for i, temp in enumerate(temperatures)
    ]
    results = await asyncio.gather(*tasks)

    all_decisions = []
    agg_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_calls": 0}

    for decision, usage in results:
        if usage:
            agg_usage["prompt_tokens"] += usage.get("prompt_tokens", 0)
            agg_usage["completion_tokens"] += usage.get("completion_tokens", 0)
            agg_usage["total_calls"] += 1
        if decision is not None:
            all_decisions.append(decision)

    if not all_decisions:
        print(f"    ⚠️ [VLM Proposal] 所有 {num_calls} 次调用均失败，返回空提议")
        return [], agg_usage if agg_usage["total_calls"] > 0 else None

    unique = _deduplicate_decisions(all_decisions)
    print(f"    ✅ [VLM Proposal] 获得 {len(all_decisions)} 个规格，去重后 {len(unique)} 个唯一规格")

    return unique, agg_usage
