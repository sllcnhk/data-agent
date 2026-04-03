"""
test_path_prefix_fix.py
========================
customer_data 双层目录 Bug 修复验证

问题根因：
  MCP Filesystem Server 以 customer_data/ 为根目录。
  当 LLM 接到 "customer_data/{user}/reports/" 这样包含 customer_data/ 前缀的相对路径并传给 MCP，
  MCP 将其解析为 customer_data/ + customer_data/{user}/reports/ → 双层目录。

修复方案：
  所有 skill 文件、analyst_agent.py、agentic_loop.py 中路径指引均去掉 customer_data/ 前缀，
  改为直接以用户名开头（如 {CURRENT_USER}/reports/），让 MCP 正确解析为 customer_data/{user}/reports/。

测试层次：
  A (5) — analyst_agent.py path_constraint 不含 customer_data/ 前缀
  B (6) — skill 文件路径指引不含 customer_data/ 前缀（覆盖所有已修复文件）
  C (4) — agentic_loop.py system prompt 路径说明正确
  D (4) — MCP 路径解析行为模拟（正确路径 vs 双层路径）
  E (3) — 回归：已修复的路径格式可被下载 API 正常使用

总计: 22 个测试用例

运行：
  /d/ProgramData/Anaconda3/envs/dataagent/python.exe -m pytest test_path_prefix_fix.py -v -s
"""
from __future__ import annotations

import inspect
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent / "backend"))
os.environ.setdefault("ENABLE_AUTH", "False")


# ══════════════════════════════════════════════════════════════════════════════
# A — analyst_agent.py path_constraint 不含错误前缀
# ══════════════════════════════════════════════════════════════════════════════

class TestAnalystAgentPathConstraint:
    """A: analyst_agent._build_file_write_prompt 路径约束已去掉 customer_data/ 前缀"""

    def _get_constraint(self, username: str = "superadmin", granted: bool = False) -> str:
        from backend.agents.analyst_agent import _build_file_write_section
        return _build_file_write_section(
            context={"username": username},
        )

    def test_a1_no_customer_data_prefix_in_path_constraint(self):
        """path_constraint 中的允许目录不含 customer_data/ 前缀"""
        text = self._get_constraint("alice")
        # 路径示例不应以 customer_data/ 开头
        lines_with_bad_prefix = [
            l for l in text.splitlines()
            if "customer_data/alice" in l or "customer_data/{" in l
        ]
        assert not lines_with_bad_prefix, \
            f"path_constraint 仍含 customer_data/ 前缀:\n" + "\n".join(lines_with_bad_prefix)

    def test_a2_correct_path_format_uses_username_prefix(self):
        """path_constraint 中示例路径以用户名开头"""
        text = self._get_constraint("alice")
        assert "alice/" in text, f"路径应含 'alice/' 前缀，实际:\n{text[:500]}"

    def test_a3_example_path_shows_reports_subdir(self):
        """示例路径包含 reports/ 子目录"""
        text = self._get_constraint("bob")
        assert "bob/reports/" in text or "reports/" in text, \
            f"示例路径应含 reports/:\n{text[:500]}"

    def test_a4_warning_note_about_double_prefix_present(self):
        """路径约束中含有关于禁止重复 customer_data/ 的警告说明"""
        text = self._get_constraint("superadmin")
        assert "customer_data" in text.lower() and ("重复" in text or "双层" in text or "禁止" in text), \
            f"应有关于禁止重复 customer_data/ 的说明:\n{text[:800]}"

    def test_a5_skills_path_still_correct(self):
        """skills 写入路径未被误改"""
        text = self._get_constraint("superadmin")
        assert ".claude/skills/user/" in text, \
            f"skills 路径应保持不变:\n{text[:500]}"


# ══════════════════════════════════════════════════════════════════════════════
# B — skill 文件路径指引检查
# ══════════════════════════════════════════════════════════════════════════════

_SKILLS_ROOT = Path(__file__).parent / ".claude" / "skills"

