"""
test_skill_prompt_isolation_e2e.py
===================================
Skill 用户隔离完整 E2E 测试 (Sections I–K)

针对 T1-T6 skill 用户隔离新功能的完整测试，覆盖 HTTP 端点、
Python 集成层，以及权限矩阵。

Section I (8) — Preview API 用户隔离（HTTP 级别）
  I1: 普通用户 preview → triggered.user 仅含自己的 skill
  I2: 普通用户 preview → triggered.user 不含他人 skill
  I3: preview match_details 不泄露其他用户的 skill 名称（安全关键）← BUG 覆盖
  I4: 非 superadmin 使用 view_as → 403
  I5: superadmin view_as=alice → 看到 alice 视图（不含 bob skill）
  I6: preview_user 字段反映实际用户身份
  I7: superadmin 自己调用 preview（无 view_as）→ preview_user = superadmin
  I8: ENABLE_AUTH=false 匿名预览看到全部 user skill（向后兼容）

Section J (4) — build_skill_prompt_async 用户过滤（集成级）
  J1: keyword 模式：bob 调用 build_skill_prompt → 不含 alice 的 skill
  J2: keyword 模式：project skill 对两个用户均可见
  J3: keyword 模式：sub_skill 展开不跨用户泄露
  J4: ENABLE_AUTH=false default user 可见全部 skill

Section K (3) — list_md_skills HTTP 用户过滤
  K1: ENABLE_AUTH=true → GET /md-skills 不含他人的 user 层 skill
  K2: GET /md-skills system/project skill 对所有用户可见
  K3: GET /md-skills viewer 角色无 md-skills 限制（公开端点）

总计: 15 个测试用例
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from typing import List
from unittest.mock import patch, MagicMock

# ── 确保 ENABLE_AUTH 初始为 false（测试框架需要）────────────────────────────────
os.environ.setdefault("ENABLE_AUTH", "False")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "backend"))

# ── 全局测试数据前缀 ─────────────────────────────────────────────────────────
_PREFIX = f"_si_{uuid.uuid4().hex[:6]}_"


# ── DB helpers ──────────────────────────────────────────────────────────────

def _db():
    from backend.config.database import SessionLocal
    return SessionLocal()


_g_db = _db()


def _make_user(suffix="", role_names=None, is_superadmin=False):
    from backend.models.user import User
    from backend.models.role import Role
    from backend.models.user_role import UserRole
    from backend.core.auth.password import hash_password

    username = f"{_PREFIX}{suffix or uuid.uuid4().hex[:6]}"
    u = User(
        username=username,
        display_name=f"Test {suffix}",
        hashed_password=hash_password("Test1234!"),
        auth_source="local",
        is_active=True,
        is_superadmin=is_superadmin,
    )
    _g_db.add(u)
    _g_db.flush()
    for rname in (role_names or []):
        role = _g_db.query(Role).filter(Role.name == rname).first()
        if role:
            _g_db.add(UserRole(user_id=u.id, role_id=role.id))
    _g_db.commit()
    _g_db.refresh(u)
    return u


def _token(user):
    from backend.config.settings import settings
    from backend.core.auth.jwt import create_access_token
    from backend.core.rbac import get_user_roles
    roles = get_user_roles(user, _g_db)
    return create_access_token(
        {"sub": str(user.id), "username": user.username, "roles": roles},
        settings.jwt_secret, settings.jwt_algorithm,
    )


def teardown_module(_=None):
    from backend.models.user import User
    try:
        _g_db.query(User).filter(
            User.username.like(f"{_PREFIX}%")
        ).delete(synchronize_session=False)
        _g_db.commit()
    finally:
        _g_db.close()


# ── FastAPI TestClient ────────────────────────────────────────────────────────

from fastapi.testclient import TestClient


def _make_app():
    from backend.main import app
    return app


# ── Skill 文件写入辅助 ────────────────────────────────────────────────────────

def _write_skill(path: Path, name: str, triggers: List[str], content: str = "body",
                 always_inject: bool = False, sub_skills: List[str] = None) -> None:
    """Write a minimal valid SKILL.md to path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    triggers_yaml = "\n".join(f"  - {t}" for t in triggers)
    ai_line = "always_inject: true\n" if always_inject else ""
    sub_line = ""
    if sub_skills:
        sub_items = "\n".join(f"  - {s}" for s in sub_skills)
        sub_line = f"sub_skills:\n{sub_items}\n"
    path.write_text(
        f"---\nname: {name}\ndescription: {name} desc\ntriggers:\n{triggers_yaml}\n"
        f"{ai_line}{sub_line}---\n\n{content}\n",
        encoding="utf-8",
    )


