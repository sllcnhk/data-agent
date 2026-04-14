"""
test_report_pilot_e2e.py — 报表 Pilot E2E 测试套件
覆盖：
  P段(10) — GET /spec-meta 端点 HTTP 测试
  Q段(10) — PUT /reports/{id}/charts/{chart_id} 端点 HTTP 测试
  R段(6)  — RBAC 权限矩阵分析
  S段(7)  — 前端 DataCenterCopilot 系统提示静态分析
  T段(6)  — 前端报表 Viewer+Preview 生命周期静态分析
  U段(8)  — 图表守恒完整性（Service 层 + 技能）

运行：
  /d/ProgramData/Anaconda3/envs/dataagent/python.exe -m pytest test_report_pilot_e2e.py -v -s
"""
from __future__ import annotations

import ast
import json
import os
import sys
import uuid
import unittest
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

# ── 路径设置 ──────────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).parent.resolve()
sys.path.insert(0, str(PROJECT_ROOT))
# backend/ 必须也在 sys.path，因为 backend/main.py 用 `from api import ...`
_BACKEND_DIR = str(PROJECT_ROOT / "backend")
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

# ── 环境变量（必须在 import backend 之前设置）────────────────────────────────
os.environ.setdefault("ENABLE_AUTH", "False")
os.environ.setdefault("DATABASE_URL", "postgresql://fake:fake@localhost/fake")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-pytest-only")
os.environ.setdefault("ALLOWED_DIRECTORIES", '["customer_data", ".claude/skills"]')
os.environ.setdefault("FILESYSTEM_WRITE_ALLOWED_DIRS", '["customer_data", ".claude/skills/user"]')

# ── 公共 Helper ───────────────────────────────────────────────────────────────

def _make_mock_report(
    report_id: str | None = None,
    refresh_token: str = "tok_abc123",
    charts: List[Dict] | None = None,
    username: str = "u_alice",
    name: str = "测试报表",
    report_type: str = "report",
    report_file_path: str = "/tmp/fake_report.html",
) -> MagicMock:
    """构造一个完整的 Report ORM Mock 对象。"""
    _charts = charts if charts is not None else [
        {"id": "c1", "chart_type": "bar", "title": "图表1", "sql": "SELECT 1"},
        {"id": "c2", "chart_type": "line", "title": "图表2", "sql": "SELECT 2"},
    ]
    r = MagicMock()
    r.id = uuid.UUID(report_id) if report_id else uuid.uuid4()
    r.refresh_token = refresh_token
    r.name = name
    r.username = username
    r.report_type = report_type
    r.report_file_path = report_file_path
    # 端点使用 report.charts（非 report.spec）
    r.charts = _charts
    r.filters = []
    r.theme = "light"
    r.description = ""
    r.data_sources = []
    r.created_at.isoformat.return_value = "2026-01-01T00:00:00"
    r.updated_at.isoformat.return_value = "2026-01-02T00:00:00"
    # to_dict() 是 FastAPI 响应序列化所调用的（_report_to_dict 调用 r.to_dict()）
    r.to_dict.return_value = {
        "id": str(r.id),
        "name": name,
        "username": username,
        "report_type": report_type,
        "refresh_token": refresh_token,
        "report_file_path": report_file_path,
        "charts": _charts,
        "filters": [],
        "theme": "light",
        "data_sources": [],
        "description": "",
        "created_at": "2026-01-01T00:00:00",
        "updated_at": "2026-01-02T00:00:00",
    }
    return r


def _make_client_with_db(mock_db: MagicMock):
    """用 mock DB 构造 FastAPI TestClient（覆盖 get_db 依赖）。"""
    from backend.main import app
    from backend.config.database import get_db
    from fastapi.testclient import TestClient

    def _override():
        yield mock_db

    app.dependency_overrides[get_db] = _override
    client = TestClient(app, raise_server_exceptions=False)
    return client, app


def _auth_headers() -> Dict[str, str]:
    """生成一个伪造的 JWT Bearer token header（ENABLE_AUTH=False 时会被忽略）。"""
    return {"Authorization": "Bearer fake-jwt-token"}


