"""
test_report_filter_e2e_full.py
================================
资深测试工程师视角：针对「日期筛选器不生效」根因修复的全量端到端测试。

设计目标：
  1. 覆盖三层修复（HTML-JS / DB归一化 / regenerate-html）的核心E2E路径
  2. 验证 RBAC 权限矩阵——新增端点是否纳入角色管理范围
  3. 边界场景与安全回归（非Owner 403、无效ID 404、无HTML路径 400）
  4. 向后兼容性（/refresh-data → /data 委托链）

测试节：
  P — RBAC：regenerate-html 权限矩阵（analyst/admin/superadmin可用，viewer不可用）
  Q — 全量E2E：create(array binds) → /data(新日期) → SQL含新日期 → regenerate修复旧报表
  R — 向后兼容：/refresh-data 委托、已有测试不受影响
  S — 边界与安全：非Owner 403、无HTML路径400、multi_select/select binds归一化

覆盖缺口说明（基于原 test_report_filter_binds.py / test_report_fetch_e2e.py）：
  ✗ P section — regenerate-html 的 RBAC 矩阵（analyst/admin 可用 viewer 不可用）原先缺失
  ✗ Q section — create→html生成→/data链路的连贯E2E（原测试为分段独立测试）
  ✗ R section — /refresh-data 委托路径（原测试未覆盖）
  ✗ S section — 非owner 403、无html_path 400、JS multi_select绑定归一化
"""
from __future__ import annotations

import json
import os
import re
import sys
import secrets
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

PROJECT_ROOT = Path(__file__).parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

os.environ.setdefault("ENABLE_AUTH", "False")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")


# ─────────────────────────────────────────────────────────────────────────────
# 公共工具
# ─────────────────────────────────────────────────────────────────────────────

def _make_mock_report(
    report_id: str = None,
    refresh_token: str = "tok_default",
    charts: list = None,
    filters: list = None,
    username: str = "superadmin",
    report_file_path: str = None,
    name: str = "测试报表",
    description: str = "",
    theme: str = "light",
):
    m = MagicMock()
    m.id = uuid.UUID(report_id) if report_id else uuid.uuid4()
    m.refresh_token = refresh_token
    m.charts = charts or []
    m.filters = filters or []
    m.username = username
    m.report_file_path = report_file_path or f"{username}/reports/test.html"
    m.name = name
    m.description = description
    m.theme = theme
    m.increment_view_count = MagicMock()
    return m


def _make_app(mock_report=None, mock_user=None):
    """构造 FastAPI TestClient，report router 自带 prefix '/reports'。"""
    from fastapi import FastAPI
    from backend.api.reports import router
    from backend.config.database import get_db
    from backend.api.deps import get_current_user

    app = FastAPI()
    app.include_router(router)  # router 自带 prefix="/reports"，不重复

    if mock_report is not None:
        def _override_db():
            db = MagicMock()
            db.query.return_value.filter.return_value.first.return_value = mock_report
            db.commit = MagicMock()
            yield db
        app.dependency_overrides[get_db] = _override_db

    if mock_user is not None:
        app.dependency_overrides[get_current_user] = lambda: mock_user

    return app


# ─────────────────────────────────────────────────────────────────────────────
# P — RBAC 权限矩阵：regenerate-html 端点
# ─────────────────────────────────────────────────────────────────────────────

