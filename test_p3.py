#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_p3.py — Phase 3: Dual ClickHouse + Agent MCP Binding

Section A — Settings: get_clickhouse_config() + has_readonly_credentials()
Section B — FilteredMCPManager: server filtering, call_tool auth
Section C — AgentMCPBinder: config loading, agent→server binding
Section D — MCPServerManager: dual server registration logic
Section E — orchestrator_v2 integration: _build_agent gets FilteredMCPManager

运行：/d/ProgramData/Anaconda3/envs/dataagent/python.exe test_p3.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

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


# ──────────────────────────────────────────────────────────
# Section A: Settings
# ──────────────────────────────────────────────────────────


def test_section_a():
    print("\n=== Section A: Settings dual config ===")
    from unittest.mock import patch

    from backend.config.settings import Settings

    base_env = {
        # IDN admin
        "CLICKHOUSE_IDN_HOST": "ch-idn.example.com",
        "CLICKHOUSE_IDN_PORT": "9000",
        "CLICKHOUSE_IDN_HTTP_PORT": "8123",
        "CLICKHOUSE_IDN_DATABASE": "mydb",
        "CLICKHOUSE_IDN_USER": "admin_user",
        "CLICKHOUSE_IDN_PASSWORD": "admin_pass",
        # IDN readonly (independent host)
        "CLICKHOUSE_IDN_READONLY_HOST": "ch-idn-ro.example.com",
        "CLICKHOUSE_IDN_READONLY_PORT": "9000",
        "CLICKHOUSE_IDN_READONLY_DATABASE": "mydb",
        "CLICKHOUSE_IDN_READONLY_USER": "ro_user",
        "CLICKHOUSE_IDN_READONLY_PASSWORD": "ro_pass",
        # SG admin only (no readonly user)
        "CLICKHOUSE_SG_HOST": "ch-sg.example.com",
        "CLICKHOUSE_SG_USER": "admin_sg",
        "CLICKHOUSE_SG_PASSWORD": "pass_sg",
        "CLICKHOUSE_SG_READONLY_USER": "",
        # MX: no host configured
        "CLICKHOUSE_MX_HOST": "",
        "CLICKHOUSE_MX_READONLY_USER": "",
    }

    with patch.dict(os.environ, base_env, clear=False):
        s = Settings()

        # A1: admin host
        cfg_admin = s.get_clickhouse_config("idn", level="admin")
        check("A1: admin host correct", cfg_admin["host"] == "ch-idn.example.com")

        # A2: admin level marker
        check("A2: admin level marker", cfg_admin.get("level") == "admin")

        # A3: readonly uses RO host
        cfg_ro = s.get_clickhouse_config("idn", level="readonly")
        check("A3: readonly host correct", cfg_ro["host"] == "ch-idn-ro.example.com")

        # A4: readonly level marker
        check("A4: readonly level marker", cfg_ro.get("level") == "readonly")

        # A5: readonly user
        check("A5: readonly user correct", cfg_ro["user"] == "ro_user")

        # A6: has_readonly_credentials True for IDN
        check("A6: has_readonly_credentials(idn) is True", s.has_readonly_credentials("idn"))

        # A7: has_readonly_credentials False for SG (user is empty)
        check("A7: has_readonly_credentials(sg) is False", not s.has_readonly_credentials("sg"))

        # A8: has_readonly_credentials False for MX
        check("A8: has_readonly_credentials(mx) is False", not s.has_readonly_credentials("mx"))

        # A9: readonly port fallback — when READONLY_PORT is 0, inherits admin port
        env_no_port = {**base_env, "CLICKHOUSE_IDN_READONLY_PORT": "0"}
        with patch.dict(os.environ, env_no_port, clear=False):
            s2 = Settings()
            cfg_ro2 = s2.get_clickhouse_config("idn", level="readonly")
            check(
                "A9: readonly port fallback to admin port when 0",
                cfg_ro2["port"] == 9000,
                f"got port={cfg_ro2['port']}",
            )

        # A10: readonly database fallback to admin database when empty
        env_no_db = {**base_env, "CLICKHOUSE_IDN_READONLY_DATABASE": ""}
        with patch.dict(os.environ, env_no_db, clear=False):
            s3 = Settings()
            cfg_ro3 = s3.get_clickhouse_config("idn", level="readonly")
            check(
                "A10: readonly database fallback to admin database",
                cfg_ro3["database"] == "mydb",
                f"got database={cfg_ro3['database']}",
            )


