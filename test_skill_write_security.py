"""
test_skill_write_security.py
=============================
针对以下修复的完整测试套件：
  1. _base-safety.md  — 双写入区规则（数据文件→customer_data/ / 技能文件→.claude/skills/user/）
  2. skill-creator.md — Step 4 改为 filesystem__write_file，不再伪造 REST API 调用
  3. FilesystemPermissionProxy — 错误消息更新为双写入区说明

测试分节：
  Section A (5): FilesystemPermissionProxy 错误消息内容验证
  Section B (6): _base-safety.md 内容完整性验证
  Section C (6): skill-creator.md 内容完整性验证
  Section D (5): FilesystemPermissionProxy 技能路径路由验证
  Section E (4): SkillLoader 读取路径验证
  Section F (3): REST API 写入路径验证（skills.py 直写 .claude/skills/user/）
  Section G (3): 错误场景：customer_data/.claude/skills/user/ 不会被 SkillLoader 发现
"""

from __future__ import annotations
import asyncio
import os
import sys
import re
import tempfile
import shutil
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "backend"))

# ── 项目路径常量 ─────────────────────────────────────────────────────────────

_PROJECT_ROOT  = Path(__file__).parent.resolve()
_CUSTOMER_DATA = _PROJECT_ROOT / "customer_data"
_SKILLS_ROOT   = _PROJECT_ROOT / ".claude" / "skills"
_SYSTEM_DIR    = _SKILLS_ROOT / "system"
_PROJECT_DIR   = _SKILLS_ROOT / "project"
_USER_DIR      = _SKILLS_ROOT / "user"

_BASE_SAFETY_PATH   = _SYSTEM_DIR / "_base-safety.md"
_SKILL_CREATOR_PATH = _SYSTEM_DIR / "skill-creator.md"


# ── 辅助函数 ─────────────────────────────────────────────────────────────────

def _make_proxy(write_allowed_dirs=None, read_allowed_dirs=None):
    """创建 FilesystemPermissionProxy（base 为 AsyncMock）。"""
    from backend.core.filesystem_permission_proxy import FilesystemPermissionProxy

    if write_allowed_dirs is None:
        write_allowed_dirs = [str(_CUSTOMER_DATA), str(_USER_DIR)]
    if read_allowed_dirs is None:
        read_allowed_dirs = [str(_CUSTOMER_DATA), str(_SKILLS_ROOT)]

    base = AsyncMock()
    base.servers = {"filesystem": MagicMock()}
    base.server_configs = {}
    base._allowed = frozenset({"filesystem"})
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


def _read_skill_file(path: Path) -> str:
    assert path.exists(), f"Skill file not found: {path}"
    return path.read_text(encoding="utf-8")


# ════════════════════════════════════════════════════════════════════════
# Section A: FilesystemPermissionProxy 错误消息内容验证
# ════════════════════════════════════════════════════════════════════════

async def test_A1_error_msg_mentions_customer_data():
    """被拦截时，错误消息应提示数据文件写入 customer_data/。"""
    proxy, _ = _make_proxy()
    result = await proxy.call_tool(
        "filesystem", "write_file",
        {"path": str(_PROJECT_ROOT / "backend" / "config" / "settings.py"), "content": "x"}
    )
    assert result.get("success") is False
    err = result.get("error", "")
    assert "customer_data" in err, f"Error should mention customer_data, got: {err}"
    print("[PASS] A1: error msg mentions customer_data/ for data files")


async def test_A2_error_msg_mentions_skills_user():
    """被拦截时，错误消息应提示技能文件写入 .claude/skills/user/。"""
    proxy, _ = _make_proxy()
    result = await proxy.call_tool(
        "filesystem", "write_file",
        {"path": str(_PROJECT_ROOT / "backend" / "main.py"), "content": "x"}
    )
    assert result.get("success") is False
    err = result.get("error", "")
    assert ".claude/skills/user/" in err, (
        f"Error should mention .claude/skills/user/, got: {err}"
    )
    print("[PASS] A2: error msg mentions .claude/skills/user/ for skill files")


