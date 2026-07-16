"""
星系形态分解 SFT 模型评测：加载 QLoRA 模型，对测试集做推理，解析输出，计算指标。

指标体系（step-level offline，teacher-forcing 风格）：
  1. format_rate:  输出能否解析出有效 JSON spec
  2. type_accuracy: 粗动作类型（add/modify/delete）是否与 GT 一致
  3. comp_count_match: 输出的成分数量是否与 GT 一致
  4. param_scores: tolerance-normalized 参数精度（0~1）
  5. param_mae: 原始 MAE（兼容旧指标）

用法（在 A6000 上）：
    source /media/data/anaconda3/etc/profile.d/conda.sh && conda activate llama-factory
    cd /media/zhongling/wyh/GalDecomp_Gen

    # 第一步：准备测试数据
    python -m eval.prepare_eval_data \\
        --input-dir output/E7_full__vlm_proposal_gemini-3.1-pro-preview_vlm_reward_gemini-3.1-pro-preview_hist \\
        --test-galaxies output/E7_full__vlm_proposal_gemini-3.1-pro-preview_vlm_reward_gemini-3.1-pro-preview_hist/test_galaxies.json \\
        --out-dir eval/eval_data

    # 第二步：推理 + 评测
    python -m eval.run_eval \\
        --eval-data eval/eval_data/galaxy_eval_test.jsonl \\
        --model-path /media/zhongling/huggingface/Qwen2.5-VL-7B-Instruct \\
        --adapter-path /media/zhongling/wyh/LLaMA-Factory/saves/qwen2_5vl-7b-galaxy-qlora \\
        --out-dir eval/eval_results

    # 跳过推理，只重新评测 + 可视化
    python -m eval.run_eval \\
        --eval-data eval/eval_data/galaxy_eval_test.jsonl \\
        --model-path dummy --adapter-path dummy \\
        --out-dir eval/eval_results \\
        --skip-inference --visualize
"""

import argparse
import json
import os
import time
import traceback
from collections import defaultdict

import torch
from PIL import Image

from eval.evaluate_action import (
    evaluate_galfit_action,
    parse_json_spec,
    normalize_coarse_label,
)


# ============================================================
# 模型加载与推理（保留原逻辑）
# ============================================================

def load_model_and_processor(model_path, adapter_path, use_4bit=True):
    """加载 Qwen2.5-VL base + LoRA adapter。"""
    from transformers import AutoProcessor, AutoModelForVision2Seq
    from peft import PeftModel

    print(f"加载模型: {model_path}")
    kwargs = {"dtype": torch.bfloat16, "device_map": "auto", "trust_remote_code": True}
    if use_4bit:
        from transformers import BitsAndBytesConfig
        kwargs["quantization_config"] = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_compute_dtype=torch.bfloat16,
            bnb_4bit_quant_type="nf4",
        )
    model = AutoModelForVision2Seq.from_pretrained(model_path, **kwargs)

    print(f"加载 LoRA adapter: {adapter_path}")
    model = PeftModel.from_pretrained(model, adapter_path)
    model.eval()

    processor = AutoProcessor.from_pretrained(model_path, trust_remote_code=True)
    return model, processor


def run_inference_single(model, processor, system_content, user_text, image_path,
                         max_new_tokens=4096):
    """对单条样本做推理。"""
    user_text_clean = user_text
    if user_text_clean.startswith("<image>\n"):
        user_text_clean = user_text_clean[len("<image>\n"):]

    messages = [
        {"role": "system", "content": [{"type": "text", "text": system_content}]},
        {"role": "user", "content": [
            {"type": "image", "image": f"file://{image_path}"},
            {"type": "text", "text": user_text_clean},
        ]},
    ]

    text_prompt = processor.apply_chat_template(messages, add_generation_prompt=True, tokenize=False)

    image = Image.open(image_path).convert("RGB")
    inputs = processor(
        text=[text_prompt],
        images=[image],
        padding=True,
        return_tensors="pt",
    ).to(model.device)

    with torch.no_grad():
        output_ids = model.generate(
            **inputs,
            max_new_tokens=max_new_tokens,
            do_sample=False,
            temperature=1.0,
            top_p=1.0,
        )

    input_len = inputs.input_ids.shape[1]
    generated = output_ids[0][input_len:]
    response = processor.decode(generated, skip_special_tokens=True)
    return response


