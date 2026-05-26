
import os
import base64
import re
from typing import Annotated, Optional, Any
import dotenv

# 处理相对导入和绝对导入
try:
    from . import prompt
except ImportError:
    import sys
    sys.path.append(os.path.abspath(os.path.dirname(__file__)))
    import prompt

try:
    from ..llms import LLMBase, create_llm_client
except ImportError:
    import sys
    sys.path.append(os.path.abspath(os.path.dirname(__file__)))
    from llms import LLMBase, create_llm_client


# 加载环境变量
dotenv.load_dotenv()


def _backfill_fitting_log(image_file: str, analysis: str, config_file: str) -> None:
    """Backfill the latest pending round in fitting_log.md with VLM analysis results.

    Best-effort: silently skips if fitting_log.md doesn't exist or has no pending round.
    """
    # Galaxy directory = look up from image path (output/.../...) to config directory
    image_dir = os.path.dirname(os.path.abspath(image_file))
    # Walk up to find fitting_log.md
    search_dir = image_dir
    log_path = None
    for _ in range(5):
        candidate = os.path.join(search_dir, "fitting_log.md")
        if os.path.exists(candidate):
            log_path = candidate
            break
        search_dir = os.path.dirname(search_dir)

    if not log_path:
        return

    with open(log_path, "r", encoding="utf-8") as f:
        content = f.read()

    # Only backfill if there's a pending round
    if "*(pending)*" not in content:
        return

    # Extract key sections from VLM analysis
    judgement = "N/A"
    avg_score = None
    problems = []
    next_step = "N/A"
    reasons = []

    lines = analysis.split("\n")
    current_section = None
    for line in lines:
        line_stripped = line.strip()

        if "Overall Score" in line_stripped or "Average Score" in line_stripped or "average score" in line_stripped.lower():
            # Extract numeric score (e.g. "63.9/100" or "54.0/100")
            score_match = re.search(r'(\d+\.?\d*)\s*/\s*100', line_stripped)
            if score_match:
                avg_score = float(score_match.group(1))
            # Extract tier info
            for tier in ["Excellent", "Good", "Fair", "Poor", "Failed"]:
                if tier.lower() in line_stripped.lower():
                    judgement = f"Tier: {tier}"
                    break

        if "Overall Quality Tier" in line_stripped:
            for tier in ["Tier 1", "Tier 2", "Tier 3", "Tier 4", "Tier 5"]:
                if tier in line_stripped:
                    judgement = f"{tier}"
                    break

        if "Primary Issue" in line_stripped or "Primary issue" in line_stripped:
            problems.append(line_stripped)
        elif "Secondary Issue" in line_stripped or "Secondary issue" in line_stripped:
            problems.append(line_stripped)

        if "Recommended Action" in line_stripped or "**7. Recommended" in line_stripped:
            current_section = "action"
        elif "**8. Next Steps" in line_stripped:
            current_section = "next"
        elif "Key Conclusion" in line_stripped or "**6." in line_stripped:
            current_section = "conclusion"
        elif "Reasoning Process" in line_stripped or "**3." in line_stripped:
            current_section = "reasoning"

        if current_section == "action" and line_stripped.startswith("-"):
            next_step = line_stripped.lstrip("- ")
            current_section = None
        elif current_section == "conclusion" and line_stripped.startswith("-"):
            reasons.append(line_stripped.lstrip("- "))

    # Build replacement text
    score_str = f" (avg score {avg_score:.1f}/100)" if avg_score is not None else ""
    judgement_text = f"- Overall Judgement: {judgement}{score_str}\n"
    problems_text = "- Fitting problems:\n"
    for p in problems:
        problems_text += f"  {p}\n"
    next_step_text = f"- Next-Step Decision: {next_step}\n"
    reasons_text = "- Reasons:\n"
    for r in reasons[:3]:
        reasons_text += f"  - {r}\n"

    # Replace the pending block in the last round
    old_pending = """- Overall Judgement: *(pending)*

- Fitting problems:
  - *(pending)*

- Next-Step Decision: *(pending)*

- Reasons: *(pending)*"""

    new_filled = f"""{judgement_text}
{problems_text}
{next_step_text}
{reasons_text}"""

    # Only replace the LAST occurrence
    last_idx = content.rfind(old_pending)
    if last_idx >= 0:
        content = content[:last_idx] + new_filled + content[last_idx + len(old_pending):]
        with open(log_path, "w", encoding="utf-8") as f:
            f.write(content)


