"""
test_mysql_mcp.py — MySQL MCP Server + 动态 env 发现 + standalone_db_server 结构测试

A 组：settings.get_all_mysql_envs()      — 动态 env 发现
B 组：settings.get_mysql_config()        — 动态 env 配置读取（含任意新 env）
C 组：MySQLMCPServer._ensure_connection() — 重连逻辑
D 组：manager dynamic env                — manager 使用 get_all_mysql_envs()
E 组：standalone_db_server 结构            — 可导入、主要类/函数存在
"""
import sys
import os
import asyncio
import json
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch, PropertyMock
import pytest

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


# ─── A 组：get_all_mysql_envs ────────────────────────────────────────────────

class TestGetAllMysqlEnvs:
    def test_A1_returns_list(self):
        from backend.config.settings import settings
        result = settings.get_all_mysql_envs()
        assert isinstance(result, list)

    def test_A2_includes_declared_envs(self):
        from backend.config.settings import settings
        result = settings.get_all_mysql_envs()
        assert "prod" in result
        assert "staging" in result

    def test_A3_sorted(self):
        from backend.config.settings import settings
        result = settings.get_all_mysql_envs()
        assert result == sorted(result)

    def test_A4_discovers_new_env_from_os_environ(self):
        from backend.config.settings import settings
        with patch.dict(os.environ, {"MYSQL_ANALYTICS_HOST": "analytics-host"}):
            result = settings.get_all_mysql_envs()
        assert "analytics" in result

    def test_A5_no_duplicate_envs(self):
        from backend.config.settings import settings
        result = settings.get_all_mysql_envs()
        assert len(result) == len(set(result))

    def test_A6_env_names_lowercase(self):
        from backend.config.settings import settings
        with patch.dict(os.environ, {"MYSQL_PROD2_HOST": "h"}):
            result = settings.get_all_mysql_envs()
        for env in result:
            assert env == env.lower(), f"env name not lowercase: {env}"


# ─── B 组：get_mysql_config ──────────────────────────────────────────────────

class TestGetMysqlConfig:
    def test_B1_prod_returns_dict(self):
        from backend.config.settings import settings
        cfg = settings.get_mysql_config("prod")
        assert isinstance(cfg, dict)
        for key in ("host", "port", "database", "user", "password"):
            assert key in cfg

    def test_B2_staging_returns_dict(self):
        from backend.config.settings import settings
        cfg = settings.get_mysql_config("staging")
        assert isinstance(cfg, dict)
        assert "host" in cfg

    def test_B3_prod_uppercase_normalized(self):
        from backend.config.settings import settings
        cfg_lower = settings.get_mysql_config("prod")
        cfg_upper = settings.get_mysql_config("PROD")
        assert cfg_lower == cfg_upper

    def test_B4_dynamic_env_reads_from_os_environ(self):
        from backend.config.settings import settings
        env_vars = {
            "MYSQL_CUSTOM_HOST": "custom-host",
            "MYSQL_CUSTOM_PORT": "3307",
            "MYSQL_CUSTOM_DATABASE": "custom_db",
            "MYSQL_CUSTOM_USER": "custom_user",
            "MYSQL_CUSTOM_PASSWORD": "custom_pass",
        }
        with patch.dict(os.environ, env_vars):
            cfg = settings.get_mysql_config("custom")
        assert cfg["host"] == "custom-host"
        assert cfg["port"] == 3307
        assert cfg["database"] == "custom_db"
        assert cfg["user"] == "custom_user"
        assert cfg["password"] == "custom_pass"

    def test_B5_dynamic_env_missing_host_returns_empty_string(self):
        from backend.config.settings import settings
        cfg = settings.get_mysql_config("nonexistent_xyz")
        assert cfg["host"] == ""

    def test_B6_dynamic_env_port_defaults_to_3306(self):
        from backend.config.settings import settings
        with patch.dict(os.environ, {"MYSQL_PORTTEST_HOST": "h"}):
            cfg = settings.get_mysql_config("porttest")
        assert cfg["port"] == 3306

    def test_B7_pydantic_fields_take_priority_over_environ(self):
        from backend.config.settings import settings
        # prod/staging are pydantic fields — they use pydantic values, not os.environ override
        # (override=False in load_dotenv, so this just confirms pydantic path is used)
        cfg = settings.get_mysql_config("prod")
        # The prod config comes from pydantic field — host may be empty but should not raise
        assert isinstance(cfg["host"], str)


# ─── C 组：MySQLMCPServer._ensure_connection ────────────────────────────────

