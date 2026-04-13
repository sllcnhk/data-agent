"""
test_pin_e2e.py — 固定报表功能 端到端 + RBAC + doc_type 完整测试套件
====================================================================

测试层次：
  D  (7)  — RBAC 权限矩阵（新 /reports/pin 端点角色覆盖）
  E  (5)  — doc_type 检测逻辑（T2 agentic_loop._detect_report_type 单元测试）
  F  (6)  — 端到端 Pin 流程（文件 → pin → 报表列表 → 访问 HTML）

总计：18 个测试用例
"""

from __future__ import annotations

import os
import sys
import uuid
import unittest
from pathlib import Path
from unittest.mock import patch

# ── 路径 & 环境初始化 ────────────────────────────────────────────────────────
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "backend"))
os.environ.setdefault("POSTGRES_PASSWORD", "Sgp013013")
os.environ.setdefault("POSTGRES_HOST", "localhost")
os.environ.setdefault("ENABLE_AUTH", "False")

from test_utils import make_test_username  # noqa: E402

_PREFIX = f"_pe2e_{uuid.uuid4().hex[:6]}_"

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


def _make_user(suffix="", role_name="analyst", is_superadmin=False):
    from backend.models.user import User
    from backend.models.role import Role
    from backend.models.user_role import UserRole
    from backend.core.auth.password import hash_password

    username = f"{_PREFIX}{suffix or uuid.uuid4().hex[:6]}"
    u = User(
        username=username,
        display_name=f"PE2E {suffix}",
        hashed_password=hash_password("Test1234!"),
        auth_source="local",
        is_active=True,
        is_superadmin=is_superadmin,
    )
    _g_db.add(u)
    _g_db.flush()
    if role_name:
        role = _g_db.query(Role).filter(Role.name == role_name).first()
        if role:
            from backend.models.user_role import UserRole
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


def _create_html_file(username: str, with_summary: bool = False, filename: str = None) -> str:
    """创建测试 HTML 文件，返回相对于 customer_data/ 的路径。"""
    from backend.api.reports import _CUSTOMER_DATA_ROOT
    fname = filename or f"{_PREFIX}{'doc' if with_summary else 'dash'}_{uuid.uuid4().hex[:6]}.html"
    report_dir = _CUSTOMER_DATA_ROOT / username / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    html_path = report_dir / fname
    if with_summary:
        content = '<html><body><div class="summary-section"><p>LLM summary</p></div><div class="chart-area"></div></body></html>'
    else:
        content = "<html><body><div class='chart-area'></div></body></html>"
    html_path.write_text(content, encoding="utf-8")
    return str(html_path.relative_to(_CUSTOMER_DATA_ROOT))


def _create_report_in_db(username: str, doc_type: str = "dashboard", name: str = None, file_path: str = None):
    """直接在 DB 中创建 Report 记录，返回 Report 对象。"""
    from backend.models.report import Report
    from backend.services.report_builder_service import generate_refresh_token
    rid = uuid.uuid4()
    r = Report(
        id=rid,
        name=name or f"{_PREFIX}rpt_{rid.hex[:6]}",
        username=username,
        refresh_token=generate_refresh_token(),
        report_file_path=file_path,
        summary_status="skipped",
        charts=[],
        filters=[],
        theme="light",
        extra_metadata={"spec_version": "1.0"},
    )
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
    from backend.api.reports import _CUSTOMER_DATA_ROOT
    import shutil

    try:
        _g_db.query(Report).filter(
            Report.username.like(f"{_PREFIX}%")
        ).delete(synchronize_session=False)
        _g_db.query(Report).filter(
            Report.name.like(f"{_PREFIX}%")
        ).delete(synchronize_session=False)
        test_users = _g_db.query(User).filter(
            User.username.like(f"{_PREFIX}%")
        ).all()
        for u in test_users:
            _g_db.delete(u)
        _g_db.commit()
    except Exception as e:
        print(f"[teardown] cleanup error: {e}")
        _g_db.rollback()

    # 清理文件系统
    for user_dir in _CUSTOMER_DATA_ROOT.iterdir():
        if user_dir.is_dir() and user_dir.name.startswith(_PREFIX):
            try:
                shutil.rmtree(user_dir)
            except Exception:
                pass
    try:
        _g_db.close()
    except Exception:
        pass


def _make_client():
    from backend.main import app
    from fastapi.testclient import TestClient
    return TestClient(app, raise_server_exceptions=False)


