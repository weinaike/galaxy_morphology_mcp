"""
验证 rule-based reward 与 VLM reward 的对齐程度。

用 pipeline 已生成的 GT 轨迹数据（每步都有 VLM reward 记录），
回溯计算 rule-based reward，与 VLM 判断对比。

流程：
  1. 加载所有 trajectory，提取有 VLM reward 的 (parent, child) 步骤对
  2. 按 physical_id 划分 val/test
  3. 对每步回溯计算 rule-based reward
  4. val 集：ROC 曲线找最优 threshold + 分维度分析 + 不一致样本导出
  5. test 集：锁参评测，报最终对齐指标

用法（在 A6000 上）：
    source /media/data/anaconda3/etc/profile.d/conda.sh && conda activate llama-factory
    cd /media/zhongling/wyh/GalDecomp_Gen

    python -m eval.validate_reward_alignment \\
        --input-dir output/E7_full__vlm_proposal_gemini-3.1-pro-preview_vlm_reward_gemini-3.1-pro-preview_hist \\
        --out-dir eval/reward_alignment \\
        --val-ratio 0.7

    # 指定 threshold（跳过 ROC 自动选择）
    python -m eval.validate_reward_alignment \\
        --input-dir ... --out-dir eval/reward_alignment \\
        --val-ratio 0.7 --threshold 0.5
"""

import argparse
import json
import math
import os
import random
from collections import defaultdict

import numpy as np

from eval.reward_for_rl import (
    check_param_bounds,
    compute_chi2_gain,
    compute_bic_gain,
    compute_rl_reward,
)


# ============================================================
# 数据加载
# ============================================================

def extract_step_pairs(trajectories):
    """
    从 trajectory 列表中提取所有 (parent, child) 对，
    仅保留 child 有 VLM reward 记录且 status=success 的步骤。
    """
    pairs = []
    for tree in trajectories:
        galaxy_id = tree.get("galaxy_id", "unknown")
        node_map = {n["node_id"]: n for n in tree.get("nodes", [])}

        for node in tree.get("nodes", []):
            parent_id = node.get("parent_id")
            if not parent_id:
                continue

            if node.get("status") not in (None, "success"):
                continue

            reward_detail = node.get("reward_detail", {})
            vlm_detail = reward_detail.get("vlm_detail")
            if vlm_detail is None:
                continue
            if "improvement" not in vlm_detail:
                continue

            parent = node_map.get(parent_id)
            if parent is None:
                continue

            pairs.append({
                "galaxy_id": galaxy_id,
                "node_id": node["node_id"],
                "parent_id": parent_id,
                "depth": node.get("depth", -1),
                "coarse_label": (node.get("action_from_parent") or {}).get("coarse_label", "unknown"),
                "action_spec": (node.get("action_from_parent") or {}).get("spec", {}),
                "parent_metrics": parent.get("metrics", {}),
                "child_metrics": node.get("metrics", {}),
                "vlm_detail": vlm_detail,
                "vlm_improvement": int(vlm_detail.get("improvement", 0)),
                "is_accepted": node.get("is_accepted", False),
            })

    return pairs


def split_val_test(pairs, val_ratio=0.7, seed=42):
    """按 physical_id 划分 val/test，确保同一星系的所有步骤在同一边。"""
    from data_gen.dataset_utils import _to_physical_id

    pid_to_pairs = defaultdict(list)
    for p in pairs:
        pid = _to_physical_id(p["galaxy_id"])
        p["physical_id"] = pid
        pid_to_pairs[pid].append(p)

    pids = sorted(pid_to_pairs.keys())
    rng = random.Random(seed)
    rng.shuffle(pids)

    n_val = int(len(pids) * val_ratio)
    val_pids = set(pids[:n_val])
    test_pids = set(pids[n_val:])

    val_pairs = [p for pid in val_pids for p in pid_to_pairs[pid]]
    test_pairs = [p for pid in test_pids for p in pid_to_pairs[pid]]

    return val_pairs, test_pairs, sorted(val_pids), sorted(test_pids)


# ============================================================
# Rule-based reward 回溯计算
# ============================================================

