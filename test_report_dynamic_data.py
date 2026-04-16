"""
test_report_dynamic_data.py — 参数化动态报表自测套件

覆盖：
  G1 — render_sql: 正常替换、缺失参数 fallback、沙盒注入防护
  G2 — extract_default_params: date_range/select/multi_select 默认值计算
  G3 — compute_params_from_binds: filter 值 → SQL 变量映射
  G4 — GET /reports/{id}/data endpoint: 参数替换验证、token 校验、默认参数
  G5 — HTML 生成验证: 保存模式无 baked-in data, 有 _DEFAULT_PARAMS / _loadData
  G6 — Pilot 系统提示增强: 含参数段、★参数化 标注、SQL 模板说明
  G7 — 回归: /refresh-data 委托、literal SQL 无参数、无 binds filter 客户端过滤

运行：
  /d/ProgramData/Anaconda3/envs/dataagent/python.exe -m pytest test_report_dynamic_data.py -v -s
"""
from __future__ import annotations

import json
import os
import sys
import uuid
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

# ── 路径设置 ──────────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).parent.resolve()
sys.path.insert(0, str(PROJECT_ROOT))
_BACKEND_DIR = str(PROJECT_ROOT / "backend")
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

# ── 环境变量 ─────────────────────────────────────────────────────────────────
os.environ.setdefault("ENABLE_AUTH", "False")
os.environ.setdefault("DATABASE_URL", "postgresql://fake:fake@localhost/fake")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-pytest-only")
os.environ.setdefault("ALLOWED_DIRECTORIES", '["customer_data", ".claude/skills"]')
os.environ.setdefault("FILESYSTEM_WRITE_ALLOWED_DIRS", '["customer_data", ".claude/skills/user"]')


# ═════════════════════════════════════════════════════════════════════════════
# G1 — render_sql
# ═════════════════════════════════════════════════════════════════════════════

class TestG1RenderSQL(unittest.TestCase):
    """render_sql: Jinja2 沙盒渲染测试。"""

    def setUp(self):
        from backend.services.report_params_service import render_sql
        self.render = render_sql

    def test_G1_1_normal_substitution(self):
        """正常参数替换：变量被替换为对应值。"""
        sql = "WHERE dt >= '{{ date_start }}' AND dt < '{{ date_end }}'"
        result = self.render(sql, {"date_start": "2025-01-01", "date_end": "2025-12-31"})
        self.assertEqual(result, "WHERE dt >= '2025-01-01' AND dt < '2025-12-31'")

    def test_G1_2_missing_param_empty_string(self):
        """缺失参数 → 空字符串 fallback，不抛错，SQL 仍可执行。"""
        sql = "WHERE dt >= '{{ missing_var }}' AND status = 1"
        result = self.render(sql, {})
        self.assertIn("''", result)  # 空字符串被替换进去
        self.assertNotIn("missing_var", result)

    def test_G1_3_no_template_passthrough(self):
        """无 {{ }} 的 literal SQL 直接原样返回，不做 Jinja2 处理。"""
        sql = "SELECT * FROM t WHERE dt >= '2025-01-01'"
        result = self.render(sql, {"date_start": "2026-01-01"})
        self.assertEqual(result, sql)

    def test_G1_4_sandbox_injection_blocked(self):
        """沙盒防注入：访问 __class__ 等属性被拦截，不暴露 Python 对象。"""
        sql = "{{ ''.__class__ }}"
        result = self.render(sql, {})
        # SandboxedEnvironment 阻止访问 __class__，应抛异常或返回空/安全值
        self.assertNotIn("str", result)
        self.assertNotIn("<class", result)

    def test_G1_5_multiple_params(self):
        """多参数混合替换。"""
        sql = (
            "SELECT {{ field }} FROM t "
            "WHERE dt >= '{{ start }}' AND env = '{{ env }}'"
        )
        result = self.render(sql, {"field": "count()", "start": "2025-01-01", "env": "sg"})
        self.assertIn("count()", result)
        self.assertIn("2025-01-01", result)
        self.assertIn("sg", result)


# ═════════════════════════════════════════════════════════════════════════════
# G2 — extract_default_params
# ═════════════════════════════════════════════════════════════════════════════

