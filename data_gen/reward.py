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
import traceback
from typing import Optional, Dict, Any, Tuple, List, Mapping
from PIL import Image
from sympy import python
from pathlib import Path

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
    temperature: float = 0.1,
    max_tokens: int = 4096,
    timeout: int = 300,
    url: str = "https://api.road2all.com/v1/chat/completions",
    max_retries: int = 3,
    retry_sleep: int = 10,
):
    """
    Call OpenAI-compatible multimodal API with two explicitly labeled images.
    Add retry mechanism for temporary server errors such as 503.
    """
    api_key = api_key or os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("api_key 未提供：请在 .env 中设置 OPENAI_API_KEY（不要硬编码到源码）")

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
    temperature: float = 0.1,
    max_tokens: int = 4096,
    timeout: int = 300,
    url: str = "https://api.road2all.com/v1/chat/completions",
    max_retries: int = 3,
    retry_sleep: int = 10,
) -> Tuple[str, Dict[str, Any]]:
    """
    Call OpenAI-compatible multimodal API with one image.
    """
    api_key = api_key or os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("api_key 未提供：请在 .env 中设置 OPENAI_API_KEY（不要硬编码到源码）")

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


def get_openAI_response_multiturn(
    model_name: str,
    system_prompt: str,
    turn_prompts: list,
    image_path: str = None,
    api_key: str = None,
    temperature: float = 0.3,
    max_tokens: int = 8192,
    timeout: int = 300,
    url: str = "https://api.road2all.com/v1/chat/completions",
    max_retries: int = 3,
    retry_sleep: int = 10,
) -> Tuple[List[str], Dict[str, Any]]:
    """
    多轮对话 API 调用。第1轮带图片，后续轮次靠对话历史。
    返回 (每轮 assistant 回复列表, 累计 usage)。
    """
    api_key = api_key or os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("api_key 未提供：请在 .env 中设置 OPENAI_API_KEY")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    image_content = None
    if image_path and os.path.exists(image_path):
        mime_type, _ = mimetypes.guess_type(image_path)
        if mime_type is None:
            ext = os.path.splitext(image_path)[1].lower()
            mime_type = {".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                         ".png": "image/png", ".webp": "image/webp"}.get(ext, "image/png")
        image_content = {
            "type": "image_url",
            "image_url": {"url": f"data:{mime_type};base64,{encode_image(image_path)}"},
        }

    messages = [{"role": "system", "content": system_prompt}]
    total_usage = {"prompt_tokens": 0, "completion_tokens": 0}
    responses = []

    for i, turn_prompt in enumerate(turn_prompts):
        if i == 0 and image_content:
            user_msg = {"role": "user", "content": [
                {"type": "text", "text": turn_prompt},
                image_content,
            ]}
        else:
            user_msg = {"role": "user", "content": turn_prompt}
        messages.append(user_msg)

        data = {
            "model": model_name,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": messages,
            "stream": False,
        }

        last_error = None
        for attempt in range(1, max_retries + 1):
            try:
                response = requests.post(url, headers=headers, json=data, timeout=timeout)
                response.raise_for_status()
                resp_json = response.json()
                assistant_text = resp_json["choices"][0]["message"]["content"]
                usage = resp_json.get("usage", {})
                total_usage["prompt_tokens"] += usage.get("prompt_tokens", 0)
                total_usage["completion_tokens"] += usage.get("completion_tokens", 0)
                messages.append({"role": "assistant", "content": assistant_text})
                responses.append(assistant_text)
                last_error = None
                break
            except (requests.exceptions.HTTPError, requests.exceptions.RequestException) as e:
                last_error = e
                status_code = getattr(getattr(e, 'response', None), 'status_code', None)
                print(f"    ⚠️ [Multiturn API] Turn {i+1} attempt {attempt}/{max_retries}, "
                      f"status={status_code}, error={e}")
                if attempt < max_retries and (status_code in [429, 500, 502, 503, 504] or status_code is None):
                    time.sleep(retry_sleep * attempt)
                    continue
                raise

        if last_error:
            raise RuntimeError(f"Multiturn API Turn {i+1} failed after {max_retries} retries: {last_error}")

    return responses, total_usage

def _safe_binary(value: Any, default: int = 0) -> int:
    """Convert model output to a strict 0/1 value."""
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed in (0, 1) else default


def _safe_float(value: Any, default: float = 0.0) -> float:
    """Convert model output to float and clamp confidence-like values to [0, 1]."""
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(1.0, parsed))


def _safe_list(value: Any) -> List[str]:
    """Normalize model evidence fields to a list of non-empty strings."""
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    return [text] if text else []

def _safe_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes"}:
            return True
        if normalized in {"false", "0", "no"}:
            return False
    return default

def _load_trajectory_context(
    trajectory_context: Optional[str],
    trajectory_context_path: Optional[str],
) -> Optional[str]:
    """Load optional trajectory context from either direct text or a file."""
    if trajectory_context and trajectory_context_path:
        raise ValueError(
            "Provide only one of trajectory_context or trajectory_context_path."
        )

    if trajectory_context_path:
        path = Path(trajectory_context_path)
        if not path.exists():
            raise FileNotFoundError(
                f"trajectory_context_path does not exist: {trajectory_context_path}"
            )
        text = path.read_text(encoding="utf-8").strip()
        return text or None

    if trajectory_context:
        text = str(trajectory_context).strip()
        return text or None

    return None

def _usage_payload(usage_attempts: List[Any]) -> Dict[str, Any]:
    totals: Dict[str, float] = {}
    for usage in usage_attempts:
        if isinstance(usage, Mapping):
            for key, value in usage.items():
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    totals[key] = totals.get(key, 0.0) + float(value)
    normalized = {
        key: int(value) if value.is_integer() else value
        for key, value in totals.items()
    }
    return {"total": normalized, "attempts": usage_attempts}


