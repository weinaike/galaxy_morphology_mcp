#!/usr/bin/env python3
"""
独立的MCP服务端启动器
支持 stdio 和 streamable-http 两种传输模式
"""

import sys
import os
import logging
import argparse
import shutil
import shlex
import importlib.util
from typing import Any
from mcp.server import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from tools.modify_feedme import add_components
from tools.run_galfit import run_galfit
from tools.run_galfits import run_galfits
from tools.analyze_image import galfit_analyze_by_vllm, galfits_analyze_by_vllm
from starlette.responses import Response, JSONResponse
from dotenv import load_dotenv

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = FastMCP(name='galaxy-morphology-mcp')
app.add_tool(add_components)
app.add_tool(run_galfit)
app.add_tool(run_galfits)
app.add_tool(galfit_analyze_by_vllm)
app.add_tool(galfits_analyze_by_vllm)


def _galfit_readiness() -> tuple[str, str | None, bool]:
    """Return (configured, resolved_path, is_executable)."""
    configured = os.getenv("GALFIT_BIN", "galfit")

    if os.path.isabs(configured):
        resolved = configured if os.path.exists(configured) else None
    else:
        resolved = shutil.which(configured)

    is_executable = bool(resolved and os.access(resolved, os.X_OK))
    return configured, resolved, is_executable


def _galfits_readiness() -> dict[str, Any]:
    """Return readiness details for GalfitS.

    GalfitS is commonly installed via shell alias. We therefore consider it usable if:
    - GALFITS_BIN is set to an executable, a .py script, or a command string, OR
    - Python module galfits.galfitS is importable, OR
    - an executable named `galfits` is found on PATH.

    Additionally, GS_DATA_PATH must point to an existing directory for GalfitS to work.
    """

    configured = os.getenv("GALFITS_BIN")
    configured_parts = shlex.split(configured) if configured else []

    resolved: str | None = None
    ok_bin = False

    if configured_parts:
        first = configured_parts[0]

        # If user points directly to a python script
        if len(configured_parts) == 1 and first.endswith(".py"):
            resolved = first if os.path.exists(first) else None
            ok_bin = bool(resolved)
        else:
            # If first token is an absolute path, check it exists/executable.
            if os.path.isabs(first):
                resolved = first if os.path.exists(first) else None
                ok_bin = bool(resolved and os.access(resolved, os.X_OK))
            else:
                # Otherwise, see if command exists on PATH.
                resolved = shutil.which(first)
                ok_bin = bool(resolved)

    # Don't let missing heavy deps (e.g. jax) crash the MCP server.
    try:
        module_ok = importlib.util.find_spec("galfits.galfitS") is not None
    except Exception:
        module_ok = False
    path_ok = shutil.which("galfits") is not None

    gs_data_path = os.getenv("GS_DATA_PATH")
    gs_data_ok = bool(gs_data_path and os.path.isdir(gs_data_path))

    # Minimal usability: have some way to run + data path present
    ok = bool((ok_bin or module_ok or path_ok) and gs_data_ok)

    return {
        "configured": configured,
        "resolved": resolved,
        "module_importable": module_ok,
        "on_path": path_ok,
        "gs_data_path": gs_data_path,
        "gs_data_exists": gs_data_ok,
        "usable": ok,
    }

def setup_http_routes():
    """配置 HTTP 路由 (仅用于 streamable-http 模式)"""

    @app.custom_route("/health", methods=["GET"])
    async def health_check(request) -> Response:
        """健康检查端点"""
        configured, resolved, ok = _galfit_readiness()
        galfits = _galfits_readiness()

        overall_ok = bool(ok or galfits.get("usable"))

        payload = {
            "status": "healthy" if overall_ok else "degraded",
            "service": "Galaxy Morphology MCP Server",
            "timestamp": __import__('datetime').datetime.now().isoformat(),
            "galfit": {
                "configured": configured,
                "resolved": resolved,
                "executable": ok,
            },
            "galfits": galfits,
        }

        errors: list[str] = []
        if not ok:
            errors.append(
                "GALFIT not available. Install galfit or set GALFIT_BIN to an executable path."
            )
        if not galfits.get("usable"):
            if not galfits.get("gs_data_exists"):
                errors.append(
                    "GalfitS not usable: GS_DATA_PATH is missing or not a directory."
                )
            else:
                errors.append(
                    "GalfitS not usable: set GALFITS_BIN (or install GalfitS as a python module, or ensure `galfits` is on PATH)."
                )

        if errors:
            payload["errors"] = errors

        # Fail hard only if neither GALFIT nor GalfitS is usable.
        if not overall_ok:
            return JSONResponse(payload, status_code=503)

        return JSONResponse(payload)

    @app.custom_route("/api/tools", methods=["GET"])
    async def list_available_tools(request) -> Response:
        """列出所有可用的工具"""
        tools = await app.list_tools()
        return JSONResponse({
            "tools": [
                {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.inputSchema
                }
                for tool in tools
            ]
        })


