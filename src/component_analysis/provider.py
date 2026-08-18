"""OpenAI-compatible VLM callback for the component-analysis shadow path."""

from __future__ import annotations

import base64
import mimetypes
import os
from pathlib import Path
from typing import Any, Callable

from dotenv import load_dotenv
from openai import OpenAI


class OpenAICompatibleVLM:
    """Call an OpenAI-compatible vision endpoint with strict JSON mode."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        timeout: float = 360.0,
        client: Any | None = None,
        client_factory: Callable[..., Any] = OpenAI,
    ) -> None:
        load_dotenv()
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        self.base_url = base_url or os.getenv("OPENAI_BASE_URL")
        self.model_id = model or os.getenv("OPENAI_MODEL", "gpt-4o")
        if not self.api_key and client is None:
            raise ValueError(
                "OPENAI_API_KEY is required for the shadow VLM callback"
            )
        if client is not None:
            self.client = client
        else:
            client_kwargs: dict[str, Any] = {
                "api_key": self.api_key,
                "timeout": timeout,
            }
            if self.base_url:
                client_kwargs["base_url"] = self.base_url
            self.client = client_factory(**client_kwargs)

    def __call__(self, comparison_png: str, prompt: str) -> str:
        image_path = Path(comparison_png).expanduser().resolve()
        if not image_path.is_file():
            raise FileNotFoundError(f"comparison_png does not exist: {image_path}")
        mime_type, _ = mimetypes.guess_type(image_path.name)
        if mime_type not in {"image/png", "image/jpeg", "image/webp"}:
            raise ValueError(f"unsupported comparison image type: {image_path.suffix}")
        encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
        try:
            response = self.client.chat.completions.create(
                model=self.model_id,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:{mime_type};base64,{encoded}"
                                },
                            },
                        ],
                    }
                ],
                temperature=0,
                max_tokens=4096,
                response_format={"type": "json_object"},
            )
        except Exception as exc:
            # Keep provider-specific SDK errors out of the shadow runner API.
            error_name = type(exc).__name__
            if error_name in {"APITimeoutError", "Timeout"}:
                raise TimeoutError("VLM provider request timed out") from exc
            if error_name in {
                "AuthenticationError",
                "PermissionDeniedError",
                "NotFoundError",
            }:
                raise PermissionError("VLM provider request was refused") from exc
            raise
        try:
            content = response.choices[0].message.content
        except (AttributeError, IndexError, KeyError) as exc:
            raise ValueError("VLM provider returned no message content") from exc
        if not isinstance(content, str) or not content.strip():
            raise ValueError("VLM provider returned an empty message")
        return content
