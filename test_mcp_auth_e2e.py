#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_mcp_auth_e2e.py — MCP API 鉴权 + 前端白屏修复 端到端测试

针对以下 5 项修复的完整测试，覆盖后端契约、前端静态结构、
角色权限矩阵、端到端鉴权流程。

Section A — 后端 /mcp/servers 响应契约               ( 6 tests)
Section B — 前端数据提取与安全守卫                    ( 8 tests)
Section C — Auth 拦截器架构                           ( 6 tests)
Section D — initAuth 并发安全                         ( 5 tests)
Section E — 角色权限矩阵（MCP + 菜单）                ( 8 tests)
Section F — 端到端鉴权流程（TestClient）              ( 6 tests)

Total: 39 tests

运行: /d/ProgramData/Anaconda3/envs/dataagent/python.exe test_mcp_auth_e2e.py
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock, patch

# ── Path setup ────────────────────────────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent / "backend"))

os.environ.setdefault("ENABLE_AUTH", "False")
os.environ.setdefault("ADMIN_SECRET_TOKEN", "test-mcp-auth-e2e")

PASS = "[PASS]"
FAIL = "[FAIL]"
results: list = []

FRONTEND_ROOT = Path(__file__).parent / "frontend" / "src"
API_TS       = FRONTEND_ROOT / "services" / "api.ts"
MCP_TSX      = FRONTEND_ROOT / "components" / "chat" / "MCPStatus.tsx"
AUTH_STORE   = FRONTEND_ROOT / "store" / "useAuthStore.ts"
APP_LAYOUT   = FRONTEND_ROOT / "components" / "AppLayout.tsx"

# 模拟正确格式的服务器列表（与 MCPStatus 接口匹配）
SAMPLE_SERVERS = [
    {
        "name": "clickhouse-my",
        "type": "clickhouse",
        "version": "23.8.1",
        "tool_count": 10,
        "resource_count": 0,
    },
    {
        "name": "filesystem",
        "type": "filesystem",
        "version": "1.0",
        "tool_count": 5,
        "resource_count": 2,
    },
]


def check(name: str, condition: bool, detail: str = "") -> bool:
    status = PASS if condition else FAIL
    results.append((name, condition))
    msg = f"  {status} {name}"
    if detail:
        msg += f"  [{detail}]"
    print(msg)
    return condition


# ─────────────────────────────────────────────────────────────────────────────
# 共用 TestClient helper（复用 /mcp/ 路由）
# ─────────────────────────────────────────────────────────────────────────────

