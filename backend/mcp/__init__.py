"""
MCP (Model Context Protocol) 服务器

实现多种数据源的MCP服务器,支持模型与外部数据的交互
"""
from backend.mcp.base import BaseMCPServer
from backend.mcp.clickhouse import ClickHouseMCPServer
from backend.mcp.mysql import MySQLMCPServer
from backend.mcp.filesystem import FilesystemMCPServer
from backend.mcp.lark import LarkMCPServer
from backend.mcp.manager import (
    MCPServerManager,
    get_mcp_manager,
    initialize_mcp_servers,
    get_clickhouse_server,
    get_mysql_server,
    get_filesystem_server,
    get_lark_server,
)

__all__ = [
    # Base
    "BaseMCPServer",

    # Servers
    "ClickHouseMCPServer",
    "MySQLMCPServer",
    "FilesystemMCPServer",
    "LarkMCPServer",

    # Manager
    "MCPServerManager",
    "get_mcp_manager",
    "initialize_mcp_servers",
    "get_clickhouse_server",
    "get_mysql_server",
    "get_filesystem_server",
    "get_lark_server",
]
