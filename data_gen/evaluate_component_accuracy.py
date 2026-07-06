"""
评估pipeline的成分预测准确率 (Component Prediction Accuracy)。

与main分支70%准确率同一指标：
- GT来源: Gadotti_params.json 中 re_disk>0 → Disk, re_bulge>0 → Bulge, re_bar>0 → Bar
- 预测来源: trajectory.json中最优叶节点的feedme解析成分

用法:
    python -m data_gen.evaluate_component_accuracy --input output/<experiment_dir>/ --data-dir <gadotti_data_path>
"""

import argparse
import json
import os
import re
import glob
from collections import defaultdict

# 只参与精确匹配比对的三类（与 Gadotti GT 对齐）；其余类别忽略（魏老师确认）
COMPARE_CLASSES = {"Disk", "Bulge", "Bar"}


def load_gadotti_gt(data_dir: str) -> dict:
    """扫描data_dir下所有Gadotti_params.json，返回 {galaxy_id: set(components)}。

    支持两种目录结构：
    1. 平铺: data_dir/<source>_Gadotti_params.json (文件内含source字段)
    2. 嵌套: data_dir/<band>/<obj>/Gadotti_params.json
    """
    gt = {}

    flat_files = glob.glob(os.path.join(data_dir, "*_Gadotti_params.json"))
    if flat_files:
        for path in flat_files:
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                continue
            source = data.get("source", "")
            if not source:
                source = os.path.basename(path).replace("_Gadotti_params.json", "")
            galaxy_id = f"SDSS_rband_{source}"

            components = set()
            if data.get("re_disk", 0.0) > 0:
                components.add("Disk")
            if data.get("re_bulge", 0.0) > 0:
                components.add("Bulge")
            if data.get("re_bar", 0.0) > 0:
                components.add("Bar")
            gt[galaxy_id] = components
        return gt

    pattern = os.path.join(data_dir, "**", "Gadotti_params.json")
    for path in glob.glob(pattern, recursive=True):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            continue

        parent_dir = os.path.dirname(path)
        obj_name = os.path.basename(parent_dir)
        band_dir = os.path.basename(os.path.dirname(parent_dir))
        galaxy_id = f"{band_dir}_{obj_name}"

        components = set()
        if data.get("re_disk", 0.0) > 0:
            components.add("Disk")
        if data.get("re_bulge", 0.0) > 0:
            components.add("Bulge")
        if data.get("re_bar", 0.0) > 0:
            components.add("Bar")
        gt[galaxy_id] = components

    return gt


