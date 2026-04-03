"""
test_filesystem_permission.py
==============================
Directory-level filesystem write permission tests for FilesystemPermissionProxy.

Tests:
  F1  Read tools always pass through (no directory check)
  F2  Write to customer_data/ is allowed
  F3  Write to .claude/skills/user/ is allowed
  F4  Write to .claude/skills/ (system skills root) is blocked
  F5  Write to project source (e.g. backend/) is blocked
  F6  delete from customer_data/ is allowed
  F7  delete from system skills dir is blocked
  F8  create_directory in customer_data/ is allowed
  F9  Path traversal attempts are blocked
  F10 Absolute path within write_allowed passes
  F11 Absolute path outside write_allowed is blocked
  F12 AgentMCPBinder wraps FilteredMCPManager with proxy when filesystem is allowed
  F13 Settings.filesystem_write_allowed_dirs defaults to customer_data/ and skills/user/
  F14 Settings.allowed_directories defaults to customer_data/ and .claude/skills/
"""

import asyncio
import sys
import os
import tempfile
import shutil
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "backend"))

# ── project root ────────────────────────────────────────────────────────────

_PROJECT_ROOT = Path(__file__).parent.resolve()
_CUSTOMER_DATA = _PROJECT_ROOT / "customer_data"
_SKILLS_ROOT = _PROJECT_ROOT / ".claude" / "skills"
_USER_SKILLS = _SKILLS_ROOT / "user"


# ── helpers ─────────────────────────────────────────────────────────────────

def _make_proxy(
    write_allowed_dirs=None,
    read_allowed_dirs=None,
    base=None,
):
    """Create a FilesystemPermissionProxy with a mock base."""
    from backend.core.filesystem_permission_proxy import FilesystemPermissionProxy

    if write_allowed_dirs is None:
        write_allowed_dirs = [str(_CUSTOMER_DATA), str(_USER_SKILLS)]
    if read_allowed_dirs is None:
        read_allowed_dirs = [str(_CUSTOMER_DATA), str(_SKILLS_ROOT)]
    if base is None:
        base = AsyncMock()
        base.servers = {"filesystem": MagicMock()}
        base.server_configs = {}
        base.list_servers = MagicMock(return_value=[{"name": "filesystem"}])
        base.get_server = MagicMock(return_value=MagicMock())
        base.get_all_tools = MagicMock(return_value=[])
        base.get_all_resources = MagicMock(return_value=[])
        base.call_tool = AsyncMock(return_value={"success": True, "result": "ok"})

    return FilesystemPermissionProxy(
        base=base,
        write_allowed_dirs=write_allowed_dirs,
        read_allowed_dirs=read_allowed_dirs,
    ), base


# ── F1: read tools always pass through ──────────────────────────────────────

async def test_F1_read_tools_pass_through():
    """read_file, list_directory etc. are never blocked."""
    proxy, base = _make_proxy()
    read_tools = ["read_file", "list_directory", "search_files", "get_file_info",
                  "get_file_type", "list_allowed_directories"]
    for tool in read_tools:
        result = await proxy.call_tool("filesystem", tool, {"path": "/etc/passwd"})
        assert result == {"success": True, "result": "ok"}, (
            f"read tool '{tool}' should pass through"
        )
    print("[PASS] F1: read tools always pass through")


# ── F2: write to customer_data/ allowed ─────────────────────────────────────

async def test_F2_write_to_customer_data_allowed():
    """write_file into customer_data/ must succeed."""
    proxy, base = _make_proxy()
    path = str(_CUSTOMER_DATA / "output.csv")
    result = await proxy.call_tool("filesystem", "write_file",
                                   {"path": path, "content": "a,b,c"})
    assert result.get("success") is True, f"Expected allowed, got: {result}"
    print("[PASS] F2: write to customer_data/ is allowed")


# ── F3: write to .claude/skills/user/ allowed ────────────────────────────────

async def test_F3_write_to_user_skills_allowed():
    """write_file into .claude/skills/user/{username}/ must succeed (Fix-4: username subdir required)."""
    proxy, base = _make_proxy()
    # Must include username subdir layer (Fix-4 blocks flat writes to user/ root)
    path = str(_USER_SKILLS / "alice" / "my-custom-skill.md")
    result = await proxy.call_tool("filesystem", "write_file",
                                   {"path": path, "content": "# test"})
    assert result.get("success") is True, f"Expected allowed, got: {result}"
    print("[PASS] F3: write to .claude/skills/user/ is allowed")


# ── F4: write to .claude/skills/ (system root) blocked ──────────────────────

