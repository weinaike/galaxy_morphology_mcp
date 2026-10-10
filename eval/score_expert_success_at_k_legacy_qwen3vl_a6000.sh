#!/usr/bin/env bash
set -euo pipefail

PROJECT=${PROJECT:-/media/zhongling/wyh/GalDecomp_Gen}
FROZEN=${FROZEN:-$PROJECT/eval/expert_benchmark_final_no_stop_v1}
MODEL_KIND=${1:?Usage: bash eval/score_expert_success_at_k_legacy_qwen3vl_a6000.sh base|full_sft [K]}
MAX_STEPS=${2:-4}
case "$MODEL_KIND" in base|full_sft) ;; *) echo "Unknown model kind: $MODEL_KIND" >&2; exit 2 ;; esac

cd "$PROJECT"
source /media/data/anaconda3/etc/profile.d/conda.sh
conda activate llama-factory

NAME="qwen3vl8b_${MODEL_KIND}_k${MAX_STEPS}_v1"
TRANSFER="$PROJECT/eval/transfers/qwen3vl8b_${MODEL_KIND}_success_k${MAX_STEPS}_v1.tar.gz"
REMOTE="si00237244jv/default/GalDecomp_Gen/eval/qwen3vl8b_${MODEL_KIND}_success_k${MAX_STEPS}_v1.tar.gz"
ROLLOUT="$PROJECT/eval/expert_success_rollout/$NAME"
OUT="$PROJECT/eval/expert_success_at_k/${NAME}_legacy_bidirectional_v1"

if [[ ! -s "$ROLLOUT/rollout_meta.json" || ! -s "$ROLLOUT/task_rollouts.jsonl" ]]; then
  mkdir -p "$(dirname "$TRANSFER")"
  if [[ ! -s "$TRANSFER" ]]; then
    mc cp "$REMOTE" "$TRANSFER"
  fi
  tar --exclude='*/input.fit' --exclude='*/mask.fit' \
    --exclude='*/psf.fit' --exclude='*/sigma.fit' -xzf "$TRANSFER" -C "$PROJECT"
fi
test -s "$ROLLOUT/rollout_meta.json"
test -s "$ROLLOUT/task_rollouts.jsonl"
test -s "$FROZEN/task_execution_a6000.jsonl"

RESUME_ARGS=()
if [[ -e "$OUT" ]]; then
  if [[ "${RESUME_SUCCESS_K_SCORE:-false}" == true ]]; then
    RESUME_ARGS+=(--resume)
  else
    echo "Output exists: $OUT; set RESUME_SUCCESS_K_SCORE=true to resume." >&2
    exit 1
  fi
fi
mkdir -p "$(dirname "$OUT")"

if [[ -z "${VLM_API_KEY:-${OPENAI_API_KEY:-}}" ]]; then
  read -rsp 'VLM API Key: ' VLM_API_KEY
  echo
  export VLM_API_KEY
  trap 'unset VLM_API_KEY' EXIT
fi

python -u -m eval.score_expert_success_at_k_a6000 \
  --rollout-dir "$ROLLOUT" \
  --task-manifest "$FROZEN/task_execution_a6000.jsonl" \
  --split "$FROZEN/split.json" \
  --out-dir "$OUT" \
  --vlm-model gemini-3.1-pro-preview \
  --judge legacy_bidirectional_v1 \
  "${RESUME_ARGS[@]}" \
  2>&1 | tee "${OUT}.log"

test -s "$OUT/success_at_k_report.json"
echo "Success@K report: $OUT/success_at_k_report.json"
