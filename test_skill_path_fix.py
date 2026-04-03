"""
test_skill_path_fix.py
======================
针对技能文件写入路径 Bug 修复的专项测试

修复范围：
  Fix-1  agentic_loop._build_system_prompt  → 注入精确路径模板
  Fix-2  FilesystemPermissionProxy._is_write_allowed → 拦截 customer_data/.claude 错误路由
  Fix-3  FilesystemPermissionProxy.call_tool 报错消息  → 包含用户名层
  Fix-4  FilesystemPermissionProxy._check_skills_user_subdir → 禁止写入 user/ 根目录

节 A  Fix-1 系统提示路径模板测试（10 个）
节 B  Fix-2 cross-root 错误路由拦截测试（8 个）
节 C  Fix-3 报错消息路径格式测试（6 个）
节 D  Fix-4 username 子目录强制校验测试（8 个）
节 E  端到端场景测试（8 个）
"""
import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

# ── path setup ────────────────────────────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).parent))


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _make_proxy(tmp: Path, customer_data: Path = None, skills_user: Path = None):
    """Create a FilesystemPermissionProxy backed by tmp directories."""
    from backend.core.filesystem_permission_proxy import FilesystemPermissionProxy

    if customer_data is None:
        customer_data = tmp / "customer_data"
    if skills_user is None:
        skills_user = tmp / ".claude" / "skills" / "user"

    customer_data.mkdir(parents=True, exist_ok=True)
    skills_user.mkdir(parents=True, exist_ok=True)
    skills_root = skills_user.parent  # .claude/skills

    base = MagicMock()
    base.call_tool = AsyncMock(return_value={"success": True, "path": "written"})

    return FilesystemPermissionProxy(
        base=base,
        write_allowed_dirs=[str(customer_data), str(skills_user)],
        read_allowed_dirs=[str(customer_data), str(skills_root)],
    )


# ─────────────────────────────────────────────────────────────────────────────
# A — Fix-1: _build_system_prompt path template
# ─────────────────────────────────────────────────────────────────────────────

