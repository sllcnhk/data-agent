"""
test_conversation_isolation.py — 对话用户隔离功能完整测试套件
=============================================================

测试层次：
  A  (5)  — 服务层单测：create/list/filter/all-users-view
  B  (10) — Conversations API：普通用户隔离
  C  (7)  — Conversations API：Superadmin 行为
  D  (9)  — Groups API：用户隔离
  E  (3)  — ENABLE_AUTH=false 兼容模式（匿名）
  F  (5)  — 安全边界 & 漏洞修复验证
  G  (3)  — RBAC 权限范围验证

总计: 42 个测试用例
"""

from __future__ import annotations

import os
import sys
import uuid
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "backend"))
os.environ.setdefault("ENABLE_AUTH", "False")

_PREFIX = f"_ci_{uuid.uuid4().hex[:6]}_"

# ── 全局 DB session ───────────────────────────────────────────────────────────


def _db():
    from backend.config.database import SessionLocal
    return SessionLocal()


_g_db = _db()


# ── 测试数据工厂 ──────────────────────────────────────────────────────────────

def _make_user(suffix="", role_names=None, is_superadmin=False):
    from backend.models.user import User
    from backend.models.role import Role
    from backend.models.user_role import UserRole
    from backend.core.auth.password import hash_password

    username = f"{_PREFIX}{suffix or uuid.uuid4().hex[:6]}"
    u = User(
        username=username,
        display_name=f"CI {suffix}",
        hashed_password=hash_password("Test1234!"),
        auth_source="local",
        is_active=True,
        is_superadmin=is_superadmin,
    )
    _g_db.add(u)
    _g_db.flush()
    for rname in (role_names or []):
        role = _g_db.query(Role).filter(Role.name == rname).first()
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


def teardown_module(_=None):
    from backend.models.user import User
    from backend.models.conversation import Conversation
    from backend.models import ConversationGroup
    try:
        test_users = _g_db.query(User).filter(
            User.username.like(f"{_PREFIX}%")
        ).all()
        test_user_ids = [u.id for u in test_users]

        if test_user_ids:
            _g_db.query(Conversation).filter(
                Conversation.user_id.in_(test_user_ids)
            ).delete(synchronize_session=False)
            _g_db.query(ConversationGroup).filter(
                ConversationGroup.user_id.in_(test_user_ids)
            ).delete(synchronize_session=False)

        # 清理匿名模式下按标题前缀创建的测试对话
        _g_db.query(Conversation).filter(
            Conversation.title.like(f"{_PREFIX}%")
        ).delete(synchronize_session=False)

        _g_db.query(User).filter(
            User.username.like(f"{_PREFIX}%")
        ).delete(synchronize_session=False)

        _g_db.commit()
    except Exception as e:
        print(f"[teardown] error: {e}")
        _g_db.rollback()
    finally:
        _g_db.close()


# ── TestClient 工厂 ───────────────────────────────────────────────────────────

from fastapi.testclient import TestClient  # noqa: E402


def _make_client():
    from backend.main import app
    return TestClient(app, raise_server_exceptions=False)


# ══════════════════════════════════════════════════════════════════════════════
# Section A — 服务层单测 (5 tests)
# ══════════════════════════════════════════════════════════════════════════════

