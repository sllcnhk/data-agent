"""
MCP基础框架

定义MCP服务器的通用接口和工具
"""
from abc import ABC, abstractmethod
from typing import Dict, List, Any, Optional, Callable
from dataclasses import dataclass, field
from datetime import datetime
import json
import asyncio
import traceback
from enum import Enum


class MCPToolType(str, Enum):
    """MCP工具类型"""
    FUNCTION = "function"
    PROMPT = "prompt"
    RESOURCE = "resource"


@dataclass
class MCPResource:
    """MCP资源"""
    uri: str
    name: str
    description: str
    mime_type: str = "text/plain"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "uri": self.uri,
            "name": self.name,
            "description": self.description,
            "mimeType": self.mime_type
        }


@dataclass
class MCPTool:
    """MCP工具"""
    name: str
    description: str
    input_schema: Dict[str, Any]
    type: MCPToolType = MCPToolType.FUNCTION
    callback: Optional[Callable] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema,
            "type": self.type.value
        }


@dataclass
class MCPPrompt:
    """MCP提示"""
    name: str
    description: str
    arguments: List[Dict[str, Any]] = field(default_factory=list)
    template: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "arguments": self.arguments,
            "template": self.template
        }


@dataclass
class MCPResponse:
    """MCP响应"""
    success: bool
    data: Any = None
    error: Optional[str] = None
    traceback: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "data": self.data,
            "error": self.error,
            "traceback": self.traceback
        }


class BaseMCPServer(ABC):
    """MCP服务器基类"""

    def __init__(self, name: str, version: str = "1.0.0"):
        self.name = name
        self.version = version
        self.tools: Dict[str, MCPTool] = {}
        self.resources: Dict[str, MCPResource] = {}
        self.prompts: Dict[str, MCPPrompt] = {}

    @abstractmethod
    async def initialize(self):
        """初始化服务器"""
        pass

    def register_tool(
        self,
        name: str,
        description: str,
        input_schema: Dict[str, Any],
        callback: Callable,
        tool_type: MCPToolType = MCPToolType.FUNCTION
    ):
        """注册工具"""
        tool = MCPTool(
            name=name,
            description=description,
            input_schema=input_schema,
            callback=callback,
            type=tool_type
        )
        self.tools[name] = tool

    def register_resource(
        self,
        uri: str,
        name: str,
        description: str,
        mime_type: str = "text/plain"
    ):
        """注册资源"""
        resource = MCPResource(
            uri=uri,
            name=name,
            description=description,
            mime_type=mime_type
        )
        self.resources[uri] = resource

    def register_prompt(
        self,
        name: str,
        description: str,
        arguments: List[Dict[str, Any]] = None,
        template: str = None
    ):
        """注册提示"""
        prompt = MCPPrompt(
            name=name,
            description=description,
            arguments=arguments or [],
            template=template
        )
        self.prompts[name] = prompt

    async def call_tool(self, name: str, arguments: Dict[str, Any]) -> MCPResponse:
        """调用工具"""
        if name not in self.tools:
            return MCPResponse(
                success=False,
                error=f"工具 {name} 未找到"
            )

        tool = self.tools[name]

        try:
            if asyncio.iscoroutinefunction(tool.callback):
                result = await tool.callback(**arguments)
            else:
                result = tool.callback(**arguments)

            return MCPResponse(
                success=True,
                data=result
            )
        except Exception as e:
            return MCPResponse(
                success=False,
                error=str(e),
                traceback=traceback.format_exc()
            )

    def get_tools_list(self) -> List[Dict[str, Any]]:
        """获取工具列表"""
        return [tool.to_dict() for tool in self.tools.values()]

    def get_resources_list(self) -> List[Dict[str, Any]]:
        """获取资源列表"""
        return [resource.to_dict() for resource in self.resources.values()]

    def get_prompts_list(self) -> List[Dict[str, Any]]:
        """获取提示列表"""
        return [prompt.to_dict() for prompt in self.prompts.values()]

    async def get_resource_content(self, uri: str) -> MCPResponse:
        """获取资源内容"""
        if uri not in self.resources:
            return MCPResponse(
                success=False,
                error=f"资源 {uri} 未找到"
            )

        # 子类应该重写此方法
        return MCPResponse(
            success=True,
            data={"uri": uri, "content": ""}
        )

    async def get_server_info(self) -> Dict[str, Any]:
        """获取服务器信息"""
        return {
            "name": self.name,
            "version": self.version,
            "tools": self.get_tools_list(),
            "resources": self.get_resources_list(),
            "prompts": self.get_prompts_list()
        }


class MCPClient:
    """MCP客户端，用于测试和集成"""

    def __init__(self, server: BaseMCPServer):
        self.server = server

    async def list_tools(self) -> List[Dict[str, Any]]:
        """列出所有工具"""
        return self.server.get_tools_list()

    async def call_tool(self, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """调用工具"""
        response = await self.server.call_tool(name, arguments)
        return response.to_dict()

    async def list_resources(self) -> List[Dict[str, Any]]:
        """列出所有资源"""
        return self.server.get_resources_list()

    async def get_resource(self, uri: str) -> Dict[str, Any]:
        """获取资源"""
        response = await self.server.get_resource_content(uri)
        return response.to_dict()


# 辅助工具

def format_query_result(result: Any, max_rows: int = 100) -> Dict[str, Any]:
    """格式化查询结果"""
    if isinstance(result, list):
        # 如果是列表，取前max_rows行
        rows = result[:max_rows]
        row_count = len(result)

        # 提取列名
        columns = []
        if rows:
            columns = list(rows[0].keys()) if isinstance(rows[0], dict) else []

        return {
            "type": "table",
            "columns": columns,
            "rows": rows,
            "row_count": row_count,
            "displayed_rows": min(row_count, max_rows)
        }
    else:
        return {
            "type": "value",
            "data": result
        }


def validate_schema(data: Dict[str, Any], schema: Dict[str, Any]) -> bool:
    """简单的Schema验证"""
    # 这里应该使用更严格的验证库如pydantic
    # 目前只是简单的检查
    required_fields = schema.get("required", [])
    for field in required_fields:
        if field not in data:
            return False
    return True


# 示例装饰器

def mcp_tool(
    name: str,
    description: str,
    input_schema: Dict[str, Any]
):
    """MCP工具装饰器"""

    def decorator(func):
        # 将函数转换为MCP工具
        def wrapper(*args, **kwargs):
            return func(*args, **kwargs)

        # 存储元数据
        wrapper._mcp_tool = {
            "name": name,
            "description": description,
            "input_schema": input_schema
        }

        return wrapper

    return decorator


# 示例用法

if __name__ == "__main__":
    import asyncio

    class TestServer(BaseMCPServer):
        def __init__(self):
            super().__init__("Test Server", "1.0.0")

        async def initialize(self):
            # 注册示例工具
            self.register_tool(
                name="test_tool",
                description="测试工具",
                input_schema={
                    "type": "object",
                    "properties": {
                        "message": {"type": "string"}
                    },
                    "required": ["message"]
                },
                callback=self.test_tool
            )

        async def test_tool(self, message: str) -> str:
            return f"收到消息: {message}"

    async def main():
        server = TestServer()
        await server.initialize()

        # 获取服务器信息
        info = await server.get_server_info()
        print(json.dumps(info, indent=2, ensure_ascii=False))

        # 调用工具
        result = await server.call_tool("test_tool", {"message": "Hello MCP!"})
        print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))

    asyncio.run(main())
