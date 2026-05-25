"""
Standalone DB MCP Server

独立启动的数据库 MCP 服务器，实现 MCP stdio 协议（JSON-RPC 2.0）。
支持所有已配置的 ClickHouse + MySQL 环境，无需启动完整后端。

用法：
    python -m backend.mcp.standalone_db_server [--env-file PATH]

Claude Code 全局配置示例（~/.claude/settings.json）：
    {
        "mcpServers": {
            "data-agent-db": {
                "command": "d:/ProgramData/Anaconda3/envs/dataagent/python.exe",
                "args": ["-m", "backend.mcp.standalone_db_server"],
                "cwd": "c:/Users/shiguangping/data-agent",
                "env": {}
            }
        }
    }
"""
import sys
import os
import json
import asyncio
import argparse
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

# ── 将 data-agent 根目录加入 sys.path，使 backend.* 可正常导入 ──────────────
_HERE = Path(__file__).resolve()
_PROJECT_ROOT = _HERE.parent.parent.parent  # backend/mcp/standalone_db_server.py → data-agent/
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("standalone_db_server")


# ── MCP stdio 协议：JSON-RPC 2.0 ──────────────────────────────────────────────

SERVER_INFO = {"name": "data-agent-db", "version": "1.0.0"}
PROTOCOL_VERSION = "2024-11-05"


def _send(obj: dict):
    """向 stdout 写一行 JSON（MCP stdio 协议）"""
    line = json.dumps(obj, ensure_ascii=False)
    sys.stdout.write(line + "\n")
    sys.stdout.flush()


def _ok(req_id: Any, result: Any):
    _send({"jsonrpc": "2.0", "id": req_id, "result": result})


def _err(req_id: Any, code: int, message: str):
    _send({"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}})


# ── 工具注册表 ─────────────────────────────────────────────────────────────────

class _ToolRegistry:
    def __init__(self):
        # mcp_tool_name → (server_instance, raw_tool_name)
        self._tools: Dict[str, tuple] = {}
        self._schemas: Dict[str, dict] = {}

    def register_server(self, server_name: str, server):
        """从 BaseMCPServer 实例批量注册工具"""
        # server_prefix：连字符 → 下划线（与 tool_formatter 一致）
        prefix = server_name.replace("-", "_")
        for tool in server.get_tools_list():
            raw_name = tool["name"]
            mcp_name = f"{prefix}__{raw_name}"
            self._tools[mcp_name] = (server, raw_name)
            self._schemas[mcp_name] = {
                "name": mcp_name,
                "description": f"[{server_name}] {tool.get('description', '')}",
                "inputSchema": tool.get("input_schema", {"type": "object", "properties": {}}),
            }

    def list_tools(self) -> List[dict]:
        return list(self._schemas.values())

    async def call(self, mcp_name: str, arguments: dict) -> dict:
        if mcp_name not in self._tools:
            return {"content": [{"type": "text", "text": f"Unknown tool: {mcp_name}"}], "isError": True}
        server, raw_name = self._tools[mcp_name]
        try:
            response = await server.call_tool(raw_name, arguments)
            text = json.dumps(response.data if response.success else {"error": response.error},
                              ensure_ascii=False, default=str)
            return {"content": [{"type": "text", "text": text}], "isError": not response.success}
        except Exception as e:
            return {"content": [{"type": "text", "text": f"Tool error: {e}"}], "isError": True}


# ── 服务器初始化 ───────────────────────────────────────────────────────────────

async def _build_registry(env_file: Optional[str]) -> _ToolRegistry:
    """初始化所有 DB MCP 服务器并注册工具"""
    if env_file:
        try:
            from dotenv import load_dotenv
            load_dotenv(env_file, override=False)
        except ImportError:
            pass

    # 延迟导入（确保 sys.path 和 dotenv 已就绪）
    from backend.config.settings import settings
    from backend.mcp.clickhouse import ClickHouseMCPServer
    from backend.mcp.mysql import MySQLMCPServer

    registry = _ToolRegistry()

    # ClickHouse
    if settings.enable_mcp_clickhouse:
        for env in settings.get_all_clickhouse_envs():
            cfg = settings.get_clickhouse_config(env, "admin")
            if not cfg.get("host"):
                continue
            server_env = env.replace("_", "-")
            server_name = f"clickhouse-{server_env}"
            try:
                srv = ClickHouseMCPServer(env=env, level="admin")
                await srv.initialize()
                registry.register_server(server_name, srv)
                logger.warning("[standalone] registered %s", server_name)
            except Exception as e:
                logger.warning("[standalone] skip %s: %s", server_name, e)

            if settings.has_readonly_credentials(env):
                try:
                    srv_ro = ClickHouseMCPServer(env=env, level="readonly")
                    await srv_ro.initialize()
                    registry.register_server(f"{server_name}-ro", srv_ro)
                    logger.warning("[standalone] registered %s-ro", server_name)
                except Exception as e:
                    logger.warning("[standalone] skip %s-ro: %s", server_name, e)

    # MySQL
    if settings.enable_mcp_mysql:
        for env in settings.get_all_mysql_envs():
            cfg = settings.get_mysql_config(env)
            if not cfg.get("host"):
                continue
            server_name = f"mysql-{env}"
            try:
                srv = MySQLMCPServer(env=env)
                await srv.initialize()
                registry.register_server(server_name, srv)
                logger.warning("[standalone] registered %s", server_name)
            except Exception as e:
                logger.warning("[standalone] skip %s: %s", server_name, e)

    return registry


# ── MCP 主事件循环 ─────────────────────────────────────────────────────────────

async def _run(env_file: Optional[str]):
    registry = await _build_registry(env_file)
    initialized = False

    for raw_line in sys.stdin:
        raw_line = raw_line.strip()
        if not raw_line:
            continue
        try:
            msg = json.loads(raw_line)
        except json.JSONDecodeError:
            continue

        method = msg.get("method", "")
        req_id = msg.get("id")  # notifications 没有 id
        params = msg.get("params", {})

        if method == "initialize":
            _ok(req_id, {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": SERVER_INFO,
            })

        elif method == "notifications/initialized":
            initialized = True  # 通知，无需回复

        elif method == "tools/list":
            _ok(req_id, {"tools": registry.list_tools()})

        elif method == "tools/call":
            tool_name = params.get("name", "")
            arguments = params.get("arguments", {})
            result = await registry.call(tool_name, arguments)
            _ok(req_id, result)

        elif method == "ping":
            _ok(req_id, {})

        else:
            if req_id is not None:
                _err(req_id, -32601, f"Method not found: {method}")


def main():
    parser = argparse.ArgumentParser(description="data-agent Standalone DB MCP Server")
    parser.add_argument(
        "--env-file",
        default=None,
        help="Path to .env file (default: auto-discover from data-agent root)",
    )
    args = parser.parse_args()

    # 将 stdout 切换为 utf-8（Windows 默认 gbk 会导致中文乱码）
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    asyncio.run(_run(args.env_file))


if __name__ == "__main__":
    main()
