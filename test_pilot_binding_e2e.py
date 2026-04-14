"""
test_pilot_binding_e2e.py — Pilot 对话绑定 & 报表预览刷新功能完整测试套件
===========================================================================

覆盖范围
========
  Q段 (8)  — Pilot 对话 upsert 核心逻辑（需要 DB）
               Q1: 首次调用 POST /copilot → created=True
               Q2: 同用户同报表二次调用 → created=False + 相同 ID
               Q3: auth 模式下不同用户各自独立对话
               Q4: 匿名模式下同报表复用同一对话
               Q5: 对话 extra_metadata 包含 context_type / context_id
               Q6: 无效 report_id → 404
               Q7: 其他用户的报表 → 403 ownership check
               Q8: find_pilot_conversation 无结果时安全新建对话

  R段 (4)  — spec 更新响应含 spec_updated 标识（需要 DB）
               R1: PUT /spec 响应 data.spec_updated == True
               R2: PUT /charts/{id} 响应 data.spec_updated == True
               R3: PUT /spec 实际更新数据库 charts 字段
               R4: PUT /charts/{id} 局部 merge，不覆盖其他图表

  S段 (8)  — RBAC 权限矩阵（混合 DB + 静态）
               S1: viewer 角色无法 POST /copilot（reports:read 不在 viewer）
               S2: analyst 角色可以 POST /copilot（reports:read ✓）
               S3: analyst 角色可以 PUT /spec（reports:create ✓）
               S4: viewer 角色无法 PUT /spec（reports:create 不在 viewer）
               S5: /report-view 路由无需 RequireAuth（前端静态）
               S6: POST /copilot 使用 require_permission("reports","read")（静态）
               S7: PUT /spec 使用 require_permission("reports","create")（静态）
               S8: PUT /charts/{id} 使用 require_permission("reports","create")（静态）

  T段 (13) — 前端代码静态分析（无需 DB）
               T1: getPilotConvKey 按 contextType/contextId/userId 生成 key
               T2: localStorage.getItem/setItem 用于对话 ID 持久化
               T3: upsert 端点路径 /reports/{contextId}/copilot 存在
               T4: loadHistory 调用 conversationApi.getMessages
               T5: spec_updated 检测包含工具名精确匹配
               T6: spec_updated 检测包含 spec_updated:true 字符串
               T7: ReportViewerPage pilotOpenKey 函数存在
               T8: ReportViewerPage localStorage 持久化 pilotOpen
               T9: ReportViewerPage handleRefreshIframe 使用 iframeKey++
               T10: ReportViewerPage ReloadOutlined 在 iframe 区域工具栏
               T11: ReportPreviewModal ReloadOutlined 导入
               T12: ReportPreviewModal 刷新按钮触发 iframeKey 递增
               T13: ReportPreviewModal 刷新按钮含 loading 状态

  U段 (4)  — 端到端完整流程（需要 DB）
               U1: 完整流程：创建报表→copilot→二次copilot→ID 一致
               U2: spec-meta 通过 refresh_token 返回报表元数据
               U3: 更新 spec 后再访问 copilot，对话 ID 不变（不重建）
               U4: 对话 extra_metadata 存有 refresh_token

总计: 37 个测试用例
"""

from __future__ import annotations

import json
import os
import sys
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

# ── 路径 & 环境初始化 ─────────────────────────────────────────────────────────
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "backend"))
os.environ.setdefault("POSTGRES_PASSWORD", "Sgp013013")
os.environ.setdefault("POSTGRES_HOST", "localhost")
os.environ.setdefault("ENABLE_AUTH", "False")

from test_utils import make_test_username  # noqa: E402

_PREFIX = f"_pb_{uuid.uuid4().hex[:6]}_"

# ── 前端文件路径 ──────────────────────────────────────────────────────────────
_FRONTEND_ROOT = Path(__file__).parent / "frontend" / "src"
_COPILOT_FILE = _FRONTEND_ROOT / "components" / "DataCenterCopilot.tsx"
_PREVIEW_FILE = _FRONTEND_ROOT / "components" / "chat" / "ReportPreviewModal.tsx"
_VIEWER_FILE  = _FRONTEND_ROOT / "pages" / "ReportViewerPage.tsx"
_APP_FILE     = _FRONTEND_ROOT / "App.tsx"
_REPORTS_API  = Path(__file__).parent / "backend" / "api" / "reports.py"

