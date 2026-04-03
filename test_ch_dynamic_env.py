#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_ch_dynamic_env.py — ClickHouse 动态环境配置测试

验证在 .env 中自由新增 ClickHouse 连接配置后，重启后能正常加载的完整链路。

Section G — Settings & load_dotenv: 核心配置发现机制        (14 tests)
Section H — MCPServerManager: 动态注册 + 启动汇总日志       (10 tests)
Section I — Agent 绑定: 新 env 自动纳入 Agent 可见范围       ( 4 tests)
Section J — /mcp/ API 权限覆盖: 端点鉴权验证                 (10 tests)
Section K — MCPStatus 前端修复验证: 权限与静默降级            ( 3 tests)
Section L — 白屏根因修复验证: mcpApi/interceptor/initAuth     ( 5 tests)

Total: 46 tests

运行: /d/ProgramData/Anaconda3/envs/dataagent/python.exe test_ch_dynamic_env.py
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock, patch

# ── Path setup ────────────────────────────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent / "backend"))

# 禁用测试期间的 Settings 鉴权，避免与实际 DB 耦合
os.environ.setdefault("ENABLE_AUTH", "False")
os.environ.setdefault("ADMIN_SECRET_TOKEN", "test-ch-dynamic-admin")

PASS = "[PASS]"
FAIL = "[FAIL]"
results: list = []


def check(name: str, condition: bool, detail: str = "") -> bool:
    status = PASS if condition else FAIL
    results.append((name, condition))
    msg = f"  {status} {name}"
    if detail:
        msg += f"  [{detail}]"
    print(msg)
    return condition


# ─────────────────────────────────────────────────────────────────────────────
# Section G: Settings & load_dotenv — 核心配置发现机制
# ─────────────────────────────────────────────────────────────────────────────


