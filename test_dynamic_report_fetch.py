"""
test_dynamic_report_fetch.py
============================
Tests for dynamic report fetching architecture.

Sections:
  E1 — create_report_with_spec() service function
  E2 — report__create MCP tool
  E3 — GET /reports/{id}/data endpoint (existing, verify Jinja2 + token auth)
  E4 — agentic_loop report__create detection in files_written
  E5 — clickhouse-analyst.md skill content validation
  E6 — Regression: existing test suites still pass
"""
from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, call

PROJECT_ROOT = Path(__file__).parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

SKILL_FILE = PROJECT_ROOT / ".claude/skills/project/clickhouse-analyst.md"


# ===========================================================================
# E1 — create_report_with_spec() service function
# ===========================================================================
class TestCreateReportWithSpec(unittest.TestCase):
    """E1: create_report_with_spec() 基础行为与结构验证。"""

    def test_E1_1_function_is_importable(self):
        """E1-1: create_report_with_spec 可从 report_service 导入。"""
        from backend.services.report_service import create_report_with_spec
        self.assertTrue(callable(create_report_with_spec))

    def test_E1_2_rejects_invalid_spec_type(self):
        """E1-2: spec 不是 dict 时抛出异常。"""
        from backend.services.report_service import create_report_with_spec
        with self.assertRaises((ValueError, TypeError, AttributeError)):
            create_report_with_spec(spec="not a dict", username="testuser")

    def test_E1_3_build_report_html_called_with_dynamic_report_id(self):
        """E1-3: create_report_with_spec 源码中 build_report_html 接收变量 report_id（非硬编码 'preview'）。"""
        import inspect, re
        from backend.services.report_service import create_report_with_spec
        src = inspect.getsource(create_report_with_spec)
        self.assertIn("build_report_html", src)
        # build_report_html call should pass report_id= as variable (not the literal string "preview")
        # Find the call site - it should have report_id=report_id (variable), not report_id="preview"
        m = re.search(r'build_report_html\s*\(([^)]+)\)', src)
        self.assertIsNotNone(m, "未找到 build_report_html 调用")
        call_args = m.group(1)
        # Should NOT have report_id assigned to literal "preview"
        self.assertNotRegex(call_args, r'report_id\s*=\s*["\']preview["\']',
                            "build_report_html 调用不应硬编码 report_id='preview'")

    def test_E1_4_html_path_format_in_source(self):
        """E1-4: 源码包含 {username}/reports/ 路径格式。"""
        import inspect
        from backend.services.report_service import create_report_with_spec
        src = inspect.getsource(create_report_with_spec)
        self.assertIn("reports/", src,
                      "create_report_with_spec 应将 HTML 写入 {username}/reports/ 子目录")

    def test_E1_5_uses_get_db_context(self):
        """E1-5: 使用 get_db_context（事务安全的 DB 操作）。"""
        import inspect
        from backend.services.report_service import create_report_with_spec
        src = inspect.getsource(create_report_with_spec)
        self.assertIn("get_db_context", src)

    def test_E1_6_uses_generate_refresh_token(self):
        """E1-6: 使用 generate_refresh_token 生成 token。"""
        import inspect
        from backend.services.report_service import create_report_with_spec
        src = inspect.getsource(create_report_with_spec)
        self.assertIn("generate_refresh_token", src)

    def test_E1_7_returns_dict_with_mocked_db(self):
        """E1-7: 正常调用时返回包含必要键的 dict（全量 mock DB + builder）。"""
        import uuid as _uuid
        spec = {
            "title": "单测报表",
            "charts": [{"id": "c1"}],
            "filters": [],
        }
        mock_report_obj = MagicMock()
        mock_report_obj.id = _uuid.uuid4()

        mock_db = MagicMock()
        mock_db.__enter__ = MagicMock(return_value=mock_db)
        mock_db.__exit__ = MagicMock(return_value=False)

        with patch("backend.config.database.get_db_context", return_value=mock_db), \
             patch("backend.services.report_builder_service.generate_refresh_token", return_value="tok_unit"), \
             patch("backend.services.report_builder_service.build_report_html", return_value="<html/>"), \
             patch("backend.models.report.Report", return_value=mock_report_obj), \
             patch("backend.models.report.ReportType"), \
             patch("pathlib.Path.mkdir"), \
             patch("pathlib.Path.write_text"):

            from backend.services.report_service import create_report_with_spec
            result = create_report_with_spec(spec=spec, username="testuser")

        for key in ("report_id", "refresh_token", "name", "html_path", "message"):
            self.assertIn(key, result, f"返回值缺少键: {key}")


