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

from data_gen.reward import compare_two_fits_symmetric


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
    返回 (node, is_root_fallback)。
    若没有任何 accepted 改进 (max_depth=0)，回退到 root 节点 (该实验在此星系卡在初始拟合)。
    """
    nodes = traj.get("nodes", [])
    if not nodes:
        return None, False

    root = next((n for n in nodes if n.get("parent_id") is None), None)

    accepted = [n for n in nodes if n.get("is_accepted") and n.get("parent_id") is not None]
    if not accepted:
        # 没有任何 accepted 改进 → 终点就是初始拟合 (root)
        return root, True

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
    return deepest[0], False


def _resolve_path(p, traj):
    """残差图/summary 路径解析: 绝对路径不存在时，尝试相对 trajectory 文件所在目录。"""
    if not p:
        return p
    if os.path.exists(p):
        return p
    # 尝试相对 trajectory 所在的实验目录重新拼接 (兼容跨机器/目录搬移)
    src = traj.get("_source_file")
    if src:
        traj_dir = os.path.dirname(os.path.abspath(src))
        # p 里通常含 galaxy_id 子目录，取 basename 之上若干层去拼
        cand = os.path.join(traj_dir, os.path.basename(p))
        if os.path.exists(cand):
            return cand
    return p


def get_endpoint(traj, image_mode="full"):
    """返回终点节点信息 dict，或 None (连 root 都没有)。"""
    leaf, is_root_fallback = find_deepest_leaf(traj)
    if leaf is None:
        return None

    residual = leaf.get("residual_path")
    if image_mode == "full":
        residual = _to_full(residual)
    residual = _resolve_path(residual, traj)
    summary = _resolve_path(leaf.get("summary_path"), traj)

    return {
        "node_id": leaf.get("node_id"),
        "depth": leaf.get("depth", 0),
        "is_root_fallback": is_root_fallback,
        "residual_path": residual,
        "residual_exists": bool(residual and os.path.exists(str(residual))),
        "summary_path": summary,
        "metrics": leaf.get("metrics", {}),
    }


def classify_verdict(result):
    """把对称对决返回的 verdict 翻译成 exp_a / exp_b / tie。

    compare_two_fits_symmetric 里 image_a=exp_a, image_b=exp_b：
      "A_better" → exp_a 胜
      "B_better" → exp_b 胜
      "tie"      → 平手 (含两个方向矛盾被判 tie 的情况)
    """
    v = result.get("verdict", "tie")
    if v == "A_better":
        return "exp_a"
    if v == "B_better":
        return "exp_b"
    return "tie"


def compare_one(gid, ep_a, ep_b, model_name, api_key):
    """对单个星系做一次【无顺序偏置】对决 (内部正反各跑一次)。返回结果 dict。

    缺图不再"默认胜"：图缺失是数据问题，不代表哪个实验拟合更好，单独记 no_endpoint。
    注意: 没有 accepted 改进的实验，终点回退到 root (初始拟合)，仍可正常参与对决。
    """
    a_ok = ep_a and ep_a.get("residual_exists")
    b_ok = ep_b and ep_b.get("residual_exists")

    if not a_ok or not b_ok:
        missing = []
        if not a_ok:
            missing.append("exp_a")
        if not b_ok:
            missing.append("exp_b")
        return {
            "galaxy_id": gid,
            "verdict": "no_endpoint",
            "reason": f"缺终点残差图({','.join(missing)})，不计胜负",
            "exp_a_residual_path": ep_a.get("residual_path") if ep_a else None,
            "exp_b_residual_path": ep_b.get("residual_path") if ep_b else None,
            "exp_a_is_root": ep_a.get("is_root_fallback") if ep_a else None,
            "exp_b_is_root": ep_b.get("is_root_fallback") if ep_b else None,
        }

    try:
        result = compare_two_fits_symmetric(
            image_a_path=ep_a["residual_path"],
            image_b_path=ep_b["residual_path"],
            summary_a_path=ep_a.get("summary_path"),
            summary_b_path=ep_b.get("summary_path"),
            model_name=model_name,
            api_key=api_key,
        )
    except Exception as e:
        return {"galaxy_id": gid, "verdict": "error", "reason": f"VLM 调用失败: {e}"}

    verdict = classify_verdict(result)
    fwd = result.get("forward", {})
    return {
        "galaxy_id": gid,
        "verdict": verdict,
        "robust": result.get("robust"),
        "confidence": fwd.get("confidence"),
        "reason": fwd.get("reason", ""),
        "forward_verdict": fwd.get("verdict"),
        "backward_verdict_translated": result.get("backward_verdict_translated"),
        "exp_a_residual_path": ep_a["residual_path"],
        "exp_b_residual_path": ep_b["residual_path"],
        "exp_a_is_root": ep_a.get("is_root_fallback"),
        "exp_b_is_root": ep_b.get("is_root_fallback"),
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
    parser = argparse.ArgumentParser(description="跨实验轨迹质量 VLM 对决 (无顺序偏置)")
    parser.add_argument("--exp-a", required=True, help="实验A的output目录")
    parser.add_argument("--exp-b", required=True, help="实验B的output目录")
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
        # 打印判别用的图路径,方便人工核对
        def _ep_desc(ep, name):
            if not ep:
                return f"    [{name}] 无任何节点(连root都没有)"
            tag = "root初始拟合" if ep.get("is_root_fallback") else f"叶节点depth={ep.get('depth')}"
            exists = "✓存在" if ep.get("residual_exists") else "✗缺失"
            return (f"    [{name}] {ep.get('node_id')} ({tag}) | 图[{exists}]: {ep.get('residual_path')}")
        print(_ep_desc(ep_a, name_a))
        print(_ep_desc(ep_b, name_b))

        r = compare_one(gid, ep_a, ep_b, args.model, api_key)
        verdict = r["verdict"]
        winner = {
            "exp_a": name_a, "exp_b": name_b, "tie": "平手",
            "no_endpoint": "无终点(不计胜负)", "error": "错误",
        }.get(verdict, verdict)
        robust_tag = "" if r.get("robust") is None else (" [双向一致]" if r.get("robust") else " [双向矛盾→tie]")
        print(f"    判定: {winner}{robust_tag} | "
              f"{name_a}χ²/ν={r.get('exp_a_chi2_nu')} vs {name_b}χ²/ν={r.get('exp_b_chi2_nu')}")
        if r.get("reason"):
            print(f"    理由: {r['reason'][:160]}")
        results.append(r)

    a_wins = sum(1 for r in results if r["verdict"] == "exp_a")
    b_wins = sum(1 for r in results if r["verdict"] == "exp_b")
    ties = sum(1 for r in results if r["verdict"] == "tie")
    no_endpoint = sum(1 for r in results if r["verdict"] == "no_endpoint")
    errors = sum(1 for r in results if r["verdict"] == "error")
    conflicts = sum(1 for r in results if r.get("robust") is False)

    valid = a_wins + b_wins + ties  # 真正参与对决的星系数(不含无终点/错误)
    report = {
        "exp_a": {"name": name_a, "dir": exp_a_dir},
        "exp_b": {"name": name_b, "dir": exp_b_dir},
        "model": args.model,
        "image_mode": args.image_mode,
        "bias_free": "symmetric (内部正反各跑一次,双向一致才算胜)",
        "num_common_galaxies": len(results),
        "num_valid_compared": valid,
        "summary": {
            f"{name_a}_wins": a_wins,
            f"{name_b}_wins": b_wins,
            "ties": ties,
            "direction_conflicts(判tie)": conflicts,
            "no_endpoint(不计胜负)": no_endpoint,
            "errors": errors,
        },
        "details": results,
    }

    print("\n" + "=" * 60)
    print(f"  对决总结: {name_a} vs {name_b}")
    print("=" * 60)
    print(f"  有效对决星系: {valid} (公共{len(results)} - 无终点{no_endpoint} - 错误{errors})")
    print(f"  {name_a} 胜: {a_wins}")
    print(f"  {name_b} 胜: {b_wins}")
    print(f"  平手:    {ties}  (其中双向矛盾判tie: {conflicts})")
    print(f"  无终点(不计胜负): {no_endpoint}")
    print(f"  错误: {errors}")
    print("=" * 60)

    output_path = args.output or os.path.join(
        os.getcwd(), f"compare_{name_a}_vs_{name_b}.json"
    )
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    print(f"\n报告已保存: {output_path}")


if __name__ == "__main__":
    main()
