"""
MCP Tool Formatter

Converts MCP server tools to Claude API tools format for use in tool_use mode.

Naming convention: {server_name_underscored}__{tool_name}
  e.g., clickhouse_idn__query, filesystem__read_file
"""
from typing import List, Dict, Any, Tuple

from backend.mcp.manager import MCPServerManager


def format_mcp_tools_for_claude(mcp_manager: MCPServerManager) -> List[Dict[str, Any]]:
    """
    Format all registered MCP server tools into Claude API tools format.

    Args:
        mcp_manager: The MCP server manager instance.

    Returns:
        List of Claude-format tool dicts, each with keys:
          name, description, input_schema
    """
    claude_tools: List[Dict[str, Any]] = []

    for server_name, server in mcp_manager.servers.items():
        server_prefix = server_name.replace("-", "_")

        for tool_name, mcp_tool in server.tools.items():
            namespaced_name = f"{server_prefix}__{tool_name}"

            claude_tool: Dict[str, Any] = {
                "name": namespaced_name,
                "description": f"[{server_name}] {mcp_tool.description}",
                "input_schema": mcp_tool.input_schema or {
                    "type": "object",
                    "properties": {},
                    "required": []
                }
            }
            claude_tools.append(claude_tool)

    return claude_tools


def parse_tool_name(namespaced_name: str) -> Tuple[str, str]:
    """
    Parse a namespaced tool name back to (server_name, tool_name).

    e.g., "clickhouse_idn__query" -> ("clickhouse-idn", "query")

    Returns:
        (server_name, tool_name). server_name is "" if parsing fails.
    """
    if "__" not in namespaced_name:
        return "", namespaced_name

    prefix, tool_name = namespaced_name.split("__", 1)
    server_name = prefix.replace("_", "-")
    return server_name, tool_name
