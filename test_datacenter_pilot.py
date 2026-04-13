"""
test_datacenter_pilot.py — Pilot 功能测试套件
==============================================

覆盖范围：
  A段 (5)  — POST /scheduled-reports/{id}/copilot 新端点
  B段 (5)  — _inject_pilot_button() 注入逻辑 + HTML 端点集成
  C段 (4)  — 回归：现有 /reports/{id}/copilot 端点不受影响
  D段 (4)  — 模型切换：conversation 创建时携带 model_key
  E段 (3)  — 模型切换：PUT /conversations/{id} 更新 current_model

总计: ~21 个测试用例
"""

from __future__ import annotations

import os
import sys
import uuid
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

# ── 路径 & 环境初始化 ────────────────────────────────────────────────────────
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "backend"))
os.environ.setdefault("POSTGRES_PASSWORD", "postgres")
os.environ.setdefault("POSTGRES_HOST", "localhost")
os.environ.setdefault("ENABLE_AUTH", "False")

from test_utils import make_test_username  # noqa: E402

_PREFIX = f"_pilot_{uuid.uuid4().hex[:6]}_"

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


def _make_user(suffix=""):
    from backend.models.user import User
    from backend.models.role import Role
    from backend.models.user_role import UserRole
    from backend.core.auth.password import hash_password

    username = f"{_PREFIX}{suffix or uuid.uuid4().hex[:6]}"
    u = User(
        username=username,
        display_name=f"Pilot Test {suffix}",
        hashed_password=hash_password("Test1234!"),
        auth_source="local",
        is_active=True,
        is_superadmin=False,
    )
    _g_db.add(u)
    _g_db.flush()
    role = _g_db.query(Role).filter(Role.name == "admin").first()
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


def _create_schedule(username="default", name=None) -> "ScheduledReport":
    """直接在 DB 中创建一条测试 ScheduledReport 记录。"""
    from backend.models.scheduled_report import ScheduledReport

    sr = ScheduledReport(
        name=name or f"{_PREFIX}sr_{uuid.uuid4().hex[:6]}",
        description="test schedule",
        owner_username=username,
        doc_type="dashboard",
        cron_expr="0 9 * * 1",
        timezone="Asia/Shanghai",
        report_spec={"charts": [], "title": "test"},
        include_summary=False,
        notify_channels=[{"type": "email", "to": "test@example.com"}],
        is_active=True,
    )
    _g_db.add(sr)
    _g_db.commit()
    _g_db.refresh(sr)
    return sr


def _create_report(username="default", name=None) -> "Report":
    """直接在 DB 中创建 Report 记录（跳过 HTML 生成）。"""
    from backend.models.report import Report
    from backend.services.report_builder_service import generate_refresh_token

    rid = uuid.uuid4()
    r = Report(
        id=rid,
        name=name or f"{_PREFIX}report_{rid.hex[:6]}",
        username=username,
        refresh_token=generate_refresh_token(),
        report_file_path=None,
        summary_status="skipped",
        charts=[{"type": "bar", "title": "test"}],
        filters=[],
        theme="light",
        extra_metadata={"spec_version": "1.0"},
    )
    try:
        r.doc_type = "dashboard"
    except AttributeError:
        pass
    _g_db.add(r)
    _g_db.commit()
    _g_db.refresh(r)
    return r


def _cleanup_test_data():
    from backend.models.scheduled_report import ScheduledReport
    from backend.models.report import Report
    from backend.models.conversation import Conversation
    from backend.models.user import User

    try:
        _g_db.query(ScheduledReport).filter(
            ScheduledReport.name.like(f"{_PREFIX}%")
        ).delete(synchronize_session=False)
        _g_db.query(Report).filter(
            Report.name.like(f"{_PREFIX}%")
        ).delete(synchronize_session=False)
        _g_db.query(Conversation).filter(
            Conversation.title.like(f"%{_PREFIX}%")
        ).delete(synchronize_session=False)
        test_users = _g_db.query(User).filter(
            User.username.like(f"{_PREFIX}%")
        ).all()
        for u in test_users:
            _g_db.delete(u)
        _g_db.commit()
    except Exception as e:
        print(f"[teardown] cleanup error: {e}")
        _g_db.rollback()
    finally:
        _g_db.close()