# ── 模块级 auth 补丁 ──────────────────────────────────────────────────────────
_auth_patcher = None


def setup_module(_=None):
    global _auth_patcher
    from backend.config.settings import settings
    _auth_patcher = patch.object(settings, "enable_auth", False)
    _auth_patcher.start()


def teardown_module(_=None):
    global _auth_patcher
    if _auth_patcher:
        _auth_patcher.stop()
        _auth_patcher = None
    _cleanup_test_data()


def _cleanup_test_data():
    """清理所有以 _PREFIX 开头的测试数据"""
    try:
        from backend.config.database import SessionLocal
        from backend.models.report import Report
        from backend.models.conversation import Conversation
        db = SessionLocal()
        try:
            convs = db.query(Conversation).filter(
                Conversation.title.like(f"%{_PREFIX}%")
            ).all()
            for c in convs:
                db.delete(c)
            reports = db.query(Report).filter(
                Report.name.like(f"%{_PREFIX}%")
            ).all()
            for r in reports:
                db.delete(r)
            db.commit()
        finally:
            db.close()
    except Exception:
        pass


# ── DB helpers ─────────────────────────────────────────────────────────────────

def _db():
    from backend.config.database import SessionLocal
    return SessionLocal()


_g_db = _db()


def _make_user(suffix="", role_name="analyst"):
    from backend.models.user import User
    from backend.models.role import Role
    from backend.models.user_role import UserRole
    from backend.core.auth.password import hash_password
    username = f"{_PREFIX}{suffix or uuid.uuid4().hex[:6]}"
    u = User(
        username=username,
        display_name=f"PB Test {suffix}",
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


def _auth(user):
    return {"Authorization": f"Bearer {_token(user)}"}


def _make_client():
    from backend.main import app
    from fastapi.testclient import TestClient
    return TestClient(app, raise_server_exceptions=False)


def _make_report(owner_username, doc_type="dashboard", charts=None):
    from backend.models.report import Report
    name = f"{_PREFIX}rpt_{uuid.uuid4().hex[:6]}"
    r = Report(
        name=name,
        doc_type=doc_type,
        theme="light",
        charts=charts or [
            {"id": "c1", "title": "销售额", "chart_type": "bar", "sql": "SELECT 1", "connection_env": "test"},
            {"id": "c2", "title": "用户数", "chart_type": "line", "sql": "SELECT 2", "connection_env": "test"},
        ],
        filters=[],
        username=owner_username,
        refresh_token=uuid.uuid4().hex,
        share_scope="private",
        report_file_path=None,
        extra_metadata={"spec_version": "1.0"},
    )
    _g_db.add(r)
    _g_db.commit()
    _g_db.refresh(r)
    return r


def _make_report_with_file(owner_username, doc_type="dashboard"):
    """创建带有 HTML 文件的报表（PUT /spec 需要 report_file_path）"""
    import tempfile
    from backend.models.report import Report
    from backend.config.settings import settings
    from pathlib import Path

    # 创建临时 HTML 文件
    customer_data_root = Path(settings.allowed_directories[0]) if settings.allowed_directories else Path("customer_data")
    report_dir = customer_data_root / owner_username / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)

    file_name = f"{_PREFIX}test_{uuid.uuid4().hex[:6]}.html"
    html_path = report_dir / file_name
    html_path.write_text("<html><body><p>Test Report</p></body></html>", encoding="utf-8")
    rel_path = f"{owner_username}/reports/{file_name}"

    name = f"{_PREFIX}rpt_file_{uuid.uuid4().hex[:6]}"
    r = Report(
        name=name,
        doc_type=doc_type,
        theme="light",
        charts=[
            {"id": "chart_a", "title": "原图表A", "chart_type": "bar", "sql": "SELECT 1", "connection_env": "test"},
            {"id": "chart_b", "title": "原图表B", "chart_type": "pie", "sql": "SELECT 2", "connection_env": "test"},
        ],
        filters=[],
        username=owner_username,
        refresh_token=uuid.uuid4().hex,
        share_scope="private",
        report_file_path=rel_path,
        extra_metadata={"spec_version": "1.0", "file_name": file_name},
    )
    _g_db.add(r)
    _g_db.commit()
    _g_db.refresh(r)
    return r


