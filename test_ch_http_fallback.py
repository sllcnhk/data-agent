"""
test_ch_http_fallback.py
========================
ClickHouse TCP→HTTP 自动回退功能 完整测试与回归

测试结构：
  Section A  ClickHouseHTTPClient — _is_select_like() 判断
  Section B  ClickHouseHTTPClient — FORMAT 追加/跳过逻辑
  Section C  ClickHouseHTTPClient — execute() 正常路径（mock requests）
  Section D  ClickHouseHTTPClient — 错误处理（连接拒绝/超时/HTTP 错误）
  Section E  ClickHouseHTTPClient — 请求结构（URL/Body/Params）
  Section F  ClickHouseMCPServer  — initialize() TCP 成功路径
  Section G  ClickHouseMCPServer  — initialize() TCP 失败→HTTP 回退
  Section H  ClickHouseMCPServer  — TCP+HTTP 均失败的错误传播
  Section I  ClickHouseMCPServer  — _test_connection() protocol 字段增强
  Section J  tool_formatter       — server 名往返一致性（sg_azure 下划线场景）
  Section K  端到端流程           — TCP 失败→HTTP→工具调用全链路
  Section L  RBAC 回归            — 新功能无新菜单/端点，不影响现有权限

运行方式：
    /d/ProgramData/Anaconda3/envs/dataagent/python.exe -X utf8 test_ch_http_fallback.py
"""

import asyncio
import json
import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

_PROJECT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _PROJECT)
sys.path.insert(0, os.path.join(_PROJECT, "backend"))

os.environ.setdefault("POSTGRES_PASSWORD", "Sgp013013")
os.environ.setdefault("POSTGRES_HOST", "localhost")
os.environ.setdefault("ENABLE_AUTH", "False")

# ─────────────────────────────────────────────────────────────────────
# 辅助工具
# ─────────────────────────────────────────────────────────────────────

def _jc(meta, data):
    """构造 ClickHouse HTTP JSONCompact 响应体 (str)"""
    return json.dumps({
        "meta": [{"name": n, "type": t} for n, t in meta],
        "data": data,
        "rows": len(data),
    })


def _mock_resp(body: str, status: int = 200):
    r = MagicMock()
    r.status_code = status
    r.text = body
    r.json.return_value = json.loads(body) if body.strip().startswith("{") else {}
    return r


_MOCK_CFG = {
    "host": "127.0.0.1",
    "port": 9000,
    "http_port": 8123,
    "user": "default",
    "password": "secret",
    "database": "testdb",
    "level": "admin",
}


# ═════════════════════════════════════════════════════════════════════
# Section A  _is_select_like()
# ═════════════════════════════════════════════════════════════════════

class TestA_SelectLike(unittest.TestCase):
    """A — ClickHouseHTTPClient._is_select_like() 边界覆盖"""

    def setUp(self):
        from backend.mcp.clickhouse.http_client import ClickHouseHTTPClient
        self.c = ClickHouseHTTPClient("h", 8123, "u", "p", "db")

    def test_A1_select_true(self):
        self.assertTrue(self.c._is_select_like("SELECT 1"))

    def test_A2_select_leading_space(self):
        self.assertTrue(self.c._is_select_like("  SELECT count(*) FROM t"))

    def test_A3_lowercase_select(self):
        self.assertTrue(self.c._is_select_like("select * from t"))

    def test_A4_show_databases(self):
        self.assertTrue(self.c._is_select_like("SHOW DATABASES"))

    def test_A5_show_tables(self):
        self.assertTrue(self.c._is_select_like("SHOW TABLES FROM db"))

    def test_A6_describe(self):
        self.assertTrue(self.c._is_select_like("DESCRIBE db.t"))

    def test_A7_desc_alias(self):
        self.assertTrue(self.c._is_select_like("DESC db.t"))

    def test_A8_with_cte(self):
        self.assertTrue(self.c._is_select_like("WITH cte AS (SELECT 1) SELECT * FROM cte"))

    def test_A9_explain(self):
        self.assertTrue(self.c._is_select_like("EXPLAIN SELECT 1"))

    def test_A10_set_not_select(self):
        self.assertFalse(self.c._is_select_like("SET max_rows=100"))

    def test_A11_insert_not_select(self):
        self.assertFalse(self.c._is_select_like("INSERT INTO t VALUES (1)"))

    def test_A12_use_not_select(self):
        self.assertFalse(self.c._is_select_like("USE mydb"))


# ═════════════════════════════════════════════════════════════════════
# Section B  FORMAT 追加逻辑
# ═════════════════════════════════════════════════════════════════════

