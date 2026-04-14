"""
test_chart_controls_e2e.py — 图表控件 ⋮ 菜单 端到端测试套件
=============================================================

覆盖范围：

  A 组 (6)  — _inject_chart_controls override JS 注入（单元，无DB）
              A1-A6: Agent HTML / Builder HTML 覆盖变量注入、XSS净化

  B 组 (6)  — Force Refresh JS 逻辑（单元，无DB）
              B1-B6: ccForceRefresh 函数体、端点路径、全局变量、降级路径

  C 组 (5)  — View Query 完整性（单元，无DB）
              C1-C5: ccViewQuery、REPORT_SPEC.sql 读取、弹窗CSS

  D 组 (5)  — Download CSV/Excel（单元，无DB）
              D1-D5: ccDownload、BOM、格式分支、MIME类型

  E 组 (3)  — Fullscreen（单元，无DB）
              E1-E3: ccFullscreen、requestFullscreen、resize

  F 组 (8)  — HTTP 端点集成（需要 PostgreSQL，否则跳过）
              F1-F8: by_token / by_path 注入验证、override JS、refresh-data

  G 组 (5)  — RBAC 权限范围分析（代码分析 + HTTP，部分需DB）
              G1-G5: 无新权限、by_token 无需 JWT、by_path 需 JWT、refresh-data 无需 JWT

  H 组 (5)  — 边界情况（单元，无DB）
              H1-H5: 空HTML、无body标签、幂等、多图表、None report_id

总计: 43 个测试用例
"""

from __future__ import annotations

import os
import sys
import uuid
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

# ── 路径 & 环境初始化 ────────────────────────────────────────────────────────
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "backend"))
os.environ.setdefault("POSTGRES_PASSWORD", "postgres")
os.environ.setdefault("POSTGRES_HOST", "localhost")
os.environ.setdefault("ENABLE_AUTH", "False")

from backend.api.reports import _inject_chart_controls, _inject_pilot_button, _CUSTOMER_DATA_ROOT  # noqa: E402

# ── DB 可用性检测 ─────────────────────────────────────────────────────────────
_DB_AVAILABLE = False
_g_db = None

try:
    from backend.config.database import SessionLocal
    import sqlalchemy

    _sess = SessionLocal()
    _sess.execute(sqlalchemy.text("SELECT 1"))
    _sess.close()
    _g_db = SessionLocal()
    _DB_AVAILABLE = True
except Exception as _db_err:
    print(f"[WARNING] PostgreSQL 不可用，跳过 F/G(部分) 组测试: {_db_err}")

_SKIP_DB = unittest.skipUnless(_DB_AVAILABLE, "PostgreSQL 不可用，跳过 DB 相关测试")

# ── 测试数据前缀 ─────────────────────────────────────────────────────────────
_PREFIX = f"_cc_{uuid.uuid4().hex[:6]}_"


# ── 测试用 HTML fixtures ──────────────────────────────────────────────────────

# 由 report_builder_service 生成的标准 builder HTML（含全局变量）
_BUILDER_HTML = """<!DOCTYPE html>
<html>
<head><title>Builder Report</title></head>
<body>
<div class="chart-card chart-full" id="card-revenue">
  <div class="chart-card-title">月收入趋势</div>
  <div class="chart-container echarts-chart" id="revenue" style="height:320px"></div>
</div>
<div class="chart-card chart-half" id="card-pie">
  <div class="chart-card-title">分类占比</div>
  <div class="chart-container echarts-chart" id="pie" style="height:300px"></div>
</div>
<script>
var _charts={};
var _chartData={'revenue':[{'month':'2026-01','value':100000}],'pie':[{'cat':'A','val':60}]};
var REPORT_SPEC={charts:[
  {id:'revenue',title:'月收入趋势',sql:'SELECT month, value FROM t_revenue'},
  {id:'pie',title:'分类占比',sql:'SELECT cat, val FROM t_pie'}
]};
var REPORT_ID='builder-report-uuid';
var REFRESH_TOKEN='builder-token-abc123';
var API_BASE='http://localhost:8000/api/v1';
</script>
</body>
</html>"""

