# data_gen/reward.py
import math
from unittest import result
from urllib import response
import numpy as np
import os
import re
import json
import base64
import mimetypes
import requests
import time
from typing import Optional, Dict, Any, Tuple
from PIL import Image

def calculate_reward_rule(old_metrics: dict, new_metrics: dict, action: dict, step: int, 
                     initial_params: dict = None, current_params: dict = None) -> tuple:
    """
    计算 5 个维度的 Reward 并汇总。
    返回: (总收益差值 Delta_R, 详细分数本字典 r_detail)
    """
    # ==========================================
    # 权重设定 (需要根据数据量级进行 tuning，确保数量级一致)
    # 建议策略：让 r_chi2 占主导，其余作为惩罚项或结构引导项
    # ==========================================
    w_step = 1.0     # 结构引导权重
    w_bound = 1.0    # 边界惩罚权重
    w_chi2 = 10.0    # 拟合优度权重 (核心)
    w_res = 1.0      # 残差惩罚权重
    w_prior = 0.5    # 偏离惩罚权重

    # ---------------------------------------------------------
    # 1. R_step (结构奖励)
    # 规则：前期鼓励加组件，后期惩罚加组件
    # ---------------------------------------------------------
    r_step = 0.0
    if action["type"] == "A":
        r_step = 1.0 if step <= 5 else -1.0
    elif action["type"] == "B":
        # 删除组件也可以加一点策略，比如惩罚盲目删除
        r_step = -0.5

    # ---------------------------------------------------------
    # 2. R_bound (边界惩罚)
    # 规则：如果进入 5% 死亡区，R_bound = -10
    # 这里做了一个基于 Action 的近似推演（如果传了 params 可以做更精确的拦截）
    # ---------------------------------------------------------
    r_bound = 0.0
    if action["type"] == "C":
        delta_n = action.get("delta_n_val", 0)
        delta_re = action.get("delta_re_factor", 1.0)
        # 如果提议的扰动极其狂野，提前给出惩罚趋势
        if abs(delta_n) > 2.0 or delta_re > 2.0 or delta_re < 0.5:
            r_bound = -5.0

    # ---------------------------------------------------------
    # 3. R_chi2 (拟合优度对数增益 - 最核心物理指标)
    # 使用刚提取的真实 chi2_nu
    # ---------------------------------------------------------
    old_chi2 = old_metrics.get("chi2_nu", 999.0)
    new_chi2 = new_metrics.get("chi2_nu", 999.0)
    
    # 为了防止数量级爆炸，推荐使用对数增益 (Log Gain)
    # 如果 new_chi2 变小（拟合变好），old/new > 1，log10 结果为正，表示奖励！
    if old_chi2 > 0 and new_chi2 > 0:
        r_chi2 = math.log10(old_chi2 / new_chi2)
    else:
        r_chi2 = 0.0
        
    # 如果模拟器崩溃或者 SSIM 判定完全无效，赋予极强惩罚
    if new_chi2 >= 9999.0:
        r_chi2 = -10.0

    # ---------------------------------------------------------
    # 4. R_residual (核心惩罚)
    # 需要读取 FITS 的掩膜方差。这里留出安全默认值 0.0，
    # 待未来你的 pipeline 挂载 FITS 读取函数时替换。
    # ---------------------------------------------------------
    r_residual = 0.0 

    # ---------------------------------------------------------
    # 5. R_prior (偏离惩罚)
    # 防止乱跑，当前参数与初始参数的 L2 距离
    # ---------------------------------------------------------
    r_prior = 0.0

    # ==========================================
    # 最终汇总得分
    # ==========================================
    r_total = (w_step * r_step) + (w_bound * r_bound) + (w_chi2 * r_chi2) + \
              (w_res * r_residual) + (w_prior * r_prior)
              
    r_detail = {
        "r_step": r_step,
        "r_bound": r_bound,
        "r_chi2": r_chi2,
        "r_residual": r_residual,
        "r_prior": r_prior,
        "chi2_nu_old": old_chi2,
        "chi2_nu_new": new_chi2
    }
    
    return r_total, r_detail



