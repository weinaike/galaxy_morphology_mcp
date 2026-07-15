#!/usr/bin/env bash
# 星系形态分解 base SFT 训练启动脚本（LLaMA-Factory）
# 用法：
#   bash run_sft.sh qlora     # A6000 48G，QLoRA 4-bit（默认）
#   bash run_sft.sh full      # A100，全参 + deepspeed（需先 pip install deepspeed）
#
# 冒烟（小样本快速验证）：
#   EXTRA="max_samples=64 max_steps=3" bash run_sft.sh qlora
#
# 可用环境变量覆盖默认路径：
#   LF_ROOT / REPO / DATA_DIR / MODEL / OUTPUT / CONDA_ENV / EXTRA
set -e

MODE=${1:-qlora}

# ---- 路径（按机器改；默认对应 A6000）----
LF_ROOT=${LF_ROOT:-/media/zhongling/wyh/LLaMA-Factory}
REPO=${REPO:-/media/zhongling/wyh/GalDecomp_Gen}
DATA_DIR=${DATA_DIR:-$REPO/train/llamafactory/data}
MODEL=${MODEL:-/media/zhongling/huggingface/Qwen2.5-VL-7B-Instruct}
CONDA_ENV=${CONDA_ENV:-llama-factory}

# ---- conda ----
source /media/data/anaconda3/etc/profile.d/conda.sh
conda activate "$CONDA_ENV"

# ---- 选择配置 ----
if [ "$MODE" = "qlora" ]; then
    YAML=$REPO/train/llamafactory/qwen2_5vl_qlora_sft.yaml
    OUTPUT=${OUTPUT:-$LF_ROOT/saves/qwen2_5vl-7b-galaxy-qlora}
elif [ "$MODE" = "full" ]; then
    YAML=$REPO/train/llamafactory/qwen2_5vl_full_sft.yaml
    OUTPUT=${OUTPUT:-$LF_ROOT/saves/qwen2_5vl-7b-galaxy-full}
else
    echo "未知模式: $MODE（用 qlora 或 full）"; exit 1
fi

cd "$LF_ROOT"
echo "=============================================="
echo " MODE   = $MODE"
echo " YAML   = $YAML"
echo " MODEL  = $MODEL"
echo " DATA   = $DATA_DIR"
echo " OUTPUT = $OUTPUT"
echo " EXTRA  = ${EXTRA:-（无）}"
echo "=============================================="
echo " 训练中看 loss：另开终端 python $REPO/train/llamafactory/watch_loss.py $OUTPUT --loop 30"
echo "=============================================="

# DISABLE_VERSION_CHECK=1：绕过 egg-info 陈旧 pin（peft/trl 实际版本已满足源码要求）
DISABLE_VERSION_CHECK=1 llamafactory-cli train "$YAML" \
    dataset_dir="$DATA_DIR" \
    model_name_or_path="$MODEL" \
    output_dir="$OUTPUT" \
    logging_steps=5 \
    plot_loss=true \
    ${EXTRA:-}