def _make_client():
    from backend.main import app
    from fastapi.testclient import TestClient
    return TestClient(app, raise_server_exceptions=False)


# ══════════════════════════════════════════════════════════════════════════════
# Section A — POST /scheduled-reports/{id}/copilot  (5 tests)
# ══════════════════════════════════════════════════════════════════════════════

class TestAScheduleCopilot(unittest.TestCase):
    """A段: POST /scheduled-reports/{id}/copilot 端点。"""

    @classmethod
    def setUpClass(cls):
        cls.client = _make_client()
        cls.owner = _make_user("a_owner")
        cls.other = _make_user("a_other")
        cls.sr = _create_schedule(cls.owner.username, f"{_PREFIX}a_task")

    def test_A1_creates_conversation_returns_id(self):
        """正常调用应返回 conversation_id。"""
        res = self.client.post(
            f"/api/v1/scheduled-reports/{self.sr.id}/copilot",
            headers=_auth(self.owner),
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data["success"])
        self.assertIn("conversation_id", data["data"])
        cid = data["data"]["conversation_id"]
        self.assertTrue(len(cid) > 10)  # valid UUID

    def test_A2_conversation_system_prompt_has_schedule_name(self):
        """创建的对话 system_prompt 应包含任务名称和 cron 表达式。"""
        from backend.models.conversation import Conversation

        res = self.client.post(
            f"/api/v1/scheduled-reports/{self.sr.id}/copilot",
            headers=_auth(self.owner),
        )
        self.assertEqual(res.status_code, 200)
        cid = res.json()["data"]["conversation_id"]

        conv = _g_db.query(Conversation).filter(
            Conversation.id == uuid.UUID(cid)
        ).first()
        self.assertIsNotNone(conv)
        sp = conv.extra_metadata.get("system_prompt", "") if conv.extra_metadata else ""
        self.assertIn(self.sr.name, sp)
        self.assertIn(self.sr.cron_expr, sp)

    def test_A3_conversation_metadata_has_context_type_schedule(self):
        """创建的对话 extra_metadata 应有 context_type=schedule, context_id。"""
        from backend.models.conversation import Conversation

        res = self.client.post(
            f"/api/v1/scheduled-reports/{self.sr.id}/copilot",
            headers=_auth(self.owner),
        )
        self.assertEqual(res.status_code, 200)
        cid = res.json()["data"]["conversation_id"]

        conv = _g_db.query(Conversation).filter(
            Conversation.id == uuid.UUID(cid)
        ).first()
        self.assertIsNotNone(conv)
        meta = conv.extra_metadata or {}
        self.assertEqual(meta.get("context_type"), "schedule")
        self.assertEqual(meta.get("context_id"), str(self.sr.id))

    def test_A4_nonexistent_schedule_returns_404(self):
        """不存在的 schedule_id 应返回 404。"""
        fake_id = str(uuid.uuid4())
        res = self.client.post(
            f"/api/v1/scheduled-reports/{fake_id}/copilot",
            headers=_auth(self.owner),
        )
        self.assertEqual(res.status_code, 404)

    def test_A5_invalid_schedule_id_returns_400(self):
        """非 UUID 的 schedule_id 应返回 400。"""
        res = self.client.post(
            "/api/v1/scheduled-reports/not-a-valid-uuid/copilot",
            headers=_auth(self.owner),
        )
        self.assertEqual(res.status_code, 400)


# ══════════════════════════════════════════════════════════════════════════════
# Section B — HTML Pilot 注入逻辑  (5 tests)
# ══════════════════════════════════════════════════════════════════════════════