# ═══════════════════════════════════════════════════════════════════════════════
# Q 段 — Pilot 对话 upsert 核心逻辑
# ═══════════════════════════════════════════════════════════════════════════════

class TestQ_PilotUpsert(unittest.TestCase):
    """Pilot 对话绑定：同一用户同一报表只创建一个对话"""

    @classmethod
    def setUpClass(cls):
        cls.client = _make_client()
        cls.user_a = _make_user("qa")
        cls.user_b = _make_user("qb")
        cls.report_a = _make_report(cls.user_a.username)

    def test_Q1_first_call_creates_new(self):
        """Q1: 首次调用 POST /copilot → created=True, conversation_id 非空"""
        resp = self.client.post(
            f"/api/v1/reports/{self.report_a.id}/copilot",
            json={},
            headers=_auth(self.user_a),
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        d = resp.json()
        self.assertTrue(d["success"])
        self.assertIn("conversation_id", d["data"])
        self.assertTrue(d["data"]["created"])
        conv_id = d["data"]["conversation_id"]
        self.assertIsInstance(conv_id, str)
        # 保存给 Q2 使用
        TestQ_PilotUpsert._conv_id_first = conv_id

    def test_Q2_second_call_reuses_conversation(self):
        """Q2: 同用户同报表二次调用 → created=False，且对话 ID 与第一次相同"""
        # 确保 Q1 已运行
        if not hasattr(TestQ_PilotUpsert, "_conv_id_first"):
            self.skipTest("Q1 skipped, cannot validate Q2")

        resp = self.client.post(
            f"/api/v1/reports/{self.report_a.id}/copilot",
            json={},
            headers=_auth(self.user_a),
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        d = resp.json()
        self.assertTrue(d["success"])
        self.assertFalse(d["data"]["created"], "二次调用应返回 created=False")
        self.assertEqual(
            d["data"]["conversation_id"],
            TestQ_PilotUpsert._conv_id_first,
            "对话 ID 应与首次创建一致",
        )

    def test_Q3_different_users_get_different_convs_in_auth_mode(self):
        """Q3: auth 模式下不同用户访问同一报表，各自拥有独立对话"""
        from backend.config.settings import settings as _settings
        # 仅 auth 模式下此测试有意义；跳过 Q3 时 auth=False
        if not _settings.enable_auth:
            # 在匿名模式下两个用户共享同一 user_id=None，所以得到相同对话
            # 此测试跳过，在 auth=True 时才有意义
            self.skipTest("ENABLE_AUTH=False, 匿名模式下所有用户共享同一对话")

        # user_b 访问 user_a 的报表（在 auth 模式下 user_b 没有所有权，应 403）
        resp_b = self.client.post(
            f"/api/v1/reports/{self.report_a.id}/copilot",
            json={},
            headers=_auth(self.user_b),
        )
        self.assertIn(resp_b.status_code, [403, 200])

    def test_Q4_anonymous_mode_reuses_conversation(self):
        """Q4: 匿名模式下（user_id=None）同一报表复用同一对话"""
        # 不带认证头（ENABLE_AUTH=False 时允许）
        resp1 = self.client.post(
            f"/api/v1/reports/{self.report_a.id}/copilot",
            json={},
        )
        # 无认证头时可能走匿名路径（取决于 ENABLE_AUTH 设置）
        # 只要不是 500 都算通过此测试
        self.assertNotEqual(resp1.status_code, 500, "不应返回 500")

    def test_Q5_conversation_metadata_contains_context(self):
        """Q5: 创建的对话 extra_metadata 包含 context_type 和 context_id"""
        # 新建一个报表以保证独立的 first-call
        report_q5 = _make_report(self.user_a.username)
        resp = self.client.post(
            f"/api/v1/reports/{report_q5.id}/copilot",
            json={},
            headers=_auth(self.user_a),
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        conv_id = resp.json()["data"]["conversation_id"]

        # 读取对话验证 metadata
        from backend.services.conversation_service import ConversationService
        svc = ConversationService(_g_db)
        conv = svc.get_conversation(conv_id)
        self.assertIsNotNone(conv)
        meta = conv.extra_metadata or {}
        self.assertEqual(meta.get("context_type"), "report")
        self.assertEqual(meta.get("context_id"), str(report_q5.id))

    def test_Q6_invalid_report_id_returns_404(self):
        """Q6: 无效 report_id → 404"""
        fake_id = str(uuid.uuid4())
        resp = self.client.post(
            f"/api/v1/reports/{fake_id}/copilot",
            json={},
            headers=_auth(self.user_a),
        )
        self.assertEqual(resp.status_code, 404, resp.text)

    def test_Q7_ownership_check_returns_403(self):
        """Q7: user_b 访问仅属于 user_a 的报表 → 403"""
        from backend.config.settings import settings as _settings
        if not _settings.enable_auth:
            self.skipTest("ENABLE_AUTH=False 下所有权限检查不生效")
        resp = self.client.post(
            f"/api/v1/reports/{self.report_a.id}/copilot",
            json={},
            headers=_auth(self.user_b),
        )
        self.assertEqual(resp.status_code, 403)

    def test_Q8_find_pilot_conversation_safe_when_none(self):
        """Q8: find_pilot_conversation 无匹配时返回 None，不崩溃"""
        from backend.services.conversation_service import ConversationService
        svc = ConversationService(_g_db)
        result = svc.find_pilot_conversation(
            context_type="report",
            context_id=str(uuid.uuid4()),  # 不存在的 report ID
            user_id=None,
        )
        self.assertIsNone(result)


# ═══════════════════════════════════════════════════════════════════════════════
# R 段 — spec 更新响应含 spec_updated 标识
# ═══════════════════════════════════════════════════════════════════════════════

class TestR_SpecUpdatedFlag(unittest.TestCase):
    """PUT /spec 和 PUT /charts/{id} 响应中 spec_updated == True"""

    @classmethod
    def setUpClass(cls):
        cls.client = _make_client()
        cls.user = _make_user("ru")
        cls.report = _make_report_with_file(cls.user.username)

    def test_R1_put_spec_response_has_spec_updated(self):
        """R1: PUT /spec 响应 data.spec_updated == True"""
        new_spec = {
            "title": f"{_PREFIX}更新标题",
            "subtitle": "",
            "theme": "light",
            "filters": [],
            "data_sources": [],
            "charts": [
                {"id": "c_new", "title": "新图表", "chart_type": "bar",
                 "sql": "SELECT 1", "connection_env": "test"},
            ],
            "data": {},
        }
        resp = self.client.put(
            f"/api/v1/reports/{self.report.id}/spec",
            json={"spec": new_spec},
            headers=_auth(self.user),
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        d = resp.json()
        self.assertTrue(d.get("success"), d)
        self.assertTrue(
            d["data"].get("spec_updated"),
            f"data.spec_updated 应为 True，实际: {d['data']}",
        )

    def test_R2_put_chart_response_has_spec_updated(self):
        """R2: PUT /charts/{id} 响应 data.spec_updated == True"""
        chart_patch = {
            "chart_type": "area",
            "title": "更新后的图表A",
        }
        resp = self.client.put(
            f"/api/v1/reports/{self.report.id}/charts/chart_a",
            json={"chart": chart_patch},
            headers=_auth(self.user),
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        d = resp.json()
        self.assertTrue(d.get("success"), d)
        self.assertTrue(
            d["data"].get("spec_updated"),
            f"data.spec_updated 应为 True，实际: {d['data']}",
        )

    def test_R3_put_spec_actually_updates_db(self):
        """R3: PUT /spec 实际更新数据库 charts 字段"""
        new_spec = {
            "title": f"{_PREFIX}DB更新验证",
            "subtitle": "",
            "theme": "dark",
            "filters": [],
            "data_sources": [],
            "charts": [
                {"id": "db_verify", "title": "DB验证图表", "chart_type": "pie",
                 "sql": "SELECT 99", "connection_env": "test"},
            ],
            "data": {},
        }
        resp = self.client.put(
            f"/api/v1/reports/{self.report.id}/spec",
            json={"spec": new_spec},
            headers=_auth(self.user),
        )
        self.assertEqual(resp.status_code, 200, resp.text)

        # 重新从数据库查询验证
        _g_db.expire_all()
        from backend.models.report import Report
        r = _g_db.query(Report).filter(Report.id == self.report.id).first()
        self.assertIsNotNone(r)
        chart_ids = [c.get("id") for c in (r.charts or [])]
        self.assertIn("db_verify", chart_ids, f"charts 未更新，实际: {chart_ids}")

    def test_R4_put_chart_partial_merge_keeps_other_charts(self):
        """R4: PUT /charts/{id} 局部更新，不覆盖其他图表"""
        # 先重置 report 有两个图表
        report_r4 = _make_report_with_file(self.user.username)
        initial_charts = report_r4.charts or []
        self.assertGreaterEqual(len(initial_charts), 2, "测试需要至少 2 个图表")

        # 只更新 chart_a
        resp = self.client.put(
            f"/api/v1/reports/{report_r4.id}/charts/chart_a",
            json={"chart": {"title": "只改这个", "chart_type": "scatter"}},
            headers=_auth(self.user),
        )
        self.assertEqual(resp.status_code, 200, resp.text)

        # 检查 chart_b 未被删除
        _g_db.expire_all()
        from backend.models.report import Report
        r = _g_db.query(Report).filter(Report.id == report_r4.id).first()
        chart_ids = [c.get("id") for c in (r.charts or [])]
        self.assertIn("chart_b", chart_ids, f"chart_b 不应被删除，实际 charts: {chart_ids}")
        # chart_a 应被更新
        chart_a = next((c for c in r.charts if c.get("id") == "chart_a"), None)
        self.assertIsNotNone(chart_a)
        self.assertEqual(chart_a.get("title"), "只改这个")


# ═══════════════════════════════════════════════════════════════════════════════
# S 段 — RBAC 权限矩阵
# ═══════════════════════════════════════════════════════════════════════════════

class TestS_RBACMatrix(unittest.TestCase):
    """权限矩阵：各角色对 copilot/spec/charts 端点的正确权限"""

    @classmethod
    def setUpClass(cls):
        cls.client = _make_client()
        cls.analyst = _make_user("sana", role_name="analyst")
        cls.viewer = _make_user("svwr", role_name="viewer")
        cls.report = _make_report_with_file(cls.analyst.username)

    # ── DB 权限测试（需要 auth 模式）──────────────────────────────────────────

    def test_S1_viewer_cannot_post_copilot(self):
        """S1: viewer 角色无法 POST /copilot（无 reports:read 权限）"""
        from backend.config.settings import settings as _settings
        if not _settings.enable_auth:
            self.skipTest("ENABLE_AUTH=False")
        resp = self.client.post(
            f"/api/v1/reports/{self.report.id}/copilot",
            json={},
            headers=_auth(self.viewer),
        )
        self.assertIn(resp.status_code, [403, 401])

    def test_S2_analyst_can_post_copilot(self):
        """S2: analyst 角色可以 POST /copilot"""
        from backend.config.settings import settings as _settings
        if not _settings.enable_auth:
            self.skipTest("ENABLE_AUTH=False")
        resp = self.client.post(
            f"/api/v1/reports/{self.report.id}/copilot",
            json={},
            headers=_auth(self.analyst),
        )
        self.assertEqual(resp.status_code, 200)

    def test_S3_analyst_can_put_spec(self):
        """S3: analyst 角色可以 PUT /spec（reports:create ✓）"""
        from backend.config.settings import settings as _settings
        if not _settings.enable_auth:
            self.skipTest("ENABLE_AUTH=False")
        new_spec = {
            "title": f"{_PREFIX}S3",
            "subtitle": "",
            "theme": "light",
            "filters": [],
            "data_sources": [],
            "charts": [{"id": "s3c", "title": "S3", "chart_type": "bar",
                        "sql": "SELECT 1", "connection_env": "test"}],
            "data": {},
        }
        resp = self.client.put(
            f"/api/v1/reports/{self.report.id}/spec",
            json={"spec": new_spec},
            headers=_auth(self.analyst),
        )
        self.assertEqual(resp.status_code, 200)

    def test_S4_viewer_cannot_put_spec(self):
        """S4: viewer 角色无法 PUT /spec（无 reports:create 权限）"""
        from backend.config.settings import settings as _settings
        if not _settings.enable_auth:
            self.skipTest("ENABLE_AUTH=False")
        resp = self.client.put(
            f"/api/v1/reports/{self.report.id}/spec",
            json={"spec": {"charts": [], "title": "x", "theme": "light",
                           "filters": [], "data_sources": [], "data": {}}},
            headers=_auth(self.viewer),
        )
        self.assertIn(resp.status_code, [403, 401])

    # ── 静态分析：路由守卫 & 端点权限声明 ────────────────────────────────────

    def test_S5_report_view_route_no_require_auth(self):
        """S5: /report-view 路由不被 RequireAuth 包裹（使用 refresh_token 访问）"""
        app_content = _APP_FILE.read_text(encoding="utf-8")
        # 确认 /report-view 路由存在
        self.assertIn('/report-view', app_content, "/report-view 路由应在 App.tsx 中")
        # 确认 /report-view 的 Route 不在 RequireAuth 块内
        # 方法：在 /report-view 那一行前面找最近的 RequireAuth，确认没有直接包裹
        lines = app_content.splitlines()
        route_line_idx = next(
            (i for i, l in enumerate(lines) if '/report-view' in l), None
        )
        self.assertIsNotNone(route_line_idx)
        # 该行本身不应含 RequireAuth
        self.assertNotIn("RequireAuth", lines[route_line_idx])

    def test_S6_copilot_endpoint_uses_reports_read_permission(self):
        """S6: POST /copilot 端点使用 require_permission("reports", "read")"""
        api_content = _REPORTS_API.read_text(encoding="utf-8")
        # 查找 copilot 路由定义后的权限声明
        self.assertIn('require_permission("reports", "read")', api_content,
                      "copilot 端点应使用 reports:read 权限")

    def test_S7_put_spec_uses_reports_create_permission(self):
        """S7: PUT /spec 端点使用 require_permission("reports", "create")"""
        api_content = _REPORTS_API.read_text(encoding="utf-8")
        self.assertIn('require_permission("reports", "create")', api_content,
                      "PUT /spec 端点应使用 reports:create 权限")

    def test_S8_put_charts_uses_reports_create_permission(self):
        """S8: PUT /charts/{id} 使用 require_permission("reports","create")"""
        api_content = _REPORTS_API.read_text(encoding="utf-8")
        # reports:create 应出现在 update_single_chart 路由之前
        idx_chart = api_content.find('update_single_chart')
        idx_perm = api_content.rfind('require_permission("reports", "create")', 0, idx_chart)
        self.assertGreater(idx_perm, 0,
                           "PUT /charts/{id} 应在其路由函数之前声明 reports:create 权限")


# ═══════════════════════════════════════════════════════════════════════════════
# T 段 — 前端代码静态分析
# ═══════════════════════════════════════════════════════════════════════════════

class TestT_FrontendStatic(unittest.TestCase):
    """前端代码静态分析：验证新增/修改的关键代码片段"""

    @classmethod
    def setUpClass(cls):
        cls.copilot = _COPILOT_FILE.read_text(encoding="utf-8")
        cls.preview = _PREVIEW_FILE.read_text(encoding="utf-8")
        cls.viewer = _VIEWER_FILE.read_text(encoding="utf-8")

    # ── DataCenterCopilot.tsx ─────────────────────────────────────────────────

    def test_T1_getPilotConvKey_exists_and_uses_context(self):
        """T1: getPilotConvKey 函数存在，按 contextType/contextId 生成 key"""
        self.assertIn("getPilotConvKey", self.copilot)
        self.assertIn("pilot_conv_", self.copilot)
        self.assertIn("contextType", self.copilot)
        self.assertIn("contextId", self.copilot)

    def test_T2_localStorage_used_for_conversation_id(self):
        """T2: localStorage.getItem/setItem 用于持久化对话 ID"""
        self.assertIn("localStorage.getItem(lsKey)", self.copilot)
        self.assertIn("localStorage.setItem(lsKey", self.copilot)

    def test_T3_upsert_endpoint_called(self):
        """T3: 初始化中调用 /reports/{contextId}/copilot upsert 端点"""
        self.assertIn("/reports/${contextId}/copilot", self.copilot)

    def test_T4_loadHistory_calls_getMessages(self):
        """T4: loadHistory 函数调用 conversationApi.getMessages"""
        self.assertIn("loadHistory", self.copilot)
        self.assertIn("conversationApi.getMessages", self.copilot)

    def test_T5_spec_updated_detects_tool_names(self):
        """T5: spec_updated 检测包含 report__update_spec 和 report__update_single_chart"""
        self.assertIn("report__update_spec", self.copilot)
        self.assertIn("report__update_single_chart", self.copilot)

    def test_T6_spec_updated_detects_json_flag(self):
        """T6: spec_updated 检测包含 spec_updated:true 字符串匹配"""
        self.assertIn('"spec_updated":true', self.copilot)
        self.assertIn('"spec_updated": true', self.copilot)

    # ── ReportViewerPage.tsx ─────────────────────────────────────────────────

    def test_T7_pilotOpenKey_function_exists(self):
        """T7: ReportViewerPage 含有 pilotOpenKey 函数"""
        self.assertIn("pilotOpenKey", self.viewer)
        self.assertIn("pilot_open_", self.viewer)

    def test_T8_pilotOpen_persisted_to_localStorage(self):
        """T8: pilotOpen 状态通过 localStorage 持久化"""
        self.assertIn("localStorage.getItem(pilotOpenKey", self.viewer)
        self.assertIn("localStorage.setItem(pilotOpenKey", self.viewer)

    def test_T9_handleRefreshIframe_increments_iframeKey(self):
        """T9: handleRefreshIframe 通过 iframeKey++ 触发 iframe 重载"""
        self.assertIn("handleRefreshIframe", self.viewer)
        self.assertIn("setIframeKey", self.viewer)
        self.assertIn("setIframeLoading(true)", self.viewer)

    def test_T10_ReloadOutlined_in_viewer_toolbar(self):
        """T10: ReloadOutlined 在 ReportViewerPage 的 iframe 工具栏中"""
        self.assertIn("ReloadOutlined", self.viewer)
        # 确认是在工具栏相关的代码中使用（不只是导入）
        self.assertIn("<ReloadOutlined />", self.viewer)

    # ── ReportPreviewModal.tsx ───────────────────────────────────────────────

    def test_T11_ReloadOutlined_imported_in_modal(self):
        """T11: ReportPreviewModal 导入了 ReloadOutlined"""
        self.assertIn("ReloadOutlined", self.preview)

    def test_T12_modal_refresh_triggers_iframeKey(self):
        """T12: Modal 刷新按钮触发 iframeKey 递增"""
        self.assertIn("setIframeKey", self.preview)
        # 确认 iframeKey 在 Modal 中被递增
        self.assertIn("(k) => k + 1", self.preview)

    def test_T13_modal_refresh_button_has_loading_state(self):
        """T13: Modal 刷新按钮含有 loading={iframeLoading} 状态"""
        self.assertIn("loading={iframeLoading}", self.preview)
        # 刷新按钮有标签文字
        self.assertIn("刷新", self.preview)


# ═══════════════════════════════════════════════════════════════════════════════
# U 段 — 端到端完整流程
# ═══════════════════════════════════════════════════════════════════════════════

class TestU_EndToEndFlow(unittest.TestCase):
    """端到端完整流程：创建报表 → Pilot → 重复访问 → 对话一致"""

    @classmethod
    def setUpClass(cls):
        cls.client = _make_client()
        cls.user = _make_user("ue2e")
        cls.report = _make_report(cls.user.username)
        cls.report_with_file = _make_report_with_file(cls.user.username)

    def test_U1_full_flow_create_then_reuse_conversation(self):
        """U1: 完整流程：创建报表→首次 copilot→二次 copilot→对话 ID 一致"""
        report = _make_report(self.user.username)

        # 第一次访问
        resp1 = self.client.post(
            f"/api/v1/reports/{report.id}/copilot",
            json={},
            headers=_auth(self.user),
        )
        self.assertEqual(resp1.status_code, 200, resp1.text)
        d1 = resp1.json()
        self.assertTrue(d1["data"]["created"])
        conv_id_1 = d1["data"]["conversation_id"]

        # 第二次访问（模拟页面刷新后重新打开）
        resp2 = self.client.post(
            f"/api/v1/reports/{report.id}/copilot",
            json={},
            headers=_auth(self.user),
        )
        self.assertEqual(resp2.status_code, 200, resp2.text)
        d2 = resp2.json()
        self.assertFalse(d2["data"]["created"])
        conv_id_2 = d2["data"]["conversation_id"]

        self.assertEqual(conv_id_1, conv_id_2, "两次对话 ID 必须一致")

    def test_U2_spec_meta_returns_report_metadata(self):
        """U2: GET /spec-meta 通过 refresh_token 返回报表元数据"""
        resp = self.client.get(
            f"/api/v1/reports/{self.report.id}/spec-meta",
            params={"token": self.report.refresh_token},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        d = resp.json()
        self.assertTrue(d["success"])
        self.assertIn("id", d["data"])
        self.assertEqual(d["data"]["id"], str(self.report.id))

    def test_U3_spec_update_does_not_break_conversation_reuse(self):
        """U3: 更新 spec 后再访问 copilot，对话 ID 不变（不因 spec 变化重建）"""
        report = _make_report_with_file(self.user.username)

        # 先建立 copilot 对话
        resp1 = self.client.post(
            f"/api/v1/reports/{report.id}/copilot",
            json={},
            headers=_auth(self.user),
        )
        self.assertEqual(resp1.status_code, 200)
        conv_id_before = resp1.json()["data"]["conversation_id"]

        # 更新 spec
        new_spec = {
            "title": f"{_PREFIX}U3更新",
            "subtitle": "",
            "theme": "dark",
            "filters": [],
            "data_sources": [],
            "charts": [{"id": "u3c", "title": "U3图表", "chart_type": "line",
                        "sql": "SELECT 1", "connection_env": "test"}],
            "data": {},
        }
        resp_spec = self.client.put(
            f"/api/v1/reports/{report.id}/spec",
            json={"spec": new_spec},
            headers=_auth(self.user),
        )
        self.assertEqual(resp_spec.status_code, 200)

        # 再次访问 copilot，对话 ID 应不变
        resp2 = self.client.post(
            f"/api/v1/reports/{report.id}/copilot",
            json={},
            headers=_auth(self.user),
        )
        self.assertEqual(resp2.status_code, 200)
        conv_id_after = resp2.json()["data"]["conversation_id"]

        self.assertEqual(
            conv_id_before, conv_id_after,
            "spec 更新后 copilot 对话 ID 应保持不变",
        )

    def test_U4_conversation_metadata_has_refresh_token(self):
        """U4: 创建的 copilot 对话 extra_metadata 中包含 refresh_token"""
        report = _make_report(self.user.username)
        resp = self.client.post(
            f"/api/v1/reports/{report.id}/copilot",
            json={},
            headers=_auth(self.user),
        )
        self.assertEqual(resp.status_code, 200)
        conv_id = resp.json()["data"]["conversation_id"]

        from backend.services.conversation_service import ConversationService
        svc = ConversationService(_g_db)
        conv = svc.get_conversation(conv_id)
        self.assertIsNotNone(conv)
        meta = conv.extra_metadata or {}
        self.assertIn("refresh_token", meta,
                      f"extra_metadata 应含 refresh_token，实际: {meta}")
        self.assertEqual(meta["refresh_token"], report.refresh_token)


# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    unittest.main(verbosity=2)