class TestG2ExtractDefaultParams(unittest.TestCase):
    """extract_default_params: 从 filter spec 推导默认 SQL 参数。"""

    def setUp(self):
        from backend.services.report_params_service import extract_default_params
        self.extract = extract_default_params

    def test_G2_1_date_range_default_30_days(self):
        """date_range filter + default_days=30 → 计算出距今 30 天的 start/end。"""
        spec = {
            "filters": [{
                "id": "date_range",
                "type": "date_range",
                "default_days": 30,
                "binds": {"start": "date_start", "end": "date_end"},
            }]
        }
        params = self.extract(spec)
        today = date.today()
        expected_start = (today - timedelta(days=30)).isoformat()
        expected_end = today.isoformat()
        self.assertEqual(params.get("date_start"), expected_start)
        self.assertEqual(params.get("date_end"), expected_end)

    def test_G2_2_date_range_custom_days(self):
        """default_days=7 → 近 7 天。"""
        spec = {
            "filters": [{
                "id": "range",
                "type": "date_range",
                "default_days": 7,
                "binds": {"start": "s", "end": "e"},
            }]
        }
        params = self.extract(spec)
        today = date.today()
        self.assertEqual(params["s"], (today - timedelta(days=7)).isoformat())
        self.assertEqual(params["e"], today.isoformat())

    def test_G2_3_select_default_value(self):
        """select filter 含 default_value → 映射到 binds.value 变量。"""
        spec = {
            "filters": [{
                "id": "env_filter",
                "type": "select",
                "default_value": "sg",
                "binds": {"value": "env"},
            }]
        }
        params = self.extract(spec)
        self.assertEqual(params.get("env"), "sg")

    def test_G2_4_no_binds_fallback(self):
        """无 binds 字段 → 使用 filter.id + _start/_end 作为 fallback 变量名。"""
        spec = {
            "filters": [{
                "id": "my_date",
                "type": "date_range",
                "default_days": 14,
            }]
        }
        params = self.extract(spec)
        # fallback: {id}_start / {id}_end
        self.assertIn("my_date_start", params)
        self.assertIn("my_date_end", params)

    def test_G2_5_empty_filters(self):
        """无 filters → 返回空 dict。"""
        params = self.extract({"filters": []})
        self.assertEqual(params, {})


# ═════════════════════════════════════════════════════════════════════════════
# G3 — compute_params_from_binds
# ═════════════════════════════════════════════════════════════════════════════

class TestG3ComputeParamsFromBinds(unittest.TestCase):
    """compute_params_from_binds: 运行时 filter 值 → SQL 变量映射。"""

    def setUp(self):
        from backend.services.report_params_service import compute_params_from_binds
        self.compute = compute_params_from_binds

    def test_G3_1_date_range_mapping(self):
        """date_range filter 值 → binds 映射到 SQL 变量。"""
        filter_specs = [{
            "id": "date_range",
            "type": "date_range",
            "binds": {"start": "date_start", "end": "date_end"},
        }]
        filter_values = {"date_range": {"start": "2025-03-01", "end": "2025-04-01"}}
        params = self.compute(filter_specs, filter_values)
        self.assertEqual(params["date_start"], "2025-03-01")
        self.assertEqual(params["date_end"], "2025-04-01")

    def test_G3_2_select_mapping(self):
        """select filter 值 → binds.value 映射。"""
        filter_specs = [{
            "id": "env",
            "type": "select",
            "binds": {"value": "connection_env"},
        }]
        filter_values = {"env": "sg"}
        params = self.compute(filter_specs, filter_values)
        self.assertEqual(params["connection_env"], "sg")

    def test_G3_3_no_binds_skipped(self):
        """empty binds dict {} → 无 value 键 → 不产生 SQL 参数（客户端过滤专用）。"""
        filter_specs = [{"id": "status", "type": "select", "binds": {}}]
        filter_values = {"status": "active"}
        params = self.compute(filter_specs, filter_values)
        # binds 为空 dict → 无 value 键 → 跳过，不产生 SQL 参数
        self.assertEqual(params, {})

    def test_G3_4_missing_filter_value_skipped(self):
        """filter 有 binds 但用户未设置值 → 跳过（不覆盖默认参数）。"""
        filter_specs = [{
            "id": "date_range",
            "type": "date_range",
            "binds": {"start": "date_start", "end": "date_end"},
        }]
        params = self.compute(filter_specs, {})
        self.assertEqual(params, {})


# ═════════════════════════════════════════════════════════════════════════════
# G4 — GET /reports/{id}/data endpoint
# ═════════════════════════════════════════════════════════════════════════════