class TestA_Fix1_SystemPromptPathTemplate(unittest.IsolatedAsyncioTestCase):
    """Fix-1: _build_system_prompt should inject unambiguous, user-specific skill path."""

    def _make_loop(self, tmp: Path, username: str):
        from backend.agents.agentic_loop import AgenticLoop

        customer_data = tmp / "customer_data"
        skills_root = tmp / ".claude" / "skills"
        customer_data.mkdir(parents=True, exist_ok=True)
        skills_root.mkdir(parents=True, exist_ok=True)

        # Fake filesystem server with allowed_directories
        fs_server = MagicMock()
        fs_server.allowed_directories = [str(customer_data), str(skills_root)]

        mcp_manager = MagicMock()
        mcp_manager.list_servers.return_value = [
            {"name": "filesystem", "type": "filesystem", "tool_count": 5}
        ]
        mcp_manager.servers = {"filesystem": fs_server}

        loop = AgenticLoop.__new__(AgenticLoop)
        loop.mcp_manager = mcp_manager
        loop.llm_adapter = None
        return loop, str(customer_data), str(skills_root), username

    async def test_A01_skill_path_contains_username(self):
        """Skill path template includes actual username, not placeholder."""
        with tempfile.TemporaryDirectory() as tmp:
            loop, cdata, skills, username = self._make_loop(Path(tmp), "alice")
            context = {"username": username, "system_prompt": ""}
            prompt = await loop._build_system_prompt(context, message="")
            self.assertIn("alice", prompt)
            self.assertIn("user/alice/", prompt)

    async def test_A02_data_root_and_skills_root_separate(self):
        """Prompt distinguishes data root from skills root explicitly."""
        with tempfile.TemporaryDirectory() as tmp:
            loop, cdata, skills, _ = self._make_loop(Path(tmp), "bob")
            context = {"username": "bob", "system_prompt": ""}
            prompt = await loop._build_system_prompt(context, message="")
            self.assertIn("customer_data", prompt)
            self.assertIn(".claude", prompt)

    async def test_A03_skill_path_not_under_customer_data(self):
        """Skill path example in prompt does NOT start with customer_data root."""
        with tempfile.TemporaryDirectory() as tmp:
            loop, cdata, skills, _ = self._make_loop(Path(tmp), "carol")
            context = {"username": "carol", "system_prompt": ""}
            prompt = await loop._build_system_prompt(context, message="")
            # Extract the skill path example line
            lines = [l for l in prompt.splitlines() if "user/carol/" in l]
            self.assertTrue(len(lines) >= 1, "No skill path example found in prompt")
            for line in lines:
                # The skill path should NOT start from customer_data
                self.assertNotIn("customer_data", line.split("user/carol/")[0],
                                 f"Skill path incorrectly rooted under customer_data: {line}")

    async def test_A04_explicit_prohibition_of_mixing_roots(self):
        """Prompt explicitly says not to write skills into customer_data."""
        with tempfile.TemporaryDirectory() as tmp:
            loop, cdata, skills, _ = self._make_loop(Path(tmp), "dave")
            context = {"username": "dave", "system_prompt": ""}
            prompt = await loop._build_system_prompt(context, message="")
            self.assertIn("customer_data", prompt)
            # Should include prohibition text
            self.assertTrue(
                "严禁" in prompt or "禁止" in prompt or "不允许" in prompt,
                "No explicit prohibition found in prompt"
            )

    async def test_A05_username_layer_required_mentioned(self):
        """Prompt mentions that user/{username}/ layer is required (not just user/)."""
        with tempfile.TemporaryDirectory() as tmp:
            loop, cdata, skills, _ = self._make_loop(Path(tmp), "eve")
            context = {"username": "eve", "system_prompt": ""}
            prompt = await loop._build_system_prompt(context, message="")
            # user/eve/ should appear (the username subdir path)
            self.assertIn("user/eve/", prompt)
            # Should NOT tell user to write at user/ root directly
            # (user/ alone with no username would be wrong)

    async def test_A06_current_user_injected_at_bottom(self):
        """CURRENT_USER: {username} is still present for skill-creator.md compatibility."""
        with tempfile.TemporaryDirectory() as tmp:
            loop, cdata, skills, _ = self._make_loop(Path(tmp), "frank")
            context = {"username": "frank", "system_prompt": ""}
            prompt = await loop._build_system_prompt(context, message="")
            self.assertIn("CURRENT_USER: frank", prompt)

    async def test_A07_anonymous_user_fallback(self):
        """When username is 'anonymous', path template still works."""
        with tempfile.TemporaryDirectory() as tmp:
            loop, cdata, skills, _ = self._make_loop(Path(tmp), "anonymous")
            context = {"username": "anonymous", "system_prompt": ""}
            prompt = await loop._build_system_prompt(context, message="")
            self.assertIn("user/anonymous/", prompt)

    async def test_A08_missing_username_in_context_defaults(self):
        """When context has no username key, defaults to 'anonymous'."""
        with tempfile.TemporaryDirectory() as tmp:
            loop, cdata, skills, _ = self._make_loop(Path(tmp), "")
            context = {}  # no username
            prompt = await loop._build_system_prompt(context, message="")
            self.assertIn("anonymous", prompt)

    async def test_A09_no_filesystem_server_no_path_hint(self):
        """When no filesystem server, no path template is injected."""
        from backend.agents.agentic_loop import AgenticLoop
        loop = AgenticLoop.__new__(AgenticLoop)
        loop.mcp_manager = MagicMock()
        loop.mcp_manager.list_servers.return_value = []  # no filesystem
        loop.mcp_manager.servers = {}
        loop.llm_adapter = None
        context = {"username": "grace", "system_prompt": "Base"}
        prompt = await loop._build_system_prompt(context, message="")
        self.assertNotIn("user/grace/", prompt)

    async def test_A10_skill_example_path_is_absolute(self):
        """Skill path example in prompt uses absolute path (not relative)."""
        import re
        with tempfile.TemporaryDirectory() as tmp:
            loop, cdata, skills, _ = self._make_loop(Path(tmp), "hank")
            context = {"username": "hank", "system_prompt": ""}
            prompt = await loop._build_system_prompt(context, message="")
            # Only check lines that actually embed an example path
            # (i.e. lines containing "路径示例" or "→ 写入" with a full absolute root)
            example_lines = [
                l for l in prompt.splitlines()
                if ("路径示例" in l or "→ 写入" in l) and "user/hank/" in l
            ]
            self.assertTrue(len(example_lines) >= 1,
                            "No skill path example line found in prompt")
            for line in example_lines:
                # Absolute path on Windows: drive letter C:/ or D:/ etc.
                path_in_line = re.search(r'[A-Za-z]:[/\\][^\s]*user/hank/', line)
                self.assertIsNotNone(
                    path_in_line,
                    f"Expected absolute path with drive letter in example line: {line}"
                )


