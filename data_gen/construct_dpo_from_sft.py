"""
低成本 DPO 数据构造：从已有 SFT 轨迹 + 弱模型 + GALFIT 客观筛选生成偏好对。

背景：NUM_CALLS=1 单链只产 SFT、不产 DPO（每个父节点仅一个子节点，无 winner/loser 兄弟）。
本脚本事后单独构造 DPO：对每个 accepted 节点(=chosen)，用弱模型对同一输入生成替代提议作为
rejected 候选，实跑 GALFIT 用客观指标(BIC/chi2_nu)筛选“确实更差”的，再用 vlm_reward(flash)
二次确认剔除假负样本。

⚠️ 必须在 A6000 上运行：trajectory.json 里的 feedme/summary 路径是 A6000 绝对路径，且需本地 GALFIT。

用法:
    python -m data_gen.construct_dpo_from_sft \
      --input output/<experiment_dir>/ \
      --gadotti-root <GalfitEnv base_project_dir> \
      --weak-model <api.road2all.com 上的便宜模型名> \
      --vlm-reward-model <flash 模型名> \
      --num-candidates 3 --max-rejected-per-chosen 2
"""

import argparse
import asyncio
import copy
import glob
import json
import os
import re

try:
    from dotenv import load_dotenv
except ImportError:  # dotenv 非必需：环境变量可能已由 shell 设置
    def load_dotenv(*a, **k):
        return False

from simulator_env.galfit_env import GalfitEnv
from simulator_env.galfit_actions import parse_components_from_feedme
from data_gen.vlm_proposal import (
    generate_vlm_proposals,
    _normalize_component,
    diff_spec_vs_parent,
)
from data_gen.reward import calculate_reward_model_with_param

load_dotenv(override=True)

# ========== 默认配置（可被 CLI 覆盖）==========
NUM_CANDIDATES = 3               # 弱模型每个 chosen 生成候选数
ENABLE_RULE_CORRUPTION = True    # 是否额外做规则注入结构错误负样本
MAX_REJECTED_PER_CHOSEN = 2      # 每个 chosen 最多保留几个 rejected
BIC_MARGIN = 2.0                 # BIC 判“更差”的绝对差值阈值
CHI2_REL_MARGIN = 0.02           # 无 BIC 时 chi2_nu 的相对差值阈值
VLM_CONF_THRESHOLD = 0.6         # 第二道：候选被判 improvement=1 且置信≥此值 → 视为假负剔除
KEEP_CRASH_AS_HARD_NEG = False   # GALFIT 崩溃的候选是否留作 hard-negative


def _count_sersic_components(feedme_path: str) -> int:
    """统计 feedme 中 sersic 成分数（复刻 pipeline._count_sersic_components）。"""
    count = 0
    if feedme_path and os.path.exists(feedme_path):
        with open(feedme_path, "r") as f:
            for line in f:
                if re.match(r'^0\)\s+sersic', line.strip()):
                    count += 1
    return max(1, count)


def _metric_value(metrics: dict):
    """返回 (value, basis)：优先 BIC，缺则 chi2_nu。值越小越好。"""
    if not metrics:
        return None, None
    bic = metrics.get("bic")
    if bic is not None:
        return float(bic), "bic"
    chi2_nu = metrics.get("chi2_nu")
    if chi2_nu is not None:
        return float(chi2_nu), "chi2_nu"
    return None, None


def _is_worse(cand_metrics: dict, chosen_metrics: dict):
    """判定候选是否“确实更差”。返回 (is_worse, basis, margin_value)。

    - 两者都有 BIC → 用 BIC 绝对差 (>BIC_MARGIN)
    - 否则回退 chi2_nu 相对差 (>CHI2_REL_MARGIN)
    - 候选指标缺失(如 chi2_nu>=9999 的崩溃兜底) → 视为极差
    """
    cand_v, cand_basis = _metric_value(cand_metrics)
    chosen_v, chosen_basis = _metric_value(chosen_metrics)
    if cand_v is None:
        return True, "invalid", float("inf")
    if chosen_v is None:
        return False, "chosen_invalid", 0.0

    # 统一口径：只有当两者用同一基准时才可比；否则都退回 chi2_nu
    if cand_basis == "bic" and chosen_basis == "bic":
        margin = cand_v - chosen_v
        return (margin > BIC_MARGIN), "bic", round(margin, 4)

    cand_c = cand_metrics.get("chi2_nu")
    chosen_c = chosen_metrics.get("chi2_nu")
    if cand_c is None or chosen_c is None:
        return False, "chi2_nu_missing", 0.0
    cand_c, chosen_c = float(cand_c), float(chosen_c)
    rel = (cand_c - chosen_c) / max(abs(chosen_c), 1e-6)
    return (rel > CHI2_REL_MARGIN), "chi2_nu", round(rel, 4)


