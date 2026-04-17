"""
Component analysis using the Claude Agent SDK.

This module provides an alternative analysis mode where Claude Code's multi-step
reasoning replaces the single-shot VLM API call. The agent reads image and
summary files via the Read tool, then performs step-by-step analysis using
the same prompt templates as the VLM mode.
"""

import os
import json
import asyncio
import tempfile
from typing import Optional, Tuple
import dotenv

dotenv.load_dotenv()

def _get_settings_file() -> str:
    """Build a temporary settings file with current environment variables."""
    settings_dict: dict = {}
    _env_map = {
        "CLAUDECODE_API_KEY": "ANTHROPIC_API_KEY",
        "CLAUDECODE_BASE_URL": "ANTHROPIC_BASE_URL",
        "CLAUDECODE_AUTH_TOKEN": "ANTHROPIC_AUTH_TOKEN",
    }
    for src, dst in _env_map.items():
        val = os.environ.get(src)
        if val:
            settings_dict[dst] = val

    # If AUTH_TOKEN is missing but API_KEY is present, reuse API_KEY for token
    if "ANTHROPIC_API_KEY" in settings_dict and "ANTHROPIC_AUTH_TOKEN" not in settings_dict:
        settings_dict["ANTHROPIC_AUTH_TOKEN"] = settings_dict["ANTHROPIC_API_KEY"]
    
    settings_file = os.path.join(tempfile.gettempdir(), "galaxy_mcp_agent_settings.json")
    with open(settings_file, "w") as f:
        json.dump({"env": settings_dict, "permissions": {"defaultMode": "bypassPermissions"}}, f)
    return settings_file


def _run_async(coro):
    """Run an async coroutine safely from a sync context.

    Handles both cases:
    - No running event loop: use asyncio.run()
    - Already inside an event loop: run in a separate thread
    """
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


def _get_agent_model() -> str | None:
    """Read the model override from environment variable CLAUDECODE_MODEL."""
    return os.environ.get("CLAUDECODE_MODEL")


async def _query_agent(system_prompt: str, user_prompt: str, session_id: str) -> str:
    """Run Claude Code agent and collect text response."""
    from claude_agent_sdk import (
        query,
        ClaudeAgentOptions,
        AssistantMessage,
        TextBlock,
    )

    model = _get_agent_model()
    settings_file = _get_settings_file()

    options = ClaudeAgentOptions(
        system_prompt=system_prompt,
        allowed_tools=["Read","TodoWrite"],
        permission_mode="bypassPermissions",
        max_turns=10,
        model=model,
        settings=settings_file,
        setting_sources=["user"],
        session_id=session_id,
    )
    text_parts = []
    async for message in query(prompt=user_prompt, options=options):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    text_parts.append(block.text)

    return "\n".join(text_parts)


def run_component_analysis_cc(
    system_prompt: str,
    analysis_prompt: str,
    session_id: str,
) -> tuple[Optional[str], Optional[str]]:
    """
    Run component analysis using the Claude Agent SDK.

    The agent reads the image and summary files via its Read tool, then
    performs multi-step reasoning guided by the same prompt templates used
    in the VLM mode.

    Args:
        system_prompt: The system message (residual analysis expert + component spec).
        analysis_prompt: The user-facing analysis prompt with summary content.
        session_id: UUID string identifying this analysis session.

    Returns:
        (analysis_text, error_message) — one of the two is None.
    """
    try:
        from claude_agent_sdk import query  # noqa: F401 — import check only
    except ImportError:
        return None, (
            "claude-agent-sdk is not installed. "
            "Install with: pip install 'galaxy-morphology-mcp[agent]'"
        )

    try:
        analysis = _run_async(_query_agent(system_prompt, analysis_prompt, session_id))
        if not analysis or not analysis.strip():
            return None, "Agent returned empty analysis"
        return analysis, None
    except Exception as e:
        return None, f"Agent SDK error: {str(e)}"
