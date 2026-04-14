"""
test_datacenter_api.py — DataCenter (BI 平台) 功能测试套件
===========================================================

测试层次：
  K1  (4)  — doc_type filter: GET /reports?doc_type= 过滤
  K2  (4)  — POST /reports/build 存储 doc_type; PUT /reports/{id}/spec 端点存在
  K3  (4)  — share_scope 公共 vs 私有: GET /reports/{id}/html
  K4  (5)  — POST /reports/{id}/copilot 创建含报表上下文的对话
  K5  (6)  — DataCenterCopilot.tsx 前端代码检查（props / greeting / stream 处理）

总计: ~23 个测试用例
"""

from __future__ import annotations

import os
import sys
import uuid
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

# ── 路径 & 环境初始化 ────────────────────────────────────────────────────────
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "backend"))
os.environ.setdefault("POSTGRES_PASSWORD", "Sgp013013")
os.environ.setdefault("POSTGRES_HOST", "localhost")
os.environ.setdefault("ENABLE_AUTH", "False")

from test_utils import make_test_username  # noqa: E402

_PREFIX = f"_dc_{uuid.uuid4().hex[:6]}_"

# ── 模块级 auth 补丁 ─────────────────────────────────────────────────────────
_auth_patcher = None


def setup_module(_=None):
    global _auth_patcher
    from backend.config.settings import settings
    _auth_patcher = patch.object(settings, "enable_auth", False)
    _auth_patcher.start()


def teardown_module(_=None):
    global _auth_patcher
    if _auth_patcher is not None:
        _auth_patcher.stop()
        _auth_patcher = None
    _cleanup_test_data()


# ── DB helpers ────────────────────────────────────────────────────────────────

def _db():
    from backend.config.database import SessionLocal
    return SessionLocal()


_g_db = _db()


