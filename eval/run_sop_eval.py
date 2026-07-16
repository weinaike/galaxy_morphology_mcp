"""
轨迹级 SOP (Semi-Online Performance) 评测框架。

模型用自身输出驱动下一步，逐步与 GT 轨迹对比：
  1. 从 root 节点开始
  2. 每步：构造 prompt → 模型推理 → 解析 spec → 执行 GALFIT → 与 GT 对比
  3. type 不匹配时终止
  4. 计算 PG / TSR / Score

指标：
  - PG (Progress) = mean(s_i / t_i)，s_i = 完成步数，t_i = GT 总步数
  - TSR (Trajectory Success Rate) = mean(1[s_i == t_i])
  - Score = (PG + TSR) / 2
  - Step Reward = 每步 rl_reward 均值

依赖：GALFIT 二进制（仅 A6000）、模型推理（GPU）。

用法（在 A6000 上）：
    source /media/data/anaconda3/etc/profile.d/conda.sh && conda activate llama-factory
    cd /media/zhongling/wyh/GalDecomp_Gen
    python -m eval.run_sop_eval \\
        --input-dir output/E7_full__vlm_proposal_gemini-3.1-pro-preview_vlm_reward_gemini-3.1-pro-preview_hist \\
        --test-galaxies output/.../test_galaxies.json \\
        --model-path /media/zhongling/huggingface/Qwen2.5-VL-7B-Instruct \\
        --adapter-path /media/zhongling/wyh/LLaMA-Factory/saves/qwen2_5vl-7b-galaxy-qlora \\
        --out-dir eval/sop_results
"""

import argparse
import asyncio
import json
import os
import time
from collections import defaultdict

from eval.evaluate_action import (
    evaluate_galfit_action,
    normalize_coarse_label,
)


