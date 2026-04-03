"""
test_sidebar_fixes.py — 侧边栏双 Bug 修复完整测试套件
=======================================================

覆盖以下修复任务：
  P1-T1/T2  GroupItem 实时 conversationCount prop（计数不一致修复）
  P2-T1/T2  Chat.tsx reactive useEffect（superadmin 看他人对话修复）
  P2-T3     后端 all-users-view 数据正确性验证

测试层次：
  A (6) — 分组计数 API：GET /groups conversation_count 正确性（P1 核心）
  B (7) — 其他用户视图 API：/conversations/all-users-view 权限与数据（P2 核心）
  C (5) — 结果字段完整性：返回结构与字段验证
  D (4) — RBAC 权限归属：新功能无需新增权限条目
  E (4) — 前端代码结构：P1/P2 修改落实到文件（静态验证）
  F (3) — 计数一致性端到端：建组→建话→移入→移出→删除全流程

总计: 29 个测试用例

关键设计结论：
  - GroupItem 计数：客户端实时计算（groupedConversations[id].length）优先于后端缓存值
  - all-users-view：通过 is_superadmin flag 控制，不新增 RBAC permission 条目
  - 时序修复：useEffect([authUser?.is_superadmin, authUser?.id]) 在 auth 完成后 reactive 触发
"""

import os
import sys
import uuid
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "backend"))
os.environ.setdefault("ENABLE_AUTH", "True")

from test_utils import make_test_username

_PREFIX = f"_t_sbfix_{uuid.uuid4().hex[:6]}_"


# ─── DB helpers ─────────────────────────────────────────────────────────────

def _db():
    from backend.config.database import SessionLocal
    return SessionLocal()


_g_db = _db()


def _make_user(suffix="", password="Test1234!", role_names=None, is_superadmin=False):
    from backend.models.user import User
    from backend.models.role import Role
    from backend.models.user_role import UserRole
    from backend.core.auth.password import hash_password

    username = f"{_PREFIX}{suffix or uuid.uuid4().hex[:6]}"
    u = User(
        username=username,
        display_name=None,
        hashed_password=hash_password(password),
        auth_source="local",
        is_active=True,
        is_superadmin=is_superadmin,
    )
    _g_db.add(u)
    _g_db.flush()
    if role_names:
        for rname in role_names:
            role = _g_db.query(Role).filter(Role.name == rname).first()
            if role:
                _g_db.add(UserRole(user_id=u.id, role_id=role.id))
    _g_db.commit()
    _g_db.refresh(u)
    return u, username, password


def _make_token(user, roles=None):
    from backend.config.settings import settings
    from backend.core.auth.jwt import create_access_token
    from backend.core.rbac import get_user_roles
    if roles is None:
        roles = get_user_roles(user, _g_db)
    return create_access_token(
        {"sub": str(user.id), "username": user.username, "roles": roles},
        settings.jwt_secret,
        settings.jwt_algorithm,
    )


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _client():
    from fastapi.testclient import TestClient
    from backend.main import app
    return TestClient(app, raise_server_exceptions=True)


def teardown_module(_=None):
    from backend.models.user import User
    from backend.models.conversation_group import ConversationGroup
    from backend.models.conversation import Conversation
    try:
        # 清理测试对话
        convs = _g_db.query(Conversation).join(
            User, Conversation.user_id == User.id
        ).filter(User.username.like(f"{_PREFIX}%")).all()
        for c in convs:
            _g_db.delete(c)

        # 清理测试分组
        groups = _g_db.query(ConversationGroup).join(
            User, ConversationGroup.user_id == User.id
        ).filter(User.username.like(f"{_PREFIX}%")).all()
        for g in groups:
            _g_db.delete(g)

        # 清理测试用户
        _g_db.query(User).filter(
            User.username.like(f"{_PREFIX}%")
        ).delete(synchronize_session=False)
        _g_db.commit()
    finally:
        _g_db.close()


# ═══════════════════════════════════════════════════════════════════════════════
# A — 分组计数 API：GET /groups conversation_count 正确性 (6 tests)
# ═══════════════════════════════════════════════════════════════════════════════

