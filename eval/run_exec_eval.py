"""
执行评测（步骤级 teacher-forcing + GALFIT 执行）。

**Option A 语义**（"动作前 vs 动作后"，见 eval/评测体系设计.md 2.1）：
从 GT 第 k 步状态（parent）出发，模型预测 action → GALFIT 执行 → 得到 model_new 状态。
判断 parent → model_new 是不是有效改进。**不跟 GT 第 k+1 步比**（那是 Option B，语义错）。

流程（对每条测试样本）：
  1. 从 GT 轨迹第 k 步的 parent 状态出发（feedme + residual + summary）
  2. 构造 prompt → 模型推理 → 得到 pred_text → 解析出 action spec
  3. 用 write_feedme_from_spec 生成新 feedme → 执行 GALFIT → model_new
  4. 三路评价：
     (a) **VLM reward** (Option A)：`vlm_reward_for_step(parent, model_new)` → improvement ∈ {0,1}
     (b) **Rule-based reward** (v11)：`compute_rl_reward(parent_metrics, model_new_metrics, ...)` → SSR
     (c) **参数距离**（诊断用，不打分）：evaluate_galfit_action(pred, gt)
     另外报告：chi2_ratio, bic_diff 作为**执行成功情况的诊断**，不参与 SSR

汇总指标：
  - Format rate + type accuracy + parameter score（沿用 offline eval，诊断）
  - GALFIT success rate（模型能不能生成合法 spec）
  - SSR = P(rule_reward > threshold)  # 主指标
  - VLM improvement rate = P(vlm.improvement == 1)  # 主指标
  - VLM-Rule 一致率 = P(rule_binary == vlm.improvement)  # 交叉验证
  - Mean chi2_ratio, Mean bic_diff, Mean rl_reward（诊断）

用法（在 A6000 上）：
    source /media/data/anaconda3/etc/profile.d/conda.sh && conda activate llama-factory
    cd /media/zhongling/wyh/GalDecomp_Gen

    # 完整跑：推理 + GALFIT + 两套 reward
    python -m eval.run_exec_eval \\
        --input-dir output/E7_full__vlm_proposal_gemini-3.1-pro-preview_vlm_reward_gemini-3.1-pro-preview_hist \\
        --test-galaxies output/E7_full__vlm_proposal_gemini-3.1-pro-preview_vlm_reward_gemini-3.1-pro-preview_hist/test_galaxies.json \\
        --model-path /media/zhongling/huggingface/Qwen2.5-VL-7B-Instruct \\
        --adapter-path /media/zhongling/wyh/LLaMA-Factory/saves/qwen2_5vl-7b-galaxy-qlora \\
        --out-dir eval/exec_eval_results \\
        --threshold 0.0514 \\
        --use-vlm --vlm-model gemini-3.1-pro-preview

    # 复用离线评测的推理结果（只跑 GALFIT + reward，不重新推理）
    python -m eval.run_exec_eval \\
        --input-dir output/E7_full__vlm_proposal_gemini-3.1-pro-preview_vlm_reward_gemini-3.1-pro-preview_hist \\
        --test-galaxies output/E7_full__vlm_proposal_gemini-3.1-pro-preview_vlm_reward_gemini-3.1-pro-preview_hist/test_galaxies.json \\
        --model-path dummy --adapter-path dummy \\
        --out-dir eval/exec_eval_results \\
        --threshold 0.0514 \\
        --reuse-predictions eval_results_full/predictions.jsonl

    # 跳过推理和 GALFIT，只重算 reward 和汇总（需要 exec predictions.jsonl 存在）
    python -m eval.run_exec_eval \\
        --input-dir ... --test-galaxies ... \\
        --model-path dummy --adapter-path dummy \\
        --out-dir eval/exec_eval_results \\
        --threshold 0.0514 \\
        --skip-inference
"""

import argparse
import asyncio
import json
import os
import time
import traceback
from collections import defaultdict

from eval.evaluate_action import (
    evaluate_galfit_action,
    parse_json_spec,
    normalize_coarse_label,
)
from eval.reward_for_rl import (
    compute_rl_reward,
    compute_noise_score,
    load_noise_inputs,
)
from eval.vlm_reward import vlm_reward_for_step
from eval.validate_reward_alignment import _parse_fitted_components


# ============================================================
# 数据加载：从 trajectory 树提取测试步骤
# ============================================================

