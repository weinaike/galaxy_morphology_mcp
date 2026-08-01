"""
OpenAI SDK analysis backend with multi-turn conversation support.

Uses the OpenAI chat completions API (compatible with Gemini and other
models via base_url). Sends focused prompts sequentially, maintaining
conversation history across turns.
"""

import os
import time
import base64
import asyncio
import uuid
from typing import Optional
import dotenv

dotenv.load_dotenv()

API_TIMEOUT = 600  # 10 minutes total


def _get_config() -> tuple[str, str, Optional[str]]:
    """Read API configuration from OPENAI_* environment variables."""
    api_key = os.environ.get("OPENAI_API_KEY", "")
    model = os.environ.get("OPENAI_MODEL", "gpt-4o")
    base_url = os.environ.get("OPENAI_BASE_URL")
    return api_key, model, base_url


def _run_async(coro):
    """Run an async coroutine safely from a potentially async MCP server context."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as pool:
            return pool.submit(asyncio.run, coro).result()
    else:
        return asyncio.run(coro)


def _image_content_block(image_path: str) -> dict:
    """One OpenAI-vision image_url content block, base64-encoded from a file."""
    ext = os.path.splitext(image_path)[1].lower()
    mime_map = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}
    mime_type = mime_map.get(ext, "image/png")
    with open(image_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("utf-8")
    return {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{b64}"}}


def _build_image_message(text: str, image_path: str) -> dict:
    """Build a user message with inline image + text (OpenAI vision format)."""
    return {
        "role": "user",
        "content": [
            _image_content_block(image_path),
            {"type": "text", "text": text},
        ],
    }


def _build_interleaved_image_message(text: str, image_path: str,
                                     reference_blocks: list[dict] | None = None,
                                     reference_intro: str | None = None) -> dict:
    """Turn-1 message: optional reference Few-shot images + captions, then the
    target image, then the prompt text — linearly interleaved (one image per
    feature), matching the visual-RAG design doc.

    Each reference block is ``{"image": <path>, "caption": <str>}``. With no
    reference blocks this is identical to :func:`_build_image_message`.
    """
    content: list[dict] = []
    if reference_blocks:
        if reference_intro:
            content.append({"type": "text", "text": reference_intro})
        for blk in reference_blocks:
            content.append(_image_content_block(blk["image"]))
            cap = blk.get("caption")
            if cap:
                content.append({"type": "text", "text": cap})
    content.append(_image_content_block(image_path))
    content.append({"type": "text", "text": text})
    return {"role": "user", "content": content}


class _UsageAccumulator:
    """Accumulates token usage across multiple API calls."""

    def __init__(self):
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.turns = 0

    def add(self, response):
        if response.usage:
            self.prompt_tokens += response.usage.prompt_tokens or 0
            self.completion_tokens += response.usage.completion_tokens or 0
            self.turns += 1

    @property
    def total_tokens(self):
        return self.prompt_tokens + self.completion_tokens

    def summary(self) -> str:
        if self.turns == 0:
            return ""
        return f"[Token usage] prompts={self.prompt_tokens}, completions={self.completion_tokens}, total={self.total_tokens} ({self.turns} turns)"


async def _call_with_retry(client, model: str, messages: list[dict], usage: _UsageAccumulator, max_retries: int = 3) -> str:
    """Call chat completions with retry on transient failures."""
    last_error = None
    for attempt in range(1, max_retries + 1):
        try:
            response = await asyncio.to_thread(
                client.chat.completions.create,
                model=model,
                messages=messages,
                max_tokens=16384,
                temperature=0.3,
            )
            usage.add(response)
            return response.choices[0].message.content
        except Exception as e:
            last_error = e
            if attempt < max_retries:
                wait = attempt * 10
                print(f"API call failed (attempt {attempt}/{max_retries}): {e}. Retrying in {wait}s...")
                await asyncio.sleep(wait)
    raise last_error


async def _query(
    system_prompt: str,
    analysis_prompts: list[str],
    image_path: str,
    deferred_system: bool = False,
    reference_blocks: list[dict] | None = None,
    reference_intro: str | None = None,
) -> tuple[str, list[dict]]:
    """Run analysis via OpenAI SDK — single or multi-turn depending on prompt count.

    Args:
        deferred_system: When False (default), system_prompt is set from the
            first turn and all history is shared across turns (Mode 1).
            When True, turn 1 runs with image + turn1 only (no system_prompt);
            from turn 2 onward, system_prompt is injected and previous turn
            results are carried as assistant messages (Mode 2).
        reference_blocks: optional Few-shot reference images + captions prepended
            to the turn-1 message (target image stays last). None = legacy
            single-image turn-1.
    """
    from openai import OpenAI

    api_key, model, base_url = _get_config()

    client_kwargs: dict = {"api_key": api_key, "timeout": 360.0}
    if base_url:
        client_kwargs["base_url"] = base_url

    client = OpenAI(**client_kwargs)

    text_parts: list[str] = []
    usage = _UsageAccumulator()
    turn_records: list[dict] = []
    messages: list[dict] = []
    for i, prompt_text in enumerate(analysis_prompts):
        if not deferred_system:
            # Mode 1: system_prompt always present, full history accumulates
            if i == 0:
                messages: list[dict] = [{"role": "system", "content": system_prompt}]
            messages.append(
                (_build_interleaved_image_message(prompt_text, image_path,
                                                  reference_blocks, reference_intro)
                 if i == 0 and reference_blocks else
                 _build_image_message(prompt_text, image_path) if i == 0
                 else {"role": "user", "content": prompt_text})
            )
        else:
            # Mode 2: turn 1 — no system, image + prompt only
            if i == 0:
                messages = [(_build_interleaved_image_message(prompt_text, image_path,
                                                              reference_blocks, reference_intro)
                             if reference_blocks else
                             _build_image_message(prompt_text, image_path))]
            else:
                # turn 2+: inject system_prompt, carry prior results, then new prompt
                messages = [{"role": "system", "content": system_prompt}]
                for prev in text_parts:
                    messages.append({"role": "assistant", "content": prev})
                messages.append({"role": "user", "content": prompt_text})

        pre_p, pre_c = usage.prompt_tokens, usage.completion_tokens
        turn_start = time.perf_counter()
        assistant_text = await _call_with_retry(client, model, messages, usage)
        turn_dur = time.perf_counter() - turn_start

        inc_p = usage.prompt_tokens - pre_p
        inc_c = usage.completion_tokens - pre_c
        tok_per_s = (inc_c / turn_dur) if (turn_dur > 0 and inc_c > 0) else 0.0
        turn_records.append({
            "turn": i + 1,
            "duration_s": round(turn_dur, 1),
            "prompt_tokens": inc_p,
            "completion_tokens": inc_c,
            "tok_per_s": round(tok_per_s, 0),
        })

        if not assistant_text:
            return ("\n\n".join(text_parts) if text_parts else ""), turn_records
        text_parts.append(assistant_text)

        if not deferred_system:
            messages.append({"role": "assistant", "content": assistant_text})
        print(
            f"Turn {i+1} completed in {turn_dur:.1f}s "
            f"(prompt+{inc_p}, completion+{inc_c}, "
            f"{tok_per_s:.0f} tok/s). {usage.summary()}"
        )
    print(f"Analysis completed. Total {usage.summary()}")
    return "\n\n".join(text_parts), turn_records


def run_openai_analysis(
    system_prompt: str,
    analysis_prompts: list[str],
    image_path: str,
    deferred_system: bool = False,
    reference_blocks: Optional[list[dict]] = None,
    reference_intro: Optional[str] = None,
) -> tuple[Optional[str], Optional[str], Optional[str], Optional[dict]]:
    """
    Run component analysis using the OpenAI SDK.

    Single-turn when *analysis_prompts* has 1 item; multi-turn when >1.
    The first turn always includes the image. Conversation history is
    maintained across turns.

    Args:
        system_prompt: System message (residual analysis expert + component spec).
        analysis_prompts: Ordered list of user prompts (1 = single-turn, >1 = multi-turn).
        image_path: Path to the combined residual image file (the analysis target).
        deferred_system: If True, turn 1 runs without system_prompt; system_prompt
            is injected from turn 2 onward along with prior turn results.
        reference_blocks: Optional Few-shot reference images + captions prepended
            to the turn-1 message (each is {"image": path, "caption": str}). The
            target image remains the last image. None keeps the legacy single-image turn.
        reference_intro: Optional explanatory text placed before the reference images.

    Returns:
        (analysis_text, session_id, error_message)
    """
    try:
        from openai import OpenAI  # noqa: F401
    except ImportError:
        return None, None, "openai is not installed. Install with: pip install openai", None

    api_key, _, _ = _get_config()
    if not api_key:
        return None, None, (
            "OPENAI_API_KEY is not set. "
            "Set it in your .env file or environment."
        ), None

    session_id = str(uuid.uuid4())

    try:
        coro = _query(system_prompt, analysis_prompts, image_path,
                      deferred_system=deferred_system,
                      reference_blocks=reference_blocks,
                      reference_intro=reference_intro)
        wrapped = asyncio.wait_for(coro, timeout=API_TIMEOUT)
        wall_start = time.perf_counter()
        analysis, turn_records = _run_async(wrapped)
        wall_time = round(time.perf_counter() - wall_start, 1)
        print(f"[Timing] analyze wall time {wall_time}s")
        timing = {"wall_time_s": wall_time, "turns": turn_records}

        if not analysis or not analysis.strip():
            return None, session_id, "OpenAI API returned empty analysis", timing
        return analysis, session_id, None, timing

    except asyncio.TimeoutError:
        return None, session_id, f"OpenAI API query timed out after {API_TIMEOUT}s", None
    except Exception as e:
        return None, session_id, f"OpenAI API error: {str(e)}", None