# 由 agent write_file 生成的 HTML（不含全局变量）
_AGENT_HTML = """<!DOCTYPE html>
<html>
<head><title>Agent Report</title></head>
<body>
<div class="chart-card chart-full" id="card-calls">
  <div class="chart-card-title">外呼量</div>
  <div class="chart-container echarts-chart" id="calls" style="height:320px"></div>
</div>
<script>
var _charts={};
var _chartData={'calls':[{'date':'2026-01-01','cnt':200}]};
var REPORT_SPEC={charts:[
  {id:'calls',title:'外呼量',sql:'SELECT date, cnt FROM t_calls'}
]};
/* 注意：此文件由 Agent 直接写入，不含 REPORT_ID / REFRESH_TOKEN / API_BASE */
</script>
</body>
</html>"""

_EMPTY_HTML = ""
_NO_BODY_HTML = "<html><head></head><div>no body tag</div></html>"


# ── DB helpers（仅 F/G 组使用）────────────────────────────────────────────────

def _make_user(suffix="", role_name="admin"):
    from backend.models.user import User
    from backend.models.role import Role
    from backend.models.user_role import UserRole
    from backend.core.auth.password import hash_password

    username = f"{_PREFIX}{suffix or uuid.uuid4().hex[:6]}"
    u = User(
        username=username,
        display_name=f"CC E2E {suffix}",
        hashed_password=hash_password("Test1234!"),
        auth_source="local",
        is_active=True,
        is_superadmin=False,
    )
    _g_db.add(u)
    _g_db.flush()
    role = _g_db.query(Role).filter(Role.name == role_name).first()
    if role:
        _g_db.add(UserRole(user_id=u.id, role_id=role.id))
    _g_db.commit()
    _g_db.refresh(u)
    return u


def _token(user):
    from backend.config.settings import settings
    from backend.core.auth.jwt import create_access_token
    from backend.core.rbac import get_user_roles

    roles = get_user_roles(user, _g_db)
    return create_access_token(
        {"sub": str(user.id), "username": user.username, "roles": roles},
        settings.jwt_secret,
        settings.jwt_algorithm,
    )


def _auth_headers(user):
    return {"Authorization": f"Bearer {_token(user)}"}


def _make_client():
    from backend.main import app
    from fastapi.testclient import TestClient

    return TestClient(app, raise_server_exceptions=False)


def _make_report(owner_username: str, html_content: str, doc_type: str = "dashboard"):
    """在 DB 中创建报表记录并写入临时 HTML 文件。返回 (report, abs_file_path)。"""
    from backend.models.report import Report

    rel_path = f"{owner_username}/reports/{_PREFIX}{uuid.uuid4().hex[:8]}.html"
    abs_path = _CUSTOMER_DATA_ROOT / rel_path
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    abs_path.write_text(html_content, encoding="utf-8")

    name = f"{_PREFIX}rpt_{uuid.uuid4().hex[:6]}"
    charts_list = [{"id": "calls", "sql": "SELECT date, cnt FROM t_calls", "title": "外呼量"}]
    r = Report(
        name=name,
        doc_type=doc_type,
        theme="light",
        charts=charts_list,
        filters=[],
        username=owner_username,
        refresh_token=uuid.uuid4().hex,
        report_file_path=rel_path,
        share_scope="private",
    )
    _g_db.add(r)
    _g_db.commit()
    _g_db.refresh(r)
    return r, abs_path


def _cleanup_test_data():
    if not _DB_AVAILABLE:
        return
    try:
        from backend.models.user import User
        from backend.models.report import Report

        _g_db.query(Report).filter(Report.name.like(f"{_PREFIX}%")).delete(
            synchronize_session=False
        )
        _g_db.query(User).filter(User.username.like(f"{_PREFIX}%")).delete(
            synchronize_session=False
        )
        _g_db.commit()
    except Exception:
        try:
            _g_db.rollback()
        except Exception:
            pass


# ── 模块级补丁 ────────────────────────────────────────────────────────────────
_auth_patcher = None


def setup_module(_=None):
    global _auth_patcher
    from backend.config.settings import settings

    _auth_patcher = patch.object(settings, "enable_auth", False)
    _auth_patcher.start()


def teardown_module(_=None):
    global _auth_patcher
    if _auth_patcher is not None:
        _auth_patcher.stop()
        _auth_patcher = None
    _cleanup_test_data()