def _decision_prompt(summary: str, trajectory: Optional[str]) -> str:
    trajectory_block = trajectory or "No trajectory context was provided."
    return f"""
You are an astronomer experienced in GALFIT galaxy-component decomposition.

Decide whether component fitting should stop. Perform the following two checks
INTERNALLY in one response. Do not reveal chain-of-thought, intermediate
reasoning, or the prompt. Return only the compact JSON object specified below.

SCOPE
Evaluate residual structure only when it is attributable to one of these four
model-component families:

- bulge;
- disk;
- bar;
- PSF or compact central point source.

Clearly identifiable spiral arms, star-forming clumps, dust lanes, tidal
features, irregular morphology, lopsidedness, shells, streams, and patchy
outer asymmetries are outside scope. Do not call a smooth coherent residual
outside scope merely because its interpretation is difficult.

INTERNAL CHECK A — candidate-stop screening
Use a relatively sensitive screen. Set candidate_pass=true only if:

1. no extended or dominant in-scope bulge/disk/bar/PSF residual clearly
   remains;
2. the large-scale target-galaxy residual is predominantly noise-like;
3. the decomposition appears physically and numerically valid.

A compact central residual may pass Check A when it is confined to the central
core and no clear evidence shows extension to bulge, bar, or disk scale.

Do not reject a candidate solely because a simple compact dipole or isolated
central speck is visible.

However, a coherent closed ring, repeated concentric transition, multi-lobed
pattern, or central feature extending toward bulge, bar, or disk scale must
not pass Check A unless affirmative image evidence shows that it is unresolved
and PSF-shaped.
Check B will determine
whether that feature is materially incompatible with excellent.


INTERNAL CHECK B — conservative veto review
Independently re-check every conclusion from Check A. Set verifier_pass=true
only if ALL are true:

1. Check A passed;
2. the 2D residual contains no extended or dominant in-scope structure;
3. any remaining central residual is confined to the compact core;
4. no independent evidence shows material decomposition failure;
5. the fit is physically and numerically valid;
6. the result is genuinely excellent, not merely acceptable or usable.

If material residual or physical-failure evidence remains genuinely ambiguous,
use the 2D residual extent and corresponding 1D mismatch as the tie-breaker.
Excellent requires affirmative evidence of an excellent fit; it is not the
default result when no decisive veto is found.

If genuine ambiguity remains between excellent and acceptable after examining
the 2D residual extent and the 1D profile, classify the fit as acceptable and
set verifier_pass=false.

CENTRAL RESIDUAL CALIBRATION

Compactness alone does not prove that a central residual is negligible.
Likewise, visibility, contrast, or a ring/dipole/bullseye description alone
does not prove that it is material.

A compact central residual remains compatible with excellent when:

- it is confined to the compact or approximately PSF-scale core;
- the surrounding large-scale 2D residual is predominantly noise-like;
- it has no coherent continuation at bulge, bar, or disk scale;
- no substantial corresponding 1D mismatch spans a meaningful radial range;
- no directly related component collapse or role takeover is present.

Downgrade to acceptable or worse when affirmative evidence shows that the
central residual is material. Such evidence includes:

- clear extension beyond the compact core to bulge, bar, or disk scale;
- a coherent closed ring or repeated concentric transition with visibly
  resolved radial extent;
- another accompanying extended in-scope residual;
- a substantial matching 1D profile mismatch;
- a directly corresponding component collapse or role takeover.

A simple compact dipole, asymmetric positive-negative pair, or isolated
central speck is not an automatic veto. When the image cannot distinguish
between an unresolved PSF-scale artifact and a materially resolved structure,
use the large-scale 2D residual and the 1D profile as the deciding evidence.


MISSING-COMPONENT INFERENCE

Do not infer a missing bulge, bar, disk, or PSF solely from a compact central
ring, dipole, bullseye, quadrupole, X-shaped, or multi-lobed appearance.

A missing-component interpretation requires affirmative supporting evidence
that the residual extends to the spatial scale of that component, or a
substantial matching 1D mismatch, or direct parameter evidence that the
intended component is absent or collapsed.

A speculative component interpretation must not by itself downgrade an
otherwise excellent fit.


MANDATORY VETOES
Continue fitting for any of the following:

- an extended bulge-scale mismatch;
- a galaxy-scale disk mismatch;
- an extended bar, boxy, X-shaped, quadrupole, elongated, elliptical, or
  coherent radial residual attributable to an in-scope component;
- a central residual extending materially beyond the compact core;
- multiple coherent in-scope residual scales;
- poor residual quality;
- failed convergence, non-finite values, required-component collapse,
  uninterpretable boundary failure, severe multi-property degeneracy, or
  unreliable contamination/masking;
- evidence that the fit is only acceptable rather than excellent, including a
  coherent central structure whose materiality cannot be confidently excluded.



PHYSICAL-VALIDITY CALIBRATION

Do not declare physical invalidity solely from:

- a fixed or low Sérsic index;
- similar or equal component radii;
- similar position angles;
- tied centres;
- component overlap;
- a single boundary warning;
- one unusual parameter;
- any combination consisting only of a low or fixed Sérsic index, similar
  component radii, and a compact central residual.

These protected features remain insufficient even when a compact central
residual is also visible. Mere coexistence between a parameter feature and a
residual is not proof of causation.

Classify physical_validity_assessment as invalid only when there is direct,
severe, and material evidence of at least one of the following:

- failed convergence or non-finite values;
- required-component collapse;
- a component taking over another component's spatial role;
- an extreme or boundary-hit parameter accompanied by a directly matching
  residual on the scale assigned to that component;
- multiple independent severe parameter failures that mutually support a
  specific degeneracy.

A residual may justify acceptable or poor fit quality without proving
physical invalidity. Evaluate residual quality separately from physical
validity.


Similar component radii or scale lengths do not establish collapse,
redundancy, or role takeover. Severe degeneracy requires mutually supporting
evidence from multiple component properties, such as size, profile shape,
axis ratio, orientation, flux contribution, and spatial role.

A weak or low-flux component is not automatically a failed component. It
prevents stopping only when that component is required for the intended
bulge/disk/bar/PSF decomposition and its loss makes the decomposition
materially incomplete or misleading.

Do not allow a protected parameter feature to amplify an otherwise compact,
non-material residual into a physical-validity failure.

EVIDENCE PRIORITY
The 2D residual is primary. The 1D profile is secondary. A good 1D profile
cannot rescue a poor 2D residual. A 1D mismatch should veto only when it spans
a meaningful radial range and agrees with an extended in-scope 2D residual.

FINAL LOGIC

Set is_stop=1 only when ALL are true:

- candidate_pass is true;
- verifier_pass is true;
- fit_quality_assessment is exactly "excellent";
- physical_validity_assessment is exactly "valid";
- veto_type is exactly "none";
- extended_in_scope_residual is false;
- clear_physical_failure is false.

Otherwise set is_stop=0.

FIELD CONSISTENCY

- If candidate_pass=false, verifier_pass must be false and is_stop must be 0.
- If verifier_pass=false, is_stop must be 0.
- If is_stop=1, veto_type must be "none".
- If is_stop=0, veto_type must identify the strongest applicable veto and
  must not be "none".
- If physical_validity_assessment="invalid",
  clear_physical_failure must be true and veto_type must be
  "physical_invalidity".
- If extended_in_scope_residual=true, is_stop must be 0.

Use one short, image-specific reason of no more than two sentences.
For is_stop=0, state the strongest veto evidence. For is_stop=1, state why no
material extended in-scope residual or physical failure remains. Do not provide
recommendations or a long explanation.

Allowed fit quality: invalid, poor, acceptable, excellent.
Allowed physical validity: valid, invalid.
Allowed veto type: none, acceptable_not_excellent, extended_residual,
poor_residual, physical_invalidity, unreliable_input.

## GALFIT summary markdown

{summary}

## Trajectory context

{trajectory_block}
""".strip()



def _call_json_once_per_attempt(
    *,
    prompt: str,
    image_path: str,
    api_key: str,
    model_name: str,
    temperature: float,
    max_tokens: int,
    timeout: int,
    max_retries: int,
    retry_sleep: int,
    json_parse_retries: int,
) -> Tuple[Dict[str, Any], Any, Dict[str, Any]]:
    last_error: Optional[Exception] = None
    last_raw: Any = None
    usage_attempts: List[Any] = []
    attempts = max(1, json_parse_retries + 1)

    for attempt in range(attempts):
        raw_response, usage = get_openAI_response_one_image(
            api_key=api_key,
            model_name=model_name,
            prompt=prompt,
            image_path=image_path,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
            max_retries=max_retries,
            retry_sleep=retry_sleep,
        )
        last_raw = raw_response
        usage_attempts.append(usage)
        try:
            parsed = extract_json_from_response(raw_response)
            if not isinstance(parsed, dict):
                raise ValueError("response JSON is not an object")
            return parsed, raw_response, _usage_payload(usage_attempts)
        except Exception as exc:
            last_error = exc
            if attempt + 1 < attempts and retry_sleep > 0:
                time.sleep(retry_sleep)

    raise ValueError(
        f"one-pass decision failed JSON parsing after {attempts} attempt(s): "
        f"{type(last_error).__name__}: {last_error}; last_response={last_raw!r}"
    )


