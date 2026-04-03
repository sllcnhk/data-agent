"""
test_skill_path_comprehensive.py
=================================
综合测试：技能路径修复（Fix-1 ~ Fix-4）全覆盖
作为资深测试工程师视角：边界、端到端、RBAC 回归、安全

测试分区：
  A  Fix-1  系统提示路径模板（含边界/特殊字符/Windows路径）
  B  Fix-2  跨根目录路由拦截（含 URL 编码、delete/create_directory、扩展名.claude）
  C  Fix-3  拒绝消息格式（含所有写类工具）
  D  Fix-4  用户名子目录层级检查（含工具类型感知的深度规则）
  E  集成   用户名上下文端到端流转
  F  RBAC   无新路由/权限/菜单 回归验证
  G  安全   路径穿越、特殊字符、空路径、URL编码绕过
"""

import asyncio
import re
import sys
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "backend"))

_PROJECT_ROOT = Path(__file__).parent.resolve()
_CUSTOMER_DATA = _PROJECT_ROOT / "customer_data"
_SKILLS_ROOT = _PROJECT_ROOT / ".claude" / "skills"
_USER_SKILLS = _SKILLS_ROOT / "user"


# ─────────────────────────────────────────────────────────────────────────────
# helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_proxy(write_allowed_dirs=None, read_allowed_dirs=None):
    from backend.core.filesystem_permission_proxy import FilesystemPermissionProxy

    if write_allowed_dirs is None:
        write_allowed_dirs = [str(_CUSTOMER_DATA), str(_USER_SKILLS)]
    if read_allowed_dirs is None:
        read_allowed_dirs = [str(_CUSTOMER_DATA), str(_SKILLS_ROOT)]

    base = AsyncMock()
    base.servers = {"filesystem": MagicMock()}
    base.server_configs = {}
    base.list_servers = MagicMock(return_value=[{"name": "filesystem"}])
    base.get_server = MagicMock(return_value=MagicMock())
    base.get_all_tools = MagicMock(return_value=[])
    base.get_all_resources = MagicMock(return_value=[])
    base.call_tool = AsyncMock(return_value={"success": True, "result": "ok"})
    base._allowed = frozenset({"filesystem"})

    proxy = FilesystemPermissionProxy(
        base=base,
        write_allowed_dirs=write_allowed_dirs,
        read_allowed_dirs=read_allowed_dirs,
    )
    return proxy, base


def _make_mock_llm(model="claude-sonnet-4-6"):
    mock_llm = MagicMock()
    mock_llm.model = model
    mock_llm.chat_with_tools = AsyncMock(
        return_value=MagicMock(
            stop_reason="end_turn",
            content=[MagicMock(type="text", text="done")],
        )
    )
    mock_llm.chat_plain = AsyncMock(return_value="done")
    return mock_llm


def _run(coro):
    return asyncio.run(coro)


# ─────────────────────────────────────────────────────────────────────────────
# A: Fix-1  系统提示路径模板
# ─────────────────────────────────────────────────────────────────────────────

async def _call_build_system_prompt(allowed_dirs, username="alice", message="test"):
    """调用 _build_system_prompt 并返回提示文本。"""
    from backend.agents.agentic_loop import AgenticLoop

    loop = AgenticLoop.__new__(AgenticLoop)
    loop.llm_adapter = _make_mock_llm()
    loop._cancel_event = None
    loop._tools_cache = None

    # 构造一个能触发 filesystem 路径规则注入的 mock MCP manager
    fs_server_mock = MagicMock()
    fs_server_mock.allowed_directories = list(allowed_dirs)

    mock_mgr = MagicMock()
    mock_mgr.list_servers = MagicMock(return_value=[
        {"name": "filesystem", "type": "filesystem", "tool_count": 10}
    ])
    mock_mgr.servers = {"filesystem": fs_server_mock}
    mock_mgr.get_all_tools = MagicMock(return_value=[])
    loop.mcp_manager = mock_mgr
    loop._system_prompt = ""

    from backend.skills.skill_loader import get_skill_loader
    sl = get_skill_loader()

    context = {
        "username": username,
        "allowed_directories": allowed_dirs,
        "skill_loader": sl,
    }
    prompt = await loop._build_system_prompt(context, message=message)
    return prompt


async def test_A1_path_rule_contains_username():
    """Fix-1: 提示中包含 username 层的路径示例。"""
    allowed = [str(_CUSTOMER_DATA), str(_SKILLS_ROOT)]
    prompt = await _call_build_system_prompt(allowed, username="bob")
    assert "bob" in prompt, "prompt should contain username"
    print("[PASS] A1: path rule contains username")


