"""
看 LLaMA-Factory 训练 loss 曲线（读 output_dir/trainer_log.jsonl）。

服务器无图形界面：用 Agg 后端把曲线存成 loss_curve.png（可 scp 下来看），并在终端打印最新数值。

用法：
    python watch_loss.py <output_dir>              # 画一次
    python watch_loss.py <output_dir> --loop 30    # 每 30s 刷新一次（Ctrl-C 退出）

<output_dir> 即训练时的 output_dir（如 saves/qwen2_5vl-7b-galaxy-qlora）。
"""

import argparse
import json
import os
import time

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


def read_log(log_path):
    """读 trainer_log.jsonl，返回 (train_steps, train_loss, eval_steps, eval_loss)。"""
    tr_s, tr_l, ev_s, ev_l = [], [], [], []
    if not os.path.isfile(log_path):
        return tr_s, tr_l, ev_s, ev_l
    with open(log_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except Exception:
                continue
            step = d.get("current_steps", d.get("step"))
            if step is None:
                continue
            if d.get("loss") is not None:
                tr_s.append(step); tr_l.append(d["loss"])
            if d.get("eval_loss") is not None:
                ev_s.append(step); ev_l.append(d["eval_loss"])
    return tr_s, tr_l, ev_s, ev_l


def plot_once(output_dir):
    log_path = os.path.join(output_dir, "trainer_log.jsonl")
    tr_s, tr_l, ev_s, ev_l = read_log(log_path)
    if not tr_s and not ev_s:
        print(f"[watch_loss] 暂无数据（{log_path} 不存在或为空），训练可能还在加载模型/预处理。")
        return

    plt.figure(figsize=(9, 5))
    if tr_s:
        plt.plot(tr_s, tr_l, color="steelblue", marker=".", ms=3, lw=1, label="train loss")
    if ev_s:
        plt.plot(ev_s, ev_l, color="crimson", marker="o", ms=5, lw=1.5, label="eval loss")
    plt.xlabel("step"); plt.ylabel("loss"); plt.legend()
    plt.title(f"SFT loss — {os.path.basename(output_dir.rstrip('/'))}")
    plt.grid(alpha=0.3)
    out_png = os.path.join(output_dir, "loss_curve.png")
    plt.tight_layout(); plt.savefig(out_png, dpi=150); plt.close()

    last_tr = f"{tr_l[-1]:.4f}@{tr_s[-1]}" if tr_s else "NA"
    last_ev = f"{ev_l[-1]:.4f}@{ev_s[-1]}" if ev_s else "NA"
    print(f"[watch_loss] steps={tr_s[-1] if tr_s else 0} | train={last_tr} | eval={last_ev} | 图: {out_png}")


def main():
    ap = argparse.ArgumentParser(description="画 LLaMA-Factory 训练 loss 曲线")
    ap.add_argument("output_dir", help="训练 output_dir")
    ap.add_argument("--loop", type=int, default=0, help="每 N 秒刷新一次（0=只画一次）")
    args = ap.parse_args()

    if args.loop <= 0:
        plot_once(args.output_dir)
        return
    print(f"[watch_loss] 每 {args.loop}s 刷新 {args.output_dir}/loss_curve.png（Ctrl-C 退出）")
    try:
        while True:
            plot_once(args.output_dir)
            time.sleep(args.loop)
    except KeyboardInterrupt:
        print("\n[watch_loss] 退出。")


if __name__ == "__main__":
    main()
