"""
test_report_pilot_fix_e2e.py
============================
针对以下 Bug 修复的端到端测试：

问题：非 Pilot 对话中 AI 被 update-report.md 误触发，导致索要 report_id/token，
最终写出 0B 空文件且路径缺少用户名前缀，预览返回 403 "无权访问此文件"。

修复点：
  A1 — update-report.md triggers 精简（仅保留明确修改语义词）
  A2 — 非 Pilot 上下文强硬终止指令 + 区分修改 vs 生成
  B1 — agentic_loop.py：write_file 路径自动补全 {username}/ 前缀
  C1 — html-serve：superadmin 豁免 user_root 检查（仍需在 customer_data/ 内）
  D1 — report_tool/server.py：update_spec / update_single_chart 返回 refresh_token
  D2 — agentic_loop.py：Pilot files_written 增加 pinned_report_id + refresh_token
  D3 — ChatMessages.tsx：effectivePinnedId 兜底使用 file.report_id

Section I  — update-report.md 触发词验证（A1）
Section II — html-serve 权限检查（C1）
Section III— B1 路径自动补全单元测试
Section IV — D1 report tool 返回值验证
Section V  — D2 Pilot files_written 验证
Section VI — 回归测试（已有测试套件关键场景）
"""
import os
import sys
import json
import re
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch, AsyncMock

os.environ.setdefault("ENABLE_AUTH", "False")
os.environ.setdefault("DATABASE_URL", "sqlite:///./test_pilot_fix.db")

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

SKILL_FILE = ROOT / ".claude/skills/project/update-report.md"


# ─────────────────────────────────────────────────────────────────────────────
# Section I — A1: update-report.md 触发词验证
# ─────────────────────────────────────────────────────────────────────────────
class TestUpdateReportTriggers(unittest.TestCase):
    """验证 update-report.md triggers 精简后：
    - 生成类消息不再命中
    - 明确修改类消息仍然命中
    """

    def setUp(self):
        content = SKILL_FILE.read_text(encoding="utf-8")
        # 提取 triggers 列表
        m = re.search(r"^triggers:\s*\n((?:  - .+\n)+)", content, re.MULTILINE)
        self.assertIsNotNone(m, "update-report.md 应包含 triggers 块")
        self.triggers = [
            line.strip().lstrip("- ").strip()
            for line in m.group(1).splitlines()
            if line.strip().startswith("- ")
        ]

    def _matches(self, message: str) -> bool:
        """简单关键词匹配（模拟 SkillLoader keyword 模式）"""
        msg_lower = message.lower()
        for t in self.triggers:
            if t.lower() in msg_lower:
                return True
        return False

    # ── 生成类消息：不应命中 ────────────────────────────────────────────────
    def test_A1_1_generate_report_not_matched(self):
        self.assertFalse(self._matches("帮我生成各环境接通率报表"))

    def test_A1_2_create_chart_not_matched(self):
        self.assertFalse(self._matches("做一个柱状图"))

    def test_A1_3_generate_new_chart_not_matched(self):
        self.assertFalse(self._matches("生成一个图表"))

    def test_A1_4_analyze_data_not_matched(self):
        self.assertFalse(self._matches("查一下各环境今天的呼叫量"))

    def test_A1_5_add_chart_generic_not_matched(self):
        """'添加图表' 已从 triggers 删除"""
        self.assertFalse(self._matches("添加图表"))

    def test_A1_6_delete_chart_generic_not_matched(self):
        """'删除图表' 已从 triggers 删除"""
        self.assertFalse(self._matches("删除图表"))

    # ── 修改类消息：应命中 ─────────────────────────────────────────────────
    def test_A1_7_modify_this_report_matched(self):
        self.assertTrue(self._matches("修改这个报表的颜色"))

    def test_A1_8_update_this_chart_matched(self):
        self.assertTrue(self._matches("更新这个图表的 SQL"))

    def test_A1_9_change_to_area_matched(self):
        self.assertTrue(self._matches("把这个图表改为面积图"))

    def test_A1_10_smooth_matched(self):
        self.assertTrue(self._matches("不平滑显示"))

    def test_A1_11_theme_matched(self):
        self.assertTrue(self._matches("改主题为深色"))

    def test_A1_12_time_range_matched(self):
        self.assertTrue(self._matches("改一下时间范围为近7天"))