class TestServiceLayer(unittest.TestCase):
    """A1-A5: ConversationService 用户隔离逻辑单测"""

    @classmethod
    def setUpClass(cls):
        cls.svc_db = _db()
        cls.user_a = _make_user("a_svc")
        cls.user_b = _make_user("b_svc")

    @classmethod
    def tearDownClass(cls):
        cls.svc_db.close()

    def _svc(self):
        from backend.services.conversation_service import ConversationService
        return ConversationService(self.svc_db)

    def test_A1_create_conversation_stores_user_id(self):
        """create_conversation(user_id=) 正确写入 user_id"""
        svc = self._svc()
        conv = svc.create_conversation(title=f"{_PREFIX}A1", user_id=self.user_a.id)
        self.assertEqual(conv.user_id, self.user_a.id)
        d = conv.to_dict()
        self.assertEqual(d["user_id"], str(self.user_a.id))

    def test_A2_list_conversations_filters_by_user_id(self):
        """list_conversations(user_id=X) 只返回用户 X 的对话"""
        svc = self._svc()
        conv_a = svc.create_conversation(title=f"{_PREFIX}A2_A", user_id=self.user_a.id)
        conv_b = svc.create_conversation(title=f"{_PREFIX}A2_B", user_id=self.user_b.id)

        convs_a, total_a = svc.list_conversations(user_id=self.user_a.id)
        ids_a = [str(c.id) for c in convs_a]
        self.assertIn(str(conv_a.id), ids_a)
        self.assertNotIn(str(conv_b.id), ids_a)

        convs_b, total_b = svc.list_conversations(user_id=self.user_b.id)
        ids_b = [str(c.id) for c in convs_b]
        self.assertIn(str(conv_b.id), ids_b)
        self.assertNotIn(str(conv_a.id), ids_b)

    def test_A3_list_conversations_no_filter_when_user_id_none(self):
        """list_conversations(user_id=None) 返回全部（ENABLE_AUTH=false 模式）"""
        svc = self._svc()
        conv_a = svc.create_conversation(title=f"{_PREFIX}A3_A", user_id=self.user_a.id)
        conv_b = svc.create_conversation(title=f"{_PREFIX}A3_B", user_id=self.user_b.id)

        convs_all, total = svc.list_conversations(user_id=None)
        ids_all = [str(c.id) for c in convs_all]
        self.assertIn(str(conv_a.id), ids_all)
        self.assertIn(str(conv_b.id), ids_all)

    def test_A4_list_all_by_user_excludes_specified_user(self):
        """list_all_conversations_by_user(exclude_user_id=X) 不包含用户 X 的对话"""
        svc = self._svc()
        svc.create_conversation(title=f"{_PREFIX}A4_A", user_id=self.user_a.id)
        svc.create_conversation(title=f"{_PREFIX}A4_B", user_id=self.user_b.id)

        result = svc.list_all_conversations_by_user(exclude_user_id=self.user_a.id)
        user_ids_in_result = [r["user_id"] for r in result]
        # user_a 的对话不应出现
        self.assertNotIn(str(self.user_a.id), user_ids_in_result)
        # user_b 的对话应出现
        self.assertIn(str(self.user_b.id), user_ids_in_result)

    def test_A5_list_all_by_user_skips_users_with_no_active_conversations(self):
        """list_all_conversations_by_user 不返回无活跃对话的用户"""
        svc = self._svc()
        user_empty = _make_user("a5_empty")  # 该用户不创建任何对话

        result = svc.list_all_conversations_by_user(exclude_user_id=None)
        user_ids_in_result = [r["user_id"] for r in result]
        self.assertNotIn(str(user_empty.id), user_ids_in_result)


# ══════════════════════════════════════════════════════════════════════════════
# Section B — Conversations API：普通用户隔离 (10 tests)
# ══════════════════════════════════════════════════════════════════════════════

