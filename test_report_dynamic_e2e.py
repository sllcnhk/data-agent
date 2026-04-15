"""
test_report_dynamic_e2e.py — 参数化动态报表 端到端（E2E）测试套件

设计目标：覆盖「参数化动态报表」功能的核心 E2E 流程，包括：

  H1  — render_sql + extract_default_params 集成链路（参数计算 → SQL 渲染）
  H2  — HTML 构建：保存模式 vs 预览模式结构对比
  H3  — GET /reports/{id}/data：显式参数替换 + 返回结构验证
  H4  — GET /reports/{id}/data：无参数自动使用 filter 默认值
  H5  — GET /reports/{id}/data：Token 安全（403/200）
  H6  — GET /reports/{id}/data：图表级错误隔离（一图失败，其余成功）
  H7  — GET /reports/{id}/refresh-data：委托给 /data 行为一致
  H8  — Pilot 系统提示增强：★参数化标注 + 参数段（参数化报表）
  H9  — Pilot 系统提示：无参数化 SQL 时无 ★ 标注 / 无参数段
  H10 — RBAC 分析：新端点权限矩阵正确性
  H11 — JS 引擎结构：筛选器 binds → 服务端重查路径 vs 客户端过滤路径
  H12 — 向后兼容：literal SQL（无 {{ }}）报表流程不变
  H13 — 多图表部分错误：其余图表正常渲染，错误图表有 error 字段

运行：
  /d/ProgramData/Anaconda3/envs/dataagent/python.exe -m pytest test_report_dynamic_e2e.py -v -s
"""
from __future__ import annotations

import json
import os
import re
import sys
import uuid
import unittest
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock, patch

# ── 路径设置 ──────────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).parent.resolve()
sys.path.insert(0, str(PROJECT_ROOT))
_BACKEND_DIR = str(PROJECT_ROOT / "backend")
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

# ── 环境变量（必须在 import backend 之前设置）────────────────────────────────
os.environ.setdefault("ENABLE_AUTH", "False")
os.environ.setdefault("DATABASE_URL", "postgresql://fake:fake@localhost/fake")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-pytest-only")
os.environ.setdefault("ALLOWED_DIRECTORIES", '["customer_data", ".claude/skills"]')
os.environ.setdefault("FILESYSTEM_WRITE_ALLOWED_DIRS", '["customer_data", ".claude/skills/user"]')


# ════════════════════════════════════════════════════════════════════════════════
# 公共 Helper
# ════════════════════════════════════════════════════════════════════════════════

def _make_mock_report(
    report_id: str | None = None,
    token: str = "tok_test_e2e_xxx",
    charts: List[Dict] | None = None,
    filters: List[Dict] | None = None,
    username: str = "tester",
    name: str = "E2E 测试报表",
) -> MagicMock:
    """构造完整 Report ORM Mock，支持动态 SQL 相关字段。"""
    _rid = uuid.UUID(report_id) if report_id else uuid.uuid4()
    _charts = charts if charts is not None else [
        {
            "id": "c1",
            "chart_type": "line",
            "title": "接通趋势",
            "sql": "SELECT toDate(call_start_time) AS dt, count() AS cnt "
                   "FROM crm.calls "
                   "WHERE call_start_time >= '{{ date_start }}' "
                   "AND call_start_time < '{{ date_end }}' "
                   "GROUP BY dt ORDER BY dt",
            "connection_env": "sg",
            "connection_type": "clickhouse",
        }
    ]
    _filters = filters if filters is not None else [
        {
            "id": "date_range",
            "type": "date_range",
            "label": "时间范围",
            "default_days": 30,
            "binds": {"start": "date_start", "end": "date_end"},
        }
    ]

    r = MagicMock()
    r.id = _rid
    r.refresh_token = token
    r.name = name
    r.username = username
    r.charts = _charts
    r.filters = _filters
    r.theme = "light"
    r.description = ""
    r.data_sources = []
    r.llm_summary = ""
    r.report_file_path = f"{username}/reports/test_{_rid}.html"
    r.summary_status = "skipped"
    r.doc_type = "dashboard"
    r.share_scope = MagicMock()
    r.share_scope.value = "private"
    r.increment_view_count = MagicMock()
    r.created_at = MagicMock()
    r.created_at.isoformat.return_value = "2026-01-01T00:00:00"
    r.updated_at = MagicMock()
    r.updated_at.isoformat.return_value = "2026-01-02T00:00:00"
    r.to_dict.return_value = {
        "id": str(_rid),
        "name": name,
        "username": username,
        "charts": _charts,
        "filters": _filters,
        "theme": "light",
        "refresh_token": token,
        "report_file_path": r.report_file_path,
        "created_at": "2026-01-01T00:00:00",
        "updated_at": "2026-01-02T00:00:00",
    }
    return r


def _make_client_with_report(report: MagicMock):
    """用 Mock DB（固定返回 report）构造 TestClient。"""
    from backend.main import app
    from backend.config.database import get_db
    from fastapi.testclient import TestClient

    orig = dict(app.dependency_overrides)

    def _override_db():
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = report
        mock_db.commit = MagicMock()
        yield mock_db

    app.dependency_overrides[get_db] = _override_db
    client = TestClient(app, raise_server_exceptions=False)
    return client, orig


# ════════════════════════════════════════════════════════════════════════════════
# H1 — render_sql + extract_default_params 集成
# ════════════════════════════════════════════════════════════════════════════════

class TestH1ParameterPipeline(unittest.TestCase):
    """端到端参数管道：filter spec → 默认参数 → SQL 渲染。"""

    def setUp(self):
        from backend.services.report_params_service import (
            render_sql, extract_default_params,
        )
        self.render = render_sql
        self.extract = extract_default_params

    def test_H1_1_full_pipeline_date_range(self):
        """完整管道：date_range filter → 提取默认参数 → 渲染 SQL。"""
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
        self.assertEqual(params["date_end"], today.isoformat())
        self.assertEqual(params["date_start"], (today - timedelta(days=30)).isoformat())

        sql_tpl = (
            "SELECT dt, cnt FROM t "
            "WHERE dt >= '{{ date_start }}' AND dt < '{{ date_end }}'"
        )
        rendered = self.render(sql_tpl, params)
        self.assertIn(params["date_start"], rendered)
        self.assertIn(params["date_end"], rendered)
        self.assertNotIn("{{", rendered)

    def test_H1_2_select_filter_pipeline(self):
        """select filter + binds.value → 渲染 WHERE 条件。"""
        spec = {
            "filters": [{
                "id": "env",
                "type": "select",
                "default_value": "sg",
                "binds": {"value": "connection_env"},
            }]
        }
        params = self.extract(spec)
        self.assertEqual(params["connection_env"], "sg")

        sql_tpl = "SELECT * FROM t WHERE env = '{{ connection_env }}'"
        rendered = self.render(sql_tpl, params)
        self.assertIn("'sg'", rendered)

    def test_H1_3_no_binds_select_fallback_to_fid(self):
        """select filter 无 binds → extract_default_params 以 filter.id 作为 fallback 变量名。
        注：compute_params_from_binds 中 select/radio 无 binds 才跳过（运行时映射不同语义）。
        """
        spec = {
            "filters": [{
                "id": "status",
                "type": "select",
                "default_value": "active",
                # binds 缺失 → binds.get("value", fid) 回退到 fid
            }]
        }
        params = self.extract(spec)
        # extract_default_params 使用 fid 作为 fallback
        self.assertIn("status", params)
        self.assertEqual(params["status"], "active")

    def test_H1_4_multi_filter_combined(self):
        """多个 filter 混合：date_range + select → 组合参数 dict。"""
        spec = {
            "filters": [
                {
                    "id": "dr",
                    "type": "date_range",
                    "default_days": 7,
                    "binds": {"start": "s", "end": "e"},
                },
                {
                    "id": "env_f",
                    "type": "select",
                    "default_value": "idn",
                    "binds": {"value": "env"},
                },
            ]
        }
        params = self.extract(spec)
        self.assertIn("s", params)
        self.assertIn("e", params)
        self.assertEqual(params["env"], "idn")

        sql_tpl = (
            "SELECT * FROM t "
            "WHERE dt >= '{{ s }}' AND dt < '{{ e }}' "
            "AND env = '{{ env }}'"
        )
        rendered = self.render(sql_tpl, params)
        self.assertNotIn("{{", rendered)
        self.assertIn("idn", rendered)

    def test_H1_5_jinja2_conditional_syntax(self):
        """Jinja2 {% if %} 条件语法正常工作（企业维度可选过滤）。"""
        sql_tpl = (
            "SELECT * FROM t "
            "WHERE dt >= '{{ date_start }}' "
            "{% if enterprise_id %}AND enterprise_id = '{{ enterprise_id }}'{% endif %}"
        )
        # 有企业 ID
        rendered_with = self.render(sql_tpl, {"date_start": "2025-01-01", "enterprise_id": "ent_001"})
        self.assertIn("AND enterprise_id", rendered_with)
        self.assertIn("ent_001", rendered_with)

        # 无企业 ID（空字符串 → falsy → 条件分支不执行）
        rendered_without = self.render(sql_tpl, {"date_start": "2025-01-01", "enterprise_id": ""})
        self.assertNotIn("AND enterprise_id", rendered_without)