def encode_image(image_path: str) -> str:
    """Encode image file as base64 string."""
    with open(image_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")
    
def crop_residual_panel(
    image_path: str,
    output_path: Optional[str] = None,
    overwrite: bool = False,
    pad_left_ratio: float = 0.005,    # 左侧多留一点
    pad_right_ratio: float = 0.07,  # 右侧少留一点
    pad_top_ratio: float = 0.06,
    pad_bottom_ratio: float = 0.03,
) -> str:
    """
    Crop the residual panel from a standard GALFIT 2x3 comparison figure.

    Target panel:
        second row, second column, i.e. residual panel.

    Save as:
        original_name_cutoff.png
    """

    if not os.path.exists(image_path):
        raise FileNotFoundError(f"Image not found: {image_path}")

    image_dir = os.path.dirname(image_path)
    image_name = os.path.basename(image_path)
    stem, _ = os.path.splitext(image_name)

    if stem.endswith("_cutoff"):
        return image_path

    if output_path is None:
        output_path = os.path.join(image_dir, f"{stem}_cutoff.png")

    if os.path.exists(output_path) and not overwrite:
        return output_path

    with Image.open(image_path) as img:
        img = img.convert("RGB")
        width, height = img.size

        panel_w = width / 3.1
        panel_h = height / 1.82

        # second row, second column
        x1 = panel_w
        x2 = 2 * panel_w
        y1 = panel_h
        y2 = height

        pad_left = panel_w * pad_left_ratio
        pad_right = panel_w * pad_right_ratio
        pad_top = panel_h * pad_top_ratio
        pad_bottom = panel_h * pad_bottom_ratio

        left = max(0, int(x1 - pad_left))
        upper = max(0, int(y1 - pad_top))
        right = min(width, int(x2 + pad_right))
        lower = min(height, int(y2 + pad_bottom))

        cropped = img.crop((left, upper, right, lower))
        cropped.save(output_path)

    return output_path

def read_summary_md(summary_md_path: str) -> str:
    """
    Read GALFIT summary markdown file.
    """
    if not os.path.exists(summary_md_path):
        raise FileNotFoundError(f"Summary md file not found: {summary_md_path}")

    with open(summary_md_path, "r", encoding="utf-8") as f:
        return f.read()
    
def get_image_mime_type(image_path: str) -> str:
    """Infer image mime type from file extension."""
    mime_type, _ = mimetypes.guess_type(image_path)
    if mime_type is None:
        # 默认按 png 处理，适合大多数 residual/comparison 图
        mime_type = "image/png"
    return mime_type


def extract_json_from_text(text: str) -> dict:
    """
    Robustly extract JSON object from model response.
    The model is asked to output pure JSON, but this handles accidental extra text.
    """
    text = text.strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if match:
        return json.loads(match.group(0))

    raise ValueError(f"Cannot parse JSON from model response:\n{text}")

def extract_json_from_response(response_text: str) -> Dict[str, Any]:
    """
    Parse JSON from model response.
    Handles pure JSON and JSON wrapped by ```json ... ```.
    """
    text = response_text.strip()

    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:].strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")

        if start != -1 and end != -1 and end > start:
            return json.loads(text[start:end + 1])

        raise ValueError(f"Failed to parse JSON from response: {response_text}")


