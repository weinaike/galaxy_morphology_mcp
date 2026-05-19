"""
Component analysis using the Gemini CLI ACP (Agent Client Protocol).

This module interacts with the Gemini CLI via the standard ACP protocol.
It spawns a 'gemini --acp' process and communicates via JSON-RPC over stdio.
"""

import os
import asyncio
from typing import Optional, List, Any
import dotenv

dotenv.load_dotenv()

ACP_TIMEOUT = 600  # 10 minutes


class MCPClient:
    """
    Implementation of the ACP Client protocol to capture agent responses.
    """
    def __init__(self):
        self.full_response = []

    async def session_update(
        self,
        session_id: str,
        update: Any,
        **kwargs: Any
    ) -> None:
        from acp.schema import AgentMessageChunk, AgentResponseMessage

        if isinstance(update, AgentMessageChunk):
            # content is a ContentBlock (e.g. TextContentBlock)
            content = update.content
            if hasattr(content, 'text'):
                self.full_response.append(content.text)
            elif isinstance(content, dict) and 'text' in content:
                self.full_response.append(content['text'])
        elif isinstance(update, AgentResponseMessage):
            # AgentResponseMessage might also contain content
            if hasattr(update, 'content') and update.content:
                content = update.content
                if hasattr(content, 'text'):
                    self.full_response.append(content.text)
                elif isinstance(content, dict) and 'text' in content:
                    self.full_response.append(content['text'])
        
    def get_text(self) -> str:
        return "".join(self.full_response)

    # Implement other required protocol methods as no-ops
    async def create_terminal(self, *args, **kwargs): pass
    async def ext_method(self, *args, **kwargs): return {}
    async def ext_notification(self, *args, **kwargs): pass
    async def kill_terminal(self, *args, **kwargs): pass
    def on_connect(self, conn): pass
    async def read_text_file(self, *args, **kwargs): return None
    async def release_terminal(self, *args, **kwargs): pass
    async def request_permission(self, *args, **kwargs): return None
    async def terminal_output(self, *args, **kwargs): return None
    async def wait_for_terminal_exit(self, *args, **kwargs): return None
    async def write_text_file(self, *args, **kwargs): pass


async def _shutdown_proc(proc, timeout=5):
    """Gracefully shut down a subprocess: terminate first, kill as fallback."""
    if proc.returncode is not None:
        return
    try:
        proc.terminate()
        await asyncio.wait_for(proc.wait(), timeout=timeout)
    except asyncio.TimeoutError:
        try:
            proc.kill()
            await proc.wait()
        except ProcessLookupError:
            pass
    except ProcessLookupError:
        pass


async def _query_agent(
    system_prompt: str,
    user_prompts: List[str],
    _partial: Optional[dict] = None,
) -> tuple[str, str]:
    """
    Run Gemini CLI in ACP mode, sending each prompt in sequence.

    Returns:
        (analysis_text, session_id) — the accumulated response and the actual session ID.
    """
    import tempfile
    import acp
    from acp.schema import ClientCapabilities, Implementation

    full_system_prompt = system_prompt + "\n\n${read_file_ToolName},${write_file_ToolName}"

    with tempfile.NamedTemporaryFile(mode='w', suffix='.md', delete=False) as tf:
        tf.write(full_system_prompt)
        system_md_path = tf.name

    gemini_cmd = "gemini"
    gemini_args = ["--acp"]

    env = os.environ.copy()
    env["GEMINI_SYSTEM_MD"] = system_md_path
    if 'ACP_GEMINI_MODEL' in env:
        env["GEMINI_MODEL"] = env['ACP_GEMINI_MODEL']
    if 'ACP_GEMINI_API_KEY' in env:
        env["GEMINI_API_KEY"] = env['ACP_GEMINI_API_KEY']
    if 'ACP_GOOGLE_GEMINI_BASE_URL' in env:
        env["GOOGLE_GEMINI_BASE_URL"] = env['ACP_GOOGLE_GEMINI_BASE_URL']

    client_impl = MCPClient()
    actual_session_id: Optional[str] = None
    conn = None
    proc = None

    try:
        async with acp.spawn_agent_process(
            client_impl,
            gemini_cmd,
            *gemini_args,
            env=env,
        ) as (conn, proc):
            # 1. Initialize
            await conn.initialize(
                protocol_version=acp.PROTOCOL_VERSION,
                client_capabilities=ClientCapabilities(roots=True, sampling=True),
                client_info=Implementation(name="galaxy-morphology-mcp", version="0.1.0"),
            )

            # 2. Start session
            session_resp = await conn.new_session(cwd=os.getcwd())
            actual_session_id = session_resp.sessionId
            print(f"[ACP] Session started: {actual_session_id}", flush=True)

            # 3. Process prompts
            for i, prompt_text in enumerate(user_prompts):
                await conn.prompt(
                    session_id=actual_session_id,
                    prompt=[acp.text_block(prompt_text)],
                )

            result_text = client_impl.get_text()

        return result_text, actual_session_id
    except asyncio.CancelledError:
        print("[ACP] Cancelled, shutting down subprocess...", flush=True)
        if conn is not None:
            try:
                await conn.close()
            except Exception:
                pass
        if proc is not None:
            await _shutdown_proc(proc)
        if _partial is not None:
            _partial['text'] = client_impl.get_text()
            _partial['session_id'] = actual_session_id
        raise
    finally:
        if os.path.exists(system_md_path):
            os.remove(system_md_path)


def run_component_analysis_acp(
    system_prompt: str,
    analysis_prompts: List[str],
) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """
    Run component analysis using the Gemini CLI via ACP.

    Args:
        system_prompt: The system message for the agent.
        analysis_prompts: Ordered list of user prompts to send one by one.

    Returns:
        (analysis_text, session_id, error_message) — analysis_text and session_id are
        both None on error; error_message is None on success.
    """
    try:
        import acp  # noqa: F401
    except ImportError:
        return None, None, (
            "agent-client-protocol is not installed. "
            "Install with: pip install agent-client-protocol"
        )

    import concurrent.futures
    session_id_captured: Optional[str] = None
    partial_result: dict = {}
    try:
        coro = _query_agent(system_prompt, analysis_prompts, _partial=partial_result)
        wrapped = asyncio.wait_for(coro, timeout=ACP_TIMEOUT)
        # Run in a dedicated thread so asyncio.run() gets its own event loop,
        # avoiding "cannot be called from a running event loop" when the MCP
        # server itself is already async.
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(asyncio.run, wrapped)
            analysis, session_id = future.result(timeout=ACP_TIMEOUT + 30)
            session_id_captured = session_id
        if not analysis or not analysis.strip():
            return None, session_id_captured, "Gemini CLI returned empty analysis"
        return analysis, session_id_captured, None
    except (concurrent.futures.TimeoutError, asyncio.TimeoutError):
        partial_text = partial_result.get('text')
        partial_sid = partial_result.get('session_id') or session_id_captured
        return partial_text, partial_sid, f"ACP agent query timed out after {ACP_TIMEOUT}s"
    except Exception as e:
        return None, session_id_captured, f"ACP Protocol error: {str(e)}"