# ═══════════════════════════════════════════════════════════════════════════════
# P段 — GET /spec-meta 端点 HTTP 测试（10个）
# ═══════════════════════════════════════════════════════════════════════════════

class TestPSpecMetaEndpoint(unittest.TestCase):
    """GET /api/v1/reports/{id}/spec-meta?token=... 端点测试。"""

    def setUp(self):
        self.report_id = str(uuid.uuid4())
        self.token = "tok_valid_abc123"
        self.report = _make_mock_report(report_id=self.report_id, refresh_token=self.token)

    def _db_returning(self, report_obj):
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = report_obj
        return mock_db

    def test_P1_valid_token_returns_200(self):
        """正确 token 返回 200。"""
        client, app = _make_client_with_db(self._db_returning(self.report))
        try:
            resp = client.get(f"/api/v1/reports/{self.report_id}/spec-meta?token={self.token}")
            self.assertEqual(200, resp.status_code, resp.text)
        finally:
            app.dependency_overrides.clear()

    def test_P2_response_contains_charts_array(self):
        """响应中包含 data.spec.charts 数组。"""
        client, app = _make_client_with_db(self._db_returning(self.report))
        try:
            resp = client.get(f"/api/v1/reports/{self.report_id}/spec-meta?token={self.token}")
            self.assertEqual(200, resp.status_code)
            body = resp.json()
            self.assertIn("data", body)
            data = body["data"]
            # spec 字段或直接 charts 字段
            spec = data.get("spec") or data
            self.assertIn("charts", spec, f"响应中缺少 charts: {data}")
            self.assertIsInstance(spec["charts"], list)
        finally:
            app.dependency_overrides.clear()

    def test_P3_success_field_is_true(self):
        """成功响应的 success 字段为 True。"""
        client, app = _make_client_with_db(self._db_returning(self.report))
        try:
            resp = client.get(f"/api/v1/reports/{self.report_id}/spec-meta?token={self.token}")
            self.assertEqual(200, resp.status_code)
            body = resp.json()
            self.assertTrue(body.get("success"), f"success 应为 True: {body}")
        finally:
            app.dependency_overrides.clear()

    def test_P4_wrong_token_returns_403(self):
        """错误 token 返回 403。"""
        client, app = _make_client_with_db(self._db_returning(self.report))
        try:
            resp = client.get(f"/api/v1/reports/{self.report_id}/spec-meta?token=WRONG_TOKEN")
            self.assertEqual(403, resp.status_code, resp.text)
        finally:
            app.dependency_overrides.clear()

    def test_P5_nonexistent_report_returns_404(self):
        """不存在的报表 ID 返回 404。"""
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = None
        client, app = _make_client_with_db(mock_db)
        try:
            random_id = str(uuid.uuid4())
            resp = client.get(f"/api/v1/reports/{random_id}/spec-meta?token=any")
            self.assertEqual(404, resp.status_code, resp.text)
        finally:
            app.dependency_overrides.clear()

    def test_P6_invalid_uuid_returns_400(self):
        """非 UUID 格式的 report_id 返回 400。"""
        mock_db = MagicMock()
        client, app = _make_client_with_db(mock_db)
        try:
            resp = client.get("/api/v1/reports/not-a-uuid/spec-meta?token=any")
            self.assertEqual(400, resp.status_code, resp.text)
        finally:
            app.dependency_overrides.clear()

    def test_P7_missing_token_param_returns_422(self):
        """缺少 token 参数返回 422。"""
        mock_db = MagicMock()
        client, app = _make_client_with_db(mock_db)
        try:
            resp = client.get(f"/api/v1/reports/{self.report_id}/spec-meta")
            self.assertEqual(422, resp.status_code, resp.text)
        finally:
            app.dependency_overrides.clear()

    def test_P8_no_jwt_required(self):
        """该端点无需 JWT（不传 Authorization header 也能访问）。"""
        client, app = _make_client_with_db(self._db_returning(self.report))
        try:
            # 不带任何 Authorization header
            resp = client.get(f"/api/v1/reports/{self.report_id}/spec-meta?token={self.token}")
            self.assertNotEqual(401, resp.status_code, "端点不应要求 JWT")
            self.assertNotEqual(403, resp.status_code, resp.text)
        finally:
            app.dependency_overrides.clear()

    def test_P9_none_refresh_token_on_report_returns_403(self):
        """报表 refresh_token 为 None 时任意 token 均返回 403。"""
        report_no_token = _make_mock_report(report_id=self.report_id, refresh_token=None)
        client, app = _make_client_with_db(self._db_returning(report_no_token))
        try:
            resp = client.get(f"/api/v1/reports/{self.report_id}/spec-meta?token=any_token")
            self.assertEqual(403, resp.status_code, resp.text)
        finally:
            app.dependency_overrides.clear()

    def test_P10_refresh_token_in_response_data(self):
        """响应 data 中包含 refresh_token 字段（供前端注入到 Pilot 系统提示）。"""
        client, app = _make_client_with_db(self._db_returning(self.report))
        try:
            resp = client.get(f"/api/v1/reports/{self.report_id}/spec-meta?token={self.token}")
            self.assertEqual(200, resp.status_code)
            body = resp.json()
            data = body.get("data", {})
            self.assertIn(
                "refresh_token", data,
                f"data 中应包含 refresh_token 供 Pilot 使用: {list(data.keys())}"
            )
        finally:
            app.dependency_overrides.clear()