# ──────────────────────────────────────────────────────────
# Section B: FilteredMCPManager
# ──────────────────────────────────────────────────────────


def test_section_b():
    print("\n=== Section B: FilteredMCPManager ===")
    from unittest.mock import AsyncMock, MagicMock

    from backend.core.agent_mcp_binder import FilteredMCPManager

    # Build a mock MCPServerManager
    mock_base = MagicMock()
    mock_base.servers = {
        "clickhouse-idn": MagicMock(tools=[], resources=[], prompts=[], version="1.0"),
        "clickhouse-idn-ro": MagicMock(tools=[], resources=[], prompts=[], version="1.0"),
        "filesystem": MagicMock(tools=[], resources=[], prompts=[], version="1.0"),
        "clickhouse-sg": MagicMock(tools=[], resources=[], prompts=[], version="1.0"),
    }
    mock_base.server_configs = {
        "clickhouse-idn": {"type": "clickhouse", "env": "idn", "level": "admin"},
        "clickhouse-idn-ro": {"type": "clickhouse", "env": "idn", "level": "readonly"},
        "filesystem": {"type": "filesystem"},
        "clickhouse-sg": {"type": "clickhouse", "env": "sg", "level": "admin"},
    }
    mock_base.list_servers = lambda: [
        {"name": n, "type": "t"} for n in mock_base.servers
    ]
    mock_base.get_server = lambda name: mock_base.servers.get(name)
    mock_base.call_tool = AsyncMock(return_value={"success": True, "data": "ok"})
    mock_base.get_all_tools = MagicMock(
        return_value=[
            {"name": "t1", "server": "clickhouse-idn"},
            {"name": "t2", "server": "clickhouse-idn-ro"},
            {"name": "t3", "server": "filesystem"},
        ]
    )
    mock_base.get_all_resources = MagicMock(return_value=[])

    allowed = frozenset({"clickhouse-idn-ro", "filesystem"})
    fm = FilteredMCPManager(base=mock_base, allowed_servers=allowed)

    # B1: .servers only contains allowed servers
    check("B1: .servers filtered correctly", set(fm.servers.keys()) == allowed)

    # B2: excluded server not in .servers
    check("B2: clickhouse-idn excluded from .servers", "clickhouse-idn" not in fm.servers)

    # B3: .server_configs filtered
    check("B3: .server_configs filtered", set(fm.server_configs.keys()) == allowed)

    # B4: list_servers returns only allowed
    names = {s["name"] for s in fm.list_servers()}
    check("B4: list_servers filtered", names == allowed)

    # B5: get_server allowed returns server
    check("B5: get_server(allowed) returns server", fm.get_server("clickhouse-idn-ro") is not None)

    # B6: get_server blocked returns None
    check("B6: get_server(blocked) returns None", fm.get_server("clickhouse-idn") is None)

    # B7: get_all_tools filtered
    tools = fm.get_all_tools()
    tool_servers = {t["server"] for t in tools}
    check("B7: get_all_tools servers subset of allowed", tool_servers.issubset(allowed))

    # B8: call_tool to allowed server succeeds
    async def _check_allowed():
        result = await fm.call_tool("filesystem", "list", {})
        return result is not None and result.get("success") is True

    ok = asyncio.run(_check_allowed())
    check("B8: call_tool to allowed server delegates to base", ok)

    # B9: call_tool to blocked server returns error dict
    async def _check_blocked():
        result = await fm.call_tool("clickhouse-idn", "query", {"sql": "SELECT 1"})
        return result is not None and result.get("success") is False

    blocked = asyncio.run(_check_blocked())
    check("B9: call_tool to blocked server returns success=False", blocked)

    # B10: base.call_tool was called exactly once (only for B8)
    check(
        "B10: base.call_tool called only once (blocked call did not reach base)",
        mock_base.call_tool.call_count == 1,
        f"actual count={mock_base.call_tool.call_count}",
    )


# ──────────────────────────────────────────────────────────
# Section C: AgentMCPBinder
# ──────────────────────────────────────────────────────────