def _make_test_loader(tmpdir, alice_username, bob_username):
    """Create a SkillLoader with alice/bob skills in a temp directory."""
    from backend.skills.skill_loader import SkillLoader

    skills_dir = Path(tmpdir) / ".claude" / "skills"

    # system
    _write_skill(skills_dir / "system" / "_base-safety.md", "_base-safety", [],
                 always_inject=True)
    _write_skill(skills_dir / "system" / "sys-skill.md", "sys-skill", ["sysword"])

    # project
    _write_skill(skills_dir / "project" / "proj-skill.md", "proj-skill", ["projword"])

    # user/alice
    alice_dir = skills_dir / "user" / alice_username
    alice_dir.mkdir(parents=True, exist_ok=True)
    _write_skill(alice_dir / "alice-skill.md", "alice-skill", ["aliceword"],
                 content="alice skill content")
    _write_skill(alice_dir / "alice-sub.md", "alice-sub", ["alicesub"],
                 content="alice sub content")
    # alice-skill declares alice-sub as sub_skill
    _write_skill(alice_dir / "alice-skill.md", "alice-skill", ["aliceword"],
                 content="alice skill content", sub_skills=["alice-sub"])

    # user/bob
    bob_dir = skills_dir / "user" / bob_username
    bob_dir.mkdir(parents=True, exist_ok=True)
    _write_skill(bob_dir / "bob-skill.md", "bob-skill", ["bobword"],
                 content="bob skill content")

    loader = SkillLoader(skills_dir=str(skills_dir))
    loader.load_all()
    return loader


# ══════════════════════════════════════════════════════════════════════════════
# I — Preview API 用户隔离（HTTP 级别）
# ══════════════════════════════════════════════════════════════════════════════