class TestGroupConversationCount(unittest.TestCase):
    """
    P1 核心依赖：GET /groups 返回的 conversation_count 应与数据库实际对话数一致。
    虽然前端已改为使用客户端实时计数，后端的 count 仍需正确，
    用于首次加载时 GroupItem 的 fallback 显示。
    """

    @classmethod
    def setUpClass(cls):
        cls.user, cls.username, cls.pwd = _make_user(suffix="auser", role_names=["analyst"])
        cls.token = _make_token(cls.user)
        cls.client = _client()

    def _create_group(self, name):
        r = self.client.post("/api/v1/groups",
                             json={"name": name},
                             headers=_auth(self.token))
        self.assertEqual(r.status_code, 200)
        return r.json()["data"]

    def _create_conv(self, title="test"):
        r = self.client.post("/api/v1/conversations",
                             json={"title": title},
                             headers=_auth(self.token))
        self.assertEqual(r.status_code, 200)
        return r.json()["data"]

    def _move_conv(self, conv_id, group_id):
        r = self.client.put(f"/api/v1/conversations/{conv_id}/group",
                            json={"group_id": group_id},
                            headers=_auth(self.token))
        self.assertEqual(r.status_code, 200)

    def _list_groups(self):
        r = self.client.get("/api/v1/groups", headers=_auth(self.token))
        self.assertEqual(r.status_code, 200)
        return {g["id"]: g for g in r.json()["groups"]}

    def test_A1_new_group_has_zero_count(self):
        """新建空分组后 GET /groups 返回 conversation_count=0"""
        g = self._create_group(f"{_PREFIX}A1grp")
        groups = self._list_groups()
        self.assertIn(g["id"], groups)
        self.assertEqual(groups[g["id"]]["conversation_count"], 0,
                         "新建空分组 count 应为 0")

    def test_A2_count_increments_after_move_in(self):
        """对话移入分组后 GET /groups 计数加 1"""
        g = self._create_group(f"{_PREFIX}A2grp")
        c = self._create_conv("A2conv")
        self._move_conv(c["id"], g["id"])
        groups = self._list_groups()
        self.assertEqual(groups[g["id"]]["conversation_count"], 1,
                         "移入一个对话后 count 应为 1")

    def test_A3_count_increments_for_second_move(self):
        """两个对话移入同一分组后 count=2"""
        g = self._create_group(f"{_PREFIX}A3grp")
        c1 = self._create_conv("A3conv1")
        c2 = self._create_conv("A3conv2")
        self._move_conv(c1["id"], g["id"])
        self._move_conv(c2["id"], g["id"])
        groups = self._list_groups()
        self.assertEqual(groups[g["id"]]["conversation_count"], 2,
                         "移入两个对话后 count 应为 2")

    def test_A4_count_decrements_after_move_out(self):
        """对话移出分组后 GET /groups 计数减 1"""
        g = self._create_group(f"{_PREFIX}A4grp")
        c1 = self._create_conv("A4conv1")
        c2 = self._create_conv("A4conv2")
        self._move_conv(c1["id"], g["id"])
        self._move_conv(c2["id"], g["id"])
        # 移出 c1
        self._move_conv(c1["id"], None)
        groups = self._list_groups()
        self.assertEqual(groups[g["id"]]["conversation_count"], 1,
                         "移出一个对话后 count 应为 1")

    def test_A5_move_between_groups_updates_both_counts(self):
        """对话从 groupA 移到 groupB：A count-1，B count+1，无双计数"""
        ga = self._create_group(f"{_PREFIX}A5grpA")
        gb = self._create_group(f"{_PREFIX}A5grpB")
        c = self._create_conv("A5conv")
        self._move_conv(c["id"], ga["id"])
        # 从 A 移到 B
        self._move_conv(c["id"], gb["id"])
        groups = self._list_groups()
        self.assertEqual(groups[ga["id"]]["conversation_count"], 0,
                         "原分组 A count 应为 0")
        self.assertEqual(groups[gb["id"]]["conversation_count"], 1,
                         "目标分组 B count 应为 1")

    def test_A6_group_count_isolation_between_users(self):
        """不同用户的分组 count 互不干扰"""
        user2, _, _ = _make_user(suffix="a6user2", role_names=["analyst"])
        token2 = _make_token(user2)
        client2 = _client()

        # user1 创建分组和对话
        g1 = self._create_group(f"{_PREFIX}A6grpU1")
        c1 = self._create_conv("A6convU1")
        self._move_conv(c1["id"], g1["id"])

        # user2 创建同名分组（允许，per-user 隔离）
        r = client2.post("/api/v1/groups",
                         json={"name": f"{_PREFIX}A6grpU2"},
                         headers=_auth(token2))
        self.assertEqual(r.status_code, 200)
        g2_id = r.json()["data"]["id"]

        # user2 的同名分组 count=0
        r2 = client2.get("/api/v1/groups", headers=_auth(token2))
        u2_groups = {g["id"]: g for g in r2.json()["groups"]}
        self.assertEqual(u2_groups[g2_id]["conversation_count"], 0,
                         "user2 的分组 count 不应受 user1 对话影响")