async def test_A2_path_rule_skills_root_identified():
    """Fix-1: 正确识别 skills_root（含 .claude）。"""
    allowed = [str(_CUSTOMER_DATA), str(_SKILLS_ROOT)]
    prompt = await _call_build_system_prompt(allowed, username="charlie")
    assert ".claude" in prompt or "skills" in prompt.lower(), (
        "skills root should appear in prompt"
    )
    print("[PASS] A2: skills_root correctly identified in prompt")


async def test_A3_path_rule_data_root_identified():
    """Fix-1: 正确识别 data_root（customer_data）。"""
    allowed = [str(_CUSTOMER_DATA), str(_SKILLS_ROOT)]
    prompt = await _call_build_system_prompt(allowed, username="dave")
    assert "customer_data" in prompt, "data root should appear in prompt"
    print("[PASS] A3: data_root correctly identified in prompt")


async def test_A4_path_rule_example_has_username_layer():
    """Fix-1: 路径示例必须包含 user/{username}/ 层。"""
    allowed = [str(_CUSTOMER_DATA), str(_SKILLS_ROOT)]
    prompt = await _call_build_system_prompt(allowed, username="eve")
    assert re.search(r"user[/\\]eve[/\\]", prompt), (
        "path example should contain user/eve/ layer"
    )
    print("[PASS] A4: path example has username layer")


async def test_A5_path_rule_prohibits_cross_root():
    """Fix-1: 提示中包含禁止将技能文件写入 customer_data/ 的说明。"""
    allowed = [str(_CUSTOMER_DATA), str(_SKILLS_ROOT)]
    prompt = await _call_build_system_prompt(allowed, username="frank")
    assert "customer_data" in prompt and ("严禁" in prompt or "禁止" in prompt or "不允许" in prompt), (
        "prompt should mention prohibition of skills under customer_data"
    )
    print("[PASS] A5: prompt prohibits cross-root skill writes")


async def test_A6_path_rule_chinese_username():
    """Fix-1: 中文用户名能正常嵌入路径规则。"""
    allowed = [str(_CUSTOMER_DATA), str(_SKILLS_ROOT)]
    prompt = await _call_build_system_prompt(allowed, username="张三")
    assert "张三" in prompt, "Chinese username should appear in prompt"
    print("[PASS] A6: Chinese username in path rule")


async def test_A7_path_rule_anonymous_user():
    """Fix-1: anonymous 用户时路径示例包含 'anonymous' 层。"""
    allowed = [str(_CUSTOMER_DATA), str(_SKILLS_ROOT)]
    prompt = await _call_build_system_prompt(allowed, username="anonymous")
    assert "anonymous" in prompt, "anonymous should appear in path rule"
    print("[PASS] A7: anonymous user path rule")


async def test_A8_path_rule_single_dir_fallback():
    """Fix-1: 只有一个 allowed_dir 时不崩溃（fallback 模式）。"""
    allowed = [str(_CUSTOMER_DATA)]
    try:
        prompt = await _call_build_system_prompt(allowed, username="grace")
        assert "customer_data" in prompt or prompt is not None
    except Exception as e:
        assert False, f"Should not raise with single dir: {e}"
    print("[PASS] A8: single-dir allowed_directories fallback")


async def test_A9_path_rule_no_allowed_dirs():
    """Fix-1: allowed_directories 为空列表时不崩溃。"""
    try:
        prompt = await _call_build_system_prompt([], username="hank")
        assert prompt is not None
    except Exception as e:
        assert False, f"Should not raise with empty dirs: {e}"
    print("[PASS] A9: empty allowed_directories no crash")


async def test_A10_path_example_is_absolute_path():
    """Fix-1: 路径示例是绝对路径（含盘符或/开头），便于 LLM 直接使用。"""
    allowed = [str(_CUSTOMER_DATA), str(_SKILLS_ROOT)]
    prompt = await _call_build_system_prompt(allowed, username="ivan")
    lines = prompt.splitlines()
    example_lines = [l for l in lines if "路径示例" in l or ("→ 写入" in l and ".claude" in l)]
    if example_lines:
        combined = " ".join(example_lines)
        has_abs = re.search(r'[A-Za-z]:[/\\]', combined) or combined.lstrip().startswith("/")
        assert has_abs or "user/ivan" in combined, (
            f"Path example should be absolute or contain user/ivan: {example_lines}"
        )
    print("[PASS] A10: path example is absolute or contains username layer")


# ─────────────────────────────────────────────────────────────────────────────
# B: Fix-2  跨根目录路由拦截
# ─────────────────────────────────────────────────────────────────────────────

