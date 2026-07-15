"""
按物理星系(任务)划分 SFT/DPO 数据为 train/test。

设计原则(与 main_generate.py 的 get_cross_band_train_test_split 一致):
  - 切分单位是**物理星系**(去掉波段前后缀),不是单条样本、也不是 band-specific id;
  - 同一物理星系的 g/r/i 全部归同一侧 → 跨波段一致,杜绝跨波段泄露;
  - 测试集物理星系列表落盘(test_galaxies.json),多次抽取 / 多实验 / SFT 与 DPO 之间复用同一划分,保证测评集恒定;
  - test 侧星系的样本永不进 train,反之亦然。

用法:
    # 划分某实验 output 目录下的 all_sft.jsonl / all_dpo.jsonl
    python -m data_gen.split_sft_train_test --input-dir output/<strategy_folder>/

    # 显式指定文件与测试比例(默认 0.1)
    python -m data_gen.split_sft_train_test --sft all_sft.jsonl --dpo all_dpo.jsonl --test-frac 0.1

    # 复用已有测试集划分(保证与之前完全一致)
    python -m data_gen.split_sft_train_test --input-dir output/<strategy_folder>/ \
        --test-galaxies output/<strategy_folder>/test_galaxies.json
"""

import argparse
import json
import os
import random

from data_gen.dataset_utils import _to_physical_id


def _load_jsonl(path):
    """读取 jsonl，返回 dict 列表。文件不存在返回 []。"""
    if not path or not os.path.isfile(path):
        return []
    rows = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def _write_jsonl(rows, path):
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def _collect_physical_ids(rows):
    """从样本列表收集物理星系 ID 集合。"""
    pids = set()
    for r in rows:
        gid = r.get("galaxy_id", "")
        if gid:
            pids.add(_to_physical_id(gid))
    return pids


def choose_test_physical_ids(all_pids, test_frac, test_num, seed, reuse_list):
    """确定测试集物理星系集合。

    优先级: reuse_list(复用已有划分) > test_num(显式条数) > test_frac(比例)。
    """
    if reuse_list is not None:
        reuse = set(reuse_list)
        missing = reuse - all_pids
        if missing:
            print(f"  [WARN] 复用的测试集里有 {len(missing)} 个物理星系不在当前数据中(将忽略): "
                  f"{sorted(missing)[:5]}{'...' if len(missing) > 5 else ''}")
        return reuse & all_pids

    ordered = sorted(all_pids)
    random.seed(seed)
    random.shuffle(ordered)

    if test_num is not None:
        k = min(test_num, len(ordered))
    else:
        k = int(round(len(ordered) * test_frac))
        k = max(1, min(k, len(ordered) - 1)) if len(ordered) >= 2 else 0
    return set(ordered[:k])


def split_rows(rows, test_pids):
    """按物理星系把样本分到 train / test 两侧。"""
    train, test = [], []
    for r in rows:
        pid = _to_physical_id(r.get("galaxy_id", ""))
        (test if pid in test_pids else train).append(r)
    return train, test


def _band_breakdown(rows):
    """统计各波段样本数(按 galaxy_id 前缀)。"""
    counts = {}
    for r in rows:
        gid = r.get("galaxy_id", "")
        band = "unknown"
        for b in ("SDSS_gband", "SDSS_rband", "SDSS_iband"):
            if gid.startswith(b):
                band = b
                break
        counts[band] = counts.get(band, 0) + 1
    return counts


