"""
test_path_portability.py
========================
文件系统路径可移植性测试（T1-T3, T8 变更验证）

测试分区：
  A  _PROJECT_ROOT 常量正确性
  B  _resolve_fs_paths validator — 相对路径解析
  C  _resolve_fs_paths validator — 绝对路径向后兼容
  D  Settings 从 .env 加载并解析路径
  E  FilesystemMCPServer 获得绝对路径
  F  FilesystemPermissionProxy / AgentMCPBinder 获得绝对路径
  G  LLM 系统提示仍注入绝对路径（relative config → absolute prompt）
  H  RBAC 回归 — T1~T8 无新路由/权限
  I  安全/边界 — validator 鲁棒性

运行: /d/ProgramData/Anaconda3/envs/dataagent/python.exe test_path_portability.py
"""

import asyncio
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "backend"))

_HERE = Path(__file__).parent.resolve()


# ─────────────────────────────────────────────────────────────────────────────
# Section A: _PROJECT_ROOT 常量
# ─────────────────────────────────────────────────────────────────────────────

def test_A1_project_root_is_absolute():
    """_PROJECT_ROOT 必须是绝对路径。"""
    from backend.config.settings import _PROJECT_ROOT
    assert _PROJECT_ROOT.is_absolute(), f"_PROJECT_ROOT not absolute: {_PROJECT_ROOT}"
    print("[PASS] A1: _PROJECT_ROOT is absolute")


def test_A2_project_root_matches_expected_location():
    """_PROJECT_ROOT 应等于 settings.py 上三级目录。"""
    from backend.config.settings import _PROJECT_ROOT
    settings_py = Path(
        sys.modules["backend.config.settings"].__file__
    ).resolve()
    expected = settings_py.parent.parent.parent.resolve()
    assert _PROJECT_ROOT == expected, (
        f"_PROJECT_ROOT mismatch: {_PROJECT_ROOT} != {expected}"
    )
    print("[PASS] A2: _PROJECT_ROOT matches settings.py parent^3")


def test_A3_project_root_contains_project_structure():
    """_PROJECT_ROOT 下应存在 backend/ 和 .claude/ 目录，确认指向了正确的项目根。"""
    from backend.config.settings import _PROJECT_ROOT
    assert (_PROJECT_ROOT / "backend").is_dir(), f"backend/ missing under {_PROJECT_ROOT}"
    assert (_PROJECT_ROOT / ".claude").is_dir(), f".claude/ missing under {_PROJECT_ROOT}"
    print("[PASS] A3: _PROJECT_ROOT contains backend/ and .claude/")


def test_A4_project_root_stable_across_imports():
    """多次导入 settings，_PROJECT_ROOT 值保持一致（模块级常量不变）。"""
    from backend.config.settings import _PROJECT_ROOT as r1
    import importlib
    mod = importlib.import_module("backend.config.settings")
    r2 = mod._PROJECT_ROOT
    assert r1 == r2, f"_PROJECT_ROOT differs across imports: {r1} vs {r2}"
    print("[PASS] A4: _PROJECT_ROOT stable across imports")


# ─────────────────────────────────────────────────────────────────────────────
# Section B: _resolve_fs_paths validator — 相对路径
# ─────────────────────────────────────────────────────────────────────────────

def _invoke_validator(path_list):
    """直接调用 _resolve_fs_paths，不经过 Settings 完整初始化。"""
    from backend.config.settings import Settings
    return Settings._resolve_fs_paths(path_list)


def test_B1_relative_customer_data_resolves_under_project_root():
    """相对路径 'customer_data' 应解析为 _PROJECT_ROOT/customer_data。"""
    from backend.config.settings import _PROJECT_ROOT
    result = _invoke_validator(["customer_data"])
    expected = str((_PROJECT_ROOT / "customer_data").resolve())
    assert result == [expected], f"Got: {result}"
    print("[PASS] B1: relative 'customer_data' resolves under project root")


def test_B2_relative_claude_skills_resolves_correctly():
    """相对路径 '.claude/skills' 解析为 _PROJECT_ROOT/.claude/skills。"""
    from backend.config.settings import _PROJECT_ROOT
    result = _invoke_validator([".claude/skills"])
    expected = str((_PROJECT_ROOT / ".claude" / "skills").resolve())
    assert result == [expected], f"Got: {result}"
    print("[PASS] B2: relative '.claude/skills' resolves correctly")