# ═══════════════════════════════════════════════════════════════════════════════
# B — 其他用户视图：/conversations/all-users-view 权限与数据 (7 tests)
# ═══════════════════════════════════════════════════════════════════════════════

class TestAllUsersView(unittest.TestCase):
    """
    P2 核心：GET /conversations/all-users-view 仅 superadmin 可访问，
    返回除自身外所有有活跃对话的用户，供 Chat.tsx 侧边栏"其他用户"区块渲染。
    """

    @classmethod
    def setUpClass(cls):
        # 超管
        cls.sa, _, cls.sa_pwd = _make_user(suffix="bsa", is_superadmin=True)
        cls.sa_token = _make_token(cls.sa, roles=["superadmin"])

        # 普通角色用户
        cls.viewer, _, _ = _make_user(suffix="bviewer", role_names=["viewer"])
        cls.analyst, _, _ = _make_user(suffix="banalyst", role_names=["analyst"])
        cls.admin, _, _ = _make_user(suffix="badmin", role_names=["admin"])

        cls.viewer_token = _make_token(cls.viewer)
        cls.analyst_token = _make_token(cls.analyst)
        cls.admin_token = _make_token(cls.admin)

        # 为 analyst 建一条 active 对话
        from backend.models.conversation import Conversation
        cls.analyst_conv = Conversation(
            title="B section analyst conv",
            status="active",
            user_id=cls.analyst.id,
        )
        _g_db.add(cls.analyst_conv)
        _g_db.commit()
        _g_db.refresh(cls.analyst_conv)

    def test_B1_viewer_gets_403(self):
        """viewer 角色访问 all-users-view 返回 403"""
        r = _client().get("/api/v1/conversations/all-users-view",
                          headers=_auth(self.viewer_token))
        self.assertEqual(r.status_code, 403)

    def test_B2_analyst_gets_403(self):
        """analyst 角色访问 all-users-view 返回 403"""
        r = _client().get("/api/v1/conversations/all-users-view",
                          headers=_auth(self.analyst_token))
        self.assertEqual(r.status_code, 403)

    def test_B3_admin_gets_403(self):
        """admin 角色（非 superadmin）访问 all-users-view 返回 403"""
        r = _client().get("/api/v1/conversations/all-users-view",
                          headers=_auth(self.admin_token))
        self.assertEqual(r.status_code, 403)

    def test_B4_superadmin_gets_200(self):
        """superadmin 访问 all-users-view 返回 200"""
        r = _client().get("/api/v1/conversations/all-users-view",
                          headers=_auth(self.sa_token))
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertIn("users", data, "响应应包含 'users' 键")
        self.assertIsInstance(data["users"], list)

    def test_B5_superadmin_excluded_from_result(self):
        """超管自己的对话不出现在 all-users-view（exclude_user_id 生效）"""
        # 为超管建一条对话
        from backend.models.conversation import Conversation
        sa_conv = Conversation(
            title="B5 sa own conv",
            status="active",
            user_id=self.sa.id,
        )
        _g_db.add(sa_conv)
        _g_db.commit()

        r = _client().get("/api/v1/conversations/all-users-view",
                          headers=_auth(self.sa_token))
        self.assertEqual(r.status_code, 200)
        user_ids = [u["user_id"] for u in r.json()["users"]]
        self.assertNotIn(str(self.sa.id), user_ids,
                         "超管自身不应出现在 all-users-view 结果中")

        _g_db.delete(sa_conv)
        _g_db.commit()

    def test_B6_user_with_active_conv_appears(self):
        """有 active 对话的用户（analyst）出现在 all-users-view"""
        r = _client().get("/api/v1/conversations/all-users-view",
                          headers=_auth(self.sa_token))
        self.assertEqual(r.status_code, 200)
        user_ids = [u["user_id"] for u in r.json()["users"]]
        self.assertIn(str(self.analyst.id), user_ids,
                      "analyst 有 active 对话，应出现在结果中")

    def test_B7_user_without_conv_excluded(self):
        """没有对话的用户（viewer）不出现在 all-users-view"""
        r = _client().get("/api/v1/conversations/all-users-view",
                          headers=_auth(self.sa_token))
        self.assertEqual(r.status_code, 200)
        user_ids = [u["user_id"] for u in r.json()["users"]]
        self.assertNotIn(str(self.viewer.id), user_ids,
                         "viewer 无对话，不应出现在结果中")