def compute_rule_reward_for_pair(pair):
    """对一步回溯计算 rule-based reward。"""
    rl_result = compute_rl_reward(
        old_metrics=pair["parent_metrics"],
        new_metrics=pair["child_metrics"],
        action_spec=pair["action_spec"],
    )

    bounds_ok, violations = check_param_bounds(pair["action_spec"])
    r_chi2 = compute_chi2_gain(pair["parent_metrics"], pair["child_metrics"])
    r_bic = compute_bic_gain(pair["parent_metrics"], pair["child_metrics"])

    chi2_direction = "decreased" if r_chi2 > 0.001 else ("increased" if r_chi2 < -0.001 else "unchanged")
    bic_direction = "decreased" if r_bic > 0.05 else ("increased" if r_bic < -0.05 else "unchanged")

    return {
        "reward": rl_result["reward"],
        "bounds_ok": rl_result["bounds_ok"],
        "chi2_vetoed": rl_result.get("chi2_vetoed", False),
        "r_chi2": rl_result["r_chi2"],
        "r_bic": rl_result["r_bic"],
        "r_noise": rl_result["r_noise"],
        "chi2_direction": chi2_direction,
        "bic_direction": bic_direction,
    }


# ============================================================
# 对齐分析
# ============================================================

def compute_alignment_metrics(pairs, threshold=0.0):
    """
    计算 rule-based vs VLM 的对齐指标。
    rule-based 二值化：reward > threshold → 1, else → 0。
    """
    tp = fp = tn = fn = 0
    for p in pairs:
        rule_pred = 1 if p["rule_reward"] > threshold else 0
        vlm_label = p["vlm_improvement"]

        if rule_pred == 1 and vlm_label == 1:
            tp += 1
        elif rule_pred == 1 and vlm_label == 0:
            fp += 1
        elif rule_pred == 0 and vlm_label == 0:
            tn += 1
        else:
            fn += 1

    n = len(pairs)
    accuracy = (tp + tn) / n if n > 0 else 0
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0

    return {
        "n": n,
        "threshold": threshold,
        "accuracy": round(accuracy, 4),
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "tp": tp, "fp": fp, "tn": tn, "fn": fn,
        "vlm_positive_rate": round((tp + fn) / n, 4) if n > 0 else 0,
        "rule_positive_rate": round((tp + fp) / n, 4) if n > 0 else 0,
    }


def find_optimal_threshold(pairs, n_candidates=200):
    """用 ROC 曲线找最优 threshold（最大化 accuracy）。"""
    rewards = sorted(set(p["rule_reward"] for p in pairs))

    if len(rewards) <= 1:
        return 0.0, []

    lo, hi = min(rewards), max(rewards)
    candidates = np.linspace(lo - 0.1, hi + 0.1, n_candidates)

    roc_points = []
    best_acc = -1
    best_thr = 0.0

    for thr in candidates:
        m = compute_alignment_metrics(pairs, threshold=thr)
        fpr = m["fp"] / (m["fp"] + m["tn"]) if (m["fp"] + m["tn"]) > 0 else 0
        tpr = m["tp"] / (m["tp"] + m["fn"]) if (m["tp"] + m["fn"]) > 0 else 0
        roc_points.append({"threshold": round(float(thr), 4), "fpr": round(fpr, 4),
                           "tpr": round(tpr, 4), "accuracy": m["accuracy"], "f1": m["f1"]})
        if m["accuracy"] > best_acc:
            best_acc = m["accuracy"]
            best_thr = float(thr)

    # AUC（梯形法）
    roc_sorted = sorted(roc_points, key=lambda x: x["fpr"])
    auc = 0.0
    for i in range(1, len(roc_sorted)):
        dx = roc_sorted[i]["fpr"] - roc_sorted[i - 1]["fpr"]
        dy = (roc_sorted[i]["tpr"] + roc_sorted[i - 1]["tpr"]) / 2
        auc += dx * dy

    return best_thr, roc_points, round(auc, 4)