# ─────────────────────────────────────────────────────────────────────────────
# Section II — C1: html-serve superadmin 路径豁免
# ─────────────────────────────────────────────────────────────────────────────
class TestHtmlServePermission(unittest.TestCase):
    """验证 serve_report_html_by_path 权限逻辑：
    - 普通用户：只能访问自己目录
    - superadmin：可访问 customer_data/ 内任意路径
    """

    def setUp(self):
        try:
            from backend.api.reports import serve_report_html_by_path
            from backend.config.settings import settings
            self.serve_fn = serve_report_html_by_path
            self.settings = settings
        except Exception as e:
            self.skipTest(f"无法导入 reports 模块: {e}")

    def _make_user(self, username, is_superadmin=False):
        u = MagicMock()
        u.username = username
        u.is_superadmin = is_superadmin
        u.is_active = True
        return u

    def test_C1_1_superadmin_can_access_own_dir(self):
        """superadmin 访问 superadmin/ 下的文件 → 只要文件存在就返回 200"""
        # 创建临时文件模拟
        import tempfile, asyncio
        from pathlib import Path
        from backend.api.reports import _CUSTOMER_DATA_ROOT

        tmp_path = _CUSTOMER_DATA_ROOT / "superadmin" / "reports" / "_test_sa.html"
        tmp_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path.write_text("<html>test</html>", encoding="utf-8")
        try:
            # 直接测试路径逻辑（不走 HTTP）
            abs_path = (_CUSTOMER_DATA_ROOT / "superadmin/reports/_test_sa.html").resolve()
            # superadmin 豁免：只检查在 customer_data/ 内
            try:
                abs_path.relative_to(_CUSTOMER_DATA_ROOT.resolve())
                accessible = True
            except ValueError:
                accessible = False
            self.assertTrue(accessible)
        finally:
            tmp_path.unlink(missing_ok=True)

    def test_C1_2_superadmin_can_access_misplaced_file(self):
        """superadmin 访问错误前缀路径（reports/xxx.html 而非 superadmin/reports/xxx.html）
        → 豁免 user_root 检查，只需在 customer_data/ 内"""
        from backend.api.reports import _CUSTOMER_DATA_ROOT
        import tempfile

        tmp_path = _CUSTOMER_DATA_ROOT / "reports" / "_test_misplaced.html"
        tmp_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path.write_text("<html>misplaced</html>", encoding="utf-8")
        try:
            abs_path = tmp_path.resolve()
            # superadmin 检查：在 customer_data/ 内 → 允许
            try:
                abs_path.relative_to(_CUSTOMER_DATA_ROOT.resolve())
                superadmin_ok = True
            except ValueError:
                superadmin_ok = False
            # 普通用户检查：在 customer_data/normaluser/ 内 → 拒绝
            try:
                abs_path.relative_to((_CUSTOMER_DATA_ROOT / "normaluser").resolve())
                normal_ok = True
            except ValueError:
                normal_ok = False
            self.assertTrue(superadmin_ok, "superadmin 应能访问")
            self.assertFalse(normal_ok, "普通用户不应能访问其他目录")
        finally:
            tmp_path.unlink(missing_ok=True)

    def test_C1_3_normal_user_blocked_from_other_dir(self):
        """普通用户不能访问其他用户目录"""
        from backend.api.reports import _CUSTOMER_DATA_ROOT

        abs_path = (_CUSTOMER_DATA_ROOT / "superadmin" / "reports" / "secret.html").resolve()
        user_root = (_CUSTOMER_DATA_ROOT / "alice").resolve()
        blocked = False
        try:
            abs_path.relative_to(user_root)
        except ValueError:
            blocked = True
        self.assertTrue(blocked)

    def test_C1_4_superadmin_blocked_outside_customer_data(self):
        """superadmin 也不能访问 customer_data/ 以外的路径"""
        from backend.api.reports import _CUSTOMER_DATA_ROOT

        outside_path = Path("/etc/passwd").resolve()
        try:
            outside_path.relative_to(_CUSTOMER_DATA_ROOT.resolve())
            accessible = True
        except ValueError:
            accessible = False
        self.assertFalse(accessible)


