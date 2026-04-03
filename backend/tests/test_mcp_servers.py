"""
MCP服务器集成测试

测试所有MCP服务器的功能
"""
import pytest
import asyncio
import json
from unittest.mock import Mock, patch, MagicMock

# 导入MCP服务器
from backend.mcp.clickhouse.server import ClickHouseMCPServer
from backend.mcp.mysql.server import MySQLMCPServer
from backend.mcp.filesystem.server import FilesystemMCPServer
from backend.mcp.lark.server import LarkMCPServer
from backend.mcp.base import MCPClient


class TestClickHouseMCPServer:
    """测试ClickHouse MCP服务器"""

    @pytest.mark.asyncio
    async def test_server_initialization(self):
        """测试服务器初始化"""
        server = ClickHouseMCPServer(env="idn")
        await server.initialize()

        # 检查工具是否注册
        assert "query" in server.tools
        assert "list_databases" in server.tools
        assert "list_tables" in server.tools

        # 检查资源是否注册
        assert len(server.resources) > 0

    @pytest.mark.asyncio
    async def test_list_databases(self):
        """测试列出数据库"""
        # Mock ClickHouse连接
        with patch('backend.mcp.clickhouse.server.ClickHouseClient') as mock_client:
            # 设置模拟返回值
            mock_instance = Mock()
            mock_instance.execute.return_value = [
                ("default",),
                ("test_db",),
                ("analytics",)
            ]
            mock_client.return_value = mock_instance

            server = ClickHouseMCPServer(env="idn")
            await server.initialize()

            result = await server.call_tool("list_databases", {})

            assert result.success is True
            assert "databases" in result.data
            assert "default" in result.data["databases"]

    @pytest.mark.asyncio
    async def test_list_tables(self):
        """测试列出表"""
        with patch('backend.mcp.clickhouse.server.ClickHouseClient') as mock_client:
            mock_instance = Mock()
            mock_instance.execute.return_value = [
                ("user_events",),
                ("orders",),
                ("products",)
            ]
            mock_client.return_value = mock_instance

            server = ClickHouseMCPServer(env="idn")
            await server.initialize()

            result = await server.call_tool("list_tables", {"database": "default"})

            assert result.success is True
            assert "tables" in result.data
            assert len(result.data["tables"]) == 3

    @pytest.mark.asyncio
    async def test_describe_table(self):
        """测试获取表结构"""
        with patch('backend.mcp.clickhouse.server.ClickHouseClient') as mock_client:
            mock_instance = Mock()
            mock_instance.execute.return_value = [
                ("id", "UInt64", "", "", "", "", ""),
                ("name", "String", "", "", "", "", ""),
                ("created_at", "DateTime", "", "", "", "", "")
            ]
            mock_client.return_value = mock_instance

            server = ClickHouseMCPServer(env="idn")
            await server.initialize()

            result = await server.call_tool("describe_table", {
                "table": "users",
                "database": "default"
            })

            assert result.success is True
            assert "columns" in result.data
            assert len(result.data["columns"]) == 3
            assert result.data["columns"][0]["name"] == "id"

    @pytest.mark.asyncio
    async def test_test_connection(self):
        """测试连接"""
        with patch('backend.mcp.clickhouse.server.ClickHouseClient') as mock_client:
            mock_instance = Mock()
            mock_instance.execute.return_value = [(1,)]
            mock_client.return_value = mock_instance

            server = ClickHouseMCPServer(env="idn")
            await server.initialize()

            result = await server.call_tool("test_connection", {})

            assert result.success is True
            assert result.data["status"] == "success"