# ─────────────────────────────────────────────────────────────────────────────
# B — Fix-2: _is_write_allowed cross-root error routing
# ─────────────────────────────────────────────────────────────────────────────

class TestB_Fix2_CrossRootErrorRouting(unittest.IsolatedAsyncioTestCase):
    """Fix-2: .claude paths rooted under customer_data/ must be blocked."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.tmp_path = Path(self.tmp)
        self.proxy = _make_proxy(self.tmp_path)
        self.customer_data = self.tmp_path / "customer_data"
        self.skills_user = self.tmp_path / ".claude" / "skills" / "user"

    def test_B01_blocks_claude_path_under_customer_data(self):
        """customer_data/.claude/skills/user/alice/skill.md must be blocked."""
        bad_path = str(self.customer_data / ".claude" / "skills" / "user" / "alice" / "skill.md")
        result = self.proxy._is_write_allowed(bad_path)
        self.assertFalse(result, f"Expected False for cross-routed path: {bad_path}")

    def test_B02_blocks_nested_claude_under_customer_data(self):
        """customer_data/subfolder/.claude/... must also be blocked."""
        bad_path = str(self.customer_data / "subfolder" / ".claude" / "file.md")
        result = self.proxy._is_write_allowed(bad_path)
        self.assertFalse(result)

    def test_B03_allows_legitimate_customer_data_file(self):
        """customer_data/result.csv must still be allowed."""
        good_path = str(self.customer_data / "result.csv")
        result = self.proxy._is_write_allowed(good_path)
        self.assertTrue(result, f"Expected True for legitimate data file: {good_path}")

    def test_B04_allows_legitimate_skill_file(self):
        """Correct .claude/skills/user/alice/skill.md must be allowed."""
        good_path = str(self.skills_user / "alice" / "my-skill.md")
        result = self.proxy._is_write_allowed(good_path)
        self.assertTrue(result, f"Expected True for correct skill path: {good_path}")

    def test_B05_allows_customer_data_subdir_without_claude(self):
        """customer_data/reports/q1.json must be allowed (no .claude segment)."""
        good_path = str(self.customer_data / "reports" / "q1.json")
        result = self.proxy._is_write_allowed(good_path)
        self.assertTrue(result)

    def test_B06_blocks_relative_path_resolving_to_customer_data_claude(self):
        """Relative path that resolves to customer_data/.claude must be blocked."""
        # This simulates LLM giving relative .claude/skills/... path,
        # which the proxy resolves against customer_data/ first
        bad_path = str(self.customer_data / ".claude" / "x.md")
        self.assertFalse(self.proxy._is_write_allowed(bad_path))

    def test_B07_fix2_warning_logged(self):
        """Fix-2 must emit a WARNING log when blocking cross-routed path."""
        import logging
        bad_path = str(self.customer_data / ".claude" / "skill.md")
        with self.assertLogs("backend.core.filesystem_permission_proxy", level="WARNING") as cm:
            self.proxy._is_write_allowed(bad_path)
        self.assertTrue(
            any("Fix-2" in msg or "incorrectly routed" in msg for msg in cm.output),
            f"Expected Fix-2 warning, got: {cm.output}"
        )

    def test_B08_outside_all_roots_returns_false(self):
        """Path outside all allowed roots returns False."""
        outside = str(Path(self.tmp) / "backend" / "secret.py")
        self.assertFalse(self.proxy._is_write_allowed(outside))


# ─────────────────────────────────────────────────────────────────────────────
# C — Fix-3: call_tool error message contains username layer
# ─────────────────────────────────────────────────────────────────────────────

class TestC_Fix3_ErrorMessagePathTemplate(unittest.IsolatedAsyncioTestCase):
    """Fix-3: Permission-denied error message must include {用户名}/ layer."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.proxy = _make_proxy(Path(self.tmp))
        self.bad_path = str(Path(self.tmp) / "backend" / "not_allowed.py")

    async def test_C01_error_message_has_username_placeholder(self):
        """Error message shows {用户名}/ in skill path example."""
        result = await self.proxy.call_tool("filesystem", "write_file",
                                             {"path": self.bad_path, "content": "x"})
        self.assertFalse(result["success"])
        error = result["error"]
        self.assertIn("用户名", error, f"No username placeholder in error: {error}")

    async def test_C02_error_message_no_longer_shows_flat_user_path(self):
        """Error message must NOT show old flat path .claude/skills/user/{skill-name}.md."""
        result = await self.proxy.call_tool("filesystem", "write_file",
                                             {"path": self.bad_path, "content": "x"})
        error = result["error"]
        # Old wrong format: user/{skill-name}.md (no username subdir)
        # It should now show user/{用户名}/{skill-name}.md
        # Specifically check that the path hint doesn't have user/... without an intermediate dir
        import re
        # Should NOT match pattern "user/some-skill.md" (flat, no subdir)
        flat_pattern = re.compile(r"user/\{?[^/{}]+\.md")
        self.assertFalse(
            flat_pattern.search(error),
            f"Old flat skill path still present in error message: {error}"
        )

    async def test_C03_error_contains_skills_user_dir(self):
        """Error message contains the actual .claude/skills/user path."""
        result = await self.proxy.call_tool("filesystem", "write_file",
                                             {"path": self.bad_path, "content": "x"})
        error = result["error"]
        self.assertIn(".claude", error, f"Skills dir not found in error: {error}")

    async def test_C04_error_mentions_forbidden_paths(self):
        """Error message mentions forbidden directories (system/ and project/)."""
        result = await self.proxy.call_tool("filesystem", "write_file",
                                             {"path": self.bad_path, "content": "x"})
        error = result["error"]
        self.assertIn("system/", error)
        self.assertIn("project/", error)

    async def test_C05_read_tools_pass_through_always(self):
        """Read tools (read_file, list_directory) are never blocked."""
        result = await self.proxy.call_tool("filesystem", "read_file",
                                             {"path": self.bad_path})
        # Base mock returns success=True
        self.assertTrue(result.get("success", True))

    async def test_C06_non_filesystem_server_passes_through(self):
        """Calls to non-filesystem servers pass through without path checks."""
        result = await self.proxy.call_tool("clickhouse-idn", "execute_query",
                                             {"sql": "SELECT 1"})
        self.assertTrue(result.get("success", True))