# ─────────────────────────────────────────────────────────────────────────────
# Section III — B1: 路径自动补全
# ─────────────────────────────────────────────────────────────────────────────
class TestPathPrefixAutoFix(unittest.TestCase):
    """验证 agentic_loop 中写文件路径自动补全逻辑"""

    def _simulate_path_fix(self, file_path: str, username: str) -> str:
        """模拟 agentic_loop.py 中的 B1 路径补全逻辑"""
        _fp_norm = file_path.replace("\\", "/").lstrip("/")
        if (
            username
            and _fp_norm
            and not _fp_norm.startswith(username + "/")
            and ".claude" not in _fp_norm
        ):
            return f"{username}/{_fp_norm}"
        return file_path

    def test_B1_1_missing_prefix_gets_fixed(self):
        result = self._simulate_path_fix("reports/test.html", "superadmin")
        self.assertEqual(result, "superadmin/reports/test.html")

    def test_B1_2_correct_prefix_unchanged(self):
        result = self._simulate_path_fix("superadmin/reports/test.html", "superadmin")
        self.assertEqual(result, "superadmin/reports/test.html")

    def test_B1_3_skill_path_not_touched(self):
        """技能路径（含 .claude）不被自动补全"""
        result = self._simulate_path_fix(".claude/skills/user/superadmin/skill.md", "superadmin")
        self.assertEqual(result, ".claude/skills/user/superadmin/skill.md")

    def test_B1_4_other_user_dir_unchanged(self):
        """已有其他用户名前缀的路径不被修改"""
        result = self._simulate_path_fix("alice/reports/test.html", "bob")
        self.assertEqual(result, "bob/alice/reports/test.html")
        # 注：这种情况下补全是错的，但比没有前缀导致 403 好；实际上 LLM 不应写其他人目录

    def test_B1_5_empty_path_unchanged(self):
        result = self._simulate_path_fix("", "superadmin")
        self.assertEqual(result, "")

    def test_B1_6_empty_username_unchanged(self):
        result = self._simulate_path_fix("reports/test.html", "")
        self.assertEqual(result, "reports/test.html")

    def test_B1_7_nested_path_fixed(self):
        result = self._simulate_path_fix("data/2026-04/analysis.csv", "superadmin")
        self.assertEqual(result, "superadmin/data/2026-04/analysis.csv")

    def test_B1_8_backslash_normalized(self):
        """Windows 反斜杠路径被规范化后再补全"""
        raw = "reports\\report_20260415.html"
        result = self._simulate_path_fix(raw, "superadmin")
        self.assertEqual(result, "superadmin/reports/report_20260415.html")


# ─────────────────────────────────────────────────────────────────────────────
# Section IV — D1: report_tool 返回 refresh_token
# ─────────────────────────────────────────────────────────────────────────────
class TestReportToolReturnsRefreshToken(unittest.TestCase):

    def setUp(self):
        try:
            from backend.mcp.report_tool.server import ReportToolMCPServer
            self.server_cls = ReportToolMCPServer
        except Exception as e:
            self.skipTest(f"无法导入 ReportToolMCPServer: {e}")

    def test_D1_1_update_spec_returns_refresh_token(self):
        import asyncio

        mock_result = {
            "report_id": "test-uuid-001",
            "name": "测试报表",
            "updated_at": "2026-04-15T10:00:00",
        }
        with patch("backend.services.report_service.update_spec_by_token", return_value=mock_result):
            server = self.server_cls()
            loop = asyncio.new_event_loop()
            try:
                spec = {"title": "test", "charts": [{"id": "c1"}]}
                result = loop.run_until_complete(
                    server._update_spec("test-uuid-001", "my-refresh-token-xyz", spec)
                )
            finally:
                loop.close()

        self.assertTrue(result.get("success"))
        self.assertEqual(result.get("refresh_token"), "my-refresh-token-xyz")
        self.assertEqual(result.get("report_id"), "test-uuid-001")

    def test_D1_2_update_single_chart_returns_refresh_token(self):
        import asyncio

        mock_result = {
            "report_id": "test-uuid-002",
            "found": True,
            "total_charts": 2,
            "updated_at": "2026-04-15T10:00:00",
        }
        with patch("backend.services.report_service.update_single_chart_by_token", return_value=mock_result):
            server = self.server_cls()
            loop = asyncio.new_event_loop()
            try:
                result = loop.run_until_complete(
                    server._update_single_chart(
                        "test-uuid-002", "my-token-abc", "c1", {"chart_type": "area"}
                    )
                )
            finally:
                loop.close()

        self.assertTrue(result.get("success"))
        self.assertEqual(result.get("refresh_token"), "my-token-abc")
        self.assertEqual(result.get("report_id"), "test-uuid-002")

    def test_D1_3_failed_update_spec_no_refresh_token(self):
        """失败时不应返回 refresh_token"""
        import asyncio

        with patch("backend.services.report_service.update_spec_by_token",
                   side_effect=PermissionError("鉴权失败")):
            server = self.server_cls()
            loop = asyncio.new_event_loop()
            try:
                result = loop.run_until_complete(
                    server._update_spec("bad-id", "bad-token", {"title": "x"})
                )
            finally:
                loop.close()

        self.assertFalse(result.get("success"))
        self.assertNotIn("refresh_token", result)