def test_section_c():
    print("\n=== Section C: AgentMCPBinder ===")
    import tempfile

    import yaml
    from unittest.mock import MagicMock

    from backend.core.agent_mcp_binder import AgentMCPBinder, FilteredMCPManager

    # Write a temp config file
    cfg = {
        "version": "1.0",
        "agents": {
            "etl_engineer": {"clickhouse_connection": "admin", "clickhouse_envs": ["idn"]},
            "analyst": {"clickhouse_connection": "readonly", "clickhouse_envs": ["idn"]},
            "general": {"clickhouse_connection": "readonly", "clickhouse_envs": ["idn"]},
        },
    }
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".yaml", delete=False, encoding="utf-8"
    ) as f:
        yaml.dump(cfg, f)
        tmp_path = f.name

    # Mock mcp_manager with both admin + ro servers
    def _make_mgr(server_names):
        m = MagicMock()
        m.servers = {k: MagicMock() for k in server_names}
        m.server_configs = {k: {} for k in server_names}
        m.list_servers = lambda: [{"name": k, "type": "t"} for k in server_names]
        m.get_server = lambda name: m.servers.get(name)
        m.get_all_tools = MagicMock(return_value=[])
        m.get_all_resources = MagicMock(return_value=[])
        return m

    mock_mgr = _make_mgr(["clickhouse-idn", "clickhouse-idn-ro", "filesystem", "lark"])

    binder = AgentMCPBinder(config_path=tmp_path)

    # C1: config loaded — 3 agent types
    check("C1: binder loaded 3 agent types", len(binder._config) == 3)

    # C2: etl_engineer gets FilesystemPermissionProxy (wraps FilteredMCPManager when filesystem is present)
    from backend.core.filesystem_permission_proxy import FilesystemPermissionProxy as _FsPP
    etl_fm = binder.get_filtered_manager("etl_engineer", mock_mgr)
    check("C2: etl_engineer → FilesystemPermissionProxy or FilteredMCPManager",
          isinstance(etl_fm, (_FsPP, FilteredMCPManager)))

    # C3: ETL gets admin server (not ro)
    check("C3: ETL allowed: clickhouse-idn", "clickhouse-idn" in etl_fm._allowed)
    check("C3b: ETL not allowed: clickhouse-idn-ro", "clickhouse-idn-ro" not in etl_fm._allowed)

    # C4: Analyst gets readonly server
    analyst_fm = binder.get_filtered_manager("analyst", mock_mgr)
    check("C4: analyst allowed: clickhouse-idn-ro", "clickhouse-idn-ro" in analyst_fm._allowed)
    check("C4b: analyst not allowed: clickhouse-idn", "clickhouse-idn" not in analyst_fm._allowed)

    # C5: General gets readonly server
    general_fm = binder.get_filtered_manager("general", mock_mgr)
    check("C5: general allowed: clickhouse-idn-ro", "clickhouse-idn-ro" in general_fm._allowed)

    # C6: All agents include filesystem
    check("C6: ETL includes filesystem", "filesystem" in etl_fm._allowed)
    check("C6b: analyst includes filesystem", "filesystem" in analyst_fm._allowed)
    check("C6c: general includes filesystem", "filesystem" in general_fm._allowed)

    # C7: All agents include lark
    check(
        "C7: all agents include lark",
        all("lark" in fm._allowed for fm in [etl_fm, analyst_fm, general_fm]),
    )

    # C8: Fallback — ro server not registered → fallback to admin
    mock_mgr_no_ro = _make_mgr(["clickhouse-idn", "filesystem"])
    analyst_fallback = binder.get_filtered_manager("analyst", mock_mgr_no_ro)
    check(
        "C8: analyst fallback to admin when ro not available",
        "clickhouse-idn" in analyst_fallback._allowed,
    )

    # C9: Unknown agent type — defaults to readonly + all envs (not zero access)
    # binder defaults: connection_level=readonly, envs=all → gets ro servers (or fallback)
    unknown_fm = binder.get_filtered_manager("unknown_agent", mock_mgr)
    check("C9: unknown agent defaults to readonly (gets idn-ro)", "clickhouse-idn-ro" in unknown_fm._allowed)
    check("C9b: unknown agent still gets filesystem", "filesystem" in unknown_fm._allowed)

    os.unlink(tmp_path)


# ──────────────────────────────────────────────────────────
# Section D: MCPServerManager dual registration
# ──────────────────────────────────────────────────────────