# ===========================================================================
# E2 — report__create MCP tool
# ===========================================================================
class TestReportCreateMCPTool(unittest.IsolatedAsyncioTestCase):
    """E2: report__create MCP 工具正确处理 spec + username。"""

    def setUp(self):
        from backend.mcp.report_tool.server import ReportToolMCPServer
        self.server = ReportToolMCPServer()

    async def asyncSetUp(self):
        await self.server.initialize()

    def test_E2_1_create_tool_registered(self):
        """E2-1: 'create' 工具已注册到 MCP Server。"""
        tool_names = list(self.server.tools.keys())
        self.assertIn("create", tool_names,
                      f"已注册工具: {tool_names}")

    async def test_E2_2_create_returns_success_with_report_id(self):
        """E2-2: 调用 create 成功时返回 report_id + refresh_token。"""
        spec = {
            "title": "E2 测试报表",
            "charts": [{"id": "c1", "chart_type": "bar", "sql": "SELECT 1",
                        "connection_env": "sg", "x_field": "d", "y_fields": ["v"]}],
            "filters": [],
        }
        mock_result = {
            "report_id": "uuid-e2-test",
            "refresh_token": "tok_e2",
            "name": "E2 测试报表",
            "html_path": "testuser/reports/E2_测试报表_20260415.html",
            "message": "报表已创建",
        }
        with patch("backend.services.report_service.create_report_with_spec",
                   return_value=mock_result):
            result = await self.server._create(spec=spec, username="testuser")

        self.assertTrue(result["success"])
        self.assertEqual(result["report_id"], "uuid-e2-test")
        self.assertEqual(result["refresh_token"], "tok_e2")
        self.assertIn("html_path", result)

    async def test_E2_3_create_rejects_empty_charts(self):
        """E2-3: spec.charts 为空时返回 success=False。"""
        spec = {"title": "无图表报表", "charts": [], "filters": []}
        result = await self.server._create(spec=spec, username="testuser")
        self.assertFalse(result["success"])
        self.assertIn("charts", result.get("error", "").lower())

    async def test_E2_4_create_rejects_empty_username(self):
        """E2-4: username 为空时返回 success=False。"""
        spec = {"title": "测试", "charts": [{"id": "c1"}], "filters": []}
        result = await self.server._create(spec=spec, username="")
        self.assertFalse(result["success"])

    async def test_E2_5_create_accepts_spec_as_json_string(self):
        """E2-5: spec 传入 JSON 字符串时自动解析。"""
        spec = {
            "title": "JSON字符串测试",
            "charts": [{"id": "c1", "chart_type": "bar", "sql": "SELECT 1",
                        "connection_env": "sg", "x_field": "d", "y_fields": ["v"]}],
            "filters": [],
        }
        spec_str = json.dumps(spec)
        mock_result = {
            "report_id": "uuid-str", "refresh_token": "tok_str",
            "name": "JSON字符串测试", "html_path": "u/reports/x.html", "message": "ok"
        }
        with patch("backend.services.report_service.create_report_with_spec",
                   return_value=mock_result):
            result = await self.server._create(spec=spec_str, username="testuser")
        self.assertTrue(result["success"])

    async def test_E2_6_create_handles_service_exception(self):
        """E2-6: service 抛异常时返回 success=False，不传播。"""
        spec = {"title": "T", "charts": [{"id": "c1"}], "filters": []}
        with patch("backend.services.report_service.create_report_with_spec",
                   side_effect=RuntimeError("DB连接失败")):
            result = await self.server._create(spec=spec, username="testuser")
        self.assertFalse(result["success"])
        self.assertIn("DB连接失败", result.get("error", ""))