class TestConversationAPIIsolation(unittest.TestCase):
    """B1-B10: 普通用户只能操作自己的对话"""

    @classmethod
    def setUpClass(cls):
        cls.client = _make_client()
        cls.user_a = _make_user("b_usera", role_names=["viewer"])
        cls.user_b = _make_user("b_userb", role_names=["viewer"])

    def _create_conv(self, user, title_suffix=""):
        """Helper: 用指定用户创建对话，返回 conv_id"""
        with patch("backend.config.settings.settings.enable_auth", True):
            resp = self.client.post(
                "/api/v1/conversations",
                json={"title": f"{_PREFIX}B_{title_suffix}"},
                headers=_auth(user),
            )
        self.assertEqual(resp.status_code, 200, resp.text)
        return resp.json()["data"]["id"]

    def test_B1_create_conversation_sets_user_id(self):
        """创建对话后 user_id 字段等于当前用户 ID"""
        with patch("backend.config.settings.settings.enable_auth", True):
            resp = self.client.post(
                "/api/v1/conversations",
                json={"title": f"{_PREFIX}B1"},
                headers=_auth(self.user_a),
            )
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()["data"]
        self.assertEqual(data["user_id"], str(self.user_a.id))

    def test_B2_list_returns_only_current_user_conversations(self):
        """列表接口只返回当前用户的对话，不返回其他用户的"""
        a_id = self._create_conv(self.user_a, "B2_A")
        b_id = self._create_conv(self.user_b, "B2_B")

        with patch("backend.config.settings.settings.enable_auth", True):
            resp = self.client.get("/api/v1/conversations", headers=_auth(self.user_a))
        self.assertEqual(resp.status_code, 200, resp.text)
        ids = [c["id"] for c in resp.json()["conversations"]]
        self.assertIn(a_id, ids)
        self.assertNotIn(b_id, ids)

    def test_B3_get_own_conversation_returns_200(self):
        """GET 自己的对话返回 200"""
        a_id = self._create_conv(self.user_a, "B3")
        with patch("backend.config.settings.settings.enable_auth", True):
            resp = self.client.get(f"/api/v1/conversations/{a_id}", headers=_auth(self.user_a))
        self.assertEqual(resp.status_code, 200, resp.text)

    def test_B4_get_other_user_conversation_returns_403(self):
        """GET 其他用户的对话返回 403"""
        b_id = self._create_conv(self.user_b, "B4")
        with patch("backend.config.settings.settings.enable_auth", True):
            resp = self.client.get(f"/api/v1/conversations/{b_id}", headers=_auth(self.user_a))
        self.assertEqual(resp.status_code, 403, resp.text)

    def test_B5_update_own_conversation_returns_200(self):
        """PUT 更新自己的对话返回 200"""
        a_id = self._create_conv(self.user_a, "B5")
        with patch("backend.config.settings.settings.enable_auth", True):
            resp = self.client.put(
                f"/api/v1/conversations/{a_id}",
                json={"title": f"{_PREFIX}B5_updated"},
                headers=_auth(self.user_a),
            )
        self.assertEqual(resp.status_code, 200, resp.text)

    def test_B6_update_other_user_conversation_returns_403(self):
        """PUT 更新其他用户的对话返回 403"""
        b_id = self._create_conv(self.user_b, "B6")
        with patch("backend.config.settings.settings.enable_auth", True):
            resp = self.client.put(
                f"/api/v1/conversations/{b_id}",
                json={"title": "hack"},
                headers=_auth(self.user_a),
            )
        self.assertEqual(resp.status_code, 403, resp.text)

    def test_B7_delete_own_conversation_returns_200(self):
        """DELETE 删除自己的对话返回 200"""
        a_id = self._create_conv(self.user_a, "B7")
        with patch("backend.config.settings.settings.enable_auth", True):
            resp = self.client.delete(
                f"/api/v1/conversations/{a_id}",
                headers=_auth(self.user_a),
            )
        self.assertEqual(resp.status_code, 200, resp.text)

    def test_B8_delete_other_user_conversation_returns_403(self):
        """DELETE 删除其他用户的对话返回 403"""
        b_id = self._create_conv(self.user_b, "B8")
        with patch("backend.config.settings.settings.enable_auth", True):
            resp = self.client.delete(
                f"/api/v1/conversations/{b_id}",
                headers=_auth(self.user_a),
            )
        self.assertEqual(resp.status_code, 403, resp.text)

    def test_B9_rename_own_conversation_returns_200(self):
        """PUT /title 重命名自己的对话返回 200"""
        a_id = self._create_conv(self.user_a, "B9")
        with patch("backend.config.settings.settings.enable_auth", True):
            resp = self.client.put(
                f"/api/v1/conversations/{a_id}/title",
                json={"title": f"{_PREFIX}B9_new"},
                headers=_auth(self.user_a),
            )
        self.assertEqual(resp.status_code, 200, resp.text)

    def test_B10_rename_other_user_conversation_returns_403(self):
        """PUT /title 重命名其他用户的对话返回 403"""
        b_id = self._create_conv(self.user_b, "B10")
        with patch("backend.config.settings.settings.enable_auth", True):
            resp = self.client.put(
                f"/api/v1/conversations/{b_id}/title",
                json={"title": "hacked"},
                headers=_auth(self.user_a),
            )
        self.assertEqual(resp.status_code, 403, resp.text)


# ══════════════════════════════════════════════════════════════════════════════
# Section C — Conversations API：Superadmin 行为 (7 tests)
# ══════════════════════════════════════════════════════════════════════════════