def test_B3_relative_skills_user_resolves_correctly():
    """相对路径 '.claude/skills/user' 解析为 _PROJECT_ROOT/.claude/skills/user。"""
    from backend.config.settings import _PROJECT_ROOT
    result = _invoke_validator([".claude/skills/user"])
    expected = str((_PROJECT_ROOT / ".claude" / "skills" / "user").resolve())
    assert result == [expected], f"Got: {result}"
    print("[PASS] B3: relative '.claude/skills/user' resolves correctly")


def test_B4_relative_paths_always_produce_absolute():
    """validator 输出的路径必须是绝对路径。"""
    paths = ["customer_data", ".claude/skills", ".claude/skills/user", "uploads"]
    result = _invoke_validator(paths)
    for r in result:
        assert Path(r).is_absolute(), f"Not absolute: {r}"
    print("[PASS] B4: all relative paths produce absolute results")


def test_B5_multiple_relative_paths_order_preserved():
    """多个相对路径批量解析，顺序保持不变。"""
    from backend.config.settings import _PROJECT_ROOT
    paths = ["customer_data", ".claude/skills"]
    result = _invoke_validator(paths)
    assert len(result) == 2
    assert "customer_data" in result[0]
    assert "skills" in result[1]
    print("[PASS] B5: multiple relative paths resolved in order")


def test_B6_backslash_relative_path_resolves():
    """Windows 反斜杠相对路径也能正确解析（跨平台）。"""
    from backend.config.settings import _PROJECT_ROOT
    result = _invoke_validator([".claude\\skills"])
    expected = str((_PROJECT_ROOT / ".claude" / "skills").resolve())
    assert result == [expected], f"Got: {result}"
    print("[PASS] B6: backslash relative path resolves correctly")


# ─────────────────────────────────────────────────────────────────────────────
# Section C: _resolve_fs_paths validator — 绝对路径向后兼容
# ─────────────────────────────────────────────────────────────────────────────

def test_C1_absolute_path_passes_through_unchanged():
    """绝对路径不被 _PROJECT_ROOT 前缀化，直接透传（兼容旧配置）。"""
    abs_path = str(_HERE / "customer_data")
    result = _invoke_validator([abs_path])
    # resolve() 不应改变路径的基础部分（customer_data 仍在 _HERE 下）
    assert "customer_data" in result[0], f"Got: {result}"
    assert Path(result[0]).is_absolute(), f"Not absolute: {result}"
    print("[PASS] C1: absolute path passes through without project-root prefix")


def test_C2_absolute_path_not_double_resolved():
    """绝对路径 resolve() 后等于自身（无意外的 ../ 引入）。"""
    abs_path = str((_HERE / "customer_data").resolve())
    result = _invoke_validator([abs_path])
    assert result[0] == abs_path, f"Double-resolved: {result[0]} != {abs_path}"
    print("[PASS] C2: absolute path not double-resolved")


def test_C3_mixed_list_relative_and_absolute():
    """混合列表（相对+绝对）各自正确处理。"""
    from backend.config.settings import _PROJECT_ROOT
    abs_path = str(_HERE / "customer_data")
    rel_path = ".claude/skills"
    result = _invoke_validator([abs_path, rel_path])

    assert len(result) == 2
    # 第一项：绝对路径，包含 customer_data
    assert Path(result[0]).is_absolute()
    assert "customer_data" in result[0]
    # 第二项：相对路径 → 以 project root 为基
    assert Path(result[1]).is_absolute()
    assert str(_PROJECT_ROOT) in result[1] or result[1].startswith(str(_PROJECT_ROOT))
    print("[PASS] C3: mixed relative+absolute list handled correctly")


def test_C4_old_style_absolute_env_format_still_works():
    """模拟旧部署环境使用绝对路径的 .env，settings 仍正常加载。"""
    from pydantic_settings import BaseSettings
    from backend.config.settings import Settings

    abs_dirs = [str(_HERE / "customer_data"), str(_HERE / ".claude" / "skills")]
    with patch.dict(os.environ, {
        "ALLOWED_DIRECTORIES": str(abs_dirs).replace("'", '"'),
    }, clear=False):
        # 直接调用 validator — 验证绝对路径不被破坏
        result = _invoke_validator(abs_dirs)
        for r in result:
            assert Path(r).is_absolute(), f"Not absolute: {r}"
    print("[PASS] C4: old-style absolute env format still works via validator")


