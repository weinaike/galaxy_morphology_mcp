"""
把测试星系的 trajectory 转成评测数据（LLaMA-Factory 格式 + GT spec/label）。

与 data_gen/convert_sft_to_llamafactory.py 相同的 prompt 重建逻辑，
但只保留 test_galaxies.json 中的测试星系，并把 GT 信息存到每条样本的 _gt 字段。

用法（在 A6000 的 GalDecomp_Gen 根目录）：
    source /media/data/anaconda3/etc/profile.d/conda.sh && conda activate llama-factory
    python -m eval.prepare_eval_data \
        --input-dir output/E7_full__vlm_proposal_gemini-3.1-pro-preview_vlm_reward_gemini-3.1-pro-preview_hist \
        --test-galaxies output/E7_full__vlm_proposal_gemini-3.1-pro-preview_vlm_reward_gemini-3.1-pro-preview_hist/test_galaxies.json \
        --out-dir eval/eval_data
"""

import argparse
import json
import os
import sys

from data_gen.convert_sft_to_llamafactory import (
    _load_trajectories,
    _selected_nodes,
    _build_history_summary_replica,
    _count_sersic,
    _assistant_target,
    _remap_image,
    _write_jsonl,
)
from data_gen.vlm_proposal import build_proposal_prompt, SYSTEM_PROMPT
from data_gen.reward import read_summary_md
from data_gen.dataset_utils import _to_physical_id
from data_gen.extract_training_data import _is_mh_accepted
from simulator_env.galfit_actions import parse_components_from_feedme


def build_test_samples(trajs, test_pids, max_steps, root_from, root_to):
    """与 convert 的 build_samples 相同逻辑，但只保留 test 星系，并附带 GT 信息。"""
    samples = []
    stats = {"trajectories": len(trajs), "selected_nodes": 0, "emitted": 0,
             "skip_not_test": 0, "skip_no_assistant": 0, "skip_no_image": 0,
             "fallback_spec": 0, "missing_summary": 0, "parse_component_fail": 0}

    for tree in trajs:
        gid = tree.get("galaxy_id", "unknown")
        pid = _to_physical_id(gid)
        node_map = {n["node_id"]: n for n in tree.get("nodes", [])}

        for node in _selected_nodes(tree):
            stats["selected_nodes"] += 1
            if pid not in test_pids:
                stats["skip_not_test"] += 1
                continue

            parent = node_map.get(node.get("parent_id"))
            if not parent:
                stats["skip_no_image"] += 1
                continue

            image_path = _remap_image(parent.get("residual_path"), root_from, root_to)
            if not image_path:
                stats["skip_no_image"] += 1
                continue

            action = node.get("action_from_parent") or {}
            assistant, used_fallback = _assistant_target(action)
            if assistant is None:
                stats["skip_no_assistant"] += 1
                continue
            if used_fallback:
                stats["fallback_spec"] += 1

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
                expert_gt=None,
                current_components=current_components,
                history_summary=history_summary,
            )

            gt_spec = action.get("spec") or {}
            if not gt_spec and action.get("components"):
                gt_spec = {"components": action["components"],
                           "sky": action.get("sky"),
                           "target": action.get("target")}

            coarse_label = action.get("coarse_label", "unknown")

            sample = {
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": "<image>\n" + user_text},
                    {"role": "assistant", "content": assistant},
                ],
                "images": [os.path.abspath(image_path)],
                "_gt": {
                    "spec": gt_spec,
                    "coarse_label": coarse_label,
                    "galaxy_id": gid,
                    "physical_id": pid,
                    "node_id": node.get("node_id"),
                    "depth": node.get("depth"),
                    "parent_metrics": parent.get("metrics", {}),
                    "child_metrics": node.get("metrics", {}),
                },
            }
            samples.append(sample)
            stats["emitted"] += 1

    return samples, stats


def main():
    ap = argparse.ArgumentParser(description="准备测试星系的评测数据")
    ap.add_argument("--input-dir", required=True)
    ap.add_argument("--test-galaxies", required=True)
    ap.add_argument("--out-dir", default="eval/eval_data")
    ap.add_argument("--max-steps", type=int, default=15)
    ap.add_argument("--image-root-from", default=None)
    ap.add_argument("--image-root-to", default=None)
    args = ap.parse_args()

    out_dir = os.path.abspath(args.out_dir)
    os.makedirs(out_dir, exist_ok=True)

    with open(args.test_galaxies, "r", encoding="utf-8") as f:
        obj = json.load(f)
    test_pids = set(obj["test_physical_ids"] if isinstance(obj, dict) else obj)
    print(f"测试物理星系: {len(test_pids)} 个")

    trajs = _load_trajectories(os.path.abspath(args.input_dir))
    print(f"扫描到 {len(trajs)} 个 trajectory")

    samples, stats = build_test_samples(
        trajs, test_pids, args.max_steps, args.image_root_from, args.image_root_to)

    out_path = os.path.join(out_dir, "galaxy_eval_test.jsonl")
    _write_jsonl(samples, out_path)

    report = {**stats, "test_samples": len(samples),
              "test_physical_ids": sorted(test_pids)}
    report_path = os.path.join(out_dir, "eval_data_report.json")
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print(f"\n测试样本: {len(samples)} 条")
    print(f"输出: {out_path}")
    print(f"报告: {report_path}")
    for k, v in stats.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