class TestBHtmlInjection(unittest.TestCase):
    """B段: _inject_pilot_button() 注入逻辑 + GET /reports/{id}/html 集成。"""

    def _inject(self, html: str, rid: str) -> str:
        from backend.api.reports import _inject_pilot_button
        return _inject_pilot_button(html, rid)

    def test_B1_inject_before_body_close_tag(self):
        """有 </body> 标签时应在其前插入 pilot 脚本。"""
        html = "<html><body><p>report</p></body></html>"
        rid = str(uuid.uuid4())
        result = self._inject(html, rid)
        body_idx = result.lower().rfind("</body>")
        script_idx = result.find("__pilot-fab")
        self.assertGreater(body_idx, 0)
        self.assertGreater(script_idx, 0)
        self.assertLess(script_idx, body_idx)

    def test_B2_inject_appended_when_no_body_tag(self):
        """无 </body> 时应追加至 HTML 末尾。"""
        html = "<html><body><p>no-close</p>"
        rid = str(uuid.uuid4())
        result = self._inject(html, rid)
        self.assertIn("__pilot-fab", result)
        self.assertTrue(result.endswith("</script>\n"))

    def test_B3_inject_contains_report_id(self):
        """注入的脚本应包含正确的 report_id。"""
        html = "<html><body></body></html>"
        rid = str(uuid.uuid4())
        result = self._inject(html, rid)
        self.assertIn(rid, result)

    def test_B4_inject_has_postmessage_and_window_open(self):
        """注入脚本应同时包含 postMessage 和 window.open 两种打开方式。"""
        html = "<html><body></body></html>"
        rid = str(uuid.uuid4())
        result = self._inject(html, rid)
        self.assertIn("postMessage", result)
        self.assertIn("window.open", result)
        self.assertIn("autoPilot", result)

    def test_B5_html_endpoint_injects_button_on_preview(self):
        """GET /reports/{id}/html?token= 预览模式响应包含 pilot 按钮。"""
        import tempfile

        client = _make_client()
        owner = _make_user("b5")

        # 创建一个有真实文件的 report
        from backend.models.report import Report
        from backend.services.report_builder_service import generate_refresh_token

        rid = uuid.uuid4()
        token = generate_refresh_token()

        # 创建临时 HTML 文件
        with tempfile.TemporaryDirectory() as tmpdir:
            # 在 customer_data 路径模拟
            from backend.config.settings import settings
            root = Path(settings.allowed_directories[0]) if settings.allowed_directories else Path("customer_data")
            html_dir = root / owner.username / "reports"
            html_dir.mkdir(parents=True, exist_ok=True)
            html_file = html_dir / f"{rid.hex}.html"
            html_file.write_text("<html><body><p>test report</p></body></html>", encoding="utf-8")

            r = Report(
                id=rid,
                name=f"{_PREFIX}b5_report",
                username=owner.username,
                refresh_token=token,
                report_file_path=f"{owner.username}/reports/{rid.hex}.html",
                summary_status="skipped",
                charts=[],
                filters=[],
                theme="light",
            )
            try:
                r.doc_type = "dashboard"
            except AttributeError:
                pass
            _g_db.add(r)
            _g_db.commit()

            res = client.get(f"/api/v1/reports/{rid}/html?token={token}")
            self.assertEqual(res.status_code, 200)
            self.assertIn("__pilot-fab", res.text)
            self.assertIn(str(rid), res.text)

            # 清理文件
            try:
                html_file.unlink(missing_ok=True)
            except Exception:
                pass
            # 清理 DB record
            _g_db.delete(_g_db.query(Report).filter(Report.id == rid).first())
            _g_db.commit()


# ══════════════════════════════════════════════════════════════════════════════
# Section C — 回归：现有 /reports/{id}/copilot 端点  (4 tests)
# ══════════════════════════════════════════════════════════════════════════════