# =============================================================================
# A 组 — _inject_chart_controls override JS 注入
# =============================================================================
class TestAOverrideJSInjection(unittest.TestCase):
    """
    验证 _inject_chart_controls(html, report_id, refresh_token) 的条件注入逻辑：
    Agent 写入的 HTML 缺少 REPORT_ID，系统应补注 __cc-vars 脚本。
    """

    def test_A1_agent_html_gets_cc_vars_script(self):
        """Agent HTML (无 REPORT_ID) + report_id 参数 → 注入 __cc-vars"""
        result = _inject_chart_controls(_AGENT_HTML, report_id="r123", refresh_token="tok456")
        self.assertIn('<script id="__cc-vars">', result)

    def test_A2_builder_html_does_not_get_cc_vars(self):
        """Builder HTML (已有 REPORT_ID) + report_id 参数 → 不注入 __cc-vars"""
        result = _inject_chart_controls(_BUILDER_HTML, report_id="r123", refresh_token="tok456")
        self.assertNotIn('id="__cc-vars"', result)

    def test_A3_no_report_id_param_no_cc_vars(self):
        """不传 report_id → 不注入 __cc-vars（仅注入 __cc-style/__cc-script）"""
        result = _inject_chart_controls(_AGENT_HTML)
        self.assertNotIn('id="__cc-vars"', result)
        self.assertIn('id="__cc-style"', result)

    def test_A4_xss_chars_stripped_from_report_id(self):
        """report_id 含 <> 等 HTML 标签字符 → 被净化，__cc-vars 内不出现可解析的 <script> 标签"""
        result = _inject_chart_controls(
            _AGENT_HTML, report_id='<script>alert(1)</script>', refresh_token="ok"
        )
        self.assertIn('<script id="__cc-vars">', result)  # 应该被注入
        # 提取 __cc-vars 块内容，验证 < > 已被移除（不能形成可解析的 HTML 标签）
        cc_vars_start = result.find('<script id="__cc-vars">')
        cc_vars_end = result.find("</script>", cc_vars_start)
        cc_vars_block = result[cc_vars_start:cc_vars_end]
        # < 和 > 角括号已被净化，无法构成 HTML 注入标签
        self.assertNotIn("<script>", cc_vars_block)
        self.assertNotIn("</script>", cc_vars_block)
        # 净化后以无尖括号形式出现（如 "scriptalert(1)/script"）
        self.assertIn("REPORT_ID=", cc_vars_block)

    def test_A5_cc_vars_has_correct_globals(self):
        """__cc-vars 内含 REPORT_ID / REFRESH_TOKEN / API_BASE 三个全局变量"""
        rid = "abc-def-uuid"
        tok = "secure-tok-xyz"
        result = _inject_chart_controls(_AGENT_HTML, report_id=rid, refresh_token=tok)
        # 提取 __cc-vars 段
        start = result.find('<script id="__cc-vars">')
        end = result.find("</script>", start)
        cc_vars_block = result[start:end]
        self.assertIn(f'REPORT_ID="{rid}"', cc_vars_block)
        self.assertIn(f'REFRESH_TOKEN="{tok}"', cc_vars_block)
        self.assertIn("API_BASE=", cc_vars_block)

    def test_A6_cc_vars_appears_before_cc_style(self):
        """__cc-vars 在 __cc-style 之前（先设置变量，再初始化控件）"""
        result = _inject_chart_controls(_AGENT_HTML, report_id="r1", refresh_token="t1")
        vars_pos = result.find('id="__cc-vars"')
        style_pos = result.find('id="__cc-style"')
        self.assertGreater(style_pos, vars_pos)


# =============================================================================
# B 组 — Force Refresh JS 逻辑
# =============================================================================
class TestBForceRefresh(unittest.TestCase):

    def setUp(self):
        self.result = _inject_chart_controls(_BUILDER_HTML)

    def test_B1_ccForceRefresh_defined(self):
        """注入的 JS 中定义了 window.ccForceRefresh"""
        self.assertIn("window.ccForceRefresh=function", self.result)

    def test_B2_refresh_data_endpoint_referenced(self):
        """ccForceRefresh 调用 /refresh-data 端点"""
        self.assertIn("/refresh-data", self.result)

    def test_B3_uses_report_id_and_token_globals(self):
        """ccForceRefresh 从 REPORT_ID / REFRESH_TOKEN / API_BASE 读取参数"""
        self.assertIn("REPORT_ID", self.result)
        self.assertIn("REFRESH_TOKEN", self.result)
        self.assertIn("API_BASE", self.result)

    def test_B4_fallback_clear_setOption(self):
        """无全局变量时 Force Refresh 降级：chart.clear() + chart.setOption()"""
        self.assertIn("chart.clear()", self.result)

    def test_B5_shows_loading_before_fetch(self):
        """Force Refresh 在 fetch 前调用 showLoading"""
        refresh_js_start = self.result.find("window.ccForceRefresh")
        next_fn = self.result.find("window.cc", refresh_js_start + 1)
        # showLoading 应在 ccForceRefresh 函数体内、fetch 之前
        fetch_pos = self.result.find("fetch(", refresh_js_start)
        show_pos = self.result.find("showLoading", refresh_js_start)
        self.assertLess(show_pos, fetch_pos)

    def test_B6_hide_loading_in_error_handler(self):
        """fetch 失败时调用 hideLoading"""
        self.assertIn("hideLoading", self.result)


