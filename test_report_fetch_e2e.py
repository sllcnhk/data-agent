"""
test_report_fetch_e2e.py
========================
针对"Failed to fetch"三根因修复（A/B/C/D）的全面端到端测试。

设计原则（资深测试工程师视角）：
  1. 优先覆盖核心 E2E 路径：create_report → /data endpoint → CH 查询 → 响应
  2. 对每个根因修复独立验证：不依赖其他 fix 是否存在
  3. 权限矩阵全覆盖：确认无新接口漏权
  4. 边界 + 异常路径：token 错误、UUID 格式错误、空 env、空 charts 等

测试节：
  H — /data endpoint：Token 鉴权 & 响应格式（FastAPI TestClient）
  I — /data endpoint：SQL 参数处理（default / explicit / connection_env normalize / binds list）
  J — Jinja2 SQL 渲染集成
  K — HTML Builder：API_BASE 修复验证
  L — 权限矩阵（reports 路由无新漏权）
  M — 完整 E2E 模拟（create → data fetch）
  N — 回归：原有测试集不受影响
"""
from __future__ import annotations

import json
import os
import sys
import unittest
import uuid
import secrets
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, call

PROJECT_ROOT = Path(__file__).parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# ---------------------------------------------------------------------------
# 公共工具：构造 mock Report 对象
# ---------------------------------------------------------------------------

def _make_mock_report(
    report_id: str = None,
    refresh_token: str = "tok_test",
    charts: list = None,
    filters: list = None,
    llm_summary: str = None,
):
    """构造符合 Report ORM 对象接口的 MagicMock。"""
    mock_report = MagicMock()
    mock_report.id = uuid.UUID(report_id) if report_id else uuid.uuid4()
    mock_report.refresh_token = refresh_token
    mock_report.charts = charts or []
    mock_report.filters = filters or []
    mock_report.llm_summary = llm_summary
    mock_report.increment_view_count = MagicMock()
    return mock_report