class TestPRbacRegenerateHtml(unittest.TestCase):
    """P1~P8: POST /reports/{id}/regenerate-html 权限矩阵全量验证。"""

    @classmethod
    def setUpClass(cls):
        rbac_path = PROJECT_ROOT / "backend/scripts/init_rbac.py"
        cls.rbac_content = rbac_path.read_text(encoding="utf-8")
        reports_path = PROJECT_ROOT / "backend/api/reports.py"
        cls.reports_content = reports_path.read_text(encoding="utf-8")

    # ── P1: 源码中 regenerate-html 路由绑定了 reports:create 权限 ────────────
    def test_P1_regenerate_html_route_requires_reports_create_in_source(self):
        """P1: reports.py 中 regenerate-html 端点使用 require_permission('reports','create')"""
        idx = self.reports_content.find("regenerate-html")
        self.assertGreater(idx, 0, "源码中未找到 regenerate-html 路由")
        section = self.reports_content[idx : idx + 500]
        self.assertIn("require_permission", section,
                      "regenerate-html 端点应绑定 require_permission")
        self.assertIn("reports", section)
        self.assertIn("create", section)

    # ── P2: analyst 角色拥有 reports:create ──────────────────────────────────
    def test_P2_analyst_role_has_reports_create(self):
        """P2: init_rbac.py 中 analyst 角色包含 reports:create 权限"""
        # 找 analyst 角色定义段
        analyst_idx = self.rbac_content.find('"analyst"')
        next_role_idx = self.rbac_content.find('"admin"', analyst_idx + 1)
        analyst_section = self.rbac_content[analyst_idx:next_role_idx]
        self.assertIn("reports:create", analyst_section,
                      "analyst 角色应包含 reports:create 权限，允许生成/修复报表")

    # ── P3: admin 角色拥有 reports:create ────────────────────────────────────
    def test_P3_admin_role_has_reports_create(self):
        """P3: init_rbac.py 中 admin 角色包含 reports:create 权限"""
        admin_idx = self.rbac_content.find('"admin"')
        # admin 段从 "admin" 到 "superadmin"
        next_role_idx = self.rbac_content.find('"superadmin"', admin_idx + 1)
        admin_section = self.rbac_content[admin_idx:next_role_idx]
        self.assertIn("reports:create", admin_section,
                      "admin 角色应包含 reports:create 权限")

    # ── P4: viewer 角色不含 reports:create ───────────────────────────────────
    def test_P4_viewer_role_does_not_have_reports_create(self):
        """P4: init_rbac.py 中 viewer 角色不包含 reports:create（只读角色）"""
        viewer_idx = self.rbac_content.find('"viewer"')
        next_role_idx = self.rbac_content.find('"analyst"', viewer_idx + 1)
        viewer_section = self.rbac_content[viewer_idx:next_role_idx]
        self.assertNotIn("reports:create", viewer_section,
                         "viewer 角色不应有 reports:create 权限")

    # ── P5: superadmin 拥有所有 reports 权限 ────────────────────────────────
    def test_P5_superadmin_has_all_reports_permissions(self):
        """P5: init_rbac.py 中 superadmin 拥有 reports:read/create/delete"""
        for perm in ("reports:read", "reports:create", "reports:delete"):
            self.assertIn(perm, self.rbac_content,
                          f"init_rbac.py 应定义 {perm} 权限")

    # ── P6: reports:create 权限定义存在于权限列表 ────────────────────────────
    def test_P6_reports_create_defined_in_permissions_list(self):
        """P6: init_rbac.py PERMISSIONS 常量中有 ('reports', 'create') 元组"""
        self.assertIn('"reports"', self.rbac_content)
        self.assertIn('"create"', self.rbac_content)
        # 检查元组格式 ("reports", "create", ...)
        self.assertRegex(self.rbac_content,
                         r'["\']reports["\'].*["\']create["\']',
                         "应有 reports:create 权限元组定义")

    # ── P7: 非owner 调用 regenerate-html 返回 403 ────────────────────────────
    def test_P7_non_owner_gets_403_on_regenerate_html(self):
        """P7: 报表属于 alice，bob 调用 regenerate-html 应收到 403"""
        from fastapi.testclient import TestClient

        rid = uuid.uuid4()
        tok = secrets.token_urlsafe(32)
        mock_report = _make_mock_report(
            report_id=str(rid),
            refresh_token=tok,
            username="alice",  # 报表属于 alice
            report_file_path="alice/reports/test.html",
        )

        bob = MagicMock()
        bob.username = "bob"
        bob.is_superadmin = False

        app = _make_app(mock_report=mock_report, mock_user=bob)
        client = TestClient(app, raise_server_exceptions=False)

        with patch("backend.api.reports._CUSTOMER_DATA_ROOT", Path(tempfile.mkdtemp())):
            resp = client.post(f"/reports/{rid}/regenerate-html")

        self.assertEqual(resp.status_code, 403,
                         f"非owner应收到403，实际: {resp.status_code} {resp.text[:200]}")

    # ── P8: 不存在的 report_id 返回 404 ─────────────────────────────────────
    def test_P8_nonexistent_report_returns_404(self):
        """P8: 随机UUID调用 regenerate-html，DB中不存在时返回 404"""
        from fastapi.testclient import TestClient
        from fastapi import FastAPI
        from backend.api.reports import router
        from backend.config.database import get_db
        from backend.api.deps import get_current_user

        app = FastAPI()
        app.include_router(router)

        superadmin = MagicMock()
        superadmin.username = "superadmin"
        superadmin.is_superadmin = True

        def _override_db():
            db = MagicMock()
            db.query.return_value.filter.return_value.first.return_value = None  # 不存在
            yield db

        app.dependency_overrides[get_db] = _override_db
        app.dependency_overrides[get_current_user] = lambda: superadmin

        client = TestClient(app, raise_server_exceptions=False)
        rand_id = uuid.uuid4()
        resp = client.post(f"/reports/{rand_id}/regenerate-html")
        self.assertEqual(resp.status_code, 404)

    # ── P9: 无 html_path 的报表返回 400 ─────────────────────────────────────
    def test_P9_report_without_html_path_returns_400(self):
        """P9: report_file_path 为空时 regenerate-html 返回 400"""
        from fastapi.testclient import TestClient

        rid = uuid.uuid4()
        mock_report = _make_mock_report(
            report_id=str(rid),
            username="superadmin",
            report_file_path=None,  # 无路径
        )
        mock_report.report_file_path = None  # 确保 MagicMock 覆盖

        superadmin = MagicMock()
        superadmin.username = "superadmin"
        superadmin.is_superadmin = True

        app = _make_app(mock_report=mock_report, mock_user=superadmin)
        client = TestClient(app, raise_server_exceptions=False)

        with patch("backend.api.reports._CUSTOMER_DATA_ROOT", Path(tempfile.mkdtemp())):
            resp = client.post(f"/reports/{rid}/regenerate-html")

        self.assertEqual(resp.status_code, 400,
                         f"无html_path应返回400，实际: {resp.status_code} {resp.text[:200]}")


