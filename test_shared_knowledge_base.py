"""
test_shared_knowledge_base.py
=============================
测试共享知识库与 Project 层 Skill 功能

覆盖：
  A (2)  — SHARED_DATA_ROOT 变量注入到 agentic_loop 系统提示
  B (6)  — Project 层 Skill 加载（clickhouse-analyst + ch-* 子技能）
  C (4)  — Project 层 Skill 路径引用正确性
  D (5)  — 共享知识库目录结构
  E (2)  — 双路径知识库查找逻辑（文档验证）
  F (3)  — Skill 晋升工作流（user → project）
  G (3)  — RBAC 权限验证（无新增菜单/权限）
  H (3)  — 文档更新验证
  I (2)  — 端到端知识库访问

总计: 30 个测试用例

执行方式（必须用 pytest，触发 conftest.py 清理）：
  /d/ProgramData/Anaconda3/envs/dataagent/python.exe -m pytest test_shared_knowledge_base.py -v -s
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import sys
import unittest
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

# ── 环境设置（必须在 backend 导入前完成）──────────────────────────────────────
os.environ.setdefault("CLICKHOUSE_HOST", "localhost")
os.environ.setdefault("CLICKHOUSE_PORT", "9000")
os.environ.setdefault("CLICKHOUSE_USER", "default")
os.environ.setdefault("CLICKHOUSE_PASSWORD", "")
os.environ.setdefault("CLICKHOUSE_DATABASE", "default")
os.environ.setdefault("ADMIN_SECRET_TOKEN", "test-admin-token-shkb")
os.environ.setdefault("ENABLE_AUTH", "False")

_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "backend"))

# ── FastAPI TestClient（技能路由专用，轻量）──────────────────────────────────
from fastapi import FastAPI
from fastapi.testclient import TestClient
from backend.api.skills import router as skills_router

_test_app = FastAPI()
_test_app.include_router(skills_router, prefix="/api/v1")
_client = TestClient(_test_app)

ADMIN_HDR = {"X-Admin-Token": "test-admin-token-shkb"}


# ─────────────────────────────────────────────────────────────────────────────
# A 组：SHARED_DATA_ROOT 注入到 agentic_loop 系统提示 (2 tests)
# ─────────────────────────────────────────────────────────────────────────────

class TestSharedDataRootInjection(unittest.TestCase):
    """A1-A2: 验证 SHARED_DATA_ROOT 出现在 AgenticLoop._build_system_prompt 结果中"""

    @classmethod
    def setUpClass(cls):
        from backend.agents.agentic_loop import AgenticLoop

        # 最小化 Mock MCPServerManager：返回一个 filesystem 类型服务器
        mock_manager = MagicMock()
        mock_manager.list_servers.return_value = [
            {"name": "filesystem", "type": "filesystem", "tool_count": 5}
        ]
        mock_fs_server = MagicMock()
        mock_fs_server.allowed_directories = [
            str(_ROOT / "customer_data"),
            str(_ROOT / ".claude" / "skills"),
        ]
        mock_manager.servers = {"filesystem": mock_fs_server}

        cls.loop = AgenticLoop(MagicMock(), mock_manager)
        cls.context = {
            "username": "testuser",
            "conversation_id": "test-a-001",
            "tools": [],
        }

    def _build_prompt(self, message: str = "测试消息") -> str:
        """同步包装 async _build_system_prompt"""
        async def _coro():
            return await self.loop._build_system_prompt(self.context, message=message)
        return asyncio.run(_coro())

    def test_a01_system_prompt_contains_shared_data_root(self):
        """A1: 系统提示中包含 SHARED_DATA_ROOT 变量定义"""
        prompt = self._build_prompt("测试消息")
        self.assertIn("SHARED_DATA_ROOT: _shared", prompt,
            "系统提示中应包含 SHARED_DATA_ROOT 变量定义")
        self.assertIn("共享项目知识库前缀", prompt,
            "系统提示中应包含 SHARED_DATA_ROOT 用途说明")

    def test_a02_shared_data_root_value_is_underscore_prefixed(self):
        """A2: SHARED_DATA_ROOT 的值必须是 '_shared'（下划线开头，区别于用户目录）"""
        prompt = self._build_prompt("测试")
        self.assertIn("SHARED_DATA_ROOT: _shared", prompt,
            "SHARED_DATA_ROOT 应为 '_shared'（下划线开头）")
        # 确认不是无下划线版本
        self.assertIsNone(
            re.search(r"SHARED_DATA_ROOT:\s+shared(?!_)", prompt),
            "SHARED_DATA_ROOT 不应是无下划线的 'shared'"
        )


# ─────────────────────────────────────────────────────────────────────────────
# B 组：Project 层 Skill 加载 (6 tests)
# ─────────────────────────────────────────────────────────────────────────────

class TestProjectSkillsLoading(unittest.TestCase):
    """B1-B6: 验证 project 层 skill 文件存在且 SkillLoader 能正确加载"""

    _PROJECT_DIR = _ROOT / ".claude" / "skills" / "project"

    def test_b01_project_directory_exists(self):
        """B1: .claude/skills/project/ 目录存在"""
        self.assertTrue(self._PROJECT_DIR.exists(), ".claude/skills/project/ 目录应存在")
        self.assertTrue(self._PROJECT_DIR.is_dir(), "应为目录而非文件")

    def test_b02_clickhouse_analyst_skill_exists(self):
        """B2: clickhouse-analyst.md 已迁移到 project 层，frontmatter 完整"""
        skill_file = self._PROJECT_DIR / "clickhouse-analyst.md"
        self.assertTrue(skill_file.exists(), "clickhouse-analyst.md 应存在于 project 层")

        content = skill_file.read_text(encoding="utf-8")
        self.assertIn("name: clickhouse-analyst", content)
        self.assertIn("version:", content)
        self.assertIn("sub_skills:", content)

    def test_b03_clickhouse_sub_skills_all_exist(self):
        """B3: clickhouse-analyst 的全部 8 个子技能均在 project 层"""
        expected = [
            "ch-sg-specific.md", "ch-idn-specific.md", "ch-br-specific.md",
            "ch-my-specific.md", "ch-thai-specific.md", "ch-mx-specific.md",
            "ch-call-metrics.md", "ch-billing-analysis.md",
        ]
        missing = [s for s in expected if not (self._PROJECT_DIR / s).exists()]
        self.assertEqual(missing, [], f"缺失子技能文件：{missing}")

    def test_b04_clickhouse_analyst_declares_all_sub_skills(self):
        """B4: clickhouse-analyst.md 的 sub_skills 字段声明全部 8 个子技能"""
        content = (self._PROJECT_DIR / "clickhouse-analyst.md").read_text(encoding="utf-8")
        expected = [
            "ch-sg-specific", "ch-idn-specific", "ch-br-specific",
            "ch-my-specific", "ch-thai-specific", "ch-mx-specific",
            "ch-call-metrics", "ch-billing-analysis",
        ]
        missing = [s for s in expected if f"- {s}" not in content]
        self.assertEqual(missing, [],
            f"clickhouse-analyst sub_skills 缺少：{missing}")

    def test_b05_skill_loader_loads_project_skills(self):
        """B5: SkillLoader.load_all() 将 project 层技能加载进 _project_skills 字典"""
        from backend.skills.skill_loader import get_skill_loader

        loader = get_skill_loader()
        loader.load_all()

        self.assertGreater(len(loader._project_skills), 0,
            "应加载至少一个 project 技能")
        self.assertIn("clickhouse-analyst", loader._project_skills,
            "clickhouse-analyst 应存在于 _project_skills")
        for name in ("ch-sg-specific", "ch-call-metrics", "ch-billing-analysis"):
            self.assertIn(name, loader._project_skills,
                f"{name} 应存在于 _project_skills")

    def test_b06_skill_triggering_with_clickhouse_keyword(self):
        """B6: 包含 clickhouse / 接通率 的消息能触发 clickhouse-analyst"""
        from backend.skills.skill_loader import get_skill_loader

        loader = get_skill_loader()
        skill = loader._project_skills.get("clickhouse-analyst")
        self.assertIsNotNone(skill, "clickhouse-analyst 应被加载")

        self.assertTrue(skill.matches("查询今天 SG 的接通率"),
            "应匹配含 '接通率' 的消息")
        self.assertTrue(skill.matches("分析 clickhouse 呼叫数据"),
            "应匹配含 'clickhouse' 的消息")


# ─────────────────────────────────────────────────────────────────────────────
# C 组：Project 层 Skill 路径引用正确性 (4 tests)
# ─────────────────────────────────────────────────────────────────────────────

class TestPathReferencesInProjectSkills(unittest.TestCase):
    """C1-C4: project 层技能中知识库路径使用 SHARED_DATA_ROOT 而非硬编码用户名"""

    _PROJECT_DIR = _ROOT / ".claude" / "skills" / "project"

    # ch-call-metrics / ch-billing-analysis 只引用共享指标，不应出现 {CURRENT_USER}/db_knowledge/
    # db-knowledge-router / db-maintainer 故意描述双路径协议，不在严格检查范围内
    _PATH_STRICT_SKILLS = {
        "ch-call-metrics",
        "ch-billing-analysis",
    }

    def test_c01_no_hardcoded_current_user_in_db_knowledge_paths(self):
        """C1: ch-call-metrics / ch-billing-analysis 不含 {CURRENT_USER}/db_knowledge/ 引用
        （这两个子技能只引用共享指标，db-knowledge-router / db-maintainer
        故意列出双路径，不在本检查范围）"""
        issues = []
        for md_file in self._PROJECT_DIR.glob("*.md"):
            if md_file.stem not in self._PATH_STRICT_SKILLS:
                continue
            for i, line in enumerate(md_file.read_text(encoding="utf-8").splitlines(), 1):
                if line.strip().startswith(">"):
                    continue  # 跳过说明性注释行
                if "{CURRENT_USER}/db_knowledge/" in line:
                    issues.append(f"{md_file.name}:{i} 使用了 CURRENT_USER 路径")
        self.assertEqual(issues, [],
            f"发现 {len(issues)} 处路径问题：\n" + "\n".join(issues))

    def test_c02_shared_data_root_used_in_metrics_skills(self):
        """C2: ch-call-metrics / ch-billing-analysis 使用 {SHARED_DATA_ROOT}/db_knowledge/metrics/"""
        for skill_name in ("ch-call-metrics.md", "ch-billing-analysis.md"):
            content = (self._PROJECT_DIR / skill_name).read_text(encoding="utf-8")
            self.assertIn("{SHARED_DATA_ROOT}/db_knowledge/metrics/", content,
                f"{skill_name} 应使用 {{SHARED_DATA_ROOT}}/db_knowledge/metrics/")

    def test_c03_db_knowledge_router_supports_dual_path_lookup(self):
        """C3: db-knowledge-router.md 说明两级查找（用户库优先，共享库兜底）"""
        content = (self._PROJECT_DIR / "db-knowledge-router.md").read_text(encoding="utf-8")

        self.assertIn("{CURRENT_USER}/db_knowledge/_index.md", content,
            "应说明第一步检查用户私有库")
        self.assertIn("{SHARED_DATA_ROOT}/db_knowledge/_index.md", content,
            "应说明第二步检查共享项目库")

        # 优先级说明：用户库描述出现在共享库描述之前
        user_pos = content.find("{CURRENT_USER}/db_knowledge/_index.md")
        shared_pos = content.find("{SHARED_DATA_ROOT}/db_knowledge/_index.md")
        self.assertGreater(user_pos, -1)
        self.assertGreater(shared_pos, -1)
        self.assertLess(user_pos, shared_pos,
            "用户库说明应先于共享库说明（体现优先级）")

        has_priority = "优先" in content or "priority" in content.lower()
        self.assertTrue(has_priority, "应明确说明优先级")

    def test_c04_db_maintainer_supports_shared_mode(self):
        """C4: db-maintainer.md 同时说明共享库模式和用户库模式"""
        content = (self._PROJECT_DIR / "db-maintainer.md").read_text(encoding="utf-8")

        has_shared_mode = "共享库模式" in content or "shared mode" in content.lower()
        self.assertTrue(has_shared_mode, "应说明共享库模式（写入 _shared/）")

        has_user_mode = "用户库模式" in content or "user mode" in content.lower()
        self.assertTrue(has_user_mode, "应说明用户库模式（写入 {CURRENT_USER}/）")

        self.assertIn("{SHARED_DATA_ROOT}/db_knowledge/", content,
            "共享库模式应说明 {SHARED_DATA_ROOT}/db_knowledge/ 路径")


# ─────────────────────────────────────────────────────────────────────────────
# D 组：共享知识库目录结构 (5 tests)
# ─────────────────────────────────────────────────────────────────────────────

class TestSharedKnowledgeBaseStructure(unittest.TestCase):
    """D1-D5: customer_data/_shared/db_knowledge/ 目录完整性"""

    _SHARED_KB = _ROOT / "customer_data" / "_shared" / "db_knowledge"

    def test_d01_shared_directory_exists(self):
        """D1: customer_data/_shared/ 目录存在"""
        shared_dir = _ROOT / "customer_data" / "_shared"
        self.assertTrue(shared_dir.exists(), "customer_data/_shared/ 目录应存在")
        self.assertTrue(shared_dir.is_dir())

    def test_d02_shared_db_knowledge_directory_exists(self):
        """D2: customer_data/_shared/db_knowledge/ 目录存在"""
        self.assertTrue(self._SHARED_KB.exists(), "共享知识库目录应存在")
        self.assertTrue(self._SHARED_KB.is_dir())

    def test_d03_shared_db_knowledge_has_index(self):
        """D3: _index.md 存在且包含有意义的索引内容"""
        index_file = self._SHARED_KB / "_index.md"
        self.assertTrue(index_file.exists(), "_index.md 应存在")
        content = index_file.read_text(encoding="utf-8")
        self.assertGreater(len(content), 100, "_index.md 不应为空文件")

    def test_d04_shared_db_knowledge_has_tables(self):
        """D4: tables/ 子目录存在且有 .md 文件"""
        tables_dir = self._SHARED_KB / "tables"
        self.assertTrue(tables_dir.exists(), "tables/ 子目录应存在")
        table_files = list(tables_dir.glob("*.md"))
        self.assertGreater(len(table_files), 0, "应至少有一张表的文档")

        # 核心表文档（存在即检查，允许文档集合扩展）
        for table in ("realtime_dwd_crm_call_record.md", "Dim_Enterprise.md"):
            f = tables_dir / table
            if f.exists():
                self.assertTrue(f.is_file(), f"{table} 应为文件")

    def test_d05_shared_db_knowledge_has_metrics(self):
        """D5: metrics/ 子目录存在且有 .md 文件"""
        metrics_dir = self._SHARED_KB / "metrics"
        self.assertTrue(metrics_dir.exists(), "metrics/ 子目录应存在")
        metric_files = list(metrics_dir.glob("*.md"))
        self.assertGreater(len(metric_files), 0, "应至少有一个指标文档")

        for metric in ("connect_rate.md", "monthly_bill.md"):
            f = metrics_dir / metric
            if f.exists():
                self.assertTrue(f.is_file(), f"{metric} 应为文件")


# ─────────────────────────────────────────────────────────────────────────────
# E 组：双路径知识库查找逻辑（文档验证） (2 tests)
# ─────────────────────────────────────────────────────────────────────────────

class TestDualPathKnowledgeLookup(unittest.TestCase):
    """E1-E2: 通过 db-knowledge-router.md 文档验证双路径查找协议"""

    _ROUTER_SKILL = _ROOT / ".claude" / "skills" / "project" / "db-knowledge-router.md"

    @classmethod
    def setUpClass(cls):
        cls.content = cls._ROUTER_SKILL.read_text(encoding="utf-8")

    def test_e01_user_kb_takes_priority_over_shared_kb(self):
        """E1: 文档中用户私有库说明（位置上）先于共享库说明"""
        user_pos = self.content.find("{CURRENT_USER}/db_knowledge/_index.md")
        shared_pos = self.content.find("{SHARED_DATA_ROOT}/db_knowledge/_index.md")
        self.assertGreater(user_pos, -1, "文档应说明用户私有库路径")
        self.assertGreater(shared_pos, -1, "文档应说明共享库路径")
        self.assertLess(user_pos, shared_pos,
            "文档应先列出用户私有库（优先级更高）")

    def test_e02_shared_kb_serves_as_fallback(self):
        """E2: 文档中说明共享库作为回退（当用户库不存在时）"""
        has_fallback = "若不存在" in self.content or "fallback" in self.content.lower()
        self.assertTrue(has_fallback,
            "应说明当用户私有库不存在时回退到共享库的逻辑")
        self.assertIn("{CURRENT_USER}/db_knowledge/", self.content)
        self.assertIn("{SHARED_DATA_ROOT}/db_knowledge/", self.content)


# ─────────────────────────────────────────────────────────────────────────────
# F 组：Skill 晋升工作流 (3 tests)
# ─────────────────────────────────────────────────────────────────────────────

class TestSkillPromotionWorkflow(unittest.TestCase):
    """F1-F3: user skill 晋升到 project 层的完整流程"""

    _PROJECT_DIR = _ROOT / ".claude" / "skills" / "project"
    _USER_SA_DIR = _ROOT / ".claude" / "skills" / "user" / "superadmin"

    # 已迁移到 project 层的技能（不应在 superadmin/ 下保留正本）
    _MIGRATED = [
        "clickhouse-analyst.md", "ch-sg-specific.md", "ch-idn-specific.md",
        "ch-br-specific.md", "ch-my-specific.md", "ch-thai-specific.md",
        "ch-mx-specific.md", "ch-call-metrics.md", "ch-billing-analysis.md",
    ]

    def test_f01_no_duplicate_skills_between_tiers(self):
        """F1: 已迁移技能不在 user/superadmin/ 下保留正本"""
        if not self._USER_SA_DIR.exists():
            return  # 目录不存在则无重复
        conflicts = []
        for skill in self._MIGRATED:
            p = self._USER_SA_DIR / skill
            if p.exists() and not (p.name.endswith(".bak") or p.name.endswith("~")):
                conflicts.append(skill)
        self.assertEqual(conflicts, [],
            f"已迁移技能仍保留在 user/superadmin/：{conflicts}")

    def test_f02_project_skills_api_lists_all_project_skills(self):
        """F2: GET /api/v1/skills/project-skills 返回所有 project 层技能"""
        response = _client.get("/api/v1/skills/project-skills")
        self.assertEqual(response.status_code, 200, "应成功返回项目技能列表")

        data = response.json()
        self.assertIsInstance(data, list, "返回应为列表")

        names = [s.get("name") for s in data]
        for expected in ("clickhouse-analyst", "db-knowledge-router", "db-maintainer"):
            self.assertIn(expected, names, f"应包含 {expected}")
        for expected in ("ch-sg-specific", "ch-call-metrics"):
            self.assertIn(expected, names, f"应包含子技能 {expected}")

    def test_f03_promote_user_skill_path_replacement(self):
        """F3: 晋升流程将 {CURRENT_USER}/db_knowledge/ 替换为 {SHARED_DATA_ROOT}/db_knowledge/"""
        from backend.skills.skill_loader import get_skill_loader

        test_username = f"_t_shkb_{uuid.uuid4().hex[:6]}_"
        test_skill_name = f"_t_promo_{uuid.uuid4().hex[:6]}_"

        user_skill_dir = _ROOT / ".claude" / "skills" / "user" / test_username
        user_skill_dir.mkdir(parents=True, exist_ok=True)

        skill_content = (
            f"---\nname: {test_skill_name}\nversion: \"1.0\"\n"
            f"description: 测试晋升技能\ntriggers:\n  - 测试晋升{test_skill_name}\n"
            f"category: general\npriority: medium\nalways_inject: false\n---\n\n"
            f"# 测试晋升技能\n\n"
            f"参考知识库：{{CURRENT_USER}}/db_knowledge/tables/\n"
        )
        skill_file = user_skill_dir / f"{test_skill_name}.md"
        skill_file.write_text(skill_content, encoding="utf-8")

        project_file = self._PROJECT_DIR / f"{test_skill_name}.md"
        try:
            # 模拟晋升：替换路径 + 升级版本
            promoted = skill_content.replace(
                "{CURRENT_USER}/db_knowledge/",
                "{SHARED_DATA_ROOT}/db_knowledge/",
            ).replace('version: "1.0"', 'version: "2.0"')
            project_file.write_text(promoted, encoding="utf-8")

            # 重载并验证
            loader = get_skill_loader()
            loader.load_all()

            self.assertIn(test_skill_name, loader._project_skills,
                "晋升后技能应存在于 _project_skills")

            promoted_text = project_file.read_text(encoding="utf-8")
            self.assertIn("{SHARED_DATA_ROOT}/db_knowledge/", promoted_text,
                "晋升后应使用 {SHARED_DATA_ROOT}")
            self.assertNotIn("{CURRENT_USER}/db_knowledge/", promoted_text,
                "晋升后不应包含 {CURRENT_USER} 路径")
        finally:
            if project_file.exists():
                project_file.unlink()
            if skill_file.exists():
                skill_file.unlink()
            # 清理测试用户技能目录
            if user_skill_dir.exists():
                shutil.rmtree(user_skill_dir, ignore_errors=True)
            get_skill_loader().load_all()


# ─────────────────────────────────────────────────────────────────────────────
# G 组：RBAC 权限验证（无新增菜单） (3 tests)
# ─────────────────────────────────────────────────────────────────────────────

class TestRBACNoNewMenus(unittest.TestCase):
    """G1-G3: 本次迁移不涉及新增 RBAC 权限或前端菜单"""

    def test_g01_skill_permissions_already_exist_in_db(self):
        """G1: skills.* 权限已存在于 RBAC 权限表（初始化脚本已写入）"""
        try:
            from backend.config.database import SessionLocal
            from backend.models.permission import Permission
        except ImportError:
            self.skipTest("RBAC 模型不可用")

        try:
            db = SessionLocal()
            try:
                permissions = db.query(Permission).filter(
                    Permission.resource.like("skills.%")
                ).all()
                perm_keys = {f"{p.resource}:{p.action}" for p in permissions}
            finally:
                db.close()
        except Exception as exc:
            self.skipTest(f"数据库不可用：{exc}")

        expected = {
            "skills.user:read", "skills.user:write",
            "skills.project:read", "skills.project:write",
            "skills.system:read",
        }
        missing = expected - perm_keys
        self.assertEqual(missing, set(),
            f"以下 skills 权限缺失（需运行 init_rbac.py）：{missing}")

    def test_g02_no_new_frontend_menu_entries(self):
        """G2: 本次 skill 迁移不新增前端菜单（使用现有 /skills 页面）"""
        # 验证没有新增 /shared-knowledge 等新路由文件
        suspicious_routes = [
            _ROOT / "frontend" / "src" / "pages" / "SharedKnowledge.tsx",
            _ROOT / "frontend" / "src" / "pages" / "SharedKnowledgeBase.tsx",
        ]
        for route_file in suspicious_routes:
            self.assertFalse(route_file.exists(),
                f"不应新增 {route_file.name} — 本次迁移复用现有 /skills 页面")

    def test_g03_project_skills_api_requires_admin_token(self):
        """G3: POST /project-skills 不带 admin token 应返回 401 或 403"""
        from backend.config.settings import settings

        if not settings.admin_secret_token:
            self.skipTest("ADMIN_SECRET_TOKEN 未配置")

        # 使用当前实际配置的 admin token（避免多测试文件并行时 env 被先行 setdefault 覆盖）
        actual_admin_hdr = {"X-Admin-Token": settings.admin_secret_token}
        skill_payload = {
            "name": "_t_g3_test_",
            "description": "G3 RBAC 权限验证技能",
            "triggers": ["g3test_rbac"],
            "category": "general",
            "priority": "medium",
            "content": "# G3 测试技能\n\nG3 RBAC 权限验证专用测试技能，请勿手动触发。",
        }

        # 无 token → 401/403
        resp_no_token = _client.post("/api/v1/skills/project-skills", json=skill_payload)
        self.assertIn(resp_no_token.status_code, [401, 403],
            f"无 token 应被拒绝，实际返回：{resp_no_token.status_code}")

        # 带正确 admin token → 201/409
        resp_with_token = _client.post(
            "/api/v1/skills/project-skills",
            json=skill_payload,
            headers=actual_admin_hdr,
        )
        self.assertIn(resp_with_token.status_code, [201, 409],
            f"携带 admin token 应成功或返回冲突，实际返回：{resp_with_token.status_code}")

        # 清理：若创建成功，删除测试技能
        if resp_with_token.status_code == 201:
            _client.delete("/api/v1/skills/project-skills/_t_g3_test_",
                           headers=actual_admin_hdr)


# ─────────────────────────────────────────────────────────────────────────────
# H 组：文档更新验证 (3 tests)
# ─────────────────────────────────────────────────────────────────────────────

class TestDocumentationUpdates(unittest.TestCase):
    """H1-H3: 确认相关文档已随本次迁移同步更新"""

    _DOCS_DIR = _ROOT / "docs"

    def test_h01_skill_system_design_doc_updated(self):
        """H1: skill_system_design.md 包含 SHARED_DATA_ROOT 和 _shared/db_knowledge 说明"""
        doc = self._DOCS_DIR / "skill_system_design.md"
        self.assertTrue(doc.exists(), "skill_system_design.md 应存在")
        content = doc.read_text(encoding="utf-8")
        self.assertIn("SHARED_DATA_ROOT", content,
            "文档应说明 SHARED_DATA_ROOT 变量")
        self.assertIn("_shared/db_knowledge", content,
            "文档应说明共享知识库路径")

    def test_h02_skill_system_design_shows_project_tier_skills(self):
        """H2: skill_system_design.md 中 project 层技能列表包含迁移的技能"""
        content = (self._DOCS_DIR / "skill_system_design.md").read_text(encoding="utf-8")
        for skill in ("clickhouse-analyst", "ch-sg-specific", "ch-call-metrics"):
            self.assertIn(skill, content,
                f"文档应列出 project 层技能：{skill}")

    def test_h03_admin_skill_guide_exists_with_key_sections(self):
        """H3: ADMIN_SKILL_GUIDE.md 存在且包含晋升说明、共享知识库维护和 Claude Code 说明"""
        guide = self._DOCS_DIR / "ADMIN_SKILL_GUIDE.md"
        self.assertTrue(guide.exists(), "ADMIN_SKILL_GUIDE.md 应存在")
        content = guide.read_text(encoding="utf-8")
        self.assertTrue("晋升" in content or "Skill 晋升" in content,
            "应包含 Skill 晋升操作说明")
        self.assertTrue("共享知识库" in content or "_shared" in content,
            "应包含共享知识库维护说明")
        self.assertIn("Claude Code", content,
            "应说明 Claude Code CLI 维护方式")


# ─────────────────────────────────────────────────────────────────────────────
# I 组：端到端知识库访问 (2 tests)
# ─────────────────────────────────────────────────────────────────────────────

class TestEndToEndKnowledgeLookup(unittest.TestCase):
    """I1-I2: 共享知识库对所有用户可读，用户可建立私有覆盖层"""

    _SHARED_KB = _ROOT / "customer_data" / "_shared" / "db_knowledge"

    def test_i01_shared_kb_accessible_to_all_users(self):
        """I1: 共享知识库路径不含用户名，对所有用户一致可读"""
        self.assertTrue(self._SHARED_KB.exists(), "共享知识库应存在")

        index_file = self._SHARED_KB / "_index.md"
        self.assertTrue(index_file.exists(), "共享索引文件应存在")

        # 共享路径中不应包含任何用户名
        shared_path_str = str(self._SHARED_KB)
        self.assertNotIn("superadmin", shared_path_str,
            "共享知识库路径不应包含用户名")
        self.assertIn("_shared", shared_path_str,
            "共享知识库路径应包含 _shared 标识")

    def test_i02_user_can_create_private_kb_override(self):
        """I2: 用户可在私有目录创建覆盖文档，目录与共享库同级隔离"""
        test_username = f"_t_shkb_{uuid.uuid4().hex[:6]}_"
        user_kb_dir = _ROOT / "customer_data" / test_username / "db_knowledge" / "tables"
        user_table_doc = user_kb_dir / "test_override.md"

        try:
            user_kb_dir.mkdir(parents=True, exist_ok=True)
            user_table_doc.write_text(
                "# 用户特定版本\n\n覆盖共享库的自定义说明。",
                encoding="utf-8"
            )
            self.assertTrue(user_table_doc.exists(),
                "用户应能在私有目录创建覆盖文档")
            # 私有路径包含用户名，与共享库隔离
            self.assertIn(test_username, str(user_table_doc))
            self.assertNotIn("_shared", str(user_table_doc))
        finally:
            user_parent = _ROOT / "customer_data" / test_username
            if user_parent.exists():
                shutil.rmtree(user_parent, ignore_errors=True)


# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    unittest.main(verbosity=2)