async def test_A3_error_msg_no_rest_api_guidance():
    """错误消息不应再出现旧的 POST /api/v1/skills/user-defined 引导（已废除）。"""
    proxy, _ = _make_proxy()
    result = await proxy.call_tool(
        "filesystem", "write_file",
        {"path": str(_PROJECT_ROOT / "frontend" / "index.html"), "content": "x"}
    )
    assert result.get("success") is False
    err = result.get("error", "")
    assert "POST /api/v1/skills/user-defined" not in err, (
        f"Error should NOT mention old REST API endpoint, got: {err}"
    )
    print("[PASS] A3: error msg does NOT contain obsolete REST API guidance")


async def test_A4_error_msg_mentions_system_project_readonly():
    """错误消息应提示 system/ 和 project/ 是只读目录。"""
    proxy, _ = _make_proxy()
    result = await proxy.call_tool(
        "filesystem", "write_file",
        {"path": str(_SYSTEM_DIR / "evil.md"), "content": "x"}
    )
    assert result.get("success") is False
    err = result.get("error", "")
    # Should mention system or project is readonly
    has_system_ref = "system" in err.lower() or "project" in err.lower() or "只读" in err
    assert has_system_ref, f"Error should reference system/project readonly, got: {err}"
    print("[PASS] A4: error msg mentions system/project dirs are readonly")


async def test_A5_error_msg_mentions_source_code_blocked():
    """错误消息应提示源代码目录不可写。"""
    proxy, _ = _make_proxy()
    result = await proxy.call_tool(
        "filesystem", "write_file",
        {"path": str(_PROJECT_ROOT / "backend" / "api" / "skills.py"), "content": "x"}
    )
    assert result.get("success") is False
    err = result.get("error", "")
    assert "backend" in err or "frontend" in err or "源代码" in err, (
        f"Error should reference source code dirs, got: {err}"
    )
    print("[PASS] A5: error msg mentions source code dirs are blocked")


# ════════════════════════════════════════════════════════════════════════
# Section B: _base-safety.md 内容完整性验证
# ════════════════════════════════════════════════════════════════════════

def test_B1_base_safety_file_exists_and_parseable():
    """_base-safety.md 存在且能被 SkillLoader 正常解析。"""
    assert _BASE_SAFETY_PATH.exists(), f"File not found: {_BASE_SAFETY_PATH}"
    from backend.skills.skill_loader import SkillLoader
    loader = SkillLoader()
    loader.load_all()
    skills = loader.get_all()
    names = [s.name for s in skills]
    assert "_base-safety" in names, f"_base-safety not loaded, loaded: {names}"
    print("[PASS] B1: _base-safety.md exists and parsed by SkillLoader")


def test_B2_base_safety_always_inject():
    """_base-safety.md 的 always_inject 必须为 True。"""
    from backend.skills.skill_loader import SkillLoader
    loader = SkillLoader()
    loader.load_all()
    skill = loader._system_skills.get("_base-safety")
    assert skill is not None, "_base-safety skill not in system_skills"
    assert skill.always_inject is True, "always_inject must be True"
    print("[PASS] B2: _base-safety.md has always_inject=True")


def test_B3_base_safety_has_dual_write_zones():
    """_base-safety.md 内容应描述两个写入区（双写入区表格或明确说明）。"""
    content = _read_skill_file(_BASE_SAFETY_PATH)
    # Should mention both write zones
    has_customer_data = "customer_data" in content
    has_skills_user = ".claude/skills/user" in content
    assert has_customer_data, "_base-safety.md should mention customer_data/"
    assert has_skills_user, "_base-safety.md should mention .claude/skills/user/"
    print("[PASS] B3: _base-safety.md describes both write zones")


def test_B4_base_safety_forbids_system_project_dirs():
    """_base-safety.md 必须明确禁止写 system/ 和 project/ 目录。"""
    content = _read_skill_file(_BASE_SAFETY_PATH)
    assert "skills/system" in content or "system/" in content, (
        "_base-safety.md should forbid .claude/skills/system/"
    )
    assert "skills/project" in content or "project/" in content, (
        "_base-safety.md should forbid .claude/skills/project/"
    )
    print("[PASS] B4: _base-safety.md forbids system/ and project/ directories")