def main():
    ap = argparse.ArgumentParser(description="按物理星系划分 SFT/DPO 数据为 train/test")
    ap.add_argument("--input-dir", default=None,
                    help="实验 output 目录(自动找 all_sft.jsonl / all_dpo.jsonl)")
    ap.add_argument("--sft", default=None, help="SFT jsonl 路径(覆盖 --input-dir)")
    ap.add_argument("--dpo", default=None, help="DPO jsonl 路径(覆盖 --input-dir)")
    ap.add_argument("--output-dir", default=None, help="输出目录(默认=input-dir 或 sft 所在目录)")
    ap.add_argument("--test-frac", type=float, default=0.1, help="测试集物理星系比例(默认 0.1)")
    ap.add_argument("--test-num", type=int, default=None, help="测试集物理星系条数(优先于 --test-frac)")
    ap.add_argument("--seed", type=int, default=42, help="随机种子(与生成阶段一致,默认 42)")
    ap.add_argument("--test-galaxies", default=None,
                    help="复用已有测试集物理星系列表(json,含 test_physical_ids 字段或纯列表)")
    args = ap.parse_args()

    # 解析路径
    if args.input_dir:
        in_dir = os.path.abspath(args.input_dir)
        sft_path = args.sft or os.path.join(in_dir, "all_sft.jsonl")
        dpo_path = args.dpo or os.path.join(in_dir, "all_dpo.jsonl")
        out_dir = os.path.abspath(args.output_dir) if args.output_dir else in_dir
    else:
        if not args.sft:
            ap.error("需提供 --input-dir 或 --sft")
        sft_path = os.path.abspath(args.sft)
        dpo_path = os.path.abspath(args.dpo) if args.dpo else None
        out_dir = os.path.abspath(args.output_dir) if args.output_dir else os.path.dirname(sft_path)

    sft_rows = _load_jsonl(sft_path)
    dpo_rows = _load_jsonl(dpo_path)
    print(f"SFT: {len(sft_rows)} 条  ({sft_path})")
    print(f"DPO: {len(dpo_rows)} 条  ({dpo_path})")
    if not sft_rows and not dpo_rows:
        print("未读到任何样本，退出。")
        return

    # 测试集物理星系必须由 SFT 与 DPO 的并集决定(保证两者划分一致)
    all_pids = _collect_physical_ids(sft_rows) | _collect_physical_ids(dpo_rows)
    print(f"物理星系总数: {len(all_pids)}")

    reuse_list = None
    if args.test_galaxies and os.path.isfile(args.test_galaxies):
        with open(args.test_galaxies, "r", encoding="utf-8") as f:
            obj = json.load(f)
        reuse_list = obj["test_physical_ids"] if isinstance(obj, dict) else obj
        print(f"复用已有测试集划分: {len(reuse_list)} 个物理星系 ({args.test_galaxies})")

    test_pids = choose_test_physical_ids(
        all_pids, args.test_frac, args.test_num, args.seed, reuse_list)
    train_pids = all_pids - test_pids
    print(f"划分结果: train {len(train_pids)} 星系 / test {len(test_pids)} 星系")

    # 泄露自检
    assert not (train_pids & test_pids), "train/test 物理星系有交集，划分错误！"

    sft_train, sft_test = split_rows(sft_rows, test_pids)
    dpo_train, dpo_test = split_rows(dpo_rows, test_pids)

    os.makedirs(out_dir, exist_ok=True)
    outputs = {
        "sft_train.jsonl": sft_train,
        "sft_test.jsonl": sft_test,
        "dpo_train.jsonl": dpo_train,
        "dpo_test.jsonl": dpo_test,
    }
    for name, rows in outputs.items():
        _write_jsonl(rows, os.path.join(out_dir, name))

    report = {
        "seed": args.seed,
        "num_physical_galaxies": len(all_pids),
        "num_train_galaxies": len(train_pids),
        "num_test_galaxies": len(test_pids),
        "test_physical_ids": sorted(test_pids),
        "sft": {
            "train": len(sft_train), "test": len(sft_test),
            "train_by_band": _band_breakdown(sft_train),
            "test_by_band": _band_breakdown(sft_test),
        },
        "dpo": {"train": len(dpo_train), "test": len(dpo_test)},
    }
    with open(os.path.join(out_dir, "test_galaxies.json"), "w", encoding="utf-8") as f:
        json.dump({"seed": args.seed, "test_physical_ids": sorted(test_pids)},
                  f, indent=2, ensure_ascii=False)
    with open(os.path.join(out_dir, "split_report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    print("\n" + "=" * 56)
    print("  划分完成")
    print("=" * 56)
    print(f"SFT  train/test = {len(sft_train)} / {len(sft_test)}")
    print(f"     train 波段分布: {report['sft']['train_by_band']}")
    print(f"     test  波段分布: {report['sft']['test_by_band']}")
    print(f"DPO  train/test = {len(dpo_train)} / {len(dpo_test)}")
    print(f"输出目录: {out_dir}")
    print(f"  sft_train.jsonl / sft_test.jsonl / dpo_train.jsonl / dpo_test.jsonl")
    print(f"  test_galaxies.json (测试集物理星系列表,后续复用)")
    print(f"  split_report.json  (划分统计)")


if __name__ == "__main__":
    main()
