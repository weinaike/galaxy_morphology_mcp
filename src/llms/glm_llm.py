"""
GLM (Zhipu AI) LLM provider implementation.

This module provides the GLM implementation of the LLM interface,
supporting Zhipu AI's chat models including vision capabilities.
"""

import os
from typing import Any, Dict, List, Optional

try:
    from zhipuai import ZhipuAI
except ImportError:
    ZhipuAI = None

from .base import LLMBase


class GlmLLM(LLMBase):
    """
    GLM (Zhipu AI) LLM provider implementation.

    Supports Zhipu AI's GLM models including multimodal capabilities
    for vision tasks through the GLM-4V series models.
    """

    def __init__(self, config: Optional[Dict] = None):
        """
        Initialize GLM LLM provider.

        Args:
            config: Configuration dict with optional keys:
                - api_key (str): Zhipu AI API key (default: from ZHIPUAI_API_KEY env var)
                - base_url (str): API base URL
                - model (str): Default model name (default: glm-4v)
        """
        super().__init__(config)

        if ZhipuAI is None:
            raise ImportError(
                "zhipuai package is required for GlmLLM. "
                "Install it with: pip install zhipuai"
            )

        # Get API key from config or environment
        api_key = self.config.get("api_key") if self.config else None
        if not api_key:
            api_key = os.getenv("ZAI_API_KEY")

        if not api_key:
            raise ValueError(
                "ZAI_API_KEY not found in config or environment. "
                "Please set it in your config or .env file."
            )

        base_url = self.config.get("base_url") if self.config else None
        if not base_url:
            base_url = os.getenv("ZAI_BASE_URL")

        # Initialize ZhipuAI client
        self.client = ZhipuAI(api_key=api_key, base_url=base_url)

        # Default model - GLM-4V for vision capabilities
        self.default_model = (
            self.config.get("model") if self.config else None
        ) or os.getenv("GLM_MODEL", "glm-4.6v")

    def chat_completions_create(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Create a chat completion using Zhipu AI's GLM.

        Args:
            messages: List of message dicts with 'role' and 'content'
            model: Model name to use (default: self.default_model)
            max_tokens: Maximum tokens in the response
            temperature: Sampling temperature
            **kwargs: Additional GLM parameters

        Returns:
            Dict with response content and metadata
        """
        self._validate_messages(messages)

        # Use default model if not specified
        if model is None:
            model = self.default_model

        # Prepare parameters
        params = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }

        # Add any additional parameters
        params.update(kwargs)

        # Call ZhipuAI API
        response = self.client.chat.completions.create(**params)

        # Extract response content
        content = response.choices[0].message.content

        # Extract usage info if available
        usage = None
        if hasattr(response, "usage") and response.usage:
            usage = {
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "total_tokens": response.usage.total_tokens,
            }

        return {
            "content": content,
            "raw_response": response,
            "usage": usage,
        }

    def supports_vision(self) -> bool:
        """
        Check if this GLM model supports vision.

        Returns:
            bool: True for GLM-4V and similar vision models
        """
        model = self.default_model.lower()
        vision_models = ["glm-4v", "glm-4v-plus", "glm-v"]
        return any(vm in model for vm in vision_models)

    def _build_multimodal_messages(
        self,
        system_message: str,
        user_text_content: List[Dict],
        base64_image: str,
        additional_images: Optional[List[Dict]] = None
    ) -> List[Dict]:
        """
        Build multimodal messages in GLM-4V format.

        GLM-4V format requires ALL message content to be in list format
        when using multimodal capabilities.

        Args:
            system_message: System message
            user_text_content: List of text content dicts
            base64_image: Base64-encoded image
            additional_images: Optional additional images

        Returns:
            List of message dicts in GLM-4V format
        """
        messages = [
            {
                "role": "system",
                "content": [{"type": "text", "text": system_message}]
            }
        ]

        # Build user message content - all in list format
        user_content = []
        user_content.extend(user_text_content)
        user_content.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/png;base64,{base64_image}"}
        })

        # Add additional images if provided
        if additional_images:
            for img in additional_images:
                user_content.append({
                    "type": "text",
                    "text": f"\n\nAdditional image: {img['description']}"
                })
                user_content.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{img['base64']}"}
                })

        messages.append({
            "role": "user",
            "content": user_content
        })

        return messages