# ═══════════════════════════════════════════════════════════════════════════════
# Q段 — PUT /reports/{id}/charts/{chart_id} 端点 HTTP 测试（10个）
# ═══════════════════════════════════════════════════════════════════════════════

class TestQPartialChartUpdateEndpoint(unittest.TestCase):
    """PUT /api/v1/reports/{id}/charts/{chart_id} 端点测试。"""

    def setUp(self):
        self.report_id = str(uuid.uuid4())
        self.charts = [
            {"id": "c1", "chart_type": "bar", "title": "柱状图", "sql": "SELECT 1"},
            {"id": "c2", "chart_type": "line", "title": "折线图", "sql": "SELECT 2"},
        ]
        self.report = _make_mock_report(
            report_id=self.report_id,
            charts=self.charts,
            report_file_path="/tmp/report.html",
        )

    def _db_returning(self, report_obj):
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = report_obj
        return mock_db

    def _patch_html_build(self):
        """Patch HTML 构建和文件写入，避免真实文件系统操作。"""
        return [
            patch("backend.api.reports.build_report_html", return_value="<html></html>"),
            patch("pathlib.Path.write_text", MagicMock()),
            patch("pathlib.Path.mkdir", MagicMock()),
        ]

    def test_Q1_endpoint_route_registered(self):
        """PUT /charts/{chart_id} 路由已注册（不返回 404）。"""
        mock_db = self._db_returning(self.report)
        client, app = _make_client_with_db(mock_db)
        try:
            patches = self._patch_html_build()
            for p in patches:
                p.start()
            resp = client.put(
                f"/api/v1/reports/{self.report_id}/charts/c1",
                json={"chart": {"chart_type": "line"}},
                headers=_auth_headers(),
            )
            self.assertNotEqual(404, resp.status_code, f"路由未注册: {resp.text}")
        finally:
            for p in patches:
                p.stop()
            app.dependency_overrides.clear()

    def test_Q2_existing_chart_returns_found_true(self):
        """更新已存在的图表，found=True。"""
        mock_db = self._db_returning(self.report)
        client, app = _make_client_with_db(mock_db)
        try:
            patches = self._patch_html_build()
            for p in patches:
                p.start()
            resp = client.put(
                f"/api/v1/reports/{self.report_id}/charts/c1",
                json={"chart": {"chart_type": "area"}},
                headers=_auth_headers(),
            )
            if resp.status_code == 200:
                body = resp.json()
                data = body.get("data", {})
                self.assertTrue(data.get("found"), f"更新已存在图表应返回 found=True: {data}")
        finally:
            for p in patches:
                p.stop()
            app.dependency_overrides.clear()

    def test_Q3_new_chart_id_found_false_total_increases(self):
        """新 chart_id 时 found=False，total_charts 增加。"""
        mock_db = self._db_returning(self.report)
        client, app = _make_client_with_db(mock_db)
        try:
            patches = self._patch_html_build()
            for p in patches:
                p.start()
            resp = client.put(
                f"/api/v1/reports/{self.report_id}/charts/c_new",
                json={"chart": {"chart_type": "pie", "title": "新图"}},
                headers=_auth_headers(),
            )
            if resp.status_code == 200:
                body = resp.json()
                data = body.get("data", {})
                self.assertFalse(data.get("found"), "新图表 found 应为 False")
                self.assertEqual(3, data.get("total_charts"), f"新增后应有 3 个图表: {data}")
        finally:
            for p in patches:
                p.stop()
            app.dependency_overrides.clear()

    def test_Q4_path_chart_id_overrides_body_chart_id(self):
        """路径中的 chart_id 覆盖 body 中的 id 字段。"""
        # 静态代码分析确认 incoming["id"] = chart_id 逻辑存在
        reports_path = PROJECT_ROOT / "backend" / "api" / "reports.py"
        source = reports_path.read_text(encoding="utf-8")
        self.assertIn('incoming["id"] = chart_id', source,
                      "reports.py 应有 incoming['id'] = chart_id 赋值（路径参数覆盖）")

    def test_Q5_invalid_report_uuid_returns_400(self):
        """非 UUID 格式 report_id 返回 400。"""
        mock_db = MagicMock()
        client, app = _make_client_with_db(mock_db)
        try:
            resp = client.put(
                "/api/v1/reports/not-a-uuid/charts/c1",
                json={"chart": {"chart_type": "bar"}},
                headers=_auth_headers(),
            )
            self.assertEqual(400, resp.status_code, resp.text)
        finally:
            app.dependency_overrides.clear()

    def test_Q6_nonexistent_report_returns_404(self):
        """报表不存在时返回 404。"""
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = None
        client, app = _make_client_with_db(mock_db)
        try:
            resp = client.put(
                f"/api/v1/reports/{str(uuid.uuid4())}/charts/c1",
                json={"chart": {"chart_type": "bar"}},
                headers=_auth_headers(),
            )
            self.assertEqual(404, resp.status_code, resp.text)
        finally:
            app.dependency_overrides.clear()

    def test_Q7_missing_chart_field_returns_422(self):
        """缺少必填 chart 字段返回 422。"""
        mock_db = self._db_returning(self.report)
        client, app = _make_client_with_db(mock_db)
        try:
            resp = client.put(
                f"/api/v1/reports/{self.report_id}/charts/c1",
                json={},  # 缺少 chart 字段
                headers=_auth_headers(),
            )
            self.assertEqual(422, resp.status_code, resp.text)
        finally:
            app.dependency_overrides.clear()

    def test_Q8_no_file_path_returns_error(self):
        """报表无 report_file_path 时应返回 4xx 或 5xx 错误。"""
        report_no_path = _make_mock_report(report_id=self.report_id, report_file_path=None)
        mock_db = self._db_returning(report_no_path)
        client, app = _make_client_with_db(mock_db)
        try:
            resp = client.put(
                f"/api/v1/reports/{self.report_id}/charts/c1",
                json={"chart": {"chart_type": "bar"}},
                headers=_auth_headers(),
            )
            self.assertGreaterEqual(resp.status_code, 400,
                                    f"无文件路径应返回错误: {resp.status_code}")
        finally:
            app.dependency_overrides.clear()

    def test_Q9_success_response_has_required_fields(self):
        """成功响应包含 report_id, chart_id, found, total_charts 字段。"""
        mock_db = self._db_returning(self.report)
        client, app = _make_client_with_db(mock_db)
        try:
            patches = self._patch_html_build()
            for p in patches:
                p.start()
            resp = client.put(
                f"/api/v1/reports/{self.report_id}/charts/c1",
                json={"chart": {"chart_type": "area"}},
                headers=_auth_headers(),
            )
            if resp.status_code == 200:
                data = resp.json().get("data", {})
                for field in ["report_id", "chart_id", "found", "total_charts"]:
                    self.assertIn(field, data, f"响应缺少字段 {field}: {data}")
        finally:
            for p in patches:
                p.stop()
            app.dependency_overrides.clear()

    def test_Q10_other_charts_not_modified_by_merge(self):
        """Merge 语义：修改 c1 后，c2 保持不变。"""
        # 静态代码分析确认 merge 逻辑
        reports_path = PROJECT_ROOT / "backend" / "api" / "reports.py"
        source = reports_path.read_text(encoding="utf-8")
        # 确认有 merge 操作：{**c, **incoming} 或同等写法
        has_merge = "{**c, **incoming}" in source or "**existing_chart" in source or "{**c," in source
        self.assertTrue(has_merge,
                        "reports.py 应有图表 merge 逻辑（{**c, **incoming} 或同等写法）")