def run_stdio_mode():
    """运行 stdio 模式 (用于本地 MCP 客户端连接)"""
    logger.info("Starting Galaxy Morphology MCP Server in STDIO mode...")
    
    app.run(transport='stdio')


def run_http_mode(host='0.0.0.0', port=38507, path='/mcp'):
    """运行 HTTP 模式 (用于远程/网络访问)"""
    setup_http_routes()

    app.settings.host = host
    app.settings.port = port
    app.settings.streamable_http_path = path

    # Configure transport security to allow all hosts for remote access
    # Get allowed hosts from environment variable or allow all
    allowed_hosts_env = os.getenv("MCP_ALLOWED_HOSTS", "*").split(",")
    if allowed_hosts_env == ["*"]:
        # Disable DNS rebinding protection to allow all hosts
        app.settings.transport_security = TransportSecuritySettings(
            enable_dns_rebinding_protection=False,
            allowed_hosts=[],
            allowed_origins=[]
        )
        logger.info("DNS rebinding protection disabled - allowing all hosts")
    else:
        # Allow specific hosts from environment variable
        app.settings.transport_security = TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=[h.strip() for h in allowed_hosts_env],
            allowed_origins=[]
        )
        logger.info(f"Allowed hosts: {app.settings.transport_security.allowed_hosts}")

    logger.info("Starting Galaxy Morphology MCP Server in HTTP mode...")
    logger.info(f"Server endpoints:")
    logger.info(f"  - MCP Protocol: http://{host}:{port}{path}")
    logger.info(f"  - Health Check: http://{host}:{port}/health")
    logger.info(f"  - API Tools:    http://{host}:{port}/api/tools")

    app.run(transport='streamable-http')


def main(argv: list[str] | None = None) -> None:
    # 设置当前目录到 sys.path，确保 tools/ 可被导入（脚本方式/console_script 都适用）
    current_dir = os.path.dirname(os.path.abspath(__file__))
    if current_dir not in sys.path:
        sys.path.insert(0, current_dir)

    load_dotenv()

    configured, resolved, ok = _galfit_readiness()
    if ok:
        logger.info(f"GALFIT available: configured={configured!r}, resolved={resolved!r}")
    else:
        logger.warning(
            f"GALFIT not available: configured={configured!r}, resolved={resolved!r}. "
            "Tools that invoke GALFIT will fail until GALFIT_BIN points to an executable."
        )

    galfits = _galfits_readiness()
    if galfits.get("usable"):
        logger.info(
            "GalfitS available: "
            f"configured={galfits.get('configured')!r}, resolved={galfits.get('resolved')!r}, "
            f"gs_data_path={galfits.get('gs_data_path')!r}"
        )
    else:
        logger.warning(
            "GalfitS not usable: "
            f"configured={galfits.get('configured')!r}, resolved={galfits.get('resolved')!r}, "
            f"module_importable={galfits.get('module_importable')}, on_path={galfits.get('on_path')}, "
            f"gs_data_path={galfits.get('gs_data_path')!r}, gs_data_exists={galfits.get('gs_data_exists')}"
        )

    parser = argparse.ArgumentParser(
        description='Galaxy Morphology MCP Server',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
Examples:
  # stdio 模式 (本地 MCP 客户端)
  python mcp_server.py --transport stdio

  # HTTP 模式 (远程访问)
  python mcp_server.py --transport http --port 8080

  # HTTP 模式，自定义路径
  python mcp_server.py --transport http --host 127.0.0.1 --port 9000 --path /mcp-endpoint
        '''
    )

    parser.add_argument(
        '--transport', '-t',
        choices=['stdio', 'http'],
        default='stdio',
        help='传输模式: stdio (本地) 或 http (远程网络访问)，默认: stdio'
    )

    parser.add_argument(
        '--host', '-H',
        default='0.0.0.0',
        help='HTTP 模式监听地址，默认: 0.0.0.0'
    )

    parser.add_argument(
        '--port', '-p',
        type=int,
        default=38507,
        help='HTTP 模式监听端口，默认: 38507'
    )

    parser.add_argument(
        '--path', '-P',
        default='/mcp',
        help='HTTP 模式 MCP 协议路径，默认: /mcp'
    )

    args = parser.parse_args(argv)

    if args.transport == 'stdio':
        run_stdio_mode()
    else:
        run_http_mode(host=args.host, port=args.port, path=args.path)


if __name__ == "__main__":
    main()
