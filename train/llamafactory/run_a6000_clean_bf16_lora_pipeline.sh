#!/usr/bin/env bash
# A6000 pipeline: prepare local clean data -> BF16 LoRA SFT -> merge ->
# deterministic pre/post-merge evaluation -> GALFIT + v11 + VLM evaluation.
#
# Usage:
#   bash train/llamafactory/run_a6000_clean_bf16_lora_pipeline.sh all
#   bash train/llamafactory/run_a6000_clean_bf16_lora_pipeline.sh prepare
#   bash train/llamafactory/run_a6000_clean_bf16_lora_pipeline.sh train
#   bash train/llamafactory/run_a6000_clean_bf16_lora_pipeline.sh merge
#   bash train/llamafactory/run_a6000_clean_bf16_lora_pipeline.sh eval
#
# If a later stage fails, rerun only that stage. Existing model/evaluation
# directories are never overwritten automatically.
set -euo pipefail

STAGE=${1:-all}

REPO=${REPO:-/media/zhongling/wyh/GalDecomp_Gen}
LF_ROOT=${LF_ROOT:-/media/zhongling/wyh/LLaMA-Factory}
BASE_MODEL=${BASE_MODEL:-/media/zhongling/huggingface/Qwen2.5-VL-7B-Instruct}
CONDA_ENV=${CONDA_ENV:-llama-factory}

E7=${E7:-$REPO/output/E7_full__vlm_proposal_gemini-3.1-pro-preview_vlm_reward_gemini-3.1-pro-preview_hist}
TEST_GALAXIES=${TEST_GALAXIES:-$E7/test_galaxies.json}
DATA_DIR=${DATA_DIR:-$REPO/train/llamafactory/data_a6000_clean_lora_v1}
EVAL_DATA_DIR=${EVAL_DATA_DIR:-$REPO/eval/eval_data_clean_lora_v1}

TRAIN_CONFIG=${TRAIN_CONFIG:-$REPO/train/llamafactory/qwen2_5vl_lora_bf16_clean_sft.yaml}
MERGE_CONFIG=${MERGE_CONFIG:-$REPO/train/llamafactory/merge_qwen2_5vl_lora_bf16_clean_sft.yaml}
LORA_DIR=${LORA_DIR:-$LF_ROOT/saves/qwen2_5vl-7b-galaxy-clean-bf16-lora-v1}
MERGED_DIR=${MERGED_DIR:-$LF_ROOT/saves/qwen2_5vl-7b-galaxy-clean-bf16-lora-merged-v1}

EVAL_ROOT=${EVAL_ROOT:-$REPO/eval/clean_bf16_lora_v1}
ADAPTER_EVAL=${ADAPTER_EVAL:-$EVAL_ROOT/offline_adapter}
MERGED_EVAL=${MERGED_EVAL:-$EVAL_ROOT/offline_merged}
ADAPTER_EXEC=${ADAPTER_EXEC:-$EVAL_ROOT/exec_adapter_vlm}
MERGED_EXEC=${MERGED_EXEC:-$EVAL_ROOT/exec_merged_vlm}
COMPARE_REPORT=${COMPARE_REPORT:-$EVAL_ROOT/merge_compare_report.json}

THRESHOLD=${THRESHOLD:-0.05139489475137804}
VLM_MODEL=${VLM_MODEL:-gemini-3.1-pro-preview}
GALFIT_CONCURRENCY=${GALFIT_CONCURRENCY:-2}

source /media/data/anaconda3/etc/profile.d/conda.sh
conda activate "$CONDA_ENV"
export PYTHONPATH="$REPO${PYTHONPATH:+:$PYTHONPATH}"
cd "$REPO"

require_file() {
  [[ -f "$1" ]] || { echo "Missing file: $1" >&2; exit 1; }
}

require_absent() {
  [[ ! -e "$1" ]] || {
    echo "Refusing to overwrite existing path: $1" >&2
    echo "Choose a new versioned path or run a later stage only." >&2
    exit 1
  }
}

prepare_data() {
  require_file "$TEST_GALAXIES"
  if [[ ! -f "$DATA_DIR/galaxy_sft_train.jsonl" || ! -f "$DATA_DIR/galaxy_sft_val.jsonl" ]]; then
    mkdir -p "$DATA_DIR"
    python -u -m data_gen.convert_sft_to_llamafactory \
      --input-dir "$E7" \
      --test-galaxies "$TEST_GALAXIES" \
      --out-dir "$DATA_DIR" \
      --max-steps 15 \
      --val-ratio 0.01 \
      --seed 42
    cp "$REPO/train/llamafactory/dataset_info.json" "$DATA_DIR/dataset_info.json"
  else
    echo "Reusing prepared training data: $DATA_DIR"
  fi

  if [[ ! -f "$EVAL_DATA_DIR/galaxy_eval_test.jsonl" ]]; then
    mkdir -p "$EVAL_DATA_DIR"
    python -u -m eval.prepare_eval_data \
      --input-dir "$E7" \
      --test-galaxies "$TEST_GALAXIES" \
      --out-dir "$EVAL_DATA_DIR" \
      --max-steps 15
  else
    echo "Reusing prepared evaluation data: $EVAL_DATA_DIR"
  fi

  require_file "$DATA_DIR/dataset_info.json"
  require_file "$DATA_DIR/galaxy_sft_train.jsonl"
  require_file "$DATA_DIR/galaxy_sft_val.jsonl"
  require_file "$EVAL_DATA_DIR/galaxy_eval_test.jsonl"
}