def is_stop(
    residual_image_path: str,
    summary_md_path: str,
    trajectory_context: Optional[str] = None,
    trajectory_context_path: Optional[str] = None,
    model_name: str = "gemini-3.1-pro-preview",
    api_key: Optional[str] = None,
    temperature: float = 0.0,
    max_tokens: int = 12000,
    timeout: int = 300,
    confidence_threshold: float = 0.6,
    force_low_confidence_stop_to_continue: bool = True,
    max_retries: int = 3,
    retry_sleep: int = 10,
    json_parse_retries: int = 1,
) -> Dict[str, Any]:
    """Make one model call containing candidate and conservative checks."""
    allowed_quality = {"invalid", "poor", "acceptable", "excellent"}
    allowed_validity = {"valid", "invalid"}
    allowed_vetoes = {
        "none",
        "acceptable_not_excellent",
        "extended_residual",
        "poor_residual",
        "physical_invalidity",
        "unreliable_input",
    }

    image_path = Path(residual_image_path)
    summary_path = Path(summary_md_path)
    if not image_path.exists():
        raise FileNotFoundError(f"residual image does not exist: {image_path}")
    if not summary_path.exists():
        raise FileNotFoundError(f"GALFIT summary does not exist: {summary_path}")

    summary = str(read_summary_md(str(summary_path))).strip()
    if not summary:
        raise ValueError(f"GALFIT summary is empty: {summary_path}")
    trajectory = _load_trajectory_context(
        trajectory_context, trajectory_context_path
    )
    resolved_api_key = api_key or os.getenv("OPENAI_API_KEY")
    if not resolved_api_key:
        raise ValueError("Pass api_key or set OPENAI_API_KEY")

    judgment, raw_response, usage = _call_json_once_per_attempt(
        prompt=_decision_prompt(summary, trajectory),
        image_path=str(image_path),
        api_key=resolved_api_key,
        model_name=model_name,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=timeout,
        max_retries=max_retries,
        retry_sleep=retry_sleep,
        json_parse_retries=json_parse_retries,
    )

    warnings: List[str] = []
    candidate_pass = _safe_bool(judgment.get("candidate_pass"), False)
    verifier_pass = _safe_bool(judgment.get("verifier_pass"), False)
    model_is_stop = _safe_binary(judgment.get("is_stop"), 0)
    confidence = _safe_float(judgment.get("confidence"), 0.0)

    quality = str(judgment.get("fit_quality_assessment", "invalid")).strip().lower()
    if quality not in allowed_quality:
        quality = "invalid"
        warnings.append("unknown fit quality was normalized to invalid")
    validity = str(
        judgment.get("physical_validity_assessment", "invalid")
    ).strip().lower()
    if validity not in allowed_validity:
        validity = "invalid"
        warnings.append("unknown physical validity was normalized to invalid")
    veto_type = str(judgment.get("veto_type", "unreliable_input")).strip().lower()
    if veto_type not in allowed_vetoes:
        veto_type = "unreliable_input"
        warnings.append("unknown veto type was normalized to unreliable_input")

    extended = _safe_bool(
        judgment.get("extended_in_scope_residual"), True
    )
    physical_failure = _safe_bool(
        judgment.get("clear_physical_failure"), True
    )
    consistent_stop = (
        candidate_pass
        and verifier_pass
        and model_is_stop == 1
        and quality == "excellent"
        and validity == "valid"
        and veto_type == "none"
        and not extended
        and not physical_failure
    )
    raw_is_stop = int(consistent_stop)
    if model_is_stop == 1 and raw_is_stop == 0:
        warnings.append("inconsistent model stop was forced to continue")

    final_is_stop = raw_is_stop
    confidence_gate_applied = False
    if (
        force_low_confidence_stop_to_continue
        and raw_is_stop == 1
        and confidence < confidence_threshold
    ):
        final_is_stop = 0
        confidence_gate_applied = True
        warnings.append(
            f"stop confidence {confidence:.3f} was below {confidence_threshold:.3f}"
        )

    if final_is_stop:
        stop_type = "successful_termination"
        stop_reason = "excellent_fit"
    else:
        stop_type = "continue_fitting"
        if validity == "invalid" or physical_failure:
            stop_reason = "parameter_invalidity"
        elif quality == "poor" or extended:
            stop_reason = "major_residual_structure"
        else:
            stop_reason = "fit_not_acceptable"

    reason = str(judgment.get("reason", "")).strip()

    # Compatibility aliases let the existing two-stage evaluator inspect the
    # two internal checks without causing a second model request.
    return {
        "is_stop": final_is_stop,
        "raw_is_stop": raw_is_stop,
        "confidence": confidence,
        "confidence_threshold": confidence_threshold,
        "confidence_gate_applied": confidence_gate_applied,
        "fit_quality_assessment": quality,
        "physical_validity_assessment": validity,
        "stop_type": stop_type,
        "stop_reason": stop_reason,
        "has_concrete_next_action": False,
        "next_action": None,
        "parameter_evidence": [],
        "residual_evidence": [reason] if reason else [],
        "reason": reason,
        "consistency_warnings": warnings,
        "candidate_pass": candidate_pass,
        "verifier_pass": verifier_pass,
        "candidate_is_stop": int(candidate_pass),
        "candidate_confidence": confidence,
        "candidate_result": {"candidate_pass": candidate_pass},
        "verifier_ran": bool(candidate_pass),
        "verifier_confirms_stop": int(verifier_pass) if candidate_pass else 0,
        "verifier_confidence": confidence,
        "verifier_result": {
            "verifier_pass": verifier_pass,
            "veto_type": veto_type,
        },
        "veto_type": veto_type,
        "extended_in_scope_residual": extended,
        "clear_physical_failure": physical_failure,
        "usage": usage,
        "candidate_raw_response": raw_response,
        "verifier_raw_response": None,
        "raw_response": raw_response,
        "image_path": str(image_path),
        "original_image_path": residual_image_path,
        "summary_md_path": summary_md_path,
        "trajectory_context_path": trajectory_context_path,
        "trajectory_context_used": bool(trajectory),
        "param_used": True,
        "model_call_count": 1,
    }



