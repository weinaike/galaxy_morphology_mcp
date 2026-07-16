"""
评测可视化：从 eval_details 生成图表。

生成：
  1. 参数误差 box plot（按动作类型着色）
  2. 动作类型混淆矩阵 heatmap
  3. 按 depth 的指标折线图

用法：
    python -m eval.visualize --details eval/eval_results/eval_details.jsonl --out-dir eval/eval_results/plots
    或由 run_eval.py --visualize 自动调用
"""

import argparse
import json
import os
from collections import defaultdict

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


TYPE_COLORS = {
    "add": "#4CAF50",
    "modify": "#2196F3",
    "delete": "#F44336",
    "unknown": "#9E9E9E",
}

PARAM_ORDER = ["mag", "re", "n", "q", "pa"]


def _collect_param_diffs_by_type(results):
    """收集每个参数按 type 分组的 diff 列表。"""
    data = {p: defaultdict(list) for p in PARAM_ORDER}
    for r in results:
        if not r.get("format_ok"):
            continue
        gt_label = r.get("gt_label", "unknown")
        for param, diffs in r.get("param_diffs", {}).items():
            if param in data:
                data[param][gt_label].extend(diffs)
    return data


def plot_param_boxplot(results, out_path):
    """参数误差 box plot，按动作类型着色。"""
    data = _collect_param_diffs_by_type(results)
    types = sorted({r.get("gt_label", "unknown") for r in results if r.get("format_ok")})
    if not types:
        return

    fig, axes = plt.subplots(1, len(PARAM_ORDER), figsize=(3 * len(PARAM_ORDER), 5), sharey=False)
    if len(PARAM_ORDER) == 1:
        axes = [axes]

    for ax, param in zip(axes, PARAM_ORDER):
        box_data = []
        colors = []
        labels = []
        for t in types:
            vals = data[param].get(t, [])
            if vals:
                box_data.append(vals)
                colors.append(TYPE_COLORS.get(t, "#9E9E9E"))
                labels.append(t)

        if box_data:
            bp = ax.boxplot(box_data, patch_artist=True, labels=labels, widths=0.6)
            for patch, color in zip(bp["boxes"], colors):
                patch.set_facecolor(color)
                patch.set_alpha(0.7)

        ax.set_title(param, fontsize=12, fontweight="bold")
        ax.set_ylabel("Absolute Error")
        ax.tick_params(axis="x", rotation=30)

    fig.suptitle("Parameter Error Distribution by Action Type", fontsize=14, y=1.02)
    fig.tight_layout()
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)


def plot_confusion_matrix(agg, out_path):
    """动作类型混淆矩阵 heatmap。"""
    confusion = agg.get("label_confusion", {})
    all_labels = sorted(set(list(confusion.keys()) +
                            [p for preds in confusion.values() for p in preds.keys()]))
    if not all_labels:
        return

    n = len(all_labels)
    matrix = np.zeros((n, n), dtype=int)
    for i, gt in enumerate(all_labels):
        for j, pred in enumerate(all_labels):
            matrix[i, j] = confusion.get(gt, {}).get(pred, 0)

    fig, ax = plt.subplots(figsize=(6, 5))
    im = ax.imshow(matrix, cmap="Blues", aspect="auto")
    ax.set_xticks(range(n))
    ax.set_yticks(range(n))
    ax.set_xticklabels(all_labels)
    ax.set_yticklabels(all_labels)
    ax.set_xlabel("Predicted")
    ax.set_ylabel("Ground Truth")
    ax.set_title("Action Type Confusion Matrix")

    for i in range(n):
        for j in range(n):
            val = matrix[i, j]
            if val > 0:
                color = "white" if val > matrix.max() * 0.5 else "black"
                ax.text(j, i, str(val), ha="center", va="center", color=color, fontsize=12)

    fig.colorbar(im, ax=ax, shrink=0.8)
    fig.tight_layout()
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)


def plot_depth_metrics(agg, out_path):
    """按 depth 的 type_accuracy 和 acc_score 折线图。"""
    per_depth = agg.get("per_depth", {})
    if not per_depth:
        return

    depths = []
    type_accs = []
    acc_scores = []
    counts = []

    for d_str in sorted(per_depth.keys(), key=lambda x: int(x)):
        m = per_depth[d_str]
        depths.append(int(d_str))
        type_accs.append(m.get("type_accuracy", 0))
        acc_scores.append(m.get("mean_acc_score", 0))
        counts.append(m.get("n", 0))

    fig, ax1 = plt.subplots(figsize=(8, 5))

    ax1.plot(depths, type_accs, "o-", color="#2196F3", label="Type Accuracy", linewidth=2)
    ax1.plot(depths, acc_scores, "s--", color="#4CAF50", label="Param Acc Score", linewidth=2)
    ax1.set_xlabel("Depth", fontsize=12)
    ax1.set_ylabel("Score", fontsize=12)
    ax1.set_ylim(0, 1.05)
    ax1.legend(loc="lower left")

    ax2 = ax1.twinx()
    ax2.bar(depths, counts, alpha=0.2, color="#9E9E9E", label="Sample Count")
    ax2.set_ylabel("Sample Count", fontsize=12, color="#9E9E9E")
    ax2.legend(loc="upper right")

    ax1.set_title("Metrics by Depth", fontsize=14)
    fig.tight_layout()
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)


def generate_plots(results, agg, out_dir):
    """生成所有图表。"""
    os.makedirs(out_dir, exist_ok=True)

    plot_param_boxplot(results, os.path.join(out_dir, "param_error_boxplot.png"))
    plot_confusion_matrix(agg, os.path.join(out_dir, "confusion_matrix.png"))
    plot_depth_metrics(agg, os.path.join(out_dir, "depth_metrics.png"))

    print(f"  已生成图表: {out_dir}/")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--details", required=True, help="eval_details.jsonl 路径")
    ap.add_argument("--report", default=None, help="eval_report.json 路径（可选，不提供则重新计算）")
    ap.add_argument("--out-dir", default="eval/eval_results/plots")
    args = ap.parse_args()

    with open(args.details, "r", encoding="utf-8") as f:
        results = [json.loads(line) for line in f if line.strip()]

    if args.report and os.path.exists(args.report):
        with open(args.report, "r", encoding="utf-8") as f:
            agg = json.load(f)
    else:
        from eval.run_eval import aggregate_results
        agg = aggregate_results(results)

    generate_plots(results, agg, args.out_dir)


if __name__ == "__main__":
    main()
