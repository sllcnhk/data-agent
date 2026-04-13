"""
test_pin_report.py — POST /reports/pin 端点测试套件
====================================================

Section A (8) — 基础 pin 功能
  A1  正常固定新文件，返回 report_id + refresh_token
  A2  幂等：同一 file_path 再次调用返回已有记录（is_new=False）
  A3  文件不存在返回 404
  A4  路径越界（path traversal）返回 403
  A5  跨用户 pin 他人文件返回 403
  A6  superadmin 可固定任意用户文件
  A7  不传 name 时自动从路径提取文件名（去 .html 扩展名）
  A8  传入自定义 name 时以传入值为准

Section B (3) — doc_type 传递
  B1  不传 doc_type 默认 dashboard
  B2  传 doc_type=document 正确存储
  B3  GET /reports 后 doc_type 可过滤到该记录

Section C (3) — message_id 回写
  C1  传入有效 message_id 后，消息 extra_metadata.files_written 被更新 pinned_report_id
  C2  传入无效 message_id（不存在）不报错，仍返回成功
  C3  刷新消息列表，pinned_report_id 持久化在 DB

总计：14 个测试用例
"""

from __future__ import annotations

import os
import sys
import uuid
import unittest
from pathlib import Path
from unittest.mock import patch

# ── 路径 & 环境初始化 ────────────────────────────────────────────────────────
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "backend"))
os.environ.setdefault("POSTGRES_PASSWORD", "postgres")
os.environ.setdefault("POSTGRES_HOST", "localhost")
os.environ.setdefault("ENABLE_AUTH", "False")

from test_utils import make_test_username  # noqa: E402

_PREFIX = f"_pin_{uuid.uuid4().hex[:6]}_"

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


def _make_user(suffix="", is_superadmin=False):
    from backend.models.user import User
    from backend.models.role import Role
    from backend.models.user_role import UserRole
    from backend.core.auth.password import hash_password

    username = f"{_PREFIX}{suffix or uuid.uuid4().hex[:6]}"
    u = User(
        username=username,
        display_name=f"Pin Test {suffix}",
        hashed_password=hash_password("Test1234!"),
        auth_source="local",
        is_active=True,
        is_superadmin=is_superadmin,
    )
    _g_db.add(u)
    _g_db.flush()
    role_name = "analyst"
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
        settings.jwt_secret, settings.jwt_algorithm,
    )


def _auth(user):
    return {"Authorization": f"Bearer {_token(user)}"}


def _create_html_file(username: str, filename: str = None) -> str:
    """在 customer_data/{username}/reports/ 下创建一个测试 HTML 文件，返回相对路径。"""
    from backend.api.reports import _CUSTOMER_DATA_ROOT
    fname = filename or f"{_PREFIX}rpt_{uuid.uuid4().hex[:6]}.html"
    report_dir = _CUSTOMER_DATA_ROOT / username / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    html_path = report_dir / fname
    html_path.write_text("<html><body>test report</body></html>", encoding="utf-8")
    return str(html_path.relative_to(_CUSTOMER_DATA_ROOT))


def _create_html_file_with_summary(username: str, filename: str = None) -> str:
    """创建含 class="summary-section" 的 HTML 文件（document 类型）。"""
    from backend.api.reports import _CUSTOMER_DATA_ROOT
    fname = filename or f"{_PREFIX}doc_{uuid.uuid4().hex[:6]}.html"
    report_dir = _CUSTOMER_DATA_ROOT / username / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    html_path = report_dir / fname
    html_path.write_text(
        '<html><body><div class="summary-section">summary</div></body></html>',
        encoding="utf-8"
    )
    return str(html_path.relative_to(_CUSTOMER_DATA_ROOT))


def _create_message(conversation_id, username: str, files_written: list = None):
    """在 DB 中创建一条 assistant 消息，返回 Message 对象。"""
    from backend.models.conversation import Message
    msg = Message(
        id=uuid.uuid4(),
        conversation_id=conversation_id,
        role="assistant",
        content="test assistant message",
        extra_metadata={"files_written": files_written or []},
    )
    _g_db.add(msg)
    _g_db.commit()
    _g_db.refresh(msg)
    return msg


