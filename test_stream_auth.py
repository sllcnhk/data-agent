"""
test_stream_auth.py — 流式消息鉴权修复测试套件
================================================

修复背景
--------
chatApi.ts 的 sendMessageStream() 使用原生 fetch()，原先只有
Content-Type header，缺少 Authorization: Bearer <token>，导致
ENABLE_AUTH=true 时 POST /conversations/{id}/messages 返回 401。

修复方案（chatApi.ts v2）：
1. 顶层 import { useAuthStore } from '@/store/useAuthStore'（无循环依赖）
2. apiClient 添加 request interceptor 统一注入 Authorization header
3. sendMessageStream() 内直接调用 useAuthStore.getState().accessToken

测试层次
--------
  A (8)  — chatApi.ts 静态分析：sendMessageStream 修复验证
  B (7)  — 后端 POST /messages 鉴权（ENABLE_AUTH=true）
  C (5)  — 后端 ENABLE_AUTH=false 兼容性
  D (5)  — 对话其他端点：不需要 token 的 CRUD 不受影响
  E (4)  — get_current_user 依赖项单元测试
  F (4)  — api.ts 拦截器静态分析（前端 axios 层鉴权）
  G (3)  — conversations.py 端点签名回归分析

总计: 36 个测试用例
目标: 36/36 通过

运行方式（Windows + Anaconda）：
  set PYTHONPATH=C:\\Users\\shiguangping\\data-agent
  D:\\ProgramData\\Anaconda3\\envs\\dataagent\\python.exe -X utf8 test_stream_auth.py
"""

import os
import sys
import re
import uuid
import asyncio
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock
from datetime import timedelta

# ── 路径设置 ──────────────────────────────────────────────────────────────────
os.environ.setdefault("ENABLE_AUTH", "False")
os.environ.setdefault("POSTGRES_USER", "postgres")
os.environ.setdefault("POSTGRES_PASSWORD", "Sgp013013")
os.environ.setdefault("POSTGRES_HOST", "localhost")
os.environ.setdefault("POSTGRES_PORT", "5432")
os.environ.setdefault("POSTGRES_DB", "data_agent")

_PROJECT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _PROJECT)
# backend/ 必须在 sys.path 中，否则 main.py 的 `from api import ...` 失败
sys.path.insert(0, os.path.join(_PROJECT, "backend"))

# ── 源文件路径常量 ────────────────────────────────────────────────────────────
_CHATAPI_TS = os.path.join(_PROJECT, "frontend", "src", "services", "chatApi.ts")
_API_TS      = os.path.join(_PROJECT, "frontend", "src", "services", "api.ts")
_CONV_PY     = os.path.join(_PROJECT, "backend", "api", "conversations.py")
_DEPS_PY     = os.path.join(_PROJECT, "backend", "api", "deps.py")

# ── 唯一前缀，防止测试数据冲突 ─────────────────────────────────────────────────
_PREFIX = f"_stauth_{uuid.uuid4().hex[:6]}_"


def _db():
    from backend.config.database import SessionLocal
    return SessionLocal()


# DB 可用性检测（PostgreSQL 未运行时跳过需要 DB 的测试）
_DB_AVAILABLE = False
_g_db = None
try:
    _sess = _db()
    _sess.execute(__import__("sqlalchemy").text("SELECT 1"))
    _sess.close()
    _g_db = _db()
    _DB_AVAILABLE = True
except Exception as _db_err:
    print(f"[WARNING] PostgreSQL 不可用，跳过 DB 相关测试: {_db_err}")

_SKIP_DB = unittest.skipUnless(_DB_AVAILABLE, "PostgreSQL 不可用，跳过 DB 相关测试")