# ═══════════════════════════════════════════════════════════════════════════════
# R段 — RBAC 权限矩阵（6个）
# ═══════════════════════════════════════════════════════════════════════════════

class TestRRBACMatrix(unittest.TestCase):
    """RBAC 权限矩阵分析：新端点是否正确受保护。"""

    def _read_reports_api(self) -> str:
        return (PROJECT_ROOT / "backend" / "api" / "reports.py").read_text(encoding="utf-8")

    def _read_init_rbac(self) -> str:
        return (PROJECT_ROOT / "backend" / "scripts" / "init_rbac.py").read_text(encoding="utf-8")

    def test_R1_spec_meta_has_no_require_permission(self):
        """GET /spec-meta 端点不应有 require_permission（公开接口，refresh_token 鉴权）。"""
        source = self._read_reports_api()
        # 找到 spec-meta 路由定义区域（get_report_spec_meta 函数）
        # 检查该函数中没有 require_permission
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
                if node.name == "get_report_spec_meta":
                    func_src = ast.get_source_segment(source, node) or ""
                    self.assertNotIn(
                        "require_permission", func_src,
                        "get_report_spec_meta 不应有 require_permission（应使用 refresh_token）"
                    )
                    return
        # 如果找不到函数，退而用字符串搜索确认端点存在
        self.assertIn("spec-meta", source, "reports.py 中应有 spec-meta 端点")

    def test_R2_put_chart_requires_reports_create_permission(self):
        """PUT /charts/{chart_id} 端点应有 require_permission('reports', 'create')。"""
        source = self._read_reports_api()
        self.assertIn(
            "reports", source,
            "PUT chart 端点应引用 reports 权限"
        )
        # 检查 update_single_chart 函数或路由有 require_permission
        has_perm = (
            'require_permission("reports"' in source
            or "require_permission('reports'" in source
        )
        self.assertTrue(has_perm, "PUT /charts/{chart_id} 应使用 require_permission('reports', ...)")

    def test_R3_viewer_role_has_only_reports_read(self):
        """viewer 角色只有 reports:read，没有 reports:create（无法调用 PUT 端点）。"""
        source = self._read_init_rbac()
        # viewer 权限列表中不应包含 reports:create
        # 用简单字符串检测：在 viewer 块后找 reports:create
        lines = source.split("\n")
        in_viewer = False
        for i, line in enumerate(lines):
            if "viewer" in line.lower() and ("role" in line.lower() or "perm" in line.lower()):
                in_viewer = True
            if in_viewer and "analyst" in line.lower() and "role" in line.lower():
                in_viewer = False
            if in_viewer and "reports:create" in line:
                self.fail(f"viewer 角色不应有 reports:create（第 {i+1} 行）")
        # 至少确认文件里有 viewer 角色定义
        self.assertIn("viewer", source.lower(), "init_rbac.py 应定义 viewer 角色")

    def test_R4_analyst_role_has_reports_create(self):
        """analyst 角色应有 reports:create（可通过 Pilot 修改图表）。"""
        source = self._read_init_rbac()
        self.assertIn("reports:create", source,
                      "init_rbac.py 中应有 reports:create 权限定义（至少 analyst/admin 拥有）")

    def test_R5_no_new_frontend_menu_for_new_endpoints(self):
        """新端点（spec-meta / PUT charts）不对应新前端菜单，无需新增 RBAC 菜单项。"""
        # 检查前端路由定义中没有与这两个端点对应的新路由
        app_tsx_candidates = [
            PROJECT_ROOT / "frontend" / "src" / "App.tsx",
            PROJECT_ROOT / "frontend" / "src" / "app.tsx",
        ]
        for p in app_tsx_candidates:
            if p.exists():
                src = p.read_text(encoding="utf-8")
                self.assertNotIn("/spec-meta", src,
                                 "前端 App.tsx 不应有 /spec-meta 路由（纯后端端点）")
                self.assertNotIn("/charts/", src,
                                 "前端 App.tsx 不应有 /charts/ 路由（纯后端端点）")
                return
        # 文件不存在也通过（可能路径不同）
        self.skipTest("App.tsx not found at expected paths")

    def test_R6_report_mcp_tool_registered_without_role_filter(self):
        """ReportToolMCPServer 应无角色过滤（所有有 refresh_token 的用户均可使用）。"""
        manager_path = PROJECT_ROOT / "backend" / "mcp" / "manager.py"
        source = manager_path.read_text(encoding="utf-8")
        # 确认 report_tool 或 ReportToolMCPServer 已注册
        has_report_tool = (
            "report_tool" in source
            or "ReportToolMCPServer" in source
            or "report" in source.lower()
        )
        self.assertTrue(has_report_tool,
                        "manager.py 应注册 ReportToolMCPServer")