def compute_dimension_alignment(pairs):
    """分维度对齐分析：逐个组件跟 VLM 的对应维度比较。"""
    results = {}

    # bounds_ok vs VLM param_plausible
    n_both = n_agree = 0
    for p in pairs:
        vlm_pp = p["vlm_detail"].get("param_plausible")
        if vlm_pp is None:
            continue
        n_both += 1
        if p["rule_bounds_ok"] == vlm_pp:
            n_agree += 1
    results["bounds_vs_param_plausible"] = {
        "n": n_both,
        "agreement": round(n_agree / n_both, 4) if n_both > 0 else None,
    }

    # chi2 方向 vs VLM chisq_trend
    n_both = n_agree = 0
    vlm_trend_dist = defaultdict(int)
    rule_trend_dist = defaultdict(int)
    crosstab = defaultdict(int)
    for p in pairs:
        vlm_trend = p["vlm_detail"].get("chisq_nu_trend")
        vlm_trend_dist[str(vlm_trend)] += 1
        rule_dir = p["rule_chi2_direction"]
        rule_trend_dist[rule_dir] += 1
        if vlm_trend not in ("decreased", "increased", "unchanged"):
            continue
        n_both += 1
        crosstab[f"rule={rule_dir},vlm={vlm_trend}"] += 1
        if rule_dir == vlm_trend:
            n_agree += 1
    results["chi2_direction_vs_vlm_chisq_nu_trend"] = {
        "n": n_both,
        "agreement": round(n_agree / n_both, 4) if n_both > 0 else None,
        "vlm_trend_distribution": dict(vlm_trend_dist),
        "rule_trend_distribution": dict(rule_trend_dist),
        "crosstab": dict(crosstab),
    }

    # metric_consistent
    n_both = n_agree = 0
    for p in pairs:
        vlm_mc = p["vlm_detail"].get("metric_consistent")
        if vlm_mc is None:
            continue
        rule_metric_ok = not p["rule_chi2_vetoed"] and p["rule_r_bic"] >= -1.0
        n_both += 1
        if rule_metric_ok == vlm_mc:
            n_agree += 1
    results["metric_consistency"] = {
        "n": n_both,
        "agreement": round(n_agree / n_both, 4) if n_both > 0 else None,
    }

    return results


def compute_per_type_alignment(pairs, threshold):
    """按动作类型分组的对齐分析。"""
    from eval.evaluate_action import normalize_coarse_label

    type_groups = defaultdict(list)
    for p in pairs:
        label = normalize_coarse_label(p["coarse_label"])
        type_groups[label].append(p)

    results = {}
    for label, group in sorted(type_groups.items()):
        m = compute_alignment_metrics(group, threshold)
        results[label] = m

    return results


def collect_disagreements(pairs, threshold):
    """收集 rule-based 和 VLM 判断不一致的样本。"""
    disagreements = []
    for p in pairs:
        rule_pred = 1 if p["rule_reward"] > threshold else 0
        vlm_label = p["vlm_improvement"]
        if rule_pred == vlm_label:
            continue

        disagreements.append({
            "galaxy_id": p["galaxy_id"],
            "node_id": p["node_id"],
            "depth": p["depth"],
            "coarse_label": p["coarse_label"],
            "rule_pred": rule_pred,
            "vlm_label": vlm_label,
            "rule_reward": round(p["rule_reward"], 4),
            "r_chi2": round(p["rule_r_chi2"], 4),
            "r_bic": round(p["rule_r_bic"], 4),
            "bounds_ok": p["rule_bounds_ok"],
            "chi2_vetoed": p["rule_chi2_vetoed"],
            "parent_chi2_nu": p["parent_metrics"].get("chi2_nu"),
            "child_chi2_nu": p["child_metrics"].get("chi2_nu"),
            "parent_bic": p["parent_metrics"].get("bic"),
            "child_bic": p["child_metrics"].get("bic"),
            "vlm_reason": p["vlm_detail"].get("reason", ""),
            "vlm_residual_improved": p["vlm_detail"].get("residual_improved"),
            "vlm_param_plausible": p["vlm_detail"].get("param_plausible"),
            "vlm_metric_consistent": p["vlm_detail"].get("metric_consistent"),
            "vlm_improvement_source": p["vlm_detail"].get("improvement_source"),
        })

    return disagreements


# ============================================================
# 可视化
# ============================================================