def get_openAI_response_two_images(
    api_key: str,
    model_name: str,
    prompt: str,
    prev_image_path: str,
    next_image_path: str,
    temperature: float = 0.0,
    max_tokens: int = 512,
    timeout: int = 120,
    url: str = "https://api.road2all.com/v1/chat/completions",
    max_retries: int = 3,
    retry_sleep: int = 10,
):
    """
    Call OpenAI-compatible multimodal API with two explicitly labeled images.
    Add retry mechanism for temporary server errors such as 503.
    """

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    mime_type_1 = get_image_mime_type(prev_image_path)
    mime_type_2 = get_image_mime_type(next_image_path)

    content = [
        {
            "type": "text",
            "text": prompt,
        },
        {
            "type": "text",
            "text": "Image 1: previous-step GALFIT comparison image. Focus on its residual panel.",
        },
        {
            "type": "image_url",
            "image_url": {
                "url": f"data:{mime_type_1};base64,{encode_image(prev_image_path)}"
            },
        },
        {
            "type": "text",
            "text": "Image 2: next-step GALFIT comparison image. Focus on its residual panel.",
        },
        {
            "type": "image_url",
            "image_url": {
                "url": f"data:{mime_type_2};base64,{encode_image(next_image_path)}"
            },
        },
    ]

    data = {
        "model": model_name,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "messages": [
            {
                "role": "user",
                "content": content,
            }
        ],
        "stream": False,
    }

    last_error = None

    for attempt in range(1, max_retries + 1):
        try:
            response = requests.post(
                url,
                headers=headers,
                json=data,
                timeout=timeout,
            )

            
            response.raise_for_status()

            resp_json = response.json()

            return (
                resp_json["choices"][0]["message"]["content"],
                resp_json.get("usage", {})
            )


        except requests.exceptions.HTTPError as e:
            last_error = e
            status_code = e.response.status_code if e.response is not None else None

            print(
                f"    ⚠️ [API HTTPError] attempt {attempt}/{max_retries}, "
                f"status_code={status_code}, error={e}"
            )

            # 这些一般是临时错误，可以重试
            if status_code in [429, 500, 502, 503, 504]:
                if attempt < max_retries:
                    time.sleep(retry_sleep * attempt)
                    continue

            # 401/403/404 这类一般不是临时错误，不建议继续重试
            raise

        except requests.exceptions.RequestException as e:
            last_error = e

            print(
                f"    ⚠️ [API RequestException] attempt {attempt}/{max_retries}, error={e}"
            )

            if attempt < max_retries:
                time.sleep(retry_sleep * attempt)
                continue

            raise

    raise RuntimeError(f"API request failed after {max_retries} retries: {last_error}")

def get_openAI_response_one_image(
    api_key: str,
    model_name: str,
    prompt: str,
    image_path: str,
    temperature: float = 0.0,
    max_tokens: int = 512,
    timeout: int = 120,
    url: str = "https://api.road2all.com/v1/chat/completions",
    max_retries: int = 3,
    retry_sleep: int = 10,
) -> Tuple[str, Dict[str, Any]]:
    """
    Call OpenAI-compatible multimodal API with one image.
    """

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    mime_type, _ = mimetypes.guess_type(image_path)

    if mime_type is None:
        ext = os.path.splitext(image_path)[1].lower()
        if ext in [".jpg", ".jpeg"]:
            mime_type = "image/jpeg"
        elif ext == ".png":
            mime_type = "image/png"
        elif ext == ".webp":
            mime_type = "image/webp"
        else:
            mime_type = "image/png"

    content = [
        {
            "type": "text",
            "text": prompt,
        },
        {
            "type": "text",
            "text": "Image: GALFIT comparison image. Focus only on the residual panel, usually the third panel from the left.",
        },
        {
            "type": "image_url",
            "image_url": {
                "url": f"data:{mime_type};base64,{encode_image(image_path)}"
            },
        },
    ]

    data = {
        "model": model_name,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "messages": [
            {
                "role": "user",
                "content": content,
            }
        ],
        "stream": False,
    }

    last_error = None

    for attempt in range(1, max_retries + 1):
        try:
            response = requests.post(
                url,
                headers=headers,
                json=data,
                timeout=timeout,
            )

            response.raise_for_status()
            resp_json = response.json()

            return (
                resp_json["choices"][0]["message"]["content"],
                resp_json.get("usage", {}),
            )

        except requests.exceptions.HTTPError as e:
            last_error = e
            status_code = e.response.status_code if e.response is not None else None

            print(
                f"    ⚠️ [API HTTPError] attempt {attempt}/{max_retries}, "
                f"status_code={status_code}, error={e}"
            )

            if status_code in [429, 500, 502, 503, 504]:
                if attempt < max_retries:
                    time.sleep(retry_sleep * attempt)
                    continue

            raise

        except requests.exceptions.RequestException as e:
            last_error = e

            print(
                f"    ⚠️ [API RequestException] attempt {attempt}/{max_retries}, error={e}"
            )

            if attempt < max_retries:
                time.sleep(retry_sleep * attempt)
                continue

            raise

    raise RuntimeError(f"API request failed after {max_retries} retries: {last_error}")