class TestB_FormatAppend(unittest.TestCase):
    """B — _build_exec_query() FORMAT JSONCompact 追加逻辑"""

    def setUp(self):
        from backend.mcp.clickhouse.http_client import ClickHouseHTTPClient
        self.c = ClickHouseHTTPClient("h", 8123, "u", "p", "db")

    def _build(self, q):
        return self.c._build_exec_query(q, self.c._is_select_like(q))

    def test_B1_select_appends_format(self):
        self.assertEqual(self._build("SELECT 1"), "SELECT 1 FORMAT JSONCompact")

    def test_B2_trailing_semicolon_stripped(self):
        self.assertEqual(self._build("SELECT 1;"), "SELECT 1 FORMAT JSONCompact")

    def test_B3_existing_format_not_doubled(self):
        self.assertEqual(self._build("SELECT 1 FORMAT CSV"), "SELECT 1 FORMAT CSV")

    def test_B4_show_databases_appends(self):
        self.assertEqual(self._build("SHOW DATABASES"), "SHOW DATABASES FORMAT JSONCompact")

    def test_B5_describe_appends(self):
        self.assertEqual(self._build("DESCRIBE db.t"), "DESCRIBE db.t FORMAT JSONCompact")

    def test_B6_non_select_unchanged(self):
        self.assertEqual(self._build("SET x=1"), "SET x=1")

    def test_B7_insert_unchanged(self):
        self.assertEqual(self._build("INSERT INTO t VALUES (1)"), "INSERT INTO t VALUES (1)")

    def test_B8_jsoncompact_case_insensitive_not_doubled(self):
        self.assertEqual(self._build("SELECT 1 format jsoncompact"), "SELECT 1 format jsoncompact")


# ═════════════════════════════════════════════════════════════════════
# Section C  execute() 正常路径
# ═════════════════════════════════════════════════════════════════════

class TestC_ExecuteNormal(unittest.TestCase):
    """C — execute() 正常路径（mock requests.Session.post）"""

    def setUp(self):
        from backend.mcp.clickhouse.http_client import ClickHouseHTTPClient
        self.c = ClickHouseHTTPClient("host", 8123, "user", "pass", "mydb")

    def _patch(self, body, status=200):
        return patch.object(self.c._session, "post", return_value=_mock_resp(body, status))

    def test_C1_select_returns_rows(self):
        body = _jc([("c", "UInt32")], [[1], [2], [3]])
        with self._patch(body):
            result = self.c.execute("SELECT c FROM t")
        self.assertEqual(result, [(1,), (2,), (3,)])

    def test_C2_with_column_types_true(self):
        body = _jc([("id", "UInt32"), ("name", "String")], [[1, "alice"]])
        with self._patch(body):
            rows, col_types = self.c.execute("SELECT id, name FROM t", with_column_types=True)
        self.assertEqual(rows, [(1, "alice")])
        self.assertEqual(col_types, [("id", "UInt32"), ("name", "String")])

    def test_C3_empty_select_result(self):
        body = _jc([("col", "String")], [])
        with self._patch(body):
            rows, col_types = self.c.execute("SELECT * FROM empty_t", with_column_types=True)
        self.assertEqual(rows, [])
        self.assertEqual(col_types, [("col", "String")])

    def test_C4_non_select_returns_empty_list(self):
        with self._patch(""):
            result = self.c.execute("SET max_rows=100")
        self.assertEqual(result, [])

    def test_C5_non_select_with_column_types(self):
        with self._patch(""):
            result = self.c.execute("SET x=1", with_column_types=True)
        self.assertEqual(result, ([], []))

    def test_C6_show_databases_row_access(self):
        """验证 row[0] 访问方式与 clickhouse-driver 一致"""
        body = _jc([("name", "String")], [["default"], ["system"]])
        with self._patch(body):
            result = self.c.execute("SHOW DATABASES")
        dbs = [row[0] for row in result]
        self.assertEqual(dbs, ["default", "system"])

    def test_C7_auth_params_injected(self):
        body = _jc([("1", "UInt8")], [[1]])
        with patch.object(self.c._session, "post", return_value=_mock_resp(body)) as mp:
            self.c.execute("SELECT 1")
        params = mp.call_args.kwargs.get("params") or mp.call_args[1]["params"]
        self.assertEqual(params["user"], "user")
        self.assertEqual(params["password"], "pass")
        self.assertEqual(params["database"], "mydb")

    def test_C8_settings_passed_as_http_params(self):
        body = _jc([("c", "UInt8")], [[42]])
        with patch.object(self.c._session, "post", return_value=_mock_resp(body)) as mp:
            self.c.execute("SELECT c FROM t",
                           settings={"max_result_rows": 100, "result_overflow_mode": "break"})
        params = mp.call_args.kwargs.get("params") or mp.call_args[1]["params"]
        self.assertEqual(params["max_result_rows"], 100)
        self.assertEqual(params["result_overflow_mode"], "break")

    def test_C9_describe_table_rows_parsed(self):
        body = _jc(
            [("name", "String"), ("type", "String"), ("default_type", "String"),
             ("default_expression", "String"), ("comment", "String")],
            [["id", "UInt32", "", "", "主键"], ["name", "String", "", "", ""]]
        )
        with self._patch(body):
            result = self.c.execute("DESCRIBE testdb.users")
        # 与 clickhouse-driver 一样：list of tuples
        self.assertEqual(result[0][0], "id")
        self.assertEqual(result[0][1], "UInt32")


# ═════════════════════════════════════════════════════════════════════
# Section D  错误处理
# ═════════════════════════════════════════════════════════════════════