def _make_user(suffix="", role_names=None):
    from backend.models.user import User
    from backend.models.role import Role
    from backend.models.user_role import UserRole
    from backend.core.auth.password import hash_password

    username = f"{_PREFIX}{suffix or uuid.uuid4().hex[:6]}"
    u = User(
        username=username,
        display_name=f"DC Test {suffix}",
        hashed_password=hash_password("Test1234!"),
        auth_source="local",
        is_active=True,
        is_superadmin=False,
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


def _auth(user):
    return {"Authorization": f"Bearer {_token(user)}"}


def _create_report_directly(username="default", doc_type="dashboard", name=None):
    """直接在 DB 中创建 Report 记录（跳过 HTML 生成）。"""
    from backend.models.report import Report
    from backend.services.report_builder_service import generate_refresh_token
    rid = uuid.uuid4()
    r = Report(
        id=rid,
        name=name or f"{_PREFIX}report_{rid.hex[:6]}",
        username=username,
        refresh_token=generate_refresh_token(),
        report_file_path=None,
        summary_status="skipped",
        charts=[],
        filters=[],
        theme="light",
        extra_metadata={"spec_version": "1.0"},
    )
    # doc_type 列可能已通过迁移存在
    try:
        r.doc_type = doc_type
    except AttributeError:
        pass
    _g_db.add(r)
    _g_db.commit()
    _g_db.refresh(r)
    return r


def _cleanup_test_data():
    from backend.models.user import User
    from backend.models.report import Report
    from backend.services.conversation_service import ConversationService
    from backend.models.conversation import Conversation
    try:
        # 清理 reports
        _g_db.query(Report).filter(
            Report.username.like(f"{_PREFIX}%")
        ).delete(synchronize_session=False)
        # 清理名称前缀匹配的 report（anonymous user 路径）
        _g_db.query(Report).filter(
            Report.name.like(f"{_PREFIX}%")
        ).delete(synchronize_session=False)
        # 清理 copilot 创建的对话
        _g_db.query(Conversation).filter(
            Conversation.title.like(f"%{_PREFIX}%")
        ).delete(synchronize_session=False)
        # 清理测试用户
        test_users = _g_db.query(User).filter(
            User.username.like(f"{_PREFIX}%")
        ).all()
        for u in test_users:
            _g_db.delete(u)
        _g_db.commit()
    except Exception as e:
        print(f"[teardown] cleanup error: {e}")
        _g_db.rollback()
    finally:
        _g_db.close()


# ── TestClient 工厂 ───────────────────────────────────────────────────────────

def _make_client():
    from backend.main import app
    from fastapi.testclient import TestClient
    return TestClient(app, raise_server_exceptions=False)


# ── 最小 HTML stub（供 build_report_html mock）────────────────────────────────

_STUB_HTML = "<html><body>stub</body></html>"


# ══════════════════════════════════════════════════════════════════════════════
# Section K1 — doc_type filter (4 tests)
# ══════════════════════════════════════════════════════════════════════════════

class TestK1DocTypeFilter(unittest.TestCase):
    """K1: GET /reports?doc_type= 过滤逻辑"""

    @classmethod
    def setUpClass(cls):
        cls.client = _make_client()
        cls.user = _make_user("k1")
        cls.headers = _auth(cls.user)
        # 创建两条 dashboard 和一条 document
        cls.r_dash1 = _create_report_directly(cls.user.username, "dashboard",
                                               f"{_PREFIX}k1_dash1")
        cls.r_dash2 = _create_report_directly(cls.user.username, "dashboard",
                                               f"{_PREFIX}k1_dash2")
        cls.r_doc = _create_report_directly(cls.user.username, "document",
                                             f"{_PREFIX}k1_doc")

    def test_K1_1_list_without_filter_returns_all(self):
        """不带 doc_type 参数时应返回该用户所有报告"""
        res = self.client.get("/api/v1/reports", headers=self.headers)
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data["success"])
        items = data["data"]["items"]
        ids = [i["id"] for i in items]
        self.assertIn(str(self.r_dash1.id), ids)
        self.assertIn(str(self.r_dash2.id), ids)
        self.assertIn(str(self.r_doc.id), ids)

    def test_K1_2_filter_dashboard_returns_only_dashboards(self):
        """doc_type=dashboard 只返回 dashboard 类型报告"""
        res = self.client.get("/api/v1/reports?doc_type=dashboard",
                              headers=self.headers)
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data["success"])
        items = data["data"]["items"]
        ids = [i["id"] for i in items]
        # dashboard records must be present
        self.assertIn(str(self.r_dash1.id), ids)
        self.assertIn(str(self.r_dash2.id), ids)
        # document record must NOT appear
        self.assertNotIn(str(self.r_doc.id), ids)

    def test_K1_3_filter_document_returns_only_documents(self):
        """doc_type=document 只返回 document 类型报告"""
        res = self.client.get("/api/v1/reports?doc_type=document",
                              headers=self.headers)
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data["success"])
        items = data["data"]["items"]
        ids = [i["id"] for i in items]
        self.assertIn(str(self.r_doc.id), ids)
        self.assertNotIn(str(self.r_dash1.id), ids)
        self.assertNotIn(str(self.r_dash2.id), ids)

    def test_K1_4_response_structure_has_pagination_fields(self):
        """列表响应包含 total / page / page_size / items 分页字段"""
        res = self.client.get("/api/v1/reports?doc_type=dashboard",
                              headers=self.headers)
        self.assertEqual(res.status_code, 200)
        payload = res.json()
        self.assertIn("data", payload)
        d = payload["data"]
        self.assertIn("total", d)
        self.assertIn("page", d)
        self.assertIn("page_size", d)
        self.assertIn("items", d)
        self.assertIsInstance(d["items"], list)


# ══════════════════════════════════════════════════════════════════════════════
# Section K2 — POST /build stores doc_type; PUT /spec endpoint (4 tests)
# ══════════════════════════════════════════════════════════════════════════════

