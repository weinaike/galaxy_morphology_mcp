"""
⚠️ DEPRECATED ⚠️  请改用 eval/vlm_reward.py（Option A 语义）。

原设计：Option B 语义（"model 跟 expert 一样好吗"）——把原 prompt 的 "improvement over"
改成 "comparable quality to"。

废弃原因（见 eval/评测体系设计.md 2.1 和 3.10）：
1. **改了 prompt 核心问句 = 改了知识**，违反"不改任何知识"约束
2. **多路径分解下有系统性偏见**——模型选了不同但合法的动作会被误判为"不如专家"
3. **复用原 prompt 会得到错语义**——语义变成"模型是不是超过专家"，跟"跟专家一样好"反了

保留此文件仅为兼容旧调用点。新代码请调用 eval/vlm_reward.py 的 vlm_reward_for_step()。
"""

import os
from typing import Optional

from data_gen.reward import (
    encode_image,
    get_image_mime_type,
    read_summary_md,
    extract_json_from_text,
)


def _build_compare_prompt(model_summary_content: str, gt_summary_content: str) -> str:
    return """
You are an astronomer experienced in GALFIT galaxy component decomposition.

You will be given exactly two images in order:

* Image 1: model-predicted fitting result.
* Image 2: expert ground-truth fitting result.

Both images are results of applying different actions to the SAME previous state.
Each image may be a residual-only image or a full GALFIT comparison figure.
If multiple panels are present, focus primarily on the residual panel, usually titled "Residual/σ", "Residual", "data-model", or similar.
Ignore the observed data panel, fitted model panel, surface-brightness profile panel, and blank diagnostic panels unless needed only to check image-summary correspondence.

Your task is to determine whether Image 1 (model prediction) achieves comparable quality to Image 2 (expert ground truth).
This is a quality comparison at a single step, not a sequential improvement judgement.

The final decision must consider:

1. residual image similarity between model and expert;
2. physical plausibility of the model-predicted parameters;
3. statistical reasonability of chisq, chisq/nu, and BIC compared to expert;
4. image-summary consistency;
5. severe residual or fitting warnings.

========================================
Input Pairing
=============

Image 1 corresponds only to the model-predicted fitting summary.
Image 2 corresponds only to the expert ground-truth fitting summary.

Do not use parameters or warnings from one summary to interpret the other image.

Before comparing the two results, internally check whether each image appears broadly consistent with its corresponding summary.
If the image-summary correspondence is too uncertain to support a reliable comparison, set:

quality_match = 0
image_text_consistent = false

Model-predicted fitting summary for Image 1:

""" + model_summary_content + """

Expert ground-truth fitting summary for Image 2:

""" + gt_summary_content + """

========================================
Part 1: Residual Image Comparison
=================================

Compare the residual structures between model prediction and expert ground truth.

Focus on:

* central positive or negative residuals;
* dipole-like red/blue residuals;
* compact clumps near the galaxy center;
* ring, spiral, bar, lens-like, or asymmetric structures;
* coherent large-scale residual patterns.

Ignore unchanged features likely unrelated to the target galaxy, such as masked regions, foreground stars, isolated artifacts far from the galaxy center, or unchanged random background noise.

Set similarity_level as one of:

* "equivalent": model residual is visually indistinguishable from or as clean as expert residual;
* "slightly_worse": model residual shows slightly more galaxy-related structures than expert;
* "much_worse": model residual shows substantially more galaxy-related structures or new artifacts;
* "better": model residual is actually cleaner than expert residual.

Set residual_similar = true for "equivalent" or "slightly_worse" or "better".
Set residual_similar = false for "much_worse".

========================================
Part 2: Hard Warning Check
==========================

Check whether Image 1 (model prediction) has any severe warning.

Hard Warning tags are:

* "fit_not_converged": the summary indicates that the fit did not converge.
* "linear_gradient": Image 1 residual shows a strong large-scale linear background gradient.
* "chaotic_dark_patches": Image 1 residual contains large irregular dark patches.
* "diffuse_fragments": Image 1 residual contains widespread diffuse fragments unrelated to meaningful galaxy structure.
* "unmasked_artifact": Image 1 residual is dominated by unmasked stars, cosmic rays, bad pixels, saturated sources, or other artifacts.

If Image 1 has a severe Hard Warning, set quality_match = 0.
Record all warning tags in hard_warnings.
If there are no Hard Warnings, use an empty list.

========================================
Part 3: Parameter Plausibility
==============================

Evaluate whether the model-predicted parameters are physically plausible.

Set param_plausible = false if there are serious issues such as:

* Sérsic index n < 0.1 or n > 8, unless physically justified, such as BCG/cD galaxies;
* effective radius Re < 0.2 pixel;
* effective radius Re unreasonably large, approaching the image size;
* axis ratio q < 0.05 or q > 1.0;
* large center offsets between components that should be co-centered;
* implausible size hierarchy, such as an unreasonable bulge/disk/bar relation;
* redundant or nearly duplicated components;
* negligible-flux components, for example mag difference > 5 from major components;
* parameter runaway, severe degeneracy, or unstable parameters;
* more components without residual or metric support.

Set param_plausible = true only if the model-predicted parameters are physically reasonable.

Record specific issues in param_issues.
If there are no parameter issues, use an empty list.

========================================
Part 4: Metric Comparison
============================

Compare chisq, chisq/nu, and BIC between the model prediction and expert ground truth.

Rules:

* chisq is better when smaller.
* chisq/nu is better when smaller.
* BIC is better when smaller.
* chisq measures total residual mismatch.
* chisq/nu measures normalized fitting quality.
* BIC penalizes model complexity.

Metric interpretation:

1. If model metrics are similar to or better than expert metrics:

   * Accept as quality match if parameters are plausible and residuals are comparable.

2. If model metrics are slightly worse than expert metrics:

   * Accept if the residuals are visually comparable and parameters are plausible.
   * Small metric differences may be tolerated for visually comparable residuals.

3. If model chisq/nu is clearly worse than expert:

   * Be conservative.
   * Even if BIC is comparable, do not accept if chisq/nu degradation is large.

4. If model BIC is much worse than expert:

   * The model may be using unnecessary components or poor parameter choices.
   * Do not accept if accompanied by unphysical parameters.

Set metric_comparable = true if:

* model metrics are similar to or better than expert metrics; or
* model metrics are slightly worse but residuals are visually comparable and parameters are plausible.

Set metric_comparable = false if:

* model chisq/nu is clearly worse than expert;
* model BIC is much worse without justification;
* the fit does not converge;
* metric differences suggest overfitting or poor fitting quality.

Determine:

* chisq_comparison: "model_better", "comparable", "model_worse", or "unavailable"
* chisq_nu_comparison: "model_better", "comparable", "model_worse", or "unavailable"

Do not add a new JSON field for BIC.
If BIC is available, use it in the final decision and mention its comparison in reason or metric_issues.

Record specific metric issues in metric_issues.
If there are no metric issues, use an empty list.

========================================
Combined Decision Rule
======================

Set quality_match = 1 if all are true:

* residual_similar = true;
* param_plausible = true;
* metric_comparable = true;
* image_text_consistent = true;
* Image 1 has no severe Hard Warning;
* the model prediction achieves comparable or better fitting quality to the expert.

Set quality_match = 0 if any are true:

* Image 1 residual is much worse than Image 2;
* Image 1 introduces severe residual failures;
* param_plausible = false;
* metric_comparable = false;
* image_text_consistent = false;
* model chisq/nu is clearly worse and residuals confirm the degradation;
* the model prediction is too ambiguous or clearly inferior to the expert result.

Be conservative:
When in doubt about whether the model prediction matches expert quality, prefer quality_match = 0.

========================================
Output Format
=============

Output strictly in JSON format.
Do not include Markdown.
Do not include any text outside the JSON.

Required JSON format:

{
"quality_match": 1,
"similarity_level": "slightly_worse",
"confidence": 0.75,
"residual_similar": true,
"param_plausible": true,
"metric_comparable": true,
"image_text_consistent": true,
"hard_warnings": [],
"chisq_comparison": "comparable",
"chisq_nu_comparison": "comparable",
"param_issues": [],
"metric_issues": [],
"reason": "Model residual shows slightly more central structure than expert but is broadly comparable. Parameters are physically plausible. chisq/nu and BIC are within acceptable range of expert values."
}

Definitions:

* quality_match: integer, must be either 0 or 1. Final decision considering residual similarity, physical plausibility, metric comparison, image-summary consistency, and hard warnings.
* similarity_level: one of ["equivalent", "slightly_worse", "much_worse", "better"]. Based on residual comparison only.
* confidence: float between 0 and 1.
* residual_similar: boolean. true if model residual is visually comparable to or better than expert residual.
* param_plausible: boolean. true if model-predicted parameters are physically reasonable.
* metric_comparable: boolean. true if model metrics are comparable to or better than expert metrics.
* image_text_consistent: boolean. true if both image-summary pairs appear broadly consistent.
* hard_warnings: list of strings. Each string must be one of ["fit_not_converged", "linear_gradient", "chaotic_dark_patches", "diffuse_fragments", "unmasked_artifact"]. Empty list if none.
* chisq_comparison: one of ["model_better", "comparable", "model_worse", "unavailable"].
* chisq_nu_comparison: one of ["model_better", "comparable", "model_worse", "unavailable"].
* param_issues: list of strings. Each string describes one specific parameter or physical issue. Empty list if none.
* metric_issues: list of strings. Each string describes one specific metric issue. Empty list if none.
* reason: concise explanation covering residual comparison, parameter assessment, image-summary correspondence, hard warnings, and metric comparison.
  """