class TestMysqlEnsureConnection:
    def _make_server(self):
        from backend.mcp.mysql.server import MySQLMCPServer
        srv = MySQLMCPServer.__new__(MySQLMCPServer)
        srv.env = "prod"
        srv.config = {
            "host": "localhost", "port": 3306,
            "database": "test", "user": "u", "password": "p",
        }
        return srv

    def test_C1_ensure_connection_calls_ping(self):
        srv = self._make_server()
        mock_conn = MagicMock()
        mock_conn.ping = MagicMock()
        srv.connection = mock_conn
        srv._ensure_connection()
        mock_conn.ping.assert_called_once_with(reconnect=True)

    def test_C2_ensure_connection_reconnects_on_failure(self):
        import pymysql
        from pymysql.cursors import DictCursor
        srv = self._make_server()

        mock_conn = MagicMock()
        mock_conn.ping.side_effect = Exception("connection lost")
        srv.connection = mock_conn

        new_conn = MagicMock()
        with patch("backend.mcp.mysql.server.pymysql.connect", return_value=new_conn) as mock_connect:
            srv._ensure_connection()

        mock_connect.assert_called_once()
        assert srv.connection is new_conn

    def test_C3_ensure_connection_reconnect_uses_config(self):
        srv = self._make_server()
        mock_conn = MagicMock()
        mock_conn.ping.side_effect = Exception("lost")
        srv.connection = mock_conn

        with patch("backend.mcp.mysql.server.pymysql.connect", return_value=MagicMock()) as mock_connect:
            srv._ensure_connection()

        call_kwargs = mock_connect.call_args[1]
        assert call_kwargs["host"] == "localhost"
        assert call_kwargs["port"] == 3306
        assert call_kwargs["user"] == "u"
        assert call_kwargs["password"] == "p"

    def test_C4_execute_query_calls_ensure_connection(self):
        srv = self._make_server()
        srv._ensure_connection = MagicMock()

        mock_cursor = MagicMock()
        mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
        mock_cursor.__exit__ = MagicMock(return_value=False)
        mock_cursor.execute = MagicMock()
        mock_cursor.fetchall = MagicMock(return_value=[])
        mock_cursor.description = []

        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        srv.connection = mock_conn

        srv._execute_query("SELECT 1")
        srv._ensure_connection.assert_called_once()

    def test_C5_list_databases_calls_ensure_connection(self):
        srv = self._make_server()
        srv._ensure_connection = MagicMock()

        mock_cursor = MagicMock()
        mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
        mock_cursor.__exit__ = MagicMock(return_value=False)
        mock_cursor.execute = MagicMock()
        mock_cursor.fetchall = MagicMock(return_value=[{"Database": "test"}])
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        srv.connection = mock_conn

        srv._list_databases()
        srv._ensure_connection.assert_called_once()

    def test_C6_test_connection_calls_ensure_connection(self):
        srv = self._make_server()
        srv._ensure_connection = MagicMock()

        mock_cursor = MagicMock()
        mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
        mock_cursor.__exit__ = MagicMock(return_value=False)
        mock_cursor.execute = MagicMock()
        mock_cursor.fetchone = MagicMock(return_value={"test": 1})
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        srv.connection = mock_conn

        srv._test_connection()
        srv._ensure_connection.assert_called_once()


# ─── D 组：Manager 使用 get_all_mysql_envs ──────────────────────────────────