def extract_eval_steps(tree):
    """
    从 trajectory 树中提取 accepted 主链路的每一步，
    返回 (parent_node, child_node) 对列表。
    """
    node_map = {n["node_id"]: n for n in tree.get("nodes", [])}

    root = None
    for n in tree["nodes"]:
        if n.get("depth", -1) == 0 or n.get("parent_id") is None:
            root = n
            break
    if root is None:
        return []

    children_map = defaultdict(list)
    for n in tree["nodes"]:
        pid = n.get("parent_id")
        if pid:
            children_map[pid].append(n)

    pairs = []
    current = root
    while True:
        accepted_children = [
            c for c in children_map.get(current["node_id"], [])
            if c.get("is_accepted") and c.get("status") in (None, "success")
        ]
        if not accepted_children:
            break
        best = min(accepted_children, key=lambda c: c.get("metrics", {}).get("chi2_nu", 9999))
        pairs.append((current, best))
        current = best

    return pairs


# ============================================================
# Prompt 构造（复用 pipeline 逻辑）
# ============================================================

def build_step_prompt(parent_node, child_node, tree, max_steps=15):
    """为单步构造推理 prompt，复用 pipeline 的 prompt 构造逻辑。"""
    from data_gen.vlm_proposal import build_proposal_prompt, SYSTEM_PROMPT
    from data_gen.reward import read_summary_md
    from data_gen.convert_sft_to_llamafactory import (
        _build_history_summary_replica,
        _count_sersic,
    )
    from simulator_env.galfit_actions import parse_components_from_feedme

    summary_path = parent_node.get("summary_path")
    summary_content = "(参数摘要不可用)"
    if summary_path and os.path.exists(summary_path):
        try:
            summary_content = read_summary_md(summary_path)
        except Exception:
            pass

    try:
        current_components = parse_components_from_feedme(parent_node.get("feedme_path"))
    except Exception:
        current_components = []

    history_summary = _build_history_summary_replica(parent_node, tree, history_max_steps=0)

    user_text = build_proposal_prompt(
        summary_content=summary_content,
        step=child_node.get("step", child_node.get("depth", 1)),
        max_steps=max_steps,
        num_sersic=_count_sersic(current_components),
        expert_gt=None,
        current_components=current_components,
        history_summary=history_summary,
    )

    image_path = parent_node.get("residual_path")

    return SYSTEM_PROMPT, user_text, image_path


# ============================================================
# GALFIT 执行
# ============================================================

async def execute_galfit_with_spec(pred_spec, parent_feedme_path, work_dir, node_id):
    """
    用模型预测的 spec 生成新 feedme，执行 GALFIT，返回结果。
    复用 pipeline 的 write_feedme_from_spec + run_galfit。
    """
    from simulator_env.galfit_actions import write_feedme_from_spec
    from src.tools.run_galfit import run_galfit

    os.makedirs(work_dir, exist_ok=True)
    new_feedme_path = os.path.join(work_dir, f"{node_id}_pred.feedme")

    success = write_feedme_from_spec(
        spec=pred_spec,
        root_feedme_path=parent_feedme_path,
        new_feedme_path=new_feedme_path,
    )

    if not success:
        return {"status": "feedme_failed", "error": "write_feedme_from_spec failed"}

    result = await run_galfit(os.path.abspath(new_feedme_path), ["-imax", "100"])

    if result.get("status") != "success":
        return {"status": "galfit_failed", "error": result.get("error", "GALFIT crashed")}

    return {
        "status": "success",
        "image_file": result.get("image_file"),
        "summary_file": result.get("summary_file"),
        "feedme_path": new_feedme_path,
    }


def extract_metrics_from_summary(summary_path):
    """从 summary.md 提取指标，复用 GalfitEnv 的逻辑。"""
    import re
    metrics = {"chi2_nu": 9999.0, "chi2": 999999.0, "ndof": 0}

    if not summary_path or not os.path.exists(summary_path):
        return metrics

    try:
        with open(summary_path, "r", encoding="utf-8") as f:
            content = f.read()

        m = re.search(r'Chi\^2/nu\s*=\s*([\d\.]+)', content)
        if m:
            metrics["chi2_nu"] = float(m.group(1))

        m = re.search(r'Chi\^2\s*=\s*([\d\.]+)', content)
        if m:
            metrics["chi2"] = float(m.group(1))

        m = re.search(r'ndof\s*=\s*(\d+)', content)
        if m:
            metrics["ndof"] = int(m.group(1))

        m = re.search(r'\|\s*BIC\s*\|\s*([-+]?\d*\.?\d+(?:[eE][+-]?\d+)?)\s*\|', content)
        if m:
            metrics["bic"] = float(m.group(1))

        m = re.search(r'χ²₁D/ν[^|]*\|\s*([-+]?\d*\.?\d+(?:[eE][+-]?\d+)?)\s*\|', content)
        if m:
            metrics["chisq1d_nu"] = float(m.group(1))

    except Exception as e:
        print(f"  [WARN] metrics extraction failed: {e}")

    return metrics