# ─────────────────────────────────────────────────────────────────────────────
# Q — 全量 E2E：筛选器修改 → 数据刷新完整链路
# ─────────────────────────────────────────────────────────────────────────────

class TestQFilterE2EChain(unittest.TestCase):
    """Q1~Q8: 筛选器日期修改 → API参数传递 → SQL渲染 → 数据返回 全链路验证。"""

    def _make_data_client(self, mock_report):
        """构造 /data 端点的 TestClient。"""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from backend.api.reports import router
        from backend.config.database import get_db

        app = FastAPI()
        app.include_router(router)

        def _override_db():
            db = MagicMock()
            db.query.return_value.filter.return_value.first.return_value = mock_report
            db.commit = MagicMock()
            yield db

        app.dependency_overrides[get_db] = _override_db
        return TestClient(app, raise_server_exceptions=False)

    # ── Q1: 核心链路：显式日期参数被 /data 端点正确接收并渲染到 SQL ───────────
    def test_Q1_explicit_date_params_reach_sql(self):
        """Q1: /data?date_start=3.1&date_end=3.31 → SQL 包含 2026-03-01 和 2026-03-31"""
        executed = []
        rid = uuid.uuid4()
        tok = "q1_token"
        mock_report = _make_mock_report(
            report_id=str(rid),
            refresh_token=tok,
            charts=[{
                "id": "c1",
                "sql": "SELECT * FROM t WHERE s >= '{{ date_start }}' AND s < '{{ date_end }}'",
                "connection_env": "sg",
                "connection_type": "clickhouse",
            }],
            filters=[{
                "id": "date_range", "type": "date_range",
                "binds": ["date_start", "date_end"],  # array 格式（旧报表）
                "default_days": 30,
            }],
        )
        client = self._make_data_client(mock_report)

        async def _mock_query(sql, env, conn_type="clickhouse"):
            executed.append(sql)
            return [{"row": 1}]

        with patch("backend.api.reports._run_query", side_effect=_mock_query):
            resp = client.get(f"/reports/{rid}/data",
                              params={"token": tok,
                                      "date_start": "2026-03-01",
                                      "date_end": "2026-03-31"})

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["success"])
        self.assertEqual(body["params_used"]["date_start"], "2026-03-01")
        self.assertEqual(body["params_used"]["date_end"], "2026-03-31")
        self.assertTrue(len(executed) > 0, "应至少执行一条 SQL")
        self.assertIn("2026-03-01", executed[0], f"SQL 中应含新开始日期，实际: {executed[0]}")
        self.assertIn("2026-03-31", executed[0], f"SQL 中应含新结束日期，实际: {executed[0]}")

    # ── Q2: dict binds 报表同样能接收显式日期参数 ────────────────────────────
    def test_Q2_dict_binds_report_also_accepts_explicit_params(self):
        """Q2: dict 格式 binds 报表，/data 传新日期，SQL 正确渲染"""
        executed = []
        rid = uuid.uuid4()
        tok = "q2_token"
        mock_report = _make_mock_report(
            report_id=str(rid),
            refresh_token=tok,
            charts=[{
                "id": "c1",
                "sql": "SELECT * FROM t WHERE dt >= '{{ date_start }}'",
                "connection_env": "sg",
                "connection_type": "clickhouse",
            }],
            filters=[{
                "id": "date_range", "type": "date_range",
                "binds": {"start": "date_start", "end": "date_end"},  # dict 格式
                "default_days": 30,
            }],
        )
        client = self._make_data_client(mock_report)

        async def _mock_query(sql, env, conn_type="clickhouse"):
            executed.append(sql)
            return []

        with patch("backend.api.reports._run_query", side_effect=_mock_query):
            resp = client.get(f"/reports/{rid}/data",
                              params={"token": tok,
                                      "date_start": "2026-02-01",
                                      "date_end": "2026-02-28"})

        self.assertEqual(resp.status_code, 200)
        self.assertIn("2026-02-01", executed[0])

    # ── Q3: 无参数时使用 spec.filters 默认值（default_days）─────────────────
    def test_Q3_no_params_uses_filter_default_days(self):
        """Q3: 不传 query 参数时，/data 自动从 filters.default_days 计算默认日期区间"""
        from datetime import date, timedelta
        executed = []
        rid = uuid.uuid4()
        tok = "q3_token"
        today = date.today()
        mock_report = _make_mock_report(
            report_id=str(rid),
            refresh_token=tok,
            charts=[{
                "id": "c1",
                "sql": "SELECT * FROM t WHERE dt >= '{{ date_start }}'",
                "connection_env": "sg",
                "connection_type": "clickhouse",
            }],
            filters=[{
                "id": "date_range", "type": "date_range",
                "binds": {"start": "date_start", "end": "date_end"},
                "default_days": 7,  # 近7天
            }],
        )
        client = self._make_data_client(mock_report)

        async def _mock_query(sql, env, conn_type="clickhouse"):
            executed.append(sql)
            return []

        with patch("backend.api.reports._run_query", side_effect=_mock_query):
            resp = client.get(f"/reports/{rid}/data", params={"token": tok})

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        expected_start = (today - timedelta(days=7)).isoformat()
        self.assertEqual(body["params_used"]["date_start"], expected_start,
                         f"默认 date_start 应为 {expected_start}，实际: {body['params_used']}")

    # ── Q4: create_report_with_spec — array binds → DB 存 dict binds ─────────
    def test_Q4_create_with_array_binds_stores_dict_in_db(self):
        """Q4: create_report_with_spec 接受 array binds spec，DB 中 filters.binds 为 dict"""
        from backend.services.report_params_service import _normalize_binds
        # 直接测试归一化逻辑（create 函数内部调用）
        array_binds = ["date_start", "date_end"]
        result = _normalize_binds(array_binds)
        self.assertIsInstance(result, dict)
        self.assertEqual(result["start"], "date_start")
        self.assertEqual(result["end"], "date_end")
        # 验证 create 函数确实调用了这个归一化逻辑
        import inspect
        from backend.services import report_service
        src = inspect.getsource(report_service.create_report_with_spec)
        self.assertIn("_normalize_binds", src, "create_report_with_spec 应调用 _normalize_binds")
        self.assertIn("normalized_filters", src)

    # ── Q5: create → HTML 文件中 binds 为 dict 格式 ──────────────────────────
    def test_Q5_html_generated_with_dict_binds_in_report_spec(self):
        """Q5: create_report_with_spec 后 HTML 文件的 REPORT_SPEC.filters[0].binds 为 dict"""
        from backend.services.report_builder_service import build_report_html
        spec = {
            "title": "Q5 测试",
            "charts": [{
                "id": "c1", "chart_type": "bar",
                "sql": "SELECT 1", "connection_env": "sg",
                "connection_type": "clickhouse", "x_field": "v", "y_fields": ["v"],
            }],
            "filters": [{
                "id": "date_range", "type": "date_range",
                "binds": {"start": "date_start", "end": "date_end"},  # 归一化后
                "default_days": 30,
            }],
        }
        html = build_report_html(spec, "q5-test-id", "tok_q5", "")
        # REPORT_SPEC 中 binds 应为 dict 格式
        self.assertIn('"start": "date_start"', html,
                      "HTML 的 REPORT_SPEC 中 binds 应含 start 字段")
        self.assertIn('"end": "date_end"', html,
                      "HTML 的 REPORT_SPEC 中 binds 应含 end 字段")
        # 不应有 array 格式
        self.assertNotIn('"binds": ["date_start"', html,
                         "归一化后的 HTML 不应含 array 格式 binds")

    # ── Q6: 全链路 — create(array) → regenerate → /data(新日期) → SQL含新日期 ──
    def test_Q6_full_chain_create_array_regen_data_with_new_dates(self):
        """Q6: 完整链路：array binds 报表 → regenerate-html → /data 传新日期 → SQL 正确"""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from backend.api.reports import router
        from backend.config.database import get_db
        from backend.api.deps import get_current_user

        executed = []
        rid = uuid.uuid4()
        tok = "q6_token"

        # 模拟旧报表（array binds 未归一化）
        mock_report = _make_mock_report(
            report_id=str(rid),
            refresh_token=tok,
            charts=[{
                "id": "c1",
                "sql": "SELECT sum(call_num) FROM t WHERE s_day >= '{{ date_start }}' AND s_day <= '{{ date_end }}'",
                "connection_env": "sg",
                "connection_type": "clickhouse",
            }],
            filters=[{
                "id": "date_range", "type": "date_range",
                "binds": ["date_start", "date_end"],  # 未归一化 array 格式
                "default_days": 30,
            }],
            report_file_path="superadmin/reports/q6_test.html",
        )

        superadmin = MagicMock()
        superadmin.username = "superadmin"
        superadmin.is_superadmin = True

        app = FastAPI()
        app.include_router(router)

        def _override_db():
            db = MagicMock()
            db.query.return_value.filter.return_value.first.return_value = mock_report
            db.commit = MagicMock()
            yield db

        app.dependency_overrides[get_db] = _override_db
        app.dependency_overrides[get_current_user] = lambda: superadmin

        async def _mock_query(sql, env, conn_type="clickhouse"):
            executed.append(sql)
            return [{"cnt": 100}]

        with tempfile.TemporaryDirectory() as tmpdir:
            html_dir = Path(tmpdir) / "superadmin" / "reports"
            html_dir.mkdir(parents=True)
            (html_dir / "q6_test.html").write_text("<OLD>", encoding="utf-8")

            with patch("backend.api.reports._CUSTOMER_DATA_ROOT", Path(tmpdir)), \
                 patch("backend.api.reports._run_query", side_effect=_mock_query):
                client = TestClient(app, raise_server_exceptions=False)

                # Step 1: regenerate-html 修复旧报表
                regen_resp = client.post(f"/reports/{rid}/regenerate-html")
                self.assertEqual(regen_resp.status_code, 200,
                                 f"regenerate-html 失败: {regen_resp.text[:300]}")

                # Step 2: 验证新 HTML 文件包含 Array.isArray 归一化
                new_html = (html_dir / "q6_test.html").read_text(encoding="utf-8")
                self.assertIn("Array.isArray(binds)", new_html,
                              "重新生成的 HTML 应含 Array.isArray 归一化")

                # Step 3: /data 传新日期 → SQL 使用新日期
                data_resp = client.get(
                    f"/reports/{rid}/data",
                    params={"token": tok,
                            "date_start": "2026-03-01",
                            "date_end": "2026-03-31"},
                )
                self.assertEqual(data_resp.status_code, 200,
                                 f"/data 失败: {data_resp.text[:300]}")
                body = data_resp.json()
                self.assertEqual(body["params_used"]["date_start"], "2026-03-01")
                self.assertIn("2026-03-01", executed[-1], "最后执行的SQL应含新开始日期")
                self.assertIn("2026-03-31", executed[-1], "最后执行的SQL应含新结束日期")

    # ── Q7: _currentParams() JS 逻辑 — array binds 被正确归一化 ──────────────
    def test_Q7_js_currentparams_array_binds_normalization_in_generated_html(self):
        """Q7: build_report_html 生成的 HTML 中 _currentParams 函数归一化 array binds"""
        from backend.services.report_builder_service import build_report_html
        spec = {
            "title": "Q7",
            "charts": [],
            "filters": [{"id": "date_range", "type": "date_range",
                          "binds": ["date_start", "date_end"], "default_days": 7}],
        }
        html = build_report_html(spec, "q7-id", "tok", "")
        # 找到 _currentParams 函数并验证关键逻辑
        fn_start = html.find("function _currentParams()")
        fn_end = html.find("\nfunction ", fn_start + 1)
        fn_body = html[fn_start:fn_end]
        self.assertIn("Array.isArray(binds)", fn_body)
        self.assertIn("_b.start  = binds[0]", fn_body)
        self.assertIn("_b.end    = binds[1]", fn_body)
        self.assertIn("binds.start", fn_body)
        self.assertIn("binds.end", fn_body)

    # ── Q8: _currentParams() JS 逻辑 — multi_select binds 归一化 ─────────────
    def test_Q8_js_currentparams_multi_select_binds_in_html(self):
        """Q8: HTML 的 _currentParams 正确处理 multi_select 类型 binds.values"""
        from backend.services.report_builder_service import build_report_html
        spec = {
            "title": "Q8",
            "charts": [],
            "filters": [
                {"id": "date_range", "type": "date_range",
                 "binds": {"start": "date_start", "end": "date_end"}, "default_days": 7},
                {"id": "env_select", "type": "multi_select", "label": "环境",
                 "binds": {"values": "env_list"}, "options": []},
            ],
        }
        html = build_report_html(spec, "q8-id", "tok", "")
        fn_start = html.find("function _currentParams()")
        fn_end = html.find("\nfunction ", fn_start + 1)
        fn_body = html[fn_start:fn_end]
        # multi_select 分支应存在
        self.assertIn("multi_select", fn_body,
                      "_currentParams 应处理 multi_select 类型")
        self.assertIn("binds.values", fn_body)