# ══════════════════════════════════════════════════════════════════════════════
# Section D — RBAC 权限矩阵（7 tests）
# ══════════════════════════════════════════════════════════════════════════════

class TestDRBACPermissions(unittest.TestCase):
    """
    D: 验证 /reports/pin（需 reports:create）和 /reports（需 reports:read）
       在各角色下的权限行为符合 RBAC 矩阵。
    """

    @classmethod
    def setUpClass(cls):
        # 创建一个 analyst 用户作为报表文件所有者（用于创建测试文件）
        cls.analyst = _make_user("d_analyst", "analyst")
        cls.viewer = _make_user("d_viewer", "viewer")
        cls.admin = _make_user("d_admin", "admin")
        cls.fp = _create_html_file(cls.analyst.username)
        cls.fp_for_viewer_delete = None  # 稍后创建

    def _client_with_auth(self, user):
        """返回开启 auth 的 TestClient + 该用户的 headers。"""
        from backend.config.settings import settings
        from backend.main import app
        from fastapi.testclient import TestClient
        # 需在 enable_auth=True 下测试 RBAC
        with patch.object(settings, "enable_auth", True):
            client = TestClient(app, raise_server_exceptions=False)
        return client, _auth(user)

    def test_D1_viewer_cannot_pin(self):
        """D1: viewer 无 reports:create → POST /reports/pin 返回 403"""
        from backend.config.settings import settings
        fp = _create_html_file(self.viewer.username)
        with patch.object(settings, "enable_auth", True):
            client = _make_client()
            res = client.post(
                "/api/v1/reports/pin",
                json={"file_path": fp, "doc_type": "dashboard"},
                headers=_auth(self.viewer),
            )
        self.assertEqual(res.status_code, 403, f"viewer should be 403 but got {res.status_code}: {res.text}")

    def test_D2_analyst_can_pin(self):
        """D2: analyst 有 reports:create → POST /reports/pin 返回 200"""
        from backend.config.settings import settings
        fp = _create_html_file(self.analyst.username)
        with patch.object(settings, "enable_auth", True):
            client = _make_client()
            res = client.post(
                "/api/v1/reports/pin",
                json={"file_path": fp, "doc_type": "dashboard"},
                headers=_auth(self.analyst),
            )
        self.assertEqual(res.status_code, 200, res.text)
        self.assertTrue(res.json()["data"]["is_new"])

    def test_D3_no_token_returns_401(self):
        """D3: ENABLE_AUTH=True + 无 token → POST /reports/pin 返回 401"""
        from backend.config.settings import settings
        fp = _create_html_file(self.analyst.username)
        with patch.object(settings, "enable_auth", True):
            client = _make_client()
            res = client.post(
                "/api/v1/reports/pin",
                json={"file_path": fp, "doc_type": "dashboard"},
                # 不附带 Authorization header
            )
        self.assertIn(res.status_code, [401, 403], f"no token should be 401/403, got {res.status_code}")

    def test_D4_viewer_cannot_list_reports(self):
        """D4: viewer 无 reports:read → GET /reports 返回 403"""
        from backend.config.settings import settings
        with patch.object(settings, "enable_auth", True):
            client = _make_client()
            res = client.get("/api/v1/reports", headers=_auth(self.viewer))
        self.assertEqual(res.status_code, 403, f"viewer should be 403 but got {res.status_code}: {res.text}")

    def test_D5_analyst_can_list_reports(self):
        """D5: analyst 有 reports:read → GET /reports 返回 200"""
        from backend.config.settings import settings
        with patch.object(settings, "enable_auth", True):
            client = _make_client()
            res = client.get("/api/v1/reports", headers=_auth(self.analyst))
        self.assertEqual(res.status_code, 200, res.text)
        self.assertIn("items", res.json()["data"])

    def test_D6_analyst_cannot_delete_report(self):
        """D6: analyst 无 reports:delete → DELETE /reports/{id} 返回 403"""
        from backend.config.settings import settings
        # 先在 db 中创建 report 属于 analyst
        r = _create_report_in_db(self.analyst.username, name=f"{_PREFIX}d6rpt")
        with patch.object(settings, "enable_auth", True):
            client = _make_client()
            res = client.delete(
                f"/api/v1/reports/{r.id}",
                headers=_auth(self.analyst),
            )
        self.assertEqual(res.status_code, 403, f"analyst should not delete, got {res.status_code}: {res.text}")

    def test_D7_admin_can_delete_report(self):
        """D7: admin 有 reports:delete → DELETE /reports/{id} 返回 200"""
        from backend.config.settings import settings
        # 创建属于 admin 的 report
        r = _create_report_in_db(self.admin.username, name=f"{_PREFIX}d7rpt")
        with patch.object(settings, "enable_auth", True):
            client = _make_client()
            res = client.delete(
                f"/api/v1/reports/{r.id}",
                headers=_auth(self.admin),
            )
        self.assertEqual(res.status_code, 200, f"admin should delete, got {res.status_code}: {res.text}")


