"""
把 E7 系列 trajectory 转成 LLaMA-Factory 多模态 SFT 数据（单轮，1 图 + 文字历史）。

设计（见 训练数据生成方案.md「模型训练与测评方案」+ plan）：
  - 本轮只做**单轮 SFT**：每个被采纳节点 → 一条独立样本，与 E7 生成/MCP main/当前 pipeline 推理零错配。
  - 忠实重建训练对话：复用 pipeline 里同一套 prompt 构造，保证 SFT 输入 == 推理输入：
      system   = data_gen.vlm_proposal.SYSTEM_PROMPT
      user     = "<image>\n" + build_proposal_prompt(...)   （+ 父节点残差合成图）
      assistant= 节点 action_from_parent["full_response"]（完整 CoT + ```json``` 规格块）
  - E7 生成配置（重建须一致）：expert_gt=None（USE_EXPERT_HINT_FOR_VLM=False）、
    history 全开（USE_HISTORY_FOR_VLM=True, VLM_HISTORY_MAX_STEPS=0）、单轮、MAX_STEPS=15。
  - 按物理星系切分：排除 test_galaxies.json 里的测评星系，其余按 --val-ratio 切 train/val（seed=42）。
  - 同物理星系的多条轨迹全部保留（增广）。

用法：
    python -m data_gen.convert_sft_to_llamafactory \
        --input-dir output/E7_full__.../ \
        --test-galaxies output/E7_full__.../test_galaxies.json \
        --out-dir train/llamafactory/data \
        --max-steps 15 --val-ratio 0.01

    # 残差图路径在 A6000 上移动过时做前缀重映射：
        --image-root-from /old/abs/prefix --image-root-to /new/abs/prefix
"""

import argparse
import glob
import json
import os
import random

# 复用现有实现（均为 import，不改原文件）
from data_gen.vlm_proposal import build_proposal_prompt, SYSTEM_PROMPT
from data_gen.reward import read_summary_md
from data_gen.dataset_utils import _to_physical_id
from data_gen.extract_training_data import _is_mh_accepted
from simulator_env.galfit_actions import parse_components_from_feedme


# ----------------------------------------------------------------------
# 照抄 pipeline.py:213-254 的 _fmt_metric / _build_history_summary，
# 改成在单条 trajectory dict 上运行（原为嵌套函数，不可 import）。
# 逻辑须与生成时严格一致，否则重建的 prompt 与 assistant 回复错配。
# ----------------------------------------------------------------------
def _fmt_metric(v):
    return f"{v:.4f}" if isinstance(v, (int, float)) else "NA"


def _build_history_summary_replica(parent_node: dict, tree: dict, history_max_steps: int = 0) -> str:
    """沿父链路生成历史轮次摘要：每步采纳动作/指标 + 同层被拒尝试。照抄自 pipeline._build_history_summary。"""
    nodes = tree.get("nodes", [])
    by_id = {n["node_id"]: n for n in nodes}
    children_by_parent = {}
    for n in nodes:
        children_by_parent.setdefault(n.get("parent_id"), []).append(n)

    # 构造 root→parent 链
    chain, cur = [], parent_node
    seen = set()
    while cur is not None and cur.get("node_id") not in seen:
        seen.add(cur.get("node_id"))
        chain.append(cur)
        cur = by_id.get(cur.get("parent_id"))
    chain.reverse()

    if history_max_steps and history_max_steps > 0:
        chain = chain[-history_max_steps:]

    lines = []
    for n in chain:
        depth = n.get("depth", 0)
        m = n.get("metrics", {})
        metric_str = f"chi2_nu={_fmt_metric(m.get('chi2_nu'))}, BIC={_fmt_metric(m.get('bic'))}"
        act = n.get("action_from_parent")
        if not act:
            lines.append(f"- 第{depth}步(根节点): {metric_str}")
        else:
            label = act.get("coarse_label", "?")
            note = (act.get("target") or act.get("reasoning") or "").strip().replace("\n", " ")[:50]
            mh_tag = "(退火接受,质量未改善)" if n.get("mh_accepted") else ""
            lines.append(f"- 第{depth}步 采纳[{label}]{mh_tag} → {metric_str}{('；' + note) if note else ''}")
        sibs = children_by_parent.get(n.get("parent_id"), [])
        rej = [s for s in sibs if s.get("node_id") != n.get("node_id") and not s.get("is_accepted")]
        if rej:
            rlabels = [(s.get("action_from_parent") or {}).get("coarse_label", "?") for s in rej]
            lines.append(f"    (同层被拒: {rlabels})")
    return "\n".join(lines) if lines else ""