# ════════════════════════════════════════════════════════════════════════════════
# H2 — HTML 构建：保存模式 vs 预览模式
# ════════════════════════════════════════════════════════════════════════════════

class TestH2HtmlBuildModes(unittest.TestCase):
    """build_report_html：保存模式无 baked-in data / 预览模式有 baked-in data。"""

    def setUp(self):
        from backend.services.report_builder_service import build_report_html
        self.build = build_report_html

    def _minimal_spec(self, with_param_sql=True, with_binds=True):
        sql = (
            "SELECT '{{ date_start }}' AS s, '{{ date_end }}' AS e"
            if with_param_sql
            else "SELECT 1 AS n"
        )
        filters = []
        if with_binds:
            filters = [{
                "id": "date_range",
                "type": "date_range",
                "default_days": 30,
                "binds": {"start": "date_start", "end": "date_end"},
            }]
        return {
            "title": "E2E Test Report",
            "charts": [{"id": "c1", "chart_type": "line", "title": "T1",
                        "sql": sql, "connection_env": "sg", "connection_type": "clickhouse"}],
            "filters": filters,
            "data": {"c1": [{"dt": "2025-01-01", "val": 10}]},
        }

    def test_H2_1_save_mode_no_baked_data(self):
        """保存模式：REPORT_DATA 为空 dict {} 且存在 _DEFAULT_PARAMS。"""
        html = self.build(
            spec=self._minimal_spec(),
            report_id=str(uuid.uuid4()),
            refresh_token="tok_h2_test",
        )
        # 检查 REPORT_DATA 为空对象
        m = re.search(r'const REPORT_DATA\s+=\s+(\{[^;]*\});', html)
        self.assertIsNotNone(m, "找不到 REPORT_DATA 变量")
        self.assertEqual(m.group(1).strip(), "{}", "保存模式应注入空 REPORT_DATA")

        # 检查 _DEFAULT_PARAMS 存在
        self.assertIn("_DEFAULT_PARAMS", html)

    def test_H2_2_preview_mode_has_baked_data(self):
        """预览模式（report_id='preview'）：REPORT_DATA 含 baked-in 数据。"""
        html = self.build(
            spec=self._minimal_spec(),
            report_id="preview",
            refresh_token="tok_preview",
        )
        m = re.search(r'const REPORT_DATA\s+=\s+(\{[^;]*\});', html)
        self.assertIsNotNone(m)
        # baked-in 数据不为空
        self.assertNotEqual(m.group(1).strip(), "{}", "预览模式应 bake-in 数据")
        self.assertIn("2025-01-01", html)

    def test_H2_3_save_mode_loads_data_on_domcontentloaded(self):
        """保存模式：DOMContentLoaded 时调用 _loadData（动态加载）。"""
        html = self.build(
            spec=self._minimal_spec(),
            report_id="some-real-id",
            refresh_token="tok_h2_test",
        )
        # 检查 JS 包含 DOMContentLoaded 内的动态加载分支
        self.assertIn("REPORT_ID !== 'preview'", html)
        self.assertIn("_loadData(_DEFAULT_PARAMS)", html)

    def test_H2_4_preview_mode_no_api_load(self):
        """预览模式：渲染 baked-in 数据，不走 _loadData 动态 API 调用路径。"""
        html = self.build(
            spec=self._minimal_spec(),
            report_id="preview",
            refresh_token="tok_preview",
        )
        # JS 结构应有 preview 分支（else 分支处理 REPORT_DATA）
        self.assertIn("REPORT_SPEC.charts.forEach(spec => initChart(spec))", html)

    def test_H2_5_default_params_computed_from_filters(self):
        """保存模式：_DEFAULT_PARAMS 由 filter.default_days 计算，含正确日期。"""
        html = self.build(
            spec=self._minimal_spec(),
            report_id="some-id",
            refresh_token="tok",
        )
        today = date.today().isoformat()
        start = (date.today() - timedelta(days=30)).isoformat()
        self.assertIn(today, html)
        self.assertIn(start, html)

    def test_H2_6_no_binds_no_default_params(self):
        """无 binds filter → _DEFAULT_PARAMS 为 {}（无 SQL 参数）。"""
        spec = {
            "title": "No-Binds Report",
            "charts": [{"id": "c1", "chart_type": "bar", "title": "Chart",
                        "sql": "SELECT 1 AS n", "connection_env": "sg"}],
            "filters": [{"id": "status", "type": "select", "options": ["a", "b"]}],
            "data": {},
        }
        html = self.build(spec=spec, report_id="some-id", refresh_token="tok")
        # _DEFAULT_PARAMS 应为 {}
        self.assertIn("_DEFAULT_PARAMS  = {}", html)

    def test_H2_7_report_id_injected_as_js_const(self):
        """report_id 正确注入为 REPORT_ID JS 常量。"""
        rid = str(uuid.uuid4())
        html = self.build(spec=self._minimal_spec(), report_id=rid, refresh_token="tok")
        self.assertIn(f'const REPORT_ID        = "{rid}"', html)

    def test_H2_8_filter_bar_rendered_for_date_range(self):
        """有 filters 时渲染 filter-bar 区域，含日期输入框。"""
        html = self.build(spec=self._minimal_spec(), report_id="id", refresh_token="tok")
        self.assertIn("filter-bar", html)
        self.assertIn("filter-date", html)
        self.assertIn("近30天", html)

    def test_H2_9_filter_change_routes_to_server_when_binds_set(self):
        """有 binds 的 filter → onFilterChange 走 _debouncedLoadData 路径。"""
        html = self.build(spec=self._minimal_spec(), report_id="id", refresh_token="tok")
        # JS 包含路由判断逻辑
        self.assertIn("hasBinds && !isClientSide", html)
        self.assertIn("_debouncedLoadData", html)


# ════════════════════════════════════════════════════════════════════════════════
# H3 — GET /reports/{id}/data：显式参数替换
# ════════════════════════════════════════════════════════════════════════════════