def parse_feedme_components(feedme_path: str) -> set:
    """解析feedme文件，返回预测的物理成分集合。

    分类逻辑：
    - sersic n≤1.5 且 Re较大 → Disk
    - sersic n>1.5 → Bulge
    - sersic n≈0.5 且 q<0.5 → Bar (如果有n≤0.8 且 q<0.5)
    - psf → PSF/Nucleus (不参与Gadotti三成分对比)
    - expdisk → Disk
    - sky → 跳过

    注意：这里用启发式规则来区分disk/bulge/bar。
    实际上我们的pipeline的action_from_parent已经明确标记了add_disk/add_bar等。
    """
    if not feedme_path or not os.path.exists(feedme_path):
        return set()

    try:
        with open(feedme_path, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception:
        return set()

    components = set()
    blocks = re.split(r'\n(?=\s*0\))', content)

    for block in blocks:
        model_match = re.match(r'\s*0\)\s+(\w+)', block)
        if not model_match:
            continue
        model_type = model_match.group(1).lower()

        if model_type == "sky":
            continue
        if model_type == "psf":
            continue
        if model_type == "expdisk":
            components.add("Disk")
            continue

        if model_type == "sersic":
            n_match = re.search(r'(?m)^\s*5\)\s+([+-]?\d+\.?\d*)', block)
            q_match = re.search(r'(?m)^\s*9\)\s+([+-]?\d+\.?\d*)', block)
            re_match = re.search(r'(?m)^\s*4\)\s+([+-]?\d+\.?\d*)', block)

            n_val = float(n_match.group(1)) if n_match else 4.0
            q_val = float(q_match.group(1)) if q_match else 0.8
            re_val = float(re_match.group(1)) if re_match else 10.0

            if n_val <= 0.8 and q_val < 0.5:
                components.add("Bar")
            elif n_val <= 1.5:
                components.add("Disk")
            else:
                components.add("Bulge")

    return components


def predict_from_trajectory(traj: dict) -> set:
    """从trajectory中提取最优节点的成分预测。

    策略1: 使用action_from_parent的coarse_label追溯最优路径包含的所有add_操作
    策略2: 直接解析最优叶节点的feedme

    这里两种策略结合：优先使用action追溯（更准确），fallback到feedme解析。
    """
    nodes = traj.get("nodes", [])
    if not nodes:
        return set()

    node_map = {n["node_id"]: n for n in nodes}

    accepted_leaves = []
    for node in nodes:
        if not node.get("is_accepted"):
            continue
        if node.get("parent_id") is None:
            continue
        is_leaf = True
        for other in nodes:
            if other.get("parent_id") == node["node_id"] and other.get("is_accepted"):
                is_leaf = False
                break
        if is_leaf:
            accepted_leaves.append(node)

    if not accepted_leaves:
        return set()

    best_leaf = min(accepted_leaves, key=lambda n: n.get("metrics", {}).get("chi2_nu", 999))

    components = set()
    cur = best_leaf
    while cur and cur.get("parent_id") is not None:
        act = cur.get("action_from_parent", {})
        if act:
            cl = act.get("coarse_label") or act.get("structural", "")
            if cl == "add_disk":
                components.add("Disk")
            elif cl == "add_bar":
                components.add("Bar")
            elif cl == "add_bulge":
                components.add("Bulge")
        cur = node_map.get(cur.get("parent_id"))

    root = node_map.get("node_0_root")
    if root and root.get("feedme_path"):
        root_comps = parse_feedme_components(root["feedme_path"])
        components.update(root_comps)

    if not components and best_leaf.get("feedme_path"):
        components = parse_feedme_components(best_leaf["feedme_path"])

    return components


def _select_best_leaf(traj: dict):
    """选最优叶节点：accepted 叶节点里 chi2_nu 最小；无则回退 root。返回 node 或 None。"""
    nodes = traj.get("nodes", [])
    if not nodes:
        return None
    node_map = {n["node_id"]: n for n in nodes}

    accepted_leaves = []
    for node in nodes:
        if not node.get("is_accepted") or node.get("parent_id") is None:
            continue
        is_leaf = not any(
            o.get("parent_id") == node["node_id"] and o.get("is_accepted") for o in nodes
        )
        if is_leaf:
            accepted_leaves.append(node)

    if accepted_leaves:
        return min(accepted_leaves, key=lambda n: n.get("metrics", {}).get("chi2_nu", 999))
    # 回退 root
    return node_map.get("node_0_root") or nodes[0]


def predict_from_trajectory_vlm(traj: dict, model_name: str, api_key: str = None,
                                cache: dict = None) -> tuple:
    """对齐 MCP 口径：对最优叶节点调 VLM 判定物理成分。

    Returns: (components_set, meta_dict)  —— components 为归一化后的全部类别集合。
    """
    from data_gen.component_labeler import label_components_via_vlm
    from data_gen.vlm_proposal import _derive_full_comparison_path

    galaxy_id = traj.get("galaxy_id", "")

    # 缓存命中直接返回
    if cache is not None and galaxy_id in cache:
        entry = cache[galaxy_id]
        return set(entry.get("components", [])), entry.get("meta", {})

    best_leaf = _select_best_leaf(traj)
    if not best_leaf:
        return set(), {"error": "no_node"}

    # 参数表内容
    summary_content = ""
    summary_path = best_leaf.get("summary_path")
    if summary_path and os.path.exists(summary_path):
        try:
            with open(summary_path, "r", encoding="utf-8") as f:
                summary_content = f.read()
        except Exception as e:
            summary_content = f"(参数表读取失败: {e})"

    # 完整对比图（原图|模型|残差|1D）
    comparison_img = _derive_full_comparison_path(best_leaf.get("residual_path"))
    if not comparison_img:
        return set(), {"error": f"no_comparison_image (residual_path={best_leaf.get('residual_path')})"}

    components, meta = label_components_via_vlm(
        summary_content=summary_content,
        comparison_image_path=comparison_img,
        model_name=model_name,
        api_key=api_key,
    )
    meta["best_leaf_node_id"] = best_leaf.get("node_id")
    meta["comparison_image"] = comparison_img

    if cache is not None:
        cache[galaxy_id] = {"components": sorted(components), "meta": meta}

    return components, meta


def evaluate(input_dir: str, data_dir: str, use_vlm: bool = True,
             model_name: str = "gemini-3.1-pro-preview", api_key: str = None,
             refresh: bool = False) -> dict:
    """执行评估，返回结果字典。

    use_vlm=True（默认，对齐 MCP 口径）：对最优叶节点调 VLM 判成分。
    use_vlm=False：回退旧 n 阈值 + action 回溯口径。
    """
    gt_all = load_gadotti_gt(data_dir)
    if not gt_all:
        print(f"[ERROR] 在 {data_dir} 下未找到Gadotti_params.json")
        return {}

    pattern = os.path.join(input_dir, "**", "*_trajectory.json")
    traj_files = sorted(glob.glob(pattern, recursive=True))

    if not traj_files:
        print(f"[ERROR] 在 {input_dir} 下未找到trajectory.json文件")
        return {}

    # VLM 预测缓存（重跑默认复用，避免重复付费；--refresh 忽略）
    cache_path = os.path.join(input_dir, "component_pred_cache.json")
    cache = {}
    if use_vlm and not refresh and os.path.exists(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                cache = json.load(f)
            print(f"  已加载预测缓存: {len(cache)} 条 ({cache_path})")
        except Exception:
            cache = {}

    results = []
    for tf in traj_files:
        try:
            with open(tf, "r", encoding="utf-8") as f:
                traj = json.load(f)
        except Exception as e:
            print(f"  [WARN] 跳过 {tf}: {e}")
            continue

        galaxy_id = traj.get("galaxy_id", "")
        if galaxy_id not in gt_all:
            alt_id = galaxy_id.replace("SDSS_gband_Plate", "SDSS_gband_Plate")
            if alt_id in gt_all:
                galaxy_id = alt_id
            else:
                print(f"  [WARN] {galaxy_id} 无GT数据，跳过")
                continue

        gt_components = gt_all[galaxy_id]  # GT 天然只含 Disk/Bulge/Bar

        raw_pred = None
        if use_vlm:
            pred_all, meta = predict_from_trajectory_vlm(traj, model_name, api_key, cache)
            raw_pred = sorted(pred_all)
            if meta.get("error"):
                print(f"  [WARN] {galaxy_id} VLM判定异常: {meta['error']}")
            # 只保留三类参与比对（其余忽略，魏老师确认）
            pred_components = pred_all & COMPARE_CLASSES
        else:
            pred_components = predict_from_trajectory(traj) & COMPARE_CLASSES

        gt_components = gt_components & COMPARE_CLASSES

        correct = gt_components == pred_components
        tp = gt_components & pred_components
        fp = pred_components - gt_components
        fn = gt_components - pred_components

        row = {
            "galaxy_id": galaxy_id,
            "gt": sorted(gt_components),
            "pred": sorted(pred_components),
            "correct": correct,
            "tp": sorted(tp),
            "fp": sorted(fp),
            "fn": sorted(fn),
        }
        if raw_pred is not None:
            row["pred_raw_all_classes"] = raw_pred  # 含 Nucleus/Companion 等，供复核
        results.append(row)

    # 落盘缓存
    if use_vlm:
        try:
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(cache, f, indent=2, ensure_ascii=False)
            print(f"  预测缓存已保存: {cache_path}")
        except Exception as e:
            print(f"  [WARN] 缓存保存失败: {e}")


    if not results:
        print("[ERROR] 无有效评估结果")
        return {}

    n_correct = sum(1 for r in results if r["correct"])
    n_total = len(results)
    accuracy = n_correct / n_total

    per_comp_stats = {}
    for comp in ["Disk", "Bulge", "Bar"]:
        gt_has = sum(1 for r in results if comp in r["gt"])
        pred_has = sum(1 for r in results if comp in r["pred"])
        tp_count = sum(1 for r in results if comp in r["tp"])
        precision = tp_count / pred_has if pred_has > 0 else 0
        recall = tp_count / gt_has if gt_has > 0 else 0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0
        per_comp_stats[comp] = {
            "gt_count": gt_has,
            "pred_count": pred_has,
            "tp": tp_count,
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1": round(f1, 4),
        }

    report = {
        "num_galaxies_evaluated": n_total,
        "exact_match_accuracy": round(accuracy, 4),
        "exact_match_count": n_correct,
        "per_component": per_comp_stats,
        "details": results,
    }

    return report


def print_report(report: dict):
    """打印评估报告。"""
    print("\n" + "=" * 60)
    print("  Component Prediction Accuracy Report")
    print("=" * 60)
    print(f"评估星系数: {report['num_galaxies_evaluated']}")
    print(f"精确匹配准确率: {report['exact_match_accuracy']*100:.1f}% ({report['exact_match_count']}/{report['num_galaxies_evaluated']})")

    print(f"\n--- 各成分统计 ---")
    print(f"{'Component':<10} {'GT#':<5} {'Pred#':<6} {'TP':<4} {'Precision':<10} {'Recall':<8} {'F1':<6}")
    for comp, stats in report["per_component"].items():
        print(f"{comp:<10} {stats['gt_count']:<5} {stats['pred_count']:<6} {stats['tp']:<4} {stats['precision']:<10.3f} {stats['recall']:<8.3f} {stats['f1']:<6.3f}")

    print(f"\n--- 逐星系详情 ---")
    for r in report["details"]:
        mark = "OK" if r["correct"] else "XX"
        print(f"  [{mark}] {r['galaxy_id']}")
        print(f"       GT:   {r['gt']}")
        print(f"       Pred: {r['pred']}")
        if r["fp"]:
            print(f"       FP(多预测): {r['fp']}")
        if r["fn"]:
            print(f"       FN(漏预测): {r['fn']}")
    print("=" * 60)


def main():
    parser = argparse.ArgumentParser(description="评估pipeline成分预测准确率（默认对齐MCP：VLM输出成分标签）")
    parser.add_argument("--input", required=True, help="实验output目录")
    parser.add_argument("--data-dir", required=True, help="Gadotti数据目录(含Gadotti_params.json)")
    parser.add_argument("--output", default=None, help="输出JSON路径(默认=input/component_accuracy.json)")
    parser.add_argument("--model", default="gemini-3.1-pro-preview", help="成分判定模型(对标同款)")
    parser.add_argument("--no-vlm", action="store_true", help="回退旧口径(n阈值+action回溯)")
    parser.add_argument("--refresh", action="store_true", help="忽略预测缓存，强制重调VLM")
    parser.add_argument("--api-key", default=None, help="API key(默认读环境变量 OPENAI_API_KEY)")
    args = parser.parse_args()

    try:
        from dotenv import load_dotenv
        load_dotenv(override=True)
    except ImportError:
        pass

    input_dir = os.path.abspath(args.input)
    data_dir = os.path.abspath(args.data_dir)
    output_path = args.output or os.path.join(input_dir, "component_accuracy.json")

    print(f"实验目录: {input_dir}")
    print(f"GT数据目录: {data_dir}")
    print(f"口径: {'旧(n阈值)' if args.no_vlm else f'MCP对齐(VLM={args.model})'}")

    report = evaluate(input_dir, data_dir, use_vlm=not args.no_vlm,
                      model_name=args.model, api_key=args.api_key, refresh=args.refresh)
    if not report:
        return

    print_report(report)

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    print(f"\n报告已保存: {output_path}")


if __name__ == "__main__":
    main()
