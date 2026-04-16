"""
Report MCP Tool — 单元测试套件

覆盖：
  A (7)  — ReportToolMCPServer 注册 + 工具列表
  B (8)  — report_service: get_spec_by_token
  C (8)  — report_service: update_spec_by_token
  D (7)  — report_service: update_single_chart_by_token
  E (6)  — ReportToolMCPServer 工具回调（_get_spec / _update_spec / _update_single_chart）
  F (4)  — MCPManager 注册 report 服务器
  G (4)  — agentic_loop 中 report tool 触发 files_written 条目
  H (3)  — 边界 & 错误用例

总计: 47 个测试用例
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import unittest
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("ENABLE_AUTH", "False")
os.environ.setdefault("ALLOWED_DIRECTORIES", '["customer_data"]')

sys.path.insert(0, os.path.dirname(__file__))


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_mock_report(
    report_id: str = None,
    name: str = "Test Report",
    refresh_token: str = "valid_token_abc",
    charts: list = None,
    filters: list = None,
    theme: str = "light",
    report_file_path: str = "superadmin/reports/test.html",
    username: str = "superadmin",
):
    r = MagicMock()
    r.id = uuid.UUID(report_id) if report_id else uuid.uuid4()
    r.name = name
    r.refresh_token = refresh_token
    r.charts = charts if charts is not None else [{"id": "c1", "title": "Chart 1", "chart_type": "line", "sql": "SELECT 1", "connection_env": "sg", "connection_type": "clickhouse"}]
    r.filters = filters or []
    r.theme = theme
    r.report_file_path = report_file_path
    r.username = username
    r.doc_type = "dashboard"
    r.description = ""
    r.data_sources = []
    r.updated_at = datetime.utcnow()
    return r


# ─────────────────────────────────────────────────────────────────────────────
# Section A: ReportToolMCPServer 注册 + 工具列表 (7)
# ─────────────────────────────────────────────────────────────────────────────

class TestReportToolServerRegistration(unittest.TestCase):
    """A: ReportToolMCPServer 工具注册验证。"""

    def _server(self):
        from backend.mcp.report_tool.server import ReportToolMCPServer
        s = ReportToolMCPServer()
        asyncio.get_event_loop().run_until_complete(s.initialize())
        return s

    def test_a01_server_name(self):
        s = self._server()
        self.assertEqual(s.name, "Report Tool MCP Server")

    def test_a02_has_get_spec_tool(self):
        s = self._server()
        self.assertIn("get_spec", s.tools)

    def test_a03_has_update_spec_tool(self):
        s = self._server()
        self.assertIn("update_spec", s.tools)

    def test_a04_has_update_single_chart_tool(self):
        s = self._server()
        self.assertIn("update_single_chart", s.tools)

    def test_a05_get_spec_required_params(self):
        s = self._server()
        schema = s.tools["get_spec"].input_schema
        self.assertIn("report_id", schema["required"])
        self.assertIn("token", schema["required"])

    def test_a06_update_spec_required_params(self):
        s = self._server()
        schema = s.tools["update_spec"].input_schema
        self.assertIn("report_id", schema["required"])
        self.assertIn("token", schema["required"])
        self.assertIn("spec", schema["required"])

    def test_a07_update_single_chart_required_params(self):
        s = self._server()
        schema = s.tools["update_single_chart"].input_schema
        for key in ("report_id", "token", "chart_id", "chart_patch"):
            self.assertIn(key, schema["required"])

    def test_a08_create_tool_description_strengthened(self):
        s = self._server()
        desc = s.tools["create"].description
        self.assertIn("date_range", desc)
        self.assertIn("include_summary=true", desc)
        self.assertIn("UNION ALL", desc)

    def test_a09_update_tools_description_strengthened(self):
        s = self._server()
        self.assertIn("date_start", s.tools["update_spec"].description)
        self.assertIn("ai_analysis", s.tools["update_single_chart"].description)
        patch_desc = s.tools["update_single_chart"].input_schema["properties"]["chart_patch"]["description"]
        self.assertIn("date_end", patch_desc)
        self.assertIn("ai_analysis", patch_desc)


# ─────────────────────────────────────────────────────────────────────────────
# Section B: report_service.get_spec_by_token (8)
# ─────────────────────────────────────────────────────────────────────────────

class TestGetSpecByToken(unittest.TestCase):
    """B: get_spec_by_token 函数验证。"""

    def _call(self, report_id: str, token: str, mock_report=None):
        if mock_report is None:
            mock_report = _make_mock_report(report_id=report_id)
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = mock_report
        mock_ctx = MagicMock()
        mock_ctx.__enter__ = MagicMock(return_value=mock_db)
        mock_ctx.__exit__ = MagicMock(return_value=False)
        with patch("backend.config.database.get_db_context", return_value=mock_ctx):
            from backend.services.report_service import get_spec_by_token
            return get_spec_by_token(report_id, token)

    def test_b01_returns_spec_with_valid_token(self):
        rid = str(uuid.uuid4())
        result = self._call(rid, "valid_token_abc")
        self.assertEqual(result["id"], rid)
        self.assertEqual(result["name"], "Test Report")

    def test_b02_includes_refresh_token(self):
        rid = str(uuid.uuid4())
        result = self._call(rid, "valid_token_abc")
        self.assertEqual(result["refresh_token"], "valid_token_abc")

    def test_b03_includes_charts(self):
        rid = str(uuid.uuid4())
        result = self._call(rid, "valid_token_abc")
        self.assertIsInstance(result["charts"], list)
        self.assertEqual(len(result["charts"]), 1)

    def test_b04_invalid_token_raises_permission_error(self):
        rid = str(uuid.uuid4())
        with self.assertRaises(PermissionError):
            self._call(rid, "wrong_token")

    def test_b05_invalid_uuid_raises_value_error(self):
        from backend.services.report_service import get_spec_by_token
        with self.assertRaises(ValueError):
            mock_ctx = MagicMock()
            mock_ctx.__enter__ = MagicMock(return_value=MagicMock())
            mock_ctx.__exit__ = MagicMock(return_value=False)
            with patch("backend.config.database.get_db_context", return_value=mock_ctx):
                get_spec_by_token("not-a-uuid", "token")

    def test_b06_report_not_found_raises_value_error(self):
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = None
        mock_ctx = MagicMock()
        mock_ctx.__enter__ = MagicMock(return_value=mock_db)
        mock_ctx.__exit__ = MagicMock(return_value=False)
        with patch("backend.config.database.get_db_context", return_value=mock_ctx):
            from backend.services.report_service import get_spec_by_token
            with self.assertRaises(ValueError):
                get_spec_by_token(str(uuid.uuid4()), "token")

    def test_b07_returns_doc_type(self):
        rid = str(uuid.uuid4())
        result = self._call(rid, "valid_token_abc")
        self.assertEqual(result["doc_type"], "dashboard")

    def test_b08_returns_username(self):
        rid = str(uuid.uuid4())
        result = self._call(rid, "valid_token_abc")
        self.assertEqual(result["username"], "superadmin")


# ─────────────────────────────────────────────────────────────────────────────
# Section C: report_service.update_spec_by_token (8)
# ─────────────────────────────────────────────────────────────────────────────

class TestUpdateSpecByToken(unittest.TestCase):
    """C: update_spec_by_token 函数验证。"""

    def _call(self, report_id: str, spec: dict, token: str, mock_report=None):
        if mock_report is None:
            mock_report = _make_mock_report(report_id=report_id)
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = mock_report
        mock_ctx = MagicMock()
        mock_ctx.__enter__ = MagicMock(return_value=mock_db)
        mock_ctx.__exit__ = MagicMock(return_value=False)

        mock_html = "<html>updated</html>"
        tmp_dir = Path(os.path.dirname(__file__)) / "tmp_test_reports"
        tmp_dir.mkdir(exist_ok=True)

        with patch("backend.config.database.get_db_context", return_value=mock_ctx), \
             patch("backend.services.report_builder_service.build_report_html", return_value=mock_html), \
             patch("backend.services.report_service._get_customer_data_root", return_value=tmp_dir):
            from backend.services.report_service import update_spec_by_token
            return update_spec_by_token(report_id, spec, token)

    def test_c01_returns_report_id_on_success(self):
        rid = str(uuid.uuid4())
        result = self._call(rid, {"charts": [{"id": "c1", "chart_type": "line", "sql": "SELECT 1", "connection_env": "sg"}], "theme": "dark"}, "valid_token_abc")
        self.assertEqual(result["report_id"], rid)

    def test_c02_returns_updated_at(self):
        rid = str(uuid.uuid4())
        result = self._call(rid, {"charts": [{"id": "c1", "chart_type": "line", "sql": "SELECT 1", "connection_env": "sg"}]}, "valid_token_abc")
        self.assertIn("updated_at", result)

    def test_c03_invalid_token_raises_permission_error(self):
        rid = str(uuid.uuid4())
        with self.assertRaises(PermissionError):
            self._call(rid, {"charts": [{"id": "c1", "chart_type": "line", "sql": "SELECT 1", "connection_env": "sg"}]}, "wrong_token")

    def test_c04_no_file_path_raises_value_error(self):
        rid = str(uuid.uuid4())
        report = _make_mock_report(report_id=rid, report_file_path=None)
        report.report_file_path = None
        with self.assertRaises(ValueError):
            self._call(rid, {"charts": [{"id": "c1", "chart_type": "line", "sql": "SELECT 1", "connection_env": "sg"}]}, "valid_token_abc", mock_report=report)

    def test_c05_html_generation_failure_raises_runtime_error(self):
        rid = str(uuid.uuid4())
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = _make_mock_report(report_id=rid)
        mock_ctx = MagicMock()
        mock_ctx.__enter__ = MagicMock(return_value=mock_db)
        mock_ctx.__exit__ = MagicMock(return_value=False)
        with patch("backend.config.database.get_db_context", return_value=mock_ctx), \
             patch("backend.services.report_builder_service.build_report_html", side_effect=Exception("build error")):
            from backend.services.report_service import update_spec_by_token
            with self.assertRaises(RuntimeError):
                update_spec_by_token(rid, {"charts": [{"id": "c1", "chart_type": "line", "sql": "SELECT 1", "connection_env": "sg"}]}, "valid_token_abc")

    def test_c06_updates_title_when_provided(self):
        rid = str(uuid.uuid4())
        mock_report = _make_mock_report(report_id=rid)
        result = self._call(rid, {"charts": [{"id": "c1", "chart_type": "line", "sql": "SELECT 1", "connection_env": "sg"}], "title": "New Title"}, "valid_token_abc", mock_report=mock_report)
        self.assertEqual(mock_report.name, "New Title")

    def test_c07_updates_theme_when_provided(self):
        rid = str(uuid.uuid4())
        mock_report = _make_mock_report(report_id=rid)
        self._call(rid, {"charts": [{"id": "c1", "chart_type": "line", "sql": "SELECT 1", "connection_env": "sg"}], "theme": "dark"}, "valid_token_abc", mock_report=mock_report)
        self.assertEqual(mock_report.theme, "dark")

    def test_c08_invalid_uuid_raises_value_error(self):
        mock_ctx = MagicMock()
        mock_ctx.__enter__ = MagicMock(return_value=MagicMock())
        mock_ctx.__exit__ = MagicMock(return_value=False)
        with patch("backend.config.database.get_db_context", return_value=mock_ctx):
            from backend.services.report_service import update_spec_by_token
            with self.assertRaises(ValueError):
                update_spec_by_token("bad-uuid", {}, "token")

    def test_c09_legacy_chart_spec_is_normalized_before_persist(self):
        rid = str(uuid.uuid4())
        mock_report = _make_mock_report(report_id=rid)
        legacy_spec = {
            "title": "Legacy Normalized",
            "charts": [{
                "id": "c1",
                "type": "bar",
                "dataset": {
                    "source": "clickhouse",
                    "server": "clickhouse-sg",
                    "query": "SELECT 1 AS cnt",
                },
                "xField": "dt",
                "yField": "cnt",
            }],
        }
        self._call(rid, legacy_spec, "valid_token_abc", mock_report=mock_report)
        self.assertEqual(mock_report.charts[0]["chart_type"], "bar")
        self.assertEqual(mock_report.charts[0]["sql"], "SELECT 1 AS cnt")
        # "clickhouse-sg" 在 normalize_chart_spec 中被自动剥离前缀为 "sg"
        self.assertEqual(mock_report.charts[0]["connection_env"], "sg")
        self.assertEqual(mock_report.charts[0]["x_field"], "dt")
        self.assertEqual(mock_report.charts[0]["y_fields"], ["cnt"])


# ─────────────────────────────────────────────────────────────────────────────
# Section D: report_service.update_single_chart_by_token (7)
# ─────────────────────────────────────────────────────────────────────────────

class TestUpdateSingleChartByToken(unittest.TestCase):
    """D: update_single_chart_by_token 函数验证。"""

    def _call(self, report_id: str, chart_id: str, patch_dict: dict, token: str, mock_report=None):
        if mock_report is None:
            mock_report = _make_mock_report(report_id=report_id)
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = mock_report
        mock_ctx = MagicMock()
        mock_ctx.__enter__ = MagicMock(return_value=mock_db)
        mock_ctx.__exit__ = MagicMock(return_value=False)

        tmp_dir = Path(os.path.dirname(__file__)) / "tmp_test_reports"
        tmp_dir.mkdir(exist_ok=True)

        with patch("backend.config.database.get_db_context", return_value=mock_ctx), \
             patch("backend.services.report_builder_service.build_report_html", return_value="<html/>"), \
             patch("backend.services.report_service._get_customer_data_root", return_value=tmp_dir):
            from backend.services.report_service import update_single_chart_by_token
            return update_single_chart_by_token(report_id, chart_id, patch_dict, token)

    def test_d01_found_existing_chart(self):
        rid = str(uuid.uuid4())
        result = self._call(rid, "c1", {"chart_type": "bar"}, "valid_token_abc")
        self.assertTrue(result["found"])
        self.assertEqual(result["chart_id"], "c1")

    def test_d02_not_found_appends_new_chart(self):
        rid = str(uuid.uuid4())
        result = self._call(rid, "c99", {"title": "New", "chart_type": "bar", "sql": "SELECT 2", "connection_env": "sg"}, "valid_token_abc")
        self.assertFalse(result["found"])
        self.assertEqual(result["total_charts"], 2)  # original 1 + new 1

    def test_d03_invalid_token_raises(self):
        rid = str(uuid.uuid4())
        with self.assertRaises(PermissionError):
            self._call(rid, "c1", {}, "bad_token")

    def test_d04_returns_total_charts(self):
        rid = str(uuid.uuid4())
        result = self._call(rid, "c1", {"chart_type": "bar"}, "valid_token_abc")
        self.assertEqual(result["total_charts"], 1)

    def test_d05_merge_preserves_other_fields(self):
        rid = str(uuid.uuid4())
        report = _make_mock_report(
            report_id=rid,
            charts=[{"id": "c1", "title": "Original", "chart_type": "line", "sql": "SELECT 1", "connection_env": "sg"}],
        )
        self._call(rid, "c1", {"chart_type": "bar"}, "valid_token_abc", mock_report=report)
        # After merge, charts should have chart_type=bar AND sql=SELECT 1
        merged = report.charts[0]
        self.assertEqual(merged["chart_type"], "bar")
        self.assertEqual(merged["sql"], "SELECT 1")

    def test_d06_invalid_uuid_raises(self):
        mock_ctx = MagicMock()
        mock_ctx.__enter__ = MagicMock(return_value=MagicMock())
        mock_ctx.__exit__ = MagicMock(return_value=False)
        with patch("backend.config.database.get_db_context", return_value=mock_ctx):
            from backend.services.report_service import update_single_chart_by_token
            with self.assertRaises(ValueError):
                update_single_chart_by_token("bad-id", "c1", {}, "token")

    def test_d07_returns_updated_at(self):
        rid = str(uuid.uuid4())
        result = self._call(rid, "c1", {"chart_type": "pie"}, "valid_token_abc")
        self.assertIn("updated_at", result)


# ─────────────────────────────────────────────────────────────────────────────
# Section E: ReportToolMCPServer 工具回调 (6)
# ─────────────────────────────────────────────────────────────────────────────

class TestReportToolServerCallbacks(unittest.IsolatedAsyncioTestCase):
    """E: MCP Server 工具回调测试。"""

    async def _server(self):
        from backend.mcp.report_tool.server import ReportToolMCPServer
        s = ReportToolMCPServer()
        await s.initialize()
        return s

    async def test_e01_get_spec_success(self):
        s = await self._server()
        mock_spec = {"id": "r1", "name": "Demo", "charts": [{"id": "c1"}], "refresh_token": "tok"}
        with patch("backend.services.report_service.get_spec_by_token", return_value=mock_spec):
            resp = await s.call_tool("get_spec", {"report_id": "r1", "token": "tok"})
        self.assertTrue(resp.success)
        self.assertTrue(resp.data["success"])
        self.assertEqual(resp.data["chart_count"], 1)

    async def test_e02_get_spec_permission_error(self):
        s = await self._server()
        with patch("backend.services.report_service.get_spec_by_token", side_effect=PermissionError("bad token")):
            resp = await s.call_tool("get_spec", {"report_id": "r1", "token": "bad"})
        self.assertTrue(resp.success)  # MCPResponse.success=True, inner data.success=False
        self.assertFalse(resp.data["success"])
        self.assertIn("鉴权失败", resp.data["error"])

    async def test_e03_update_spec_success_message_contains_keyword(self):
        s = await self._server()
        mock_result = {"report_id": "r1", "name": "Demo", "updated_at": "2026-01-01T00:00:00"}
        with patch("backend.services.report_service.update_spec_by_token", return_value=mock_result):
            resp = await s.call_tool("update_spec", {
                "report_id": "r1", "token": "tok",
                "spec": {"charts": [{"id": "c1", "chart_type": "line", "sql": "SELECT 1", "connection_env": "sg"}], "theme": "light"},
            })
        self.assertTrue(resp.data["success"])
        self.assertIn("报表已更新", resp.data["message"])

    async def test_e04_update_spec_string_spec_auto_parsed(self):
        """spec 传成 JSON 字符串时应自动解析。"""
        s = await self._server()
        mock_result = {"report_id": "r1", "name": "Demo", "updated_at": "2026-01-01T00:00:00"}
        with patch("backend.services.report_service.update_spec_by_token", return_value=mock_result):
            spec_str = json.dumps({"charts": [{"id": "c1"}]})
            resp = await s.call_tool("update_spec", {
                "report_id": "r1", "token": "tok", "spec": spec_str,
            })
        self.assertTrue(resp.data["success"])

    async def test_e05_update_single_chart_success_message(self):
        s = await self._server()
        mock_result = {
            "report_id": "r1", "chart_id": "c1", "found": True,
            "total_charts": 2, "updated_at": "2026-01-01T00:00:00",
        }
        with patch("backend.services.report_service.update_single_chart_by_token", return_value=mock_result):
            resp = await s.call_tool("update_single_chart", {
                "report_id": "r1", "token": "tok",
                "chart_id": "c1", "chart_patch": {"chart_type": "bar"},
            })
        self.assertTrue(resp.data["success"])
        self.assertIn("报表已更新", resp.data["message"])

    async def test_e06_update_single_chart_string_patch_auto_parsed(self):
        s = await self._server()
        mock_result = {"report_id": "r1", "chart_id": "c1", "found": True, "total_charts": 1, "updated_at": "2026"}
        with patch("backend.services.report_service.update_single_chart_by_token", return_value=mock_result):
            patch_str = json.dumps({"chart_type": "bar"})
            resp = await s.call_tool("update_single_chart", {
                "report_id": "r1", "token": "tok",
                "chart_id": "c1", "chart_patch": patch_str,
            })
        self.assertTrue(resp.data["success"])


# ─────────────────────────────────────────────────────────────────────────────
# Section F: MCPManager 注册 report 服务器 (4)
# ─────────────────────────────────────────────────────────────────────────────

class TestMCPManagerReportRegistration(unittest.IsolatedAsyncioTestCase):
    """F: MCPServerManager 包含 report 服务器。"""

    async def _manager(self):
        from backend.mcp.manager import MCPServerManager
        mgr = MCPServerManager()
        await mgr.create_report_tool_server()
        return mgr

    async def test_f01_report_server_in_servers(self):
        mgr = await self._manager()
        self.assertIn("report", mgr.servers)

    async def test_f02_report_server_type_in_configs(self):
        mgr = await self._manager()
        self.assertEqual(mgr.server_configs["report"]["type"], "report_tool")

    async def test_f03_tool_names_formatted_correctly(self):
        """tool_formatter 应将 report server 格式化为 report__xxx 工具名。"""
        from backend.mcp.tool_formatter import format_mcp_tools_for_claude
        mgr = await self._manager()
        tools = format_mcp_tools_for_claude(mgr)
        tool_names = [t["name"] for t in tools]
        self.assertIn("report__get_spec", tool_names)
        self.assertIn("report__update_spec", tool_names)
        self.assertIn("report__update_single_chart", tool_names)

    async def test_f04_parse_tool_name_roundtrip(self):
        from backend.mcp.tool_formatter import parse_tool_name
        server_name, tool_name = parse_tool_name("report__update_spec")
        self.assertEqual(server_name, "report")
        self.assertEqual(tool_name, "update_spec")


# ─────────────────────────────────────────────────────────────────────────────
# Section G: agentic_loop 中 report tool 触发 files_written (4)
# ─────────────────────────────────────────────────────────────────────────────

class TestAgenticLoopReportToolTracking(unittest.IsolatedAsyncioTestCase):
    """G: report__update_spec / update_single_chart 触发 written_files 条目。"""

    async def _run_mock_loop_with_tool(self, tool_name: str, tool_input: dict, tool_result: dict):
        """构造一个只执行一次工具调用就结束的 agentic loop，收集事件。"""
        from backend.agents.agentic_loop import AgenticLoop, AgentEvent

        mock_llm = AsyncMock()
        mock_mcp = MagicMock()
        mock_mcp.servers = {}

        # 第一次 LLM 调用返回 tool_use
        mock_llm.chat_with_tools = AsyncMock(return_value={
            "stop_reason": "tool_use",
            "content": [
                {"type": "tool_use", "id": "t1", "name": tool_name, "input": tool_input}
            ],
        })

        # 工具执行返回成功结果
        mock_mcp.call_tool = AsyncMock(return_value=tool_result)
        mock_mcp.get_all_tools = MagicMock(return_value=[])

        # 第二次 LLM 调用（处理 tool_result）返回 end_turn
        mock_llm.chat_with_tools.side_effect = [
            {
                "stop_reason": "tool_use",
                "content": [
                    {"type": "tool_use", "id": "t1", "name": tool_name, "input": tool_input}
                ],
            },
            {
                "stop_reason": "end_turn",
                "content": [{"type": "text", "text": "报表已更新"}],
            },
        ]

        loop = AgenticLoop(llm_adapter=mock_llm, mcp_manager=mock_mcp, max_iterations=20)

        with patch("backend.agents.agentic_loop.format_mcp_tools_for_claude", return_value=[{"name": tool_name, "description": "test", "input_schema": {"type": "object", "properties": {}, "required": []}}]):
            with patch.object(loop, "_execute_tool", new_callable=AsyncMock, return_value=tool_result):
                with patch.object(loop, "_build_system_prompt", new_callable=AsyncMock, return_value="sys"):
                    events = []
                    async for ev in loop.run_streaming("update chart", {"history": [], "system_prompt": ""}):
                        events.append(ev)
        return events

    async def test_g01_update_spec_triggers_files_written(self):
        events = await self._run_mock_loop_with_tool(
            "report__update_spec",
            {"report_id": "r123", "token": "tok", "spec": {}},
            {"success": True, "report_id": "r123", "name": "Demo Report", "updated_at": "2026"},
        )
        fw_events = [e for e in events if e.type == "files_written"]
        self.assertEqual(len(fw_events), 1)
        files = fw_events[0].data["files"]
        self.assertEqual(len(files), 1)
        self.assertTrue(files[0]["is_report"])

    async def test_g02_update_single_chart_triggers_files_written(self):
        events = await self._run_mock_loop_with_tool(
            "report__update_single_chart",
            {"report_id": "r456", "token": "tok", "chart_id": "c1", "chart_patch": {}},
            {"success": True, "report_id": "r456", "name": "Chart Report", "total_charts": 2, "updated_at": "2026"},
        )
        fw_events = [e for e in events if e.type == "files_written"]
        self.assertEqual(len(fw_events), 1)

    async def test_g03_failed_tool_does_not_trigger_files_written(self):
        events = await self._run_mock_loop_with_tool(
            "report__update_spec",
            {"report_id": "r789", "token": "bad", "spec": {}},
            {"success": False, "error": "鉴权失败"},
        )
        fw_events = [e for e in events if e.type == "files_written"]
        self.assertEqual(len(fw_events), 0)

    async def test_g04_report_id_in_files_written_entry(self):
        events = await self._run_mock_loop_with_tool(
            "report__update_spec",
            {"report_id": "r_specific", "token": "tok", "spec": {}},
            {"success": True, "report_id": "r_specific", "name": "My Report", "updated_at": "2026"},
        )
        fw_events = [e for e in events if e.type == "files_written"]
        self.assertTrue(len(fw_events) > 0)
        self.assertEqual(fw_events[0].data["files"][0]["report_id"], "r_specific")


# ─────────────────────────────────────────────────────────────────────────────
# Section H: 边界 & 错误用例 (3)
# ─────────────────────────────────────────────────────────────────────────────

class TestReportToolEdgeCases(unittest.IsolatedAsyncioTestCase):
    """H: 边界与错误用例。"""

    async def test_h01_update_spec_invalid_spec_json_string(self):
        from backend.mcp.report_tool.server import ReportToolMCPServer
        s = ReportToolMCPServer()
        await s.initialize()
        resp = await s.call_tool("update_spec", {
            "report_id": "r1", "token": "tok", "spec": "not-json{{{",
        })
        self.assertFalse(resp.data["success"])
        self.assertIn("解析失败", resp.data["error"])

    async def test_h02_update_single_chart_invalid_patch_json_string(self):
        from backend.mcp.report_tool.server import ReportToolMCPServer
        s = ReportToolMCPServer()
        await s.initialize()
        resp = await s.call_tool("update_single_chart", {
            "report_id": "r1", "token": "tok",
            "chart_id": "c1", "chart_patch": "{{bad",
        })
        self.assertFalse(resp.data["success"])

    async def test_h03_update_spec_spec_not_dict_returns_error(self):
        from backend.mcp.report_tool.server import ReportToolMCPServer
        s = ReportToolMCPServer()
        await s.initialize()
        resp = await s.call_tool("update_spec", {
            "report_id": "r1", "token": "tok", "spec": 12345,
        })
        self.assertFalse(resp.data["success"])
        self.assertIn("JSON 对象", resp.data["error"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
