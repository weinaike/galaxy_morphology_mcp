#!/bin/bash
for exp_dir in \
  output/E1__rule_based_proposal_vlm_reward_gemini-3.1-pro-preview__20260626_173004 \
  output/E2__expert_guided_proposal_vlm_reward_gemini-3.1-pro-preview__20260626_173138 \
  output/E3__vlm_proposal_gemini-3.1-pro-preview_vlm_reward_gemini-3.1-pro-preview_hist__20260629_145453 \
  output/E4__vlm_proposal_gemini-3.1-pro-preview_vlm_reward_gemini-3.1-pro-preview_experthint_hist__20260629_145217 \
  output/E5__vlm_proposal_gemini-3.1-pro-preview_vlm_reward_gemini-3.1-pro-preview_hist__20260630_093119 \
  output/E6__vlm_proposal_gemini-3.1-pro-preview_vlm_reward_gemini-3.1-pro-preview_hist__20260630_093230; do
  echo "========== $(basename $exp_dir | cut -d'_' -f1) =========="
  python -m data_gen.evaluate_component_accuracy \
    --input "$exp_dir" \
    --data-dir /media/zhongling/wyh/GalDecomp_Gen/gadotti_data/
  echo ""
done