# ============================================================
# 评测汇总
# ============================================================

def aggregate_results(results):
    """汇总所有样本的评测结果。"""
    n = len(results)
    if n == 0:
        return {}

    format_ok = sum(r["format_ok"] for r in results)
    type_match = sum(r["type_match"] for r in results)
    comp_match = sum(r["comp_count_match"] for r in results)
    format_rate = format_ok / n
    type_acc = type_match / format_ok if format_ok > 0 else 0
    comp_match_rate = comp_match / format_ok if format_ok > 0 else 0

    # 原始 MAE（兼容旧输出）
    param_all_diffs = defaultdict(list)
    for r in results:
        for k, vs in r.get("param_diffs", {}).items():
            param_all_diffs[k].extend(vs)
    param_mae = {}
    for k, vs in param_all_diffs.items():
        if vs:
            param_mae[k] = sum(vs) / len(vs)

    # tolerance-normalized 参数得分
    param_all_scores = defaultdict(list)
    for r in results:
        for k, v in r.get("param_scores", {}).items():
            param_all_scores[k].append(v)
    param_scores_avg = {}
    for k, vs in param_all_scores.items():
        if vs:
            param_scores_avg[k] = sum(vs) / len(vs)

    # acc_score 均值
    acc_scores = [r["acc_score"] for r in results if r["format_ok"]]
    mean_acc_score = sum(acc_scores) / len(acc_scores) if acc_scores else 0

    # 混淆矩阵
    label_confusion = defaultdict(lambda: defaultdict(int))
    for r in results:
        label_confusion[r["gt_label"]][r["pred_label"]] += 1

    # 按 type 分组
    per_type = defaultdict(lambda: {"n": 0, "format_ok": 0, "type_match": 0,
                                     "acc_scores": [], "param_diffs": defaultdict(list)})
    for r in results:
        gt = r["gt_label"]
        pt = per_type[gt]
        pt["n"] += 1
        if r["format_ok"]:
            pt["format_ok"] += 1
        if r["type_match"]:
            pt["type_match"] += 1
        if r["format_ok"]:
            pt["acc_scores"].append(r["acc_score"])
        for k, vs in r.get("param_diffs", {}).items():
            pt["param_diffs"][k].extend(vs)

    per_type_report = {}
    for gt_label, pt in per_type.items():
        per_type_report[gt_label] = {
            "n": pt["n"],
            "format_rate": pt["format_ok"] / pt["n"] if pt["n"] > 0 else 0,
            "type_accuracy": pt["type_match"] / pt["format_ok"] if pt["format_ok"] > 0 else 0,
            "mean_acc_score": sum(pt["acc_scores"]) / len(pt["acc_scores"]) if pt["acc_scores"] else 0,
            "param_mae": {k: sum(vs) / len(vs) for k, vs in pt["param_diffs"].items() if vs},
        }

    # 按 depth 分组
    per_depth = defaultdict(lambda: {"n": 0, "format_ok": 0, "type_match": 0, "acc_scores": []})
    for r in results:
        d = r.get("depth", -1)
        pd = per_depth[d]
        pd["n"] += 1
        if r["format_ok"]:
            pd["format_ok"] += 1
        if r["type_match"]:
            pd["type_match"] += 1
        if r["format_ok"]:
            pd["acc_scores"].append(r["acc_score"])

    per_depth_report = {}
    for depth, pd in sorted(per_depth.items()):
        per_depth_report[str(depth)] = {
            "n": pd["n"],
            "format_rate": pd["format_ok"] / pd["n"] if pd["n"] > 0 else 0,
            "type_accuracy": pd["type_match"] / pd["format_ok"] if pd["format_ok"] > 0 else 0,
            "mean_acc_score": sum(pd["acc_scores"]) / len(pd["acc_scores"]) if pd["acc_scores"] else 0,
        }

    return {
        "n_samples": n,
        "format_rate": round(format_rate, 4),
        "type_accuracy": round(type_acc, 4),
        "comp_count_match_rate": round(comp_match_rate, 4),
        "mean_acc_score": round(mean_acc_score, 4),
        "param_scores": {k: round(v, 4) for k, v in param_scores_avg.items()},
        "param_mae": {k: round(v, 4) for k, v in param_mae.items()},
        "n_format_ok": format_ok,
        "n_type_match": type_match,
        "n_comp_match": comp_match,
        "label_confusion": {gt: dict(pred_counts) for gt, pred_counts in label_confusion.items()},
        "per_type": per_type_report,
        "per_depth": per_depth_report,
    }