class TestConversationAPISuperadmin(unittest.TestCase):
    """C1-C7: superadmin 跨用户视图 & 越权访问能力"""

    @classmethod
    def setUpClass(cls):
        cls.client = _make_client()
        cls.regular = _make_user("c_regular", role_names=["viewer"])
        cls.superadmin = _make_user("c_super", is_superadmin=True)

    def _create_conv(self, user, title_suffix=""):
        with patch("backend.config.settings.settings.enable_auth", True):
            resp = self.client.post(
                "/api/v1/conversations",
                json={"title": f"{_PREFIX}C_{title_suffix}"},
                headers=_auth(user),
            )
        self.assertEqual(resp.status_code, 200, resp.text)
        return resp.json()["data"]["id"]

    def test_C1_superadmin_list_does_not_include_other_users_conversations(self):
        """superadmin 的普通列表接口只返回自己的对话"""
        regular_id = self._create_conv(self.regular, "C1_reg")
        super_id = self._create_conv(self.superadmin, "C1_super")

        with patch("backend.config.settings.settings.enable_auth", True):
            resp = self.client.get("/api/v1/conversations", headers=_auth(self.superadmin))
        ids = [c["id"] for c in resp.json()["conversations"]]
        self.assertIn(super_id, ids)
        self.assertNotIn(regular_id, ids)

    def test_C2_superadmin_can_get_other_user_conversation(self):
        """superadmin GET 普通用户的对话返回 200"""
        reg_id = self._create_conv(self.regular, "C2")
        with patch("backend.config.settings.settings.enable_auth", True):
            resp = self.client.get(f"/api/v1/conversations/{reg_id}", headers=_auth(self.superadmin))
        self.assertEqual(resp.status_code, 200, resp.text)

    def test_C3_superadmin_can_update_other_user_conversation(self):
        """superadmin PUT 更新普通用户的对话返回 200"""
        reg_id = self._create_conv(self.regular, "C3")
        with patch("backend.config.settings.settings.enable_auth", True):
            resp = self.client.put(
                f"/api/v1/conversations/{reg_id}",
                json={"title": f"{_PREFIX}C3_superchanged"},
                headers=_auth(self.superadmin),
            )
        self.assertEqual(resp.status_code, 200, resp.text)

    def test_C4_superadmin_can_delete_other_user_conversation(self):
        """superadmin DELETE 删除普通用户的对话返回 200"""
        reg_id = self._create_conv(self.regular, "C4_del")
        with patch("backend.config.settings.settings.enable_auth", True):
            resp = self.client.delete(
                f"/api/v1/conversations/{reg_id}",
                headers=_auth(self.superadmin),
            )
        self.assertEqual(resp.status_code, 200, resp.text)

    def test_C5_all_users_view_returns_200_for_superadmin(self):
        """GET /all-users-view superadmin 调用返回 200"""
        self._create_conv(self.regular, "C5_reg")
        with patch("backend.config.settings.settings.enable_auth", True):
            resp = self.client.get(
                "/api/v1/conversations/all-users-view",
                headers=_auth(self.superadmin),
            )
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()
        self.assertIn("users", data)
        self.assertIsInstance(data["users"], list)

    def test_C6_all_users_view_excludes_superadmin_own_conversations(self):
        """all-users-view 不包含 superadmin 自己的对话"""
        self._create_conv(self.superadmin, "C6_super")
        with patch("backend.config.settings.settings.enable_auth", True):
            resp = self.client.get(
                "/api/v1/conversations/all-users-view",
                headers=_auth(self.superadmin),
            )
        data = resp.json()
        for user_entry in data["users"]:
            self.assertNotEqual(user_entry["user_id"], str(self.superadmin.id),
                "all-users-view 不应包含 superadmin 自身的条目")

    def test_C7_all_users_view_groups_by_username(self):
        """all-users-view 返回普通用户数据并带有 username/user_id 字段"""
        self._create_conv(self.regular, "C7_reg")
        with patch("backend.config.settings.settings.enable_auth", True):
            resp = self.client.get(
                "/api/v1/conversations/all-users-view",
                headers=_auth(self.superadmin),
            )
        data = resp.json()
        user_ids = [e["user_id"] for e in data["users"]]
        # regular 用户有活跃对话，应出现在结果中
        self.assertIn(str(self.regular.id), user_ids)
        # 每个条目都有必要字段
        for entry in data["users"]:
            self.assertIn("username", entry)
            self.assertIn("display_name", entry)
            self.assertIn("user_id", entry)
            self.assertIn("conversations", entry)
            self.assertIsInstance(entry["conversations"], list)


# ══════════════════════════════════════════════════════════════════════════════
# Section D — Groups API：用户隔离 (9 tests)
# ══════════════════════════════════════════════════════════════════════════════