def plot_roc_curve(roc_points, best_threshold, auc, out_path):
    """绘制 ROC 曲线。"""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fprs = [r["fpr"] for r in roc_points]
    tprs = [r["tpr"] for r in roc_points]

    fig, ax = plt.subplots(figsize=(6, 6))
    ax.plot(fprs, tprs, "b-", linewidth=2, label=f"ROC (AUC={auc:.3f})")
    ax.plot([0, 1], [0, 1], "k--", alpha=0.3)

    best_pt = min(roc_points, key=lambda r: abs(r["threshold"] - best_threshold))
    ax.plot(best_pt["fpr"], best_pt["tpr"], "ro", markersize=10,
            label=f"Best thr={best_threshold:.3f}")

    ax.set_xlabel("False Positive Rate")
    ax.set_ylabel("True Positive Rate")
    ax.set_title("Rule-based vs VLM Reward: ROC Curve")
    ax.legend()
    ax.set_xlim(-0.02, 1.02)
    ax.set_ylim(-0.02, 1.02)
    fig.tight_layout()
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)


def plot_confusion_matrix(metrics, title, out_path):
    """绘制混淆矩阵。"""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    matrix = np.array([
        [metrics["tn"], metrics["fp"]],
        [metrics["fn"], metrics["tp"]],
    ])
    labels = ["VLM=0\n(no improve)", "VLM=1\n(improve)"]
    pred_labels = ["Rule=0", "Rule=1"]

    fig, ax = plt.subplots(figsize=(5, 4))
    im = ax.imshow(matrix, cmap="Blues", aspect="auto")
    ax.set_xticks([0, 1])
    ax.set_yticks([0, 1])
    ax.set_xticklabels(pred_labels)
    ax.set_yticklabels(labels)
    ax.set_xlabel("Rule-based Prediction")
    ax.set_ylabel("VLM Ground Truth")
    ax.set_title(title)

    for i in range(2):
        for j in range(2):
            val = matrix[i, j]
            color = "white" if val > matrix.max() * 0.5 else "black"
            ax.text(j, i, str(val), ha="center", va="center", color=color, fontsize=14)

    fig.colorbar(im, ax=ax, shrink=0.8)
    fig.tight_layout()
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)


def plot_reward_distribution(pairs, threshold, out_path):
    """绘制 rule-based reward 分布，按 VLM 标签着色。"""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    vlm_pos = [p["rule_reward"] for p in pairs if p["vlm_improvement"] == 1]
    vlm_neg = [p["rule_reward"] for p in pairs if p["vlm_improvement"] == 0]

    fig, ax = plt.subplots(figsize=(8, 5))
    bins = np.linspace(
        min(min(vlm_pos, default=0), min(vlm_neg, default=0)) - 0.5,
        max(max(vlm_pos, default=0), max(vlm_neg, default=0)) + 0.5,
        50)
    ax.hist(vlm_pos, bins=bins, alpha=0.6, color="#4CAF50", label=f"VLM=1 (n={len(vlm_pos)})")
    ax.hist(vlm_neg, bins=bins, alpha=0.6, color="#F44336", label=f"VLM=0 (n={len(vlm_neg)})")
    ax.axvline(threshold, color="black", linestyle="--", linewidth=2, label=f"threshold={threshold:.3f}")
    ax.set_xlabel("Rule-based Reward")
    ax.set_ylabel("Count")
    ax.set_title("Reward Distribution by VLM Label")
    ax.legend()
    fig.tight_layout()
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)


# ============================================================
# 主流程
# ============================================================

