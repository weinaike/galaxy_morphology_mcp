
import os
import base64
from typing import Optional, Any
import dotenv

try:
    from ..llms import LLMBase, create_llm_client
except ImportError:
    import sys
    sys.path.append(os.path.abspath(os.path.dirname(__file__)))
    from llms import LLMBase, create_llm_client


# 加载环境变量
dotenv.load_dotenv()


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