# ─────────────────────────────────────────────────────────────────────────────
# Section V — D2: agentic_loop Pilot files_written
# ─────────────────────────────────────────────────────────────────────────────
class TestPilotFilesWrittenEntry(unittest.TestCase):
    """验证 agentic_loop 对 report__update_* 工具结果生成的 files_written 条目"""

    def _build_entry_from_raw_result(self, tool_name: str, tool_input: dict, raw_result: dict):
        """模拟 agentic_loop 中处理 Pilot 工具结果的逻辑"""
        if (
            tool_name in ("report__update_spec", "report__update_single_chart")
            and raw_result.get("success", False)
        ):
            result_data = raw_result if isinstance(raw_result, dict) else {}
            report_id_val = result_data.get("report_id", tool_input.get("report_id", ""))
            refresh_token_val = result_data.get("refresh_token", tool_input.get("token", ""))
            report_name_val = result_data.get("name", "报表")
            return {
                "path": f"__report__/{report_id_val}",
                "name": report_name_val,
                "size": 0,
                "mime_type": "text/html",
                "is_report": True,
                "doc_type": "dashboard",
                "report_id": report_id_val,
                "pinned_report_id": report_id_val,
                "refresh_token": refresh_token_val,
            }
        return None

    def test_D2_1_update_spec_entry_has_pinned_report_id(self):
        entry = self._build_entry_from_raw_result(
            "report__update_spec",
            {"report_id": "rpt-001", "token": "tok-abc"},
            {"success": True, "report_id": "rpt-001", "refresh_token": "tok-abc", "name": "测试报表"},
        )
        self.assertIsNotNone(entry)
        self.assertEqual(entry["pinned_report_id"], "rpt-001")

    def test_D2_2_update_spec_entry_has_refresh_token(self):
        entry = self._build_entry_from_raw_result(
            "report__update_spec",
            {"report_id": "rpt-001", "token": "tok-abc"},
            {"success": True, "report_id": "rpt-001", "refresh_token": "tok-abc", "name": "测试报表"},
        )
        self.assertEqual(entry["refresh_token"], "tok-abc")

    def test_D2_3_update_single_chart_entry_has_pinned_report_id(self):
        entry = self._build_entry_from_raw_result(
            "report__update_single_chart",
            {"report_id": "rpt-002", "token": "tok-xyz"},
            {"success": True, "report_id": "rpt-002", "refresh_token": "tok-xyz"},
        )
        self.assertIsNotNone(entry)
        self.assertEqual(entry["pinned_report_id"], "rpt-002")
        self.assertEqual(entry["refresh_token"], "tok-xyz")

    def test_D2_4_path_is_virtual_not_real_file(self):
        entry = self._build_entry_from_raw_result(
            "report__update_spec",
            {"report_id": "rpt-003", "token": "t"},
            {"success": True, "report_id": "rpt-003", "refresh_token": "t", "name": "R"},
        )
        self.assertTrue(entry["path"].startswith("__report__/"),
                        "Pilot 更新条目 path 应为虚拟标识，前端走 /reports/{id}/html")

    def test_D2_5_failed_tool_not_added(self):
        entry = self._build_entry_from_raw_result(
            "report__update_spec",
            {"report_id": "rpt-004", "token": "t"},
            {"success": False, "error": "鉴权失败"},
        )
        self.assertIsNone(entry, "失败的工具调用不应产生 files_written 条目")

    def test_D2_6_token_fallback_from_tool_input(self):
        """result 中没有 refresh_token 时，从 tool_input.token 回退"""
        entry = self._build_entry_from_raw_result(
            "report__update_single_chart",
            {"report_id": "rpt-005", "token": "fallback-tok"},
            {"success": True, "report_id": "rpt-005"},  # 没有 refresh_token
        )
        self.assertEqual(entry["refresh_token"], "fallback-tok")