class TestMySQLMCPServer:
    """测试MySQL MCP服务器"""

    @pytest.mark.asyncio
    async def test_server_initialization(self):
        """测试服务器初始化"""
        server = MySQLMCPServer(env="prod")
        await server.initialize()

        # 检查工具是否注册
        assert "query" in server.tools
        assert "list_databases" in server.tools
        assert "list_tables" in server.tools

    @pytest.mark.asyncio
    async def test_list_databases(self):
        """测试列出数据库"""
        with patch('backend.mcp.mysql.server.pymysql.connect') as mock_connect:
            mock_conn = Mock()
            mock_cursor = Mock()
            mock_cursor.fetchall.return_value = [
                {"Database": "information_schema"},
                {"Database": "mysql"},
                {"Database": "test_db"}
            ]
            mock_conn.cursor.return_value = mock_cursor
            mock_connect.return_value = mock_conn

            server = MySQLMCPServer(env="prod")
            await server.initialize()

            result = await server.call_tool("list_databases", {})

            assert result.success is True
            assert "databases" in result.data
            assert "test_db" in result.data["databases"]

    @pytest.mark.asyncio
    async def test_list_tables(self):
        """测试列出表"""
        with patch('backend.mcp.mysql.server.pymysql.connect') as mock_connect:
            mock_conn = Mock()
            mock_cursor = Mock()
            mock_cursor.fetchall.return_value = [
                {"Tables_in_test_db": "users"},
                {"Tables_in_test_db": "orders"}
            ]
            mock_conn.cursor.return_value = mock_cursor
            mock_connect.return_value = mock_conn

            server = MySQLMCPServer(env="prod")
            await server.initialize()

            result = await server.call_tool("list_tables", {"database": "test_db"})

            assert result.success is True
            assert "tables" in result.data
            assert len(result.data["tables"]) == 2

    @pytest.mark.asyncio
    async def test_describe_table(self):
        """测试获取表结构"""
        with patch('backend.mcp.mysql.server.pymysql.connect') as mock_connect:
            mock_conn = Mock()
            mock_cursor = Mock()
            mock_cursor.fetchall.return_value = [
                {
                    "Field": "id",
                    "Type": "int(11)",
                    "Null": "NO",
                    "Key": "PRI",
                    "Default": None,
                    "Extra": "auto_increment"
                },
                {
                    "Field": "name",
                    "Type": "varchar(255)",
                    "Null": "NO",
                    "Key": "",
                    "Default": None,
                    "Extra": ""
                }
            ]
            mock_conn.cursor.return_value = mock_cursor
            mock_connect.return_value = mock_conn

            server = MySQLMCPServer(env="prod")
            await server.initialize()

            result = await server.call_tool("describe_table", {
                "table": "users",
                "database": "test_db"
            })

            assert result.success is True
            assert "columns" in result.data
            assert len(result.data["columns"]) == 2
            assert result.data["columns"][0]["Field"] == "id"

    @pytest.mark.asyncio
    async def test_test_connection(self):
        """测试连接"""
        with patch('backend.mcp.mysql.server.pymysql.connect') as mock_connect:
            mock_conn = Mock()
            mock_cursor = Mock()
            mock_cursor.fetchone.return_value = {"test": 1}
            mock_conn.cursor.return_value = mock_cursor
            mock_connect.return_value = mock_conn

            server = MySQLMCPServer(env="prod")
            await server.initialize()

            result = await server.call_tool("test_connection", {})

            assert result.success is True
            assert result.data["status"] == "success"


class TestFilesystemMCPServer:
    """测试Filesystem MCP服务器"""

    @pytest.mark.asyncio
    async def test_server_initialization(self):
        """测试服务器初始化"""
        server = FilesystemMCPServer()
        await server.initialize()

        # 检查工具是否注册
        assert "list_directory" in server.tools
        assert "read_file" in server.tools
        assert "write_file" in server.tools
        assert "get_file_info" in server.tools

    @pytest.mark.asyncio
    async def test_list_allowed_directories(self):
        """测试列出允许的目录"""
        server = FilesystemMCPServer()
        await server.initialize()

        result = await server.call_tool("list_allowed_directories", {})

        assert result.success is True
        assert "directories" in result.data
        assert len(result.data["directories"]) > 0

    @pytest.mark.asyncio
    async def test_create_directory(self):
        """测试创建目录"""
        import tempfile
        import os

        with tempfile.TemporaryDirectory() as tmpdir:
            # 模拟允许的目录
            server = FilesystemMCPServer()
            server.allowed_directories = [tmpdir]
            await server.initialize()

            result = await server.call_tool("create_directory", {
                "path": "test_dir"
            })

            assert result.success is True
            assert os.path.exists(os.path.join(tmpdir, "test_dir"))

    @pytest.mark.asyncio
    async def test_write_and_read_file(self):
        """测试写入和读取文件"""
        import tempfile
        import os

        with tempfile.TemporaryDirectory() as tmpdir:
            server = FilesystemMCPServer()
            server.allowed_directories = [tmpdir]
            await server.initialize()

            # 写入文件
            write_result = await server.call_tool("write_file", {
                "path": "test.txt",
                "content": "Hello, World!"
            })

            assert write_result.success is True

            # 读取文件
            read_result = await server.call_tool("read_file", {
                "path": "test.txt"
            })

            assert read_result.success is True
            assert read_result.data["content"] == "Hello, World!"

    @pytest.mark.asyncio
    async def test_search_files(self):
        """测试搜索文件"""
        import tempfile
        import os

        with tempfile.TemporaryDirectory() as tmpdir:
            # 创建测试文件
            os.makedirs(os.path.join(tmpdir, "subdir"))
            os.makedirs(os.path.join(tmpdir, "subdir", "docs"))

            # 创建一些文件
            open(os.path.join(tmpdir, "test.txt"), "w").close()
            open(os.path.join(tmpdir, "test.csv"), "w").close()
            open(os.path.join(tmpdir, "subdir", "document.txt"), "w").close()

            server = FilesystemMCPServer()
            server.allowed_directories = [tmpdir]
            await server.initialize()

            result = await server.call_tool("search_files", {
                "path": "",
                "pattern": "test"
            })

            assert result.success is True
            assert "matches" in result.data
            assert result.data["match_count"] >= 2