class TestPreviewApiUserIsolation(unittest.TestCase):
    """I1-I8: /skills/preview 端点用户隔离完整验证"""

    @classmethod
    def setUpClass(cls):
        cls.app = _make_app()
        cls.client = TestClient(cls.app, raise_server_exceptions=True)
        cls.alice = _make_user("i_alice", role_names=["analyst"])
        cls.bob   = _make_user("i_bob",   role_names=["analyst"])
        cls.sadmin = _make_user("i_super", role_names=["superadmin"], is_superadmin=True)

    def _auth(self, user):
        return {"Authorization": f"Bearer {_token(user)}"}

    def _get_preview(self, message, user=None, view_as="", enable_auth=True,
                     loader=None):
        """Call GET /skills/preview with optional patching."""
        url = f"/api/v1/skills/preview?message={message}"
        if view_as:
            url += f"&view_as={view_as}"
        headers = self._auth(user) if user else {}

        patches = [
            patch("backend.config.settings.settings.enable_auth", enable_auth),
        ]
        if loader is not None:
            patches.append(patch("backend.skills.skill_loader._singleton", loader))

        with contextlib.ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            return self.client.get(url, headers=headers)

    def test_I1_alice_preview_only_contains_alice_skill(self):
        """I1: Alice 预览 'aliceword' → triggered.user 含 alice-skill"""
        with tempfile.TemporaryDirectory() as tmpdir:
            loader = _make_test_loader(tmpdir, self.alice.username, self.bob.username)
            resp = self._get_preview("aliceword", self.alice, enable_auth=True,
                                     loader=loader)
        self.assertEqual(resp.status_code, 200, resp.text)
        user_triggered = [s["name"] for s in resp.json()["triggered"]["user"]]
        self.assertIn("alice-skill", user_triggered,
                      "Alice 的 alice-skill 应在 triggered.user 中")

    def test_I2_bob_preview_does_not_contain_alice_skill(self):
        """I2: Bob 预览 'aliceword' → triggered.user 不含 alice-skill（隔离验证）"""
        with tempfile.TemporaryDirectory() as tmpdir:
            loader = _make_test_loader(tmpdir, self.alice.username, self.bob.username)
            resp = self._get_preview("aliceword", self.bob, enable_auth=True,
                                     loader=loader)
        self.assertEqual(resp.status_code, 200, resp.text)
        user_triggered = [s["name"] for s in resp.json()["triggered"]["user"]]
        self.assertNotIn("alice-skill", user_triggered,
                         "Bob 不应在 triggered.user 中看到 alice-skill")

    def test_I3_match_details_does_not_leak_other_user_skills(self):
        """I3: Bob 预览时 match_details 不应包含 alice-skill（信息泄露防护）"""
        with tempfile.TemporaryDirectory() as tmpdir:
            loader = _make_test_loader(tmpdir, self.alice.username, self.bob.username)
            resp = self._get_preview("aliceword", self.bob, enable_auth=True,
                                     loader=loader)
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()
        match_details = data.get("match_details", {})
        self.assertNotIn(
            "alice-skill", match_details,
            f"match_details 不应泄露 alice 的 skill 名称，实际 keys: {list(match_details.keys())}",
        )

    def test_I4_non_superadmin_view_as_returns_403(self):
        """I4: 非 superadmin 使用 view_as 参数 → 403 Forbidden"""
        with patch("backend.config.settings.settings.enable_auth", True):
            resp = self.client.get(
                f"/api/v1/skills/preview?message=test&view_as={self.alice.username}",
                headers=self._auth(self.bob),
            )
        self.assertEqual(resp.status_code, 403,
                         f"非 superadmin 使用 view_as 应得 403，实际: {resp.status_code}")

    def test_I5_superadmin_view_as_sees_target_user_skills(self):
        """I5: superadmin view_as=alice → triggered.user 含 alice-skill，不含 bob-skill"""
        with tempfile.TemporaryDirectory() as tmpdir:
            loader = _make_test_loader(tmpdir, self.alice.username, self.bob.username)
            resp = self._get_preview(
                "aliceword", self.sadmin,
                view_as=self.alice.username,
                enable_auth=True, loader=loader,
            )
        self.assertEqual(resp.status_code, 200, resp.text)
        user_triggered = [s["name"] for s in resp.json()["triggered"]["user"]]
        self.assertIn("alice-skill", user_triggered,
                      "superadmin view_as=alice 应看到 alice-skill")
        self.assertNotIn("bob-skill", user_triggered,
                         "superadmin view_as=alice 不应看到 bob-skill")

    def test_I6_preview_user_field_reflects_caller_identity(self):
        """I6: preview_user 字段值 = 调用者用户名"""
        with patch("backend.config.settings.settings.enable_auth", True):
            resp = self.client.get(
                "/api/v1/skills/preview?message=anything",
                headers=self._auth(self.alice),
            )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["preview_user"], self.alice.username,
                         "preview_user 应为 alice 的用户名")

    def test_I7_superadmin_own_preview_uses_own_identity(self):
        """I7: superadmin 不传 view_as → preview_user = superadmin 自己的用户名"""
        with patch("backend.config.settings.settings.enable_auth", True):
            resp = self.client.get(
                "/api/v1/skills/preview?message=anything",
                headers=self._auth(self.sadmin),
            )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["preview_user"], self.sadmin.username)

    def test_I8_anon_preview_sees_all_user_skills(self):
        """I8: ENABLE_AUTH=false 匿名预览 → alice/bob skill 均可见（向后兼容）"""
        with tempfile.TemporaryDirectory() as tmpdir:
            loader = _make_test_loader(tmpdir, self.alice.username, self.bob.username)
            resp = self._get_preview("aliceword", user=None,
                                     enable_auth=False, loader=loader)
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()
        user_triggered = [s["name"] for s in data["triggered"]["user"]]
        self.assertIn("alice-skill", user_triggered,
                      "匿名模式下 alice-skill 应对所有人可见")