class TestD_ErrorHandling(unittest.TestCase):
    """D — execute() 各类错误场景"""

    def setUp(self):
        from backend.mcp.clickhouse.http_client import ClickHouseHTTPClient
        self.c = ClickHouseHTTPClient("host", 8123, "u", "p", "db")

    def test_D1_connection_error_raises(self):
        import requests as _req
        with patch.object(self.c._session, "post", side_effect=_req.ConnectionError("refused")):
            with self.assertRaises(ConnectionError) as ctx:
                self.c.execute("SELECT 1")
        self.assertIn("8123", str(ctx.exception))

    def test_D2_timeout_raises(self):
        import requests as _req
        with patch.object(self.c._session, "post", side_effect=_req.Timeout()):
            with self.assertRaises(TimeoutError):
                self.c.execute("SELECT 1")

    def test_D3_http_500_raises_runtime(self):
        with patch.object(self.c._session, "post", return_value=_mock_resp("DB Error", 500)):
            with self.assertRaises(RuntimeError) as ctx:
                self.c.execute("SELECT 1")
        self.assertIn("500", str(ctx.exception))

    def test_D4_http_401_raises_runtime(self):
        with patch.object(self.c._session, "post",
                          return_value=_mock_resp("Authentication failed", 401)):
            with self.assertRaises(RuntimeError) as ctx:
                self.c.execute("SELECT 1")
        self.assertIn("401", str(ctx.exception))

    def test_D5_error_includes_host_port(self):
        import requests as _req
        with patch.object(self.c._session, "post", side_effect=_req.ConnectionError("x")):
            with self.assertRaises(ConnectionError) as ctx:
                self.c.execute("SELECT 1")
        msg = str(ctx.exception)
        self.assertIn("host", msg)
        self.assertIn("8123", msg)


# ═════════════════════════════════════════════════════════════════════
# Section E  请求结构
# ═════════════════════════════════════════════════════════════════════

class TestE_RequestStructure(unittest.TestCase):
    """E — HTTP 请求 URL / Body / Params 结构验证"""

    def setUp(self):
        from backend.mcp.clickhouse.http_client import ClickHouseHTTPClient
        self.c = ClickHouseHTTPClient("myhost", 8123, "u", "p", "db")

    def test_E1_base_url_correct(self):
        self.assertEqual(self.c._base_url, "http://myhost:8123/")

    def test_E2_body_contains_format_jsoncompact(self):
        body = _jc([("1", "UInt8")], [[1]])
        with patch.object(self.c._session, "post", return_value=_mock_resp(body)) as mp:
            self.c.execute("SELECT 1")
        data = mp.call_args.kwargs.get("data") or mp.call_args[1]["data"]
        self.assertIn(b"FORMAT JSONCompact", data)

    def test_E3_format_not_doubled_in_body(self):
        body = _jc([("1", "UInt8")], [[1]])
        with patch.object(self.c._session, "post", return_value=_mock_resp(body)) as mp:
            self.c.execute("SELECT 1 FORMAT JSONCompact")
        data = mp.call_args.kwargs.get("data") or mp.call_args[1]["data"]
        self.assertEqual(data.decode().count("FORMAT"), 1)

    def test_E4_post_to_correct_url(self):
        body = _jc([("1", "UInt8")], [[1]])
        with patch.object(self.c._session, "post", return_value=_mock_resp(body)) as mp:
            self.c.execute("SELECT 1")
        url = mp.call_args.args[0] if mp.call_args.args else mp.call_args[0][0]
        self.assertEqual(url, "http://myhost:8123/")

    def test_E5_body_is_bytes(self):
        body = _jc([("1", "UInt8")], [[1]])
        with patch.object(self.c._session, "post", return_value=_mock_resp(body)) as mp:
            self.c.execute("SELECT 1")
        data = mp.call_args.kwargs.get("data") or mp.call_args[1]["data"]
        self.assertIsInstance(data, bytes)


# ═════════════════════════════════════════════════════════════════════
# Section F  Server.initialize() — TCP 成功路径
# ═════════════════════════════════════════════════════════════════════