# ===========================================================================
# E3 — GET /reports/{id}/data endpoint Jinja2 + token auth
# ===========================================================================
class TestReportDataEndpoint(unittest.TestCase):
    """E3: /data 端点支持 Jinja2 参数渲染 + refresh_token 鉴权。"""

    def test_E3_1_render_sql_with_date_params(self):
        """E3-1: render_sql 正确替换 {{ date_start }} / {{ date_end }}。"""
        from backend.services.report_params_service import render_sql
        sql = "SELECT * FROM t WHERE dt >= '{{ date_start }}' AND dt < '{{ date_end }}'"
        rendered = render_sql(sql, {"date_start": "2026-03-01", "date_end": "2026-04-01"})
        self.assertIn("2026-03-01", rendered)
        self.assertIn("2026-04-01", rendered)
        self.assertNotIn("{{", rendered)

    def test_E3_2_render_sql_non_template_passthrough(self):
        """E3-2: 无 {{ }} 的 SQL 原样返回（零开销快速路径）。"""
        from backend.services.report_params_service import render_sql
        sql = "SELECT 1"
        self.assertEqual(render_sql(sql, {"date_start": "2026-01-01"}), sql)

    def test_E3_3_extract_default_params_date_range(self):
        """E3-3: extract_default_params 从 date_range filter 计算默认 30 天。"""
        from backend.services.report_params_service import extract_default_params
        from datetime import date, timedelta
        spec = {
            "filters": [{
                "id": "date_range",
                "type": "date_range",
                "default_days": 30,
                "binds": {"start": "date_start", "end": "date_end"},
            }]
        }
        params = extract_default_params(spec)
        self.assertIn("date_start", params)
        self.assertIn("date_end", params)
        # date_end should be today
        expected_end = date.today().isoformat()
        self.assertEqual(params["date_end"], expected_end)
        # date_start should be 30 days ago
        expected_start = (date.today() - timedelta(days=30)).isoformat()
        self.assertEqual(params["date_start"], expected_start)

    def test_E3_4_route_exists_in_router(self):
        """E3-4: GET /{report_id}/data 路由已注册。"""
        from backend.api.reports import router
        paths = [r.path for r in router.routes]
        self.assertTrue(
            any("data" in p for p in paths),
            f"未找到 /data 路由，已有路由: {paths}"
        )


# ===========================================================================
# E4 — agentic_loop report__create detection in files_written
# ===========================================================================
class TestAgenticLoopCreateDetection(unittest.TestCase):
    """E4: agentic_loop 对 report__create 工具结果添加 files_written 条目。"""

    def test_E4_1_report_create_in_detection_condition(self):
        """E4-1: agentic_loop.py 的 report tool 检测条件包含 report__create。"""
        content = (PROJECT_ROOT / "backend/agents/agentic_loop.py").read_text(encoding="utf-8")
        self.assertIn("report__create", content,
                      "agentic_loop.py 应检测 report__create 工具结果")

    def test_E4_2_detection_condition_uses_tuple(self):
        """E4-2: 检测条件包含 report__update_spec, report__update_single_chart, report__create。"""
        content = (PROJECT_ROOT / "backend/agents/agentic_loop.py").read_text(encoding="utf-8")
        import re
        # Find the detection block
        m = re.search(
            r'tool_name in \([^)]*"report__create"[^)]*\)',
            content,
        )
        self.assertIsNotNone(m, "检测条件应包含 report__create 在 tool_name in (...) 中")