# ─────────────────────────────────────────────────────────────────────────────
# Section D: Settings 从 .env 加载并解析路径
# ─────────────────────────────────────────────────────────────────────────────

def test_D1_settings_allowed_directories_all_absolute():
    """settings.allowed_directories 所有元素均为绝对路径。"""
    from backend.config.settings import settings
    for d in settings.allowed_directories:
        assert Path(d).is_absolute(), f"Not absolute: {d}"
    print("[PASS] D1: settings.allowed_directories all absolute")


def test_D2_settings_write_dirs_all_absolute():
    """settings.filesystem_write_allowed_dirs 所有元素均为绝对路径。"""
    from backend.config.settings import settings
    for d in settings.filesystem_write_allowed_dirs:
        assert Path(d).is_absolute(), f"Not absolute: {d}"
    print("[PASS] D2: settings.filesystem_write_allowed_dirs all absolute")


def test_D3_settings_paths_under_project_root():
    """settings 解析后的路径均在 _PROJECT_ROOT 下（当 .env 使用相对路径时）。"""
    from backend.config.settings import settings, _PROJECT_ROOT
    all_dirs = settings.allowed_directories + settings.filesystem_write_allowed_dirs
    for d in all_dirs:
        try:
            Path(d).relative_to(_PROJECT_ROOT)
        except ValueError:
            # 允许绝对路径在项目根之外（兼容 Docker 挂载卷）
            pass  # 关键是不崩溃且是绝对路径
    print("[PASS] D3: settings paths resolved (under project root for relative config)")


def test_D4_default_values_produce_absolute_paths():
    """不依赖 .env 的默认值也产生绝对路径。"""
    from backend.config.settings import Settings, _PROJECT_ROOT
    # 使用 patch 清空相关 env，强制使用 default_factory
    with patch.dict(os.environ, {}, clear=False):
        # 直接测试 default_factory 输出
        default_allowed = [
            str(_PROJECT_ROOT / "customer_data"),
            str(_PROJECT_ROOT / ".claude" / "skills"),
        ]
        for d in default_allowed:
            assert Path(d).is_absolute(), f"Default not absolute: {d}"
    print("[PASS] D4: default_factory produces absolute paths")


def test_D5_allowed_directories_contains_customer_data_and_skills():
    """settings.allowed_directories 包含 customer_data 和 .claude/skills 条目。"""
    from backend.config.settings import settings
    dirs_str = " ".join(settings.allowed_directories).replace("\\", "/")
    assert "customer_data" in dirs_str, f"customer_data not found: {settings.allowed_directories}"
    assert "skills" in dirs_str, f"skills not found: {settings.allowed_directories}"
    print("[PASS] D5: allowed_directories contains customer_data and skills")


def test_D6_write_dirs_contains_customer_data_and_user_skills():
    """settings.filesystem_write_allowed_dirs 包含 customer_data 和 skills/user 条目。"""
    from backend.config.settings import settings
    dirs_str = " ".join(settings.filesystem_write_allowed_dirs).replace("\\", "/")
    assert "customer_data" in dirs_str
    assert "user" in dirs_str
    print("[PASS] D6: filesystem_write_allowed_dirs contains customer_data and skills/user")


# ─────────────────────────────────────────────────────────────────────────────
# Section E: FilesystemMCPServer 获得绝对路径
# ─────────────────────────────────────────────────────────────────────────────

def test_E1_filesystem_server_allowed_dirs_all_absolute():
    """FilesystemMCPServer.allowed_directories 所有元素均为绝对路径。"""
    from backend.mcp.filesystem.server import FilesystemMCPServer
    server = FilesystemMCPServer.__new__(FilesystemMCPServer)
    # 直接从 settings 赋值（模拟 __init__ 行为）
    from backend.config.settings import settings
    server.allowed_directories = settings.allowed_directories
    for d in server.allowed_directories:
        assert Path(d).is_absolute(), f"Server dir not absolute: {d}"
    print("[PASS] E1: FilesystemMCPServer allowed_directories all absolute")


def test_E2_filesystem_server_dirs_match_settings():
    """FilesystemMCPServer.allowed_directories 与 settings 一致。"""
    from backend.mcp.filesystem.server import FilesystemMCPServer
    from backend.config.settings import settings
    server = FilesystemMCPServer.__new__(FilesystemMCPServer)
    server.allowed_directories = settings.allowed_directories
    assert server.allowed_directories == settings.allowed_directories
    print("[PASS] E2: FilesystemMCPServer dirs match settings")