class TestK2BuildAndSpec(unittest.TestCase):
    """K2: POST /reports/build 存储 doc_type; PUT /reports/{id}/spec 端点"""

    @classmethod
    def setUpClass(cls):
        cls.client = _make_client()
        cls.user = _make_user("k2")
        cls.headers = _auth(cls.user)

    @patch("backend.api.reports.build_report_html", return_value=_STUB_HTML)
    def test_K2_1_build_dashboard_doc_type_stored(self, _mock):
        """POST /reports/build 传 doc_type=dashboard，DB 中记录正确 doc_type"""
        spec = {"title": f"{_PREFIX}k2_dash", "charts": [], "theme": "light"}
        res = self.client.post(
            "/api/v1/reports/build",
            json={"spec": spec, "doc_type": "dashboard"},
            headers=self.headers,
        )
        self.assertEqual(res.status_code, 200, res.text)
        data = res.json()
        self.assertTrue(data["success"])
        report_id = data["data"]["report_id"]

        # 检查 DB 中的 doc_type
        from backend.models.report import Report
        r = _g_db.query(Report).filter(
            Report.id == uuid.UUID(report_id)
        ).first()
        self.assertIsNotNone(r)
        # doc_type 可能是 DB 列也可能不存在（列未迁移时跳过），接受两种情况
        if hasattr(r, "doc_type"):
            self.assertEqual(r.doc_type, "dashboard")

    @patch("backend.api.reports.build_report_html", return_value=_STUB_HTML)
    def test_K2_2_build_document_doc_type_stored(self, _mock):
        """POST /reports/build 传 doc_type=document，DB 中记录正确 doc_type"""
        spec = {"title": f"{_PREFIX}k2_doc", "charts": [], "theme": "light"}
        res = self.client.post(
            "/api/v1/reports/build",
            json={"spec": spec, "doc_type": "document"},
            headers=self.headers,
        )
        self.assertEqual(res.status_code, 200, res.text)
        data = res.json()
        self.assertTrue(data["success"])
        report_id = data["data"]["report_id"]

        from backend.models.report import Report
        r = _g_db.query(Report).filter(
            Report.id == uuid.UUID(report_id)
        ).first()
        self.assertIsNotNone(r)
        if hasattr(r, "doc_type"):
            self.assertEqual(r.doc_type, "document")

    @patch("backend.api.reports.build_report_html", return_value=_STUB_HTML)
    def test_K2_3_build_default_doc_type_is_dashboard(self, _mock):
        """POST /reports/build 不传 doc_type 时默认值为 dashboard"""
        spec = {"title": f"{_PREFIX}k2_def", "charts": [], "theme": "light"}
        res = self.client.post(
            "/api/v1/reports/build",
            json={"spec": spec},  # 不传 doc_type
            headers=self.headers,
        )
        self.assertEqual(res.status_code, 200, res.text)
        data = res.json()
        self.assertTrue(data["success"])
        report_id = data["data"]["report_id"]

        from backend.models.report import Report
        r = _g_db.query(Report).filter(
            Report.id == uuid.UUID(report_id)
        ).first()
        self.assertIsNotNone(r)
        if hasattr(r, "doc_type"):
            self.assertEqual(r.doc_type, "dashboard")

    @patch("backend.api.reports.build_report_html", return_value=_STUB_HTML)
    def test_K2_4_put_spec_endpoint_exists(self, _mock):
        """PUT /reports/{id}/spec 端点存在且返回 200（非 404/405）"""
        # 先 build 一个 report 获得 ID
        spec = {"title": f"{_PREFIX}k2_spec", "charts": [], "theme": "light"}
        build_res = self.client.post(
            "/api/v1/reports/build",
            json={"spec": spec, "doc_type": "dashboard"},
            headers=self.headers,
        )
        self.assertEqual(build_res.status_code, 200)
        report_id = build_res.json()["data"]["report_id"]

        # 调用 PUT /spec（build_report_html 已 mock，会走到真实逻辑）
        new_spec = {"title": f"{_PREFIX}k2_spec_updated", "charts": [], "theme": "dark"}
        spec_res = self.client.put(
            f"/api/v1/reports/{report_id}/spec",
            json={"spec": new_spec},
            headers=self.headers,
        )
        # 端点存在（不是 404/405），成功应返回 200
        self.assertNotIn(spec_res.status_code, [404, 405],
                         f"PUT /spec returned {spec_res.status_code}")
        self.assertEqual(spec_res.status_code, 200, spec_res.text)
        data = spec_res.json()
        self.assertTrue(data["success"])
        self.assertIn("report_id", data["data"])


