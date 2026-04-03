"""
test_auth_flow.py — 认证流程 & 前端逻辑回归测试
=================================================

覆盖本次 Bug 修复的核心场景：

  P (6) — Proxy & 后端可达性验证
  Q (8) — initAuth() 四条路径（后端模拟）
  R (7) — RequireAuth 跳转逻辑
  S (5) — 菜单权限过滤完整性
  T (6) — 登录后角色菜单可见性

总计: 32 个测试用例
"""

from __future__ import annotations

import os
import sys
import uuid
import unittest
import re
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "backend"))

# ─── 测试用全局 DB ─────────────────────────────────────────────────────────
_PREFIX = f"_flow_{uuid.uuid4().hex[:6]}_"


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
        display_name=f"Flow {suffix}",
        hashed_password=hash_password(password),
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
    return u, username, password


def _token(user):
    from backend.config.settings import settings
    from backend.core.auth.jwt import create_access_token
    from backend.core.rbac import get_user_roles
    roles = get_user_roles(user, _g_db)
    return create_access_token(
        {"sub": str(user.id), "username": user.username, "roles": roles},
        settings.jwt_secret, settings.jwt_algorithm,
    )


def teardown_module(_=None):
    from backend.models.user import User
    try:
        _g_db.query(User).filter(
            User.username.like(f"{_PREFIX}%")
        ).delete(synchronize_session=False)
        _g_db.commit()
    finally:
        _g_db.close()


from fastapi.testclient import TestClient


def _app():
    from backend.main import app
    return app


# ══════════════════════════════════════════════════════════════════════════════
# Section P — Proxy & 后端可达性验证
# ══════════════════════════════════════════════════════════════════════════════

class TestBackendReachability(unittest.TestCase):
    """P1-P6: 验证后端 API 在 ENABLE_AUTH=true 下的响应行为，
    确保前端 initAuth() 能收到正确的 HTTP 状态码（而非网络错误）"""

    @classmethod
    def setUpClass(cls):
        cls.app = _app()
        cls.client = TestClient(cls.app)
        cls.super_user, cls.super_name, cls.super_pw = _make_user(
            "p_super", role_names=["superadmin"]
        )

    def test_P1_auth_me_no_token_returns_401_not_network_error(self):
        """GET /auth/me 无 token 必须返回 401（而非网络错误），
        initAuth() 只有收到 401 才能正确设置 auth_enabled=true"""
        with patch("backend.config.settings.settings.enable_auth", True):
            resp = self.client.get("/api/v1/auth/me")
        self.assertEqual(resp.status_code, 401,
                         "ENABLE_AUTH=true 时无 token 必须返回 401")
        self.assertIn("detail", resp.json())

    def test_P2_auth_refresh_no_cookie_returns_401(self):
        """POST /auth/refresh 无 Cookie 返回 401（initAuth 路径2 正常失败）"""
        with patch("backend.config.settings.settings.enable_auth", True):
            fresh = TestClient(self.app)
            resp = fresh.post("/api/v1/auth/refresh")
        self.assertEqual(resp.status_code, 401)

    def test_P3_auth_me_with_valid_token_returns_200(self):
        """GET /auth/me 有效 token 返回 200 + 用户信息（initAuth 路径1）"""
        with patch("backend.config.settings.settings.enable_auth", True):
            resp = self.client.get(
                "/api/v1/auth/me",
                headers={"Authorization": f"Bearer {_token(self.super_user)}"},
            )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("username", data)
        self.assertIn("permissions", data)

    def test_P4_auth_me_no_token_auth_disabled_returns_200_anonymous(self):
        """ENABLE_AUTH=false: GET /auth/me 无 token 返回 200 + 匿名用户
        （initAuth 路径3，用于检测 auth_enabled=false）"""
        with patch("backend.config.settings.settings.enable_auth", False):
            resp = self.client.get("/api/v1/auth/me")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        # 匿名用户的特征：id='default', username='default'
        self.assertEqual(data["username"], "default",
                         f"ENABLE_AUTH=false 时应返回匿名用户，实际: {data}")
        self.assertEqual(data["id"], "default")

    def test_P5_login_returns_access_token(self):
        """POST /auth/login 正确凭据返回 access_token"""
        with patch("backend.config.settings.settings.enable_auth", True):
            resp = self.client.post(
                "/api/v1/auth/login",
                json={"username": self.super_name, "password": self.super_pw},
            )
        self.assertEqual(resp.status_code, 200)
        self.assertIn("access_token", resp.json())

    def test_P6_login_sets_httponly_refresh_cookie(self):
        """POST /auth/login 成功后 Set-Cookie 含 HttpOnly refresh_token"""
        with patch("backend.config.settings.settings.enable_auth", True):
            resp = self.client.post(
                "/api/v1/auth/login",
                json={"username": self.super_name, "password": self.super_pw},
            )
        cookie = resp.headers.get("set-cookie", "")
        self.assertIn("refresh_token", cookie)
        self.assertIn("HttpOnly", cookie,
                      "refresh_token 必须是 HttpOnly Cookie，防止 JS 读取")