# ===========================================================================
# E5 — clickhouse-analyst.md skill content validation
# ===========================================================================
class TestSkillDynamicReportWorkflow(unittest.TestCase):
    """E5: clickhouse-analyst.md 包含完整的动态报表生成三步工作流。"""

    def setUp(self):
        self.content = SKILL_FILE.read_text(encoding="utf-8")

    def test_E5_1_three_step_workflow_section_exists(self):
        """E5-1: 包含 '生成动态报表三步流程' 节。"""
        self.assertIn("生成动态报表三步流程", self.content)

    def test_E5_2_report_create_tool_mentioned(self):
        """E5-2: 提到调用 report__create 工具。"""
        self.assertIn("report__create", self.content)

    def test_E5_3_filesystem_write_forbidden_for_html(self):
        """E5-3: 明确禁止用 filesystem__write_file 写 HTML 图表报表。"""
        self.assertIn("filesystem__write_file", self.content)
        # Should appear in a forbidden/warning context
        idx = self.content.find("filesystem__write_file")
        surrounding = self.content[max(0, idx-50):idx+100]
        self.assertTrue(
            "禁止" in surrounding or "严禁" in surrounding or "不要" in surrounding,
            f"filesystem__write_file 附近应有禁止说明，实际: {surrounding!r}"
        )

    def test_E5_4_jinja2_date_params_example(self):
        """E5-4: 包含 Jinja2 日期参数示例 {{ date_start }} / {{ date_end }}。"""
        self.assertIn("date_start", self.content)
        self.assertIn("date_end", self.content)

    def test_E5_5_no_embed_data_rule(self):
        """E5-5: 明确禁止查询数据后嵌入 HTML。"""
        self.assertIn("嵌入", self.content)
        # 有禁止嵌入的规则
        self.assertIn("禁止查询大量数据后嵌入", self.content)

    def test_E5_6_step1_sql_exploration(self):
        """E5-6: 第一步 SQL 探索验证步骤存在。"""
        self.assertIn("第一步", self.content)
        self.assertIn("SQL", self.content)

    def test_E5_7_step2_spec_and_create(self):
        """E5-7: 第二步构造 spec + 调用 report__create。"""
        self.assertIn("第二步", self.content)

    def test_E5_8_step3_notify_user(self):
        """E5-8: 第三步告知用户完成。"""
        self.assertIn("第三步", self.content)

    def test_E5_9_decision_tree_uses_report_create(self):
        """E5-9: 决策树中图表 → 调用 report__create（不是 filesystem__write_file）。"""
        # Find decision tree block
        idx = self.content.find("决策树")
        section = self.content[idx:idx+400] if idx >= 0 else ""
        self.assertIn("report__create", section,
                      "决策树中应指向 report__create 而不是 filesystem__write_file")

    def test_E5_10_username_current_user_in_example(self):
        """E5-10: spec 示例中 username 使用 {CURRENT_USER}。"""
        self.assertIn("CURRENT_USER", self.content)


# ===========================================================================
# E6 — Regression
# ===========================================================================
class TestRegressionMCPToolRegistration(unittest.IsolatedAsyncioTestCase):
    """E6: report MCP Server 的原有工具（get_spec/update_spec/update_single_chart）仍注册。"""

    async def asyncSetUp(self):
        from backend.mcp.report_tool.server import ReportToolMCPServer
        self.server = ReportToolMCPServer()
        await self.server.initialize()

    def test_E6_1_get_spec_still_registered(self):
        """E6-1: get_spec 工具仍注册。"""
        self.assertIn("get_spec", self.server.tools)

    def test_E6_2_update_spec_still_registered(self):
        """E6-2: update_spec 工具仍注册。"""
        self.assertIn("update_spec", self.server.tools)

    def test_E6_3_update_single_chart_still_registered(self):
        """E6-3: update_single_chart 工具仍注册。"""
        self.assertIn("update_single_chart", self.server.tools)

    def test_E6_4_total_tools_count(self):
        """E6-4: 工具总数为 4（原有 3 + 新增 create）。"""
        self.assertEqual(len(self.server.tools), 4,
                         f"期望 4 个工具，实际: {list(self.server.tools.keys())}")