def _gen_rule_corruptions(chosen_spec: dict, parent_components: list) -> list:
    """从 chosen spec 做定向劣化，产出结构错误候选 [(spec, tag), ...]。"""
    out = []
    comps = chosen_spec.get("components") or []
    sky = chosen_spec.get("sky", {"value": None, "fix": 0})
    if not comps:
        return out

    P = len(parent_components)
    added_idx = len(comps) - 1 if len(comps) == P + 1 else None  # chosen 新增的成分位置

    # 变换1：角色互换 bar↔disk（把该加的结构改成错的结构）
    if added_idx is not None:
        added = comps[added_idx]
        role = (added.get("role") or "").lower()
        swap_map = {"bar": "disk", "disk": "bar", "bulge": "disk"}
        if role in swap_map:
            new_comps = copy.deepcopy(comps)
            tgt = new_comps[added_idx]
            new_role = swap_map[role]
            tgt["role"] = new_role
            tgt.pop("model", None)  # 让 _normalize_component 按新 role 推断 model/n
            normed = [_normalize_component(c) for c in new_comps]
            normed = [c for c in normed if c]
            if len(normed) == len(new_comps):
                out.append(({"components": normed, "sky": sky}, f"swap_{role}2{new_role}"))

    # 变换2：删掉 chosen 新增的关键成分（欠拟合）
    if added_idx is not None and len(comps) > 1:
        new_comps = [copy.deepcopy(c) for i, c in enumerate(comps) if i != added_idx]
        normed = [_normalize_component(c) for c in new_comps]
        normed = [c for c in normed if c]
        if len(normed) == len(new_comps) and normed:
            out.append(({"components": normed, "sky": sky}, "drop_added"))

    # 变换3：加一个多余的 psf 核点源（过拟合/无谓成分）
    center = comps[0]
    extra = _normalize_component({
        "role": "nucleus", "model": "psf",
        "x": center.get("x"), "y": center.get("y"),
        "mag": (center.get("mag") or 18.0) + 2.0, "fix": {},
    })
    if extra:
        new_comps = [copy.deepcopy(c) for c in comps] + [extra]
        out.append(({"components": new_comps, "sky": sky}, "extra_psf"))

    return out


async def _run_candidate(env, cand_spec, coarse_label, parent, node_id, step_idx, sandbox_dir):
    """把一个候选 spec 实跑 GALFIT，返回 step 结果（含 metrics/residual/summary）。"""
    action = {"spec": cand_spec, "coarse_label": coarse_label}
    try:
        return await env.step(
            action=action,
            current_feedme_path=parent["feedme_path"],
            current_png_path=parent["residual_path"],
            output_dir=sandbox_dir,
            node_id=node_id,
            summary_path=parent.get("summary_path"),
            step_idx=step_idx,
        )
    except Exception as e:
        return {"status": "failed", "error": f"exception: {e}"}