# 需要检查的 skill 文件（相对于项目根）
_SKILL_FILES_TO_CHECK = [
    ".claude/skills/system/_base-tools.md",
    ".claude/skills/system/_base-safety.md",
    ".claude/skills/project/db-knowledge-router.md",
    ".claude/skills/project/db-maintainer.md",
    ".claude/skills/user/superadmin/clickhouse-analyst.md",
    ".claude/skills/user/superadmin/clickhouse-analyst-mx.md",
    ".claude/skills/user/superadmin/ch-billing-analysis.md",
    ".claude/skills/user/superadmin/ch-call-metrics.md",
    ".claude/skills/user/superadmin/ch-sg-specific.md",
    ".claude/skills/user/superadmin/ch-br-specific.md",
    ".claude/skills/user/superadmin/ch-idn-specific.md",
    ".claude/skills/user/superadmin/ch-mx-specific.md",
    ".claude/skills/user/superadmin/ch-my-specific.md",
    ".claude/skills/user/superadmin/ch-thai-specific.md",
]

_PROJECT_ROOT = Path(__file__).parent


def _read_skill(rel_path: str) -> str:
    p = _PROJECT_ROOT / rel_path
    if not p.exists():
        return ""
    return p.read_text(encoding="utf-8")


class TestSkillFilePaths:
    """B: 所有已修复 skill 文件中不含 customer_data/{CURRENT_USER}/ 这种错误路径格式"""

    @pytest.mark.parametrize("skill_path", _SKILL_FILES_TO_CHECK)
    def test_b1_no_customer_data_current_user_prefix(self, skill_path):
        """skill 文件中不含 customer_data/{CURRENT_USER}/ 路径格式"""
        content = _read_skill(skill_path)
        if not content:
            pytest.skip(f"文件不存在: {skill_path}")

        bad_lines = [
            (i + 1, line)
            for i, line in enumerate(content.splitlines())
            if "customer_data/{CURRENT_USER}/" in line
            # 排除警告/说明行（含"说明"、"禁止"、"勿"等字样的行是正确的解释性文字）
            and not any(kw in line for kw in ["说明", "禁止重复", "勿重复", "⚠️", "直接用"])
        ]
        assert not bad_lines, (
            f"{skill_path} 仍含 customer_data/{{CURRENT_USER}}/ 路径指引:\n"
            + "\n".join(f"  L{ln}: {text}" for ln, text in bad_lines)
        )

    def test_b2_base_safety_has_correct_path(self):
        """_base-safety.md 数据文件路径是 {CURRENT_USER}/"""
        content = _read_skill(".claude/skills/system/_base-safety.md")
        assert "| `{CURRENT_USER}/`" in content or "{CURRENT_USER}/" in content, \
            "应含 {CURRENT_USER}/ 路径（不含 customer_data/ 前缀）"

    def test_b3_db_knowledge_router_read_path_correct(self):
        """db-knowledge-router.md 读取路径是 {CURRENT_USER}/db_knowledge/_index.md"""
        content = _read_skill(".claude/skills/project/db-knowledge-router.md")
        assert "{CURRENT_USER}/db_knowledge/_index.md" in content, \
            "读取路径应为 {CURRENT_USER}/db_knowledge/_index.md（不含 customer_data/）"

    def test_b4_clickhouse_analyst_report_path_correct(self):
        """clickhouse-analyst.md 报告路径是 superadmin/reports/"""
        content = _read_skill(".claude/skills/user/superadmin/clickhouse-analyst.md")
        assert "superadmin/reports/" in content, \
            "报告路径应为 superadmin/reports/"
        assert "customer_data/superadmin/reports/" not in content, \
            "报告路径不应含 customer_data/ 前缀（会产生双层目录）"

    def test_b5_db_maintainer_write_path_correct(self):
        """db-maintainer.md 写入路径是 {CURRENT_USER}/db_knowledge/"""
        content = _read_skill(".claude/skills/project/db-maintainer.md")
        assert "{CURRENT_USER}/db_knowledge/" in content
        assert "customer_data/{CURRENT_USER}/db_knowledge/" not in content