def is_good_fit(
    residual_image_path: str,
    param: bool = False,
    summary_md_path: Optional[str] = None,
    model_name: str = "gemini-3.1-pro-preview",
    api_key: str = "sk-orCNeVDDaLy7zNfXvDYx9FX7z5uTdUkbWBJjFWeFDarSysSq",
    temperature: float = 0.0,
    max_tokens: int = 512,
    timeout: int = 120,
    confidence_threshold: float = 0.6,
    max_retries: int = 3,
    retry_sleep: int = 10,
) -> Dict[str, Any]:
    """
    Judge whether a GALFIT residual image is good_fit.

    Args:
        residual_image_path:
            Path to residual image or GALFIT comparison image.

        param:
            Whether to use GALFIT summary markdown information.
            False: judge only by residual image.
            True: judge by residual image + summary_md_path content.

        summary_md_path:
            Path to *_summary.md file.
            Only used when param=True.

        model_name:
            Multimodal model name.

        api_key:
            API key.

        temperature:
            Recommend 0.0 for stable evaluation.

        confidence_threshold:
            If model outputs good_fit=1 but confidence is lower than this threshold,
            force good_fit to 0.

    Returns:
        {
            "good_fit": 0 or 1,
            "raw_good_fit": 0 or 1,
            "confidence": float,
            "reason": str,
            "usage": dict,
            "raw_response": str,
            "image_path": str,
            "summary_md_path": str or None,
            "param_used": bool
        }
    """

    if param:
        if summary_md_path is None:
            print("    ⚠️ param=True but summary_md_path is None. Fall back to residual-only judgement.")
            param_used = False
            summary_md_text = None
        else:
            summary_md_text = read_summary_md(summary_md_path)
            param_used = True
    else:
        param_used = False
        summary_md_text = None

    prompt = """
You are an astronomer experienced in GALFIT galaxy component decomposition.

You will be given exactly one image.

The image may be a full GALFIT comparison figure with multiple panels.
If multiple panels are present, focus mainly on the residual panel, usually the third panel from the left, titled "Residual/σ", "Residual", or similar.
Ignore the original data panel, fitted model panel, surface-brightness profile panel, and any blank diagnostic panels, unless they help identify the residual panel.

Task:
Evaluate whether the GALFIT decomposition has reached an acceptable final fit based mainly on the residual image.

Current modeling scope:
The current GALFIT model is mainly intended to describe four component types:

* bulge
* disk
* bar
* PSF / compact central source

Do not require the model to perfectly fit all possible galaxy substructures.
Weak spiral arms, faint diffuse features, minor asymmetries, weak outer clumps, or small background fluctuations may remain in the residual image and can still be acceptable, as long as they do not indicate a major missing or poorly fitted bulge, disk, bar, or PSF component.

Core idea:
Do NOT directly judge the image only by whether it looks perfectly white-noise-like.
Instead, evaluate the residual image using a structured scoring scheme.

The final decision should be based on:

1. whether any hard failure pattern is present;
2. five visual residual quality scores;
3. the weighted final_score;
4. whether final_score passes the threshold.

Scoring dimensions:
Assign each score as a float between 0 and 1.

1. central_residual_score:
   Measures whether the PSF, compact central source, and central bulge region are well fitted.

* 1.0: center is clean or only has tiny noise-level residuals.
* 0.7-0.9: very weak central residuals exist, but they are not visually dominant and do not form a clear red/blue pair.
* 0.4-0.6: noticeable central residuals exist, such as compact red/blue blobs, mild dipole, or moderate central over/under-subtraction.
* 0.0-0.3: strong central residuals, compact point-like residual, clear red/blue positive-negative pair, central dipole, or severe PSF/bulge mismatch.

2. bar_residual_score:
   Measures whether bar-like structures are reasonably modeled.

* 1.0: no obvious bar-like, X-shaped, or elongated central residual.
* 0.7-0.9: only weak elongated residuals that are not dominant.
* 0.4-0.6: noticeable bar-like or elongated residuals remain.
* 0.0-0.3: strong bar-like, X-shaped, or elongated symmetric residual indicating a poorly fitted or missing bar.

3. disk_residual_score:
   Measures whether the disk and large-scale galaxy structure are reasonably modeled.

* 1.0: no obvious large-scale disk residual, ring, bullseye pattern, or strong gradient.
* 0.7-0.9: weak outer spiral-like or diffuse residuals remain but are not dominant.
* 0.4-0.6: noticeable disk-like residuals, ring-like features, elliptical residuals, or mild large-scale mismatch.
* 0.0-0.3: strong disk mismatch, strong ring/bullseye structure, large-scale over/under-subtraction, or obvious systematic gradient.

4. artifact_score:
   Measures whether artifacts or masking problems affect the fit judgment.

* 1.0: no obvious foreground star, diffraction spike, cosmic ray, bad pixel, or unmasked contaminant affecting the residual.
* 0.7-0.9: minor artifacts exist but do not affect the galaxy fitting judgment.
* 0.4-0.6: noticeable artifacts may affect the judgment.
* 0.0-0.3: severe artifacts, unmasked stars, bad pixels, or contaminants dominate or strongly affect the galaxy region.

5. overall_noise_score:
   Measures how close the residual image is to random noise overall.

* 1.0: residual is mostly random white-noise-like.
* 0.7-0.9: mostly noise-like, with only weak non-dominant structures.
* 0.4-0.6: several visible residual structures remain.
* 0.0-0.3: residual is dominated by coherent, structured, or systematic patterns.

Weighted final score:
Compute final_score using the following weighting:

final_score =
0.35 * central_residual_score

* 0.20 * disk_residual_score
* 0.20 * bar_residual_score
* 0.15 * overall_noise_score
* 0.10 * artifact_score

The central residual score has the highest weight because PSF, compact central source, and bulge residuals are especially important for deciding whether the fit can stop.

Hard failure patterns:
Set hard_failure = true if any of the following strong failure patterns are clearly present:

* strong central compact point-like residual at or very near the galaxy center;
* clear adjacent red/blue positive-negative residual blobs near the center;
* clear central dipole or bipolar residual pattern;
* strong central over-subtraction or under-subtraction around the bulge;
* clear bar-like, X-shaped, or elongated symmetric central residual;
* strong large-scale disk-like residual, strong elliptical residual, or obvious disk over/under-subtraction;
* strong radial bullseye pattern or strong closed ring structure;
* strong large-scale linear gradient across the galaxy region;
* dominant clumpy irregular residuals in the galaxy region;
* severe dark patchy residuals suggesting strong over-subtraction;
* prominent arcs, shells, tidal tails, or diffuse fragments that dominate the residual;
* obvious unmasked artifact, foreground star, diffraction spike, cosmic ray, bad pixel, or contaminant that strongly affects the fit judgment.

Important tolerance rules:
Do NOT set hard_failure = true for weak or minor residuals.

The following should NOT be considered hard failure if weak:

* faint spiral-like residuals;
* weak diffuse outskirts;
* weak outer clumps far from the center;
* mild background texture;
* slight asymmetry;
* tiny weak central speck close to the surrounding noise level;
* tiny weak central residual that does not form a clear red/blue pair, dipole, or bar-like shape.

Central residual distinction:
This distinction is critical.

Classify as bad:

* compact central residual that is high-contrast compared with the surrounding noise;
* adjacent red/blue blobs near the center;
* central dipole or bipolar residual;
* central residual that is visually dominant;
* central residual clearly suggesting poor PSF, bulge, or bar fitting.

Classify as acceptable:

* tiny weak central residual;
* central speck close to noise level;
* weak isolated center residual without clear red/blue pair;
* weak center residual that is not visually dominant and the rest of the residual is mostly noise-like.

Decision rule:
Use the following rule to determine good_fit:

* If hard_failure = true, output good_fit = 0.
* Else, if final_score >= 0.75, output good_fit = 1.
* Else, output good_fit = 0.

Confidence rule:

* Use high confidence, around 0.80-0.95, when the decision is visually clear.
* Use moderate confidence, around 0.60-0.79, when the case is borderline.
* Use lower confidence, around 0.50-0.65, when the image is ambiguous or the residual panel is hard to identify.

Interpretation:

* good_fit = 1 means the fit is acceptable under the current bulge/disk/bar/PSF modeling scope, and the fitting process can reasonably stop.
* good_fit = 0 means the fit is not yet acceptable, and fitting should continue.

Please focus mainly on the residual quality.
Do not judge based only on whether the model image visually resembles the original galaxy, unless this helps interpret the residual panel.
The main criterion is whether the residual panel shows serious systematic structures related to bulge, disk, bar, PSF, artifacts, or severe over/under-subtraction.
"""
    if param_used and summary_md_text is not None:
        prompt += f"""

Additional GALFIT summary information is provided below.
This information comes from a *_summary.md file.

Use the summary information only as supporting evidence.
The residual image is still the primary criterion.

GALFIT summary markdown:
{summary_md_text}

When using the summary markdown, consider whether:

* reduced chi-square, BIC, AIC, or other fitting indicators are reasonable;
* key GALFIT parameters are physically plausible;
* parameters are not obviously stuck at hard boundaries;
* the number of components is reasonable for the current bulge/disk/bar/PSF modeling scope;
* magnitudes, effective radii, Sérsic indices, axis ratios, and position angles are plausible;
* the fit does not appear to be severely overfitting with unnecessary components;
* the numerical indicators are broadly consistent with the visual residual quality.

However:

* Do not mark good_fit = 1 based only on good numerical indicators.
* Do not mark good_fit = 0 based only on imperfect numerical indicators if the residual image is visually acceptable.
* The residual image is the primary evidence.
* The residual does not need to be perfectly white-noise-like.
* Weak outer residual structures are acceptable if they do not indicate a major missing or poorly fitted bulge, disk, bar, or PSF component.
* A tiny weak central residual is acceptable if it is not visually dominant and does not form a clear red/blue pair, dipole, or bar-like structure.
* Obvious compact central residuals are not acceptable, even if numerical indicators look reasonable.
* If the summary information conflicts with the residual image, trust the residual image more.
  """
    prompt += """

Output STRICTLY in JSON format.
Do not include any text outside the JSON.

Required JSON format:
{
"good_fit": 1,
"final_score": 0.82,
"confidence": 0.76,
"hard_failure": false,
"scores": {
"central_residual_score": 0.78,
"bar_residual_score": 0.88,
"disk_residual_score": 0.86,
"artifact_score": 0.90,
"overall_noise_score": 0.82
},
"detected_patterns": [
"tiny_weak_central_residual",
"mostly_noise_like_background"
],
"reason": "The residual image is acceptable under the current bulge/disk/bar/PSF modeling scope. Only a tiny weak central residual remains, without a clear high-contrast red/blue pair, central dipole, bar-like structure, strong disk mismatch, severe gradient, or dominant artifact."
}

Output field definitions:

* good_fit: integer, must be either 0 or 1.

  * 0 means not fitted well enough; fitting should continue.
  * 1 means acceptable final fit under the current bulge/disk/bar/PSF modeling scope; fitting can stop.
* final_score: float between 0 and 1, computed using the weighted scoring formula.
* confidence: float between 0 and 1, indicating confidence in the final good_fit decision.
* hard_failure: boolean. True if a strong failure pattern is clearly present.
* scores: object containing the five residual quality scores.
* detected_patterns: list of concise pattern labels observed in the residual image.
* reason: concise explanation focusing on the residual quality and the final decision.
  """



    # Crop residual panel before sending image to VLM
    try:
        vlm_image_path = crop_residual_panel(residual_image_path)
    except Exception as e:
        print(f"    ⚠️ [Crop Warning] Failed to crop residual panel: {e}")
        print("    ⚠️ Fall back to original image.")
        vlm_image_path = residual_image_path
    raw_response, usage = get_openAI_response_one_image(
        api_key=api_key,
        model_name=model_name,
        prompt=prompt,
        image_path=vlm_image_path,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=timeout,
        max_retries=max_retries,
        retry_sleep=retry_sleep,
    )
        
    result = extract_json_from_response(raw_response)

    raw_good_fit = int(result.get("good_fit", 0))
    confidence = float(result.get("confidence", 0.0))
    reason = str(result.get("reason", ""))

    if raw_good_fit not in [0, 1]:
        raw_good_fit = 0

    final_good_fit = raw_good_fit

    if raw_good_fit == 1 and confidence < confidence_threshold:
        final_good_fit = 0
        reason = f"[Forced to 0 due to low confidence < {confidence_threshold}] " + reason

    
    return {
        "good_fit": final_good_fit,
        "raw_good_fit": raw_good_fit,
        "confidence": confidence,
        "reason": reason,
        "usage": usage,
        "raw_response": raw_response,
        "image_path": vlm_image_path,
        "original_image_path": residual_image_path,
        "summary_md_path": summary_md_path,
        "param_used": param_used,
    }