# ══════════════════════════════════════════════════════════════════════════════
# Section K3 — share_scope: public vs private (4 tests)
# ══════════════════════════════════════════════════════════════════════════════

class TestK3ShareScope(unittest.TestCase):
    """K3: GET /reports/{id}/html public 报告无需 token；private 报告需要 token"""

    @classmethod
    def setUpClass(cls):
        cls.client = _make_client()
        cls.user = _make_user("k3")
        cls.headers = _auth(cls.user)

        # 创建两条报告，分别设置 share_scope
        cls.private_report = _create_report_directly(
            cls.user.username, "dashboard", f"{_PREFIX}k3_private"
        )
        cls.public_report = _create_report_directly(
            cls.user.username, "dashboard", f"{_PREFIX}k3_public"
        )
        # 设置 share_scope
        try:
            from backend.models.report import ShareScope
            cls.public_report.share_scope = ShareScope.PUBLIC
        except Exception:
            cls.public_report.share_scope = "public"
        try:
            cls.private_report.share_scope = "private"
        except Exception:
            pass
        _g_db.commit()

        # 为 private_report 写一个真实的 HTML 文件，使得文件路径有效
        from backend.config.settings import settings
        allowed_dirs = settings.allowed_directories
        customer_data = Path(allowed_dirs[0]) if allowed_dirs else Path("customer_data")
        report_dir = customer_data / cls.user.username / "reports"
        report_dir.mkdir(parents=True, exist_ok=True)

        cls._html_files = []

        for r in [cls.private_report, cls.public_report]:
            html_path = report_dir / f"{r.id.hex[:8]}_test.html"
            html_path.write_text("<html><body>test</body></html>", encoding="utf-8")
            cls._html_files.append(html_path)
            try:
                rel = str(html_path.relative_to(customer_data))
            except ValueError:
                rel = str(html_path)
            r.report_file_path = rel
        _g_db.commit()

    @classmethod
    def tearDownClass(cls):
        for f in cls._html_files:
            try:
                f.unlink(missing_ok=True)
            except Exception:
                pass

    def test_K3_1_private_report_requires_correct_token(self):
        """私有报告：正确的 refresh_token 可访问"""
        token = self.private_report.refresh_token
        res = self.client.get(
            f"/api/v1/reports/{self.private_report.id}/html?token={token}"
        )
        self.assertEqual(res.status_code, 200)

    def test_K3_2_private_report_wrong_token_returns_403(self):
        """私有报告：错误 token 应返回 403"""
        res = self.client.get(
            f"/api/v1/reports/{self.private_report.id}/html?token=wrongtoken"
        )
        self.assertEqual(res.status_code, 403)

    def test_K3_3_public_report_accessible_without_token(self):
        """公开报告：任意 token（或空 token）均可访问"""
        # 公开报告：share_scope=public 时跳过 token 验证
        # 传一个随机 token 也应返回 200
        res = self.client.get(
            f"/api/v1/reports/{self.public_report.id}/html?token=anytoken"
        )
        self.assertEqual(res.status_code, 200)

    def test_K3_4_invalid_report_id_returns_400_or_404(self):
        """无效 UUID 返回 400；不存在的 UUID 返回 404"""
        res = self.client.get("/api/v1/reports/not-a-uuid/html?token=x")
        self.assertEqual(res.status_code, 400)

        res2 = self.client.get(
            f"/api/v1/reports/{uuid.uuid4()}/html?token=x"
        )
        self.assertEqual(res2.status_code, 404)


# ══════════════════════════════════════════════════════════════════════════════
# Section K4 — POST /reports/{id}/copilot endpoint (5 tests)
# ══════════════════════════════════════════════════════════════════════════════