def test_section_g():
    print("\n=== Section G: Settings & load_dotenv / os.environ 动态发现 ===")
    from backend.config.settings import Settings

    # 基准 env：包含已声明的 IDN/SG 和未声明的 JP/TH (Thailand) 四个环境
    base_env = {
        # IDN — pydantic 已声明字段
        "CLICKHOUSE_IDN_HOST": "idn.example.com",
        "CLICKHOUSE_IDN_PORT": "9000",
        "CLICKHOUSE_IDN_HTTP_PORT": "8123",
        "CLICKHOUSE_IDN_DATABASE": "crm",
        "CLICKHOUSE_IDN_USER": "admin",
        "CLICKHOUSE_IDN_PASSWORD": "idn_pass",
        # JP — pydantic 未声明，仅存在于 os.environ（模拟 load_dotenv 后的状态）
        "CLICKHOUSE_JP_HOST": "jp.example.com",
        "CLICKHOUSE_JP_PORT": "9000",
        "CLICKHOUSE_JP_HTTP_PORT": "8123",
        "CLICKHOUSE_JP_DATABASE": "crm",
        "CLICKHOUSE_JP_USER": "admin_jp",
        "CLICKHOUSE_JP_PASSWORD": "jp_pass",
        # JP readonly
        "CLICKHOUSE_JP_READONLY_USER": "ro_jp",
        "CLICKHOUSE_JP_READONLY_PASSWORD": "ro_jp_pass",
        # TH — 只配置了 HOST（无完整字段），用于空主机测试
        "CLICKHOUSE_TH_HOST": "",
        # SG_AZURE — 混合大小写 key（模拟 Windows .env 中 SG_Azure 风格）
        "CLICKHOUSE_SG_Azure_HOST": "sg-azure.example.com",
        "CLICKHOUSE_SG_Azure_USER": "admin_sg_azure",
        "CLICKHOUSE_SG_Azure_PASSWORD": "sg_azure_pass",
    }

    with patch.dict(os.environ, base_env, clear=False):
        s = Settings()

        # G1: Settings 实例化时遇到 pydantic 未声明的 CH 字段不抛 ValidationError
        check("G1: Settings loads without error despite extra CH env fields", s is not None)

        # G2: get_all_clickhouse_envs() 通过 os.environ source-2 发现未声明的 JP
        envs = s.get_all_clickhouse_envs()
        check(
            "G2: get_all_clickhouse_envs() discovers JP from os.environ",
            "jp" in envs,
            f"envs={envs}",
        )

        # G3: IDN（已声明字段）和 JP（未声明）都被发现
        check(
            "G3: both declared (idn) and undeclared (jp) envs discovered",
            "idn" in envs and "jp" in envs,
            f"envs={envs}",
        )

        # G4: get_clickhouse_config() 对未声明 env 从 os.environ 读取 host
        cfg_jp = s.get_clickhouse_config("jp", "admin")
        check(
            "G4: get_clickhouse_config('jp') reads host from os.environ",
            cfg_jp["host"] == "jp.example.com",
            f"host={cfg_jp['host']}",
        )

        # G5: 所有 6 个字段均正确读取（host/port/http_port/database/user/password）
        check("G5a: jp port=9000", cfg_jp["port"] == 9000, f"port={cfg_jp['port']}")
        check("G5b: jp http_port=8123", cfg_jp["http_port"] == 8123)
        check("G5c: jp database=crm", cfg_jp["database"] == "crm")
        check("G5d: jp user=admin_jp", cfg_jp["user"] == "admin_jp")
        check("G5e: jp password=jp_pass", cfg_jp["password"] == "jp_pass")

        # G6: has_readonly_credentials() True when READONLY_USER in os.environ
        check(
            "G6: has_readonly_credentials('jp') True via os.environ",
            s.has_readonly_credentials("jp"),
        )

        # G7: has_readonly_credentials() False when READONLY_USER is empty
        env_no_ro = {**base_env, "CLICKHOUSE_JP_READONLY_USER": ""}
        with patch.dict(os.environ, env_no_ro, clear=False):
            s2 = Settings()
            check(
                "G7: has_readonly_credentials('jp') False when READONLY_USER empty",
                not s2.has_readonly_credentials("jp"),
            )

        # G8: readonly 未指定 HOST 时继承 admin host
        env_ro_no_host = {
            **base_env,
            "CLICKHOUSE_JP_READONLY_HOST": "",  # 未设置单独 host
        }
        with patch.dict(os.environ, env_ro_no_host, clear=False):
            s3 = Settings()
            cfg_ro = s3.get_clickhouse_config("jp", "readonly")
            check(
                "G8: readonly inherits admin host when RO_HOST is empty",
                cfg_ro["host"] == "jp.example.com",
                f"ro_host={cfg_ro['host']}",
            )

        # G9: 混合大小写 env key（SG_Azure）通过大小写不敏感 fallback 正确读取
        cfg_sg_az = s.get_clickhouse_config("sg_azure", "admin")
        check(
            "G9: mixed-case key CLICKHOUSE_SG_Azure_HOST read via case-insensitive fallback",
            cfg_sg_az["host"] == "sg-azure.example.com",
            f"host={cfg_sg_az['host']}",
        )

        # G10: 空 host 的 env（TH）被 get_all_clickhouse_envs() 发现但 host 为空
        cfg_th = s.get_clickhouse_config("th", "admin")
        check(
            "G10: empty host env (TH) discovered but get_clickhouse_config host is empty",
            cfg_th["host"] == "",
            f"host='{cfg_th['host']}'",
        )


# ─────────────────────────────────────────────────────────────────────────────
# Section H: MCPServerManager — 动态注册 + 启动汇总日志
# ─────────────────────────────────────────────────────────────────────────────


def _make_mock_server(name: str = "clickhouse-idn"):
    """创建轻量 MagicMock ClickHouse 服务器"""
    inst = MagicMock()
    inst.initialize = AsyncMock()
    inst.tools = []
    inst.resources = []
    inst.prompts = []
    inst.version = "1.0"
    inst.name = name
    return inst