class TestF_InitTCPSuccess(unittest.IsolatedAsyncioTestCase):
    """F — TCP 连接成功时 server 状态"""

    async def test_F1_protocol_is_native(self):
        from backend.mcp.clickhouse.server import ClickHouseMCPServer
        s = ClickHouseMCPServer(env="idn")
        with patch("backend.mcp.clickhouse.server.settings") as ms, \
             patch("backend.mcp.clickhouse.server.ClickHouseClient") as MockTCP:
            ms.get_clickhouse_config.return_value = _MOCK_CFG.copy()
            tcp = MagicMock()
            tcp.execute.return_value = [(1,)]
            MockTCP.return_value = tcp
            await s.initialize()
        self.assertEqual(s._protocol, "native")
        self.assertIs(s.client, tcp)

    async def test_F2_connect_timeout_5s(self):
        """TCP 探测强制使用 connect_timeout=5，避免阻塞启动"""
        from backend.mcp.clickhouse.server import ClickHouseMCPServer
        s = ClickHouseMCPServer(env="idn")
        with patch("backend.mcp.clickhouse.server.settings") as ms, \
             patch("backend.mcp.clickhouse.server.ClickHouseClient") as MockTCP:
            ms.get_clickhouse_config.return_value = _MOCK_CFG.copy()
            tcp = MagicMock(); tcp.execute.return_value = [(1,)]
            MockTCP.return_value = tcp
            await s.initialize()
        kw = MockTCP.call_args.kwargs
        self.assertEqual(kw.get("connect_timeout"), 5)
        self.assertEqual(kw.get("send_receive_timeout"), 10)

    async def test_F3_tools_registered_after_tcp_success(self):
        from backend.mcp.clickhouse.server import ClickHouseMCPServer
        s = ClickHouseMCPServer(env="idn")
        with patch("backend.mcp.clickhouse.server.settings") as ms, \
             patch("backend.mcp.clickhouse.server.ClickHouseClient") as MockTCP:
            ms.get_clickhouse_config.return_value = _MOCK_CFG.copy()
            tcp = MagicMock(); tcp.execute.return_value = [(1,)]
            MockTCP.return_value = tcp
            await s.initialize()
        for expected in ("query", "list_databases", "describe_table",
                         "batch_describe_tables", "test_connection"):
            self.assertIn(expected, s.tools, f"Missing tool: {expected}")

    async def test_F4_http_client_not_instantiated_on_tcp_success(self):
        """TCP 成功时不应创建 HTTP client"""
        from backend.mcp.clickhouse.server import ClickHouseMCPServer
        s = ClickHouseMCPServer(env="idn")
        with patch("backend.mcp.clickhouse.server.settings") as ms, \
             patch("backend.mcp.clickhouse.server.ClickHouseClient") as MockTCP, \
             patch("backend.mcp.clickhouse.server.ClickHouseHTTPClient") as MockHTTP:
            ms.get_clickhouse_config.return_value = _MOCK_CFG.copy()
            tcp = MagicMock(); tcp.execute.return_value = [(1,)]
            MockTCP.return_value = tcp
            await s.initialize()
        MockHTTP.assert_not_called()


# ═════════════════════════════════════════════════════════════════════
# Section G  Server.initialize() — TCP 失败→HTTP 回退
# ═════════════════════════════════════════════════════════════════════

class TestG_InitHTTPFallback(unittest.IsolatedAsyncioTestCase):
    """G — TCP 各类失败均触发 HTTP 回退"""

    async def _run_with_tcp_err(self, tcp_err):
        from backend.mcp.clickhouse.server import ClickHouseMCPServer
        s = ClickHouseMCPServer(env="sg_azure")
        with patch("backend.mcp.clickhouse.server.settings") as ms, \
             patch("backend.mcp.clickhouse.server.ClickHouseClient") as MockTCP, \
             patch("backend.mcp.clickhouse.server.ClickHouseHTTPClient") as MockHTTP:
            ms.get_clickhouse_config.return_value = _MOCK_CFG.copy()
            tcp = MagicMock(); tcp.execute.side_effect = tcp_err
            MockTCP.return_value = tcp
            http = MagicMock(); http.execute.return_value = [(1,)]
            MockHTTP.return_value = http
            await s.initialize()
        return s, MockHTTP

    async def test_G1_connection_refused_triggers_fallback(self):
        s, MockHTTP = await self._run_with_tcp_err(ConnectionRefusedError("9000"))
        self.assertEqual(s._protocol, "http")

    async def test_G2_socket_timeout_triggers_fallback(self):
        import socket
        s, _ = await self._run_with_tcp_err(socket.timeout("timed out"))
        self.assertEqual(s._protocol, "http")

    async def test_G3_generic_exception_triggers_fallback(self):
        s, _ = await self._run_with_tcp_err(Exception("network error"))
        self.assertEqual(s._protocol, "http")

    async def test_G4_http_client_gets_http_port(self):
        """HTTP 回退使用 http_port=8123 而非 tcp_port=9000"""
        _, MockHTTP = await self._run_with_tcp_err(ConnectionRefusedError("9000"))
        kw = MockHTTP.call_args.kwargs
        self.assertEqual(kw.get("port"), 8123)

    async def test_G5_http_client_gets_correct_credentials(self):
        _, MockHTTP = await self._run_with_tcp_err(Exception("TCP down"))
        kw = MockHTTP.call_args.kwargs
        self.assertEqual(kw.get("host"), "127.0.0.1")
        self.assertEqual(kw.get("user"), "default")
        self.assertEqual(kw.get("password"), "secret")
        self.assertEqual(kw.get("database"), "testdb")

    async def test_G6_tools_registered_after_http_fallback(self):
        s, _ = await self._run_with_tcp_err(ConnectionRefusedError("9000"))
        for expected in ("query", "list_databases", "test_connection"):
            self.assertIn(expected, s.tools)

    async def test_G7_default_protocol_before_init_is_native(self):
        from backend.mcp.clickhouse.server import ClickHouseMCPServer
        s = ClickHouseMCPServer(env="idn")
        self.assertEqual(s._protocol, "native")

    async def test_G8_http_probe_executes_select1(self):
        """HTTP 回退时也执行 SELECT 1 探测，验证 HTTP 可用"""
        from backend.mcp.clickhouse.server import ClickHouseMCPServer
        s = ClickHouseMCPServer(env="sg_azure")
        with patch("backend.mcp.clickhouse.server.settings") as ms, \
             patch("backend.mcp.clickhouse.server.ClickHouseClient") as MockTCP, \
             patch("backend.mcp.clickhouse.server.ClickHouseHTTPClient") as MockHTTP:
            ms.get_clickhouse_config.return_value = _MOCK_CFG.copy()
            tcp = MagicMock(); tcp.execute.side_effect = Exception("TCP down")
            MockTCP.return_value = tcp
            http = MagicMock(); http.execute.return_value = [(1,)]
            MockHTTP.return_value = http
            await s.initialize()
        # HTTP probe 被调用（SELECT 1）
        http.execute.assert_called_once_with("SELECT 1")