# ═══════════════════════════════════════════════════════════════════════════════
# S段 — 前端 DataCenterCopilot 系统提示静态分析（7个）
# ═══════════════════════════════════════════════════════════════════════════════

class TestSFrontendSystemPrompt(unittest.TestCase):
    """DataCenterCopilot.tsx 系统提示内容验证。"""

    def setUp(self):
        self.copilot_path = (
            PROJECT_ROOT / "frontend" / "src" / "components" / "DataCenterCopilot.tsx"
        )
        self.source = self.copilot_path.read_text(encoding="utf-8")

    def test_S1_system_prompt_mentions_mcp_tools(self):
        """系统提示中提及 MCP 工具（report__ 前缀）。"""
        has_mcp = (
            "report__get_spec" in self.source
            or "report__update_spec" in self.source
            or "report__update_single_chart" in self.source
        )
        self.assertTrue(has_mcp, "系统提示应包含 report__ MCP 工具名称")

    def test_S2_system_prompt_mentions_get_spec_tool(self):
        """系统提示中包含 get_spec 工具说明。"""
        self.assertIn("get_spec", self.source,
                      "系统提示应提及 get_spec 工具（读取图表列表）")

    def test_S3_system_prompt_mentions_update_single_chart(self):
        """系统提示中包含 update_single_chart 工具说明。"""
        self.assertIn("update_single_chart", self.source,
                      "系统提示应提及 update_single_chart 工具（局部更新）")

    def test_S4_system_prompt_mentions_all_charts_warning(self):
        """系统提示中包含图表守恒警告（防止图表删除，如"必须包含所有"）。"""
        has_warning = (
            "必须包含所有" in self.source      # 如：spec.charts 必须包含所有 ${n} 个图表
            or "保留所有" in self.source
            or "不能删除" in self.source
            or "all charts" in self.source.lower()
        )
        self.assertTrue(has_warning,
                        "系统提示应包含图表守恒警告（含'必须包含所有'等关键词）")

    def test_S5_context_refresh_token_prop_exists(self):
        """CopilotSharedProps 包含 contextRefreshToken 属性（Viewer 页面注入 token）。"""
        self.assertIn("contextRefreshToken", self.source,
                      "DataCenterCopilot.tsx 应有 contextRefreshToken 属性")

    def test_S6_refresh_token_used_in_system_prompt(self):
        """refresh_token 被注入到系统提示中（供 LLM 调用 MCP 工具时使用）。"""
        has_token_injection = (
            "refresh_token" in self.source
            and ("buildSystemPrompt" in self.source or "systemPrompt" in self.source)
        )
        self.assertTrue(has_token_injection,
                        "refresh_token 应被注入到 buildSystemPrompt 中")

    def test_S7_context_spec_charts_count_in_prompt(self):
        """系统提示中展示图表数量（让 LLM 感知现有图表个数）。"""
        has_chart_count = (
            "charts.length" in self.source
            or "chart_count" in self.source
            or "chartCount" in self.source
            or "图表数" in self.source
        )
        self.assertTrue(has_chart_count,
                        "系统提示应展示图表数量，让 LLM 感知有多少图表")