# 多模态模型对galfits 或 galfit 的优化结果进行分析
# 输入图像路径和优化总结文件。VLM阅读图像，一步步分析图像中的内容，并结合优化总结文件，生成推理描述，说明图像中的主要特征和优化结果。并提供下一步的决策建议


def encode_image_to_base64(image_path: str) -> Optional[str]:
    """
    Encode an image file to base64 string.

    Args:
        image_path (str): Path to the image file.

    Returns:
        Optional[str]: Base64 encoded string of the image, or None if encoding fails.
    """
    try:
        with open(image_path, "rb") as image_file:
            return base64.b64encode(image_file.read()).decode('utf-8')
    except Exception as e:
        print(f"Error encoding image: {e}")
        return None


def read_summary_file(summary_path: str) -> Optional[str]:
    """
    Read the optimization summary file.

    Args:
        summary_path (str): Path to the summary file.

    Returns:
        Optional[str]: Content of the summary file, or None if reading fails.
    """
    try:
        with open(summary_path, 'r') as f:
            return f.read()
    except Exception as e:
        print(f"Error reading summary file: {e}")
        return None


def read_file_content(file_path: str) -> Optional[str]:
    """
    Read the content of a text file.

    Args:
        file_path (str): Path to the file.

    Returns:
        Optional[str]: Content of the file, or None if reading fails.
    """
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            return f.read()
    except Exception as e:
        print(f"Error reading file: {e}")
        return None


def create_vlm_client(
    llm_type: str = "openai",
    config: Optional[dict] = None
) -> tuple[Optional[LLMBase], Optional[str]]:
    """
    Create and initialize the LLM client.

    Args:
        llm_type: Type of LLM to create. Supported values:
            - "openai": OpenAI GPT models
            - "glm": Zhipu AI GLM models
        config: Optional configuration dict for the LLM

    Returns:
        tuple[Optional[LLMBase], Optional[str]]:
            - LLM client instance if successful, None otherwise
            - Error message if failed, None otherwise
    """
    try:
        client = create_llm_client(llm_type=llm_type, config=config)
        return client, None
    except ValueError as e:
        return None, str(e)
    except Exception as e:
        return None, f"Error creating LLM client: {str(e)}"


def call_vlm_api(
    client: LLMBase,
    base64_image: str,
    additional_content: list[dict[str, Any]],
    system_message: str,
    model: Optional[str] = None,
    max_tokens: int = 9600,
    temperature: float = 0.3,
    additional_images: Optional[list[dict[str, str]]] = None
) -> tuple[Optional[str], Optional[str]]:
    """
    Call the LLM multimodal API with an image and additional content.

    Args:
        client (LLMBase): The initialized LLM client.
        base64_image (str): Base64-encoded string of the primary image.
        additional_content (list[dict[str, Any]]): Additional text content to include in the message.
        system_message (str): System message to set the model's behavior.
        model (Optional[str]): Model name. If None, uses the client's default model.
        max_tokens (int): Maximum tokens in the response. Default is 9600.
        temperature (float): Sampling temperature. Lower values produce more deterministic responses. Default is 0.3.
        additional_images (Optional[list[dict[str, str]]]): List of additional images to include. Each dict should have 'base64' and 'description' keys.

    Returns:
        tuple[Optional[str], Optional[str]]:
            - The analysis response if successful, None otherwise
            - Error message if failed, None otherwise
    """
    max_retries = 3
    last_error = None
    for attempt in range(1, max_retries + 1):
        try:
            result = client.chat_with_image(
                base64_image=base64_image,
                user_text_content=additional_content,
                system_message=system_message,
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
                additional_images=additional_images
            )

            analysis = result.get("content")
            return analysis, None

        except Exception as e:
            last_error = e
            if attempt < max_retries:
                wait = attempt * 10
                print(f"VLM API call failed (attempt {attempt}/{max_retries}): {e}. Retrying in {wait}s...")
                import time
                time.sleep(wait)

    return None, f"VLM API failed after {max_retries} attempts: {str(last_error)}"


