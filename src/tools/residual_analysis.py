
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


def component_analysis(
    image_file: Annotated[str, "Path to the combined residual image file [png file] containing three stamps: original, model, residual"],
    summary_file: Annotated[str, "Path to the optimization summary file containing detailed fitting information"],
    mode: Annotated[str, "Fitting mode: 'single-band' for GALFIT or 'multi-band' for GalfitS"],
    custom_instructions: Annotated[str, "Optional custom instructions to guide the component analysis"] = "",
) -> dict[str, Any]:
    """
    Analyze galaxy fitting results to determine component composition and parameter adjustments.

    This function examines the fitting result stamps (Original | Model | Residual) alongside
    the fitting summary, identifies missing or misconfigured physical components (bulge, disk,
    bar, AGN, etc.), and provides actionable suggestions for component addition/removal and
    parameter refinement.

    Args:
        image_file (str): Path to the combined image file containing three stamps displayed horizontally:
                        - Original galaxy image
                        - Model image (fitted model)
                        - Residual image (Original - Model)
                        For multi-band fitting, each band has its own set of stamps.
        summary_file (str): Path to the optimization summary file containing:
                          - Fitted parameter values and their uncertainties
                          - Chi-squared statistics and goodness-of-fit metrics
                          - Component descriptions
        mode (str): 'single-band' for GALFIT or 'multi-band' for GalfitS.
        custom_instructions (str): Optional additional instructions to guide the analysis,

    Returns:
        dict[str, Any]: A dictionary containing:
            - status (str): "success" if analysis completed successfully, "failure" otherwise
            - analysis (str, optional): The diagnostic analysis report (only on success)
            - analysis_file (str, optional): Path to the saved analysis markdown file (only on success)
    """
    # Create LLM client
    client, error = create_vlm_client()
    if error:
        return {"status": "failure", "error": error}

    # Validate input files
    if not os.path.exists(image_file):
        return {"status": "failure", "error": f"Image file not found: {image_file}"}
    if not os.path.exists(summary_file):
        return {"status": "failure", "error": f"Summary file not found: {summary_file}"}

    # Encode image
    base64_image = encode_image_to_base64(image_file)
    if not base64_image:
        return {"status": "failure", "error": f"Failed to encode image: {image_file}"}

    # Read summary
    summary_content = read_summary_file(summary_file)
    if not summary_content:
        return {"status": "failure", "error": f"Failed to read summary file: {summary_file}"}

    # Build prompt and system message from the residual analysis templates
    analysis_prompt = prompt.get_residual_analysis_prompt(summary_content)
    system_message = prompt.RESIDUAL_ANALYSIS_SYSTEM_MESSAGE

    # Append component specification based on mode
    if mode == "multi-band":
        component_spec = prompt.get_component_specification_galfits()
    else:
        component_spec = prompt.get_component_specification_galfit()

    if component_spec:
        system_message = system_message + "\n\n" + component_spec

    if custom_instructions:
        analysis_prompt += f"\n\n--- Additional requirements ---\n{custom_instructions}"
    additional_content = [{"type": "text", "text": analysis_prompt}]

    # Call VLM
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

    # Save analysis
    base_name = os.path.splitext(os.path.basename(image_file))[0]
    output_file = os.path.join(os.path.dirname(image_file), f"{base_name}_component_analysis.md")

    try:
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(analysis)
        print(f"Component analysis saved to: {output_file}")
    except Exception as e:
        print(f"Warning: Failed to save analysis to file: {e}")
        output_file = None

    return {
        "status": "success",
        "analysis": analysis,
        "analysis_file": output_file,
    }