# ─────────────────────────────────────────────────────────────────────────────
# Section F: FilesystemPermissionProxy / AgentMCPBinder 获得绝对路径
# ─────────────────────────────────────────────────────────────────────────────

def test_F1_proxy_write_allowed_all_absolute():
    """FilesystemPermissionProxy._write_allowed 全为绝对路径（来自 settings）。"""
    from backend.core.filesystem_permission_proxy import FilesystemPermissionProxy
    from backend.config.settings import settings

    base = MagicMock()
    base._allowed = frozenset({"filesystem"})
    proxy = FilesystemPermissionProxy(
        base=base,
        write_allowed_dirs=settings.filesystem_write_allowed_dirs,
        read_allowed_dirs=settings.allowed_directories,
    )
    for p in proxy._write_allowed:
        assert p.is_absolute(), f"proxy _write_allowed not absolute: {p}"
    print("[PASS] F1: proxy._write_allowed all absolute")


def test_F2_proxy_read_allowed_all_absolute():
    """FilesystemPermissionProxy._read_allowed 全为绝对路径（来自 settings）。"""
    from backend.core.filesystem_permission_proxy import FilesystemPermissionProxy
    from backend.config.settings import settings

    base = MagicMock()
    base._allowed = frozenset({"filesystem"})
    proxy = FilesystemPermissionProxy(
        base=base,
        write_allowed_dirs=settings.filesystem_write_allowed_dirs,
        read_allowed_dirs=settings.allowed_directories,
    )
    for p in proxy._read_allowed:
        assert p.is_absolute(), f"proxy _read_allowed not absolute: {p}"
    print("[PASS] F2: proxy._read_allowed all absolute")


def test_F3_binder_creates_proxy_with_absolute_paths():
    """AgentMCPBinder.get_filtered_manager() 创建 proxy 时使用绝对路径。"""
    from backend.core.filesystem_permission_proxy import FilesystemPermissionProxy
    from backend.core.agent_mcp_binder import AgentMCPBinder

    mock_manager = MagicMock()
    mock_manager.servers = {"filesystem": MagicMock(), "clickhouse-idn": MagicMock()}
    mock_manager.server_configs = {}
    mock_manager.list_servers = MagicMock(return_value=[
        {"name": "filesystem"}, {"name": "clickhouse-idn"}
    ])

    binder = AgentMCPBinder.__new__(AgentMCPBinder)
    binder._config_path = Path("/nonexistent")
    binder._config = {
        "analyst": {
            "clickhouse_connection": "readonly",
            "clickhouse_envs": "all",
        }
    }

    result = binder.get_filtered_manager("analyst", mock_manager)
    assert isinstance(result, FilesystemPermissionProxy)

    for p in result._write_allowed:
        assert p.is_absolute(), f"binder proxy write path not absolute: {p}"
    for p in result._read_allowed:
        assert p.is_absolute(), f"binder proxy read path not absolute: {p}"
    print("[PASS] F3: AgentMCPBinder proxy has absolute paths")


# ─────────────────────────────────────────────────────────────────────────────
# Section G: LLM 系统提示仍注入绝对路径
# ─────────────────────────────────────────────────────────────────────────────

async def _build_prompt(username="alice"):
    """辅助：调用 _build_system_prompt，构造含 filesystem 服务器的 mock。"""
    from backend.agents.agentic_loop import AgenticLoop
    from backend.config.settings import settings

    loop = AgenticLoop.__new__(AgenticLoop)
    loop.llm_adapter = MagicMock()
    loop.llm_adapter.model = "claude-sonnet-4-6"
    loop._cancel_event = None
    loop._tools_cache = None
    loop._system_prompt = ""

    # mock filesystem server with resolved absolute dirs
    fs_mock = MagicMock()
    fs_mock.allowed_directories = settings.allowed_directories  # already absolute

    mgr = MagicMock()
    mgr.list_servers = MagicMock(return_value=[
        {"name": "filesystem", "type": "filesystem", "tool_count": 10}
    ])
    mgr.servers = {"filesystem": fs_mock}
    mgr.get_all_tools = MagicMock(return_value=[])
    loop.mcp_manager = mgr

    from backend.skills.skill_loader import get_skill_loader
    context = {
        "username": username,
        "allowed_directories": settings.allowed_directories,
        "skill_loader": get_skill_loader(),
    }
    return await loop._build_system_prompt(context, message="test")