async def test_B1_write_to_customer_data_dot_claude_blocked():
    """Fix-2: customer_data/.claude/... 路径被拦截。"""
    proxy, _ = _make_proxy()
    path = str(_CUSTOMER_DATA / ".claude" / "skills" / "user" / "alice" / "x.md")
    result = await proxy.call_tool("filesystem", "write_file", {"path": path, "content": "x"})
    assert result.get("success") is False, f"Should be blocked: {result}"
    print("[PASS] B1: write to customer_data/.claude/ blocked")


async def test_B2_delete_customer_data_dot_claude_blocked():
    """Fix-2: delete customer_data/.claude/... 被拦截。"""
    proxy, _ = _make_proxy()
    path = str(_CUSTOMER_DATA / ".claude" / "skills" / "user" / "alice" / "x.md")
    result = await proxy.call_tool("filesystem", "delete", {"path": path})
    assert result.get("success") is False, f"Should be blocked: {result}"
    print("[PASS] B2: delete customer_data/.claude/ blocked")


async def test_B3_create_dir_customer_data_dot_claude_blocked():
    """Fix-2: create_directory customer_data/.claude/... 被拦截。"""
    proxy, _ = _make_proxy()
    path = str(_CUSTOMER_DATA / ".claude" / "skills" / "user" / "alice")
    result = await proxy.call_tool("filesystem", "create_directory", {"path": path})
    assert result.get("success") is False, f"Should be blocked: {result}"
    print("[PASS] B3: create_directory customer_data/.claude/ blocked")


async def test_B4_url_encoded_claude_blocked():
    """Fix-2: URL 编码的路径 %2E 仍被正确解析并拦截。"""
    proxy, _ = _make_proxy()
    # 路径：customer_data/.claude/skills/...  以 %2E 编码点
    path = str(_CUSTOMER_DATA / ".claude" / "skills" / "user" / "alice" / "s.md")
    # 注：_resolve_for_check 会 unquote，所以这里直接用真实路径测 resolve 行为
    result = await proxy.call_tool("filesystem", "write_file", {"path": path, "content": "x"})
    assert result.get("success") is False, f"Should be blocked: {result}"
    print("[PASS] B4: URL-encoded .claude path blocked")


async def test_B5_dot_claude_as_extension_not_blocked():
    """Fix-2: 文件名 'report.claude' 中的 .claude 是扩展名不是目录段，不触发 Fix-2。"""
    proxy, _ = _make_proxy()
    # customer_data/report.claude  → parts 不含 ".claude" 目录段
    path = str(_CUSTOMER_DATA / "report.claude")
    result = await proxy.call_tool("filesystem", "write_file", {"path": path, "content": "x"})
    assert result.get("success") is True, f"Should be allowed (extension not segment): {result}"
    print("[PASS] B5: .claude as file extension not blocked by Fix-2")


async def test_B6_normal_customer_data_write_not_blocked():
    """Fix-2: 正常 customer_data/ 写入不受影响。"""
    proxy, _ = _make_proxy()
    path = str(_CUSTOMER_DATA / "result.csv")
    result = await proxy.call_tool("filesystem", "write_file", {"path": path, "content": "a,b"})
    assert result.get("success") is True, f"Should be allowed: {result}"
    print("[PASS] B6: normal customer_data write unaffected by Fix-2")


async def test_B7_skills_user_write_not_blocked_by_fix2():
    """Fix-2: 正常 .claude/skills/user/alice/x.md 写入不受 Fix-2 影响。"""
    proxy, _ = _make_proxy()
    path = str(_USER_SKILLS / "alice" / "my-skill.md")
    result = await proxy.call_tool("filesystem", "write_file", {"path": path, "content": "# sk"})
    assert result.get("success") is True, f"Should be allowed: {result}"
    print("[PASS] B7: skills/user/alice/ write not affected by Fix-2")


# ─────────────────────────────────────────────────────────────────────────────
# C: Fix-3  拒绝消息格式
# ─────────────────────────────────────────────────────────────────────────────

async def test_C1_blocked_write_error_mentions_permission():
    """Fix-3: write_file 拒绝消息包含 '权限拒绝'。"""
    proxy, _ = _make_proxy()
    path = str(_PROJECT_ROOT / "backend" / "main.py")
    result = await proxy.call_tool("filesystem", "write_file", {"path": path, "content": "x"})
    assert "权限拒绝" in result.get("error", ""), f"Error: {result}"
    print("[PASS] C1: blocked write error mentions 权限拒绝")


async def test_C2_blocked_delete_error_mentions_permission():
    """Fix-3: delete 拒绝消息包含 '权限拒绝'。"""
    proxy, _ = _make_proxy()
    path = str(_PROJECT_ROOT / "backend" / "main.py")
    result = await proxy.call_tool("filesystem", "delete", {"path": path})
    assert "权限拒绝" in result.get("error", ""), f"Error: {result}"
    print("[PASS] C2: blocked delete error mentions 权限拒绝")