def test_B5_base_safety_forbids_source_code_dirs():
    """_base-safety.md 必须明确禁止写 backend/ 和 frontend/ 目录。"""
    content = _read_skill_file(_BASE_SAFETY_PATH)
    assert "backend" in content or "frontend" in content or "源代码" in content, (
        "_base-safety.md should forbid project source code dirs (backend/frontend)"
    )
    print("[PASS] B5: _base-safety.md forbids source code directories")


def test_B6_base_safety_no_customer_data_only_rule():
    """_base-safety.md 不应再有 '仅允许将文件写入 customer_data/' 的单一限制。"""
    content = _read_skill_file(_BASE_SAFETY_PATH)
    # Old rule was: 仅允许将文件写入 `customer_data/` 目录或经管理员明确授权的目录
    assert "仅允许将文件写入 `customer_data/`" not in content, (
        "_base-safety.md should not have old single-dir-only rule; it now has dual zones"
    )
    print("[PASS] B6: _base-safety.md does not have old single-directory-only restriction")


# ════════════════════════════════════════════════════════════════════════
# Section C: skill-creator.md 内容完整性验证
# ════════════════════════════════════════════════════════════════════════

def test_C1_skill_creator_file_exists_and_parseable():
    """skill-creator.md 存在且能被 SkillLoader 正常解析。"""
    assert _SKILL_CREATOR_PATH.exists(), f"File not found: {_SKILL_CREATOR_PATH}"
    from backend.skills.skill_loader import SkillLoader
    loader = SkillLoader()
    loader.load_all()
    skill = loader._system_skills.get("skill-creator")
    assert skill is not None, "skill-creator not loaded by SkillLoader"
    print("[PASS] C1: skill-creator.md exists and parsed by SkillLoader")


def test_C2_skill_creator_step4_uses_filesystem_write():
    """skill-creator.md Step 4 必须使用 filesystem__write_file 工具，不再伪造 REST 调用。"""
    content = _read_skill_file(_SKILL_CREATOR_PATH)
    assert "filesystem__write_file" in content, (
        "skill-creator.md Step 4 should instruct to use filesystem__write_file tool"
    )
    print("[PASS] C2: skill-creator.md Step 4 uses filesystem__write_file")


def test_C3_skill_creator_step4_correct_path():
    """skill-creator.md Step 4 写入路径必须是 .claude/skills/user/{skill-name}.md。"""
    content = _read_skill_file(_SKILL_CREATOR_PATH)
    assert ".claude/skills/user/" in content, (
        "skill-creator.md Step 4 path should be .claude/skills/user/"
    )
    # Should contain the path pattern
    assert "{skill-name}.md" in content or "skill-name}.md" in content or (
        "skills/user/" in content and ".md" in content
    ), "skill-creator.md should show the .md path pattern"
    print("[PASS] C3: skill-creator.md Step 4 specifies correct write path")


def test_C4_skill_creator_no_old_rest_api_in_step4():
    """skill-creator.md Step 4 不应包含旧的 POST /api/v1/skills/user-defined 指令。"""
    content = _read_skill_file(_SKILL_CREATOR_PATH)
    # Find Step 4 section
    step4_match = re.search(r'### 步骤 4[^\n]*\n(.*?)(?=### 步骤 5|## )', content, re.DOTALL)
    if step4_match:
        step4_content = step4_match.group(1)
        # Step 4 should NOT tell agent to call POST /api/v1/skills/user-defined as action
        # (It may appear in Step 5 context for the promotion endpoint, that's OK)
        assert "POST /api/v1/skills/user-defined" not in step4_content, (
            "Step 4 should not instruct to POST to skills/user-defined API; "
            "agent should use filesystem__write_file instead"
        )
    else:
        # If step 4 pattern not found, just check filesystem__write_file is mentioned
        assert "filesystem__write_file" in content, (
            "skill-creator.md should use filesystem__write_file for skill creation"
        )
    print("[PASS] C4: skill-creator.md Step 4 does not contain obsolete REST API call")