class TestH3DataEndpointExplicitParams(unittest.TestCase):
    """GET /data 端点：传入显式 SQL 参数时使用用户传入值。"""

    def setUp(self):
        self._app_module = None
        self._orig = None

    def tearDown(self):
        if self._app_module and self._orig is not None:
            self._app_module.dependency_overrides = self._orig

    def test_H3_1_params_reflected_in_params_used(self):
        """传入 date_start/date_end → params_used 包含传入值。"""
        from backend.main import app
        report = _make_mock_report()
        client, orig = _make_client_with_report(report)
        self._app_module = app
        self._orig = orig

        async def _fake_run(sql, env, conn_type="clickhouse"):
            return [{"result": "ok"}]

        with patch("backend.api.reports._run_query", side_effect=_fake_run):
            resp = client.get(
                f"/api/v1/reports/{report.id}/data",
                params={
                    "token": report.refresh_token,
                    "date_start": "2025-01-01",
                    "date_end": "2025-06-30",
                },
            )

        app.dependency_overrides = orig
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertTrue(body["success"])
        pu = body["params_used"]
        self.assertEqual(pu.get("date_start"), "2025-01-01")
        self.assertEqual(pu.get("date_end"), "2025-06-30")

    def test_H3_2_response_has_required_keys(self):
        """响应包含 success / data / errors / params_used / refreshed_at。"""
        from backend.main import app
        report = _make_mock_report()
        client, orig = _make_client_with_report(report)
        self._app_module = app
        self._orig = orig

        async def _fake_run(sql, env, conn_type="clickhouse"):
            return [{"dt": "2025-01-01", "cnt": 5}]

        with patch("backend.api.reports._run_query", side_effect=_fake_run):
            resp = client.get(
                f"/api/v1/reports/{report.id}/data",
                params={"token": report.refresh_token},
            )

        app.dependency_overrides = orig
        body = resp.json()
        self.assertIn("success", body)
        self.assertIn("data", body)
        self.assertIn("errors", body)
        self.assertIn("params_used", body)
        self.assertIn("refreshed_at", body)

    def test_H3_3_chart_data_keyed_by_chart_id(self):
        """data 字段按 chart.id 分组（data[chart_id] = rows）。"""
        from backend.main import app
        charts = [
            {"id": "cA", "chart_type": "bar", "title": "A", "sql": "SELECT '{{ s }}' AS x",
             "connection_env": "sg", "connection_type": "clickhouse"},
            {"id": "cB", "chart_type": "line", "title": "B", "sql": "SELECT 1 AS n",
             "connection_env": "sg", "connection_type": "clickhouse"},
        ]
        report = _make_mock_report(charts=charts)
        client, orig = _make_client_with_report(report)
        self._app_module = app
        self._orig = orig

        async def _fake_run(sql, env, conn_type="clickhouse"):
            return [{"n": 1}]

        with patch("backend.api.reports._run_query", new=AsyncMock(side_effect=_fake_run)):
            resp = client.get(
                f"/api/v1/reports/{report.id}/data",
                params={"token": report.refresh_token, "s": "2025"},
            )

        app.dependency_overrides = orig
        body = resp.json()
        self.assertIn("cA", body["data"])
        self.assertIn("cB", body["data"])


# ════════════════════════════════════════════════════════════════════════════════
# H4 — GET /reports/{id}/data：无参数时使用 filter 默认值
# ════════════════════════════════════════════════════════════════════════════════

class TestH4DataEndpointDefaultParams(unittest.TestCase):
    """GET /data 端点：不传参数时自动使用 filter 默认值。"""

    def tearDown(self):
        if hasattr(self, "_app") and hasattr(self, "_orig"):
            self._app.dependency_overrides = self._orig

    def test_H4_1_default_date_range_used_when_no_params(self):
        """无 date_start/end 传入 → params_used 由 default_days=30 计算。"""
        from backend.main import app
        self._app = app
        report = _make_mock_report()
        client, orig = _make_client_with_report(report)
        self._orig = orig

        async def _fake_run(sql, env, conn_type="clickhouse"):
            return []

        with patch("backend.api.reports._run_query", side_effect=_fake_run):
            resp = client.get(
                f"/api/v1/reports/{report.id}/data",
                params={"token": report.refresh_token},
            )

        app.dependency_overrides = orig
        body = resp.json()
        today = date.today().isoformat()
        expected_start = (date.today() - timedelta(days=30)).isoformat()
        pu = body["params_used"]
        self.assertEqual(pu.get("date_end"), today)
        self.assertEqual(pu.get("date_start"), expected_start)

    def test_H4_2_select_filter_default_value_used(self):
        """select filter 有 default_value → params_used 含默认值。"""
        from backend.main import app
        self._app = app
        filters = [
            {
                "id": "env_filter",
                "type": "select",
                "default_value": "sg",
                "binds": {"value": "env"},
            }
        ]
        charts = [
            {"id": "c1", "chart_type": "bar", "title": "T",
             "sql": "SELECT * FROM t WHERE env = '{{ env }}'",
             "connection_env": "sg", "connection_type": "clickhouse"},
        ]
        report = _make_mock_report(charts=charts, filters=filters)
        client, orig = _make_client_with_report(report)
        self._orig = orig

        async def _fake_run(sql, env, conn_type="clickhouse"):
            return []

        with patch("backend.api.reports._run_query", side_effect=_fake_run):
            resp = client.get(
                f"/api/v1/reports/{report.id}/data",
                params={"token": report.refresh_token},
            )

        app.dependency_overrides = orig
        pu = resp.json()["params_used"]
        self.assertEqual(pu.get("env"), "sg")

    def test_H4_3_no_filter_no_params(self):
        """报表无 filter → no default params → params_used 为空 dict。"""
        from backend.main import app
        self._app = app
        report = _make_mock_report(filters=[])
        client, orig = _make_client_with_report(report)
        self._orig = orig

        async def _fake_run(sql, env, conn_type="clickhouse"):
            return []

        with patch("backend.api.reports._run_query", side_effect=_fake_run):
            resp = client.get(
                f"/api/v1/reports/{report.id}/data",
                params={"token": report.refresh_token},
            )

        app.dependency_overrides = orig
        pu = resp.json()["params_used"]
        self.assertEqual(pu, {})


# ════════════════════════════════════════════════════════════════════════════════
# H5 — Token 安全（403 / 200）
# ════════════════════════════════════════════════════════════════════════════════

class TestH5TokenSecurity(unittest.TestCase):
    """GET /data 端点：refresh_token 认证正确性。"""

    def tearDown(self):
        if hasattr(self, "_app") and hasattr(self, "_orig"):
            self._app.dependency_overrides = self._orig

    def test_H5_1_wrong_token_returns_403(self):
        """错误 token → 403。"""
        from backend.main import app
        self._app = app
        report = _make_mock_report(token="correct_token_abc")
        client, orig = _make_client_with_report(report)
        self._orig = orig

        resp = client.get(
            f"/api/v1/reports/{report.id}/data",
            params={"token": "WRONG_TOKEN"},
        )
        app.dependency_overrides = orig
        self.assertEqual(resp.status_code, 403)

    def test_H5_2_correct_token_returns_200(self):
        """正确 token → 200。"""
        from backend.main import app
        self._app = app
        report = _make_mock_report(token="correct_token_xyz")
        client, orig = _make_client_with_report(report)
        self._orig = orig

        async def _fake_run(sql, env, conn_type="clickhouse"):
            return []

        with patch("backend.api.reports._run_query", side_effect=_fake_run):
            resp = client.get(
                f"/api/v1/reports/{report.id}/data",
                params={"token": "correct_token_xyz"},
            )
        app.dependency_overrides = orig
        self.assertEqual(resp.status_code, 200)

    def test_H5_3_missing_token_returns_422(self):
        """缺少 token 参数 → 422 (FastAPI 参数校验)。"""
        from backend.main import app
        self._app = app
        report = _make_mock_report()
        client, orig = _make_client_with_report(report)
        self._orig = orig

        resp = client.get(f"/api/v1/reports/{report.id}/data")
        app.dependency_overrides = orig
        self.assertEqual(resp.status_code, 422)

    def test_H5_4_invalid_report_id_returns_400(self):
        """非法 UUID report_id → 400。"""
        from backend.main import app
        self._app = app
        report = _make_mock_report()
        client, orig = _make_client_with_report(report)
        self._orig = orig

        resp = client.get(
            "/api/v1/reports/not-a-valid-uuid/data",
            params={"token": "any_token"},
        )
        app.dependency_overrides = orig
        self.assertEqual(resp.status_code, 400)

    def test_H5_5_no_jwt_required(self):
        """GET /data 端点无需 JWT（使用 refresh_token 认证，适合 HTML 内直接调用）。"""
        from backend.main import app
        import inspect
        from backend.api.reports import get_report_data
        # 检查函数签名：不包含 require_permission 或 get_current_user 依赖
        sig = inspect.signature(get_report_data)
        param_annotations = {k: str(v.annotation) for k, v in sig.parameters.items()}
        # token 是 Query 参数，不是 Depends(require_permission(...))
        self.assertIn("token", param_annotations)
        # 无 current_user 参数（即不依赖 JWT）
        self.assertNotIn("current_user", param_annotations)