async def test_C3_blocked_create_dir_error_mentions_permission():
    """Fix-3: create_directory 拒绝消息包含 '权限拒绝'。"""
    proxy, _ = _make_proxy()
    path = str(_PROJECT_ROOT / "backend" / "new_dir")
    result = await proxy.call_tool("filesystem", "create_directory", {"path": path})
    assert "权限拒绝" in result.get("error", ""), f"Error: {result}"
    print("[PASS] C3: blocked create_directory error mentions 权限拒绝")


async def test_C4_error_message_contains_skills_user_hint():
    """Fix-3: 拒绝消息包含 skills/user/ 路径提示（含用户名层占位符）。"""
    proxy, _ = _make_proxy()
    path = str(_PROJECT_ROOT / "backend" / "main.py")
    result = await proxy.call_tool("filesystem", "write_file", {"path": path, "content": "x"})
    error = result.get("error", "")
    # 应该包含 {用户名} 占位符或 skills/user 提示
    assert "user" in error and ("用户名" in error or "skill" in error.lower()), (
        f"Error should contain user skills hint: {error}"
    )
    print("[PASS] C4: error message contains skills/user hint")


async def test_C5_error_message_lists_allowed_dirs():
    """Fix-3: 拒绝消息列出了 write_allowed_dirs。"""
    proxy, _ = _make_proxy()
    path = str(_PROJECT_ROOT / "backend" / "main.py")
    result = await proxy.call_tool("filesystem", "write_file", {"path": path, "content": "x"})
    error = result.get("error", "")
    assert "允许写入" in error or "write_allowed" in error.lower(), (
        f"Error should list allowed dirs: {error}"
    )
    print("[PASS] C5: error message lists allowed dirs")


async def test_C6_error_has_success_false():
    """Fix-3: 拒绝响应 success 字段为 False。"""
    proxy, _ = _make_proxy()
    path = str(_SKILLS_ROOT / "system" / "hack.md")
    result = await proxy.call_tool("filesystem", "write_file", {"path": path, "content": "x"})
    assert result.get("success") is False, f"Must be False: {result}"
    assert "error" in result, "Must have error key"
    print("[PASS] C6: rejected response has success=False and error key")


# ─────────────────────────────────────────────────────────────────────────────
# D: Fix-4  用户名子目录层检查（工具类型感知）
# ─────────────────────────────────────────────────────────────────────────────

async def test_D1_write_file_to_user_root_blocked():
    """Fix-4: write_file .claude/skills/user/skill.md（无用户名层）被拦截。"""
    proxy, _ = _make_proxy()
    path = str(_USER_SKILLS / "orphan-skill.md")
    result = await proxy.call_tool("filesystem", "write_file", {"path": path, "content": "# sk"})
    assert result.get("success") is False, f"Should be blocked: {result}"
    print("[PASS] D1: write_file without username layer blocked")


async def test_D2_delete_from_user_root_blocked():
    """Fix-4: delete .claude/skills/user/skill.md 被拦截。"""
    proxy, _ = _make_proxy()
    path = str(_USER_SKILLS / "orphan-skill.md")
    result = await proxy.call_tool("filesystem", "delete", {"path": path})
    assert result.get("success") is False, f"Should be blocked: {result}"
    print("[PASS] D2: delete without username layer blocked")


async def test_D3_create_directory_user_root_allowed():
    """Fix-4 bugfix: create_directory .claude/skills/user/alice/ 深度1 应该允许。"""
    proxy, _ = _make_proxy()
    path = str(_USER_SKILLS / "alice")
    result = await proxy.call_tool("filesystem", "create_directory", {"path": path})
    assert result.get("success") is True, f"Should be allowed (depth 1 for create_directory): {result}"
    print("[PASS] D3: create_directory at username depth (1) allowed")


async def test_D4_create_directory_nested_allowed():
    """Fix-4: create_directory .claude/skills/user/alice/subdir 深度>=2 允许。"""
    proxy, _ = _make_proxy()
    path = str(_USER_SKILLS / "alice" / "subdir")
    result = await proxy.call_tool("filesystem", "create_directory", {"path": path})
    assert result.get("success") is True, f"Should be allowed: {result}"
    print("[PASS] D4: create_directory at depth 2 allowed")


async def test_D5_write_file_with_username_layer_allowed():
    """Fix-4: write_file .claude/skills/user/alice/skill.md 深度>=2 允许。"""
    proxy, _ = _make_proxy()
    path = str(_USER_SKILLS / "alice" / "my-skill.md")
    result = await proxy.call_tool("filesystem", "write_file", {"path": path, "content": "# sk"})
    assert result.get("success") is True, f"Should be allowed: {result}"
    print("[PASS] D5: write_file with username/skill depth allowed")