def test_section_d():
    print("\n=== Section D: MCPServerManager dual registration ===")
    from unittest.mock import AsyncMock, MagicMock, patch

    # Mock settings
    mock_settings = MagicMock()
    mock_settings.enable_mcp_clickhouse = True
    mock_settings.enable_mcp_mysql = False
    mock_settings.enable_mcp_filesystem = False
    mock_settings.enable_mcp_lark = False

    _configs = {
        ("idn", "admin"): {"host": "ch-idn.example.com", "level": "admin"},
        ("sg", "admin"): {"host": "ch-sg.example.com", "level": "admin"},
        ("mx", "admin"): {"host": "", "level": "admin"},  # no host
    }

    def mock_get_config(env, level="admin"):
        return _configs.get((env, level), {"host": "", "level": level})

    mock_settings.get_clickhouse_config = mock_get_config

    def mock_has_readonly(env):
        return env == "idn"  # only IDN has readonly creds

    mock_settings.has_readonly_credentials = mock_has_readonly
    # get_all_clickhouse_envs — returns idn/sg/mx for this test
    mock_settings.get_all_clickhouse_envs = lambda: ["idn", "sg", "mx"]

    # Each ClickHouseMCPServer instantiation returns a fresh MagicMock
    def make_ch_server(env="idn", level="admin"):
        inst = MagicMock()
        inst.initialize = AsyncMock()
        inst.tools = []
        inst.resources = []
        inst.prompts = []
        inst.version = "1.0"
        return inst

    with patch("backend.mcp.manager.settings", mock_settings), patch(
        "backend.mcp.manager.ClickHouseMCPServer", side_effect=make_ch_server
    ):
        from backend.mcp.manager import MCPServerManager

        manager = MCPServerManager()

        async def run():
            await manager.initialize_all()

        asyncio.run(run())

    # D1: clickhouse-idn registered (IDN has host)
    check("D1: clickhouse-idn registered", "clickhouse-idn" in manager.servers)

    # D2: clickhouse-idn-ro registered (IDN has readonly creds)
    check("D2: clickhouse-idn-ro registered", "clickhouse-idn-ro" in manager.servers)

    # D3: clickhouse-sg registered (SG has host)
    check("D3: clickhouse-sg registered", "clickhouse-sg" in manager.servers)

    # D4: clickhouse-sg-ro NOT registered (SG has no readonly user)
    check("D4: clickhouse-sg-ro NOT registered", "clickhouse-sg-ro" not in manager.servers)

    # D5: clickhouse-mx NOT registered (MX has no host)
    check("D5: clickhouse-mx NOT registered", "clickhouse-mx" not in manager.servers)

    # D6: clickhouse-mx-ro NOT registered
    check("D6: clickhouse-mx-ro NOT registered", "clickhouse-mx-ro" not in manager.servers)

    # D7: total server count = 3 (idn, idn-ro, sg)
    check(
        "D7: total 3 servers registered",
        len(manager.servers) == 3,
        f"actual={list(manager.servers.keys())}",
    )

    # D8: server_configs includes level=admin for clickhouse-idn
    idn_cfg = manager.server_configs.get("clickhouse-idn", {})
    check("D8: clickhouse-idn config level=admin", idn_cfg.get("level") == "admin")

    # D9: server_configs level=readonly for clickhouse-idn-ro
    idn_ro_cfg = manager.server_configs.get("clickhouse-idn-ro", {})
    check("D9: clickhouse-idn-ro config level=readonly", idn_ro_cfg.get("level") == "readonly")


# ──────────────────────────────────────────────────────────
# Section E: orchestrator_v2 integration
# ──────────────────────────────────────────────────────────