# ═══════════════════════════════════════════════════════════════════════════════
# C — 结果字段完整性 (5 tests)
# ═══════════════════════════════════════════════════════════════════════════════

class TestAllUsersViewFields(unittest.TestCase):
    """
    验证 all-users-view 返回的数据结构与字段，
    确保前端 OtherUserConversations 接口能正确解析。
    """

    @classmethod
    def setUpClass(cls):
        cls.sa, _, _ = _make_user(suffix="csa", is_superadmin=True)
        cls.sa_token = _make_token(cls.sa, roles=["superadmin"])

        # 带 display_name 的用户
        from backend.models.user import User
        from backend.core.auth.password import hash_password
        cls.user_with_dn = User(
            username=f"{_PREFIX}cwithdn",
            display_name="测试展示名",
            hashed_password=hash_password("Test1234!"),
            auth_source="local",
            is_active=True,
        )
        _g_db.add(cls.user_with_dn)
        _g_db.flush()

        # 无 display_name 的用户
        cls.user_no_dn = User(
            username=f"{_PREFIX}cnodn",
            display_name=None,
            hashed_password=hash_password("Test1234!"),
            auth_source="local",
            is_active=True,
        )
        _g_db.add(cls.user_no_dn)
        _g_db.flush()

        from backend.models.conversation import Conversation
        # active 对话
        cls.active_conv = Conversation(
            title="C active conv",
            status="active",
            user_id=cls.user_with_dn.id,
        )
        # archived 对话（不应出现）
        cls.archived_conv = Conversation(
            title="C archived conv",
            status="archived",
            user_id=cls.user_with_dn.id,
        )
        # user_no_dn 的对话
        cls.nodn_conv = Conversation(
            title="C nodn conv",
            status="active",
            user_id=cls.user_no_dn.id,
        )
        _g_db.add_all([cls.active_conv, cls.archived_conv, cls.nodn_conv])
        _g_db.commit()

    def _get_result(self):
        r = _client().get("/api/v1/conversations/all-users-view",
                          headers=_auth(self.sa_token))
        self.assertEqual(r.status_code, 200)
        return r.json()["users"]

    def test_C1_user_entry_has_required_fields(self):
        """users 每项包含 username, display_name, user_id, conversations"""
        users = self._get_result()
        target = next((u for u in users if u["user_id"] == str(self.user_with_dn.id)), None)
        self.assertIsNotNone(target, "user_with_dn 应出现在结果中")
        for field in ["username", "display_name", "user_id", "conversations"]:
            self.assertIn(field, target, f"缺少字段: {field}")

    def test_C2_conversation_entry_has_required_fields(self):
        """conversations 每项包含 id, title, message_count, status, updated_at"""
        users = self._get_result()
        target = next((u for u in users if u["user_id"] == str(self.user_with_dn.id)), None)
        self.assertIsNotNone(target)
        conv = target["conversations"][0]
        for field in ["id", "title", "message_count", "status", "updated_at"]:
            self.assertIn(field, conv, f"对话缺少字段: {field}")

    def test_C3_display_name_fallback_to_username(self):
        """display_name=None 时返回 username 作为展示名"""
        users = self._get_result()
        target = next((u for u in users if u["user_id"] == str(self.user_no_dn.id)), None)
        self.assertIsNotNone(target)
        # 后端逻辑: user.display_name or user.username
        self.assertEqual(target["display_name"], self.user_no_dn.username,
                         "display_name 为 None 时应回退到 username")

    def test_C4_only_active_conversations_returned(self):
        """archived 对话不出现在 all-users-view（只返回 active）"""
        users = self._get_result()
        target = next((u for u in users if u["user_id"] == str(self.user_with_dn.id)), None)
        self.assertIsNotNone(target)
        conv_ids = [c["id"] for c in target["conversations"]]
        self.assertIn(str(self.active_conv.id), conv_ids,
                      "active 对话应出现")
        self.assertNotIn(str(self.archived_conv.id), conv_ids,
                         "archived 对话不应出现")

    def test_C5_users_sorted_by_username(self):
        """返回的 users 列表按 username 排序"""
        users = self._get_result()
        # 过滤本测试创建的用户
        test_users = [u for u in users
                      if u["username"].startswith(_PREFIX)]
        usernames = [u["username"] for u in test_users]
        self.assertEqual(usernames, sorted(usernames),
                         "users 列表应按 username 字母序排列")