# Galfit 专用的分析接口

def galfit_analyze_by_vlm(
    image_file: Annotated[str, "Path to the combined image file [png file] containing three stamps displayed horizontally: original, model, residual"],
    summary_file: Annotated[str, "Path to the optimization summary file containing detailed fitting information"],
    custom_instructions: Annotated[str, "Optional custom instructions to guide the VLM analysis"] = "",
    llm_type: Annotated[str, "LLM provider: 'openai' or 'glm'"] = "openai",
    model: Annotated[str | None, "Model name, e.g. 'gpt-4o', 'glm-4.6v'. None uses provider default"] = None,
) -> dict[str, Any]:
    """
    Analyze the optimization results from GALFIT using a multimodal model.

    This function performs a comprehensive analysis of GALFIT optimization results by combining
    visual inspection with quantitative evaluation from the optimization summary. The multimodal model
    examines the image stamps and optimization parameters to assess the quality and reasonableness
    of the fitting process.

    Returns:
        dict[str, Any]: A dictionary containing the analysis results:
            - status (str): "success" if analysis completed successfully, "failure" otherwise
            - analysis (str, optional): The comprehensive analysis report (only on success)
            - analysis_file (str, optional): Path to the saved analysis markdown file (only on success)
    """
    # Create LLM client
    config = {"model": model} if model else None
    client, error = create_vlm_client(llm_type=llm_type, config=config)
    if error:
        return {
            "status": "failure",
            "error": error
        }

    # Validate input files exist
    if not os.path.exists(image_file):
        return {
            "status": "failure",
            "error": f"Image file not found: {image_file}"
        }
    if not os.path.exists(summary_file):
        return {
            "status": "failure",
            "error": f"Summary file not found: {summary_file}"
        }

    # Encode image to base64
    base64_image = encode_image_to_base64(image_file)
    if not base64_image:
        return {
            "status": "failure",
            "error": f"Failed to encode image: {image_file}"
        }

    # Read summary file
    summary_content = read_summary_file(summary_file)
    if not summary_content:
        return {
            "status": "failure",
            "error": f"Failed to read summary file: {summary_file}"
        }

    # Create the prompt for the multimodal model
    analysis_prompt = prompt.get_galfit_analysis_prompt(summary_content)

    if custom_instructions:
        analysis_prompt += f"\n\n--- Additional requirements ---\n{custom_instructions}"

    # Prepare additional content
    additional_content = [
        {
            "type": "text",
            "text": analysis_prompt
        }
    ]

    # System message
    system_message = prompt.GALFIT_SYSTEM_MESSAGE

    # Call VLM API
    analysis, error = call_vlm_api(
        client=client,
        base64_image=base64_image,
        additional_content=additional_content,
        system_message=system_message,
        model=model,
    )

    if error:
        return {
            "status": "failure",
            "error": error
        }

    # Save analysis to markdown file
    base_name = os.path.splitext(os.path.basename(image_file))[0]
    output_file = os.path.join(os.path.dirname(image_file), f"{base_name}_analysis.md")

    try:
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(analysis)
        print(f"Analysis saved to: {output_file}")
    except Exception as e:
        print(f"Warning: Failed to save analysis to file: {e}")
        output_file = None

    # Backfill fitting_log.md with VLM analysis
    try:
        _backfill_fitting_log(image_file, analysis, summary_file)
    except Exception:
        pass

    # Return successful result
    return {
        "status": "success",
        "analysis": analysis,
        "analysis_file": output_file
    }



# GalfitS 专用分析接口 - 多波段拟合