def calculate_reward_model(
    prev_residual_image_path: str,
    next_residual_image_path: str,
    model_name: str = "gemini-3.1-pro-preview",
    api_key: str = "sk-orCNeVDDaLy7zNfXvDYx9FX7z5uTdUkbWBJjFWeFDarSysSq",
    temperature: float = 0.7,
    max_tokens: int = 512,
    timeout: int = 120,
    confidence_threshold: float = 0.6,
):
    """
    Compare two GALFIT residual images and judge whether the next-step residual is better.

    Parameters
    ----------
    prev_residual_image_path : str
        Path to the previous-step residual image.

    next_residual_image_path : str
        Path to the next-step residual image.

    model_name : str
        Multimodal model name. Default: "gemini-3.1-pro-preview".

    api_key : str
        API key. If None, read from environment variable OPENAI_API_KEY.

    temperature : float
        Sampling temperature. Recommend 0.0 for stable binary judgment.

    max_tokens : int
        Max output tokens.

    timeout : int
        Request timeout.

    confidence_threshold : float
        If improvement == 1 but confidence is lower than this threshold,
        force improvement to 0.


    Returns
    -------
    int or dict
        1 if the next residual image is clearly better than the previous one.
        0 otherwise.
    """

    if api_key is None:
        api_key = os.getenv("OPENAI_API_KEY")

    if not api_key:
        raise ValueError(
            "api_key is None. Please pass api_key explicitly or set OPENAI_API_KEY."
        )

    if not os.path.exists(prev_residual_image_path):
        raise FileNotFoundError(
            f"Previous residual image not found: {prev_residual_image_path}"
        )

    if not os.path.exists(next_residual_image_path):
        raise FileNotFoundError(
            f"Next residual image not found: {next_residual_image_path}"
        )

    prompt = """
You are an astronomer experienced in GALFIT galaxy component decomposition.

You will be given exactly two images in order.

The first attached image is Image 1: previous-step residual image.
The second attached image is Image 2: next-step residual image.

Each image may be a full GALFIT comparison figure with multiple panels.
For each image, focus only on the third panel from the left, titled "Residual/σ" or "Residual".
Ignore the original data panel, fitted model panel, surface-brightness profile panel, and any blank diagnostic panels.

Your task is to compare whether Image 2 is better than Image 1 as a GALFIT residual result.

A better residual image means:
- residuals are closer to white-noise-like background;
- central residuals are reduced;
- structured residuals are weaker or fewer;
- spiral, bar, ring, bullseye, dipole, clumpy, or off-center residual structures are reduced;
- large-scale gradients, chaotic dark patches, over-subtraction, or unmasked artifacts are reduced;
- the galaxy region is cleaner and less systematically structured.

A worse or not improved residual image means:
- structured residuals become stronger or more obvious;
- new central residuals, rings, dipoles, spiral patterns, clumps, or artifacts appear;
- the residual is not meaningfully improved;
- the difference is too small or ambiguous to confidently say Image 2 is better.

Important decision rule:
Only output improvement = 1 when Image 2 is clearly better than Image 1.
If the improvement is weak, ambiguous, visually negligible, or Image 2 is worse, output improvement = 0.

Please focus mainly on the residual quality.
Do not judge based on whether the model image visually resembles the original galaxy, unless the provided image contains multiple panels.
The main criterion is whether the residual panel becomes cleaner and less structured.

Output STRICTLY in JSON format.
Do not include any text outside the JSON.

Required JSON format:
{
  "improvement": 1,
  "confidence": 0.85,
  "reason": "Image 2 has weaker central residuals and fewer structured patterns than Image 1."
}

Definitions:
- improvement: integer, must be either 0 or 1.
- confidence: float between 0 and 1.
- reason: concise explanation.
"""

    # Crop residual panels before sending images to VLM
    try:
        prev_vlm_image_path = crop_residual_panel(prev_residual_image_path)
    except Exception as e:
        print(f"    ⚠️ [Crop Warning] Failed to crop previous residual panel: {e}")
        print("    ⚠️ Fall back to original previous image.")
        prev_vlm_image_path = prev_residual_image_path

    try:
        next_vlm_image_path = crop_residual_panel(next_residual_image_path)
    except Exception as e:
        print(f"    ⚠️ [Crop Warning] Failed to crop next residual panel: {e}")
        print("    ⚠️ Fall back to original next image.")
        next_vlm_image_path = next_residual_image_path

    raw_response, usage = get_openAI_response_two_images(
        api_key=api_key,
        model_name=model_name,
        prompt=prompt,
        prev_image_path=prev_vlm_image_path,
        next_image_path=next_vlm_image_path,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=timeout,
    )
    
    result = extract_json_from_text(raw_response)

    improvement = int(result.get("improvement", 0))
    confidence = float(result.get("confidence", 0.0))

    # 强制保证只返回 0 或 1
    if improvement not in [0, 1]:
        improvement = 0

    # 低置信度保护：只有高置信度的明显改善才算正样本
    if improvement == 1 and confidence < confidence_threshold:
        improvement = 0
        result["confidence_filter_applied"] = True
        result["original_improvement"] = 1
    else:
        result["confidence_filter_applied"] = False
        result["original_improvement"] = improvement


    result["final_improvement"] = improvement
    result["usage"] = usage
    result["prev_image_path"] = prev_vlm_image_path
    result["next_image_path"] = next_vlm_image_path
    result["original_prev_image_path"] = prev_residual_image_path
    result["original_next_image_path"] = next_residual_image_path

    return result

