
import os
from typing import Annotated, Any
import dotenv
from . import prompt
from .analyze_image import (
    create_vlm_client,
    encode_image_to_base64,
    read_summary_file,
    call_vlm_api,
)

dotenv.load_dotenv()


def analyze_fitlog(
    image_file: Annotated[str, "Path to the combined residual image file [png file] containing three stamps: original, model, residual"],
    console_file: Annotated[str, "Path to the console_log_file to analyze"],
    summary_file: Annotated[str, "Path to the summary file"],
    custom_instructions: Annotated[str, "Optional custom instructions to guide the VLM analysis"] = "",
) -> dict[str, Any]:
    """Analyze GALFIT console.log and residual images for parameter anomalies using VLM.

    Combines visual inspection of residual images with numerical analysis of the
    fitting log to detect four types of issues in GALFIT fitting output:
    1. Parameter Degeneracy — components "compensate" each other
    2. Uncertainty Anomalies — parameter errors much larger than values
    3. Boundary Hits — parameters at constraint limits
    4. Unphysical Results — parameter combinations violating physical expectations

    Uses a multimodal LLM call to jointly analyze residual images and log text.

    Returns:
        dict with status, analysis text, and analysis_file path.
    """
    # Create VLM client
    client, error = create_vlm_client()
    if error:
        return {"status": "failure", "error": error}

    # Validate input files
    if not os.path.exists(image_file):
        return {"status": "failure", "error": f"Image file not found: {image_file}"}
    if not os.path.exists(console_file):
        return {"status": "failure", "error": f"Console log file not found: {console_file}"}
    if not os.path.exists(summary_file):
        return {"status": "failure", "error": f"Summary file not found: {summary_file}"}

    # Encode image
    base64_image = encode_image_to_base64(image_file)
    if not base64_image:
        return {"status": "failure", "error": f"Failed to encode image: {image_file}"}

    # Read console log content
    log_content = read_summary_file(console_file)
    if not log_content:
        return {"status": "failure", "error": f"Failed to read console log file: {console_file}"}

    # Read summary content
    summary_content = read_summary_file(summary_file)
    if not summary_content:
        return {"status": "failure", "error": f"Failed to read summary file: {summary_file}"}

    # Build prompt and system message
    analysis_prompt = prompt.get_fitlog_analysis_prompt(
        console_content=log_content,
        summary=summary_content,
    )
    system_message = prompt.FITLOG_SYSTEM_MESSAGE

    if custom_instructions:
        analysis_prompt += f"\n\n--- Additional requirements ---\n{custom_instructions}"
    additional_content = [{"type": "text", "text": analysis_prompt}]

    # Call VLM API
    analysis, error = call_vlm_api(
        client=client,  # type: ignore[arg-type]
        base64_image=base64_image,
        additional_content=additional_content,
        system_message=system_message,
    )
    if error:
        return {"status": "failure", "error": error}

    # analysis is guaranteed to be str when error is None
    assert analysis is not None, "Analysis should not be None when error is None"

    # Save analysis to markdown file
    base_name = os.path.splitext(os.path.basename(console_file))[0]
    output_file = os.path.join(os.path.dirname(console_file), f"{base_name}_analysis.md")

    try:
        with open(output_file, "w", encoding="utf-8") as f:
            f.write(analysis)
        print(f"Fitlog analysis saved to: {output_file}")
    except Exception as e:
        print(f"Warning: Failed to save analysis to file: {e}")
        output_file = None
    tips = ("如果overall_verdict的结论为 CRITICAL，则需要根据诊断建议，及时调整参数，重新拟合；没必要看残差图了，因为已经明确拟合结果有问题了\n\n",
            "如果overall_verdict的结论为 WARNING 或 PASS，则可以调用 residual_analysis_by_vlm 进一步分析残差细节\n\n",
            "\n诊断结论：\n",
            analysis
        )
    return {
        "status": "success",
        "analysis": tips,
        "analysis_file": output_file,
    }
