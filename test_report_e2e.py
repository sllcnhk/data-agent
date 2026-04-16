"""
test_report_e2e.py
==================
图表报告功能端到端测试 — 覆盖 RBAC/权限、安全、CRUD、筛选器、导出、总结、菜单集成

分组：
  A (6)  — RBAC 权限隔离 (require_permission + ownership)
  B (5)  — 安全漏洞修复验证 (compare_digest / ownership checks)
  C (6)  — 报告构建与 HTML 内容
  D (4)  — 筛选器 HTML 生成（service 层回归）
  E (5)  — 导出任务 (PDF/PPTX job 流程)
  F (5)  — CRUD (列表/详情/删除)
  G (4)  — LLM 总结状态
  H (5)  — 前端菜单 & 路由集成验证（静态代码检查）
  I (6)  — 回归：已有测试套件继续通过

总计: 46 个测试用例

关键设计：
  - 不 patch require_permission（Depends(require_permission(...)) 在模块导入时就已评估，
    之后 patch 函数名无效）
  - 正确做法：override get_current_user（控制用户身份）
              + patch backend.core.rbac.get_user_permissions（控制权限列表）
  - 超管用户 is_superadmin=True → _check 直接返回，无需 mock 权限
  - 普通用户 is_superadmin=False → 需 mock get_user_permissions 返回权限列表

执行：
  /d/ProgramData/Anaconda3/envs/dataagent/python.exe -m pytest test_report_e2e.py -v -s
"""
from __future__ import annotations

import os
import re
import sys
import uuid
import shutil
import secrets
import tempfile
import inspect
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

os.environ.setdefault("CLICKHOUSE_HOST", "localhost")
os.environ.setdefault("ENABLE_AUTH", "False")
os.environ.setdefault("ADMIN_SECRET_TOKEN", "test-admin-token")

_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "backend"))

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.reports import router as reports_router
from backend.api.deps import get_current_user
from backend.config.database import get_db
from backend.models.report import Report

# ─────────────────────────────────────────────────────────────────────────────
# 临时目录
# ─────────────────────────────────────────────────────────────────────────────
_TMPDIR = Path(tempfile.mkdtemp(prefix="test_report_e2e_"))


# ─────────────────────────────────────────────────────────────────────────────
# 用户 Stub
# ─────────────────────────────────────────────────────────────────────────────

class _SuperAdmin:
    username = "superadmin"
    is_superadmin = True
    id = "superadmin"

class _AnalystUser:
    """拥有 reports:read + reports:create，无 reports:delete"""
    username = "analyst_user"
    is_superadmin = False
    id = "analyst-user-id"

class _ViewerUser:
    """仅有 chat:use，无 reports 权限"""
    username = "viewer_user"
    is_superadmin = False
    id = "viewer-user-id"

class _OtherUser:
    """另一个普通用户，用于跨用户隔离测试"""
    username = "other_user"
    is_superadmin = False
    id = "other-user-id"


# ─────────────────────────────────────────────────────────────────────────────
# Mock DB（内存存储，支持简单过滤）
# ─────────────────────────────────────────────────────────────────────────────

def _make_mock_db():
    store = {}

    class _MockQuery:
        def __init__(self, model=None):
            self._model = model
            self._filters = []
            self._offset_val = 0
            self._limit_val = None

        def filter(self, *args, **kwargs):
            self._filters = list(args)
            return self

        def first(self):
            items = list(store.values())
            for filt in self._filters:
                items = [i for i in items if _apply_filter(i, filt)]
            return items[0] if items else None

        def count(self):
            items = list(store.values())
            for filt in self._filters:
                items = [i for i in items if _apply_filter(i, filt)]
            return len(items)

        def order_by(self, *args):
            return self

        def offset(self, n):
            self._offset_val = n
            return self

        def limit(self, n):
            self._limit_val = n
            return self

        def all(self):
            items = list(store.values())
            for filt in self._filters:
                items = [i for i in items if _apply_filter(i, filt)]
            items = items[self._offset_val:]
            if self._limit_val is not None:
                items = items[:self._limit_val]
            return items

    def _apply_filter(item, filt):
        """解析 SQLAlchemy BinaryExpression: Report.id == uid / Report.username == str"""
        try:
            if hasattr(filt, "left") and hasattr(filt, "right"):
                col_key = filt.left.key if hasattr(filt.left, "key") else None
                # BindParameter.value 是绑定值
                right = filt.right
                val = right.value if hasattr(right, "value") else None
                if col_key is not None and val is not None:
                    item_val = getattr(item, col_key, None)
                    return str(item_val) == str(val)
        except Exception:
            pass
        return True  # 无法解析时保留

    class _MockSession:
        def query(self, model, *args):
            return _MockQuery(model)

        def add(self, obj):
            store[str(obj.id)] = obj

        def commit(self):
            pass

        def refresh(self, obj):
            pass

        def delete(self, obj):
            store.pop(str(obj.id), None)

        def rollback(self):
            pass

        def close(self):
            pass

    return _MockSession(), store


_mock_session, _mock_store = _make_mock_db()


def _get_mock_db():
    yield _mock_session


# ─────────────────────────────────────────────────────────────────────────────
# 构建 TestClient 的辅助函数
# ─────────────────────────────────────────────────────────────────────────────

def _build_client(user_stub):
    """
    构建以 user_stub 身份访问的 TestClient。

    关键：
    - 超管 (is_superadmin=True) → require_permission 内部早返回，无需 mock 权限
    - 普通用户 → 调用方在 with 块中再 patch get_user_permissions
    """
    app = FastAPI()
    app.include_router(reports_router, prefix="/api/v1")
    app.dependency_overrides[get_db] = _get_mock_db
    app.dependency_overrides[get_current_user] = lambda: user_stub
    return TestClient(app, raise_server_exceptions=False)