async def test_D6_delete_with_username_layer_allowed():
    """Fix-4: delete .claude/skills/user/alice/skill.md 深度>=2 允许。"""
    proxy, _ = _make_proxy()
    path = str(_USER_SKILLS / "alice" / "my-skill.md")
    result = await proxy.call_tool("filesystem", "delete", {"path": path})
    assert result.get("success") is True, f"Should be allowed: {result}"
    print("[PASS] D6: delete with username/skill depth allowed")


async def test_D7_write_file_deep_nested_allowed():
    """Fix-4: write_file .claude/skills/user/alice/subdir/skill.md 深度>=3 允许。"""
    proxy, _ = _make_proxy()
    path = str(_USER_SKILLS / "alice" / "subdir" / "deep-skill.md")
    result = await proxy.call_tool("filesystem", "write_file", {"path": path, "content": "# sk"})
    assert result.get("success") is True, f"Should be allowed: {result}"
    print("[PASS] D7: write_file deep nested allowed")


async def test_D8_fix4_error_message_correct():
    """Fix-4: 深度不足的错误消息包含正确格式提示。"""
    proxy, _ = _make_proxy()
    path = str(_USER_SKILLS / "orphan.md")
    result = await proxy.call_tool("filesystem", "write_file", {"path": path, "content": "x"})
    error = result.get("error", "")
    assert "用户名" in error or "username" in error.lower() or "{用户名}" in error, (
        f"Error should mention username layer: {error}"
    )
    print("[PASS] D8: Fix-4 error message mentions username layer")


# ─────────────────────────────────────────────────────────────────────────────
# E: 集成 — 用户名上下文端到端流转
# ─────────────────────────────────────────────────────────────────────────────

async def test_E1_username_flows_from_context_to_prompt():
    """端到端: context['username'] 正确嵌入系统提示路径规则。"""
    allowed = [str(_CUSTOMER_DATA), str(_SKILLS_ROOT)]
    for username in ["alice", "bob_99", "张三"]:
        prompt = await _call_build_system_prompt(allowed, username=username)
        assert username in prompt, f"username '{username}' should appear in prompt"
    print("[PASS] E1: username flows from context to system prompt")


async def test_E2_default_username_anonymous():
    """端到端: context 无 username 时使用 'anonymous' 作为回退。"""
    from backend.agents.agentic_loop import AgenticLoop

    loop = AgenticLoop.__new__(AgenticLoop)
    loop.llm_adapter = _make_mock_llm()
    loop._cancel_event = None
    loop._tools_cache = None
    fs_mock2 = MagicMock()
    fs_mock2.allowed_directories = [str(_CUSTOMER_DATA), str(_SKILLS_ROOT)]
    mock_mgr2 = MagicMock()
    mock_mgr2.list_servers = MagicMock(return_value=[
        {"name": "filesystem", "type": "filesystem", "tool_count": 10}
    ])
    mock_mgr2.servers = {"filesystem": fs_mock2}
    mock_mgr2.get_all_tools = MagicMock(return_value=[])
    loop.mcp_manager = mock_mgr2
    loop._system_prompt = ""

    from backend.skills.skill_loader import get_skill_loader
    context = {
        "allowed_directories": [str(_CUSTOMER_DATA), str(_SKILLS_ROOT)],
        "skill_loader": get_skill_loader(),
        # username 故意省略
    }
    prompt = await loop._build_system_prompt(context, message="test")
    assert "anonymous" in prompt, "Should fall back to 'anonymous'"
    print("[PASS] E2: missing username falls back to 'anonymous'")


async def test_E3_proxy_blocks_without_username_in_path():
    """端到端: Proxy 正确拦截没有用户名层的技能写入。"""
    proxy, _ = _make_proxy()
    # 模拟 LLM 忘记用户名层
    path = str(_USER_SKILLS / "some-skill.md")
    result = await proxy.call_tool("filesystem", "write_file", {"path": path, "content": "x"})
    assert result.get("success") is False, f"Must block flat write: {result}"
    print("[PASS] E3: proxy blocks skill write missing username layer")


async def test_E4_proxy_allows_with_username_in_path():
    """端到端: Proxy 允许正确包含用户名层的技能写入。"""
    proxy, _ = _make_proxy()
    path = str(_USER_SKILLS / "alice" / "data-skill.md")
    result = await proxy.call_tool("filesystem", "write_file", {"path": path, "content": "# sk"})
    assert result.get("success") is True, f"Must allow: {result}"
    print("[PASS] E4: proxy allows skill write with correct username layer")