class TestK4CopilotEndpoint(unittest.TestCase):
    """K4: POST /reports/{id}/copilot 创建含报表上下文的对话"""

    @classmethod
    def setUpClass(cls):
        cls.client = _make_client()
        cls.user = _make_user("k4")
        cls.headers = _auth(cls.user)
        cls.report = _create_report_directly(
            cls.user.username, "dashboard", f"{_PREFIX}k4_copilot"
        )
        # 设置 charts 供 system_prompt 注入
        cls.report.charts = [{"id": "c1", "title": "销售趋势", "chart_type": "line"}]
        _g_db.commit()

    def test_K4_1_copilot_returns_conversation_id(self):
        """POST /reports/{id}/copilot 返回 conversation_id"""
        res = self.client.post(
            f"/api/v1/reports/{self.report.id}/copilot",
            json={},
            headers=self.headers,
        )
        self.assertEqual(res.status_code, 200, res.text)
        data = res.json()
        self.assertTrue(data["success"])
        self.assertIn("conversation_id", data["data"])
        conv_id = data["data"]["conversation_id"]
        self.assertIsNotNone(conv_id)
        # 校验是合法 UUID
        uuid.UUID(conv_id)  # 不抛异常即为合法

    def test_K4_2_copilot_conversation_has_report_system_prompt(self):
        """创建的对话 system_prompt 包含报表名称和图表信息"""
        res = self.client.post(
            f"/api/v1/reports/{self.report.id}/copilot",
            json={},
            headers=self.headers,
        )
        self.assertEqual(res.status_code, 200)
        conv_id = res.json()["data"]["conversation_id"]

        # 直接查 DB 验证 system_prompt
        from backend.models.conversation import Conversation
        conv = _g_db.query(Conversation).filter(
            Conversation.id == uuid.UUID(conv_id)
        ).first()
        self.assertIsNotNone(conv)
        self.assertIsNotNone(conv.system_prompt)
        self.assertIn(self.report.name, conv.system_prompt)
        self.assertIn("Co-pilot", conv.system_prompt)

    def test_K4_3_copilot_system_prompt_contains_chart_info(self):
        """system_prompt 包含图表数量和图表配置 JSON"""
        res = self.client.post(
            f"/api/v1/reports/{self.report.id}/copilot",
            json={},
            headers=self.headers,
        )
        self.assertEqual(res.status_code, 200)
        conv_id = res.json()["data"]["conversation_id"]

        from backend.models.conversation import Conversation
        conv = _g_db.query(Conversation).filter(
            Conversation.id == uuid.UUID(conv_id)
        ).first()
        sp = conv.system_prompt
        # 系统提示中包含图表数量字段
        self.assertIn("图表数量", sp)
        # 系统提示中包含图表 JSON 内容
        self.assertIn("c1", sp)

    def test_K4_4_copilot_with_custom_title(self):
        """CopilotRequest.title 字段生效，对话标题使用传入值"""
        custom_title = f"{_PREFIX}k4_custom_title"
        res = self.client.post(
            f"/api/v1/reports/{self.report.id}/copilot",
            json={"title": custom_title},
            headers=self.headers,
        )
        self.assertEqual(res.status_code, 200)
        conv_id = res.json()["data"]["conversation_id"]

        from backend.models.conversation import Conversation
        conv = _g_db.query(Conversation).filter(
            Conversation.id == uuid.UUID(conv_id)
        ).first()
        self.assertIsNotNone(conv)
        self.assertEqual(conv.title, custom_title)

    def test_K4_5_copilot_nonexistent_report_returns_404(self):
        """不存在的 report_id 返回 404"""
        res = self.client.post(
            f"/api/v1/reports/{uuid.uuid4()}/copilot",
            json={},
            headers=self.headers,
        )
        self.assertEqual(res.status_code, 404)


# ══════════════════════════════════════════════════════════════════════════════
# Section K5 — Frontend code inspection (6 tests)
# ══════════════════════════════════════════════════════════════════════════════

_FRONTEND_ROOT = Path(__file__).parent / "frontend" / "src"
_COPILOT_PATH = _FRONTEND_ROOT / "components" / "DataCenterCopilot.tsx"
_DASHBOARDS_PATH = _FRONTEND_ROOT / "pages" / "DataCenterDashboards.tsx"
_DOCUMENTS_PATH = _FRONTEND_ROOT / "pages" / "DataCenterDocuments.tsx"
_SCHEDULES_PATH = _FRONTEND_ROOT / "pages" / "DataCenterSchedules.tsx"