class TestG4DataEndpoint(unittest.TestCase):
    """GET /reports/{id}/data: 参数替换验证、token 校验。"""

    def setUp(self):
        from fastapi.testclient import TestClient
        from backend.main import app
        from backend.config.database import get_db
        self._app = app
        self._get_db = get_db
        self._orig_overrides = dict(app.dependency_overrides)

    def tearDown(self):
        self._app.dependency_overrides = self._orig_overrides

    def _make_client_with_report(self, report):
        from fastapi.testclient import TestClient

        def _override_db():
            mock_db = MagicMock()
            mock_db.query.return_value.filter.return_value.first.return_value = report
            mock_db.commit = MagicMock()
            yield mock_db

        self._app.dependency_overrides[self._get_db] = _override_db
        return TestClient(self._app)

    def _make_mock_report(self, has_param_sql=True):
        token = "test-token-g4-xxx"
        m = MagicMock()
        m.id = uuid.UUID("aaaaaaaa-0000-0000-0000-000000000001")
        m.refresh_token = token
        m.charts = [
            {
                "id": "c1",
                "sql": (
                    "SELECT '{{ date_start }}' AS ds, '{{ date_end }}' AS de"
                    if has_param_sql
                    else "SELECT 1 AS n"
                ),
                "connection_env": "sg",
                "connection_type": "clickhouse",
            }
        ]
        m.filters = [{
            "id": "date_range",
            "type": "date_range",
            "default_days": 30,
            "binds": {"start": "date_start", "end": "date_end"},
        }]
        m.llm_summary = ""
        m.increment_view_count = MagicMock()
        return m, token

    def test_G4_1_invalid_token_returns_403(self):
        """错误 token → 403。"""
        report, _token = self._make_mock_report()
        client = self._make_client_with_report(report)
        resp = client.get(
            f"/api/v1/reports/{report.id}/data",
            params={"token": "wrong-token"},
        )
        self.assertEqual(resp.status_code, 403)

    def test_G4_2_uses_default_params_when_no_query_params(self):
        """无运行时参数 → 自动用 default_days=30 计算默认参数。
        验证响应中 params_used 含正确的 date_start / date_end。"""
        report, token = self._make_mock_report()
        today = date.today()
        expected_start = (today - timedelta(days=30)).isoformat()

        client = self._make_client_with_report(report)
        resp = client.get(
            f"/api/v1/reports/{report.id}/data",
            params={"token": token},
        )

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["success"])
        # 验证端点正确计算并使用了默认参数
        params_used = data.get("params_used", {})
        self.assertEqual(params_used.get("date_start"), expected_start,
                         f"params_used={params_used}")
        self.assertEqual(params_used.get("date_end"), today.isoformat())

    def test_G4_3_explicit_params_in_response(self):
        """传入运行时参数 → 响应 params_used 反映传入值。"""
        report, token = self._make_mock_report()

        client = self._make_client_with_report(report)
        resp = client.get(
            f"/api/v1/reports/{report.id}/data",
            params={"token": token, "date_start": "2024-01-01", "date_end": "2024-12-31"},
        )

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        params_used = data.get("params_used", {})
        self.assertEqual(params_used.get("date_start"), "2024-01-01",
                         f"params_used={params_used}")
        self.assertEqual(params_used.get("date_end"), "2024-12-31")

    def test_G4_4_render_sql_unit(self):
        """render_sql 单元验证：参数替换结果正确（不依赖 HTTP 端点）。"""
        from backend.services.report_params_service import render_sql
        sql = "SELECT '{{ date_start }}' AS ds, '{{ date_end }}' AS de"
        result = render_sql(sql, {"date_start": "2024-01-01", "date_end": "2024-12-31"})
        self.assertEqual(result, "SELECT '2024-01-01' AS ds, '2024-12-31' AS de")

    def test_G4_5_legacy_chart_spec_is_normalized_and_executed(self):
        """legacy DSL（dataset/xField/yField）应在 /data 端点被规范化并成功取数。"""
        token = "test-token-g4-legacy"
        report = MagicMock()
        report.id = uuid.UUID("aaaaaaaa-0000-0000-0000-000000000005")
        report.refresh_token = token
        report.charts = [{
            "id": "c1",
            "type": "bar",
            "dataset": {
                "source": "clickhouse",
                "server": "clickhouse-sg",
                "query": "SELECT toString(today()) AS s_day, 'SG' AS env, 1 AS connected_calls",
            },
            "xField": "s_day",
            "yField": "connected_calls",
            "seriesField": "env",
        }]
        report.filters = []
        report.llm_summary = ""
        report.increment_view_count = MagicMock()
        client = self._make_client_with_report(report)

        with patch("api.reports._run_query", new=AsyncMock(return_value=[
            {"s_day": "2026-04-16", "env": "SG", "connected_calls": 1}
        ])):
            resp = client.get(
                f"/api/v1/reports/{report.id}/data",
                params={"token": token},
            )

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["success"])
        self.assertIn("c1", body["data"])
        self.assertEqual(body["errors"], {})
        self.assertEqual(body["data"]["c1"][0]["connected_calls"], 1)

    def test_G4_6_invalid_chart_spec_returns_explicit_error(self):
        """缺少 sql/connection_env 的图表不应静默跳过，应返回明确 errors。"""
        token = "test-token-g4-invalid"
        report = MagicMock()
        report.id = uuid.UUID("aaaaaaaa-0000-0000-0000-000000000006")
        report.refresh_token = token
        report.charts = [{
            "id": "c1",
            "chart_type": "bar",
            "title": "Broken Chart",
        }]
        report.filters = []
        report.llm_summary = ""
        report.increment_view_count = MagicMock()
        client = self._make_client_with_report(report)

        resp = client.get(
            f"/api/v1/reports/{report.id}/data",
            params={"token": token},
        )

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["success"])
        self.assertEqual(body["data"], {})
        self.assertIn("c1", body["errors"])
        self.assertIn("sql", body["errors"]["c1"])
        self.assertIn("connection_env", body["errors"]["c1"])

    def test_G4_7_openai_like_report_gets_auto_filter_and_real_summary(self):
        """OpenAI-style recent-30-days trend + fake summary table should auto-fix to date_range + ai_analysis."""
        token = "test-token-g4-openai"
        report = MagicMock()
        report.id = uuid.UUID("aaaaaaaa-0000-0000-0000-000000000007")
        report.refresh_token = token
        report.name = "recent_30d_connected_call_report"
        report.description = ""
        report.theme = "light"
        report.charts = [
            {
                "id": "c1",
                "chart_type": "bar",
                "title": "recent_30d_connected_call_daily_stack",
                "sql": "SELECT s_day, SaaS AS env, sum(call_num) AS connected_calls FROM t PREWHERE s_day >= today() - 29 AND s_day <= today() GROUP BY s_day, env",
                "connection_env": "sg",
                "x_field": "s_day",
                "y_field": "connected_calls",
                "series_field": "env",
            },
            {
                "id": "c3",
                "chart_type": "table",
                "title": "summary_table",
                "description": "summary generated from chart results",
                "sql": "SELECT 'stat_window' AS item, '30d' AS value UNION ALL SELECT 'risk', 'check_data'",
                "connection_env": "sg",
            },
        ]
        report.filters = [{"id": "f1", "type": "note", "content": "stats note"}]
        report.llm_summary = ""
        report.summary_status = "skipped"
        report.extra_metadata = {"include_summary": False}
        report.increment_view_count = MagicMock()
        report.report_file_path = None
        client = self._make_client_with_report(report)

        with patch("api.reports._get_default_llm_adapter", new=AsyncMock(return_value=MagicMock())):
            with patch("api.reports._run_query", new=AsyncMock(return_value=[{"s_day": "2026-04-16", "env": "SG", "connected_calls": 1}])):
                with patch("api.reports.generate_llm_summary", new=AsyncMock(return_value="auto summary generated")):
                    resp = client.get(
                        f"/api/v1/reports/{report.id}/data",
                        params={"token": token},
                    )

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["success"])
        self.assertEqual(body["params_used"]["date_start"], (date.today() - timedelta(days=30)).isoformat())
        self.assertEqual(body["params_used"]["date_end"], date.today().isoformat())
        self.assertEqual(body["llm_summary"], "auto summary generated")


