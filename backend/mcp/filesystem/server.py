"""
Filesystem MCP服务器实现

提供文件系统的浏览、读取、写入等功能
"""
import asyncio
from typing import Dict, List, Any, Optional
from datetime import datetime
import json
import os
from pathlib import Path
import mimetypes
from urllib.parse import unquote

from backend.mcp.base import BaseMCPServer, MCPResponse
from backend.config.settings import settings


class FilesystemMCPServer(BaseMCPServer):
    """Filesystem MCP服务器"""

    def __init__(self):
        """初始化Filesystem MCP服务器"""
        super().__init__(
            name="Filesystem MCP Server",
            version="1.0.0"
        )
        # 获取允许的目录
        self.allowed_directories = settings.allowed_directories
        # 确保目录存在
        for directory in self.allowed_directories:
            Path(directory).mkdir(parents=True, exist_ok=True)

    async def initialize(self):
        """初始化服务器"""
        # 注册工具
        self._register_tools()

        # 注册资源
        self._register_resources()

    def _register_tools(self):
        """注册工具"""

        # 列出目录内容
        self.register_tool(
            name="list_directory",
            description="列出目录内容",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "目录路径（相对于允许的目录）"
                    }
                },
                "required": ["path"]
            },
            callback=self._list_directory
        )

        # 读取文件
        self.register_tool(
            name="read_file",
            description="读取文件内容",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "文件路径（相对于允许的目录）"
                    }
                },
                "required": ["path"]
            },
            callback=self._read_file
        )

        # 写入文件
        self.register_tool(
            name="write_file",
            description="写入文件",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "文件路径（相对于允许的目录）"
                    },
                    "content": {
                        "type": "string",
                        "description": "文件内容"
                    },
                    "mode": {
                        "type": "string",
                        "description": "写入模式 (write/append)",
                        "default": "write"
                    }
                },
                "required": ["path", "content"]
            },
            callback=self._write_file
        )

        # 获取文件信息
        self.register_tool(
            name="get_file_info",
            description="获取文件信息",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "文件或目录路径（相对于允许的目录）"
                    }
                },
                "required": ["path"]
            },
            callback=self._get_file_info
        )

        # 创建目录
        self.register_tool(
            name="create_directory",
            description="创建目录",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "目录路径（相对于允许的目录）"
                    }
                },
                "required": ["path"]
            },
            callback=self._create_directory
        )

        # 删除文件或目录
        self.register_tool(
            name="delete",
            description="删除文件或目录",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "文件或目录路径（相对于允许的目录）"
                    },
                    "force": {
                        "type": "boolean",
                        "description": "是否强制删除（删除非空目录）",
                        "default": False
                    }
                },
                "required": ["path"]
            },
            callback=self._delete
        )

        # 搜索文件
        self.register_tool(
            name="search_files",
            description="搜索文件",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "搜索目录（相对于允许的目录）"
                    },
                    "pattern": {
                        "type": "string",
                        "description": "搜索模式（文件名）"
                    },
                    "recursive": {
                        "type": "boolean",
                        "description": "是否递归搜索",
                        "default": True
                    }
                },
                "required": ["path", "pattern"]
            },
            callback=self._search_files
        )

        # 列出允许的目录
        self.register_tool(
            name="list_allowed_directories",
            description="列出允许访问的目录",
            input_schema={
                "type": "object",
                "properties": {}
            },
            callback=self._list_allowed_directories
        )

        # 获取文件类型
        self.register_tool(
            name="get_file_type",
            description="获取文件类型",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "文件路径（相对于允许的目录）"
                    }
                },
                "required": ["path"]
            },
            callback=self._get_file_type
        )

    def _register_resources(self):
        """注册资源"""
        # 列出允许的目录
        self.register_resource(
            uri="filesystem://directories",
            name="允许的目录",
            description="文件系统允许访问的目录列表",
            mime_type="application/json"
        )

        # 列出根目录内容
        self.register_resource(
            uri="filesystem://root",
            name="根目录",
            description="根目录内容",
            mime_type="application/json"
        )

    def _normalize_path(self, path: str) -> str:
        """规范化路径"""
        # 解码URL编码
        path = unquote(path)
        # 统一斜杠方向（Windows 反斜杠 → 正斜杠）
        path = path.replace("\\", "/")
        # 移除开头的斜杠（Unix 绝对路径）
        path = path.lstrip("/")
        return path

    def _resolve_path(self, path: str) -> Path:
        """解析路径并检查权限。

        支持两种输入形式：
          1. 相对路径：  backend/skills/my_skill.md
          2. 绝对路径：  C:/Users/.../data-agent/backend/skills/my_skill.md
             对于绝对路径，先尝试直接使用；若不在允许目录内则报错。
        """
        normalized = self._normalize_path(path)

        # 若规范化后仍是绝对路径（带盘符，如 C:/...），直接解析
        abs_candidate = Path(normalized)
        if abs_candidate.is_absolute():
            resolved = abs_candidate.resolve()
            for allowed_dir in self.allowed_directories:
                try:
                    resolved.relative_to(Path(allowed_dir).resolve())
                    return abs_candidate
                except ValueError:
                    continue
            raise PermissionError(f"路径不在允许的目录中: {path}")

        # 相对路径：在允许目录中逐一尝试拼接
        for allowed_dir in self.allowed_directories:
            candidate = Path(allowed_dir) / normalized
            try:
                candidate.resolve().relative_to(Path(allowed_dir).resolve())
                return candidate
            except ValueError:
                continue

        raise PermissionError(f"路径不在允许的目录中: {path}")

    async def _list_directory(self, path: str) -> Dict[str, Any]:
        """列出目录内容"""
        try:
            resolved_path = self._resolve_path(path)

            if not resolved_path.exists():
                return {
                    "error": f"目录不存在: {path}"
                }

            if not resolved_path.is_dir():
                return {
                    "error": f"不是目录: {path}"
                }

            items = []
            for item in resolved_path.iterdir():
                try:
                    stat = item.stat()
                    items.append({
                        "name": item.name,
                        "type": "directory" if item.is_dir() else "file",
                        "size": stat.st_size if item.is_file() else None,
                        "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                        "path": str(item.relative_to(Path(self.allowed_directories[0])))
                    })
                except (PermissionError, OSError):
                    # 跳过无法访问的文件
                    continue

            return {
                "type": "directory_list",
                "path": path,
                "items": items,
                "item_count": len(items)
            }

        except PermissionError as e:
            return {
                "error": str(e)
            }
        except Exception as e:
            return {
                "error": f"列出目录失败: {str(e)}"
            }

    async def _read_file(self, path: str) -> Dict[str, Any]:
        """读取文件"""
        try:
            resolved_path = self._resolve_path(path)

            if not resolved_path.exists():
                return {
                    "error": f"文件不存在: {path}"
                }

            if not resolved_path.is_file():
                return {
                    "error": f"不是文件: {path}"
                }

            # 检查文件大小（限制10MB）
            file_size = resolved_path.stat().st_size
            if file_size > 10 * 1024 * 1024:
                return {
                    "error": f"文件太大 ({file_size / 1024 / 1024:.2f}MB)，超过10MB限制"
                }

            # 读取文件
            with open(resolved_path, 'r', encoding='utf-8') as f:
                content = f.read()

            # 检测文件类型
            mime_type, _ = mimetypes.guess_type(str(resolved_path))

            return {
                "type": "file_content",
                "path": path,
                "content": content,
                "size": file_size,
                "mime_type": mime_type,
                "encoding": "utf-8"
            }

        except PermissionError as e:
            return {
                "error": str(e)
            }
        except UnicodeDecodeError:
            # 尝试以二进制方式读取
            try:
                with open(resolved_path, 'rb') as f:
                    content = f.read()

                return {
                    "type": "file_content",
                    "path": path,
                    "content": content.hex(),  # 以十六进制形式返回
                    "size": len(content),
                    "mime_type": "application/octet-stream",
                    "encoding": "binary"
                }
            except Exception as e:
                return {
                    "error": f"读取文件失败: {str(e)}"
                }
        except Exception as e:
            return {
                "error": f"读取文件失败: {str(e)}"
            }

    async def _write_file(self, path: str, content: str, mode: str = "write") -> Dict[str, Any]:
        """写入文件"""
        try:
            resolved_path = self._resolve_path(path)

            # 创建目录
            resolved_path.parent.mkdir(parents=True, exist_ok=True)

            # 写入文件
            if mode == "append":
                with open(resolved_path, 'a', encoding='utf-8') as f:
                    f.write(content)
                operation = "append"
            else:
                with open(resolved_path, 'w', encoding='utf-8') as f:
                    f.write(content)
                operation = "write"

            # 获取文件信息
            stat = resolved_path.stat()

            return {
                "type": "write_result",
                "path": path,
                "operation": operation,
                "size": stat.st_size,
                "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                "success": True
            }

        except PermissionError as e:
            return {
                "error": str(e)
            }
        except Exception as e:
            return {
                "error": f"写入文件失败: {str(e)}"
            }

    async def _get_file_info(self, path: str) -> Dict[str, Any]:
        """获取文件信息"""
        try:
            resolved_path = self._resolve_path(path)

            if not resolved_path.exists():
                return {
                    "error": f"文件或目录不存在: {path}"
                }

            stat = resolved_path.stat()

            # 检测类型
            if resolved_path.is_dir():
                file_type = "directory"
                # 尝试获取目录内容数量
                try:
                    item_count = len(list(resolved_path.iterdir()))
                except PermissionError:
                    item_count = None
            else:
                file_type = "file"
                item_count = None

            # 检测MIME类型
            mime_type = None
            if resolved_path.is_file():
                mime_type, _ = mimetypes.guess_type(str(resolved_path))

            return {
                "type": "file_info",
                "path": path,
                "name": resolved_path.name,
                "file_type": file_type,
                "size": stat.st_size,
                "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                "created": datetime.fromtimestamp(stat.st_ctime).isoformat(),
                "mime_type": mime_type,
                "item_count": item_count
            }

        except PermissionError as e:
            return {
                "error": str(e)
            }
        except Exception as e:
            return {
                "error": f"获取文件信息失败: {str(e)}"
            }

    async def _create_directory(self, path: str) -> Dict[str, Any]:
        """创建目录"""
        try:
            resolved_path = self._resolve_path(path)

            # 检查是否已存在
            if resolved_path.exists():
                return {
                    "error": f"路径已存在: {path}"
                }

            # 创建目录
            resolved_path.mkdir(parents=True, exist_ok=True)

            return {
                "type": "create_directory",
                "path": path,
                "success": True,
                "created": datetime.now().isoformat()
            }

        except PermissionError as e:
            return {
                "error": str(e)
            }
        except Exception as e:
            return {
                "error": f"创建目录失败: {str(e)}"
            }

    async def _delete(self, path: str, force: bool = False) -> Dict[str, Any]:
        """删除文件或目录"""
        try:
            resolved_path = self._resolve_path(path)

            if not resolved_path.exists():
                return {
                    "error": f"文件或目录不存在: {path}"
                }

            if resolved_path.is_dir():
                if not force:
                    # 检查目录是否为空
                    if any(resolved_path.iterdir()):
                        return {
                            "error": f"目录不为空: {path}，使用 force=true 强制删除"
                        }
                resolved_path.rmdir() if not force else self._remove_directory(resolved_path)
            else:
                resolved_path.unlink()

            return {
                "type": "delete",
                "path": path,
                "success": True,
                "deleted": datetime.now().isoformat()
            }

        except PermissionError as e:
            return {
                "error": str(e)
            }
        except Exception as e:
            return {
                "error": f"删除失败: {str(e)}"
            }

    def _remove_directory(self, path: Path):
        """递归删除目录"""
        for item in path.iterdir():
            if item.is_dir():
                self._remove_directory(item)
            else:
                item.unlink()
        path.rmdir()

    async def _search_files(self, path: str, pattern: str, recursive: bool = True) -> Dict[str, Any]:
        """搜索文件"""
        try:
            resolved_path = self._resolve_path(path)

            if not resolved_path.exists():
                return {
                    "error": f"目录不存在: {path}"
                }

            if not resolved_path.is_dir():
                return {
                    "error": f"不是目录: {path}"
                }

            matches = []
            search_pattern = pattern.lower()

            def search(directory: Path):
                try:
                    for item in directory.iterdir():
                        if item.name.lower().find(search_pattern) != -1:
                            try:
                                stat = item.stat()
                                matches.append({
                                    "name": item.name,
                                    "path": str(item.relative_to(Path(self.allowed_directories[0]))),
                                    "type": "directory" if item.is_dir() else "file",
                                    "size": stat.st_size if item.is_file() else None,
                                    "modified": datetime.fromtimestamp(stat.st_mtime).isoformat()
                                })
                            except (PermissionError, OSError):
                                pass

                        if recursive and item.is_dir():
                            search(item)
                except PermissionError:
                    pass

            search(resolved_path)

            return {
                "type": "search_result",
                "path": path,
                "pattern": pattern,
                "recursive": recursive,
                "matches": matches,
                "match_count": len(matches)
            }

        except PermissionError as e:
            return {
                "error": str(e)
            }
        except Exception as e:
            return {
                "error": f"搜索文件失败: {str(e)}"
            }

    async def _list_allowed_directories(self) -> Dict[str, Any]:
        """列出允许的目录"""
        directories = []
        for directory in self.allowed_directories:
            dir_path = Path(directory)
            try:
                stat = dir_path.stat()
                directories.append({
                    "path": directory,
                    "exists": True,
                    "modified": datetime.fromtimestamp(stat.st_mtime).isoformat()
                })
            except Exception:
                directories.append({
                    "path": directory,
                    "exists": False
                })

        return {
            "type": "allowed_directories",
            "directories": directories,
            "count": len(directories)
        }

    async def _get_file_type(self, path: str) -> Dict[str, Any]:
        """获取文件类型"""
        try:
            resolved_path = self._resolve_path(path)

            if not resolved_path.exists():
                return {
                    "error": f"文件不存在: {path}"
                }

            if resolved_path.is_dir():
                # 检查是否为符号链接
                if resolved_path.is_symlink():
                    target = os.readlink(resolved_path)
                    return {
                        "type": "file_type",
                        "path": path,
                        "file_type": "directory_symlink",
                        "target": target
                    }
                return {
                    "type": "file_type",
                    "path": path,
                    "file_type": "directory"
                }
            else:
                mime_type, encoding = mimetypes.guess_type(str(resolved_path))

                if resolved_path.is_symlink():
                    target = os.readlink(resolved_path)
                    return {
                        "type": "file_type",
                        "path": path,
                        "file_type": "file_symlink",
                        "target": target,
                        "mime_type": mime_type,
                        "encoding": encoding
                    }

                # 检查文件扩展名
                extension = resolved_path.suffix.lower()

                return {
                    "type": "file_type",
                    "path": path,
                    "file_type": "file",
                    "extension": extension,
                    "mime_type": mime_type,
                    "encoding": encoding
                }

        except PermissionError as e:
            return {
                "error": str(e)
            }
        except Exception as e:
            return {
                "error": f"获取文件类型失败: {str(e)}"
            }

    async def get_resource_content(self, uri: str) -> MCPResponse:
        """获取资源内容"""
        try:
            if uri == "filesystem://directories":
                # 获取允许的目录列表
                result = await self._list_allowed_directories()
                return MCPResponse(
                    success=True,
                    data=result
                )
            elif uri == "filesystem://root":
                # 获取根目录内容
                result = await self._list_directory("")
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

def create_filesystem_server() -> FilesystemMCPServer:
    """
    创建Filesystem MCP服务器实例

    Returns:
        FilesystemMCPServer实例
    """
    return FilesystemMCPServer()


# 示例用法

if __name__ == "__main__":
    import asyncio

    async def main():
        # 创建服务器
        server = create_filesystem_server()
        await server.initialize()

        # 获取服务器信息
        info = await server.get_server_info()
        print(json.dumps(info, indent=2, ensure_ascii=False))

        # 列出允许的目录
        result = await server.call_tool("list_allowed_directories", {})
        print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))

    # asyncio.run(main())