# ═════════════════════════════════════════════════════════════════════
# Section H  TCP+HTTP 均失败
# ═════════════════════════════════════════════════════════════════════

class TestH_BothFail(unittest.IsolatedAsyncioTestCase):
    """H — TCP 和 HTTP 均失败时的错误传播"""

    async def _both_fail(self):
        from backend.mcp.clickhouse.server import ClickHouseMCPServer
        s = ClickHouseMCPServer(env="sg_azure")
        with patch("backend.mcp.clickhouse.server.settings") as ms, \
             patch("backend.mcp.clickhouse.server.ClickHouseClient") as MockTCP, \
             patch("backend.mcp.clickhouse.server.ClickHouseHTTPClient") as MockHTTP:
            ms.get_clickhouse_config.return_value = _MOCK_CFG.copy()
            tcp = MagicMock(); tcp.execute.side_effect = ConnectionRefusedError("9000")
            MockTCP.return_value = tcp
            http = MagicMock(); http.execute.side_effect = ConnectionError("8123 down")
            MockHTTP.return_value = http
            try:
                await s.initialize()
            except Exception:
                pass
        return s

    async def test_H1_both_fail_raises(self):
        from backend.mcp.clickhouse.server import ClickHouseMCPServer
        s = ClickHouseMCPServer(env="sg_azure")
        with patch("backend.mcp.clickhouse.server.settings") as ms, \
             patch("backend.mcp.clickhouse.server.ClickHouseClient") as MockTCP, \
             patch("backend.mcp.clickhouse.server.ClickHouseHTTPClient") as MockHTTP:
            ms.get_clickhouse_config.return_value = _MOCK_CFG.copy()
            tcp = MagicMock(); tcp.execute.side_effect = ConnectionRefusedError("9000")
            MockTCP.return_value = tcp
            http = MagicMock(); http.execute.side_effect = ConnectionError("8123 down")
            MockHTTP.return_value = http
            with self.assertRaises(Exception):
                await s.initialize()

    async def test_H2_client_remains_none(self):
        """两者均失败时 self.client 不被赋值，保持 None"""
        s = await self._both_fail()
        self.assertIsNone(s.client)

    async def test_H3_manager_skips_registration_on_error(self):
        """manager.initialize_all() 捕获异常并跳过注册，不崩溃"""
        from backend.mcp.manager import MCPServerManager
        mgr = MCPServerManager()
        with patch("backend.mcp.manager.settings") as ms, \
             patch("backend.mcp.manager.ClickHouseMCPServer") as MockServer:
            ms.enable_mcp_clickhouse = True
            ms.enable_mcp_mysql = False
            ms.enable_mcp_filesystem = False
            ms.enable_mcp_lark = False
            ms.get_all_clickhouse_envs.return_value = ["sg_azure"]
            ms.get_clickhouse_config.return_value = _MOCK_CFG.copy()
            ms.has_readonly_credentials.return_value = False
            inst = MagicMock()
            inst.initialize = AsyncMock(side_effect=ConnectionError("both fail"))
            MockServer.return_value = inst
            await mgr.initialize_all()  # 不应抛出
        # 服务器未注册
        self.assertNotIn("clickhouse-sg-azure", mgr.servers)


# ═════════════════════════════════════════════════════════════════════
# Section I  _test_connection() protocol 字段
# ═════════════════════════════════════════════════════════════════════