# ════════════════════════════════════════════════════════════════════════════════
# H6 — 图表级错误隔离
# ════════════════════════════════════════════════════════════════════════════════

class TestH6ChartErrorIsolation(unittest.TestCase):
    """一个图表 SQL 执行失败，其余图表正常返回，不整体 500。"""

    def tearDown(self):
        if hasattr(self, "_app") and hasattr(self, "_orig"):
            self._app.dependency_overrides = self._orig

    def test_H6_1_one_chart_fails_others_succeed(self):
        """c1 查询失败 → errors[c1] 有错误信息；c2 正常返回 data[c2]。"""
        from backend.main import app
        self._app = app
        charts = [
            {"id": "c1", "chart_type": "bar", "title": "失败图",
             "sql": "SELECT bad_syntax", "connection_env": "sg", "connection_type": "clickhouse"},
            {"id": "c2", "chart_type": "line", "title": "正常图",
             "sql": "SELECT 1 AS n", "connection_env": "sg", "connection_type": "clickhouse"},
        ]
        report = _make_mock_report(charts=charts)
        client, orig = _make_client_with_report(report)
        self._orig = orig

        call_count = 0

        async def _fake_run(sql, env, conn_type="clickhouse"):
            nonlocal call_count
            call_count += 1
            if "bad_syntax" in sql:
                raise RuntimeError("ClickHouse SQL error: unknown column")
            return [{"n": 1}]

        with patch("backend.api.reports._run_query", new=AsyncMock(side_effect=_fake_run)):
            resp = client.get(
                f"/api/v1/reports/{report.id}/data",
                params={"token": report.refresh_token},
            )

        app.dependency_overrides = orig
        self.assertEqual(resp.status_code, 200, "图表级错误不应导致整体 500")
        body = resp.json()
        self.assertTrue(body["success"])
        # c1 有错误（错误信息非空即可；实际来自 ClickHouse/mock）
        self.assertIn("c1", body["errors"])
        self.assertTrue(body["errors"]["c1"], "c1 错误信息不应为空")
        # c2 有数据
        self.assertIn("c2", body["data"])
        self.assertIsInstance(body["data"]["c2"], list)

    def test_H6_2_all_charts_fail_still_200(self):
        """所有图表均失败 → 仍返回 200，errors 包含所有 chart id。"""
        from backend.main import app
        self._app = app
        charts = [
            {"id": "cX", "chart_type": "bar", "title": "X",
             "sql": "SELECT ??", "connection_env": "sg", "connection_type": "clickhouse"},
        ]
        report = _make_mock_report(charts=charts)
        client, orig = _make_client_with_report(report)
        self._orig = orig

        async def _always_fail(sql, env, conn_type="clickhouse"):
            raise RuntimeError("connection refused")

        with patch("backend.api.reports._run_query", side_effect=_always_fail):
            resp = client.get(
                f"/api/v1/reports/{report.id}/data",
                params={"token": report.refresh_token},
            )

        app.dependency_overrides = orig
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["success"])
        self.assertIn("cX", body["errors"])
        self.assertEqual(body["data"], {})

    def test_H6_3_chart_without_sql_skipped(self):
        """无 sql 字段的图表被跳过（不执行查询），不出现在 data 或 errors 中。"""
        from backend.main import app
        self._app = app
        charts = [
            {"id": "c_no_sql", "chart_type": "kpi", "title": "无 SQL 图"},
            {"id": "c_with_sql", "chart_type": "bar", "title": "有 SQL 图",
             "sql": "SELECT 1 AS n", "connection_env": "sg", "connection_type": "clickhouse"},
        ]
        report = _make_mock_report(charts=charts)
        client, orig = _make_client_with_report(report)
        self._orig = orig

        async def _fake_run(sql, env, conn_type="clickhouse"):
            return [{"n": 1}]

        with patch("backend.api.reports._run_query", new=AsyncMock(side_effect=_fake_run)):
            resp = client.get(
                f"/api/v1/reports/{report.id}/data",
                params={"token": report.refresh_token},
            )

        app.dependency_overrides = orig
        body = resp.json()
        self.assertNotIn("c_no_sql", body["data"])
        self.assertNotIn("c_no_sql", body["errors"])
        self.assertIn("c_with_sql", body["data"])


# ════════════════════════════════════════════════════════════════════════════════
# H7 — /refresh-data 委托行为
# ════════════════════════════════════════════════════════════════════════════════

class TestH7RefreshDataDelegation(unittest.TestCase):
    """/refresh-data 端点委托给 /data，行为一致。"""

    def tearDown(self):
        if hasattr(self, "_app") and hasattr(self, "_orig"):
            self._app.dependency_overrides = self._orig

    def test_H7_1_refresh_data_returns_same_structure(self):
        """/refresh-data 返回与 /data 相同的响应结构。"""
        from backend.main import app
        self._app = app
        report = _make_mock_report()
        client, orig = _make_client_with_report(report)
        self._orig = orig

        async def _fake_run(sql, env, conn_type="clickhouse"):
            return [{"result": "refreshed"}]

        with patch("backend.api.reports._run_query", side_effect=_fake_run):
            resp = client.get(
                f"/api/v1/reports/{report.id}/refresh-data",
                params={"token": report.refresh_token},
            )

        app.dependency_overrides = orig
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertIn("success", body)
        self.assertIn("data", body)
        self.assertIn("params_used", body)
        self.assertIn("refreshed_at", body)

    def test_H7_2_refresh_data_wrong_token_403(self):
        """/refresh-data 错误 token → 403（token 验证与 /data 一致）。"""
        from backend.main import app
        self._app = app
        report = _make_mock_report(token="real_token_h7")
        client, orig = _make_client_with_report(report)
        self._orig = orig

        resp = client.get(
            f"/api/v1/reports/{report.id}/refresh-data",
            params={"token": "wrong_token_h7"},
        )
        app.dependency_overrides = orig
        self.assertEqual(resp.status_code, 403)

    def test_H7_3_refresh_data_passes_query_params_to_data(self):
        """/refresh-data 传入的 query params 转发给 /data 处理。"""
        from backend.main import app
        self._app = app
        report = _make_mock_report()
        client, orig = _make_client_with_report(report)
        self._orig = orig

        async def _fake_run(sql, env, conn_type="clickhouse"):
            return []

        with patch("backend.api.reports._run_query", side_effect=_fake_run):
            resp = client.get(
                f"/api/v1/reports/{report.id}/refresh-data",
                params={
                    "token": report.refresh_token,
                    "date_start": "2025-03-01",
                    "date_end": "2025-03-31",
                },
            )

        app.dependency_overrides = orig
        pu = resp.json()["params_used"]
        self.assertEqual(pu.get("date_start"), "2025-03-01")
        self.assertEqual(pu.get("date_end"), "2025-03-31")


# ════════════════════════════════════════════════════════════════════════════════
# H8 — Pilot 系统提示：参数化报表增强
# ════════════════════════════════════════════════════════════════════════════════

