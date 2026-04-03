"""
test_customer_data_isolation.py
================================
customer_data 用户隔离功能测试（T1-T10 改造验收）

测试分区：
  U (7)  — 迁移结果：文件系统状态验证
  V (6)  — agentic_loop._build_system_prompt 路径注入
  W (5)  — analyst_agent._build_file_write_section 路径约束
  X (6)  — FilesystemPermissionProxy 子目录写权限覆盖
  Y (4)  — Skill 文件内容验证
  Z (3)  — RBAC 范围（无新端点/菜单）

共 31 个测试。

RBAC 注意：本次改造为纯后端基础设施变更，无新菜单、无新 API 端点，
所有现有路由和权限矩阵保持不变。
"""

import asyncio
import sys
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "backend"))
sys.path.insert(0, os.path.dirname(__file__))

# ── 项目路径常量 ──────────────────────────────────────────────────────────────

_ROOT = Path(__file__).parent.resolve()
_CUSTOMER_DATA = _ROOT / "customer_data"
_SKILLS_ROOT = _ROOT / ".claude" / "skills"
_USER_SKILLS = _SKILLS_ROOT / "user"
_SUPERADMIN_DATA = _CUSTOMER_DATA / "superadmin"


# ── 公共 helpers ─────────────────────────────────────────────────────────────

def _make_proxy(write_allowed_dirs=None, read_allowed_dirs=None, base=None):
    """创建 FilesystemPermissionProxy，write_allowed 默认为 customer_data/ 根。"""
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


async def _call_build_system_prompt(username: str, dirs: list) -> str:
    """调用 AgenticLoop._build_system_prompt 的最小 mock 辅助函数。"""
    from backend.agents.agentic_loop import AgenticLoop

    # 构造最小 mock self
    loop = MagicMock(spec=AgenticLoop)
    loop.llm_adapter = MagicMock()

    # Mock filesystem MCP server
    fs_server = MagicMock()
    fs_server.allowed_directories = dirs
    loop.mcp_manager = MagicMock()
    loop.mcp_manager.list_servers.return_value = [
        {"name": "filesystem", "type": "filesystem", "tool_count": 5}
    ]
    loop.mcp_manager.servers = {"filesystem": fs_server}
    loop.mcp_manager.get_all_tools.return_value = []

    context = {"username": username, "system_prompt": "", "conversation_id": "test"}

    # 屏蔽 skill_loader 避免实际 DB 访问（懒加载，需 patch 源模块）
    with patch("backend.skills.skill_loader.get_skill_loader") as mock_sl:
        mock_sl.return_value.build_skill_prompt_async = AsyncMock(return_value="")
        result = await AgenticLoop._build_system_prompt(loop, context, message="test")

    return result


def _default_dirs():
    """返回 settings 的默认 allowed_directories（绝对路径）。"""
    from backend.config.settings import settings
    return list(settings.allowed_directories)


# ═══════════════════════════════════════════════════════════════════════════════
# Section U — 迁移结果：文件系统状态验证
# ═══════════════════════════════════════════════════════════════════════════════

def test_U1_superadmin_dir_exists():
    """customer_data/superadmin/ 目录存在。"""
    assert _SUPERADMIN_DATA.exists(), "customer_data/superadmin/ 目录不存在"
    assert _SUPERADMIN_DATA.is_dir()


def test_U2_db_knowledge_moved_to_superadmin():
    """customer_data/superadmin/db_knowledge/ 存在且含 _index.md。"""
    db_knowledge = _SUPERADMIN_DATA / "db_knowledge"
    assert db_knowledge.exists(), "customer_data/superadmin/db_knowledge/ 不存在"
    assert (db_knowledge / "_index.md").exists(), "_index.md 未迁移"


def test_U3_reports_moved_to_superadmin():
    """customer_data/superadmin/reports/ 存在且含报告文件。"""
    reports = _SUPERADMIN_DATA / "reports"
    assert reports.exists(), "customer_data/superadmin/reports/ 不存在"
    md_files = list(reports.glob("*.md"))
    assert len(md_files) >= 1, "reports/ 目录为空，迁移可能未完成"