async def test_F4_write_to_system_skills_blocked():
    """write_file into .claude/skills/ (not user/) must be blocked."""
    proxy, base = _make_proxy()
    path = str(_SKILLS_ROOT / "etl-engineer.md")
    result = await proxy.call_tool("filesystem", "write_file",
                                   {"path": path, "content": "# tampered"})
    assert result.get("success") is False, f"Expected blocked, got: {result}"
    assert "权限拒绝" in result.get("error", ""), "Error message should mention permission"
    print("[PASS] F4: write to system skills root is blocked")


# ── F5: write to project source blocked ─────────────────────────────────────

async def test_F5_write_to_backend_blocked():
    """write_file into backend/ source directory must be blocked."""
    proxy, base = _make_proxy()
    path = str(_PROJECT_ROOT / "backend" / "config" / "settings.py")
    result = await proxy.call_tool("filesystem", "write_file",
                                   {"path": path, "content": "malicious"})
    assert result.get("success") is False, f"Expected blocked, got: {result}"
    print("[PASS] F5: write to project source is blocked")


# ── F6: delete from customer_data/ allowed ──────────────────────────────────

async def test_F6_delete_in_customer_data_allowed():
    """delete inside customer_data/ must succeed."""
    proxy, base = _make_proxy()
    path = str(_CUSTOMER_DATA / "old_report.csv")
    result = await proxy.call_tool("filesystem", "delete", {"path": path})
    assert result.get("success") is True, f"Expected allowed, got: {result}"
    print("[PASS] F6: delete in customer_data/ is allowed")


# ── F7: delete from system skills blocked ───────────────────────────────────

async def test_F7_delete_system_skills_blocked():
    """delete inside .claude/skills/ (system dir) must be blocked."""
    proxy, base = _make_proxy()
    path = str(_SKILLS_ROOT / "clickhouse-analyst.md")
    result = await proxy.call_tool("filesystem", "delete", {"path": path})
    assert result.get("success") is False, f"Expected blocked, got: {result}"
    print("[PASS] F7: delete from system skills dir is blocked")


# ── F8: create_directory in customer_data/ allowed ──────────────────────────

async def test_F8_create_directory_in_customer_data_allowed():
    """create_directory inside customer_data/ must succeed."""
    proxy, base = _make_proxy()
    path = str(_CUSTOMER_DATA / "reports" / "2025")
    result = await proxy.call_tool("filesystem", "create_directory", {"path": path})
    assert result.get("success") is True, f"Expected allowed, got: {result}"
    print("[PASS] F8: create_directory in customer_data/ is allowed")


# ── F9: path traversal attempts blocked ─────────────────────────────────────

async def test_F9_path_traversal_blocked():
    """Traversal attempts outside read_allowed_dirs are blocked."""
    proxy, base = _make_proxy()
    traversal_paths = [
        "../backend/config/settings.py",
        "../../etc/passwd",
        str(_CUSTOMER_DATA / ".." / "backend" / "api" / "skills.py"),
    ]
    for path in traversal_paths:
        result = await proxy.call_tool("filesystem", "write_file",
                                       {"path": path, "content": "x"})
        assert result.get("success") is False, (
            f"Traversal path '{path}' should be blocked, got: {result}"
        )
    print("[PASS] F9: path traversal attempts are blocked")


# ── F10: absolute path within write_allowed passes ──────────────────────────

async def test_F10_absolute_path_in_write_allowed_passes():
    """Absolute paths inside write_allowed_dirs must pass."""
    proxy, base = _make_proxy()
    abs_path = str(_CUSTOMER_DATA.resolve() / "data.csv")
    result = await proxy.call_tool("filesystem", "write_file",
                                   {"path": abs_path, "content": "col1,col2"})
    assert result.get("success") is True, f"Expected allowed, got: {result}"
    print("[PASS] F10: absolute path within write_allowed passes")


# ── F11: absolute path outside write_allowed blocked ────────────────────────

async def test_F11_absolute_path_outside_blocked():
    """Absolute paths outside write_allowed_dirs must be blocked."""
    proxy, base = _make_proxy()
    abs_path = str((_PROJECT_ROOT / "backend" / "main.py").resolve())
    result = await proxy.call_tool("filesystem", "write_file",
                                   {"path": abs_path, "content": "x"})
    assert result.get("success") is False, f"Expected blocked, got: {result}"
    print("[PASS] F11: absolute path outside write_allowed is blocked")


# ── F12: AgentMCPBinder wraps with proxy ────────────────────────────────────