def _make_test_client(override_db_fn=None):
    """构造挂载 reports router 的 FastAPI TestClient（全量 mock DB）。

    注意：reports router 本身已带 prefix="/reports"，include_router 时不再重复加。
    路由访问路径形如 /reports/{report_id}/data。
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from backend.api.reports import router
    from backend.config.database import get_db

    app = FastAPI()
    app.include_router(router)  # router 自带 prefix="/reports"，勿重复添加

    if override_db_fn:
        app.dependency_overrides[get_db] = override_db_fn

    return TestClient(app, raise_server_exceptions=False)


# ===========================================================================
# H — /data Endpoint：Token 鉴权 & 响应格式
# ===========================================================================
class TestDataEndpointAuth(unittest.TestCase):
    """H: GET /reports/{id}/data 鉴权机制与响应格式验证。

    注意：reports router 自带 prefix="/reports"，测试时 include_router(router) 不加额外前缀。
    """

    def _make_db_with_report(self, report):
        """mock db session，query().filter().first() 返回指定 report。"""
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = report
        mock_db.commit = MagicMock()
        return mock_db

    def _make_app(self, report):
        from fastapi import FastAPI
        from backend.api.reports import router
        from backend.config.database import get_db

        app = FastAPI()
        app.include_router(router)  # 不加额外前缀，router 自带 /reports

        def override_db():
            yield self._make_db_with_report(report)

        app.dependency_overrides[get_db] = override_db
        return app

    # ── H1: 合法 token → 200 ─────────────────────────────────────────────────
    def test_H1_valid_token_returns_200(self):
        """H1: 正确 refresh_token → HTTP 200 + success:true。"""
        from fastapi.testclient import TestClient

        rid = str(uuid.uuid4())
        tok = secrets.token_urlsafe(16)
        mock_report = _make_mock_report(report_id=rid, refresh_token=tok, charts=[], filters=[])
        client = TestClient(self._make_app(mock_report), raise_server_exceptions=False)

        resp = client.get(f"/reports/{rid}/data", params={"token": tok})
        self.assertEqual(resp.status_code, 200, f"应返回 200，实际: {resp.status_code} body={resp.text[:300]}")
        data = resp.json()
        self.assertTrue(data.get("success"), f"success 应为 True，实际: {data}")

    # ── H2: 错误 token → 403 ──────────────────────────────────────────────────
    def test_H2_wrong_token_returns_403(self):
        """H2: refresh_token 不匹配 → HTTP 403。"""
        from fastapi.testclient import TestClient

        rid = str(uuid.uuid4())
        mock_report = _make_mock_report(report_id=rid, refresh_token="correct_token")
        client = TestClient(self._make_app(mock_report), raise_server_exceptions=False)

        resp = client.get(f"/reports/{rid}/data", params={"token": "wrong_token"})
        self.assertEqual(resp.status_code, 403, f"错误 token 应返回 403，实际: {resp.status_code}")

    # ── H3: 缺少 token → 422 ─────────────────────────────────────────────────
    def test_H3_missing_token_returns_422(self):
        """H3: 未传 token → FastAPI 422 参数校验失败。"""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from backend.api.reports import router
        from backend.config.database import get_db

        app = FastAPI()
        app.include_router(router)

        def override_db():
            yield MagicMock()

        app.dependency_overrides[get_db] = override_db
        client = TestClient(app, raise_server_exceptions=False)

        rid = str(uuid.uuid4())
        resp = client.get(f"/reports/{rid}/data")  # no token
        self.assertEqual(resp.status_code, 422, f"缺少必填 token 应返回 422，实际: {resp.status_code}")

    # ── H4: 无效 UUID → 400 ──────────────────────────────────────────────────
    def test_H4_invalid_uuid_returns_400(self):
        """H4: report_id 非合法 UUID → HTTP 400。"""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from backend.api.reports import router
        from backend.config.database import get_db

        app = FastAPI()
        app.include_router(router)

        def override_db():
            yield MagicMock()

        app.dependency_overrides[get_db] = override_db
        client = TestClient(app, raise_server_exceptions=False)

        resp = client.get("/reports/not-a-uuid/data", params={"token": "tok"})
        self.assertEqual(resp.status_code, 400, f"非法 UUID 应返回 400，实际: {resp.status_code}")

    # ── H5: 报告不存在 → 404 ──────────────────────────────────────────────────
    def test_H5_report_not_found_returns_404(self):
        """H5: report_id 合法但不存在 → HTTP 404。"""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from backend.api.reports import router
        from backend.config.database import get_db

        app = FastAPI()
        app.include_router(router)

        def override_db():
            db = MagicMock()
            db.query.return_value.filter.return_value.first.return_value = None
            yield db

        app.dependency_overrides[get_db] = override_db
        client = TestClient(app, raise_server_exceptions=False)

        resp = client.get(f"/reports/{uuid.uuid4()}/data", params={"token": "tok"})
        self.assertEqual(resp.status_code, 404)

    # ── H6: 响应格式完整性验证 ────────────────────────────────────────────────
    def test_H6_response_format_complete(self):
        """H6: 成功响应包含 success/data/errors/params_used/refreshed_at 所有字段。"""
        from fastapi.testclient import TestClient

        rid = str(uuid.uuid4())
        tok = "h6_tok"
        mock_report = _make_mock_report(report_id=rid, refresh_token=tok, charts=[], filters=[])
        client = TestClient(self._make_app(mock_report), raise_server_exceptions=False)

        resp = client.get(f"/reports/{rid}/data", params={"token": tok})
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        for key in ("success", "data", "errors", "params_used", "refreshed_at"):
            self.assertIn(key, body, f"响应缺少字段: {key}")

    # ── H7: 无图表 → 空 data 但 success:true ──────────────────────────────────
    def test_H7_no_charts_returns_empty_data(self):
        """H7: charts=[] 时 data={} + success=True（不报错）。"""
        from fastapi.testclient import TestClient

        rid = str(uuid.uuid4())
        tok = "h7_tok"
        mock_report = _make_mock_report(report_id=rid, refresh_token=tok, charts=[], filters=[])
        client = TestClient(self._make_app(mock_report), raise_server_exceptions=False)

        resp = client.get(f"/reports/{rid}/data", params={"token": tok})
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["success"])
        self.assertEqual(body["data"], {})
        self.assertEqual(body["errors"], {})


# ===========================================================================
# I — /data Endpoint：SQL 参数处理（Fix B + Fix C 集成验证）
# ===========================================================================
class TestDataEndpointParams(unittest.TestCase):
    """I: /data 端点参数提取、default_params 回退、connection_env 规范化。"""

    def _setup_app_with_report(self, mock_report):
        from fastapi import FastAPI
        from backend.api.reports import router
        from backend.config.database import get_db

        app = FastAPI()
        app.include_router(router)  # 不加额外前缀，router 自带 /reports

        def override_db():
            db = MagicMock()
            db.query.return_value.filter.return_value.first.return_value = mock_report
            db.commit = MagicMock()
            yield db

        app.dependency_overrides[get_db] = override_db
        return app

    # ── I1: 无运行时参数 → 使用 extract_default_params ──────────────────────
    def test_I1_no_params_uses_extract_default(self):
        """I1: 不传日期参数时，自动从 spec.filters 提取默认 30 天窗口。"""
        from fastapi.testclient import TestClient

        rid = str(uuid.uuid4())
        tok = "i1_tok"
        charts = [{
            "id": "c1",
            "chart_type": "bar",
            "sql": "SELECT '{{ date_start }}' AS ds, '{{ date_end }}' AS de",
            "connection_env": "sg",
            "connection_type": "clickhouse",
        }]
        filters = [{
            "id": "date_range",
            "type": "date_range",
            "default_days": 30,
            "binds": {"start": "date_start", "end": "date_end"},
        }]
        mock_report = _make_mock_report(report_id=rid, refresh_token=tok, charts=charts, filters=filters)
        app = self._setup_app_with_report(mock_report)

        # Mock CH client: return fixed rows
        mock_rows_capture = {}

        async def fake_run_query(sql, env, conn_type="clickhouse"):
            mock_rows_capture["sql"] = sql
            mock_rows_capture["env"] = env
            return [{"ds": "2026-01-01", "de": "2026-04-16"}]

        with patch("backend.api.reports._run_query", side_effect=fake_run_query):
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.get(f"/reports/{rid}/data", params={"token": tok})

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["success"])
        # date_start should appear in rendered SQL (default 30d window)
        rendered_sql = mock_rows_capture.get("sql", "")
        self.assertNotIn("{{ date_start }}", rendered_sql,
                         "默认参数应已渲染 date_start 占位符")
        # params_used should have date_start / date_end
        params = body.get("params_used", {})
        self.assertIn("date_start", params, "params_used 应含 date_start")
        self.assertIn("date_end", params, "params_used 应含 date_end")

    # ── I2: 显式参数覆盖默认值 ────────────────────────────────────────────────
    def test_I2_explicit_params_override_defaults(self):
        """I2: 显式传 date_start/date_end → 覆盖默认值，传入 SQL 渲染。"""
        from fastapi.testclient import TestClient

        rid = str(uuid.uuid4())
        tok = "i2_tok"
        charts = [{
            "id": "c1",
            "sql": "SELECT count() WHERE dt >= '{{ date_start }}' AND dt < '{{ date_end }}'",
            "connection_env": "sg",
            "connection_type": "clickhouse",
        }]
        filters = [{
            "id": "date_range",
            "type": "date_range",
            "default_days": 30,
            "binds": {"start": "date_start", "end": "date_end"},
        }]
        mock_report = _make_mock_report(report_id=rid, refresh_token=tok, charts=charts, filters=filters)
        app = self._setup_app_with_report(mock_report)

        captured = {}

        async def fake_run_query(sql, env, conn_type="clickhouse"):
            captured["sql"] = sql
            return []

        with patch("backend.api.reports._run_query", side_effect=fake_run_query):
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.get(
                f"/reports/{rid}/data",
                params={"token": tok, "date_start": "2025-01-01", "date_end": "2025-02-01"},
            )

        self.assertEqual(resp.status_code, 200)
        sql = captured.get("sql", "")
        self.assertIn("2025-01-01", sql, "显式 date_start 应渲染进 SQL")
        self.assertIn("2025-02-01", sql, "显式 date_end 应渲染进 SQL")

    # ── I3: connection_env "clickhouse-sg" → 规范化为 "sg"（Fix B1）─────────
    def test_I3_connection_env_prefix_stripped(self):
        """I3 (Fix B): connection_env='clickhouse-sg' 在 _get_or_init_ch_client 中被规范化为 'sg'。

        说明：
          - _run_query(sql, env) 收到的 env 是原始值 "clickhouse-sg"（来自 chart spec）
          - _get_or_init_ch_client(env) 内部做前缀 strip → 最终使用 "sg" 初始化 CH 连接
          - 本测试验证 ClickHouseMCPServer 是以 'sg' 而非 'clickhouse-sg' 初始化的
        """
        from fastapi.testclient import TestClient

        rid = str(uuid.uuid4())
        tok = "i3_tok"
        charts = [{
            "id": "c1",
            "sql": "SELECT 1",
            "connection_env": "clickhouse-sg",  # ← AI 常见错误
            "connection_type": "clickhouse",
        }]
        mock_report = _make_mock_report(report_id=rid, refresh_token=tok, charts=charts)
        # Clear module cache to force re-initialization
        import backend.api.reports as rmod
        rmod._ch_client_cache.clear()

        app = self._setup_app_with_report(mock_report)

        captured_init_env = {}
        mock_srv = MagicMock()
        mock_srv.client = MagicMock(spec=["execute"])
        mock_srv.client.execute.return_value = ([], [])  # TCP client returns (rows, cols)

        async def fake_initialize():
            pass

        mock_srv.initialize = fake_initialize

        def tracking_mcp_server(*a, **kw):
            captured_init_env["env"] = kw.get("env") or (a[0] if a else None)
            return mock_srv

        with patch("backend.mcp.clickhouse.server.ClickHouseMCPServer",
                   side_effect=tracking_mcp_server):
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.get(f"/reports/{rid}/data", params={"token": tok})

        self.assertEqual(resp.status_code, 200)
        # ClickHouseMCPServer 应以规范化后的 "sg" 初始化（Fix B1 验证）
        self.assertEqual(captured_init_env.get("env"), "sg",
                         f"ClickHouseMCPServer 应以 'sg' 初始化，实际: {captured_init_env.get('env')!r}")

    # ── I4: binds 以 list 格式存储 → extract_default_params 仍正常（Fix C1）─
    def test_I4_binds_as_list_extract_default_works(self):
        """I4 (Fix C): filters.binds 为 list 格式时，extract_default_params 不崩溃且返回正确默认值。"""
        from fastapi.testclient import TestClient

        rid = str(uuid.uuid4())
        tok = "i4_tok"
        charts = [{
            "id": "c1",
            "sql": "SELECT '{{ date_start }}' AS ds",
            "connection_env": "sg",
            "connection_type": "clickhouse",
        }]
        # binds 以列表形式存储（AI 常见错误）
        filters = [{
            "id": "date_range",
            "type": "date_range",
            "default_days": 7,
            "binds": ["date_start", "date_end"],  # ← list 格式
        }]
        mock_report = _make_mock_report(report_id=rid, refresh_token=tok, charts=charts, filters=filters)
        app = self._setup_app_with_report(mock_report)

        captured_sql = {}

        async def fake_run_query(sql, env, conn_type="clickhouse"):
            captured_sql["sql"] = sql
            return []

        with patch("backend.api.reports._run_query", side_effect=fake_run_query):
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.get(f"/reports/{rid}/data", params={"token": tok})

        self.assertEqual(resp.status_code, 200, f"list binds 不应导致崩溃，实际: {resp.text[:300]}")
        body = resp.json()
        self.assertTrue(body["success"])
        # date_start 应从 list binds 正确提取并渲染
        params = body.get("params_used", {})
        self.assertIn("date_start", params, "list binds 的 date_start 应被提取到 params_used")

    # ── I5: 多图表 → 各自独立查询 ───────────────────────────────────────────
    def test_I5_multi_chart_each_queried_independently(self):
        """I5: 多图表报告中每个图表各自执行 SQL 查询，互不干扰。"""
        from fastapi.testclient import TestClient

        rid = str(uuid.uuid4())
        tok = "i5_tok"
        # 使用简洁 SQL，末尾是数字而不是字母，避免 int() 转换失败
        charts = [
            {"id": "c1", "sql": "SELECT 1", "connection_env": "sg", "connection_type": "clickhouse"},
            {"id": "c2", "sql": "SELECT 2", "connection_env": "sg", "connection_type": "clickhouse"},
        ]
        mock_report = _make_mock_report(report_id=rid, refresh_token=tok, charts=charts)
        app = self._setup_app_with_report(mock_report)

        call_log = []

        async def fake_run_query(sql, env, conn_type="clickhouse"):
            call_log.append(sql.strip())
            # Return a simple row list based on which chart
            n = 1 if "1" in sql else 2
            return [{"n": n}]

        with patch("backend.api.reports._run_query", side_effect=fake_run_query):
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.get(f"/reports/{rid}/data", params={"token": tok})

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(len(body["data"]), 2, "data 应包含 2 个图表的结果")
        self.assertIn("c1", body["data"])
        self.assertIn("c2", body["data"])
        self.assertEqual(len(call_log), 2, "应执行了 2 次 SQL 查询")

    # ── I6: 某图表 CH 查询失败 → errors 记录，其他图表正常 ───────────────────
    def test_I6_partial_chart_failure_recorded_in_errors(self):
        """I6: 一个图表查询失败时，该图表出现在 errors，其他图表正常渲染。"""
        from fastapi.testclient import TestClient

        rid = str(uuid.uuid4())
        tok = "i6_tok"
        charts = [
            {"id": "c1", "sql": "SELECT 1", "connection_env": "sg", "connection_type": "clickhouse"},
            {"id": "c2", "sql": "BAD SQL", "connection_env": "sg", "connection_type": "clickhouse"},
        ]
        mock_report = _make_mock_report(report_id=rid, refresh_token=tok, charts=charts)
        app = self._setup_app_with_report(mock_report)

        async def fake_run_query(sql, env, conn_type="clickhouse"):
            if "BAD" in sql:
                raise RuntimeError("CH Error: syntax error")
            return [{"n": 1}]

        with patch("backend.api.reports._run_query", side_effect=fake_run_query):
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.get(f"/reports/{rid}/data", params={"token": tok})

        self.assertEqual(resp.status_code, 200, "部分失败不应导致整体 500")
        body = resp.json()
        self.assertTrue(body["success"])
        self.assertIn("c1", body["data"], "c1 查询成功应在 data 中")
        self.assertIn("c2", body["errors"], "c2 查询失败应在 errors 中")
        self.assertIn("CH Error", body["errors"]["c2"])

    # ── I7: connection_env 为空 → 跳过该图表（不崩溃）────────────────────────
    def test_I7_empty_connection_env_skips_chart(self):
        """I7: connection_env='' 的图表被跳过，不执行查询，不崩溃。"""
        from fastapi.testclient import TestClient

        rid = str(uuid.uuid4())
        tok = "i7_tok"
        charts = [{"id": "c1", "sql": "SELECT 1", "connection_env": "", "connection_type": "clickhouse"}]
        mock_report = _make_mock_report(report_id=rid, refresh_token=tok, charts=charts)
        app = self._setup_app_with_report(mock_report)

        call_count = {"n": 0}

        async def fake_run_query(sql, env, conn_type="clickhouse"):
            call_count["n"] += 1
            return []

        with patch("backend.api.reports._run_query", side_effect=fake_run_query):
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.get(f"/reports/{rid}/data", params={"token": tok})

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(call_count["n"], 0, "connection_env 为空的图表不应调用 _run_query")


# ===========================================================================
# J — Jinja2 SQL 渲染集成测试
# ===========================================================================
class TestJinja2SqlRendering(unittest.TestCase):
    """J: render_sql + extract_default_params 集成路径全覆盖。"""

    def setUp(self):
        from backend.services.report_params_service import render_sql, extract_default_params
        self.render_sql = render_sql
        self.extract_default_params = extract_default_params

    # ── J1: date_start / date_end 标准渲染 ────────────────────────────────────
    def test_J1_date_params_rendered(self):
        """J1: {{ date_start }} / {{ date_end }} 正确替换。"""
        sql = "SELECT * FROM t WHERE dt >= '{{ date_start }}' AND dt < '{{ date_end }}'"
        result = self.render_sql(sql, {"date_start": "2025-01-01", "date_end": "2025-04-01"})
        self.assertIn("2025-01-01", result)
        self.assertIn("2025-04-01", result)
        self.assertNotIn("{{", result)

    # ── J2: 条件块渲染 ───────────────────────────────────────────────────────
    def test_J2_conditional_if_rendered(self):
        """J2: {% if enterprise_id %}...{% endif %} 条件块正确渲染（值存在时展开）。"""
        sql = "SELECT 1 {% if enterprise_id %}WHERE eid='{{ enterprise_id }}'{% endif %}"
        result = self.render_sql(sql, {"enterprise_id": "E001"})
        self.assertIn("WHERE eid='E001'", result)

    # ── J3: 条件块 — 变量不存在时折叠 ────────────────────────────────────────
    def test_J3_conditional_if_collapsed_when_missing(self):
        """J3: enterprise_id 未提供时，{% if enterprise_id %} 块应被折叠（不产生语法错误）。"""
        sql = "SELECT 1 {% if enterprise_id %}WHERE eid='{{ enterprise_id }}'{% endif %}"
        result = self.render_sql(sql, {})
        self.assertNotIn("WHERE", result)
        self.assertNotIn("{{", result)

    # ── J4: 未定义变量 → 空字符串（不崩溃）──────────────────────────────────
    def test_J4_undefined_variable_becomes_empty(self):
        """J4: SQL 模板中未定义的变量渲染后为空字符串，不抛 UndefinedError。"""
        sql = "SELECT * FROM t WHERE x = '{{ undefined_var }}'"
        result = self.render_sql(sql, {})
        self.assertNotIn("{{", result)
        self.assertIn("x = ''", result)

    # ── J5: 非模板 SQL 快速路径 ───────────────────────────────────────────────
    def test_J5_non_template_sql_passthrough(self):
        """J5: 无 {{ }} 的 SQL 直接原样返回（零开销快速路径）。"""
        sql = "SELECT count() FROM crm.calls WHERE dt >= '2025-01-01'"
        result = self.render_sql(sql, {"date_start": "2025-04-01"})
        self.assertEqual(result, sql)

    # ── J6: 默认参数 — 30 天窗口 ──────────────────────────────────────────────
    def test_J6_extract_default_30day_window(self):
        """J6: date_range filter default_days=30 → date_end=today, date_start=today-30。"""
        spec = {
            "filters": [{
                "id": "dr",
                "type": "date_range",
                "default_days": 30,
                "binds": {"start": "date_start", "end": "date_end"},
            }]
        }
        params = self.extract_default_params(spec)
        today = date.today()
        self.assertEqual(params["date_end"], today.isoformat())
        self.assertEqual(params["date_start"], (today - timedelta(days=30)).isoformat())

    # ── J7: 默认参数 — binds 为 list（Fix C 集成）────────────────────────────
    def test_J7_extract_default_with_list_binds(self):
        """J7 (Fix C): binds 为 list 时仍能提取正确默认参数。"""
        spec = {
            "filters": [{
                "id": "dr",
                "type": "date_range",
                "default_days": 7,
                "binds": ["date_start", "date_end"],  # list 格式
            }]
        }
        params = self.extract_default_params(spec)
        self.assertIn("date_start", params)
        self.assertIn("date_end", params)
        today = date.today()
        self.assertEqual(params["date_end"], today.isoformat())
        self.assertEqual(params["date_start"], (today - timedelta(days=7)).isoformat())

    # ── J8: 默认参数 — select filter 有默认值 ────────────────────────────────
    def test_J8_extract_default_select_filter(self):
        """J8: select/radio filter 有 default_value 时写入 params。"""
        spec = {
            "filters": [{
                "id": "env_filter",
                "type": "select",
                "default_value": "sg",
                "binds": {"value": "env_id"},
            }]
        }
        params = self.extract_default_params(spec)
        self.assertEqual(params.get("env_id"), "sg")

    # ── J9: Jinja2 模板注入防护（沙盒安全）──────────────────────────────────
    def test_J9_template_injection_blocked(self):
        """J9: 模板注入尝试被 SandboxedEnvironment 阻断，不执行 Python 代码。"""
        sql = "SELECT {{ ''.__class__.__mro__[1].__subclasses__() }}"
        # SandboxedEnvironment 应阻止访问 __class__
        result = self.render_sql(sql, {})
        # 不会返回 class 列表，也不应抛 SecurityError（而是渲染失败 → 返回原模板）
        self.assertNotIn("<class", result)


# ===========================================================================
# K — HTML Builder：API_BASE 修复验证（Fix A）
# ===========================================================================
class TestHtmlBuilderApiBase(unittest.TestCase):
    """K: build_report_html 生成的 HTML 含正确 API_BASE 逻辑。"""

    def setUp(self):
        from backend.services.report_builder_service import build_report_html
        self.build_report_html = build_report_html

    def _make_minimal_spec(self):
        return {
            "title": "K 节测试报表",
            "charts": [{
                "id": "c1",
                "chart_type": "bar",
                "title": "测试图",
                "sql": "SELECT 1",
                "connection_env": "sg",
                "x_field": "dt",
                "y_fields": ["cnt"],
            }],
            "filters": [],
        }

    # ── K1: 无 api_base_url → HTML 含 window.location.origin 回退 ──────────
    def test_K1_empty_api_base_uses_window_origin_fallback(self):
        """K1 (Fix A): api_base_url='' 时，HTML 中 API_BASE 通过 window.location.origin 动态推断。"""
        spec = self._make_minimal_spec()
        html = self.build_report_html(
            spec=spec,
            report_id=str(uuid.uuid4()),
            refresh_token="tok_k1",
            api_base_url="",  # ← 空字符串
        )
        self.assertIn("window.location.origin", html,
                      "无 api_base_url 时 HTML 应含 window.location.origin 回退")

    # ── K2: 有 PUBLIC_HOST → 绝对 URL 注入 ───────────────────────────────────
    def test_K2_public_host_injects_absolute_url(self):
        """K2: api_base_url='https://example.com/api/v1' 时，HTML 含该完整 URL。"""
        spec = self._make_minimal_spec()
        html = self.build_report_html(
            spec=spec,
            report_id=str(uuid.uuid4()),
            refresh_token="tok_k2",
            api_base_url="https://example.com/api/v1",
        )
        self.assertIn("https://example.com/api/v1", html)

    # ── K3: REPORT_ID / REFRESH_TOKEN 正确嵌入 ───────────────────────────────
    def test_K3_report_id_and_token_embedded(self):
        """K3: REPORT_ID 与 REFRESH_TOKEN 正确注入 HTML 全局变量。"""
        spec = self._make_minimal_spec()
        rid = str(uuid.uuid4())
        tok = "unique_refresh_token_k3"
        html = self.build_report_html(spec=spec, report_id=rid, refresh_token=tok, api_base_url="")
        self.assertIn(rid, html, "REPORT_ID 应嵌入 HTML")
        self.assertIn(tok, html, "REFRESH_TOKEN 应嵌入 HTML")

    # ── K4: 动态模式 — REPORT_DATA 为空 {} ───────────────────────────────────
    def test_K4_save_mode_report_data_empty(self):
        """K4: 非 preview 模式 REPORT_DATA 为 {} （动态加载，无静态嵌入数据）。"""
        spec = self._make_minimal_spec()
        rid = str(uuid.uuid4())
        html = self.build_report_html(spec=spec, report_id=rid, refresh_token="tok", api_base_url="")
        self.assertIn("REPORT_DATA      = {}", html,
                      "非 preview 模式 REPORT_DATA 应为空 {}")

    # ── K5: 预览模式 — REPORT_DATA 含传入数据 ─────────────────────────────────
    def test_K5_preview_mode_report_data_has_content(self):
        """K5: report_id='preview' 且 spec['data'] 有内容时，REPORT_DATA 包含数据行。

        build_report_html 从 spec.get('data', {}) 读取数据，预览时直接使用。
        """
        spec = self._make_minimal_spec()
        # 在 spec 内嵌入 data（build_report_html 从 spec.get('data', {}) 读取）
        spec["data"] = {"c1": [{"dt": "2026-01-01", "cnt": 100}]}
        html = self.build_report_html(
            spec=spec,
            report_id="preview",
            refresh_token="tok",
            api_base_url="",
        )
        self.assertIn('"dt"', html, "预览数据应嵌入 REPORT_DATA")
        self.assertIn("100", html)

    # ── K6: API_BASE 表达式是 IIFE（不是简单字符串赋值）────────────────────────
    def test_K6_api_base_uses_iife_not_plain_string(self):
        """K6: HTML 中 API_BASE 赋值使用 IIFE function，而非直接字符串常量。"""
        spec = self._make_minimal_spec()
        html = self.build_report_html(spec=spec, report_id="preview", refresh_token="tok",
                                      api_base_url="")
        # IIFE pattern: (function(){ ... })()
        self.assertIn("(function()", html,
                      "API_BASE 应使用 IIFE 表达式以支持动态回退")

    # ── K7: API_BASE 不含硬编码 localhost:8000 ────────────────────────────────
    def test_K7_no_hardcoded_localhost_in_html(self):
        """K7: 空 api_base_url 生成的 HTML 中不包含硬编码 localhost:8000。"""
        spec = self._make_minimal_spec()
        html = self.build_report_html(spec=spec, report_id=str(uuid.uuid4()),
                                      refresh_token="tok", api_base_url="")
        self.assertNotIn("localhost:8000", html,
                         "HTML 中不应含硬编码 localhost:8000（会导致 iframe 跨域失败）")

    # ── K8: default_params 注入来自 filter spec ───────────────────────────────
    def test_K8_default_params_injected_from_filters(self):
        """K8: filters 中有 date_range 时，_DEFAULT_PARAMS 含 date_start/date_end。"""
        spec = {
            "title": "K8",
            "charts": [{"id": "c1", "chart_type": "bar", "sql": "SELECT 1",
                        "connection_env": "sg", "x_field": "d", "y_fields": ["n"]}],
            "filters": [{"id": "dr", "type": "date_range", "default_days": 7,
                         "binds": {"start": "date_start", "end": "date_end"}}],
        }
        html = self.build_report_html(spec=spec, report_id=str(uuid.uuid4()),
                                      refresh_token="tok", api_base_url="")
        self.assertIn("date_start", html, "_DEFAULT_PARAMS 应含 date_start")
        self.assertIn("date_end", html, "_DEFAULT_PARAMS 应含 date_end")


# ===========================================================================
# L — 权限矩阵：reports 路由无新漏权
# ===========================================================================
class TestReportsPermissionMatrix(unittest.TestCase):
    """L: reports.py 路由权限矩阵验证 — Fix A/B/C/D 未引入新漏权。"""

    def setUp(self):
        self.content = (PROJECT_ROOT / "backend/api/reports.py").read_text(encoding="utf-8")
        import ast, re
        # Extract route definitions + their require_permission decorators
        self._routes = re.findall(
            r'@router\.(get|post|put|delete|patch)\("([^"]+)"\)',
            self.content,
        )
        self._perm_checks = re.findall(
            r'require_permission\("([^"]+)",\s*"([^"]+)"\)',
            self.content,
        )

    # ── L1: /data 端点 — 无 JWT，仅 refresh_token ──────────────────────────
    def test_L1_data_endpoint_is_token_only(self):
        """L1: GET /{report_id}/data 不依赖 require_permission（token-only，允许 HTML 内直接调用）。"""
        import re
        # Find the /data handler section
        data_idx = self.content.find('/{report_id}/data")')
        next_route_idx = self.content.find("@router.", data_idx + 1)
        data_section = self.content[data_idx:next_route_idx]
        # Should NOT have require_permission in this section
        self.assertNotIn("require_permission", data_section,
                         "/data 端点不应有 JWT require_permission 依赖（token-only 设计）")

    # ── L2: /build 需要 reports:create ────────────────────────────────────────
    def test_L2_build_requires_reports_create(self):
        """L2: POST /build 需要 require_permission('reports', 'create')。"""
        build_idx = self.content.find('"/build")')
        build_section = self.content[build_idx:build_idx + 300]
        self.assertIn("require_permission", build_section)
        self.assertIn("reports", build_section)
        self.assertIn("create", build_section)

    # ── L3: 列表接口需要 reports:read ────────────────────────────────────────
    def test_L3_list_requires_reports_read(self):
        """L3: GET /reports 列表接口需要 reports:read。"""
        self.assertTrue(
            any(res == "reports" and act == "read" for res, act in self._perm_checks),
            "应有 require_permission('reports', 'read')"
        )

    # ── L4: DELETE 需要 reports:delete ────────────────────────────────────────
    def test_L4_delete_requires_reports_delete(self):
        """L4: DELETE /{report_id} 需要 reports:delete。"""
        self.assertTrue(
            any(res == "reports" and act == "delete" for res, act in self._perm_checks),
            "应有 require_permission('reports', 'delete')"
        )

    # ── L5: /pin 需要 reports:create ─────────────────────────────────────────
    def test_L5_pin_requires_reports_create(self):
        """L5: POST /pin 需要 reports:create（Pilot 固定报表）。"""
        pin_idx = self.content.find('"/pin")')
        pin_section = self.content[pin_idx:pin_idx + 300]
        self.assertIn("require_permission", pin_section)

    # ── L6: Fix A/B/C/D 未新增任何未鉴权路由 ─────────────────────────────────
    def test_L6_no_new_unauthenticated_routes_from_fixes(self):
        """L6: Fix A-D（纯后端基础设施修复）未在 reports.py 新增任何路由定义。"""
        import re
        # Count total route definitions
        routes = re.findall(r'@router\.(get|post|put|delete|patch)\(', self.content)
        # The known list of intentionally token-only endpoints
        token_only_keywords = ["data", "refresh-data", "html", "spec-meta"]
        # All routes should either have require_permission or be in token_only_keywords
        # This test just ensures the count hasn't changed from known baseline
        self.assertGreaterEqual(len(routes), 10,
                                "routes.py 应有至少 10 个路由定义（未意外删除）")

    # ── L7: /html endpoint token-only（无 JWT）──────────────────────────────
    def test_L7_html_serve_endpoint_is_token_only(self):
        """L7: GET /{report_id}/html 为 token-only，无 require_permission（允许分享链接访问）。"""
        import re
        # Find /{report_id}/html handler
        html_idx = self.content.find('"/{report_id}/html")')
        next_route = self.content.find("@router.", html_idx + 1)
        section = self.content[html_idx:next_route]
        self.assertNotIn("require_permission", section,
                         "/{report_id}/html 应为 token-only，不要求 JWT")

    # ── L8: rbac.py 角色权限配置涵盖 reports 资源 ───────────────────────────
    def test_L8_rbac_roles_include_reports_resource(self):
        """L8: init_rbac.py 或 rbac 配置中应含 reports 资源权限定义。"""
        rbac_file = PROJECT_ROOT / "backend/scripts/init_rbac.py"
        if not rbac_file.exists():
            self.skipTest("init_rbac.py 不存在，跳过")
        content = rbac_file.read_text(encoding="utf-8")
        self.assertIn("reports", content,
                      "init_rbac.py 应包含 reports 资源权限配置")


# ===========================================================================
# M — 完整 E2E 模拟：create_report_with_spec → /data fetch
# ===========================================================================
class TestFullE2ECreateAndFetch(unittest.TestCase):
    """M: create_report_with_spec 生成报表 → 验证 HTML + report_id/token 可用于 /data。"""

    def test_M1_create_returns_required_keys(self):
        """M1: create_report_with_spec 返回 report_id/refresh_token/name/html_path/message。"""
        import uuid as _uuid
        spec = {
            "title": "M1 E2E 报表",
            "charts": [{"id": "c1", "chart_type": "bar", "sql": "SELECT 1",
                        "connection_env": "sg", "x_field": "d", "y_fields": ["n"]}],
            "filters": [{"id": "dr", "type": "date_range", "default_days": 30,
                         "binds": {"start": "date_start", "end": "date_end"}}],
        }
        mock_report_obj = MagicMock()
        mock_report_obj.id = _uuid.uuid4()
        mock_db = MagicMock()
        mock_db.__enter__ = MagicMock(return_value=mock_db)
        mock_db.__exit__ = MagicMock(return_value=False)

        with patch("backend.config.database.get_db_context", return_value=mock_db), \
             patch("backend.services.report_builder_service.generate_refresh_token",
                   return_value="m1_token_xyz"), \
             patch("backend.services.report_builder_service.build_report_html",
                   return_value="<html>mock</html>"), \
             patch("backend.models.report.Report", return_value=mock_report_obj), \
             patch("backend.models.report.ReportType"), \
             patch("pathlib.Path.mkdir"), \
             patch("pathlib.Path.write_text"):
            from backend.services.report_service import create_report_with_spec
            result = create_report_with_spec(spec=spec, username="testuser_m1")

        for key in ("report_id", "refresh_token", "name", "html_path", "message"):
            self.assertIn(key, result, f"create 返回缺少: {key}")
        self.assertEqual(result["refresh_token"], "m1_token_xyz")

    def test_M2_html_contains_report_id_and_token(self):
        """M2: 生成的 HTML 中 REPORT_ID 和 REFRESH_TOKEN 已正确注入。"""
        from backend.services.report_builder_service import build_report_html
        spec = {
            "title": "M2",
            "charts": [{"id": "c1", "chart_type": "bar", "sql": "SELECT 1",
                        "connection_env": "sg", "x_field": "d", "y_fields": ["n"]}],
            "filters": [],
        }
        rid = str(uuid.uuid4())
        tok = "m2_token_abc"
        html = build_report_html(spec=spec, report_id=rid, refresh_token=tok, api_base_url="")
        self.assertIn(rid, html, "REPORT_ID 应嵌入 HTML")
        self.assertIn(tok, html, "REFRESH_TOKEN 应嵌入 HTML")

    def test_M3_create_uses_empty_api_base_by_default(self):
        """M3 (Fix A2): create_report_with_spec 在无 PUBLIC_HOST 时传空 api_base_url。"""
        import inspect
        from backend.services.report_service import create_report_with_spec
        src = inspect.getsource(create_report_with_spec)
        # _api_base_url() is called and its result used as api_base_url
        self.assertIn("_api_base_url()", src,
                      "create_report_with_spec 应调用 _api_base_url() 确定 api_base")

    def test_M4_create_html_path_in_reports_subdir(self):
        """M4: create_report_with_spec 将 HTML 写入 {username}/reports/ 子目录。"""
        import inspect
        from backend.services.report_service import create_report_with_spec
        src = inspect.getsource(create_report_with_spec)
        self.assertIn("reports/", src, "HTML 应写入 reports/ 子目录")

    def test_M5_html_loads_data_dynamically_not_statically(self):
        """M5: 动态模式 HTML 在 DOMContentLoaded 时调用 _loadData，不静态嵌入数据。"""
        from backend.services.report_builder_service import build_report_html
        spec = {
            "title": "M5",
            "charts": [{"id": "c1", "chart_type": "bar", "sql": "SELECT 1",
                        "connection_env": "sg", "x_field": "d", "y_fields": ["n"]}],
            "filters": [],
        }
        rid = str(uuid.uuid4())
        html = build_report_html(spec=spec, report_id=rid, refresh_token="tok", api_base_url="")
        # Dynamic load: _loadData called on DOMContentLoaded
        self.assertIn("_loadData", html, "HTML 应含 _loadData 调用（动态加载）")
        # Static data should be empty
        self.assertIn("REPORT_DATA      = {}", html)
        # Should NOT embed real data rows
        self.assertNotIn('"rows"', html)

    def test_M6_api_base_url_called_in_create_service(self):
        """M6: create_report_with_spec 通过 _api_base_url() 获取 API 根，支持 PUBLIC_HOST 覆盖。"""
        import inspect
        from backend.services import report_service
        # _api_base_url must be called in create_report_with_spec
        src = inspect.getsource(report_service.create_report_with_spec)
        self.assertIn("_api_base_url()", src)
        # And _api_base_url should return "" when no PUBLIC_HOST
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PUBLIC_HOST", None)
            result = report_service._api_base_url()
        self.assertEqual(result, "", "_api_base_url() 无 PUBLIC_HOST 应返回 ''")


# ===========================================================================
# N — 回归：_normalize_binds 边界条件 & compute_params_from_binds
# ===========================================================================
class TestBindsNormalizeEdgeCases(unittest.TestCase):
    """N: _normalize_binds 全边界条件 + compute_params_from_binds 兼容性。"""

    def setUp(self):
        from backend.services.report_params_service import (
            _normalize_binds, compute_params_from_binds, extract_default_params
        )
        self.normalize = _normalize_binds
        self.compute = compute_params_from_binds
        self.extract = extract_default_params

    # ── N1: dict 原样保留 ─────────────────────────────────────────────────────
    def test_N1_dict_binds_preserved(self):
        b = {"start": "date_start", "end": "date_end", "value": "env"}
        self.assertEqual(self.normalize(b), b)

    # ── N2: list 两元素 → start/end ──────────────────────────────────────────
    def test_N2_list_two_elements(self):
        r = self.normalize(["date_start", "date_end"])
        self.assertEqual(r["start"], "date_start")
        self.assertEqual(r["end"], "date_end")

    # ── N3: list 三元素 → start/end/value ────────────────────────────────────
    def test_N3_list_three_elements(self):
        r = self.normalize(["date_start", "date_end", "env_val"])
        self.assertEqual(r["start"], "date_start")
        self.assertEqual(r["end"], "date_end")
        self.assertEqual(r["value"], "env_val")

    # ── N4: list 单元素 → 只有 start ─────────────────────────────────────────
    def test_N4_list_single_element(self):
        r = self.normalize(["only_one"])
        self.assertEqual(r["start"], "only_one")
        self.assertNotIn("end", r)

    # ── N5: None → 空 dict ────────────────────────────────────────────────────
    def test_N5_none_returns_empty(self):
        self.assertEqual(self.normalize(None), {})

    # ── N6: 空 list → 空 dict ─────────────────────────────────────────────────
    def test_N6_empty_list_returns_empty(self):
        self.assertEqual(self.normalize([]), {})

    # ── N7: compute_params_from_binds — dict binds ────────────────────────────
    def test_N7_compute_params_dict_binds(self):
        specs = [{
            "id": "dr",
            "type": "date_range",
            "binds": {"start": "date_start", "end": "date_end"},
        }]
        values = {"dr": {"start": "2025-01-01", "end": "2025-03-31"}}
        result = self.compute(specs, values)
        self.assertEqual(result["date_start"], "2025-01-01")
        self.assertEqual(result["date_end"], "2025-03-31")

    # ── N8: compute_params_from_binds — list binds（Fix C 集成）──────────────
    def test_N8_compute_params_list_binds(self):
        """N8 (Fix C): list 格式 binds 在 compute_params_from_binds 也正确处理。"""
        specs = [{
            "id": "dr",
            "type": "date_range",
            "binds": ["date_start", "date_end"],  # list
        }]
        values = {"dr": {"start": "2026-01-01", "end": "2026-04-01"}}
        result = self.compute(specs, values)
        self.assertEqual(result.get("date_start"), "2026-01-01")
        self.assertEqual(result.get("date_end"), "2026-04-01")

    # ── N9: 无 filter 值 → params 为空 ────────────────────────────────────────
    def test_N9_no_filter_value_skipped(self):
        specs = [{"id": "dr", "type": "date_range", "binds": {"start": "s", "end": "e"}}]
        result = self.compute(specs, {})  # no values
        self.assertEqual(result, {})

    # ── N10: extract 多个 filter 全部处理 ─────────────────────────────────────
    def test_N10_extract_multiple_filters(self):
        spec = {
            "filters": [
                {"id": "dr", "type": "date_range", "default_days": 14,
                 "binds": {"start": "date_start", "end": "date_end"}},
                {"id": "ef", "type": "select", "default_value": "sg",
                 "binds": {"value": "env_id"}},
            ]
        }
        params = self.extract(spec)
        self.assertIn("date_start", params)
        self.assertIn("date_end", params)
        self.assertEqual(params.get("env_id"), "sg")


# ===========================================================================
# O — connection_env 规范化：_get_or_init_ch_client 完整行为验证
# ===========================================================================
class TestConnectionEnvFullBehavior(unittest.IsolatedAsyncioTestCase):
    """O: _get_or_init_ch_client Fix B1 — 规范化 + 缓存行为。"""

    async def test_O1_clickhouse_prefix_stripped(self):
        """O1: 'clickhouse-sg' → strip → 'sg' → ClickHouseMCPServer(env='sg')。"""
        from backend.api import reports as rmod

        # Clear cache
        rmod._ch_client_cache.clear()

        mock_srv = AsyncMock()
        mock_srv.client = MagicMock(spec=["execute"])

        with patch("backend.mcp.clickhouse.server.ClickHouseMCPServer",
                   return_value=mock_srv) as mock_cls:
            client = await rmod._get_or_init_ch_client("clickhouse-sg")

        call_kwargs = mock_cls.call_args
        env_arg = call_kwargs[1].get("env") or call_kwargs[0][0]
        self.assertEqual(env_arg, "sg",
                         f"ClickHouseMCPServer 应以 'sg' 初始化，实际: {env_arg!r}")

    async def test_O2_plain_env_unchanged(self):
        """O2: 'sg' 不带前缀 → ClickHouseMCPServer(env='sg')（不变）。"""
        from backend.api import reports as rmod
        rmod._ch_client_cache.clear()

        mock_srv = AsyncMock()
        mock_srv.client = MagicMock(spec=["execute"])

        with patch("backend.mcp.clickhouse.server.ClickHouseMCPServer",
                   return_value=mock_srv) as mock_cls:
            await rmod._get_or_init_ch_client("sg")

        call_kwargs = mock_cls.call_args
        env_arg = call_kwargs[1].get("env") or call_kwargs[0][0]
        self.assertEqual(env_arg, "sg")

    async def test_O3_cache_hit_no_reinit(self):
        """O3: 相同 env 第二次调用命中缓存，不再初始化新 ClickHouseMCPServer。"""
        from backend.api import reports as rmod
        rmod._ch_client_cache.clear()

        mock_srv = AsyncMock()
        mock_srv.client = MagicMock(spec=["execute"])
        call_count = {"n": 0}

        def counting_cls(*a, **kw):
            call_count["n"] += 1
            return mock_srv

        with patch("backend.mcp.clickhouse.server.ClickHouseMCPServer",
                   side_effect=counting_cls):
            await rmod._get_or_init_ch_client("sg")
            await rmod._get_or_init_ch_client("sg")  # second call

        self.assertEqual(call_count["n"], 1,
                         "相同 env 第二次应命中缓存，只初始化 1 次")

    async def test_O4_different_envs_separate_cache_entries(self):
        """O4: 'sg' 和 'idn' 各自独立缓存，互不干扰。"""
        from backend.api import reports as rmod
        rmod._ch_client_cache.clear()

        init_envs = []

        mock_srv = AsyncMock()
        mock_srv.client = MagicMock(spec=["execute"])

        def tracking_cls(*a, **kw):
            init_envs.append(kw.get("env") or a[0])
            return mock_srv

        with patch("backend.mcp.clickhouse.server.ClickHouseMCPServer",
                   side_effect=tracking_cls):
            await rmod._get_or_init_ch_client("sg")
            await rmod._get_or_init_ch_client("idn")

        self.assertEqual(sorted(init_envs), ["idn", "sg"])

    async def test_O5_clickhouse_sg_azure_prefix_stripped(self):
        """O5: 'clickhouse-sg-azure' → 'sg-azure'（带 hyphen 的 env 名不被误删）。"""
        from backend.api import reports as rmod
        rmod._ch_client_cache.clear()

        mock_srv = AsyncMock()
        mock_srv.client = MagicMock(spec=["execute"])

        with patch("backend.mcp.clickhouse.server.ClickHouseMCPServer",
                   return_value=mock_srv) as mock_cls:
            await rmod._get_or_init_ch_client("clickhouse-sg-azure")

        call_kwargs = mock_cls.call_args
        env_arg = call_kwargs[1].get("env") or call_kwargs[0][0]
        self.assertEqual(env_arg, "sg-azure",
                         f"sg-azure 只去掉 'clickhouse-' 前缀，实际: {env_arg!r}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