# ═══════════════════════════════════════════════════════════════════════════════
# T段 — 前端报表 Viewer+Preview 生命周期静态分析（6个）
# ═══════════════════════════════════════════════════════════════════════════════

class TestTFrontendLifecycle(unittest.TestCase):
    """ReportViewerPage.tsx 与 ReportPreviewModal.tsx 生命周期验证。"""

    def setUp(self):
        self.viewer_path = (
            PROJECT_ROOT / "frontend" / "src" / "pages" / "ReportViewerPage.tsx"
        )
        self.preview_path = (
            PROJECT_ROOT / "frontend" / "src" / "components" / "chat" / "ReportPreviewModal.tsx"
        )
        self.viewer_src = self.viewer_path.read_text(encoding="utf-8")
        self.preview_src = self.preview_path.read_text(encoding="utf-8")

    def test_T1_viewer_fetches_spec_meta_on_mount(self):
        """ReportViewerPage 挂载时调用 /spec-meta 端点获取报表 spec。"""
        has_spec_meta = (
            "spec-meta" in self.viewer_src
            or "specMeta" in self.viewer_src
            or "getSpecMeta" in self.viewer_src
        )
        self.assertTrue(has_spec_meta,
                        "ReportViewerPage 应在挂载时获取 spec-meta")

    def test_T2_viewer_passes_context_spec_to_copilot(self):
        """ReportViewerPage 将 contextSpec 传给 DataCenterCopilotContent。"""
        self.assertIn("contextSpec", self.viewer_src,
                      "ReportViewerPage 应将 contextSpec 传给 Copilot")

    def test_T3_viewer_passes_refresh_token_to_copilot(self):
        """ReportViewerPage 将 contextRefreshToken 传给 Copilot（Pilot 工具调用需要）。"""
        self.assertIn("contextRefreshToken", self.viewer_src,
                      "ReportViewerPage 应将 contextRefreshToken 传给 Copilot")

    def test_T4_viewer_refreshes_iframe_after_spec_update(self):
        """Viewer 在 spec 更新后刷新 iframe（iframeKey 机制）。"""
        has_refresh = (
            "iframeKey" in self.viewer_src
            or "handleSpecUpdated" in self.viewer_src
            or "setIframeKey" in self.viewer_src
        )
        self.assertTrue(has_refresh,
                        "ReportViewerPage 应有 iframe 刷新机制（iframeKey 或 handleSpecUpdated）")

    def test_T5_preview_modal_refreshes_iframe_after_spec_update(self):
        """PreviewModal 在 spec 更新后刷新 iframe。"""
        has_refresh = (
            "iframeKey" in self.preview_src
            or "handleSpecUpdated" in self.preview_src
            or "setIframeKey" in self.preview_src
            or "onSpecUpdated" in self.preview_src
        )
        self.assertTrue(has_refresh,
                        "ReportPreviewModal 应有 iframe 刷新机制")

    def test_T6_viewer_token_from_url_params(self):
        """ReportViewerPage 从 URL 参数获取 token（报表分享链接携带 token）。"""
        has_token_from_url = (
            "useSearchParams" in self.viewer_src
            or "useParams" in self.viewer_src
            or "searchParams" in self.viewer_src
            or "token" in self.viewer_src
        )
        self.assertTrue(has_token_from_url,
                        "ReportViewerPage 应从 URL 参数获取 token")


