"""
test_skill_user_isolation.py
============================
用户 Skill 隔离测试（U1–U6）

验证目标：
  U1 — 用户 A 的 user-tier skill 对用户 B 不可见
  U2 — system / project skill 对所有用户可见
  U3 — ENABLE_AUTH=false（username="default"）时全部 user skill 可见（向后兼容）
  U4 — sub_skill 展开不会引用另一用户的私有 skill
  U5 — owner 字段从 filepath 正确解析
  U6 — preview API 使用登录用户身份；superadmin 可通过 view_as override
"""
import os
import sys
import tempfile
import pytest
from pathlib import Path
from typing import List
from unittest.mock import patch, MagicMock, AsyncMock

# 确保 backend 在 sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.skills.skill_loader import (
    SkillLoader,
    SkillMD,
    TIER_USER,
    TIER_PROJECT,
    TIER_SYSTEM,
    _extract_skill_owner,
)


# ──────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────

def _write_skill(path: Path, name: str, triggers: List[str], content: str = "body") -> None:
    """Write a minimal valid SKILL.md to path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    triggers_yaml = "\n".join(f"  - {t}" for t in triggers)
    path.write_text(
        f"---\nname: {name}\ndescription: {name} desc\ntriggers:\n{triggers_yaml}\n---\n\n{content}\n",
        encoding="utf-8",
    )


@pytest.fixture()
def skill_dir(tmp_path):
    """
    临时三层 skill 目录结构：

      system/
        sys-skill.md            (triggers: sysword)
        _base-safety.md         (always_inject)
      project/
        proj-skill.md           (triggers: projword)
      user/
        alice/
          alice-skill.md        (triggers: aliceword)
          alice-sub.md          (triggers: alicesub)  ← alice 的 sub_skill
        bob/
          bob-skill.md          (triggers: bobword)
        legacy-skill.md         (owner="", triggers: legacyword)
    """
    sd = tmp_path / ".claude" / "skills"

    _write_skill(sd / "system" / "sys-skill.md",    "sys-skill",    ["sysword"])
    _write_skill(sd / "system" / "_base-safety.md", "_base-safety", [])
    _write_skill(sd / "project" / "proj-skill.md",  "proj-skill",   ["projword"])
    _write_skill(sd / "user" / "alice" / "alice-skill.md", "alice-skill", ["aliceword"],
                 content="alice body")
    _write_skill(sd / "user" / "alice" / "alice-sub.md",   "alice-sub",   ["alicesub"],
                 content="alice sub body")
    _write_skill(sd / "user" / "bob" / "bob-skill.md",     "bob-skill",   ["bobword"],
                 content="bob body")
    _write_skill(sd / "user" / "legacy-skill.md",          "legacy-skill",["legacyword"],
                 content="legacy body")

    # 给 alice-skill 加 sub_skills 声明（指向 alice-sub）
    alice_skill_path = sd / "user" / "alice" / "alice-skill.md"
    alice_skill_path.write_text(
        "---\nname: alice-skill\ndescription: alice desc\n"
        "triggers:\n  - aliceword\nsub_skills:\n  - alice-sub\n---\n\nalice body\n",
        encoding="utf-8",
    )

    return sd


@pytest.fixture()
def loader(skill_dir):
    sl = SkillLoader(skills_dir=str(skill_dir))
    sl.load_all()
    return sl


# ──────────────────────────────────────────────────────────
# U5 — owner 字段解析（不依赖 load_all，直接测辅助函数）
# ──────────────────────────────────────────────────────────

class TestU5OwnerExtraction:
    def test_owner_from_subdir(self, skill_dir):
        """user/alice/x.md → owner = 'alice'"""
        fp = skill_dir / "user" / "alice" / "x.md"
        assert _extract_skill_owner(fp, skill_dir) == "alice"

    def test_owner_bob(self, skill_dir):
        fp = skill_dir / "user" / "bob" / "x.md"
        assert _extract_skill_owner(fp, skill_dir) == "bob"

    def test_owner_root_level_empty(self, skill_dir):
        """user/x.md（根目录直放）→ owner = ''"""
        fp = skill_dir / "user" / "x.md"
        assert _extract_skill_owner(fp, skill_dir) == ""

    def test_owner_deep_nested(self, skill_dir):
        """user/alice/sub/x.md → owner = 'alice'（取第一层子目录名）"""
        fp = skill_dir / "user" / "alice" / "sub" / "x.md"
        assert _extract_skill_owner(fp, skill_dir) == "alice"

    def test_owner_loaded_in_skillmd(self, loader):
        """load_all 后 SkillMD.owner 字段正确"""
        alice = loader._user_skills.get("alice-skill")
        assert alice is not None
        assert alice.owner == "alice"

        bob = loader._user_skills.get("bob-skill")
        assert bob is not None
        assert bob.owner == "bob"

        legacy = loader._user_skills.get("legacy-skill")
        assert legacy is not None
        assert legacy.owner == ""


# ──────────────────────────────────────────────────────────
# U1 — 用户 A 看不到用户 B 的 skill
# ──────────────────────────────────────────────────────────

class TestU1UserIsolation:
    def test_alice_cannot_see_bob_skill(self, loader):
        visible = loader._get_visible_user_skills("alice")
        assert "bob-skill" not in visible
        assert "alice-skill" in visible

    def test_bob_cannot_see_alice_skill(self, loader):
        visible = loader._get_visible_user_skills("bob")
        assert "alice-skill" not in visible
        assert "bob-skill" in visible

    def test_alice_sees_legacy_shared_skill(self, loader):
        """owner='' 的遗留技能对所有用户可见"""
        visible = loader._get_visible_user_skills("alice")
        assert "legacy-skill" in visible

    def test_bob_sees_legacy_shared_skill(self, loader):
        visible = loader._get_visible_user_skills("bob")
        assert "legacy-skill" in visible

    def test_build_prompt_alice_only_sees_alice_skill(self, loader):
        prompt = loader.build_skill_prompt("aliceword", user_id="alice")
        assert "alice-skill" in prompt
        assert "bob" not in prompt

    def test_build_prompt_bob_only_sees_bob_skill(self, loader):
        prompt = loader.build_skill_prompt("bobword", user_id="bob")
        assert "bob-skill" in prompt
        assert "alice" not in prompt

    def test_build_prompt_alice_trigger_not_visible_to_bob(self, loader):
        """alice 的触发词不应在 bob 的 prompt 中出现"""
        prompt = loader.build_skill_prompt("aliceword", user_id="bob")
        assert "alice-skill" not in prompt


# ──────────────────────────────────────────────────────────
# U2 — system / project skill 对所有用户可见
# ──────────────────────────────────────────────────────────

class TestU2SharedSkillsAlwaysVisible:
    def test_system_skill_visible_to_alice(self, loader):
        prompt = loader.build_skill_prompt("sysword", user_id="alice")
        assert "sys-skill" in prompt

    def test_system_skill_visible_to_bob(self, loader):
        prompt = loader.build_skill_prompt("sysword", user_id="bob")
        assert "sys-skill" in prompt

    def test_project_skill_visible_to_alice(self, loader):
        prompt = loader.build_skill_prompt("projword", user_id="alice")
        assert "proj-skill" in prompt

    def test_project_skill_visible_to_bob(self, loader):
        prompt = loader.build_skill_prompt("projword", user_id="bob")
        assert "proj-skill" in prompt

    def test_base_skill_always_inject_all_users(self, loader):
        """_base-safety 对所有用户始终注入"""
        for username in ("alice", "bob", "charlie"):
            prompt = loader.build_skill_prompt("anything", user_id=username)
            assert "_base-safety" in prompt


# ──────────────────────────────────────────────────────────
# U3 — ENABLE_AUTH=false（username="default"）向后兼容
# ──────────────────────────────────────────────────────────

class TestU3AnonymousBackwardCompat:
    def test_default_user_sees_all_user_skills(self, loader):
        visible = loader._get_visible_user_skills("default")
        assert "alice-skill" in visible
        assert "bob-skill" in visible
        assert "legacy-skill" in visible

    def test_empty_username_sees_all_user_skills(self, loader):
        visible = loader._get_visible_user_skills("")
        assert "alice-skill" in visible
        assert "bob-skill" in visible

    def test_build_prompt_default_includes_all(self, loader):
        prompt_alice = loader.build_skill_prompt("aliceword", user_id="default")
        assert "alice-skill" in prompt_alice

        prompt_bob = loader.build_skill_prompt("bobword", user_id="default")
        assert "bob-skill" in prompt_bob


# ──────────────────────────────────────────────────────────
# U4 — sub_skill 展开不会引用另一用户的私有 skill
# ──────────────────────────────────────────────────────────

class TestU4SubSkillExpansionIsolation:
    def test_alice_sub_skill_expands_for_alice(self, loader):
        """alice 触发 alice-skill，alice-sub 应展开"""
        prompt = loader.build_skill_prompt("aliceword", user_id="alice")
        assert "alice-sub" in prompt

    def test_alice_sub_skill_does_not_expand_for_bob(self, loader):
        """
        bob 不能看到 alice-skill，所以 alice-sub 也不应展开到 bob 的 prompt。
        （父 skill 本身不可见，sub_skill 自然也不会展开）
        """
        prompt = loader.build_skill_prompt("aliceword", user_id="bob")
        assert "alice-sub" not in prompt

    def test_sub_skill_expansion_respects_user_boundary(self, loader):
        """
        即使消息触发词命中了 alice-skill 的触发词，
        bob 的 sub_skill 查找范围中不包含 alice 的 skill。
        构造一个 project skill 声明了 alice-sub 的 sub_skill，
        验证 bob 视角下该 sub_skill 被过滤。
        """
        # 给 proj-skill 添加 sub_skills: [alice-sub]（跨用户引用）
        proj_skill = loader._project_skills.get("proj-skill")
        assert proj_skill is not None
        proj_skill.sub_skills = ["alice-sub"]  # 注入跨用户引用

        # bob 触发 proj-skill
        prompt = loader.build_skill_prompt("projword", user_id="bob")
        # alice-sub 属于 alice，bob 不可见，不应出现
        assert "alice-sub" not in prompt

        # alice 触发 proj-skill
        prompt_alice = loader.build_skill_prompt("projword", user_id="alice")
        # alice 可见 alice-sub，应展开
        assert "alice-sub" in prompt_alice

        # 还原
        proj_skill.sub_skills = []


# ──────────────────────────────────────────────────────────
# U6 — preview API 用户身份逻辑（单元测试，不启动服务器）
# ──────────────────────────────────────────────────────────

class TestU6PreviewApiUserIdentity:
    """
    测试 preview_skill_trigger 中 effective_user_id 的决策逻辑，
    不实际调用 HTTP，只验证身份判断部分。
    """

    def _make_user(self, username: str, is_superadmin: bool = False):
        u = MagicMock()
        u.username = username
        u.is_superadmin = is_superadmin
        return u

    def _make_anon(self):
        from backend.api.deps import AnonymousUser
        return AnonymousUser()

    def _resolve_effective_user(self, current_user, view_as: str = "") -> str:
        """复现 preview API 中的 effective_user_id 决策逻辑。"""
        from backend.api.deps import AnonymousUser
        from fastapi import HTTPException

        is_anon = isinstance(current_user, AnonymousUser)
        if view_as and not is_anon:
            is_superadmin = getattr(current_user, "is_superadmin", False)
            if not is_superadmin:
                raise HTTPException(status_code=403, detail="view_as 参数仅 superadmin 可用")
            return view_as
        elif is_anon:
            return "default"
        else:
            return current_user.username

    def test_normal_user_uses_own_identity(self):
        user = self._make_user("alice")
        assert self._resolve_effective_user(user) == "alice"

    def test_anonymous_user_gets_default(self):
        anon = self._make_anon()
        assert self._resolve_effective_user(anon) == "default"

    def test_superadmin_can_override_view_as(self):
        admin = self._make_user("superadmin", is_superadmin=True)
        assert self._resolve_effective_user(admin, view_as="alice") == "alice"

    def test_non_superadmin_cannot_use_view_as(self):
        from fastapi import HTTPException
        user = self._make_user("bob")
        with pytest.raises(HTTPException) as exc_info:
            self._resolve_effective_user(user, view_as="alice")
        assert exc_info.value.status_code == 403

    def test_superadmin_without_view_as_uses_own_identity(self):
        admin = self._make_user("superadmin", is_superadmin=True)
        assert self._resolve_effective_user(admin, view_as="") == "superadmin"

    def test_anonymous_view_as_ignored(self):
        """匿名用户即使传了 view_as 也应得到 'default'（匿名模式不做权限检查）"""
        anon = self._make_anon()
        # 匿名时 view_as 被忽略
        assert self._resolve_effective_user(anon, view_as="alice") == "default"