def compare_model_vs_gt(
    model_residual_image_path: str,
    gt_residual_image_path: str,
    model_summary_path: str,
    gt_summary_path: str,
    model_name: str = "gemini-3.1-pro-preview",
    api_key: str = None,
    temperature: float = 0.1,
    max_tokens: int = 8192,
    timeout: int = 300,
    confidence_threshold: float = 0.6,
):
    """
    Compare model's GALFIT result with expert ground truth using VLM.

    Adapted from calculate_reward_model_with_param:
    - Original: compares previous step vs next step (improvement check)
    - This: compares model prediction vs expert GT (quality match)
    - Domain knowledge is identical; only the comparison framing changed.

    Returns dict with quality_match (0/1), similarity_level, confidence, etc.
    """
    if api_key is None:
        api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("api_key is None. Set OPENAI_API_KEY.")

    if not os.path.exists(model_residual_image_path):
        raise FileNotFoundError(f"Model residual not found: {model_residual_image_path}")
    if not os.path.exists(gt_residual_image_path):
        raise FileNotFoundError(f"GT residual not found: {gt_residual_image_path}")

    model_summary_content = ""
    if model_summary_path and os.path.exists(model_summary_path):
        try:
            model_summary_content = read_summary_md(model_summary_path)
        except Exception as e:
            model_summary_content = f"(Model summary unavailable: {e})"
    else:
        model_summary_content = "(Model summary unavailable)"

    gt_summary_content = ""
    if gt_summary_path and os.path.exists(gt_summary_path):
        try:
            gt_summary_content = read_summary_md(gt_summary_path)
        except Exception as e:
            gt_summary_content = f"(Expert summary unavailable: {e})"
    else:
        gt_summary_content = "(Expert summary unavailable)"

    prompt = _build_compare_prompt(model_summary_content, gt_summary_content)

    from data_gen.reward import get_openAI_response_two_images
    raw_response, usage = get_openAI_response_two_images(
        api_key=api_key,
        model_name=model_name,
        prompt=prompt,
        prev_image_path=model_residual_image_path,
        next_image_path=gt_residual_image_path,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=timeout,
    )

    if raw_response is None or not str(raw_response).strip():
        raise ValueError("Model returned empty response.")

    result = extract_json_from_text(raw_response)

    quality_match = int(result.get("quality_match", 0))
    confidence = float(result.get("confidence", 0.0))
    residual_similar = bool(result.get("residual_similar", False))
    param_plausible = bool(result.get("param_plausible", True))
    metric_comparable = bool(result.get("metric_comparable", True))
    image_text_consistent = bool(result.get("image_text_consistent", True))
    hard_warnings = result.get("hard_warnings", [])

    if quality_match not in [0, 1]:
        quality_match = 0

    if quality_match == 1 and confidence < confidence_threshold:
        quality_match = 0
        result["confidence_filter_applied"] = True
    else:
        result["confidence_filter_applied"] = False

    if quality_match == 1 and not param_plausible:
        quality_match = 0
        result["param_override_applied"] = True
    else:
        result["param_override_applied"] = False

    if quality_match == 1 and not metric_comparable:
        quality_match = 0
        result["metric_override_applied"] = True
    else:
        result["metric_override_applied"] = False

    if quality_match == 1 and not image_text_consistent:
        quality_match = 0
        result["image_text_override_applied"] = True
    else:
        result["image_text_override_applied"] = False

    if quality_match == 1 and hard_warnings:
        quality_match = 0
        result["hard_warning_override_applied"] = True
    else:
        result["hard_warning_override_applied"] = False

    result["quality_match"] = quality_match
    result["usage"] = usage
    result["model_image_path"] = model_residual_image_path
    result["gt_image_path"] = gt_residual_image_path

    return result
