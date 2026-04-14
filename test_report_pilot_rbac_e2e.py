"""
test_report_pilot_rbac_e2e.py — Report Pilot RBAC & E2E 测试套件
=================================================================

覆盖 Report Pilot（AI 对话驱动报表修改）功能的核心端到端流程：

  E  ( 6) — RBAC 权限管控：POST /reports/{id}/copilot 权限矩阵
  F  ( 5) — Copilot 系统提示内容质量
  G  ( 5) — Token 安全边界（refresh_token 鉴权）
  H  ( 4) — Agent MCP 工具可用性验证
  I  ( 5) — E2E 数据流（DB 写入 / HTML 生成 / merge 语义）

总计: 25 个测试用例
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

# ── sys.path 保证 backend 包可导入 ───────────────────────────────────────────
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "backend"))

from test_utils import make_test_username

# ─── 模块级共享状态（由 setup_module 填充，避免被 conftest 预清理删除）────────

_g_db = None
_PREFIX = None

# 测试用户（setup_module 中创建）
_u_viewer = None
_u_analyst = None
_u_analyst_b = None
_u_admin = None
_u_superadmin = None

# 测试报表（setup_module 中创建）
_report_analyst = None   # analyst 拥有
_report_b = None         # analyst_b 拥有（跨用户测试用）

# 临时目录（供 Section I 写 HTML 文件使用）
_tmpdir = None


# ─── 工厂函数（需要 _g_db 已初始化）───────────────────────────────────────────

def _make_user(suffix: str, role_names=None, is_superadmin=False):
    from backend.models.user import User
    from backend.models.role import Role
    from backend.models.user_role import UserRole
    from backend.core.auth.password import hash_password

    username = f"{_PREFIX}{suffix}"
    u = User(
        username=username,
        display_name=f"Pilot {suffix}",
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


def _make_report(username: str, charts=None, report_file_path=None):
    """在 DB 中创建测试报表，返回 Report 对象。"""
    from backend.models.report import Report, ReportType
    from backend.services.report_builder_service import generate_refresh_token

    r = Report(
        name=f"Test Report {uuid.uuid4().hex[:6]}",
        report_type=ReportType.DASHBOARD,
        username=username,
        refresh_token=generate_refresh_token(),
        charts=charts or [{"id": "c1", "title": "销售额", "chart_type": "bar"}],
        filters=[],
        theme="light",
        report_file_path=report_file_path,
    )
    _g_db.add(r)
    _g_db.commit()
    _g_db.refresh(r)
    return r


def _make_token(user):
    from backend.config.settings import settings
    from backend.core.auth.jwt import create_access_token
    from backend.core.rbac import get_user_roles
    roles = get_user_roles(user, _g_db)
    return create_access_token(
        {"sub": str(user.id), "username": user.username, "roles": roles},
        settings.jwt_secret, settings.jwt_algorithm,
    )


# ─── setup_module / teardown_module ──────────────────────────────────────────

def setup_module(_=None):
    """
    在 conftest 预清理运行之后创建所有测试数据。
    这样 conftest 不会在测试运行前把我们的测试数据删掉。
    """
    global _g_db, _PREFIX
    global _u_viewer, _u_analyst, _u_analyst_b, _u_admin, _u_superadmin
    global _report_analyst, _report_b
    global _tmpdir

    from backend.config.database import SessionLocal
    _g_db = SessionLocal()

    # 唯一前缀（每次 setup 生成，避免并发冲突）
    _PREFIX = make_test_username("pilot")  # e.g. _t_pilot_a3b2c1_

    # 测试用户
    _u_viewer = _make_user("viewer", role_names=["viewer"])
    _u_analyst = _make_user("analyst", role_names=["analyst"])
    _u_analyst_b = _make_user("anlstb", role_names=["analyst"])
    _u_admin = _make_user("admin", role_names=["admin"])
    _u_superadmin = _make_user("sa", is_superadmin=True)

    # 测试报表
    _report_analyst = _make_report(_u_analyst.username)
    _report_b = _make_report(_u_analyst_b.username)

    # 临时目录（Section I 写 HTML）
    _tmpdir = tempfile.TemporaryDirectory(prefix="_t_rbe_")


def teardown_module(_=None):
    global _g_db, _tmpdir

    if _g_db is not None:
        try:
            from backend.models.report import Report
            from backend.models.user import User
            from backend.models.user_role import UserRole
            from backend.models.conversation import Conversation

            # 清理本次创建的报表（通过 username 前缀识别）
            _g_db.query(Report).filter(
                Report.username.like(f"{_PREFIX}%")
            ).delete(synchronize_session=False)

            # 清理本次创建的对话（title 以 "报表助手" 开头的 copilot 对话）
            try:
                test_convs = _g_db.query(Conversation).filter(
                    Conversation.title.like("报表助手%")
                ).all()
                for c in test_convs:
                    # 只删除系统提示中包含测试 PREFIX 的对话
                    meta = c.extra_metadata or {}
                    sp = meta.get("system_prompt", "") or ""
                    if _PREFIX in sp or str(_report_analyst.id) in sp if _report_analyst else False:
                        _g_db.delete(c)
            except Exception:
                pass

            # 清理测试用户
            if _PREFIX:
                users = _g_db.query(User).filter(
                    User.username.like(f"{_PREFIX}%")
                ).all()
                for u in users:
                    _g_db.query(UserRole).filter(
                        UserRole.user_id == u.id
                    ).delete(synchronize_session=False)
                    _g_db.delete(u)

            _g_db.commit()
        except Exception as e:
            print(f"\n[teardown] cleanup error: {e}")
            _g_db.rollback()
        finally:
            _g_db.close()
            _g_db = None

    if _tmpdir is not None:
        _tmpdir.cleanup()
        _tmpdir = None


# ─── FastAPI TestClient 工厂 ───────────────────────────────────────────────────

def _make_client():
    from backend.main import app
    from fastapi.testclient import TestClient
    return TestClient(app, raise_server_exceptions=True)


# ══════════════════════════════════════════════════════════════════════════════
# Section E — RBAC 权限矩阵：POST /reports/{id}/copilot
# ══════════════════════════════════════════════════════════════════════════════

class TestCopilotRBAC(unittest.TestCase):
    """E1-E6: copilot 端点权限矩阵验证"""

    @classmethod
    def setUpClass(cls):
        cls.client = _make_client()

    def _auth(self, user):
        return {"Authorization": f"Bearer {_make_token(user)}"}

    def _copilot_url(self, report_id):
        return f"/api/v1/reports/{report_id}/copilot"

    def test_E1_viewer_cannot_access_copilot(self):
        """E1: viewer 只有 chat:use，缺少 reports:read → 403"""
        with patch("backend.config.settings.settings.enable_auth", True):
            resp = self.client.post(
                self._copilot_url(_report_analyst.id),
                headers=self._auth(_u_viewer),
            )
        self.assertEqual(resp.status_code, 403, resp.text)

    def test_E2_analyst_can_access_own_report_copilot(self):
        """E2: analyst 有 reports:read 且是报表 owner → 200"""
        with patch("backend.config.settings.settings.enable_auth", True):
            resp = self.client.post(
                self._copilot_url(_report_analyst.id),
                headers=self._auth(_u_analyst),
            )
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()
        self.assertTrue(data.get("success"))
        self.assertIn("conversation_id", data.get("data", {}))

    def test_E3_admin_can_access_own_report_copilot(self):
        """E3: admin 有 reports:read，访问自己的报表 → 200"""
        report_admin = _make_report(_u_admin.username)
        with patch("backend.config.settings.settings.enable_auth", True):
            resp = self.client.post(
                self._copilot_url(report_admin.id),
                headers=self._auth(_u_admin),
            )
        self.assertEqual(resp.status_code, 200, resp.text)

    def test_E4_unauthenticated_copilot_returns_401(self):
        """E4: 未提供 JWT → 401"""
        with patch("backend.config.settings.settings.enable_auth", True):
            resp = self.client.post(
                self._copilot_url(_report_analyst.id),
            )
        self.assertEqual(resp.status_code, 401, resp.text)

    def test_E5_cross_user_analyst_cannot_access_others_report(self):
        """E5: analyst_b 有 reports:read，但报表属于 analyst → ownership 检查 → 403"""
        with patch("backend.config.settings.settings.enable_auth", True):
            resp = self.client.post(
                self._copilot_url(_report_analyst.id),  # analyst 的报表
                headers=self._auth(_u_analyst_b),       # 以 analyst_b 身份访问
            )
        self.assertEqual(resp.status_code, 403, resp.text)

    def test_E6_superadmin_can_access_any_report_copilot(self):
        """E6: superadmin 绕过 ownership check，可访问任意报表 → 200"""
        with patch("backend.config.settings.settings.enable_auth", True):
            resp = self.client.post(
                self._copilot_url(_report_b.id),    # analyst_b 的报表
                headers=self._auth(_u_superadmin),  # 以 superadmin 访问
            )
        self.assertEqual(resp.status_code, 200, resp.text)


# ══════════════════════════════════════════════════════════════════════════════
# Section F — Copilot 系统提示内容质量
# ══════════════════════════════════════════════════════════════════════════════

class TestCopilotSystemPrompt(unittest.TestCase):
    """F1-F5: 验证注入系统提示的内容完整性"""

    @classmethod
    def setUpClass(cls):
        cls.client = _make_client()
        # 创建带多图表的报表
        cls.charts = [
            {"id": "c1", "title": "销售额趋势", "chart_type": "line"},
            {"id": "c2", "title": "品类占比", "chart_type": "pie"},
        ]
        cls.report = _make_report(_u_analyst.username, charts=cls.charts)

    def _get_conv_metadata(self):
        """调用 copilot 端点，返回新建对话的 extra_metadata（从 DB 查询）。"""
        with patch("backend.config.settings.settings.enable_auth", True):
            resp = self.client.post(
                f"/api/v1/reports/{self.report.id}/copilot",
                headers={"Authorization": f"Bearer {_make_token(_u_analyst)}"},
            )
        self.assertEqual(resp.status_code, 200, resp.text)
        conv_id = resp.json()["data"]["conversation_id"]

        from backend.models.conversation import Conversation
        conv = _g_db.query(Conversation).filter(
            Conversation.id == uuid.UUID(conv_id)
        ).first()
        _g_db.refresh(conv)
        return conv.extra_metadata or {}

    def test_F1_system_prompt_contains_report_id(self):
        """F1: 系统提示包含 report_id"""
        meta = self._get_conv_metadata()
        prompt = meta.get("system_prompt", "")
        self.assertIn(str(self.report.id), prompt)

    def test_F2_system_prompt_contains_refresh_token(self):
        """F2: 系统提示包含 refresh_token（供 MCP 工具使用）"""
        meta = self._get_conv_metadata()
        prompt = meta.get("system_prompt", "")
        self.assertIn(self.report.refresh_token, prompt)

    def test_F3_system_prompt_contains_chart_ids(self):
        """F3: 系统提示包含每个图表 ID"""
        meta = self._get_conv_metadata()
        prompt = meta.get("system_prompt", "")
        for chart in self.charts:
            self.assertIn(chart["id"], prompt, f"缺少图表 {chart['id']}")

    def test_F4_system_prompt_contains_mcp_tool_instructions(self):
        """F4: 系统提示包含三个 MCP 工具说明"""
        meta = self._get_conv_metadata()
        prompt = meta.get("system_prompt", "")
        self.assertIn("report__get_spec", prompt)
        self.assertIn("report__update_single_chart", prompt)
        self.assertIn("report__update_spec", prompt)

    def test_F5_conversation_metadata_contains_refresh_token_and_context_type(self):
        """F5: 对话 extra_metadata 含 context_type=report 和 refresh_token"""
        meta = self._get_conv_metadata()
        self.assertEqual(meta.get("context_type"), "report")
        self.assertIn("refresh_token", meta)
        self.assertEqual(meta["refresh_token"], self.report.refresh_token)


# ══════════════════════════════════════════════════════════════════════════════
# Section G — Token 安全边界
# ══════════════════════════════════════════════════════════════════════════════

class TestTokenSecurity(unittest.TestCase):
    """G1-G5: refresh_token 鉴权边界验证"""

    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient
        from backend.main import app
        cls.client = TestClient(app, raise_server_exceptions=True)
        cls.report = _make_report(_u_analyst.username)
        cls.report_b = _make_report(_u_analyst_b.username)  # 不同报表，token 不同

    def test_G1_valid_token_get_spec_succeeds(self):
        """G1: 正确 token 调用 get_spec_by_token 返回 spec"""
        from backend.services.report_service import get_spec_by_token
        spec = get_spec_by_token(str(self.report.id), self.report.refresh_token)
        self.assertEqual(spec["id"], str(self.report.id))
        self.assertIn("charts", spec)

    def test_G2_wrong_token_get_spec_raises_permission_error(self):
        """G2: 错误 token 调用 get_spec_by_token → PermissionError"""
        from backend.services.report_service import get_spec_by_token
        with self.assertRaises(PermissionError):
            get_spec_by_token(str(self.report.id), "wrong_token_abc123")

    def test_G3_cross_report_token_get_spec_raises_permission_error(self):
        """G3: 用另一个报表的 token 访问本报表 → PermissionError（token 不匹配）"""
        from backend.services.report_service import get_spec_by_token
        with self.assertRaises(PermissionError):
            get_spec_by_token(str(self.report.id), self.report_b.refresh_token)

    def test_G4_spec_meta_endpoint_valid_token_returns_200(self):
        """G4: GET /reports/{id}/spec-meta?token=valid → 200（无需 JWT）"""
        resp = self.client.get(
            f"/api/v1/reports/{self.report.id}/spec-meta",
            params={"token": self.report.refresh_token},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()
        self.assertTrue(data.get("success"))

    def test_G5_spec_meta_endpoint_invalid_token_returns_403(self):
        """G5: GET /reports/{id}/spec-meta?token=bad → 403"""
        resp = self.client.get(
            f"/api/v1/reports/{self.report.id}/spec-meta",
            params={"token": "totally_wrong_token_xyz"},
        )
        self.assertEqual(resp.status_code, 403, resp.text)


# ══════════════════════════════════════════════════════════════════════════════
# Section H — Agent MCP 工具可用性
# ══════════════════════════════════════════════════════════════════════════════

class TestAgentMCPToolAvailability(unittest.TestCase):
    """H1-H4: 验证 report__ 工具对所有 Agent 类型可见"""

    def test_H1_mcp_manager_can_register_report_server(self):
        """H1: MCPServerManager 可成功注册并访问 report 服务器"""
        from backend.mcp.manager import MCPServerManager
        from backend.mcp.report_tool.server import ReportToolMCPServer

        # 直接验证 ReportToolMCPServer 初始化后包含 3 个工具
        # 使用 asyncio.run() 创建新事件循环，避免与先前测试的事件循环冲突
        server = ReportToolMCPServer()
        asyncio.run(server.initialize())
        self.assertIn("get_spec", server.tools, "report 服务器缺少 get_spec 工具")
        self.assertIn("update_spec", server.tools, "report 服务器缺少 update_spec 工具")
        self.assertIn("update_single_chart", server.tools, "report 服务器缺少 update_single_chart 工具")

        # 验证 MCPServerManager 可注册该服务器
        mgr = MCPServerManager()
        asyncio.run(mgr.create_report_tool_server())
        self.assertIn("report", mgr.servers, "MCPServerManager 未找到 'report' 服务器")

    def test_H2_agent_config_does_not_exclude_report_server(self):
        """H2: agent_config.yaml 中所有 Agent 类型都未在 excluded_non_ch_servers 中排除 report"""
        import yaml
        config_path = Path(__file__).parent / ".claude" / "agent_config.yaml"
        self.assertTrue(config_path.exists(), "agent_config.yaml 不存在")
        cfg = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        agent_types = cfg.get("agents", {})
        self.assertTrue(len(agent_types) > 0, "agent_config.yaml 中没有定义任何 Agent 类型")
        for agent_name, agent_cfg in agent_types.items():
            excluded = agent_cfg.get("excluded_non_ch_servers", [])
            self.assertNotIn(
                "report", excluded,
                f"Agent '{agent_name}' 在 excluded_non_ch_servers 中排除了 'report'！"
            )

    def test_H3_format_mcp_tools_includes_report_tools(self):
        """H3: format_mcp_tools_for_claude 将 report 服务器工具转为正确的 Claude tool 格式"""
        from backend.mcp.tool_formatter import format_mcp_tools_for_claude
        from backend.mcp.report_tool.server import ReportToolMCPServer

        # 用真实的 ReportToolMCPServer 实例测试 formatter
        # 使用 asyncio.run() 创建新事件循环，避免事件循环状态干扰
        server = ReportToolMCPServer()
        asyncio.run(server.initialize())

        mock_mgr = MagicMock()
        mock_mgr.servers = {"report": server}

        tools = format_mcp_tools_for_claude(mock_mgr)
        tool_names = {t["name"] for t in tools}

        self.assertIn("report__get_spec", tool_names)
        self.assertIn("report__update_spec", tool_names)
        self.assertIn("report__update_single_chart", tool_names)

        # 验证工具结构符合 Claude API 规范
        for tool in tools:
            if tool["name"].startswith("report__"):
                self.assertIn("description", tool)
                self.assertIn("input_schema", tool)
                schema = tool["input_schema"]
                self.assertEqual(schema.get("type"), "object")
                self.assertIn("properties", schema)

    def test_H4_agent_mcp_binder_excludes_nothing_for_report(self):
        """H4: AgentMCPBinder 对 etl_engineer/analyst/general 均未排除 report 服务器"""
        from backend.core.agent_mcp_binder import AgentMCPBinder

        binder = AgentMCPBinder()
        for agent_type in ["etl_engineer", "analyst", "general"]:
            cfg = binder._config.get(agent_type, {})
            excluded = set(cfg.get("excluded_non_ch_servers", []))
            self.assertNotIn(
                "report", excluded,
                f"Agent type '{agent_type}' 在 _config 中排除了 report 服务器！"
            )


# ══════════════════════════════════════════════════════════════════════════════
# Section I — E2E 数据流（DB 写入 / HTML 生成 / merge 语义）
# ══════════════════════════════════════════════════════════════════════════════

class TestE2EDataFlow(unittest.TestCase):
    """I1-I5: 验证 MCP 工具调用的完整数据流"""

    @classmethod
    def setUpClass(cls):
        # 每个 Section I 测试使用独立的报表，避免相互干扰
        cls.tmp_root = Path(_tmpdir.name)
        cls.rel_path = f"{_u_analyst.username}/reports/test_e2e.html"
        cls.report = _make_report(
            _u_analyst.username,
            charts=[
                {"id": "c1", "title": "销售额", "chart_type": "bar"},
                {"id": "c2", "title": "品类占比", "chart_type": "pie"},
            ],
            report_file_path=cls.rel_path,
        )

    def _patch_customer_root(self):
        """指向临时目录，避免写入真实 customer_data/。"""
        return patch(
            "backend.services.report_service._get_customer_data_root",
            return_value=self.tmp_root,
        )

    def _patch_build_html(self):
        """跳过真实 ClickHouse 查询，返回固定 HTML。"""
        return patch(
            "backend.services.report_builder_service.build_report_html",
            return_value="<html><body>Test Report</body></html>",
        )

    def _reload(self):
        """重新从 DB 加载报表（避免 SQLAlchemy 脏缓存）。"""
        from backend.models.report import Report
        _g_db.expire(self.report)
        return _g_db.query(Report).filter(Report.id == self.report.id).first()

    def test_I1_update_spec_by_token_updates_db_charts(self):
        """I1: update_spec_by_token 成功后 DB 中的 charts 被完整更新"""
        from backend.services.report_service import update_spec_by_token

        new_spec = {
            "title": "已更新报表",
            "charts": [
                {"id": "c1", "title": "新销售额趋势", "chart_type": "line"},
                {"id": "c2", "title": "品类占比", "chart_type": "pie"},
                {"id": "c3", "title": "新增图表", "chart_type": "bar"},
            ],
            "filters": [],
            "theme": "dark",
        }

        with self._patch_customer_root(), self._patch_build_html():
            result = update_spec_by_token(
                str(self.report.id),
                new_spec,
                self.report.refresh_token,
            )

        self.assertEqual(result["report_id"], str(self.report.id))

        updated = self._reload()
        self.assertEqual(len(updated.charts), 3)
        chart_ids = {c["id"] for c in updated.charts}
        self.assertIn("c3", chart_ids, "新增的 c3 未写入 DB")
        self.assertEqual(updated.theme, "dark")
        self.assertEqual(updated.name, "已更新报表")

    def test_I2_update_spec_by_token_writes_html_file(self):
        """I2: update_spec_by_token 在正确路径写入 HTML 文件"""
        from backend.services.report_service import update_spec_by_token

        spec = {
            "charts": [{"id": "c1", "title": "图表", "chart_type": "bar"}],
            "filters": [],
            "theme": "light",
        }
        expected_path = self.tmp_root / self.rel_path

        with self._patch_customer_root(), self._patch_build_html():
            update_spec_by_token(
                str(self.report.id),
                spec,
                self.report.refresh_token,
            )

        self.assertTrue(expected_path.exists(), "HTML 文件未写入磁盘")
        content = expected_path.read_text(encoding="utf-8")
        self.assertIn("<html>", content)

    def test_I3_update_single_chart_preserves_other_charts(self):
        """I3: update_single_chart_by_token 只更新目标图表，其他图表保持不变"""
        from backend.services.report_service import update_single_chart_by_token

        # 重置为已知初始状态
        self.report.charts = [
            {"id": "c1", "title": "销售额", "chart_type": "bar", "color": "blue"},
            {"id": "c2", "title": "品类占比", "chart_type": "pie"},
        ]
        _g_db.commit()

        with self._patch_customer_root(), self._patch_build_html():
            result = update_single_chart_by_token(
                report_id=str(self.report.id),
                chart_id="c1",
                chart_patch={"chart_type": "line", "title": "销售额趋势"},
                refresh_token=self.report.refresh_token,
            )

        self.assertTrue(result["found"], "c1 应被找到并更新")

        updated = self._reload()
        c1 = next((c for c in updated.charts if c["id"] == "c1"), None)
        self.assertIsNotNone(c1)
        self.assertEqual(c1["chart_type"], "line", "c1 chart_type 应更新为 line")
        self.assertEqual(c1["title"], "销售额趋势")
        self.assertEqual(c1.get("color"), "blue", "c1 color 未在 patch 中包含，应保持原值")

        c2 = next((c for c in updated.charts if c["id"] == "c2"), None)
        self.assertIsNotNone(c2)
        self.assertEqual(c2["chart_type"], "pie", "c2 不应被修改")

    def test_I4_update_spec_chart_count_conservation(self):
        """I4: update_spec_by_token 传入 N 个图表 → DB 中图表数量精确为 N"""
        from backend.services.report_service import update_spec_by_token

        charts_n = [
            {"id": f"c{i}", "title": f"图表{i}", "chart_type": "bar"}
            for i in range(1, 6)  # 5 个图表
        ]
        spec = {"charts": charts_n, "filters": [], "theme": "light"}

        with self._patch_customer_root(), self._patch_build_html():
            update_spec_by_token(
                str(self.report.id),
                spec,
                self.report.refresh_token,
            )

        updated = self._reload()
        self.assertEqual(
            len(updated.charts), 5,
            f"预期 5 个图表，实际 {len(updated.charts)} 个"
        )

    def test_I5_wrong_token_leaves_db_unchanged(self):
        """I5: 错误 token 调用 update_spec_by_token → PermissionError，DB 不被修改"""
        from backend.services.report_service import update_spec_by_token

        before = self._reload()
        original_count = len(before.charts or [])

        malicious_spec = {
            "charts": [{"id": "evil", "title": "恶意注入", "chart_type": "bar"}],
            "filters": [],
            "theme": "dark",
        }

        with self.assertRaises(PermissionError):
            with self._patch_customer_root(), self._patch_build_html():
                update_spec_by_token(
                    str(self.report.id),
                    malicious_spec,
                    "completely_wrong_token_xyz",
                )

        after = self._reload()
        self.assertEqual(
            len(after.charts or []), original_count,
            "错误 token 不应修改 DB 中的图表数量"
        )


# ══════════════════════════════════════════════════════════════════════════════
# 入口
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    unittest.main(verbosity=2)