async def process_chosen(env, node, node_map, galaxy_id, sandbox_root, args, stats):
    """对一个 accepted 节点(chosen)构造 DPO 对，返回 pair 列表。"""
    parent = node_map.get(node.get("parent_id"))
    if not parent:
        stats["skip_no_parent"] += 1
        return []
    parent_feedme = parent.get("feedme_path")
    if not parent_feedme or not os.path.exists(parent_feedme):
        print(f"  [跳过] {galaxy_id}/{node['node_id']}: 父 feedme 缺失 {parent_feedme}")
        stats["skip_no_parent_feedme"] += 1
        return []

    act = node.get("action_from_parent") or {}
    chosen_spec = act.get("spec")
    if not chosen_spec or not chosen_spec.get("components"):
        stats["skip_no_chosen_spec"] += 1
        return []
    chosen_metrics = node.get("metrics") or {}
    chosen_label = act.get("coarse_label", "unknown")

    depth = node.get("depth", node.get("step", 1)) or 1
    parent_components = parse_components_from_feedme(parent_feedme)
    num_sersic = _count_sersic_components(parent_feedme)
    state_context = {
        "num_sersic": num_sersic,
        "parent_components": parent_components,
        "residual_path": parent.get("residual_path"),
        "summary_path": parent.get("summary_path"),
        "history_summary": None,
        "expert_gt": None,
    }

    # ---- 1) 生成候选：弱模型 + 规则注入 ----
    candidates = []  # [(spec, coarse_label, source, tag)]
    try:
        weak_decisions, usage = await generate_vlm_proposals(
            state_context, current_step=depth,
            num_calls=args.num_candidates,
            model_name=args.weak_model,
            multiturn=False,
        )
        if usage:
            stats["weak_prompt_tokens"] += usage.get("prompt_tokens", 0)
            stats["weak_completion_tokens"] += usage.get("completion_tokens", 0)
        for d in (weak_decisions or []):
            candidates.append((d["spec"], d.get("coarse_label", "modify"), "weak_model", "weak"))
    except Exception as e:
        print(f"  [警告] {galaxy_id}/{node['node_id']} 弱模型提议失败: {e}")

    if args.enable_rule_corruption:
        for spec, tag in _gen_rule_corruptions(chosen_spec, parent_components):
            _, cl = diff_spec_vs_parent(parent_components, spec["components"])
            candidates.append((spec, cl, "rule_corruption", tag))

    stats["candidates_generated"] += len(candidates)

    # ---- 2) 实跑 GALFIT + 第一道客观筛 ----
    survivors = []  # [(step_result, coarse_label, source, tag, basis, margin)]
    for i, (spec, cl, source, tag) in enumerate(candidates):
        node_id = f"{node['node_id']}__rej_{i}_{tag}"
        sandbox = os.path.join(sandbox_root, galaxy_id, node["node_id"], f"{i}_{tag}")
        res = await _run_candidate(env, spec, cl, parent, node_id, depth, sandbox)
        status = res.get("status")

        if status == "failed":
            stats["cand_crashed"] += 1
            if args.keep_crash_as_hard_neg:
                res.setdefault("metrics", {"chi2_nu": 9999.0})
                survivors.append((res, cl, source, tag, "crash", float("inf"), spec))
            continue
        if status != "success":  # rejected_by_ssim 等：与父几乎无差，不作 rejected
            stats["cand_ssim_or_other"] += 1
            continue

        is_worse, basis, margin = _is_worse(res.get("metrics", {}), chosen_metrics)
        if not is_worse:
            stats["cand_not_worse"] += 1  # 更好或持平 → 丢弃（潜在新 SFT，仅记数）
            continue
        survivors.append((res, cl, source, tag, basis, margin, spec))

    stats["passed_objective"] += len(survivors)

    # ---- 3) 第二道 vlm_reward(flash) 确认 ----
    confirmed = []
    for res, cl, source, tag, basis, margin, spec in survivors:
        if basis == "crash":  # 崩溃硬负样本无残差图，跳过视觉确认
            confirmed.append((res, cl, source, tag, basis, margin, spec, None))
            continue
        try:
            verdict = calculate_reward_model_with_param(
                prev_residual_image_path=parent.get("residual_path"),
                next_residual_image_path=res.get("residual_path"),
                prev_summary_path=parent.get("summary_path"),
                new_summary_path=res.get("summary_path"),
                model_name=args.vlm_reward_model,
            )
            u = verdict.get("usage") or {}
            stats["vlm_prompt_tokens"] += u.get("prompt_tokens", 0)
            stats["vlm_completion_tokens"] += u.get("completion_tokens", 0)
        except Exception as e:
            print(f"  [警告] vlm 二次确认失败 {node['node_id']}/{tag}: {e}")
            verdict = {"improvement": 0, "confidence": 0.0, "reason": f"vlm_error: {e}"}

        improved = verdict.get("improvement", 0) == 1
        conf = verdict.get("confidence", 0.0)
        if improved and conf >= VLM_CONF_THRESHOLD:
            stats["vlm_rejected_as_false_neg"] += 1  # 候选其实改善了 → 假负，剔除
            continue
        confirmed.append((res, cl, source, tag, basis, margin, spec,
                          {"improvement": verdict.get("improvement"),
                           "confidence": conf, "reason": verdict.get("reason")}))

    # ---- 4) 排序取 top-N，成对输出 ----
    # 优先“略差”的做细微偏好，但至少保 1 个结构错误(rule_corruption)
    confirmed.sort(key=lambda x: x[5])  # 按 margin 升序
    picked, has_struct = [], False
    for item in confirmed:
        if len(picked) >= args.max_rejected_per_chosen:
            break
        picked.append(item)
        if item[2] == "rule_corruption":
            has_struct = True
    if not has_struct:
        for item in confirmed:
            if item[2] == "rule_corruption":
                if len(picked) >= args.max_rejected_per_chosen:
                    picked[-1] = item
                else:
                    picked.append(item)
                break

    pairs = []
    for j, (res, cl, source, tag, basis, margin, spec, verdict) in enumerate(picked):
        pairs.append({
            "pair_id": f"{galaxy_id}__{node['node_id']}_vs_synth{j}_{tag}",
            "galaxy_id": galaxy_id,
            "parent_node_id": node.get("parent_id"),
            "depth": depth,
            "chosen_node_id": node["node_id"],
            "chosen_coarse_label": chosen_label,
            "chosen_spec": chosen_spec,
            "chosen_metrics": chosen_metrics,
            "rejected_node_id": f"synth_{j}_{tag}",
            "rejected_coarse_label": cl,
            "rejected_spec": spec,
            "rejected_metrics": res.get("metrics", {}),
            "rejected_residual_path": res.get("residual_path"),
            "rejected_feedme_path": res.get("feedme_path"),
            "parent_metrics": parent.get("metrics", {}),
            "rejected_source": source,
            "generating_model": args.weak_model if source == "weak_model" else "rule",
            "objective_basis": basis,
            "objective_margin": margin,
            "vlm_verdict": verdict,
        })
    stats["pairs_emitted"] += len(pairs)
    return pairs


