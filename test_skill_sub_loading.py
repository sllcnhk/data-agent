"""
test_skill_sub_loading.py
=========================
T9: 单元测试 — SkillMD 新字段 + Sub-skill 动态加载 + _detect_env()

测试分组:
  A: SkillMD 新字段解析（T5）
  B: Sub-skill 动态加载（T6）
  C: _detect_env() 环境检测（T6）
  D: P0 Skill 内容验证（T1-T4）
"""
import os
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

# 确保项目根目录在 sys.path
PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "backend"))
os.environ.setdefault("ENABLE_AUTH", "False")


from backend.skills.skill_loader import SkillLoader, SkillMD, _detect_env, TIER_USER, TIER_PROJECT, TIER_SYSTEM


def _make_skill_file(directory: Path, filename: str, frontmatter: str, body: str = "## Content\ntest") -> Path:
    """Helper: write a skill .md file and return its path."""
    path = directory / filename
    content = f"---\n{frontmatter}\n---\n\n{body}"
    path.write_text(content, encoding="utf-8")
    return path


# ──────────────────────────────────────────────────────────
# A: SkillMD 新字段解析
# ──────────────────────────────────────────────────────────

class TestSkillMDNewFields(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.system_dir = Path(self.tmpdir) / "system"
        self.project_dir = Path(self.tmpdir) / "project"
        self.user_dir = Path(self.tmpdir) / "user"
        for d in [self.system_dir, self.project_dir, self.user_dir]:
            d.mkdir()
        self.loader = SkillLoader(skills_dir=self.tmpdir)

    def test_a1_scope_field_parsed(self):
        _make_skill_file(
            self.project_dir, "test-scope.md",
            "name: test-scope\ntriggers:\n  - test\nscope: env-sg\ncategory: analytics\npriority: high\nalways_inject: false"
        )
        self.loader.load_all()
        skill = self.loader._project_skills.get("test-scope")
        self.assertIsNotNone(skill)
        self.assertEqual(skill.scope, "env-sg")

    def test_a2_layer_field_parsed(self):
        _make_skill_file(
            self.project_dir, "test-layer.md",
            "name: test-layer\ntriggers:\n  - test\nlayer: workflow\ncategory: analytics\npriority: high\nalways_inject: false"
        )
        self.loader.load_all()
        skill = self.loader._project_skills.get("test-layer")
        self.assertIsNotNone(skill)
        self.assertEqual(skill.layer, "workflow")

    def test_a3_sub_skills_field_parsed(self):
        _make_skill_file(
            self.user_dir, "test-parent.md",
            "name: test-parent\ntriggers:\n  - parent\nsub_skills:\n  - child-a\n  - child-b\ncategory: analytics\npriority: high\nalways_inject: false"
        )
        self.loader.load_all()
        skill = self.loader._user_skills.get("test-parent")
        self.assertIsNotNone(skill)
        self.assertEqual(skill.sub_skills, ["child-a", "child-b"])

    def test_a4_env_tags_field_parsed(self):
        _make_skill_file(
            self.user_dir, "test-env.md",
            "name: test-env\ntriggers:\n  - env\nenv_tags:\n  - sg\n  - idn\ncategory: analytics\npriority: high\nalways_inject: false"
        )
        self.loader.load_all()
        skill = self.loader._user_skills.get("test-env")
        self.assertIsNotNone(skill)
        self.assertEqual(skill.env_tags, ["sg", "idn"])

    def test_a5_new_fields_default_when_absent(self):
        """Skills without new fields should have empty defaults."""
        _make_skill_file(
            self.project_dir, "test-plain.md",
            "name: test-plain\ntriggers:\n  - plain\ncategory: analytics\npriority: medium\nalways_inject: false"
        )
        self.loader.load_all()
        skill = self.loader._project_skills.get("test-plain")
        self.assertIsNotNone(skill)
        self.assertEqual(skill.scope, "")
        self.assertEqual(skill.layer, "")
        self.assertEqual(skill.sub_skills, [])
        self.assertEqual(skill.env_tags, [])


# ──────────────────────────────────────────────────────────
# B: Sub-skill 动态加载
# ──────────────────────────────────────────────────────────

class TestSubSkillLoading(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.system_dir = Path(self.tmpdir) / "system"
        self.project_dir = Path(self.tmpdir) / "project"
        self.user_dir = Path(self.tmpdir) / "user"
        for d in [self.system_dir, self.project_dir, self.user_dir]:
            d.mkdir()
        self.loader = SkillLoader(skills_dir=self.tmpdir)

    def _setup_parent_child(self, child_env_tags=None):
        """Create a parent (user tier) with two sub_skills."""
        sub_fm = "name: child-skill\ntriggers:\n  - child\ncategory: analytics\npriority: high\nalways_inject: false"
        if child_env_tags:
            sub_fm += f"\nenv_tags:\n" + "".join(f"  - {t}\n" for t in child_env_tags)
        _make_skill_file(self.user_dir, "child-skill.md", sub_fm)

        parent_fm = (
            "name: parent-skill\ntriggers:\n  - parent\nsub_skills:\n  - child-skill\n"
            "category: analytics\npriority: high\nalways_inject: false\nlayer: workflow"
        )
        _make_skill_file(self.user_dir, "parent-skill.md", parent_fm)
        self.loader.load_all()

    def test_b1_sub_skill_loaded_when_parent_matched(self):
        """When parent is matched by keyword, its declared sub_skill is also loaded."""
        self._setup_parent_child()
        result = self.loader.build_skill_prompt("query about parent stuff")
        self.assertIn("parent-skill", result)
        self.assertIn("child-skill", result)

    def test_b2_sub_skill_filtered_by_env_tag_match(self):
        """Sub-skill with env_tags=[sg] IS loaded when message mentions sg."""
        self._setup_parent_child(child_env_tags=["sg"])
        result = self.loader.build_skill_prompt("parent query for sg environment")
        self.assertIn("child-skill", result)

    def test_b3_sub_skill_filtered_by_env_tag_no_match(self):
        """Sub-skill with env_tags=[idn] is NOT loaded when message mentions sg."""
        self._setup_parent_child(child_env_tags=["idn"])
        result = self.loader.build_skill_prompt("parent query for sg environment")
        # child-skill has env_tags=[idn], message is sg -> should NOT appear
        self.assertNotIn("child-skill", result)

    def test_b4_sub_skill_not_duplicate(self):
        """If sub_skill is already keyword-matched, it's not added twice."""
        self._setup_parent_child()
        # Message matches both parent AND child by keywords
        result = self.loader.build_skill_prompt("parent child query")
        # Should appear exactly once
        self.assertEqual(result.count("child-skill"), result.count("## 专业技能：child-skill") if "## 专业技能：child-skill" in result else result.count("child-skill"))
        # More precisely: the injection header should appear only once
        import re
        matches = re.findall(r'专业技能：child-skill', result)
        self.assertLessEqual(len(matches), 1)

    def test_b5_sub_skill_missing_does_not_crash(self):
        """If declared sub_skill doesn't exist, expansion silently skips it."""
        parent_fm = (
            "name: orphan-parent\ntriggers:\n  - orphan\nsub_skills:\n  - nonexistent-skill\n"
            "category: analytics\npriority: high\nalways_inject: false"
        )
        _make_skill_file(self.user_dir, "orphan-parent.md", parent_fm)
        self.loader.load_all()
        # Should not raise
        result = self.loader.build_skill_prompt("orphan query")
        self.assertIn("orphan-parent", result)
        self.assertNotIn("nonexistent-skill", result)

    def test_b6_sub_skill_under_char_cap(self):
        """Even with sub-skills, total injection stays under _MAX_INJECT_CHARS or switches to summary mode."""
        from backend.skills.skill_loader import _MAX_INJECT_CHARS
        self._setup_parent_child()
        result = self.loader.build_skill_prompt("parent child query")
        # Either full content or summary mode (摘要模式)
        self.assertTrue(len(result) <= _MAX_INJECT_CHARS or "摘要模式" in result)

    def test_b7_expand_sub_skills_multi_tier(self):
        """Sub-skill in project tier is added to project list when parent matches."""
        sub_fm = "name: proj-child\ntriggers:\n  - projchild\ncategory: analytics\npriority: high\nalways_inject: false"
        _make_skill_file(self.project_dir, "proj-child.md", sub_fm)
        parent_fm = (
            "name: user-parent\ntriggers:\n  - userparent\nsub_skills:\n  - proj-child\n"
            "category: analytics\npriority: high\nalways_inject: false"
        )
        _make_skill_file(self.user_dir, "user-parent.md", parent_fm)
        self.loader.load_all()
        result = self.loader.build_skill_prompt("userparent query")
        self.assertIn("user-parent", result)
        self.assertIn("proj-child", result)


# ──────────────────────────────────────────────────────────
# C: _detect_env() 环境检测
# ──────────────────────────────────────────────────────────

class TestDetectEnv(unittest.TestCase):

    def test_c1_sg_english(self):
        self.assertEqual(_detect_env("query clickhouse sg data"), "sg")

    def test_c2_sg_chinese(self):
        self.assertEqual(_detect_env("查询新加坡环境数据"), "sg")

    def test_c3_sg_azure(self):
        self.assertEqual(_detect_env("SG_AZURE environment analysis"), "sg")

    def test_c4_idn(self):
        self.assertEqual(_detect_env("IDN data analysis"), "idn")

    def test_c5_idn_chinese(self):
        self.assertEqual(_detect_env("印尼外呼数据"), "idn")

    def test_c6_br(self):
        self.assertEqual(_detect_env("Brazil billing"), "br")

    def test_c7_my(self):
        self.assertEqual(_detect_env("malaysia report"), "my")

    def test_c8_thai(self):
        self.assertEqual(_detect_env("thailand clickhouse"), "thai")

    def test_c9_mx(self):
        self.assertEqual(_detect_env("MX environment data"), "mx")

    def test_c10_none_unrelated(self):
        self.assertIsNone(_detect_env("what is the weather today"))

    def test_c11_none_empty(self):
        self.assertIsNone(_detect_env(""))

    def test_c12_case_insensitive(self):
        self.assertEqual(_detect_env("SG ANALYSIS"), "sg")
        self.assertEqual(_detect_env("BRAZIL DATA"), "br")


# ──────────────────────────────────────────────────────────
# D: P0 Skill 内容验证（T1-T4 结果）
# ──────────────────────────────────────────────────────────

class TestP0SkillContent(unittest.TestCase):
    """Verify that P0 skill content changes are correctly applied to real project files."""

    def setUp(self):
        self.loader = SkillLoader()  # real project skills dir
        self.loader.load_all()

    def test_d1_base_safety_has_knowledge_first_rule(self):
        """_base-safety.md must contain STRICTLY FORBIDDEN rule."""
        skill = self.loader._system_skills.get("_base-safety")
        self.assertIsNotNone(skill, "_base-safety not found")
        self.assertIn("STRICTLY FORBIDDEN", skill.content)

    def test_d2_base_safety_has_db_knowledge_path(self):
        """_base-safety.md must mention db_knowledge path."""
        skill = self.loader._system_skills.get("_base-safety")
        self.assertIsNotNone(skill)
        self.assertIn("db_knowledge", skill.content)

    def test_d3_clickhouse_analyst_has_prohibition(self):
        """clickhouse-analyst.md must have STRICTLY FORBIDDEN wording."""
        skill = self.loader._user_skills.get("clickhouse-analyst")
        self.assertIsNotNone(skill, "clickhouse-analyst not found")
        self.assertIn("STRICTLY FORBIDDEN", skill.content)

    def test_d4_clickhouse_analyst_layer_workflow(self):
        """clickhouse-analyst.md must declare layer=workflow."""
        skill = self.loader._user_skills.get("clickhouse-analyst")
        self.assertIsNotNone(skill)
        self.assertEqual(skill.layer, "workflow")

    def test_d5_clickhouse_analyst_has_sub_skills(self):
        """clickhouse-analyst.md must declare sub_skills list."""
        skill = self.loader._user_skills.get("clickhouse-analyst")
        self.assertIsNotNone(skill)
        self.assertGreater(len(skill.sub_skills), 0)
        self.assertIn("ch-sg-specific", skill.sub_skills)

    def test_d6_db_knowledge_router_exists(self):
        """db-knowledge-router.md must be loaded in project tier."""
        skill = self.loader._project_skills.get("db-knowledge-router")
        self.assertIsNotNone(skill, "db-knowledge-router not found in project tier")
        self.assertEqual(skill.tier, TIER_PROJECT)

    def test_d7_db_knowledge_router_triggers_analytics(self):
        """db-knowledge-router must trigger on analytics keywords."""
        skill = self.loader._project_skills.get("db-knowledge-router")
        self.assertIsNotNone(skill)
        trigger_words = [t.lower() for t in skill.triggers]
        self.assertIn("clickhouse", trigger_words)

    def test_d8_db_maintainer_exists(self):
        """db-maintainer.md must be loaded in project tier."""
        skill = self.loader._project_skills.get("db-maintainer")
        self.assertIsNotNone(skill, "db-maintainer not found in project tier")
        self.assertEqual(skill.tier, TIER_PROJECT)

    def test_d9_db_maintainer_triggers_update(self):
        """db-maintainer must trigger on '更新知识库' equivalent."""
        skill = self.loader._project_skills.get("db-maintainer")
        self.assertIsNotNone(skill)
        trigger_words = [t.lower() for t in skill.triggers]
        # At least one trigger should contain 'knowledge' or update intent
        has_update_trigger = any(
            kw in " ".join(trigger_words)
            for kw in ["update", "db_knowledge", "refresh"]
        )
        self.assertTrue(has_update_trigger)

    def test_d10_env_sub_skills_loaded(self):
        """All 6 env-specific sub-skills must be loaded."""
        expected = ["ch-sg-specific", "ch-idn-specific", "ch-br-specific",
                    "ch-my-specific", "ch-thai-specific", "ch-mx-specific"]
        for name in expected:
            skill = self.loader._user_skills.get(name)
            self.assertIsNotNone(skill, f"{name} not found in user tier")

    def test_d11_scenario_sub_skills_loaded(self):
        """Scenario sub-skills must be loaded."""
        for name in ["ch-call-metrics", "ch-billing-analysis"]:
            skill = self.loader._user_skills.get(name)
            self.assertIsNotNone(skill, f"{name} not found")

    def test_d12_env_sub_skills_have_env_tags(self):
        """All env-specific sub-skills must have env_tags set."""
        env_skills = {
            "ch-sg-specific": "sg",
            "ch-idn-specific": "idn",
            "ch-br-specific": "br",
            "ch-my-specific": "my",
            "ch-thai-specific": "thai",
            "ch-mx-specific": "mx",
        }
        for name, expected_tag in env_skills.items():
            skill = self.loader._user_skills.get(name)
            if skill:
                self.assertIn(expected_tag, skill.env_tags,
                              f"{name} missing env_tag '{expected_tag}'")

    def test_d13_sg_query_loads_sg_sub_skill(self):
        """SG query must trigger ch-sg-specific via sub-skill expansion."""
        result = self.loader.build_skill_prompt("analyze SG clickhouse call data")
        self.assertIn("ch-sg-specific", result)

    def test_d14_idn_query_does_not_load_sg_sub_skill(self):
        """IDN query must NOT load ch-sg-specific."""
        result = self.loader.build_skill_prompt("analyze IDN clickhouse call data")
        self.assertNotIn("ch-sg-specific", result)

    def test_d15_maintainer_triggers_on_update_message(self):
        """db-maintainer must be triggered by 'update db_knowledge'."""
        triggered = self.loader.find_triggered("update db_knowledge")
        names = [s.name for s in triggered]
        self.assertIn("db-maintainer", names)


if __name__ == "__main__":
    unittest.main(verbosity=2)