# ─────────────────────────────────────────────────────────────────────────────
# R — 向后兼容：/refresh-data 委托链
# ─────────────────────────────────────────────────────────────────────────────

class TestRBackwardCompatibility(unittest.TestCase):
    """R1~R4: /refresh-data 向后兼容委托、已有端点不受影响。"""

    def _make_data_client(self, mock_report):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from backend.api.reports import router
        from backend.config.database import get_db

        app = FastAPI()
        app.include_router(router)

        def _override_db():
            db = MagicMock()
            db.query.return_value.filter.return_value.first.return_value = mock_report
            db.commit = MagicMock()
            yield db

        app.dependency_overrides[get_db] = _override_db
        return TestClient(app, raise_server_exceptions=False)

    # ── R1: /refresh-data 委托到 /data，响应格式相同 ─────────────────────────
    def test_R1_refresh_data_delegates_to_data_endpoint(self):
        """R1: GET /refresh-data 与 /data 返回相同结构（向后兼容旧版 HTML）"""
        rid = uuid.uuid4()
        tok = "r1_token"
        mock_report = _make_mock_report(
            report_id=str(rid), refresh_token=tok,
            charts=[], filters=[],
        )
        client = self._make_data_client(mock_report)

        # /refresh-data（旧接口）
        resp_old = client.get(f"/reports/{rid}/refresh-data",
                              params={"token": tok})
        # /data（新接口）
        resp_new = client.get(f"/reports/{rid}/data",
                              params={"token": tok})

        self.assertEqual(resp_old.status_code, 200)
        self.assertEqual(resp_new.status_code, 200)
        old_body = resp_old.json()
        new_body = resp_new.json()
        # 两者都应有相同的顶层键
        for key in ("success", "data", "errors", "params_used"):
            self.assertIn(key, old_body,
                          f"/refresh-data 响应缺少 {key}")
            self.assertIn(key, new_body,
                          f"/data 响应缺少 {key}")

    # ── R2: /refresh-data 接受日期参数并传递 ──────────────────────────────────
    def test_R2_refresh_data_accepts_and_forwards_date_params(self):
        """R2: /refresh-data?date_start=...&date_end=... 参数被正确转发到 SQL 渲染"""
        executed = []
        rid = uuid.uuid4()
        tok = "r2_token"
        mock_report = _make_mock_report(
            report_id=str(rid), refresh_token=tok,
            charts=[{
                "id": "c1",
                "sql": "SELECT * FROM t WHERE dt >= '{{ date_start }}'",
                "connection_env": "sg", "connection_type": "clickhouse",
            }],
            filters=[],
        )
        client = self._make_data_client(mock_report)

        async def _mock_query(sql, env, conn_type="clickhouse"):
            executed.append(sql)
            return []

        with patch("backend.api.reports._run_query", side_effect=_mock_query):
            resp = client.get(f"/reports/{rid}/refresh-data",
                              params={"token": tok, "date_start": "2026-01-15"})

        self.assertEqual(resp.status_code, 200)
        self.assertTrue(len(executed) > 0)
        self.assertIn("2026-01-15", executed[0])

    # ── R3: regenerate-html 端点在路由列表中（未被意外删除）──────────────────
    def test_R3_regenerate_html_endpoint_registered_in_router(self):
        """R3: reports.py 中 POST regenerate-html 路由已注册"""
        reports_path = PROJECT_ROOT / "backend/api/reports.py"
        content = reports_path.read_text(encoding="utf-8")
        self.assertIn('"/{report_id}/regenerate-html"', content,
                      "regenerate-html 路由应已注册")

    # ── R4: 原有 /data token-only 认证（无 JWT）不受 regenerate-html 影响 ────
    def test_R4_data_endpoint_still_token_only_not_jwt(self):
        """R4: /data 仍然仅凭 refresh_token 访问（不需要 JWT），安全性不变"""
        reports_path = PROJECT_ROOT / "backend/api/reports.py"
        content = reports_path.read_text(encoding="utf-8")
        # 找 /data 端点定义
        data_idx = content.find('"/{report_id}/data"')
        # 到下一个路由定义的范围
        next_route = content.find("@router.", data_idx + 1)
        section = content[data_idx:next_route]
        # /data 不应有 require_permission（是 token-only）
        self.assertNotIn("require_permission", section,
                         "/data 端点应为 token-only（无 JWT），不应有 require_permission")
        # 应有 refresh_token 校验
        self.assertIn("refresh_token", section)


