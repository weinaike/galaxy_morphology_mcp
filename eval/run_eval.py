"""
星系形态分解 SFT 模型评测：加载 QLoRA 模型，对测试集做推理，解析输出，计算指标。

指标体系（step-level offline，teacher-forcing 风格）：
  1. format_rate:  输出能否解析出有效 JSON spec（```json``` 块）
  2. type_accuracy: 粗动作类型（add/modify/delete/stop）是否与 GT 一致
  3. comp_count_match: 输出的成分数量是否与 GT 一致
  4. parameter_metrics: 对齐的成分之间参数距离（mag/Re/n/q/pa/x/y）

用法（在 A6000 上）：
    source /media/data/anaconda3/etc/profile.d/conda.sh && conda activate llama-factory
    cd /media/zhongling/wyh/GalDecomp_Gen

    # 第一步：准备测试数据
    python -m eval.prepare_eval_data \
        --input-dir output/E7_full__vlm_proposal_gemini-3.1-pro-preview_vlm_reward_gemini-3.1-pro-preview_hist \
        --test-galaxies output/E7_full__vlm_proposal_gemini-3.1-pro-preview_vlm_reward_gemini-3.1-pro-preview_hist/test_galaxies.json \
        --out-dir eval/eval_data

    # 第二步：推理 + 评测
    python -m eval.run_eval \
        --eval-data eval/eval_data/galaxy_eval_test.jsonl \
        --model-path /media/zhongling/huggingface/Qwen2.5-VL-7B-Instruct \
        --adapter-path /media/zhongling/wyh/LLaMA-Factory/saves/qwen2_5vl-7b-galaxy-qlora \
        --out-dir eval/eval_results
"""

import argparse
import json
import os
import re
import time
from collections import defaultdict

import traceback

import torch
from PIL import Image


def _fix_json_escapes(s):
    """修复模型输出中常见的非法 JSON 转义（如 LaTeX \\chi, \\nu 中的反斜杠）。"""
    return re.sub(r'\\(?!["\\/bfnrtu])', r'\\\\', s)


def parse_json_spec(text):
    """从模型输出中提取 ```json``` 块，解析为 dict。"""
    patterns = [
        r'```json\s*\n(.*?)\n\s*```',
        r'```json\s*(.*?)```',
        r'```\s*\n(\{.*?\})\s*\n\s*```',
        r'```\s*(\{.*?\})\s*```',
    ]
    for pat in patterns:
        m = re.search(pat, text, re.DOTALL)
        if m:
            raw = m.group(1)
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                pass
            try:
                return json.loads(_fix_json_escapes(raw))
            except json.JSONDecodeError:
                continue
    try:
        start = text.rfind('{"components')
        if start < 0:
            start = text.rfind('{')
        end = text.rfind('}')
        if start >= 0 and end > start:
            candidate = text[start:end + 1]
            return json.loads(candidate)
    except Exception:
        pass
    return None


def _normalize_label(label):
    """把细粒度 label 归一化到粗粒度：add_psf/add_disk/add_sersic → add，delete_* → delete。"""
    if not label:
        return "unknown"
    label = label.lower().strip()
    if label.startswith("add"):
        return "add"
    if label.startswith("delete") or label.startswith("remove"):
        return "delete"
    if label in ("modify", "stop", "unknown"):
        return label
    return label


def derive_coarse_label(pred_spec, gt_spec):
    """从 pred spec 的 target 文本推导粗动作类型。"""
    if not pred_spec:
        return "unknown"
    target = (pred_spec.get("target") or "").lower()

    if any(kw in target for kw in ["stop", "终止", "结束", "满意", "完美", "最终确认", "维持当前"]):
        return "stop"
    if any(kw in target for kw in ["删除", "移除", "去掉", "减少"]):
        return "delete"
    if any(kw in target for kw in ["添加", "增加", "新增", "拆分", "加入", "引入"]):
        return "add"
    return "modify"


def match_components(pred_comps, gt_comps):
    """按 role/model 匹配预测和 GT 的成分，返回 (matched_pairs, unmatched_pred, unmatched_gt)。"""
    gt_available = list(range(len(gt_comps)))
    matched = []
    pred_used = set()

    for pi, pc in enumerate(pred_comps):
        p_role = (pc.get("role") or pc.get("name") or "").lower()
        p_model = (pc.get("model") or "").lower()
        best_gi = None
        best_score = -1
        for gi in gt_available:
            gc = gt_comps[gi]
            g_role = (gc.get("role") or gc.get("name") or "").lower()
            g_model = (gc.get("model") or "").lower()
            score = 0
            if p_role and g_role and p_role == g_role:
                score += 2
            if p_model and g_model and p_model == g_model:
                score += 1
            if score > best_score:
                best_score = score
                best_gi = gi
        if best_gi is not None and best_score > 0:
            matched.append((pi, best_gi))
            pred_used.add(pi)
            gt_available.remove(best_gi)

    unmatched_pred = [i for i in range(len(pred_comps)) if i not in pred_used]
    unmatched_gt = gt_available
    return matched, unmatched_pred, unmatched_gt


PARAM_KEYS = ["mag", "re", "n", "q", "pa", "x", "y"]