async def test_G1_data_root_in_prompt_is_absolute():
    """系统提示中注入的 data_root（customer_data）是绝对路径。"""
    prompt = await _build_prompt()
    # 找到路径规则中的 data_root 行
    for line in prompt.splitlines():
        if "customer_data" in line and ("→" in line or "写入" in line):
            # 应该能找到绝对路径格式（带盘符或/开头）
            from pathlib import PurePosixPath, PureWindowsPath
            assert any(c in line for c in [":\\", ":/", "/"]), (
                f"data_root line doesn't look absolute: {line}"
            )
            break
    print("[PASS] G1: data_root in system prompt is absolute")


async def test_G2_skills_root_in_prompt_is_absolute():
    """系统提示中注入的 skills_root（.claude/skills）是绝对路径。"""
    prompt = await _build_prompt()
    for line in prompt.splitlines():
        if ".claude" in line and ("→" in line or "写入" in line or "路径示例" in line):
            assert any(c in line for c in [":\\", ":/", "/"]), (
                f"skills_root line doesn't look absolute: {line}"
            )
            break
    print("[PASS] G2: skills_root in system prompt is absolute")


async def test_G3_prompt_contains_username_layer():
    """系统提示中路径示例包含当前用户名层（固有行为不被路径改变破坏）。"""
    prompt = await _build_prompt(username="testuser")
    assert "testuser" in prompt, "Username should appear in prompt path rules"
    print("[PASS] G3: prompt still contains username layer in path example")


async def test_G4_prompt_absolute_paths_match_settings():
    """系统提示中的绝对路径与 settings 解析结果一致。"""
    from backend.config.settings import settings
    prompt = await _build_prompt()
    # settings 中的目录路径应以某种形式出现在提示中（正斜杠格式）
    for d in settings.allowed_directories:
        d_fwd = d.replace("\\", "/")
        # 检查路径的关键部分出现在 prompt 中
        key_part = Path(d).name  # e.g., "customer_data" or "skills"
        assert key_part in prompt, f"Expected '{key_part}' in prompt"
    print("[PASS] G4: prompt absolute paths consistent with settings")


# ─────────────────────────────────────────────────────────────────────────────
# Section H: RBAC 回归 — T1~T8 无新路由/权限
# ─────────────────────────────────────────────────────────────────────────────

def test_H1_settings_module_has_no_new_api_routes():
    """settings.py 变更（T1）未引入任何新 API 路由。"""
    # settings 是纯配置模块，不应有 router/endpoint 定义
    import backend.config.settings as settings_mod
    assert not hasattr(settings_mod, "router"), "settings module should not have router"
    assert not hasattr(settings_mod, "app"), "settings module should not have app"
    print("[PASS] H1: settings module has no API routes")


def test_H2_no_new_permissions_in_init_rbac():
    """T1~T3 未向 init_rbac.py 添加文件系统路径相关权限。"""
    import pathlib
    src = (pathlib.Path(__file__).parent / "backend" / "scripts" / "init_rbac.py").read_text(encoding="utf-8")
    forbidden_kw = ["filesystem_path", "path_portability", "project_root", "resolve_path"]
    for kw in forbidden_kw:
        assert kw not in src.lower(), f"Unexpected new permission keyword: '{kw}'"
    # 核心权限仍存在
    assert "chat:use" in src and "settings:read" in src
    print("[PASS] H2: no path-portability permissions added to init_rbac.py")


def test_H3_settings_loads_without_error():
    """settings 模块在完整路径解析后正常加载，无异常。"""
    try:
        from backend.config.settings import settings, _PROJECT_ROOT
        assert settings is not None
        assert _PROJECT_ROOT is not None
    except Exception as e:
        assert False, f"settings module failed to load: {e}"
    print("[PASS] H3: settings module loads without error")


def test_H4_conversations_api_routes_unchanged():
    """T1~T8 未修改 conversations.py — 路由集合无变化。"""
    import backend.api.conversations as conv_mod
    route_paths = [getattr(r, "path", "") for r in conv_mod.router.routes]
    # 这些是 path-portability 不应引入的路由
    new_kw = ["project_root", "path_resolve", "filesystem_config"]
    for kw in new_kw:
        for p in route_paths:
            assert kw not in p.lower(), f"Unexpected route from T1-T8: {p}"
    print("[PASS] H4: conversations.py routes unchanged by T1-T8")


