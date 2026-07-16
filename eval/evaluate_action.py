"""
离线评测核心原语：评估模型输出与 GT 的匹配度（不跑 GALFIT）。

用于步骤级 teacher-forcing 评测：给模型专家输入，比较其输出与 GT action 的一致性。
衡量维度：格式正确率、动作类型准确率、参数精度（tolerance 归一化）。

此模块独立于 RL reward（reward_for_rl.py），后者评估执行结果质量。
"""

import json
import re
from collections import defaultdict


# ============================================================
# JSON 解析
# ============================================================

def _fix_json_escapes(s):
    """修复模型输出中常见的非法 JSON 转义（如 LaTeX \\chi, \\nu）。"""
    return re.sub(r'\\(?!["\\/bfnrtu])', r'\\\\', s)


def parse_json_spec(text):
    """从模型输出中提取 JSON spec dict。支持 ```json``` 块和裸 JSON。"""
    if not text:
        return None

    patterns = [
        r'```json\s*\n(.*?)\n\s*```',
        r'```json\s*(.*?)```',
        r'```\s*\n(\{.*?\})\s*\n\s*```',
        r'```\s*(\{.*?\})\s*```',
    ]
    for pat in patterns:
        m = re.search(pat, text, re.DOTALL)
        if m:
            raw = m.group(1)
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                pass
            try:
                return json.loads(_fix_json_escapes(raw))
            except json.JSONDecodeError:
                continue

    try:
        start = text.rfind('{"components')
        if start < 0:
            start = text.rfind('{')
        end = text.rfind('}')
        if start >= 0 and end > start:
            candidate = text[start:end + 1]
            return json.loads(candidate)
    except Exception:
        pass
    return None


# ============================================================
# 动作类型分类
# ============================================================

def normalize_coarse_label(label):
    """细粒度 label → 粗粒度：add_psf/add_disk/add_sersic → add, delete_* → delete。"""
    if not label:
        return "unknown"
    label = label.lower().strip()
    if label.startswith("add"):
        return "add"
    if label.startswith("delete") or label.startswith("remove"):
        return "delete"
    if label in ("modify", "unknown"):
        return label
    return label


def derive_coarse_label(pred_spec):
    """从 pred spec 的 target 文字推导粗动作类型。"""
    if not pred_spec:
        return "unknown"
    target = (pred_spec.get("target") or "").lower()

    if any(kw in target for kw in ["删除", "移除", "去掉", "减少"]):
        return "delete"
    if any(kw in target for kw in ["添加", "增加", "新增", "拆分", "加入", "引入"]):
        return "add"
    return "modify"


# ============================================================
# 组件匹配
# ============================================================

def match_components(pred_comps, gt_comps):
    """按 role/model 贪心匹配，返回 (matched_pairs, unmatched_pred, unmatched_gt)。"""
    gt_available = list(range(len(gt_comps)))
    matched = []
    pred_used = set()

    for pi, pc in enumerate(pred_comps):
        p_role = (pc.get("role") or pc.get("name") or "").lower()
        p_model = (pc.get("model") or "").lower()
        best_gi = None
        best_score = -1
        for gi in gt_available:
            gc = gt_comps[gi]
            g_role = (gc.get("role") or gc.get("name") or "").lower()
            g_model = (gc.get("model") or "").lower()
            score = 0
            if p_role and g_role and p_role == g_role:
                score += 2
            if p_model and g_model and p_model == g_model:
                score += 1
            if score > best_score:
                best_score = score
                best_gi = gi
        if best_gi is not None and best_score > 0:
            matched.append((pi, best_gi))
            pred_used.add(pi)
            gt_available.remove(best_gi)

    unmatched_pred = [i for i in range(len(pred_comps)) if i not in pred_used]
    return matched, unmatched_pred, gt_available


# ============================================================
# 参数精度
# ============================================================

PARAM_KEYS = ["mag", "re", "n", "q", "pa"]

