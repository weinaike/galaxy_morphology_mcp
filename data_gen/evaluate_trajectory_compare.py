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


def _leaf_sort_key(node):
    """叶子排序键: BIC 优先 (内置过拟合惩罚), BIC 缺失则回退 chi2_nu。

    返回 (bic_or_inf, chi2_nu_or_inf): 先按 BIC 升序, BIC 相同/都缺失时按 chi2_nu 升序。
    BIC 缺失的节点排到最后 (用 inf), 避免"没算出BIC"被误当成最优。
    """
    m = node.get("metrics", {}) or {}
    bic = m.get("bic")
    chi2_nu = m.get("chi2_nu")
    bic_key = bic if isinstance(bic, (int, float)) else float("inf")
    chi2_key = chi2_nu if isinstance(chi2_nu, (int, float)) else float("inf")
    return (bic_key, chi2_key)


def find_best_leaf(traj):
    """取 BIC 最优的 accepted 叶节点作为该方法在此星系的"终点代表"。

    用 BIC (而非深度) 选代表, 因为 BIC 内置过拟合惩罚, 能避开"靠堆简并成分走深"
    的过拟合死叶。BIC 缺失时回退 chi2_nu。

    叶节点 = accepted 且没有任何 accepted 子节点。
    返回 (node, is_root_fallback)。
    若没有任何 accepted 改进, 回退到 root 节点 (该实验在此星系卡在初始拟合)。
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

    # 按 BIC 最优选代表 (BIC 缺失回退 chi2_nu)
    leaves.sort(key=_leaf_sort_key)
    return leaves[0], False


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
    leaf, is_root_fallback = find_best_leaf(traj)
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
    """把对称对决返回的 verdict 翻译成 exp_a / exp_b / tie / conflict。

    compare_two_fits_symmetric 里 image_a=exp_a, image_b=exp_b：
      "A_better" → exp_a 胜
      "B_better" → exp_b 胜
      "tie"      → 平手
    若两个方向矛盾 (robust=False)，标 conflict (不再混入平手)。
    """
    if result.get("robust") is False:
        return "conflict"
    v = result.get("verdict", "tie")
    if v == "A_better":
        return "exp_a"
    if v == "B_better":
        return "exp_b"
    return "tie"


def _metric_verdict(ep_a, ep_b, eps_bic=2.0, eps_chi2=0.01):
    """纯客观指标对决: 先比 BIC (差异 > eps_bic 才算赢), 否则比 chi2_nu。

    BIC 差异阈值 eps_bic=2.0 对应统计学上 "positive evidence" 的最低门槛。
    返回 (verdict, basis): verdict ∈ {exp_a, exp_b, tie}, basis 说明依据。
    """
    a = ep_a.get("metrics", {}) or {}
    b = ep_b.get("metrics", {}) or {}
    a_bic, b_bic = a.get("bic"), b.get("bic")
    a_chi, b_chi = a.get("chi2_nu"), b.get("chi2_nu")

    if isinstance(a_bic, (int, float)) and isinstance(b_bic, (int, float)):
        if abs(a_bic - b_bic) > eps_bic:
            return ("exp_a" if a_bic < b_bic else "exp_b", f"BIC ({a_bic:.2f} vs {b_bic:.2f})")
        # BIC 接近 → 用 chi2_nu 做 tiebreaker
        if isinstance(a_chi, (int, float)) and isinstance(b_chi, (int, float)) and abs(a_chi - b_chi) > eps_chi2:
            return ("exp_a" if a_chi < b_chi else "exp_b", f"BIC接近,chi2_nu ({a_chi:.3f} vs {b_chi:.3f})")
        return ("tie", f"BIC接近 ({a_bic:.2f} vs {b_bic:.2f}), chi2_nu接近")

    # BIC 缺失 → 退回 chi2_nu
    if isinstance(a_chi, (int, float)) and isinstance(b_chi, (int, float)):
        if abs(a_chi - b_chi) > eps_chi2:
            return ("exp_a" if a_chi < b_chi else "exp_b", f"无BIC,chi2_nu ({a_chi:.3f} vs {b_chi:.3f})")
        return ("tie", f"无BIC,chi2_nu接近 ({a_chi:.3f} vs {b_chi:.3f})")

    return ("tie", "无可用指标")


def compare_one(gid, ep_a, ep_b, model_name, api_key, mode="both"):
    """对单个星系做对决。mode ∈ {metric, vlm, both}。返回结果 dict。

    缺图不再"默认胜"：图缺失是数据问题，不代表哪个实验拟合更好，单独记 no_endpoint。
    注意: 没有 accepted 改进的实验，终点回退到 root (初始拟合)，仍可正常参与对决。
    顶层 verdict 取自 mode: metric→指标判定; vlm→VLM判定; both→以指标为准, VLM作旁证。
    """
    a_ok = ep_a and ep_a.get("residual_exists")
    b_ok = ep_b and ep_b.get("residual_exists")

    base = {
        "galaxy_id": gid,
        "exp_a_residual_path": ep_a.get("residual_path") if ep_a else None,
        "exp_b_residual_path": ep_b.get("residual_path") if ep_b else None,
        "exp_a_is_root": ep_a.get("is_root_fallback") if ep_a else None,
        "exp_b_is_root": ep_b.get("is_root_fallback") if ep_b else None,
        "exp_a_chi2_nu": (ep_a.get("metrics", {}) or {}).get("chi2_nu") if ep_a else None,
        "exp_b_chi2_nu": (ep_b.get("metrics", {}) or {}).get("chi2_nu") if ep_b else None,
        "exp_a_bic": (ep_a.get("metrics", {}) or {}).get("bic") if ep_a else None,
        "exp_b_bic": (ep_b.get("metrics", {}) or {}).get("bic") if ep_b else None,
        "exp_a_depth": ep_a.get("depth") if ep_a else None,
        "exp_b_depth": ep_b.get("depth") if ep_b else None,
        "exp_a_node": ep_a.get("node_id") if ep_a else None,
        "exp_b_node": ep_b.get("node_id") if ep_b else None,
    }

    # 指标判定不需要图，只要有 metrics 就能算
    has_metrics = ep_a and ep_b
    if has_metrics:
        m_verdict, m_basis = _metric_verdict(ep_a, ep_b)
        base["metric_verdict"] = m_verdict
        base["metric_basis"] = m_basis
    else:
        base["metric_verdict"] = "no_endpoint"
        base["metric_basis"] = "缺节点"

    # ---- metric-only 模式: 不调 VLM ----
    if mode == "metric":
        base["verdict"] = base["metric_verdict"]
        return base

    # ---- 需要 VLM, 但缺图 → 该星系 VLM 部分判 no_endpoint ----
    if not a_ok or not b_ok:
        missing = [n for n, ok in [("exp_a", a_ok), ("exp_b", b_ok)] if not ok]
        base["vlm_verdict"] = "no_endpoint"
        base["reason"] = f"缺终点残差图({','.join(missing)})，VLM跳过"
        base["verdict"] = base["metric_verdict"] if mode == "both" else "no_endpoint"
        return base

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
        base["vlm_verdict"] = "error"
        base["reason"] = f"VLM 调用失败: {e}"
        base["verdict"] = base.get("metric_verdict") if mode == "both" else "error"
        return base

    vlm_verdict = classify_verdict(result)
    fwd = result.get("forward", {})
    bwd = result.get("backward", {})
    base["vlm_verdict"] = vlm_verdict
    base["vlm_robust"] = result.get("robust")
    base["vlm_confidence"] = fwd.get("confidence")
    base["reason"] = fwd.get("reason", "")
    base["forward_verdict"] = fwd.get("verdict")
    base["forward_reason"] = fwd.get("reason", "")
    base["backward_verdict_translated"] = result.get("backward_verdict_translated")
    base["backward_reason"] = bwd.get("reason", "")

    # 顶层 verdict: both→以客观指标为准(VLM作旁证); vlm→VLM判定
    base["verdict"] = base["metric_verdict"] if mode == "both" else vlm_verdict
    return base


def main():
    parser = argparse.ArgumentParser(description="跨实验终点质量对决 (客观指标 + 无顺序偏置VLM)")
    parser.add_argument("--exp-a", required=True, help="实验A的output目录")
    parser.add_argument("--exp-b", required=True, help="实验B的output目录")
    parser.add_argument("--mode", default="both", choices=["metric", "vlm", "both"],
                        help="metric=纯客观BIC/chi2(零成本); vlm=仅VLM视觉; both=两者都跑,顶层以客观指标为准")
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
    if args.mode in ("vlm", "both") and not api_key:
        print("[ERROR] 该模式需要 VLM, 未设置 API key (OPENAI_API_KEY)。或改用 --mode metric")
        return

    print(f"模式: {args.mode} | 实验A ({name_a}): {exp_a_dir}")
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
            m = ep.get("metrics", {}) or {}
            return (f"    [{name}] {ep.get('node_id')} ({tag}) | "
                    f"BIC={m.get('bic')} χ²/ν={m.get('chi2_nu')} | 图[{exists}]: {ep.get('residual_path')}")
        print(_ep_desc(ep_a, name_a))
        print(_ep_desc(ep_b, name_b))

        r = compare_one(gid, ep_a, ep_b, args.model, api_key, mode=args.mode)
        name_map = {
            "exp_a": name_a, "exp_b": name_b, "tie": "平手", "conflict": "VLM双向分歧",
            "no_endpoint": "无终点(不计胜负)", "error": "错误",
        }
        # 打印客观指标判定
        mv = r.get("metric_verdict")
        if mv:
            print(f"    [客观指标] {name_map.get(mv, mv)} | 依据: {r.get('metric_basis')}")
        # 打印VLM判定
        vv = r.get("vlm_verdict")
        if vv:
            print(f"    [VLM视觉]  {name_map.get(vv, vv)}")
            if vv == "conflict":
                print(f"        正向: {name_map.get('exp_a' if r.get('forward_verdict')=='A_better' else ('exp_b' if r.get('forward_verdict')=='B_better' else 'tie'))} | {str(r.get('forward_reason',''))[:120]}")
                print(f"        反向: {str(r.get('backward_reason',''))[:120]}")
            elif r.get("reason"):
                print(f"        理由: {str(r['reason'])[:140]}")
        results.append(r)

    # ---- 客观指标维度汇总 ----
    def _count(key, val):
        return sum(1 for r in results if r.get(key) == val)

    m_a = _count("metric_verdict", "exp_a")
    m_b = _count("metric_verdict", "exp_b")
    m_tie = _count("metric_verdict", "tie")
    m_none = _count("metric_verdict", "no_endpoint")

    # ---- VLM 维度汇总 ----
    v_a = _count("vlm_verdict", "exp_a")
    v_b = _count("vlm_verdict", "exp_b")
    v_tie = _count("vlm_verdict", "tie")
    v_conflict = _count("vlm_verdict", "conflict")
    v_none = _count("vlm_verdict", "no_endpoint")
    v_err = _count("vlm_verdict", "error")

    report = {
        "exp_a": {"name": name_a, "dir": exp_a_dir},
        "exp_b": {"name": name_b, "dir": exp_b_dir},
        "mode": args.mode,
        "model": args.model,
        "image_mode": args.image_mode,
        "leaf_selection": "按BIC最低选代表叶(内置过拟合惩罚), BIC缺失回退chi2_nu",
        "num_common_galaxies": len(results),
        "metric_summary": {
            f"{name_a}_wins": m_a,
            f"{name_b}_wins": m_b,
            "ties": m_tie,
            "no_endpoint": m_none,
        },
        "vlm_summary": {
            f"{name_a}_wins": v_a,
            f"{name_b}_wins": v_b,
            "ties": v_tie,
            "conflicts(双向分歧)": v_conflict,
            "no_endpoint": v_none,
            "errors": v_err,
        } if args.mode in ("vlm", "both") else None,
        "details": results,
    }

    print("\n" + "=" * 64)
    print(f"  对决总结: {name_a} vs {name_b}  (mode={args.mode})")
    print("=" * 64)
    print(f"  [客观指标 BIC/chi2]  {name_a}胜 {m_a} | {name_b}胜 {m_b} | 平手 {m_tie} | 无终点 {m_none}")
    if args.mode in ("vlm", "both"):
        print(f"  [VLM 视觉对决]       {name_a}胜 {v_a} | {name_b}胜 {v_b} | 平手 {v_tie} | "
              f"双向分歧 {v_conflict} | 无终点 {v_none} | 错误 {v_err}")
    print("=" * 64)

    output_path = args.output or os.path.join(
        os.getcwd(), f"compare_{name_a}_vs_{name_b}.json"
    )
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    print(f"\n报告已保存: {output_path}")


if __name__ == "__main__":
    main()
