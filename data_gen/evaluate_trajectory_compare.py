"""
跨实验轨迹质量对决 (Trajectory Quality Head-to-Head)。

复用 data_gen.reward.calculate_reward_model_with_param —— 即 pipeline 内部用的
"两张残差图 + 两份参数摘要" VLM 评判函数 —— 对两个实验在【同一星系】上的
最终拟合结果做两两对决，判定哪个实验的终点效果更好。

核心思路:
- E1-E6 跑的是同样 20 个星系 (seed=42)，同一星系 root 完全相同，
  因此终点残差图/参数跨实验直接可比。
- 取每个实验该星系的"最深叶节点"作为终点 (代表轨迹自然终止时的状态)。
- 把 (exp_a 终点, exp_b 终点) 当作 (Image1=prev, Image2=next) 喂给 VLM:
    improvement_level = worse        → exp_b 比 exp_a 差 (exp_a 胜)
    improvement_level = no_improvement → 平手
    improvement_level = slight/clear_improvement → exp_b 比 exp_a 好 (exp_b 胜)

用法:
    python -m data_gen.evaluate_trajectory_compare \
        --exp-a output/E1__rule_based.../ \
        --exp-b output/E3__vlm_proposal.../ \
        [--model gemini-3.1-pro-preview] \
        [--image-mode full] \
        [--output compare_E1_vs_E3.json]
"""

import argparse
import json
import os
import glob

from data_gen.reward import calculate_reward_model_with_param


def _to_full(p):
    """残差图默认存 _cutoff 版；full 版去掉 _cutoff 后缀。与 pipeline 逻辑一致。"""
    if not p:
        return p
    stem, ext = os.path.splitext(p)
    if stem.endswith("_cutoff"):
        full_p = stem[:-len("_cutoff")] + ext
        if os.path.exists(full_p):
            return full_p
    return p


def load_trajectories(exp_dir):
    """扫描实验目录下所有 *_trajectory.json，返回 {galaxy_id: traj_dict}。"""
    pattern = os.path.join(exp_dir, "**", "*_trajectory.json")
    files = glob.glob(pattern, recursive=True)
    out = {}
    for f in sorted(files):
        try:
            with open(f, "r", encoding="utf-8") as fh:
                traj = json.load(fh)
        except Exception as e:
            print(f"  [WARN] 跳过无法解析 {f}: {e}")
            continue
        gid = traj.get("galaxy_id")
        if gid:
            traj["_source_file"] = f
            out[gid] = traj
    return out


def find_deepest_leaf(traj):
    """取最深的 accepted 叶节点 (代表轨迹自然终止时的状态)。

    叶节点 = accepted 且没有任何 accepted 子节点。
    多个同深度叶节点时取 chi2_nu 最小的那个。
    返回 node dict 或 None。
    """
    nodes = traj.get("nodes", [])
    if not nodes:
        return None

    accepted = [n for n in nodes if n.get("is_accepted") and n.get("parent_id") is not None]
    if not accepted:
        return None

    accepted_ids = {n["node_id"] for n in accepted}
    has_accepted_child = set()
    for n in nodes:
        pid = n.get("parent_id")
        if n.get("is_accepted") and pid in accepted_ids:
            has_accepted_child.add(pid)

    leaves = [n for n in accepted if n["node_id"] not in has_accepted_child]
    if not leaves:
        leaves = accepted

    max_depth = max(n.get("depth", 0) for n in leaves)
    deepest = [n for n in leaves if n.get("depth", 0) == max_depth]
    deepest.sort(key=lambda n: n.get("metrics", {}).get("chi2_nu", 9999.0))
    return deepest[0]


def get_endpoint(traj, image_mode="full"):
    """返回终点节点的 (residual_image_path, summary_path, metrics, node_id)。"""
    leaf = find_deepest_leaf(traj)
    if leaf is None:
        return None

    residual = leaf.get("residual_path")
    if image_mode == "full":
        residual = _to_full(residual)
    summary = leaf.get("summary_path")
    return {
        "node_id": leaf.get("node_id"),
        "depth": leaf.get("depth", 0),
        "residual_path": residual,
        "summary_path": summary,
        "metrics": leaf.get("metrics", {}),
    }


def classify_verdict(result):
    """把 VLM 返回的判定翻译成 exp_a / exp_b / tie 胜负。

    注意: exp_a=Image1(prev), exp_b=Image2(next)。
    judge 描述 "Image2 相对 Image1" 的变化。

    函数末尾把 improvement_level 重命名为 residual_improvement_level (reward.py:1538)，
    这里以 residual_improvement_level 为主信号。
    """
    level = result.get("residual_improvement_level") or result.get("improvement_level") or "no_improvement"
    if level in ("clear_improvement", "slight_improvement"):
        return "exp_b"   # Image2(exp_b) 残差更干净
    if level == "worse":
        return "exp_a"   # Image2(exp_b) 更差 → exp_a 胜
    return "tie"         # no_improvement