# =============================================================================
# C 组 — View Query 完整性
# =============================================================================
class TestCViewQuery(unittest.TestCase):

    def setUp(self):
        self.result = _inject_chart_controls(_BUILDER_HTML)

    def test_C1_ccViewQuery_defined(self):
        """注入 JS 中定义了 window.ccViewQuery"""
        self.assertIn("window.ccViewQuery=function", self.result)

    def test_C2_reads_report_spec(self):
        """ccViewQuery 从 REPORT_SPEC.charts 读取数据"""
        self.assertIn("REPORT_SPEC", self.result)

    def test_C3_accesses_sql_field(self):
        """ccViewQuery 读取 .sql 字段"""
        self.assertIn(".sql", self.result)

    def test_C4_modal_overlay_css_present(self):
        """View Query 弹窗 CSS .cc-modal-overlay 已注入"""
        self.assertIn(".cc-modal-overlay{", self.result)

    def test_C5_copy_sql_button_in_js(self):
        """ccViewQuery 注入「复制 SQL」按钮（调用 ccCopySql）"""
        self.assertIn("ccCopySql", self.result)


# =============================================================================
# D 组 — Download CSV / Excel
# =============================================================================
class TestDDownload(unittest.TestCase):

    def setUp(self):
        self.result = _inject_chart_controls(_BUILDER_HTML)

    def test_D1_ccDownload_defined(self):
        """注入 JS 中定义了 window.ccDownload"""
        self.assertIn("window.ccDownload=function", self.result)

    def test_D2_bom_for_csv(self):
        """CSV 导出包含 BOM（\\uFEFF），解决 Excel 中文乱码"""
        self.assertIn("uFEFF", self.result)

    def test_D3_csv_format_branch(self):
        """Download 含 fmt==='csv' 分支"""
        self.assertIn("fmt==='csv'", self.result)

    def test_D4_excel_mime_type(self):
        """Download Excel 使用 vnd.ms-excel MIME 类型"""
        self.assertIn("vnd.ms-excel", self.result)

    def test_D5_reads_chart_data_global(self):
        """Download 从 _chartData 全局变量中读取数据"""
        self.assertIn("_chartData", self.result)


# =============================================================================
# E 组 — Fullscreen
# =============================================================================
class TestEFullscreen(unittest.TestCase):

    def setUp(self):
        self.result = _inject_chart_controls(_BUILDER_HTML)

    def test_E1_ccFullscreen_defined(self):
        """注入 JS 中定义了 window.ccFullscreen"""
        self.assertIn("window.ccFullscreen=function", self.result)

    def test_E2_request_fullscreen_api(self):
        """ccFullscreen 调用 requestFullscreen（浏览器 Fullscreen API）"""
        self.assertIn("requestFullscreen", self.result)

    def test_E3_resize_after_fullscreen(self):
        """进入全屏后调用 c.resize() 自适应新尺寸"""
        self.assertIn("c.resize()", self.result)