# ══════════════════════════════════════════════════════════════════════════════
# C — agentic_loop.py system prompt 路径说明正确
# ══════════════════════════════════════════════════════════════════════════════

class TestAgenticLoopPathPrompt:
    """C: agentic_loop._build_system_prompt 路径说明不含错误格式"""

    def _get_prompt(self, username: str = "superadmin") -> str:
        from backend.agents.agentic_loop import AgenticLoop
        mock_mcp = MagicMock()
        mock_mcp.list_servers.return_value = [
            {"name": "filesystem", "type": "filesystem", "tool_count": 5}
        ]
        from backend.config.settings import settings
        fs_obj = MagicMock()
        fs_obj.allowed_directories = list(settings.allowed_directories) if settings.allowed_directories else []
        mock_mcp.servers = {"filesystem": fs_obj}

        loop = AgenticLoop(llm_adapter=MagicMock(), mcp_manager=mock_mcp)

        import asyncio
        return asyncio.get_event_loop().run_until_complete(
            loop._build_system_prompt(context={"username": username}, message="写报告")
        )

    def test_c1_customer_data_prefix_only_in_bad_example(self):
        """system prompt 中 customer_data/{当前用户}/ 仅出现在"错误格式"说明中，不出现在推荐路径里"""
        prompt = self._get_prompt("superadmin")
        # 找到含 customer_data/{当前用户}/ 的行
        bad_lines = [
            l for l in prompt.splitlines()
            if "customer_data/{当前用户}/" in l
            # 含"错误格式"的行是反例说明，是正确的
            and "错误格式" not in l
        ]
        assert not bad_lines, (
            f"customer_data/{{当前用户}}/ 出现在非反例行中:\n"
            + "\n".join(bad_lines)
        )

    def test_c2_correct_format_hint_in_prompt(self):
        """system prompt 中路径示例是 {当前用户}/文件名.md 格式"""
        prompt = self._get_prompt("superadmin")
        assert "{当前用户}/文件名.md" in prompt, \
            f"system prompt 应含 {{当前用户}}/文件名.md 格式示例:\n{prompt[700:1100]}"

    def test_c3_double_prefix_warning_in_prompt(self):
        """system prompt 中含有关于禁止重复 customer_data/ 的说明"""
        prompt = self._get_prompt("superadmin")
        assert "customer_data/" in prompt and ("重复" in prompt or "双层" in prompt), \
            "system prompt 应有禁止重复 customer_data/ 的警告说明"

    def test_c4_absolute_path_used_in_path_rule(self):
        """path_rule 中使用绝对路径（由 settings.allowed_directories 生成）"""
        from backend.config.settings import settings
        if not settings.allowed_directories:
            pytest.skip("allowed_directories 未配置")

        prompt = self._get_prompt("superadmin")
        # 绝对路径中应含用户名（避免 LLM 再加 customer_data/ 前缀）
        assert "superadmin" in prompt, \
            "path_rule 应包含用户名路径"


# ══════════════════════════════════════════════════════════════════════════════
# D — MCP 路径解析行为模拟
# ══════════════════════════════════════════════════════════════════════════════

