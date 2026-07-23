"""
Option A VLM reward for step-level execution eval.

调用 pipeline 原生 `calculate_reward_model_with_param` 完成"动作前 vs 动作后"改进判断。
prompt / 知识 / JSON 输出格式**一字不改**——语义直接对应原设计。

Semantic:
  Input: (parent state) → 模型 action → GALFIT → (model_new state)
  VLM 判断: is model_new an improvement over parent? → improvement ∈ {0, 1}

对比 vlm_compare.py（已废弃）：
  vlm_compare 是 Option B 语义（"model 跟 expert 一样好吗"），需要改 prompt 核心问句，
  违反"不改知识"约束，且在多路径分解下有系统性偏见。见 eval/评测体系设计.md 2.1 和 3.10。
"""

from data_gen.reward import calculate_reward_model_with_param


def vlm_reward_for_step(
    parent_residual_image_path: str,
    parent_summary_path: str,
    model_new_residual_image_path: str,
    model_new_summary_path: str,
    model_name: str = "gemini-3.1-pro-preview",
    api_key: str = None,
    **kwargs,
) -> dict:
    """
    调用 pipeline 原生 VLM reward，Option A 语义。

    Args:
        parent_residual_image_path: 父节点残差图 PNG（动作前状态）
        parent_summary_path:        父节点 summary.md
        model_new_residual_image_path: 模型动作后 GALFIT 输出的残差图 PNG
        model_new_summary_path:     模型动作后的 summary.md
        model_name: VLM 模型名（默认 gemini-3.1-pro-preview，跟 pipeline 一致）
        api_key: OPENAI_API_KEY（None 则从环境变量读）
        **kwargs: 转给 calculate_reward_model_with_param 的其他参数
                  （temperature, max_tokens, timeout, confidence_threshold）

    Returns:
        dict with keys:
          improvement           ∈ {0, 1}   核心 binary label
          improvement_source    "residual_driven" | "metric_driven" | "none"
          residual_improved     bool
          param_plausible       bool
          metric_consistent     bool
          image_text_consistent bool
          residual_improvement_level  "clear_improvement" | "slight_improvement" | "no_improvement" | "worse"
          hard_warnings         list of warning tags
          chisq_trend           "decreased" | "increased" | "unchanged"
          chisq_nu_trend        same
          param_issues          list
          metric_issues         list
          reason                str  VLM 判断依据
          confidence            float 0~1
          usage                 API token cost
    """
    return calculate_reward_model_with_param(
        prev_residual_image_path=parent_residual_image_path,
        next_residual_image_path=model_new_residual_image_path,
        prev_summary_path=parent_summary_path,
        new_summary_path=model_new_summary_path,
        model_name=model_name,
        api_key=api_key,
        **kwargs,
    )