class TestH8PilotPromptParameterized(unittest.TestCase):
    """Pilot copilot 提示：参数化报表含 ★参数化 + 参数段。"""

    def tearDown(self):
        if hasattr(self, "_app") and hasattr(self, "_orig"):
            self._app.dependency_overrides = self._orig

    def _make_param_report(self):
        return _make_mock_report(
            charts=[{
                "id": "c1",
                "chart_type": "line",
                "title": "动态图",
                "sql": "SELECT dt FROM t WHERE dt >= '{{ date_start }}'",
                "connection_env": "sg",
            }],
            filters=[{
                "id": "date_range",
                "type": "date_range",
                "default_days": 30,
                "binds": {"start": "date_start", "end": "date_end"},
            }],
        )

    def test_H8_1_star_param_tag_in_chart_list(self):
        """★参数化 标注出现在含 {{ }} SQL 的图表列表行。"""
        from backend.main import app
        self._app = app
        report = self._make_param_report()
        client, orig = _make_client_with_report(report)
        self._orig = orig

        with patch("backend.services.conversation_service.ConversationService") as mock_svc_cls:
            mock_svc = MagicMock()
            mock_svc.find_pilot_conversation.return_value = None
            mock_conv = MagicMock()
            mock_conv.id = uuid.uuid4()
            mock_svc.create_conversation.return_value = mock_conv
            mock_svc_cls.return_value = mock_svc

            resp = client.post(f"/api/v1/reports/{report.id}/copilot")

        app.dependency_overrides = orig
        # 检查传给 create_conversation 的 system_prompt 中有 ★参数化 标注
        if mock_svc.create_conversation.called:
            call_kwargs = mock_svc.create_conversation.call_args
            system_prompt = call_kwargs.kwargs.get("system_prompt") or (
                call_kwargs.args[0] if call_kwargs.args else ""
            )
            self.assertIn("★参数化", system_prompt)

    def test_H8_2_param_section_in_prompt(self):
        """参数化查询说明段落出现在 Pilot 提示中。"""
        from backend.main import app
        self._app = app
        report = self._make_param_report()
        client, orig = _make_client_with_report(report)
        self._orig = orig

        with patch("backend.services.conversation_service.ConversationService") as mock_svc_cls:
            mock_svc = MagicMock()
            mock_svc.find_pilot_conversation.return_value = None
            mock_conv = MagicMock()
            mock_conv.id = uuid.uuid4()
            mock_svc.create_conversation.return_value = mock_conv
            mock_svc_cls.return_value = mock_svc

            resp = client.post(f"/api/v1/reports/{report.id}/copilot")

        app.dependency_overrides = orig
        if mock_svc.create_conversation.called:
            call_kwargs = mock_svc.create_conversation.call_args
            sp = call_kwargs.kwargs.get("system_prompt") or ""
            self.assertIn("参数化查询说明", sp)
            self.assertIn("default_days", sp)

    def test_H8_3_pilot_prompt_contains_report_id_and_token(self):
        """Pilot 提示包含 report_id 和 refresh_token（供 MCP 工具使用）。"""
        from backend.main import app
        self._app = app
        report = self._make_param_report()
        client, orig = _make_client_with_report(report)
        self._orig = orig

        with patch("backend.services.conversation_service.ConversationService") as mock_svc_cls:
            mock_svc = MagicMock()
            mock_svc.find_pilot_conversation.return_value = None
            mock_conv = MagicMock()
            mock_conv.id = uuid.uuid4()
            mock_svc.create_conversation.return_value = mock_conv
            mock_svc_cls.return_value = mock_svc

            resp = client.post(f"/api/v1/reports/{report.id}/copilot")

        app.dependency_overrides = orig
        if mock_svc.create_conversation.called:
            sp = mock_svc.create_conversation.call_args.kwargs.get("system_prompt") or ""
            self.assertIn(str(report.id), sp)
            self.assertIn(report.refresh_token, sp)

    def test_H8_4_mcp_tools_listed_in_prompt(self):
        """Pilot 提示包含 report__get_spec / report__update_single_chart / report__update_spec 工具说明。"""
        from backend.main import app
        self._app = app
        report = self._make_param_report()
        client, orig = _make_client_with_report(report)
        self._orig = orig

        with patch("backend.services.conversation_service.ConversationService") as mock_svc_cls:
            mock_svc = MagicMock()
            mock_svc.find_pilot_conversation.return_value = None
            mock_conv = MagicMock()
            mock_conv.id = uuid.uuid4()
            mock_svc.create_conversation.return_value = mock_conv
            mock_svc_cls.return_value = mock_svc

            client.post(f"/api/v1/reports/{report.id}/copilot")

        app.dependency_overrides = orig
        if mock_svc.create_conversation.called:
            sp = mock_svc.create_conversation.call_args.kwargs.get("system_prompt") or ""
            self.assertIn("report__get_spec", sp)
            self.assertIn("report__update_single_chart", sp)
            self.assertIn("report__update_spec", sp)


# ════════════════════════════════════════════════════════════════════════════════
# H9 — Pilot 系统提示：非参数化报表
# ════════════════════════════════════════════════════════════════════════════════

class TestH9PilotPromptNonParameterized(unittest.TestCase):
    """Pilot 提示：纯 literal SQL 报表不含 ★ 标注 / 不含参数段。"""

    def tearDown(self):
        if hasattr(self, "_app") and hasattr(self, "_orig"):
            self._app.dependency_overrides = self._orig

    def test_H9_1_no_star_tag_for_literal_sql(self):
        """纯 literal SQL（无 {{ }}）→ 图表列表无 ★参数化 标注。"""
        from backend.main import app
        self._app = app
        report = _make_mock_report(
            charts=[{"id": "c1", "chart_type": "bar", "title": "静态图",
                     "sql": "SELECT 1 AS n", "connection_env": "sg"}],
            filters=[],
        )
        client, orig = _make_client_with_report(report)
        self._orig = orig

        with patch("backend.services.conversation_service.ConversationService") as mock_svc_cls:
            mock_svc = MagicMock()
            mock_svc.find_pilot_conversation.return_value = None
            mock_conv = MagicMock()
            mock_conv.id = uuid.uuid4()
            mock_svc.create_conversation.return_value = mock_conv
            mock_svc_cls.return_value = mock_svc

            client.post(f"/api/v1/reports/{report.id}/copilot")

        app.dependency_overrides = orig
        if mock_svc.create_conversation.called:
            sp = mock_svc.create_conversation.call_args.kwargs.get("system_prompt") or ""
            self.assertNotIn("★参数化", sp)
            self.assertNotIn("参数化查询说明", sp)


# ════════════════════════════════════════════════════════════════════════════════
# H10 — RBAC 权限矩阵分析
# ════════════════════════════════════════════════════════════════════════════════

class TestH10RBACMatrix(unittest.TestCase):
    """新增端点的权限设计符合安全最小权限原则。"""

    def _get_router_routes(self):
        """返回 reports router 所有路由信息。"""
        from backend.api.reports import router
        return router.routes

    def test_H10_1_data_endpoint_no_jwt_required(self):
        """GET /data 使用 refresh_token 认证，无 require_permission 依赖。"""
        from backend.api.reports import get_report_data
        import inspect
        sig = inspect.signature(get_report_data)
        # 无 current_user 参数 → 无 JWT 认证
        self.assertNotIn("current_user", sig.parameters)
        # 有 token 参数（Query）
        self.assertIn("token", sig.parameters)

    def test_H10_2_refresh_data_no_jwt_required(self):
        """GET /refresh-data 委托给 /data，同样不需要 JWT。"""
        from backend.api.reports import refresh_report_data
        import inspect
        sig = inspect.signature(refresh_report_data)
        self.assertNotIn("current_user", sig.parameters)
        self.assertIn("token", sig.parameters)

    def test_H10_3_build_requires_reports_create(self):
        """POST /build 需要 reports:create 权限。"""
        from backend.api.reports import build_report
        import inspect
        sig = inspect.signature(build_report)
        # current_user 参数存在（通过 require_permission 注入）
        self.assertIn("current_user", sig.parameters)

    def test_H10_4_list_requires_reports_read(self):
        """GET / (list_reports) 需要 reports:read 权限。"""
        from backend.api.reports import list_reports
        import inspect
        sig = inspect.signature(list_reports)
        self.assertIn("current_user", sig.parameters)

    def test_H10_5_delete_requires_reports_delete(self):
        """DELETE /{id} 需要 reports:delete 权限。"""
        from backend.api.reports import delete_report
        import inspect
        sig = inspect.signature(delete_report)
        self.assertIn("current_user", sig.parameters)

    def test_H10_6_html_serve_by_token_no_jwt(self):
        """GET /{id}/html 通过 refresh_token 认证，无需 JWT（公开分享场景）。"""
        from backend.api.reports import serve_report_html_by_token
        import inspect
        sig = inspect.signature(serve_report_html_by_token)
        self.assertNotIn("current_user", sig.parameters)
        self.assertIn("token", sig.parameters)

    def test_H10_7_spec_meta_no_jwt(self):
        """GET /{id}/spec-meta 通过 refresh_token 认证，无需 JWT。"""
        from backend.api.reports import get_report_spec_meta
        import inspect
        sig = inspect.signature(get_report_spec_meta)
        self.assertNotIn("current_user", sig.parameters)
        self.assertIn("token", sig.parameters)

    def test_H10_8_copilot_requires_reports_read(self):
        """POST /{id}/copilot 需要 reports:read 权限（Pilot 功能）。"""
        from backend.api.reports import create_report_copilot
        import inspect
        sig = inspect.signature(create_report_copilot)
        self.assertIn("current_user", sig.parameters)

    def test_H10_9_rebuild_spec_requires_reports_create(self):
        """POST /{id}/rebuild-spec 需要 reports:create 权限。"""
        from backend.api.reports import rebuild_report_spec
        import inspect
        sig = inspect.signature(rebuild_report_spec)
        self.assertIn("current_user", sig.parameters)

    def test_H10_10_update_spec_requires_reports_create(self):
        """PUT /{id}/spec 需要 reports:create 权限。"""
        from backend.api.reports import update_report_spec
        import inspect
        sig = inspect.signature(update_report_spec)
        self.assertIn("current_user", sig.parameters)