# ══════════════════════════════════════════════════════════════════════════════
# Section E — doc_type 检测逻辑单元测试（5 tests）
# ══════════════════════════════════════════════════════════════════════════════

class TestEDocTypeDetection(unittest.TestCase):
    """
    E: 验证 _detect_report_type 函数（T2）对各种文件/HTML 的分类是否正确。
    函数签名：_detect_report_type(file_path, content, mime) -> (is_report, doc_type)
    """

    @classmethod
    def setUpClass(cls):
        from backend.agents.agentic_loop import _detect_report_type
        cls.detect = staticmethod(_detect_report_type)

    def test_E1_non_html_file_is_not_report(self):
        """E1: CSV / Excel 等非 HTML 文件 → is_report=False，doc_type=None"""
        for fname, mime in [
            ("customer_data/alice/reports/data.csv", "text/csv"),
            ("customer_data/alice/reports/table.xlsx",
             "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
            ("customer_data/alice/reports/note.txt", "text/plain"),
        ]:
            with self.subTest(fname=fname):
                is_report, doc_type = self.detect(fname, "", mime)
                self.assertFalse(is_report, f"{fname} should not be report")
                self.assertIsNone(doc_type)

    def test_E2_html_outside_reports_dir_is_not_report(self):
        """E2: HTML 文件不在 /reports/ 路径下 → is_report=False"""
        paths = [
            "customer_data/alice/outputs/chart.html",
            "customer_data/alice/chart.html",
            "customer_data/alice/exports/dashboard.html",
        ]
        for p in paths:
            with self.subTest(path=p):
                is_report, doc_type = self.detect(p, "<html></html>", "text/html")
                self.assertFalse(is_report, f"{p} should not be report (not in /reports/)")
                self.assertIsNone(doc_type)

    def test_E3_html_in_reports_no_summary_is_dashboard(self):
        """E3: HTML 在 /reports/ 路径，不含 summary-section → dashboard"""
        path = "customer_data/alice/reports/chart_20260414.html"
        content = "<html><body><div class='chart-container'></div></body></html>"
        is_report, doc_type = self.detect(path, content, "text/html")
        self.assertTrue(is_report)
        self.assertEqual(doc_type, "dashboard")

    def test_E4_html_in_reports_with_summary_is_document(self):
        """E4: HTML 含 class="summary-section" → document（LLM 分析报告）"""
        path = "customer_data/alice/reports/analysis_20260414.html"
        content = (
            '<html><body>'
            '<div class="summary-section"><p>LLM generated summary</p></div>'
            '<div class="chart-area"></div>'
            '</body></html>'
        )
        is_report, doc_type = self.detect(path, content, "text/html")
        self.assertTrue(is_report)
        self.assertEqual(doc_type, "document")

    def test_E5_windows_backslash_path_detected_correctly(self):
        """E5: Windows 反斜线路径下，/reports/ 仍能正确检测"""
        path_win = r"customer_data\alice\reports\chart_20260414.html"
        # 无 summary-section → dashboard
        is_report, doc_type = self.detect(path_win, "<html></html>", "text/html")
        self.assertTrue(is_report, "Windows-style path should still be detected as report")
        self.assertEqual(doc_type, "dashboard")

        # 含 summary-section → document
        is_report2, doc_type2 = self.detect(
            path_win,
            '<div class="summary-section">summary</div>',
            "text/html",
        )
        self.assertTrue(is_report2)
        self.assertEqual(doc_type2, "document")


# ══════════════════════════════════════════════════════════════════════════════
# Section F — 端到端 Pin 流程（6 tests）
# ══════════════════════════════════════════════════════════════════════════════

class TestFEndToEndFlow(unittest.TestCase):
    """
    F: 完整链路：创建 HTML 文件 → POST /reports/pin → GET /reports
       → GET /reports/{id} → GET /reports/{id}/html?token=
    """

    @classmethod
    def setUpClass(cls):
        cls.client = _make_client()
        cls.user = _make_user("f_user", "analyst")
        cls.headers = _auth(cls.user)

    # ─── helper: pin a file and return response data ──────────────────────────

    def _pin(self, fp: str, doc_type: str = "dashboard") -> dict:
        res = self.client.post(
            "/api/v1/reports/pin",
            json={"file_path": fp, "doc_type": doc_type},
            headers=self.headers,
        )
        self.assertEqual(res.status_code, 200, res.text)
        return res.json()["data"]

    def test_F1_pin_dashboard_appears_in_list(self):
        """F1: pin dashboard HTML → GET /reports?doc_type=dashboard 可见"""
        fp = _create_html_file(self.user.username, with_summary=False)
        d = self._pin(fp, "dashboard")
        pinned_id = d["report_id"]

        res = self.client.get("/api/v1/reports?doc_type=dashboard", headers=self.headers)
        self.assertEqual(res.status_code, 200)
        ids = [item["id"] for item in res.json()["data"]["items"]]
        self.assertIn(pinned_id, ids, "pinned dashboard should appear in doc_type=dashboard list")

    def test_F2_pin_document_appears_in_document_list(self):
        """F2: pin document HTML → GET /reports?doc_type=document 可见"""
        fp = _create_html_file(self.user.username, with_summary=True)
        d = self._pin(fp, "document")
        pinned_id = d["report_id"]
        self.assertEqual(d["doc_type"], "document")

        res = self.client.get("/api/v1/reports?doc_type=document", headers=self.headers)
        self.assertEqual(res.status_code, 200)
        ids = [item["id"] for item in res.json()["data"]["items"]]
        self.assertIn(pinned_id, ids, "pinned document should appear in doc_type=document list")

    def test_F3_pin_then_get_single_report(self):
        """F3: pin 后 GET /reports/{id} 可取到单条，且字段完整"""
        fp = _create_html_file(self.user.username)
        d = self._pin(fp)
        pinned_id = d["report_id"]

        res = self.client.get(f"/api/v1/reports/{pinned_id}", headers=self.headers)
        self.assertEqual(res.status_code, 200)
        item = res.json()["data"]
        self.assertEqual(item["id"], pinned_id)
        self.assertIn("refresh_token", item)
        self.assertIn("report_file_path", item)
        self.assertEqual(item["report_file_path"], fp.replace("\\", "/"))

    def test_F4_pin_then_access_html_via_token(self):
        """F4: pin 后可通过 GET /reports/{id}/html?token= 访问 HTML 内容（无 JWT）"""
        fp = _create_html_file(self.user.username)
        d = self._pin(fp)
        pinned_id = d["report_id"]
        refresh_token = d["refresh_token"]

        res = self.client.get(
            f"/api/v1/reports/{pinned_id}/html",
            params={"token": refresh_token},
        )
        self.assertEqual(res.status_code, 200)
        self.assertIn("text/html", res.headers.get("content-type", ""))
        # HTML 内容非空
        self.assertGreater(len(res.text), 10)

    def test_F5_pin_two_files_both_appear(self):
        """F5: pin 两个不同文件 → GET /reports 均可见（共 2 条）"""
        fp1 = _create_html_file(self.user.username)
        fp2 = _create_html_file(self.user.username)
        d1 = self._pin(fp1)
        d2 = self._pin(fp2)

        res = self.client.get("/api/v1/reports", headers=self.headers)
        self.assertEqual(res.status_code, 200)
        ids = [item["id"] for item in res.json()["data"]["items"]]
        self.assertIn(d1["report_id"], ids)
        self.assertIn(d2["report_id"], ids)

    def test_F6_idempotent_pin_does_not_duplicate(self):
        """F6: 同一文件 pin 两次（幂等）→ GET /reports 中只有一条（不重复入库）"""
        fp = _create_html_file(self.user.username)
        d1 = self._pin(fp)
        d2 = self._pin(fp)  # 第二次 pin，应幂等

        self.assertEqual(d1["report_id"], d2["report_id"], "idempotent: same report_id")
        self.assertFalse(d2["is_new"], "second pin should return is_new=False")

        # 列表中只有一条该 id
        res = self.client.get("/api/v1/reports", headers=self.headers)
        ids = [item["id"] for item in res.json()["data"]["items"]]
        self.assertEqual(
            ids.count(d1["report_id"]),
            1,
            "duplicate pin should not create duplicate report in list",
        )


# ══════════════════════════════════════════════════════════════════════════════
# 入口
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    unittest.main(verbosity=2)
