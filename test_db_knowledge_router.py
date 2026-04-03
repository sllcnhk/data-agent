"""
test_db_knowledge_router.py
============================
T10: 集成测试 — db-knowledge-router + db-maintainer Skill 注入行为验证

测试分组:
  D: db-knowledge-router 触发与内容验证
  E: clickhouse-analyst 约束规则验证
  F: 环境子 Skill 按消息路由验证
  G: async build_skill_prompt_async 路径验证
"""
import asyncio
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch

PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "backend"))
os.environ.setdefault("ENABLE_AUTH", "False")

from backend.skills.skill_loader import SkillLoader, _detect_env


def run_async(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


class TestDBKnowledgeRouterSkill(unittest.TestCase):
    """D: db-knowledge-router skill injection behavior."""

    def setUp(self):
        self.loader = SkillLoader()
        self.loader.load_all()

    def test_d1_router_triggers_on_clickhouse_query(self):
        triggered = self.loader.find_triggered("clickhouse data query")
        names = [s.name for s in triggered]
        self.assertIn("db-knowledge-router", names)

    def test_d2_router_triggers_on_analysis_chinese(self):
        # db-knowledge-router triggers include 'clickhouse' and '外呼'
        triggered = self.loader.find_triggered("clickhouse 外呼 data query")
        names = [s.name for s in triggered]
        self.assertIn("db-knowledge-router", names)

    def test_d3_router_not_triggered_on_unrelated(self):
        triggered = self.loader.find_triggered("write a python function")
        names = [s.name for s in triggered]
        self.assertNotIn("db-knowledge-router", names)

    def test_d4_maintainer_triggers_on_update_english(self):
        triggered = self.loader.find_triggered("update db_knowledge tables")
        names = [s.name for s in triggered]
        self.assertIn("db-maintainer", names)

    def test_d5_maintainer_triggers_on_refresh(self):
        triggered = self.loader.find_triggered("refresh db_knowledge")
        names = [s.name for s in triggered]
        self.assertIn("db-maintainer", names)

    def test_d6_router_content_has_step1(self):
        skill = self.loader._project_skills.get("db-knowledge-router")
        self.assertIsNotNone(skill)
        self.assertIn("_index.md", skill.content)
        self.assertIn("Step 1", skill.content)

    def test_d7_router_content_prohibits_list_tables(self):
        skill = self.loader._project_skills.get("db-knowledge-router")
        self.assertIsNotNone(skill)
        self.assertIn("list_tables", skill.content)
        # Should mention it in prohibition context
        content_lower = skill.content.lower()
        has_prohibition = "forbidden" in content_lower or "prohibited" in content_lower or "禁止" in skill.content
        self.assertTrue(has_prohibition)

    def test_d8_maintainer_content_has_workflow_steps(self):
        skill = self.loader._project_skills.get("db-maintainer")
        self.assertIsNotNone(skill)
        self.assertIn("Step", skill.content)
        self.assertIn("_index.md", skill.content)


class TestClickhouseAnalystRules(unittest.TestCase):
    """E: clickhouse-analyst constraint rule validation."""

    def setUp(self):
        self.loader = SkillLoader()
        self.loader.load_all()

    def test_e1_analyst_has_prohibition_rule(self):
        skill = self.loader._user_skills.get("clickhouse-analyst")
        self.assertIsNotNone(skill)
        self.assertIn("STRICTLY FORBIDDEN", skill.content)

    def test_e2_analyst_references_current_user_path(self):
        """Path must use {CURRENT_USER} not hardcoded username."""
        skill = self.loader._user_skills.get("clickhouse-analyst")
        self.assertIsNotNone(skill)
        self.assertIn("{CURRENT_USER}", skill.content)
        # Should NOT have hardcoded 'superadmin' in path context
        # (the path customer_data/superadmin/ should be replaced)
        import re
        hardcoded = re.search(r'customer_data/superadmin/', skill.content)
        self.assertIsNone(hardcoded, "Hardcoded superadmin path found in skill content")

    def test_e3_analyst_declares_sub_skills(self):
        skill = self.loader._user_skills.get("clickhouse-analyst")
        self.assertIsNotNone(skill)
        expected_subs = [
            "ch-sg-specific", "ch-idn-specific", "ch-call-metrics", "ch-billing-analysis"
        ]
        for sub in expected_subs:
            self.assertIn(sub, skill.sub_skills,
                          f"Expected sub_skill '{sub}' in clickhouse-analyst")

    def test_e4_analyst_prompt_injection_contains_prohibition(self):
        """Skill object must have STRICTLY FORBIDDEN; summary mode lists skill by name."""
        # Full content check via skill object (d3 already covers this, confirm here)
        skill = self.loader._user_skills.get("clickhouse-analyst")
        self.assertIsNotNone(skill)
        self.assertIn("STRICTLY FORBIDDEN", skill.content)
        # Assembled prompt: either full content or summary mode — either way skill is present
        result = self.loader.build_skill_prompt("clickhouse query analysis")
        self.assertIn("clickhouse-analyst", result)  # appears in both modes

    def test_e5_base_safety_always_injected(self):
        """_base-safety skill must be listed in assembled prompt (full or summary mode)."""
        result = self.loader.build_skill_prompt("clickhouse data")
        # In summary mode: "- **_base-safety** [system]:" appears
        # In full mode: "## 专业技能：_base-safety" appears
        self.assertIn("_base-safety", result)


class TestEnvSubSkillRouting(unittest.TestCase):
    """F: Environment-specific sub-skill loading."""

    def setUp(self):
        self.loader = SkillLoader()
        self.loader.load_all()

    def test_f1_sg_message_loads_sg_sub_skill(self):
        result = self.loader.build_skill_prompt("SG clickhouse analysis")
        self.assertIn("ch-sg-specific", result)

    def test_f2_sg_message_no_idn_sub_skill(self):
        result = self.loader.build_skill_prompt("SG clickhouse analysis")
        self.assertNotIn("ch-idn-specific", result)

    def test_f3_idn_message_loads_idn_sub_skill(self):
        result = self.loader.build_skill_prompt("IDN clickhouse data query")
        self.assertIn("ch-idn-specific", result)

    def test_f4_idn_message_no_sg_sub_skill(self):
        result = self.loader.build_skill_prompt("IDN clickhouse data query")
        self.assertNotIn("ch-sg-specific", result)

    def test_f5_br_message_loads_br_sub_skill(self):
        result = self.loader.build_skill_prompt("Brazil clickhouse report")
        self.assertIn("ch-br-specific", result)

    def test_f6_billing_keywords_load_billing_sub_skill(self):
        result = self.loader.build_skill_prompt("clickhouse billing analysis")
        self.assertIn("ch-billing-analysis", result)

    def test_f7_no_env_no_env_specific_sub_skill(self):
        """Without env keyword, no env-specific sub-skills loaded."""
        result = self.loader.build_skill_prompt("clickhouse analysis query")
        # None of the env-specific skills should appear (no env keyword)
        for env_skill in ["ch-sg-specific", "ch-idn-specific", "ch-br-specific"]:
            self.assertNotIn(env_skill, result,
                             f"{env_skill} should not be loaded without env keyword")

    def test_f8_env_sub_skill_has_server_info(self):
        """SG sub-skill content must mention server name."""
        skill = self.loader._user_skills.get("ch-sg-specific")
        self.assertIsNotNone(skill)
        self.assertIn("clickhouse-sg", skill.content)


class TestAsyncBuildSkillPrompt(unittest.TestCase):
    """G: async build_skill_prompt_async with sub-skill expansion."""

    def setUp(self):
        self.loader = SkillLoader()
        self.loader.load_all()

    def test_g1_async_keyword_mode_loads_sub_skills(self):
        """In keyword mode, async path also expands sub-skills."""
        mock_settings = MagicMock()
        mock_settings.skill_match_mode = "keyword"
        mock_settings.skill_semantic_threshold = 0.45

        with patch("backend.config.settings.settings", mock_settings):
            result = run_async(
                self.loader.build_skill_prompt_async("SG clickhouse analysis", llm_adapter=None)
            )
        self.assertIn("ch-sg-specific", result)

    def test_g2_async_hybrid_mode_loads_sub_skills(self):
        """In hybrid mode, sub-skill expansion works after keyword phase."""
        mock_settings = MagicMock()
        mock_settings.skill_match_mode = "hybrid"
        mock_settings.skill_semantic_threshold = 0.45

        mock_llm = MagicMock()
        mock_llm.chat_plain = AsyncMock(return_value="{}")

        with patch("backend.config.settings.settings", mock_settings):
            result = run_async(
                self.loader.build_skill_prompt_async(
                    "SG clickhouse analysis", llm_adapter=mock_llm
                )
            )
        self.assertIn("ch-sg-specific", result)

    def test_g3_async_match_info_populated(self):
        """After async call, _last_match_info should be populated."""
        mock_settings = MagicMock()
        mock_settings.skill_match_mode = "keyword"
        mock_settings.skill_semantic_threshold = 0.45

        with patch("backend.config.settings.settings", mock_settings):
            run_async(
                self.loader.build_skill_prompt_async("SG clickhouse data", llm_adapter=None)
            )
        info = self.loader.get_last_match_info()
        self.assertIn("matched", info)
        matched_names = [m["name"] for m in info["matched"]]
        self.assertIn("clickhouse-analyst", matched_names)


if __name__ == "__main__":
    unittest.main(verbosity=2)