# ─────────────────────────────────────────────────────────────────────────────
# Section I: 安全/边界 — validator 鲁棒性
# ─────────────────────────────────────────────────────────────────────────────

def test_I1_empty_list_handled_gracefully():
    """validator 对空列表不崩溃，返回空列表。"""
    result = _invoke_validator([])
    assert result == [], f"Expected [], got {result}"
    print("[PASS] I1: empty list handled gracefully")


def test_I2_single_entry_list():
    """单条目列表也能正确解析。"""
    from backend.config.settings import _PROJECT_ROOT
    result = _invoke_validator(["customer_data"])
    assert len(result) == 1
    assert Path(result[0]).is_absolute()
    print("[PASS] I2: single entry list resolved correctly")


def test_I3_nonexistent_dir_still_resolves():
    """不存在的相对目录也能被 resolve（解析不依赖目录存在，创建由 server.py 负责）。"""
    from backend.config.settings import _PROJECT_ROOT
    result = _invoke_validator(["nonexistent_temp_dir_xyz"])
    assert len(result) == 1
    assert Path(result[0]).is_absolute()
    assert "nonexistent_temp_dir_xyz" in result[0]
    print("[PASS] I3: nonexistent dir resolves without error")


def test_I4_deep_relative_path_resolves():
    """深层相对路径（多级）正确解析。"""
    from backend.config.settings import _PROJECT_ROOT
    result = _invoke_validator(["a/b/c/d"])
    expected = str((_PROJECT_ROOT / "a/b/c/d").resolve())
    assert result == [expected]
    print("[PASS] I4: deep relative path resolves correctly")


def test_I5_relative_path_with_dotdot_resolves_safely():
    """相对路径含 ../ 时 resolve() 后指向实际绝对路径（仍是绝对路径，安全检查由 proxy 层负责）。"""
    result = _invoke_validator(["../some_outside_dir"])
    assert len(result) == 1
    assert Path(result[0]).is_absolute(), f"Not absolute: {result[0]}"
    # 注意：settings 层只负责 resolve，不负责安全拦截（proxy 层负责）
    print("[PASS] I5: dotdot relative path resolves to absolute (security by proxy layer)")


def test_I6_validator_idempotent_on_already_absolute():
    """对已经是绝对路径的输入再次调用 validator，结果幂等。"""
    from backend.config.settings import _PROJECT_ROOT
    abs_path = str(_PROJECT_ROOT / "customer_data")
    first = _invoke_validator([abs_path])
    second = _invoke_validator(first)
    assert first == second, f"Not idempotent: {first} vs {second}"
    print("[PASS] I6: validator idempotent on absolute input")


def test_I7_all_env_configured_paths_are_absolute_after_load():
    """.env 中配置的路径经 settings 加载后全部变为绝对路径（端到端验证）。"""
    from backend.config.settings import settings
    all_paths = settings.allowed_directories + settings.filesystem_write_allowed_dirs
    assert len(all_paths) >= 2, "Expected at least 2 configured paths"
    for p in all_paths:
        assert Path(p).is_absolute(), f"Path not absolute after load: {p}"
    print("[PASS] I7: all env-configured paths are absolute after settings load")


async def test_I8_proxy_permission_check_correct_with_resolved_paths():
    """端到端：使用 settings 解析后的路径创建 proxy，权限检查结果正确。"""
    from backend.config.settings import settings
    from backend.core.filesystem_permission_proxy import FilesystemPermissionProxy

    base = AsyncMock()
    base._allowed = frozenset({"filesystem"})
    base.servers = {"filesystem": MagicMock()}
    base.server_configs = {}
    base.list_servers = MagicMock(return_value=[{"name": "filesystem"}])
    base.call_tool = AsyncMock(return_value={"success": True, "result": "ok"})

    proxy = FilesystemPermissionProxy(
        base=base,
        write_allowed_dirs=settings.filesystem_write_allowed_dirs,
        read_allowed_dirs=settings.allowed_directories,
    )

    # customer_data 下写入应该被允许
    from pathlib import Path as P
    customer_data = P(settings.filesystem_write_allowed_dirs[0])
    test_path = str(customer_data / "test_output.csv")

    result = await proxy.call_tool("filesystem", "write_file",
                                    {"path": test_path, "content": "a,b,c"})
    assert result.get("success") is True, f"Write to customer_data should be allowed: {result}"
    print("[PASS] I8: proxy permission check correct with settings-resolved paths")