def test_U4_old_nested_dir_removed():
    """旧的 customer_data/customer_data/ 嵌套目录已清除。"""
    nested = _CUSTOMER_DATA / "customer_data"
    assert not nested.exists(), (
        "customer_data/customer_data/ 仍然存在，迁移未清理旧目录"
    )


def test_U5_old_root_reports_removed():
    """旧的 customer_data/reports/ 根目录已清除（内容已并入 superadmin/reports/）。"""
    old_reports = _CUSTOMER_DATA / "reports"
    assert not old_reports.exists(), (
        "customer_data/reports/ 根目录仍然存在，迁移未清理"
    )


def test_U6_stray_skill_mx_归位():
    """误放的 clickhouse-analyst-mx.md 已归位到 .claude/skills/user/superadmin/。"""
    target = _USER_SKILLS / "superadmin" / "clickhouse-analyst-mx.md"
    assert target.exists(), (
        f"clickhouse-analyst-mx.md 未归位到 {target}"
    )


def test_U7_stray_skill_conflict_saved_as_bak():
    """冲突的旧版 clickhouse-analyst.md 保存为 .bak，新版已保留。"""
    bak = _USER_SKILLS / "superadmin" / "clickhouse-analyst.md.bak"
    main = _USER_SKILLS / "superadmin" / "clickhouse-analyst.md"
    assert bak.exists(), "冲突文件 .bak 未创建"
    assert main.exists(), "主 clickhouse-analyst.md 丢失"


# ═══════════════════════════════════════════════════════════════════════════════
# Section V — agentic_loop._build_system_prompt 路径注入
# ═══════════════════════════════════════════════════════════════════════════════

async def test_V1_alice_gets_user_specific_data_path():
    """alice 登录时，系统提示包含 customer_data/alice/ 路径。"""
    dirs = _default_dirs()
    result = await _call_build_system_prompt("alice", dirs)
    assert "alice" in result, "系统提示未包含 alice 用户名"
    # 数据路径必须含用户名子目录
    assert "/alice/" in result.replace("\\", "/"), (
        "系统提示路径未使用 customer_data/alice/ 格式"
    )


async def test_V2_superadmin_gets_superadmin_data_path():
    """superadmin 登录时，系统提示包含 customer_data/superadmin/ 路径。"""
    dirs = _default_dirs()
    result = await _call_build_system_prompt("superadmin", dirs)
    assert "/superadmin/" in result.replace("\\", "/"), (
        "系统提示路径未使用 customer_data/superadmin/ 格式"
    )


async def test_V3_anonymous_gets_anonymous_data_path():
    """匿名用户时，系统提示包含 customer_data/anonymous/ 路径。"""
    dirs = _default_dirs()
    result = await _call_build_system_prompt("anonymous", dirs)
    assert "/anonymous/" in result.replace("\\", "/"), (
        "匿名用户路径未包含 anonymous 子目录"
    )


async def test_V4_skills_path_still_injected():
    """技能文件路径注入仍然正常（skills_root/user/{username}/）。"""
    dirs = _default_dirs()
    result = await _call_build_system_prompt("alice", dirs)
    assert ".claude" in result, "系统提示中缺少 .claude 技能路径"
    assert "user/alice" in result.replace("\\", "/"), (
        "系统提示中缺少 user/alice 技能子目录"
    )


async def test_V5_data_path_contains_user_isolation_hint():
    """系统提示包含"禁止写入其他用户目录"提示。"""
    dirs = _default_dirs()
    result = await _call_build_system_prompt("alice", dirs)
    assert "其他用户" in result, "系统提示缺少跨用户写入禁止说明"