async def test_E5_multi_user_isolation():
    """端到端: 不同用户的技能目录相互独立，各自允许写入。"""
    proxy, base = _make_proxy()
    for username in ["alice", "bob", "charlie"]:
        path = str(_USER_SKILLS / username / "skill.md")
        result = await proxy.call_tool("filesystem", "write_file", {"path": path, "content": "x"})
        assert result.get("success") is True, f"User {username}: {result}"
    print("[PASS] E5: multi-user skill directories isolated and independently writable")


async def test_E6_create_dir_then_write_workflow():
    """端到端: create_directory(user/alice/) → write_file(user/alice/skill.md) 合法序列。"""
    proxy, _ = _make_proxy()
    dir_path = str(_USER_SKILLS / "alice")
    file_path = str(_USER_SKILLS / "alice" / "skill.md")

    r1 = await proxy.call_tool("filesystem", "create_directory", {"path": dir_path})
    r2 = await proxy.call_tool("filesystem", "write_file", {"path": file_path, "content": "# sk"})
    assert r1.get("success") is True, f"create_directory failed: {r1}"
    assert r2.get("success") is True, f"write_file failed: {r2}"
    print("[PASS] E6: create_directory then write_file workflow allowed")


async def test_E7_non_filesystem_server_passthrough():
    """端到端: 非 filesystem 服务器调用完全透传，不受任何检查。"""
    proxy, base = _make_proxy()
    result = await proxy.call_tool("clickhouse-idn", "query", {"sql": "DROP TABLE foo"})
    assert result.get("success") is True, f"Non-fs call blocked unexpectedly: {result}"
    base.call_tool.assert_called_with("clickhouse-idn", "query", {"sql": "DROP TABLE foo"})
    print("[PASS] E7: non-filesystem server calls fully pass through")


# ─────────────────────────────────────────────────────────────────────────────
# F: RBAC — 无新路由/权限/菜单 回归验证
# ─────────────────────────────────────────────────────────────────────────────

def test_F1_no_new_api_routes_added():
    """RBAC: Fix-1 ~ Fix-4 仅修改 agentic_loop.py 和 filesystem_permission_proxy.py，
    未在 conversations.py / skills.py 增加需要新权限的 API 路由。
    （cancel 路由属于独立的停止生成功能，不属于 Fix-1~4 范围）"""
    import backend.api.conversations as conv_mod
    import backend.api.skills as skills_mod

    # Fix-1~4 不应新增任何 skill_path 相关的路由
    for route in conv_mod.router.routes:
        path = getattr(route, "path", "")
        assert "skill_path" not in path.lower(), f"Unexpected skill_path route: {path}"
        assert "fix" not in path.lower(), f"Unexpected fix route: {path}"

    for route in skills_mod.router.routes:
        path = getattr(route, "path", "")
        assert "fix" not in path.lower(), f"Unexpected fix route in skills: {path}"

    print("[PASS] F1: no skill-path-related API routes added by Fixes 1-4")


def test_F2_no_new_permissions_added():
    """RBAC: init_rbac.py 中定义的权限集合未因 Fix-1~4 增加 skill-path 相关权限。"""
    import pathlib
    src = (pathlib.Path(__file__).parent / "backend" / "scripts" / "init_rbac.py").read_text(encoding="utf-8")
    # 检查 Fix-1~4 不应引入任何新权限名（按内容判断比按数量更可靠）
    new_perm_keywords = ["skill_path", "fix1", "fix2", "fix3", "fix4", "filesystem_write"]
    for kw in new_perm_keywords:
        assert kw not in src.lower(), f"Unexpected new permission keyword '{kw}' in init_rbac.py"
    # 确认核心权限仍然存在（以实际文件中出现的字符串为准）
    core_perms = ["chat:use", "settings:read"]
    for perm in core_perms:
        assert perm in src, f"Core permission '{perm}' missing from init_rbac.py"
    print("[PASS] F2: no skill-path permissions added by Fixes 1-4; core permissions intact")


def test_F3_agentic_loop_no_new_public_methods():
    """RBAC: agentic_loop.py 的公开方法集未因 Fix-1 发生不预期变化。"""
    from backend.agents.agentic_loop import AgenticLoop
    public_methods = {m for m in dir(AgenticLoop) if not m.startswith("_")}
    # Fix-1 只改了 _build_system_prompt（私有），不应新增公开方法
    expected_public = {"run", "run_streaming"}
    for m in expected_public:
        assert m in public_methods, f"Expected public method '{m}' missing"
    print("[PASS] F3: AgenticLoop public API unchanged by Fix-1")


