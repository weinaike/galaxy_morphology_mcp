"""
诊断 summary_path 里 expdisk/psf 组件的实际格式。

背景：v7 加了多类型 fit-log 解析器，但 top FP 里 Rs=0/q=0.02 等明显违规
没被拦 → parser 可能没匹配到 expdisk 行。

用法（在 A6000）：
    python -m eval.debug_summary_format \\
        --input-dir output/E7_full__vlm_proposal_gemini-3.1-pro-preview_vlm_reward_gemini-3.1-pro-preview_hist \\
        --pattern expdisk \\
        --max-samples 5
"""
import argparse
import glob
import json
import os
import re
import sys


def find_trajectories_with_component(input_dir, pattern, max_samples=5):
    """在轨迹里找含指定 component 类型的 summary_path。"""
    trajectory_files = sorted(glob.glob(os.path.join(input_dir, "**/trajectory.json"), recursive=True))
    print(f"找到 {len(trajectory_files)} 个 trajectory.json", flush=True)

    matched = []
    for i, tf in enumerate(trajectory_files):
        if len(matched) >= max_samples:
            break
        try:
            with open(tf, "r", encoding="utf-8") as f:
                tree = json.load(f)
        except Exception:
            continue

        for node in tree.get("nodes", []):
            sp = node.get("summary_path")
            if not sp or not os.path.exists(sp):
                continue
            try:
                with open(sp, "r", encoding="utf-8") as f:
                    content = f.read()
            except Exception:
                continue

            # 找到含 pattern 的 summary
            if re.search(rf"\b{pattern}\b", content, re.IGNORECASE):
                matched.append((tf, node["node_id"], sp, content))
                break

        if (i + 1) % 100 == 0:
            print(f"  已扫描 {i+1}/{len(trajectory_files)}, 匹配 {len(matched)}", flush=True)

    return matched


def show_fit_log_section(content, comp_type):
    """打印 summary 里 fit log 相关的段落。"""
    # 找 ## Fit log Content 段落
    m = re.search(r"## Fit log Content\n(.*?)(?=\n---|\n## |\Z)", content, re.DOTALL)
    if m:
        print("=== ## Fit log Content 段落 ===")
        fit_log = m.group(1)
        print(fit_log[:2000])  # 只打印前 2000 字符
        print("=== END ===\n")
    else:
        print("(未找到 ## Fit log Content 段落)")

    # 提取所有含 comp_type 的行
    print(f"\n=== 所有含 '{comp_type}' 的行 ===")
    for line in content.split("\n"):
        if comp_type.lower() in line.lower():
            print(repr(line))
    print("=== END ===\n")


def try_parse(content, comp_type):
    """用 v7 的 regex 尝试匹配，看能不能抓到。"""
    m = re.search(r"## Fit log Content\n(.*?)(?=\n---|\Z)", content, re.DOTALL)
    fit_log = m.group(1) if m else content

    line_re = re.compile(r"^\s*(sersic|expdisk|psf)\s*:\s*\(([^)]+)\)\s*(.*)$", re.MULTILINE)
    val_re = re.compile(r"[-+]?\d*\.?\d+(?:[eE][+-]?\d+)?|---")

    print(f"=== v7 regex 匹配结果 ===")
    found = 0
    for match in line_re.finditer(fit_log):
        model = match.group(1)
        rest = match.group(3)
        vals = val_re.findall(rest)
        print(f"  matched: model={model} vals={vals}")
        found += 1
    print(f"总匹配: {found} 个组件")
    print("=== END ===\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input-dir", required=True)
    ap.add_argument("--pattern", default="expdisk", help="要查找的组件类型")
    ap.add_argument("--max-samples", type=int, default=3)
    args = ap.parse_args()

    matched = find_trajectories_with_component(args.input_dir, args.pattern, args.max_samples)

    if not matched:
        print(f"❌ 没找到含 '{args.pattern}' 的 summary")
        sys.exit(1)

    for i, (tf, nid, sp, content) in enumerate(matched):
        print("=" * 70)
        print(f"样本 {i+1}: {sp}")
        print("=" * 70)
        show_fit_log_section(content, args.pattern)
        try_parse(content, args.pattern)


if __name__ == "__main__":
    main()