async def test_V6_different_users_get_different_paths():
    """不同用户获得不同的数据路径注入。"""
    dirs = _default_dirs()
    result_alice = await _call_build_system_prompt("alice", dirs)
    result_bob = await _call_build_system_prompt("bob", dirs)
    # alice 路径在 bob 的结果中不应出现（数据段）
    alice_data_hint = "/alice/"
    bob_data_hint = "/bob/"
    assert alice_data_hint in result_alice.replace("\\", "/")
    assert bob_data_hint in result_bob.replace("\\", "/")
    assert alice_data_hint not in result_bob.replace("\\", "/"), (
        "bob 的系统提示包含了 alice 的路径"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Section W — analyst_agent._build_file_write_section 路径约束
# ═══════════════════════════════════════════════════════════════════════════════

def _call_build_file_write_section(username: str, conversation_id: str = "conv-test",
                                   granted: bool = False) -> str:
    """调用 _build_file_write_section 的辅助函数。"""
    from backend.agents.analyst_agent import _build_file_write_section
    from unittest.mock import patch

    context = {"username": username, "conversation_id": conversation_id}

    # approval_manager 在函数内懒加载，patch 源模块
    with patch("backend.core.approval_manager.approval_manager") as mock_am:
        mock_am.is_file_write_granted.return_value = granted
        return _build_file_write_section(context)


def test_W1_alice_path_constraint():
    """alice 的文件写入约束包含 customer_data/alice/。"""
    result = _call_build_file_write_section("alice")
    assert "customer_data/alice/" in result, (
        "alice 的路径约束未使用 customer_data/alice/"
    )


def test_W2_superadmin_path_constraint():
    """superadmin 的文件写入约束包含 customer_data/superadmin/。"""
    result = _call_build_file_write_section("superadmin")
    assert "customer_data/superadmin/" in result


def test_W3_example_path_contains_username():
    """路径示例中包含用户名（如 customer_data/bob/reports/...）。"""
    result = _call_build_file_write_section("bob")
    assert "customer_data/bob/" in result
    # 示例路径也应含用户名
    assert "bob/reports/" in result, "示例路径未包含用户名子目录"


def test_W4_anonymous_path_constraint():
    """匿名用户的路径约束包含 customer_data/anonymous/。"""
    result = _call_build_file_write_section("anonymous")
    assert "customer_data/anonymous/" in result


def test_W5_granted_state_also_uses_user_path():
    """已授权写入状态下，路径约束同样包含用户专属目录。"""
    result = _call_build_file_write_section("alice", granted=True)
    assert "customer_data/alice/" in result
    assert "已获得文件写入授权" in result


# ═══════════════════════════════════════════════════════════════════════════════
# Section X — FilesystemPermissionProxy 子目录写权限覆盖
# ═══════════════════════════════════════════════════════════════════════════════

async def test_X1_write_to_user_subdir_allowed():
    """customer_data/{username}/ 子目录写入被允许（代理不阻止）。"""
    proxy, base = _make_proxy()
    user_file = str(_CUSTOMER_DATA / "alice" / "report.csv")
    result = await proxy.call_tool("filesystem", "write_file", {"path": user_file})
    assert result.get("success") is True, (
        f"customer_data/alice/report.csv 写入被意外拦截: {result}"
    )
    base.call_tool.assert_called_once()


async def test_X2_write_to_superadmin_subdir_allowed():
    """customer_data/superadmin/ 子目录写入被允许。"""
    proxy, base = _make_proxy()
    target = str(_CUSTOMER_DATA / "superadmin" / "reports" / "new_report.md")
    result = await proxy.call_tool("filesystem", "write_file", {"path": target})
    assert result.get("success") is True, (
        f"customer_data/superadmin/reports/ 写入被意外拦截: {result}"
    )


async def test_X3_deep_nested_user_subdir_allowed():
    """深层嵌套用户目录写入被允许（customer_data/bob/deep/subdir/file.json）。"""
    proxy, base = _make_proxy()
    deep = str(_CUSTOMER_DATA / "bob" / "deep" / "subdir" / "file.json")
    result = await proxy.call_tool("filesystem", "write_file", {"path": deep})
    assert result.get("success") is True, f"深层子目录写入被拦截: {result}"


async def test_X4_fix2_still_blocks_claude_under_customer_data():
    """Fix-2 仍然生效：customer_data/.claude/skills/... 写入被拦截。"""
    proxy, base = _make_proxy()
    bad_path = str(_CUSTOMER_DATA / ".claude" / "skills" / "user" / "alice" / "skill.md")
    result = await proxy.call_tool("filesystem", "write_file", {"path": bad_path})
    assert result.get("success") is False, (
        "Fix-2 失效：customer_data/.claude/ 路径未被拦截"
    )
    assert "权限拒绝" in result.get("error", ""), "拦截消息格式异常"


async def test_X5_skills_user_subdir_still_allowed():
    """.claude/skills/user/{username}/ 写入仍然被允许（技能路径不受影响）。"""
    proxy, base = _make_proxy()
    skill_path = str(_USER_SKILLS / "alice" / "my-skill.md")
    result = await proxy.call_tool("filesystem", "write_file", {"path": skill_path})
    assert result.get("success") is True, (
        f".claude/skills/user/alice/ 写入被意外拦截: {result}"
    )


async def test_X6_create_directory_for_new_user():
    """新用户 customer_data/newuser/ 目录创建被允许。"""
    proxy, base = _make_proxy()
    new_dir = str(_CUSTOMER_DATA / "newuser")
    result = await proxy.call_tool("filesystem", "create_directory", {"path": new_dir})
    assert result.get("success") is True, (
        f"新用户目录 customer_data/newuser/ 创建被拦截: {result}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Section Y — Skill 文件内容验证
# ═══════════════════════════════════════════════════════════════════════════════

def test_Y1_clickhouse_analyst_uses_superadmin_db_knowledge():
    """clickhouse-analyst.md 知识库路径已更新为 customer_data/superadmin/db_knowledge/。"""
    skill_file = _USER_SKILLS / "superadmin" / "clickhouse-analyst.md"
    assert skill_file.exists(), "clickhouse-analyst.md 不存在"
    content = skill_file.read_text(encoding="utf-8")
    assert "customer_data/superadmin/db_knowledge/" in content, (
        "clickhouse-analyst.md 知识库路径未更新为 superadmin 子目录"
    )
    # 旧路径不应再存在（非注释上下文）
    assert "customer_data/db_knowledge/\n" not in content, (
        "clickhouse-analyst.md 仍包含旧的未分用户路径"
    )


def test_Y2_base_safety_uses_current_user_placeholder():
    """_base-safety.md 数据文件路径规则使用 {CURRENT_USER} 占位符。"""
    safety_file = _SKILLS_ROOT / "system" / "_base-safety.md"
    assert safety_file.exists()
    content = safety_file.read_text(encoding="utf-8")
    assert "customer_data/{CURRENT_USER}/" in content, (
        "_base-safety.md 未更新为 customer_data/{CURRENT_USER}/ 格式"
    )


def test_Y3_base_tools_uses_current_user_placeholder():
    """_base-tools.md 写入目录说明使用 {CURRENT_USER} 占位符。"""
    tools_file = _SKILLS_ROOT / "system" / "_base-tools.md"
    assert tools_file.exists()
    content = tools_file.read_text(encoding="utf-8")
    assert "customer_data/{CURRENT_USER}/" in content, (
        "_base-tools.md 未更新为 customer_data/{CURRENT_USER}/ 格式"
    )


def test_Y4_clickhouse_analyst_mx_归位():
    """clickhouse-analyst-mx.md 已归位到 .claude/skills/user/superadmin/。"""
    mx_file = _USER_SKILLS / "superadmin" / "clickhouse-analyst-mx.md"
    assert mx_file.exists(), "clickhouse-analyst-mx.md 未归位"
    content = mx_file.read_text(encoding="utf-8")
    # 文件内容应为 MX 相关
    assert "MX" in content or "mx" in content.lower() or "墨西哥" in content


# ═══════════════════════════════════════════════════════════════════════════════
# Section Z — RBAC 范围（无新端点/菜单）
# ═══════════════════════════════════════════════════════════════════════════════

def test_Z1_no_new_api_endpoints_for_migration():
    """迁移功能无新 REST API 端点（迁移是一次性脚本，非 API）。"""
    from backend.api import users as users_mod
    routes = [r.path for r in users_mod.router.routes]
    # 确认没有 customer_data 相关路由
    bad = [r for r in routes if "customer_data" in r.lower()]
    assert not bad, f"发现意外的 customer_data 相关路由: {bad}"
    # users router 固定 7 条 route（每 HTTP method 一条）：
    # GET+POST /users, GET+PUT /users/{id}, PUT /users/{id}/password,
    # POST /users/{id}/roles, DELETE /users/{id}/roles/{role_id}
    assert len(routes) == 7, (
        f"users router 路由数量变化（预期 7，实际 {len(routes)}）: {routes}"
    )


def test_Z2_no_new_frontend_routes_needed():
    """customer_data 用户隔离无需新前端页面（纯后端基础设施）。"""
    # 检查前端路由配置（App.tsx）不含 customer_data 相关新路由
    app_tsx = _ROOT / "frontend" / "src" / "App.tsx"
    if app_tsx.exists():
        content = app_tsx.read_text(encoding="utf-8")
        # 确认没有新增 customer-data 相关路由
        assert "customer-data" not in content.lower() or "CustomerData" not in content, \
            "发现意外新增的前端 customer-data 路由"


def test_Z3_filesystem_write_dirs_cover_user_subdirs():
    """settings.filesystem_write_allowed_dirs 包含 customer_data/ 根，可覆盖所有用户子目录。"""
    from backend.config.settings import settings

    write_dirs = settings.filesystem_write_allowed_dirs
    # 找到 customer_data 目录
    customer_data_entries = [
        d for d in write_dirs
        if "customer_data" in d.replace("\\", "/")
    ]
    assert customer_data_entries, "filesystem_write_allowed_dirs 中缺少 customer_data 条目"

    # 验证 customer_data/alice/ 是 customer_data/ 的子路径
    from pathlib import Path
    customer_root = Path(customer_data_entries[0]).resolve()
    alice_subdir = (customer_root / "alice" / "report.csv").resolve()
    # resolve() 后 relative_to() 不抛出 → 说明是子路径
    try:
        alice_subdir.relative_to(customer_root)
    except ValueError:
        assert False, (
            f"customer_data/alice/ 不是 {customer_root} 的子路径，"
            "写权限无法覆盖用户子目录"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 运行器
# ═══════════════════════════════════════════════════════════════════════════════

async def run_all():
    results = []
    passed = failed = 0

    # 同步测试
    sync_tests = [
        test_U1_superadmin_dir_exists,
        test_U2_db_knowledge_moved_to_superadmin,
        test_U3_reports_moved_to_superadmin,
        test_U4_old_nested_dir_removed,
        test_U5_old_root_reports_removed,
        test_U6_stray_skill_mx_归位,
        test_U7_stray_skill_conflict_saved_as_bak,
        test_W1_alice_path_constraint,
        test_W2_superadmin_path_constraint,
        test_W3_example_path_contains_username,
        test_W4_anonymous_path_constraint,
        test_W5_granted_state_also_uses_user_path,
        test_Y1_clickhouse_analyst_uses_superadmin_db_knowledge,
        test_Y2_base_safety_uses_current_user_placeholder,
        test_Y3_base_tools_uses_current_user_placeholder,
        test_Y4_clickhouse_analyst_mx_归位,
        test_Z1_no_new_api_endpoints_for_migration,
        test_Z2_no_new_frontend_routes_needed,
        test_Z3_filesystem_write_dirs_cover_user_subdirs,
    ]

    # 异步测试
    async_tests = [
        test_V1_alice_gets_user_specific_data_path,
        test_V2_superadmin_gets_superadmin_data_path,
        test_V3_anonymous_gets_anonymous_data_path,
        test_V4_skills_path_still_injected,
        test_V5_data_path_contains_user_isolation_hint,
        test_V6_different_users_get_different_paths,
        test_X1_write_to_user_subdir_allowed,
        test_X2_write_to_superadmin_subdir_allowed,
        test_X3_deep_nested_user_subdir_allowed,
        test_X4_fix2_still_blocks_claude_under_customer_data,
        test_X5_skills_user_subdir_still_allowed,
        test_X6_create_directory_for_new_user,
    ]

    total = len(sync_tests) + len(async_tests)

    for fn in sync_tests:
        name = fn.__name__
        try:
            fn()
            results.append(f"  PASS  {name}")
            passed += 1
        except Exception as e:
            results.append(f"  FAIL  {name}: {e}")
            failed += 1

    for fn in async_tests:
        name = fn.__name__
        try:
            await fn()
            results.append(f"  PASS  {name}")
            passed += 1
        except Exception as e:
            results.append(f"  FAIL  {name}: {e}")
            failed += 1

    print(f"\n{'='*70}")
    print(f"test_customer_data_isolation.py  — {total} tests")
    print(f"{'='*70}")
    for r in results:
        print(r)
    print(f"{'='*70}")
    print(f"{'PASSED' if failed == 0 else 'FAILED'}: {passed} passed, {failed} failed\n")
    return failed


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