def galfits_analyze_by_vlm(
    image_file: Annotated[str, "Path to the combined image file [png file] containing three stamps displayed horizontally: original, model, residual"],
    summary_file: Annotated[str, "Path to the optimization summary file containing detailed fitting information"],
    config_file: Annotated[str, "Path to the GalfitS configuration file used for the optimization"],
    user_prompt: Annotated[str, "Structured analysis request for the internal agent. Extract and organize user observations into this format:\n\n"
                    "## CURRENT PHASE\n"
                    "Phase 1/2/3 (from user input)\n\n"
                    "## USER OBSERVATIONS\n"
                    "### Image Residuals\n"
                    "- Describe patterns: e.g., 'Circular positive residual in center', 'Blue/red split', 'Flat noise-like'\n\n"
                    "### Summary Statistics\n"
                    "- Parameters hitting limits? (Yes/No - specify which)\n"
                    "- Reduced chi-squared issues? (Yes/No - specify bands)\n\n"
                    "### SED Analysis (if Phase 2/3)\n"
                    "- Data vs Model quality: e.g., 'Good match', 'Large deviation'\n\n"
                    "## USER QUESTION\n"
                    "- Specific question if any (e.g., 'Is this acceptable to proceed?', 'What component should I add?')\n\n"
                    "Example: '## CURRENT PHASE\\nPhase 1\\n\\n## USER OBSERVATIONS\\n"
                    "### Image Residuals\\n- Circular positive residual in center\\n"
                    "### Summary Statistics\\n- No parameters hitting limits\\n- Chi-sq balanced\\n'"],
    sed_file: Annotated[Optional[str], "Path to the SED model file generated by GalfitS (optional)"] = None,
    llm_type: Annotated[str, "LLM provider: 'openai' or 'glm'"] = "openai",
    model: Annotated[str | None, "Model name, e.g. 'gpt-4o', 'glm-4.6v'. None uses provider default"] = None,
) -> dict[str, Any]:
    """
    Analyze the multi-band optimization results from GalfitS using a multimodal model.

    This function performs a comprehensive analysis of GalfitS multi-band optimization results by combining
    visual inspection with quantitative evaluation from the optimization summary, configuration file,
    and SED (Spectral Energy Distribution) model. Unlike the single-band fitting in `galfit_analyze_by_vlm`,
    GalfitS performs simultaneous fitting across multiple photometric bands, constraining the galaxy morphology
    parameters consistently while allowing the SED to vary across bands.

    The multimodal model examines the image stamps, SED model, configuration file, and optimization parameters
    to assess the quality and reasonableness of the multi-band fitting process, taking advantage of the
    additional information provided by fitting multiple wavelengths simultaneously.

    Args:
        image_file (str): Path to the combined image file containing three stamps displayed horizontally:
                        - Original galaxy image (source galaxy, typically from a representative band)
                        - Model image (fitted model from multi-band optimization)
                        - Residual image (difference between original and model)
                        These stamps are arranged side-by-side to facilitate visual comparison and
                        analysis by the multimodal model.
        summary_file (str): Path to the optimization summary file containing detailed information
                          about the multi-band fitting process, including:
                          - Multi-band optimization iterations and convergence status
                          - Fitted parameter values (shared across bands) and their uncertainties
                          - SED parameters specific to each band
                          - Chi-squared statistics per band and combined goodness-of-fit metrics
                          - Component descriptions and parameter bounds
        config_file (str): Path to the GalfitS configuration file used for the optimization, containing:
                         - Input/output file paths for each band
                         - Initial parameter guesses and bounds
                         - Component definitions (Sersic, exponential, etc.)
                         - Constraint settings and optimization options
                         - This provides crucial context for understanding the fitting setup
        user_prompt (str): Structured observations extracted from user input by the external agent.
                          This parameter should contain organized information following this template:

                          ```
                          ## CURRENT PHASE
                          Phase 1/2/3

                          ## USER OBSERVATIONS
                          ### Image Residuals
                          - [Describe patterns: e.g., "Circular positive residual in center"]

                          ### Summary Statistics
                          - Parameters hitting limits? [Yes/No - specify which]
                          - Reduced chi-squared issues? [Yes/No - specify bands]

                          ### SED Analysis (if Phase 2/3)
                          - Data vs Model quality: [e.g., "Good match" / "Large deviation"]

                          ## USER QUESTION
                          - [Specific question if any]
                          ```

                          **For External Agent:**
                          Extract key information from user's natural language input and structure it
                          according to the template above. The internal agent will use this structured
                          input along with the image and summary file to provide diagnosis.

                          **Example User Input → Structured Output:**
                          User: "I'm in Phase 1 and see a circular bright spot in the residual center."
                          → agent structures as: "## CURRENT PHASE\\nPhase 1\\n\\n## USER OBSERVATIONS\\n### Image Residuals\\n- Circular positive residual in center"
        sed_file (str, optional): Path to the SED model image file generated by GalfitS (PNG format),
                                  containing a plot of the spectral energy distribution that describes how
                                  the galaxy's flux varies across different bands. This provides crucial
                                  multi-band constraints that are not available in single-band fits.
        llm_type (str): LLM provider type. Supported: "openai", "glm". Default is "openai".
        llm_config (dict, optional): Additional LLM configuration parameters.

    Returns:
        dict[str, Any]: A dictionary containing the analysis results:
            - status (str): "success" if analysis completed successfully, "failure" otherwise
            - analysis (str, optional): The comprehensive analysis report (only on success)
            - analysis_file (str, optional): Path to the saved analysis markdown file (only on success)

    Note:
        This function should be used for GalfitS results which perform multi-band fitting with SED modeling.
        For single-band GALFIT results without SED information, use `galfit_analyze_by_vlm` instead.
    """
    # Create LLM client
    config = {"model": model} if model else None
    client, error = create_vlm_client(llm_type=llm_type, config=config)
    if error:
        return {
            "status": "failure",
            "error": error
        }

    # Validate input files exist
    if not os.path.exists(image_file):
        return {
            "status": "failure",
            "error": f"Image file not found: {image_file}"
        }
    if not os.path.exists(config_file):
        return {
            "status": "failure",
            "error": f"Config file not found: {config_file}"
        }
    if sed_file is not None and not os.path.exists(sed_file):
        return {
            "status": "failure",
            "error": f"SED file not found: {sed_file}"
        }
    if not os.path.exists(summary_file):
        return {
            "status": "failure",
            "error": f"Summary file not found: {summary_file}"
        }

    # Encode image to base64
    base64_image = encode_image_to_base64(image_file)
    if not base64_image:
        return {
            "status": "failure",
            "error": f"Failed to encode image: {image_file}"
        }

    # Encode SED image to base64 (if provided)
    base64_sed_image = None
    if sed_file is not None:
        base64_sed_image = encode_image_to_base64(sed_file)
        if not base64_sed_image:
            return {
                "status": "failure",
                "error": f"Failed to encode SED image: {sed_file}"
            }

    # Read summary file
    summary_content = read_summary_file(summary_file)
    if not summary_content:
        return {
            "status": "failure",
            "error": f"Failed to read summary file: {summary_file}"
        }

    # Read config file
    config_content = read_file_content(config_file)
    if not config_content:
        return {
            "status": "failure",
            "error": f"Failed to read config file: {config_file}"
        }

    # Create the prompt for the multimodal model
    analysis_prompt = prompt.get_galfits_analysis_prompt(summary_content, config_content, user_prompt)

    # Prepare additional content
    additional_content = [
        {
            "type": "text",
            "text": analysis_prompt
        }
    ]

    # System message
    system_message = prompt.GALFITS_SYSTEM_MESSAGE

    # Prepare additional images (SED image, if provided)
    additional_images = None
    if base64_sed_image is not None:
        additional_images = [
            {
                "base64": base64_sed_image,
                "description": "SED (Spectral Energy Distribution) plot showing the multi-band fitting results with flux vs wavelength"
            }
        ]

    # Call VLLM API with main image and optionally SED image
    analysis, error = call_vlm_api(
        client=client,
        base64_image=base64_image,
        additional_content=additional_content,
        system_message=system_message,
        model=model,
        additional_images=additional_images
    )

    if error:
        return {
            "status": "failure",
            "error": error
        }

    # analysis is guaranteed to be str when error is None
    assert analysis is not None, "Analysis should not be None when error is None"

    # Save analysis to markdown file
    base_name = os.path.splitext(os.path.basename(image_file))[0]
    output_file = os.path.join(os.path.dirname(image_file), f"{base_name}_analysis.md")

    try:
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(analysis)
        print(f"Analysis saved to: {output_file}")
    except Exception as e:
        print(f"Warning: Failed to save analysis to file: {e}")
        output_file = None

    # Backfill fitting_log.md with VLM analysis
    try:
        _backfill_fitting_log(image_file, analysis, config_file)
    except Exception:
        pass

    # Return successful result
    return {
        "status": "success",
        "analysis": analysis,
        "analysis_file": output_file
    }

