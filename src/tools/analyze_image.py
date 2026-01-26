
import os
import base64
from typing import Annotated, Optional, Any
from openai import OpenAI
import dotenv
from . import prompt


# 加载环境变量
dotenv.load_dotenv()


# 多模态模型对galfits 或 galfit 的优化结果进行分析
# 输入图像路径和优化总结文件。VLLM阅读图像，一步步分析图像中的内容，并结合优化总结文件，生成推理描述，说明图像中的主要特征和优化结果。并提供下一步的决策建议


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


def create_vllm_client() -> tuple[Optional[OpenAI], Optional[str]]:
    """
    Create and initialize the VLLM (OpenAI-compatible) client.

    Returns:
        tuple[Optional[OpenAI], Optional[str]]: 
            - OpenAI client instance if successful, None otherwise
            - Error message if failed, None otherwise
    """
    # Get API key from environment variable
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return None, "OPENAI_API_KEY environment variable is not set. Please set it in your environment or .env file."

    # Initialize OpenAI client
    base_url = os.getenv("OPENAI_BASE_URL")
    if base_url:
        client = OpenAI(api_key=api_key, base_url=base_url)
    else:
        client = OpenAI(api_key=api_key)

    return client, None


def call_vllm_api(
    client: OpenAI,
    base64_image: str,
    additional_content: list[dict[str, Any]],
    system_message: str,
    model: Optional[str] = None,
    max_tokens: int = 9600,
    temperature: float = 0.3,
    additional_images: Optional[list[dict[str, str]]] = None
) -> tuple[Optional[str], Optional[str]]:
    """
    Call the VLLM multimodal API with an image and additional content.

    Args:
        client (OpenAI): The initialized OpenAI client.
        base64_image (str): Base64-encoded string of the primary image.
        additional_content (list[dict[str, Any]]): Additional text content to include in the message.
        system_message (str): System message to set the model's behavior.
        model (Optional[str]): Model name. If None, reads from OPENAI_MODEL environment variable.
        max_tokens (int): Maximum tokens in the response. Default is 9600.
        temperature (float): Sampling temperature. Lower values produce more deterministic responses. Default is 0.3.
        additional_images (Optional[list[dict[str, str]]]): List of additional images to include. Each dict should have 'base64' and 'description' keys.

    Returns:
        tuple[Optional[str], Optional[str]]:
            - The analysis response if successful, None otherwise
            - Error message if failed, None otherwise
    """
    # Get model from environment variable or use default
    if model is None:
        model = os.getenv("OPENAI_MODEL", "gpt-5.2")  # gpt-5.2 supports multimodal

    # Build the user message content
    user_content = []
    user_content.extend(additional_content)
    user_content.append({
        "type": "image_url",
        "image_url": {
            "url": f"data:image/png;base64,{base64_image}"
        }
    })

    # Add additional images if provided
    if additional_images:
        for img in additional_images:
            user_content.append({
                "type": "text",
                "text": f"\n\nAdditional image: {img['description']}",
            })
            user_content.append({
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/png;base64,{img['base64']}"
                }
            })

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": system_message
                },
                {
                    "role": "user",
                    "content": user_content
                }
            ],
            max_tokens=max_tokens,
            temperature=temperature,
        )

        # Extract the analysis
        analysis = response.choices[0].message.content
        return analysis, None

    except Exception as e:
        return None, f"Error calling VLLM API: {str(e)}"


# Galfit 专用的分析接口