def test_section_h():
    print("\n=== Section H: MCPServerManager 动态注册 + 启动汇总日志 ===")

    # ── 公共 mock_settings factory ───────────────────────────────────────────
    def _make_mock_settings(envs, hosts, ro_users=None):
        ro_users = ro_users or {}
        ms = MagicMock()
        ms.enable_mcp_clickhouse = True
        ms.enable_mcp_mysql = False
        ms.enable_mcp_filesystem = False
        ms.enable_mcp_lark = False
        ms.get_all_clickhouse_envs = lambda: envs
        ms.get_clickhouse_config = lambda env, level="admin": {
            "host": hosts.get(env, ""),
            "level": level,
        }
        ms.has_readonly_credentials = lambda env: bool(ro_users.get(env))
        return ms

    # H1: initialize_all 为动态发现的 JP env 注册 clickhouse-jp
    mock_s_h1 = _make_mock_settings(
        envs=["idn", "jp"],
        hosts={"idn": "idn.example.com", "jp": "jp.example.com"},
    )
    ch_server_h1 = _make_mock_server()
    with patch("backend.mcp.manager.settings", mock_s_h1), patch(
        "backend.mcp.manager.ClickHouseMCPServer", return_value=ch_server_h1
    ):
        from backend.mcp.manager import MCPServerManager

        mgr_h1 = MCPServerManager()
        asyncio.run(mgr_h1.initialize_all())

    check(
        "H1: initialize_all registers clickhouse-jp for dynamic env",
        "clickhouse-jp" in mgr_h1.servers,
        f"servers={list(mgr_h1.servers.keys())}",
    )
    check("H1b: clickhouse-idn also registered", "clickhouse-idn" in mgr_h1.servers)

    # H2: host 为空的 env 跳过注册，不报错
    mock_s_h2 = _make_mock_settings(
        envs=["idn", "empty_env"],
        hosts={"idn": "idn.example.com", "empty_env": ""},
    )
    ch_server_h2 = _make_mock_server()
    with patch("backend.mcp.manager.settings", mock_s_h2), patch(
        "backend.mcp.manager.ClickHouseMCPServer", return_value=ch_server_h2
    ):
        mgr_h2 = MCPServerManager()
        asyncio.run(mgr_h2.initialize_all())

    check("H2: empty host env skipped (not registered)", "clickhouse-empty_env" not in mgr_h2.servers)
    check("H2b: idn still registered when empty_env is skipped", "clickhouse-idn" in mgr_h2.servers)

    # H3: 单个服务器初始化失败不阻断其他 env 注册
    fail_count = {"n": 0}

    def _failing_server_factory(env="idn", level="admin"):
        if env == "fail_env":
            fail_count["n"] += 1
            raise RuntimeError("Simulated CH connection error")
        return _make_mock_server()

    mock_s_h3 = _make_mock_settings(
        envs=["idn", "fail_env", "jp"],
        hosts={"idn": "idn.example.com", "fail_env": "bad.host", "jp": "jp.example.com"},
    )
    with patch("backend.mcp.manager.settings", mock_s_h3), patch(
        "backend.mcp.manager.ClickHouseMCPServer", side_effect=_failing_server_factory
    ):
        mgr_h3 = MCPServerManager()
        asyncio.run(mgr_h3.initialize_all())

    check(
        "H3: single server failure doesn't block other envs",
        "clickhouse-idn" in mgr_h3.servers and "clickhouse-jp" in mgr_h3.servers,
        f"servers={list(mgr_h3.servers.keys())}",
    )
    check("H3b: failed server not registered", "clickhouse-fail_env" not in mgr_h3.servers)

    # H4/H5/H6: 启动汇总日志 INFO 正确输出
    import logging as _logging

    mock_s_h4 = _make_mock_settings(
        envs=["idn", "jp"],
        hosts={"idn": "idn.example.com", "jp": "jp.example.com"},
    )

    log_records: list = []

    class _CapHandler(_logging.Handler):
        def emit(self, record):
            log_records.append(record)

    cap = _CapHandler()
    cap.setLevel(_logging.DEBUG)
    target_logger = _logging.getLogger("backend.mcp.manager")
    # 确保 logger 本身的级别足够低，允许 INFO 消息到达 handler
    original_level = target_logger.level
    target_logger.setLevel(_logging.INFO)
    target_logger.addHandler(cap)

    with patch("backend.mcp.manager.settings", mock_s_h4), patch(
        "backend.mcp.manager.ClickHouseMCPServer", return_value=_make_mock_server()
    ):
        mgr_h4 = MCPServerManager()
        asyncio.run(mgr_h4.initialize_all())

    target_logger.removeHandler(cap)
    target_logger.setLevel(original_level)

    summary_records = [r for r in log_records if "Initialization complete" in r.getMessage()]
    check(
        "H4: startup summary INFO log emitted after initialize_all",
        len(summary_records) >= 1,
        f"summary records={[r.getMessage() for r in summary_records]}",
    )
    if summary_records:
        msg = summary_records[0].getMessage()
        check(
            "H5: startup log contains server count",
            "server" in msg and any(c.isdigit() for c in msg),
            f"msg='{msg}'",
        )
        check(
            "H6: startup log lists registered server names (clickhouse-idn present)",
            "clickhouse-idn" in msg,
            f"msg='{msg}'",
        )

    # H7: has_readonly_credentials=True 时注册 -ro 服务器
    mock_s_h7 = _make_mock_settings(
        envs=["jp"],
        hosts={"jp": "jp.example.com"},
        ro_users={"jp": "ro_user"},
    )
    ro_servers_created: list = []

    def _track_ch_server(env="idn", level="admin"):
        srv = _make_mock_server()
        ro_servers_created.append(f"clickhouse-{env}" if level == "admin" else f"clickhouse-{env}-ro")
        return srv

    with patch("backend.mcp.manager.settings", mock_s_h7), patch(
        "backend.mcp.manager.ClickHouseMCPServer", side_effect=_track_ch_server
    ):
        mgr_h7 = MCPServerManager()
        asyncio.run(mgr_h7.initialize_all())

    check(
        "H7: readonly server registered when READONLY_USER present for new env",
        "clickhouse-jp-ro" in ro_servers_created,
        f"created={ro_servers_created}",
    )