def test_C5_skill_creator_warns_not_customer_data():
    """skill-creator.md 必须明确警告技能文件不要写入 customer_data/。"""
    content = _read_skill_file(_SKILL_CREATOR_PATH)
    # Should contain warning about not writing to customer_data
    has_warning = (
        "禁止写入 `customer_data/`" in content
        or "不要写入 customer_data" in content
        or ("customer_data" in content and "禁止" in content)
        or ("customer_data" in content and ("只属于" in content or "才写" in content))
    )
    assert has_warning, (
        "skill-creator.md should warn that skill files go to .claude/skills/user/, NOT customer_data/"
    )
    print("[PASS] C5: skill-creator.md warns not to write skills to customer_data/")


def test_C6_skill_creator_has_step5_promotion():
    """skill-creator.md 必须包含第5步（申请提升为项目技能）。"""
    content = _read_skill_file(_SKILL_CREATOR_PATH)
    has_step5 = "步骤 5" in content or "申请提升" in content or "project-skills" in content
    assert has_step5, (
        "skill-creator.md should have Step 5 for promoting skill to project level"
    )
    print("[PASS] C6: skill-creator.md includes Step 5 (skill promotion to project level)")


# ════════════════════════════════════════════════════════════════════════
# Section D: FilesystemPermissionProxy 技能路径路由验证
# ════════════════════════════════════════════════════════════════════════

async def test_D1_user_skill_path_allowed():
    """.claude/skills/user/skill.md 写入应被允许（正确技能路径）。"""
    proxy, _ = _make_proxy()
    path = str(_USER_DIR / "test-skill.md")
    result = await proxy.call_tool("filesystem", "write_file",
                                   {"path": path, "content": "---\nname: test\n---"})
    assert result.get("success") is True, (
        f".claude/skills/user/ write should be ALLOWED, got: {result}"
    )
    print("[PASS] D1: .claude/skills/user/{skill}.md write is ALLOWED")


async def test_D2_project_skill_path_blocked():
    """.claude/skills/project/skill.md 直接写入应被拦截（通过 admin API 维护）。"""
    proxy, _ = _make_proxy()
    path = str(_PROJECT_DIR / "team-skill.md")
    result = await proxy.call_tool("filesystem", "write_file",
                                   {"path": path, "content": "---\nname: team\n---"})
    assert result.get("success") is False, (
        f".claude/skills/project/ write should be BLOCKED, got: {result}"
    )
    print("[PASS] D2: .claude/skills/project/{skill}.md write is BLOCKED")


async def test_D3_system_skill_path_blocked():
    """.claude/skills/system/skill.md 直接写入应被拦截。"""
    proxy, _ = _make_proxy()
    path = str(_SYSTEM_DIR / "override-safety.md")
    result = await proxy.call_tool("filesystem", "write_file",
                                   {"path": path, "content": "---\nname: override\n---"})
    assert result.get("success") is False, (
        f".claude/skills/system/ write should be BLOCKED, got: {result}"
    )
    print("[PASS] D3: .claude/skills/system/{skill}.md write is BLOCKED")


async def test_D4_customer_data_subdir_allowed_by_proxy():
    """customer_data/.claude/skills/user/skill.md 在 proxy 层是允许的（在 customer_data 内），
    但 SkillLoader 不会从这里读取技能。代理层允许，但这是错误用法。"""
    proxy, _ = _make_proxy()
    # customer_data/ 是 write_allowed，所以其子目录也是允许的
    wrong_path = str(_CUSTOMER_DATA / ".claude" / "skills" / "user" / "misplaced.md")
    result = await proxy.call_tool("filesystem", "write_file",
                                   {"path": wrong_path, "content": "---\nname: misplaced\n---"})
    # 代理层：允许（在 customer_data/ 内），但逻辑上这是错误路径
    # 正确路径应是 .claude/skills/user/
    assert result.get("success") is True, (
        "customer_data/.claude/skills/user/ is inside customer_data/ so proxy allows it; "
        "but _base-safety.md rule prevents Agent from writing here"
    )
    print("[PASS] D4: customer_data/.claude/skills/user/ is proxy-allowed (in customer_data/)")
    print("       NOTE: _base-safety.md rule is the guard preventing Agent from using this wrong path")