def test_section_e():
    print("\n=== Section E: orchestrator_v2 integration ===")
    from unittest.mock import MagicMock

    from backend.agents.agentic_loop import AgenticLoop
    from backend.agents.analyst_agent import DataAnalystAgent
    from backend.agents.etl_agent import ETLEngineerAgent
    from backend.agents.orchestrator_v2 import AgentOrchestrator
    from backend.core.agent_mcp_binder import AgentMCPBinder, FilteredMCPManager

    mock_llm = MagicMock()
    mock_mgr = MagicMock()
    mock_mgr.servers = {
        "clickhouse-idn": MagicMock(),
        "clickhouse-idn-ro": MagicMock(),
        "filesystem": MagicMock(),
    }
    mock_mgr.server_configs = {k: {} for k in mock_mgr.servers}
    mock_mgr.list_servers = lambda: [{"name": k, "type": "t"} for k in mock_mgr.servers]
    mock_mgr.get_server = lambda name: mock_mgr.servers.get(name)
    mock_mgr.get_all_tools = MagicMock(return_value=[])
    mock_mgr.get_all_resources = MagicMock(return_value=[])

    # AgentOrchestrator will load from the real .claude/agent_config.yaml
    orch = AgentOrchestrator(llm_adapter=mock_llm, mcp_manager=mock_mgr)

    # E1: orchestrator has _binder attribute
    check("E1: orchestrator has _binder", hasattr(orch, "_binder"))

    # E2: _build_agent("etl_engineer") is ETLEngineerAgent
    etl_agent = orch._build_agent("etl_engineer")
    check("E2: _build_agent(etl_engineer) is ETLEngineerAgent", isinstance(etl_agent, ETLEngineerAgent))

    # E3: etl_agent.mcp_manager is FilesystemPermissionProxy (wraps FilteredMCPManager)
    from backend.core.filesystem_permission_proxy import FilesystemPermissionProxy as _FsProxy
    check("E3: ETL mcp_manager is FilesystemPermissionProxy or FilteredMCPManager",
          isinstance(etl_agent.mcp_manager, (_FsProxy, FilteredMCPManager)))

    # E4: ETL allowed clickhouse-idn (admin)
    check("E4: ETL allowed: clickhouse-idn", "clickhouse-idn" in etl_agent.mcp_manager._allowed)
    check("E4b: ETL not allowed: clickhouse-idn-ro", "clickhouse-idn-ro" not in etl_agent.mcp_manager._allowed)

    # E5: _build_agent("analyst") is DataAnalystAgent
    analyst_agent = orch._build_agent("analyst")
    check("E5: _build_agent(analyst) is DataAnalystAgent", isinstance(analyst_agent, DataAnalystAgent))

    # E6: analyst.mcp_manager is FilesystemPermissionProxy or FilteredMCPManager (before ReadOnlyMCPProxy wraps it)
    check("E6: analyst mcp_manager is FilesystemPermissionProxy or FilteredMCPManager",
          isinstance(analyst_agent.mcp_manager, (_FsProxy, FilteredMCPManager)))

    # E7: analyst allowed clickhouse-idn-ro (readonly)
    check("E7: analyst allowed: clickhouse-idn-ro", "clickhouse-idn-ro" in analyst_agent.mcp_manager._allowed)
    check("E7b: analyst not allowed: clickhouse-idn", "clickhouse-idn" not in analyst_agent.mcp_manager._allowed)

    # E8: _build_agent("general") is AgenticLoop
    general_agent = orch._build_agent("general")
    check("E8: _build_agent(general) is AgenticLoop", isinstance(general_agent, AgenticLoop))

    # E9: general mcp_manager is FilesystemPermissionProxy (wrapping FilteredMCPManager)
    #     because filesystem is now included for all agents with directory-level write control
    from backend.core.filesystem_permission_proxy import FilesystemPermissionProxy
    check("E9: general mcp_manager is FilesystemPermissionProxy or FilteredMCPManager",
          isinstance(general_agent.mcp_manager, (FilesystemPermissionProxy, FilteredMCPManager)))

    # E10: filesystem now included for all agents; FilesystemPermissionProxy is the security boundary
    # (replaces excluded_non_ch_servers: [filesystem])
    check("E10: ETL includes filesystem (FilesystemPermissionProxy is the security boundary)", "filesystem" in etl_agent.mcp_manager._allowed)
    check("E10b: analyst includes filesystem (FilesystemPermissionProxy guards writes)", "filesystem" in analyst_agent.mcp_manager._allowed)
    check("E10c: general includes filesystem", "filesystem" in general_agent.mcp_manager._allowed)

    # E11: per-agent max_iterations from agent_config.yaml
    binder = AgentMCPBinder()
    check("E11a: analyst max_iterations >= 30", binder.get_max_iterations("analyst") >= 30)
    check("E11b: etl_engineer max_iterations == 15", binder.get_max_iterations("etl_engineer") == 15)
    check("E11c: general max_iterations >= 20", binder.get_max_iterations("general") >= 20)
    # 未知 agent 类型使用内置默认值 20
    check("E11d: unknown agent_type fallback == 20", binder.get_max_iterations("unknown_agent") == 20)