def compare_one(gid, ep_a, ep_b, model_name, api_key):
    """对单个星系做一次 VLM 对决。返回结果 dict。"""
    # 缺图直接判:谁有终点谁赢
    a_ok = ep_a and ep_a.get("residual_path") and os.path.exists(str(ep_a["residual_path"]))
    b_ok = ep_b and ep_b.get("residual_path") and os.path.exists(str(ep_b["residual_path"]))

    if not a_ok and not b_ok:
        return {"galaxy_id": gid, "verdict": "skip", "reason": "两个实验都缺终点残差图"}
    if not a_ok:
        return {"galaxy_id": gid, "verdict": "exp_b", "reason": "exp_a 缺终点残差图，exp_b 默认胜", "by_default": True}
    if not b_ok:
        return {"galaxy_id": gid, "verdict": "exp_a", "reason": "exp_b 缺终点残差图，exp_a 默认胜", "by_default": True}

    try:
        result = calculate_reward_model_with_param(
            prev_residual_image_path=ep_a["residual_path"],
            next_residual_image_path=ep_b["residual_path"],
            prev_summary_path=ep_a.get("summary_path"),
            new_summary_path=ep_b.get("summary_path"),
            model_name=model_name,
            api_key=api_key,
        )
    except Exception as e:
        return {"galaxy_id": gid, "verdict": "error", "reason": f"VLM 调用失败: {e}"}

    verdict = classify_verdict(result)
    return {
        "galaxy_id": gid,
        "verdict": verdict,
        "improvement_level": result.get("residual_improvement_level") or result.get("improvement_level"),
        "confidence": result.get("confidence"),
        "reason": result.get("reason", ""),
        "exp_a_chi2_nu": ep_a["metrics"].get("chi2_nu"),
        "exp_b_chi2_nu": ep_b["metrics"].get("chi2_nu"),
        "exp_a_bic": ep_a["metrics"].get("bic"),
        "exp_b_bic": ep_b["metrics"].get("bic"),
        "exp_a_depth": ep_a["depth"],
        "exp_b_depth": ep_b["depth"],
        "exp_a_node": ep_a["node_id"],
        "exp_b_node": ep_b["node_id"],
    }


def main():
    parser = argparse.ArgumentParser(description="跨实验轨迹质量 VLM 对决")
    parser.add_argument("--exp-a", required=True, help="实验A的output目录 (作为 Image1/prev)")
    parser.add_argument("--exp-b", required=True, help="实验B的output目录 (作为 Image2/next)")
    parser.add_argument("--model", default="gemini-3.1-pro-preview", help="VLM模型名")
    parser.add_argument("--image-mode", default="full", choices=["full", "cutoff"], help="残差图模式")
    parser.add_argument("--output", default=None, help="输出JSON路径")
    parser.add_argument("--api-key", default=None, help="API key (默认读 OPENAI_API_KEY)")
    args = parser.parse_args()

    exp_a_dir = os.path.abspath(args.exp_a)
    exp_b_dir = os.path.abspath(args.exp_b)
    name_a = os.path.basename(exp_a_dir.rstrip("/")).split("__")[0] or "exp_a"
    name_b = os.path.basename(exp_b_dir.rstrip("/")).split("__")[0] or "exp_b"

    api_key = args.api_key or os.getenv("OPENAI_API_KEY")
    if not api_key:
        print("[ERROR] 未设置 API key (OPENAI_API_KEY)")
        return

    print(f"实验A ({name_a}): {exp_a_dir}")
    print(f"实验B ({name_b}): {exp_b_dir}")

    trajs_a = load_trajectories(exp_a_dir)
    trajs_b = load_trajectories(exp_b_dir)
    common = sorted(set(trajs_a) & set(trajs_b))
    print(f"\n实验A星系数: {len(trajs_a)} | 实验B星系数: {len(trajs_b)} | 公共星系数: {len(common)}")

    if not common:
        print("[ERROR] 两个实验没有公共星系")
        return

    results = []
    for gid in common:
        ep_a = get_endpoint(trajs_a[gid], args.image_mode)
        ep_b = get_endpoint(trajs_b[gid], args.image_mode)
        print(f"\n>>> 对决星系: {gid}")
        r = compare_one(gid, ep_a, ep_b, args.model, api_key)
        verdict = r["verdict"]
        winner = {"exp_a": name_a, "exp_b": name_b, "tie": "平手", "skip": "跳过", "error": "错误"}.get(verdict, verdict)
        print(f"    判定: {winner} | level={r.get('improvement_level')} | "
              f"{name_a}χ²/ν={r.get('exp_a_chi2_nu')} vs {name_b}χ²/ν={r.get('exp_b_chi2_nu')}")
        if r.get("reason"):
            print(f"    理由: {r['reason'][:160]}")
        results.append(r)

    a_wins = sum(1 for r in results if r["verdict"] == "exp_a")
    b_wins = sum(1 for r in results if r["verdict"] == "exp_b")
    ties = sum(1 for r in results if r["verdict"] == "tie")
    skips = sum(1 for r in results if r["verdict"] in ("skip", "error"))

    report = {
        "exp_a": {"name": name_a, "dir": exp_a_dir},
        "exp_b": {"name": name_b, "dir": exp_b_dir},
        "model": args.model,
        "image_mode": args.image_mode,
        "num_compared": len(results),
        "summary": {
            f"{name_a}_wins": a_wins,
            f"{name_b}_wins": b_wins,
            "ties": ties,
            "skip_or_error": skips,
        },
        "details": results,
    }

    print("\n" + "=" * 60)
    print(f"  对决总结: {name_a} vs {name_b}")
    print("=" * 60)
    print(f"  {name_a} 胜: {a_wins}")
    print(f"  {name_b} 胜: {b_wins}")
    print(f"  平手:    {ties}")
    print(f"  跳过/错误: {skips}")
    print("=" * 60)

    output_path = args.output or os.path.join(
        os.getcwd(), f"compare_{name_a}_vs_{name_b}.json"
    )
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    print(f"\n报告已保存: {output_path}")


if __name__ == "__main__":
    main()