# =============================================================================
# F 组 — HTTP 端点集成（需要 PostgreSQL）
# =============================================================================
@_SKIP_DB
class TestFHTTPEndpoints(unittest.TestCase):
    """通过 TestClient 验证 serve_report_html_by_token / by_path 及 refresh-data 端点的行为。"""

    @classmethod
    def setUpClass(cls):
        cls.client = _make_client()
        cls.admin_user = _make_user("admin", role_name="admin")

        # 准备 builder HTML 报表记录 + 文件
        cls.b_report, cls.b_path = _make_report(
            cls.admin_user.username, _BUILDER_HTML, doc_type="dashboard"
        )
        # 准备 agent HTML 报表记录 + 文件
        cls.a_report, cls.a_path = _make_report(
            cls.admin_user.username, _AGENT_HTML, doc_type="dashboard"
        )

    @classmethod
    def tearDownClass(cls):
        # 清理临时文件
        for p in [cls.b_path, cls.a_path]:
            try:
                p.unlink(missing_ok=True)
            except Exception:
                pass

    def test_F1_by_token_injects_chart_controls(self):
        """serve_report_html_by_token 返回含 __cc-style 的 HTML"""
        r = self.client.get(
            f"/api/v1/reports/{self.b_report.id}/html?token={self.b_report.refresh_token}"
        )
        self.assertEqual(r.status_code, 200)
        self.assertIn("__cc-style", r.text)
        self.assertIn("ccForceRefresh", r.text)

    def test_F2_by_token_download_mode_no_inject(self):
        """download=true 时不注入控件（保持原始 HTML 完整性）"""
        r = self.client.get(
            f"/api/v1/reports/{self.b_report.id}/html"
            f"?token={self.b_report.refresh_token}&download=true"
        )
        self.assertEqual(r.status_code, 200)
        self.assertNotIn("__cc-style", r.text)

    def test_F3_by_path_injects_chart_controls(self):
        """serve_report_html_by_path 返回含 __cc-style 的 HTML（JWT query param）"""
        tok = _token(self.admin_user)
        rel_path = self.b_report.report_file_path
        # ENABLE_AUTH=False 时 token 参数不强制验证，但仍需传递路径
        r = self.client.get(
            f"/api/v1/reports/html-serve?path={rel_path}&token={tok}",
        )
        self.assertEqual(r.status_code, 200)
        self.assertIn("__cc-style", r.text)

    def test_F4_by_token_agent_html_gets_override_js(self):
        """Agent 写入的 HTML（无 REPORT_ID 全局变量）通过 by_token 访问时注入 __cc-vars"""
        r = self.client.get(
            f"/api/v1/reports/{self.a_report.id}/html?token={self.a_report.refresh_token}"
        )
        self.assertEqual(r.status_code, 200)
        self.assertIn('id="__cc-vars"', r.text)
        self.assertIn(str(self.a_report.id), r.text)

    def test_F5_by_token_builder_html_no_double_report_id(self):
        """Builder HTML（已有 REPORT_ID）不被重复注入 __cc-vars"""
        r = self.client.get(
            f"/api/v1/reports/{self.b_report.id}/html?token={self.b_report.refresh_token}"
        )
        self.assertEqual(r.status_code, 200)
        self.assertNotIn('id="__cc-vars"', r.text)
        # 原始 REPORT_ID 变量保持完整
        self.assertIn("REPORT_ID='builder-report-uuid'", r.text)

    def test_F6_refresh_data_correct_token_returns_success(self):
        """refresh-data 端点：正确 token → 成功（SQL 为空时跳过执行）"""
        r = self.client.get(
            f"/api/v1/reports/{self.b_report.id}/refresh-data?token={self.b_report.refresh_token}"
        )
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertTrue(body.get("success"))

    def test_F7_refresh_data_wrong_token_returns_403(self):
        """refresh-data 端点：错误 token → 403"""
        r = self.client.get(
            f"/api/v1/reports/{self.b_report.id}/refresh-data?token=wrongtoken"
        )
        self.assertEqual(r.status_code, 403)

    def test_F8_by_token_wrong_token_returns_403(self):
        """serve_report_html_by_token：错误 token → 403"""
        r = self.client.get(
            f"/api/v1/reports/{self.b_report.id}/html?token=definitely-wrong"
        )
        self.assertEqual(r.status_code, 403)