class TestLarkMCPServer:
    """测试Lark MCP服务器"""

    @pytest.mark.asyncio
    async def test_server_initialization(self):
        """测试服务器初始化"""
        server = LarkMCPServer()
        await server.initialize()

        # 检查工具是否注册
        assert "get_access_token" in server.tools
        assert "list_documents" in server.tools
        assert "list_sheets" in server.tools

    @pytest.mark.asyncio
    async def test_get_access_token_without_config(self):
        """测试获取访问令牌(未配置)"""
        server = LarkMCPServer()
        server.app_id = ""
        server.app_secret = ""
        await server.initialize()

        # 应该不会报错，但会有警告
        result = await server.call_tool("get_access_token", {})

        # 可能会返回错误，因为我们没有配置
        assert "error" in result.data or result.data.get("cached") == True

    @pytest.mark.asyncio
    async def test_search_documents(self):
        """测试搜索文档"""
        with patch('backend.mcp.lark.server.LarkMCPServer._get_access_token') as mock_token:
            mock_token.return_value = {"token": "test_token"}

            server = LarkMCPServer()
            await server.initialize()

            result = await server.call_tool("search_documents", {
                "keyword": "测试"
            })

            assert result.success is True
            assert "keyword" in result.data
            assert result.data["keyword"] == "测试"


class TestMCPClient:
    """测试MCP客户端"""

    @pytest.mark.asyncio
    async def test_client_initialization(self):
        """测试客户端初始化"""
        server = FilesystemMCPServer()
        await server.initialize()

        client = MCPClient(server)

        # 列出工具
        tools = await client.list_tools()
        assert len(tools) > 0

        # 列出资源
        resources = await client.list_resources()
        assert len(resources) > 0


# 集成测试

class TestMCPIntegration:
    """MCP集成测试"""

    @pytest.mark.asyncio
    async def test_all_servers_have_unique_names(self):
        """测试所有服务器都有唯一名称"""
        servers = [
            ClickHouseMCPServer(env="idn"),
            MySQLMCPServer(env="prod"),
            FilesystemMCPServer(),
            LarkMCPServer()
        ]

        names = []
        for server in servers:
            await server.initialize()
            names.append(server.name)

        # 检查名称唯一性
        assert len(names) == len(set(names))

    @pytest.mark.asyncio
    async def test_all_servers_have_query_tool(self):
        """测试有数据库的服务器都有查询工具"""
        # ClickHouse和MySQL应该有query工具
        ch_server = ClickHouseMCPServer(env="idn")
        await ch_server.initialize()
        assert "query" in ch_server.tools

        mysql_server = MySQLMCPServer(env="prod")
        await mysql_server.initialize()
        assert "query" in mysql_server.tools

        # Filesystem和Lark不应该有query工具
        fs_server = FilesystemMCPServer()
        await fs_server.initialize()
        assert "query" not in fs_server.tools

        lark_server = LarkMCPServer()
        await lark_server.initialize()
        assert "query" not in lark_server.tools

    @pytest.mark.asyncio
    async def test_server_info_consistency(self):
        """测试服务器信息一致性"""
        server = FilesystemMCPServer()
        await server.initialize()

        info = await server.get_server_info()

        # 检查信息结构
        assert "name" in info
        assert "version" in info
        assert "tools" in info
        assert "resources" in info

        # 检查工具列表不为空
        assert len(info["tools"]) > 0
        assert len(info["resources"]) > 0


# 运行示例

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