# ----------------------------------------------------------------------
def _count_sersic(components: list) -> int:
    """与 pipeline._count_sersic_components 口径一致：数 sersic 成分，min 1。"""
    return max(1, sum(1 for c in components if c.get("model") == "sersic"))


def _load_trajectories(input_dir: str) -> list:
    files = sorted(glob.glob(os.path.join(input_dir, "**", "*_trajectory.json"), recursive=True))
    trajs = []
    for f in files:
        try:
            with open(f, "r", encoding="utf-8") as fh:
                t = json.load(fh)
            t["_source_file"] = f
            trajs.append(t)
        except Exception as e:
            print(f"  [WARN] 跳过无法解析的 trajectory: {f}: {e}")
    return trajs


def _selected_nodes(tree: dict):
    """节点选择规则与 extract_training_data.extract_from_trajectory 完全一致。"""
    for node in tree.get("nodes", []):
        if node.get("parent_id") is None or node.get("depth", 0) == 0:
            continue
        if not node.get("is_accepted"):
            continue
        if node.get("status") is not None and node["status"] != "success":
            continue
        if _is_mh_accepted(node):
            continue
        yield node


def _assistant_target(action: dict):
    """优先用完整原始回复(full_response, 含 CoT+JSON)；缺失则回退序列化 spec。返回 (text, used_fallback)。"""
    fr = action.get("full_response")
    if fr and isinstance(fr, str) and fr.strip():
        return fr, False
    # 回退：从 action 里拼一个 ```json``` 规格块
    keys = ("components", "sky", "target", "confidence", "reasoning")
    payload = {k: action[k] for k in keys if k in action}
    if not payload.get("components"):
        return None, True  # 无可用输出 → 上层跳过
    body = json.dumps(payload, ensure_ascii=False, indent=2)
    return f"```json\n{body}\n```", True


def _remap_image(path: str, root_from: str, root_to: str) -> str:
    if not path:
        return path
    if root_from and root_to:
        return path.replace(root_from, root_to)
    return path


def build_samples(trajs: list, test_pids: set, max_steps: int, root_from: str, root_to: str):
    """遍历所有 trajectory，产出 (physical_id, sample_dict) 列表 + 统计。"""
    samples = []  # [(physical_id, sample)]
    stats = {
        "trajectories": len(trajs),
        "selected_nodes": 0,
        "emitted": 0,
        "skip_test_galaxy": 0,
        "skip_no_assistant": 0,
        "skip_no_image": 0,
        "fallback_spec": 0,
        "missing_summary": 0,
        "parse_component_fail": 0,
    }

    for tree in trajs:
        gid = tree.get("galaxy_id", "unknown")
        pid = _to_physical_id(gid)
        node_map = {n["node_id"]: n for n in tree.get("nodes", [])}

        for node in _selected_nodes(tree):
            stats["selected_nodes"] += 1

            if pid in test_pids:
                stats["skip_test_galaxy"] += 1
                continue

            parent = node_map.get(node.get("parent_id"))
            if not parent:
                stats["skip_no_image"] += 1
                continue

            # 输入图 = 父节点残差合成图（原图|模型|2D残差|1D profile）
            image_path = _remap_image(parent.get("residual_path"), root_from, root_to)
            if not image_path:
                stats["skip_no_image"] += 1
                continue

            # assistant 目标
            action = node.get("action_from_parent") or {}
            assistant, used_fallback = _assistant_target(action)
            if assistant is None:
                stats["skip_no_assistant"] += 1
                continue
            if used_fallback:
                stats["fallback_spec"] += 1

            # 重建 user prompt（与生成时一致）
            try:
                current_components = parse_components_from_feedme(parent.get("feedme_path"))
            except Exception:
                current_components = []
                stats["parse_component_fail"] += 1

            summary_path = parent.get("summary_path")
            summary_content = "(参数摘要不可用)"
            if summary_path and os.path.exists(summary_path):
                try:
                    summary_content = read_summary_md(summary_path)
                except Exception:
                    stats["missing_summary"] += 1
            else:
                stats["missing_summary"] += 1

            history_summary = _build_history_summary_replica(parent, tree, history_max_steps=0)
            user_text = build_proposal_prompt(
                summary_content=summary_content,
                step=node.get("step", node.get("depth", 1)),
                max_steps=max_steps,
                num_sersic=_count_sersic(current_components),
                expert_gt=None,                      # E7: USE_EXPERT_HINT_FOR_VLM=False
                current_components=current_components,
                history_summary=history_summary,     # E7: USE_HISTORY_FOR_VLM=True
            )

            sample = {
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": "<image>\n" + user_text},
                    {"role": "assistant", "content": assistant},
                ],
                "images": [os.path.abspath(image_path)],
            }
            samples.append((pid, sample))
            stats["emitted"] += 1

    return samples, stats