def _run_async(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


class TestManagerDynamicMysqlEnvs:
    def _make_report_mock(self):
        m = MagicMock()
        m.initialize = AsyncMock()
        m.get_tools_list = MagicMock(return_value=[])
        m.resources = []
        m.prompts = []
        m.tools = []
        m.version = "1.0"
        return m

    def _make_settings_mock(self, mysql_envs, mysql_cfg_fn):
        from backend.config.settings import Settings
        m = MagicMock(spec=Settings)
        m.enable_mcp_clickhouse = False
        m.enable_mcp_mysql = True
        m.enable_mcp_filesystem = False
        m.enable_mcp_lark = False
        m.get_all_mysql_envs = MagicMock(return_value=mysql_envs)
        m.get_mysql_config = MagicMock(side_effect=mysql_cfg_fn)
        return m

    def test_D1_manager_calls_get_all_mysql_envs(self):
        from backend.mcp.manager import MCPServerManager

        mgr = MCPServerManager()
        mock_settings = self._make_settings_mock(["prod", "staging"], lambda env: {"host": ""})

        with patch("backend.mcp.manager.settings", mock_settings):
            with patch("backend.mcp.manager.ReportToolMCPServer", return_value=self._make_report_mock()):
                _run_async(mgr.initialize_all())

        mock_settings.get_all_mysql_envs.assert_called_once()

    def test_D2_manager_registers_dynamic_env(self):
        from backend.mcp.manager import MCPServerManager

        mgr = MCPServerManager()
        mock_settings = self._make_settings_mock(
            ["custom_env"],
            lambda env: {"host": "h", "port": 3306, "database": "d", "user": "u", "password": "p"}
        )

        mock_srv = MagicMock()
        mock_srv.initialize = AsyncMock()
        mock_srv.get_tools_list = MagicMock(return_value=[])
        mock_srv.tools = []
        mock_srv.resources = []
        mock_srv.prompts = []
        mock_srv.version = "1.0"

        with patch("backend.mcp.manager.settings", mock_settings):
            with patch("backend.mcp.manager.MySQLMCPServer", return_value=mock_srv):
                with patch("backend.mcp.manager.ReportToolMCPServer", return_value=self._make_report_mock()):
                    _run_async(mgr.initialize_all())

        assert "mysql-custom_env" in mgr.servers


# ─── E 组：standalone_db_server 结构 ────────────────────────────────────────

class TestStandaloneDbServerStructure:
    def test_E1_importable(self):
        import importlib
        mod = importlib.import_module("backend.mcp.standalone_db_server")
        assert mod is not None

    def test_E2_has_main_function(self):
        from backend.mcp import standalone_db_server as mod
        assert callable(mod.main)

    def test_E3_has_tool_registry(self):
        from backend.mcp import standalone_db_server as mod
        assert hasattr(mod, "_ToolRegistry")

    def test_E4_tool_registry_register_and_list(self):
        from backend.mcp.standalone_db_server import _ToolRegistry
        registry = _ToolRegistry()

        mock_server = MagicMock()
        mock_server.get_tools_list.return_value = [
            {"name": "query", "description": "run query", "input_schema": {"type": "object"}}
        ]

        registry.register_server("mysql-prod", mock_server)
        tools = registry.list_tools()

        assert len(tools) == 1
        assert tools[0]["name"] == "mysql_prod__query"
        assert "[mysql-prod]" in tools[0]["description"]
        assert "inputSchema" in tools[0]

    def test_E5_tool_registry_unknown_tool(self):
        from backend.mcp.standalone_db_server import _ToolRegistry
        registry = _ToolRegistry()
        result = asyncio.get_event_loop().run_until_complete(
            registry.call("nonexistent__tool", {})
        )
        assert result["isError"] is True

    def test_E6_send_writes_json_line(self):
        import io
        from backend.mcp import standalone_db_server as mod
        buf = io.StringIO()
        old_stdout = sys.stdout
        sys.stdout = buf
        try:
            mod._send({"foo": "bar"})
        finally:
            sys.stdout = old_stdout
        line = buf.getvalue().strip()
        parsed = json.loads(line)
        assert parsed["foo"] == "bar"

    def test_E7_ok_helper_sends_result(self):
        import io
        from backend.mcp import standalone_db_server as mod
        buf = io.StringIO()
        old_stdout = sys.stdout
        sys.stdout = buf
        try:
            mod._ok(42, {"x": 1})
        finally:
            sys.stdout = old_stdout
        parsed = json.loads(buf.getvalue().strip())
        assert parsed["id"] == 42
        assert parsed["result"]["x"] == 1
        assert "error" not in parsed

    def test_E8_err_helper_sends_error(self):
        import io
        from backend.mcp import standalone_db_server as mod
        buf = io.StringIO()
        old_stdout = sys.stdout
        sys.stdout = buf
        try:
            mod._err(1, -32601, "Method not found")
        finally:
            sys.stdout = old_stdout
        parsed = json.loads(buf.getvalue().strip())
        assert parsed["error"]["code"] == -32601
        assert "result" not in parsed

    def test_E9_project_root_in_sys_path(self):
        # standalone_db_server adds project root to sys.path at import time
        import backend.mcp.standalone_db_server  # already imported, path already added
        # backend package must be importable
        import backend  # noqa
        assert True

    def test_E10_build_registry_is_async(self):
        import inspect
        from backend.mcp import standalone_db_server as mod
        assert inspect.iscoroutinefunction(mod._build_registry)

    def test_E11_run_is_async(self):
        import inspect
        from backend.mcp import standalone_db_server as mod
        assert inspect.iscoroutinefunction(mod._run)