class TestI_TestConnectionProtocol(unittest.IsolatedAsyncioTestCase):
    """I — _test_connection() 返回 protocol 字段"""

    async def _make_server(self, protocol: str) -> object:
        from backend.mcp.clickhouse.server import ClickHouseMCPServer
        s = ClickHouseMCPServer(env="idn")
        s.config = _MOCK_CFG.copy()
        s._protocol = protocol
        s.client = MagicMock()
        s.client.execute.return_value = [(1,)]
        return s

    async def test_I1_native_protocol_reported(self):
        s = await self._make_server("native")
        r = await s._test_connection()
        self.assertEqual(r["protocol"], "native")
        self.assertEqual(r["status"], "success")

    async def test_I2_http_protocol_reported(self):
        s = await self._make_server("http")
        r = await s._test_connection()
        self.assertEqual(r["protocol"], "http")
        self.assertEqual(r["status"], "success")

    async def test_I3_native_reports_tcp_port(self):
        s = await self._make_server("native")
        r = await s._test_connection()
        self.assertEqual(r["port"], 9000)

    async def test_I4_http_reports_http_port(self):
        s = await self._make_server("http")
        r = await s._test_connection()
        self.assertEqual(r["port"], 8123)

    async def test_I5_failed_reports_protocol_and_http_port(self):
        from backend.mcp.clickhouse.server import ClickHouseMCPServer
        s = ClickHouseMCPServer(env="idn")
        s.config = _MOCK_CFG.copy()
        s._protocol = "http"
        s.client = MagicMock()
        s.client.execute.side_effect = Exception("query failed")
        r = await s._test_connection()
        self.assertEqual(r["status"], "failed")
        self.assertEqual(r["protocol"], "http")
        self.assertIn("http_port", r)


# ═════════════════════════════════════════════════════════════════════
# Section J  tool_formatter 往返一致性
# ═════════════════════════════════════════════════════════════════════

class TestJ_ToolFormatterRoundTrip(unittest.TestCase):
    """J — server 名含下划线时 format→parse 往返正确"""

    def setUp(self):
        from backend.mcp.tool_formatter import parse_tool_name
        self.parse = parse_tool_name

    def _trip(self, server_name, tool_name="execute_query"):
        prefix = server_name.replace("-", "_")
        namespaced = f"{prefix}__{tool_name}"
        return self.parse(namespaced)

    def test_J1_sg_azure_roundtrip(self):
        """clickhouse-sg-azure 往返不变（注册名用纯连字符）"""
        s, t = self._trip("clickhouse-sg-azure")
        self.assertEqual(s, "clickhouse-sg-azure")
        self.assertEqual(t, "execute_query")

    def test_J2_idn_roundtrip(self):
        s, t = self._trip("clickhouse-idn", "query")
        self.assertEqual(s, "clickhouse-idn")
        self.assertEqual(t, "query")

    def test_J3_sg_roundtrip(self):
        s, _ = self._trip("clickhouse-sg")
        self.assertEqual(s, "clickhouse-sg")

    def test_J4_filesystem_roundtrip(self):
        s, t = self._trip("filesystem", "read_file")
        self.assertEqual(s, "filesystem")
        self.assertEqual(t, "read_file")

    async def _create_server_async(self, env):
        from backend.mcp.manager import MCPServerManager
        mgr = MCPServerManager()
        with patch("backend.mcp.manager.ClickHouseMCPServer") as MockServer:
            inst = MagicMock()
            inst.tools = {}; inst.resources = {}
            inst.version = "1.0.0"; inst.prompts = {}
            inst.initialize = AsyncMock()
            MockServer.return_value = inst
            await mgr.create_clickhouse_server(env=env, level="admin")
        return mgr

    def test_J5_manager_registers_hyphenated_name_for_sg_azure(self):
        """manager.create_clickhouse_server(env='sg_azure') → 'clickhouse-sg-azure'"""
        mgr = asyncio.run(self._create_server_async("sg_azure"))
        self.assertIn("clickhouse-sg-azure", mgr.servers)
        self.assertNotIn("clickhouse-sg_azure", mgr.servers)

    def test_J6_manager_registers_plain_env_correctly(self):
        """无下划线的 env（'idn'）不受影响"""
        mgr = asyncio.run(self._create_server_async("idn"))
        self.assertIn("clickhouse-idn", mgr.servers)


# ═════════════════════════════════════════════════════════════════════
# Section K  端到端流程
# ═════════════════════════════════════════════════════════════════════