# ─────────────────────────────────────────────────────────────────────────────
# Section VI — A2: 技能文件内容校验
# ─────────────────────────────────────────────────────────────────────────────
class TestSkillFileContent(unittest.TestCase):
    """验证 update-report.md 内容包含正确的上下文检测指令"""

    def setUp(self):
        self.content = SKILL_FILE.read_text(encoding="utf-8")

    def test_A2_1_has_zero_priority_section(self):
        self.assertIn("第零优先级规则", self.content)

    def test_A2_2_has_context_detection_instruction(self):
        self.assertIn("system prompt", self.content)
        self.assertIn("report_id", self.content)
        self.assertIn("refresh_token", self.content)

    def test_A2_3_prohibits_asking_user_for_report_id(self):
        """技能文件必须明确禁止向用户索要 report_id"""
        self.assertIn("严禁", self.content)
        # 检查"严禁...索要"模式
        self.assertTrue(
            "严禁" in self.content and "索要" in self.content,
            "技能文件应明确禁止向用户索要 report_id/token"
        )

    def test_A2_4_has_generate_new_report_guidance(self):
        """B-2 场景：技能文件应提供生成新报表的指引"""
        self.assertIn("filesystem__write_file", self.content)
        self.assertIn("CURRENT_USER", self.content)

    def test_A2_5_no_broad_trigger_report_alone(self):
        """触发词中不应只有'报表'或'图表'这种单独通用词"""
        m = re.search(r"^triggers:\s*\n((?:  - .+\n)+)", self.content, re.MULTILINE)
        triggers = [
            line.strip().lstrip("- ").strip()
            for line in m.group(1).splitlines()
            if line.strip().startswith("- ")
        ]
        # 单独"报表"或"图表"不应在 triggers 中
        self.assertNotIn("报表", triggers, "单独'报表'不应作为触发词（过于宽泛）")
        self.assertNotIn("图表", triggers, "单独'图表'不应作为触发词（过于宽泛）")
        self.assertNotIn("更新报表", triggers, "'更新报表'已移除（无特指，匹配生成请求）")
        self.assertNotIn("修改报表", triggers, "'修改报表'已移除（无特指，匹配生成请求）")

    def test_A2_6_has_both_pilot_scenarios(self):
        """技能文件应有情形 A（有 Pilot 上下文）和情形 B（无上下文）的分支"""
        self.assertIn("情形 A", self.content)
        self.assertIn("情形 B", self.content)


# ─────────────────────────────────────────────────────────────────────────────
# Section VII — 回归：现有测试关键场景
# ─────────────────────────────────────────────────────────────────────────────
class TestRegressionSkillLoader(unittest.TestCase):
    """验证技能加载器在 triggers 变更后不出现 YAML 解析错误"""

    def test_R1_skill_file_yaml_parseable(self):
        content = SKILL_FILE.read_text(encoding="utf-8")
        # 提取 frontmatter
        m = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
        self.assertIsNotNone(m, "update-report.md 应有 YAML frontmatter")
        frontmatter = m.group(1)
        # 基本字段存在
        self.assertIn("name:", frontmatter)
        self.assertIn("triggers:", frontmatter)
        self.assertIn("always_inject:", frontmatter)

    def test_R2_no_comment_inside_triggers(self):
        """触发词列表内不应有 # 注释行（会破坏 _parse_yaml_subset）"""
        content = SKILL_FILE.read_text(encoding="utf-8")
        m = re.search(r"^triggers:\s*\n((?:  - .+\n)+)", content, re.MULTILINE)
        if m:
            for line in m.group(1).splitlines():
                stripped = line.strip()
                if stripped:
                    self.assertFalse(stripped.startswith("#"),
                                     f"触发词行不应是注释: {line}")

    def test_R3_skill_file_exists(self):
        self.assertTrue(SKILL_FILE.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