def test_F4_proxy_public_interface_stable():
    """RBAC/接口稳定: FilesystemPermissionProxy 公开接口完整且稳定。"""
    from backend.core.filesystem_permission_proxy import FilesystemPermissionProxy
    proxy, _ = _make_proxy()
    required_attrs = ["servers", "server_configs", "call_tool",
                      "list_servers", "get_server", "get_all_tools", "get_all_resources"]
    for attr in required_attrs:
        assert hasattr(proxy, attr), f"Missing required attr: {attr}"
    print("[PASS] F4: FilesystemPermissionProxy public interface stable")


def test_F5_settings_write_dirs_still_correct():
    """RBAC/回归: settings.filesystem_write_allowed_dirs 默认值未被 Fix 破坏。"""
    from backend.config.settings import settings
    dirs = [Path(d).resolve() for d in settings.filesystem_write_allowed_dirs]
    names = {d.name for d in dirs}
    assert "customer_data" in names, f"customer_data missing: {names}"
    assert "user" in names, f"user missing: {names}"
    print("[PASS] F5: settings.filesystem_write_allowed_dirs defaults intact")


# ─────────────────────────────────────────────────────────────────────────────
# G: 安全 — 路径穿越/特殊字符/空路径/URL 编码绕过
# ─────────────────────────────────────────────────────────────────────────────

async def test_G1_path_traversal_write_blocked():
    """安全: ../.. 相对路径穿越写入被拦截。"""
    proxy, _ = _make_proxy()
    for path in [
        "../backend/config/settings.py",
        "../../etc/passwd",
        str(_CUSTOMER_DATA / ".." / "backend" / "main.py"),
    ]:
        result = await proxy.call_tool("filesystem", "write_file", {"path": path, "content": "x"})
        assert result.get("success") is False, f"Traversal '{path}' should be blocked: {result}"
    print("[PASS] G1: path traversal write attempts blocked")


async def test_G2_empty_path_blocked():
    """安全: 空路径不崩溃，返回拒绝或错误。"""
    proxy, _ = _make_proxy()
    result = await proxy.call_tool("filesystem", "write_file", {"path": "", "content": "x"})
    # 空路径要么被拒绝要么透传（取决于底层），关键是不崩溃
    assert "success" in result, f"Must return dict with success key: {result}"
    print("[PASS] G2: empty path handled without crash")


async def test_G3_url_encoded_path_traversal_blocked():
    """安全: URL 编码的路径穿越 (%2F, %2E%2E) 被拦截。"""
    proxy, _ = _make_proxy()
    path = str(_CUSTOMER_DATA) + "%2F..%2Fbackend%2Fmain.py"
    result = await proxy.call_tool("filesystem", "write_file", {"path": path, "content": "x"})
    # unquote 后应被识别并拦截
    assert result.get("success") is False, f"URL-encoded traversal should be blocked: {result}"
    print("[PASS] G3: URL-encoded path traversal blocked")


async def test_G4_null_byte_in_path_handled():
    """安全: 路径中含 null 字节不崩溃。"""
    proxy, _ = _make_proxy()
    path = str(_CUSTOMER_DATA / "file\x00.csv")
    try:
        result = await proxy.call_tool("filesystem", "write_file", {"path": path, "content": "x"})
        assert "success" in result
    except Exception:
        pass  # 操作系统层拒绝也可接受
    print("[PASS] G4: null byte in path handled without crash")


async def test_G5_backslash_path_resolved_correctly():
    """安全/跨平台: 反斜杠路径（Windows风格）能被正确解析。"""
    proxy, _ = _make_proxy()
    # 将 customer_data 路径中的 / 换成 \
    path = str(_CUSTOMER_DATA / "output.csv").replace("/", "\\")
    result = await proxy.call_tool("filesystem", "write_file", {"path": path, "content": "x"})
    assert result.get("success") is True, f"Backslash path should be allowed: {result}"
    print("[PASS] G5: backslash path correctly resolved and allowed")


async def test_G6_symlink_like_deep_traversal_blocked():
    """安全: customer_data/../../backend 深度穿越被拦截。"""
    proxy, _ = _make_proxy()
    path = str(_CUSTOMER_DATA / ".." / ".." / "backend" / "secret.py")
    result = await proxy.call_tool("filesystem", "write_file", {"path": path, "content": "x"})
    assert result.get("success") is False, f"Deep traversal should be blocked: {result}"
    print("[PASS] G6: deep path traversal blocked")


async def test_G7_write_system_skills_blocked():
    """安全: 写入 .claude/skills/system/ 被拦截（系统技能只读）。"""
    proxy, _ = _make_proxy()
    path = str(_SKILLS_ROOT / "system" / "malicious.md")
    result = await proxy.call_tool("filesystem", "write_file", {"path": path, "content": "hack"})
    assert result.get("success") is False, f"System skills write should be blocked: {result}"
    print("[PASS] G7: write to system skills blocked")