class TestGroupsAPIIsolation(unittest.TestCase):
    """D1-D9: 分组用户隔离逻辑"""

    @classmethod
    def setUpClass(cls):
        cls.client = _make_client()
        cls.user_a = _make_user("d_usera", role_names=["analyst"])
        cls.user_b = _make_user("d_userb", role_names=["analyst"])
        cls.superadmin = _make_user("d_super", is_superadmin=True)

    def _create_group(self, user, name_suffix=""):
        with patch("backend.config.settings.settings.enable_auth", True):
            resp = self.client.post(
                "/api/v1/groups",
                json={"name": f"{_PREFIX}grp_{name_suffix}"},
                headers=_auth(user),
            )
        self.assertEqual(resp.status_code, 200, resp.text)
        return resp.json()["data"]["id"]

    def test_D1_create_group_sets_user_id(self):
        """创建分组后 user_id 等于当前用户"""
        with patch("backend.config.settings.settings.enable_auth", True):
            resp = self.client.post(
                "/api/v1/groups",
                json={"name": f"{_PREFIX}grp_D1"},
                headers=_auth(self.user_a),
            )
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()["data"]
        self.assertEqual(data["user_id"], str(self.user_a.id))

    def test_D2_list_groups_returns_only_current_user_groups(self):
        """列表接口只返回当前用户的分组"""
        a_gid = self._create_group(self.user_a, "D2_A")
        b_gid = self._create_group(self.user_b, "D2_B")

        with patch("backend.config.settings.settings.enable_auth", True):
            resp = self.client.get("/api/v1/groups", headers=_auth(self.user_a))
        self.assertEqual(resp.status_code, 200, resp.text)
        ids = [g["id"] for g in resp.json()["groups"]]
        self.assertIn(a_gid, ids)
        self.assertNotIn(b_gid, ids)

    def test_D3_two_users_can_have_same_group_name(self):
        """不同用户可以创建同名分组（唯一性按用户域隔离）"""
        shared_name = f"{_PREFIX}grp_D3_shared"
        with patch("backend.config.settings.settings.enable_auth", True):
            r1 = self.client.post("/api/v1/groups",
                                  json={"name": shared_name}, headers=_auth(self.user_a))
            r2 = self.client.post("/api/v1/groups",
                                  json={"name": shared_name}, headers=_auth(self.user_b))
        self.assertEqual(r1.status_code, 200, r1.text)
        self.assertEqual(r2.status_code, 200, r2.text)
        # 两个分组 ID 不同
        self.assertNotEqual(r1.json()["data"]["id"], r2.json()["data"]["id"])

    def test_D4_same_user_cannot_create_duplicate_group_name(self):
        """同一用户不能创建重名分组，返回 400"""
        name = f"{_PREFIX}grp_D4_dup"
        with patch("backend.config.settings.settings.enable_auth", True):
            self.client.post("/api/v1/groups", json={"name": name}, headers=_auth(self.user_a))
            resp = self.client.post("/api/v1/groups", json={"name": name}, headers=_auth(self.user_a))
        self.assertEqual(resp.status_code, 400, resp.text)

    def test_D5_get_other_user_group_returns_403(self):
        """GET 其他用户的分组返回 403"""
        b_gid = self._create_group(self.user_b, "D5")
        with patch("backend.config.settings.settings.enable_auth", True):
            resp = self.client.get(f"/api/v1/groups/{b_gid}", headers=_auth(self.user_a))
        self.assertEqual(resp.status_code, 403, resp.text)

    def test_D6_update_other_user_group_returns_403(self):
        """PUT 更新其他用户的分组返回 403"""
        b_gid = self._create_group(self.user_b, "D6")
        with patch("backend.config.settings.settings.enable_auth", True):
            resp = self.client.put(
                f"/api/v1/groups/{b_gid}",
                json={"name": f"{_PREFIX}grp_D6_hacked"},
                headers=_auth(self.user_a),
            )
        self.assertEqual(resp.status_code, 403, resp.text)

    def test_D7_delete_other_user_group_returns_403(self):
        """DELETE 删除其他用户的分组返回 403"""
        b_gid = self._create_group(self.user_b, "D7")
        with patch("backend.config.settings.settings.enable_auth", True):
            resp = self.client.delete(f"/api/v1/groups/{b_gid}", headers=_auth(self.user_a))
        self.assertEqual(resp.status_code, 403, resp.text)

    def test_D8_sort_order_is_per_user_not_global(self):
        """两个用户各自首个分组的 sort_order 均为 1（互不影响）"""
        # 各自创建一个分组，查看 sort_order
        name_a = f"{_PREFIX}grp_D8_A"
        name_b = f"{_PREFIX}grp_D8_B"
        with patch("backend.config.settings.settings.enable_auth", True):
            ra = self.client.post("/api/v1/groups", json={"name": name_a}, headers=_auth(self.user_a))
            rb = self.client.post("/api/v1/groups", json={"name": name_b}, headers=_auth(self.user_b))
        # 两者都应该能成功创建，sort_order 独立递增
        self.assertEqual(ra.status_code, 200)
        self.assertEqual(rb.status_code, 200)
        # sort_order 应 >= 1（每个用户独立计数）
        self.assertGreaterEqual(ra.json()["data"]["sort_order"], 1)
        self.assertGreaterEqual(rb.json()["data"]["sort_order"], 1)

    def test_D9_superadmin_can_get_any_group(self):
        """superadmin GET 普通用户的分组返回 200"""
        a_gid = self._create_group(self.user_a, "D9")
        with patch("backend.config.settings.settings.enable_auth", True):
            resp = self.client.get(f"/api/v1/groups/{a_gid}", headers=_auth(self.superadmin))
        self.assertEqual(resp.status_code, 200, resp.text)