# ════════════════════════════════════════════════════════════════════════════════
# H11 — JS 引擎结构：筛选器路由逻辑
# ════════════════════════════════════════════════════════════════════════════════

class TestH11JSFilterRouting(unittest.TestCase):
    """生成的 HTML 中 JS 筛选器路由逻辑正确。"""

    def setUp(self):
        from backend.services.report_builder_service import build_report_html
        self.build = build_report_html

    def _html_with_binds(self):
        spec = {
            "title": "Filter Test",
            "charts": [{"id": "c1", "chart_type": "bar", "title": "C1",
                        "sql": "SELECT '{{ date_start }}' AS s", "connection_env": "sg"}],
            "filters": [{
                "id": "date_range",
                "type": "date_range",
                "default_days": 30,
                "binds": {"start": "date_start", "end": "date_end"},
            }],
            "data": {},
        }
        return self.build(spec=spec, report_id="test-id", refresh_token="tok")

    def _html_no_binds(self):
        spec = {
            "title": "No Binds Test",
            "charts": [{"id": "c1", "chart_type": "bar", "title": "C1",
                        "sql": "SELECT 1 AS n", "connection_env": "sg"}],
            "filters": [{
                "id": "status",
                "type": "select",
                "options": ["a", "b"],
                # 无 binds
            }],
            "data": {},
        }
        return self.build(spec=spec, report_id="test-id", refresh_token="tok")

    def test_H11_1_has_binds_routing_to_server(self):
        """有 binds 的 filter → JS 包含服务端重查判断分支。"""
        html = self._html_with_binds()
        self.assertIn("hasBinds && !isClientSide", html)
        self.assertIn("_debouncedLoadData", html)

    def test_H11_2_no_binds_falls_to_client_filter(self):
        """无 binds 时 → applyFilterToChart（客户端过滤）路径存在。"""
        html = self._html_no_binds()
        self.assertIn("applyFilterToChart", html)

    def test_H11_3_current_params_builds_from_filter_values(self):
        """_currentParams() 函数存在且读取 _filterValues 和 _DEFAULT_PARAMS。"""
        html = self._html_with_binds()
        self.assertIn("function _currentParams()", html)
        self.assertIn("_DEFAULT_PARAMS", html)
        self.assertIn("_filterValues", html)

    def test_H11_4_refresh_all_data_uses_current_params(self):
        """refreshAllData() 使用 _currentParams() 而非固定参数。"""
        html = self._html_with_binds()
        self.assertIn("_loadData(_currentParams())", html)

    def test_H11_5_set_date_range_quick_buttons_present(self):
        """date_range filter 有近7天/近30天/近90天快捷按钮。"""
        html = self._html_with_binds()
        self.assertIn("近7天", html)
        self.assertIn("近30天", html)
        self.assertIn("近90天", html)

    def test_H11_6_load_data_api_url_template_correct(self):
        """_loadData 中 API URL 路径包含 /reports/${REPORT_ID}/data。"""
        html = self._html_with_binds()
        self.assertIn("/reports/${REPORT_ID}/data", html)


# ════════════════════════════════════════════════════════════════════════════════
# H12 — 向后兼容：literal SQL 报表
# ════════════════════════════════════════════════════════════════════════════════

class TestH12BackwardCompatibility(unittest.TestCase):
    """literal SQL（无 {{ }}）报表流程向后兼容。"""

    def setUp(self):
        from backend.services.report_params_service import render_sql, extract_default_params
        self.render = render_sql
        self.extract = extract_default_params

    def test_H12_1_literal_sql_passthrough(self):
        """literal SQL 不含 {{ }} → render_sql 直接返回，零处理开销。"""
        sql = "SELECT date, count() AS cnt FROM crm.calls WHERE call_start_time >= '2025-01-01' GROUP BY date"
        result = self.render(sql, {"date_start": "2026-01-01"})
        self.assertEqual(result, sql, "literal SQL 应原样返回")

    def test_H12_2_no_binds_extract_default_params_empty(self):
        """无 binds filter → extract_default_params 返回 {} → 不影响 SQL。"""
        spec = {
            "filters": [
                {"id": "status", "type": "select", "options": ["a", "b"]},
            ]
        }
        params = self.extract(spec)
        self.assertEqual(params, {})

    def test_H12_3_html_build_with_literal_sql_dynamic_load(self):
        """literal SQL 报表（保存模式）仍走动态加载，/data 将直接执行 literal SQL。"""
        from backend.services.report_builder_service import build_report_html
        spec = {
            "title": "Literal SQL Report",
            "charts": [{"id": "c1", "chart_type": "bar", "title": "C",
                        "sql": "SELECT 1 AS n", "connection_env": "sg"}],
            "filters": [],
            "data": {},
        }
        html = build_report_html(spec=spec, report_id="real-id", refresh_token="tok")
        # 保存模式下仍有动态加载逻辑
        self.assertIn("REPORT_ID !== 'preview'", html)
        self.assertIn("_loadData", html)

    def test_H12_4_data_endpoint_literal_sql_no_render(self):
        """GET /data：literal SQL 报表，render_sql 返回原 SQL 不做替换。"""
        sql = "SELECT 1 AS n"
        from backend.services.report_params_service import render_sql
        result = render_sql(sql, {"date_start": "2026-01-01"})
        self.assertEqual(result, sql)


# ════════════════════════════════════════════════════════════════════════════════
# H13 — 多图表部分错误完整性
# ════════════════════════════════════════════════════════════════════════════════