# ══════════════════════════════════════════════════════════════════════════════
# J — build_skill_prompt_async 用户过滤（Python 集成级）
# ══════════════════════════════════════════════════════════════════════════════

class TestBuildSkillPromptIsolation(unittest.TestCase):
    """J1-J4: build_skill_prompt_async 在 keyword 模式下的用户隔离验证"""

    def _run_async(self, coro):
        return asyncio.get_event_loop().run_until_complete(coro)

    def test_J1_bob_prompt_does_not_contain_alice_skill(self):
        """J1: build_skill_prompt keyword 模式 — bob 的 prompt 不含 alice-skill"""
        with tempfile.TemporaryDirectory() as tmpdir:
            loader = _make_test_loader(tmpdir, "alice_j", "bob_j")
            # Bob 触发 aliceword
            prompt = self._run_async(
                loader.build_skill_prompt_async(
                    "aliceword", llm_adapter=None, user_id="bob_j"
                )
            )
        self.assertNotIn("alice-skill", prompt,
                         "bob 的 prompt 不应包含 alice 的 skill")

    def test_J2_project_skill_visible_to_both_users(self):
        """J2: project skill 对 alice 和 bob 均可见"""
        with tempfile.TemporaryDirectory() as tmpdir:
            loader = _make_test_loader(tmpdir, "alice_j2", "bob_j2")
            prompt_alice = self._run_async(
                loader.build_skill_prompt_async(
                    "projword", llm_adapter=None, user_id="alice_j2"
                )
            )
            prompt_bob = self._run_async(
                loader.build_skill_prompt_async(
                    "projword", llm_adapter=None, user_id="bob_j2"
                )
            )
        self.assertIn("proj-skill", prompt_alice, "alice 应看到 proj-skill")
        self.assertIn("proj-skill", prompt_bob,   "bob 应看到 proj-skill")

    def test_J3_sub_skill_expansion_not_cross_user(self):
        """J3: alice-skill 声明的 alice-sub 不出现在 bob 的 prompt 中"""
        with tempfile.TemporaryDirectory() as tmpdir:
            loader = _make_test_loader(tmpdir, "alice_j3", "bob_j3")
            # Alice 触发 aliceword → alice-sub 应展开
            prompt_alice = self._run_async(
                loader.build_skill_prompt_async(
                    "aliceword", llm_adapter=None, user_id="alice_j3"
                )
            )
            # Bob 触发 aliceword → alice-sub 不应展开
            prompt_bob = self._run_async(
                loader.build_skill_prompt_async(
                    "aliceword", llm_adapter=None, user_id="bob_j3"
                )
            )
        self.assertIn("alice-sub", prompt_alice,
                      "alice 的 sub_skill alice-sub 应在 alice 的 prompt 中")
        self.assertNotIn("alice-sub", prompt_bob,
                         "alice 的 sub_skill alice-sub 不应出现在 bob 的 prompt 中")

    def test_J4_default_user_sees_all_skills(self):
        """J4: username='default'（ENABLE_AUTH=false）→ 看到全部 user skill"""
        with tempfile.TemporaryDirectory() as tmpdir:
            loader = _make_test_loader(tmpdir, "alice_j4", "bob_j4")
            prompt = self._run_async(
                loader.build_skill_prompt_async(
                    "aliceword", llm_adapter=None, user_id="default"
                )
            )
        self.assertIn("alice-skill", prompt,
                      "default 用户应能看到 alice-skill（向后兼容）")


# ══════════════════════════════════════════════════════════════════════════════
# K — list_md_skills HTTP 用户过滤
# ══════════════════════════════════════════════════════════════════════════════