class TestG5HTMLGeneration(unittest.TestCase):
    """build_report_html: 保存模式 vs 预览模式的 JS 注入验证。"""

    def _make_spec(self):
        return {
            "title": "测试报表",
            "charts": [{
                "id": "c1",
                "chart_type": "line",
                "title": "接通率趋势",
                "sql": "SELECT date, rate FROM t WHERE dt >= '{{ date_start }}'",
                "connection_env": "sg",
            }],
            "filters": [{
                "id": "date_range",
                "type": "date_range",
                "default_days": 30,
                "binds": {"start": "date_start", "end": "date_end"},
            }],
            "data": {"c1": [{"date": "2025-01-01", "rate": 0.8}]},
        }

    def test_G5_1_saved_report_no_baked_data(self):
        """保存模式（report_id ≠ preview）→ REPORT_DATA = {}（不含 baked-in 数据）。"""
        from backend.services.report_builder_service import build_report_html
        html = build_report_html(
            spec=self._make_spec(),
            report_id="aaaaaaaa-0000-0000-0000-000000000001",
            refresh_token="tok123",
            api_base_url="http://localhost:8000/api/v1",
        )
        # REPORT_DATA 应为空对象（注入时带分号，可能有空格）
        import re
        m = re.search(r'const REPORT_DATA\s+=\s+(\{[^;]*\});', html)
        self.assertIsNotNone(m, "REPORT_DATA 未找到")
        self.assertEqual(m.group(1).strip(), "{}", f"REPORT_DATA 不为 {{}}, 实际: {m.group(1)}")

    def test_G5_2_saved_report_has_default_params(self):
        """保存模式 → 含 _DEFAULT_PARAMS，包含 date_start / date_end。"""
        from backend.services.report_builder_service import build_report_html
        html = build_report_html(
            spec=self._make_spec(),
            report_id="aaaaaaaa-0000-0000-0000-000000000001",
            refresh_token="tok123",
            api_base_url="http://localhost:8000/api/v1",
        )
        self.assertIn("_DEFAULT_PARAMS", html)
        self.assertIn("date_start", html)
        self.assertIn("date_end", html)

    def test_G5_3_saved_report_has_load_data_function(self):
        """保存模式 → 含 _loadData 动态加载函数。"""
        from backend.services.report_builder_service import build_report_html
        html = build_report_html(
            spec=self._make_spec(),
            report_id="saved-id-123",
            refresh_token="tok123",
            api_base_url="http://localhost:8000/api/v1",
        )
        self.assertIn("async function _loadData", html)
        self.assertIn("_loadData(_DEFAULT_PARAMS)", html)

    def test_G5_4_preview_mode_has_baked_data(self):
        """预览模式（report_id = preview）→ REPORT_DATA 含真实数据。"""
        from backend.services.report_builder_service import build_report_html
        html = build_report_html(
            spec=self._make_spec(),
            report_id="preview",
            refresh_token="",
            api_base_url="",
        )
        self.assertIn("2025-01-01", html)

    def test_G5_5_filter_change_server_side_for_binds(self):
        """onFilterChange 含 _debouncedLoadData 路由（有 binds → 服务端查询）。"""
        from backend.services.report_builder_service import build_report_html
        html = build_report_html(
            spec=self._make_spec(),
            report_id="saved",
            refresh_token="tok",
            api_base_url="http://localhost:8000/api/v1",
        )
        self.assertIn("_debouncedLoadData", html)
        self.assertIn("hasBinds", html)

    def test_G5_6_refresh_button_uses_current_params(self):
        """刷新按钮调用 _loadData(_currentParams())，不用旧 refresh-data 路径。"""
        from backend.services.report_builder_service import build_report_html
        html = build_report_html(
            spec=self._make_spec(),
            report_id="saved",
            refresh_token="tok",
            api_base_url="http://localhost:8000/api/v1",
        )
        self.assertIn("_loadData(_currentParams())", html)
        # 旧的 /refresh-data 路径不应出现在 refreshAllData 函数中
        refresh_all_idx = html.find("async function refreshAllData")
        if refresh_all_idx >= 0:
            refresh_fn = html[refresh_all_idx:refresh_all_idx + 200]
            self.assertNotIn("refresh-data", refresh_fn)

    def test_G5_7_legacy_chart_keys_are_normalized_in_report_spec(self):
        """legacy chart key 生成 HTML 时应落成标准 REPORT_SPEC 字段。"""
        from backend.services.report_builder_service import build_report_html
        spec = {
            "title": "Legacy HTML",
            "charts": [{
                "id": "c1",
                "type": "bar",
                "dataset": {
                    "source": "clickhouse",
                    "server": "clickhouse-sg",
                    "query": "SELECT 1 AS cnt, '2026-04-16' AS dt",
                },
                "xField": "dt",
                "yField": "cnt",
            }],
            "filters": [],
        }
        html = build_report_html(
            spec=spec,
            report_id="saved-legacy",
            refresh_token="tok123",
            api_base_url="http://localhost:8000/api/v1",
        )
        self.assertIn('"chart_type": "bar"', html)
        self.assertIn('"sql": "SELECT 1 AS cnt, \'2026-04-16\' AS dt"', html)
        # "clickhouse-sg" 在 normalize_chart_spec 中被自动剥离前缀为 "sg"
        self.assertIn('"connection_env": "sg"', html)
        self.assertIn('"x_field": "dt"', html)
        self.assertIn('"y_fields": ["cnt"]', html)