# ============================================================
# 残差噪声质量比较
# ============================================================

def compute_residual_noise_comparison(model_summary_path, gt_summary_path,
                                      galaxy_data_dir):
    """
    计算模型和 GT 残差的噪声质量。
    需要 sigma.fits 和 mask.fits（在 galaxy 数据目录下）。
    残差从 GALFIT 输出的 galfit.fits HDU[3] 读取。
    """
    sigma_path = os.path.join(galaxy_data_dir, "sigma.fits")
    mask_path = os.path.join(galaxy_data_dir, "mask.fits")

    if not os.path.exists(sigma_path) or not os.path.exists(mask_path):
        return {"error": "sigma.fits or mask.fits not found"}

    result = {}

    # 这里暂时只返回 metrics 比较，噪声质量需要从 galfit.fits 读残差
    # 实际的 residual FITS 路径需要从 GALFIT 输出中获取
    result["sigma_path"] = sigma_path
    result["mask_path"] = mask_path

    return result


# ============================================================
# 单步执行评测
# ============================================================

async def eval_single_step(
    parent_node, child_node, tree, pred_text,
    work_dir, threshold=0.0514,
    use_vlm=False, vlm_model=None, api_key=None,
):
    """
    对单步执行完整评测（Option A 语义："动作前 vs 动作后"）：

    1. 参数距离（诊断用，vs GT spec）
    2. GALFIT 执行
    3. Rule-based reward: compute_rl_reward(parent_metrics, model_new_metrics, ...)
       → 二值化: rule_binary = 1 if reward > threshold else 0  ← SSR 用
    4. VLM reward (Option A): vlm_reward_for_step(parent, model_new)
       → improvement ∈ {0, 1}  ← VLM improvement rate 用
    5. 一致率 = agreement(rule_binary, vlm_improvement)

    chi2_ratio / bic_diff 保留但**降级为诊断字段**（不参与主指标）。
    """
    galaxy_id = tree.get("galaxy_id", "unknown")
    node_id = child_node.get("node_id", "unknown")
    depth = child_node.get("depth", -1)

    gt_action = child_node.get("action_from_parent", {})
    gt_spec = gt_action.get("spec") or {}
    if not gt_spec and gt_action.get("components"):
        gt_spec = {
            "components": gt_action["components"],
            "sky": gt_action.get("sky"),
            "target": gt_action.get("target"),
        }
    gt_label = gt_action.get("coarse_label", "unknown")

    # --- (a) 离线评测：参数距离（诊断用） ---
    offline_result = evaluate_galfit_action(pred_text, gt_spec, gt_label)

    result = {
        "galaxy_id": galaxy_id,
        "node_id": node_id,
        "depth": depth,
        **offline_result,
        "galfit_status": "not_run",
        # 主指标（Option A）
        "rule_reward": None,
        "rule_binary": None,           # rule_reward > threshold ? 1 : 0
        "vlm_improvement": None,       # Option A VLM improvement ∈ {0, 1}
        "agreement": None,             # rule_binary == vlm_improvement
        # 诊断字段（不参与主指标）
        "model_metrics": None,
        "gt_metrics": child_node.get("metrics", {}),
        "chi2_ratio_diagnostic": None,     # model_chi2 / gt_chi2, 仅诊断"跟 GT 的差距"
        "bic_diff_diagnostic": None,       # model_bic - gt_bic, 同上
        "vlm_detail": None,
    }

    # --- (b) GALFIT 执行 ---
    pred_spec = parse_json_spec(pred_text)
    parent_feedme = parent_node.get("feedme_path")

    if pred_spec is None:
        result["galfit_status"] = "no_spec"
        return result

    if not parent_feedme or not os.path.exists(parent_feedme):
        result["galfit_status"] = "no_parent_feedme"
        return result

    step_work_dir = os.path.join(work_dir, galaxy_id, node_id)
    galfit_result = await execute_galfit_with_spec(
        pred_spec, parent_feedme, step_work_dir, node_id)

    result["galfit_status"] = galfit_result["status"]

    if galfit_result["status"] != "success":
        result["galfit_error"] = galfit_result.get("error")
        return result

    # --- (c) 提取 metrics + 拟合参数 ---
    model_summary = galfit_result.get("summary_file")
    model_metrics = extract_metrics_from_summary(model_summary)
    gt_metrics = child_node.get("metrics", {})
    result["model_metrics"] = model_metrics
    result["gt_metrics"] = gt_metrics

    # 诊断字段：跟 GT 的差距（不参与打分）
    gt_chi2 = gt_metrics.get("chi2_nu", 9999.0)
    model_chi2 = model_metrics.get("chi2_nu", 9999.0)
    if gt_chi2 > 0 and gt_chi2 < 9999:
        result["chi2_ratio_diagnostic"] = round(model_chi2 / gt_chi2, 4)
    gt_bic = gt_metrics.get("bic")
    model_bic = model_metrics.get("bic")
    if gt_bic is not None and model_bic is not None:
        result["bic_diff_diagnostic"] = round(model_bic - gt_bic, 2)

    # --- (d) Rule-based reward (Option A: parent → model_new) ---
    parent_metrics = parent_node.get("metrics", {})
    fitted_components = _parse_fitted_components(model_summary)

    rl_reward_result = compute_rl_reward(
        old_metrics=parent_metrics,
        new_metrics=model_metrics,
        action_spec=pred_spec,
        fitted_components=fitted_components,
    )
    rule_reward = rl_reward_result["reward"]
    result["rule_reward"] = round(rule_reward, 4)
    result["rule_binary"] = 1 if rule_reward > threshold else 0
    result["rule_reward_detail"] = {
        "bounds_ok": rl_reward_result["bounds_ok"],
        "fitted_bounds_ok": rl_reward_result.get("fitted_bounds_ok", True),
        "chi2_vetoed": rl_reward_result.get("chi2_vetoed", False),
        "r_chi2": rl_reward_result["r_chi2"],
        "r_bic": rl_reward_result["r_bic"],
        "r_noise": rl_reward_result["r_noise"],
    }

    # --- (e) VLM reward (Option A: parent → model_new)，可选 ---
    if use_vlm and vlm_model:
        parent_image = parent_node.get("residual_path")
        parent_summary = parent_node.get("summary_path")
        model_image = galfit_result.get("image_file")

        if not (parent_image and os.path.exists(parent_image)):
            result["vlm_detail"] = {"error": f"parent residual image not found: {parent_image}"}
        elif not (model_image and os.path.exists(model_image)):
            result["vlm_detail"] = {"error": f"model residual image not found: {model_image}"}
        else:
            try:
                vlm_result = vlm_reward_for_step(
                    parent_residual_image_path=parent_image,
                    parent_summary_path=parent_summary,
                    model_new_residual_image_path=model_image,
                    model_new_summary_path=model_summary,
                    model_name=vlm_model,
                    api_key=api_key,
                )
                vlm_imp = int(vlm_result.get("improvement", 0))
                result["vlm_improvement"] = vlm_imp
                result["vlm_detail"] = {
                    "improvement": vlm_imp,
                    "improvement_source": vlm_result.get("improvement_source"),
                    "residual_improved": vlm_result.get("residual_improved"),
                    "param_plausible": vlm_result.get("param_plausible"),
                    "metric_consistent": vlm_result.get("metric_consistent"),
                    "residual_improvement_level": vlm_result.get("residual_improvement_level"),
                    "hard_warnings": vlm_result.get("hard_warnings", []),
                    "reason": vlm_result.get("reason"),
                }
                # 一致率：rule_binary vs vlm_improvement
                result["agreement"] = 1 if result["rule_binary"] == vlm_imp else 0
            except Exception as e:
                result["vlm_detail"] = {"error": str(e)}

    return result