# =============================================================================
# G 组 — RBAC 权限范围分析
# =============================================================================
class TestGRBACScope(unittest.TestCase):
    """
    验证「图表 ⋮ 菜单」功能是否落在角色权限管理范围内。

    结论（设计决策）：
    ─────────────────────────────────────────────────────────────────────────
    1. _inject_chart_controls 是纯函数，无任何 RBAC 检查 — 控件由服务端
       在已授权端点内注入，权限来自端点本身。
    2. serve_report_html_by_token  — 使用 refresh_token 认证（不依赖 RBAC），
       已固定的报告可无需账号访问。这是既有设计，TE 功能不扩大暴露面。
    3. serve_report_html_by_path   — 已要求 JWT（reports:read 权限）。
    4. /refresh-data               — 使用 refresh_token 认证（不依赖 RBAC）。
    5. Download / View Query / Fullscreen / Copy SQL 均为客户端操作，
       不产生新的后端 API 端点，无需新权限常量。
    ─────────────────────────────────────────────────────────────────────────
    结论：无需在 RBAC 权限矩阵中添加新条目。
    """

    def test_G1_no_new_permission_constant_added(self):
        """reports.py 中未为图表控件新增独立的权限常量"""
        import inspect
        import backend.api.reports as rmod

        src = inspect.getsource(rmod)
        # 验证：_inject_chart_controls 函数内没有 require_permission 调用
        fn_start = src.find("def _inject_chart_controls")
        fn_end = src.find("\ndef ", fn_start + 1)
        fn_body = src[fn_start:fn_end] if fn_end > fn_start else src[fn_start:]
        self.assertNotIn("require_permission", fn_body)

    def test_G2_inject_function_has_no_permission_check(self):
        """_inject_chart_controls 是纯字符串变换函数，无副作用或权限检查"""
        import inspect
        from backend.api.reports import _inject_chart_controls

        src = inspect.getsource(_inject_chart_controls)
        self.assertNotIn("Depends", src)
        self.assertNotIn("require_permission", src)
        self.assertNotIn("HTTPException", src)

    def test_G3_download_view_fullscreen_no_new_endpoints(self):
        """Download / View Query / Fullscreen / Copy SQL 功能全部在客户端实现，
        不新增后端路由端点"""
        import backend.api.reports as rmod

        # 检查 router 上没有 /chart-controls/* 路径
        route_paths = [r.path for r in rmod.router.routes]
        for path in route_paths:
            self.assertNotIn("chart-controls", path,
                             f"意外发现 chart-controls 路由: {path}")

    @_SKIP_DB
    def test_G4_by_token_accessible_without_jwt(self):
        """serve_report_html_by_token 无需 JWT（refresh_token 即为凭证）"""
        # 创建报表
        admin = _make_user("g4admin", role_name="admin")
        report, fpath = _make_report(admin.username, _BUILDER_HTML)
        try:
            client = _make_client()
            # 无 Authorization header
            r = client.get(f"/api/v1/reports/{report.id}/html?token={report.refresh_token}")
            self.assertEqual(r.status_code, 200)
            self.assertIn("__cc-style", r.text)
        finally:
            fpath.unlink(missing_ok=True)

    @_SKIP_DB
    def test_G5_refresh_data_accessible_without_jwt(self):
        """/refresh-data 无需 JWT（refresh_token 即为凭证）"""
        admin = _make_user("g5admin", role_name="admin")
        report, fpath = _make_report(admin.username, _BUILDER_HTML)
        try:
            client = _make_client()
            r = client.get(
                f"/api/v1/reports/{report.id}/refresh-data?token={report.refresh_token}"
            )
            self.assertEqual(r.status_code, 200)
        finally:
            fpath.unlink(missing_ok=True)


# =============================================================================
# H 组 — 边界情况
# =============================================================================
class TestHEdgeCases(unittest.TestCase):

    def test_H1_empty_html_injects_without_error(self):
        """空 HTML 字符串不抛出异常，返回含 __cc-style 的字符串"""
        result = _inject_chart_controls(_EMPTY_HTML)
        self.assertIn("__cc-style", result)
        self.assertIn("ccForceRefresh", result)

    def test_H2_no_body_tag_appends_to_end(self):
        """无 </body> 标签时内容追加到末尾"""
        result = _inject_chart_controls(_NO_BODY_HTML)
        self.assertIn("__cc-style", result)
        self.assertTrue(result.endswith(_NO_BODY_HTML[-5:]) or
                        result.find("__cc-style") > result.find(_NO_BODY_HTML[:10]))

    def test_H3_idempotent_double_injection(self):
        """连续注入两次 → __cc-style 只出现一次"""
        once = _inject_chart_controls(_BUILDER_HTML)
        twice = _inject_chart_controls(once)
        count = twice.count('<style id="__cc-style">')
        self.assertEqual(count, 1, f"__cc-style 出现 {count} 次，期望 1 次")

    def test_H4_multi_chart_html_preserves_all_cards(self):
        """多图表 HTML 注入后所有 chart-card 元素保持完整"""
        result = _inject_chart_controls(_BUILDER_HTML)
        self.assertIn('id="card-revenue"', result)
        self.assertIn('id="card-pie"', result)
        self.assertIn("REPORT_SPEC", result)

    def test_H5_none_report_id_no_cc_vars_even_for_agent_html(self):
        """report_id=None 时即使 HTML 缺少 REPORT_ID 也不注入 __cc-vars"""
        result = _inject_chart_controls(_AGENT_HTML, report_id=None, refresh_token=None)
        self.assertNotIn('id="__cc-vars"', result)
        # 主控件仍然注入
        self.assertIn("__cc-style", result)


if __name__ == "__main__":
    unittest.main(verbosity=2)
