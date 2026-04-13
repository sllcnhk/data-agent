"""
test_pilot_e2e.py — Pilot 功能端到端全面测试套件
=================================================

覆盖范围：
  F段 (9)  — 前端代码静态分析（无需 DB）
               F1-F9: 组件结构、model_key bug修复、autoPilot URL处理等
  G段 (6)  — RBAC 权限矩阵（需要 DB）
               G1-G6: viewer/analyst 权限、ownership 隔离
  H段 (4)  — 模型切换集成（需要 DB）
               H1-H4: PUT model_key 字段、更新持久化、消息不受影响
  I段 (4)  — HTML 注入 doc_type 路由（纯函数 + DB）
               I1-I4: dashboard/document 分别路由正确页面
  J段 (4)  — 端到端 Pilot 流程（需要 DB）
               J1-J4: schedule/report copilot 完整流程、ownership

总计: 27 个测试用例
"""

from __future__ import annotations

import os
import sys
import uuid
import json
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

# ── 路径 & 环境初始化 ────────────────────────────────────────────────────────
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "backend"))
os.environ.setdefault("POSTGRES_PASSWORD", "postgres")
os.environ.setdefault("POSTGRES_HOST", "localhost")
os.environ.setdefault("ENABLE_AUTH", "False")

from test_utils import make_test_username  # noqa: E402

_PREFIX = f"_pe2e_{uuid.uuid4().hex[:6]}_"

# ── 前端文件路径 ──────────────────────────────────────────────────────────────
_FRONTEND_ROOT = Path(__file__).parent / "frontend" / "src"
_COPILOT_FILE = _FRONTEND_ROOT / "components" / "DataCenterCopilot.tsx"
_MINI_SEL_FILE = _FRONTEND_ROOT / "components" / "ModelSelectorMini.tsx"
_PREVIEW_FILE = _FRONTEND_ROOT / "components" / "chat" / "ReportPreviewModal.tsx"
_DASHBOARDS_FILE = _FRONTEND_ROOT / "pages" / "DataCenterDashboards.tsx"
_DOCUMENTS_FILE = _FRONTEND_ROOT / "pages" / "DataCenterDocuments.tsx"
_SCHEDULES_FILE = _FRONTEND_ROOT / "pages" / "DataCenterSchedules.tsx"

# ── 模块级 auth 补丁 ─────────────────────────────────────────────────────────
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


# ── DB helpers ────────────────────────────────────────────────────────────────

def _db():
    from backend.config.database import SessionLocal
    return SessionLocal()


_g_db = _db()