# ============================================================
# 汇总报告
# ============================================================

def aggregate_exec_results(results):
    """汇总执行评测结果（Option A 语义）。

    主指标（决策用）：
      - SSR (Step Success Rate) = mean(rule_binary)
      - VLM improvement rate = mean(vlm_improvement)
      - VLM-Rule agreement = mean(agreement)

    诊断指标（不参与主指标，只帮助排查）：
      - format_rate / type_accuracy / mean_acc_score (离线评测复用)
      - galfit_success_rate (基础能力)
      - mean_chi2_ratio_diagnostic, mean_bic_diff_diagnostic (跟 GT 差距)
      - mean_rule_reward (连续值，看 reward gaming)
    """
    n = len(results)
    if n == 0:
        return {}

    # === 离线评测复用（诊断用） ===
    format_ok = sum(r["format_ok"] for r in results)
    type_match = sum(r["type_match"] for r in results)
    param_all_scores = defaultdict(list)
    for r in results:
        for k, v in r.get("param_scores", {}).items():
            param_all_scores[k].append(v)
    param_scores_avg = {k: round(sum(vs) / len(vs), 4) for k, vs in param_all_scores.items() if vs}
    acc_scores = [r["acc_score"] for r in results if r["format_ok"]]
    mean_acc_score = round(sum(acc_scores) / len(acc_scores), 4) if acc_scores else 0

    # === 基础能力：GALFIT 能不能跑通 ===
    galfit_success = sum(1 for r in results if r["galfit_status"] == "success")
    galfit_rate = galfit_success / n if n > 0 else 0

    # === 主指标 1: SSR (rule-based binary) ===
    rule_binaries = [r["rule_binary"] for r in results if r.get("rule_binary") is not None]
    ssr = round(sum(rule_binaries) / len(rule_binaries), 4) if rule_binaries else None

    # === 主指标 2: VLM improvement rate (Option A) ===
    vlm_imps = [r["vlm_improvement"] for r in results if r.get("vlm_improvement") is not None]
    vlm_imp_rate = round(sum(vlm_imps) / len(vlm_imps), 4) if vlm_imps else None

    # === 主指标 3: VLM-Rule 一致率（交叉验证） ===
    agreements = [r["agreement"] for r in results if r.get("agreement") is not None]
    agreement_rate = round(sum(agreements) / len(agreements), 4) if agreements else None

    # === 诊断: chi2_ratio / bic_diff / mean_rule_reward ===
    chi2_ratios = [r["chi2_ratio_diagnostic"] for r in results if r.get("chi2_ratio_diagnostic") is not None]
    mean_chi2_ratio = round(sum(chi2_ratios) / len(chi2_ratios), 4) if chi2_ratios else None
    bic_diffs = [r["bic_diff_diagnostic"] for r in results if r.get("bic_diff_diagnostic") is not None]
    mean_bic_diff = round(sum(bic_diffs) / len(bic_diffs), 2) if bic_diffs else None
    rule_rewards = [r["rule_reward"] for r in results if r.get("rule_reward") is not None]
    mean_rule_reward = round(sum(rule_rewards) / len(rule_rewards), 4) if rule_rewards else None

    # === 按动作类型分组 ===
    per_type = defaultdict(lambda: {
        "n": 0, "galfit_ok": 0, "type_match": 0,
        "rule_binaries": [], "vlm_imps": [], "agreements": [],
        "chi2_ratios": []})
    for r in results:
        gt = r["gt_label"]
        pt = per_type[gt]
        pt["n"] += 1
        if r["type_match"]:
            pt["type_match"] += 1
        if r["galfit_status"] == "success":
            pt["galfit_ok"] += 1
            if r.get("rule_binary") is not None:
                pt["rule_binaries"].append(r["rule_binary"])
            if r.get("vlm_improvement") is not None:
                pt["vlm_imps"].append(r["vlm_improvement"])
            if r.get("agreement") is not None:
                pt["agreements"].append(r["agreement"])
            if r.get("chi2_ratio_diagnostic") is not None:
                pt["chi2_ratios"].append(r["chi2_ratio_diagnostic"])

    per_type_report = {}
    for label, pt in per_type.items():
        per_type_report[label] = {
            "n": pt["n"],
            "galfit_success_rate": round(pt["galfit_ok"] / pt["n"], 4) if pt["n"] > 0 else 0,
            "type_accuracy": round(pt["type_match"] / pt["n"], 4) if pt["n"] > 0 else 0,
            "ssr": round(sum(pt["rule_binaries"]) / len(pt["rule_binaries"]), 4) if pt["rule_binaries"] else None,
            "vlm_imp_rate": round(sum(pt["vlm_imps"]) / len(pt["vlm_imps"]), 4) if pt["vlm_imps"] else None,
            "agreement_rate": round(sum(pt["agreements"]) / len(pt["agreements"]), 4) if pt["agreements"] else None,
            "mean_chi2_ratio_diagnostic": round(sum(pt["chi2_ratios"]) / len(pt["chi2_ratios"]), 4) if pt["chi2_ratios"] else None,
        }

    return {
        "n_samples": n,
        "n_galfit_success": galfit_success,
        # === 主指标 ===
        "ssr": ssr,                          # 单步成功率（rule-based，binary）
        "vlm_improvement_rate": vlm_imp_rate,  # VLM 判 improvement 的比例
        "agreement_rate": agreement_rate,     # rule vs VLM 一致率
        # === 基础能力 ===
        "format_rate": round(format_ok / n, 4),
        "type_accuracy": round(type_match / format_ok, 4) if format_ok > 0 else 0,
        "galfit_success_rate": round(galfit_rate, 4),
        # === 诊断字段 ===
        "mean_acc_score_diagnostic": mean_acc_score,
        "param_scores_diagnostic": param_scores_avg,
        "mean_chi2_ratio_diagnostic": mean_chi2_ratio,
        "mean_bic_diff_diagnostic": mean_bic_diff,
        "mean_rule_reward_diagnostic": mean_rule_reward,
        # === 按类型分组 ===
        "per_type": per_type_report,
    }