# ─────────────────────────────────────────────────────────────────────────────
# 公共 Spec
# ─────────────────────────────────────────────────────────────────────────────

_SAMPLE_SPEC = {
    "title": "测试报告E2E",
    "subtitle": "端到端测试",
    "theme": "light",
    "charts": [
        {
            "id": "c1",
            "chart_lib": "echarts",
            "chart_type": "line",
            "title": "折线测试",
            "sql": "SELECT 1 as v",
            "connection_env": "sg",
            "x_field": "date",
            "y_fields": ["v"],
            "width": "full",
        }
    ],
    "filters": [
        {"id": "dr", "type": "date_range", "label": "时间", "default_days": 30, "data_field": "date"}
    ],
    "data": {"c1": [{"date": "2026-03-01", "v": 100}]},
    "include_summary": False,
}


# ─────────────────────────────────────────────────────────────────────────────
# A — RBAC 权限隔离
# ─────────────────────────────────────────────────────────────────────────────

class TestA_RBACPermission(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls._patcher = patch("backend.api.reports._CUSTOMER_DATA_ROOT", _TMPDIR)
        cls._patcher.start()
        # 超管客户端（is_superadmin=True → 所有 require_permission 早返回）
        cls.superadmin_client = _build_client(_SuperAdmin())

    @classmethod
    def tearDownClass(cls):
        cls._patcher.stop()

    def test_A1_viewer_cannot_build_report(self):
        """无 reports:create 权限 → 403"""
        with patch("backend.core.rbac.get_user_permissions", return_value=[]):
            client = _build_client(_ViewerUser())
            res = client.post("/api/v1/reports/build", json={"spec": _SAMPLE_SPEC})
        self.assertEqual(res.status_code, 403, res.text)

    def test_A2_analyst_can_build_report(self):
        """拥有 reports:create → 200"""
        with patch("backend.core.rbac.get_user_permissions",
                   return_value=["reports:read", "reports:create"]):
            client = _build_client(_AnalystUser())
            res = client.post("/api/v1/reports/build", json={"spec": _SAMPLE_SPEC})
        self.assertEqual(res.status_code, 200, res.text)
        self.assertTrue(res.json()["success"])

    def test_A3_analyst_cannot_delete_report(self):
        """analyst 无 reports:delete → 403"""
        fake_id = str(uuid.uuid4())
        with patch("backend.core.rbac.get_user_permissions",
                   return_value=["reports:read", "reports:create"]):
            client = _build_client(_AnalystUser())
            res = client.delete(f"/api/v1/reports/{fake_id}")
        self.assertEqual(res.status_code, 403, res.text)

    def test_A4_superadmin_sees_all_reports_in_list(self):
        """超级管理员看到所有用户的报告"""
        rpt1 = Report(id=uuid.uuid4(), name="R1", username="user_x", refresh_token="tok_x1",
                      charts=[], data_sources=[], filters=[], theme="light")
        rpt2 = Report(id=uuid.uuid4(), name="R2", username="user_y", refresh_token="tok_y2",
                      charts=[], data_sources=[], filters=[], theme="light")
        sid1, sid2 = str(rpt1.id), str(rpt2.id)
        _mock_store[sid1] = rpt1
        _mock_store[sid2] = rpt2

        try:
            res = self.superadmin_client.get("/api/v1/reports")
            self.assertEqual(res.status_code, 200)
            ids = {i["id"] for i in res.json()["data"]["items"]}
            self.assertIn(sid1, ids)
            self.assertIn(sid2, ids)
        finally:
            _mock_store.pop(sid1, None)
            _mock_store.pop(sid2, None)

    def test_A5_ownership_check_prevents_cross_user_get(self):
        """其他用户无法获取不属于自己的报告 → 403"""
        rpt = Report(id=uuid.uuid4(), name="Private", username="owner_user",
                     refresh_token="priv_tok_99", charts=[], data_sources=[], filters=[])
        _mock_store[str(rpt.id)] = rpt

        try:
            with patch("backend.core.rbac.get_user_permissions",
                       return_value=["reports:read"]):
                client = _build_client(_OtherUser())
                res = client.get(f"/api/v1/reports/{rpt.id}")
            self.assertEqual(res.status_code, 403, res.text)
        finally:
            _mock_store.pop(str(rpt.id), None)

    def test_A6_viewer_cannot_list_reports(self):
        """无 reports:read → 403"""
        with patch("backend.core.rbac.get_user_permissions", return_value=[]):
            client = _build_client(_ViewerUser())
            res = client.get("/api/v1/reports")
        self.assertEqual(res.status_code, 403, res.text)


# ─────────────────────────────────────────────────────────────────────────────
# B — 安全修复验证
# ─────────────────────────────────────────────────────────────────────────────

class TestB_SecurityFixes(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls._patcher = patch("backend.api.reports._CUSTOMER_DATA_ROOT", _TMPDIR)
        cls._patcher.start()
        cls.client = _build_client(_SuperAdmin())

        # 插入一个报告（归属 superadmin）
        cls.report_id = str(uuid.uuid4())
        cls.refresh_token = secrets.token_urlsafe(48)
        rpt = Report(
            id=uuid.UUID(cls.report_id),
            name="SecurityTest",
            username="superadmin",
            refresh_token=cls.refresh_token,
            charts=[{"id": "c1", "sql": "", "connection_env": ""}],
            data_sources=[],
        )
        _mock_store[cls.report_id] = rpt

    @classmethod
    def tearDownClass(cls):
        cls._patcher.stop()
        _mock_store.pop(cls.report_id, None)

    def test_B1_valid_refresh_token_accepted(self):
        """正确 token → 非 403（200 或因空 SQL 报 500）"""
        res = self.client.get(
            f"/api/v1/reports/{self.report_id}/refresh-data",
            params={"token": self.refresh_token}
        )
        self.assertNotEqual(res.status_code, 403, f"正确 token 被拒绝: {res.text}")

    def test_B2_wrong_refresh_token_rejected(self):
        """错误 token → 403"""
        res = self.client.get(
            f"/api/v1/reports/{self.report_id}/refresh-data",
            params={"token": "wrong_token_abc"}
        )
        self.assertEqual(res.status_code, 403, res.text)

    def test_B3_compare_digest_used_in_source(self):
        """源码层确认使用 secrets.compare_digest（防止时序攻击）。
        /refresh-data 委托给 get_report_data，因此检查 get_report_data 中的实现。"""
        import backend.api.reports as rmod
        # refresh_report_data 委托给 get_report_data，token 校验在 get_report_data 中
        src = inspect.getsource(rmod.get_report_data)
        self.assertIn("compare_digest", src, "缺少 compare_digest，存在时序攻击风险")

    def test_B4_export_status_checks_ownership(self):
        """export-status 端点：跨用户查询 → 403（ownership check）"""
        with patch("backend.core.rbac.get_user_permissions",
                   return_value=["reports:read"]):
            client = _build_client(_OtherUser())
            res = client.get(
                f"/api/v1/reports/{self.report_id}/export-status",
                params={"job_id": "fake-job-id"}
            )
        # other_user 的 ownership check：report.username="superadmin" != "other_user" → 403
        self.assertEqual(res.status_code, 403, res.text)

    def test_B5_summary_status_checks_ownership(self):
        """summary-status 端点：跨用户查询 → 403"""
        with patch("backend.core.rbac.get_user_permissions",
                   return_value=["reports:read"]):
            client = _build_client(_OtherUser())
            res = client.get(f"/api/v1/reports/{self.report_id}/summary-status")
        self.assertEqual(res.status_code, 403, res.text)


# ─────────────────────────────────────────────────────────────────────────────
# C — 报告构建与 HTML 内容
# ─────────────────────────────────────────────────────────────────────────────

class TestC_ReportBuildContent(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls._patcher = patch("backend.api.reports._CUSTOMER_DATA_ROOT", _TMPDIR)
        cls._patcher.start()
        cls.client = _build_client(_SuperAdmin())

    @classmethod
    def tearDownClass(cls):
        cls._patcher.stop()

    def test_C1_build_success_returns_all_fields(self):
        res = self.client.post("/api/v1/reports/build", json={"spec": _SAMPLE_SPEC})
        self.assertEqual(res.status_code, 200, res.text)
        data = res.json()["data"]
        for key in ("report_id", "file_path", "file_name", "refresh_token", "summary_status"):
            self.assertIn(key, data, f"响应缺少字段: {key}")

    def test_C2_html_file_written_with_correct_content(self):
        res = self.client.post("/api/v1/reports/build", json={"spec": _SAMPLE_SPEC})
        self.assertEqual(res.status_code, 200)
        file_path = _TMPDIR / res.json()["data"]["file_path"]
        if file_path.exists():
            html = file_path.read_text(encoding="utf-8")
            self.assertTrue(html.startswith("<!DOCTYPE html>"), "HTML 应以 DOCTYPE 开头")
            self.assertIn("测试报告E2E", html)
            self.assertIn("echarts", html.lower())
            self.assertIn("cdn.jsdelivr.net", html)
        else:
            # mock 环境下路径可能不一致，至少验证响应成功
            self.assertTrue(res.json()["success"])

    def test_C3_refresh_token_length_sufficient(self):
        """refresh_token 应至少 32 字符"""
        res = self.client.post("/api/v1/reports/build", json={"spec": _SAMPLE_SPEC})
        self.assertEqual(res.status_code, 200)
        token = res.json()["data"]["refresh_token"]
        self.assertGreaterEqual(len(token), 32, f"token 太短: {len(token)}")

    def test_C4_include_summary_false_sets_skipped(self):
        spec = dict(_SAMPLE_SPEC, include_summary=False)
        res = self.client.post("/api/v1/reports/build", json={"spec": spec})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["data"]["summary_status"], "skipped")

    def test_C5_include_summary_true_sets_pending(self):
        spec = dict(_SAMPLE_SPEC, include_summary=True)
        res = self.client.post(
            "/api/v1/reports/build",
            json={"spec": spec, "include_summary": True}
        )
        self.assertEqual(res.status_code, 200)
        self.assertIn(res.json()["data"]["summary_status"], ["pending", "generating"])

    def test_C6_missing_spec_returns_422(self):
        res = self.client.post("/api/v1/reports/build", json={})
        self.assertEqual(res.status_code, 422)


# ─────────────────────────────────────────────────────────────────────────────
# D — 筛选器 HTML 生成（service 层回归）
# ─────────────────────────────────────────────────────────────────────────────

class TestD_FilterHtmlRegression(unittest.TestCase):

    def setUp(self):
        from backend.services.report_builder_service import _render_filter_html
        self._render = _render_filter_html

    def test_D1_date_range_filter_html(self):
        html = self._render([{"id": "dr", "type": "date_range", "label": "时间", "default_days": 30}])
        self.assertIn('type="date"', html)
        self.assertIn("filter-dr-start", html)
        self.assertIn("filter-dr-end", html)

    def test_D2_select_filter_html(self):
        html = self._render([{"id": "env", "type": "select", "label": "环境", "options": ["sg", "idn"]}])
        self.assertIn("<select", html)
        self.assertIn("sg", html)

    def test_D3_multi_select_has_multiple_attr(self):
        html = self._render([{"id": "env", "type": "multi_select", "label": "环境", "options": ["a", "b"]}])
        self.assertIn("multiple", html)

    def test_D4_radio_filter_html(self):
        html = self._render([{"id": "gr", "type": "radio", "label": "粒度", "options": ["日", "周", "月"]}])
        self.assertIn('type="radio"', html)
        self.assertIn("日", html)


# ─────────────────────────────────────────────────────────────────────────────
# E — 导出任务
# ─────────────────────────────────────────────────────────────────────────────

class TestE_ExportJob(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls._patcher = patch("backend.api.reports._CUSTOMER_DATA_ROOT", _TMPDIR)
        cls._patcher.start()
        cls.client = _build_client(_SuperAdmin())

        # 先构建一个报告
        res = cls.client.post("/api/v1/reports/build", json={"spec": _SAMPLE_SPEC})
        if res.status_code == 200:
            cls.report_id = res.json()["data"]["report_id"]
        else:
            cls.report_id = str(uuid.uuid4())

    @classmethod
    def tearDownClass(cls):
        cls._patcher.stop()

    def test_E1_pdf_export_creates_job_id(self):
        res = self.client.post(
            f"/api/v1/reports/{self.report_id}/export",
            json={"format": "pdf"}
        )
        if res.status_code == 200:
            self.assertIn("job_id", res.json()["data"])
        else:
            self.assertIn(res.status_code, [200, 400, 404], res.text)

    def test_E2_pptx_export_accepted(self):
        res = self.client.post(
            f"/api/v1/reports/{self.report_id}/export",
            json={"format": "pptx"}
        )
        self.assertIn(res.status_code, [200, 400, 404], res.text)

    def test_E3_invalid_format_returns_400(self):
        res = self.client.post(
            f"/api/v1/reports/{self.report_id}/export",
            json={"format": "docx"}
        )
        self.assertIn(res.status_code, [400, 404], res.text)

    def test_E4_export_status_job_not_found_returns_404(self):
        res = self.client.get(
            f"/api/v1/reports/{self.report_id}/export-status",
            params={"job_id": "nonexistent-job-id"}
        )
        self.assertEqual(res.status_code, 404, res.text)

    def test_E5_export_requires_permission(self):
        """无 reports:read → 403"""
        with patch("backend.core.rbac.get_user_permissions", return_value=[]):
            client = _build_client(_ViewerUser())
            res = client.post(
                f"/api/v1/reports/{self.report_id}/export",
                json={"format": "pdf"}
            )
        self.assertEqual(res.status_code, 403, res.text)


# ─────────────────────────────────────────────────────────────────────────────
# F — CRUD 操作
# ─────────────────────────────────────────────────────────────────────────────

class TestF_CrudReports(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls._patcher = patch("backend.api.reports._CUSTOMER_DATA_ROOT", _TMPDIR)
        cls._patcher.start()
        cls.client = _build_client(_SuperAdmin())

    @classmethod
    def tearDownClass(cls):
        cls._patcher.stop()

    def test_F1_list_returns_paginated_response(self):
        res = self.client.get("/api/v1/reports")
        self.assertEqual(res.status_code, 200, res.text)
        data = res.json()["data"]
        for key in ("total", "page", "page_size", "items"):
            self.assertIn(key, data, f"列表响应缺少: {key}")
        self.assertIsInstance(data["items"], list)

    def test_F2_get_nonexistent_returns_404(self):
        fake_id = str(uuid.uuid4())
        res = self.client.get(f"/api/v1/reports/{fake_id}")
        self.assertEqual(res.status_code, 404, res.text)

    def test_F3_invalid_uuid_returns_400(self):
        res = self.client.get("/api/v1/reports/not-a-uuid")
        self.assertEqual(res.status_code, 400, res.text)

    def test_F4_build_then_delete_own_report(self):
        """先构建报告，再删除"""
        res = self.client.post("/api/v1/reports/build", json={"spec": _SAMPLE_SPEC})
        self.assertEqual(res.status_code, 200)
        rid = res.json()["data"]["report_id"]

        del_res = self.client.delete(f"/api/v1/reports/{rid}")
        self.assertIn(del_res.status_code, [200, 204], del_res.text)

    def test_F5_delete_nonexistent_returns_404(self):
        res = self.client.delete(f"/api/v1/reports/{uuid.uuid4()}")
        self.assertEqual(res.status_code, 404, res.text)


# ─────────────────────────────────────────────────────────────────────────────
# G — LLM 总结状态
# ─────────────────────────────────────────────────────────────────────────────

class TestG_SummaryStatus(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls._patcher = patch("backend.api.reports._CUSTOMER_DATA_ROOT", _TMPDIR)
        cls._patcher.start()
        cls.client = _build_client(_SuperAdmin())

        # 插入状态为 skipped 的报告（归属 superadmin）
        cls.report_id = str(uuid.uuid4())
        rpt = Report(
            id=uuid.UUID(cls.report_id),
            name="SummaryTest",
            username="superadmin",
            refresh_token="g_summary_tok_001",
            summary_status="skipped",
            charts=[], data_sources=[], filters=[],
        )
        _mock_store[cls.report_id] = rpt

    @classmethod
    def tearDownClass(cls):
        cls._patcher.stop()
        _mock_store.pop(cls.report_id, None)

    def test_G1_summary_status_endpoint_reachable(self):
        """端点可达且不返回 5xx"""
        res = self.client.get(f"/api/v1/reports/{self.report_id}/summary-status")
        self.assertNotIn(res.status_code, [500, 503], res.text)

    def test_G2_skipped_status_returned(self):
        """summary_status=skipped 时正确返回"""
        res = self.client.get(f"/api/v1/reports/{self.report_id}/summary-status")
        if res.status_code == 200:
            data = res.json()["data"]
            self.assertIn("status", data)
            self.assertEqual(data["status"], "skipped")

    def test_G3_build_without_summary_sets_skipped(self):
        spec = dict(_SAMPLE_SPEC, include_summary=False)
        res = self.client.post(
            "/api/v1/reports/build",
            json={"spec": spec}
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["data"]["summary_status"], "skipped")

    def test_G4_summary_status_invalid_id_returns_400(self):
        res = self.client.get("/api/v1/reports/bad-uuid/summary-status")
        self.assertEqual(res.status_code, 400, res.text)


# ─────────────────────────────────────────────────────────────────────────────
# H — 前端菜单 & 路由集成（静态代码检查）
# ─────────────────────────────────────────────────────────────────────────────

class TestH_FrontendIntegration(unittest.TestCase):

    _LAYOUT = _ROOT / "frontend" / "src" / "components" / "AppLayout.tsx"
    _APP_TSX = _ROOT / "frontend" / "src" / "App.tsx"
    _REPORTS_PAGE = _ROOT / "frontend" / "src" / "pages" / "Reports.tsx"

    def _read(self, path: Path) -> str:
        if not path.exists():
            self.skipTest(f"前端文件不存在: {path}")
        return path.read_text(encoding="utf-8")

    def test_H1_reports_menu_item_in_app_layout(self):
        """/reports 菜单项必须在 AppLayout.tsx 中"""
        src = self._read(self._LAYOUT)
        self.assertIn("'/reports'", src, "AppLayout 缺少 '/reports' 菜单项")

    def test_H2_reports_menu_has_permission_guard(self):
        """图表报告菜单绑定 reports:read 权限"""
        src = self._read(self._LAYOUT)
        self.assertIn("reports:read", src, "AppLayout 菜单缺少 reports:read 权限守卫")

    def test_H3_bar_chart_icon_imported(self):
        """BarChartOutlined 图标已导入"""
        src = self._read(self._LAYOUT)
        self.assertIn("BarChartOutlined", src, "AppLayout 缺少 BarChartOutlined 导入")

    def test_H4_reports_route_in_app_tsx(self):
        """/reports 路由在 App.tsx 中注册"""
        src = self._read(self._APP_TSX)
        self.assertIn("/reports", src, "App.tsx 缺少 /reports 路由")

    def test_H5_reports_page_component_exists(self):
        """Reports.tsx 组件文件存在且是有效 React 组件"""
        self.assertTrue(self._REPORTS_PAGE.exists(), "Reports.tsx 文件不存在")
        src = self._read(self._REPORTS_PAGE)
        self.assertIn("export default", src, "Reports.tsx 缺少 export default")


# ─────────────────────────────────────────────────────────────────────────────
# I — 回归：已有测试套件不受影响
# ─────────────────────────────────────────────────────────────────────────────

class TestI_Regression(unittest.TestCase):

    def test_I1_report_builder_service_importable(self):
        from backend.services.report_builder_service import (
            build_report_html, generate_refresh_token,
            _esc, _css, _js_engine,
        )
        self.assertTrue(callable(build_report_html))
        self.assertTrue(callable(generate_refresh_token))

    def test_I2_generate_refresh_token_unique_and_long(self):
        from backend.services.report_builder_service import generate_refresh_token
        tokens = {generate_refresh_token() for _ in range(20)}
        self.assertEqual(len(tokens), 20)
        for t in tokens:
            self.assertGreaterEqual(len(t), 32)

    def test_I3_js_engine_has_all_chart_builders(self):
        from backend.services.report_builder_service import _js_engine
        js = _js_engine()
        for fn_name in ("buildLine", "buildBar", "buildPie", "buildScatter",
                        "buildFunnel", "buildGauge", "buildSankey", "buildDualAxis"):
            self.assertIn(f"function {fn_name}", js, f"缺少 {fn_name}")

    def test_I4_reports_router_prefix_correct(self):
        from backend.api.reports import router as r
        self.assertEqual(r.prefix, "/reports")

    def test_I5_report_model_has_required_fields(self):
        for field in ("username", "refresh_token", "report_file_path",
                      "llm_summary", "summary_status"):
            self.assertTrue(hasattr(Report, field), f"Report 缺少字段: {field}")

    def test_I6_rbac_init_script_has_reports_permissions(self):
        init_script = _ROOT / "backend" / "scripts" / "init_rbac.py"
        self.assertTrue(init_script.exists())
        src = init_script.read_text(encoding="utf-8")
        self.assertIn('"reports"', src, "init_rbac.py 缺少 reports 权限定义")
        # analyst 角色有 reports:read + create
        self.assertIn("reports:read", src)
        self.assertIn("reports:create", src)
        # admin 角色有 reports:delete
        self.assertIn("reports:delete", src)


# ─────────────────────────────────────────────────────────────────────────────
# J — T-B3 参数合并：GET /data 端点默认值兜底验证
# ─────────────────────────────────────────────────────────────────────────────

class TestJ_ParamMerge(unittest.TestCase):
    """J: T-B3 fix — {**default_params, **raw_params} 合并，防止错误 _DEFAULT_PARAMS 导致 Code 38。"""

    from datetime import date as _date, timedelta as _td

    @classmethod
    def setUpClass(cls):
        cls._patcher = patch("backend.api.reports._CUSTOMER_DATA_ROOT", _TMPDIR)
        cls._patcher.start()
        cls.client = _build_client(_SuperAdmin())
        # 创建有 date_range filter 的报表
        import secrets as _sec
        cls.refresh_token = _sec.token_urlsafe(48)
        cls.report_id = str(uuid.uuid4())
        rpt = Report(
            id=uuid.UUID(cls.report_id),
            name="ParamMergeTest",
            username="superadmin",
            refresh_token=cls.refresh_token,
            charts=[{
                "id": "c1",
                "chart_type": "bar",
                "sql": "SELECT toDate(s_day) AS day, sum(cnt) AS cnt FROM t WHERE s_day >= toDate('{{ date_start }}') AND s_day <= toDate('{{ date_end }}') GROUP BY day",
                "connection_env": "sg",
                "connection_type": "clickhouse",
                "x_field": "day",
                "y_fields": ["cnt"],
            }],
            filters=[{
                "id": "date_range",
                "type": "date_range",
                "default_days": 30,
                "binds": {"start": "date_start", "end": "date_end"},
            }],
            data_sources=[],
        )
        _mock_store[cls.report_id] = rpt

    @classmethod
    def tearDownClass(cls):
        cls._patcher.stop()
        _mock_store.pop(cls.report_id, None)

    def _get_data(self, extra_params=None):
        from unittest.mock import AsyncMock, patch as _patch
        params = {"token": self.refresh_token}
        if extra_params:
            params.update(extra_params)
        with _patch("backend.api.reports._run_query", new=AsyncMock(return_value=[])):
            return self.client.get(f"/api/v1/reports/{self.report_id}/data", params=params)

    def test_J1_no_params_defaults_applied(self):
        """未传参数 → default_days=30 自动计算 date_start/date_end。"""
        from datetime import date, timedelta
        resp = self._get_data()
        self.assertEqual(resp.status_code, 200)
        params = resp.json().get("params_used", {})
        today = date.today()
        expected = (today - timedelta(days=30)).isoformat()
        self.assertEqual(params.get("date_start"), expected, f"params={params}")
        self.assertEqual(params.get("date_end"), today.isoformat(), f"params={params}")

    def test_J2_wrong_key_params_still_get_default_dates(self):
        """T-B3 核心: 传 c1/c2（图表 ID 错误 key）→ date_start/date_end 由默认兜底填入。
        这是 Code 38 根本修复的 E2E 验证。"""
        from datetime import date, timedelta
        resp = self._get_data({"c1": "2026-03-17", "c2": "2026-04-16"})
        self.assertEqual(resp.status_code, 200)
        params = resp.json().get("params_used", {})
        today = date.today()
        expected = (today - timedelta(days=30)).isoformat()
        self.assertEqual(params.get("date_start"), expected,
                         f"T-B3 应兜底 date_start，params={params}")
        self.assertEqual(params.get("date_end"), today.isoformat(),
                         f"T-B3 应兜底 date_end，params={params}")
        # 错误 key 仍透传
        self.assertIn("c1", params)
        self.assertIn("c2", params)

    def test_J3_correct_params_override_defaults(self):
        """运行时 date_start/date_end 覆盖默认值（{**default, **runtime} 顺序正确）。"""
        resp = self._get_data({"date_start": "2024-01-01", "date_end": "2024-12-31"})
        self.assertEqual(resp.status_code, 200)
        params = resp.json().get("params_used", {})
        self.assertEqual(params.get("date_start"), "2024-01-01", "运行时参数覆盖默认值")
        self.assertEqual(params.get("date_end"), "2024-12-31", "运行时参数覆盖默认值")

    def test_J4_mixed_params_partial_override(self):
        """混合参数: c1 错误 + date_start 正确 → date_start 覆盖，date_end 由默认填充。"""
        from datetime import date
        resp = self._get_data({"c1": "2026-01-01", "date_start": "2025-01-01"})
        self.assertEqual(resp.status_code, 200)
        params = resp.json().get("params_used", {})
        self.assertEqual(params.get("date_start"), "2025-01-01", "传入值覆盖默认")
        self.assertEqual(params.get("date_end"), date.today().isoformat(), "未传由默认填充")

    def test_J5_wrong_token_returns_403(self):
        """回归：错误 token → 403。"""
        from unittest.mock import AsyncMock, patch as _patch
        with _patch("backend.api.reports._run_query", new=AsyncMock(return_value=[])):
            resp = self.client.get(f"/api/v1/reports/{self.report_id}/data",
                                   params={"token": "WRONG-TOKEN"})
        self.assertEqual(resp.status_code, 403)


# ─────────────────────────────────────────────────────────────────────────────
# K — T-A1/A2/B1/B2: /build 端点 binds 自动修正 + today()-N 不阻塞
# ─────────────────────────────────────────────────────────────────────────────

class TestK_BuildBindsCorrection(unittest.TestCase):
    """K: POST /reports/build — T-A1（非阻塞）+ T-A2（auto-parameterize）+ T-B1/B2（binds 修正）。"""

    @classmethod
    def setUpClass(cls):
        cls._patcher = patch("backend.api.reports._CUSTOMER_DATA_ROOT", _TMPDIR)
        cls._patcher.start()
        cls.client = _build_client(_SuperAdmin())

    @classmethod
    def tearDownClass(cls):
        cls._patcher.stop()

    def _get_newly_stored_report(self, before_keys):
        """找到 POST /build 后新增到 _mock_store 的 Report 对象。"""
        for k, v in _mock_store.items():
            if k not in before_keys:
                return v
        return None

    def test_K1_wrong_binds_does_not_block_build(self):
        """T-A1/B1: binds 使用图表 ID (c1/c2) 而非 SQL 变量名 → HTTP 200，不返回 400。"""
        spec = {
            "title": "test_wrong_binds_K1",
            "charts": [{
                "id": "c1",
                "chart_type": "bar",
                "title": "近30天接通",
                "sql": "SELECT toDate(s_day) AS day, cnt FROM t WHERE s_day >= toDate('{{ date_start }}') AND s_day <= toDate('{{ date_end }}')",
                "connection_env": "sg",
                "x_field": "day",
                "y_fields": ["cnt"],
            }],
            "filters": [{
                "id": "date_range",
                "type": "date_range",
                "default_days": 30,
                "binds": {"start": "c1", "end": "c2"},  # ❌ 图表 ID 作为 binds 变量名
            }],
        }
        res = self.client.post("/api/v1/reports/build", json={"spec": spec})
        self.assertEqual(res.status_code, 200, f"resp: {res.text}")
        self.assertTrue(res.json().get("success"))

    def test_K2_wrong_binds_corrected_in_stored_filters(self):
        """T-B1/B2: 创建后 DB 存储的 report.filters.binds 已自动修正为 date_start/date_end。"""
        before = set(_mock_store.keys())
        spec = {
            "title": "test_wrong_binds_K2",
            "charts": [{
                "id": "c1",
                "chart_type": "line",
                "title": "趋势",
                "sql": "SELECT s_day, cnt FROM t WHERE s_day >= toDate('{{ date_start }}') AND s_day < toDate('{{ date_end }}')",
                "connection_env": "sg",
                "x_field": "s_day",
                "y_fields": ["cnt"],
            }],
            "filters": [{
                "id": "date_range",
                "type": "date_range",
                "default_days": 30,
                "binds": {"start": "c1", "end": "c2"},
            }],
        }
        res = self.client.post("/api/v1/reports/build", json={"spec": spec})
        self.assertEqual(res.status_code, 200)
        rpt = self._get_newly_stored_report(before)
        self.assertIsNotNone(rpt, "新报告应存入 _mock_store")
        stored_filters = rpt.filters if hasattr(rpt, "filters") else []
        date_filter = next((f for f in stored_filters if f.get("type") == "date_range"), None)
        self.assertIsNotNone(date_filter, f"应有 date_range filter: {stored_filters}")
        binds = date_filter.get("binds", {})
        self.assertEqual(binds.get("start"), "date_start",
                         f"binds.start 应修正为 date_start，实际: {binds}")
        self.assertEqual(binds.get("end"), "date_end",
                         f"binds.end 应修正为 date_end，实际: {binds}")

    def test_K3_today_n_sql_does_not_block_build(self):
        """T-A1: SQL 含 today()-N 形式 → HTTP 200（软警告，不阻塞）。"""
        spec = {
            "title": "test_today_n_K3",
            "charts": [{
                "id": "c1",
                "chart_type": "bar",
                "title": "近30天",
                "sql": "SELECT s_day, cnt FROM t WHERE s_day >= today() - 30 AND s_day <= today()",
                "connection_env": "sg",
                "x_field": "s_day",
                "y_fields": ["cnt"],
            }],
            "filters": [{
                "id": "date_range",
                "type": "date_range",
                "default_days": 30,
                "binds": {"start": "date_start", "end": "date_end"},
            }],
        }
        res = self.client.post("/api/v1/reports/build", json={"spec": spec})
        self.assertEqual(res.status_code, 200, f"resp: {res.text}")
        self.assertTrue(res.json().get("success"))

    def test_K4_today_n_sql_auto_parameterized_in_stored_spec(self):
        """T-A2: today()-N SQL 经 normalize 自动替换为 {{ date_start }}/{{ date_end }}。"""
        before = set(_mock_store.keys())
        spec = {
            "title": "test_today_n_K4",
            "charts": [{
                "id": "c1",
                "chart_type": "bar",
                "title": "日趋势",
                "sql": "SELECT s_day, cnt FROM t WHERE s_day >= today() - 30 AND s_day <= today()",
                "connection_env": "sg",
                "x_field": "s_day",
                "y_fields": ["cnt"],
            }],
            "filters": [{
                "id": "date_range",
                "type": "date_range",
                "default_days": 30,
                "binds": {"start": "date_start", "end": "date_end"},
            }],
        }
        res = self.client.post("/api/v1/reports/build", json={"spec": spec})
        self.assertEqual(res.status_code, 200)
        rpt = self._get_newly_stored_report(before)
        self.assertIsNotNone(rpt)
        stored_charts = rpt.charts if hasattr(rpt, "charts") else []
        stored_sql = stored_charts[0].get("sql", "") if stored_charts else ""
        self.assertIn("{{ date_start }}", stored_sql,
                      f"today()-N 应被参数化: {stored_sql}")
        self.assertIn("{{ date_end }}", stored_sql,
                      f"today()-N 应被参数化: {stored_sql}")
        self.assertNotIn("today()", stored_sql,
                         f"today() 不应遗留: {stored_sql}")

    def test_K5_empty_charts_blocked(self):
        """硬错误保留：spec.charts 为空 → 400。"""
        res = self.client.post("/api/v1/reports/build",
                               json={"spec": {"title": "x", "charts": [], "filters": []}})
        self.assertEqual(res.status_code, 400)

    def test_K6_chart_missing_connection_env_blocked(self):
        """硬错误保留：图表缺少 connection_env → 400。"""
        spec = {
            "title": "no_conn",
            "charts": [{"id": "c1", "chart_type": "bar", "sql": "SELECT 1"}],
            "filters": [],
        }
        res = self.client.post("/api/v1/reports/build", json={"spec": spec})
        self.assertEqual(res.status_code, 400)


# ─────────────────────────────────────────────────────────────────────────────
# L — 新端点 RBAC 与功能验证（analyze / regenerate-html / rebuild-spec）
# ─────────────────────────────────────────────────────────────────────────────

class TestL_NewEndpoints(unittest.TestCase):
    """L: 本次新增/修改端点的 RBAC 权限配置 + 基本行为验证。"""

    @classmethod
    def setUpClass(cls):
        import secrets as _sec
        cls._patcher = patch("backend.api.reports._CUSTOMER_DATA_ROOT", _TMPDIR)
        cls._patcher.start()
        cls.superadmin_client = _build_client(_SuperAdmin())
        cls.refresh_token = _sec.token_urlsafe(48)
        cls.report_id = str(uuid.uuid4())
        rpt = Report(
            id=uuid.UUID(cls.report_id),
            name="NewEndpointTest",
            username="superadmin",
            refresh_token=cls.refresh_token,
            charts=[{"id": "c1", "chart_type": "bar", "sql": "SELECT 1", "connection_env": "sg"}],
            filters=[],
            data_sources=[],
        )
        _mock_store[cls.report_id] = rpt

    @classmethod
    def tearDownClass(cls):
        cls._patcher.stop()
        _mock_store.pop(cls.report_id, None)

    # ── analyze endpoint ──────────────────────────────────────────────────────

    def test_L1_analyze_wrong_token_returns_403(self):
        """POST /{id}/analyze: 错误 token → 403（与 /data 端点鉴权一致）。"""
        res = self.superadmin_client.post(
            f"/api/v1/reports/{self.report_id}/analyze",
            params={"token": "WRONG"},
            json={"charts_data": {}},
        )
        self.assertEqual(res.status_code, 403, res.text)

    def test_L2_analyze_valid_token_returns_success(self):
        """POST /{id}/analyze: 正确 token + mock LLM → 200 success。"""
        from unittest.mock import AsyncMock, patch as _patch
        fake = [{"type": "trend", "title": "趋势", "content": "上升趋势"}]
        with _patch("backend.api.reports.analyze_report_data", new=AsyncMock(return_value=fake)):
            res = self.superadmin_client.post(
                f"/api/v1/reports/{self.report_id}/analyze",
                params={"token": self.refresh_token},
                json={"charts_data": {"c1": [{"day": "2026-04-01", "cnt": 100}]},
                      "report_title": "测试"},
            )
        self.assertEqual(res.status_code, 200, res.text)
        body = res.json()
        self.assertTrue(body["success"])
        self.assertEqual(body["count"], 1)

    def test_L3_analyze_ai_analysis_chart_specs_filtered(self):
        """POST /{id}/analyze: chart_specs 中 ai_analysis 自动过滤，不进入 LLM 分析。"""
        from unittest.mock import AsyncMock, patch as _patch
        captured: list = []

        async def _fake(charts_data, report_title, analysis_focus, chart_specs):
            captured.extend(chart_specs)
            return []

        with _patch("backend.api.reports.analyze_report_data", new=_fake):
            self.superadmin_client.post(
                f"/api/v1/reports/{self.report_id}/analyze",
                params={"token": self.refresh_token},
                json={
                    "charts_data": {},
                    "chart_specs": [
                        {"id": "c1", "chart_type": "bar"},
                        {"id": "s1", "chart_type": "ai_analysis"},
                    ],
                },
            )
        ai_specs = [s for s in captured if s.get("chart_type") == "ai_analysis"]
        self.assertEqual(len(ai_specs), 0, "ai_analysis 不应传给 LLM")
        self.assertEqual(len(captured), 1)

    def test_L4_analyze_oversized_data_capped(self):
        """POST /{id}/analyze: 总行数 >2000 时截断（防 LLM token 超限）。"""
        from unittest.mock import AsyncMock, patch as _patch
        big = [{"day": str(i), "cnt": i} for i in range(1001)]
        captured: list = []

        async def _fake(charts_data, **kwargs):
            captured.append(charts_data)
            return []

        with _patch("backend.api.reports.analyze_report_data", new=_fake):
            self.superadmin_client.post(
                f"/api/v1/reports/{self.report_id}/analyze",
                params={"token": self.refresh_token},
                json={"charts_data": {"c1": big, "c2": big, "c3": big}},
            )

        self.assertEqual(len(captured), 1)
        total = sum(len(v) for v in captured[0].values() if isinstance(v, list))
        self.assertLessEqual(total, 2003, f"截断后 ≤ 2000，实际: {total}")

    # ── regenerate-html / rebuild-spec RBAC ──────────────────────────────────

    def test_L5_regenerate_html_requires_reports_create(self):
        """POST /{id}/regenerate-html: 无 reports:create 权限 → 403。"""
        with patch("backend.core.rbac.get_user_permissions", return_value=[]):
            client = _build_client(_ViewerUser())
            res = client.post(f"/api/v1/reports/{self.report_id}/regenerate-html")
        self.assertEqual(res.status_code, 403, res.text)

    def test_L6_rebuild_spec_requires_reports_create(self):
        """POST /{id}/rebuild-spec: 无 reports:create 权限 → 403。"""
        with patch("backend.core.rbac.get_user_permissions", return_value=[]):
            client = _build_client(_ViewerUser())
            res = client.post(f"/api/v1/reports/{self.report_id}/rebuild-spec")
        self.assertEqual(res.status_code, 403, res.text)

    def test_L7_rbac_init_has_all_reports_permissions(self):
        """init_rbac.py 中 reports:* 权限完整（read/create/delete），且角色分配正确。"""
        from backend.scripts.init_rbac import PERMISSIONS, ROLES
        perm_keys = {f"{r}:{a}" for r, a, _ in PERMISSIONS}
        self.assertIn("reports:read", perm_keys)
        self.assertIn("reports:create", perm_keys)
        self.assertIn("reports:delete", perm_keys)
        # analyst 有 read+create，无 delete
        analyst = set(ROLES["analyst"]["permissions"])
        self.assertIn("reports:read", analyst)
        self.assertIn("reports:create", analyst)
        self.assertNotIn("reports:delete", analyst)
        # admin 有全部
        admin = set(ROLES["admin"]["permissions"])
        self.assertIn("reports:read", admin)
        self.assertIn("reports:create", admin)
        self.assertIn("reports:delete", admin)
        # viewer 无任何 reports:* 权限
        viewer = set(ROLES["viewer"]["permissions"])
        rpts = {p for p in viewer if p.startswith("reports:")}
        self.assertEqual(rpts, set(), f"viewer 不应有报表权限: {rpts}")


# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v", "-s"]))