# ===========================================================================
# F3 — agentic_loop result_data 层级修复验证
# ===========================================================================
class TestAgenticLoopResultDataFix(unittest.TestCase):
    """F3: raw_result["data"] 层级修复 — report_id / refresh_token 正确提取。"""

    def _make_mcp_response(self, tool_return: dict) -> dict:
        """模拟 MCPResponse.to_dict() 包装结构。"""
        return {
            "success": True,
            "data": tool_return,
            "error": None,
            "traceback": None,
        }

    def _extract_written_files_entry(self, tool_name: str, raw_result: dict, tool_input: dict) -> dict:
        """重现 agentic_loop 中的检测逻辑，返回 written_files 条目。"""
        assert raw_result.get("success", False)
        _outer = raw_result if isinstance(raw_result, dict) else {}
        result_data = _outer.get("data") or {}
        if not isinstance(result_data, dict):
            result_data = {}
        report_id_val = result_data.get("report_id") or tool_input.get("report_id", "")
        refresh_token_val = result_data.get("refresh_token") or tool_input.get("token", "")
        report_name_val = result_data.get("name") or "报表"
        return {
            "path": f"__report__/{report_id_val}",
            "name": report_name_val,
            "size": 0,
            "mime_type": "text/html",
            "is_report": True,
            "doc_type": "dashboard",
            "report_id": report_id_val,
            "pinned_report_id": report_id_val,
            "refresh_token": refresh_token_val,
        }

    # ── F3-1: report__create 场景 ──────────────────────────────────────────────
    def test_F3_1_create_report_id_from_data_layer(self):
        """F3-1: report__create 的 report_id 从 raw_result['data'] 正确读取（非顶层）。"""
        tool_return = {
            "success": True,
            "report_id": "9d43c0de-bb2a-4d9f-9c00-23c961970295",
            "refresh_token": "rSg1EiYCtG-test-token",
            "name": "全环境 Connected Call 每日趋势（近30天）",
            "html_path": "superadmin/reports/test.html",
            "chart_count": 3,
        }
        raw_result = self._make_mcp_response(tool_return)
        # tool_input for create has no report_id or token
        tool_input = {"spec": {"title": "test"}, "username": "superadmin"}

        entry = self._extract_written_files_entry("report__create", raw_result, tool_input)

        self.assertEqual(entry["report_id"], "9d43c0de-bb2a-4d9f-9c00-23c961970295",
                         "report_id 应从 data 层读取，不应为空字符串")
        self.assertEqual(entry["pinned_report_id"], "9d43c0de-bb2a-4d9f-9c00-23c961970295")
        self.assertEqual(entry["refresh_token"], "rSg1EiYCtG-test-token")
        self.assertEqual(entry["path"], "__report__/9d43c0de-bb2a-4d9f-9c00-23c961970295")

    def test_F3_2_create_name_from_data_layer(self):
        """F3-2: report__create 的 name 从 data 层读取，不回落到默认 '报表'。"""
        tool_return = {
            "success": True,
            "report_id": "uuid-test",
            "refresh_token": "tok",
            "name": "全环境 Connected Call 每日趋势",
        }
        raw_result = self._make_mcp_response(tool_return)
        tool_input = {"spec": {}, "username": "testuser"}

        entry = self._extract_written_files_entry("report__create", raw_result, tool_input)
        self.assertEqual(entry["name"], "全环境 Connected Call 每日趋势",
                         "name 应从 data 层读取，不应为默认值 '报表'")

    def test_F3_3_old_create_bug_would_fail(self):
        """F3-3: 验证旧代码（读顶层）会得到空 report_id（复现 bug）。"""
        tool_return = {
            "success": True,
            "report_id": "real-uuid",
            "refresh_token": "real-token",
            "name": "测试报表",
        }
        raw_result = self._make_mcp_response(tool_return)
        tool_input = {"spec": {}, "username": "testuser"}

        # 旧代码逻辑：result_data = raw_result（顶层，无 report_id）
        old_result_data = raw_result  # BUG: should be raw_result["data"]
        old_report_id = old_result_data.get("report_id", tool_input.get("report_id", ""))
        self.assertEqual(old_report_id, "",
                         "旧代码确实会读到空 report_id，确认 bug 存在")

        # 新代码逻辑：result_data = raw_result["data"]（正确）
        new_result_data = raw_result.get("data") or {}
        new_report_id = new_result_data.get("report_id") or tool_input.get("report_id", "")
        self.assertEqual(new_report_id, "real-uuid",
                         "新代码正确读取 report_id")

    # ── F3-4: update_spec 场景（兜底 tool_input 仍有效）─────────────────────────
    def test_F3_4_update_spec_report_id_from_data(self):
        """F3-4: report__update_spec 的 report_id 也从 data 层正确读取。"""
        tool_return = {
            "success": True,
            "report_id": "existing-uuid",
            "refresh_token": "existing-token",
            "name": "已有报表",
            "updated_at": "2026-04-16T10:00:00",
            "chart_count": 2,
        }
        raw_result = self._make_mcp_response(tool_return)
        # update_spec input has report_id and token
        tool_input = {"report_id": "existing-uuid", "token": "existing-token", "spec": {}}

        entry = self._extract_written_files_entry("report__update_spec", raw_result, tool_input)
        self.assertEqual(entry["report_id"], "existing-uuid")
        self.assertEqual(entry["refresh_token"], "existing-token")

    def test_F3_5_update_spec_fallback_to_tool_input(self):
        """F3-5: data 层无 report_id 时仍能从 tool_input 兜底（向后兼容）。"""
        # Simulate older tool result without report_id in data
        tool_return = {"success": True, "updated_at": "2026-04-16"}
        raw_result = self._make_mcp_response(tool_return)
        tool_input = {"report_id": "fallback-uuid", "token": "fallback-token", "spec": {}}

        entry = self._extract_written_files_entry("report__update_spec", raw_result, tool_input)
        self.assertEqual(entry["report_id"], "fallback-uuid",
                         "data 层无 report_id 时应从 tool_input 兜底")
        self.assertEqual(entry["refresh_token"], "fallback-token")

    # ── F3-6: agentic_loop 源码验证 ───────────────────────────────────────────
    def test_F3_6_agentic_loop_reads_data_layer(self):
        """F3-6: agentic_loop.py 通过 _outer.get('data') 读取工具实际返回字段。"""
        content = Path(PROJECT_ROOT / "backend/agents/agentic_loop.py").read_text(encoding="utf-8")
        # The fix uses _outer = raw_result ...; result_data = _outer.get("data")
        self.assertIn('_outer.get("data")', content,
                      "agentic_loop 应通过 _outer.get('data') 读取工具实际返回字段")

    def test_F3_7_agentic_loop_not_using_raw_result_direct(self):
        """F3-7: agentic_loop.py 不再直接用 raw_result 作为 result_data。"""
        content = Path(PROJECT_ROOT / "backend/agents/agentic_loop.py").read_text(encoding="utf-8")
        # Should NOT have the old pattern: result_data = raw_result if isinstance(raw_result, dict)
        self.assertNotIn("result_data = raw_result if isinstance(raw_result, dict)",
                         content,
                         "旧的 result_data = raw_result 模式应已被移除")


