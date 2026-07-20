"""
执行评测（步骤级 teacher-forcing + GALFIT 执行）。

在离线评测基础上增加 GALFIT 执行，用残差质量 + 参数距离双维度评价。

流程（对每条测试样本）：
  1. 从 GT 轨迹的第 k 步状态出发（parent feedme + residual）
  2. 构造 prompt → 模型推理 → 得到 pred_text → 解析出 action spec
  3. 用 write_feedme_from_spec 生成新 feedme → 执行 GALFIT
  4. 评价：
     a. 参数距离（pred spec vs GT spec，复用 evaluate_galfit_action）
     b. 指标对比（model chi2/BIC vs GT chi2/BIC）
     c. 残差噪声质量（model vs GT）
     d. VLM 残差比较（可选，compare_model_vs_gt）

用法（在 A6000 上）：
    source /media/data/anaconda3/etc/profile.d/conda.sh && conda activate llama-factory
    cd /media/zhongling/wyh/GalDecomp_Gen

    python -m eval.run_exec_eval \\
        --input-dir output/E7_full__vlm_proposal_gemini-3.1-pro-preview_vlm_reward_gemini-3.1-pro-preview_hist \\
        --test-galaxies output/.../test_galaxies.json \\
        --model-path /media/zhongling/huggingface/Qwen2.5-VL-7B-Instruct \\
        --adapter-path /media/zhongling/wyh/LLaMA-Factory/saves/qwen2_5vl-7b-galaxy-qlora \\
        --out-dir eval/exec_eval_results

    # 跳过推理，只重新评测已有预测
    python -m eval.run_exec_eval \\
        --input-dir ... --test-galaxies ... \\
        --model-path dummy --adapter-path dummy \\
        --out-dir eval/exec_eval_results \\
        --skip-inference

    # 加 VLM 比较（慢，需要 API key）
    python -m eval.run_exec_eval \\
        ... --use-vlm --vlm-model gemini-3.1-pro-preview
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
    work_dir, use_vlm=False, vlm_model=None, api_key=None,
):
    """
    对单步执行完整评测：参数距离 + GALFIT 执行 + 指标对比 + VLM（可选）。
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

    # --- (a) 离线评测：参数距离 ---
    offline_result = evaluate_galfit_action(pred_text, gt_spec, gt_label)

    result = {
        "galaxy_id": galaxy_id,
        "node_id": node_id,
        "depth": depth,
        **offline_result,
        "galfit_status": "not_run",
        "model_metrics": None,
        "gt_metrics": child_node.get("metrics", {}),
        "chi2_ratio": None,
        "bic_diff": None,
        "vlm_result": None,
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

    # --- (c) 指标对比 ---
    model_metrics = extract_metrics_from_summary(galfit_result.get("summary_file"))
    gt_metrics = child_node.get("metrics", {})
    result["model_metrics"] = model_metrics
    result["gt_metrics"] = gt_metrics

    gt_chi2 = gt_metrics.get("chi2_nu", 9999.0)
    model_chi2 = model_metrics.get("chi2_nu", 9999.0)
    if gt_chi2 > 0 and gt_chi2 < 9999:
        result["chi2_ratio"] = round(model_chi2 / gt_chi2, 4)

    gt_bic = gt_metrics.get("bic")
    model_bic = model_metrics.get("bic")
    if gt_bic is not None and model_bic is not None:
        result["bic_diff"] = round(model_bic - gt_bic, 2)

    # --- (d) RL Reward（model vs parent，增量改善） ---
    parent_metrics = parent_node.get("metrics", {})
    rl_reward_result = compute_rl_reward(
        old_metrics=parent_metrics,
        new_metrics=model_metrics,
        action_spec=pred_spec,
    )
    result["rl_reward"] = rl_reward_result["reward"]
    result["rl_reward_detail"] = {
        "bounds_ok": rl_reward_result["bounds_ok"],
        "r_chi2": rl_reward_result["r_chi2"],
        "r_noise": rl_reward_result["r_noise"],
    }

    # GT 的 RL reward 作为参考基线
    gt_rl_reward = compute_rl_reward(
        old_metrics=parent_metrics,
        new_metrics=gt_metrics,
        action_spec=gt_spec,
    )
    result["gt_rl_reward"] = gt_rl_reward["reward"]

    # --- (e) VLM 比较（可选） ---
    if use_vlm and vlm_model:
        model_image = galfit_result.get("image_file")
        gt_image = child_node.get("residual_path")
        model_summary = galfit_result.get("summary_file")
        gt_summary = child_node.get("summary_path")

        if (model_image and os.path.exists(model_image) and
                gt_image and os.path.exists(gt_image)):
            try:
                from eval.vlm_compare import compare_model_vs_gt
                vlm_result = compare_model_vs_gt(
                    model_residual_image_path=model_image,
                    gt_residual_image_path=gt_image,
                    model_summary_path=model_summary,
                    gt_summary_path=gt_summary,
                    model_name=vlm_model,
                    api_key=api_key,
                )
                result["vlm_result"] = {
                    "quality_match": vlm_result.get("quality_match"),
                    "similarity_level": vlm_result.get("similarity_level"),
                    "confidence": vlm_result.get("confidence"),
                    "reason": vlm_result.get("reason"),
                }
            except Exception as e:
                result["vlm_result"] = {"error": str(e)}
        else:
            result["vlm_result"] = {"error": "image not found"}

    return result


# ============================================================
# 汇总报告
# ============================================================

def aggregate_exec_results(results):
    """汇总执行评测结果。"""
    n = len(results)
    if n == 0:
        return {}

    # 离线评测指标（同 run_eval.py）
    format_ok = sum(r["format_ok"] for r in results)
    type_match = sum(r["type_match"] for r in results)

    param_all_scores = defaultdict(list)
    for r in results:
        for k, v in r.get("param_scores", {}).items():
            param_all_scores[k].append(v)
    param_scores_avg = {k: round(sum(vs) / len(vs), 4) for k, vs in param_all_scores.items() if vs}

    acc_scores = [r["acc_score"] for r in results if r["format_ok"]]
    mean_acc_score = round(sum(acc_scores) / len(acc_scores), 4) if acc_scores else 0

    # 执行评测指标
    galfit_success = sum(1 for r in results if r["galfit_status"] == "success")
    galfit_rate = galfit_success / n if n > 0 else 0

    chi2_ratios = [r["chi2_ratio"] for r in results if r.get("chi2_ratio") is not None]
    mean_chi2_ratio = round(sum(chi2_ratios) / len(chi2_ratios), 4) if chi2_ratios else None

    bic_diffs = [r["bic_diff"] for r in results if r.get("bic_diff") is not None]
    mean_bic_diff = round(sum(bic_diffs) / len(bic_diffs), 2) if bic_diffs else None

    rl_rewards = [r["rl_reward"] for r in results if r.get("rl_reward") is not None]
    gt_rl_rewards = [r["gt_rl_reward"] for r in results if r.get("gt_rl_reward") is not None]
    mean_rl_reward = round(sum(rl_rewards) / len(rl_rewards), 4) if rl_rewards else None
    mean_gt_rl_reward = round(sum(gt_rl_rewards) / len(gt_rl_rewards), 4) if gt_rl_rewards else None

    # VLM 指标
    vlm_results = [r["vlm_result"] for r in results
                   if r.get("vlm_result") and "quality_match" in r.get("vlm_result", {})]
    vlm_match_rate = None
    if vlm_results:
        vlm_match_rate = round(
            sum(v["quality_match"] for v in vlm_results) / len(vlm_results), 4)

    # 按类型分组
    per_type = defaultdict(lambda: {
        "n": 0, "galfit_ok": 0, "type_match": 0,
        "chi2_ratios": [], "rl_rewards": [], "gt_rl_rewards": []})
    for r in results:
        gt = r["gt_label"]
        pt = per_type[gt]
        pt["n"] += 1
        if r["type_match"]:
            pt["type_match"] += 1
        if r["galfit_status"] == "success":
            pt["galfit_ok"] += 1
            if r.get("chi2_ratio") is not None:
                pt["chi2_ratios"].append(r["chi2_ratio"])
            if r.get("rl_reward") is not None:
                pt["rl_rewards"].append(r["rl_reward"])
            if r.get("gt_rl_reward") is not None:
                pt["gt_rl_rewards"].append(r["gt_rl_reward"])

    per_type_report = {}
    for label, pt in per_type.items():
        per_type_report[label] = {
            "n": pt["n"],
            "galfit_success_rate": round(pt["galfit_ok"] / pt["n"], 4) if pt["n"] > 0 else 0,
            "type_accuracy": round(pt["type_match"] / pt["n"], 4) if pt["n"] > 0 else 0,
            "mean_chi2_ratio": round(sum(pt["chi2_ratios"]) / len(pt["chi2_ratios"]), 4) if pt["chi2_ratios"] else None,
            "mean_rl_reward": round(sum(pt["rl_rewards"]) / len(pt["rl_rewards"]), 4) if pt["rl_rewards"] else None,
            "mean_gt_rl_reward": round(sum(pt["gt_rl_rewards"]) / len(pt["gt_rl_rewards"]), 4) if pt["gt_rl_rewards"] else None,
        }

    return {
        "n_samples": n,
        # 离线评测
        "format_rate": round(format_ok / n, 4),
        "type_accuracy": round(type_match / format_ok, 4) if format_ok > 0 else 0,
        "mean_acc_score": mean_acc_score,
        "param_scores": param_scores_avg,
        # 执行评测
        "galfit_success_rate": round(galfit_rate, 4),
        "n_galfit_success": galfit_success,
        "mean_chi2_ratio": mean_chi2_ratio,
        "mean_bic_diff": mean_bic_diff,
        "mean_rl_reward": mean_rl_reward,
        "mean_gt_rl_reward": mean_gt_rl_reward,
        # VLM
        "vlm_match_rate": vlm_match_rate,
        "n_vlm_evaluated": len(vlm_results),
        # 按类型
        "per_type": per_type_report,
    }


# ============================================================
# 主流程
# ============================================================

async def run_exec_evaluation(
    test_trajectories, model, processor, out_dir,
    max_steps=15, max_new_tokens=4096,
    use_vlm=False, vlm_model=None, api_key=None,
    skip_inference=False,
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

    # ---- 推理阶段 ----
    if not skip_inference:
        from eval.run_eval import run_inference_single

        predictions = []
        for i, (tree, parent, child) in enumerate(all_steps):
            gid = tree.get("galaxy_id", "unknown")
            nid = child.get("node_id", "unknown")

            system_prompt, user_text, image_path = build_step_prompt(
                parent, child, tree, max_steps)

            print(f"\n[{i + 1}/{len(all_steps)}] {gid}/{nid} (depth={child.get('depth')})")

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

        with open(pred_path, "w", encoding="utf-8") as f:
            for p in predictions:
                f.write(json.dumps(p, ensure_ascii=False) + "\n")
        print(f"\npredictions saved: {pred_path}")
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
            work_dir, use_vlm, vlm_model, api_key)
        elapsed = time.time() - t0

        result["elapsed"] = round(elapsed, 2)
        results.append(result)

        status = result["galfit_status"]
        chi2_r = result.get("chi2_ratio", "N/A")
        rl_r = result.get("rl_reward")
        rl_str = f"{rl_r:.3f}" if rl_r is not None else "N/A"
        print(f"  galfit={status}, chi2_ratio={chi2_r}, rl_reward={rl_str} ({elapsed:.1f}s)")

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
    print("  执行评测报告")
    print("=" * 60)
    print(f"  样本数:             {agg.get('n_samples', 0)}")
    print(f"  --- 离线评测 ---")
    print(f"  格式正确率:         {agg.get('format_rate', 0):.1%}")
    print(f"  动作类型准确率:     {agg.get('type_accuracy', 0):.1%}")
    print(f"  综合参数精度:       {agg.get('mean_acc_score', 0):.3f}")
    ps = agg.get("param_scores", {})
    for k, v in ps.items():
        print(f"    {k:>5}: {v:.3f}")
    print(f"  --- 执行评测 ---")
    print(f"  GALFIT 成功率:      {agg.get('galfit_success_rate', 0):.1%} ({agg.get('n_galfit_success', 0)}/{agg.get('n_samples', 0)})")
    cr = agg.get('mean_chi2_ratio')
    print(f"  平均 chi2 ratio:    {cr:.3f}" if cr else "  平均 chi2 ratio:    N/A")
    bd = agg.get('mean_bic_diff')
    print(f"  平均 BIC diff:      {bd:.1f}" if bd else "  平均 BIC diff:      N/A")
    mr = agg.get('mean_rl_reward')
    gr = agg.get('mean_gt_rl_reward')
    print(f"  平均 RL reward:     {mr:.3f} (model) / {gr:.3f} (GT)" if mr and gr else "  平均 RL reward:     N/A")
    vr = agg.get('vlm_match_rate')
    if vr is not None:
        print(f"  VLM 质量匹配率:    {vr:.1%} ({agg.get('n_vlm_evaluated', 0)} evaluated)")
    print(f"\n  按类型分组:")
    for label, metrics in agg.get("per_type", {}).items():
        cr = metrics.get('mean_chi2_ratio')
        cr_str = f"{cr:.2f}" if cr else "N/A"
        print(f"    {label}: n={metrics['n']}, galfit={metrics['galfit_success_rate']:.0%}, "
              f"type_acc={metrics['type_accuracy']:.0%}, chi2_ratio={cr_str}")
    print(f"\n  详细结果: {detail_path}")
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
    ap.add_argument("--use-vlm", action="store_true",
                    help="启用 VLM 残差比较（需要 API key）")
    ap.add_argument("--vlm-model", default="gemini-3.1-pro-preview")
    ap.add_argument("--api-key", default=None)
    args = ap.parse_args()

    # 加载测试轨迹
    from data_gen.extract_training_data import load_trajectories
    from data_gen.dataset_utils import _to_physical_id

    with open(args.test_galaxies, "r", encoding="utf-8") as f:
        obj = json.load(f)
    test_pids = set(obj["test_physical_ids"] if isinstance(obj, dict) else obj)

    all_trajs = load_trajectories(args.input_dir)
    test_trajs = [t for t in all_trajs
                  if _to_physical_id(t.get("galaxy_id", "")) in test_pids]
    print(f"测试轨迹: {len(test_trajs)} 条 (共 {len(all_trajs)} 条)")

    # 加载模型
    model, processor = None, None
    if not args.skip_inference:
        from eval.run_eval import load_model_and_processor
        model, processor = load_model_and_processor(
            args.model_path, args.adapter_path, use_4bit=not args.no_4bit)

    asyncio.run(run_exec_evaluation(
        test_trajs, model, processor, args.out_dir,
        max_steps=args.max_steps,
        max_new_tokens=args.max_new_tokens,
        use_vlm=args.use_vlm,
        vlm_model=args.vlm_model,
        api_key=args.api_key,
        skip_inference=args.skip_inference,
    ))


if __name__ == "__main__":
    main()
