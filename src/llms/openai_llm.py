"""
OpenAI LLM provider implementation.

This module provides the OpenAI implementation of the LLM interface,
supporting both text and multimodal models.
"""

import os
from typing import Any, Dict, List, Optional

from openai import OpenAI

from .base import LLMBase


class OpenAILLM(LLMBase):
    """
    OpenAI LLM provider implementation.

    Supports OpenAI's GPT models including multimodal capabilities
    for vision tasks.
    """

    def __init__(self, config: Optional[Dict] = None):
        """
        Initialize OpenAI LLM provider.

        Args:
            config: Configuration dict with optional keys:
                - api_key (str): OpenAI API key (default: from OPENAI_API_KEY env var)
                - base_url (str): API base URL (default: from OPENAI_BASE_URL env var)
                - model (str): Default model name
        """
        super().__init__(config)

        # Get API key from config or environment
        api_key = self.config.get("api_key") if self.config else None
        if not api_key:
            api_key = os.getenv("OPENAI_API_KEY")

        if not api_key:
            raise ValueError(
                "OPENAI_API_KEY not found in config or environment. "
                "Please set it in your config or .env file."
            )

        # Get base URL from config or environment
        base_url = self.config.get("base_url") if self.config else None
        if not base_url:
            base_url = os.getenv("OPENAI_BASE_URL")

        # Initialize OpenAI client
        if base_url:
            self.client = OpenAI(api_key=api_key, base_url=base_url)
        else:
            self.client = OpenAI(api_key=api_key)

        # Default model
        self.default_model = (
            self.config.get("model") if self.config else None
        ) or os.getenv("OPENAI_MODEL", "gpt-4o")

    def chat_completions_create(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Create a chat completion using OpenAI.

        Args:
            messages: List of message dicts with 'role' and 'content'
            model: Model name to use (default: self.default_model)
            max_tokens: Maximum tokens in the response
            temperature: Sampling temperature
            **kwargs: Additional OpenAI parameters

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

        # Call OpenAI API
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
        Check if this OpenAI model supports vision.

        Returns:
            bool: True for GPT-4o, GPT-4V, and similar models
        """
        model = self.default_model.lower()
        vision_models = ["gpt-4o", "gpt-4-v", "gpt-4-vision", "claude-3"]
        return any(vm in model for vm in vision_models)

    def _build_multimodal_messages(
        self,
        system_message: str,
        user_text_content: List[Dict],
        base64_image: str,
        additional_images: Optional[List[Dict]] = None
    ) -> List[Dict]:
        """
        Build multimodal messages in OpenAI format.

        OpenAI format:
        - system message: content as string
        - user message: content as list with text and image_url items

        Args:
            system_message: System message
            user_text_content: List of text content dicts
            base64_image: Base64-encoded image
            additional_images: Optional additional images

        Returns:
            List of message dicts in OpenAI format
        """
        messages = [
            {
                "role": "system",
                "content": system_message
            }
        ]

        # Build user message content
        user_content = []
        user_content.extend(user_text_content)
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

        messages.append({
            "role": "user",
            "content": user_content
        })

        return messages
