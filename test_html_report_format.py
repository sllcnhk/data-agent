"""
test_html_report_format.py
==========================
Tests for Bug 2 fix: chart requests must produce HTML reports, not MD files.

Sections:
  E1 — clickhouse-analyst.md 决策规则内容验证
  E2 — agentic_loop.py 系统提示路径规则验证
  E3 — 回归：现有核心测试套件仍通过
"""
from __future__ import annotations

import os
import re
import sys
import unittest
from pathlib import Path

# ---------------------------------------------------------------------------
# Project root on sys.path
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

SKILL_FILE = PROJECT_ROOT / ".claude/skills/project/clickhouse-analyst.md"
AGENTIC_LOOP = PROJECT_ROOT / "backend/agents/agentic_loop.py"


# ===========================================================================
# E1 — clickhouse-analyst.md 决策规则内容验证
# ===========================================================================
class TestSkillFileDecisionRule(unittest.TestCase):
    """E1: clickhouse-analyst.md 顶部必须有强制 HTML 决策规则。"""

    def setUp(self):
        self.content = SKILL_FILE.read_text(encoding="utf-8")

    # ── E1-1 ──
    def test_decision_rule_section_exists(self):
        """E1-1: 文件包含 '零、输出格式强制决策规则' 节。"""
        self.assertIn("零、输出格式强制决策规则", self.content,
                      "clickhouse-analyst.md 缺少强制输出格式决策规则节")

    # ── E1-2 ──
    def test_html_mandatory_for_chart_keywords(self):
        """E1-2: 规则节中声明图表关键词 → 必须调用 report__create 动态报表。"""
        # 决策树已更新为调用 report__create（动态报表）而非直接生成静态 HTML
        self.assertTrue(
            "report__create" in self.content or "必须生成 HTML 报表文件" in self.content,
            "规则节应声明图表 → report__create 或 HTML 报表"
        )

    # ── E1-3 ──
    def test_md_forbidden_for_chart_reports(self):
        """E1-3: 规则节中声明禁止为图表生成 .md 文件。"""
        self.assertIn("禁止生成 `.md` 图表报告", self.content)

    # ── E1-4 ──
    def test_chart_keywords_listed(self):
        """E1-4: 规则节包含堆积图/折线图/柱状图等关键词示例。"""
        for kw in ["堆积图", "折线图", "柱状图", "面积图", "饼图"]:
            self.assertIn(kw, self.content, f"clickhouse-analyst.md 缺少关键词: {kw}")

    # ── E1-5 ──
    def test_decision_tree_present(self):
        """E1-5: 决策树包含图表 → 动态报表分支（report__create 或 HTML 报表）。"""
        has_old = "是 → 必须生成 HTML 报表文件" in self.content
        has_new = "report__create" in self.content and "动态报表" in self.content
        self.assertTrue(has_old or has_new,
                        "决策树应有图表 → 生成报表的分支")

    # ── E1-6 ──
    def test_reports_path_mentioned(self):
        """E1-6: HTML 报表路径提示指向 reports/ 子目录。"""
        self.assertIn("reports/", self.content)

    # ── E1-7 ──
    def test_decision_rule_before_section_one(self):
        """E1-7: 零节出现在 '一、环境映射' 之前。"""
        pos_zero = self.content.find("零、输出格式强制决策规则")
        pos_one = self.content.find("一、环境映射")
        self.assertGreater(pos_one, pos_zero,
                           "决策规则节必须出现在一、环境映射之前")

    # ── E1-8 ──
    def test_forced_rules_numbered(self):
        """E1-8: 包含'强制规则'列表（不得以数据量理由跳过）。"""
        self.assertIn("数据量太大", self.content,
                      "应有明文禁止以数据量为由跳过 HTML 的规则")

    # ── E1-9 ──
    def test_generation_combination_rule(self):
        """E1-9: 包含 '生成报表+图表 → HTML' 组合规则说明。"""
        self.assertIn("生成报表", self.content)
        # 确保 HTML 明确说明
        self.assertIn("HTML", self.content)


