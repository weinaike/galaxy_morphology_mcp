"""
Component analysis using the Claude Agent SDK.

This module provides multi-turn analysis where a list of questions is sent
to the agent sequentially within a single session.  The agent retains full
context across all turns.
"""

import os
import json
import asyncio
import tempfile
from typing import Optional
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


async def _query_agent(
    system_prompt: str,
    user_prompts: list[str],
    session_id: str,
) -> str:
    """Run Claude Code agent, sending each prompt in sequence."""
    from claude_agent_sdk import (
        ClaudeAgentOptions,
        AssistantMessage,
        TextBlock,
        ClaudeSDKClient,
    )

    model = _get_agent_model()
    settings_file = _get_settings_file()

    options = ClaudeAgentOptions(
        system_prompt=system_prompt,
        allowed_tools=["Read", "TodoWrite"],
        permission_mode="bypassPermissions",
        max_turns=10,
        model=model,
        settings=settings_file,
        setting_sources=["user"],
        session_id=session_id,
    )

    text_parts: list[str] = []
    async with ClaudeSDKClient(options=options) as client:
        for user_prompt in user_prompts:
            await client.query(prompt=user_prompt)
            async for message in client.receive_response():
                if isinstance(message, AssistantMessage):
                    for block in message.content:
                        if isinstance(block, TextBlock):
                            text_parts.append(block.text)

    return "\n".join(text_parts)


def run_component_analysis_cc(
    system_prompt: str,
    analysis_prompts: list[str],
    session_id: str,
) -> tuple[Optional[str], Optional[str]]:
    """
    Run component analysis using the Claude Agent SDK.

    The agent reads image and summary files via its Read tool, then
    performs multi-step reasoning.  Each prompt in *analysis_prompts* is
    sent sequentially within the same session, so the agent retains full
    context across turns.

    Args:
        system_prompt: The system message (residual analysis expert + component spec).
        analysis_prompts: Ordered list of user prompts to send one by one.
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
        analysis = _run_async(_query_agent(system_prompt, analysis_prompts, session_id))
        if not analysis or not analysis.strip():
            return None, "Agent returned empty analysis"
        return analysis, None
    except Exception as e:
        return None, f"Agent SDK error: {str(e)}"