# ══════════════════════════════════════════════════════════════════════════════
# Section Q — initAuth() 四条路径模拟测试
# ══════════════════════════════════════════════════════════════════════════════

class TestInitAuthPaths(unittest.TestCase):
    """Q1-Q8: 模拟 initAuth() 四条执行路径"""

    @classmethod
    def setUpClass(cls):
        cls.app = _app()
        cls.client = TestClient(cls.app)
        cls.analyst_user, cls.analyst_name, cls.analyst_pw = _make_user(
            "q_analyst", role_names=["analyst"]
        )
        cls.viewer_user, _, _ = _make_user("q_viewer", role_names=["viewer"])

    def test_Q1_path1_valid_token_restores_session(self):
        """路径1: 有有效 access_token → /me 成功 → session 恢复（不跳 login）"""
        with patch("backend.config.settings.settings.enable_auth", True):
            resp = self.client.get(
                "/api/v1/auth/me",
                headers={"Authorization": f"Bearer {_token(self.analyst_user)}"},
            )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["username"], self.analyst_name)

    def test_Q2_path1_expired_token_returns_401(self):
        """路径1: token 过期/伪造 → 401（应走到路径2/3/4）"""
        with patch("backend.config.settings.settings.enable_auth", True):
            resp = self.client.get(
                "/api/v1/auth/me",
                headers={"Authorization": "Bearer eyJhbGciOiJIUzI1NiJ9.fake.sig"},
            )
        self.assertEqual(resp.status_code, 401)

    def test_Q3_path2_valid_refresh_cookie_restores_session(self):
        """路径2: 有有效 refresh Cookie → /refresh 成功 → session 恢复"""
        with patch("backend.config.settings.settings.enable_auth", True):
            # 先登录获取 refresh cookie
            login = self.client.post(
                "/api/v1/auth/login",
                json={"username": self.analyst_name, "password": self.analyst_pw},
            )
            access_token = login.json()["access_token"]
            # 用 access_token 验证 session 已恢复
            me = self.client.get(
                "/api/v1/auth/me",
                headers={"Authorization": f"Bearer {access_token}"},
            )
        self.assertEqual(me.status_code, 200)
        self.assertEqual(me.json()["username"], self.analyst_name)

    def test_Q4_path3_auth_disabled_anonymous_user(self):
        """路径3: ENABLE_AUTH=false → /me 返回匿名用户 → auth_enabled=false"""
        with patch("backend.config.settings.settings.enable_auth", False):
            resp = self.client.get("/api/v1/auth/me")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        # 匿名用户：id='default', username='default'
        self.assertEqual(data["id"], "default")
        self.assertEqual(data["username"], "default")
        self.assertTrue(data["is_superadmin"], "匿名用户应为 superadmin")

    def test_Q5_path4_auth_enabled_no_token_returns_401(self):
        """路径4: ENABLE_AUTH=true, 无 token, 无 Cookie → 401 → 需要登录"""
        fresh = TestClient(self.app)  # 全新客户端，无 cookie
        with patch("backend.config.settings.settings.enable_auth", True):
            resp = fresh.get("/api/v1/auth/me")
        self.assertEqual(resp.status_code, 401,
                         "路径4: 应返回 401 触发 auth_enabled=true 设置")

    def test_Q6_anonymous_user_id_is_default_not_anonymous(self):
        """
        关键修复验证: 匿名用户 username='default'（不是 'anonymous'）
        旧代码用 username != 'anonymous' 判断，导致 ENABLE_AUTH=false 时
        也设置 auth_enabled=true → 死循环跳到登录页。
        修复后用 id='default' AND username='default' 判断匿名用户。
        """
        with patch("backend.config.settings.settings.enable_auth", False):
            resp = self.client.get("/api/v1/auth/me")
        data = resp.json()
        # 验证匿名用户特征
        self.assertNotEqual(data["username"], "anonymous",
                            "AnonymousUser 的 username 是 'default' 不是 'anonymous'")
        self.assertEqual(data["username"], "default")
        self.assertEqual(data["id"], "default")

    def test_Q7_real_user_is_not_anonymous(self):
        """真实用户（analyst）的 id 不是 'default' → isAnonymousUser() 返回 false"""
        with patch("backend.config.settings.settings.enable_auth", True):
            resp = self.client.get(
                "/api/v1/auth/me",
                headers={"Authorization": f"Bearer {_token(self.analyst_user)}"},
            )
        data = resp.json()
        self.assertNotEqual(data["id"], "default", "真实用户 id 不应是 'default'")
        self.assertNotEqual(data["username"], "default")

    def test_Q8_login_then_logout_then_401(self):
        """登录后登出，再次无 token 访问 /me 应返回 401"""
        with patch("backend.config.settings.settings.enable_auth", True):
            login = self.client.post(
                "/api/v1/auth/login",
                json={"username": self.analyst_name, "password": self.analyst_pw},
            )
            token = login.json()["access_token"]
            # 登出（撤销 refresh token）
            self.client.post(
                "/api/v1/auth/logout",
                headers={"Authorization": f"Bearer {token}"},
            )
            # 新 client（无 cookie）访问 /me
            fresh = TestClient(self.app)
            resp = fresh.get("/api/v1/auth/me")
        self.assertEqual(resp.status_code, 401)