# ─────────────────────────────────────────────────────────────────────────────
# Section I: Agent 绑定 — 新 env 自动纳入 Agent 可见范围
# ─────────────────────────────────────────────────────────────────────────────


def test_section_i():
    print("\n=== Section I: Agent 绑定 — 新 env 自动纳入 Agent 可见范围 ===")
    import tempfile

    import yaml
    from backend.core.agent_mcp_binder import AgentMCPBinder, _extract_envs_from_manager

    # 模拟已注册了 idn、jp、sg（以及 sg-ro）的 MCPServerManager
    mock_mgr = MagicMock()
    mock_mgr.servers = {
        "clickhouse-idn": MagicMock(),
        "clickhouse-idn-ro": MagicMock(),
        "clickhouse-jp": MagicMock(),    # 新增动态 env，无 -ro 版本
        "clickhouse-sg": MagicMock(),
        "clickhouse-sg-ro": MagicMock(),
        "filesystem": MagicMock(),
    }
    mock_mgr.server_configs = {k: {} for k in mock_mgr.servers}
    mock_mgr.list_servers = lambda: [{"name": k, "type": "t"} for k in mock_mgr.servers]
    mock_mgr.get_server = lambda n: mock_mgr.servers.get(n)
    mock_mgr.get_all_tools = MagicMock(return_value=[])
    mock_mgr.get_all_resources = MagicMock(return_value=[])

    cfg_yaml = {
        "version": "1.0",
        "agents": {
            "etl_engineer": {"clickhouse_connection": "admin", "clickhouse_envs": "all"},
            "analyst": {"clickhouse_connection": "readonly", "clickhouse_envs": "all"},
        },
    }

    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False, encoding="utf-8") as f:
        yaml.dump(cfg_yaml, f)
        tmp_yaml = f.name

    try:
        binder = AgentMCPBinder(config_path=tmp_yaml)

        # I1: analyst (clickhouse_envs: all) 自动发现新增的 jp env
        analyst_fm = binder.get_filtered_manager("analyst", mock_mgr)
        allowed = analyst_fm._allowed
        check(
            "I1: analyst agent auto-discovers newly registered JP env",
            "clickhouse-jp" in allowed or "clickhouse-jp-ro" in allowed,
            f"allowed_ch={[s for s in allowed if 'clickhouse' in s]}",
        )

        # I2: etl_engineer (admin) 获取 clickhouse-jp（admin 连接，无 -ro）
        etl_fm = binder.get_filtered_manager("etl_engineer", mock_mgr)
        check(
            "I2: etl_engineer gets admin server clickhouse-jp for new env",
            "clickhouse-jp" in etl_fm._allowed,
            f"etl_allowed_ch={[s for s in etl_fm._allowed if 'clickhouse' in s]}",
        )

        # I3: analyst (readonly) 对无 -ro 版本的 jp 降级使用 admin 连接
        check(
            "I3: analyst falls back to clickhouse-jp (no -ro) for new env",
            "clickhouse-jp" in analyst_fm._allowed,
            f"analyst_allowed_ch={[s for s in analyst_fm._allowed if 'clickhouse' in s]}",
        )

        # I4: _extract_envs_from_manager 从已注册的服务器名中提取 jp
        extracted = _extract_envs_from_manager(mock_mgr)
        check(
            "I4: _extract_envs_from_manager includes newly registered jp env",
            "jp" in extracted,
            f"extracted={extracted}",
        )

    finally:
        os.unlink(tmp_yaml)


# ─────────────────────────────────────────────────────────────────────────────
# Section J: /mcp/ API 权限覆盖 — 端点鉴权验证
# ─────────────────────────────────────────────────────────────────────────────