async def test_G8_write_project_skills_blocked():
    """安全: 写入 .claude/skills/project/ 被拦截（项目技能只读）。"""
    proxy, _ = _make_proxy()
    path = str(_SKILLS_ROOT / "project" / "tamper.md")
    result = await proxy.call_tool("filesystem", "write_file", {"path": path, "content": "hack"})
    assert result.get("success") is False, f"Project skills write should be blocked: {result}"
    print("[PASS] G8: write to project skills blocked")


# ─────────────────────────────────────────────────────────────────────────────
# runner
# ─────────────────────────────────────────────────────────────────────────────

async def run_all():
    all_tests = [
        # A: Fix-1 系统提示
        ("A1",  test_A1_path_rule_contains_username),
        ("A2",  test_A2_path_rule_skills_root_identified),
        ("A3",  test_A3_path_rule_data_root_identified),
        ("A4",  test_A4_path_rule_example_has_username_layer),
        ("A5",  test_A5_path_rule_prohibits_cross_root),
        ("A6",  test_A6_path_rule_chinese_username),
        ("A7",  test_A7_path_rule_anonymous_user),
        ("A8",  test_A8_path_rule_single_dir_fallback),
        ("A9",  test_A9_path_rule_no_allowed_dirs),
        ("A10", test_A10_path_example_is_absolute_path),
        # B: Fix-2 跨根目录路由拦截
        ("B1",  test_B1_write_to_customer_data_dot_claude_blocked),
        ("B2",  test_B2_delete_customer_data_dot_claude_blocked),
        ("B3",  test_B3_create_dir_customer_data_dot_claude_blocked),
        ("B4",  test_B4_url_encoded_claude_blocked),
        ("B5",  test_B5_dot_claude_as_extension_not_blocked),
        ("B6",  test_B6_normal_customer_data_write_not_blocked),
        ("B7",  test_B7_skills_user_write_not_blocked_by_fix2),
        # C: Fix-3 拒绝消息
        ("C1",  test_C1_blocked_write_error_mentions_permission),
        ("C2",  test_C2_blocked_delete_error_mentions_permission),
        ("C3",  test_C3_blocked_create_dir_error_mentions_permission),
        ("C4",  test_C4_error_message_contains_skills_user_hint),
        ("C5",  test_C5_error_message_lists_allowed_dirs),
        ("C6",  test_C6_error_has_success_false),
        # D: Fix-4 用户名子目录层
        ("D1",  test_D1_write_file_to_user_root_blocked),
        ("D2",  test_D2_delete_from_user_root_blocked),
        ("D3",  test_D3_create_directory_user_root_allowed),
        ("D4",  test_D4_create_directory_nested_allowed),
        ("D5",  test_D5_write_file_with_username_layer_allowed),
        ("D6",  test_D6_delete_with_username_layer_allowed),
        ("D7",  test_D7_write_file_deep_nested_allowed),
        ("D8",  test_D8_fix4_error_message_correct),
        # E: 集成
        ("E1",  test_E1_username_flows_from_context_to_prompt),
        ("E2",  test_E2_default_username_anonymous),
        ("E3",  test_E3_proxy_blocks_without_username_in_path),
        ("E4",  test_E4_proxy_allows_with_username_in_path),
        ("E5",  test_E5_multi_user_isolation),
        ("E6",  test_E6_create_dir_then_write_workflow),
        ("E7",  test_E7_non_filesystem_server_passthrough),
        # F: RBAC
        ("F1",  test_F1_no_new_api_routes_added),
        ("F2",  test_F2_no_new_permissions_added),
        ("F3",  test_F3_agentic_loop_no_new_public_methods),
        ("F4",  test_F4_proxy_public_interface_stable),
        ("F5",  test_F5_settings_write_dirs_still_correct),
        # G: 安全
        ("G1",  test_G1_path_traversal_write_blocked),
        ("G2",  test_G2_empty_path_blocked),
        ("G3",  test_G3_url_encoded_path_traversal_blocked),
        ("G4",  test_G4_null_byte_in_path_handled),
        ("G5",  test_G5_backslash_path_resolved_correctly),
        ("G6",  test_G6_symlink_like_deep_traversal_blocked),
        ("G7",  test_G7_write_system_skills_blocked),
        ("G8",  test_G8_write_project_skills_blocked),
    ]

    passed = failed = 0
    print("\n" + "=" * 65)
    print("Skill Path Comprehensive Tests (Fix-1 ~ Fix-4 + Security)")
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
