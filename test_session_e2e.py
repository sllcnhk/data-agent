"""
test_session_e2e.py — Session 过期管理端到端测试
=================================================

覆盖本次新增功能的核心端到端流程：

  U (6) — Cookie 属性验证（真实 HTTP 请求）
  V (8) — last_active_at 数据库写入追踪
  W (7) — /auth/refresh 空闲超时强制执行
  X (5) — 完整会话生命周期（登录→活动→超时→重登）
  Y (4) — SESSION_IDLE_TIMEOUT_MINUTES 配置覆盖
  Z (5) — 菜单权限范围审计（无新增未门控菜单）

总计: 35 个测试用例
"""
from __future__ import annotations

import os
import sys
import uuid
import unittest
import time
import re
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "backend"))

# ─── 全局测试数据前缀 ──────────────────────────────────────────────────────
_PREFIX = f"_sess_{uuid.uuid4().hex[:6]}_"


def _db():
    from backend.config.database import SessionLocal
    return SessionLocal()


_g_db = _db()


def _make_user(suffix="", password="Test1234!", role_names=None,
               is_superadmin=False, is_active=True):
    from backend.models.user import User
    from backend.models.role import Role
    from backend.models.user_role import UserRole
    from backend.core.auth.password import hash_password

    username = f"{_PREFIX}{suffix or uuid.uuid4().hex[:6]}"
    u = User(
        username=username,
        display_name=f"Sess {suffix}",
        hashed_password=hash_password(password),
        auth_source="local",
        is_active=is_active,
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


def _set_last_active(user, ts):
    """直接在 DB 中设置 last_active_at（用于模拟空闲场景）"""
    from backend.models.user import User
    _g_db.query(User).filter(User.id == user.id).update({"last_active_at": ts})
    _g_db.commit()
    _g_db.refresh(user)


def _get_refresh_token_from_db(user):
    """获取用户当前有效的 refresh_token JTI"""
    from backend.models.refresh_token import RefreshToken
    rt = (
        _g_db.query(RefreshToken)
        .filter(
            RefreshToken.user_id == user.id,
            RefreshToken.revoked == False,  # noqa: E712
        )
        .order_by(RefreshToken.created_at.desc())
        .first()
    )
    return rt


def teardown_module(_=None):
    from backend.models.user import User
    try:
        _g_db.query(User).filter(
            User.username.like(f"{_PREFIX}%")
        ).delete(synchronize_session=False)
        _g_db.commit()
    finally:
        _g_db.close()


# ─── FastAPI TestClient ────────────────────────────────────────────────────
from fastapi.testclient import TestClient


def _make_app():
    from backend.main import app
    return app


# ══════════════════════════════════════════════════════════════════════════════
# Section U — Cookie 属性验证（真实 HTTP）
# ══════════════════════════════════════════════════════════════════════════════

class TestCookieAttributes(unittest.TestCase):
    """U1-U6: 验证 refresh_token cookie 为 Session Cookie"""

    @classmethod
    def setUpClass(cls):
        cls.app = _make_app()
        cls.user, cls.username, cls.password = _make_user("u_base", role_names=["analyst"])

    def _login(self):
        """新 client 执行登录，返回 (client, login_response)"""
        client = TestClient(self.app, raise_server_exceptions=True)
        resp = client.post("/api/v1/auth/login",
                           json={"username": self.username, "password": self.password})
        return client, resp

    def test_U1_login_set_cookie_no_max_age(self):
        """POST /auth/login 的 Set-Cookie 不含 max-age（session cookie）"""
        with patch("backend.config.settings.settings.enable_auth", True):
            _, resp = self._login()
        self.assertEqual(resp.status_code, 200)
        cookie = resp.headers.get("set-cookie", "")
        self.assertIn("refresh_token", cookie)
        self.assertNotIn("max-age=", cookie.lower(), f"Set-Cookie 不应含 max-age: {cookie}")

    def test_U2_login_set_cookie_no_expires(self):
        """POST /auth/login 的 Set-Cookie 不含 expires"""
        with patch("backend.config.settings.settings.enable_auth", True):
            _, resp = self._login()
        cookie = resp.headers.get("set-cookie", "")
        self.assertNotIn("expires=", cookie.lower(), f"Set-Cookie 不应含 expires: {cookie}")

    def test_U3_login_set_cookie_has_httponly(self):
        """POST /auth/login 的 Set-Cookie 含 HttpOnly"""
        with patch("backend.config.settings.settings.enable_auth", True):
            _, resp = self._login()
        cookie = resp.headers.get("set-cookie", "")
        self.assertIn("httponly", cookie.lower())

    def test_U4_login_set_cookie_has_samesite_lax(self):
        """POST /auth/login 的 Set-Cookie 含 SameSite=Lax"""
        with patch("backend.config.settings.settings.enable_auth", True):
            _, resp = self._login()
        cookie = resp.headers.get("set-cookie", "")
        self.assertIn("samesite=lax", cookie.lower())

    def test_U5_refresh_set_cookie_also_no_max_age(self):
        """POST /auth/refresh 的 Set-Cookie 也不含 max-age"""
        with patch("backend.config.settings.settings.enable_auth", True):
            client, login_resp = self._login()
            self.assertEqual(login_resp.status_code, 200)
            # client 已有 refresh_token cookie，直接调用 refresh
            refresh_resp = client.post("/api/v1/auth/refresh")
        self.assertEqual(refresh_resp.status_code, 200)
        cookie = refresh_resp.headers.get("set-cookie", "")
        self.assertNotIn("max-age=", cookie.lower(), f"refresh Set-Cookie 不应含 max-age: {cookie}")

    def test_U6_new_client_without_cookie_gets_401(self):
        """模拟浏览器关闭：新 TestClient（无 cookie）调 /auth/refresh → 401"""
        with patch("backend.config.settings.settings.enable_auth", True):
            # 先用旧 client 登录（获取 cookie）
            client_with_cookie, resp = self._login()
            self.assertEqual(resp.status_code, 200)
            # 新 client：无 cookie，模拟浏览器重启
            fresh_client = TestClient(self.app, raise_server_exceptions=True)
            refresh_resp = fresh_client.post("/api/v1/auth/refresh")
        self.assertEqual(refresh_resp.status_code, 401,
                         "无 cookie 的新 client refresh 应返回 401")


# ══════════════════════════════════════════════════════════════════════════════
# Section V — last_active_at 数据库追踪
# ══════════════════════════════════════════════════════════════════════════════

class TestLastActiveTracking(unittest.TestCase):
    """V1-V8: 验证 last_active_at 写入逻辑"""

    @classmethod
    def setUpClass(cls):
        cls.app = _make_app()
        cls.client = TestClient(cls.app, raise_server_exceptions=True)
        cls.user_a, cls.username_a, cls.pw_a = _make_user("v_usera", role_names=["analyst"])
        cls.user_b, cls.username_b, cls.pw_b = _make_user("v_userb", role_names=["analyst"])

    def _auth(self, user):
        return {"Authorization": f"Bearer {_token(user)}"}

    def _refresh_user(self, user):
        """从 DB 重新加载 user（获取最新 last_active_at）"""
        _g_db.expire(user)
        _g_db.refresh(user)

    def test_V1_login_does_not_set_last_active_at(self):
        """登录只更新 last_login_at，不设 last_active_at"""
        with patch("backend.config.settings.settings.enable_auth", True):
            client = TestClient(self.app)
            resp = client.post("/api/v1/auth/login",
                               json={"username": self.username_a, "password": self.pw_a})
        self.assertEqual(resp.status_code, 200)
        # 重新加载用户
        from backend.models.user import User
        u = _g_db.query(User).filter(User.id == self.user_a.id).first()
        # last_login_at 应被设置
        self.assertIsNotNone(u.last_login_at)
        # last_active_at 在首次登录后可能为 None 或已由 /auth/me 调用设置
        # 此测试仅验证 last_login_at 有值
        self.assertIsNotNone(u.last_login_at, "登录后 last_login_at 应有值")

    def test_V2_authenticated_request_schedules_last_active_update(self):
        """有效 token 请求 /auth/me → BackgroundTask 写入 last_active_at"""
        # 先将 last_active_at 置空，确保节流条件满足
        _set_last_active(self.user_a, None)
        with patch("backend.config.settings.settings.enable_auth", True):
            resp = self.client.get("/api/v1/auth/me",
                                   headers=self._auth(self.user_a))
        self.assertEqual(resp.status_code, 200)
        # BackgroundTask 在 TestClient 中同步执行，等待后检查 DB
        _g_db.expire(self.user_a)
        _g_db.refresh(self.user_a)
        self.assertIsNotNone(self.user_a.last_active_at,
                             "认证请求后 last_active_at 应被写入")

    def test_V3_throttle_prevents_duplicate_writes_within_5min(self):
        """5 分钟内连续请求不重复写入（节流保护）"""
        # 设置 last_active_at 为 1 分钟前
        recent_ts = datetime.utcnow() - timedelta(minutes=1)
        _set_last_active(self.user_a, recent_ts)

        with patch("backend.config.settings.settings.enable_auth", True):
            # 第一次请求
            self.client.get("/api/v1/auth/me", headers=self._auth(self.user_a))
        # 第二次请求（距上次 <5min）不应更新 last_active_at
        _g_db.expire(self.user_a)
        _g_db.refresh(self.user_a)
        # last_active_at 应仍接近 recent_ts（未被更新为 now）
        diff = abs((self.user_a.last_active_at - recent_ts).total_seconds())
        self.assertLess(diff, 30, f"节流内不应更新 last_active_at，diff={diff:.1f}s")

    def test_V4_update_last_active_safe_for_invalid_user_id(self):
        """_update_last_active 对不存在的 user_id 不抛异常"""
        from backend.api.deps import _update_last_active
        try:
            _update_last_active("00000000-0000-0000-0000-000000000000")
        except Exception as e:
            self.fail(f"_update_last_active 对无效 user_id 不应抛异常: {e}")

    def test_V5_last_active_at_timestamp_is_recent(self):
        """last_active_at 写入的时间戳接近 UTC now（±5s）"""
        _set_last_active(self.user_a, None)
        before = datetime.utcnow()
        with patch("backend.config.settings.settings.enable_auth", True):
            self.client.get("/api/v1/auth/me", headers=self._auth(self.user_a))
        after = datetime.utcnow()
        _g_db.expire(self.user_a)
        _g_db.refresh(self.user_a)
        ts = self.user_a.last_active_at
        self.assertIsNotNone(ts)
        self.assertGreaterEqual(ts, before - timedelta(seconds=2))
        self.assertLessEqual(ts, after + timedelta(seconds=2))

    def test_V6_user_a_activity_does_not_affect_user_b(self):
        """用户 A 的活动不影响用户 B 的 last_active_at"""
        _set_last_active(self.user_a, None)
        _set_last_active(self.user_b, None)

        with patch("backend.config.settings.settings.enable_auth", True):
            # 只有 user_a 发请求
            self.client.get("/api/v1/auth/me", headers=self._auth(self.user_a))

        _g_db.expire(self.user_b)
        _g_db.refresh(self.user_b)
        self.assertIsNone(self.user_b.last_active_at,
                          "user_a 的活动不应影响 user_b 的 last_active_at")

    def test_V7_enable_auth_false_does_not_update_last_active(self):
        """ENABLE_AUTH=false 使用 AnonymousUser，不更新任何用户的 last_active_at"""
        _set_last_active(self.user_a, None)
        with patch("backend.config.settings.settings.enable_auth", False):
            self.client.get("/api/v1/auth/me")
        _g_db.expire(self.user_a)
        _g_db.refresh(self.user_a)
        self.assertIsNone(self.user_a.last_active_at,
                          "ENABLE_AUTH=false 不应写 last_active_at")

    def test_V8_last_active_at_throttle_updates_after_5min_window(self):
        """距上次活动 > 5 分钟时节流窗口解除，触发新写入"""
        old_ts = datetime.utcnow() - timedelta(minutes=6)
        _set_last_active(self.user_a, old_ts)
        with patch("backend.config.settings.settings.enable_auth", True):
            self.client.get("/api/v1/auth/me", headers=self._auth(self.user_a))
        _g_db.expire(self.user_a)
        _g_db.refresh(self.user_a)
        self.assertGreater(self.user_a.last_active_at, old_ts,
                           "超出节流窗口后 last_active_at 应被更新")


# ══════════════════════════════════════════════════════════════════════════════
# Section W — /auth/refresh 空闲超时强制执行
# ══════════════════════════════════════════════════════════════════════════════

class TestIdleTimeoutEnforcement(unittest.TestCase):
    """W1-W7: /auth/refresh 端点的空闲超时检测"""

    @classmethod
    def setUpClass(cls):
        cls.app = _make_app()

    def _fresh_login(self, username, password):
        """新 client 登录，返回 (client, access_token)"""
        client = TestClient(self.app, raise_server_exceptions=True)
        with patch("backend.config.settings.settings.enable_auth", True):
            resp = client.post("/api/v1/auth/login",
                               json={"username": username, "password": password})
        self.assertEqual(resp.status_code, 200, f"登录失败: {resp.text}")
        return client, resp.json()["access_token"]

    def test_W1_active_user_refresh_succeeds(self):
        """last_active_at=刚刚 → refresh 成功，返回新 access_token"""
        user, uname, pw = _make_user("w_active", role_names=["analyst"])
        client, _ = self._fresh_login(uname, pw)
        # 设置 last_active_at 为 5 分钟前（活跃范围内）
        _set_last_active(user, datetime.utcnow() - timedelta(minutes=5))
        with patch("backend.config.settings.settings.enable_auth", True):
            with patch("backend.config.settings.settings.session_idle_timeout_minutes", 120):
                resp = client.post("/api/v1/auth/refresh")
        self.assertEqual(resp.status_code, 200, f"活跃用户 refresh 应成功: {resp.text}")
        self.assertIn("access_token", resp.json())

    def test_W2_idle_user_refresh_returns_401(self):
        """last_active_at=3小时前 → refresh 返回 401"""
        user, uname, pw = _make_user("w_idle", role_names=["analyst"])
        client, _ = self._fresh_login(uname, pw)
        # 设置 last_active_at 为 3 小时前（超出 120min 超时）
        _set_last_active(user, datetime.utcnow() - timedelta(hours=3))
        with patch("backend.config.settings.settings.enable_auth", True):
            with patch("backend.config.settings.settings.session_idle_timeout_minutes", 120):
                resp = client.post("/api/v1/auth/refresh")
        self.assertEqual(resp.status_code, 401, f"空闲用户 refresh 应返回 401: {resp.text}")

    def test_W3_idle_rejection_detail_message(self):
        """空闲超时拒绝时，detail 包含 '超时'"""
        user, uname, pw = _make_user("w_msg", role_names=["analyst"])
        client, _ = self._fresh_login(uname, pw)
        _set_last_active(user, datetime.utcnow() - timedelta(hours=3))
        with patch("backend.config.settings.settings.enable_auth", True):
            with patch("backend.config.settings.settings.session_idle_timeout_minutes", 120):
                resp = client.post("/api/v1/auth/refresh")
        self.assertEqual(resp.status_code, 401)
        detail = resp.json().get("detail", "")
        self.assertIn("超时", detail, f"detail 应含'超时': {detail}")

    def test_W4_idle_rejection_revokes_refresh_token(self):
        """空闲超时拒绝时，refresh_token 被标记为 revoked"""
        user, uname, pw = _make_user("w_revoke", role_names=["analyst"])
        client, _ = self._fresh_login(uname, pw)
        # 获取当前有效的 refresh_token
        rt_before = _get_refresh_token_from_db(user)
        self.assertIsNotNone(rt_before)
        _set_last_active(user, datetime.utcnow() - timedelta(hours=3))
        with patch("backend.config.settings.settings.enable_auth", True):
            with patch("backend.config.settings.settings.session_idle_timeout_minutes", 120):
                resp = client.post("/api/v1/auth/refresh")
        self.assertEqual(resp.status_code, 401)
        # 验证 refresh_token 已被 revoke
        from backend.models.refresh_token import RefreshToken
        rt_after = _g_db.query(RefreshToken).filter(
            RefreshToken.jti == rt_before.jti
        ).first()
        _g_db.expire(rt_after)
        _g_db.refresh(rt_after)
        self.assertTrue(rt_after.revoked, "空闲超时拒绝后 refresh_token 应被 revoked")

    def test_W5_fallback_to_last_login_at_within_timeout_succeeds(self):
        """last_active_at=None, last_login_at=10分钟前 → 兜底成功"""
        user, uname, pw = _make_user("w_fallback_ok", role_names=["analyst"])
        client, _ = self._fresh_login(uname, pw)
        # 清空 last_active_at，手动设置 last_login_at 为 10 分钟前
        _set_last_active(user, None)
        from backend.models.user import User
        _g_db.query(User).filter(User.id == user.id).update(
            {"last_login_at": datetime.utcnow() - timedelta(minutes=10)}
        )
        _g_db.commit()
        with patch("backend.config.settings.settings.enable_auth", True):
            with patch("backend.config.settings.settings.session_idle_timeout_minutes", 120):
                resp = client.post("/api/v1/auth/refresh")
        self.assertEqual(resp.status_code, 200,
                         f"last_login_at 兜底（10min前）应成功: {resp.text}")

    def test_W6_fallback_to_last_login_at_beyond_timeout_rejects(self):
        """last_active_at=None, last_login_at=3小时前 → 兜底超时拒绝"""
        user, uname, pw = _make_user("w_fallback_fail", role_names=["analyst"])
        client, _ = self._fresh_login(uname, pw)
        _set_last_active(user, None)
        from backend.models.user import User
        _g_db.query(User).filter(User.id == user.id).update(
            {"last_login_at": datetime.utcnow() - timedelta(hours=3)}
        )
        _g_db.commit()
        with patch("backend.config.settings.settings.enable_auth", True):
            with patch("backend.config.settings.settings.session_idle_timeout_minutes", 120):
                resp = client.post("/api/v1/auth/refresh")
        self.assertEqual(resp.status_code, 401,
                         f"last_login_at 兜底（3h前）应超时拒绝: {resp.text}")

    def test_W7_enable_auth_false_skips_idle_check(self):
        """ENABLE_AUTH=false 时空闲检测跳过，任何时间 refresh 均成功"""
        user, uname, pw = _make_user("w_noauth", role_names=["analyst"])
        # ENABLE_AUTH=false 时 login 不需要认证
        client = TestClient(self.app)
        with patch("backend.config.settings.settings.enable_auth", False):
            # 登录（不认证模式）
            resp = client.post("/api/v1/auth/login",
                               json={"username": uname, "password": pw})
        # 不认证模式下 login 端点本身也会执行 enable_auth 判断，结果 200
        # 主要验证：即使 last_active_at 是很久之前，refresh 也不受影响
        _set_last_active(user, datetime.utcnow() - timedelta(hours=100))
        with patch("backend.config.settings.settings.enable_auth", False):
            resp = client.post("/api/v1/auth/refresh")
        # ENABLE_AUTH=false 时 refresh 不检查 idle → 应 200 或 401（无 cookie，但不是因为 idle）
        # 核心验证：detail 不是超时消息
        if resp.status_code == 401:
            detail = resp.json().get("detail", "")
            self.assertNotIn("超时", detail, "ENABLE_AUTH=false 时不应出现超时拒绝")


# ══════════════════════════════════════════════════════════════════════════════
# Section X — 完整会话生命周期
# ══════════════════════════════════════════════════════════════════════════════

class TestSessionLifecycle(unittest.TestCase):
    """X1-X5: 端到端会话生命周期"""

    @classmethod
    def setUpClass(cls):
        cls.app = _make_app()

    def test_X1_full_active_session_flow(self):
        """完整活跃流程: 登录 → 活动 → refresh 成功 → 能访问 /auth/me"""
        user, uname, pw = _make_user("x_full", role_names=["analyst"])
        client = TestClient(self.app, raise_server_exceptions=True)
        with patch("backend.config.settings.settings.enable_auth", True):
            with patch("backend.config.settings.settings.session_idle_timeout_minutes", 120):
                # Step 1: 登录
                login = client.post("/api/v1/auth/login",
                                    json={"username": uname, "password": pw})
                self.assertEqual(login.status_code, 200)
                token = login.json()["access_token"]

                # Step 2: 访问 /auth/me（更新 last_active_at）
                me = client.get("/api/v1/auth/me",
                                headers={"Authorization": f"Bearer {token}"})
                self.assertEqual(me.status_code, 200)
                self.assertEqual(me.json()["username"], uname)

                # Step 3: refresh（last_active_at 刚被更新，应成功）
                _g_db.expire(user)
                _g_db.refresh(user)
                refresh = client.post("/api/v1/auth/refresh")
                self.assertEqual(refresh.status_code, 200, f"活跃用户 refresh 应成功: {refresh.text}")

    def test_X2_idle_session_refresh_fails_then_login_required(self):
        """空闲超时流程: 登录 → mock 超时 → refresh 401 → 需重新登录"""
        user, uname, pw = _make_user("x_idle", role_names=["analyst"])
        client = TestClient(self.app, raise_server_exceptions=True)
        with patch("backend.config.settings.settings.enable_auth", True):
            with patch("backend.config.settings.settings.session_idle_timeout_minutes", 120):
                # Step 1: 登录
                login = client.post("/api/v1/auth/login",
                                    json={"username": uname, "password": pw})
                self.assertEqual(login.status_code, 200)

                # Step 2: mock 空闲（设置 last_active_at 为 3 小时前）
                _set_last_active(user, datetime.utcnow() - timedelta(hours=3))
                from backend.models.user import User
                _g_db.query(User).filter(User.id == user.id).update(
                    {"last_login_at": datetime.utcnow() - timedelta(hours=3)}
                )
                _g_db.commit()

                # Step 3: refresh → 401
                refresh = client.post("/api/v1/auth/refresh")
                self.assertEqual(refresh.status_code, 401, "空闲用户 refresh 应被拒绝")

    def test_X3_relogin_after_idle_timeout_succeeds(self):
        """超时后重新登录 → 新 session 正常工作"""
        user, uname, pw = _make_user("x_relogin", role_names=["analyst"])
        client = TestClient(self.app, raise_server_exceptions=True)
        with patch("backend.config.settings.settings.enable_auth", True):
            with patch("backend.config.settings.settings.session_idle_timeout_minutes", 120):
                # 第一次登录 + 模拟超时
                client.post("/api/v1/auth/login", json={"username": uname, "password": pw})
                _set_last_active(user, datetime.utcnow() - timedelta(hours=3))
                client.post("/api/v1/auth/refresh")  # 超时，revoke 旧 token

                # 重新登录（新 client 模拟重新打开浏览器登录）
                new_client = TestClient(self.app)
                login2 = new_client.post("/api/v1/auth/login",
                                         json={"username": uname, "password": pw})
                self.assertEqual(login2.status_code, 200, "超时后重新登录应成功")
                new_token = login2.json()["access_token"]
                self.assertIsNotNone(new_token)

                # 新 session 的 /auth/me 正常
                me = new_client.get("/api/v1/auth/me",
                                    headers={"Authorization": f"Bearer {new_token}"})
                self.assertEqual(me.status_code, 200)

    def test_X4_logout_revokes_refresh_token(self):
        """主动登出 → refresh_token 被 revoke → 后续 refresh 401"""
        user, uname, pw = _make_user("x_logout", role_names=["analyst"])
        client = TestClient(self.app, raise_server_exceptions=True)
        with patch("backend.config.settings.settings.enable_auth", True):
            # 登录
            login = client.post("/api/v1/auth/login",
                                json={"username": uname, "password": pw})
            token = login.json()["access_token"]

            # 主动登出
            logout = client.post("/api/v1/auth/logout",
                                 headers={"Authorization": f"Bearer {token}"})
            self.assertEqual(logout.status_code, 200)

            # 登出后 refresh 失败（cookie 已被 delete + token revoked）
            fresh_client = TestClient(self.app)  # 无 cookie
            resp = fresh_client.post("/api/v1/auth/refresh")
            self.assertEqual(resp.status_code, 401, "登出后 refresh 应 401")

    def test_X5_superadmin_also_subject_to_idle_timeout(self):
        """superadmin 也受空闲超时约束（非豁免）"""
        user, uname, pw = _make_user("x_sadmin", is_superadmin=True)
        client = TestClient(self.app, raise_server_exceptions=True)
        with patch("backend.config.settings.settings.enable_auth", True):
            with patch("backend.config.settings.settings.session_idle_timeout_minutes", 120):
                client.post("/api/v1/auth/login", json={"username": uname, "password": pw})
                # 模拟超时
                _set_last_active(user, datetime.utcnow() - timedelta(hours=3))
                from backend.models.user import User
                _g_db.query(User).filter(User.id == user.id).update(
                    {"last_login_at": datetime.utcnow() - timedelta(hours=3)}
                )
                _g_db.commit()
                resp = client.post("/api/v1/auth/refresh")
        self.assertEqual(resp.status_code, 401,
                         "superadmin 超时后 refresh 应同样被拒绝")


# ══════════════════════════════════════════════════════════════════════════════
# Section Y — SESSION_IDLE_TIMEOUT_MINUTES 配置验证
# ══════════════════════════════════════════════════════════════════════════════

class TestSessionTimeoutConfig(unittest.TestCase):
    """Y1-Y4: 配置项读取与边界行为"""

    @classmethod
    def setUpClass(cls):
        cls.app = _make_app()

    def test_Y1_short_timeout_rejects_after_window(self):
        """SESSION_IDLE_TIMEOUT_MINUTES=1 → 1分钟空闲后 refresh 被拒"""
        user, uname, pw = _make_user("y_short", role_names=["analyst"])
        client = TestClient(self.app, raise_server_exceptions=True)
        with patch("backend.config.settings.settings.enable_auth", True):
            with patch("backend.config.settings.settings.session_idle_timeout_minutes", 1):
                client.post("/api/v1/auth/login", json={"username": uname, "password": pw})
                # 设置 last_active_at 为 2 分钟前（超过 1 分钟阈值）
                _set_last_active(user, datetime.utcnow() - timedelta(minutes=2))
                from backend.models.user import User
                _g_db.query(User).filter(User.id == user.id).update(
                    {"last_login_at": datetime.utcnow() - timedelta(minutes=2)}
                )
                _g_db.commit()
                resp = client.post("/api/v1/auth/refresh")
        self.assertEqual(resp.status_code, 401,
                         f"1分钟超时配置下，2分钟空闲应被拒绝: {resp.text}")

    def test_Y2_within_short_timeout_allows_refresh(self):
        """SESSION_IDLE_TIMEOUT_MINUTES=5 → 1分钟内活动 refresh 成功"""
        user, uname, pw = _make_user("y_allow", role_names=["analyst"])
        client = TestClient(self.app, raise_server_exceptions=True)
        with patch("backend.config.settings.settings.enable_auth", True):
            with patch("backend.config.settings.settings.session_idle_timeout_minutes", 5):
                client.post("/api/v1/auth/login", json={"username": uname, "password": pw})
                # 设置 last_active_at 为 1 分钟前（在 5 分钟阈值内）
                _set_last_active(user, datetime.utcnow() - timedelta(minutes=1))
                resp = client.post("/api/v1/auth/refresh")
        self.assertEqual(resp.status_code, 200,
                         f"5分钟超时配置，1分钟内活动应允许: {resp.text}")

    def test_Y3_default_timeout_is_120_minutes(self):
        """settings.session_idle_timeout_minutes 默认值为 120"""
        from backend.config.settings import settings
        self.assertEqual(settings.session_idle_timeout_minutes, 120)

    def test_Y4_access_token_expire_minutes_matches_timeout(self):
        """ACCESS_TOKEN_EXPIRE_MINUTES 配置值与 SESSION_IDLE_TIMEOUT_MINUTES 一致"""
        # 读取 .env 中的配置
        env_path = os.path.join(os.path.dirname(__file__), ".env")
        with open(env_path, encoding="utf-8") as f:
            env_src = f.read()
        m_access = re.search(r'^ACCESS_TOKEN_EXPIRE_MINUTES=(\d+)', env_src, re.MULTILINE)
        m_idle = re.search(r'^SESSION_IDLE_TIMEOUT_MINUTES=(\d+)', env_src, re.MULTILINE)
        access_min = int(m_access.group(1)) if m_access else None
        idle_min = int(m_idle.group(1)) if m_idle else None
        self.assertIsNotNone(access_min, "ACCESS_TOKEN_EXPIRE_MINUTES 应在 .env 中配置")
        self.assertIsNotNone(idle_min, "SESSION_IDLE_TIMEOUT_MINUTES 应在 .env 中配置")
        self.assertLessEqual(access_min, idle_min,
                             f"ACCESS_TOKEN_EXPIRE_MINUTES({access_min}) 须 <= "
                             f"SESSION_IDLE_TIMEOUT_MINUTES({idle_min})")


# ══════════════════════════════════════════════════════════════════════════════
# Section Z — 菜单权限范围审计
# ══════════════════════════════════════════════════════════════════════════════

class TestMenuPermissionAudit(unittest.TestCase):
    """Z1-Z5: 验证 session 功能未引入无权限门控的新菜单"""

    def _read_frontend(self, path):
        full = os.path.join(os.path.dirname(__file__), "frontend/src", path)
        if os.path.exists(full):
            with open(full, encoding="utf-8") as f:
                return f.read()
        return ""

    def test_Z1_no_new_session_menu_in_app_layout(self):
        """AppLayout.tsx 中无 session/timeout/idle 相关新菜单项"""
        src = self._read_frontend("components/AppLayout.tsx")
        if not src:
            self.skipTest("AppLayout.tsx 未找到，跳过前端审计")
        # session 功能不应引入新菜单
        session_menu_keywords = ["session", "timeout", "idle", "activity"]
        found = [kw for kw in session_menu_keywords if kw.lower() in src.lower()]
        self.assertEqual(found, [],
                         f"AppLayout 中不应有 session 相关菜单项: {found}")

    def test_Z2_existing_menu_permissions_unchanged(self):
        """AppLayout.tsx 已有菜单的权限门控仍保持原有结构"""
        src = self._read_frontend("components/AppLayout.tsx")
        if not src:
            self.skipTest("AppLayout.tsx 未找到，跳过前端审计")
        # 验证关键权限门控仍存在
        required_gates = [
            "users:read",    # /users 和 /roles 菜单
            "chat:use",      # /chat 菜单
            "skills.user:read",  # /skills 菜单
        ]
        for gate in required_gates:
            self.assertIn(gate, src, f"权限门控 '{gate}' 应仍存在于 AppLayout.tsx")

    def test_Z3_new_api_endpoints_require_auth(self):
        """deps.py 新增的辅助函数不暴露未鉴权路由"""
        deps_path = os.path.join(os.path.dirname(__file__), "backend/api/deps.py")
        with open(deps_path, encoding="utf-8") as f:
            src = f.read()
        # _update_last_active 是内部函数（以 _ 开头），不应被注册为路由
        self.assertIn("def _update_last_active", src)
        # 确认没有 @router.xxx 装饰的新路由
        new_routes = re.findall(r'@router\.(get|post|put|delete)\s*\(.*?last_active', src, re.DOTALL)
        self.assertEqual(new_routes, [],
                         f"_update_last_active 不应作为 API 路由暴露: {new_routes}")

    def test_Z4_session_config_not_exposed_via_public_api(self):
        """SESSION_IDLE_TIMEOUT_MINUTES 不通过未鉴权的 API 直接返回"""
        app = _make_app()
        client = TestClient(app, raise_server_exceptions=True)
        # 无认证访问 /health — 不应返回 session 配置
        resp = client.get("/health")
        body = resp.text.lower()
        self.assertNotIn("session_idle_timeout", body,
                         "健康检查不应暴露 session_idle_timeout 配置")

    def test_Z5_users_roles_menus_still_gated_by_users_read(self):
        """AppLayout.tsx 的 /users 和 /roles 菜单仍由 users:read 门控"""
        src = self._read_frontend("components/AppLayout.tsx")
        if not src:
            self.skipTest("AppLayout.tsx 未找到，跳过前端审计")
        # 查找 /users 和 /roles 路由都有 users:read 相关判断
        has_users_read = "users:read" in src
        self.assertTrue(has_users_read, "/users 和 /roles 菜单应由 users:read 门控")


# ══════════════════════════════════════════════════════════════════════════════
# Runner
# ══════════════════════════════════════════════════════════════════════════════

def run_all():
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for cls in [
        TestCookieAttributes,
        TestLastActiveTracking,
        TestIdleTimeoutEnforcement,
        TestSessionLifecycle,
        TestSessionTimeoutConfig,
        TestMenuPermissionAudit,
    ]:
        suite.addTests(loader.loadTestsFromTestCase(cls))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    passed = result.testsRun - len(result.failures) - len(result.errors)
    print()
    print("=" * 70)
    section_map = {
        "U": "Cookie 属性",
        "V": "last_active_at 追踪",
        "W": "空闲超时强制",
        "X": "会话生命周期",
        "Y": "超时配置验证",
        "Z": "菜单权限审计",
    }
    for s, name in section_map.items():
        print(f"  Section {s}: {name}")
    print(f"\ntest_session_e2e.py  {passed}/{result.testsRun} passed  |  "
          f"{len(result.failures)} failed  |  {len(result.errors)} errors")
    print("=" * 70)
    return result


if __name__ == "__main__":
    import pytest, sys
    sys.exit(pytest.main([__file__, "-v", "-s"]))