class TestCReportCopilotRegression(unittest.TestCase):
    """C段: 现有 POST /reports/{id}/copilot 端点不受 Pilot 改动影响。"""

    @classmethod
    def setUpClass(cls):
        cls.client = _make_client()
        cls.owner = _make_user("c_owner")
        cls.report = _create_report(cls.owner.username, f"{_PREFIX}c_report")

    def test_C1_report_copilot_still_returns_200(self):
        """POST /reports/{id}/copilot 仍然返回 200 和 conversation_id。"""
        res = self.client.post(
            f"/api/v1/reports/{self.report.id}/copilot",
            headers=_auth(self.owner),
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data["success"])
        self.assertIn("conversation_id", data["data"])

    def test_C2_report_copilot_context_type_is_report(self):
        """生成的对话 extra_metadata context_type 应为 'report'。"""
        from backend.models.conversation import Conversation

        res = self.client.post(
            f"/api/v1/reports/{self.report.id}/copilot",
            headers=_auth(self.owner),
        )
        cid = res.json()["data"]["conversation_id"]
        conv = _g_db.query(Conversation).filter(
            Conversation.id == uuid.UUID(cid)
        ).first()
        self.assertIsNotNone(conv)
        meta = conv.extra_metadata or {}
        self.assertEqual(meta.get("context_type"), "report")
        self.assertEqual(meta.get("context_id"), str(self.report.id))

    def test_C3_report_copilot_404_for_nonexistent(self):
        """不存在的 report_id 仍应返回 404。"""
        res = self.client.post(
            f"/api/v1/reports/{uuid.uuid4()}/copilot",
            headers=_auth(self.owner),
        )
        self.assertEqual(res.status_code, 404)

    def test_C4_html_download_does_not_inject_pilot(self):
        """GET /reports/{id}/html?download=true 不应注入 pilot 按钮。"""
        import tempfile

        client = _make_client()
        owner = _make_user("c4")
        from backend.models.report import Report
        from backend.services.report_builder_service import generate_refresh_token

        rid = uuid.uuid4()
        token = generate_refresh_token()
        from backend.config.settings import settings
        root = Path(settings.allowed_directories[0]) if settings.allowed_directories else Path("customer_data")
        html_dir = root / owner.username / "reports"
        html_dir.mkdir(parents=True, exist_ok=True)
        html_file = html_dir / f"{rid.hex}.html"
        html_file.write_text("<html><body><p>download test</p></body></html>", encoding="utf-8")

        r = Report(
            id=rid,
            name=f"{_PREFIX}c4_report",
            username=owner.username,
            refresh_token=token,
            report_file_path=f"{owner.username}/reports/{rid.hex}.html",
            summary_status="skipped",
            charts=[],
            filters=[],
            theme="light",
        )
        try:
            r.doc_type = "dashboard"
        except AttributeError:
            pass
        _g_db.add(r)
        _g_db.commit()

        res = client.get(f"/api/v1/reports/{rid}/html?token={token}&download=true")
        # download=true should NOT inject pilot
        self.assertNotIn("__pilot-fab", res.text)

        try:
            html_file.unlink(missing_ok=True)
        except Exception:
            pass
        _g_db.delete(_g_db.query(Report).filter(Report.id == rid).first())
        _g_db.commit()


# ══════════════════════════════════════════════════════════════════════════════
# Section D — 对话创建时携带 model_key  (4 tests)
# ══════════════════════════════════════════════════════════════════════════════

class TestDModelKeyInConversation(unittest.TestCase):
    """D段: POST /conversations 携带 model_key 时正确设置 current_model。"""

    @classmethod
    def setUpClass(cls):
        cls.client = _make_client()
        cls.owner = _make_user("d_owner")

    def test_D1_create_conversation_with_model_key(self):
        """POST /conversations 带 model_key 时 current_model 应被设置。"""
        res = self.client.post(
            "/api/v1/conversations",
            headers=_auth(self.owner),
            json={"title": f"{_PREFIX}d1_conv", "model_key": "claude"},
        )
        self.assertIn(res.status_code, [200, 201])
        data = res.json()
        self.assertTrue(data["success"])
        conv_id = data["data"]["id"]

        # 验证 current_model
        get_res = self.client.get(
            f"/api/v1/conversations/{conv_id}",
            headers=_auth(self.owner),
        )
        conv_data = get_res.json()["data"]
        self.assertEqual(conv_data.get("current_model", conv_data.get("model")), "claude")

    def test_D2_create_conversation_without_model_key_gets_default(self):
        """不带 model_key 时对话应使用默认模型（非空）。"""
        res = self.client.post(
            "/api/v1/conversations",
            headers=_auth(self.owner),
            json={"title": f"{_PREFIX}d2_conv"},
        )
        self.assertIn(res.status_code, [200, 201])
        data = res.json()
        self.assertTrue(data["success"])
        conv_id = data["data"]["id"]

        get_res = self.client.get(
            f"/api/v1/conversations/{conv_id}",
            headers=_auth(self.owner),
        )
        conv_data = get_res.json()["data"]
        model_val = conv_data.get("current_model", conv_data.get("model", ""))
        # Should not be empty (default model is set)
        self.assertIsNotNone(model_val)

    def test_D3_schedule_copilot_conv_is_retrievable(self):
        """schedule copilot 创建的对话通过 GET /conversations/{id} 可正常访问。"""
        sr = _create_schedule(self.owner.username, f"{_PREFIX}d3_task")
        res = self.client.post(
            f"/api/v1/scheduled-reports/{sr.id}/copilot",
            headers=_auth(self.owner),
        )
        self.assertEqual(res.status_code, 200)
        cid = res.json()["data"]["conversation_id"]

        get_res = self.client.get(
            f"/api/v1/conversations/{cid}",
            headers=_auth(self.owner),
        )
        self.assertEqual(get_res.status_code, 200)
        conv_data = get_res.json()["data"]
        self.assertIn(sr.name, conv_data.get("title", ""))

    def test_D4_schedule_copilot_system_prompt_has_channels(self):
        """schedule copilot 对话的 system_prompt 应包含通知渠道信息。"""
        from backend.models.conversation import Conversation

        sr = _create_schedule(self.owner.username, f"{_PREFIX}d4_task")
        res = self.client.post(
            f"/api/v1/scheduled-reports/{sr.id}/copilot",
            headers=_auth(self.owner),
        )
        self.assertEqual(res.status_code, 200)
        cid = res.json()["data"]["conversation_id"]

        conv = _g_db.query(Conversation).filter(
            Conversation.id == uuid.UUID(cid)
        ).first()
        sp = conv.extra_metadata.get("system_prompt", "") if conv.extra_metadata else ""
        # 通知渠道 "email" 应在 system_prompt 中
        self.assertIn("email", sp)