class TestMCPPathResolution:
    """D: 模拟 MCP Filesystem Server 路径解析，验证正确路径 vs 双层路径"""

    def _resolve(self, mcp_root: Path, user_path: str) -> Path:
        """模拟 MCP 将相对路径解析为绝对路径（基于 mcp_root）"""
        p = Path(user_path)
        if p.is_absolute():
            return p
        return (mcp_root / user_path).resolve()

    def test_d1_correct_format_resolves_to_right_dir(self, tmp_path):
        """正确格式 superadmin/reports/ → customer_data/superadmin/reports/"""
        customer_data = tmp_path / "customer_data"
        customer_data.mkdir()

        result = self._resolve(customer_data, "superadmin/reports/analysis.md")
        expected = customer_data / "superadmin" / "reports" / "analysis.md"
        assert result == expected.resolve(), f"期望 {expected}，得到 {result}"

    def test_d2_wrong_format_causes_double_dir(self, tmp_path):
        """错误格式 customer_data/superadmin/reports/ → customer_data/customer_data/superadmin/reports/（双层！）"""
        customer_data = tmp_path / "customer_data"
        customer_data.mkdir()

        result = self._resolve(customer_data, "customer_data/superadmin/reports/analysis.md")
        double_path = customer_data / "customer_data" / "superadmin" / "reports" / "analysis.md"
        assert result == double_path.resolve(), \
            f"双层路径验证：期望 {double_path}，得到 {result}"

    def test_d3_db_knowledge_correct_format(self, tmp_path):
        """db_knowledge 正确格式 {user}/db_knowledge/ → customer_data/{user}/db_knowledge/"""
        customer_data = tmp_path / "customer_data"
        customer_data.mkdir()

        result = self._resolve(customer_data, "superadmin/db_knowledge/_index.md")
        expected = customer_data / "superadmin" / "db_knowledge" / "_index.md"
        assert result == expected.resolve()

    def test_d4_absolute_path_not_affected_by_mcp_root(self, tmp_path):
        """绝对路径直接使用，不受 mcp_root 影响"""
        customer_data = tmp_path / "customer_data"
        customer_data.mkdir()
        user_dir = customer_data / "superadmin"
        user_dir.mkdir()
        abs_path = str(user_dir / "report.md")

        result = self._resolve(customer_data, abs_path)
        assert result == Path(abs_path).resolve()


# ══════════════════════════════════════════════════════════════════════════════
# E — 回归：修复后路径格式与下载 API 兼容
# ══════════════════════════════════════════════════════════════════════════════

class TestDownloadAPICompatibility:
    """E: 修复后的相对路径格式（不含 customer_data/ 前缀）可正常被下载 API 使用"""

    @pytest.fixture(autouse=True)
    def setup(self):
        from fastapi.testclient import TestClient
        from backend.main import app
        import api.files as files_mod

        self.client = TestClient(app)
        self.customer_root = files_mod._CUSTOMER_DATA_ROOT
        self.default_dir = self.customer_root / "default"
        self.default_dir.mkdir(parents=True, exist_ok=True)
        self._created = []
        yield
        for f in self._created:
            Path(f).unlink(missing_ok=True)

    def _create_file(self, filename: str, content: str = "test") -> str:
        p = self.default_dir / filename
        p.write_text(content, encoding="utf-8")
        self._created.append(str(p))
        return filename

    def test_e1_username_slash_filename_downloads_ok(self):
        """修复后格式 'default/filename.txt' → 下载 API 200"""
        fname = self._create_file("_t_fix_e1.txt", "content e1")
        resp = self.client.get(
            "/api/v1/files/download",
            params={"path": f"default/{fname}"},
        )
        assert resp.status_code == 200, f"got {resp.status_code}"
        assert b"content e1" in resp.content

    def test_e2_no_double_customer_data_in_path(self):
        """'customer_data/default/filename.txt' 与 'default/filename.txt' 均能正常访问"""
        fname = self._create_file("_t_fix_e2.csv", "a,b\n1,2")
        # 格式1（含前缀）
        resp1 = self.client.get("/api/v1/files/download",
                                params={"path": f"customer_data/default/{fname}"})
        # 格式2（修复后，不含前缀）
        resp2 = self.client.get("/api/v1/files/download",
                                params={"path": f"default/{fname}"})
        assert resp1.status_code == 200, f"格式1失败: {resp1.status_code}"
        assert resp2.status_code == 200, f"格式2失败: {resp2.status_code}"

    def test_e3_double_prefix_path_is_rejected(self):
        """'customer_data/customer_data/default/filename.txt' → 403（路径越界被拦截）"""
        fname = self._create_file("_t_fix_e3.txt", "data")
        # 双层路径：下载 API 会验证失败（403 或 404）
        resp = self.client.get(
            "/api/v1/files/download",
            params={"path": f"customer_data/customer_data/default/{fname}"},
        )
        assert resp.status_code in (403, 404), \
            f"双层路径应被拒绝（403/404），实际: {resp.status_code}"
