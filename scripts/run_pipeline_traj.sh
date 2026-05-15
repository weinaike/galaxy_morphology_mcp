#!/bin/bash
set -e

# ==========================================
# GalDecomp Agent: 强化学习离线轨迹生成流水线
# ==========================================
BASE_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$BASE_DIR"

RAW_DATA_ROOT="data/galfit_sample_0204"
TRAJ_ROOT="data/trajectories"
SFT_OUT_DIR="data/sft_dataset"
WORKSPACE="galfit_workspace"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)

FIELDS=("COS" "EGS" "GOODSN" "GOODSS" "UDS")

mkdir -p logs
exec > >(tee -a "logs/pipeline_${TIMESTAMP}.log") 2>&1

eval "$(conda shell.bash hook)"
conda activate mcp

echo "🚀 [阶段1] 并发执行 MDP 策略探索与轨迹生成..."
for field in "${FIELDS[@]}"; do
    if [ ! -d "${RAW_DATA_ROOT}/${field}" ]; then continue; fi
    echo "🪐 处理天区: ${field}"
    
    mkdir -p "${TRAJ_ROOT}/${field}"
    
    python scripts/run_trajectory_gen.py \
        --input_dir "${RAW_DATA_ROOT}/${field}" \
        --field "$field" \
        --traj_out_dir "${TRAJ_ROOT}/${field}"
done

echo "🧬 [阶段2] 提炼多模态 SFT 训练集..."
python scripts/build_sft.py \
    --traj_dir "$TRAJ_ROOT" \
    --output_jsonl "${SFT_OUT_DIR}/galdecomp_train_${TIMESTAMP}.jsonl"

echo "🧹 [阶段3] 释放临时沙盒空间..."
rm -rf "$WORKSPACE"/*

echo "🎉 全流程完成！数据集已就绪。"