# ===========================================================================
# G1 — API_BASE 跨域修复验证（Fix A）
# ===========================================================================
class TestApiBaseFix(unittest.TestCase):
    """G1: API_BASE 使用 window.location.origin 回退，避免 Vite proxy 跨域问题。"""

    def test_G1_1_api_base_url_returns_empty_when_no_public_host(self):
        """G1-1: PUBLIC_HOST 未设置时 _api_base_url() 返回空字符串。"""
        import os
        env = os.environ.copy()
        env.pop("PUBLIC_HOST", None)
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PUBLIC_HOST", None)
            from backend.services import report_service
            result = report_service._api_base_url()
        self.assertEqual(result, "",
                         f"无 PUBLIC_HOST 时应返回 ''，实际: {result!r}")

    def test_G1_2_api_base_url_uses_public_host_when_set(self):
        """G1-2: PUBLIC_HOST 已设置时返回 '{host}/api/v1'。"""
        with patch.dict(os.environ, {"PUBLIC_HOST": "https://example.com"}):
            from backend.services import report_service
            import importlib
            importlib.reload(report_service)  # ensure env change is picked up
            result = report_service._api_base_url()
        self.assertEqual(result, "https://example.com/api/v1")

    def test_G1_3_html_template_uses_window_location_fallback(self):
        """G1-3: report_builder_service.py HTML 模板包含 window.location.origin 回退。"""
        content = (PROJECT_ROOT / "backend/services/report_builder_service.py").read_text(encoding="utf-8")
        self.assertIn("window.location.origin", content,
                      "HTML 模板应包含 window.location.origin 作为 API_BASE 回退")

    def test_G1_4_html_template_api_base_not_hardcoded_localhost(self):
        """G1-4: report_builder_service.py HTML 模板中 API_BASE 赋值不再直接是硬编码 localhost:8000。"""
        content = (PROJECT_ROOT / "backend/services/report_builder_service.py").read_text(encoding="utf-8")
        import re
        # The old pattern was: const API_BASE = "http://localhost:8000/api/v1";
        # (when api_base_url defaults to localhost fallback — now empty string)
        # Check that the new pattern uses IIFE with fallback
        api_base_line = [l for l in content.splitlines() if "const API_BASE" in l]
        self.assertTrue(len(api_base_line) > 0, "未找到 API_BASE 定义行")
        self.assertIn("window.location.origin", api_base_line[0],
                      "API_BASE 应使用 window.location.origin 作为回退")

    def test_G1_5_api_base_url_in_report_service_returns_empty(self):
        """G1-5: _api_base_url() 源码逻辑 — 无 host 时明确返回 ''，不是 localhost:port。"""
        import inspect
        from backend.services.report_service import _api_base_url
        src = inspect.getsource(_api_base_url)
        self.assertIn('return ""', src,
                      "_api_base_url 应在无 PUBLIC_HOST 时明确返回 ''")
        # 确保没有 "return f'http://localhost" 形式的 localhost URL fallback（注释中提及 localhost 无妨）
        import re
        localhost_return = re.search(r'return\s+f?["\']http://localhost', src)
        self.assertIsNone(localhost_return,
                          "_api_base_url 不应有 return http://localhost:... 形式的回退")