def _iter_accepted_nodes(traj):
    """产出 (node, node_map)：accepted、非 root、success 的节点（=SFT chosen）。"""
    nodes = traj.get("nodes", [])
    node_map = {n["node_id"]: n for n in nodes}
    for n in nodes:
        if n.get("parent_id") is None:
            continue
        if not n.get("is_accepted"):
            continue
        if n.get("status") is not None and n["status"] != "success":
            continue
        yield n, node_map


async def main_async(args):
    env = GalfitEnv(base_project_dir=os.path.abspath(args.gadotti_root), max_iter=args.max_iter)
    input_dir = os.path.abspath(args.input)
    sandbox_root = os.path.join(input_dir, "_dpo_sandbox")

    traj_files = sorted(glob.glob(os.path.join(input_dir, "**", "*_trajectory.json"), recursive=True))
    if not traj_files:
        print(f"[ERROR] {input_dir} 下未找到 trajectory.json")
        return

    stats = {k: 0 for k in [
        "chosen_processed", "candidates_generated", "cand_crashed", "cand_ssim_or_other",
        "cand_not_worse", "passed_objective", "vlm_rejected_as_false_neg", "pairs_emitted",
        "skip_no_parent", "skip_no_parent_feedme", "skip_no_chosen_spec",
        "weak_prompt_tokens", "weak_completion_tokens", "vlm_prompt_tokens", "vlm_completion_tokens",
    ]}

    all_pairs = []
    for tf in traj_files:
        try:
            with open(tf, "r", encoding="utf-8") as f:
                traj = json.load(f)
        except Exception as e:
            print(f"  [WARN] 跳过 {tf}: {e}")
            continue
        galaxy_id = traj.get("galaxy_id", os.path.basename(os.path.dirname(tf)))
        print(f"\n=== {galaxy_id} ===")
        for node, node_map in _iter_accepted_nodes(traj):
            stats["chosen_processed"] += 1
            pairs = await process_chosen(env, node, node_map, galaxy_id, sandbox_root, args, stats)
            all_pairs.extend(pairs)
            print(f"  {node['node_id']} ({node.get('action_from_parent',{}).get('coarse_label','?')}): "
                  f"+{len(pairs)} rejected")

    # ---- 写出 ----
    out_jsonl = os.path.join(input_dir, "all_dpo_synthetic.jsonl")
    with open(out_jsonl, "w", encoding="utf-8") as f:
        for p in all_pairs:
            f.write(json.dumps(p, ensure_ascii=False) + "\n")

    IN, OUT = 2.0 / 1e6, 12.0 / 1e6  # 注：flash 实际更便宜，此处按 pro 上界估
    report = {
        "num_trajectory_files": len(traj_files),
        "config": {
            "weak_model": args.weak_model, "vlm_reward_model": args.vlm_reward_model,
            "num_candidates": args.num_candidates, "enable_rule_corruption": args.enable_rule_corruption,
            "max_rejected_per_chosen": args.max_rejected_per_chosen,
            "bic_margin": BIC_MARGIN, "chi2_rel_margin": CHI2_REL_MARGIN,
        },
        "stats": stats,
        "funnel": {
            "chosen_processed": stats["chosen_processed"],
            "candidates_generated": stats["candidates_generated"],
            "passed_objective_filter": stats["passed_objective"],
            "dropped_not_worse": stats["cand_not_worse"],
            "dropped_vlm_false_neg": stats["vlm_rejected_as_false_neg"],
            "pairs_emitted": stats["pairs_emitted"],
        },
        "token_upper_bound_usd": round(
            (stats["weak_prompt_tokens"] + stats["vlm_prompt_tokens"]) * IN +
            (stats["weak_completion_tokens"] + stats["vlm_completion_tokens"]) * OUT, 2),
    }
    out_report = os.path.join(input_dir, "dpo_synthetic_report.json")
    with open(out_report, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    print("\n" + "=" * 60)
    print(f"chosen 处理: {stats['chosen_processed']}, 候选生成: {stats['candidates_generated']}")
    print(f"过客观筛: {stats['passed_objective']} (更好/持平丢弃 {stats['cand_not_worse']}, 崩溃 {stats['cand_crashed']})")
    print(f"vlm 剔除假负: {stats['vlm_rejected_as_false_neg']}")
    print(f"最终 DPO 对: {stats['pairs_emitted']}")
    print(f"Token 成本(pro上界估): ${report['token_upper_bound_usd']}")
    print(f"\n写入: {out_jsonl}\n写入: {out_report}")


def main():
    ap = argparse.ArgumentParser(description="从 SFT 轨迹构造 DPO 偏好对（弱模型+GALFIT客观筛+vlm二次确认）")
    ap.add_argument("--input", required=True, help="实验 output 目录（含 *_trajectory.json）")
    ap.add_argument("--gadotti-root", required=True, help="GalfitEnv base_project_dir（解析 feedme 相对路径）")
    ap.add_argument("--weak-model", required=True, help="弱模型名（api.road2all.com 上的便宜模型）")
    ap.add_argument("--vlm-reward-model", required=True, help="第二道确认模型名（建议 flash）")
    ap.add_argument("--num-candidates", type=int, default=NUM_CANDIDATES)
    ap.add_argument("--max-rejected-per-chosen", type=int, default=MAX_REJECTED_PER_CHOSEN)
    ap.add_argument("--enable-rule-corruption", action="store_true", default=ENABLE_RULE_CORRUPTION)
    ap.add_argument("--no-rule-corruption", dest="enable_rule_corruption", action="store_false")
    ap.add_argument("--keep-crash-as-hard-neg", action="store_true", default=KEEP_CRASH_AS_HARD_NEG)
    ap.add_argument("--max-iter", type=int, default=100)
    args = ap.parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
