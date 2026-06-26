"""
从实验output目录中提取SFT/DPO训练数据。

用法:
    python -m data_gen.extract_training_data --input output/<strategy_folder>/
    python -m data_gen.extract_training_data --input output/<strategy_folder>/ --output-dir ./extracted/
"""

import argparse
import json
import os
import glob
from collections import defaultdict


def load_trajectories(input_dir):
    """扫描input_dir下所有子目录中的 *_trajectory.json，返回列表。"""
    pattern = os.path.join(input_dir, "**", "*_trajectory.json")
    files = glob.glob(pattern, recursive=True)
    trajectories = []
    for f in sorted(files):
        try:
            with open(f, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            data["_source_file"] = f
            trajectories.append(data)
        except Exception as e:
            print(f"  [WARN] 跳过无法解析的文件 {f}: {e}")
    return trajectories


def _is_mh_accepted(node):
    """判断节点是否为MH退火接受（is_accepted=True但delta_R<0）。"""
    if node.get("mh_accepted") is not None:
        return bool(node["mh_accepted"])
    return bool(node.get("is_accepted")) and (node.get("delta_R", 0) < 0)


def _get_coarse_label(node):
    """从action_from_parent中提取coarse_label。"""
    act = node.get("action_from_parent")
    if not act:
        return "unknown"
    if "coarse_label" in act:
        return act["coarse_label"]
    if "structural" in act:
        s = act["structural"]
        if s.startswith("add_"):
            return s
        return "modify"
    t = act.get("type", "C")
    if t == "A":
        ct = act.get("component_type", "unknown")
        return f"add_{ct}"
    if t == "B":
        return "delete"
    return "modify"


def _trace_real_depth(node_id, node_map):
    """沿parent链向上追溯，统计真实改善的步数。"""
    real = 0
    cur = node_map.get(node_id)
    while cur and cur.get("parent_id"):
        if cur.get("delta_R", 0) >= 0 and cur.get("is_accepted"):
            real += 1
        cur = node_map.get(cur["parent_id"])
    return real


def extract_from_trajectory(traj):
    """从单个trajectory提取SFT样本和DPO配对。"""
    galaxy_id = traj.get("galaxy_id", "unknown")
    nodes = traj.get("nodes", [])

    node_map = {n["node_id"]: n for n in nodes}

    sft_samples = []
    children_by_parent = defaultdict(list)

    for node in nodes:
        if node.get("parent_id") is None or node.get("depth", 0) == 0:
            continue
        children_by_parent[node["parent_id"]].append(node)

        if not node.get("is_accepted"):
            continue
        if node.get("status") is not None and node["status"] != "success":
            continue

        mh = _is_mh_accepted(node)
        parent = node_map.get(node["parent_id"], {})
        act = node.get("action_from_parent", {})

        real_depth = _trace_real_depth(node["node_id"], node_map)

        sample = {
            "sample_id": f"{galaxy_id}__{node['node_id']}",
            "galaxy_id": galaxy_id,
            "node_id": node["node_id"],
            "parent_node_id": node["parent_id"],
            "depth": node.get("depth", 0),
            "step": node.get("step", node.get("depth", 0)),
            "coarse_label": _get_coarse_label(node),
            "mh_accepted": mh,
            "real_improvement_depth": real_depth,
            "spec": act.get("spec"),
            "parent_metrics": parent.get("metrics", {}),
            "child_metrics": node.get("metrics", {}),
            "delta_R": node.get("delta_R", 0),
            "reward_detail": node.get("reward_detail", {}),
            "feedme_path": node.get("feedme_path"),
            "residual_path": node.get("residual_path"),
            "parent_residual_path": parent.get("residual_path"),
        }
        sft_samples.append(sample)

    dpo_pairs = []
    for parent_id, children in children_by_parent.items():
        winners = [
            c for c in children
            if c.get("is_accepted")
            and not _is_mh_accepted(c)
            and (c.get("status") is None or c["status"] == "success")
        ]
        losers = [
            c for c in children
            if not c.get("is_accepted")
            and c.get("status") == "success"
        ]

        if not winners or not losers:
            continue

        parent = node_map.get(parent_id, {})
        for w in winners:
            w_act = w.get("action_from_parent", {})
            for l in losers:
                l_act = l.get("action_from_parent", {})
                pair = {
                    "pair_id": f"{galaxy_id}__{w['node_id']}_vs_{l['node_id']}",
                    "galaxy_id": galaxy_id,
                    "parent_node_id": parent_id,
                    "depth": w.get("depth", 0),
                    "chosen_node_id": w["node_id"],
                    "chosen_coarse_label": _get_coarse_label(w),
                    "chosen_spec": w_act.get("spec"),
                    "chosen_delta_R": w.get("delta_R", 0),
                    "chosen_metrics": w.get("metrics", {}),
                    "rejected_node_id": l["node_id"],
                    "rejected_coarse_label": _get_coarse_label(l),
                    "rejected_spec": l_act.get("spec"),
                    "rejected_delta_R": l.get("delta_R", 0),
                    "rejected_metrics": l.get("metrics", {}),
                    "parent_metrics": parent.get("metrics", {}),
                }
                dpo_pairs.append(pair)

    return sft_samples, dpo_pairs


def build_report(all_sft, all_dpo, num_files, num_galaxies):
    """生成统计报告字典。"""
    total_sft = len(all_sft)
    mh_count = sum(1 for s in all_sft if s["mh_accepted"])
    real_count = total_sft - mh_count

    by_depth = defaultdict(lambda: {"total": 0, "real": 0, "mh": 0})
    for s in all_sft:
        d = s["depth"]
        by_depth[d]["total"] += 1
        if s["mh_accepted"]:
            by_depth[d]["mh"] += 1
        else:
            by_depth[d]["real"] += 1

    by_label = defaultdict(int)
    for s in all_sft:
        by_label[s["coarse_label"]] += 1

    by_real_depth = defaultdict(int)
    for s in all_sft:
        by_real_depth[s["real_improvement_depth"]] += 1

    dpo_by_depth = defaultdict(int)
    for p in all_dpo:
        dpo_by_depth[p["depth"]] += 1

    scale_factor = 926 / num_galaxies if num_galaxies > 0 else 0

    report = {
        "num_trajectory_files": num_files,
        "num_galaxies": num_galaxies,
        "sft": {
            "total": total_sft,
            "real_improvement": real_count,
            "mh_accepted": mh_count,
            "by_depth": {str(k): v for k, v in sorted(by_depth.items())},
            "by_coarse_label": dict(sorted(by_label.items(), key=lambda x: -x[1])),
            "by_real_improvement_depth": {str(k): v for k, v in sorted(by_real_depth.items())},
        },
        "dpo": {
            "total": len(all_dpo),
            "by_depth": {str(k): v for k, v in sorted(dpo_by_depth.items())},
        },
        "full_scale_estimate": {
            "target_galaxies": 926,
            "sft_estimate": round(total_sft * scale_factor),
            "dpo_estimate": round(len(all_dpo) * scale_factor),
        },
    }
    return report


def print_report(report):
    """将统计报告以可读格式打印。"""
    print("\n" + "=" * 60)
    print("  训练数据提取报告")
    print("=" * 60)
    print(f"扫描trajectory文件数: {report['num_trajectory_files']}")
    print(f"有效星系数: {report['num_galaxies']}")

    sft = report["sft"]
    print(f"\n--- SFT样本统计 ---")
    print(f"总数: {sft['total']}")
    real_pct = (sft['real_improvement'] / sft['total'] * 100) if sft['total'] > 0 else 0
    mh_pct = (sft['mh_accepted'] / sft['total'] * 100) if sft['total'] > 0 else 0
    print(f"  真实改善: {sft['real_improvement']} ({real_pct:.1f}%)")
    print(f"  MH退火接受: {sft['mh_accepted']} ({mh_pct:.1f}%)")

    print(f"\n按depth分布:")
    for d, v in sorted(sft["by_depth"].items(), key=lambda x: int(x[0])):
        print(f"  depth={d}: {v['total']} (真实{v['real']}, MH{v['mh']})")

    print(f"\n按coarse_label分布:")
    for label, count in sft["by_coarse_label"].items():
        print(f"  {label}: {count}")

    print(f"\n按真实改善深度分布:")
    for d, count in sorted(sft["by_real_improvement_depth"].items(), key=lambda x: int(x[0])):
        print(f"  real_depth={d}: {count}")

    dpo = report["dpo"]
    print(f"\n--- DPO配对统计 ---")
    print(f"总数: {dpo['total']}")
    print(f"按depth分布:")
    for d, count in sorted(dpo["by_depth"].items(), key=lambda x: int(x[0])):
        print(f"  depth={d}: {count}")

    est = report["full_scale_estimate"]
    print(f"\n--- 全量估算({est['target_galaxies']}个星系) ---")
    print(f"SFT预估: ~{est['sft_estimate']:,}")
    print(f"DPO预估: ~{est['dpo_estimate']:,}")
    print("=" * 60)


def main():
    parser = argparse.ArgumentParser(description="从实验output提取SFT/DPO训练数据")
    parser.add_argument("--input", required=True, help="实验output目录路径")
    parser.add_argument("--output-dir", default=None, help="输出目录(默认=input)")
    args = parser.parse_args()

    input_dir = os.path.abspath(args.input)
    output_dir = os.path.abspath(args.output_dir) if args.output_dir else input_dir

    print(f"扫描目录: {input_dir}")
    trajectories = load_trajectories(input_dir)
    print(f"找到 {len(trajectories)} 个trajectory文件")

    if not trajectories:
        print("未找到任何trajectory文件，退出。")
        return

    all_sft = []
    all_dpo = []
    num_galaxies = 0

    for traj in trajectories:
        sft, dpo = extract_from_trajectory(traj)
        if sft or dpo:
            num_galaxies += 1
        all_sft.extend(sft)
        all_dpo.extend(dpo)

    report = build_report(all_sft, all_dpo, len(trajectories), num_galaxies)
    print_report(report)

    os.makedirs(output_dir, exist_ok=True)

    sft_path = os.path.join(output_dir, "all_sft.jsonl")
    with open(sft_path, "w", encoding="utf-8") as f:
        for s in all_sft:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")
    print(f"\nSFT样本已写入: {sft_path}")

    dpo_path = os.path.join(output_dir, "all_dpo.jsonl")
    with open(dpo_path, "w", encoding="utf-8") as f:
        for p in all_dpo:
            f.write(json.dumps(p, ensure_ascii=False) + "\n")
    print(f"DPO配对已写入: {dpo_path}")

    report_path = os.path.join(output_dir, "extraction_report.json")
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    print(f"统计报告已写入: {report_path}")


if __name__ == "__main__":
    main()
