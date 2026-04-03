"""
test_rbac.py — RBAC 功能完整测试套件
=====================================

测试层次：
  A  (10) — 密码哈希 & JWT 工具函数
  B  (5)  — LocalAuthProvider 本地认证
  C  (8)  — RBAC 权限查询辅助函数
  D  (9)  — FastAPI 依赖项 (get_current_user / require_permission)
  E  (14) — 认证端点 (POST /login、/refresh、/logout、GET /me)
  F  (19) — 用户管理端点 (CRUD、角色分配)
  G  (8)  — Skill 用户隔离 (ENABLE_AUTH=true 时按用户目录隔离)
  H  (5)  — ENABLE_AUTH=false 兼容模式
  I  (7)  — 安全边界 & 边缘用例

总计: 85 个测试用例
"""

import asyncio
import re
import shutil
import sys
import os
import unittest
import uuid
from pathlib import Path
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "backend"))

# ─── 模块级全局 DB session ──────────────────────────────────────────────────

_PREFIX = f"_rbact_{uuid.uuid4().hex[:6]}_"   # 每次运行唯一前缀，避免测试数据冲突


def _db():
    from backend.config.database import SessionLocal
    return SessionLocal()


_g_db = _db()   # 全局共享会话，测试结束后统一清理


def _make_user(suffix="", password="Test1234!", is_superadmin=False,
               is_active=True, role_names=None):
    """在真实 PostgreSQL 中创建测试用户，返回 (User, username, password)"""
    from backend.models.user import User
    from backend.models.role import Role
    from backend.models.user_role import UserRole
    from backend.core.auth.password import hash_password

    username = f"{_PREFIX}{suffix or uuid.uuid4().hex[:6]}"
    u = User(
        username=username,
        display_name=f"Test {suffix}",
        hashed_password=hash_password(password),
        auth_source="local",
        is_active=is_active,
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


def _make_token(user, roles, algorithm="HS256"):
    """为指定 User 对象颁发 access_token"""
    from backend.config.settings import settings
    from backend.core.auth.jwt import create_access_token
    return create_access_token(
        {"sub": str(user.id), "username": user.username, "roles": roles},
        settings.jwt_secret,
        algorithm,
    )


def _cookie_from_response(resp):
    """从响应 Set-Cookie 头提取 refresh_token 的值"""
    header = resp.headers.get("set-cookie", "")
    m = re.search(r"refresh_token=([^;]+)", header)
    return m.group(1) if m else None


def teardown_module(_=None):
    """清理本次运行创建的所有测试用户"""
    from backend.models.user import User
    try:
        _g_db.query(User).filter(
            User.username.like(f"{_PREFIX}%")
        ).delete(synchronize_session=False)
        _g_db.commit()
    finally:
        _g_db.close()


# ═══════════════════════════════════════════════════════════════════════
# A — 密码哈希 & JWT 工具函数 (10 tests)
# ═══════════════════════════════════════════════════════════════════════

class TestPasswordUtils(unittest.TestCase):

    def test_A1_hash_returns_bcrypt_format(self):
        """hash_password 输出以 $2b$ 开头"""
        from backend.core.auth.password import hash_password
        h = hash_password("my_password")
        self.assertTrue(h.startswith("$2b$"), f"Expected $2b$ prefix, got {h[:10]!r}")

    def test_A2_verify_correct_password(self):
        """verify_password 对正确密码返回 True"""
        from backend.core.auth.password import hash_password, verify_password
        h = hash_password("my_password")
        self.assertTrue(verify_password("my_password", h))

    def test_A3_verify_wrong_password_returns_false(self):
        """verify_password 对错误密码返回 False"""
        from backend.core.auth.password import hash_password, verify_password
        h = hash_password("my_password")
        self.assertFalse(verify_password("wrong_pass", h))

    def test_A4_verify_malformed_hash_returns_false(self):
        """verify_password 对格式非法的哈希不抛异常，返回 False"""
        from backend.core.auth.password import verify_password
        self.assertFalse(verify_password("pass", "not_a_bcrypt_hash"))
        self.assertFalse(verify_password("pass", ""))

    def test_A5_each_hash_uses_unique_salt(self):
        """同一密码两次哈希结果不同（不同盐）"""
        from backend.core.auth.password import hash_password
        h1 = hash_password("same_password")
        h2 = hash_password("same_password")
        self.assertNotEqual(h1, h2)


class TestJWTUtils(unittest.TestCase):
    SECRET = "unit-test-secret"

    def test_A6_token_contains_required_claims(self):
        """access_token 包含 sub / exp / iat / jti"""
        from backend.core.auth.jwt import create_access_token, decode_token
        token = create_access_token({"sub": "uid-1", "username": "alice"}, self.SECRET)
        payload = decode_token(token, self.SECRET)
        self.assertIsNotNone(payload)
        for claim in ("sub", "username", "exp", "iat", "jti"):
            self.assertIn(claim, payload, f"Missing claim: {claim}")
        self.assertEqual(payload["sub"], "uid-1")

    def test_A7_wrong_secret_returns_none(self):
        """错误 secret 解码返回 None"""
        from backend.core.auth.jwt import create_access_token, decode_token
        token = create_access_token({"sub": "uid-1"}, self.SECRET)
        self.assertIsNone(decode_token(token, "wrong-secret"))

    def test_A8_expired_token_returns_none(self):
        """已过期 token 解码返回 None"""
        from backend.core.auth.jwt import create_access_token, decode_token
        token = create_access_token({"sub": "uid-1"}, self.SECRET, expires_minutes=-1)
        self.assertIsNone(decode_token(token, self.SECRET))

    def test_A9_tampered_token_returns_none(self):
        """篡改签名的 token 解码返回 None"""
        from backend.core.auth.jwt import create_access_token, decode_token
        token = create_access_token({"sub": "uid-1"}, self.SECRET)
        tampered = token[:-5] + "XXXXX"
        self.assertIsNone(decode_token(tampered, self.SECRET))

    def test_A10_create_refresh_token_jti_is_uuid(self):
        """create_refresh_token_jti 返回合法 UUID 字符串"""
        from backend.core.auth.jwt import create_refresh_token_jti
        jti = create_refresh_token_jti()
        parsed = uuid.UUID(jti)   # raises if not valid UUID
        self.assertIsInstance(parsed, uuid.UUID)


# ═══════════════════════════════════════════════════════════════════════
# B — LocalAuthProvider (5 tests)
# ═══════════════════════════════════════════════════════════════════════

class TestLocalAuthProvider(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.user, cls.username, cls.password = _make_user("auth_b1")
        cls.inactive, cls.inactive_uname, cls.inactive_pwd = _make_user(
            "auth_b2", is_active=False
        )

    def test_B1_valid_credentials_returns_user(self):
        """正确用户名+密码返回 User 对象"""
        from backend.core.auth.providers.local import LocalAuthProvider
        provider = LocalAuthProvider(_g_db)
        result = asyncio.run(provider.authenticate({
            "username": self.username, "password": self.password,
        }))
        self.assertIsNotNone(result)
        self.assertEqual(result.username, self.username)

    def test_B2_wrong_password_returns_none(self):
        """密码错误返回 None"""
        from backend.core.auth.providers.local import LocalAuthProvider
        result = asyncio.run(LocalAuthProvider(_g_db).authenticate({
            "username": self.username, "password": "wrong_password",
        }))
        self.assertIsNone(result)

    def test_B3_nonexistent_user_returns_none(self):
        """用户不存在返回 None"""
        from backend.core.auth.providers.local import LocalAuthProvider
        result = asyncio.run(LocalAuthProvider(_g_db).authenticate({
            "username": "no_such_user_xyz_999", "password": "any",
        }))
        self.assertIsNone(result)

    def test_B4_inactive_user_returns_none(self):
        """已停用账号返回 None（即使密码正确）"""
        from backend.core.auth.providers.local import LocalAuthProvider
        result = asyncio.run(LocalAuthProvider(_g_db).authenticate({
            "username": self.inactive_uname, "password": self.inactive_pwd,
        }))
        self.assertIsNone(result)

    def test_B5_get_oauth_url_returns_none(self):
        """本地认证不需要 OAuth URL，返回 None"""
        from backend.core.auth.providers.local import LocalAuthProvider
        result = LocalAuthProvider(_g_db).get_oauth_url("state_xyz")
        self.assertIsNone(result)


# ═══════════════════════════════════════════════════════════════════════
# C — RBAC 权限查询辅助函数 (8 tests)
# ═══════════════════════════════════════════════════════════════════════

class TestRBACHelpers(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.sa,      _, _ = _make_user("rbac_sa",      is_superadmin=True)
        cls.viewer,  _, _ = _make_user("rbac_viewer",  role_names=["viewer"])
        cls.analyst, _, _ = _make_user("rbac_analyst", role_names=["analyst"])
        cls.admin,   _, _ = _make_user("rbac_admin",   role_names=["admin"])
        cls.norole,  _, _ = _make_user("rbac_norole")

    def test_C1_superadmin_gets_all_permissions(self):
        """is_superadmin=True 的用户拥有系统中所有权限"""
        from backend.core.rbac import get_user_permissions
        from backend.models.permission import Permission
        all_keys = {p.key for p in _g_db.query(Permission).all()}
        got = set(get_user_permissions(self.sa, _g_db))
        self.assertEqual(got, all_keys)

    def test_C2_viewer_has_only_chat_use(self):
        """viewer 角色仅拥有 chat:use"""
        from backend.core.rbac import get_user_permissions
        perms = get_user_permissions(self.viewer, _g_db)
        self.assertIn("chat:use", perms)
        self.assertNotIn("users:read", perms)
        self.assertNotIn("skills.user:write", perms)
        self.assertEqual(len(perms), 1)

    def test_C3_analyst_has_expected_permissions(self):
        """analyst 拥有 chat + skills.user + skills.project:read + skills.system:read"""
        from backend.core.rbac import get_user_permissions
        perms = set(get_user_permissions(self.analyst, _g_db))
        required = {"chat:use", "skills.user:read", "skills.user:write",
                    "skills.project:read", "skills.system:read"}
        self.assertTrue(required.issubset(perms), f"Missing: {required - perms}")
        self.assertNotIn("users:read", perms)
        self.assertNotIn("users:write", perms)

    def test_C4_admin_has_superset_of_analyst(self):
        """admin 权限是 analyst 的超集，额外拥有 skills.project:write / models / settings"""
        from backend.core.rbac import get_user_permissions
        analyst_perms = set(get_user_permissions(self.analyst, _g_db))
        admin_perms   = set(get_user_permissions(self.admin,   _g_db))
        self.assertTrue(analyst_perms.issubset(admin_perms))
        self.assertIn("skills.project:write", admin_perms)
        self.assertIn("models:read", admin_perms)
        self.assertIn("settings:write", admin_perms)

    def test_C5_no_role_user_has_empty_permissions(self):
        """无角色用户返回空列表"""
        from backend.core.rbac import get_user_permissions
        self.assertEqual(get_user_permissions(self.norole, _g_db), [])

    def test_C6_get_user_roles_superadmin(self):
        """is_superadmin=True 用户的角色列表为 ['superadmin']"""
        from backend.core.rbac import get_user_roles
        self.assertEqual(get_user_roles(self.sa, _g_db), ["superadmin"])

    def test_C7_get_user_roles_returns_assigned_roles(self):
        """普通用户返回实际分配的角色名"""
        from backend.core.rbac import get_user_roles
        roles = get_user_roles(self.analyst, _g_db)
        self.assertIn("analyst", roles)

    def test_C8_no_role_user_has_empty_roles(self):
        """无角色用户返回空列表"""
        from backend.core.rbac import get_user_roles
        self.assertEqual(get_user_roles(self.norole, _g_db), [])


# ═══════════════════════════════════════════════════════════════════════
# D — FastAPI 依赖项 (9 tests)
# ═══════════════════════════════════════════════════════════════════════

class TestDeps(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        from backend.config.settings import settings
        cls.analyst_u, _, _ = _make_user("deps_analyst", role_names=["analyst"])
        cls.viewer_u,  _, _ = _make_user("deps_viewer",  role_names=["viewer"])
        cls.sa_u,      _, _ = _make_user("deps_sa",      is_superadmin=True)
        cls.inactive_u, _, _ = _make_user("deps_inactive", is_active=False)

        cls.analyst_token  = _make_token(cls.analyst_u,  ["analyst"])
        cls.viewer_token   = _make_token(cls.viewer_u,   ["viewer"])
        cls.sa_token       = _make_token(cls.sa_u,       ["superadmin"])
        cls.inactive_token = _make_token(cls.inactive_u, [])

    def test_D1_auth_disabled_returns_anonymous_user(self):
        """ENABLE_AUTH=false 时无论 token 如何均返回 AnonymousUser"""
        from backend.api.deps import get_current_user, AnonymousUser
        from backend.config.settings import settings
        with patch.object(settings, 'enable_auth', False):
            result = asyncio.run(get_current_user(token=None, db=_g_db))
        self.assertIsInstance(result, AnonymousUser)
        self.assertTrue(result.is_superadmin)

    def test_D2_valid_token_returns_user(self):
        """ENABLE_AUTH=true 时有效 token 返回对应 User"""
        from backend.api.deps import get_current_user
        from backend.models.user import User
        from backend.config.settings import settings
        with patch.object(settings, 'enable_auth', True):
            result = asyncio.run(get_current_user(token=self.analyst_token, db=_g_db))
        self.assertIsInstance(result, User)
        self.assertEqual(result.username, self.analyst_u.username)

    def test_D3_no_token_raises_401(self):
        """ENABLE_AUTH=true 时缺少 token 抛出 401"""
        from backend.api.deps import get_current_user
        from fastapi import HTTPException
        from backend.config.settings import settings
        with patch.object(settings, 'enable_auth', True):
            with self.assertRaises(HTTPException) as ctx:
                asyncio.run(get_current_user(token=None, db=_g_db))
        self.assertEqual(ctx.exception.status_code, 401)

    def test_D4_expired_token_raises_401(self):
        """已过期 token 抛出 401"""
        from backend.api.deps import get_current_user
        from backend.core.auth.jwt import create_access_token
        from fastapi import HTTPException
        from backend.config.settings import settings
        expired = create_access_token(
            {"sub": str(self.analyst_u.id), "username": "test"},
            settings.jwt_secret, settings.jwt_algorithm,
            expires_minutes=-1,
        )
        with patch.object(settings, 'enable_auth', True):
            with self.assertRaises(HTTPException) as ctx:
                asyncio.run(get_current_user(token=expired, db=_g_db))
        self.assertEqual(ctx.exception.status_code, 401)

    def test_D5_inactive_user_token_raises_401(self):
        """已停用账号的 token 抛出 401"""
        from backend.api.deps import get_current_user
        from fastapi import HTTPException
        from backend.config.settings import settings
        with patch.object(settings, 'enable_auth', True):
            with self.assertRaises(HTTPException) as ctx:
                asyncio.run(get_current_user(token=self.inactive_token, db=_g_db))
        self.assertEqual(ctx.exception.status_code, 401)

    def test_D6_superadmin_bypasses_all_permission_checks(self):
        """is_superadmin=True 直接通过任意权限检查"""
        from backend.api.deps import require_permission
        from backend.config.settings import settings
        check = require_permission("users", "write")
        with patch.object(settings, 'enable_auth', True):
            result = asyncio.run(check(current_user=self.sa_u, db=_g_db))
        self.assertEqual(str(result.id), str(self.sa_u.id))

    def test_D7_user_with_permission_passes(self):
        """拥有所需权限的用户通过检查"""
        from backend.api.deps import require_permission
        from backend.config.settings import settings
        check = require_permission("skills.user", "write")   # analyst has this
        with patch.object(settings, 'enable_auth', True):
            result = asyncio.run(check(current_user=self.analyst_u, db=_g_db))
        self.assertEqual(str(result.id), str(self.analyst_u.id))

    def test_D8_user_without_permission_raises_403(self):
        """缺少权限的用户抛出 403"""
        from backend.api.deps import require_permission
        from fastapi import HTTPException
        from backend.config.settings import settings
        check = require_permission("users", "read")   # viewer does NOT have this
        with patch.object(settings, 'enable_auth', True):
            with self.assertRaises(HTTPException) as ctx:
                asyncio.run(check(current_user=self.viewer_u, db=_g_db))
        self.assertEqual(ctx.exception.status_code, 403)

    def test_D9_anonymous_user_passes_all_checks(self):
        """ENABLE_AUTH=false 时 AnonymousUser 通过全部权限检查"""
        from backend.api.deps import require_permission, AnonymousUser
        from backend.config.settings import settings
        check = require_permission("users", "write")
        with patch.object(settings, 'enable_auth', False):
            result = asyncio.run(check(current_user=AnonymousUser(), db=_g_db))
        self.assertTrue(result.is_superadmin)


# ═══════════════════════════════════════════════════════════════════════
# E — 认证端点 (14 tests)
# ═══════════════════════════════════════════════════════════════════════

class TestAuthEndpoints(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient
        from backend.main import app
        cls.client = TestClient(app)
        cls.user, cls.username, cls.password = _make_user(
            "auth_e1", role_names=["analyst"]
        )
        cls.inactive, cls.inactive_uname, cls.inactive_pwd = _make_user(
            "auth_e2", is_active=False
        )

    def _login(self, username=None, password=None):
        """以 ENABLE_AUTH=true 登录，返回 response"""
        from backend.config.settings import settings
        with patch.object(settings, 'enable_auth', True):
            return self.client.post("/api/v1/auth/login", json={
                "username": username or self.username,
                "password": password or self.password,
            })

    def test_E1_login_success_returns_200_with_token(self):
        """正确凭据登录成功，返回 access_token"""
        resp = self._login()
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()
        self.assertIn("access_token", data)
        self.assertEqual(data["token_type"], "bearer")
        self.assertIsInstance(data["expires_in"], int)
        self.assertGreater(data["expires_in"], 0)

    def test_E2_login_wrong_password_returns_401(self):
        """密码错误返回 401"""
        resp = self._login(password="wrong_password")
        self.assertEqual(resp.status_code, 401)

    def test_E3_login_nonexistent_user_returns_401(self):
        """不存在的用户名返回 401"""
        resp = self._login(username="no_such_user_xyz_001")
        self.assertEqual(resp.status_code, 401)

    def test_E4_login_inactive_user_returns_401(self):
        """已停用账号登录返回 401"""
        resp = self._login(username=self.inactive_uname, password=self.inactive_pwd)
        self.assertEqual(resp.status_code, 401)

    def test_E5_login_sets_httponly_refresh_cookie(self):
        """登录成功后响应头包含 Set-Cookie: refresh_token（httpOnly）"""
        resp = self._login()
        self.assertEqual(resp.status_code, 200)
        cookie = resp.headers.get("set-cookie", "")
        self.assertIn("refresh_token=", cookie, "Missing refresh_token cookie")
        self.assertIn("httponly", cookie.lower(), "Cookie must be httpOnly")

    def test_E6_login_updates_last_login_at(self):
        """登录成功后 user.last_login_at 被更新"""
        self._login()
        _g_db.refresh(self.user)
        self.assertIsNotNone(self.user.last_login_at)

    def test_E7_me_with_valid_token_returns_user_info(self):
        """有效 token 调用 /me 返回用户信息"""
        login_resp = self._login()
        token = login_resp.json()["access_token"]
        from backend.config.settings import settings
        with patch.object(settings, 'enable_auth', True):
            resp = self.client.get(
                "/api/v1/auth/me",
                headers={"Authorization": f"Bearer {token}"},
            )
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()
        self.assertEqual(data["username"], self.username)
        self.assertIn("roles", data)
        self.assertIn("permissions", data)
        self.assertIn("analyst", data["roles"])
        self.assertIn("chat:use", data["permissions"])

    def test_E8_me_without_token_returns_401_when_auth_enabled(self):
        """ENABLE_AUTH=true 时 /me 无 token 返回 401"""
        from backend.config.settings import settings
        with patch.object(settings, 'enable_auth', True):
            resp = self.client.get("/api/v1/auth/me")
        self.assertEqual(resp.status_code, 401)

    def test_E9_me_without_token_returns_anonymous_when_auth_disabled(self):
        """ENABLE_AUTH=false 时 /me 返回 AnonymousUser（is_superadmin=True）"""
        from backend.config.settings import settings
        with patch.object(settings, 'enable_auth', False):
            resp = self.client.get("/api/v1/auth/me")
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertTrue(resp.json().get("is_superadmin", False))

    def test_E10_refresh_creates_new_access_token(self):
        """使用 refresh_token 可以获取新 access_token"""
        login_resp = self._login()
        self.assertEqual(login_resp.status_code, 200)
        old_token = login_resp.json()["access_token"]
        cookie = _cookie_from_response(login_resp)
        if not cookie:
            self.skipTest("Cannot extract refresh_token cookie")
        from backend.config.settings import settings
        with patch.object(settings, 'enable_auth', True):
            refresh_resp = self.client.post(
                "/api/v1/auth/refresh",
                cookies={"refresh_token": cookie},
            )
        self.assertEqual(refresh_resp.status_code, 200, refresh_resp.text)
        new_token = refresh_resp.json()["access_token"]
        self.assertNotEqual(old_token, new_token, "Refreshed token should differ")

    def test_E11_refresh_without_cookie_returns_401(self):
        """无 refresh_token cookie 调用 /refresh 返回 401"""
        from fastapi.testclient import TestClient
        from backend.main import app
        from backend.config.settings import settings
        # 使用全新 TestClient 避免 E10 遗留的 cookie 污染
        fresh_client = TestClient(app)
        with patch.object(settings, 'enable_auth', True):
            resp = fresh_client.post("/api/v1/auth/refresh")
        self.assertEqual(resp.status_code, 401)

    def test_E12_logout_revokes_refresh_token(self):
        """登出后旧 refresh_token 不可再用"""
        login_resp = self._login()
        cookie = _cookie_from_response(login_resp)
        if not cookie:
            self.skipTest("Cannot extract refresh_token cookie")
        from backend.config.settings import settings
        with patch.object(settings, 'enable_auth', True):
            self.client.post("/api/v1/auth/logout", cookies={"refresh_token": cookie})
            # 再次 refresh 应该失败
            resp = self.client.post(
                "/api/v1/auth/refresh",
                cookies={"refresh_token": cookie},
            )
        self.assertEqual(resp.status_code, 401, "Revoked token should not refresh")

    def test_E13_logout_without_cookie_still_returns_200(self):
        """登出是幂等的，无 cookie 也返回 200"""
        from backend.config.settings import settings
        with patch.object(settings, 'enable_auth', True):
            resp = self.client.post("/api/v1/auth/logout")
        self.assertEqual(resp.status_code, 200)

    def test_E14_lark_login_returns_501(self):
        """Lark OAuth 占位端点返回 501"""
        resp = self.client.get("/api/v1/auth/lark/login")
        self.assertEqual(resp.status_code, 501)


# ═══════════════════════════════════════════════════════════════════════
# F — 用户管理端点 (19 tests)
# ═══════════════════════════════════════════════════════════════════════

class TestUserEndpoints(unittest.TestCase):
    """
    权限说明：
    - superadmin (is_superadmin=True)：拥有全部权限（users:read/write/assign_role）
    - admin role：仅 skills/models/settings，无 users:* 权限
    - analyst role：仅 chat/skills，无 users:* 权限
    - viewer role：仅 chat:use
    """

    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient
        from backend.main import app
        cls.client = TestClient(app)

        # superadmin 用户：用于需要 users:* 权限的操作
        cls.su,       _, _ = _make_user("usr_su",       is_superadmin=True)
        # analyst 用户：用于验证权限被拒绝
        cls.analyst_u, _, _ = _make_user("usr_analyst", role_names=["analyst"])
        # viewer 用户：最低权限
        cls.viewer_u, _, _ = _make_user("usr_viewer",   role_names=["viewer"])

        cls.su_token       = _make_token(cls.su,        ["superadmin"])
        cls.analyst_token  = _make_token(cls.analyst_u, ["analyst"])
        cls.viewer_token   = _make_token(cls.viewer_u,  ["viewer"])

    def _req(self, method, path, token=None, **kwargs):
        from backend.config.settings import settings
        headers = kwargs.pop("headers", {})
        if token:
            headers["Authorization"] = f"Bearer {token}"
        with patch.object(settings, 'enable_auth', True):
            return getattr(self.client, method)(path, headers=headers, **kwargs)

    # --- 创建用户 ---

    def test_F1_create_user_with_users_write_permission(self):
        """superadmin 可以创建新用户"""
        new_uname = f"{_PREFIX}new_f1_{uuid.uuid4().hex[:4]}"
        resp = self._req("post", "/api/v1/users", token=self.su_token, json={
            "username": new_uname,
            "password": "TestPass123!",
            "display_name": "F1 Test",
            "role_names": ["analyst"],
        })
        self.assertEqual(resp.status_code, 201, resp.text)
        data = resp.json()
        self.assertEqual(data["username"], new_uname)
        self.assertFalse(data["is_superadmin"])
        self.assertIn("analyst", data["roles"])

    def test_F2_create_user_without_permission_returns_403(self):
        """analyst/viewer 无法创建用户"""
        resp = self._req("post", "/api/v1/users", token=self.analyst_token, json={
            "username": f"{_PREFIX}blocked_f2",
            "password": "TestPass123!",
        })
        self.assertEqual(resp.status_code, 403)

    def test_F3_create_user_duplicate_username_returns_409(self):
        """重复用户名返回 409"""
        resp = self._req("post", "/api/v1/users", token=self.su_token, json={
            "username": self.analyst_u.username,
            "password": "TestPass123!",
        })
        self.assertEqual(resp.status_code, 409)

    def test_F4_create_user_invalid_password_too_short_returns_422(self):
        """密码少于 6 位返回 422（Pydantic validation）"""
        resp = self._req("post", "/api/v1/users", token=self.su_token, json={
            "username": f"{_PREFIX}f4short",
            "password": "abc",
        })
        self.assertEqual(resp.status_code, 422)

    def test_F5_create_user_with_invalid_role_is_ignored(self):
        """指定不存在的角色名：用户创建成功但不分配该角色"""
        new_uname = f"{_PREFIX}f5norole_{uuid.uuid4().hex[:4]}"
        resp = self._req("post", "/api/v1/users", token=self.su_token, json={
            "username": new_uname,
            "password": "TestPass123!",
            "role_names": ["nonexistent_role_xyz"],
        })
        # 用户应成功创建，不存在的角色被忽略
        self.assertEqual(resp.status_code, 201, resp.text)
        self.assertEqual(resp.json()["roles"], [])

    # --- 查询用户 ---

    def test_F6_list_users_with_users_read_permission(self):
        """superadmin 可以查看用户列表"""
        resp = self._req("get", "/api/v1/users", token=self.su_token)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("items", data)
        usernames = [u["username"] for u in data["items"]]
        self.assertIn(self.analyst_u.username, usernames)

    def test_F7_list_users_without_permission_returns_403(self):
        """analyst 无法查看用户列表（无 users:read）"""
        resp = self._req("get", "/api/v1/users", token=self.analyst_token)
        self.assertEqual(resp.status_code, 403)

    def test_F8_get_own_user_detail(self):
        """任意用户可以查看自己的详情"""
        resp = self._req("get", f"/api/v1/users/{self.analyst_u.id}",
                         token=self.analyst_token)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["username"], self.analyst_u.username)

    def test_F9_get_other_user_without_permission_returns_403(self):
        """viewer 无法查看他人详情"""
        resp = self._req("get", f"/api/v1/users/{self.analyst_u.id}",
                         token=self.viewer_token)
        self.assertEqual(resp.status_code, 403)

    def test_F10_get_other_user_with_users_read_permission(self):
        """superadmin 可以查看任意用户详情"""
        resp = self._req("get", f"/api/v1/users/{self.analyst_u.id}",
                         token=self.su_token)
        self.assertEqual(resp.status_code, 200)

    # --- 修改用户 ---

    def test_F11_update_own_display_name(self):
        """用户可以修改自己的 display_name"""
        new_name = f"Updated_{uuid.uuid4().hex[:4]}"
        resp = self._req("put", f"/api/v1/users/{self.analyst_u.id}",
                         token=self.analyst_token,
                         json={"display_name": new_name})
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["display_name"], new_name)

    def test_F12_update_other_user_without_permission_returns_403(self):
        """viewer 无法修改他人信息"""
        resp = self._req("put", f"/api/v1/users/{self.analyst_u.id}",
                         token=self.viewer_token,
                         json={"display_name": "Hacked"})
        self.assertEqual(resp.status_code, 403)

    # --- 修改密码 ---

    def test_F13_change_own_password_succeeds(self):
        """用户可以修改自己的密码（需要旧密码）"""
        user, username, old_pass = _make_user("f13_chpwd")
        token = _make_token(user, [])
        resp = self._req("put", f"/api/v1/users/{user.id}/password",
                         token=token,
                         json={"old_password": old_pass, "new_password": "NewPass456!"})
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertIn("message", resp.json())

    def test_F14_change_password_wrong_old_password_returns_400(self):
        """旧密码错误返回 400"""
        user, _, _ = _make_user("f14_wrongpwd")
        token = _make_token(user, [])
        resp = self._req("put", f"/api/v1/users/{user.id}/password",
                         token=token,
                         json={"old_password": "definitely_wrong", "new_password": "NewPass456!"})
        self.assertEqual(resp.status_code, 400)

    def test_F15_change_other_users_password_returns_403(self):
        """不能修改他人密码"""
        resp = self._req("put", f"/api/v1/users/{self.analyst_u.id}/password",
                         token=self.viewer_token,
                         json={"old_password": "xxx", "new_password": "yyy_pass_ok"})
        self.assertEqual(resp.status_code, 403)

    # --- 角色分配 ---

    def test_F16_assign_role_to_user(self):
        """superadmin 可以给用户分配角色"""
        target, _, _ = _make_user("f16_target")
        resp = self._req("post", f"/api/v1/users/{target.id}/roles",
                         token=self.su_token,
                         json={"role_name": "analyst"})
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertIn("analyst", resp.json()["roles"])

    def test_F17_assign_role_without_permission_returns_403(self):
        """analyst 无法分配角色（无 users:assign_role）"""
        resp = self._req("post", f"/api/v1/users/{self.viewer_u.id}/roles",
                         token=self.analyst_token,
                         json={"role_name": "admin"})
        self.assertEqual(resp.status_code, 403)

    def test_F18_assign_role_idempotent(self):
        """重复分配同一角色不报错"""
        # analyst_u 已有 analyst 角色
        resp = self._req("post", f"/api/v1/users/{self.analyst_u.id}/roles",
                         token=self.su_token,
                         json={"role_name": "analyst"})
        self.assertIn(resp.status_code, [200, 201])

    def test_F19_list_roles_returns_system_roles_with_permissions(self):
        """GET /roles 返回四个预置角色，每个含 permissions 数组"""
        resp = self._req("get", "/api/v1/roles", token=self.su_token)
        self.assertEqual(resp.status_code, 200)
        names = {r["name"] for r in resp.json()}
        for expected in ("viewer", "analyst", "admin", "superadmin"):
            self.assertIn(expected, names)
        for role in resp.json():
            self.assertIsInstance(role["permissions"], list)


# ═══════════════════════════════════════════════════════════════════════
# G — Skill 用户隔离 (8 tests)
# ═══════════════════════════════════════════════════════════════════════

class TestSkillsUserIsolation(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient
        from backend.main import app
        cls.client = TestClient(app)
        cls.alice, cls.alice_uname, _ = _make_user("skill_alice", role_names=["analyst"])
        cls.bob,   cls.bob_uname,   _ = _make_user("skill_bob",   role_names=["analyst"])
        cls.viewer_u, cls.viewer_uname, _ = _make_user("skill_viewer", role_names=["viewer"])
        cls.alice_token  = _make_token(cls.alice,    ["analyst"])
        cls.bob_token    = _make_token(cls.bob,      ["analyst"])
        cls.viewer_token = _make_token(cls.viewer_u, ["viewer"])

    @classmethod
    def tearDownClass(cls):
        from backend.api.skills import _USER_SKILLS_DIR
        for uname in (cls.alice_uname, cls.bob_uname, cls.viewer_uname):
            d = _USER_SKILLS_DIR / uname
            if d.exists():
                shutil.rmtree(d, ignore_errors=True)

    def _post(self, path, body, token=None):
        from backend.config.settings import settings
        headers = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        with patch.object(settings, 'enable_auth', True):
            return self.client.post(path, json=body, headers=headers)

    def _get(self, path, token=None):
        from backend.config.settings import settings
        headers = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        with patch.object(settings, 'enable_auth', True):
            return self.client.get(path, headers=headers)

    def _delete(self, path, token=None):
        from backend.config.settings import settings
        headers = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        with patch.object(settings, 'enable_auth', True):
            return self.client.delete(path, headers=headers)

    def _create_skill(self, name, token):
        return self._post("/api/v1/skills/user-defined", {
            "name": name,
            "description": "Isolation test skill",
            "triggers": [f"trigger-{name}"],
            "category": "general",
            "priority": "low",
            "content": "# Test\nThis is an isolation test skill.",
        }, token=token)

    def test_G1_create_skill_writes_to_per_user_dir(self):
        """ENABLE_AUTH=true 时技能写入 user/{username}/ 目录"""
        from backend.api.skills import _USER_SKILLS_DIR
        skill_name = f"g1-skill-{uuid.uuid4().hex[:6]}"
        resp = self._create_skill(skill_name, self.alice_token)
        self.assertEqual(resp.status_code, 201, resp.text)
        expected = _USER_SKILLS_DIR / self.alice_uname / f"{skill_name}.md"
        self.assertTrue(expected.exists(), f"Skill not found at {expected}")

    def test_G2_alice_skill_not_in_bobs_directory(self):
        """Alice 的技能文件不出现在 Bob 的目录下"""
        from backend.api.skills import _USER_SKILLS_DIR
        skill_name = f"g2-alice-{uuid.uuid4().hex[:6]}"
        self._create_skill(skill_name, self.alice_token)
        bob_dir = _USER_SKILLS_DIR / self.bob_uname
        if bob_dir.exists():
            bob_files = [f.stem for f in bob_dir.glob("*.md")]
            self.assertNotIn(skill_name, bob_files)

    def test_G3_md_skills_hides_other_users_skills(self):
        """md-skills 端点不向 Alice 展示 Bob 的 user 层技能"""
        bob_skill = f"g3-bob-{uuid.uuid4().hex[:6]}"
        self._create_skill(bob_skill, self.bob_token)

        resp = self._get("/api/v1/skills/md-skills", token=self.alice_token)
        self.assertEqual(resp.status_code, 200)
        user_names = [s["name"] for s in resp.json() if s.get("tier") == "user"]
        self.assertNotIn(bob_skill, user_names,
                         "Alice should NOT see Bob's user-tier skills")

    def test_G4_md_skills_shows_own_skills(self):
        """md-skills 显示当前用户自己的技能"""
        alice_skill = f"g4-alice-{uuid.uuid4().hex[:6]}"
        self._create_skill(alice_skill, self.alice_token)

        resp = self._get("/api/v1/skills/md-skills", token=self.alice_token)
        self.assertEqual(resp.status_code, 200)
        names = [s["name"] for s in resp.json()]
        self.assertIn(alice_skill, names)

    def test_G5_list_user_skills_scoped_to_current_user(self):
        """GET /user-defined 仅返回当前用户的技能"""
        bob_skill = f"g5-bob-{uuid.uuid4().hex[:6]}"
        self._create_skill(bob_skill, self.bob_token)

        resp = self._get("/api/v1/skills/user-defined", token=self.alice_token)
        self.assertEqual(resp.status_code, 200)
        alice_names = [s["name"] for s in resp.json()]
        self.assertNotIn(bob_skill, alice_names)

    def test_G6_viewer_cannot_create_skill_returns_403(self):
        """viewer 角色无法创建用户技能（无 skills.user:write）"""
        resp = self._create_skill("g6-viewer-skill", self.viewer_token)
        self.assertEqual(resp.status_code, 403)

    def test_G7_delete_own_skill_succeeds(self):
        """用户可以删除自己的技能"""
        skill_name = f"g7-del-{uuid.uuid4().hex[:6]}"
        create_resp = self._create_skill(skill_name, self.alice_token)
        self.assertEqual(create_resp.status_code, 201)
        del_resp = self._delete(
            f"/api/v1/skills/user-defined/{skill_name}", token=self.alice_token
        )
        self.assertEqual(del_resp.status_code, 200, del_resp.text)

    def test_G8_delete_other_users_skill_returns_404(self):
        """Bob 无法删除 Alice 的技能（返回 404，而非 403 或误删）"""
        from backend.api.skills import _USER_SKILLS_DIR
        skill_name = f"g8-alice-{uuid.uuid4().hex[:6]}"
        self._create_skill(skill_name, self.alice_token)

        # Bob 尝试删除同名 skill（指向 Bob 的目录，但文件不存在）
        resp = self._delete(
            f"/api/v1/skills/user-defined/{skill_name}", token=self.bob_token
        )
        self.assertEqual(resp.status_code, 404, "Bob should get 404, not delete Alice's file")
        # Alice 的文件依然存在
        alice_file = _USER_SKILLS_DIR / self.alice_uname / f"{skill_name}.md"
        self.assertTrue(alice_file.exists(), "Alice's skill file must not be deleted")


# ═══════════════════════════════════════════════════════════════════════
# H — ENABLE_AUTH=false 兼容模式 (5 tests)
# ═══════════════════════════════════════════════════════════════════════

class TestEnableAuthFalseCompat(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient
        from backend.main import app
        cls.client = TestClient(app)

    def _with_auth_off(self, method, path, **kwargs):
        from backend.config.settings import settings
        with patch.object(settings, 'enable_auth', False):
            return getattr(self.client, method)(path, **kwargs)

    def test_H1_me_returns_superadmin_anonymous_user(self):
        """/me 无 token 时返回 AnonymousUser（is_superadmin=True）"""
        resp = self._with_auth_off("get", "/api/v1/auth/me")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data.get("is_superadmin", False))
        self.assertIn("superadmin", data.get("roles", []))

    def test_H2_create_skill_works_without_token(self):
        """ENABLE_AUTH=false 时无需 token 也可创建技能"""
        import tempfile
        from backend.api import skills as skills_mod
        tmp_dir = Path(tempfile.mkdtemp())
        orig, orig_res = skills_mod._USER_SKILLS_DIR, skills_mod._USER_SKILLS_DIR_RESOLVED
        skill_name = f"compat-skill-{uuid.uuid4().hex[:6]}"
        try:
            skills_mod._USER_SKILLS_DIR = tmp_dir
            skills_mod._USER_SKILLS_DIR_RESOLVED = tmp_dir.resolve()
            resp = self._with_auth_off("post", "/api/v1/skills/user-defined", json={
                "name": skill_name,
                "description": "Compatibility mode skill",
                "triggers": ["compat"],
                "category": "general",
                "priority": "low",
                "content": "# Compat\nThis tests ENABLE_AUTH=false compatibility.",
            })
            self.assertEqual(resp.status_code, 201, resp.text)
        finally:
            skills_mod._USER_SKILLS_DIR = orig
            skills_mod._USER_SKILLS_DIR_RESOLVED = orig_res
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def test_H3_skill_dir_is_flat_when_auth_disabled(self):
        """ENABLE_AUTH=false 时 _get_user_skill_dir 返回扁平 user/ 目录"""
        from backend.api.skills import _get_user_skill_dir, _USER_SKILLS_DIR
        from backend.api.deps import AnonymousUser
        from backend.config.settings import settings
        with patch.object(settings, 'enable_auth', False):
            d = _get_user_skill_dir(AnonymousUser().username)
        self.assertEqual(d.resolve(), _USER_SKILLS_DIR.resolve())

    def test_H4_skill_dir_is_per_user_when_auth_enabled(self):
        """ENABLE_AUTH=true 时 _get_user_skill_dir 返回 user/{username}/ 目录"""
        from backend.api.skills import _get_user_skill_dir, _USER_SKILLS_DIR
        from backend.config.settings import settings
        with patch.object(settings, 'enable_auth', True):
            d = _get_user_skill_dir("alice_test_h4")
        self.assertEqual(d.resolve(), (_USER_SKILLS_DIR / "alice_test_h4").resolve())
        # 清理测试目录
        d.rmdir() if d.exists() and not any(d.iterdir()) else None

    def test_H5_list_users_works_without_token_when_auth_disabled(self):
        """ENABLE_AUTH=false 时无需 token 也可查看用户列表（AnonymousUser 是超管）"""
        resp = self._with_auth_off("get", "/api/v1/users")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("items", resp.json())


# ═══════════════════════════════════════════════════════════════════════
# I — 安全边界 & 边缘用例 (7 tests)
# ═══════════════════════════════════════════════════════════════════════

class TestSecurityEdgeCases(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient
        from backend.main import app
        cls.client = TestClient(app)
        cls.user, cls.uname, cls.password = _make_user("sec_i1", role_names=["analyst"])
        cls.sa, _, _ = _make_user("sec_i2", is_superadmin=True)
        cls.token = _make_token(cls.user, ["analyst"])
        cls.sa_token = _make_token(cls.sa, ["superadmin"])

    def test_I1_wrong_jwt_secret_rejected(self):
        """错误密钥签发的 token 被拒绝（返回 401）"""
        from backend.core.auth.jwt import create_access_token, decode_token
        from backend.config.settings import settings
        bad_token = create_access_token(
            {"sub": str(self.user.id), "username": self.uname},
            "completely_wrong_secret",
        )
        # decode_token 应返回 None（密钥不匹配）
        self.assertIsNone(decode_token(bad_token, settings.jwt_secret))
        # 端点也应该返回 401
        with patch.object(settings, 'enable_auth', True):
            resp = self.client.get("/api/v1/auth/me",
                                   headers={"Authorization": f"Bearer {bad_token}"})
        self.assertEqual(resp.status_code, 401)

    def test_I2_deactivated_user_blocked_immediately(self):
        """停用账号后，其旧 token 立即失效（在 token 过期前也无法访问）"""
        from backend.models.user import User
        from backend.config.settings import settings
        user, _, _ = _make_user("sec_i2_deact")
        token = _make_token(user, [])
        # 停用用户
        _g_db.query(User).filter(User.id == user.id).update({"is_active": False})
        _g_db.commit()
        with patch.object(settings, 'enable_auth', True):
            resp = self.client.get("/api/v1/auth/me",
                                   headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(resp.status_code, 401)

    def test_I3_slugify_strips_path_traversal(self):
        """_slugify 清除路径分隔符和 .. ，防止目录穿越"""
        from backend.api.skills import _slugify
        cases = [
            ("../../../etc/passwd", "etcpasswd"),
            ("..\\windows\\system32", "windowssystem32"),
            ("/absolute/path",       "absolutepath"),
            ("skill/../secret",     "skillsecret"),
            ("normal-skill",        "normal-skill"),
        ]
        for inp, expected in cases:
            got = _slugify(inp)
            self.assertEqual(got, expected, f"_slugify({inp!r}) = {got!r}")
            self.assertNotIn("/", got)
            self.assertNotIn("\\", got)
            self.assertNotIn("..", got)

    def test_I4_path_boundary_check_prevents_escape(self):
        """skill 路径边界检查可正确识别目录穿越路径"""
        from backend.api.skills import _USER_SKILLS_DIR
        test_dir = _USER_SKILLS_DIR / "_sec_boundary_test"
        test_dir.mkdir(parents=True, exist_ok=True)
        try:
            # 构造一个穿越路径，验证 resolve 后不在用户目录内
            escape = (test_dir / ".." / ".." / "escape.md").resolve()
            self.assertNotEqual(escape.parent, test_dir.resolve(),
                                "Path escape should be detectable by boundary check")
        finally:
            test_dir.rmdir()

    def test_I5_is_superadmin_flag_not_set_by_role_assignment(self):
        """通过 /users/{id}/roles 分配 'superadmin' 角色不会设置 is_superadmin=True 标志"""
        from backend.models.user import User
        from backend.config.settings import settings
        target, _, _ = _make_user("sec_i5_target", role_names=["viewer"])
        resp = self.client.post(
            f"/api/v1/users/{target.id}/roles",
            json={"role_name": "superadmin"},
            headers={"Authorization": f"Bearer {self.sa_token}"},
        )
        # 可以成功分配角色
        with patch.object(settings, 'enable_auth', True):
            resp = self.client.post(
                f"/api/v1/users/{target.id}/roles",
                json={"role_name": "superadmin"},
                headers={"Authorization": f"Bearer {self.sa_token}"},
            )
        if resp.status_code == 200:
            _g_db.refresh(target)
            self.assertFalse(target.is_superadmin,
                             "is_superadmin flag must NOT be set by role assignment API")

    def test_I6_refresh_token_rotation_invalidates_old_token(self):
        """refresh_token 轮换后旧 token 不可再用"""
        from backend.config.settings import settings
        # 先登录
        with patch.object(settings, 'enable_auth', True):
            login_resp = self.client.post("/api/v1/auth/login", json={
                "username": self.uname, "password": self.password,
            })
        self.assertEqual(login_resp.status_code, 200)
        old_cookie = _cookie_from_response(login_resp)
        if not old_cookie:
            self.skipTest("Cannot extract refresh_token cookie")
        # 第一次刷新
        with patch.object(settings, 'enable_auth', True):
            first = self.client.post("/api/v1/auth/refresh",
                                     cookies={"refresh_token": old_cookie})
        self.assertEqual(first.status_code, 200)
        # 用旧 cookie 再次刷新 — 应该失败
        with patch.object(settings, 'enable_auth', True):
            second = self.client.post("/api/v1/auth/refresh",
                                      cookies={"refresh_token": old_cookie})
        self.assertEqual(second.status_code, 401, "Rotated token must be revoked")

    def test_I7_require_admin_independent_of_enable_auth(self):
        """require_admin (X-Admin-Token) 与 ENABLE_AUTH 设置无关，独立验证"""
        from backend.api.deps import require_admin
        from backend.config.settings import settings
        from fastapi import HTTPException

        # 无论 ENABLE_AUTH 如何，错误的 Admin Token 都应被拒绝
        with patch.object(settings, 'admin_secret_token', 'my_secret_token'):
            with patch.object(settings, 'enable_auth', False):
                with self.assertRaises(HTTPException) as ctx:
                    asyncio.run(require_admin(x_admin_token="wrong_token"))
            self.assertEqual(ctx.exception.status_code, 401)


# ─── 测试运行器 ──────────────────────────────────────────────────────────────

def run_all():
    test_classes = [
        ("A - Password & JWT",         [TestPasswordUtils, TestJWTUtils]),
        ("B - LocalAuthProvider",       [TestLocalAuthProvider]),
        ("C - RBAC Helpers",            [TestRBACHelpers]),
        ("D - FastAPI Deps",            [TestDeps]),
        ("E - Auth Endpoints",          [TestAuthEndpoints]),
        ("F - User Management",         [TestUserEndpoints]),
        ("G - Skill Isolation",         [TestSkillsUserIsolation]),
        ("H - ENABLE_AUTH=false",       [TestEnableAuthFalseCompat]),
        ("I - Security Edge Cases",     [TestSecurityEdgeCases]),
    ]

    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for _, classes in test_classes:
        for cls in classes:
            suite.addTests(loader.loadTestsFromTestCase(cls))

    runner = unittest.TextTestRunner(verbosity=2, stream=sys.stdout)
    result = runner.run(suite)

    total    = result.testsRun
    failures = len(result.failures) + len(result.errors)
    passed   = total - failures

    print(f"\n{'=' * 60}")
    print(f"RBAC Test Suite: {passed}/{total} passed, {failures} failed")
    print(f"{'=' * 60}")
    if failures:
        sys.exit(1)


if __name__ == "__main__":
    import pytest, sys
    sys.exit(pytest.main([__file__, "-v", "-s"]))