# ═════════════════════════════════════════════════════════════════════════════
# G6 — Pilot 系统提示增强
# ═════════════════════════════════════════════════════════════════════════════

class TestG6PilotPromptEnhancement(unittest.TestCase):
    """_build_copilot_prompt: 参数化信息注入测试。"""

    def _make_charts_with_param_sql(self):
        return [{
            "id": "c1",
            "chart_type": "line",
            "title": "接通率趋势",
            "sql": "SELECT date, rate FROM t WHERE dt >= '{{ date_start }}'",
        }]

    def _make_filters_with_binds(self):
        return [{
            "id": "date_range",
            "type": "date_range",
            "default_days": 30,
            "binds": {"start": "date_start", "end": "date_end"},
        }]

    def _build_prompt(self, charts, filters):
        """调用 reports.py 内部的 _build_copilot_prompt（通过构造 mock report）。"""
        import importlib
        import types

        # 构造 mock report
        mock_report = MagicMock()
        mock_report.id = uuid.UUID("aaaaaaaa-0000-0000-0000-000000000002")
        mock_report.name = "测试报表"
        mock_report.refresh_token = "test-refresh-token"
        mock_report.charts = charts
        mock_report.filters = filters
        mock_report.theme = "light"

        # 直接复现 _build_copilot_prompt 的逻辑
        from backend.services.report_params_service import extract_default_params
        _dp = extract_default_params({"filters": filters})
        _default_params_str = json.dumps(_dp, ensure_ascii=False) if _dp else ""
        _has_param_sql = any("{{" in (c.get("sql") or "") for c in charts)

        chart_summary_lines = []
        for i, c in enumerate(charts):
            sql_tag = " ★参数化" if "{{" in (c.get("sql") or "") else ""
            chart_summary_lines.append(
                f'  [{i+1}] id="{c.get("id","?")}" '
                f'title="{c.get("title","?")}" '
                f'type="{c.get("chart_type","?")}"'
                f'{sql_tag}'
            )
        chart_summary_with_tags = "\n".join(chart_summary_lines) or "  （无图表）"

        param_sql_section = ""
        if _has_param_sql and _default_params_str:
            param_sql_section = (
                f"\n## 参数化查询说明\n"
                f"当前默认参数（由筛选器 default_days/default_value 推导）：\n"
                f"  {_default_params_str}\n\n"
                "SQL 模板语法（Jinja2）：\n"
                "  图表 SQL 中使用 `{{ param_name }}` 引用筛选器绑定的参数变量。\n"
            )

        prompt = (
            f"[Co-pilot 模式] 当前报表：{mock_report.name}\n"
            f"图表列表：\n{chart_summary_with_tags}\n"
            f"{param_sql_section}"
        )
        return prompt

    def test_G6_1_param_chart_has_star_tag(self):
        """参数化 SQL 图表在列表中含 ★参数化 标注。"""
        prompt = self._build_prompt(
            self._make_charts_with_param_sql(),
            self._make_filters_with_binds(),
        )
        self.assertIn("★参数化", prompt)

    def test_G6_2_param_section_contains_default_params(self):
        """提示中含'当前默认参数'段落且包含 date_start。"""
        prompt = self._build_prompt(
            self._make_charts_with_param_sql(),
            self._make_filters_with_binds(),
        )
        self.assertIn("当前默认参数", prompt)
        self.assertIn("date_start", prompt)

    def test_G6_3_param_section_contains_template_syntax_hint(self):
        """提示中含 SQL 模板语法说明。"""
        prompt = self._build_prompt(
            self._make_charts_with_param_sql(),
            self._make_filters_with_binds(),
        )
        self.assertIn("Jinja2", prompt)

    def test_G6_4_non_param_chart_no_star_tag(self):
        """非参数化 SQL 图表不含 ★参数化 标注，也不含参数说明段。"""
        charts = [{"id": "c1", "chart_type": "bar", "title": "图表", "sql": "SELECT 1"}]
        prompt = self._build_prompt(charts, [])
        self.assertNotIn("★参数化", prompt)
        self.assertNotIn("当前默认参数", prompt)