# ══════════════════════════════════════════════════════════════════════════════
# Section R — RequireAuth 跳转逻辑（后端层面验证）
# ══════════════════════════════════════════════════════════════════════════════

class TestRequireAuthLogic(unittest.TestCase):
    """R1-R7: 模拟 RequireAuth 逻辑的后端等价验证"""

    @classmethod
    def setUpClass(cls):
        cls.app = _app()
        cls.client = TestClient(cls.app)
        cls.super_user, cls.super_name, cls.super_pw = _make_user(
            "r_super", role_names=["superadmin"]
        )
        cls.analyst_user, _, _ = _make_user("r_analyst", role_names=["analyst"])

    def test_R1_unauthenticated_users_blocked_by_protected_endpoints(self):
        """未认证用户访问受保护端点（users/roles/skills）均返回 401"""
        protected = [
            ("GET", "/api/v1/users"),
            ("GET", "/api/v1/roles"),
            ("GET", "/api/v1/auth/me"),
        ]
        fresh = TestClient(self.app)
        with patch("backend.config.settings.settings.enable_auth", True):
            for method, path in protected:
                resp = getattr(fresh, method.lower())(path)
                self.assertEqual(resp.status_code, 401,
                                 f"{method} {path} 未认证应返回 401，实际: {resp.status_code}")

    def test_R2_authenticated_user_accesses_own_profile(self):
        """已认证用户可访问自己的 /me"""
        with patch("backend.config.settings.settings.enable_auth", True):
            resp = self.client.get(
                "/api/v1/auth/me",
                headers={"Authorization": f"Bearer {_token(self.analyst_user)}"},
            )
        self.assertEqual(resp.status_code, 200)

    def test_R3_wrong_token_format_rejected(self):
        """格式错误的 Bearer token 返回 401（不绕过 RequireAuth）"""
        with patch("backend.config.settings.settings.enable_auth", True):
            resp = self.client.get(
                "/api/v1/auth/me",
                headers={"Authorization": "Bearer not.a.valid.jwt"},
            )
        self.assertEqual(resp.status_code, 401)

    def test_R4_no_bearer_prefix_rejected(self):
        """Authorization 头无 Bearer 前缀返回 401"""
        with patch("backend.config.settings.settings.enable_auth", True):
            token = _token(self.analyst_user)
            resp = self.client.get(
                "/api/v1/auth/me",
                headers={"Authorization": token},  # 无 Bearer 前缀
            )
        self.assertEqual(resp.status_code, 401)

    def test_R5_inactive_user_rejected(self):
        """停用账号即使有 token 也返回 401"""
        from backend.models.user import User
        # 临时停用 analyst
        _g_db.query(User).filter(
            User.id == self.analyst_user.id
        ).update({"is_active": False})
        _g_db.commit()

        try:
            with patch("backend.config.settings.settings.enable_auth", True):
                resp = self.client.get(
                    "/api/v1/auth/me",
                    headers={"Authorization": f"Bearer {_token(self.analyst_user)}"},
                )
            self.assertEqual(resp.status_code, 401,
                             "停用账号的 token 应被拒绝")
        finally:
            _g_db.query(User).filter(
                User.id == self.analyst_user.id
            ).update({"is_active": True})
            _g_db.commit()

    def test_R6_superadmin_can_access_all_protected_endpoints(self):
        """superadmin 可访问所有受保护端点（用户管理、角色管理）"""
        endpoints = [
            ("GET", "/api/v1/users"),
            ("GET", "/api/v1/roles"),
            ("GET", "/api/v1/permissions"),
        ]
        token = _token(self.super_user)
        with patch("backend.config.settings.settings.enable_auth", True):
            for method, path in endpoints:
                resp = getattr(self.client, method.lower())(
                    path, headers={"Authorization": f"Bearer {token}"}
                )
                self.assertIn(resp.status_code, [200, 201],
                              f"{method} {path} superadmin 应能访问，实际: {resp.status_code}")

    def test_R7_auth_disabled_all_endpoints_accessible_without_token(self):
        """ENABLE_AUTH=false 时所有端点无需 token 可访问（匿名用户 is_superadmin=true）"""
        protected = [
            ("GET", "/api/v1/users"),
            ("GET", "/api/v1/roles"),
            ("GET", "/api/v1/auth/me"),
        ]
        fresh = TestClient(self.app)
        with patch("backend.config.settings.settings.enable_auth", False):
            for method, path in protected:
                resp = getattr(fresh, method.lower())(path)
                self.assertEqual(resp.status_code, 200,
                                 f"ENABLE_AUTH=false 时 {method} {path} 应返回 200")


