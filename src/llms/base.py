"""
LLM base module for galaxy morphology MCP server.

Provides abstract base class for LLM providers, enabling flexible
integration with different language model backends.
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Union


class LLMBase(ABC):
    """
    Abstract base class for all LLM providers.

    This class defines the common interface that all LLM implementations must follow,
    enabling seamless switching between different providers (OpenAI, GLM, etc.).
    """

    def __init__(self, config: Optional[Union[Dict, Any]] = None):
        """
        Initialize the LLM base class.

        Args:
            config: LLM configuration (dict or config object)
        """
        self.config = config or {}

    @abstractmethod
    def chat_completions_create(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Create a chat completion using the LLM.

        Args:
            messages: List of message dicts with 'role' and 'content'
            model: Model name to use
            max_tokens: Maximum tokens in the response
            temperature: Sampling temperature (0-2)
            **kwargs: Additional provider-specific parameters

        Returns:
            Dict containing:
                - content (str): The response content
                - raw_response (Any): Raw response from the provider
                - usage (Dict, optional): Token usage information
        """
        pass

    @abstractmethod
    def supports_vision(self) -> bool:
        """
        Check if this LLM provider supports vision/multimodal capabilities.

        Returns:
            bool: True if vision is supported
        """
        pass

    @abstractmethod
    def _build_multimodal_messages(
        self,
        system_message: str,
        user_text_content: List[Dict],
        base64_image: str,
        additional_images: Optional[List[Dict]] = None
    ) -> List[Dict]:
        """
        Build multimodal messages with image and text content.

        Each LLM provider may have different message format requirements.
        This method should be implemented by subclasses to construct
        provider-specific message formats.

        Args:
            system_message: System message to set the model's behavior
            user_text_content: List of text content dicts to include in the user message
            base64_image: Base64-encoded string of the primary image
            additional_images: Optional list of additional images, each dict has 'base64' and 'description' keys

        Returns:
            List of message dicts in the provider's expected format
        """
        pass

    def chat_with_image(
        self,
        base64_image: str,
        user_text_content: List[Dict],
        system_message: str,
        model: Optional[str] = None,
        max_tokens: int = 9600,
        temperature: float = 0.3,
        additional_images: Optional[List[Dict]] = None
    ) -> Dict[str, Any]:
        """
        High-level method to chat with an image using the LLM.

        This is a convenience method that builds the appropriate messages
        and calls the LLM. Subclasses can override _build_multimodal_messages
        to customize the message format for their specific API.

        Args:
            base64_image: Base64-encoded string of the primary image
            user_text_content: List of text content dicts to include in the user message
            system_message: System message to set the model's behavior
            model: Model name to use
            max_tokens: Maximum tokens in the response
            temperature: Sampling temperature
            additional_images: Optional list of additional images

        Returns:
            Dict with response content and metadata
        """
        # Build messages using provider-specific format
        messages = self._build_multimodal_messages(
            system_message=system_message,
            user_text_content=user_text_content,
            base64_image=base64_image,
            additional_images=additional_images
        )

        # Call the chat completions API
        return self.chat_completions_create(
            messages=messages,
            model=model,
            max_tokens=max_tokens,
            temperature=temperature
        )

    def _validate_messages(self, messages: List[Dict[str, str]]) -> None:
        """
        Validate that messages are in the correct format.

        Args:
            messages: List of message dicts to validate

        Raises:
            ValueError: If messages are invalid
        """
        if not isinstance(messages, list):
            raise ValueError("messages must be a list")

        for msg in messages:
            if not isinstance(msg, dict):
                raise ValueError("Each message must be a dict")
            if "role" not in msg or "content" not in msg:
                raise ValueError("Each message must have 'role' and 'content' keys")
            if msg["role"] not in ["system", "user", "assistant"]:
                raise ValueError(f"Invalid role: {msg['role']}")