# ─────────────────────────────────────────────────────────────────────────────
# D — Fix-4: username subdir enforcement
# ─────────────────────────────────────────────────────────────────────────────

class TestD_Fix4_UsernameSubdirEnforcement(unittest.IsolatedAsyncioTestCase):
    """Fix-4: Writes to .claude/skills/user/ must include a username subdirectory."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.tmp_path = Path(self.tmp)
        self.proxy = _make_proxy(self.tmp_path)
        self.skills_user = self.tmp_path / ".claude" / "skills" / "user"

    def test_D01_blocks_direct_write_to_user_root(self):
        """Writing skill.md directly to user/ root must be blocked."""
        flat_path = str(self.skills_user / "my-skill.md")
        error = self.proxy._check_skills_user_subdir(flat_path)
        self.assertIsNotNone(error, "Expected error for flat path under user/")
        self.assertIn("用户名", error)

    def test_D02_allows_write_with_username_subdir(self):
        """Writing to user/alice/skill.md must pass."""
        good_path = str(self.skills_user / "alice" / "my-skill.md")
        error = self.proxy._check_skills_user_subdir(good_path)
        self.assertIsNone(error, f"Expected None for valid path, got: {error}")

    def test_D03_allows_write_with_deep_path(self):
        """Writing to user/alice/category/skill.md must pass (depth > 2 is fine)."""
        deep_path = str(self.skills_user / "alice" / "subdir" / "skill.md")
        error = self.proxy._check_skills_user_subdir(deep_path)
        self.assertIsNone(error)

    def test_D04_does_not_affect_customer_data_paths(self):
        """customer_data writes are not subject to username check."""
        data_path = str(self.tmp_path / "customer_data" / "result.csv")
        error = self.proxy._check_skills_user_subdir(data_path)
        self.assertIsNone(error)

    async def test_D05_call_tool_blocks_flat_skill_path(self):
        """call_tool blocks flat user/skill.md and returns descriptive error."""
        flat_path = str(self.skills_user / "flat-skill.md")
        result = await self.proxy.call_tool("filesystem", "write_file",
                                             {"path": flat_path, "content": "---\nname: x\n---"})
        self.assertFalse(result["success"])
        self.assertIn("用户名", result["error"])

    async def test_D06_call_tool_allows_username_subdir_skill(self):
        """call_tool allows user/alice/skill.md and passes to base."""
        good_path = str(self.skills_user / "alice" / "my-skill.md")
        result = await self.proxy.call_tool("filesystem", "write_file",
                                             {"path": good_path, "content": "---\nname: x\n---"})
        self.assertTrue(result.get("success", False),
                         f"Expected success for valid skill path: {result}")

    def test_D07_fix4_warning_logged_for_flat_path(self):
        """Fix-4 must emit WARNING when blocking flat path."""
        flat_path = str(self.skills_user / "bad.md")
        with self.assertLogs("backend.core.filesystem_permission_proxy", level="WARNING") as cm:
            self.proxy._check_skills_user_subdir(flat_path)
        self.assertTrue(
            any("Fix-4" in msg or "username subdirectory" in msg or "username subdir" in msg
                for msg in cm.output),
            f"Expected Fix-4 warning, got: {cm.output}"
        )

    def test_D08_no_skills_user_in_write_allowed_skips_check(self):
        """When write_allowed has no .claude dir, subdir check is skipped gracefully."""
        from backend.core.filesystem_permission_proxy import FilesystemPermissionProxy
        tmp = Path(tempfile.mkdtemp())
        cdata = tmp / "customer_data"
        cdata.mkdir()
        # No .claude dir in write_allowed
        proxy = FilesystemPermissionProxy(
            base=MagicMock(),
            write_allowed_dirs=[str(cdata)],
            read_allowed_dirs=[str(cdata)],
        )
        result = proxy._check_skills_user_subdir(str(cdata / "test.csv"))
        self.assertIsNone(result)


# ─────────────────────────────────────────────────────────────────────────────
# E — End-to-end scenarios
# ─────────────────────────────────────────────────────────────────────────────

class TestE_EndToEnd(unittest.IsolatedAsyncioTestCase):
    """End-to-end: simulate LLM writing skill file in various scenarios."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.tmp_path = Path(self.tmp)
        self.proxy = _make_proxy(self.tmp_path)
        self.customer_data = self.tmp_path / "customer_data"
        self.skills_user = self.tmp_path / ".claude" / "skills" / "user"

    async def test_E01_correct_absolute_skill_path_succeeds(self):
        """LLM uses correct absolute path: .claude/skills/user/alice/skill.md → allowed."""
        path = str(self.skills_user / "alice" / "data-quality.md")
        result = await self.proxy.call_tool("filesystem", "write_file",
                                             {"path": path, "content": "---\nname: x\n---"})
        self.assertTrue(result["success"])

    async def test_E02_wrong_root_customer_data_blocked(self):
        """LLM mistakenly uses customer_data/.claude/... → blocked by Fix-2."""
        path = str(self.customer_data / ".claude" / "skills" / "user" / "alice" / "skill.md")
        result = await self.proxy.call_tool("filesystem", "write_file",
                                             {"path": path, "content": "x"})
        self.assertFalse(result["success"])
        self.assertIn("权限拒绝", result["error"])

    async def test_E03_missing_username_subdir_blocked(self):
        """LLM writes to user/skill.md (skips username dir) → blocked by Fix-4."""
        path = str(self.skills_user / "my-skill.md")
        result = await self.proxy.call_tool("filesystem", "write_file",
                                             {"path": path, "content": "x"})
        self.assertFalse(result["success"])
        self.assertIn("用户名", result["error"])

    async def test_E04_correct_data_file_succeeds(self):
        """LLM writes data CSV to customer_data/ → allowed."""
        path = str(self.customer_data / "report_2026.csv")
        result = await self.proxy.call_tool("filesystem", "write_file",
                                             {"path": path, "content": "a,b\n1,2"})
        self.assertTrue(result["success"])

    async def test_E05_system_skill_dir_blocked(self):
        """LLM tries to write to .claude/skills/system/ → blocked (not in write_allowed)."""
        system_path = str(self.tmp_path / ".claude" / "skills" / "system" / "evil.md")
        result = await self.proxy.call_tool("filesystem", "write_file",
                                             {"path": system_path, "content": "x"})
        self.assertFalse(result["success"])

    async def test_E06_project_skill_dir_blocked(self):
        """LLM tries to write to .claude/skills/project/ → blocked."""
        project_path = str(self.tmp_path / ".claude" / "skills" / "project" / "evil.md")
        result = await self.proxy.call_tool("filesystem", "write_file",
                                             {"path": project_path, "content": "x"})
        self.assertFalse(result["success"])

    async def test_E07_different_users_isolated(self):
        """alice's skill write succeeds; no cross-contamination check (proxy is stateless)."""
        alice_path = str(self.skills_user / "alice" / "skill.md")
        bob_path = str(self.skills_user / "bob" / "skill.md")
        r1 = await self.proxy.call_tool("filesystem", "write_file",
                                         {"path": alice_path, "content": "x"})
        r2 = await self.proxy.call_tool("filesystem", "write_file",
                                         {"path": bob_path, "content": "x"})
        self.assertTrue(r1["success"])
        self.assertTrue(r2["success"])

    async def test_E08_error_message_guides_to_correct_path(self):
        """When wrong path used, error message contains the correct skills/user dir."""
        bad_path = str(self.customer_data / ".claude" / "skills" / "user" / "alice" / "s.md")
        result = await self.proxy.call_tool("filesystem", "write_file",
                                             {"path": bad_path, "content": "x"})
        self.assertFalse(result["success"])
        # The error should contain the correct skills path, not the wrong customer_data path
        error = result["error"]
        # Error must mention .claude (the correct root)
        self.assertIn(".claude", error)


# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    unittest.main(verbosity=2)
