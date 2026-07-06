"""
VLM 成分判定模块——对齐 MCP main 分支的成分准确率口径。

MCP 不用 n 阈值反推成分，而是让模型在写总结报告时综合判断、直接输出成分标签列表
（见 src/prompts/workflow_galfit.md 阶段四的 JSON 输出）。本模块复现这一步：给最终最优拟合的
参数表 + 拟合对比图，用对标同款模型判出该模型包含哪些物理成分。

复用资产：
- vlm_proposal.SYSTEM_PROMPT       成分物理判据（Disk/Bulge/Bar/Nucleus 定义）
- vlm_proposal._extract_json_object_from_text  健壮 JSON 提取
- reward.get_openAI_response_one_image         带图调用 VLM
"""

from typing import Optional, Tuple, Dict, Set

from data_gen.vlm_proposal import SYSTEM_PROMPT, _extract_json_object_from_text
from data_gen.reward import get_openAI_response_one_image

# MCP workflow_galfit.md 阶段四的成分词表
COMPONENT_VOCAB = ["Disk", "Bulge", "Bar", "Nucleus", "Companion", "Fourier", "SingleSersic"]

# 归一化：模型可能输出各种写法 → 规范成词表里的类别
_CANON = {
    "disk": "Disk", "disc": "Disk", "expdisk": "Disk",
    "bulge": "Bulge",
    "bar": "Bar",
    "nucleus": "Nucleus", "agn": "Nucleus", "psf": "Nucleus", "point source": "Nucleus",
    "companion": "Companion",
    "fourier": "Fourier", "f1": "Fourier", "fourier mode": "Fourier",
    "singlesersic": "SingleSersic", "single sersic": "SingleSersic", "sersic": "SingleSersic",
}


def _canon_component(name: str) -> Optional[str]:
    """把模型输出的成分名归一化到词表。无法识别返回 None。"""
    if not name:
        return None
    key = str(name).strip().lower()
    if key in _CANON:
        return _CANON[key]
    # 词表原样（大小写不敏感）
    for v in COMPONENT_VOCAB:
        if key == v.lower():
            return v
    return None


def build_labeling_prompt(summary_content: str) -> str:
    """构造成分判定 prompt：复用 SYSTEM_PROMPT 成分判据 + MCP 词表 + JSON 输出格式。"""
    return f"""你是星系形态学成分分析专家。下面给你某星系「最终最优拟合」的参数表和拟合对比图
（对比图从左到右为：原始图 | 模型图 | 残差图 | 1D 光度剖面）。请综合参数表和图像，判断该最优
拟合模型实际包含哪些物理成分。

## 成分物理判据（据此判断每个拟合成分属于哪一类）
{SYSTEM_PROMPT}

## 最终最优拟合参数表
{summary_content}

## 任务
综合上面的参数表与对比图，判断该星系最优模型包含哪些物理成分。成分类型只能从下面词表中选取：
{COMPONENT_VOCAB}
- Disk / Bulge / Bar 严格依据上面的成分物理判据（模型类型、Re、n、q 等）判断
- Nucleus：中心点源（psf）
- Companion / Fourier：伴星系 / 偏心非对称补偿（若无则不列）
- SingleSersic：仅当模型是单个未分解 Sersic、无法明确归入 Disk/Bulge/Bar 时使用

## 输出格式
先简述判断依据，然后**只输出一个 JSON 对象**（不要用 markdown 代码围栏包裹）：
{{"components": ["<成分1>", "<成分2>", ...], "reasoning": "一句话依据"}}
"""


def label_components_via_vlm(
    summary_content: str,
    comparison_image_path: str,
    model_name: str = "gemini-3.1-pro-preview",
    api_key: str = None,
    temperature: float = 0.1,
) -> Tuple[Set[str], Dict]:
    """对最优拟合结果调 VLM 判定物理成分。

    Returns:
        (components_set, meta)
        - components_set: 归一化后的成分集合（含全部识别到的类别，比对时上游再筛三类）
        - meta: {"raw_response","usage","reasoning","parsed_components","error"?}
    """
    prompt = build_labeling_prompt(summary_content or "(参数表不可用)")
    meta: Dict = {"model": model_name}

    try:
        text, usage = get_openAI_response_one_image(
            api_key=api_key,
            model_name=model_name,
            prompt=prompt,
            image_path=comparison_image_path,
            temperature=temperature,
        )
    except Exception as e:
        meta["error"] = f"vlm_call_failed: {e}"
        return set(), meta

    meta["raw_response"] = text
    meta["usage"] = usage or {}

    obj = _extract_json_object_from_text(text)
    if not obj or "components" not in obj:
        meta["error"] = "json_parse_failed"
        meta["parsed_components"] = []
        return set(), meta

    raw_list = obj.get("components") or []
    meta["reasoning"] = obj.get("reasoning", "")
    canon = set()
    dropped = []
    for c in raw_list:
        cc = _canon_component(c)
        if cc:
            canon.add(cc)
        else:
            dropped.append(c)
    meta["parsed_components"] = sorted(canon)
    if dropped:
        meta["unrecognized"] = dropped
    return canon, meta