def _create_conversation(username: str):
    """在 DB 中创建一个对话，返回 Conversation 对象。"""
    from backend.models.conversation import Conversation
    conv = Conversation(
        id=uuid.uuid4(),
        title=f"{_PREFIX}conv",
        current_model="claude",
        status="active",
    )
    _g_db.add(conv)
    _g_db.commit()
    _g_db.refresh(conv)
    return conv


def _cleanup_test_data():
    from backend.models.user import User
    from backend.models.report import Report
    from backend.models.conversation import Conversation, Message
    from backend.api.reports import _CUSTOMER_DATA_ROOT
    import shutil

    try:
        # 清理 DB: reports
        _g_db.query(Report).filter(
            Report.username.like(f"{_PREFIX}%")
        ).delete(synchronize_session=False)
        _g_db.query(Report).filter(
            Report.name.like(f"{_PREFIX}%")
        ).delete(synchronize_session=False)
        # 清理 DB: conversations + messages
        test_convs = _g_db.query(Conversation).filter(
            Conversation.title.like(f"{_PREFIX}%")
        ).all()
        for conv in test_convs:
            _g_db.query(Message).filter(
                Message.conversation_id == conv.id
            ).delete(synchronize_session=False)
            _g_db.delete(conv)
        # 清理 DB: users
        test_users = _g_db.query(User).filter(
            User.username.like(f"{_PREFIX}%")
        ).all()
        for u in test_users:
            _g_db.delete(u)
        _g_db.commit()
    except Exception as e:
        print(f"[teardown] cleanup error: {e}")
        _g_db.rollback()

    # 清理文件系统：删除测试 HTML 文件
    for user_dir in _CUSTOMER_DATA_ROOT.iterdir():
        if user_dir.is_dir() and user_dir.name.startswith(_PREFIX):
            try:
                shutil.rmtree(user_dir)
            except Exception:
                pass

    try:
        _g_db.close()
    except Exception:
        pass


# ── TestClient 工厂 ───────────────────────────────────────────────────────────

def _make_client():
    from backend.main import app
    from fastapi.testclient import TestClient
    return TestClient(app, raise_server_exceptions=False)


# ══════════════════════════════════════════════════════════════════════════════
# Section A — 基础 pin 功能（8 tests）
# ══════════════════════════════════════════════════════════════════════════════