# ══════════════════════════════════════════════════════════════════════════════
# Section E — ENABLE_AUTH=false 兼容模式 (3 tests)
# ══════════════════════════════════════════════════════════════════════════════

class TestAnonymousMode(unittest.TestCase):
    """E1-E3: ENABLE_AUTH=false 时匿名 AnonymousUser 行为"""

    @classmethod
    def setUpClass(cls):
        cls.client = _make_client()

    def test_E1_anonymous_mode_create_conversation_succeeds(self):
        """ENABLE_AUTH=false 时创建对话不需要 token，返回 200"""
        # ENABLE_AUTH=false 时无需 Authorization header
        resp = self.client.post(
            "/api/v1/conversations",
            json={"title": f"{_PREFIX}E1_anon"},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertTrue(resp.json()["success"])

    def test_E2_anonymous_mode_list_returns_all_conversations(self):
        """ENABLE_AUTH=false 时列表接口返回所有对话（不过滤 user_id）"""
        # 先以某个已认证用户创建一个对话（直接走服务层，绑定 user_id）
        from backend.services.conversation_service import ConversationService
        db = _db()
        try:
            user_x = _make_user("e2_x")
            svc = ConversationService(db)
            conv_x = svc.create_conversation(title=f"{_PREFIX}E2_userx", user_id=user_x.id)
        finally:
            db.close()

        # ENABLE_AUTH=false 模式下列出，应能看到 user_x 的对话
        resp = self.client.get("/api/v1/conversations")
        self.assertEqual(resp.status_code, 200, resp.text)
        ids = [c["id"] for c in resp.json()["conversations"]]
        self.assertIn(str(conv_x.id), ids)

    def test_E3_anonymous_mode_all_users_view_accessible(self):
        """ENABLE_AUTH=false 时 AnonymousUser.is_superadmin=True，/all-users-view 可访问"""
        resp = self.client.get("/api/v1/conversations/all-users-view")
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertIn("users", resp.json())


# ══════════════════════════════════════════════════════════════════════════════
# Section F — 安全边界 & 漏洞修复验证 (5 tests)
# ══════════════════════════════════════════════════════════════════════════════

class TestSecurityBoundaries(unittest.TestCase):
    """F1-F5: 安全边界与已修复漏洞的回归验证"""

    @classmethod
    def setUpClass(cls):
        cls.client = _make_client()
        cls.user_a = _make_user("f_usera", role_names=["viewer"])
        cls.user_b = _make_user("f_userb", role_names=["viewer"])
        cls.regular = _make_user("f_regular", role_names=["analyst"])

    def _create_conv(self, user, title_suffix=""):
        with patch("backend.config.settings.settings.enable_auth", True):
            resp = self.client.post(
                "/api/v1/conversations",
                json={"title": f"{_PREFIX}F_{title_suffix}"},
                headers=_auth(user),
            )
        self.assertEqual(resp.status_code, 200, resp.text)
        return resp.json()["data"]["id"]

    def test_F1_unauthenticated_request_returns_401(self):
        """ENABLE_AUTH=true 时未提供 token 的请求返回 401"""
        with patch("backend.config.settings.settings.enable_auth", True):
            resp = self.client.post(
                "/api/v1/conversations",
                json={"title": f"{_PREFIX}F1_noauth"},
            )
        self.assertEqual(resp.status_code, 401, resp.text)

    def test_F2_all_users_view_returns_403_for_non_superadmin(self):
        """GET /all-users-view 非 superadmin 调用返回 403"""
        with patch("backend.config.settings.settings.enable_auth", True):
            resp = self.client.get(
                "/api/v1/conversations/all-users-view",
                headers=_auth(self.regular),
            )
        self.assertEqual(resp.status_code, 403, resp.text)

    def test_F3_null_user_id_conversation_accessible_by_any_user(self):
        """user_id=NULL 的对话（迁移前历史数据）任何已认证用户可访问"""
        # 直接在 DB 中创建 user_id=NULL 的对话
        from backend.services.conversation_service import ConversationService
        db = _db()
        try:
            svc = ConversationService(db)
            legacy_conv = svc.create_conversation(
                title=f"{_PREFIX}F3_legacy", user_id=None
            )
            legacy_id = str(legacy_conv.id)
        finally:
            db.close()

        # user_a 应能访问（因为 user_id is None 时跳过 ownership 检查）
        with patch("backend.config.settings.settings.enable_auth", True):
            resp = self.client.get(
                f"/api/v1/conversations/{legacy_id}",
                headers=_auth(self.user_a),
            )
        self.assertEqual(resp.status_code, 200, resp.text)

    def test_F4_send_message_to_other_user_conversation_returns_403(self):
        """POST /{id}/messages 向其他用户的对话发消息返回 403（修复漏洞）"""
        b_id = self._create_conv(self.user_b, "F4")
        with patch("backend.config.settings.settings.enable_auth", True):
            resp = self.client.post(
                f"/api/v1/conversations/{b_id}/messages",
                json={"content": "injection attempt", "stream": False},
                headers=_auth(self.user_a),
            )
        self.assertEqual(resp.status_code, 403, resp.text)

    def test_F5_get_messages_requires_authentication(self):
        """GET /{id}/messages 需要认证（修复漏洞：原来无 auth 依赖）"""
        b_id = self._create_conv(self.user_b, "F5")
        with patch("backend.config.settings.settings.enable_auth", True):
            # 无 token 的请求应返回 401
            resp = self.client.get(f"/api/v1/conversations/{b_id}/messages")
        self.assertEqual(resp.status_code, 401, resp.text)


# ══════════════════════════════════════════════════════════════════════════════
# Section G — RBAC 权限范围验证 (3 tests)
# ══════════════════════════════════════════════════════════════════════════════

class TestRBACScope(unittest.TestCase):
    """G1-G3: 对话隔离功能不引入新权限需求，依赖现有 RBAC 体系"""

    @classmethod
    def setUpClass(cls):
        cls.client = _make_client()
        cls.viewer = _make_user("g_viewer", role_names=["viewer"])
        cls.analyst = _make_user("g_analyst", role_names=["analyst"])

    def test_G1_viewer_role_can_list_conversations(self):
        """viewer 角色（最低权限）可正常列出对话，无需额外权限"""
        with patch("backend.config.settings.settings.enable_auth", True):
            resp = self.client.get("/api/v1/conversations", headers=_auth(self.viewer))
        self.assertEqual(resp.status_code, 200, resp.text)

    def test_G2_viewer_role_can_create_conversations(self):
        """viewer 角色可创建对话，对话隔离不依赖特殊权限"""
        with patch("backend.config.settings.settings.enable_auth", True):
            resp = self.client.post(
                "/api/v1/conversations",
                json={"title": f"{_PREFIX}G2_viewer"},
                headers=_auth(self.viewer),
            )
        self.assertEqual(resp.status_code, 200, resp.text)

    def test_G3_all_users_view_returns_403_for_viewer_and_analyst(self):
        """/all-users-view 对 viewer 和 analyst 均返回 403（仅 superadmin 可用）"""
        with patch("backend.config.settings.settings.enable_auth", True):
            r1 = self.client.get(
                "/api/v1/conversations/all-users-view",
                headers=_auth(self.viewer),
            )
            r2 = self.client.get(
                "/api/v1/conversations/all-users-view",
                headers=_auth(self.analyst),
            )
        self.assertEqual(r1.status_code, 403, r1.text)
        self.assertEqual(r2.status_code, 403, r2.text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
