
import os
from typing import Annotated, Any

from . import prompt
from .analyze_image import create_vlm_client, encode_image_to_base64, call_vlm_api


def view_original_image(
    image_file: Annotated[str, "Path to the galaxy image file [png/jpg] to be classified"],
    source_id: Annotated[str, "Identifier for the source/galaxy in the image"] = "",
    custom_instructions: Annotated[str, "Optional custom instructions to guide the VLM classification"] = "",
) -> dict[str, Any]:
    """
    Analyze an original galaxy image to extract galaxy morphological classification
    and structural component information.

    This function uses a multimodal model to classify galaxy morphology based on
    structural components visible in the image. It follows a three-stage reasoning
    process: overall structure description, component analysis, and final classification.

    The model identifies:
    - Number of galaxies in the image
    - Galaxy center positions
    - Morphology type (disk, elliptical, or uncertain)
    - Presence of bar, spiral arms, and tidal tails
    - Confidence level of each classification

    Args:
        image_file (str): Path to the galaxy image file to analyze.
                         Supports common image formats (PNG, JPG).
        source_id (str): Identifier for the source/galaxy. Default is empty string.
        custom_instructions (str): Optional custom instructions to guide the VLM classification.

    Returns:
        dict[str, Any]: A dictionary containing the classification results:
            - status (str): "success" if analysis completed successfully, "failure" otherwise
            - classification (str, optional): The raw JSON classification result from the model (only on success)
            - classification_file (str, optional): Path to the saved classification JSON file (only on success)
    """
    # Create LLM client
    client, error = create_vlm_client()
    if error:
        return {
            "status": "failure",
            "error": error
        }

    # Validate input file exists
    if not os.path.exists(image_file):
        return {
            "status": "failure",
            "error": f"Image file not found: {image_file}"
        }

    # Encode image to base64
    base64_image = encode_image_to_base64(image_file)
    if not base64_image:
        return {
            "status": "failure",
            "error": f"Failed to encode image: {image_file}"
        }

    # Get classification prompt and system message
    classification_prompt = prompt.get_classification_prompt()
    system_message = prompt.CLASSIFICATION_SYSTEM_MESSAGE

    # Inject source_id into the prompt context
    prompt_text = f"source_id: {source_id}\n\n{classification_prompt}"
    if custom_instructions:
        prompt_text += f"\n\n--- Additional requirements ---\n{custom_instructions}"
    additional_content = [
        {
            "type": "text",
            "text": prompt_text
        }
    ]

    # Call LLM API
    classification, error = call_vlm_api(
        client=client,
        base64_image=base64_image,
        additional_content=additional_content,
        system_message=system_message,
        max_tokens=4800,
        temperature=0.2
    )

    if error or not classification:
        return {
            "status": "failure",
            "error": error
        }

    # Save classification result
    base_name = os.path.splitext(os.path.basename(image_file))[0]
    output_file = os.path.join(os.path.dirname(image_file), f"{base_name}_classification.md")

    try:
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(classification)
        print(f"Classification saved to: {output_file}")
    except Exception as e:
        print(f"Warning: Failed to save classification to file: {e}")
        output_file = None

    return {
        "status": "success",
        "classification": classification + '\n该结果仅供参考，实际成分需要结合残差图和拟合结果进行综合判断。',
        "classification_file": output_file
    }