class TestAPinBasics(unittest.TestCase):
    """A: POST /reports/pin 基础行为"""

    @classmethod
    def setUpClass(cls):
        cls.client = _make_client()
        cls.user = _make_user("a")
        cls.headers = _auth(cls.user)
        cls.fp = _create_html_file(cls.user.username)

    def test_A1_pin_new_file_returns_report_id_and_token(self):
        """A1: 正常固定新文件，返回 report_id + refresh_token + is_new=True"""
        fp = _create_html_file(self.user.username)
        res = self.client.post(
            "/api/v1/reports/pin",
            json={"file_path": fp, "doc_type": "dashboard"},
            headers=self.headers,
        )
        self.assertEqual(res.status_code, 200, res.text)
        data = res.json()["data"]
        self.assertTrue(data["is_new"])
        self.assertTrue(len(data["report_id"]) > 0)
        self.assertTrue(len(data["refresh_token"]) > 0)

    def test_A2_idempotent_second_call_returns_is_new_false(self):
        """A2: 幂等——同一 file_path 再次调用，返回已有记录（is_new=False）"""
        fp = _create_html_file(self.user.username)
        body = {"file_path": fp, "doc_type": "dashboard"}

        r1 = self.client.post("/api/v1/reports/pin", json=body, headers=self.headers)
        self.assertEqual(r1.status_code, 200)
        id1 = r1.json()["data"]["report_id"]

        r2 = self.client.post("/api/v1/reports/pin", json=body, headers=self.headers)
        self.assertEqual(r2.status_code, 200)
        data2 = r2.json()["data"]
        self.assertFalse(data2["is_new"])
        self.assertEqual(data2["report_id"], id1)

    def test_A3_file_not_found_returns_404(self):
        """A3: 文件不存在返回 404"""
        res = self.client.post(
            "/api/v1/reports/pin",
            json={"file_path": f"{self.user.username}/reports/nonexistent_{uuid.uuid4().hex}.html"},
            headers=self.headers,
        )
        self.assertEqual(res.status_code, 404)

    def test_A4_path_traversal_returns_403(self):
        """A4: 路径越界（../）返回 403"""
        res = self.client.post(
            "/api/v1/reports/pin",
            json={"file_path": "../../etc/passwd"},
            headers=self.headers,
        )
        self.assertEqual(res.status_code, 403)

    def test_A5_cross_user_pin_returns_403(self):
        """A5: 普通用户无法固定他人文件（需 enable_auth=True）"""
        from backend.config.settings import settings
        other = _make_user("a_other")
        fp = _create_html_file(other.username)
        # 临时开启 auth，验证跨用户隔离
        with patch.object(settings, "enable_auth", True):
            client = _make_client()
            res = client.post(
                "/api/v1/reports/pin",
                json={"file_path": fp, "doc_type": "dashboard"},
                headers=self.headers,
            )
        self.assertEqual(res.status_code, 403)

    def test_A6_superadmin_can_pin_any_user_file(self):
        """A6: superadmin 可固定任意用户的文件"""
        admin = _make_user("a_admin", is_superadmin=True)
        other = _make_user("a_target")
        fp = _create_html_file(other.username)
        res = self.client.post(
            "/api/v1/reports/pin",
            json={"file_path": fp, "doc_type": "dashboard"},
            headers=_auth(admin),
        )
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json()["data"]["is_new"])

    def test_A7_name_auto_extracted_from_path(self):
        """A7: 不传 name 时，报告名称从文件名提取（去 .html）"""
        fname = f"{_PREFIX}myreport.html"
        fp = _create_html_file(self.user.username, fname)
        res = self.client.post(
            "/api/v1/reports/pin",
            json={"file_path": fp},
            headers=self.headers,
        )
        self.assertEqual(res.status_code, 200)
        report_id = res.json()["data"]["report_id"]
        # 查 DB 验证名称
        from backend.models.report import Report
        r = _g_db.query(Report).filter(Report.id == uuid.UUID(report_id)).first()
        self.assertIsNotNone(r)
        self.assertIn(_PREFIX, r.name)
        self.assertNotIn(".html", r.name)

    def test_A8_custom_name_used_when_provided(self):
        """A8: 传入自定义 name 时，报告名称以传入值为准"""
        fp = _create_html_file(self.user.username)
        custom_name = f"{_PREFIX}custom_name"
        res = self.client.post(
            "/api/v1/reports/pin",
            json={"file_path": fp, "name": custom_name},
            headers=self.headers,
        )
        self.assertEqual(res.status_code, 200)
        report_id = res.json()["data"]["report_id"]
        from backend.models.report import Report
        r = _g_db.query(Report).filter(Report.id == uuid.UUID(report_id)).first()
        self.assertEqual(r.name, custom_name)


# ══════════════════════════════════════════════════════════════════════════════
# Section B — doc_type 传递（3 tests）
# ══════════════════════════════════════════════════════════════════════════════

class TestBDocType(unittest.TestCase):
    """B: doc_type 字段正确传递与存储"""

    @classmethod
    def setUpClass(cls):
        cls.client = _make_client()
        cls.user = _make_user("b")
        cls.headers = _auth(cls.user)

    def test_B1_default_doc_type_is_dashboard(self):
        """B1: 不传 doc_type 默认为 dashboard"""
        fp = _create_html_file(self.user.username)
        res = self.client.post(
            "/api/v1/reports/pin",
            json={"file_path": fp},
            headers=self.headers,
        )
        self.assertEqual(res.status_code, 200)
        d = res.json()["data"]
        self.assertEqual(d["doc_type"], "dashboard")

    def test_B2_document_doc_type_stored_correctly(self):
        """B2: 传 doc_type=document 正确存储到数据库"""
        fp = _create_html_file_with_summary(self.user.username)
        res = self.client.post(
            "/api/v1/reports/pin",
            json={"file_path": fp, "doc_type": "document"},
            headers=self.headers,
        )
        self.assertEqual(res.status_code, 200)
        d = res.json()["data"]
        self.assertEqual(d["doc_type"], "document")
        # 验证 DB 中的 doc_type
        from backend.models.report import Report
        r = _g_db.query(Report).filter(Report.id == uuid.UUID(d["report_id"])).first()
        if r and hasattr(r, "doc_type"):
            self.assertEqual(r.doc_type, "document")

    def test_B3_list_reports_can_filter_by_doc_type(self):
        """B3: 固定后 GET /reports?doc_type= 可过滤到该记录"""
        fp = _create_html_file(self.user.username)
        res_pin = self.client.post(
            "/api/v1/reports/pin",
            json={"file_path": fp, "doc_type": "dashboard"},
            headers=self.headers,
        )
        self.assertEqual(res_pin.status_code, 200)
        pinned_id = res_pin.json()["data"]["report_id"]

        res_list = self.client.get(
            "/api/v1/reports?doc_type=dashboard",
            headers=self.headers,
        )
        self.assertEqual(res_list.status_code, 200)
        items = res_list.json()["data"]["items"]
        ids = [item["id"] for item in items]
        self.assertIn(pinned_id, ids)