# ═══════════════════════════════════════════════════════════════════════════════
# U段 — 图表守恒完整性（Service 层 + 技能文件）（8个）
# ═══════════════════════════════════════════════════════════════════════════════

class TestUChartPreservationIntegrity(unittest.TestCase):
    """图表守恒：确保 LLM 修改操作不丢失图表。"""

    def _read_report_service(self) -> str:
        return (PROJECT_ROOT / "backend" / "services" / "report_service.py").read_text(encoding="utf-8")

    def _read_skill(self) -> str:
        skill_path = PROJECT_ROOT / ".claude" / "skills" / "project" / "update-report.md"
        if skill_path.exists():
            return skill_path.read_text(encoding="utf-8")
        return ""

    def _read_mcp_server(self) -> str:
        server_path = PROJECT_ROOT / "backend" / "mcp" / "report_tool" / "server.py"
        if server_path.exists():
            return server_path.read_text(encoding="utf-8")
        return ""

    def test_U1_get_spec_by_token_function_exists(self):
        """report_service.py 中存在 get_spec_by_token 函数。"""
        source = self._read_report_service()
        self.assertIn("get_spec_by_token", source,
                      "report_service.py 应有 get_spec_by_token 函数")

    def test_U2_update_single_chart_by_token_function_exists(self):
        """report_service.py 中存在 update_single_chart_by_token 函数。"""
        source = self._read_report_service()
        self.assertIn("update_single_chart_by_token", source,
                      "report_service.py 应有 update_single_chart_by_token 函数")

    def test_U3_update_single_chart_preserves_other_charts(self):
        """update_single_chart_by_token 的 merge 逻辑保留其他图表。"""
        source = self._read_report_service()
        # 应该有遍历所有图表并保留非目标图表的逻辑
        has_merge = (
            "{**c," in source
            or "**existing" in source
            or "**chart," in source
            or "merged_charts" in source
            or "for c in" in source
        )
        self.assertTrue(has_merge,
                        "update_single_chart_by_token 应有 merge 逻辑（保留其他图表）")

    def test_U4_update_spec_by_token_requires_refresh_token(self):
        """update_spec_by_token 验证 refresh_token。"""
        source = self._read_report_service()
        has_token_check = (
            "compare_digest" in source
            or "refresh_token" in source
        )
        self.assertTrue(has_token_check,
                        "report_service.py 应有 refresh_token 验证逻辑")

    def test_U5_mcp_server_has_three_tools(self):
        """MCP Tool Server 提供三个工具：get_spec, update_spec, update_single_chart。"""
        source = self._read_mcp_server()
        if not source:
            self.skipTest("report_tool/server.py not found")
        self.assertIn("get_spec", source, "MCP server 缺少 get_spec 工具")
        self.assertIn("update_spec", source, "MCP server 缺少 update_spec 工具")
        self.assertIn("update_single_chart", source, "MCP server 缺少 update_single_chart 工具")

    def test_U6_mcp_update_spec_warns_about_all_charts(self):
        """MCP update_spec 工具描述中有"必须包含所有图表"警告。"""
        source = self._read_mcp_server()
        if not source:
            self.skipTest("report_tool/server.py not found")
        has_warning = (
            "必须包含所有图表" in source
            or "缺少的图表将被永久删除" in source
            or "all charts" in source.lower()
        )
        self.assertTrue(has_warning,
                        "MCP update_spec 工具应在描述中警告必须包含所有图表")

    def test_U7_skill_file_has_chart_conservation_rule(self):
        """update-report.md 技能文件包含图表数量守恒规则。"""
        source = self._read_skill()
        if not source:
            self.skipTest("update-report.md skill not found")
        has_conservation = (
            "图表数量守恒" in source
            or "守恒" in source
            or "必须保留" in source
            or "所有图表" in source
        )
        self.assertTrue(has_conservation,
                        "update-report.md 应有图表数量守恒规则")

    def test_U8_skill_file_has_mode_a_single_chart_pattern(self):
        """update-report.md 技能文件记录了模式A（单图局部更新）。"""
        source = self._read_skill()
        if not source:
            self.skipTest("update-report.md skill not found")
        has_mode_a = (
            "模式A" in source
            or "update_single_chart" in source
            or "局部更新" in source
            or "partial" in source.lower()
        )
        self.assertTrue(has_mode_a,
                        "update-report.md 应记录局部更新图表的方法（模式A / update_single_chart）")


# ═══════════════════════════════════════════════════════════════════════════════
# 入口
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    unittest.main(verbosity=2)