def calculate_reward_model(
    prev_residual_image_path: str,
    next_residual_image_path: str,
    model_name: str = "gemini-3.1-pro-preview",
    api_key: str = None,
    temperature: float = 0.1,
    max_tokens: int = 8192,
    timeout: int = 300,
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
####prompt 0
#     prompt = """
# You are an astronomer experienced in GALFIT galaxy component decomposition.

# You will be given exactly two images in order.

# The first attached image is Image 1: previous-step residual image.
# The second attached image is Image 2: next-step residual image.

# Each image may be a full GALFIT comparison figure contains three panels:
# - Left: original cutout image
# - Middle: fitted model image
# - Right: residual image (data - model)

# Your task is to compare whether Image 2 is better than Image 1 as a GALFIT residual result.

# A better residual image means:
# - residuals are closer to white-noise-like background;
# - central residuals are reduced;
# - structured residuals are weaker or fewer;
# - spiral, bar, ring, bullseye, dipole, clumpy, or off-center residual structures are reduced;
# - large-scale gradients, chaotic dark patches, over-subtraction, or unmasked artifacts are reduced;
# - the galaxy region is cleaner and less systematically structured.

# A worse or not improved residual image means:
# - structured residuals become stronger or more obvious;
# - new central residuals, rings, dipoles, spiral patterns, clumps, or artifacts appear;
# - the residual is not meaningfully improved;
# - the difference is too small or ambiguous to confidently say Image 2 is better.

# Important decision rule:
# Only output improvement = 1 when Image 2 is clearly better than Image 1.
# If the improvement is weak, ambiguous, visually negligible, or Image 2 is worse, output improvement = 0.

# Please focus mainly on the residual quality.
# Do not judge based on whether the model image visually resembles the original galaxy, unless the provided image contains multiple panels.
# The main criterion is whether the residual panel becomes cleaner and less structured.

# Output STRICTLY in JSON format.
# Do not include any text outside the JSON.

# Required JSON format:
# {
#   "improvement": 1,
#   "confidence": 0.85,
#   "reason": "Image 2 has weaker central residuals and fewer structured patterns than Image 1."
# }

# Definitions:
# - improvement: integer, must be either 0 or 1.
# - confidence: float between 0 and 1.
# - reason: concise explanation.
# """
    #裁剪
    # Crop residual panels before sending images to VLM
    # try:
    #     prev_vlm_image_path = crop_residual_panel(prev_residual_image_path)
    # except Exception as e:
    #     print(f"    ⚠️ [Crop Warning] Failed to crop previous residual panel: {e}")
    #     print("    ⚠️ Fall back to original previous image.")
    #     prev_vlm_image_path = prev_residual_image_path

    # try:
    #     next_vlm_image_path = crop_residual_panel(next_residual_image_path)
    # except Exception as e:
    #     print(f"    ⚠️ [Crop Warning] Failed to crop next residual panel: {e}")
    #     print("    ⚠️ Fall back to original next image.")
    #     next_vlm_image_path = next_residual_image_path
    
    
    
    prompt = """
You are an astronomer experienced in GALFIT galaxy component decomposition.

You will be given exactly two images in order.

The first attached image is Image 1: previous-step residual image.
The second attached image is Image 2: next-step residual image.

The image may be:
- a residual-only image, or
- a full GALFIT comparison figure with multiple panels.

If multiple panels are present, focus only on the residual panel, usually titled "Residual/σ", "Residual", or similar.
Ignore the original data panel, fitted model panel, surface-brightness profile panel, and blank diagnostic panels.

Your task is to compare whether Image 2 is improved relative to Image 1 as a GALFIT residual result.

Important:
This is a relative step-by-step comparison, not a final good-fit judgement.
Image 2 does NOT need to be a good final fit.
It only needs to show a meaningful reduction of residual structures compared with Image 1.

Focus on the galaxy-related residual structures, especially:
- central positive/negative residuals;
- dipole-like residuals;
- compact red/blue clumps near the galaxy center;
- ring, spiral, bar, or asymmetric structures;
- coherent large-scale residual patterns.

Ignore features that are unchanged and likely unrelated to the galaxy fit, such as:
- masked regions;
- saturated foreground stars;
- isolated artifacts far from the galaxy center;
- unchanged random background noise.

Decision criteria:

Output improvement = 1 if Image 2 shows any meaningful residual improvement compared with Image 1, including:
- central residuals become weaker, smaller, or less saturated;
- dipole-like red/blue structures are reduced;
- structured residuals become less coherent or less extended;
- the galaxy center becomes cleaner, even if the overall background remains similar;
- the improvement is local but clearly related to the target galaxy.

Output improvement = 0 if:
- Image 2 is visually identical to Image 1;
- differences are only random noise fluctuations;
- the main central or galaxy-related residual structure remains equally strong;
- new structured residuals appear;
- Image 2 is worse or the comparison is too ambiguous.

Use the following judgement scale internally:
- clear_improvement: obvious reduction of galaxy-related residual structures;
- slight_improvement: small but visible reduction of central or structured residuals;
- no_improvement: no meaningful change;
- worse: residual structures become stronger or new artifacts appear.

For the binary output:
- clear_improvement -> improvement = 1
- slight_improvement -> improvement = 1
- no_improvement or worse -> improvement = 0

Be careful:
Do not require Image 2 to look like white noise.
A residual can still be imperfect but improved.

Output STRICTLY in JSON format.
Do not include any text outside the JSON.

Required JSON format:
{
  "improvement": 1,
  "improvement_level": "slight_improvement",
  "confidence": 0.75,
  "reason": "Image 2 shows a small but visible reduction of the central blue/red residual structure, although the overall residual background remains similar."
}

Definitions:
- improvement: integer, must be either 0 or 1.
- improvement_level: one of ["clear_improvement", "slight_improvement", "no_improvement", "worse"].
- confidence: float between 0 and 1.
- reason: concise explanation focusing on residual differences.
"""

    raw_response, usage = get_openAI_response_two_images(
        api_key=api_key,
        model_name=model_name,
        prompt=prompt,
        prev_image_path=prev_residual_image_path,
        next_image_path=next_residual_image_path,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=timeout,
    )
    
    result = extract_json_from_text(raw_response)

    improvement = int(result.get("improvement", 0))
    confidence = float(result.get("confidence", 0.0))
    improvement_level = result.get("improvement_level", "no_improvement")

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
    result["final_improvement_level"] = improvement_level
    result["usage"] = usage
    # result["prev_image_path"] = prev_vlm_image_path
    # result["next_image_path"] = next_vlm_image_path
    result["original_prev_image_path"] = prev_residual_image_path
    result["original_next_image_path"] = next_residual_image_path

    return result


def calculate_reward_model_with_param(
    prev_residual_image_path: str,
    next_residual_image_path: str,
    prev_summary_path: str,
    new_summary_path: str,
    model_name: str = "gemini-3.1-pro-preview",
    api_key: str = None,
    temperature: float = 0.1,
    max_tokens: int = 8192,
    timeout: int = 300,
    confidence_threshold: float = 0.6,
):
    """
    Compare two GALFIT residual images AND evaluate parameter plausibility / metric consistency.

    This function compares:
    1. Previous-step residual image vs next-step residual image;
    2. Previous-step fitting summary vs next-step fitting summary.

    Key behavior:
    - If residual visually improves and parameters / metrics are also reasonable, improvement = 1.
    - If residual visually improves but parameters become physically implausible or metrics suggest overfitting,
      improvement is forced to 0.
    - Both chisq and chisq/nu should be considered. Both are better when smaller.
      chisq/nu is treated similarly to BIC-like model-selection information, because it can partially reflect
      whether the fit improvement is obtained by overfitting.
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

    # Read previous-step summary content
    prev_summary_content = ""
    if prev_summary_path and os.path.exists(prev_summary_path):
        try:
            prev_summary_content = read_summary_md(prev_summary_path)
        except Exception as e:
            print(f"    ⚠️ [Param Check] previous summary 读取失败: {e}")
            prev_summary_content = "(上一步参数摘要不可用)"
    else:
        prev_summary_content = "(上一步参数摘要不可用)"

    # Read next-step summary content
    new_summary_content = ""
    if new_summary_path and os.path.exists(new_summary_path):
        try:
            new_summary_content = read_summary_md(new_summary_path)
        except Exception as e:
            print(f"    ⚠️ [Param Check] next summary 读取失败: {e}")
            new_summary_content = "(下一步参数摘要不可用)"
    else:
        new_summary_content = "(下一步参数摘要不可用)"

    prompt_new = """
You are an astronomer experienced in GALFIT galaxy component decomposition.

You will be given exactly two images in order:

* Image 1: previous-step fitting result.
* Image 2: next-step fitting result.

Each image may be a residual-only image or a full GALFIT comparison figure.
If multiple panels are present, focus primarily on the residual panel, usually titled "Residual/σ", "Residual", "data-model", or similar.
Ignore the observed data panel, fitted model panel, surface-brightness profile panel, and blank diagnostic panels unless needed only to check image-summary correspondence.

Your task is to determine whether Image 2 is a valid improvement over Image 1.
This is a relative step-by-step comparison, not a final good-fit judgement.
Image 2 does not need to be a final good fit.

The final decision must consider:

1. residual image change;
2. physical plausibility of the next-step parameters;
3. statistical reasonability of chisq, chisq/nu, and BIC;
4. image-summary consistency;
5. severe residual or fitting warnings.

========================================
Input Pairing
=============

Image 1 corresponds only to the previous-step fitting summary.
Image 2 corresponds only to the next-step fitting summary.

Do not use parameters or warnings from one summary to interpret the other image.

Before comparing the two results, internally check whether each image appears broadly consistent with its corresponding summary.
If the image-summary correspondence is too uncertain to support a reliable comparison, set:

improvement = 0
image_text_consistent = false

Previous-step fitting summary for Image 1:

""" + prev_summary_content + """

Next-step fitting summary for Image 2:

""" + new_summary_content + """

========================================
Part 1: Residual Image Comparison
=================================

Compare only galaxy-related residual structures.

Focus on:

* central positive or negative residuals;
* dipole-like red/blue residuals;
* compact clumps near the galaxy center;
* ring, spiral, bar, lens-like, or asymmetric structures;
* coherent large-scale residual patterns.

Ignore unchanged features likely unrelated to the target galaxy, such as masked regions, foreground stars, isolated artifacts far from the galaxy center, or unchanged random background noise.

Set improvement_level as one of:

* "clear_improvement": obvious reduction of galaxy-related residual structures;
* "slight_improvement": small but visible reduction of central or structured residuals;
* "no_improvement": no meaningful visual change;
* "worse": residual structures become stronger or new artifacts appear.

Set residual_improved = true only for "clear_improvement" or "slight_improvement".
Set residual_improved = false for "no_improvement" or "worse".

Important:
If Image 2 is visually identical or nearly identical to Image 1, set:

residual_improved = false
improvement_level = "no_improvement"

However, this does not automatically mean improvement = 0.
If the residual is visually unchanged but Image 2 is not worse, the final decision may still be improvement = 1 if the metrics clearly improve and the parameters remain physically plausible.

========================================
Part 2: Hard Warning Check
==========================

Check whether Image 2 has any severe warning.

Hard Warning tags are:

* "fit_not_converged": the summary indicates that the fit did not converge.
* "linear_gradient": Image 2 residual shows a strong large-scale linear background gradient.
* "chaotic_dark_patches": Image 2 residual contains large irregular dark patches.
* "diffuse_fragments": Image 2 residual contains widespread diffuse fragments unrelated to meaningful galaxy structure.
* "unmasked_artifact": Image 2 residual is dominated by unmasked stars, cosmic rays, bad pixels, saturated sources, or other artifacts.

If Image 2 has a severe Hard Warning, set improvement = 0.
Record all warning tags in hard_warnings.
If there are no Hard Warnings, use an empty list.

========================================
Part 3: Parameter Plausibility
==============================

Evaluate whether the next-step parameters are physically plausible and whether the parameter changes from Image 1 to Image 2 are reasonable.

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

Set param_plausible = true only if the next-step parameters are physically reasonable and do not show suspicious degradation from the previous step.

Record specific issues in param_issues.
If there are no parameter issues, use an empty list.

========================================
Part 4: Metric Reasonability
============================

Compare chisq, chisq/nu, and BIC between the previous-step and next-step summaries.

Rules:

* chisq is better when smaller.
* chisq/nu is better when smaller.
* BIC is better when smaller.
* chisq measures total residual mismatch.
* chisq/nu measures normalized fitting quality and should not become clearly worse.
* BIC penalizes model complexity and is especially important when residual images are visually similar.

Metric interpretation:

1. If residuals visually improve:

   * Accept improvement if parameters are plausible and metrics do not clearly contradict it.
   * A small or ambiguous metric change may be tolerated for clear visual improvement.

2. If residuals are visually similar or nearly unchanged:

   * Do not automatically set improvement = 0.
   * BIC becomes especially important.
   * If BIC clearly decreases and chisq/nu is unchanged or only mildly worse, this supports metric-driven improvement.
   * If chisq/nu clearly decreases and BIC does not clearly worsen, this also supports metric-driven improvement.
   * If chisq decreases only negligibly but BIC increases, the next-step model is not statistically preferred.
   * If chisq/nu is almost unchanged and BIC increases, do not consider Image 2 better, especially if it uses more free parameters.

3. If chisq/nu clearly worsens:

   * Be conservative.
   * Even if BIC decreases, do not accept the result as improvement = 1 when chisq/nu degradation is large.

4. If BIC improves but parameters become suspicious:

   * Do not accept the improvement.
   * BIC improvement must not override unphysical parameters, parameter runaway, severe center offsets, redundancy, or obvious overfitting.

Set metric_consistent = true if:

* residuals visually improve and the metrics do not clearly contradict the improvement; or
* residuals are visually similar, BIC clearly decreases, and chisq/nu does not clearly worsen; or
* residuals are visually similar, chisq/nu decreases, and BIC does not clearly worsen.

Set metric_consistent = false if:

* chisq/nu clearly worsens, even if BIC decreases;
* BIC clearly worsens without clear residual improvement;
* the fit does not converge;
* metric changes suggest overfitting or loss of fitting quality;
* metric improvement is accompanied by suspicious or physically implausible parameters.

Determine:

* chisq_trend: "decreased", "increased", "unchanged", or "unavailable"
* chisq_nu_trend: "decreased", "increased", "unchanged", or "unavailable"

Do not add a new JSON field for BIC.
If BIC is available, use it in the final decision and mention its trend in reason or metric_issues.

Record specific metric issues in metric_issues.
If there are no metric issues, use an empty list.

========================================
Combined Decision Rule
======================

There are two valid ways for Image 2 to be considered an improvement.

A. Residual-driven improvement:

Set improvement = 1 if all are true:

* residual_improved = true;
* param_plausible = true;
* metric_consistent = true;
* image_text_consistent = true;
* Image 2 has no severe Hard Warning;
* the improvement is not caused by overfitting or unnecessary components.

B. Metric-driven improvement when residuals are visually similar:

Set improvement = 1 even if residual_improved = false only if all are true:

* Image 2 is visually identical, nearly identical, or only marginally different from Image 1;
* Image 2 is not visually worse than Image 1;
* improvement_level = "no_improvement" or "slight_improvement";
* param_plausible = true;
* metric_consistent = true;
* image_text_consistent = true;
* Image 2 has no severe Hard Warning;
* BIC clearly decreases while chisq/nu does not clearly worsen, or chisq/nu clearly decreases while BIC does not clearly worsen;
* the metric improvement is not accompanied by suspicious components, unphysical parameters, parameter runaway, strong degeneracy, or obvious overfitting.

Set improvement = 0 if any are true:

* Image 2 is visually worse;
* Image 2 introduces severe residual failures;
* param_plausible = false;
* metric_consistent = false;
* image_text_consistent = false;
* the improvement is too ambiguous and not supported by either residual comparison or metrics;
* residuals are visually unchanged and neither BIC nor chisq/nu improves;
* residuals are visually unchanged, BIC improves, but chisq/nu clearly worsens;
* residuals are visually unchanged, BIC increases, and chisq/nu is unchanged or only negligibly better;
* residuals are visually unchanged, metrics improve, but the improvement is accompanied by suspicious components, unphysical parameters, parameter degeneracy, or likely overfitting;
* residual improvement is only slight but accompanied by worse chisq/nu, worse BIC, suspicious components, or extreme parameter changes.

Be conservative:
A visually tiny or metric-only improvement should not be accepted if it is accompanied by worse chisq/nu, worse BIC, suspicious components, unphysical parameters, image-summary mismatch, or Hard Warnings.

========================================
Output Format
=============

Output strictly in JSON format.
Do not include Markdown.
Do not include any text outside the JSON.

Required JSON format:

{
"improvement": 1,
"improvement_level": "slight_improvement",
"confidence": 0.75,
"residual_improved": true,
"param_plausible": true,
"metric_consistent": true,
"image_text_consistent": true,
"hard_warnings": [],
"chisq_trend": "decreased",
"chisq_nu_trend": "decreased",
"param_issues": [],
"metric_issues": [],
"reason": "Image 2 shows reduced central residuals. The image-summary correspondence is reliable. The next-step parameters remain physically plausible, no hard warnings are present, and chisq / chisq_nu / BIC changes support the improvement."
}

Definitions:

* improvement: integer, must be either 0 or 1. Final decision considering residual comparison, physical plausibility, metric consistency, image-summary consistency, and hard warnings. Improvement can be residual-driven or metric-driven.
* improvement_level: one of ["clear_improvement", "slight_improvement", "no_improvement", "worse"]. Based on residual comparison only.
* confidence: float between 0 and 1.
* residual_improved: boolean. true only if Image 2 is visually improved in galaxy-related residual structures.
* param_plausible: boolean. true if the next-step parameters are physically reasonable and do not show suspicious degradation.
* metric_consistent: boolean. true if chisq, chisq/nu, and BIC changes support or do not contradict the claimed improvement. When residuals are visually similar, metric_consistent can be true if BIC clearly decreases and chisq/nu does not clearly worsen. It should be false if chisq/nu clearly worsens, even when BIC decreases.
* image_text_consistent: boolean. true if both image-summary pairs appear broadly consistent.
* hard_warnings: list of strings. Each string must be one of ["fit_not_converged", "linear_gradient", "chaotic_dark_patches", "diffuse_fragments", "unmasked_artifact"]. Empty list if none.
* chisq_trend: one of ["decreased", "increased", "unchanged", "unavailable"].
* chisq_nu_trend: one of ["decreased", "increased", "unchanged", "unavailable"].
* param_issues: list of strings. Each string describes one specific parameter or physical issue. Empty list if none.
* metric_issues: list of strings. Each string describes one specific metric issue. Empty list if none.
* reason: concise explanation covering residual comparison, parameter assessment, image-summary correspondence, hard warnings, and chisq / chisq_nu / BIC comparison.
  """
  

    raw_response, usage = get_openAI_response_two_images(
        api_key=api_key,
        model_name=model_name,
        prompt=prompt_new,
        prev_image_path=prev_residual_image_path,
        next_image_path=next_residual_image_path,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=timeout,
    )
    


    print("========== RAW RESPONSE START ==========")
    print(repr(raw_response))
    print("========== RAW RESPONSE END ==========")
    print("usage:", usage)

    if raw_response is None or not str(raw_response).strip():
        raise ValueError(
            "Model returned empty response. "
            "This is usually caused by API failure, empty image input, too-long prompt, timeout, or unstable Gemini output."
        )


    result = extract_json_from_text(raw_response)

    # ===== 读取模型输出 =====
    improvement = int(result.get("improvement", 0))
    confidence = float(result.get("confidence", 0.0))

    residual_improvement_level = result.get("improvement_level", "no_improvement")
    residual_improved = bool(result.get("residual_improved", False))

    param_plausible = bool(result.get("param_plausible", True))
    metric_consistent = bool(result.get("metric_consistent", True))
    image_text_consistent = bool(result.get("image_text_consistent", True))

    hard_warnings = result.get("hard_warnings", [])

    chisq_trend = result.get("chisq_trend", "unavailable")
    chisq_nu_trend = result.get("chisq_nu_trend", "unavailable")

    param_issues = result.get("param_issues", [])
    metric_issues = result.get("metric_issues", [])

    # ===== 清洗 improvement =====
    if improvement not in [0, 1]:
        improvement = 0

    # ===== 后处理保护 =====
    if improvement == 1 and confidence < confidence_threshold:
        improvement = 0
        result["confidence_filter_applied"] = True
    else:
        result["confidence_filter_applied"] = False

    if improvement == 1 and not param_plausible:
        improvement = 0
        result["param_override_applied"] = True
    else:
        result["param_override_applied"] = False

    if improvement == 1 and not metric_consistent:
        improvement = 0
        result["metric_override_applied"] = True
    else:
        result["metric_override_applied"] = False

    if improvement == 1 and not image_text_consistent:
        improvement = 0
        result["image_text_override_applied"] = True
    else:
        result["image_text_override_applied"] = False

    if improvement == 1 and hard_warnings:
        improvement = 0
        result["hard_warning_override_applied"] = True
    else:
        result["hard_warning_override_applied"] = False

    # 注意：residual_improved=False 不再强制置 0
    result["residual_override_applied"] = False

    # ===== 判断 improvement 来源 =====
    if improvement == 1 and residual_improved:
        improvement_source = "residual_driven"
    elif improvement == 1 and not residual_improved:
        improvement_source = "metric_driven"
    else:
        improvement_source = "none"

    # ===== 统一最终输出字段 =====
    result["improvement"] = improvement

    # improvement_level 改成更明确的 residual_improvement_level
    result.pop("improvement_level", None)
    result["residual_improvement_level"] = residual_improvement_level

    result["residual_improved"] = residual_improved
    result["param_plausible"] = param_plausible
    result["metric_consistent"] = metric_consistent
    result["image_text_consistent"] = image_text_consistent
    result["hard_warnings"] = hard_warnings

    result["chisq_trend"] = chisq_trend
    result["chisq_nu_trend"] = chisq_nu_trend

    result["param_issues"] = param_issues
    result["metric_issues"] = metric_issues

    result["improvement_source"] = improvement_source

    result["usage"] = usage
    result["original_prev_image_path"] = prev_residual_image_path
    result["original_next_image_path"] = next_residual_image_path
    result["prev_summary_path_used"] = prev_summary_path
    result["new_summary_path_used"] = new_summary_path

    return result



def get_openAI_response_two_images_neutral(
    api_key: str,
    model_name: str,
    prompt: str,
    image_a_path: str,
    image_b_path: str,
    temperature: float = 0.1,
    max_tokens: int = 4096,
    timeout: int = 300,
    url: str = "https://api.road2all.com/v1/chat/completions",
    max_retries: int = 3,
    retry_sleep: int = 10,
):
    """
    与 get_openAI_response_two_images 相同的调用机制，但图片标签【中性】：
    用 "Image A" / "Image B"，不含 previous/next 的先后暗示，用于无顺序偏置对决。
    """
    api_key = api_key or os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("api_key 未提供：请在 .env 中设置 OPENAI_API_KEY（不要硬编码到源码）")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    mime_type_a = get_image_mime_type(image_a_path)
    mime_type_b = get_image_mime_type(image_b_path)

    content = [
        {"type": "text", "text": prompt},
        {"type": "text", "text": "Image A: a GALFIT comparison image. Focus on its residual panel."},
        {"type": "image_url", "image_url": {"url": f"data:{mime_type_a};base64,{encode_image(image_a_path)}"}},
        {"type": "text", "text": "Image B: a GALFIT comparison image. Focus on its residual panel."},
        {"type": "image_url", "image_url": {"url": f"data:{mime_type_b};base64,{encode_image(image_b_path)}"}},
    ]

    data = {
        "model": model_name,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "messages": [{"role": "user", "content": content}],
        "stream": False,
    }

    last_error = None
    for attempt in range(1, max_retries + 1):
        try:
            response = requests.post(url, headers=headers, json=data, timeout=timeout)
            response.raise_for_status()
            resp_json = response.json()
            return (
                resp_json["choices"][0]["message"]["content"],
                resp_json.get("usage", {}),
            )
        except requests.exceptions.HTTPError as e:
            last_error = e
            status_code = e.response.status_code if e.response is not None else None
            print(f"    ⚠️ [API HTTPError] attempt {attempt}/{max_retries}, status_code={status_code}, error={e}")
            if status_code in [429, 500, 502, 503, 504] and attempt < max_retries:
                time.sleep(retry_sleep * attempt)
                continue
            raise
        except requests.exceptions.RequestException as e:
            last_error = e
            print(f"    ⚠️ [API RequestException] attempt {attempt}/{max_retries}, error={e}")
            if attempt < max_retries:
                time.sleep(retry_sleep * attempt)
                continue
            raise

    raise RuntimeError(f"API request failed after {max_retries} retries: {last_error}")


def _compare_two_fits_single(
    image_a_path: str,
    image_b_path: str,
    summary_a_content: str,
    summary_b_content: str,
    model_name: str,
    api_key: str,
    temperature: float = 0.1,
    max_tokens: int = 8192,
    timeout: int = 300,
):
    """对称对决的【单次】调用：判定 Image A vs Image B 哪个拟合更好。

    保留 calculate_reward_model_with_param 的全部天文知识（残差结构、hard warning、
    参数合理性、chisq/chisq_nu/BIC 指标规则），但框架改为【对称】：
    不预设谁是基准、谁是候选，只问 A 和 B 哪个是更好的拟合。

    返回 dict，含 verdict ∈ {"A_better","B_better","tie"} 及各项理由字段。
    """
    prompt = """
You are an astronomer experienced in GALFIT galaxy component decomposition.

You will be given exactly two images:

* Image A: one GALFIT fitting result.
* Image B: another GALFIT fitting result of the SAME galaxy.

Both are independent candidate fits of the same galaxy. NEITHER is a baseline or a
"previous step". Your task is a SYMMETRIC comparison: decide which fit is better,
or whether they are of equal quality. Do not assume either image is the reference.

Each image may be a residual-only image or a full GALFIT comparison figure.
If multiple panels are present, focus primarily on the residual panel, usually titled
"Residual/σ", "Residual", "data-model", or similar. Ignore the observed data panel,
fitted model panel, and surface-brightness profile panel unless needed to check
image-summary correspondence.

The judgement must consider, symmetrically for both fits:

1. galaxy-related residual structures (cleaner residual is better);
2. physical plausibility of the parameters;
3. statistical reasonability of chisq, chisq/nu, and BIC;
4. image-summary consistency;
5. severe residual or fitting warnings.

========================================
Input Pairing
=============

Image A corresponds only to fitting summary A.
Image B corresponds only to fitting summary B.
Do not use parameters or warnings from one summary to interpret the other image.

Fitting summary for Image A:

""" + summary_a_content + """

Fitting summary for Image B:

""" + summary_b_content + """

========================================
Part 1: Residual Image Quality (judge each fit, then compare)
=============================================================

For EACH image independently, assess the galaxy-related residual structures:

* central positive or negative residuals;
* dipole-like red/blue residuals;
* compact clumps near the galaxy center;
* ring, spiral, bar, lens-like, or asymmetric structures;
* coherent large-scale residual patterns.

Ignore features unrelated to the target galaxy: masked regions, foreground stars,
isolated far artifacts, unchanged background noise.

The fit whose residual is visually cleaner / flatter / more structure-free in the
galaxy region is better on the residual dimension. If both residuals are visually
indistinguishable, residuals are a tie.

========================================
Part 2: Hard Warning Check (each fit)
=====================================

For EACH image, check severe warnings:

* "fit_not_converged": summary indicates the fit did not converge.
* "linear_gradient": residual shows a strong large-scale linear background gradient.
* "chaotic_dark_patches": residual contains large irregular dark patches.
* "diffuse_fragments": widespread diffuse fragments unrelated to galaxy structure.
* "unmasked_artifact": residual dominated by unmasked stars, cosmic rays, bad pixels,
  saturated sources, or other artifacts.

A fit with a severe Hard Warning is strongly penalized. If one fit has a hard warning
and the other does not, the clean one is better.

========================================
Part 3: Parameter Plausibility (each fit)
=========================================

For EACH fit, judge whether parameters are physically plausible. Serious issues:

* Sérsic index n < 0.1 or n > 8, unless physically justified (e.g. BCG/cD);
* effective radius Re < 0.2 pixel;
* Re unreasonably large, approaching the image size;
* axis ratio q < 0.05 or q > 1.0;
* large center offsets between components that should be co-centered;
* implausible size hierarchy (unreasonable bulge/disk/bar relation);
* redundant or nearly duplicated components;
* negligible-flux components (mag difference > 5 from major components);
* parameter runaway, severe degeneracy, or unstable parameters;
* more components without residual or metric support.

The fit with more physically plausible parameters is better on the parameter dimension.

========================================
Part 4: Metric Reasonability (compare A vs B)
=============================================

Compare chisq, chisq/nu, and BIC between summary A and summary B.

* chisq, chisq/nu, BIC are all better when smaller.
* chisq/nu measures normalized fitting quality.
* BIC penalizes model complexity; especially important when residuals look similar.

Rules:
* The fit with clearly smaller chisq/nu is better, UNLESS achieved via implausible
  parameters or obvious overfitting.
* When residuals and chisq/nu are similar, the fit with clearly smaller BIC is better
  (more parsimonious, fewer unjustified components).
* A smaller chisq achieved only by adding suspicious/unphysical components, with BIC
  increasing, is NOT better — that is overfitting.
* If both fits have comparable metrics, plausible parameters, and similar residuals,
  the result is a tie.

========================================
Final Symmetric Verdict
=======================

Weigh the dimensions holistically (residual quality is primary; parameter plausibility
and BIC guard against overfitting). Decide:

* "A_better": Image A is the better fit overall.
* "B_better": Image B is the better fit overall.
* "tie": the two fits are of essentially equal quality, or evidence is too ambiguous
  to prefer one.

Be conservative: if one fit reaches lower chisq/nu only through unphysical parameters,
overfitting, or hard warnings, prefer the other (or call it a tie).

========================================
Output Format
=============

Output strictly in JSON. No Markdown. No text outside the JSON.

{
"verdict": "A_better",
"confidence": 0.75,
"a_residual_quality": "clean | minor_structure | strong_structure",
"b_residual_quality": "clean | minor_structure | strong_structure",
"a_param_plausible": true,
"b_param_plausible": true,
"a_hard_warnings": [],
"b_hard_warnings": [],
"chisq_nu_compare": "A_lower | B_lower | similar | unavailable",
"bic_compare": "A_lower | B_lower | similar | unavailable",
"reason": "Concise explanation comparing residual quality, parameter plausibility, hard warnings, and chisq/chisq_nu/BIC between the two fits, and why the chosen verdict holds."
}

Definitions:
* verdict: one of ["A_better", "B_better", "tie"]. The holistic symmetric judgement.
* confidence: float between 0 and 1.
* a_residual_quality / b_residual_quality: one of ["clean", "minor_structure", "strong_structure"].
* a_param_plausible / b_param_plausible: boolean.
* a_hard_warnings / b_hard_warnings: list from ["fit_not_converged","linear_gradient","chaotic_dark_patches","diffuse_fragments","unmasked_artifact"]. Empty list if none.
* chisq_nu_compare / bic_compare: one of ["A_lower","B_lower","similar","unavailable"].
* reason: concise explanation.
"""

    raw_response, usage = get_openAI_response_two_images_neutral(
        api_key=api_key,
        model_name=model_name,
        prompt=prompt,
        image_a_path=image_a_path,
        image_b_path=image_b_path,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=timeout,
    )

    if raw_response is None or not str(raw_response).strip():
        raise ValueError("Model returned empty response in symmetric comparison.")

    result = extract_json_from_text(raw_response)
    verdict = result.get("verdict", "tie")
    if verdict not in ("A_better", "B_better", "tie"):
        verdict = "tie"
    result["verdict"] = verdict
    result["usage"] = usage
    return result


def compare_two_fits_symmetric(
    image_a_path: str,
    image_b_path: str,
    summary_a_path: str = None,
    summary_b_path: str = None,
    model_name: str = "gemini-3.1-pro-preview",
    api_key: str = None,
    temperature: float = 0.1,
    max_tokens: int = 8192,
    timeout: int = 300,
):
    """无顺序偏置的两拟合对决。

    保留 calculate_reward_model_with_param 的全部天文知识，但：
    1. prompt 框架对称（不预设谁是基准），图片标签中性（Image A/B）；
    2. 内部自动跑两次 —— (A,B) 和 调换位置的 (B,A) —— 只有两次判同一赢家才算数，
       否则判 tie。这从机制上彻底消除顺序偏置。

    返回 dict:
      {
        "verdict": "A_better" | "B_better" | "tie",
        "robust": bool,                 # 两个方向是否一致
        "forward":  {...单次结果...},   # (A作Image A, B作Image B)
        "backward": {...单次结果...},   # (B作Image A, A作Image B)
      }
    """
    if api_key is None:
        api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("api_key is None. Please pass api_key or set OPENAI_API_KEY.")

    for p in (image_a_path, image_b_path):
        if not p or not os.path.exists(p):
            raise FileNotFoundError(f"Comparison image not found: {p}")

    def _read(p):
        if p and os.path.exists(p):
            try:
                return read_summary_md(p)
            except Exception as e:
                print(f"    ⚠️ [Symmetric] summary 读取失败 {p}: {e}")
        return "(参数摘要不可用)"

    summary_a_content = _read(summary_a_path)
    summary_b_content = _read(summary_b_path)

    # 方向1: A=Image A, B=Image B
    fwd = _compare_two_fits_single(
        image_a_path, image_b_path, summary_a_content, summary_b_content,
        model_name, api_key, temperature, max_tokens, timeout,
    )
    # 方向2: 调换 —— 把真实的 B 放到 Image A 槽位，真实的 A 放到 Image B 槽位
    bwd_raw = _compare_two_fits_single(
        image_b_path, image_a_path, summary_b_content, summary_a_content,
        model_name, api_key, temperature, max_tokens, timeout,
    )

    # 把方向2的 verdict 翻译回 A/B 语义 (方向2里 "A_better" 实际指真实的 B)
    bwd_verdict_raw = bwd_raw.get("verdict", "tie")
    bwd_verdict_translated = {
        "A_better": "B_better",
        "B_better": "A_better",
        "tie": "tie",
    }[bwd_verdict_raw]

    # 稳健判定：两个方向一致才采纳，否则 tie
    if fwd.get("verdict") == bwd_verdict_translated:
        final_verdict = fwd["verdict"]
        robust = True
    else:
        final_verdict = "tie"
        robust = False

    return {
        "verdict": final_verdict,
        "robust": robust,
        "forward": fwd,
        "backward": bwd_raw,
        "backward_verdict_translated": bwd_verdict_translated,
    }



def calculate_reward(old_metrics: dict, new_metrics: dict, action: dict, step: int,
                     prev_image_path: str = None, next_image_path: str = None,
                     use_llm: bool = False, vlm_reward_model_name: str = "gemini-3.1-pro-preview",
                     use_param_check: bool = False, new_summary_path: str = None,
                     prev_summary_path: str = None) -> tuple:
    """
    终极 Reward 整合枢纽 (三模式)：
    - use_llm=False                        → Mode 1: 纯物理规则驱动 (Rule-based)
    - use_llm=True, use_param_check=False  → Mode 2: 纯视觉大模型驱动 (VLM-based)
    - use_llm=True, use_param_check=True   → Mode 3: VLM 图像 + 参数合理性审查
    """
    # 基础安全检查：如果新变体直接物理崩溃 (Chi2 >= 9999.0)，任何模式都直接判死刑
    if new_metrics.get("chi2_nu", 999.0) >= 9999.0:
        print("    💀 [物理引擎崩溃] 物理引擎崩溃 (Chi2=9999.0)")
        return -100.0, {"fatal_error": "Physics engine crashed (Chi2=9999.0)"}

    # ==========================================
    # 模式 2/3：大模型视觉驱动 (VLM-based)
    # ==========================================
    if use_llm:
        # 根据 use_param_check 选择 Mode 2 或 Mode 3
        if use_param_check and new_summary_path and os.path.exists(str(new_summary_path)):
            mode_label = "VLM+PARAM"
            print("    👁️ [VLM+参数审查] 视觉+参数合理性模式启动: 评判残差图+参数...")
        else:
            mode_label = "VLM_ONLY"
            print("    👁️ [VLM] 纯视觉模式启动: 评判残差图...")

        r_total = 0.0
        r_detail = {"mode": mode_label}

        # 必须确保有图可看
        if prev_image_path and next_image_path and os.path.exists(prev_image_path) and os.path.exists(next_image_path):
            try:
                if mode_label == "VLM+PARAM":
                    vlm_result = calculate_reward_model_with_param(
                        prev_residual_image_path=prev_image_path,
                        next_residual_image_path=next_image_path,
                        prev_summary_path=prev_summary_path,
                        new_summary_path=new_summary_path,
                        model_name=vlm_reward_model_name
                    )
                else:
                    vlm_result = calculate_reward_model(
                        prev_residual_image_path=prev_image_path,
                        next_residual_image_path=next_image_path,
                        model_name=vlm_reward_model_name
                    )

                # 用 final_improvement（已过 置信度/参数/指标/残差 各道闸门），回退到 improvement
                final_imp = vlm_result.get("final_improvement")
                if final_imp is None:
                    final_imp = vlm_result.get("improvement", 0)
                if int(final_imp) == 1:
                    r_total = 1  # 大模型说好，直接给高分
                else:
                    r_total = -1  # 大模型说不好，直接扣分
                print(f"    [{mode_label}] 评判结果: {vlm_result}")

                # Mode 3 额外日志：参数审查结果
                if mode_label == "VLM+PARAM":
                    param_ok = vlm_result.get("param_plausible", True)
                    param_issues = vlm_result.get("param_issues", [])
                    if not param_ok:
                        print(f"    ⚠️ [参数审查] 参数不合理: {param_issues}")
                    if vlm_result.get("param_override_applied"):
                        print(f"    🛡️ [参数审查] 残差改善但参数跑飞 → 降级为 improvement=0")

                r_detail["vlm_detail"] = vlm_result
            except Exception as e:
                print(f"    ⚠️ [VLM 警告] 视觉模型调用失败: {e}")
                print("    📋 完整报错记录:")
                traceback.print_exc()
                full_error = traceback.format_exc()
                r_total = -1.0 # API 失败时的微弱惩罚
                r_detail["error"] = full_error
        else:
            r_total = -10.0
            r_detail["error"] = "Missing image files for VLM."

        r_detail["r_total"] = r_total
        return r_total, r_detail

    # ==========================================
    # 模式 1：纯物理规则驱动 (Rule-based)
    # ==========================================
    else:
        print("    👁️ [Rule-based] 纯物理规则模式启动: 评判残差图...")
        # 完全调用原来的规则函数，不掺杂任何模型得分
        r_total, r_detail = calculate_reward_rule(old_metrics, new_metrics, action, step)
        r_detail["mode"] = "RULE_ONLY"
        
        return r_total, r_detail