class TestListMdSkillsIsolation(unittest.TestCase):
    """K1-K3: GET /md-skills 端点用户层 skill 隔离验证"""

    @classmethod
    def setUpClass(cls):
        cls.app    = _make_app()
        cls.client = TestClient(cls.app, raise_server_exceptions=True)
        cls.alice  = _make_user("k_alice", role_names=["analyst"])
        cls.bob    = _make_user("k_bob",   role_names=["analyst"])
        cls.viewer = _make_user("k_viewer", role_names=["viewer"])

    def _auth(self, user):
        return {"Authorization": f"Bearer {_token(user)}"}

    def _skills_list(self, user, loader, enable_auth=True):
        """GET /md-skills with patched loader."""
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch("backend.config.settings.settings.enable_auth", enable_auth))
            stack.enter_context(patch("backend.skills.skill_loader._singleton", loader))
            resp = self.client.get("/api/v1/skills/md-skills",
                                   headers=self._auth(user))
        return resp

    def test_K1_alice_md_skills_does_not_include_bob_user_skill(self):
        """K1: ENABLE_AUTH=true → Alice 的 /md-skills 不含 bob 的 user 层 skill"""
        with tempfile.TemporaryDirectory() as tmpdir:
            # 显式使用用户真实用户名，让 _get_user_skill_dir 可以正确匹配
            loader = _make_test_loader(tmpdir, self.alice.username, self.bob.username)

            # 额外 patch _USER_SKILLS_DIR 以指向 tmpdir 的 user/ 目录
            user_skills_dir = Path(tmpdir) / ".claude" / "skills" / "user"
            with contextlib.ExitStack() as stack:
                stack.enter_context(
                    patch("backend.config.settings.settings.enable_auth", True))
                stack.enter_context(
                    patch("backend.skills.skill_loader._singleton", loader))
                stack.enter_context(
                    patch("backend.api.skills._USER_SKILLS_DIR", user_skills_dir))
                resp_alice = self.client.get("/api/v1/skills/md-skills",
                                             headers=self._auth(self.alice))
        self.assertEqual(resp_alice.status_code, 200, resp_alice.text)
        names = [s["name"] for s in resp_alice.json()]
        self.assertNotIn("bob-skill", names,
                         "Alice 的 md-skills 不应含 bob-skill")

    def test_K2_system_and_project_skills_visible_to_all(self):
        """K2: system/project skill 对 alice 和 bob 均可见（不受用户过滤影响）"""
        with tempfile.TemporaryDirectory() as tmpdir:
            loader = _make_test_loader(tmpdir, self.alice.username, self.bob.username)
            user_skills_dir = Path(tmpdir) / ".claude" / "skills" / "user"

            def _get_list(user):
                with contextlib.ExitStack() as stack:
                    stack.enter_context(
                        patch("backend.config.settings.settings.enable_auth", True))
                    stack.enter_context(
                        patch("backend.skills.skill_loader._singleton", loader))
                    stack.enter_context(
                        patch("backend.api.skills._USER_SKILLS_DIR", user_skills_dir))
                    return self.client.get("/api/v1/skills/md-skills",
                                          headers=self._auth(user))

            resp_alice = _get_list(self.alice)
            resp_bob   = _get_list(self.bob)

        def _names(resp):
            return {s["name"] for s in resp.json()}

        alice_names = _names(resp_alice)
        bob_names   = _names(resp_bob)

        for name in ("sys-skill", "proj-skill"):
            self.assertIn(name, alice_names, f"alice 应看到 {name}")
            self.assertIn(name, bob_names,   f"bob 应看到 {name}")

    def test_K3_md_skills_accessible_without_special_permission(self):
        """K3: GET /md-skills 使用 get_current_user（无额外权限），analyst/viewer 均可访问"""
        with patch("backend.config.settings.settings.enable_auth", True):
            # analyst: has skills.user:read
            resp_analyst = self.client.get("/api/v1/skills/md-skills",
                                           headers=self._auth(self.alice))
            # viewer: has only chat:use, but md-skills uses get_current_user not require_permission
            resp_viewer = self.client.get("/api/v1/skills/md-skills",
                                          headers=self._auth(self.viewer))
        # md-skills 端点使用 get_current_user（无权限门槛），所有登录用户均可访问
        self.assertEqual(resp_analyst.status_code, 200,
                         f"analyst 应可访问 md-skills: {resp_analyst.text}")
        self.assertEqual(resp_viewer.status_code, 200,
                         f"viewer 也应可访问 md-skills（无额外权限要求）: {resp_viewer.text}")


# ══════════════════════════════════════════════════════════════════════════════
# Runner
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v", "-s"]))
