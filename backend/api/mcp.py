"""
MCP管理API

提供MCP服务器管理、工具调用等功能

权限说明
--------
ENABLE_AUTH=false: AnonymousUser is_superadmin=True → 所有端点直接通过（向后兼容）
ENABLE_AUTH=true:
  - GET  /mcp/*                 → 需要 settings:read（admin 及以上角色）
  - POST /mcp/.../tools/...     → 需要 settings:write（admin 及以上角色）
  - POST /mcp/test-connection   → 需要 settings:write
"""
from fastapi import APIRouter, Depends, HTTPException
from typing import Dict, List, Any, Optional
from pydantic import BaseModel

from backend.mcp.manager import get_mcp_manager
from backend.api.deps import require_permission

router = APIRouter(prefix="/mcp", tags=["MCP管理"])


# Request/Response 模型

class ToolCallRequest(BaseModel):
    """工具调用请求"""
    arguments: Dict[str, Any]


class TestConnectionRequest(BaseModel):
    """测试连接请求"""
    server_type: str  # clickhouse, mysql, filesystem, lark
    config: Dict[str, Any]


# API端点

@router.get("/servers")
async def list_servers(
    _user=Depends(require_permission("settings", "read")),
):
    """
    列出所有MCP服务器（需要 settings:read 权限）

    Returns:
        所有已注册的MCP服务器列表
    """
    try:
        manager = get_mcp_manager()
        servers = manager.list_servers()

        return {
            "success": True,
            "data": servers
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取服务器列表失败: {str(e)}")


@router.get("/servers/{server_name}")
async def get_server_info(
    server_name: str,
    _user=Depends(require_permission("settings", "read")),
):
    """
    获取指定MCP服务器的详细信息（需要 settings:read 权限）

    Args:
        server_name: 服务器名称

    Returns:
        服务器详细信息
    """
    try:
        manager = get_mcp_manager()
        server = manager.get_server(server_name)

        if not server:
            raise HTTPException(status_code=404, detail=f"服务器不存在: {server_name}")

        # 获取服务器信息
        info = await server.get_server_info() if hasattr(server, "get_server_info") else {}

        return {
            "success": True,
            "data": {
                "name": server.name,
                "version": server.version,
                "tools": [
                    {
                        "name": tool.name,
                        "description": tool.description,
                        "input_schema": tool.input_schema
                    }
                    for tool in server.list_tools()
                ],
                "resources": [
                    {
                        "uri": res.uri,
                        "name": res.name,
                        "description": res.description,
                        "mime_type": res.mime_type
                    }
                    for res in server.list_resources()
                ],
                "info": info
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取服务器信息失败: {str(e)}")


@router.post("/servers/{server_name}/tools/{tool_name}")
async def call_tool(
    server_name: str,
    tool_name: str,
    request: ToolCallRequest,
    _user=Depends(require_permission("settings", "write")),
):
    """
    调用MCP工具

    Args:
        server_name: 服务器名称
        tool_name: 工具名称
        request: 工具参数

    Returns:
        工具执行结果
    """
    try:
        manager = get_mcp_manager()

        # 调用工具
        result = await manager.call_tool(
            server_name=server_name,
            tool_name=tool_name,
            arguments=request.arguments
        )

        if result is None:
            raise HTTPException(
                status_code=404,
                detail=f"服务器或工具不存在: {server_name}/{tool_name}"
            )

        return {
            "success": result.get("success", True) if isinstance(result, dict) else True,
            "data": result
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"工具调用失败: {str(e)}")


@router.get("/servers/{server_name}/tools")
async def list_tools(
    server_name: str,
    _user=Depends(require_permission("settings", "read")),
):
    """
    列出服务器的所有工具

    Args:
        server_name: 服务器名称

    Returns:
        工具列表
    """
    try:
        manager = get_mcp_manager()
        server = manager.get_server(server_name)

        if not server:
            raise HTTPException(status_code=404, detail=f"服务器不存在: {server_name}")

        tools = [
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.input_schema
            }
            for tool in server.list_tools()
        ]

        return {
            "success": True,
            "data": tools
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取工具列表失败: {str(e)}")


@router.get("/servers/{server_name}/resources")
async def list_resources(
    server_name: str,
    _user=Depends(require_permission("settings", "read")),
):
    """
    列出服务器的所有资源

    Args:
        server_name: 服务器名称

    Returns:
        资源列表
    """
    try:
        manager = get_mcp_manager()
        server = manager.get_server(server_name)

        if not server:
            raise HTTPException(status_code=404, detail=f"服务器不存在: {server_name}")

        resources = [
            {
                "uri": res.uri,
                "name": res.name,
                "description": res.description,
                "mime_type": res.mime_type
            }
            for res in server.list_resources()
        ]

        return {
            "success": True,
            "data": resources
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取资源列表失败: {str(e)}")


@router.post("/test-connection")
async def test_connection(
    request: TestConnectionRequest,
    _user=Depends(require_permission("settings", "write")),
):
    """
    测试MCP连接（用于配置前测试）

    Args:
        request: 包含服务器类型和配置的请求

    Returns:
        连接测试结果
    """
    try:
        # 这里可以临时创建一个MCP服务器实例进行测试
        # 具体实现取决于需求
        return {
            "success": True,
            "message": "连接测试功能待实现"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"连接测试失败: {str(e)}")


# 统计信息端点

@router.get("/stats")
async def get_mcp_stats(
    _user=Depends(require_permission("settings", "read")),
):
    """
    获取MCP使用统计信息

    Returns:
        统计信息
    """
    try:
        manager = get_mcp_manager()
        servers = manager.list_servers()

        total_tools = 0
        total_resources = 0

        for server_info in servers:
            total_tools += server_info.get("tool_count", 0)
            total_resources += server_info.get("resource_count", 0)

        return {
            "success": True,
            "data": {
                "total_servers": len(servers),
                "total_tools": total_tools,
                "total_resources": total_resources,
                "servers": servers
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取统计信息失败: {str(e)}")