def galfit_analyze_by_vllm(
    image_file: Annotated[str, "Path to the combined image file [png file] containing three stamps displayed horizontally: original, model, residual"],
    summary_file: Annotated[str, "Path to the optimization summary file containing detailed fitting information"]
) -> dict[str, Any]:
    """
    Analyze the optimization results from GALFIT using a multimodal model.

    This function performs a comprehensive analysis of GALFIT optimization results by combining
    visual inspection with quantitative evaluation from the optimization summary. The multimodal model
    examines the image stamps and optimization parameters to assess the quality and reasonableness
    of the fitting process.

    Args:
        image_file (str): Path to the combined image file containing three stamps displayed horizontally:
                        - Original galaxy image (source galaxy)
                        - Model image (fitted model)
                        - Residual image (difference between original and model)
                        These stamps are arranged side-by-side to facilitate visual comparison and
                        analysis by the multimodal model.
        summary_file (str): Path to the optimization summary file containing detailed information
                          about the fitting process, including:
                          - Optimization iterations and convergence status
                          - Fitted parameter values and their uncertainties
                          - Chi-squared statistics and goodness-of-fit metrics
                          - Component descriptions and parameter bounds

    Returns:
        dict[str, Any]: A dictionary containing the analysis results:
            - status (str): "success" if analysis completed successfully, "failure" otherwise
            - analysis (str, optional): The comprehensive analysis report (only on success)
            - analysis_file (str, optional): Path to the saved analysis markdown file (only on success)
    """
    # Create VLLM client
    client, error = create_vllm_client()
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

    # Prepare additional content
    additional_content = [
        {
            "type": "text",
            "text": analysis_prompt
        }
    ]

    # System message
    system_message = prompt.GALFIT_SYSTEM_MESSAGE

    # Call VLLM API
    analysis, error = call_vllm_api(
        client=client,
        base64_image=base64_image,
        additional_content=additional_content,
        system_message=system_message
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

    # Return successful result
    return {
        "status": "success",
        "analysis": analysis,
        "analysis_file": output_file
    }
    


# GalfitS 专用分析接口 - 多波段拟合

def galfits_analyze_by_vllm(
    image_file: Annotated[str, "Path to the combined image file [png file] containing three stamps displayed horizontally: original, model, residual"],
    sed_file: Annotated[str, "Path to the SED model file generated by GalfitS"],
    summary_file: Annotated[str, "Path to the optimization summary file containing detailed fitting information"]
) -> dict[str, Any]:
    """
    Analyze the multi-band optimization results from GalfitS using a multimodal model.

    This function performs a comprehensive analysis of GalfitS multi-band optimization results by combining
    visual inspection with quantitative evaluation from the optimization summary and SED (Spectral Energy
    Distribution) model. Unlike the single-band fitting in `galfit_analyze_by_vllm`, GalfitS performs simultaneous
    fitting across multiple photometric bands, constraining the galaxy morphology parameters consistently
    while allowing the SED to vary across bands.

    The multimodal model examines the image stamps, SED model, and optimization parameters to assess
    the quality and reasonableness of the multi-band fitting process, taking advantage of the additional
    information provided by fitting multiple wavelengths simultaneously.

    Args:
        image_file (str): Path to the combined image file containing three stamps displayed horizontally:
                        - Original galaxy image (source galaxy, typically from a representative band)
                        - Model image (fitted model from multi-band optimization)
                        - Residual image (difference between original and model)
                        These stamps are arranged side-by-side to facilitate visual comparison and
                        analysis by the multimodal model.
        sed_file (str): Path to the SED model image file generated by GalfitS (PNG format), containing a plot of the
                        spectral energy distribution that describes how the galaxy's flux varies across different
                        bands. This provides crucial multi-band constraints that are not available in single-band fits.
        summary_file (str): Path to the optimization summary file containing detailed information
                          about the multi-band fitting process, including:
                          - Multi-band optimization iterations and convergence status
                          - Fitted parameter values (shared across bands) and their uncertainties
                          - SED parameters specific to each band
                          - Chi-squared statistics per band and combined goodness-of-fit metrics
                          - Component descriptions and parameter bounds

    Returns:
        dict[str, Any]: A dictionary containing the analysis results:
            - status (str): "success" if analysis completed successfully, "failure" otherwise
            - analysis (str, optional): The comprehensive analysis report (only on success)
            - analysis_file (str, optional): Path to the saved analysis markdown file (only on success)

    Note:
        This function should be used for GalfitS results which perform multi-band fitting with SED modeling.
        For single-band GALFIT results without SED information, use `galfit_analyze_by_vllm` instead.
    """
    # Create VLLM client
    client, error = create_vllm_client()
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
    if not os.path.exists(sed_file):
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

    # Encode SED image to base64
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

    # Create the prompt for the multimodal model
    analysis_prompt = prompt.get_galfits_analysis_prompt(summary_content)

    # Prepare additional content
    additional_content = [
        {
            "type": "text",
            "text": analysis_prompt
        }
    ]

    # System message
    system_message = prompt.GALFITS_SYSTEM_MESSAGE

    # Prepare additional images (SED image)
    additional_images = [
        {
            "base64": base64_sed_image,
            "description": "SED (Spectral Energy Distribution) plot showing the multi-band fitting results with flux vs wavelength"
        }
    ]

    # Call VLLM API with both main image and SED image
    analysis, error = call_vllm_api(
        client=client,
        base64_image=base64_image,
        additional_content=additional_content,
        system_message=system_message,
        additional_images=additional_images
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

    # Return successful result
    return {
        "status": "success",
        "analysis": analysis,
        "analysis_file": output_file
    }