# ============================================================
# 主流程
# ============================================================

async def run_exec_evaluation(
    test_trajectories, model, processor, out_dir,
    max_steps=15, max_new_tokens=4096,
    threshold=0.0514,
    use_vlm=False, vlm_model=None, api_key=None,
    skip_inference=False,
    reuse_predictions_path=None,
):
    """对所有测试轨迹执行评测。"""
    os.makedirs(out_dir, exist_ok=True)
    work_dir = os.path.join(out_dir, "galfit_work")
    pred_path = os.path.join(out_dir, "predictions.jsonl")

    # 展开所有 (parent, child) 步骤对
    all_steps = []
    for tree in test_trajectories:
        pairs = extract_eval_steps(tree)
        for parent, child in pairs:
            all_steps.append((tree, parent, child))

    print(f"测试步骤: {len(all_steps)} 步 (来自 {len(test_trajectories)} 条轨迹)")

    # ---- 加载可复用的推理结果 ----
    reuse_map = {}
    if reuse_predictions_path and os.path.exists(reuse_predictions_path):
        with open(reuse_predictions_path, "r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                rec = json.loads(line)
                key = (rec.get("galaxy_id", ""), rec.get("node_id", ""))
                if key != ("", "") and rec.get("prediction"):
                    reuse_map[key] = rec["prediction"]
        print(f"从 {reuse_predictions_path} 加载 {len(reuse_map)} 条可复用预测")

    # ---- 推理阶段 ----
    if not skip_inference:
        from eval.run_eval import run_inference_single

        predictions = []
        n_reused = 0
        for i, (tree, parent, child) in enumerate(all_steps):
            gid = tree.get("galaxy_id", "unknown")
            nid = child.get("node_id", "unknown")

            print(f"\n[{i + 1}/{len(all_steps)}] {gid}/{nid} (depth={child.get('depth')})")

            # 尝试复用已有预测
            reuse_key = (gid, nid)
            if reuse_key in reuse_map:
                pred_text = reuse_map[reuse_key]
                print(f"  [REUSE] {len(pred_text)} chars from previous predictions")
                predictions.append({
                    "index": i, "galaxy_id": gid, "node_id": nid,
                    "prediction": pred_text, "reused": True,
                })
                n_reused += 1
                continue

            system_prompt, user_text, image_path = build_step_prompt(
                parent, child, tree, max_steps)

            if not image_path or not os.path.exists(image_path):
                print(f"  [SKIP] image not found: {image_path}")
                predictions.append({
                    "index": i, "galaxy_id": gid, "node_id": nid,
                    "prediction": "", "error": "image_not_found",
                })
                continue

            t0 = time.time()
            try:
                pred_text = run_inference_single(
                    model, processor, system_prompt, user_text,
                    image_path, max_new_tokens)
                elapsed = time.time() - t0
                print(f"  inference done ({elapsed:.1f}s, {len(pred_text)} chars)")
                predictions.append({
                    "index": i, "galaxy_id": gid, "node_id": nid,
                    "prediction": pred_text, "elapsed": round(elapsed, 2),
                })
            except Exception as e:
                elapsed = time.time() - t0
                print(f"  [ERROR] {e} ({elapsed:.1f}s)")
                predictions.append({
                    "index": i, "galaxy_id": gid, "node_id": nid,
                    "prediction": "", "error": str(e),
                })

        if n_reused > 0:
            print(f"\n复用了 {n_reused}/{len(all_steps)} 条预测，"
                  f"新推理 {len(all_steps) - n_reused} 条")

        with open(pred_path, "w", encoding="utf-8") as f:
            for p in predictions:
                f.write(json.dumps(p, ensure_ascii=False) + "\n")
        print(f"predictions saved: {pred_path}")
    else:
        print(f"skip inference, loading: {pred_path}")
        with open(pred_path, "r", encoding="utf-8") as f:
            predictions = [json.loads(line) for line in f if line.strip()]

    # ---- 执行评测阶段 ----
    print("\n" + "=" * 60)
    print("  执行评测 (GALFIT + 指标对比)")
    print("=" * 60)

    results = []
    for i, ((tree, parent, child), pred) in enumerate(zip(all_steps, predictions)):
        gid = tree.get("galaxy_id", "unknown")
        nid = child.get("node_id", "unknown")
        pred_text = pred.get("prediction", "")

        print(f"\n[{i + 1}/{len(all_steps)}] {gid}/{nid}")

        t0 = time.time()
        result = await eval_single_step(
            parent, child, tree, pred_text,
            work_dir, threshold=threshold,
            use_vlm=use_vlm, vlm_model=vlm_model, api_key=api_key)
        elapsed = time.time() - t0

        result["elapsed"] = round(elapsed, 2)
        results.append(result)

        status = result["galfit_status"]
        rr = result.get("rule_reward")
        rb = result.get("rule_binary")
        vi = result.get("vlm_improvement")
        rr_str = f"{rr:.3f}" if rr is not None else "N/A"
        rb_str = str(rb) if rb is not None else "N/A"
        vi_str = str(vi) if vi is not None else "N/A"
        print(f"  galfit={status}  rule_reward={rr_str}  rule_binary={rb_str}  vlm_imp={vi_str}  ({elapsed:.1f}s)")

    # ---- 汇总 ----
    agg = aggregate_exec_results(results)

    detail_path = os.path.join(out_dir, "exec_eval_details.jsonl")
    with open(detail_path, "w", encoding="utf-8") as f:
        for r in results:
            f.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")

    report_path = os.path.join(out_dir, "exec_eval_report.json")
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(agg, f, ensure_ascii=False, indent=2)

    # ---- 打印报告 ----
    print("\n" + "=" * 60)
    print("  执行评测报告 (Option A: 动作前后对比)")
    print("=" * 60)
    print(f"  样本数:                 {agg.get('n_samples', 0)}")
    print(f"  Threshold:              {threshold}")
    print()
    print("  === 主指标 ===")
    ssr = agg.get("ssr")
    print(f"  SSR (rule-based):       {ssr:.1%}" if ssr is not None else "  SSR:                    N/A")
    vir = agg.get("vlm_improvement_rate")
    if vir is not None:
        print(f"  VLM improvement rate:   {vir:.1%}")
        ar = agg.get("agreement_rate")
        print(f"  VLM-Rule 一致率:        {ar:.1%}" if ar is not None else "")
    print()
    print("  === 基础能力 ===")
    print(f"  Format rate:            {agg.get('format_rate', 0):.1%}")
    print(f"  Type accuracy:          {agg.get('type_accuracy', 0):.1%}")
    print(f"  GALFIT success rate:    {agg.get('galfit_success_rate', 0):.1%} ({agg.get('n_galfit_success', 0)}/{agg.get('n_samples', 0)})")
    print()
    print("  === 诊断字段 (辅助排查) ===")
    print(f"  Mean acc_score:         {agg.get('mean_acc_score_diagnostic', 0):.3f}")
    ps = agg.get("param_scores_diagnostic", {})
    for k, v in ps.items():
        print(f"    {k:>5}: {v:.3f}")
    cr = agg.get('mean_chi2_ratio_diagnostic')
    print(f"  Mean chi2_ratio vs GT:  {cr:.3f}" if cr is not None else "  Mean chi2_ratio vs GT:  N/A")
    bd = agg.get('mean_bic_diff_diagnostic')
    print(f"  Mean BIC diff vs GT:    {bd:.1f}" if bd is not None else "  Mean BIC diff vs GT:    N/A")
    mrr = agg.get('mean_rule_reward_diagnostic')
    print(f"  Mean rule_reward:       {mrr:.3f}" if mrr is not None else "  Mean rule_reward:       N/A")
    print()
    print("  === 按 action 类型分组 ===")
    for label, metrics in agg.get("per_type", {}).items():
        ssr_t = metrics.get('ssr')
        vlm_t = metrics.get('vlm_imp_rate')
        agr_t = metrics.get('agreement_rate')
        ssr_s = f"{ssr_t:.0%}" if ssr_t is not None else "N/A"
        vlm_s = f"{vlm_t:.0%}" if vlm_t is not None else "N/A"
        agr_s = f"{agr_t:.0%}" if agr_t is not None else "N/A"
        print(f"    {label}: n={metrics['n']}  galfit={metrics['galfit_success_rate']:.0%}  "
              f"type_acc={metrics['type_accuracy']:.0%}  ssr={ssr_s}  vlm_imp={vlm_s}  agree={agr_s}")
    print()
    print(f"  详细结果: {detail_path}")
    print(f"  汇总报告: {report_path}")

    return agg


def main():
    ap = argparse.ArgumentParser(description="执行评测 (teacher-forcing + GALFIT)")
    ap.add_argument("--input-dir", required=True, help="trajectory 输出目录")
    ap.add_argument("--test-galaxies", required=True, help="test_galaxies.json")
    ap.add_argument("--model-path", required=True)
    ap.add_argument("--adapter-path", required=True)
    ap.add_argument("--out-dir", default="eval/exec_eval_results")
    ap.add_argument("--max-steps", type=int, default=15)
    ap.add_argument("--max-new-tokens", type=int, default=4096)
    ap.add_argument("--no-4bit", action="store_true")
    ap.add_argument("--skip-inference", action="store_true",
                    help="跳过推理，从 predictions.jsonl 读取已有预测")
    ap.add_argument("--reuse-predictions", default=None,
                    help="复用已有 predictions.jsonl 的推理结果（按 galaxy_id+node_id 匹配），"
                         "未命中的步骤仍正常推理")
    ap.add_argument("--use-vlm", action="store_true",
                    help="启用 VLM reward (Option A: parent → model_new)，需要 API key")
    ap.add_argument("--vlm-model", default="gemini-3.1-pro-preview")
    ap.add_argument("--api-key", default=None)
    ap.add_argument("--threshold", type=float, default=0.0514,
                    help="rule_reward > threshold 判为 accepted（SSR 用）。默认 0.0514 来自 v11 val 集校准。")
    args = ap.parse_args()

    # 加载测试轨迹
    from data_gen.extract_training_data import load_trajectories
    from data_gen.dataset_utils import _to_physical_id

    with open(args.test_galaxies, "r", encoding="utf-8") as f:
        obj = json.load(f)
    if isinstance(obj, dict):
        test_pids = set(obj.get("test_physical_ids") or obj.get("test_pids") or [])
    else:
        test_pids = set(obj)

    all_trajs = load_trajectories(args.input_dir)
    test_trajs = [t for t in all_trajs
                  if _to_physical_id(t.get("galaxy_id", "")) in test_pids]
    print(f"测试轨迹: {len(test_trajs)} 条 (共 {len(all_trajs)} 条)")

    # 加载模型（有 reuse-predictions 时，如果所有步骤都命中则不需要模型）
    model, processor = None, None
    if not args.skip_inference:
        # 先检查能否跳过模型加载
        need_model = True
        if args.reuse_predictions and os.path.exists(args.reuse_predictions):
            # 快速统计：reuse 能覆盖多少步骤？
            reuse_keys = set()
            with open(args.reuse_predictions, "r", encoding="utf-8") as f:
                for line in f:
                    if not line.strip():
                        continue
                    rec = json.loads(line)
                    k = (rec.get("galaxy_id", ""), rec.get("node_id", ""))
                    if rec.get("prediction"):
                        reuse_keys.add(k)
            # 计算需要推理的步骤数
            n_miss = 0
            for tree in test_trajs:
                for parent, child in extract_eval_steps(tree):
                    gid = tree.get("galaxy_id", "unknown")
                    nid = child.get("node_id", "unknown")
                    if (gid, nid) not in reuse_keys:
                        n_miss += 1
            if n_miss == 0:
                print(f"所有步骤均可复用，跳过模型加载")
                need_model = False
            else:
                print(f"有 {n_miss} 步需要新推理，加载模型...")

        if need_model:
            from eval.run_eval import load_model_and_processor
            model, processor = load_model_and_processor(
                args.model_path, args.adapter_path, use_4bit=not args.no_4bit)

    asyncio.run(run_exec_evaluation(
        test_trajs, model, processor, args.out_dir,
        max_steps=args.max_steps,
        max_new_tokens=args.max_new_tokens,
        threshold=args.threshold,
        use_vlm=args.use_vlm,
        vlm_model=args.vlm_model,
        api_key=args.api_key,
        skip_inference=args.skip_inference,
        reuse_predictions_path=args.reuse_predictions,
    ))


if __name__ == "__main__":
    main()