class TestK_EndToEnd(unittest.IsolatedAsyncioTestCase):
    """K — TCP 失败→HTTP 回退→工具调用全链路"""

    async def _server_with_http(self, http_body: str):
        """构建 TCP 失败、HTTP 成功的真实 ClickHouseMCPServer（mocked requests）"""
        from backend.mcp.clickhouse.server import ClickHouseMCPServer
        from backend.mcp.clickhouse.http_client import ClickHouseHTTPClient
        s = ClickHouseMCPServer(env="sg_azure")
        with patch("backend.mcp.clickhouse.server.settings") as ms, \
             patch("backend.mcp.clickhouse.server.ClickHouseClient") as MockTCP, \
             patch("backend.mcp.clickhouse.server.ClickHouseHTTPClient") as MockHTTP:
            ms.get_clickhouse_config.return_value = _MOCK_CFG.copy()
            tcp = MagicMock()
            tcp.execute.side_effect = ConnectionRefusedError("9000")
            MockTCP.return_value = tcp
            # 使用真实 ClickHouseHTTPClient，但 mock 其 requests.Session.post
            real_http = ClickHouseHTTPClient("127.0.0.1", 8123, "default", "secret", "testdb")
            real_http._session.post = MagicMock(return_value=_mock_resp(http_body))
            MockHTTP.return_value = real_http
            await s.initialize()
        return s

    async def test_K1_query_tool_via_http(self):
        body = _jc([("id", "UInt32"), ("name", "String")], [[1, "alice"], [2, "bob"]])
        s = await self._server_with_http(body)
        resp = await s.call_tool("query", {"query": "SELECT id, name FROM users"})
        d = resp.to_dict()
        self.assertTrue(d["success"])
        self.assertEqual(d["data"]["type"], "query_result")
        self.assertEqual(d["data"]["row_count"], 2)
        self.assertEqual(d["data"]["rows"][0]["id"], 1)
        self.assertEqual(d["data"]["rows"][0]["name"], "alice")

    async def test_K2_list_databases_via_http(self):
        body = _jc([("name", "String")], [["default"], ["analytics"]])
        s = await self._server_with_http(body)
        resp = await s.call_tool("list_databases", {})
        d = resp.to_dict()
        self.assertTrue(d["success"])
        self.assertEqual(d["data"]["databases"], ["default", "analytics"])

    async def test_K3_test_connection_reports_http(self):
        body = _jc([("test", "UInt8")], [[1]])
        s = await self._server_with_http(body)
        resp = await s.call_tool("test_connection", {})
        d = resp.to_dict()
        self.assertTrue(d["success"])
        self.assertEqual(d["data"]["protocol"], "http")
        self.assertEqual(d["data"]["status"], "success")
        self.assertEqual(d["data"]["port"], 8123)

    async def test_K4_filtered_manager_finds_hyphenated_server(self):
        """manager 注册 clickhouse-sg-azure，FilteredMCPManager 可正确查找"""
        from backend.mcp.manager import MCPServerManager
        from backend.core.agent_mcp_binder import FilteredMCPManager
        mgr = MCPServerManager()
        with patch("backend.mcp.manager.settings") as ms, \
             patch("backend.mcp.manager.ClickHouseMCPServer") as MockServer:
            ms.enable_mcp_clickhouse = True
            ms.enable_mcp_mysql = False
            ms.enable_mcp_filesystem = False
            ms.enable_mcp_lark = False
            ms.get_all_clickhouse_envs.return_value = ["sg_azure"]
            ms.get_clickhouse_config.return_value = _MOCK_CFG.copy()
            ms.has_readonly_credentials.return_value = False
            inst = MagicMock()
            inst.tools = {"query": MagicMock()}
            inst.resources = {}; inst.version = "1.0.0"; inst.prompts = {}
            inst.initialize = AsyncMock()
            MockServer.return_value = inst
            await mgr.initialize_all()
        # 注册名必须是纯连字符
        self.assertIn("clickhouse-sg-azure", mgr.servers)
        # FilteredMCPManager 通过允许集合能查到该服务器
        fm = FilteredMCPManager(
            base=mgr, allowed_servers=frozenset(["clickhouse-sg-azure"])
        )
        self.assertIsNotNone(fm.get_server("clickhouse-sg-azure"))

    async def test_K5_tool_call_blocked_for_wrong_server_name(self):
        """旧名 clickhouse-sg_azure（下划线）在 FilteredMCPManager 中被拒绝"""
        from backend.mcp.manager import MCPServerManager
        from backend.core.agent_mcp_binder import FilteredMCPManager
        mgr = MCPServerManager()
        with patch("backend.mcp.manager.settings") as ms, \
             patch("backend.mcp.manager.ClickHouseMCPServer") as MockServer:
            ms.enable_mcp_clickhouse = True
            ms.enable_mcp_mysql = False
            ms.enable_mcp_filesystem = False
            ms.enable_mcp_lark = False
            ms.get_all_clickhouse_envs.return_value = ["sg_azure"]
            ms.get_clickhouse_config.return_value = _MOCK_CFG.copy()
            ms.has_readonly_credentials.return_value = False
            inst = MagicMock()
            inst.tools = {}; inst.resources = {}
            inst.version = "1.0.0"; inst.prompts = {}
            inst.initialize = AsyncMock()
            MockServer.return_value = inst
            await mgr.initialize_all()
        fm = FilteredMCPManager(
            base=mgr, allowed_servers=frozenset(["clickhouse-sg-azure"])
        )
        # 错误名（下划线）查不到
        self.assertIsNone(fm.get_server("clickhouse-sg_azure"))


# ═════════════════════════════════════════════════════════════════════
# Section L  RBAC 回归 — 无新菜单/端点，不影响现有权限
# ═════════════════════════════════════════════════════════════════════