# ═══════════════════════════════════════════════════════════════════════════════
# D — RBAC 权限归属：新功能无需新增权限条目 (4 tests)
# ═══════════════════════════════════════════════════════════════════════════════

class TestRBACScope(unittest.TestCase):
    """
    设计结论验证：
    - all-users-view 通过 is_superadmin flag 控制（不是 require_permission()）
    - 分组 CRUD 使用用户鉴权（user_id 隔离），不需要新 permission 条目
    - 权限表中无 group_count / other_users / sidebar 等新增条目
    """

    def test_D1_all_users_view_uses_superadmin_flag_not_permission(self):
        """
        all-users-view 由 is_superadmin flag 控制，
        普通 superadmin 角色（有所有权限但 is_superadmin=False）也不能访问
        """
        # 创建一个拥有超管角色权限但 is_superadmin=False 的用户
        non_flag_sa, _, _ = _make_user(
            suffix="d1user",
            role_names=["superadmin"],  # 有 superadmin 角色（所有权限）
            is_superadmin=False,         # 但 DB flag 不是超管
        )
        token = _make_token(non_flag_sa)
        r = _client().get("/api/v1/conversations/all-users-view",
                          headers=_auth(token))
        # 没有 is_superadmin flag 即使有 superadmin 角色也应返回 403
        self.assertEqual(r.status_code, 403,
                         "is_superadmin DB flag 为 False 时应返回 403，与权限角色无关")

    def test_D2_group_crud_requires_no_new_permission(self):
        """分组 CRUD（create/list/update/delete）无需特殊 RBAC 权限，仅需认证"""
        # viewer 是最低权限角色，只有 chat:use
        viewer, _, _ = _make_user(suffix="d2viewer", role_names=["viewer"])
        token = _make_token(viewer)
        client = _client()

        # viewer 应能创建分组（不需要额外 permission）
        r = client.post("/api/v1/groups",
                        json={"name": f"{_PREFIX}D2grp"},
                        headers=_auth(token))
        self.assertEqual(r.status_code, 200,
                         "viewer（最低权限）也应能创建分组，无需额外 permission")

    def test_D3_no_new_permissions_in_seed_data(self):
        """权限种子数据中无 group_count / other_users / sidebar 相关新增条目"""
        from backend.models.permission import Permission
        all_perms = [f"{p.resource}:{p.action}"
                     for p in _g_db.query(Permission).all()]
        new_feature_perms = [
            p for p in all_perms
            if any(kw in p for kw in
                   ["group_count", "other_users", "sidebar", "all_users", "dropdown"])
        ]
        self.assertEqual(new_feature_perms, [],
                         f"不应存在与侧边栏新功能相关的权限条目: {new_feature_perms}")

    def test_D4_superadmin_flag_true_can_access(self):
        """is_superadmin=True 的用户（无论角色）可访问 all-users-view"""
        flag_sa, _, _ = _make_user(
            suffix="d4flagsa",
            role_names=[],          # 无角色
            is_superadmin=True,     # 但有超管 flag
        )
        token = _make_token(flag_sa, roles=[])
        r = _client().get("/api/v1/conversations/all-users-view",
                          headers=_auth(token))
        self.assertEqual(r.status_code, 200,
                         "is_superadmin=True 应能访问 all-users-view")


# ═══════════════════════════════════════════════════════════════════════════════
# E — 前端代码结构：P1/P2 修改落实验证 (4 tests)
# ═══════════════════════════════════════════════════════════════════════════════

_FRONTEND = os.path.join(os.path.dirname(__file__), "frontend", "src")