def run_alignment_validation(pairs, out_dir, val_ratio=0.7, threshold=None, skip_test=False):
    """完整的对齐验证流程。"""
    os.makedirs(out_dir, exist_ok=True)

    # 1. 计算 rule-based reward
    print("计算 rule-based reward...")
    for p in pairs:
        rule = compute_rule_reward_for_pair(p)
        p["rule_reward"] = rule["reward"]
        p["rule_bounds_ok"] = rule["bounds_ok"]
        p["rule_chi2_vetoed"] = rule["chi2_vetoed"]
        p["rule_r_chi2"] = rule["r_chi2"]
        p["rule_r_bic"] = rule["r_bic"]
        p["rule_r_noise"] = rule["r_noise"]
        p["rule_chi2_direction"] = rule["chi2_direction"]
        p["rule_bic_direction"] = rule["bic_direction"]

    # 2. 划分 val/test
    val_pairs, test_pairs, val_pids, test_pids = split_val_test(pairs, val_ratio)
    print(f"Val: {len(val_pairs)} steps from {len(val_pids)} galaxies")
    print(f"Test: {len(test_pairs)} steps from {len(test_pids)} galaxies")

    split_info = {
        "val_n_galaxies": len(val_pids),
        "val_n_steps": len(val_pairs),
        "test_n_galaxies": len(test_pids),
        "test_n_steps": len(test_pairs),
        "val_pids": val_pids,
        "test_pids": test_pids,
    }
    with open(os.path.join(out_dir, "val_test_split.json"), "w", encoding="utf-8") as f:
        json.dump(split_info, f, ensure_ascii=False, indent=2)

    # 3. Val 集分析
    print("\n" + "=" * 60)
    print("  Val 集对齐分析")
    print("=" * 60)

    # ROC + 最优 threshold
    if threshold is None:
        best_thr, roc_points, auc = find_optimal_threshold(val_pairs)
        print(f"  ROC AUC: {auc}")
        print(f"  最优 threshold: {best_thr:.4f}")
        plot_roc_curve(roc_points, best_thr, auc, os.path.join(out_dir, "val_roc_curve.png"))
    else:
        best_thr = threshold
        auc = None
        print(f"  使用指定 threshold: {best_thr}")

    val_metrics = compute_alignment_metrics(val_pairs, best_thr)
    print(f"  Accuracy:  {val_metrics['accuracy']:.1%}")
    print(f"  Precision: {val_metrics['precision']:.1%}")
    print(f"  Recall:    {val_metrics['recall']:.1%}")
    print(f"  F1:        {val_metrics['f1']:.1%}")
    print(f"  TP={val_metrics['tp']} FP={val_metrics['fp']} TN={val_metrics['tn']} FN={val_metrics['fn']}")

    # 分维度
    dim_alignment = compute_dimension_alignment(val_pairs)
    print(f"\n  分维度对齐:")
    for dim, info in dim_alignment.items():
        agr = info.get("agreement")
        agr_str = f"{agr:.1%}" if agr is not None else "N/A"
        print(f"    {dim}: {agr_str} (n={info['n']})")

    # 按类型
    per_type = compute_per_type_alignment(val_pairs, best_thr)
    print(f"\n  按动作类型:")
    for label, m in per_type.items():
        print(f"    {label}: acc={m['accuracy']:.1%}, f1={m['f1']:.1%}, n={m['n']}")

    # Reward 分布统计
    rewards = [p["rule_reward"] for p in val_pairs]
    vlm_pos_rewards = [p["rule_reward"] for p in val_pairs if p["vlm_improvement"] == 1]
    vlm_neg_rewards = [p["rule_reward"] for p in val_pairs if p["vlm_improvement"] == 0]

    def _dist_summary(arr, label):
        a = np.array(arr)
        pcts = np.percentile(a, [1, 5, 25, 50, 75, 95, 99])
        print(f"    {label} (n={len(a)}):")
        print(f"      mean={a.mean():.4f}, std={a.std():.4f}, min={a.min():.4f}, max={a.max():.4f}")
        print(f"      p1={pcts[0]:.4f}, p5={pcts[1]:.4f}, p25={pcts[2]:.4f}, "
              f"median={pcts[3]:.4f}, p75={pcts[4]:.4f}, p95={pcts[5]:.4f}, p99={pcts[6]:.4f}")

    print(f"\n  Reward 分布:")
    _dist_summary(rewards, "全体")
    if vlm_pos_rewards:
        _dist_summary(vlm_pos_rewards, "VLM=1")
    if vlm_neg_rewards:
        _dist_summary(vlm_neg_rewards, "VLM=0")

    # 各分量统计
    r_chi2s = [p["rule_r_chi2"] for p in val_pairs]
    r_bics = [p["rule_r_bic"] for p in val_pairs]
    print(f"\n  分量分布:")
    _dist_summary(r_chi2s, "r_chi2")
    _dist_summary(r_bics, "r_bic")

    # 边界/否决统计
    n_bounds_fail = sum(1 for p in val_pairs if not p["rule_bounds_ok"])
    n_chi2_veto = sum(1 for p in val_pairs if p["rule_chi2_vetoed"])
    n_zero_reward = sum(1 for p in val_pairs if p["rule_reward"] == 0.0)
    print(f"\n  门控统计:")
    print(f"    边界违规 (R=0): {n_bounds_fail} ({n_bounds_fail/len(val_pairs):.1%})")
    print(f"    chi2 否决 (R=0): {n_chi2_veto} ({n_chi2_veto/len(val_pairs):.1%})")
    print(f"    总 reward=0: {n_zero_reward} ({n_zero_reward/len(val_pairs):.1%})")

    # 不一致样本
    disagreements = collect_disagreements(val_pairs, best_thr)
    print(f"\n  不一致样本: {len(disagreements)}")
    rule_loose = sum(1 for d in disagreements if d["rule_pred"] == 1 and d["vlm_label"] == 0)
    rule_strict = sum(1 for d in disagreements if d["rule_pred"] == 0 and d["vlm_label"] == 1)
    print(f"    Rule 过于宽松 (rule=1, vlm=0): {rule_loose}")
    print(f"    Rule 过于严格 (rule=0, vlm=1): {rule_strict}")

    # 可视化
    plot_confusion_matrix(val_metrics, "Val Set: Rule vs VLM",
                          os.path.join(out_dir, "val_confusion_matrix.png"))
    plot_reward_distribution(val_pairs, best_thr,
                             os.path.join(out_dir, "val_reward_distribution.png"))

    # 保存 val 报告
    val_report = {
        "threshold": best_thr,
        "auc": auc,
        "metrics": val_metrics,
        "dimension_alignment": dim_alignment,
        "per_type": per_type,
        "n_disagreements": len(disagreements),
        "rule_too_loose": rule_loose,
        "rule_too_strict": rule_strict,
        "reward_stats": {
            "mean": round(float(np.mean(rewards)), 4),
            "std": round(float(np.std(rewards)), 4),
            "r_chi2_mean": round(float(np.mean(r_chi2s)), 4),
            "r_chi2_std": round(float(np.std(r_chi2s)), 4),
            "r_bic_mean": round(float(np.mean(r_bics)), 4),
            "r_bic_std": round(float(np.std(r_bics)), 4),
        },
    }
    with open(os.path.join(out_dir, "val_alignment_report.json"), "w", encoding="utf-8") as f:
        json.dump(val_report, f, ensure_ascii=False, indent=2)

    with open(os.path.join(out_dir, "val_disagreements.jsonl"), "w", encoding="utf-8") as f:
        for d in disagreements:
            f.write(json.dumps(d, ensure_ascii=False, default=str) + "\n")

    # 4. Test 集评测（锁参）
    test_report = None
    if not skip_test:
        print("\n" + "=" * 60)
        print("  Test 集评测（锁参）")
        print("=" * 60)

        test_metrics = compute_alignment_metrics(test_pairs, best_thr)
        print(f"  Threshold: {best_thr:.4f} (from val)")
        print(f"  Accuracy:  {test_metrics['accuracy']:.1%}")
        print(f"  Precision: {test_metrics['precision']:.1%}")
        print(f"  Recall:    {test_metrics['recall']:.1%}")
        print(f"  F1:        {test_metrics['f1']:.1%}")
        print(f"  TP={test_metrics['tp']} FP={test_metrics['fp']} TN={test_metrics['tn']} FN={test_metrics['fn']}")

        test_per_type = compute_per_type_alignment(test_pairs, best_thr)
        print(f"\n  按动作类型:")
        for label, m in test_per_type.items():
            print(f"    {label}: acc={m['accuracy']:.1%}, f1={m['f1']:.1%}, n={m['n']}")

        test_disagreements = collect_disagreements(test_pairs, best_thr)
        print(f"\n  不一致样本: {len(test_disagreements)}")

        plot_confusion_matrix(test_metrics, "Test Set: Rule vs VLM",
                              os.path.join(out_dir, "test_confusion_matrix.png"))
        plot_reward_distribution(test_pairs, best_thr,
                                 os.path.join(out_dir, "test_reward_distribution.png"))

        test_report = {
            "threshold": best_thr,
            "metrics": test_metrics,
            "per_type": test_per_type,
            "n_disagreements": len(test_disagreements),
        }
        with open(os.path.join(out_dir, "test_alignment_report.json"), "w", encoding="utf-8") as f:
            json.dump(test_report, f, ensure_ascii=False, indent=2)

        with open(os.path.join(out_dir, "test_disagreements.jsonl"), "w", encoding="utf-8") as f:
            for d in test_disagreements:
                f.write(json.dumps(d, ensure_ascii=False, default=str) + "\n")
    else:
        print("\n  (--skip-test: 跳过 test 集评测)")

    # 总结
    print("\n" + "=" * 60)
    print("  总结")
    print("=" * 60)
    print(f"  Val  accuracy: {val_metrics['accuracy']:.1%} (n={val_metrics['n']})")
    if test_report:
        print(f"  Test accuracy: {test_report['metrics']['accuracy']:.1%} (n={test_report['metrics']['n']})")
    if auc:
        print(f"  ROC AUC: {auc}")
    print(f"  Threshold: {best_thr:.4f}")
    print(f"\n  输出目录: {out_dir}")

    return val_report, test_report


