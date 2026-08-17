#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="${PROJECT_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
OUT_ROOT="${1:-$PROJECT_ROOT/eval/reward_version_benchmark_20260817}"
THRESHOLD=0.05139489475137804
E7="$PROJECT_ROOT/output/E7_full__vlm_proposal_gemini-3.1-pro-preview_vlm_reward_gemini-3.1-pro-preview_hist"

if [[ -e "$OUT_ROOT" ]]; then
  echo "Output already exists; choose another path: $OUT_ROOT"
  exit 1
fi
mkdir -p "$OUT_ROOT/e7" "$OUT_ROOT/e1_e6"
cd "$PROJECT_ROOT"
export PYTHONPATH="$PROJECT_ROOT:${PYTHONPATH:-}"

printf '\n========== 1. E7_full binary alignment ==========\n'
for version in v11 v12.4 v12.5; do
  safe_version="${version//./_}"
  python -u -m eval.validate_reward_alignment \
    --input-dir "$E7" \
    --out-dir "$OUT_ROOT/e7/$safe_version" \
    --val-ratio 0.7 \
    --threshold "$THRESHOLD" \
    --reward-version "$version" \
    2>&1 | tee "$OUT_ROOT/e7/$safe_version.log"
done

printf '\n========== 2. E1-E6 replay and GroupGate ==========\n'
E1_E6=(
  "$PROJECT_ROOT/output/E1__rule_based_proposal_vlm_reward_gemini-3.1-pro-preview__20260626_173004"
  "$PROJECT_ROOT/output/E2__expert_guided_proposal_vlm_reward_gemini-3.1-pro-preview__20260626_173138"
  "$PROJECT_ROOT/output/E3__vlm_proposal_gemini-3.1-pro-preview_vlm_reward_gemini-3.1-pro-preview_hist__20260629_145453"
  "$PROJECT_ROOT/output/E4__vlm_proposal_gemini-3.1-pro-preview_vlm_reward_gemini-3.1-pro-preview_experthint_hist__20260629_145217"
  "$PROJECT_ROOT/output/E5__vlm_proposal_gemini-3.1-pro-preview_vlm_reward_gemini-3.1-pro-preview_hist__20260630_093119"
  "$PROJECT_ROOT/output/E6__vlm_proposal_gemini-3.1-pro-preview_vlm_reward_gemini-3.1-pro-preview_hist__20260630_093230"
)
INPUT_ARGS=()
for input_dir in "${E1_E6[@]}"; do
  [[ -d "$input_dir" ]] || { echo "Missing E1-E6 directory: $input_dir"; exit 1; }
  INPUT_ARGS+=(--input-dir "$input_dir")
done
for version in v11 v12.4 v12.5; do
  safe_version="${version//./_}"
  replay="$OUT_ROOT/e1_e6/${safe_version}_replay.jsonl"
  python -u -m eval.prepare_grpo_replay \
    "${INPUT_ARGS[@]}" \
    --reward-version "$version" \
    --output "$replay"
  python -u -m eval.validate_grpo_reward \
    --input "$replay" \
    --report "$OUT_ROOT/e1_e6/${safe_version}_report.json" \
    --details "$OUT_ROOT/e1_e6/${safe_version}_details.jsonl" \
    --threshold "$THRESHOLD" \
    2>&1 | tee "$OUT_ROOT/e1_e6/${safe_version}.log"
done

printf '\n========== 3. Frozen-SFT on-policy comparison ==========\n'
ONPOLICY="$PROJECT_ROOT/eval/grpo_onpolicy_sft_n8_seed42"
RESIDUAL="$PROJECT_ROOT/eval/residual_v12_5_onpolicy_n8_audit_20260817/onpolicy_residual_scores.jsonl"
RESIDUAL_CONFIG="$PROJECT_ROOT/eval/reward_alignment_v12_5_pathfix_20260817/residual_model_config.json"
ONPOLICY_ARGS=(
  --parents "$ONPOLICY/parents.jsonl"
  --predictions "$ONPOLICY/predictions.jsonl"
  --rollouts-vlm "$ONPOLICY/rollouts_vlm.jsonl"
  --out-dir "$OUT_ROOT/onpolicy_n8"
  --threshold "$THRESHOLD"
)
if [[ -f "$RESIDUAL" && -f "$RESIDUAL_CONFIG" ]]; then
  ONPOLICY_ARGS+=(--residual-scores "$RESIDUAL" --residual-model-config "$RESIDUAL_CONFIG")
else
  echo "Residual-only inputs not found; final V12.5 is still evaluated, residual diagnostics are skipped."
fi
python -u -m eval.compare_grpo_reward_versions "${ONPOLICY_ARGS[@]}" \
  2>&1 | tee "$OUT_ROOT/onpolicy_n8.log"

printf '\n========== 4. Step-300 rule-only failure coverage ==========\n'
STEP300_DIR="${STEP300_DIR:-$PROJECT_ROOT/eval/exec_eval_grpo_full_sft_rl_lora_v2_step300_vlm}"
STEP300_WORK_ROOT="${STEP300_WORK_ROOT:-$PROJECT_ROOT/exec_eval_grpo_full_sft_rl_lora_v2_step300_vlm_galfit_work}"
if [[ -f "$STEP300_DIR/predictions.jsonl" \
   && -f "$STEP300_DIR/exec_eval_details.jsonl" \
   && -d "$STEP300_WORK_ROOT" ]]; then
  python -u -m eval.audit_grpo_rule_only_coverage \
    --trajectory-dir "$E7" \
    --predictions "$STEP300_DIR/predictions.jsonl" \
    --exec-details "$STEP300_DIR/exec_eval_details.jsonl" \
    --work-root "$STEP300_WORK_ROOT" \
    --out-dir "$OUT_ROOT/step300_rule_only" \
    --threshold "$THRESHOLD" \
    2>&1 | tee "$OUT_ROOT/step300_rule_only.log"
else
  echo "Complete step-300 inputs were not found; section 4 is skipped:"
  echo "  STEP300_DIR=$STEP300_DIR"
  echo "  STEP300_WORK_ROOT=$STEP300_WORK_ROOT"
fi

printf '\nUnified benchmark completed: %s\n' "$OUT_ROOT"