def compute_param_distance(pred_comp, gt_comp):
    """计算两个成分之间的参数距离。返回 {param: abs_diff} dict。"""
    diffs = {}
    for k in PARAM_KEYS:
        pv = pred_comp.get(k)
        gv = gt_comp.get(k)
        if pv is not None and gv is not None:
            try:
                pv, gv = float(pv), float(gv)
                diffs[k] = abs(pv - gv)
            except (ValueError, TypeError):
                pass
    return diffs


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
    """对单条样本做推理，返回生成的文本。"""
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


def evaluate_sample(pred_text, gt_spec, gt_label):
    """评测单条样本，返回指标 dict。"""
    result = {
        "format_ok": False,
        "type_match": False,
        "comp_count_match": False,
        "pred_label": "unknown",
        "gt_label": gt_label,
        "pred_n_comps": 0,
        "gt_n_comps": len(gt_spec.get("components", [])) if gt_spec else 0,
        "param_diffs": {},
        "n_matched_comps": 0,
    }

    pred_spec = parse_json_spec(pred_text)
    if pred_spec is None:
        return result

    result["format_ok"] = True
    pred_comps = pred_spec.get("components", [])
    gt_comps = gt_spec.get("components", []) if gt_spec else []
    result["pred_n_comps"] = len(pred_comps)
    result["comp_count_match"] = len(pred_comps) == len(gt_comps)

    pred_label = derive_coarse_label(pred_spec, gt_spec)
    gt_label_norm = _normalize_label(gt_label)
    result["pred_label"] = pred_label
    result["gt_label"] = gt_label_norm
    result["gt_label_raw"] = gt_label
    result["type_match"] = (pred_label == gt_label_norm)

    matched, _, _ = match_components(pred_comps, gt_comps)
    result["n_matched_comps"] = len(matched)

    all_diffs = defaultdict(list)
    for pi, gi in matched:
        diffs = compute_param_distance(pred_comps[pi], gt_comps[gi])
        for k, v in diffs.items():
            all_diffs[k].append(v)
    result["param_diffs"] = dict(all_diffs)

    return result


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

    param_all_diffs = defaultdict(list)
    for r in results:
        for k, vs in r.get("param_diffs", {}).items():
            param_all_diffs[k].extend(vs)

    param_mae = {}
    for k, vs in param_all_diffs.items():
        if vs:
            param_mae[k] = sum(vs) / len(vs)

    label_confusion = defaultdict(lambda: defaultdict(int))
    for r in results:
        label_confusion[r["gt_label"]][r["pred_label"]] += 1

    return {
        "n_samples": n,
        "format_rate": round(format_rate, 4),
        "type_accuracy": round(type_acc, 4),
        "comp_count_match_rate": round(comp_match_rate, 4),
        "param_mae": {k: round(v, 4) for k, v in param_mae.items()},
        "n_format_ok": format_ok,
        "n_type_match": type_match,
        "n_comp_match": comp_match,
        "label_confusion": {gt: dict(pred_counts) for gt, pred_counts in label_confusion.items()},
    }


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
    args = ap.parse_args()

    out_dir = os.path.abspath(args.out_dir)
    os.makedirs(out_dir, exist_ok=True)

    print("="*60)
    print("  星系形态分解 SFT 评测")
    print("="*60)

    with open(args.eval_data, "r", encoding="utf-8") as f:
        samples = [json.loads(line) for line in f if line.strip()]
    print(f"测试样本: {len(samples)} 条")

    if args.max_samples > 0:
        samples = samples[:args.max_samples]
        print(f"限制评测: 前 {args.max_samples} 条")

    pred_path = os.path.join(out_dir, "predictions.jsonl")

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
            print(f"\n[{i+1}/{len(samples)}] {gid} / {nid}")

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

    print("\n" + "="*60)
    print("  计算评测指标")
    print("="*60)

    results = []
    for i, (sample, pred) in enumerate(zip(samples, predictions)):
        gt = sample.get("_gt", {})
        gt_spec = gt.get("spec", {})
        gt_label = gt.get("coarse_label", "unknown")
        pred_text = pred.get("prediction", "")
        gt_assistant = sample["messages"][-1]["content"]

        r = evaluate_sample(pred_text, gt_spec, gt_label)
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

    print("\n" + "="*60)
    print("  评测报告")
    print("="*60)
    print(f"  样本数:          {agg.get('n_samples', 0)}")
    print(f"  格式正确率:      {agg.get('format_rate', 0):.1%} ({agg.get('n_format_ok', 0)}/{agg.get('n_samples', 0)})")
    print(f"  动作类型准确率:  {agg.get('type_accuracy', 0):.1%} ({agg.get('n_type_match', 0)}/{agg.get('n_format_ok', 0)})")
    print(f"  成分数一致率:    {agg.get('comp_count_match_rate', 0):.1%} ({agg.get('n_comp_match', 0)}/{agg.get('n_format_ok', 0)})")
    print(f"\n  参数 MAE (匹配成分):")
    for k, v in agg.get("param_mae", {}).items():
        print(f"    {k:>5}: {v:.4f}")
    print(f"\n  动作类型混淆矩阵 (GT → Pred):")
    for gt_label, preds in agg.get("label_confusion", {}).items():
        print(f"    {gt_label}: {dict(preds)}")
    print(f"\n  详细结果: {detail_path}")
    print(f"  汇总报告: {report_path}")


if __name__ == "__main__":
    main()