def extract_accepted_chain(tree):
    """
    从轨迹树中提取 accepted 主链路：root → ... → best leaf。
    返回有序节点列表（不含 root）。
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

    chain = []
    current = root
    while True:
        accepted_children = [
            c for c in children_map.get(current["node_id"], [])
            if c.get("is_accepted") and c.get("status") in (None, "success")
        ]
        if not accepted_children:
            break
        best = min(accepted_children, key=lambda c: c.get("metrics", {}).get("chi2_nu", 9999))
        chain.append(best)
        current = best

    return chain


async def run_sop_single_trajectory(
    tree,
    model,
    processor,
    out_dir,
    max_steps=15,
):
    """
    对单条测试轨迹执行 SOP 评测。

    TODO: 实际实现需要：
      - GalfitEnv 来执行 GALFIT
      - build_proposal_prompt 构造输入
      - compute_rl_reward 计算每步 reward
    """
    galaxy_id = tree.get("galaxy_id", "unknown")
    gt_chain = extract_accepted_chain(tree)
    t_i = len(gt_chain)

    if t_i == 0:
        return {
            "galaxy_id": galaxy_id,
            "gt_steps": 0,
            "completed_steps": 0,
            "progress": 0.0,
            "success": False,
            "steps": [],
            "error": "no accepted chain in GT trajectory",
        }

    # --- STUB: 以下是框架，实际推理和 GALFIT 执行待填充 ---
    s_i = 0
    steps = []

    for step_idx, gt_node in enumerate(gt_chain):
        gt_action = gt_node.get("action_from_parent", {})
        gt_spec = gt_action.get("spec", {})
        gt_label = gt_action.get("coarse_label", "unknown")

        # TODO: 构造 prompt（需要当前状态的 residual image + summary + history）
        # TODO: 模型推理 → pred_text
        # TODO: 解析 pred spec
        # TODO: 对比 type
        # TODO: 如果 type match → 执行 GALFIT → 更新状态
        # TODO: 如果 type mismatch → 终止
        # TODO: compute_rl_reward()

        step_result = {
            "step": step_idx + 1,
            "gt_label": normalize_coarse_label(gt_label),
            "pred_label": "unknown",
            "type_match": False,
            "rl_reward": None,
            "status": "not_implemented",
        }
        steps.append(step_result)

        # STUB: 假设全部失败（待实现后移除）
        break

    progress = s_i / t_i if t_i > 0 else 0.0

    return {
        "galaxy_id": galaxy_id,
        "gt_steps": t_i,
        "completed_steps": s_i,
        "progress": progress,
        "success": s_i == t_i,
        "steps": steps,
    }


def compute_sop_metrics(trajectory_results):
    """计算 SOP 汇总指标。"""
    n = len(trajectory_results)
    if n == 0:
        return {}

    progresses = [r["progress"] for r in trajectory_results]
    successes = [1.0 if r["success"] else 0.0 for r in trajectory_results]

    pg = sum(progresses) / n
    tsr = sum(successes) / n
    score = (pg + tsr) / 2.0

    return {
        "n_trajectories": n,
        "PG": round(pg, 4),
        "TSR": round(tsr, 4),
        "Score": round(score, 4),
        "avg_gt_steps": round(sum(r["gt_steps"] for r in trajectory_results) / n, 2),
        "avg_completed_steps": round(sum(r["completed_steps"] for r in trajectory_results) / n, 2),
    }


async def run_sop_evaluation(
    test_trajectories,
    model,
    processor,
    out_dir,
    max_steps=15,
):
    """
    对所有测试轨迹执行 SOP 评测。

    Args:
        test_trajectories: GT 轨迹列表
        model: 加载好的模型
        processor: tokenizer/processor
        out_dir: 输出目录
    """
    os.makedirs(out_dir, exist_ok=True)

    results = []
    for i, tree in enumerate(test_trajectories):
        gid = tree.get("galaxy_id", "unknown")
        print(f"\n[{i + 1}/{len(test_trajectories)}] SOP: {gid}")

        t0 = time.time()
        result = await run_sop_single_trajectory(tree, model, processor, out_dir, max_steps)
        elapsed = time.time() - t0

        result["elapsed"] = round(elapsed, 2)
        results.append(result)

        print(f"  完成 {result['completed_steps']}/{result['gt_steps']} 步, "
              f"progress={result['progress']:.2f}, success={result['success']} ({elapsed:.1f}s)")

    sop_metrics = compute_sop_metrics(results)

    # 保存
    detail_path = os.path.join(out_dir, "sop_details.json")
    with open(detail_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    report_path = os.path.join(out_dir, "sop_report.json")
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(sop_metrics, f, ensure_ascii=False, indent=2)

    print("\n" + "=" * 60)
    print("  SOP 评测报告")
    print("=" * 60)
    print(f"  轨迹数:     {sop_metrics.get('n_trajectories', 0)}")
    print(f"  PG:         {sop_metrics.get('PG', 0):.2%}")
    print(f"  TSR:        {sop_metrics.get('TSR', 0):.2%}")
    print(f"  Score:      {sop_metrics.get('Score', 0):.4f}")
    print(f"  平均 GT 步数:    {sop_metrics.get('avg_gt_steps', 0):.1f}")
    print(f"  平均完成步数:    {sop_metrics.get('avg_completed_steps', 0):.1f}")
    print(f"\n  详细结果: {detail_path}")
    print(f"  汇总报告: {report_path}")

    return sop_metrics


def main():
    ap = argparse.ArgumentParser(description="SOP 轨迹级评测")
    ap.add_argument("--input-dir", required=True, help="trajectory 输出目录")
    ap.add_argument("--test-galaxies", required=True, help="test_galaxies.json")
    ap.add_argument("--model-path", required=True)
    ap.add_argument("--adapter-path", required=True)
    ap.add_argument("--out-dir", default="eval/sop_results")
    ap.add_argument("--max-steps", type=int, default=15)
    args = ap.parse_args()

    from data_gen.extract_training_data import load_trajectories
    from data_gen.dataset_utils import _to_physical_id

    with open(args.test_galaxies, "r", encoding="utf-8") as f:
        obj = json.load(f)
    test_pids = set(obj["test_physical_ids"] if isinstance(obj, dict) else obj)

    all_trajs = load_trajectories(args.input_dir)
    test_trajs = [t for t in all_trajs if _to_physical_id(t.get("galaxy_id", "")) in test_pids]
    print(f"测试轨迹: {len(test_trajs)} 条 (共 {len(all_trajs)} 条)")

    from eval.run_eval import load_model_and_processor
    model, processor = load_model_and_processor(args.model_path, args.adapter_path)

    asyncio.run(run_sop_evaluation(test_trajs, model, processor, args.out_dir, args.max_steps))


if __name__ == "__main__":
    main()