TOLERANCES = {
    "mag": 0.5,
    "n": 1.0,
    "q": 0.15,
    "pa": 20.0,
}


def _re_tolerance(gt_re):
    """Re 的动态 tolerance：max(2px, gt×0.5)。"""
    if gt_re is None or gt_re <= 0:
        return 2.0
    return max(2.0, gt_re * 0.5)


def _pa_diff(a, b):
    """位置角差值（0~90°），处理 180° wrap-around。"""
    diff = abs(a - b) % 180.0
    return min(diff, 180.0 - diff)


def _get_tolerance(param, gt_value=None):
    if param == "re":
        return _re_tolerance(gt_value)
    return TOLERANCES.get(param, 1.0)


def compute_param_score(pred_comp, gt_comp):
    """
    计算两个匹配组件间的参数精度。
    返回 (scores_dict, diffs_dict)，scores 归一化到 0~1。
    """
    scores = {}
    diffs = {}

    for k in PARAM_KEYS:
        pv = pred_comp.get(k)
        gv = gt_comp.get(k)
        if pv is None or gv is None:
            continue
        try:
            pv, gv = float(pv), float(gv)
        except (ValueError, TypeError):
            continue

        if k == "pa":
            diff = _pa_diff(pv, gv)
        else:
            diff = abs(pv - gv)

        tol = _get_tolerance(k, gv)
        score = max(0.0, 1.0 - diff / tol)

        diffs[k] = diff
        scores[k] = score

    return scores, diffs


# ============================================================
# 核心评估函数
# ============================================================

def evaluate_galfit_action(pred_text, gt_spec, gt_label, pred_spec=None):
    """
    离线评测：模型输出 vs GT。

    Args:
        pred_text: 模型原始文本输出
        gt_spec: GT action spec dict (components + sky + target)
        gt_label: GT coarse label (add_disk/modify/delete/...)
        pred_spec: 可选，已解析的 pred spec（跳过 parse）

    Returns:
        dict with: format_ok, type_match, pred_label, gt_label,
                   comp_count_match, param_scores, param_diffs,
                   acc_score, n_matched, detail
    """
    gt_label_norm = normalize_coarse_label(gt_label)
    gt_comps = gt_spec.get("components", []) if gt_spec else []

    result = {
        "format_ok": False,
        "type_match": False,
        "pred_label": "unknown",
        "gt_label": gt_label_norm,
        "gt_label_raw": gt_label,
        "comp_count_match": False,
        "param_scores": {},
        "param_diffs": {},
        "acc_score": 0.0,
        "n_matched": 0,
        "pred_n_comps": 0,
        "gt_n_comps": len(gt_comps),
    }

    if pred_spec is None:
        pred_spec = parse_json_spec(pred_text)
    if pred_spec is None:
        return result

    result["format_ok"] = True

    pred_comps = pred_spec.get("components", [])
    result["pred_n_comps"] = len(pred_comps)
    result["comp_count_match"] = len(pred_comps) == len(gt_comps)

    pred_label = derive_coarse_label(pred_spec)
    result["pred_label"] = pred_label
    result["type_match"] = (pred_label == gt_label_norm)

    matched, _, _ = match_components(pred_comps, gt_comps)
    result["n_matched"] = len(matched)

    all_scores = defaultdict(list)
    all_diffs = defaultdict(list)
    comp_acc_scores = []

    for pi, gi in matched:
        scores, diffs = compute_param_score(pred_comps[pi], gt_comps[gi])
        for k, v in scores.items():
            all_scores[k].append(v)
        for k, v in diffs.items():
            all_diffs[k].append(v)
        if scores:
            comp_acc_scores.append(sum(scores.values()) / len(scores))

    result["param_scores"] = {k: sum(vs) / len(vs) for k, vs in all_scores.items() if vs}
    result["param_diffs"] = dict(all_diffs)

    if comp_acc_scores:
        result["acc_score"] = sum(comp_acc_scores) / len(comp_acc_scores)

    return result