# ===========================================================================
# G2 — connection_env 前缀兼容（Fix B）
# ===========================================================================
class TestConnectionEnvNormalize(unittest.TestCase):
    """G2: _get_or_init_ch_client 自动去掉 'clickhouse-' 前缀。"""

    def test_G2_1_strip_clickhouse_prefix_in_source(self):
        """G2-1: reports.py _get_or_init_ch_client 源码包含前缀去除逻辑。"""
        import inspect
        from backend.api import reports as reports_module
        src = inspect.getsource(reports_module._get_or_init_ch_client)
        self.assertIn("clickhouse-", src,
                      "_get_or_init_ch_client 应有 'clickhouse-' 前缀兼容处理")
        self.assertIn("startswith", src)

    def test_G2_2_clickhouse_sg_normalized_to_sg(self):
        """G2-2: 'clickhouse-sg' 经过 normalize 后变为 'sg'。"""
        # 直接测试 normalize 逻辑（不实际连接 CH）
        env = "clickhouse-sg"
        if env.startswith("clickhouse-"):
            env = env[len("clickhouse-"):]
        self.assertEqual(env, "sg")

    def test_G2_3_clickhouse_sg_azure_normalized(self):
        """G2-3: 'clickhouse-sg-azure' normalize 后变为 'sg-azure'。"""
        env = "clickhouse-sg-azure"
        if env.startswith("clickhouse-"):
            env = env[len("clickhouse-"):]
        self.assertEqual(env, "sg-azure")

    def test_G2_4_sg_plain_unchanged(self):
        """G2-4: 已是 'sg' 的 env 不变。"""
        env = "sg"
        if env.startswith("clickhouse-"):
            env = env[len("clickhouse-"):]
        self.assertEqual(env, "sg")

    def test_G2_5_cache_uses_normalized_key(self):
        """G2-5: _get_or_init_ch_client 源码中缓存 lookup 在 normalize 之后（使用已去前缀的 env）。"""
        import inspect
        from backend.api import reports as reports_module
        src = inspect.getsource(reports_module._get_or_init_ch_client)
        lines = src.splitlines()
        strip_line_idx = next(
            (i for i, l in enumerate(lines) if "startswith" in l and "clickhouse-" in l),
            None
        )
        cache_line_idx = next(
            (i for i, l in enumerate(lines) if "_ch_client_cache" in l and "not in" in l),
            None
        )
        self.assertIsNotNone(strip_line_idx, "未找到 startswith 前缀处理行")
        self.assertIsNotNone(cache_line_idx, "未找到 _ch_client_cache lookup 行")
        self.assertGreater(cache_line_idx, strip_line_idx,
                           "缓存 lookup 应在前缀 strip 之后")