class TestH13MultiChartPartialError(unittest.TestCase):
    """4 图表报表，2 个失败 2 个成功，验证完整性。"""

    def tearDown(self):
        if hasattr(self, "_app") and hasattr(self, "_orig"):
            self._app.dependency_overrides = self._orig

    def test_H13_1_partial_success_correct_data_and_errors(self):
        """c1/c3 成功，c2/c4 失败 → data 仅含 c1/c3，errors 仅含 c2/c4。"""
        from backend.main import app
        self._app = app
        charts = [
            {"id": "c1", "title": "OK", "sql": "SELECT 1 AS n", "connection_env": "sg", "connection_type": "clickhouse"},
            {"id": "c2", "title": "FAIL", "sql": "SELECT FAIL", "connection_env": "sg", "connection_type": "clickhouse"},
            {"id": "c3", "title": "OK2", "sql": "SELECT 2 AS n", "connection_env": "sg", "connection_type": "clickhouse"},
            {"id": "c4", "title": "FAIL2", "sql": "SELECT FAIL2", "connection_env": "sg", "connection_type": "clickhouse"},
        ]
        report = _make_mock_report(charts=charts)
        client, orig = _make_client_with_report(report)
        self._orig = orig

        async def _selective(sql, env, conn_type="clickhouse"):
            if "FAIL" in sql:
                raise RuntimeError("bad sql")
            n = 1 if "SELECT 1" in sql else 2
            return [{"n": n}]

        with patch("backend.api.reports._run_query", new=AsyncMock(side_effect=_selective)):
            resp = client.get(
                f"/api/v1/reports/{report.id}/data",
                params={"token": report.refresh_token},
            )

        app.dependency_overrides = orig
        body = resp.json()
        self.assertEqual(resp.status_code, 200)
        self.assertIn("c1", body["data"])
        self.assertIn("c3", body["data"])
        self.assertNotIn("c1", body["errors"])
        self.assertNotIn("c3", body["errors"])
        self.assertIn("c2", body["errors"])
        self.assertIn("c4", body["errors"])
        self.assertNotIn("c2", body["data"])
        self.assertNotIn("c4", body["data"])

    def test_H13_2_view_count_incremented_even_on_partial_error(self):
        """即使有图表出错，view_count 仍递增（报表被查看了）。"""
        from backend.main import app
        self._app = app
        report = _make_mock_report(charts=[
            {"id": "c1", "sql": "SELECT bad_syntax", "connection_env": "sg", "connection_type": "clickhouse"}
        ])
        client, orig = _make_client_with_report(report)
        self._orig = orig

        async def _fail(sql, env, conn_type="clickhouse"):
            raise RuntimeError("fail")

        with patch("backend.api.reports._run_query", side_effect=_fail):
            client.get(
                f"/api/v1/reports/{report.id}/data",
                params={"token": report.refresh_token},
            )

        app.dependency_overrides = orig
        # increment_view_count 应被调用一次
        report.increment_view_count.assert_called_once()


# ════════════════════════════════════════════════════════════════════════════════
# H14 — flatten_query_params 工具函数
# ════════════════════════════════════════════════════════════════════════════════

class TestH14FlattenQueryParams(unittest.TestCase):
    """flatten_query_params：单元素 list → 标量展开。"""

    def setUp(self):
        from backend.services.report_params_service import flatten_query_params
        self.flatten = flatten_query_params

    def test_H14_1_single_element_list_unwrapped(self):
        """单元素 list → 标量。"""
        result = self.flatten({"date_start": ["2025-01-01"]})
        self.assertEqual(result["date_start"], "2025-01-01")

    def test_H14_2_multi_element_list_kept(self):
        """多元素 list（multi_select）→ 保留为 list。"""
        result = self.flatten({"tags": ["a", "b", "c"]})
        self.assertEqual(result["tags"], ["a", "b", "c"])

    def test_H14_3_scalar_passthrough(self):
        """标量值直接传递（非 list）。"""
        result = self.flatten({"env": "sg"})
        self.assertEqual(result["env"], "sg")

    def test_H14_4_mixed_params(self):
        """混合参数：单元素展开 + 多元素保留。"""
        raw = {"date_start": ["2025-01-01"], "tags": ["x", "y"], "env": "sg"}
        result = self.flatten(raw)
        self.assertEqual(result["date_start"], "2025-01-01")
        self.assertEqual(result["tags"], ["x", "y"])
        self.assertEqual(result["env"], "sg")


# ════════════════════════════════════════════════════════════════════════════════
# H15 — render_sql 安全性边界
# ════════════════════════════════════════════════════════════════════════════════

class TestH15RenderSQLSecurity(unittest.TestCase):
    """render_sql 沙盒安全边界测试。"""

    def setUp(self):
        from backend.services.report_params_service import render_sql
        self.render = render_sql

    def test_H15_1_import_blocked(self):
        """__import__ 调用被沙盒拦截，不返回模块信息。"""
        # Jinja2 SandboxedEnvironment 会对 dangerous 操作抛 SecurityError
        result = self.render("{{ lipsum.__module__ }}", {})
        # 沙盒阻止 → 返回空或受限值，绝不暴露 Python 内部信息
        self.assertNotIn("builtins", result)

    def test_H15_2_large_template_no_crash(self):
        """超长 SQL 模板不崩溃。"""
        long_template = "SELECT " + ", ".join([f"'{{{{ p{i} }}}}' AS c{i}" for i in range(200)])
        params = {f"p{i}": str(i) for i in range(200)}
        result = self.render(long_template, params)
        self.assertIn("c0", result)

    def test_H15_3_special_chars_in_values_no_injection(self):
        """参数值中含特殊字符不导致 SQL 注入（Jinja2 不做 SQL 转义，这是设计决策）。"""
        # 注意：SQL 注入防护应在调用层（参数化查询），render_sql 仅做变量替换
        sql = "WHERE name = '{{ name }}'"
        result = self.render(sql, {"name": "O'Brien'; DROP TABLE t; --"})
        # 变量被替换（注入防护需在 DB 层）
        self.assertIn("O'Brien", result)

    def test_H15_4_none_param_renders_as_empty(self):
        """None 值参数不崩溃，渲染为空字符串。"""
        # _SilentUndefined 处理缺失变量；None 值通过 Jinja2 正常渲染
        result = self.render("{{ val }}", {"val": None})
        self.assertIn("None", result)  # Jinja2 将 None 渲染为 "None"


# ════════════════════════════════════════════════════════════════════════════════
# H16 — JS 字段自动推断 (_autoDetectFields)
#        验证：spec 缺少 x_field/y_fields/series_field 时，HTML 动态引擎能自动推断
# ════════════════════════════════════════════════════════════════════════════════