def _make_user(suffix="", role_name="admin"):
    from backend.models.user import User
    from backend.models.role import Role
    from backend.models.user_role import UserRole
    from backend.core.auth.password import hash_password

    username = f"{_PREFIX}{suffix or uuid.uuid4().hex[:6]}"
    u = User(
        username=username,
        display_name=f"E2E Test {suffix}",
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


def _make_report(owner_username, doc_type="dashboard"):
    from backend.models.report import Report
    name = f"{_PREFIX}rpt_{uuid.uuid4().hex[:6]}"
    r = Report(
        name=name,
        doc_type=doc_type,
        theme="light",
        charts=[{"type": "bar", "title": "测试图表"}],
        filters=[],
        username=owner_username,
        refresh_token=uuid.uuid4().hex,
        share_scope="private",
    )
    _g_db.add(r)
    _g_db.commit()
    _g_db.refresh(r)
    return r


def _make_schedule(owner_username, is_active=True):
    from backend.models.scheduled_report import ScheduledReport
    name = f"{_PREFIX}sch_{uuid.uuid4().hex[:6]}"
    sr = ScheduledReport(
        name=name,
        cron_expr="0 9 * * 1",
        timezone="Asia/Shanghai",
        report_id=None,
        username=owner_username,
        is_active=is_active,
        notify_channels=[{"type": "email", "to": "test@example.com"}],
        run_count=0,
        fail_count=0,
    )
    _g_db.add(sr)
    _g_db.commit()
    _g_db.refresh(sr)
    return sr


def _cleanup_test_data():
    """删除本次测试生成的全部数据"""
    try:
        from backend.models.user import User
        from backend.models.report import Report
        from backend.models.conversation import Conversation
        _g_db.query(User).filter(User.username.like(f"{_PREFIX}%")).delete(synchronize_session=False)
        _g_db.query(Report).filter(Report.name.like(f"{_PREFIX}%")).delete(synchronize_session=False)
        _g_db.query(Conversation).filter(Conversation.title.like(f"%{_PREFIX}%")).delete(synchronize_session=False)
        _g_db.commit()
    except Exception:
        try:
            _g_db.rollback()
        except Exception:
            pass

    try:
        from backend.models.scheduled_report import ScheduledReport
        _g_db.query(ScheduledReport).filter(ScheduledReport.name.like(f"{_PREFIX}%")).delete(synchronize_session=False)
        _g_db.commit()
    except Exception:
        try:
            _g_db.rollback()
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# F段 — 前端代码静态分析（无需 DB）
# ─────────────────────────────────────────────────────────────────────────────

class TestFFrontendCodeAnalysis(unittest.TestCase):
    """前端组件结构与 bug 修复验证（静态代码分析，无需数据库）"""

    def _read(self, path: Path) -> str:
        return path.read_text(encoding="utf-8")

    # F1: ModelSelectorMini 使用正确 API
    def test_F1_model_selector_mini_fetches_llm_configs(self):
        code = self._read(_MINI_SEL_FILE)
        self.assertIn("llm-configs", code, "ModelSelectorMini 应请求 /llm-configs 端点")
        self.assertIn("enabled_only", code, "应过滤 enabled_only=true")

    # F2: Bug1 已修复 — model_key (非 model) 用于 PUT /conversations/{id}
    def test_F2_model_switch_uses_model_key_not_model(self):
        import re
        code = self._read(_COPILOT_FILE)
        # 必须有 model_key: 的对象键形式
        self.assertIn("model_key:", code, "Bug1 修复: handleModelChange 应发送 model_key 字段")
        # JSON.stringify 调用里不应出现 { model: ... }（不带下划线 key）
        bad_pattern = re.search(r'JSON\.stringify\(\s*\{\s*model\s*:', code)
        self.assertIsNone(bad_pattern, "Bug1 修复: 不应有 { model: modelKey } — 应为 { model_key: modelKey }")

    # F3: ReportPreviewModal 导出 PilotContext 并接受 pilotContext prop
    def test_F3_report_preview_modal_has_pilot_context(self):
        code = self._read(_PREVIEW_FILE)
        self.assertIn("PilotContext", code, "ReportPreviewModal 应导出 PilotContext 接口")
        self.assertIn("pilotContext", code, "ReportPreviewModal props 应包含 pilotContext")
        self.assertIn("pilotOpen", code, "ReportPreviewModal 应有 pilotOpen state")

    # F4: DataCenterDashboards 传递 contextType=dashboard
    def test_F4_dashboards_pilot_context_type(self):
        code = self._read(_DASHBOARDS_FILE)
        self.assertIn("contextType", code, "DataCenterDashboards 应传递 pilotContext")
        self.assertIn("'dashboard'", code, "contextType 应为 'dashboard'")

    # F5: DataCenterDocuments 传递 contextType=document
    def test_F5_documents_pilot_context_type(self):
        code = self._read(_DOCUMENTS_FILE)
        self.assertIn("contextType", code, "DataCenterDocuments 应传递 pilotContext")
        self.assertIn("'document'", code, "contextType 应为 'document'")

    # F6: DataCenterSchedules 在历史 Drawer 中有 pilot FAB
    def test_F6_schedules_history_drawer_has_pilot_fab(self):
        code = self._read(_SCHEDULES_FILE)
        # 应有 copilot/pilot 相关的按钮在历史 drawer 中
        self.assertIn("copilotSchedule", code, "DataCenterSchedules 应有 copilotSchedule state")
        self.assertIn("RobotOutlined", code, "历史 Drawer 应有 pilot FAB 按钮")

    # F7: DataCenterDashboards 处理 autoPilot URL 参数
    def test_F7_dashboards_handles_auto_pilot_url_param(self):
        code = self._read(_DASHBOARDS_FILE)
        self.assertIn("autoPilot", code, "DataCenterDashboards 应处理 autoPilot URL 参数")
        self.assertIn("window.location.search", code, "应从 URL 读取 autoPilot 参数")
        self.assertIn("replaceState", code, "应清理 URL 中的 autoPilot 参数")

    # F8: Bug3 已修复 — DataCenterDocuments 处理 autoPilot URL 参数
    def test_F8_documents_handles_auto_pilot_url_param(self):
        code = self._read(_DOCUMENTS_FILE)
        self.assertIn("autoPilot", code, "Bug3 修复: DataCenterDocuments 应处理 autoPilot URL 参数")
        self.assertIn("window.location.search", code, "应从 URL 读取 autoPilot 参数")
        self.assertIn("replaceState", code, "应清理 URL 中的 autoPilot 参数")

    # F9: DataCenterCopilot 创建对话时携带 model_key
    def test_F9_copilot_content_creates_conversation_with_model_key(self):
        code = self._read(_COPILOT_FILE)
        self.assertIn("body.model_key = selectedModel", code,
                      "DataCenterCopilotContent 应在 createConversation 时传递 model_key")
        # 有 selectedModel state
        self.assertIn("selectedModel", code, "应有 selectedModel state")
        # 有 ModelSelectorMini
        self.assertIn("ModelSelectorMini", code, "应渲染 ModelSelectorMini 组件")


# ─────────────────────────────────────────────────────────────────────────────
# G段 — RBAC 权限矩阵（需要 DB）
# ─────────────────────────────────────────────────────────────────────────────

class TestGRBACMatrix(unittest.TestCase):
    """viewer/analyst 权限与 ownership 隔离测试"""

    @classmethod
    def setUpClass(cls):
        cls.client = _make_client()
        cls.viewer = _make_user("g_viewer", role_name="viewer")
        cls.analyst = _make_user("g_analyst", role_name="analyst")
        cls.owner = _make_user("g_owner", role_name="analyst")
        cls.other = _make_user("g_other", role_name="analyst")

        cls.report = _make_report(cls.owner.username, doc_type="dashboard")
        cls.schedule = _make_schedule(cls.owner.username)

    # G1: viewer 不能访问 report copilot
    def test_G1_viewer_cannot_access_report_copilot(self):
        r = self.client.post(
            f"/api/v1/reports/{self.report.id}/copilot",
            headers=_auth(self.viewer),
        )
        self.assertEqual(r.status_code, 403, "viewer 应被 403 拒绝访问 report copilot")

    # G2: viewer 不能访问 schedule copilot
    def test_G2_viewer_cannot_access_schedule_copilot(self):
        r = self.client.post(
            f"/api/v1/scheduled-reports/{self.schedule.id}/copilot",
            headers=_auth(self.viewer),
        )
        self.assertEqual(r.status_code, 403, "viewer 应被 403 拒绝访问 schedule copilot")

    # G3: analyst 可以访问自己的 report copilot
    def test_G3_analyst_can_access_own_report_copilot(self):
        my_report = _make_report(self.analyst.username, doc_type="dashboard")
        r = self.client.post(
            f"/api/v1/reports/{my_report.id}/copilot",
            headers=_auth(self.analyst),
        )
        self.assertEqual(r.status_code, 200, f"analyst 应可访问自己的 report copilot，实际: {r.status_code}")
        self.assertTrue(r.json().get("success"), "应返回 success=True")

    # G4: analyst 可以访问自己的 schedule copilot
    def test_G4_analyst_can_access_own_schedule_copilot(self):
        my_sch = _make_schedule(self.analyst.username)
        r = self.client.post(
            f"/api/v1/scheduled-reports/{my_sch.id}/copilot",
            headers=_auth(self.analyst),
        )
        self.assertEqual(r.status_code, 200, f"analyst 应可访问自己的 schedule copilot，实际: {r.status_code}")
        self.assertTrue(r.json().get("success"), "应返回 success=True")

    # G5: 用户不能访问他人的 report copilot（ownership check）
    def test_G5_user_cannot_copilot_other_users_report(self):
        r = self.client.post(
            f"/api/v1/reports/{self.report.id}/copilot",
            headers=_auth(self.other),
        )
        # ownership check 应返回 403
        self.assertIn(r.status_code, [403], f"其他用户不能访问 ownership 保护的 report copilot，实际: {r.status_code}")

    # G6: 用户不能访问他人的 schedule copilot（ownership check）
    def test_G6_user_cannot_copilot_other_users_schedule(self):
        r = self.client.post(
            f"/api/v1/scheduled-reports/{self.schedule.id}/copilot",
            headers=_auth(self.other),
        )
        self.assertIn(r.status_code, [403], f"其他用户不能访问 ownership 保护的 schedule copilot，实际: {r.status_code}")


# ─────────────────────────────────────────────────────────────────────────────
# H段 — 模型切换集成（需要 DB）
# ─────────────────────────────────────────────────────────────────────────────

class TestHModelSwitch(unittest.TestCase):
    """PUT /conversations/{id} model_key 字段与持久化验证"""

    @classmethod
    def setUpClass(cls):
        cls.client = _make_client()
        cls.user = _make_user("h_user", role_name="admin")

    def _create_conv(self):
        r = self.client.post(
            "/api/v1/conversations",
            json={"title": f"{_PREFIX}conv_h", "model_key": "claude"},
            headers=_auth(self.user),
        )
        self.assertEqual(r.status_code, 200)
        return r.json()["data"]["id"]

    # H1: PUT /conversations/{id} 更新 current_model（使用 model_key 字段）
    def test_H1_put_model_key_updates_current_model(self):
        cid = self._create_conv()
        r = self.client.put(
            f"/api/v1/conversations/{cid}",
            json={"model_key": "qianwen"},
            headers=_auth(self.user),
        )
        self.assertEqual(r.status_code, 200, f"PUT model_key 应返回 200，实际: {r.status_code}")
        data = r.json()
        self.assertTrue(data.get("success"), "应返回 success=True")

    # H2: model_key 字段持久化到 current_model
    def test_H2_model_key_persisted_in_current_model(self):
        cid = self._create_conv()
        self.client.put(
            f"/api/v1/conversations/{cid}",
            json={"model_key": "doubao"},
            headers=_auth(self.user),
        )
        # 重新获取对话验证持久化
        r = self.client.get(
            f"/api/v1/conversations/{cid}",
            headers=_auth(self.user),
        )
        self.assertEqual(r.status_code, 200)
        conv_data = r.json().get("data", {})
        self.assertEqual(conv_data.get("current_model"), "doubao",
                         f"current_model 应已更新为 doubao，实际: {conv_data.get('current_model')}")

    # H3: 对不存在的对话 PUT model_key 返回 404
    def test_H3_put_model_key_nonexistent_conv_returns_404(self):
        fake_id = str(uuid.uuid4())
        r = self.client.put(
            f"/api/v1/conversations/{fake_id}",
            json={"model_key": "claude"},
            headers=_auth(self.user),
        )
        self.assertEqual(r.status_code, 404, "不存在的对话 PUT 应返回 404")

    # H4: 模型切换不影响已有消息
    def test_H4_model_switch_does_not_affect_existing_messages(self):
        cid = self._create_conv()
        # 先检查消息列表为空
        r_msgs_before = self.client.get(
            f"/api/v1/conversations/{cid}/messages",
            headers=_auth(self.user),
        )
        self.assertEqual(r_msgs_before.status_code, 200)
        count_before = len(r_msgs_before.json().get("data", {}).get("messages", []))

        # 切换模型
        self.client.put(
            f"/api/v1/conversations/{cid}",
            json={"model_key": "gemini"},
            headers=_auth(self.user),
        )

        # 消息数量不变
        r_msgs_after = self.client.get(
            f"/api/v1/conversations/{cid}/messages",
            headers=_auth(self.user),
        )
        count_after = len(r_msgs_after.json().get("data", {}).get("messages", []))
        self.assertEqual(count_before, count_after, "模型切换不应改变消息列表")


# ─────────────────────────────────────────────────────────────────────────────
# I段 — HTML 注入 doc_type 路由（纯函数 + 端点）
# ─────────────────────────────────────────────────────────────────────────────

class TestIHtmlInjectionDocType(unittest.TestCase):
    """Bug2 修复验证：_inject_pilot_button 按 doc_type 路由到正确页面"""

    def _inject(self, html, report_id, doc_type="dashboard"):
        from backend.api.reports import _inject_pilot_button
        return _inject_pilot_button(html, report_id, doc_type=doc_type)

    # I1: dashboard 类型注入 /data-center/dashboards URL
    def test_I1_dashboard_injects_dashboards_url(self):
        html = "<html><body><h1>Dashboard</h1></body></html>"
        rid = str(uuid.uuid4())
        result = self._inject(html, rid, doc_type="dashboard")
        self.assertIn("/data-center/dashboards", result,
                      "dashboard 报表注入按钮应链接到 /data-center/dashboards")
        self.assertNotIn("/data-center/documents", result,
                         "dashboard 报表不应链接到 /data-center/documents")

    # I2: document 类型注入 /data-center/documents URL（Bug2 修复）
    def test_I2_document_injects_documents_url(self):
        html = "<html><body><h1>Document</h1></body></html>"
        rid = str(uuid.uuid4())
        result = self._inject(html, rid, doc_type="document")
        self.assertIn("/data-center/documents", result,
                      "Bug2 修复: document 报告注入按钮应链接到 /data-center/documents")
        self.assertNotIn("/data-center/dashboards", result,
                         "Bug2 修复: document 报告不应链接到 /data-center/dashboards")

    # I3: 注入内容包含正确的 report_id
    def test_I3_injection_contains_correct_report_id(self):
        html = "<html><body></body></html>"
        rid = str(uuid.uuid4())
        result = self._inject(html, rid, doc_type="dashboard")
        self.assertIn(rid, result, "注入内容应包含正确的 report_id")

    # I4: 无论 doc_type，都应有 postMessage 分支（iframe 场景）
    def test_I4_injection_always_has_postmessage_branch(self):
        html = "<html><body></body></html>"
        rid = str(uuid.uuid4())
        for doc_type in ["dashboard", "document"]:
            result = self._inject(html, rid, doc_type=doc_type)
            self.assertIn("postMessage", result,
                          f"doc_type={doc_type} 的注入应包含 postMessage（iframe 场景）")
            self.assertIn("window.top", result,
                          f"doc_type={doc_type} 的注入应检测 window.top（iframe 判断）")


# ─────────────────────────────────────────────────────────────────────────────
# J段 — 端到端 Pilot 流程（需要 DB）
# ─────────────────────────────────────────────────────────────────────────────

class TestJEndToEndPilot(unittest.TestCase):
    """完整的 Pilot 流程端到端测试"""

    @classmethod
    def setUpClass(cls):
        cls.client = _make_client()
        cls.user = _make_user("j_user", role_name="admin")
        cls.other = _make_user("j_other", role_name="analyst")

        cls.report = _make_report(cls.user.username, doc_type="dashboard")
        cls.doc_report = _make_report(cls.user.username, doc_type="document")
        cls.schedule = _make_schedule(cls.user.username)

    # J1: Schedule Copilot 完整流程：创建 → 获取对话
    def test_J1_schedule_copilot_full_flow(self):
        r = self.client.post(
            f"/api/v1/scheduled-reports/{self.schedule.id}/copilot",
            headers=_auth(self.user),
        )
        self.assertEqual(r.status_code, 200, f"schedule copilot 创建失败: {r.text}")
        conv_id = r.json()["data"]["conversation_id"]
        self.assertTrue(conv_id, "应返回有效的 conversation_id")

        # 获取对话，验证 system_prompt 包含任务名称
        r2 = self.client.get(
            f"/api/v1/conversations/{conv_id}",
            headers=_auth(self.user),
        )
        self.assertEqual(r2.status_code, 200)
        conv = r2.json().get("data", {})
        extra = conv.get("extra_metadata", {}) or {}
        system_prompt = extra.get("system_prompt", "") or ""
        self.assertIn(self.schedule.name, system_prompt,
                      "system_prompt 应包含推送任务名称")
        self.assertIn(self.schedule.cron_expr, system_prompt,
                      "system_prompt 应包含 cron 表达式")

    # J2: Report Copilot 完整流程：创建 → 获取对话 → 验证 context_type
    def test_J2_report_copilot_full_flow(self):
        r = self.client.post(
            f"/api/v1/reports/{self.report.id}/copilot",
            headers=_auth(self.user),
        )
        self.assertEqual(r.status_code, 200, f"report copilot 创建失败: {r.text}")
        conv_id = r.json()["data"]["conversation_id"]

        r2 = self.client.get(
            f"/api/v1/conversations/{conv_id}",
            headers=_auth(self.user),
        )
        self.assertEqual(r2.status_code, 200)
        conv = r2.json().get("data", {})
        extra = conv.get("extra_metadata", {}) or {}

        self.assertEqual(extra.get("context_type"), "report",
                         "report copilot 的 context_type 应为 'report'")
        self.assertEqual(extra.get("context_id"), str(self.report.id),
                         "context_id 应等于报表 ID")
        system_prompt = extra.get("system_prompt", "") or ""
        self.assertIn(self.report.name, system_prompt,
                      "system_prompt 应包含报表名称")

    # J3: Document Report Copilot 验证 context_type=report（非 document）
    def test_J3_document_report_copilot_context_type(self):
        """doc_type=document 的报告通过同一 /reports/{id}/copilot 端点，context_type=report"""
        r = self.client.post(
            f"/api/v1/reports/{self.doc_report.id}/copilot",
            headers=_auth(self.user),
        )
        self.assertEqual(r.status_code, 200)
        conv_id = r.json()["data"]["conversation_id"]

        r2 = self.client.get(
            f"/api/v1/conversations/{conv_id}",
            headers=_auth(self.user),
        )
        extra = r2.json().get("data", {}).get("extra_metadata", {}) or {}
        self.assertEqual(extra.get("context_type"), "report",
                         "document 报告的 copilot context_type 应为 'report'")

    # J4: Schedule Copilot Ownership 隔离（他人不能访问）
    def test_J4_schedule_copilot_ownership_isolation(self):
        r = self.client.post(
            f"/api/v1/scheduled-reports/{self.schedule.id}/copilot",
            headers=_auth(self.other),
        )
        self.assertIn(r.status_code, [403],
                      f"其他用户不能访问他人的 schedule copilot，实际: {r.status_code}")


# ─────────────────────────────────────────────────────────────────────────────
# 入口
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    unittest.main(verbosity=2)