# ══════════════════════════════════════════════════════════════════════════════
# Section C — message_id 回写（3 tests）
# ══════════════════════════════════════════════════════════════════════════════

class TestCMessageWriteback(unittest.TestCase):
    """C: message_id 传入后，消息 extra_metadata 中的 files_written 被回写 pinned_report_id"""

    @classmethod
    def setUpClass(cls):
        cls.client = _make_client()
        cls.user = _make_user("c")
        cls.headers = _auth(cls.user)
        cls.conv = _create_conversation(cls.user.username)

    def _make_msg_with_file(self, fp: str):
        """创建带 files_written 的消息。"""
        return _create_message(
            self.conv.id,
            self.user.username,
            files_written=[{"path": fp, "name": "test.html", "size": 100, "mime_type": "text/html", "is_report": True}],
        )

    def test_C1_pin_with_message_id_writes_back_pinned_report_id(self):
        """C1: 传入有效 message_id 后，消息 extra_metadata.files_written 被更新 pinned_report_id"""
        fp = _create_html_file(self.user.username)
        msg = self._make_msg_with_file(fp)

        res = self.client.post(
            "/api/v1/reports/pin",
            json={
                "file_path": fp,
                "doc_type": "dashboard",
                "conversation_id": str(self.conv.id),
                "message_id": str(msg.id),
            },
            headers=self.headers,
        )
        self.assertEqual(res.status_code, 200)
        report_id = res.json()["data"]["report_id"]

        # 查 DB 验证回写
        from backend.models.conversation import Message
        _g_db.refresh(msg)
        files_written = msg.extra_metadata.get("files_written", [])
        self.assertEqual(len(files_written), 1)
        self.assertEqual(files_written[0].get("pinned_report_id"), report_id)
        self.assertTrue(len(files_written[0].get("refresh_token", "")) > 0)

    def test_C2_invalid_message_id_does_not_cause_error(self):
        """C2: 传入不存在的 message_id 不报错，仍返回成功"""
        fp = _create_html_file(self.user.username)
        fake_msg_id = str(uuid.uuid4())

        res = self.client.post(
            "/api/v1/reports/pin",
            json={
                "file_path": fp,
                "doc_type": "dashboard",
                "message_id": fake_msg_id,
            },
            headers=self.headers,
        )
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json()["data"]["is_new"])

    def test_C3_pinned_report_id_persists_in_db(self):
        """C3: pinned_report_id 持久化在消息 DB 中，重新查询仍存在"""
        fp = _create_html_file(self.user.username)
        msg = self._make_msg_with_file(fp)

        res = self.client.post(
            "/api/v1/reports/pin",
            json={
                "file_path": fp,
                "doc_type": "dashboard",
                "message_id": str(msg.id),
            },
            headers=self.headers,
        )
        self.assertEqual(res.status_code, 200)
        report_id = res.json()["data"]["report_id"]

        # 重新从 DB 查询（确保是持久化的，不是缓存）
        from backend.config.database import SessionLocal
        fresh_db = SessionLocal()
        try:
            from backend.models.conversation import Message as Msg
            fresh_msg = fresh_db.query(Msg).filter(Msg.id == msg.id).first()
            self.assertIsNotNone(fresh_msg)
            files_written = (fresh_msg.extra_metadata or {}).get("files_written", [])
            self.assertEqual(len(files_written), 1)
            self.assertEqual(files_written[0].get("pinned_report_id"), report_id)
        finally:
            fresh_db.close()


# ══════════════════════════════════════════════════════════════════════════════
# 入口
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    unittest.main(verbosity=2)
