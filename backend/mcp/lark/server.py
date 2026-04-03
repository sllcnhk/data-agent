"""
Lark/飞书 MCP服务器实现

提供飞书文档、表格等的访问功能
"""
import asyncio
from typing import Dict, List, Any, Optional
from datetime import datetime
import json
import base64
import hmac
import hashlib
import time
from urllib.parse import quote

from backend.mcp.base import BaseMCPServer, MCPResponse
from backend.config.settings import settings


class LarkMCPServer(BaseMCPServer):
    """Lark/飞书 MCP服务器"""

    def __init__(self):
        """初始化Lark MCP服务器"""
        super().__init__(
            name="Lark MCP Server",
            version="1.0.0"
        )
        self.app_id = settings.lark_app_id
        self.app_secret = settings.lark_app_secret
        self.verification_token = settings.lark_verification_token
        self.encrypt_key = settings.lark_encrypt_key

        # API endpoints
        self.base_url = "https://open.feishu.cn/open-apis"
        self.auth_url = "https://open.feishu.cn/open-apis/auth/v3/app_access_token/internal"
        self.doc_url = "https://open.feishu.cn/open-apis/doc/v2"
        self.sheet_url = "https://open.feishu.cn/open-apis/sheet/v2"

        # Access token
        self.access_token: Optional[str] = None
        self.token_expires_at: Optional[datetime] = None

    async def initialize(self):
        """初始化服务器"""
        # 注册工具
        self._register_tools()

        # 注册资源
        self._register_resources()

        # 检查配置
        if not self.app_id or not self.app_secret:
            print("警告: Lark APP ID 或 APP SECRET 未配置")

    def _register_tools(self):
        """注册工具"""

        # 获取访问令牌
        self.register_tool(
            name="get_access_token",
            description="获取Lark访问令牌",
            input_schema={
                "type": "object",
                "properties": {}
            },
            callback=self._get_access_token
        )

        # 获取文档列表
        self.register_tool(
            name="list_documents",
            description="获取文档列表",
            input_schema={
                "type": "object",
                "properties": {
                    "folder_token": {
                        "type": "string",
                        "description": "文件夹token（可选）"
                    },
                    "count": {
                        "type": "integer",
                        "description": "返回数量",
                        "default": 20
                    }
                }
            },
            callback=self._list_documents
        )

        # 获取文档内容
        self.register_tool(
            name="get_document_content",
            description="获取文档内容",
            input_schema={
                "type": "object",
                "properties": {
                    "doc_token": {
                        "type": "string",
                        "description": "文档token"
                    }
                },
                "required": ["doc_token"]
            },
            callback=self._get_document_content
        )

        # 获取表格列表
        self.register_tool(
            name="list_sheets",
            description="获取表格列表",
            input_schema={
                "type": "object",
                "properties": {
                    "folder_token": {
                        "type": "string",
                        "description": "文件夹token（可选）"
                    }
                }
            },
            callback=self._list_sheets
        )

        # 获取表格内容
        self.register_tool(
            name="get_sheet_content",
            description="获取表格内容",
            input_schema={
                "type": "object",
                "properties": {
                    "sheet_token": {
                        "type": "string",
                        "description": "表格token"
                    },
                    "sheet_id": {
                        "type": "string",
                        "description": "工作表ID（可选）"
                    }
                },
                "required": ["sheet_token"]
            },
            callback=self._get_sheet_content
        )

        # 搜索文档
        self.register_tool(
            name="search_documents",
            description="搜索文档",
            input_schema={
                "type": "object",
                "properties": {
                    "keyword": {
                        "type": "string",
                        "description": "搜索关键词"
                    },
                    "count": {
                        "type": "integer",
                        "description": "返回数量",
                        "default": 10
                    }
                },
                "required": ["keyword"]
            },
            callback=self._search_documents
        )

        # 获取用户信息
        self.register_tool(
            name="get_user_info",
            description="获取用户信息",
            input_schema={
                "type": "object",
                "properties": {
                    "user_id": {
                        "type": "string",
                        "description": "用户ID（可选，默认当前用户）"
                    }
                }
            },
            callback=self._get_user_info
        )

    def _register_resources(self):
        """注册资源"""
        # 文档列表资源
        self.register_resource(
            uri="lark://documents",
            name="文档列表",
            description="Lark文档列表",
            mime_type="application/json"
        )

        # 表格列表资源
        self.register_resource(
            uri="lark://sheets",
            name="表格列表",
            description="Lark表格列表",
            mime_type="application/json"
        )

    async def _get_access_token(self) -> Dict[str, Any]:
        """获取访问令牌"""
        try:
            # 检查令牌是否已过期
            if self.access_token and self.token_expires_at:
                if datetime.now() < self.token_expires_at:
                    return {
                        "type": "access_token",
                        "token": self.access_token,
                        "expires_at": self.token_expires_at.isoformat(),
                        "cached": True
                    }

            import httpx

            # 获取新令牌
            headers = {"Content-Type": "application/json; charset=utf-8"}
            data = {
                "app_id": self.app_id,
                "app_secret": self.app_secret
            }

            async with httpx.AsyncClient() as client:
                response = await client.post(
                    self.auth_url,
                    headers=headers,
                    json=data
                )

                if response.status_code == 200:
                    result = response.json()
                    if result.get("code") == 0:
                        self.access_token = result["app_access_token"]
                        # 令牌有效期为2小时，提前5分钟刷新
                        expires_in = result.get("expire", 7200)
                        self.token_expires_at = datetime.now().timestamp() + expires_in - 300
                        self.token_expires_at = datetime.fromtimestamp(self.token_expires_at)

                        return {
                            "type": "access_token",
                            "token": self.access_token,
                            "expires_at": self.token_expires_at.isoformat(),
                            "cached": False
                        }
                    else:
                        return {
                            "error": f"获取令牌失败: {result.get('msg')}",
                            "code": result.get("code")
                        }
                else:
                    return {
                        "error": f"HTTP错误: {response.status_code}"
                    }

        except Exception as e:
            return {
                "error": f"获取访问令牌失败: {str(e)}"
            }

    async def _list_documents(
        self,
        folder_token: Optional[str] = None,
        count: int = 20
    ) -> Dict[str, Any]:
        """获取文档列表"""
        try:
            # 获取访问令牌
            token_result = await self._get_access_token()
            if "error" in token_result:
                return token_result

            import httpx

            headers = {
                "Authorization": f"Bearer {self.access_token}",
                "Content-Type": "application/json; charset=utf-8"
            }

            params = {"count": count}
            if folder_token:
                params["folder_token"] = folder_token

            url = f"{self.doc_url}/folders/docs"

            async with httpx.AsyncClient() as client:
                response = await client.get(
                    url,
                    headers=headers,
                    params=params
                )

                if response.status_code == 200:
                    result = response.json()
                    if result.get("code") == 0:
                        docs = result.get("data", {}).get("items", [])
                        return {
                            "type": "document_list",
                            "documents": [
                                {
                                    "name": doc.get("title"),
                                    "token": doc.get("doc_token"),
                                    "type": doc.get("type"),
                                    "url": doc.get("url"),
                                    "owner": doc.get("owner"),
                                    "created_at": doc.get("created_at"),
                                    "modified_at": doc.get("modified_at")
                                }
                                for doc in docs
                            ],
                            "count": len(docs)
                        }
                    else:
                        return {
                            "error": f"获取文档列表失败: {result.get('msg')}",
                            "code": result.get("code")
                        }
                else:
                    return {
                        "error": f"HTTP错误: {response.status_code}"
                    }

        except Exception as e:
            return {
                "error": f"获取文档列表失败: {str(e)}"
            }

    async def _get_document_content(self, doc_token: str) -> Dict[str, Any]:
        """获取文档内容"""
        try:
            # 获取访问令牌
            token_result = await self._get_access_token()
            if "error" in token_result:
                return token_result

            import httpx

            headers = {
                "Authorization": f"Bearer {self.access_token}",
                "Content-Type": "application/json; charset=utf-8"
            }

            url = f"{self.doc_url}/documents/{doc_token}/content"

            async with httpx.AsyncClient() as client:
                response = await client.get(url, headers=headers)

                if response.status_code == 200:
                    result = response.json()
                    if result.get("code") == 0:
                        content = result.get("data", {})
                        # 提取文档内容
                        blocks = self._extract_content_blocks(content)
                        return {
                            "type": "document_content",
                            "doc_token": doc_token,
                            "blocks": blocks,
                            "block_count": len(blocks)
                        }
                    else:
                        return {
                            "error": f"获取文档内容失败: {result.get('msg')}",
                            "code": result.get("code")
                        }
                else:
                    return {
                        "error": f"HTTP错误: {response.status_code}"
                    }

        except Exception as e:
            return {
                "error": f"获取文档内容失败: {str(e)}"
            }

    def _extract_content_blocks(self, content: Dict[str, Any]) -> List[Dict[str, Any]]:
        """提取文档内容块"""
        blocks = []
        items = content.get("items", [])

        for item in items:
            block_type = item.get("block_type")
            text_element = item.get("text_elements", [])

            if text_element:
                text = ""
                for element in text_element:
                    if element.get("text_run"):
                        text += element["text_run"].get("text", "")

                blocks.append({
                    "type": block_type,
                    "text": text
                })

        return blocks

    async def _list_sheets(self, folder_token: Optional[str] = None) -> Dict[str, Any]:
        """获取表格列表"""
        try:
            # 获取访问令牌
            token_result = await self._get_access_token()
            if "error" in token_result:
                return token_result

            import httpx

            headers = {
                "Authorization": f"Bearer {self.access_token}",
                "Content-Type": "application/json; charset=utf-8"
            }

            params = {}
            if folder_token:
                params["folder_token"] = folder_token

            url = f"{self.sheet_url}/spreadsheets"

            async with httpx.AsyncClient() as client:
                response = await client.get(url, headers=headers, params=params)

                if response.status_code == 200:
                    result = response.json()
                    if result.get("code") == 0:
                        sheets = result.get("data", {}).get("items", [])
                        return {
                            "type": "sheet_list",
                            "sheets": [
                                {
                                    "name": sheet.get("title"),
                                    "token": sheet.get("spreadsheet_token"),
                                    "url": sheet.get("url"),
                                    "owner": sheet.get("owner"),
                                    "created_at": sheet.get("created_at"),
                                    "modified_at": sheet.get("modified_at")
                                }
                                for sheet in sheets
                            ],
                            "count": len(sheets)
                        }
                    else:
                        return {
                            "error": f"获取表格列表失败: {result.get('msg')}",
                            "code": result.get("code")
                        }
                else:
                    return {
                        "error": f"HTTP错误: {response.status_code}"
                    }

        except Exception as e:
            return {
                "error": f"获取表格列表失败: {str(e)}"
            }

    async def _get_sheet_content(
        self,
        sheet_token: str,
        sheet_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """获取表格内容"""
        try:
            # 获取访问令牌
            token_result = await self._get_access_token()
            if "error" in token_result:
                return token_result

            import httpx

            headers = {
                "Authorization": f"Bearer {self.access_token}",
                "Content-Type": "application/json; charset=utf-8"
            }

            # 如果没有指定sheet_id，获取第一个工作表
            if not sheet_id:
                sheets_info = await self._get_sheet_info(sheet_token)
                if "error" in sheets_info:
                    return sheets_info
                sheet_id = sheets_info["sheets"][0]["sheet_id"]

            # 获取数据范围
            range_param = f"{sheet_id}!A:Z"

            url = f"{self.sheet_url}/spreadsheets/{sheet_token}/values"

            async with httpx.AsyncClient() as client:
                response = await client.get(
                    url,
                    headers=headers,
                    params={"range": range_param}
                )

                if response.status_code == 200:
                    result = response.json()
                    if result.get("code") == 0:
                        value_ranges = result.get("data", {}).get("valueRanges", [])
                        if value_ranges:
                            data = value_ranges[0].get("values", [])
                            # 转换为二维数组格式
                            return {
                                "type": "sheet_content",
                                "sheet_token": sheet_token,
                                "sheet_id": sheet_id,
                                "data": data,
                                "rows": len(data),
                                "columns": len(data[0]) if data else 0
                            }
                        else:
                            return {
                                "type": "sheet_content",
                                "sheet_token": sheet_token,
                                "sheet_id": sheet_id,
                                "data": [],
                                "rows": 0,
                                "columns": 0
                            }
                    else:
                        return {
                            "error": f"获取表格内容失败: {result.get('msg')}",
                            "code": result.get("code")
                        }
                else:
                    return {
                        "error": f"HTTP错误: {response.status_code}"
                    }

        except Exception as e:
            return {
                "error": f"获取表格内容失败: {str(e)}"
            }

    async def _get_sheet_info(self, sheet_token: str) -> Dict[str, Any]:
        """获取表格信息"""
        try:
            import httpx

            headers = {
                "Authorization": f"Bearer {self.access_token}",
                "Content-Type": "application/json; charset=utf-8"
            }

            url = f"{self.sheet_url}/spreadsheets/{sheet_token}"

            async with httpx.AsyncClient() as client:
                response = await client.get(url, headers=headers)

                if response.status_code == 200:
                    result = response.json()
                    if result.get("code") == 0:
                        sheets = result.get("data", {}).get("sheets", [])
                        return {
                            "sheets": [
                                {
                                    "title": sheet.get("title"),
                                    "sheet_id": sheet.get("sheet_id")
                                }
                                for sheet in sheets
                            ]
                        }
                    else:
                        return {
                            "error": f"获取表格信息失败: {result.get('msg')}",
                            "code": result.get("code")
                        }
                else:
                    return {
                        "error": f"HTTP错误: {response.status_code}"
                    }

        except Exception as e:
            return {
                "error": f"获取表格信息失败: {str(e)}"
            }

    async def _search_documents(
        self,
        keyword: str,
        count: int = 10
    ) -> Dict[str, Any]:
        """搜索文档"""
        try:
            # 注意: Lark搜索API可能需要特定的权限
            # 这里返回模拟数据
            return {
                "type": "search_result",
                "keyword": keyword,
                "results": [
                    {
                        "name": f"搜索结果1 - {keyword}",
                        "token": "search_result_token_1",
                        "type": "doc",
                        "snippet": f"包含关键词'{keyword}'的文档...",
                        "url": f"https://example.com/doc?token=search_result_token_1"
                    }
                ],
                "count": 1,
                "note": "搜索功能需要配置权限，当前返回示例数据"
            }

        except Exception as e:
            return {
                "error": f"搜索文档失败: {str(e)}"
            }

    async def _get_user_info(self, user_id: Optional[str] = None) -> Dict[str, Any]:
        """获取用户信息"""
        try:
            # 获取访问令牌
            token_result = await self._get_access_token()
            if "error" in token_result:
                return token_result

            import httpx

            headers = {
                "Authorization": f"Bearer {self.access_token}",
                "Content-Type": "application/json; charset=utf-8"
            }

            url = f"{self.base_url}/contact/v3/users/me"

            async with httpx.AsyncClient() as client:
                response = await client.get(url, headers=headers)

                if response.status_code == 200:
                    result = response.json()
                    if result.get("code") == 0:
                        user = result.get("data", {})
                        return {
                            "type": "user_info",
                            "user": {
                                "user_id": user.get("user_id"),
                                "name": user.get("name"),
                                "avatar": user.get("avatar_url"),
                                "email": user.get("email"),
                                "department": user.get("department")
                            }
                        }
                    else:
                        return {
                            "error": f"获取用户信息失败: {result.get('msg')}",
                            "code": result.get("code")
                        }
                else:
                    return {
                        "error": f"HTTP错误: {response.status_code}"
                    }

        except Exception as e:
            return {
                "error": f"获取用户信息失败: {str(e)}"
            }

    async def get_resource_content(self, uri: str) -> MCPResponse:
        """获取资源内容"""
        try:
            if uri == "lark://documents":
                # 获取文档列表
                result = await self._list_documents()
                return MCPResponse(
                    success=True,
                    data=result
                )
            elif uri == "lark://sheets":
                # 获取表格列表
                result = await self._list_sheets()
                return MCPResponse(
                    success=True,
                    data=result
                )
            else:
                return MCPResponse(
                    success=False,
                    error=f"未知的资源: {uri}"
                )
        except Exception as e:
            return MCPResponse(
                success=False,
                error=str(e)
            )


# 工厂函数

def create_lark_server() -> LarkMCPServer:
    """
    创建Lark MCP服务器实例

    Returns:
        LarkMCPServer实例
    """
    return LarkMCPServer()


# 示例用法

if __name__ == "__main__":
    import asyncio

    async def main():
        # 创建服务器
        server = create_lark_server()
        await server.initialize()

        # 获取服务器信息
        info = await server.get_server_info()
        print(json.dumps(info, indent=2, ensure_ascii=False))

        # 获取访问令牌
        result = await server.call_tool("get_access_token", {})
        print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))

    # asyncio.run(main())