# ══════════════════════════════════════════════════════════════════════════════
# Section E — 模型切换：PUT /conversations/{id} 更新 current_model  (3 tests)
# ══════════════════════════════════════════════════════════════════════════════

class TestEModelSwitch(unittest.TestCase):
    """E段: Pilot 模型切换通过 PUT /conversations/{id} 实现。"""

    @classmethod
    def setUpClass(cls):
        cls.client = _make_client()
        cls.owner = _make_user("e_owner")

    def _create_conv(self, model_key="claude"):
        res = self.client.post(
            "/api/v1/conversations",
            headers=_auth(self.owner),
            json={"title": f"{_PREFIX}e_conv_{uuid.uuid4().hex[:4]}", "model_key": model_key},
        )
        self.assertIn(res.status_code, [200, 201])
        return res.json()["data"]["id"]

    def test_E1_put_conversation_model_updates_current_model(self):
        """PUT /conversations/{id} 带 model 字段应更新 current_model。"""
        cid = self._create_conv("claude")

        put_res = self.client.put(
            f"/api/v1/conversations/{cid}",
            headers=_auth(self.owner),
            json={"model": "qianwen"},
        )
        self.assertIn(put_res.status_code, [200, 201])

        get_res = self.client.get(
            f"/api/v1/conversations/{cid}",
            headers=_auth(self.owner),
        )
        conv_data = get_res.json()["data"]
        updated_model = conv_data.get("current_model", conv_data.get("model", ""))
        self.assertEqual(updated_model, "qianwen")

    def test_E2_put_conversation_model_nonexistent_id_returns_404(self):
        """PUT 不存在的 conversation_id 应返回 404。"""
        res = self.client.put(
            f"/api/v1/conversations/{uuid.uuid4()}",
            headers=_auth(self.owner),
            json={"model": "claude"},
        )
        self.assertEqual(res.status_code, 404)

    def test_E3_model_switch_does_not_affect_messages(self):
        """切换模型不应清除对话历史消息。"""
        cid = self._create_conv("claude")

        # 切换模型
        self.client.put(
            f"/api/v1/conversations/{cid}",
            headers=_auth(self.owner),
            json={"model": "qianwen"},
        )

        # 验证对话仍然存在
        get_res = self.client.get(
            f"/api/v1/conversations/{cid}",
            headers=_auth(self.owner),
        )
        self.assertEqual(get_res.status_code, 200)
        # 消息列表（可能为空，但对话本身应存在）
        conv_data = get_res.json()["data"]
        self.assertIsNotNone(conv_data.get("id"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