def split_train_val(samples: list, val_ratio: float, seed: int):
    """按物理星系切 train/val，同一星系不跨侧。"""
    pids = sorted({pid for pid, _ in samples})
    random.seed(seed)
    random.shuffle(pids)
    n_val = int(round(len(pids) * val_ratio))
    n_val = max(1, n_val) if (val_ratio > 0 and len(pids) >= 2) else 0
    val_pids = set(pids[:n_val])
    train = [s for pid, s in samples if pid not in val_pids]
    val = [s for pid, s in samples if pid in val_pids]
    return train, val, val_pids


def _write_jsonl(rows, path):
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def main():
    ap = argparse.ArgumentParser(description="trajectory → LLaMA-Factory 多模态 SFT（单轮）")
    ap.add_argument("--input-dir", required=True, help="实验 output 目录（递归扫 *_trajectory.json）")
    ap.add_argument("--test-galaxies", default=None, help="test_galaxies.json（排除测评物理星系）")
    ap.add_argument("--out-dir", default=None, help="输出目录（默认=input-dir）")
    ap.add_argument("--max-steps", type=int, default=15, help="prompt 里的最大步数（E7=15）")
    ap.add_argument("--val-ratio", type=float, default=0.01, help="从 train 里按物理星系切验证集比例")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--image-root-from", default=None, help="残差图路径前缀重映射：旧前缀")
    ap.add_argument("--image-root-to", default=None, help="残差图路径前缀重映射：新前缀")
    args = ap.parse_args()

    in_dir = os.path.abspath(args.input_dir)
    out_dir = os.path.abspath(args.out_dir) if args.out_dir else in_dir
    os.makedirs(out_dir, exist_ok=True)

    test_pids = set()
    if args.test_galaxies and os.path.isfile(args.test_galaxies):
        with open(args.test_galaxies, "r", encoding="utf-8") as f:
            obj = json.load(f)
        test_pids = set(obj["test_physical_ids"] if isinstance(obj, dict) else obj)
        print(f"排除测评物理星系: {len(test_pids)} 个 ({args.test_galaxies})")

    trajs = _load_trajectories(in_dir)
    print(f"扫描到 {len(trajs)} 个 trajectory")
    if not trajs:
        print("未找到 trajectory，退出。")
        return

    samples, stats = build_samples(
        trajs, test_pids, args.max_steps, args.image_root_from, args.image_root_to)
    if not samples:
        print("没有产出任何样本，退出。")
        print(json.dumps(stats, ensure_ascii=False, indent=2))
        return

    train, val, val_pids = split_train_val(samples, args.val_ratio, args.seed)

    _write_jsonl(train, os.path.join(out_dir, "galaxy_sft_train.jsonl"))
    _write_jsonl(val, os.path.join(out_dir, "galaxy_sft_val.jsonl"))

    report = {
        **stats,
        "train_samples": len(train),
        "val_samples": len(val),
        "num_physical_galaxies": len({pid for pid, _ in samples}),
        "num_val_galaxies": len(val_pids),
        "seed": args.seed,
        "val_ratio": args.val_ratio,
        "max_steps": args.max_steps,
    }
    with open(os.path.join(out_dir, "convert_report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print("\n" + "=" * 56)
    print("  转换完成")
    print("=" * 56)
    for k, v in report.items():
        print(f"  {k}: {v}")
    print(f"\n输出目录: {out_dir}")
    print("  galaxy_sft_train.jsonl / galaxy_sft_val.jsonl / convert_report.json")


if __name__ == "__main__":
    main()