def calculate_reward(old_metrics: dict, new_metrics: dict, action: dict, step: int, 
                     prev_image_path: str = None, next_image_path: str = None,
                     use_llm: bool = False, vlm_reward_model_name: str = "gemini-3.1-pro-preview") -> tuple:
    """
    终极 Reward 整合枢纽 (严格互斥模式)：
    - use_llm = False: 纯物理规则驱动 (Rule-based)
    - use_llm = True:  纯视觉大模型驱动 (VLM-based)
    """
    # 基础安全检查：如果新变体直接物理崩溃 (Chi2 >= 9999.0)，任何模式都直接判死刑
    if new_metrics.get("chi2_nu", 999.0) >= 9999.0:
        return -100.0, {"fatal_error": "Physics engine crashed (Chi2=9999.0)"}

    # ==========================================
    # 模式 1：纯大模型视觉驱动 (VLM-based)
    # ==========================================
    if use_llm:
        print("    👁️ [VLM] 纯视觉模式启动: 评判残差图...")
        r_total = 0.0
        r_detail = {"mode": "VLM_ONLY"}
        
        # 必须确保有图可看
        if prev_image_path and next_image_path and os.path.exists(prev_image_path) and os.path.exists(next_image_path):
            try:
                print("    👁️ [VLM] 纯视觉模式启动: 评判残差图...")
                vlm_result = calculate_reward_model(
                    prev_residual_image_path=prev_image_path,
                    next_residual_image_path=next_image_path,
                    model_name=vlm_reward_model_name
                )
                
                if int(vlm_result.get("improvement")) == 1:
                    r_total = 1  # 大模型说好，直接给高分
                else:
                    r_total = -1  # 大模型说不好，直接扣分
                print(f"    👁️ [VLM] 纯视觉模式启动: 评判残差图... 结果: {vlm_result}")
                    
                r_detail["vlm_detail"] = vlm_result
            except Exception as e:
                print(f"    ⚠️ [VLM 警告] 视觉模型调用失败: {e}")
                r_total = -1.0 # API 失败时的微弱惩罚
                r_detail["error"] = str(e)
        else:
            r_total = -10.0
            r_detail["error"] = "Missing image files for VLM."
            
        r_detail["r_total"] = r_total
        return r_total, r_detail

    # ==========================================
    # 模式 2：纯物理规则驱动 (Rule-based)
    # ==========================================
    else:
        # 完全调用原来的规则函数，不掺杂任何模型得分
        r_total, r_detail = calculate_reward_rule(old_metrics, new_metrics, action, step)
        r_detail["mode"] = "RULE_ONLY"
        
        return r_total, r_detail