# ============================================================
# 主流程
# ============================================================

def main():
    ap = argparse.ArgumentParser(description="SFT 模型评测: 推理 + 指标计算")
    ap.add_argument("--eval-data", required=True, help="galaxy_eval_test.jsonl")
    ap.add_argument("--model-path", required=True, help="Qwen2.5-VL base model 路径")
    ap.add_argument("--adapter-path", required=True, help="LoRA adapter 路径")
    ap.add_argument("--out-dir", default="eval/eval_results")
    ap.add_argument("--max-new-tokens", type=int, default=4096)
    ap.add_argument("--max-samples", type=int, default=0, help="限制评测样本数（0=全部）")
    ap.add_argument("--no-4bit", action="store_true", help="不用4-bit量化加载")
    ap.add_argument("--skip-inference", action="store_true",
                    help="跳过推理，直接从 predictions.jsonl 读取已有预测结果")
    ap.add_argument("--visualize", action="store_true",
                    help="生成可视化图表到 out_dir/plots/")
    args = ap.parse_args()

    out_dir = os.path.abspath(args.out_dir)
    os.makedirs(out_dir, exist_ok=True)

    print("=" * 60)
    print("  星系形态分解 SFT 评测")
    print("=" * 60)

    with open(args.eval_data, "r", encoding="utf-8") as f:
        samples = [json.loads(line) for line in f if line.strip()]
    print(f"测试样本: {len(samples)} 条")

    if args.max_samples > 0:
        samples = samples[:args.max_samples]
        print(f"限制评测: 前 {args.max_samples} 条")

    pred_path = os.path.join(out_dir, "predictions.jsonl")

    # ---- 推理阶段 ----
    if not args.skip_inference:
        model, processor = load_model_and_processor(
            args.model_path, args.adapter_path, use_4bit=not args.no_4bit)

        predictions = []
        for i, sample in enumerate(samples):
            gt = sample.get("_gt", {})
            msgs = sample["messages"]
            system_content = msgs[0]["content"]
            user_content = msgs[1]["content"]
            image_path = sample["images"][0]

            gid = gt.get("galaxy_id", "?")
            nid = gt.get("node_id", "?")
            print(f"\n[{i + 1}/{len(samples)}] {gid} / {nid}")

            if not os.path.isfile(image_path):
                print(f"  [SKIP] 图像不存在: {image_path}")
                predictions.append({"index": i, "galaxy_id": gid, "node_id": nid,
                                    "prediction": "", "error": "image_not_found"})
                continue

            t0 = time.time()
            try:
                pred_text = run_inference_single(
                    model, processor, system_content, user_content,
                    image_path, args.max_new_tokens)
                elapsed = time.time() - t0
                print(f"  生成完毕 ({elapsed:.1f}s, {len(pred_text)} chars)")
                predictions.append({"index": i, "galaxy_id": gid, "node_id": nid,
                                    "prediction": pred_text, "elapsed": round(elapsed, 2)})
            except Exception as e:
                elapsed = time.time() - t0
                err_msg = traceback.format_exc()
                print(f"  [ERROR] {err_msg} ({elapsed:.1f}s)")
                predictions.append({"index": i, "galaxy_id": gid, "node_id": nid,
                                    "prediction": "", "error": err_msg})

        with open(pred_path, "w", encoding="utf-8") as f:
            for p in predictions:
                f.write(json.dumps(p, ensure_ascii=False) + "\n")
        print(f"\n预测结果已保存: {pred_path}")
    else:
        print(f"跳过推理，读取已有预测: {pred_path}")
        with open(pred_path, "r", encoding="utf-8") as f:
            predictions = [json.loads(line) for line in f if line.strip()]

    # ---- 评测阶段 ----
    print("\n" + "=" * 60)
    print("  计算评测指标")
    print("=" * 60)

    results = []
    for i, (sample, pred) in enumerate(zip(samples, predictions)):
        gt = sample.get("_gt", {})
        gt_spec = gt.get("spec", {})
        gt_label = gt.get("coarse_label", "unknown")
        pred_text = pred.get("prediction", "")

        r = evaluate_galfit_action(pred_text, gt_spec, gt_label)
        r["index"] = i
        r["galaxy_id"] = gt.get("galaxy_id")
        r["node_id"] = gt.get("node_id")
        r["depth"] = gt.get("depth")

        if not r["format_ok"]:
            print(f"  [{i}] {r['galaxy_id']}/{r['node_id']}: FORMAT FAIL")
        elif not r["type_match"]:
            print(f"  [{i}] {r['galaxy_id']}/{r['node_id']}: type {r['pred_label']} != {r['gt_label']}")
        results.append(r)

    agg = aggregate_results(results)

    detail_path = os.path.join(out_dir, "eval_details.jsonl")
    with open(detail_path, "w", encoding="utf-8") as f:
        for r in results:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    report_path = os.path.join(out_dir, "eval_report.json")
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(agg, f, ensure_ascii=False, indent=2)

    # ---- 打印报告 ----
    print("\n" + "=" * 60)
    print("  评测报告")
    print("=" * 60)
    print(f"  样本数:          {agg.get('n_samples', 0)}")
    print(f"  格式正确率:      {agg.get('format_rate', 0):.1%} ({agg.get('n_format_ok', 0)}/{agg.get('n_samples', 0)})")
    print(f"  动作类型准确率:  {agg.get('type_accuracy', 0):.1%} ({agg.get('n_type_match', 0)}/{agg.get('n_format_ok', 0)})")
    print(f"  成分数一致率:    {agg.get('comp_count_match_rate', 0):.1%} ({agg.get('n_comp_match', 0)}/{agg.get('n_format_ok', 0)})")
    print(f"  综合参数精度:    {agg.get('mean_acc_score', 0):.3f}")

    print(f"\n  参数得分 (tolerance-normalized, 0~1):")
    for k, v in agg.get("param_scores", {}).items():
        print(f"    {k:>5}: {v:.3f}")

    print(f"\n  参数 MAE (原始):")
    for k, v in agg.get("param_mae", {}).items():
        print(f"    {k:>5}: {v:.4f}")

    print(f"\n  动作类型混淆矩阵 (GT → Pred):")
    for gt_label, preds in agg.get("label_confusion", {}).items():
        print(f"    {gt_label}: {dict(preds)}")

    print(f"\n  按类型分组:")
    for label, metrics in agg.get("per_type", {}).items():
        print(f"    {label}: n={metrics['n']}, type_acc={metrics['type_accuracy']:.1%}, "
              f"acc_score={metrics['mean_acc_score']:.3f}")

    print(f"\n  按 depth 分组:")
    for depth, metrics in agg.get("per_depth", {}).items():
        print(f"    depth={depth}: n={metrics['n']}, type_acc={metrics['type_accuracy']:.1%}, "
              f"acc_score={metrics['mean_acc_score']:.3f}")

    print(f"\n  详细结果: {detail_path}")
    print(f"  汇总报告: {report_path}")

    # ---- 可视化 ----
    if args.visualize:
        try:
            from eval.visualize import generate_plots
            plots_dir = os.path.join(out_dir, "plots")
            generate_plots(results, agg, plots_dir)
            print(f"  可视化输出: {plots_dir}")
        except ImportError:
            print("  [WARN] eval.visualize 不可用，跳过可视化")
        except Exception as e:
            print(f"  [WARN] 可视化失败: {e}")


if __name__ == "__main__":
    main()