async def test_D5_skills_root_direct_write_blocked():
    """.claude/skills/ 根目录直接写入技能文件应被拦截。"""
    proxy, _ = _make_proxy()
    path = str(_SKILLS_ROOT / "new-skill.md")
    result = await proxy.call_tool("filesystem", "write_file",
                                   {"path": path, "content": "---\nname: root\n---"})
    assert result.get("success") is False, (
        f".claude/skills/ root write should be BLOCKED (only user/ subdir is writable), got: {result}"
    )
    print("[PASS] D5: .claude/skills/ root directory write is BLOCKED")


# ════════════════════════════════════════════════════════════════════════
# Section E: SkillLoader 读取路径验证
# ════════════════════════════════════════════════════════════════════════

def test_E1_skill_loader_reads_from_user_dir():
    """SkillLoader 从 .claude/skills/user/ 加载技能（不是 customer_data/ 下的任何子目录）。"""
    from backend.skills.skill_loader import SkillLoader
    loader = SkillLoader()
    # Check the user_dir path
    user_dir = loader.skills_dir / "user"
    assert "customer_data" not in str(user_dir), (
        f"SkillLoader user_dir should NOT be inside customer_data/, got: {user_dir}"
    )
    assert str(_USER_DIR.resolve()) == str(user_dir.resolve()), (
        f"SkillLoader user_dir should be {_USER_DIR}, got {user_dir}"
    )
    print("[PASS] E1: SkillLoader reads user skills from .claude/skills/user/")


def test_E2_skill_loader_skills_dir_not_in_customer_data():
    """SkillLoader.skills_dir 不在 customer_data/ 内部。"""
    from backend.skills.skill_loader import SkillLoader
    loader = SkillLoader()
    skills_dir = loader.skills_dir.resolve()
    customer_data = _CUSTOMER_DATA.resolve()
    try:
        skills_dir.relative_to(customer_data)
        assert False, (
            f"SkillLoader.skills_dir should NOT be inside customer_data/, got: {skills_dir}"
        )
    except ValueError:
        pass  # 正确：skills_dir 不在 customer_data/ 内
    print("[PASS] E2: SkillLoader.skills_dir is NOT inside customer_data/")


def test_E3_skill_in_customer_data_not_loaded():
    """放在 customer_data/.claude/skills/user/ 的技能文件不会被 SkillLoader 加载。"""
    # 在 customer_data 里创建一个技能文件
    wrong_dir = _CUSTOMER_DATA / ".claude" / "skills" / "user"
    wrong_dir.mkdir(parents=True, exist_ok=True)
    wrong_file = wrong_dir / "should-not-load.md"
    wrong_file.write_text(
        "---\nname: should-not-load\nversion: \"1.0\"\n"
        "description: This should not be loaded\ntriggers:\n  - should-not-load\n"
        "category: general\npriority: low\n---\n\n# Should not load\n",
        encoding="utf-8"
    )
    try:
        from backend.skills.skill_loader import SkillLoader
        loader = SkillLoader()
        loader.load_all()
        names = [s.name for s in loader.get_all()]
        assert "should-not-load" not in names, (
            f"Skill in customer_data/ should NOT be loaded, but found in: {names}"
        )
    finally:
        wrong_file.unlink(missing_ok=True)
        # Clean up empty dirs
        try:
            wrong_dir.rmdir()
            wrong_dir.parent.rmdir()
            wrong_dir.parent.parent.rmdir()
        except OSError:
            pass
    print("[PASS] E3: skill file in customer_data/.claude/skills/user/ is NOT loaded by SkillLoader")