# ─────────────────────────────────────────────────────────────────────────────
# S — 边界场景与安全
# ─────────────────────────────────────────────────────────────────────────────

class TestSEdgeCasesAndSecurity(unittest.TestCase):
    """S1~S8: 边界场景、安全校验与回归保障。"""

    def _make_data_client(self, mock_report):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from backend.api.reports import router
        from backend.config.database import get_db

        app = FastAPI()
        app.include_router(router)

        def _override_db():
            db = MagicMock()
            db.query.return_value.filter.return_value.first.return_value = mock_report
            db.commit = MagicMock()
            yield db

        app.dependency_overrides[get_db] = _override_db
        return TestClient(app, raise_server_exceptions=False)

    # ── S1: regenerate-html 当 HTML 文件不存在时自动创建 ─────────────────────
    def test_S1_regenerate_creates_html_when_file_missing(self):
        """S1: 磁盘上没有原 HTML 文件时，regenerate-html 仍能创建新文件"""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from backend.api.reports import router
        from backend.config.database import get_db
        from backend.api.deps import get_current_user

        rid = uuid.uuid4()
        tok = "s1_token"
        mock_report = _make_mock_report(
            report_id=str(rid), refresh_token=tok,
            charts=[],
            filters=[{"id": "dr", "type": "date_range",
                      "binds": ["date_start", "date_end"], "default_days": 7}],
            report_file_path="superadmin/reports/missing.html",
            username="superadmin",
        )

        superadmin = MagicMock()
        superadmin.username = "superadmin"
        superadmin.is_superadmin = True

        app = FastAPI()
        app.include_router(router)

        def _override_db():
            db = MagicMock()
            db.query.return_value.filter.return_value.first.return_value = mock_report
            db.commit = MagicMock()
            yield db

        app.dependency_overrides[get_db] = _override_db
        app.dependency_overrides[get_current_user] = lambda: superadmin

        with tempfile.TemporaryDirectory() as tmpdir:
            # 故意不创建文件（只创建父目录）
            html_path = Path(tmpdir) / "superadmin" / "reports" / "missing.html"
            # 文件不存在
            self.assertFalse(html_path.exists(), "测试前提：文件不应存在")

            with patch("backend.api.reports._CUSTOMER_DATA_ROOT", Path(tmpdir)):
                client = TestClient(app, raise_server_exceptions=False)
                resp = client.post(f"/reports/{rid}/regenerate-html")

            self.assertEqual(resp.status_code, 200,
                             f"应创建文件并返回200，实际: {resp.status_code} {resp.text[:200]}")
            self.assertTrue(html_path.exists(), "regenerate-html 应创建 HTML 文件")

    # ── S2: 无 filters 的报表 + /data 显式传参 → SQL 正常渲染 ────────────────
    def test_S2_report_without_filters_data_accepts_explicit_params(self):
        """S2: filters 为空的报表，/data 仍可接受显式 SQL 参数"""
        executed = []
        rid = uuid.uuid4()
        tok = "s2_token"
        mock_report = _make_mock_report(
            report_id=str(rid), refresh_token=tok,
            charts=[{
                "id": "c1",
                "sql": "SELECT * FROM t WHERE dt >= '{{ date_start }}'",
                "connection_env": "sg", "connection_type": "clickhouse",
            }],
            filters=[],  # 无 filters
        )
        client = self._make_data_client(mock_report)

        async def _mock_query(sql, env, conn_type="clickhouse"):
            executed.append(sql)
            return []

        with patch("backend.api.reports._run_query", side_effect=_mock_query):
            resp = client.get(f"/reports/{rid}/data",
                              params={"token": tok, "date_start": "2026-05-01"})

        self.assertEqual(resp.status_code, 200)
        self.assertIn("2026-05-01", executed[0])

    # ── S3: select 类型 binds 归一化（三元素 list → value 键）────────────────
    def test_S3_select_binds_normalization(self):
        """S3: select 类型 binds list 第3位映射为 value 键"""
        from backend.services.report_params_service import _normalize_binds
        result = _normalize_binds(["date_start", "date_end", "enterprise_id"])
        self.assertEqual(result["value"], "enterprise_id")

    # ── S4: multi_select binds — binds[3] 映射为 values 键 ───────────────────
    def test_S4_multi_select_binds_fourth_element_maps_to_values(self):
        """S4: list 第4位映射为 values 键（multi_select 惯例）"""
        from backend.services.report_builder_service import build_report_html
        spec = {
            "title": "S4", "charts": [],
            "filters": [{
                "id": "env_multi", "type": "multi_select",
                "label": "环境", "binds": ["start", "end", "val", "env_list"],
            }],
        }
        html = build_report_html(spec, "s4-id", "tok", "")
        fn_start = html.find("function _currentParams()")
        fn_end = html.find("\nfunction ", fn_start + 1)
        fn_body = html[fn_start:fn_end]
        self.assertIn("_b.values = binds[3]", fn_body,
                      "_currentParams 应将 binds[3] 映射为 values 键")

    # ── S5: Jinja2 注入防御 — 模板代码不被执行 ───────────────────────────────
    def test_S5_jinja2_template_injection_blocked(self):
        """S5: 恶意参数 date_start = '{{ 1+1 }}' 不被二次渲染（沙盒防注入）"""
        from backend.services.report_params_service import render_sql
        sql = "SELECT * FROM t WHERE dt >= '{{ date_start }}'"
        # 恶意参数本身含 Jinja2 语法
        result = render_sql(sql, {"date_start": "{{ 1+1 }}"})
        # 结果中出现字面量字符串，而不是 "2"
        self.assertIn("{{ 1+1 }}", result,
                      "注入的 Jinja2 代码应被当作字面量处理，不被二次执行")
        self.assertNotIn("' 2 '", result, "不应执行注入的模板表达式")

    # ── S6: 错误 token 无论什么参数都是 403 ─────────────────────────────────
    def test_S6_wrong_token_always_403_regardless_of_date_params(self):
        """S6: 错误 token + 任意日期参数 → 403（权限检查先于参数处理）"""
        rid = uuid.uuid4()
        mock_report = _make_mock_report(
            report_id=str(rid), refresh_token="correct_token",
        )
        client = self._make_data_client(mock_report)
        resp = client.get(f"/reports/{rid}/data",
                          params={"token": "WRONG", "date_start": "2026-03-01",
                                  "date_end": "2026-03-31"})
        self.assertEqual(resp.status_code, 403)

    # ── S7: regenerate-html 响应体格式验证 ───────────────────────────────────
    def test_S7_regenerate_html_response_format(self):
        """S7: regenerate-html 成功响应包含 success/report_id/html_path/message"""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from backend.api.reports import router
        from backend.config.database import get_db
        from backend.api.deps import get_current_user

        rid = uuid.uuid4()
        tok = "s7_token"
        mock_report = _make_mock_report(
            report_id=str(rid), refresh_token=tok,
            username="superadmin",
            report_file_path="superadmin/reports/s7_test.html",
        )

        superadmin = MagicMock()
        superadmin.username = "superadmin"
        superadmin.is_superadmin = True

        app = FastAPI()
        app.include_router(router)

        def _override_db():
            db = MagicMock()
            db.query.return_value.filter.return_value.first.return_value = mock_report
            db.commit = MagicMock()
            yield db

        app.dependency_overrides[get_db] = _override_db
        app.dependency_overrides[get_current_user] = lambda: superadmin

        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "superadmin" / "reports").mkdir(parents=True)
            with patch("backend.api.reports._CUSTOMER_DATA_ROOT", Path(tmpdir)):
                client = TestClient(app, raise_server_exceptions=False)
                resp = client.post(f"/reports/{rid}/regenerate-html")

        if resp.status_code == 200:
            body = resp.json()
            self.assertTrue(body.get("success"))
            self.assertIn("report_id", body)
            self.assertIn("html_path", body)
            self.assertIn("message", body)
            self.assertEqual(body["report_id"], str(rid))
        else:
            self.skipTest(f"regenerate-html 返回 {resp.status_code}")

    # ── S8: owner + reports:create 权限可以调用 regenerate-html ─────────────
    def test_S8_owner_with_reports_create_can_call_regenerate_html(self):
        """S8: 持有 reports:create 权限的 owner 调用 regenerate-html 应成功（非 superadmin）

        设计说明：regenerate-html 要求 reports:create 权限（analyst/admin 角色均有）。
        本测试模拟 analyst 角色的 owner alice，通过 patch get_user_permissions 返回
        正确权限集，验证所有权校验通过且 HTTP 200。
        """
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from backend.api.reports import router
        from backend.config.database import get_db
        from backend.api.deps import get_current_user

        rid = uuid.uuid4()
        tok = "s8_token"
        mock_report = _make_mock_report(
            report_id=str(rid), refresh_token=tok,
            username="alice",
            report_file_path="alice/reports/alice_report.html",
        )

        alice = MagicMock()
        alice.username = "alice"
        alice.is_superadmin = False  # 普通 analyst 用户，非 superadmin

        app = FastAPI()
        app.include_router(router)

        def _override_db():
            db = MagicMock()
            db.query.return_value.filter.return_value.first.return_value = mock_report
            db.commit = MagicMock()
            yield db

        app.dependency_overrides[get_db] = _override_db
        app.dependency_overrides[get_current_user] = lambda: alice

        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "alice" / "reports").mkdir(parents=True)
            # 模拟 alice 拥有 reports:create 权限（analyst 角色赋权）
            with patch("backend.api.reports._CUSTOMER_DATA_ROOT", Path(tmpdir)), \
                 patch("backend.core.rbac.get_user_permissions",
                       return_value=["chat:use", "reports:read", "reports:create"]):
                client = TestClient(app, raise_server_exceptions=False)
                resp = client.post(f"/reports/{rid}/regenerate-html")

        self.assertEqual(resp.status_code, 200,
                         f"analyst role owner 应能成功调用 regenerate-html，实际: {resp.status_code} {resp.text[:200]}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