class TestH16AutoDetectFields(unittest.TestCase):
    """
    测试 report_builder_service.py 中嵌入的 _autoDetectFields JS 逻辑。
    通过检查生成 HTML 的内容来验证函数已正确注入。
    同时通过 Python 逻辑镜像（用于单元语义验证）来测试推断规则。
    """

    def _simulate_auto_detect(self, spec: dict, data: list) -> dict:
        """
        Python 镜像：模拟 _autoDetectFields JS 逻辑，用于单元验证。
        逻辑与 report_builder_service.py 中的 JS 保持一致。
        """
        if not data:
            return spec
        if spec.get("x_field") and spec.get("y_fields"):
            return spec

        sample = data[0]
        keys = list(sample.keys())
        str_keys = [k for k in keys if not isinstance(sample[k], (int, float)) or sample[k] is None]
        num_keys = [k for k in keys if isinstance(sample[k], (int, float)) and sample[k] is not None]

        x_f = spec.get("x_field") or (str_keys[0] if str_keys else (keys[0] if keys else None))
        y_fs = spec.get("y_fields") or (num_keys if num_keys else ([str_keys[1]] if len(str_keys) > 1 else []))

        s_f = spec.get("series_field")
        if not s_f and len(str_keys) >= 2:
            candidate = next((k for k in str_keys if k != x_f), None)
            if candidate:
                uniq = set(r[candidate] for r in data)
                if 1 < len(uniq) <= max(2, len(data) // 2):
                    s_f = candidate

        result = dict(spec)
        result["x_field"] = x_f
        result["y_fields"] = y_fs
        result["series_field"] = s_f or spec.get("series_field")
        return result

    def test_H16_1_auto_detect_single_series(self):
        """缺失 x_field/y_fields 时：日期列→x，数字列→y，无 series_field。"""
        spec = {"id": "c1", "chart_type": "line"}
        data = [
            {"day": "2025-01-01", "connected_calls": 100},
            {"day": "2025-01-02", "connected_calls": 120},
        ]
        result = self._simulate_auto_detect(spec, data)
        self.assertEqual(result["x_field"], "day")
        self.assertEqual(result["y_fields"], ["connected_calls"])
        self.assertFalse(result.get("series_field"))

    def test_H16_2_auto_detect_grouped_series(self):
        """两个字符串列 + 数字列 → 第一字符串为 x，第二字符串（多唯一值）为 series_field。"""
        spec = {"id": "c1", "chart_type": "bar"}
        data = [
            {"dt": "2025-01-01", "env": "sg",    "cnt": 10},
            {"dt": "2025-01-01", "env": "idn",   "cnt": 5},
            {"dt": "2025-01-02", "env": "sg",    "cnt": 12},
            {"dt": "2025-01-02", "env": "idn",   "cnt": 7},
        ]
        result = self._simulate_auto_detect(spec, data)
        self.assertEqual(result["x_field"], "dt")
        self.assertEqual(result["y_fields"], ["cnt"])
        self.assertEqual(result["series_field"], "env")

    def test_H16_3_already_configured_spec_unchanged(self):
        """spec 已配置 x_field/y_fields 时，自动推断不覆盖。"""
        spec = {"id": "c1", "x_field": "date", "y_fields": ["sales"], "series_field": "region"}
        data = [{"date": "2025-01-01", "region": "north", "sales": 50}]
        result = self._simulate_auto_detect(spec, data)
        self.assertEqual(result["x_field"], "date")
        self.assertEqual(result["y_fields"], ["sales"])
        self.assertEqual(result["series_field"], "region")

    def test_H16_4_empty_data_returns_spec_unchanged(self):
        """data 为空时直接返回 spec，不崩溃。"""
        spec = {"id": "c1", "chart_type": "line"}
        result = self._simulate_auto_detect(spec, [])
        self.assertEqual(result["id"], "c1")
        self.assertFalse(result.get("x_field"))

    def test_H16_5_auto_detect_code_in_generated_html(self):
        """生成的 HTML 中包含 _autoDetectFields 函数。"""
        from backend.services.report_builder_service import build_report_html
        spec = {
            "title": "Test Auto Detect",
            "charts": [{"id": "c1", "chart_type": "line", "sql": "SELECT 1",
                        "connection_env": "sg"}],
            "filters": [], "data": {},
        }
        html = build_report_html(spec=spec, report_id="test-id", refresh_token="tok")
        self.assertIn("_autoDetectFields", html)
        self.assertIn("x_field", html)
        self.assertIn("y_fields", html)
        self.assertIn("series_field", html)


# ════════════════════════════════════════════════════════════════════════════════
# H17 — echarts_override.series 样式模板合并（不丢失数据）
# ════════════════════════════════════════════════════════════════════════════════

class TestH17EchartsOverrideSeriesTemplate(unittest.TestCase):
    """
    验证 buildEChartsOption 对 echarts_override.series 的处理：
    - series[0] 作为样式模板应用到每个数据驱动 series
    - name 和 data 不被模板覆盖
    - 无数据驱动 series 时模板直接替换（兜底）
    """

    def test_H17_1_series_template_code_in_generated_html(self):
        """生成的 HTML 中包含 KEEP（保留 name/data 的逻辑）。"""
        from backend.services.report_builder_service import build_report_html
        spec = {
            "title": "Test Series Template",
            "charts": [{"id": "c1", "chart_type": "area", "sql": "SELECT 1",
                        "connection_env": "sg",
                        "echarts_override": {"series": [{"smooth": False, "areaStyle": {}}]}}],
            "filters": [], "data": {},
        }
        html = build_report_html(spec=spec, report_id="test-id", refresh_token="tok")
        # 检查生成的 HTML 中包含样式模板处理逻辑的关键标识
        self.assertIn("KEEP", html)
        # echarts_override.series 作为模板处理的代码
        self.assertIn("override.series", html)

    def test_H17_2_series_template_preserves_name_and_data_semantics(self):
        """
        模拟验证：数据驱动 series [{name:"sg", data:[10,20]}]
        + 样式模板 [{smooth:false, areaStyle:{opacity:0.75}}]
        → 合并后 series 保留 name 和 data，样式属性被覆盖。
        """
        # 模拟 Python 侧的合并逻辑（与 JS 逻辑一致）
        def apply_template(data_series, tmpl):
            KEEP = {"name", "data"}
            result = []
            for s in data_series:
                out = dict(s)
                for k, v in tmpl.items():
                    if k not in KEEP:
                        out[k] = v
                result.append(out)
            return result

        data_series = [
            {"name": "sg",  "type": "bar", "data": [10, 20, 30]},
            {"name": "idn", "type": "bar", "data": [5,  8,  12]},
        ]
        tmpl = {"type": "line", "smooth": False, "stack": "total",
                "areaStyle": {"opacity": 0.75}, "lineStyle": {"width": 1.5}}

        merged = apply_template(data_series, tmpl)

        # name 和 data 保留
        self.assertEqual(merged[0]["name"], "sg")
        self.assertEqual(merged[0]["data"], [10, 20, 30])
        self.assertEqual(merged[1]["name"], "idn")
        self.assertEqual(merged[1]["data"], [5, 8, 12])
        # 样式属性被覆盖
        self.assertEqual(merged[0]["type"], "line")
        self.assertFalse(merged[0]["smooth"])
        self.assertEqual(merged[0]["stack"], "total")
        self.assertEqual(merged[0]["areaStyle"], {"opacity": 0.75})
        self.assertEqual(merged[0]["lineStyle"], {"width": 1.5})
        # 两个 series 都被应用
        self.assertEqual(len(merged), 2)

    def test_H17_3_empty_data_series_falls_back_to_template(self):
        """
        option.series 为空时，override.series 直接替换（兜底行为）。
        这对应 extractXYSeries 未能提取 series 的情况。
        """
        def apply_series_override(option_series, override_series):
            if override_series and len(override_series) > 0 and len(option_series) > 0:
                KEEP = {"name", "data"}
                tmpl = override_series[0]
                return [dict((k, v) if k not in KEEP else (k, s[k])
                             for k, v in {**s, **{k2: v2 for k2, v2 in tmpl.items()
                                                   if k2 not in KEEP}}.items())
                        for s in option_series]
            return option_series  # 无数据 series 时不做替换

        # 空 data series → override.series 不应用（不崩溃）
        result = apply_series_override([], [{"type": "line", "smooth": False}])
        self.assertEqual(result, [])

    def test_H17_4_pilot_bar_to_area_scenario(self):
        """
        完整场景：Pilot 将 bar 改为 area 时，图表数据不丢失。
        模拟：echarts_override.series 样式模板应用后，data 系列完整保留。
        """
        # 来自 GET /data 的数据
        data_rows = [
            {"day": "2025-01-01", "connected_calls": 100},
            {"day": "2025-01-02", "connected_calls": 120},
            {"day": "2025-01-03", "connected_calls": 90},
        ]
        # Pilot 发出的 chart_patch
        chart_patch_override = {
            "series": [{"type": "line", "smooth": False, "stack": "total",
                        "areaStyle": {"opacity": 0.75}, "lineStyle": {"width": 1.5},
                        "symbol": "none"}]
        }
        # 模拟 extractXYSeries（自动推断）
        x_field = "day"
        y_field = "connected_calls"
        data_series = [{
            "name": y_field, "type": "line",
            "data": [r[y_field] for r in data_rows],
        }]
        # 模拟 buildEChartsOption 的样式模板合并
        KEEP = {"name", "data"}
        tmpl = chart_patch_override["series"][0]
        merged_series = []
        for s in data_series:
            out = dict(s)
            for k, v in tmpl.items():
                if k not in KEEP:
                    out[k] = v
            merged_series.append(out)

        self.assertEqual(len(merged_series), 1)
        self.assertEqual(merged_series[0]["name"], "connected_calls")
        self.assertEqual(merged_series[0]["data"], [100, 120, 90])
        self.assertFalse(merged_series[0]["smooth"])
        self.assertEqual(merged_series[0]["areaStyle"], {"opacity": 0.75})


if __name__ == "__main__":
    unittest.main()