def test_E4_skill_in_user_dir_is_loaded():
    """放在 .claude/skills/user/ 的技能文件会被 SkillLoader 正常加载。"""
    test_file = _USER_DIR / "test-e4-load.md"
    _USER_DIR.mkdir(parents=True, exist_ok=True)
    test_file.write_text(
        "---\nname: test-e4-load\nversion: \"1.0\"\n"
        "description: Test E4 skill load\ntriggers:\n  - test-e4\n"
        "category: general\npriority: low\n---\n\n# Test E4\n",
        encoding="utf-8"
    )
    try:
        from backend.skills.skill_loader import SkillLoader
        loader = SkillLoader()
        loader.load_all()
        names = [s.name for s in loader.get_all()]
        assert "test-e4-load" in names, (
            f"Skill in .claude/skills/user/ should be loaded, loaded skills: {names}"
        )
    finally:
        test_file.unlink(missing_ok=True)
    print("[PASS] E4: skill file in .claude/skills/user/ IS loaded by SkillLoader")


# ════════════════════════════════════════════════════════════════════════
# Section F: REST API 写入路径验证
# ════════════════════════════════════════════════════════════════════════

def test_F1_skills_api_user_dir_is_correct():
    """backend/api/skills.py 的 _USER_SKILLS_DIR 指向 .claude/skills/user/（不是 customer_data/）。"""
    from backend.api.skills import _USER_SKILLS_DIR
    resolved = Path(_USER_SKILLS_DIR).resolve()
    customer_data_resolved = _CUSTOMER_DATA.resolve()

    # Should NOT be in customer_data/
    try:
        resolved.relative_to(customer_data_resolved)
        assert False, f"_USER_SKILLS_DIR should NOT be inside customer_data/, got: {resolved}"
    except ValueError:
        pass  # correct

    # Should be .claude/skills/user/
    assert resolved == _USER_DIR.resolve(), (
        f"_USER_SKILLS_DIR should be {_USER_DIR}, got {resolved}"
    )
    print("[PASS] F1: skills.py _USER_SKILLS_DIR points to .claude/skills/user/ (not customer_data/)")


def test_F2_skills_api_project_dir_is_correct():
    """backend/api/skills.py 的 _PROJECT_SKILLS_DIR 指向 .claude/skills/project/。"""
    from backend.api.skills import _PROJECT_SKILLS_DIR
    resolved = Path(_PROJECT_SKILLS_DIR).resolve()
    assert resolved == _PROJECT_DIR.resolve(), (
        f"_PROJECT_SKILLS_DIR should be {_PROJECT_DIR}, got {resolved}"
    )
    print("[PASS] F2: skills.py _PROJECT_SKILLS_DIR points to .claude/skills/project/")


def test_F3_skills_api_skills_root_not_in_customer_data():
    """backend/api/skills.py 的 _SKILLS_ROOT 不在 customer_data/ 内。"""
    from backend.api.skills import _SKILLS_ROOT as API_SKILLS_ROOT
    resolved = Path(API_SKILLS_ROOT).resolve()
    customer_data_resolved = _CUSTOMER_DATA.resolve()
    try:
        resolved.relative_to(customer_data_resolved)
        assert False, f"_SKILLS_ROOT should NOT be inside customer_data/, got: {resolved}"
    except ValueError:
        pass
    print("[PASS] F3: skills.py _SKILLS_ROOT is NOT inside customer_data/")


# ════════════════════════════════════════════════════════════════════════
# Section G: 错误场景——customer_data 内的技能路径不被发现
# ════════════════════════════════════════════════════════════════════════

def test_G1_base_safety_triggers_in_every_message():
    """_base-safety.md 的 always_inject=True 确保规则对每条消息都生效。"""
    from backend.skills.skill_loader import SkillLoader
    loader = SkillLoader()
    loader.load_all()
    # Build a skill prompt with a totally unrelated message
    prompt = loader.build_skill_prompt("帮我写一首诗")
    # _base-safety content should be injected regardless of message
    assert "customer_data" in prompt or "写入" in prompt or "安全" in prompt, (
        "_base-safety should always be injected into prompt"
    )
    print("[PASS] G1: _base-safety.md always injected (always_inject=True) for every message")


