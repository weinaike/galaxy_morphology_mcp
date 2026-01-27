"""
LLM providers for galaxy morphology MCP server.

This module provides a unified interface for different LLM providers,
including OpenAI and Zhipu AI (GLM).
"""

from typing import Optional
from .base import LLMBase
from .openai_llm import OpenAILLM
from .glm_llm import GlmLLM

__all__ = [
    "LLMBase",
    "OpenAILLM",
    "GlmLLM",
    "create_llm_client",
]


def create_llm_client(
    llm_type: str = "openai",
    config: Optional[dict] = None
) -> LLMBase:
    """
    Create an LLM client based on the specified type.

    Args:
        llm_type: Type of LLM to create. Supported values:
            - "openai": OpenAI GPT models
            - "glm": Zhipu AI GLM models
        config: Optional configuration dict for the LLM

    Returns:
        LLMBase: An instance of the requested LLM provider

    Raises:
        ValueError: If llm_type is not supported

    Example:
        >>> # Create OpenAI client
        >>> client = create_llm_client("openai")
        >>>
        >>> # Create GLM client with custom config
        >>> client = create_llm_client("glm", config={"model": "glm-4v-plus"})
    """
    llm_type = llm_type.lower()

    if llm_type == "openai":
        return OpenAILLM(config)
    elif llm_type == "glm":
        return GlmLLM(config)
    else:
        raise ValueError(
            f"Unsupported LLM type: {llm_type}. "
            f"Supported types: 'openai', 'glm'"
        )