def test_F12_binder_wraps_with_proxy():
    """get_filtered_manager returns FilesystemPermissionProxy when filesystem is in allowed."""
    from backend.core.filesystem_permission_proxy import FilesystemPermissionProxy
    from backend.core.agent_mcp_binder import AgentMCPBinder

    # Mock mcp_manager with a filesystem server
    mock_manager = MagicMock()
    mock_manager.servers = {"filesystem": MagicMock(), "clickhouse-idn": MagicMock()}
    mock_manager.server_configs = {}
    mock_manager.list_servers = MagicMock(return_value=[
        {"name": "filesystem"}, {"name": "clickhouse-idn"}
    ])
    mock_manager.get_server = MagicMock(return_value=MagicMock())
    mock_manager.get_all_tools = MagicMock(return_value=[])
    mock_manager.get_all_resources = MagicMock(return_value=[])

    binder = AgentMCPBinder.__new__(AgentMCPBinder)
    binder._config_path = Path("/nonexistent")
    binder._config = {
        "analyst": {
            "clickhouse_connection": "readonly",
            "clickhouse_envs": "all",
            # no excluded_non_ch_servers -> filesystem included
        }
    }

    result = binder.get_filtered_manager("analyst", mock_manager)
    assert isinstance(result, FilesystemPermissionProxy), (
        f"Expected FilesystemPermissionProxy, got {type(result).__name__}"
    )
    print("[PASS] F12: AgentMCPBinder wraps FilteredMCPManager with FilesystemPermissionProxy")


# ── F13: settings.filesystem_write_allowed_dirs default ─────────────────────

def test_F13_settings_write_dirs_default():
    """filesystem_write_allowed_dirs must include customer_data/ and skills/user/."""
    from backend.config.settings import settings

    write_dirs = [Path(d).resolve() for d in settings.filesystem_write_allowed_dirs]
    expected_names = {"customer_data", "user"}
    actual_names = {d.name for d in write_dirs}
    assert expected_names == actual_names, (
        f"Expected dirs named {expected_names}, got {actual_names}"
    )
    # user dir must be inside .claude/skills/
    user_dirs = [d for d in write_dirs if d.name == "user"]
    assert any("skills" in str(d) for d in user_dirs), (
        "user/ write dir should be inside .claude/skills/"
    )
    print("[PASS] F13: settings.filesystem_write_allowed_dirs defaults correct")


# ── F14: settings.allowed_directories default ───────────────────────────────

def test_F14_settings_allowed_dirs_default():
    """allowed_directories must include customer_data/ and .claude/skills/."""
    from backend.config.settings import settings

    allowed = [Path(d).resolve() for d in settings.allowed_directories]
    names = {d.name for d in allowed}
    assert "customer_data" in names, f"customer_data not in allowed_directories: {names}"
    assert "skills" in names, f"skills not in allowed_directories: {names}"
    print("[PASS] F14: settings.allowed_directories defaults correct")


# ── non-filesystem server calls pass through ────────────────────────────────

async def test_F_non_filesystem_passthrough():
    """Calls to non-filesystem servers are never intercepted."""
    proxy, base = _make_proxy()
    result = await proxy.call_tool("clickhouse-idn", "query",
                                   {"sql": "DROP TABLE users"})
    assert result.get("success") is True, "Non-filesystem calls should always pass through"
    base.call_tool.assert_called_once_with("clickhouse-idn", "query",
                                           {"sql": "DROP TABLE users"})
    print("[PASS] F-extra: non-filesystem server calls always pass through")


# ── runner ───────────────────────────────────────────────────────────────────

async def run_all():
    all_tests = [
        ("F1",     test_F1_read_tools_pass_through),
        ("F2",     test_F2_write_to_customer_data_allowed),
        ("F3",     test_F3_write_to_user_skills_allowed),
        ("F4",     test_F4_write_to_system_skills_blocked),
        ("F5",     test_F5_write_to_backend_blocked),
        ("F6",     test_F6_delete_in_customer_data_allowed),
        ("F7",     test_F7_delete_system_skills_blocked),
        ("F8",     test_F8_create_directory_in_customer_data_allowed),
        ("F9",     test_F9_path_traversal_blocked),
        ("F10",    test_F10_absolute_path_in_write_allowed_passes),
        ("F11",    test_F11_absolute_path_outside_blocked),
        ("F12",    test_F12_binder_wraps_with_proxy),
        ("F13",    test_F13_settings_write_dirs_default),
        ("F14",    test_F14_settings_allowed_dirs_default),
        ("F-ext",  test_F_non_filesystem_passthrough),
    ]

    passed = failed = 0
    print("\n" + "=" * 60)
    print("Filesystem Permission Tests")
    print("=" * 60)

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

    print(f"\n{'=' * 60}")
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