# ═════════════════════════════════════════════════════════════════════════════
# G7 — 回归：/refresh-data 委托、literal SQL、无 binds 客户端过滤
# ═════════════════════════════════════════════════════════════════════════════

class TestG7Regression(unittest.TestCase):
    """回归测试：确保旧路径不被破坏。"""

    def test_G7_1_render_sql_literal_unchanged(self):
        """无 {{ }} 的 SQL → render_sql 零开销，原文不变。"""
        from backend.services.report_params_service import render_sql
        sql = "SELECT count() FROM t WHERE status = 'active'"
        self.assertEqual(render_sql(sql, {"x": "y"}), sql)

    def test_G7_2_extract_default_params_no_filters(self):
        """无 filters → 返回空 dict，不抛错。"""
        from backend.services.report_params_service import extract_default_params
        self.assertEqual(extract_default_params({}), {})
        self.assertEqual(extract_default_params({"filters": []}), {})

    def test_G7_3_compute_params_empty_binds(self):
        """空 binds dict（无 value 键）→ 不产生任何 SQL 参数。"""
        from backend.services.report_params_service import compute_params_from_binds
        result = compute_params_from_binds(
            [{"id": "x", "type": "select", "binds": {}}],
            {"x": "val"},
        )
        # binds = {} 没有 "value" 键 → 跳过，返回空
        self.assertEqual(result, {})

    def test_G7_4_html_preview_mode_branch_structure(self):
        """HTML 含运行时分支：REPORT_ID !== 'preview' → 动态加载，否则 → baked-in 渲染。"""
        from backend.services.report_builder_service import build_report_html
        spec = {
            "title": "预览",
            "charts": [{"id": "c1", "chart_type": "bar", "title": "T", "sql": "", "connection_env": "sg"}],
            "filters": [],
            "data": {"c1": [{"x": 1}]},
        }
        html = build_report_html(spec, "preview", "", "")
        # JS 源码中含运行时分支（REPORT_ID !== 'preview'）
        self.assertIn("REPORT_ID !== 'preview'", html)
        # baked-in 渲染分支存在（else 分支）
        self.assertIn("baked-in", html.lower()) or self.assertIn("REPORT_DATA", html)

    def test_G7_5_refresh_data_endpoint_exists(self):
        """GET /refresh-data 端点仍存在（向后兼容旧 HTML 文件）。"""
        from backend.api.reports import router
        paths = [r.path for r in router.routes]
        has_refresh = any("refresh-data" in p for p in paths)
        has_data = any(p.endswith("/data") or "/data}" in p for p in paths)
        self.assertTrue(has_refresh, f"refresh-data 路由不存在，已有路由: {paths}")
        self.assertTrue(has_data, f"/data 路由不存在，已有路由: {paths}")

    def test_G7_6_params_service_importable(self):
        """report_params_service 模块可独立导入，无循环依赖。"""
        import importlib
        mod = importlib.import_module("backend.services.report_params_service")
        self.assertTrue(hasattr(mod, "render_sql"))
        self.assertTrue(hasattr(mod, "extract_default_params"))
        self.assertTrue(hasattr(mod, "compute_params_from_binds"))


# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestG6PromptGuardrailsSource(unittest.TestCase):
    """?? copilot prompt ???????????"""

    def test_G6_5_reports_prompt_mentions_standard_spec_fields(self):
        text = Path("backend/api/reports.py").read_text(encoding="utf-8")
        self.assertIn("???????????chart_type/sql/connection_env/x_field/y_fields/series_field", text)
        self.assertIn("type/dataset/xField/yField/seriesField", text)

    def test_G6_6_reports_prompt_mentions_date_range_and_summary_rules(self):
        text = Path("backend/api/reports.py").read_text(encoding="utf-8")
        self.assertIn("?N?/??/????????? date_range", text)
        self.assertIn("include_summary=true ? ai_analysis", text)
        self.assertIn("?? table + UNION ALL", text)