train_lora() {
  prepare_data
  require_absent "$LORA_DIR"
  mkdir -p "$(dirname "$LORA_DIR")"
  cd "$LF_ROOT"
  DISABLE_VERSION_CHECK=1 llamafactory-cli train "$TRAIN_CONFIG" \
    model_name_or_path="$BASE_MODEL" \
    dataset_dir="$DATA_DIR" \
    output_dir="$LORA_DIR" \
    2>&1 | tee "$REPO/train/llamafactory/clean_bf16_lora_v1_train.log"
  cd "$REPO"
  require_file "$LORA_DIR/adapter_config.json"
}

merge_lora() {
  require_file "$LORA_DIR/adapter_config.json"
  require_absent "$MERGED_DIR"
  mkdir -p "$(dirname "$MERGED_DIR")"
  cd "$LF_ROOT"
  DISABLE_VERSION_CHECK=1 llamafactory-cli export "$MERGE_CONFIG" \
    model_name_or_path="$BASE_MODEL" \
    adapter_name_or_path="$LORA_DIR" \
    export_dir="$MERGED_DIR" \
    2>&1 | tee "$REPO/train/llamafactory/clean_bf16_lora_v1_merge.log"
  cd "$REPO"
  require_file "$MERGED_DIR/config.json"
}

load_dotenv_for_vlm() {
  if [[ -f "$REPO/.env" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "$REPO/.env"
    set +a
  fi
  [[ -n "${OPENAI_API_KEY:-}" ]] || {
    echo "OPENAI_API_KEY is unavailable; check $REPO/.env before VLM evaluation." >&2
    exit 1
  }
}

evaluate_models() {
  prepare_data
  require_file "$LORA_DIR/adapter_config.json"
  require_file "$MERGED_DIR/config.json"
  require_absent "$ADAPTER_EVAL"
  require_absent "$MERGED_EVAL"
  require_absent "$ADAPTER_EXEC"
  require_absent "$MERGED_EXEC"
  mkdir -p "$EVAL_ROOT"

  python -u -m eval.run_eval \
    --eval-data "$EVAL_DATA_DIR/galaxy_eval_test.jsonl" \
    --model-path "$BASE_MODEL" \
    --adapter-path "$LORA_DIR" \
    --out-dir "$ADAPTER_EVAL" \
    --max-new-tokens 4096 \
    --no-4bit \
    2>&1 | tee "$EVAL_ROOT/offline_adapter.log"

  python -u -m eval.run_eval \
    --eval-data "$EVAL_DATA_DIR/galaxy_eval_test.jsonl" \
    --model-path "$MERGED_DIR" \
    --adapter-path none \
    --out-dir "$MERGED_EVAL" \
    --max-new-tokens 4096 \
    --no-4bit \
    2>&1 | tee "$EVAL_ROOT/offline_merged.log"

  python -m eval.compare_prediction_runs \
    --left "$ADAPTER_EVAL/predictions.jsonl" \
    --right "$MERGED_EVAL/predictions.jsonl" \
    --report "$COMPARE_REPORT"

  load_dotenv_for_vlm

  python -u -m eval.run_exec_eval \
    --input-dir "$E7" \
    --test-galaxies "$TEST_GALAXIES" \
    --out-dir "$ADAPTER_EXEC" \
    --threshold "$THRESHOLD" \
    --reuse-predictions "$ADAPTER_EVAL/predictions.jsonl" \
    --use-vlm \
    --vlm-model "$VLM_MODEL" \
    2>&1 | tee "$EVAL_ROOT/exec_adapter_vlm.log"

  python -u -m eval.run_exec_eval \
    --input-dir "$E7" \
    --test-galaxies "$TEST_GALAXIES" \
    --out-dir "$MERGED_EXEC" \
    --threshold "$THRESHOLD" \
    --reuse-predictions "$MERGED_EVAL/predictions.jsonl" \
    --use-vlm \
    --vlm-model "$VLM_MODEL" \
    2>&1 | tee "$EVAL_ROOT/exec_merged_vlm.log"

  echo "Pipeline evaluation completed."
  echo "Merge comparison: $COMPARE_REPORT"
  echo "Adapter report: $ADAPTER_EXEC/exec_eval_report.json"
  echo "Merged report:  $MERGED_EXEC/exec_eval_report.json"
}

case "$STAGE" in
  prepare)
    prepare_data
    ;;
  train)
    train_lora
    ;;
  merge)
    merge_lora
    ;;
  eval)
    evaluate_models
    ;;
  all)
    train_lora
    merge_lora
    evaluate_models
    ;;
  *)
    echo "Usage: $0 {prepare|train|merge|eval|all}" >&2
    exit 2
    ;;
esac