def main():
    ap = argparse.ArgumentParser(description="验证 rule-based reward 与 VLM reward 对齐")
    ap.add_argument("--input-dir", required=True, help="trajectory 输出目录")
    ap.add_argument("--out-dir", default="eval/reward_alignment")
    ap.add_argument("--val-ratio", type=float, default=0.7)
    ap.add_argument("--threshold", default=None,
                    help="二值化 threshold（默认 auto: ROC 最优），可指定数值")
    ap.add_argument("--skip-test", action="store_true",
                    help="只跑 val 集，不跑 test（调参阶段用）")
    args = ap.parse_args()

    threshold = None
    if args.threshold is not None and args.threshold != "auto":
        threshold = float(args.threshold)

    import glob as _glob
    import time as _time

    print(f"正在扫描 trajectory 目录: {args.input_dir}", flush=True)
    t0 = _time.time()
    pattern = os.path.join(args.input_dir, "**", "*_trajectory.json")
    traj_files = _glob.glob(pattern, recursive=True)
    print(f"  找到 {len(traj_files)} 个 trajectory 文件 ({_time.time()-t0:.1f}s)", flush=True)

    if len(traj_files) == 0:
        print("  ERROR: 没有找到任何 *_trajectory.json 文件！检查路径是否正确。", flush=True)
        return

    import json as _json
    all_trajs = []
    for i, f in enumerate(sorted(traj_files)):
        try:
            with open(f, "r", encoding="utf-8") as fh:
                data = _json.load(fh)
            data["_source_file"] = f
            all_trajs.append(data)
        except Exception as e:
            print(f"  [WARN] 跳过: {f}: {e}", flush=True)
        if (i + 1) % 20 == 0 or (i + 1) == len(traj_files):
            print(f"  加载进度: {i+1}/{len(traj_files)}", flush=True)

    print(f"加载 {len(all_trajs)} 条 trajectory ({_time.time()-t0:.1f}s)", flush=True)

    pairs = extract_step_pairs(all_trajs)
    print(f"提取 {len(pairs)} 个有 VLM reward 的步骤", flush=True)

    if len(pairs) == 0:
        print("  ERROR: 没有提取到任何有 VLM reward 的步骤！", flush=True)
        print("  检查 trajectory 数据中是否有 reward_detail.vlm_detail.improvement 字段", flush=True)
        return

    vlm_pos = sum(1 for p in pairs if p["vlm_improvement"] == 1)
    print(f"VLM improvement=1: {vlm_pos} ({vlm_pos / len(pairs):.1%})", flush=True)
    print(f"VLM improvement=0: {len(pairs) - vlm_pos} ({(len(pairs) - vlm_pos) / len(pairs):.1%})", flush=True)

    run_alignment_validation(pairs, args.out_dir, args.val_ratio, threshold,
                             skip_test=args.skip_test)


if __name__ == "__main__":
    main()