class TestL_RBACRegression(unittest.TestCase):
    """
    L — HTTP 回退是纯后端基础设施变更，不引入任何新 API 端点或前端菜单。
    本节验证：
      1. http_client.py 无 FastAPI 路由注册
      2. server.py 修改无新路由
      3. 无新 RBAC 权限节点（clickhouse:http 等）
      4. 现有 /mcp/servers 等端点不因此变更
      5. MCPStatus 前端组件无需改动（protocol 字段透传即可）
    """

    def test_L1_http_client_no_fastapi_routes(self):
        """http_client.py 不注册任何 FastAPI 路由"""
        src_path = os.path.join(_PROJECT, "backend", "mcp", "clickhouse", "http_client.py")
        with open(src_path, encoding="utf-8") as f:
            src = f.read()
        self.assertNotIn("@router.", src, "http_client.py 不应包含 FastAPI 路由")
        self.assertNotIn("APIRouter", src)
        self.assertNotIn("@app.", src)

    def test_L2_server_no_new_routes(self):
        """server.py 修改不向前端暴露新路由"""
        src_path = os.path.join(_PROJECT, "backend", "mcp", "clickhouse", "server.py")
        with open(src_path, encoding="utf-8") as f:
            src = f.read()
        self.assertNotIn("APIRouter", src)
        self.assertNotIn("@router.", src)

    def test_L3_no_new_rbac_permissions_in_init_script(self):
        """init_rbac.py 不包含 clickhouse:http 等新权限节点"""
        rbac_path = os.path.join(_PROJECT, "backend", "scripts", "init_rbac.py")
        if not os.path.exists(rbac_path):
            self.skipTest("init_rbac.py 不存在")
        with open(rbac_path, encoding="utf-8") as f:
            src = f.read()
        self.assertNotIn("clickhouse:http", src)
        self.assertNotIn("mcp:http", src)
        self.assertNotIn("http_fallback", src)

    def test_L4_mcp_api_file_no_protocol_field_hardcoded(self):
        """MCP status API 不硬编码 _protocol 字段（协议字段仅在工具响应中出现）"""
        api_dir = os.path.join(_PROJECT, "backend", "api")
        mcp_files = [f for f in os.listdir(api_dir) if "mcp" in f.lower()]
        for fname in mcp_files:
            with open(os.path.join(api_dir, fname), encoding="utf-8") as f:
                src = f.read()
            self.assertNotIn("_protocol", src,
                             f"{fname} 不应直接引用 _protocol 内部属性")

    def test_L5_http_client_import_no_circular_dep(self):
        """http_client.py 可独立导入，不造成循环依赖"""
        try:
            import importlib
            mod = importlib.import_module("backend.mcp.clickhouse.http_client")
            self.assertTrue(hasattr(mod, "ClickHouseHTTPClient"))
        except ImportError as e:
            self.fail(f"http_client 导入失败（循环依赖或缺少依赖）: {e}")

    def test_L6_server_import_no_circular_dep(self):
        """修改后的 server.py 可正常导入"""
        try:
            import importlib
            mod = importlib.import_module("backend.mcp.clickhouse.server")
            self.assertTrue(hasattr(mod, "ClickHouseMCPServer"))
        except ImportError as e:
            self.fail(f"server.py 导入失败: {e}")

    def test_L7_existing_rbac_tests_file_exists(self):
        """test_rbac.py 回归测试文件存在，可独立执行"""
        rbac_test = os.path.join(_PROJECT, "test_rbac.py")
        self.assertTrue(os.path.exists(rbac_test),
                        "test_rbac.py 应存在以保证 RBAC 回归覆盖")

    def test_L8_frontend_mcpstatus_no_protocol_handling_required(self):
        """
        前端 MCPStatus 组件无需特殊处理 protocol 字段：
        test_connection 工具的 JSON 响应由前端透明显示。
        验证 MCPStatus 相关文件中不存在拦截 http/native 的特殊逻辑。
        """
        candidates = [
            os.path.join(_PROJECT, "frontend", "src", "components", "MCPStatus.tsx"),
            os.path.join(_PROJECT, "frontend", "src", "pages", "MCPStatus.tsx"),
        ]
        for path in candidates:
            if os.path.exists(path):
                with open(path) as f:
                    src = f.read()
                # 前端不需要为 "native" vs "http" 分支处理
                self.assertNotIn('"native"', src,
                                 "MCPStatus 无需硬编码 protocol='native'")
                return  # 找到文件且通过即退出
        # 文件不存在也可接受（组件尚未开发或路径不同）


# ═════════════════════════════════════════════════════════════════════
# 主程序
# ═════════════════════════════════════════════════════════════════════

_SECTIONS = [
    ("A  HTTPClient _is_select_like()", TestA_SelectLike),
    ("B  HTTPClient FORMAT 追加逻辑",    TestB_FormatAppend),
    ("C  HTTPClient execute() 正常路径", TestC_ExecuteNormal),
    ("D  HTTPClient 错误处理",           TestD_ErrorHandling),
    ("E  HTTPClient 请求结构",           TestE_RequestStructure),
    ("F  Server initialize() TCP 成功", TestF_InitTCPSuccess),
    ("G  Server initialize() HTTP 回退", TestG_InitHTTPFallback),
    ("H  Server TCP+HTTP 均失败",        TestH_BothFail),
    ("I  _test_connection() protocol",  TestI_TestConnectionProtocol),
    ("J  tool_formatter 往返一致性",     TestJ_ToolFormatterRoundTrip),
    ("K  端到端 HTTP 回退全链路",         TestK_EndToEnd),
    ("L  RBAC 回归",                     TestL_RBACRegression),
]

if __name__ == "__main__":
    import pytest, sys
    sys.exit(pytest.main([__file__, "-v", "-s"]))