class TestFrontendStructure(unittest.TestCase):
    """
    静态验证 P1/P2 的前端代码修改已正确落实：
    - GroupItem: conversationCount prop 定义与使用
    - ConversationSidebar: 传入实时计数
    - Chat.tsx: reactive useEffect + loadOtherUsersData 独立函数
    """

    def _read(self, rel_path):
        path = os.path.join(_FRONTEND, rel_path)
        self.assertTrue(os.path.exists(path), f"文件不存在: {path}")
        return open(path, encoding="utf-8").read()

    def test_E1_groupitem_has_conversation_count_prop(self):
        """P1-T1: GroupItem.tsx 定义了 conversationCount 可选 prop"""
        content = self._read("components/chat/GroupItem.tsx")
        self.assertIn("conversationCount", content,
                      "GroupItem.tsx 应定义 conversationCount prop")
        self.assertIn("conversationCount?", content,
                      "conversationCount 应为可选 prop（带 ? 号）")

    def test_E2_groupitem_uses_nullish_coalescing(self):
        """P1-T1: GroupItem.tsx 使用 conversationCount ?? group.conversation_count"""
        content = self._read("components/chat/GroupItem.tsx")
        self.assertIn("conversationCount ?? group.conversation_count",
                      content,
                      "GroupItem 应使用 ?? 运算符，优先实时计数，后备到 backend 缓存值")

    def test_E3_sidebar_passes_realtime_count(self):
        """P1-T2: ConversationSidebar.tsx 传入 groupedConversations 派生的实时计数"""
        content = self._read("components/chat/ConversationSidebar.tsx")
        self.assertIn("conversationCount={groupedConversations[group.id]?.length ?? 0}",
                      content,
                      "ConversationSidebar 应传入 groupedConversations 派生的实时计数")

    def test_E4_chat_has_reactive_useeffect_for_superadmin(self):
        """P2-T2: Chat.tsx 包含依赖 authUser.is_superadmin 的 reactive useEffect"""
        content = self._read("pages/Chat.tsx")
        # 验证 reactive useEffect 依赖数组
        self.assertIn("authUser?.is_superadmin", content,
                      "Chat.tsx 应在 useEffect 依赖中包含 authUser?.is_superadmin")
        self.assertIn("loadOtherUsersData", content,
                      "Chat.tsx 应提取独立的 loadOtherUsersData 函数")
        # 验证旧的静态 getState 模式已移除
        self.assertNotIn(
            "useAuthStore.getState().user?.is_superadmin",
            content,
            "Chat.tsx 不应再使用静态 getState 判断（已被 reactive useEffect 替代）"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# F — 计数一致性端到端：全流程 (3 tests)
# ═══════════════════════════════════════════════════════════════════════════════

class TestCountConsistencyE2E(unittest.TestCase):
    """
    端到端流程测试：模拟用户完整操作序列，
    验证每步后 GET /groups 返回的 count 与实际对话数完全一致。
    """

    @classmethod
    def setUpClass(cls):
        cls.user, _, _ = _make_user(suffix="fe2e", role_names=["analyst"])
        cls.token = _make_token(cls.user)

    def _client(self):
        return _client()

    def test_F1_full_lifecycle_count_consistency(self):
        """
        全生命周期：建分组 → 建2对话 → 移入 → 移出1 → 移入另一分组
        每步验证 GET /groups 计数与预期一致
        """
        c = self._client()
        h = _auth(self.token)

        # 建两个分组
        gA = c.post("/api/v1/groups", json={"name": f"{_PREFIX}F1grpA"}, headers=h).json()["data"]
        gB = c.post("/api/v1/groups", json={"name": f"{_PREFIX}F1grpB"}, headers=h).json()["data"]

        # 建两个对话
        cv1 = c.post("/api/v1/conversations", json={"title": "F1cv1"}, headers=h).json()["data"]
        cv2 = c.post("/api/v1/conversations", json={"title": "F1cv2"}, headers=h).json()["data"]

        def counts():
            r = c.get("/api/v1/groups", headers=h)
            gs = {g["id"]: g["conversation_count"] for g in r.json()["groups"]}
            return gs.get(gA["id"], -1), gs.get(gB["id"], -1)

        # 初始：0, 0
        cA, cB = counts()
        self.assertEqual((cA, cB), (0, 0), f"初始应均为 0，实际 A={cA} B={cB}")

        # 移 cv1 → A：1, 0
        c.put(f"/api/v1/conversations/{cv1['id']}/group", json={"group_id": gA["id"]}, headers=h)
        cA, cB = counts()
        self.assertEqual((cA, cB), (1, 0), f"cv1→A 后应 A=1 B=0，实际 A={cA} B={cB}")

        # 移 cv2 → A：2, 0
        c.put(f"/api/v1/conversations/{cv2['id']}/group", json={"group_id": gA["id"]}, headers=h)
        cA, cB = counts()
        self.assertEqual((cA, cB), (2, 0), f"cv2→A 后应 A=2 B=0，实际 A={cA} B={cB}")

        # 移 cv1 → B：1, 1
        c.put(f"/api/v1/conversations/{cv1['id']}/group", json={"group_id": gB["id"]}, headers=h)
        cA, cB = counts()
        self.assertEqual((cA, cB), (1, 1), f"cv1→B 后应 A=1 B=1，实际 A={cA} B={cB}")

        # 移 cv2 → 未分组：0, 1
        c.put(f"/api/v1/conversations/{cv2['id']}/group", json={"group_id": None}, headers=h)
        cA, cB = counts()
        self.assertEqual((cA, cB), (0, 1), f"cv2移出后应 A=0 B=1，实际 A={cA} B={cB}")

    def test_F2_no_double_counting_on_move(self):
        """移动操作不造成双计数（A+B 总和在移动前后保持不变）"""
        c = self._client()
        h = _auth(self.token)

        gA = c.post("/api/v1/groups", json={"name": f"{_PREFIX}F2grpA"}, headers=h).json()["data"]
        gB = c.post("/api/v1/groups", json={"name": f"{_PREFIX}F2grpB"}, headers=h).json()["data"]
        cv = c.post("/api/v1/conversations", json={"title": "F2cv"}, headers=h).json()["data"]

        # 移入 A
        c.put(f"/api/v1/conversations/{cv['id']}/group",
              json={"group_id": gA["id"]}, headers=h)

        def total_count():
            r = c.get("/api/v1/groups", headers=h)
            gs = {g["id"]: g["conversation_count"] for g in r.json()["groups"]}
            return gs.get(gA["id"], 0) + gs.get(gB["id"], 0)

        before = total_count()
        self.assertEqual(before, 1, "移入 A 后总计数应为 1")

        # 移到 B
        c.put(f"/api/v1/conversations/{cv['id']}/group",
              json={"group_id": gB["id"]}, headers=h)
        after = total_count()
        self.assertEqual(after, 1,
                         f"A→B 后总计数仍应为 1（无双计数），实际: {after}")

    def test_F3_count_consistent_with_actual_listed_conversations(self):
        """
        GET /groups 返回的 count 与 GET /conversations 中属于该分组的对话数一致：
        这是 P1 修复前后均应满足的不变式——backend count 与 client-side filter 结果一致。
        """
        c = self._client()
        h = _auth(self.token)

        g = c.post("/api/v1/groups", json={"name": f"{_PREFIX}F3grp"}, headers=h).json()["data"]
        cv1 = c.post("/api/v1/conversations", json={"title": "F3cv1"}, headers=h).json()["data"]
        cv2 = c.post("/api/v1/conversations", json={"title": "F3cv2"}, headers=h).json()["data"]
        cv3 = c.post("/api/v1/conversations", json={"title": "F3cv3"}, headers=h).json()["data"]

        # 移入 2 个
        for cv in [cv1, cv2]:
            c.put(f"/api/v1/conversations/{cv['id']}/group",
                  json={"group_id": g["id"]}, headers=h)

        # GET /groups 的 count
        groups_resp = c.get("/api/v1/groups", headers=h).json()["groups"]
        backend_count = next(
            gr["conversation_count"] for gr in groups_resp if gr["id"] == g["id"]
        )

        # GET /conversations 中过滤 group_id 匹配的数量
        convs_resp = c.get("/api/v1/conversations",
                           params={"status": "active", "limit": 100},
                           headers=h).json()["conversations"]
        client_count = sum(1 for cv in convs_resp if cv.get("group_id") == g["id"])

        self.assertEqual(backend_count, client_count,
                         f"后端 count={backend_count} 应与客户端实际对话数 {client_count} 一致")
        self.assertEqual(backend_count, 2,
                         "移入 2 个对话后 count 应为 2")


# ═══════════════════════════════════════════════════════════════════════════════
# 入口
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    unittest.main(verbosity=2)