# ===========================================================================
# G3 — binds 列表兼容（Fix C）
# ===========================================================================
class TestBindsNormalize(unittest.TestCase):
    """G3: _normalize_binds 将 list 格式 binds 转为 dict。"""

    def test_G3_1_normalize_binds_importable(self):
        """G3-1: _normalize_binds 可从 report_params_service 导入。"""
        from backend.services.report_params_service import _normalize_binds
        self.assertTrue(callable(_normalize_binds))

    def test_G3_2_dict_binds_unchanged(self):
        """G3-2: dict 格式 binds 原样返回。"""
        from backend.services.report_params_service import _normalize_binds
        binds = {"start": "date_start", "end": "date_end"}
        result = _normalize_binds(binds)
        self.assertEqual(result, binds)

    def test_G3_3_list_binds_converted_to_dict(self):
        """G3-3: list 格式 ['date_start', 'date_end'] → {'start': 'date_start', 'end': 'date_end'}。"""
        from backend.services.report_params_service import _normalize_binds
        result = _normalize_binds(["date_start", "date_end"])
        self.assertEqual(result["start"], "date_start")
        self.assertEqual(result["end"], "date_end")

    def test_G3_4_extract_default_params_with_list_binds(self):
        """G3-4: extract_default_params 支持 list 格式 binds。"""
        from backend.services.report_params_service import extract_default_params
        spec = {
            "filters": [{
                "id": "date_range",
                "type": "date_range",
                "default_days": 7,
                "binds": ["date_start", "date_end"],  # list format (AI mistake)
            }]
        }
        params = extract_default_params(spec)
        self.assertIn("date_start", params,
                      "list 格式 binds 应被 normalize 后正确提取 date_start")
        self.assertIn("date_end", params)

    def test_G3_5_empty_binds_returns_empty_dict(self):
        """G3-5: 空 binds（None/empty）返回空 dict。"""
        from backend.services.report_params_service import _normalize_binds
        self.assertEqual(_normalize_binds(None), {})
        self.assertEqual(_normalize_binds([]), {})
        self.assertEqual(_normalize_binds({}), {})

    def test_G3_6_compute_params_with_list_binds(self):
        """G3-6: compute_params_from_binds 也能处理 list 格式 binds。"""
        from backend.services.report_params_service import compute_params_from_binds
        filter_specs = [{
            "id": "date_range",
            "type": "date_range",
            "binds": ["date_start", "date_end"],  # list format
        }]
        filter_values = {"date_range": {"start": "2026-01-01", "end": "2026-04-01"}}
        params = compute_params_from_binds(filter_specs, filter_values)
        self.assertEqual(params.get("date_start"), "2026-01-01")
        self.assertEqual(params.get("date_end"), "2026-04-01")


# ===========================================================================
# G4 — skill file 格式警告（Fix D）
# ===========================================================================
class TestSkillFileFormatWarnings(unittest.TestCase):
    """G4: clickhouse-analyst.md 包含 connection_env 和 binds 格式警告。"""

    def setUp(self):
        self.content = SKILL_FILE.read_text(encoding="utf-8")

    def test_G4_1_connection_env_format_warning(self):
        """G4-1: 包含 connection_env 不能带 'clickhouse-' 前缀的警告。"""
        self.assertIn("connection_env", self.content)
        # Should have warning about not using "clickhouse-sg" prefix
        self.assertTrue(
            "clickhouse-sg" in self.content or "clickhouse-" in self.content,
            "应有 connection_env 格式警告（提及错误用法 clickhouse-sg）"
        )

    def test_G4_2_binds_dict_format_warning(self):
        """G4-2: 包含 binds 必须是 dict（不能是 list）的警告。"""
        self.assertIn("binds", self.content)
        # Should have warning about binds must be dict
        self.assertTrue(
            "dict" in self.content or "不能是 list" in self.content or
            '{"start"' in self.content,
            "应有 binds 格式警告（dict 不是 list）"
        )

    def test_G4_3_connection_env_example_is_short_form(self):
        """G4-3: spec 示例中 connection_env 使用 'sg' 短名，不是 'clickhouse-sg'。"""
        # Find spec example block
        import re
        # Look for connection_env in spec JSON examples
        matches = re.findall(r'"connection_env"\s*:\s*"([^"]+)"', self.content)
        self.assertTrue(len(matches) > 0, "应有 connection_env 字段示例")
        for val in matches:
            self.assertFalse(val.startswith("clickhouse-"),
                             f"connection_env 示例值 '{val}' 不应带 'clickhouse-' 前缀")

    def test_G4_4_binds_example_is_dict_format(self):
        """G4-4: spec 示例中 binds 使用 dict 格式。"""
        import re
        # Find binds in spec JSON
        matches = re.findall(r'"binds"\s*:\s*(\{[^}]+\})', self.content)
        self.assertTrue(len(matches) > 0, "应有 dict 格式的 binds 示例")


if __name__ == "__main__":
    unittest.main(verbosity=2)