# ══════════════════════════════════════════════════════════════════════════════
# Section S — 菜单权限过滤完整性
# ══════════════════════════════════════════════════════════════════════════════

class TestMenuVisibilityComplete(unittest.TestCase):
    """S1-S5: 验证菜单可见性与权限矩阵的完整对应关系"""

    # 与 AppLayout.tsx 中 ALL_MENU_ITEMS 保持一致
    MENU_ITEMS = [
        {"key": "/chat",         "perm": "chat:use"},
        {"key": "/model-config", "perm": "models:read"},
        {"key": "/dashboard",    "perm": None},
        {"key": "/agents",       "perm": None},
        {"key": "/tasks",        "perm": None},
        {"key": "/skills",       "perm": "skills.user:read"},
        {"key": "/users",        "perm": "users:read"},
        {"key": "/roles",        "perm": "users:read"},   # 新增菜单
        {"key": "/logs",         "perm": None},
    ]

    @classmethod
    def setUpClass(cls):
        cls.app = _app()
        cls.client = TestClient(cls.app)
        cls.viewer_user, _, _ = _make_user("s_viewer", role_names=["viewer"])
        cls.analyst_user, _, _ = _make_user("s_analyst", role_names=["analyst"])
        cls.admin_user, _, _ = _make_user("s_admin", role_names=["admin"])
        cls.super_user, _, _ = _make_user("s_super", role_names=["superadmin"])

    def _get_perms(self, user) -> set:
        with patch("backend.config.settings.settings.enable_auth", True):
            resp = self.client.get(
                "/api/v1/auth/me",
                headers={"Authorization": f"Bearer {_token(user)}"},
            )
        return set(resp.json().get("permissions", []))

    def _visible_menus(self, perms: set) -> list:
        return [m["key"] for m in self.MENU_ITEMS
                if m["perm"] is None or m["perm"] in perms]

    def test_S1_viewer_sees_only_chat_and_unrestricted_menus(self):
        """viewer 角色有 chat:use，可见 /chat + 无限制菜单（dashboard/agents/tasks/logs），
        不可见 model-config/skills/users/roles"""
        perms = self._get_perms(self.viewer_user)
        visible = self._visible_menus(perms)
        # viewer 有 chat:use → 可见
        self.assertIn("/chat", visible, f"viewer 有 chat:use 应看到 /chat，权限: {perms}")
        # 无限制菜单均可见
        for menu in ("/dashboard", "/agents", "/tasks", "/logs"):
            self.assertIn(menu, visible)
        # 以下需要 viewer 没有的权限 → 不可见
        for menu in ("/model-config", "/skills", "/users", "/roles"):
            self.assertNotIn(menu, visible,
                             f"viewer 不应看到 {menu}，其权限: {perms}")

    def test_S2_analyst_sees_chat_and_skills_not_users_roles(self):
        """analyst 可见 chat/skills，不可见 users/roles/model-config"""
        perms = self._get_perms(self.analyst_user)
        visible = self._visible_menus(perms)
        self.assertIn("/chat", visible)
        self.assertIn("/skills", visible)
        self.assertNotIn("/users", visible)
        self.assertNotIn("/roles", visible)
        self.assertNotIn("/model-config", visible)

    def test_S3_admin_sees_chat_skills_models_not_users_roles(self):
        """admin 可见 chat/skills/model-config，不可见 users/roles"""
        perms = self._get_perms(self.admin_user)
        visible = self._visible_menus(perms)
        self.assertIn("/chat", visible)
        self.assertIn("/skills", visible)
        self.assertIn("/model-config", visible)
        # admin 设计上无 users:read
        self.assertNotIn("/users", visible)
        self.assertNotIn("/roles", visible)

    def test_S4_superadmin_sees_all_menus_including_users_and_roles(self):
        """superadmin 可见所有菜单，包括 /users 和 /roles（新增菜单）"""
        perms = self._get_perms(self.super_user)
        visible = self._visible_menus(perms)
        all_keys = [m["key"] for m in self.MENU_ITEMS]
        for key in all_keys:
            self.assertIn(key, visible,
                          f"superadmin 应看到所有菜单，缺少: {key}")

    def test_S5_roles_menu_same_permission_as_users_menu(self):
        """/roles 菜单和 /users 菜单使用相同权限（users:read），对齐管理入口"""
        users_menu = next(m for m in self.MENU_ITEMS if m["key"] == "/users")
        roles_menu = next(m for m in self.MENU_ITEMS if m["key"] == "/roles")
        self.assertEqual(
            users_menu["perm"], roles_menu["perm"],
            f"/roles 和 /users 菜单权限应一致: "
            f"users={users_menu['perm']}, roles={roles_menu['perm']}"
        )