# ===========================================================================
# E2 — agentic_loop.py 系统提示路径规则验证
# ===========================================================================
class TestAgenticLoopPathRule(unittest.TestCase):
    """E2: agentic_loop.py path_rule 必须区分 HTML 报表与 MD 分析报告。"""

    def setUp(self):
        self.content = AGENTIC_LOOP.read_text(encoding="utf-8")

    # ── E2-1 ──
    def test_html_chart_report_path_hint(self):
        """E2-1: path_rule 包含 HTML 图表报表 → reports/ 路径提示。"""
        self.assertIn("HTML 图表报表", self.content)

    # ── E2-2 ──
    def test_html_reports_subdir_mentioned(self):
        """E2-2: path_rule 包含 /reports/ 子目录提示。"""
        self.assertIn("user_data_root}/reports/", self.content)

    # ── E2-3 ──
    def test_chart_type_keywords_in_hint(self):
        """E2-3: path_rule ⚠️ 警告中包含图表关键词列表。"""
        self.assertIn("堆积图", self.content)
        self.assertIn("折线图", self.content)
        self.assertIn("柱状图", self.content)

    # ── E2-4 ──
    def test_html_mandatory_warning_in_path_rule(self):
        """E2-4: 包含 '必须生成 .html 文件，禁止用 .md 替代'。"""
        self.assertIn("必须生成 .html 文件，禁止用 .md 替代", self.content)

    # ── E2-5 ──
    def test_md_report_path_hint_exists(self):
        """E2-5: path_rule 保留纯文字 MD 报告路径提示（兼容性）。"""
        self.assertIn("纯文字分析报告", self.content)

    # ── E2-6 ──
    def test_csv_json_path_hint_preserved(self):
        """E2-6: CSV/JSON 数据文件路径提示仍存在。"""
        self.assertIn("CSV/JSON/SQL", self.content)

    # ── E2-7 ──
    def test_fen_xi_jieguo_removed_from_csv_line(self):
        """E2-7: CSV/JSON 行不再包含模糊的'分析结果'（避免误导 MD）。"""
        # 找到 CSV/JSON/SQL 的路径提示行，确认该行不包含 "分析结果"
        csv_match = re.search(r"CSV/JSON/SQL.*?\n", self.content)
        if csv_match:
            self.assertNotIn("分析结果", csv_match.group(0),
                             "CSV/JSON 路径行不应包含'分析结果'（会误导生成 MD）")


# ===========================================================================
# E3 — 回归：YAML 前端、技能触发规则未被破坏
# ===========================================================================
class TestRegressionSkillYaml(unittest.TestCase):
    """E3: clickhouse-analyst.md YAML 前端格式正确，触发词未被删除。"""

    def setUp(self):
        self.content = SKILL_FILE.read_text(encoding="utf-8")

    # ── E3-1 ──
    def test_yaml_frontmatter_intact(self):
        """E3-1: YAML 前端有效（以 --- 开头和关闭）。"""
        self.assertTrue(self.content.startswith("---\n"),
                        "YAML 前端必须以 --- 开头")
        # 第二个 --- 结束前端
        second_dash = self.content.index("---\n", 4)
        self.assertGreater(second_dash, 4)

    # ── E3-2 ──
    def test_triggers_field_present(self):
        """E3-2: triggers 字段存在且非空。"""
        self.assertIn("triggers:", self.content)
        self.assertIn("  - clickhouse", self.content)

    # ── E3-3 ──
    def test_key_triggers_preserved(self):
        """E3-3: 外呼/呼叫等业务触发词未被误删。"""
        for kw in ["外呼", "呼叫", "接通", "计费"]:
            self.assertIn(kw, self.content,
                          f"触发词 '{kw}' 不应从 clickhouse-analyst.md 中删除")

    # ── E3-4 ──
    def test_section_seven_report_spec_intact(self):
        """E3-4: 七节 REPORT_SPEC 规范仍存在。"""
        self.assertIn("REPORT_SPEC", self.content,
                      "七节 HTML 报表 REPORT_SPEC 标记规范不应被删除")

    # ── E3-5 ──
    def test_environment_mapping_intact(self):
        """E3-5: 环境映射表（SG/IDN/BR 等）未被删除。"""
        for env in ["SG", "IDN", "BR", "MY", "THAI", "MX"]:
            self.assertIn(env, self.content,
                          f"环境标识 '{env}' 不应从 clickhouse-analyst.md 中删除")


# ===========================================================================
# E4 — agentic_loop.py 其他路径规则回归
# ===========================================================================
class TestAgenticLoopRegressionPathRule(unittest.TestCase):
    """E4: path_rule 原有技能文件路径提示仍保留。"""

    def setUp(self):
        self.content = AGENTIC_LOOP.read_text(encoding="utf-8")

    # ── E4-1 ──
    def test_skill_file_path_hint_preserved(self):
        """E4-1: 用户技能文件路径提示（.md SKILL格式）仍存在。"""
        self.assertIn("用户技能文件（*.md SKILL格式）", self.content)

    # ── E4-2 ──
    def test_skill_path_username_guard_preserved(self):
        """E4-2: 严禁省略用户名层级的警告仍存在。"""
        self.assertIn("严禁省略用户名层级", self.content)

    # ── E4-3 ──
    def test_customer_data_write_block_preserved(self):
        """E4-3: 严禁将技能文件写入 customer_data/ 的警告仍存在。"""
        self.assertIn("严禁将技能文件写入 customer_data/", self.content)


if __name__ == "__main__":
    unittest.main(verbosity=2)