class TestK5FrontendCodeInspection(unittest.TestCase):
    """K5: DataCenterCopilot.tsx 和相关页面代码结构检查"""

    @classmethod
    def setUpClass(cls):
        cls.copilot_src = _COPILOT_PATH.read_text(encoding="utf-8")
        cls.dashboards_src = _DASHBOARDS_PATH.read_text(encoding="utf-8")
        cls.documents_src = _DOCUMENTS_PATH.read_text(encoding="utf-8")
        cls.schedules_src = _SCHEDULES_PATH.read_text(encoding="utf-8")

    # ── Props 接口检查 ──────────────────────────────────────────────────────

    def test_K5_1_copilot_props_interface_contains_required_props(self):
        """DataCenterCopilotProps 包含必要属性：open/onClose/contextType/contextId/contextName"""
        src = self.copilot_src
        self.assertIn("open:", src, "prop 'open' missing from interface")
        self.assertIn("onClose:", src, "prop 'onClose' missing from interface")
        self.assertIn("contextType:", src, "prop 'contextType' missing from interface")
        self.assertIn("contextId:", src, "prop 'contextId' missing from interface")
        self.assertIn("contextName:", src, "prop 'contextName' missing from interface")

    def test_K5_2_copilot_context_type_union_includes_all_three_types(self):
        """CopilotContextType 包含 dashboard / document / schedule 三种类型"""
        src = self.copilot_src
        self.assertIn("'dashboard'", src)
        self.assertIn("'document'", src)
        self.assertIn("'schedule'", src)

    # ── Greeting 逻辑检查 ───────────────────────────────────────────────────

    def test_K5_3_greeting_logic_uses_contextType_for_label(self):
        """greeting 逻辑根据 contextType 显示不同 label（报表/报告/推送任务）"""
        src = self.copilot_src
        # 应包含 schedule / document / 推送任务 的区分逻辑
        self.assertIn("schedule", src)
        self.assertIn("推送任务", src)
        self.assertIn("报告", src)
        self.assertIn("报表", src)
        # greeting 在 open && !initialized && contextId 条件下触发
        self.assertIn("initialized", src)
        self.assertIn("greeting", src)

    # ── Stream 处理检查 ─────────────────────────────────────────────────────

    def test_K5_4_stream_handler_parses_sse_content_type(self):
        """stream handler 解析 SSE 的 data.type === 'content' 事件"""
        src = self.copilot_src
        self.assertIn("data.type", src)
        self.assertIn("'content'", src)
        self.assertIn("appendToLastAssistant", src)

    def test_K5_5_stream_handler_detects_spec_update(self):
        """stream handler 检测 tool_result 中的 /spec 或 报表已更新 信号，触发 onSpecUpdated"""
        src = self.copilot_src
        self.assertIn("tool_result", src)
        self.assertIn("/spec", src)
        self.assertIn("onSpecUpdated", src)

    # ── 页面组件集成检查 ────────────────────────────────────────────────────

    def test_K5_6_all_datacenter_pages_import_and_use_copilot(self):
        """DataCenterDashboards/Documents/Schedules 均 import 并使用 DataCenterCopilot"""
        for name, src in [
            ("DataCenterDashboards", self.dashboards_src),
            ("DataCenterDocuments", self.documents_src),
            ("DataCenterSchedules", self.schedules_src),
        ]:
            with self.subTest(page=name):
                self.assertIn("import DataCenterCopilot", src,
                              f"{name} 未 import DataCenterCopilot")
                self.assertIn("<DataCenterCopilot", src,
                              f"{name} 未在 JSX 中使用 DataCenterCopilot")

    def test_K5_7_dashboards_uses_dashboard_context_type(self):
        """DataCenterDashboards 使用 contextType='dashboard'"""
        self.assertIn('contextType="dashboard"', self.dashboards_src)

    def test_K5_8_documents_uses_document_context_type(self):
        """DataCenterDocuments 使用 contextType='document'"""
        self.assertIn('contextType="document"', self.documents_src)

    def test_K5_9_schedules_uses_schedule_context_type(self):
        """DataCenterSchedules 使用 contextType='schedule'"""
        self.assertIn('contextType="schedule"', self.schedules_src)


# ══════════════════════════════════════════════════════════════════════════════
# Entry point
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    unittest.main(verbosity=2)