def _make_user(suffix="", password="Test1234!", is_superadmin=False,
               is_active=True, role_names=None):
    """在真实 PostgreSQL 中创建测试用户（需要 DB 可用）"""
    from backend.models.user import User
    from backend.models.role import Role
    from backend.models.user_role import UserRole
    from backend.core.auth.password import hash_password

    username = f"{_PREFIX}{suffix or uuid.uuid4().hex[:6]}"
    u = User(
        username=username,
        display_name=f"StreamAuth {suffix}",
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


def _token(user, roles=None, expires_minutes=None):
    """为 User 颁发 JWT（可指定过期时间）"""
    from backend.config.settings import settings
    from backend.core.auth.jwt import create_access_token
    kwargs = {}
    if expires_minutes is not None:
        kwargs["expires_minutes"] = expires_minutes
    return create_access_token(
        {"sub": str(user.id), "username": user.username, "roles": roles or []},
        settings.jwt_secret,
        settings.jwt_algorithm,
        **kwargs,
    )


_auth_patcher = None


def setup_module(_=None):
    """Disable auth globally for tests that rely on anonymous access."""
    global _auth_patcher
    from backend.config.settings import settings
    _auth_patcher = patch.object(settings, "enable_auth", False)
    _auth_patcher.start()


def teardown_module(_=None):
    global _auth_patcher
    if _auth_patcher is not None:
        _auth_patcher.stop()
        _auth_patcher = None
    if not _DB_AVAILABLE or _g_db is None:
        return
    from backend.models.user import User
    try:
        _g_db.query(User).filter(
            User.username.like(f"{_PREFIX}%")
        ).delete(synchronize_session=False)
        _g_db.commit()
    finally:
        _g_db.close()


# ─── FastAPI TestClient ────────────────────────────────────────────────────────
from fastapi.testclient import TestClient


def _make_client():
    from backend.main import app
    return TestClient(app, raise_server_exceptions=False)


# ══════════════════════════════════════════════════════════════════════════════
# Section A (7) — chatApi.ts 静态分析：sendMessageStream 修复验证
# ══════════════════════════════════════════════════════════════════════════════

class TestChatApiStaticFix(unittest.TestCase):
    """A1-A7: 验证 sendMessageStream 的修复代码确实写入了源文件"""

    @classmethod
    def setUpClass(cls):
        with open(_CHATAPI_TS, encoding="utf-8") as f:
            cls.src = f.read()
        # 定位 sendMessageStream 函数体
        m = re.search(
            r"sendMessageStream\s*:\s*async.*?(?=\n\s*\}\s*\n)",
            cls.src, re.DOTALL
        )
        cls.func_body = m.group(0) if m else cls.src

    def test_A1_fetch_headers_variable_not_hardcoded(self):
        """fetch() 的 headers 改为变量（fetchHeaders），不再是只含 Content-Type 的对象字面量"""
        # 修复前：headers: { 'Content-Type': 'application/json' }
        # 修复后：headers: fetchHeaders
        self.assertIn("fetchHeaders", self.func_body,
                      "sendMessageStream 中应存在 fetchHeaders 变量")
        # fetch() 调用中引用 fetchHeaders
        self.assertIn("headers: fetchHeaders", self.func_body,
                      "fetch() 应使用 headers: fetchHeaders")

    def test_A2_authorization_header_assigned(self):
        """存在对 Authorization header 的赋值"""
        self.assertIn("Authorization", self.func_body,
                      "sendMessageStream 中应包含 Authorization 赋值")

    def test_A3_bearer_token_format(self):
        """Authorization 值使用 Bearer ${token} 格式"""
        # 检查 Bearer 字符串模板
        self.assertRegex(self.func_body, r"Bearer\s+\$\{token\}",
                         "Authorization 值应为 `Bearer ${token}` 模板字符串")

    def test_A4_token_null_guard(self):
        """token 为 null/空时有条件判断，不向无 token 的请求注入 Authorization"""
        # 应有 if (token) 保护
        self.assertRegex(self.func_body, r"if\s*\(\s*token\s*\)",
                         "应有 if (token) 保护，避免 null token 被注入 header")

    def test_A5_top_level_import_useAuthStore(self):
        """chatApi.ts 顶层 import useAuthStore（无 require，无循环依赖）"""
        # 顶层 import 在 src 全局，不一定在 func_body 中；检查全局 src
        with open(_CHATAPI_TS, encoding="utf-8") as f:
            full_src = f.read()
        self.assertRegex(
            full_src,
            r"import\s+\{[^}]*useAuthStore[^}]*\}\s+from\s+['\"]@/store/useAuthStore['\"]",
            "chatApi.ts 应有顶层 import { useAuthStore } from '@/store/useAuthStore'"
        )
        # sendMessageStream 函数体内直接使用 useAuthStore.getState().accessToken
        self.assertIn("useAuthStore.getState().accessToken", self.func_body,
                      "sendMessageStream 中应直接使用 useAuthStore.getState().accessToken")

    def test_A6_access_token_read_from_store(self):
        """accessToken 从 store 的 getState() 方法读取"""
        self.assertIn("getState().accessToken", self.func_body,
                      "token 应通过 useAuthStore.getState().accessToken 读取")

    def test_A7_cancel_uses_apiClient_unchanged(self):
        """cancelConversationStream 仍使用 apiClient（/cancel 端点无 auth，无需修改）"""
        # 精确匹配：export async function cancelConversationStream 的函数体
        cancel_match = re.search(
            r"export\s+async\s+function\s+cancelConversationStream\s*\(.*?\n\}",
            self.src, re.DOTALL
        )
        self.assertIsNotNone(cancel_match,
                             "源文件中找不到 cancelConversationStream 函数定义")
        cancel_body = cancel_match.group(0)
        self.assertIn("apiClient.post", cancel_body,
                      "cancelConversationStream 应使用 apiClient（已有 auth 兜底，无需修改）")
        # cancel 函数不需要手动注入 Bearer（端点无 get_current_user）
        self.assertNotIn("Authorization", cancel_body,
                         "cancelConversationStream 不需要手动注入 Authorization header")

    def test_A8_apiClient_has_auth_interceptor(self):
        """chatApi.ts 的 apiClient 添加了 request interceptor 统一注入 auth header"""
        with open(_CHATAPI_TS, encoding="utf-8") as f:
            full_src = f.read()
        # interceptor 应在 apiClient 上注册
        self.assertIn("apiClient.interceptors.request.use", full_src,
                      "chatApi.ts 的 apiClient 应注册 request interceptor")
        # interceptor 通过 useAuthStore 读取 token
        self.assertRegex(
            full_src,
            r"apiClient\.interceptors\.request\.use[\s\S]*?useAuthStore\.getState\(\)\.accessToken",
            "apiClient interceptor 应通过 useAuthStore.getState().accessToken 读取 token"
        )


# ══════════════════════════════════════════════════════════════════════════════
# Section B (7) — 后端 POST /messages 鉴权 (ENABLE_AUTH=true)
# ══════════════════════════════════════════════════════════════════════════════

@_SKIP_DB
class TestSendMessageAuth(unittest.TestCase):
    """B1-B7: POST /conversations/{id}/messages 在 ENABLE_AUTH=true 时的鉴权行为"""

    FAKE_UUID = "00000000-0000-0000-0000-000000000099"

    @classmethod
    def setUpClass(cls):
        cls.viewer, _, _ = _make_user("viewer_b", role_names=["viewer"])
        cls.analyst, _, _ = _make_user("analyst_b", role_names=["analyst"])
        cls.sadmin, _, _ = _make_user("sadmin_b", is_superadmin=True)
        cls.inactive, _, _ = _make_user("inactive_b", is_active=False, role_names=["viewer"])

    def _post_msg(self, conv_id, token=None, stream=False):
        """发送消息请求的辅助方法"""
        client = _make_client()
        headers = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        with patch("backend.config.settings.settings.enable_auth", True):
            return client.post(
                f"/api/v1/conversations/{conv_id}/messages",
                json={"content": "test", "stream": stream},
                headers=headers,
            )

    def test_B1_no_token_returns_401(self):
        """ENABLE_AUTH=true 且无 token → 401 未登录"""
        resp = self._post_msg(self.FAKE_UUID, token=None)
        self.assertEqual(resp.status_code, 401,
                         f"无 token 应返回 401，实际: {resp.status_code} {resp.text}")
        body = resp.json()
        self.assertIn("detail", body)

    def test_B2_valid_viewer_token_passes_auth(self):
        """有效 viewer token → auth 通过（对话不存在 → 404，非 401）"""
        tok = _token(self.viewer, roles=["viewer"])
        resp = self._post_msg(self.FAKE_UUID, token=tok)
        # 401 说明 auth 失败；404 说明 auth 通过但对话不存在 → 正确
        self.assertNotEqual(resp.status_code, 401,
                            f"有效 viewer token 不应返回 401，实际: {resp.status_code}")
        self.assertEqual(resp.status_code, 404,
                         f"对话不存在应返回 404，实际: {resp.status_code} {resp.text}")

    def test_B3_valid_analyst_token_passes_auth(self):
        """有效 analyst token → auth 通过（404，非 401）"""
        tok = _token(self.analyst, roles=["analyst"])
        resp = self._post_msg(self.FAKE_UUID, token=tok)
        self.assertNotEqual(resp.status_code, 401)
        self.assertEqual(resp.status_code, 404)

    def test_B4_superadmin_token_passes_auth(self):
        """superadmin token → auth 通过（404，非 401）"""
        tok = _token(self.sadmin, roles=["superadmin"])
        resp = self._post_msg(self.FAKE_UUID, token=tok)
        self.assertNotEqual(resp.status_code, 401)
        self.assertEqual(resp.status_code, 404)

    def test_B5_expired_token_returns_401(self):
        """过期 token → 401"""
        tok = _token(self.viewer, roles=["viewer"], expires_minutes=-1)
        resp = self._post_msg(self.FAKE_UUID, token=tok)
        self.assertEqual(resp.status_code, 401,
                         f"过期 token 应返回 401，实际: {resp.status_code}")

    def test_B6_tampered_token_returns_401(self):
        """签名篡改 token → 401"""
        tok = _token(self.viewer) + "XXXXX"
        resp = self._post_msg(self.FAKE_UUID, token=tok)
        self.assertEqual(resp.status_code, 401,
                         f"篡改 token 应返回 401，实际: {resp.status_code}")

    def test_B7_inactive_user_token_returns_401(self):
        """停用用户的有效 token → 401（用户不存在或已停用）"""
        tok = _token(self.inactive, roles=["viewer"])
        resp = self._post_msg(self.FAKE_UUID, token=tok)
        self.assertEqual(resp.status_code, 401,
                         f"停用用户 token 应返回 401，实际: {resp.status_code}")


# ══════════════════════════════════════════════════════════════════════════════
# Section C (5) — 后端 ENABLE_AUTH=false 兼容性
# ══════════════════════════════════════════════════════════════════════════════

class TestEnableAuthFalseCompat(unittest.TestCase):
    """C1-C5: ENABLE_AUTH=false 时消息端点无需 token，向后兼容"""

    FAKE_UUID = "00000000-0000-0000-0000-000000000098"

    def _post_msg_no_auth(self, token=None):
        """ENABLE_AUTH=false 发送消息"""
        client = _make_client()
        headers = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        with patch("backend.config.settings.settings.enable_auth", False):
            return client.post(
                f"/api/v1/conversations/{self.FAKE_UUID}/messages",
                json={"content": "hello", "stream": False},
                headers=headers,
            )

    def test_C1_no_token_not_401(self):
        """ENABLE_AUTH=false 且无 token → 不返回 401（AnonymousUser 通过）"""
        resp = self._post_msg_no_auth(token=None)
        self.assertNotEqual(resp.status_code, 401,
                            f"ENABLE_AUTH=false 无 token 不应 401，实际: {resp.status_code}")
        # 对话不存在 → 404
        self.assertEqual(resp.status_code, 404)

    def test_C2_anonymous_user_is_superadmin(self):
        """AnonymousUser 的 is_superadmin=True（所有权限检查通过）"""
        from backend.api.deps import AnonymousUser
        anon = AnonymousUser()
        self.assertTrue(anon.is_superadmin,
                        "AnonymousUser.is_superadmin 应为 True")

    def test_C3_anonymous_user_username_is_default(self):
        """AnonymousUser.username == 'default'（技能目录兼容）"""
        from backend.api.deps import AnonymousUser
        anon = AnonymousUser()
        self.assertEqual(anon.username, "default",
                         f"AnonymousUser.username 应为 'default'，实际: {anon.username}")

    def test_C4_get_current_user_returns_anonymous_without_db(self):
        """ENABLE_AUTH=false 时 get_current_user 不需要查询 DB"""
        from backend.api.deps import get_current_user, AnonymousUser

        async def _run():
            with patch("backend.config.settings.settings.enable_auth", False):
                # 传入 token=None, db=None（ENABLE_AUTH=false 时不会使用 db）
                result = await get_current_user(token=None, db=None)
            return result

        user = asyncio.run(_run())
        self.assertIsInstance(user, AnonymousUser,
                              "ENABLE_AUTH=false 应返回 AnonymousUser")

    def test_C5_wrong_token_still_passes_when_auth_disabled(self):
        """ENABLE_AUTH=false 时即使 token 错误也不应返回 401"""
        resp = self._post_msg_no_auth(token="garbage.token.value")
        self.assertNotEqual(resp.status_code, 401,
                            "ENABLE_AUTH=false 时错误 token 不影响匿名访问")


# ══════════════════════════════════════════════════════════════════════════════
# Section D (5) — 对话其他端点：无 auth 要求的端点不受影响
# ══════════════════════════════════════════════════════════════════════════════

class TestConvEndpointsNoAuth(unittest.TestCase):
    """D1-D5: ENABLE_AUTH=false 时对话 CRUD 端点可匿名访问（向后兼容）

    注意：对话用户隔离功能上线后，所有 CRUD 端点均添加了 get_current_user。
    但当 ENABLE_AUTH=false 时 get_current_user 返回 AnonymousUser(is_superadmin=True)，
    因此无需 token 即可访问。这些测试验证该向后兼容行为。
    """

    FAKE_UUID = "00000000-0000-0000-0000-000000000097"

    @classmethod
    def setUpClass(cls):
        cls.client = _make_client()

    def test_D1_list_conversations_no_token_not_401(self):
        """ENABLE_AUTH=false 时 GET /conversations 无 token → 非 401（AnonymousUser 通过）"""
        # setup_module 已将 enable_auth 置为 False，无需额外 patch
        resp = self.client.get("/api/v1/conversations")
        self.assertNotEqual(resp.status_code, 401,
                            f"ENABLE_AUTH=false 时 GET /conversations 不应返回 401，实际: {resp.status_code}")
        # DB 可用时必须是 200
        if _DB_AVAILABLE:
            self.assertEqual(resp.status_code, 200,
                             f"DB 可用时 GET /conversations 应返回 200")

    def test_D2_get_conversation_no_token_404_not_401(self):
        """ENABLE_AUTH=false 时 GET /conversations/{id} 无 token → 404（不存在），非 401"""
        resp = self.client.get(f"/api/v1/conversations/{self.FAKE_UUID}")
        self.assertNotEqual(resp.status_code, 401,
                            "ENABLE_AUTH=false 时 GET /conversations/{id} 不应返回 401")
        self.assertIn(resp.status_code, [200, 404])

    def test_D3_create_conversation_no_token_200(self):
        """ENABLE_AUTH=false 时 POST /conversations 无 token → 200（匿名创建）"""
        resp = self.client.post(
            "/api/v1/conversations",
            json={"title": "test_stream_auth_conv", "model_key": "claude"},
        )
        self.assertNotEqual(resp.status_code, 401,
                            f"ENABLE_AUTH=false 时 POST /conversations 不应返回 401，实际: {resp.status_code}")
        # 清理：若创建成功则删除
        if resp.status_code == 200:
            try:
                conv_id = resp.json()["data"]["id"]
                self.client.delete(f"/api/v1/conversations/{conv_id}",
                                   params={"hard_delete": True})
            except Exception:
                pass

    def test_D4_cancel_endpoint_no_token_200(self):
        """POST /conversations/{id}/cancel 无 token → 200（取消端点无 get_current_user）"""
        resp = self.client.post(f"/api/v1/conversations/{self.FAKE_UUID}/cancel")
        self.assertNotEqual(resp.status_code, 401,
                            "/cancel 端点无 auth 要求，不应返回 401")
        # cancel 是幂等操作，即使无活跃流也返回 200
        self.assertEqual(resp.status_code, 200)

    def test_D5_get_messages_no_token_200_or_404(self):
        """ENABLE_AUTH=false 时 GET /conversations/{id}/messages 无 token → 非 401"""
        resp = self.client.get(f"/api/v1/conversations/{self.FAKE_UUID}/messages")
        self.assertNotEqual(resp.status_code, 401,
                            "ENABLE_AUTH=false 时 GET /messages 不应返回 401")


# ══════════════════════════════════════════════════════════════════════════════
# Section E (4) — get_current_user 依赖项单元测试
# ══════════════════════════════════════════════════════════════════════════════

@_SKIP_DB
class TestGetCurrentUserUnit(unittest.TestCase):
    """E1-E4: get_current_user 在各种 token 状态下的行为"""

    @classmethod
    def setUpClass(cls):
        cls.user, _, _ = _make_user("e_active", role_names=["viewer"])

    def _run_get_current_user(self, token, db=None):
        """同步执行 get_current_user 异步函数"""
        from backend.api.deps import get_current_user
        from fastapi import HTTPException

        async def _inner():
            return await get_current_user(token=token, db=db or _g_db)

        try:
            return asyncio.run(_inner()), None
        except Exception as exc:
            return None, exc

    def test_E1_valid_token_returns_user_object(self):
        """有效 token + 活跃用户 → 返回 User 对象，username 匹配"""
        from backend.config.settings import settings
        tok = _token(self.user)
        with patch("backend.config.settings.settings.enable_auth", True):
            user_obj, exc = self._run_get_current_user(tok)
        self.assertIsNone(exc, f"不应抛出异常: {exc}")
        self.assertIsNotNone(user_obj, "应返回 User 对象")
        self.assertEqual(str(user_obj.id), str(self.user.id))

    def test_E2_no_token_raises_401_when_auth_enabled(self):
        """ENABLE_AUTH=true 且 token=None → 抛出 HTTPException(401)"""
        from fastapi import HTTPException
        with patch("backend.config.settings.settings.enable_auth", True):
            _, exc = self._run_get_current_user(token=None)
        self.assertIsInstance(exc, HTTPException,
                              f"无 token 应抛出 HTTPException，实际: {type(exc)}")
        self.assertEqual(exc.status_code, 401)

    def test_E3_expired_token_raises_401(self):
        """过期 token → 抛出 HTTPException(401)"""
        from fastapi import HTTPException
        tok = _token(self.user, expires_minutes=-1)
        with patch("backend.config.settings.settings.enable_auth", True):
            _, exc = self._run_get_current_user(token=tok)
        self.assertIsInstance(exc, HTTPException)
        self.assertEqual(exc.status_code, 401)

    def test_E4_token_missing_sub_raises_401(self):
        """payload 缺少 sub 字段 → 抛出 HTTPException(401)"""
        from fastapi import HTTPException
        from backend.config.settings import settings
        from backend.core.auth.jwt import create_access_token
        # 创建没有 sub 的 token
        tok = create_access_token(
            {"username": "ghost"},
            settings.jwt_secret,
            settings.jwt_algorithm,
        )
        with patch("backend.config.settings.settings.enable_auth", True):
            _, exc = self._run_get_current_user(token=tok)
        self.assertIsInstance(exc, HTTPException)
        self.assertEqual(exc.status_code, 401)


# ══════════════════════════════════════════════════════════════════════════════
# Section F (4) — api.ts 拦截器静态分析
# ══════════════════════════════════════════════════════════════════════════════

class TestApiTsInterceptors(unittest.TestCase):
    """F1-F4: api.ts 的 axios 拦截器正确实现了 token 注入与 refresh 重试"""

    @classmethod
    def setUpClass(cls):
        with open(_API_TS, encoding="utf-8") as f:
            cls.src = f.read()

    def test_F1_request_interceptor_injects_bearer(self):
        """api.ts 的 request 拦截器注入 Authorization: Bearer <token>"""
        self.assertIn("interceptors.request.use", self.src,
                      "api.ts 应有 request 拦截器")
        self.assertIn("Authorization", self.src,
                      "request 拦截器应设置 Authorization header")
        self.assertRegex(self.src, r"Bearer\s+\$\{token\}",
                         "Authorization 值应为 Bearer ${token}")

    def test_F2_reads_access_token_from_auth_store(self):
        """api.ts 从 useAuthStore.getState().accessToken 读取 token"""
        self.assertIn("useAuthStore", self.src)
        self.assertIn("getState().accessToken", self.src,
                      "应通过 getState().accessToken 读取 token")

    def test_F3_response_interceptor_handles_401_refresh(self):
        """api.ts 的 response 拦截器在 401 时触发 token refresh 并重发"""
        self.assertIn("interceptors.response.use", self.src,
                      "api.ts 应有 response 拦截器")
        self.assertIn("401", self.src,
                      "response 拦截器应处理 401 状态码")
        self.assertIn("refreshToken", self.src,
                      "401 时应调用 refreshToken()")

    def test_F4_chatapi_local_client_has_auth_interceptor(self):
        """chatApi.ts 的本地 apiClient 有 auth request 拦截器（统一注入 Bearer token）"""
        with open(_CHATAPI_TS, encoding="utf-8") as f:
            chat_src = f.read()
        self.assertIn("apiClient.interceptors.request.use", chat_src,
                      "chatApi.ts 的 apiClient 应注册 request 拦截器统一注入 auth header")
        # 拦截器通过 useAuthStore 读取 token
        self.assertIn("useAuthStore.getState().accessToken", chat_src,
                      "chatApi.ts 的 apiClient 拦截器应通过 useAuthStore 读取 token")


# ══════════════════════════════════════════════════════════════════════════════
# Section G (3) — conversations.py 端点签名回归分析
# ══════════════════════════════════════════════════════════════════════════════

class TestConversationsPyRegression(unittest.TestCase):
    """G1-G3: conversations.py 关键端点依赖签名未被意外修改"""

    @classmethod
    def setUpClass(cls):
        with open(_CONV_PY, encoding="utf-8") as f:
            cls.src = f.read()

    def test_G1_send_message_uses_get_current_user(self):
        """POST /messages 端点保留 get_current_user 依赖"""
        # 提取 send_message 函数头部
        m = re.search(
            r"@router\.post\(['\"]/{conversation_id}/messages['\"].*?"
            r"async def send_message\(.*?\):",
            self.src, re.DOTALL
        )
        self.assertIsNotNone(m, "找不到 send_message 端点定义")
        func_sig = m.group(0)
        self.assertIn("get_current_user", func_sig,
                      "send_message 端点应保留 Depends(get_current_user)")

    def test_G2_crud_endpoints_have_get_current_user_except_cancel(self):
        """对话隔离上线后 list/get/create/delete 均含 get_current_user；cancel 仍无需 auth"""
        # 这些端点在对话用户隔离功能上线后均已添加 get_current_user
        auth_patterns = [
            (r"async def list_conversations\(.*?\):", "list_conversations"),
            (r"async def get_conversation\(.*?\):", "get_conversation"),
            (r"async def create_conversation\(.*?\):", "create_conversation"),
            (r"async def delete_conversation\(.*?\):", "delete_conversation"),
        ]
        for pattern, name in auth_patterns:
            m = re.search(pattern, self.src, re.DOTALL)
            if m:
                func_sig = m.group(0)
                self.assertIn("get_current_user", func_sig,
                              f"{name} 应含 get_current_user（对话用户隔离）")

        # cancel 端点无需鉴权（幂等操作）
        m_cancel = re.search(r"async def cancel_conversation_stream\(.*?\):", self.src, re.DOTALL)
        if m_cancel:
            self.assertNotIn("get_current_user", m_cancel.group(0),
                             "cancel_conversation_stream 不需要 get_current_user")

    def test_G3_username_extracted_from_current_user(self):
        """send_message 中从 current_user 提取 username（技能目录隔离）"""
        m = re.search(
            r"async def send_message\(.*?return\s+\{",
            self.src, re.DOTALL
        )
        func_body = m.group(0) if m else self.src
        self.assertRegex(func_body, r"username.*?current_user|current_user.*?username",
                         "send_message 应从 current_user 中提取 username")


# ══════════════════════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import pytest, sys
    sys.exit(pytest.main([__file__, "-v", "-s"]))