def _build_mcp_client(enable_auth: bool = False, server_list: list = None):
    """
    构建包含 /mcp/ 路由的 FastAPI TestClient。

    注意: mcp.py 用 `from backend.mcp.manager import get_mcp_manager`（复制引用），
    因此 mock 必须打在 backend.api.mcp.get_mcp_manager，不能打在源模块属性上。

    enable_auth: True 时后续测试需通过 dependency_overrides 提供用户身份。
    server_list: mock manager 返回的服务器列表，默认 SAMPLE_SERVERS。
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from backend.api.mcp import router as mcp_router

    app = FastAPI()
    app.include_router(mcp_router, prefix="/api/v1")

    mock_mgr = MagicMock()
    mock_mgr.list_servers.return_value = server_list if server_list is not None else SAMPLE_SERVERS
    mock_mgr.servers = {}
    mock_mgr.server_configs = {}

    # mcp.py 使用 from ... import get_mcp_manager，必须打在 backend.api.mcp 模块上
    import backend.api.mcp as _mcp_api_mod
    original_get = _mcp_api_mod.get_mcp_manager

    def _patched_get():
        return mock_mgr

    _mcp_api_mod.get_mcp_manager = _patched_get

    client = TestClient(app, raise_server_exceptions=False)
    client._mcp_manager_restore = (original_get, _mcp_api_mod)
    client._app = app
    return client


def _restore(client):
    orig, mod = client._mcp_manager_restore
    mod.get_mcp_manager = orig
    client._app.dependency_overrides.clear()


# ─────────────────────────────────────────────────────────────────────────────
# Section A: 后端 /mcp/servers 响应契约
# ─────────────────────────────────────────────────────────────────────────────

def test_section_a():
    print("\n=== Section A: 后端 /mcp/servers 响应契约 ===")

    from backend.api.deps import get_current_user, get_db
    from backend.api.deps import AnonymousUser

    # ── A1: ENABLE_AUTH=false → AnonymousUser → 200，响应体含 success + data ──
    client_a1 = _build_mcp_client(enable_auth=False, server_list=SAMPLE_SERVERS)
    try:
        with patch("backend.api.deps.settings") as ms:
            ms.enable_auth = False
            r = client_a1.get("/api/v1/mcp/servers")
        body = r.json()
        check(
            "A1: ENABLE_AUTH=false → 200 with {success, data} envelope",
            r.status_code == 200 and "success" in body and "data" in body,
            f"status={r.status_code}, keys={list(body.keys())}",
        )
    finally:
        _restore(client_a1)

    # ── A2: data 字段是列表（不是 null / 对象）──────────────────────────────────
    client_a2 = _build_mcp_client(server_list=SAMPLE_SERVERS)
    try:
        with patch("backend.api.deps.settings") as ms:
            ms.enable_auth = False
            r2 = client_a2.get("/api/v1/mcp/servers")
        body2 = r2.json()
        check(
            "A2: response.data is always a list (not object/null)",
            isinstance(body2.get("data"), list),
            f"type={type(body2.get('data')).__name__}",
        )
    finally:
        _restore(client_a2)

    # ── A3: 列表中每个 server 含必要字段 (name/type/version/tool_count/resource_count)
    client_a3 = _build_mcp_client(server_list=SAMPLE_SERVERS)
    try:
        with patch("backend.api.deps.settings") as ms:
            ms.enable_auth = False
            r3 = client_a3.get("/api/v1/mcp/servers")
        servers_list = r3.json().get("data", [])
        required_fields = {"name", "type", "version", "tool_count", "resource_count"}
        all_have_fields = all(required_fields.issubset(s.keys()) for s in servers_list)
        check(
            "A3: each server in data has name/type/version/tool_count/resource_count",
            len(servers_list) > 0 and all_have_fields,
            f"servers={len(servers_list)}, fields_ok={all_have_fields}",
        )
    finally:
        _restore(client_a3)

    # ── A4: 空 server 列表时 data=[] 而非 null ─────────────────────────────────
    client_a4 = _build_mcp_client(server_list=[])
    try:
        with patch("backend.api.deps.settings") as ms:
            ms.enable_auth = False
            r4 = client_a4.get("/api/v1/mcp/servers")
        body4 = r4.json()
        check(
            "A4: empty server list → data=[] (not null)",
            r4.status_code == 200 and body4.get("data") == [],
            f"data={body4.get('data')}",
        )
    finally:
        _restore(client_a4)

    # ── A5: ENABLE_AUTH=true + 无 token → 401 ─────────────────────────────────
    client_a5 = _build_mcp_client(enable_auth=True)
    try:
        with patch("backend.api.deps.settings") as ms:
            ms.enable_auth = True
            ms.jwt_secret = "test-secret"
            ms.jwt_algorithm = "HS256"
            r5 = client_a5.get("/api/v1/mcp/servers")
        check(
            "A5: ENABLE_AUTH=true + no token → 401",
            r5.status_code == 401,
            f"status={r5.status_code}",
        )
    finally:
        _restore(client_a5)

    # ── A6: success 字段为 True（非 False / 不存在）────────────────────────────
    client_a6 = _build_mcp_client(server_list=SAMPLE_SERVERS)
    try:
        with patch("backend.api.deps.settings") as ms:
            ms.enable_auth = False
            r6 = client_a6.get("/api/v1/mcp/servers")
        check(
            "A6: response.success is True",
            r6.json().get("success") is True,
            f"success={r6.json().get('success')}",
        )
    finally:
        _restore(client_a6)


# ─────────────────────────────────────────────────────────────────────────────
# Section B: 前端数据提取与安全守卫（静态分析）
# ─────────────────────────────────────────────────────────────────────────────

def test_section_b():
    print("\n=== Section B: 前端数据提取与安全守卫 ===")

    api_content = API_TS.read_text(encoding="utf-8")
    tsx_content = MCP_TSX.read_text(encoding="utf-8")

    # ── B1: mcpApi.getServers 使用 response.data.data（不是 response.data）─────
    # 后端返回 {success,data:[...]}，axios response.data = 整个 JSON 体
    # 必须取 response.data.data 才得到数组
    if "getServers" in api_content:
        start = api_content.index("getServers")
        snippet = api_content[start:start + 500]
        uses_data_data = "data?.data" in snippet or "data.data" in snippet
        uses_bare_data = (
            "return (response as any).data ?? []" in snippet
            or "return response.data ?? []" in snippet
        )
        check(
            "B1: mcpApi.getServers() uses response.data.data (extracts inner array)",
            uses_data_data and not uses_bare_data,
            f"uses_data_data={uses_data_data}, uses_bare_data={uses_bare_data}",
        )
    else:
        check("B1: mcpApi.getServers() uses response.data.data (extracts inner array)", False,
              "getServers not found in api.ts")

    # ── B2: getServers 有 ?? [] 兜底（空 data 不崩溃）──────────────────────────
    if "getServers" in api_content:
        start = api_content.index("getServers")
        snippet = api_content[start:start + 500]
        has_fallback = "?? []" in snippet
        check(
            "B2: mcpApi.getServers() has ?? [] fallback for null/undefined data",
            has_fallback,
            f"has_fallback={has_fallback}",
        )
    else:
        check("B2: mcpApi.getServers() has ?? [] fallback for null/undefined data", False,
              "getServers not found")

    # ── B3: MCPStatus.tsx 使用 Array.isArray 守卫（防 map() TypeError 白屏）────
    has_array_guard = "Array.isArray" in tsx_content
    check(
        "B3: MCPStatus has Array.isArray guard before setServers (prevents TypeError crash)",
        has_array_guard,
        f"has_array_guard={has_array_guard}",
    )

    # ── B4: MCPStatus 调用 mcpApi.getServers()（非裸 fetch）────────────────────
    uses_mcp_api = "mcpApi.getServers()" in tsx_content
    uses_raw_fetch = "fetch('/api/v1/mcp" in tsx_content or 'fetch("/api/v1/mcp' in tsx_content
    check(
        "B4: MCPStatus uses mcpApi.getServers() (not raw fetch)",
        uses_mcp_api and not uses_raw_fetch,
        f"uses_mcpApi={uses_mcp_api}, uses_raw_fetch={uses_raw_fetch}",
    )

    # ── B5: MCPStatus 对 401/403 静默降级（不显示错误文本）────────────────────
    has_401_check = "401" in tsx_content
    has_403_check = "403" in tsx_content
    has_set_error = "setError(" in tsx_content
    has_return_null = "return null" in tsx_content
    check(
        "B5: MCPStatus silently degrades on 401/403 (no setError, returns null)",
        has_401_check and has_403_check and not has_set_error and has_return_null,
        f"has_401={has_401_check}, has_403={has_403_check}, "
        f"has_setError={has_set_error}, has_return_null={has_return_null}",
    )

    # ── B6: mcpApi 使用 apiClient（带 Bearer token 注入），非 axios 裸请求 ────
    if "mcpApi" in api_content:
        start = api_content.index("mcpApi")
        snippet = api_content[start:start + 400]
        uses_api_client = "apiClient" in snippet
        uses_raw_axios = (
            "axios.get('/mcp" in snippet
            or "axios.get(\"/mcp" in snippet
        )
        check(
            "B6: mcpApi.getServers() uses apiClient (Bearer token auto-injected)",
            uses_api_client and not uses_raw_axios,
            f"uses_apiClient={uses_api_client}, uses_raw_axios={uses_raw_axios}",
        )
    else:
        check("B6: mcpApi.getServers() uses apiClient (Bearer token auto-injected)", False,
              "mcpApi not found in api.ts")

    # ── B7: MCPStatus.tsx 不含 error state（已彻底移除旧错误显示逻辑）──────────
    # 旧版有 const [error, setError] = useState<string | null>(null)
    has_error_state = "useState<string | null>(null)" in tsx_content and "setError" in tsx_content
    check(
        "B7: MCPStatus has no legacy error state (error display removed)",
        not has_error_state,
        f"has_error_state={has_error_state}",
    )

    # ── B8: MCPStatus 从 api.ts 导入 mcpApi（而非自定义 fetch 函数）────────────
    has_import_mcp_api = "mcpApi" in tsx_content and "from" in tsx_content
    check(
        "B8: MCPStatus.tsx imports mcpApi from services/api",
        has_import_mcp_api,
        f"has_import_mcpApi={has_import_mcp_api}",
    )


# ─────────────────────────────────────────────────────────────────────────────
# Section C: Auth 拦截器架构（静态分析）
# ─────────────────────────────────────────────────────────────────────────────

def test_section_c():
    print("\n=== Section C: Auth 拦截器架构 ===")

    api_content = API_TS.read_text(encoding="utf-8")

    # ── C1: 只有一个响应拦截器（已移除重复的第二个）────────────────────────────
    interceptor_count = api_content.count("apiClient.interceptors.response.use(")
    check(
        "C1: exactly one apiClient.interceptors.response.use() (duplicate removed)",
        interceptor_count == 1,
        f"count={interceptor_count} (expected 1)",
    )

    # ── C2: 无 window.location.href 硬跳转（防白屏）───────────────────────────
    has_hard_redirect = "window.location.href" in api_content
    check(
        "C2: no window.location.href hard redirect in api.ts",
        not has_hard_redirect,
        f"has_hard_redirect={has_hard_redirect}",
    )

    # ── C3: _isRefreshing 锁变量存在（防多请求并发 refresh）────────────────────
    has_refreshing_lock = "_isRefreshing" in api_content
    check(
        "C3: _isRefreshing lock present (concurrent 401 de-duplication)",
        has_refreshing_lock,
        f"has_lock={has_refreshing_lock}",
    )

    # ── C4: _retried 标记防止无限重试循环 ────────────────────────────────────
    has_retried = "_retried" in api_content
    check(
        "C4: _retried flag prevents infinite retry loop on 401",
        has_retried,
        f"has_retried={has_retried}",
    )

    # ── C5: /auth/login 和 /auth/refresh 被排除在 401 重试之外 ──────────────
    excludes_login = "/auth/login" in api_content
    excludes_refresh = "/auth/refresh" in api_content
    check(
        "C5: /auth/login and /auth/refresh excluded from 401 retry (prevents auth loop)",
        excludes_login and excludes_refresh,
        f"excludes_login={excludes_login}, excludes_refresh={excludes_refresh}",
    )

    # ── C6: refresh 失败时用 Promise.reject(error)（保留原始 error.response）─
    # 旧写法是 return Promise.reject(new Error(...))，丢失 err.response
    has_reject_new_error = "Promise.reject(new Error(" in api_content
    has_reject_error = "Promise.reject(error)" in api_content
    check(
        "C6: interceptor rejects with original error (not new Error), preserving err.response",
        not has_reject_new_error and has_reject_error,
        f"has_reject_new_Error={has_reject_new_error}, has_reject_error={has_reject_error}",
    )


# ─────────────────────────────────────────────────────────────────────────────
# Section D: initAuth 并发安全（静态分析）
# ─────────────────────────────────────────────────────────────────────────────

def test_section_d():
    print("\n=== Section D: initAuth 并发安全 ===")

    auth_content = AUTH_STORE.read_text(encoding="utf-8")

    # ── D1: _initAuthPromise 模块级去重变量存在 ──────────────────────────────
    has_promise_var = "_initAuthPromise" in auth_content
    check(
        "D1: _initAuthPromise module-level variable exists in useAuthStore",
        has_promise_var,
        f"has_var={has_promise_var}",
    )

    # ── D2: initAuth 存在 null check（早返回）──────────────────────────────
    has_null_check = (
        "_initAuthPromise !== null" in auth_content
        or "_initAuthPromise != null" in auth_content
    )
    check(
        "D2: initAuth early-returns if _initAuthPromise !== null (dedup guard)",
        has_null_check,
        f"has_null_check={has_null_check}",
    )

    # ── D3: .finally 中清空 _initAuthPromise = null ──────────────────────────
    has_finally_clear = "_initAuthPromise = null" in auth_content
    check(
        "D3: _initAuthPromise cleared in .finally() (allows re-init after completion)",
        has_finally_clear,
        f"has_finally_clear={has_finally_clear}",
    )

    # ── D4: initAuth 在所有路径设置 authChecked: true ────────────────────────
    # 验证 authChecked: true 出现次数 ≥ 2（多条成功/失败路径各自设置）
    auth_checked_count = auth_content.count("authChecked: true")
    check(
        "D4: authChecked: true set in multiple paths (all exit points of initAuth)",
        auth_checked_count >= 2,
        f"authChecked:true count={auth_checked_count}",
    )

    # ── D5: 并发调用 return _initAuthPromise（第二次调用等待第一次的 Promise）─
    has_return_promise = "return _initAuthPromise" in auth_content
    check(
        "D5: concurrent initAuth returns same promise (React 18 Strict Mode safe)",
        has_return_promise,
        f"has_return_promise={has_return_promise}",
    )


# ─────────────────────────────────────────────────────────────────────────────
# Section E: 角色权限矩阵（MCP + 菜单）
# ─────────────────────────────────────────────────────────────────────────────

def test_section_e():
    print("\n=== Section E: 角色权限矩阵（MCP + 菜单） ===")

    import importlib
    import backend.scripts.init_rbac as _rbac_mod
    importlib.reload(_rbac_mod)

    ROLES = _rbac_mod.ROLES

    # ── E1: analyst 角色含 settings:read（MCPStatus 可见）────────────────────
    analyst_perms = ROLES.get("analyst", {}).get("permissions", [])
    check(
        "E1: analyst role has settings:read (MCPStatus accessible)",
        "settings:read" in analyst_perms,
        f"analyst_perms={analyst_perms}",
    )

    # ── E2: admin 角色含 settings:read ────────────────────────────────────────
    admin_perms = ROLES.get("admin", {}).get("permissions", [])
    check(
        "E2: admin role has settings:read",
        "settings:read" in admin_perms,
        f"admin_perms={admin_perms}",
    )

    # ── E3: superadmin 角色含所有权限（含 settings:read/write）─────────────────
    superadmin_perms = ROLES.get("superadmin", {}).get("permissions", [])
    check(
        "E3: superadmin role has settings:read and settings:write",
        "settings:read" in superadmin_perms and "settings:write" in superadmin_perms,
        f"superadmin_perms has settings:read={('settings:read' in superadmin_perms)}",
    )

    # ── E4: viewer 角色不含 settings:read（MCPStatus 不可见）────────────────
    viewer_perms = ROLES.get("viewer", {}).get("permissions", [])
    check(
        "E4: viewer role does NOT have settings:read (MCPStatus hidden by 403)",
        "settings:read" not in viewer_perms,
        f"viewer_perms={viewer_perms}",
    )

    # ── E5~E8: AppLayout 菜单权限门控审计 ─────────────────────────────────────
    layout_content = APP_LAYOUT.read_text(encoding="utf-8")

    # E5: /roles 菜单设置了 perm: 'users:read'
    check(
        "E5: AppLayout /roles menu requires users:read permission",
        "'/roles'" in layout_content and "users:read" in layout_content,
        "",
    )

    # E6: /users 菜单设置了 perm: 'users:read'
    check(
        "E6: AppLayout /users menu requires users:read permission",
        "'/users'" in layout_content and "users:read" in layout_content,
        "",
    )

    # E7: /chat 菜单设置了 perm: 'chat:use'
    check(
        "E7: AppLayout /chat menu requires chat:use permission",
        "'/chat'" in layout_content and "chat:use" in layout_content,
        "",
    )

    # E8: /skills 菜单设置了 perm: 'skills.user:read'
    check(
        "E8: AppLayout /skills menu requires skills.user:read permission",
        "'/skills'" in layout_content and "skills.user:read" in layout_content,
        "",
    )


# ─────────────────────────────────────────────────────────────────────────────
# Section F: 端到端鉴权流程（TestClient + dependency overrides）
# ─────────────────────────────────────────────────────────────────────────────

def test_section_f():
    print("\n=== Section F: 端到端鉴权流程 ===")

    from backend.api.deps import get_current_user, get_db

    # 公共 helper：用 dependency override 注入指定权限列表的模拟用户
    def _client_with_perms(permissions: list, is_superadmin: bool = False):
        client = _build_mcp_client(enable_auth=True, server_list=SAMPLE_SERVERS)

        mock_user = MagicMock()
        mock_user.is_superadmin = is_superadmin
        mock_user.is_active = True

        async def _get_user():
            return mock_user

        async def _get_db():
            yield MagicMock()

        client._app.dependency_overrides[get_current_user] = _get_user
        client._app.dependency_overrides[get_db] = _get_db

        return client, mock_user, permissions

    # ── F1: superadmin → GET /mcp/servers → 200，data 是列表 ─────────────────
    client_f1, user_f1, _ = _client_with_perms([], is_superadmin=True)
    try:
        r1 = client_f1.get("/api/v1/mcp/servers")
        body1 = r1.json()
        check(
            "F1: superadmin → GET /mcp/servers → 200, data is list",
            r1.status_code == 200 and isinstance(body1.get("data"), list),
            f"status={r1.status_code}, data_type={type(body1.get('data')).__name__}",
        )
    finally:
        _restore(client_f1)

    # ── F2: settings:read 用户 → 200 ──────────────────────────────────────────
    client_f2, _, perms_f2 = _client_with_perms(["settings:read"])
    try:
        with patch("backend.core.rbac.get_user_permissions", return_value=perms_f2):
            r2 = client_f2.get("/api/v1/mcp/servers")
        check(
            "F2: user with settings:read → GET /mcp/servers → 200",
            r2.status_code == 200,
            f"status={r2.status_code}",
        )
    finally:
        _restore(client_f2)

    # ── F3: analyst 权限集（含 settings:read）→ 200 ───────────────────────────
    import importlib
    import backend.scripts.init_rbac as _rbac_mod
    importlib.reload(_rbac_mod)
    analyst_perms = _rbac_mod.ROLES["analyst"]["permissions"]
    client_f3, _, perms_f3 = _client_with_perms(analyst_perms)
    try:
        with patch("backend.core.rbac.get_user_permissions", return_value=perms_f3):
            r3 = client_f3.get("/api/v1/mcp/servers")
        check(
            "F3: analyst permissions (incl. settings:read) → 200",
            r3.status_code == 200,
            f"status={r3.status_code}",
        )
    finally:
        _restore(client_f3)

    # ── F4: viewer 权限集（无 settings:read）→ 403 ──────────────────────────
    viewer_perms = _rbac_mod.ROLES["viewer"]["permissions"]
    client_f4, _, perms_f4 = _client_with_perms(viewer_perms)
    try:
        with patch("backend.core.rbac.get_user_permissions", return_value=perms_f4):
            r4 = client_f4.get("/api/v1/mcp/servers")
        check(
            "F4: viewer permissions (no settings:read) → 403",
            r4.status_code == 403,
            f"status={r4.status_code}",
        )
    finally:
        _restore(client_f4)

    # ── F5: 无 token (ENABLE_AUTH=true) → 401 ────────────────────────────────
    client_f5 = _build_mcp_client(enable_auth=True, server_list=SAMPLE_SERVERS)
    try:
        with patch("backend.api.deps.settings") as ms:
            ms.enable_auth = True
            ms.jwt_secret = "test-secret"
            ms.jwt_algorithm = "HS256"
            r5 = client_f5.get("/api/v1/mcp/servers")
        check(
            "F5: no token (ENABLE_AUTH=true) → 401 Unauthorized",
            r5.status_code == 401,
            f"status={r5.status_code}",
        )
    finally:
        _restore(client_f5)

    # ── F6: ENABLE_AUTH=false → AnonymousUser(is_superadmin=True) → 200 ───────
    client_f6 = _build_mcp_client(enable_auth=False, server_list=SAMPLE_SERVERS)
    try:
        with patch("backend.api.deps.settings") as ms:
            ms.enable_auth = False
            r6 = client_f6.get("/api/v1/mcp/servers")
        body6 = r6.json()
        check(
            "F6: ENABLE_AUTH=false (AnonymousUser) → 200, data is list",
            r6.status_code == 200 and isinstance(body6.get("data"), list),
            f"status={r6.status_code}, data_type={type(body6.get('data')).__name__}",
        )
    finally:
        _restore(client_f6)


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 65)
    print("test_mcp_auth_e2e.py — MCP API 鉴权 + 前端白屏修复 E2E 测试")
    print("=" * 65)

    test_section_a()
    test_section_b()
    test_section_c()
    test_section_d()
    test_section_e()
    test_section_f()

    total = len(results)
    passed = sum(1 for _, ok in results if ok)
    failed = total - passed

    print("\n" + "=" * 65)
    print(f"Results: {passed}/{total} passed, {failed} failed")
    if failed:
        print("\nFailed checks:")
        for name, ok in results:
            if not ok:
                print(f"  {FAIL} {name}")
    print("=" * 65)
    sys.exit(0 if failed == 0 else 1)