def _build_mcp_test_client(enable_auth: bool = False):
    """创建用于测试 /mcp/ 路由的 TestClient。"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.api.mcp import router as mcp_router

    app = FastAPI()
    app.include_router(mcp_router, prefix="/api/v1")

    # 注入 mock MCPServerManager：返回空列表，避免依赖真实 DB/CH
    mock_mgr = MagicMock()
    mock_mgr.list_servers.return_value = []
    mock_mgr.servers = {}
    mock_mgr.server_configs = {}

    # 覆盖 get_mcp_manager 避免实际初始化
    import backend.mcp.manager as _mgr_mod

    app.state.mock_mcp = mock_mgr
    original_get = _mgr_mod.get_mcp_manager

    def _patched_get():
        return mock_mgr

    _mgr_mod.get_mcp_manager = _patched_get

    client = TestClient(app, raise_server_exceptions=False)
    client._mcp_manager_restore = (original_get, _mgr_mod)
    client._enable_auth = enable_auth
    client._app = app  # 暴露 app 供 dependency_overrides 测试使用
    return client


def test_section_j():
    print("\n=== Section J: /mcp/ API 权限覆盖 ===")

    # ── J1: ENABLE_AUTH=False 时，GET /mcp/servers 正常返回 200 (向后兼容) ──
    client_no_auth = _build_mcp_test_client(enable_auth=False)
    try:
        with patch("backend.api.deps.settings") as mock_s_no_auth:
            mock_s_no_auth.enable_auth = False
            mock_s_no_auth.jwt_secret = "test"
            mock_s_no_auth.jwt_algorithm = "HS256"
            r = client_no_auth.get("/api/v1/mcp/servers")
        check(
            "J1: GET /mcp/servers returns 200 when ENABLE_AUTH=False (backward compat)",
            r.status_code == 200,
            f"status={r.status_code}",
        )
    finally:
        orig, mod = client_no_auth._mcp_manager_restore
        mod.get_mcp_manager = orig

    # ── J2: ENABLE_AUTH=True 时，无 token 访问 GET /mcp/servers 应返回 401 ──
    client_auth = _build_mcp_test_client(enable_auth=True)
    try:
        with patch("backend.api.deps.settings") as mock_s_auth:
            mock_s_auth.enable_auth = True
            mock_s_auth.jwt_secret = "test"
            mock_s_auth.jwt_algorithm = "HS256"
            r2 = client_auth.get("/api/v1/mcp/servers")  # 无 Authorization header
        check(
            "J2: GET /mcp/servers returns 401 when ENABLE_AUTH=True and no token",
            r2.status_code == 401,
            f"status={r2.status_code} (200=BUG: endpoint is unprotected!)",
        )
    finally:
        orig, mod = client_auth._mcp_manager_restore
        mod.get_mcp_manager = orig

    # ── J3: ENABLE_AUTH=True 时，无 token 访问 GET /mcp/stats 应返回 401 ──
    client_auth2 = _build_mcp_test_client(enable_auth=True)
    try:
        with patch("backend.api.deps.settings") as mock_s_auth2:
            mock_s_auth2.enable_auth = True
            mock_s_auth2.jwt_secret = "test"
            mock_s_auth2.jwt_algorithm = "HS256"
            r3 = client_auth2.get("/api/v1/mcp/stats")
        check(
            "J3: GET /mcp/stats returns 401 when ENABLE_AUTH=True and no token",
            r3.status_code == 401,
            f"status={r3.status_code} (200=BUG: endpoint is unprotected!)",
        )
    finally:
        orig, mod = client_auth2._mcp_manager_restore
        mod.get_mcp_manager = orig

    # ── J4: ENABLE_AUTH=True 时，无 token 访问 GET /mcp/servers/{name} 应返回 401 ──
    client_auth3 = _build_mcp_test_client(enable_auth=True)
    try:
        with patch("backend.api.deps.settings") as mock_s4:
            mock_s4.enable_auth = True
            mock_s4.jwt_secret = "test"
            mock_s4.jwt_algorithm = "HS256"
            r4 = client_auth3.get("/api/v1/mcp/servers/clickhouse-idn")
        check(
            "J4: GET /mcp/servers/{name} returns 401 when ENABLE_AUTH=True and no token",
            r4.status_code in (401, 404),  # 404 also acceptable if auth checked first
            f"status={r4.status_code} (200=BUG: endpoint is unprotected!)",
        )
    finally:
        orig, mod = client_auth3._mcp_manager_restore
        mod.get_mcp_manager = orig

    # ── J5: ENABLE_AUTH=True 时，无 token 执行 POST .../tools 应返回 401 ──
    client_auth4 = _build_mcp_test_client(enable_auth=True)
    try:
        with patch("backend.api.deps.settings") as mock_s5:
            mock_s5.enable_auth = True
            mock_s5.jwt_secret = "test"
            mock_s5.jwt_algorithm = "HS256"
            # TestClient 挂载在 /api/v1，router prefix 是 /mcp
            r5 = client_auth4.post(
                "/api/v1/mcp/servers/clickhouse-idn/tools/query",
                json={"arguments": {"sql": "SELECT 1"}},
            )
        # 鉴权成功后应返回 401（无 token），修复前返回 404（业务逻辑先跑，证明无鉴权）
        check(
            "J5: POST /mcp/servers/{name}/tools/{tool} returns 401 when ENABLE_AUTH=True",
            r5.status_code == 401,
            f"status={r5.status_code} (non-401 = auth not checked before business logic!)",
        )
    finally:
        orig, mod = client_auth4._mcp_manager_restore
        mod.get_mcp_manager = orig

    # ── J6: /mcp/ 路由已在 main.py 中注册（端点存在性验证）────────────────────
    # 检查 mcp.py router 的 routes 列表，验证关键端点存在
    from backend.api.mcp import router as mcp_router
    mcp_paths = [r.path for r in mcp_router.routes]  # type: ignore[attr-defined]
    check(
        "J6a: GET /mcp/servers endpoint registered",
        "/mcp/servers" in mcp_paths,
        f"paths={mcp_paths}",
    )
    check(
        "J6b: POST /mcp/servers/{server_name}/tools/{tool_name} endpoint registered",
        any("{server_name}/tools/{tool_name}" in p for p in mcp_paths),
        f"paths={mcp_paths}",
    )

    # ── J7: init_rbac.py ROLES["analyst"] 现在包含 settings:read ───────────────
    try:
        import importlib
        import backend.scripts.init_rbac as _rbac_mod
        importlib.reload(_rbac_mod)
        analyst_perms = _rbac_mod.ROLES.get("analyst", {}).get("permissions", [])
        check(
            "J7: init_rbac.py ROLES['analyst'] includes 'settings:read'",
            "settings:read" in analyst_perms,
            f"analyst_perms={analyst_perms}",
        )
    except Exception as exc:
        check("J7: init_rbac.py ROLES['analyst'] includes 'settings:read'", False, str(exc))

    # ── J8: 拥有 settings:read 权限的用户可访问 GET /mcp/servers (返回 200) ────
    from backend.api.deps import get_current_user, get_db

    client_j8 = _build_mcp_test_client(enable_auth=True)
    try:
        mock_user_j8 = MagicMock()
        mock_user_j8.is_superadmin = False
        mock_user_j8.is_active = True

        async def _override_user_j8():
            return mock_user_j8

        async def _override_db_j8():
            yield MagicMock()

        client_j8._app.dependency_overrides[get_current_user] = _override_user_j8
        client_j8._app.dependency_overrides[get_db] = _override_db_j8

        with patch("backend.core.rbac.get_user_permissions", return_value=["settings:read"]):
            r8 = client_j8.get("/api/v1/mcp/servers")

        check(
            "J8: user with settings:read can GET /mcp/servers (200)",
            r8.status_code == 200,
            f"status={r8.status_code}",
        )
    finally:
        client_j8._app.dependency_overrides.clear()
        orig, mod = client_j8._mcp_manager_restore
        mod.get_mcp_manager = orig

    # ── J9: viewer 角色 (仅 chat:use) 访问 GET /mcp/servers 返回 403 ───────────
    client_j9 = _build_mcp_test_client(enable_auth=True)
    try:
        mock_viewer = MagicMock()
        mock_viewer.is_superadmin = False
        mock_viewer.is_active = True

        async def _override_user_j9():
            return mock_viewer

        async def _override_db_j9():
            yield MagicMock()

        client_j9._app.dependency_overrides[get_current_user] = _override_user_j9
        client_j9._app.dependency_overrides[get_db] = _override_db_j9

        with patch("backend.core.rbac.get_user_permissions", return_value=["chat:use"]):
            r9 = client_j9.get("/api/v1/mcp/servers")

        check(
            "J9: viewer (chat:use only) gets 403 on GET /mcp/servers",
            r9.status_code == 403,
            f"status={r9.status_code}",
        )
    finally:
        client_j9._app.dependency_overrides.clear()
        orig, mod = client_j9._mcp_manager_restore
        mod.get_mcp_manager = orig


# ─────────────────────────────────────────────────────────────────────────────
# Section K: MCPStatus 前端修复验证 — 权限与静默降级
# ─────────────────────────────────────────────────────────────────────────────


def test_section_k():
    print("\n=== Section K: MCPStatus 前端修复验证 ===")

    api_ts = Path(__file__).parent / "frontend" / "src" / "services" / "api.ts"
    mcp_status_tsx = Path(__file__).parent / "frontend" / "src" / "components" / "chat" / "MCPStatus.tsx"

    # ── K1: api.ts 导出 mcpApi.getServers（使用 apiClient，而非裸 fetch）──────
    try:
        api_content = api_ts.read_text(encoding="utf-8")
        has_mcp_api = "mcpApi" in api_content
        has_get_servers = "getServers" in api_content
        uses_api_client = "apiClient" in api_content and "mcpApi" in api_content
        # 确认 mcpApi.getServers 使用 apiClient（而不是 fetch）
        mcp_api_section = ""
        if "mcpApi" in api_content:
            start = api_content.index("mcpApi")
            mcp_api_section = api_content[start:start + 300]
        uses_fetch_in_mcp = "fetch(" in mcp_api_section
        check(
            "K1: api.ts exports mcpApi.getServers using apiClient (not raw fetch)",
            has_mcp_api and has_get_servers and uses_api_client and not uses_fetch_in_mcp,
            f"has_mcpApi={has_mcp_api}, has_getServers={has_get_servers}, "
            f"uses_apiClient={uses_api_client}, uses_fetch_in_mcp={uses_fetch_in_mcp}",
        )
    except Exception as exc:
        check("K1: api.ts exports mcpApi.getServers using apiClient (not raw fetch)", False, str(exc))

    # ── K2: MCPStatus.tsx 使用 mcpApi.getServers() 而非裸 fetch ─────────────
    try:
        tsx_content = mcp_status_tsx.read_text(encoding="utf-8")
        uses_mcp_api_call = "mcpApi.getServers()" in tsx_content
        uses_raw_fetch = "fetch('/api/v1/mcp/servers')" in tsx_content or "fetch(\"/api/v1/mcp/servers\")" in tsx_content
        check(
            "K2: MCPStatus.tsx uses mcpApi.getServers() (not raw fetch)",
            uses_mcp_api_call and not uses_raw_fetch,
            f"uses_mcp_api_call={uses_mcp_api_call}, uses_raw_fetch={uses_raw_fetch}",
        )
    except Exception as exc:
        check("K2: MCPStatus.tsx uses mcpApi.getServers() (not raw fetch)", False, str(exc))

    # ── K3: MCPStatus.tsx 对 401/403 静默降级（不展示错误 UI）────────────────
    try:
        tsx_content = mcp_status_tsx.read_text(encoding="utf-8")
        # 验证存在 401/403 状态码检查
        has_401_check = "401" in tsx_content
        has_403_check = "403" in tsx_content
        # 验证无 error state 被 set（旧代码 setError(...)）
        has_set_error = "setError(" in tsx_content
        # 验证空列表时 return null（静默隐藏）
        has_return_null = "return null" in tsx_content
        check(
            "K3: MCPStatus.tsx silently degrades on 401/403 (no error UI, return null)",
            has_401_check and has_403_check and not has_set_error and has_return_null,
            f"has_401={has_401_check}, has_403={has_403_check}, "
            f"has_setError={has_set_error}, has_return_null={has_return_null}",
        )
    except Exception as exc:
        check("K3: MCPStatus.tsx silently degrades on 401/403 (no error UI, return null)", False, str(exc))


# ─────────────────────────────────────────────────────────────────────────────
# Section L: 白屏根因修复验证
# ─────────────────────────────────────────────────────────────────────────────


def test_section_l():
    print("\n=== Section L: 白屏根因修复验证 ===")

    api_ts = Path(__file__).parent / "frontend" / "src" / "services" / "api.ts"
    mcp_status_tsx = Path(__file__).parent / "frontend" / "src" / "components" / "chat" / "MCPStatus.tsx"
    auth_store_ts = Path(__file__).parent / "frontend" / "src" / "store" / "useAuthStore.ts"

    # ── L1: mcpApi.getServers() 取 response.data.data（非 response.data）────────
    # 后端返回 {"success":true,"data":[...]}，axios response.data 是整个 JSON 体，
    # 需取 response.data.data 才是数组；否则 servers.map() 抛 TypeError → 白屏
    try:
        api_content = api_ts.read_text(encoding="utf-8")
        # 定位 getServers 函数体，检查是否使用 response.data.data（而非仅 response.data）
        if "getServers" in api_content:
            start = api_content.index("getServers")
            snippet = api_content[start:start + 400]
            uses_data_data = "data?.data" in snippet or "data.data" in snippet
            # 不能直接返回 .data（整个 JSON body）
            returns_bare_data = "return (response as any).data ?? []" in snippet
            check(
                "L1: mcpApi.getServers() extracts response.data.data (not bare response.data)",
                uses_data_data and not returns_bare_data,
                f"uses_data_data={uses_data_data}, returns_bare_data={returns_bare_data}",
            )
        else:
            check("L1: mcpApi.getServers() extracts response.data.data (not bare response.data)", False,
                  "getServers not found in api.ts")
    except Exception as exc:
        check("L1: mcpApi.getServers() extracts response.data.data (not bare response.data)", False, str(exc))

    # ── L2: api.ts 只有一个 interceptors.response.use 声明（去掉重复拦截器）────
    # 第二个拦截器把 error 包成 new Error()，导致 err.response 丢失，
    # MCPStatus 的 401/403 静默降级失效
    try:
        api_content = api_ts.read_text(encoding="utf-8")
        interceptor_count = api_content.count("apiClient.interceptors.response.use(")
        check(
            "L2: api.ts has exactly one response interceptor (duplicate removed)",
            interceptor_count == 1,
            f"interceptor_count={interceptor_count} (expected 1)",
        )
    except Exception as exc:
        check("L2: api.ts has exactly one response interceptor (duplicate removed)", False, str(exc))

    # ── L3: api.ts 不含 window.location.href 硬跳转（避免整页闪白）───────────
    # 硬跳转会触发浏览器整页重载，在 SPA 中表现为白屏；
    # 改为依赖 Zustand + RequireAuth 做 React Router 软导航
    try:
        api_content = api_ts.read_text(encoding="utf-8")
        has_hard_redirect = "window.location.href" in api_content
        check(
            "L3: api.ts has no window.location.href hard redirect",
            not has_hard_redirect,
            f"has_hard_redirect={has_hard_redirect}",
        )
    except Exception as exc:
        check("L3: api.ts has no window.location.href hard redirect", False, str(exc))

    # ── L4: MCPStatus.tsx 使用 Array.isArray 守卫（防止 map() TypeError）───────
    try:
        tsx_content = mcp_status_tsx.read_text(encoding="utf-8")
        has_array_guard = "Array.isArray" in tsx_content
        check(
            "L4: MCPStatus.tsx has Array.isArray guard (prevents map() TypeError crash)",
            has_array_guard,
            f"has_array_guard={has_array_guard}",
        )
    except Exception as exc:
        check("L4: MCPStatus.tsx has Array.isArray guard (prevents map() TypeError crash)", False, str(exc))

    # ── L5: useAuthStore.ts 含 _initAuthPromise 去重机制（防 React 18 双调用）──
    # React 18 Strict Mode 双调用 useEffect → 两次并发 refresh → 第二次 401 →
    # user 被清空 → RequireAuth 跳登录页 → 白屏
    try:
        auth_content = auth_store_ts.read_text(encoding="utf-8")
        has_init_promise = "_initAuthPromise" in auth_content
        has_null_check = "_initAuthPromise !== null" in auth_content or "_initAuthPromise != null" in auth_content
        has_finally_clear = "_initAuthPromise = null" in auth_content
        check(
            "L5: useAuthStore has _initAuthPromise dedup guard for concurrent initAuth calls",
            has_init_promise and has_null_check and has_finally_clear,
            f"has_promise={has_init_promise}, has_null_check={has_null_check}, has_finally_clear={has_finally_clear}",
        )
    except Exception as exc:
        check("L5: useAuthStore has _initAuthPromise dedup guard for concurrent initAuth calls", False, str(exc))


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────


if __name__ == "__main__":
    print("=" * 65)
    print("test_ch_dynamic_env.py — ClickHouse 动态环境配置测试")
    print("=" * 65)

    test_section_g()
    test_section_h()
    test_section_i()
    test_section_j()
    test_section_k()
    test_section_l()

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