def test_G2_skill_creator_triggers_on_create_keywords():
    """skill-creator.md 在用户说「创建技能」时应被触发。"""
    from backend.skills.skill_loader import SkillLoader
    loader = SkillLoader()
    loader.load_all()
    skill = loader._system_skills.get("skill-creator")
    assert skill is not None, "skill-creator not loaded"
    assert skill.matches("我想创建技能，帮助我分析数据"), (
        "skill-creator should match '创建技能' trigger"
    )
    assert skill.matches("新建技能 for etl"), (
        "skill-creator should match '新建技能' trigger"
    )
    print("[PASS] G2: skill-creator.md triggers on '创建技能' / '新建技能' keywords")


def test_G3_skill_creator_has_filesystem_write_instruction():
    """skill-creator.md 的内容中，Agent 被明确指示使用 filesystem__write_file 而非 HTTP API。"""
    content = _read_skill_file(_SKILL_CREATOR_PATH)
    # Count references: filesystem__write_file should appear, REST POST for create should not be in Step 4
    fs_count = content.count("filesystem__write_file")
    assert fs_count >= 1, (
        f"skill-creator.md should mention filesystem__write_file at least once, found {fs_count} times"
    )
    # The specific path instruction should be present
    assert ".claude/skills/user/" in content, (
        "skill-creator.md should specify the .claude/skills/user/ path"
    )
    print("[PASS] G3: skill-creator.md clearly instructs filesystem__write_file to .claude/skills/user/")


# ════════════════════════════════════════════════════════════════════════
# Runner
# ════════════════════════════════════════════════════════════════════════

async def run_all():
    all_tests = [
        # Section A: Error message content
        ("A1",  test_A1_error_msg_mentions_customer_data),
        ("A2",  test_A2_error_msg_mentions_skills_user),
        ("A3",  test_A3_error_msg_no_rest_api_guidance),
        ("A4",  test_A4_error_msg_mentions_system_project_readonly),
        ("A5",  test_A5_error_msg_mentions_source_code_blocked),
        # Section B: _base-safety.md content
        ("B1",  test_B1_base_safety_file_exists_and_parseable),
        ("B2",  test_B2_base_safety_always_inject),
        ("B3",  test_B3_base_safety_has_dual_write_zones),
        ("B4",  test_B4_base_safety_forbids_system_project_dirs),
        ("B5",  test_B5_base_safety_forbids_source_code_dirs),
        ("B6",  test_B6_base_safety_no_customer_data_only_rule),
        # Section C: skill-creator.md content
        ("C1",  test_C1_skill_creator_file_exists_and_parseable),
        ("C2",  test_C2_skill_creator_step4_uses_filesystem_write),
        ("C3",  test_C3_skill_creator_step4_correct_path),
        ("C4",  test_C4_skill_creator_no_old_rest_api_in_step4),
        ("C5",  test_C5_skill_creator_warns_not_customer_data),
        ("C6",  test_C6_skill_creator_has_step5_promotion),
        # Section D: Proxy routing
        ("D1",  test_D1_user_skill_path_allowed),
        ("D2",  test_D2_project_skill_path_blocked),
        ("D3",  test_D3_system_skill_path_blocked),
        ("D4",  test_D4_customer_data_subdir_allowed_by_proxy),
        ("D5",  test_D5_skills_root_direct_write_blocked),
        # Section E: SkillLoader paths
        ("E1",  test_E1_skill_loader_reads_from_user_dir),
        ("E2",  test_E2_skill_loader_skills_dir_not_in_customer_data),
        ("E3",  test_E3_skill_in_customer_data_not_loaded),
        ("E4",  test_E4_skill_in_user_dir_is_loaded),
        # Section F: REST API paths
        ("F1",  test_F1_skills_api_user_dir_is_correct),
        ("F2",  test_F2_skills_api_project_dir_is_correct),
        ("F3",  test_F3_skills_api_skills_root_not_in_customer_data),
        # Section G: Error scenarios
        ("G1",  test_G1_base_safety_triggers_in_every_message),
        ("G2",  test_G2_skill_creator_triggers_on_create_keywords),
        ("G3",  test_G3_skill_creator_has_filesystem_write_instruction),
    ]

    passed = failed = 0
    print("\n" + "=" * 70)
    print("Skill Write Security Tests — dual write zones + skill-creator fix")
    print("=" * 70)

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

    print(f"\n{'=' * 70}")
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