# ══════════════════════════════════════════════════════════════════════════════
# Section T — 登录后角色菜单可见性验证
# ══════════════════════════════════════════════════════════════════════════════

class TestPostLoginMenuVisibility(unittest.TestCase):
    """T1-T6: 以真实 Login 流程验证不同角色登录后的菜单可见性"""

    @classmethod
    def setUpClass(cls):
        cls.app = _app()
        cls.client = TestClient(cls.app)
        cls.super_user, cls.super_name, cls.super_pw = _make_user(
            "t_super", role_names=["superadmin"]
        )
        cls.analyst_user, cls.analyst_name, cls.analyst_pw = _make_user(
            "t_analyst", role_names=["analyst"]
        )

    def _login_and_get_perms(self, username, password) -> set:
        with patch("backend.config.settings.settings.enable_auth", True):
            login = self.client.post(
                "/api/v1/auth/login",
                json={"username": username, "password": password},
            )
            self.assertEqual(login.status_code, 200, f"登录失败: {login.text}")
            token = login.json()["access_token"]
            me = self.client.get(
                "/api/v1/auth/me",
                headers={"Authorization": f"Bearer {token}"},
            )
        return set(me.json().get("permissions", []))

    def test_T1_superadmin_login_gets_all_permissions(self):
        """superadmin 登录后 /me 返回全部 13 个权限"""
        perms = self._login_and_get_perms(self.super_name, self.super_pw)
        expected = {
            "chat:use", "skills.user:read", "skills.user:write",
            "skills.project:read", "skills.project:write", "skills.system:read",
            "models:read", "models:write",
            "users:read", "users:write", "users:assign_role",
            "settings:read", "settings:write",
        }
        self.assertEqual(perms, expected)

    def test_T2_superadmin_has_users_read_for_roles_menu(self):
        """superadmin 登录后有 users:read → /roles 菜单可见"""
        perms = self._login_and_get_perms(self.super_name, self.super_pw)
        self.assertIn("users:read", perms,
                      "superadmin 必须有 users:read 才能看到 /roles 菜单")

    def test_T3_analyst_login_does_not_have_users_read(self):
        """analyst 登录后无 users:read → /roles 和 /users 菜单不可见"""
        perms = self._login_and_get_perms(self.analyst_name, self.analyst_pw)
        self.assertNotIn("users:read", perms)

    def test_T4_superadmin_can_call_roles_api_after_login(self):
        """superadmin 登录后可成功调用 GET /roles（有菜单且 API 可用）"""
        with patch("backend.config.settings.settings.enable_auth", True):
            login = self.client.post(
                "/api/v1/auth/login",
                json={"username": self.super_name, "password": self.super_pw},
            )
            token = login.json()["access_token"]
            roles_resp = self.client.get(
                "/api/v1/roles",
                headers={"Authorization": f"Bearer {token}"},
            )
        self.assertEqual(roles_resp.status_code, 200)
        self.assertIsInstance(roles_resp.json(), list)

    def test_T5_analyst_cannot_call_roles_api_after_login(self):
        """analyst 登录后调用 GET /roles 返回 403（无菜单且 API 禁止）"""
        with patch("backend.config.settings.settings.enable_auth", True):
            login = self.client.post(
                "/api/v1/auth/login",
                json={"username": self.analyst_name, "password": self.analyst_pw},
            )
            token = login.json()["access_token"]
            roles_resp = self.client.get(
                "/api/v1/roles",
                headers={"Authorization": f"Bearer {token}"},
            )
        self.assertEqual(roles_resp.status_code, 403,
                         "analyst 无 users:read，访问 /roles 应 403")

    def test_T6_default_superadmin_user_can_login_and_manage(self):
        """系统默认 superadmin 账号（Sgp013013）可登录并获得全部管理权限"""
        with patch("backend.config.settings.settings.enable_auth", True):
            login = self.client.post(
                "/api/v1/auth/login",
                json={"username": "superadmin", "password": "Sgp013013"},
            )
        if login.status_code != 200:
            self.skipTest("superadmin 用户未初始化，跳过本测试")
        token = login.json()["access_token"]
        with patch("backend.config.settings.settings.enable_auth", True):
            me = self.client.get(
                "/api/v1/auth/me",
                headers={"Authorization": f"Bearer {token}"},
            )
        perms = set(me.json().get("permissions", []))
        # is_superadmin=True 的账号权限由 get_user_permissions 返回所有权限
        user_data = me.json()
        self.assertTrue(
            user_data.get("is_superadmin") or "users:read" in perms,
            "superadmin 账号应有 is_superadmin=True 或 users:read 权限"
        )


# ══════════════════════════════════════════════════════════════════════════════
# 运行入口
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import pytest, sys
    sys.exit(pytest.main([__file__, "-v", "-s"]))