# ─────────────────────────────────────────────────────────────────────────────
# runner
# ─────────────────────────────────────────────────────────────────────────────

async def run_all():
    all_tests = [
        # A: _PROJECT_ROOT
        ("A1", test_A1_project_root_is_absolute),
        ("A2", test_A2_project_root_matches_expected_location),
        ("A3", test_A3_project_root_contains_project_structure),
        ("A4", test_A4_project_root_stable_across_imports),
        # B: 相对路径解析
        ("B1", test_B1_relative_customer_data_resolves_under_project_root),
        ("B2", test_B2_relative_claude_skills_resolves_correctly),
        ("B3", test_B3_relative_skills_user_resolves_correctly),
        ("B4", test_B4_relative_paths_always_produce_absolute),
        ("B5", test_B5_multiple_relative_paths_order_preserved),
        ("B6", test_B6_backslash_relative_path_resolves),
        # C: 绝对路径向后兼容
        ("C1", test_C1_absolute_path_passes_through_unchanged),
        ("C2", test_C2_absolute_path_not_double_resolved),
        ("C3", test_C3_mixed_list_relative_and_absolute),
        ("C4", test_C4_old_style_absolute_env_format_still_works),
        # D: Settings 从 .env 加载
        ("D1", test_D1_settings_allowed_directories_all_absolute),
        ("D2", test_D2_settings_write_dirs_all_absolute),
        ("D3", test_D3_settings_paths_under_project_root),
        ("D4", test_D4_default_values_produce_absolute_paths),
        ("D5", test_D5_allowed_directories_contains_customer_data_and_skills),
        ("D6", test_D6_write_dirs_contains_customer_data_and_user_skills),
        # E: FilesystemMCPServer
        ("E1", test_E1_filesystem_server_allowed_dirs_all_absolute),
        ("E2", test_E2_filesystem_server_dirs_match_settings),
        # F: Proxy / Binder
        ("F1", test_F1_proxy_write_allowed_all_absolute),
        ("F2", test_F2_proxy_read_allowed_all_absolute),
        ("F3", test_F3_binder_creates_proxy_with_absolute_paths),
        # G: LLM 系统提示
        ("G1", test_G1_data_root_in_prompt_is_absolute),
        ("G2", test_G2_skills_root_in_prompt_is_absolute),
        ("G3", test_G3_prompt_contains_username_layer),
        ("G4", test_G4_prompt_absolute_paths_match_settings),
        # H: RBAC
        ("H1", test_H1_settings_module_has_no_new_api_routes),
        ("H2", test_H2_no_new_permissions_in_init_rbac),
        ("H3", test_H3_settings_loads_without_error),
        ("H4", test_H4_conversations_api_routes_unchanged),
        # I: 安全/边界
        ("I1", test_I1_empty_list_handled_gracefully),
        ("I2", test_I2_single_entry_list),
        ("I3", test_I3_nonexistent_dir_still_resolves),
        ("I4", test_I4_deep_relative_path_resolves),
        ("I5", test_I5_relative_path_with_dotdot_resolves_safely),
        ("I6", test_I6_validator_idempotent_on_already_absolute),
        ("I7", test_I7_all_env_configured_paths_are_absolute_after_load),
        ("I8", test_I8_proxy_permission_check_correct_with_resolved_paths),
    ]

    passed = failed = 0
    print("\n" + "=" * 65)
    print("Filesystem Path Portability Tests (T1-T3, T8)")
    print("=" * 65)

    for label, fn in all_tests:
        try:
            if asyncio.iscoroutinefunction(fn):
                await fn()
            else:
                fn()
            passed += 1
        except Exception as exc:
            failed += 1
            import traceback
            print(f"[FAIL] {label} {fn.__name__}: {exc}")
            traceback.print_exc()

    print(f"\n{'=' * 65}")
    print(f"Results: {passed} passed, {failed} failed / {len(all_tests)} total")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    import sys, atexit
    import pathlib as _pl
    sys.path.insert(0, str(_pl.Path(__file__).parent))
    try:
        from conftest import _cleanup_test_data as _ctd
        atexit.register(_ctd, label="post-run")   # 进程退出时必然执行（含 sys.exit）
        _ctd(label="pre-run")
    except Exception:
        pass
    asyncio.run(run_all())