# ──────────────────────────────────────────────────────────
# Section F: Dynamic env discovery
# ──────────────────────────────────────────────────────────


def test_section_f():
    print("\n=== Section F: Dynamic env discovery ===")
    import tempfile
    from unittest.mock import AsyncMock, MagicMock, patch

    import yaml

    from backend.config.settings import Settings
    from backend.core.agent_mcp_binder import (
        AgentMCPBinder,
        FilteredMCPManager,
        _extract_envs_from_manager,
    )

    # ── F1: model_fields scan picks up idn / sg / mx ──
    base_env = {
        "CLICKHOUSE_IDN_HOST": "idn.example.com",
        "CLICKHOUSE_SG_HOST": "sg.example.com",
        "CLICKHOUSE_MX_HOST": "",
    }
    with patch.dict(os.environ, base_env, clear=False):
        s = Settings()
        envs = s.get_all_clickhouse_envs()
    check("F1: idn in get_all_clickhouse_envs", "idn" in envs)
    check("F1b: sg in get_all_clickhouse_envs", "sg" in envs)
    check("F1c: mx in get_all_clickhouse_envs", "mx" in envs)

    # ── F2: new JP env added via os.environ (no pydantic field) ──
    jp_env = {
        "CLICKHOUSE_JP_HOST": "jp.example.com",
        "CLICKHOUSE_JP_PORT": "9000",
        "CLICKHOUSE_JP_USER": "jp_user",
        "CLICKHOUSE_JP_PASSWORD": "jp_pass",
    }
    with patch.dict(os.environ, jp_env, clear=False):
        s2 = Settings()
        envs2 = s2.get_all_clickhouse_envs()
    check("F2: jp appears via os.environ scan", "jp" in envs2, f"got envs={envs2}")

    # ── F3: get_clickhouse_config("jp") works via raw env vars ──
    with patch.dict(os.environ, jp_env, clear=False):
        s3 = Settings()
        cfg = s3.get_clickhouse_config("jp", level="admin")
    check("F3: jp host from env", cfg["host"] == "jp.example.com", f"got host={cfg['host']}")
    check("F3b: jp level=admin", cfg["level"] == "admin")
    check("F3c: jp port defaults to 9000", cfg["port"] == 9000)

    # ── F4: has_readonly_credentials for new env ──
    jp_ro_env = {**jp_env, "CLICKHOUSE_JP_READONLY_USER": "jp_ro_user"}
    with patch.dict(os.environ, jp_ro_env, clear=False):
        s4 = Settings()
        has_ro = s4.has_readonly_credentials("jp")
    check("F4: has_readonly_credentials(jp) True when READONLY_USER set", has_ro)

    without_ro = {**jp_env}
    without_ro.pop("CLICKHOUSE_JP_READONLY_USER", None)
    # ensure key absent
    saved = os.environ.pop("CLICKHOUSE_JP_READONLY_USER", None)
    try:
        with patch.dict(os.environ, without_ro, clear=False):
            s5 = Settings()
            has_ro_false = s5.has_readonly_credentials("jp")
        check("F4b: has_readonly_credentials(jp) False when no READONLY_USER", not has_ro_false)
    finally:
        if saved is not None:
            os.environ["CLICKHOUSE_JP_READONLY_USER"] = saved

    # ── F5: initialize_all registers clickhouse-jp when JP host set ──
    mock_settings = MagicMock()
    mock_settings.enable_mcp_clickhouse = True
    mock_settings.enable_mcp_mysql = False
    mock_settings.enable_mcp_filesystem = False
    mock_settings.enable_mcp_lark = False
    mock_settings.get_all_clickhouse_envs = lambda: ["idn", "jp"]
    mock_settings.get_clickhouse_config = lambda env, level="admin": {
        "idn": {"host": "idn.example.com", "level": level},
        "jp": {"host": "jp.example.com", "level": level},
    }.get(env, {"host": "", "level": level})
    mock_settings.has_readonly_credentials = lambda env: False

    def make_ch_server(env="idn", level="admin"):
        inst = MagicMock()
        inst.initialize = AsyncMock()
        inst.tools = []
        inst.resources = []
        inst.prompts = []
        inst.version = "1.0"
        return inst

    with patch("backend.mcp.manager.settings", mock_settings), patch(
        "backend.mcp.manager.ClickHouseMCPServer", side_effect=make_ch_server
    ):
        from backend.mcp.manager import MCPServerManager

        mgr = MCPServerManager()
        asyncio.run(mgr.initialize_all())

    check("F5: clickhouse-idn registered", "clickhouse-idn" in mgr.servers)
    check("F5b: clickhouse-jp registered via dynamic env", "clickhouse-jp" in mgr.servers, f"servers={list(mgr.servers.keys())}")

    # ── F6: agent_config.yaml with clickhouse_envs: all → auto-discovers envs ──
    cfg_all = {
        "version": "1.0",
        "agents": {
            "analyst": {"clickhouse_connection": "readonly", "clickhouse_envs": "all"},
        },
    }
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".yaml", delete=False, encoding="utf-8"
    ) as f:
        yaml.dump(cfg_all, f)
        tmp_path = f.name

    mock_mgr_multi = MagicMock()
    mock_mgr_multi.servers = {
        "clickhouse-idn": MagicMock(),
        "clickhouse-idn-ro": MagicMock(),
        "clickhouse-sg": MagicMock(),
        "clickhouse-sg-ro": MagicMock(),
        "clickhouse-jp": MagicMock(),
        "filesystem": MagicMock(),
    }
    mock_mgr_multi.server_configs = {k: {} for k in mock_mgr_multi.servers}
    mock_mgr_multi.list_servers = lambda: [{"name": k, "type": "t"} for k in mock_mgr_multi.servers]
    mock_mgr_multi.get_server = lambda name: mock_mgr_multi.servers.get(name)
    mock_mgr_multi.get_all_tools = MagicMock(return_value=[])
    mock_mgr_multi.get_all_resources = MagicMock(return_value=[])

    binder_all = AgentMCPBinder(config_path=tmp_path)
    analyst_fm = binder_all.get_filtered_manager("analyst", mock_mgr_multi)

    # analyst with readonly → should get all *-ro servers (falling back to admin if no ro)
    check("F6: clickhouse-idn-ro in analyst (all envs)", "clickhouse-idn-ro" in analyst_fm._allowed)
    check("F6b: clickhouse-sg-ro in analyst (all envs)", "clickhouse-sg-ro" in analyst_fm._allowed)
    # jp has no -ro server, should fallback to clickhouse-jp
    check("F6c: clickhouse-jp fallback in analyst (all envs, no ro)", "clickhouse-jp" in analyst_fm._allowed)
    check("F6d: filesystem always included", "filesystem" in analyst_fm._allowed)
    os.unlink(tmp_path)

    # ── F7: _extract_envs_from_manager correctly extracts env names ──
    mock_mgr_extract = MagicMock()
    mock_mgr_extract.servers = {
        "clickhouse-idn": MagicMock(),
        "clickhouse-idn-ro": MagicMock(),
        "clickhouse-sg": MagicMock(),
        "clickhouse-jp": MagicMock(),
        "clickhouse-jp-ro": MagicMock(),
        "filesystem": MagicMock(),
        "lark": MagicMock(),
    }
    extracted = _extract_envs_from_manager(mock_mgr_extract)
    check("F7: idn extracted", "idn" in extracted)
    check("F7b: sg extracted", "sg" in extracted)
    check("F7c: jp extracted", "jp" in extracted)
    check("F7d: no duplicates (idn-ro deduped)", extracted.count("idn") == 1)
    check("F7e: non-CH servers not extracted", "filesystem" not in extracted and "lark" not in extracted)
    check("F7f: result is sorted", extracted == sorted(extracted))


# ──────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("test_p3.py - Dual ClickHouse + Agent MCP Binding")
    print("=" * 60)

    test_section_a()
    test_section_b()
    test_section_c()
    test_section_d()
    test_section_e()
    test_section_f()

    total = len(results)
    passed = sum(1 for _, ok in results if ok)
    failed = total - passed

    print("\n" + "=" * 60)
    print(f"Results: {passed}/{total} passed, {failed} failed")
    if failed:
        print("\nFailed checks:")
        for name, ok in results:
            if not ok:
                print(f"  {FAIL} {name}")
    print("=" * 60)

    sys.exit(0 if failed == 0 else 1)
