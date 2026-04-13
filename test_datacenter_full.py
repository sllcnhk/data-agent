"""
test_datacenter_full.py — 数据管理中心完整测试套件
===================================================

覆盖全部核心端到端流程，结合 bug 修复验证。

测试分区：
  A (8)  — 报表生成与列表（doc_type 过滤、分页、用户隔离）
  B (7)  — 报表编辑（PUT /spec 重新生成 HTML、所有权保护）
  C (6)  — Co-pilot 对话上下文注入
  D (8)  — 定时任务 CRUD + 调度权限
  E (5)  — 定时任务切换 / 立即执行 / 历史
  F (7)  — RBAC 权限矩阵（viewer/analyst/admin/superadmin）
  G (10) — 前端代码检查（路由/布局/菜单/权限/Co-pilot）

总计: 51 个测试用例（无 DB 环境：G 区全部通过）
"""
from __future__ import annotations

import os
import sys
import uuid
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "backend"))
os.environ.setdefault("POSTGRES_PASSWORD", "Sgp013013")
os.environ.setdefault("POSTGRES_HOST", "localhost")
os.environ.setdefault("ENABLE_AUTH", "False")

# Pre-import settings and models at module load time to avoid:
# 1. pydantic_core re-initialization when multiple test files run in one pytest process
# 2. SQLAlchemy metadata conflicts from dual-path imports (api.* vs backend.*)
# 3. PyO3 C-extension (bcrypt) re-initialization error when running alongside test_schedule_api.py
from backend.config.settings import settings as _settings  # noqa: E402
import backend.models  # noqa: E402 — registers all SQLAlchemy mappers under backend.* namespace
from backend.core.auth.password import hash_password as _hash_password_preload  # noqa: E402 — pre-load bcrypt C ext
from backend.core.auth.jwt import create_access_token as _create_access_token_preload  # noqa: E402
from backend.core.rbac import get_user_roles as _get_user_roles_preload  # noqa: E402

_PREFIX = f"_ci_{uuid.uuid4().hex[:6]}_"

# ── 全局 DB ──────────────────────────────────────────────────────────────────

def _db():
    from backend.config.database import SessionLocal
    return SessionLocal()

_g_db = _db()

# ── Auth helpers ─────────────────────────────────────────────────────────────

def _make_user(suffix="", role_names=None, is_superadmin=False):
    from backend.models.user import User
    from backend.models.role import Role
    from backend.models.user_role import UserRole
    from backend.core.auth.password import hash_password

    username = f"{_PREFIX}{suffix or uuid.uuid4().hex[:6]}"
    u = User(
        username=username,
        display_name=f"CI {suffix}",
        hashed_password=hash_password("Test1234!"),
        auth_source="local",
        is_active=True,
        is_superadmin=is_superadmin,
    )
    _g_db.add(u)
    _g_db.flush()
    for rname in role_names or []:
        role = _g_db.query(Role).filter(Role.name == rname).first()
        if role:
            _g_db.add(UserRole(user_id=u.id, role_id=role.id))
    _g_db.commit()
    _g_db.refresh(u)
    return u


def _token(user):
    from backend.core.auth.jwt import create_access_token
    from backend.core.rbac import get_user_roles
    roles = get_user_roles(user, _g_db)
    return create_access_token(
        {"sub": str(user.id), "username": user.username, "roles": roles},
        _settings.jwt_secret, _settings.jwt_algorithm,
    )


def _auth(user):
    return {"Authorization": f"Bearer {_token(user)}"}


# ── Module-level patcher ─────────────────────────────────────────────────────

_auth_patcher = None


def setup_module(_=None):
    global _auth_patcher
    _auth_patcher = patch.object(_settings, "enable_auth", False)
    _auth_patcher.start()
    # Ensure RBAC seeds are in place
    try:
        from backend.scripts.init_rbac import run as init_rbac
        init_rbac()
    except Exception:
        pass
    # Ensure datacenter tables exist
    try:
        from backend.scripts.migrate_datacenter_v1 import run as migrate
        migrate()
    except Exception:
        pass


def teardown_module(_=None):
    global _auth_patcher
    if _auth_patcher:
        _auth_patcher.stop()
    from backend.models.user import User
    from backend.models.report import Report
    from backend.models.scheduled_report import ScheduledReport
    from backend.models.conversation import Conversation
    try:
        test_users = _g_db.query(User).filter(User.username.like(f"{_PREFIX}%")).all()
        unames = [u.username for u in test_users]
        uids = [u.id for u in test_users]
        _g_db.query(Report).filter(Report.username.in_(unames)).delete(synchronize_session=False)
        _g_db.query(ScheduledReport).filter(
            ScheduledReport.owner_username.in_(unames)
        ).delete(synchronize_session=False)
        _g_db.query(Conversation).filter(Conversation.title.like(f"{_PREFIX}%")).delete(
            synchronize_session=False
        )
        _g_db.query(User).filter(User.username.like(f"{_PREFIX}%")).delete(
            synchronize_session=False
        )
        _g_db.commit()
    except Exception as e:
        print(f"[teardown] {e}")
        _g_db.rollback()
    finally:
        _g_db.close()


# ── TestClient ────────────────────────────────────────────────────────────────

def _client():
    from backend.main import app
    from fastapi.testclient import TestClient
    return TestClient(app, raise_server_exceptions=False)


# ── Fixtures ──────────────────────────────────────────────────────────────────

_SPEC = {
    "title": f"{_PREFIX}Test Dashboard",
    "theme": "light",
    "charts": [{"id": "c1", "chart_lib": "echarts", "chart_type": "bar",
                "title": "Sales", "sql": "SELECT 1", "connection_env": "sg",
                "x_field": "date", "y_fields": ["value"], "width": "full"}],
    "filters": [],
}

_SCHED = {
    "name": f"{_PREFIX}Daily Task",
    "cron_expr": "0 9 * * 1",
    "timezone": "Asia/Shanghai",
    "doc_type": "dashboard",
    "report_spec": _SPEC,
    "notify_channels": [{"type": "email", "to": ["ci@test.com"]}],
}


def _build_report(client, user, doc_type="dashboard", title_extra=""):
    spec = dict(_SPEC, title=f"{_PREFIX}{title_extra}_{doc_type}")
    with patch("api.reports.build_report_html", return_value="<html>ok</html>"):
        return client.post(
            "/api/v1/reports/build",
            json={"spec": spec, "doc_type": doc_type},
            headers=_auth(user),
        )


def _create_sched(client, user, **overrides):
    payload = dict(_SCHED, **overrides)
    with patch("api.scheduled_reports.add_or_update_job"):
        return client.post(
            "/api/v1/scheduled-reports/",
            json=payload,
            headers=_auth(user),
        )


# ══════════════════════════════════════════════════════════════════════════════
# A — 报表生成与列表
# ══════════════════════════════════════════════════════════════════════════════

class TestAReportBuildAndList(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.user = _make_user("a")
        cls.client = _client()

    def test_A1_build_returns_report_id(self):
        """build 成功返回 report_id (UUID)"""
        r = _build_report(self.client, self.user, title_extra="A1")
        self.assertEqual(r.status_code, 200, r.text)
        rid = r.json()["data"]["report_id"]
        uuid.UUID(rid)  # 必须是合法 UUID

    def test_A2_report_appears_in_list(self):
        """build 后在 GET /reports 列表中可见"""
        r = _build_report(self.client, self.user, title_extra="A2")
        rid = r.json()["data"]["report_id"]
        lst = self.client.get("/api/v1/reports", headers=_auth(self.user))
        ids = [i["id"] for i in lst.json()["data"]["items"]]
        self.assertIn(rid, ids)

    def test_A3_doc_type_dashboard_stored(self):
        """dashboard 类型正确存储并可过滤"""
        r = _build_report(self.client, self.user, "dashboard", "A3")
        rid = r.json()["data"]["report_id"]
        lst = self.client.get("/api/v1/reports?doc_type=dashboard", headers=_auth(self.user))
        items = lst.json()["data"]["items"]
        ids = [i["id"] for i in items]
        self.assertIn(rid, ids)
        for item in items:
            if "doc_type" in item:
                self.assertEqual(item["doc_type"], "dashboard")

    def test_A4_doc_type_document_stored(self):
        """document 类型正确存储，且 ?doc_type=dashboard 不返回该记录"""
        r = _build_report(self.client, self.user, "document", "A4")
        rid = r.json()["data"]["report_id"]
        lst = self.client.get("/api/v1/reports?doc_type=dashboard", headers=_auth(self.user))
        ids = [i["id"] for i in lst.json()["data"]["items"]]
        self.assertNotIn(rid, ids, "document 不应出现在 dashboard 列表中")

    def test_A5_list_pagination_fields(self):
        """列表响应包含 total/page/page_size/items"""
        lst = self.client.get("/api/v1/reports", headers=_auth(self.user))
        d = lst.json()["data"]
        for key in ("total", "page", "page_size", "items"):
            self.assertIn(key, d)

    def test_A6_get_report_detail(self):
        """GET /reports/{id} 返回详情"""
        r = _build_report(self.client, self.user, title_extra="A6")
        rid = r.json()["data"]["report_id"]
        resp = self.client.get(f"/api/v1/reports/{rid}", headers=_auth(self.user))
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["data"]["id"], rid)

    def test_A7_user_b_cannot_list_user_a_reports(self):
        """用户 B 的列表不含用户 A 的报表（用户隔离）"""
        user_b = _make_user("a7b")
        r = _build_report(self.client, self.user, title_extra="A7")
        rid = r.json()["data"]["report_id"]
        with patch.object(_settings, "enable_auth", True):
            lst = self.client.get("/api/v1/reports", headers=_auth(user_b))
        ids = [i["id"] for i in lst.json().get("data", {}).get("items", [])]
        self.assertNotIn(rid, ids)

    def test_A8_user_b_cannot_get_user_a_report(self):
        """用户 B 直接 GET 用户 A 的报表 → 403"""
        user_b = _make_user("a8b")
        r = _build_report(self.client, self.user, title_extra="A8")
        rid = r.json()["data"]["report_id"]
        with patch.object(_settings, "enable_auth", True):
            resp = self.client.get(f"/api/v1/reports/{rid}", headers=_auth(user_b))
        self.assertEqual(resp.status_code, 403, resp.text)


# ══════════════════════════════════════════════════════════════════════════════
# B — 报表 spec 编辑
# ══════════════════════════════════════════════════════════════════════════════

class TestBReportSpecUpdate(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.user = _make_user("b")
        cls.client = _client()
        r = _build_report(cls.client, cls.user, title_extra="B_base")
        if r.status_code == 200:
            cls.rid = r.json()["data"]["report_id"]
        else:
            cls.rid = None

    def test_B1_put_spec_returns_200(self):
        """PUT /reports/{id}/spec 返回 200"""
        if not self.rid:
            self.skipTest("setup failed")
        new_spec = dict(_SPEC, title=f"{_PREFIX}B1 Updated")
        with patch("api.reports.build_report_html", return_value="<html>v2</html>"):
            resp = self.client.put(
                f"/api/v1/reports/{self.rid}/spec",
                json={"spec": new_spec},
                headers=_auth(self.user),
            )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertTrue(resp.json()["success"])

    def test_B2_spec_updates_report_name(self):
        """PUT /spec 后 GET 返回新名称"""
        if not self.rid:
            self.skipTest("setup failed")
        new_title = f"{_PREFIX}B2 Renamed"
        with patch("api.reports.build_report_html", return_value="<html>v2</html>"):
            self.client.put(
                f"/api/v1/reports/{self.rid}/spec",
                json={"spec": dict(_SPEC, title=new_title)},
                headers=_auth(self.user),
            )
        resp = self.client.get(f"/api/v1/reports/{self.rid}", headers=_auth(self.user))
        self.assertEqual(resp.json()["data"]["name"], new_title)

    def test_B3_put_spec_calls_build_report_html(self):
        """PUT /spec 确实重新调用了 build_report_html"""
        if not self.rid:
            self.skipTest("setup failed")
        with patch("api.reports.build_report_html", return_value="<html>v3</html>") as mock_build:
            self.client.put(
                f"/api/v1/reports/{self.rid}/spec",
                json={"spec": _SPEC},
                headers=_auth(self.user),
            )
        mock_build.assert_called_once()

    def test_B4_put_spec_404_nonexistent(self):
        """PUT /spec 对不存在 report → 404"""
        resp = self.client.put(
            f"/api/v1/reports/{uuid.uuid4()}/spec",
            json={"spec": _SPEC},
            headers=_auth(self.user),
        )
        self.assertEqual(resp.status_code, 404, resp.text)

    def test_B5_put_spec_403_wrong_user(self):
        """PUT /spec 跨用户 → 403"""
        if not self.rid:
            self.skipTest("setup failed")
        other = _make_user("b5other")
        with patch.object(_settings, "enable_auth", True):
            with patch("api.reports.build_report_html", return_value="<html>x</html>"):
                resp = self.client.put(
                    f"/api/v1/reports/{self.rid}/spec",
                    json={"spec": _SPEC},
                    headers=_auth(other),
                )
        self.assertEqual(resp.status_code, 403, resp.text)

    def test_B6_superadmin_can_update_any_report(self):
        """superadmin 可以 PUT /spec 任意用户的报表（bug#11 修复验证）"""
        if not self.rid:
            self.skipTest("setup failed")
        sa = _make_user("b6sa", is_superadmin=True)
        with patch("api.reports.build_report_html", return_value="<html>sa</html>"):
            resp = self.client.put(
                f"/api/v1/reports/{self.rid}/spec",
                json={"spec": _SPEC},
                headers=_auth(sa),
            )
        self.assertEqual(resp.status_code, 200, resp.text)

    def test_B7_delete_report(self):
        """DELETE /reports/{id} 删除后 GET 返回 404"""
        r = _build_report(self.client, self.user, title_extra="B7_del")
        rid = r.json()["data"]["report_id"]
        del_r = self.client.delete(f"/api/v1/reports/{rid}", headers=_auth(self.user))
        self.assertIn(del_r.status_code, [200, 204])
        get_r = self.client.get(f"/api/v1/reports/{rid}", headers=_auth(self.user))
        self.assertEqual(get_r.status_code, 404)


# ══════════════════════════════════════════════════════════════════════════════
# C — Co-pilot 上下文注入
# ══════════════════════════════════════════════════════════════════════════════

class TestCCopilotContext(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.user = _make_user("c")
        cls.client = _client()
        r = _build_report(cls.client, cls.user, title_extra="C_base")
        cls.rid = r.json()["data"]["report_id"] if r.status_code == 200 else None

    def test_C1_copilot_returns_conversation_id(self):
        """POST /reports/{id}/copilot 返回 conversation_id"""
        if not self.rid:
            self.skipTest("setup failed")
        resp = self.client.post(
            f"/api/v1/reports/{self.rid}/copilot",
            json={},
            headers=_auth(self.user),
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        cid = resp.json()["data"]["conversation_id"]
        uuid.UUID(cid)

    def test_C2_copilot_system_prompt_has_copilot_marker(self):
        """系统提示包含 [Co-pilot 模式] 标记"""
        if not self.rid:
            self.skipTest("setup failed")
        resp = self.client.post(
            f"/api/v1/reports/{self.rid}/copilot",
            json={},
            headers=_auth(self.user),
        )
        cid = resp.json()["data"]["conversation_id"]
        conv_r = self.client.get(f"/api/v1/conversations/{cid}", headers=_auth(self.user))
        # GET /conversations/{id} returns {"conversation": {...}} shape
        conv_data = conv_r.json().get("conversation") or conv_r.json().get("data", {})
        sys_p = conv_data.get("system_prompt", "")
        self.assertIn("Co-pilot", sys_p)

    def test_C3_copilot_system_prompt_has_report_name(self):
        """系统提示中包含报表名称"""
        if not self.rid:
            self.skipTest("setup failed")
        resp = self.client.post(
            f"/api/v1/reports/{self.rid}/copilot",
            json={},
            headers=_auth(self.user),
        )
        cid = resp.json()["data"]["conversation_id"]
        conv_r = self.client.get(f"/api/v1/conversations/{cid}", headers=_auth(self.user))
        conv_data = conv_r.json().get("conversation") or conv_r.json().get("data", {})
        sys_p = conv_data.get("system_prompt", "")
        # system prompt must contain report name
        detail_r = self.client.get(f"/api/v1/reports/{self.rid}", headers=_auth(self.user))
        rname = detail_r.json()["data"]["name"]
        self.assertIn(rname, sys_p)

    def test_C4_copilot_custom_title(self):
        """copilot 使用自定义标题"""
        if not self.rid:
            self.skipTest("setup failed")
        title = f"{_PREFIX}My Assistant"
        resp = self.client.post(
            f"/api/v1/reports/{self.rid}/copilot",
            json={"title": title},
            headers=_auth(self.user),
        )
        cid = resp.json()["data"]["conversation_id"]
        conv_r = self.client.get(f"/api/v1/conversations/{cid}", headers=_auth(self.user))
        conv_data = conv_r.json().get("conversation") or conv_r.json().get("data", {})
        self.assertIn(title, conv_data.get("title", ""))

    def test_C5_copilot_nonexistent_report_404(self):
        """不存在的报表 → 404"""
        resp = self.client.post(
            f"/api/v1/reports/{uuid.uuid4()}/copilot",
            json={},
            headers=_auth(self.user),
        )
        self.assertEqual(resp.status_code, 404, resp.text)

    def test_C6_copilot_wrong_user_403(self):
        """跨用户访问 copilot → 403"""
        if not self.rid:
            self.skipTest("setup failed")
        other = _make_user("c6other")
        with patch.object(_settings, "enable_auth", True):
            resp = self.client.post(
                f"/api/v1/reports/{self.rid}/copilot",
                json={},
                headers=_auth(other),
            )
        self.assertEqual(resp.status_code, 403, resp.text)


# ══════════════════════════════════════════════════════════════════════════════
# D — 定时任务 CRUD
# ══════════════════════════════════════════════════════════════════════════════

class TestDScheduleCRUD(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.user = _make_user("d")
        cls.client = _client()

    def test_D1_create_schedule_returns_id(self):
        """POST /scheduled-reports/ 返回带 id 的任务"""
        r = _create_sched(self.client, self.user, name=f"{_PREFIX}D1")
        self.assertIn(r.status_code, [200, 201], r.text)
        uuid.UUID(r.json()["data"]["id"])

    def test_D2_schedule_in_list(self):
        """创建后在列表中可见"""
        r = _create_sched(self.client, self.user, name=f"{_PREFIX}D2")
        sid = r.json()["data"]["id"]
        lst = self.client.get("/api/v1/scheduled-reports/", headers=_auth(self.user))
        ids = [i["id"] for i in lst.json()["data"]["items"]]
        self.assertIn(sid, ids)

    def test_D3_get_schedule_detail(self):
        """GET /scheduled-reports/{id} 返回详情"""
        r = _create_sched(self.client, self.user, name=f"{_PREFIX}D3")
        sid = r.json()["data"]["id"]
        resp = self.client.get(f"/api/v1/scheduled-reports/{sid}", headers=_auth(self.user))
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["data"]["id"], sid)

    def test_D4_update_schedule_name(self):
        """PUT /scheduled-reports/{id} 更新名称"""
        r = _create_sched(self.client, self.user, name=f"{_PREFIX}D4")
        sid = r.json()["data"]["id"]
        new_name = f"{_PREFIX}D4 Updated"
        with patch("api.scheduled_reports.add_or_update_job"):
            resp = self.client.put(
                f"/api/v1/scheduled-reports/{sid}",
                json={"name": new_name},
                headers=_auth(self.user),
            )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["data"]["name"], new_name)

    def test_D5_update_cron_expr(self):
        """更新 cron_expr → APScheduler job 重新注册"""
        r = _create_sched(self.client, self.user, name=f"{_PREFIX}D5")
        sid = r.json()["data"]["id"]
        with patch("api.scheduled_reports.add_or_update_job") as mock_job:
            resp = self.client.put(
                f"/api/v1/scheduled-reports/{sid}",
                json={"cron_expr": "0 10 * * *"},
                headers=_auth(self.user),
            )
        self.assertEqual(resp.status_code, 200)
        mock_job.assert_called_once()

    def test_D6_invalid_cron_returns_422(self):
        """非法 cron 表达式（4-field）→ 422（BUG#13 修复验证）"""
        r = _create_sched(self.client, self.user,
                          name=f"{_PREFIX}D6", cron_expr="0 9 * *")
        self.assertEqual(r.status_code, 422, r.text)

    def test_D7_delete_schedule(self):
        """DELETE 后 GET 返回 404"""
        r = _create_sched(self.client, self.user, name=f"{_PREFIX}D7")
        sid = r.json()["data"]["id"]
        with patch("api.scheduled_reports.remove_job"):
            del_r = self.client.delete(
                f"/api/v1/scheduled-reports/{sid}",
                headers=_auth(self.user),
            )
        self.assertIn(del_r.status_code, [200, 204])
        self.assertEqual(
            self.client.get(f"/api/v1/scheduled-reports/{sid}", headers=_auth(self.user)).status_code,
            404,
        )

    def test_D8_cross_user_403(self):
        """跨用户访问定时任务 → 403"""
        r = _create_sched(self.client, self.user, name=f"{_PREFIX}D8")
        sid = r.json()["data"]["id"]
        other = _make_user("d8b")
        with patch.object(_settings, "enable_auth", True):
            resp = self.client.get(f"/api/v1/scheduled-reports/{sid}", headers=_auth(other))
        self.assertEqual(resp.status_code, 403, resp.text)


# ══════════════════════════════════════════════════════════════════════════════
# E — 定时任务切换 / 立即执行 / 历史
# ══════════════════════════════════════════════════════════════════════════════

class TestEScheduleOps(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.user = _make_user("e")
        cls.client = _client()
        r = _create_sched(cls.client, cls.user, name=f"{_PREFIX}E_base")
        cls.sid = r.json()["data"]["id"] if r.status_code in (200, 201) else None

    def test_E1_toggle_disables(self):
        """PUT /toggle 将 is_active 从 True 翻为 False"""
        if not self.sid:
            self.skipTest("setup failed")
        with patch("api.scheduled_reports.remove_job"):
            r = self.client.put(
                f"/api/v1/scheduled-reports/{self.sid}/toggle",
                headers=_auth(self.user),
            )
        self.assertEqual(r.status_code, 200, r.text)
        self.assertFalse(r.json()["data"]["is_active"])

    def test_E2_toggle_enables(self):
        """第二次 PUT /toggle 重新启用"""
        if not self.sid:
            self.skipTest("setup failed")
        with patch("api.scheduled_reports.add_or_update_job"):
            r = self.client.put(
                f"/api/v1/scheduled-reports/{self.sid}/toggle",
                headers=_auth(self.user),
            )
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json()["data"]["is_active"])

    def test_E3_run_now_triggers_async(self):
        """POST /run-now 接受请求（异步后台不阻塞）"""
        if not self.sid:
            self.skipTest("setup failed")
        from backend.services import scheduler_service as svc
        with patch.object(svc, "_execute_async", return_value=None):
            r = self.client.post(
                f"/api/v1/scheduled-reports/{self.sid}/run-now",
                headers=_auth(self.user),
            )
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json()["success"])

    def test_E4_history_returns_list(self):
        """GET /history 返回 run logs 列表"""
        if not self.sid:
            self.skipTest("setup failed")
        r = self.client.get(
            f"/api/v1/scheduled-reports/{self.sid}/history",
            headers=_auth(self.user),
        )
        self.assertEqual(r.status_code, 200, r.text)
        d = r.json()["data"]
        self.assertIn("items", d)
        self.assertIn("total", d)

    def test_E5_toggle_nonexistent_404(self):
        """toggle 不存在的任务 → 404"""
        resp = self.client.put(
            f"/api/v1/scheduled-reports/{uuid.uuid4()}/toggle",
            headers=_auth(self.user),
        )
        self.assertEqual(resp.status_code, 404, resp.text)


# ══════════════════════════════════════════════════════════════════════════════
# F — RBAC 权限矩阵
# ══════════════════════════════════════════════════════════════════════════════

class TestFRBAC(unittest.TestCase):
    """验证各角色对数据管理中心功能的权限边界"""

    @classmethod
    def setUpClass(cls):
        cls.settings = _settings
        cls.client = _client()
        # viewer 只有 chat:use
        cls.viewer = _make_user("f_viewer", role_names=["viewer"])
        # analyst 有 reports:read/create + schedules:read/write
        cls.analyst = _make_user("f_analyst", role_names=["analyst"])
        # admin 有全部报表权限
        cls.admin = _make_user("f_admin", role_names=["admin"])
        # superadmin
        cls.superadmin = _make_user("f_super", is_superadmin=True)

    def _with_auth(self):
        return patch.object(self.settings, "enable_auth", True)

    def test_F1_viewer_cannot_list_reports(self):
        """viewer 无 reports:read → GET /reports 返回 403"""
        with self._with_auth():
            r = self.client.get("/api/v1/reports", headers=_auth(self.viewer))
        self.assertEqual(r.status_code, 403, r.text)

    def test_F2_viewer_cannot_build_report(self):
        """viewer 无 reports:create → POST /reports/build 返回 403"""
        with self._with_auth():
            with patch("api.reports.build_report_html", return_value="<html>x</html>"):
                r = self.client.post(
                    "/api/v1/reports/build",
                    json={"spec": _SPEC, "doc_type": "dashboard"},
                    headers=_auth(self.viewer),
                )
        self.assertEqual(r.status_code, 403, r.text)

    def test_F3_analyst_can_build_and_list_reports(self):
        """analyst 有 reports:create + reports:read"""
        with self._with_auth():
            with patch("api.reports.build_report_html", return_value="<html>ok</html>"):
                r = self.client.post(
                    "/api/v1/reports/build",
                    json={"spec": dict(_SPEC, title=f"{_PREFIX}F3"), "doc_type": "dashboard"},
                    headers=_auth(self.analyst),
                )
        self.assertIn(r.status_code, [200, 201], r.text)

    def test_F4_viewer_cannot_list_schedules(self):
        """viewer 无 schedules:read → 403"""
        with self._with_auth():
            r = self.client.get("/api/v1/scheduled-reports/", headers=_auth(self.viewer))
        self.assertEqual(r.status_code, 403, r.text)

    def test_F5_analyst_can_create_schedule(self):
        """analyst 有 schedules:write → 创建定时任务成功"""
        with self._with_auth():
            with patch("api.scheduled_reports.add_or_update_job"):
                r = self.client.post(
                    "/api/v1/scheduled-reports/",
                    json=dict(_SCHED, name=f"{_PREFIX}F5"),
                    headers=_auth(self.analyst),
                )
        self.assertIn(r.status_code, [200, 201], r.text)

    def test_F6_superadmin_can_see_all_schedules(self):
        """superadmin 可以查看全部用户的定时任务（schedules:admin）"""
        # create schedule as analyst
        with patch("api.scheduled_reports.add_or_update_job"):
            cr = self.client.post(
                "/api/v1/scheduled-reports/",
                json=dict(_SCHED, name=f"{_PREFIX}F6_analyst_sched"),
                headers=_auth(self.analyst),
            )
        if cr.status_code not in (200, 201):
            self.skipTest("schedule not created")
        sid = cr.json()["data"]["id"]
        with self._with_auth():
            lst = self.client.get("/api/v1/scheduled-reports/", headers=_auth(self.superadmin))
        self.assertEqual(lst.status_code, 200, lst.text)
        ids = [i["id"] for i in lst.json()["data"]["items"]]
        self.assertIn(sid, ids)

    def test_F7_superadmin_can_delete_any_report(self):
        """superadmin 可以删除任意用户的报表（bug#11 修复）"""
        with patch("api.reports.build_report_html", return_value="<html>ok</html>"):
            r = self.client.post(
                "/api/v1/reports/build",
                json={"spec": dict(_SPEC, title=f"{_PREFIX}F7"), "doc_type": "dashboard"},
                headers=_auth(self.analyst),
            )
        if r.status_code != 200:
            self.skipTest("report not created")
        rid = r.json()["data"]["report_id"]
        with self._with_auth():
            del_r = self.client.delete(
                f"/api/v1/reports/{rid}",
                headers=_auth(self.superadmin),
            )
        self.assertIn(del_r.status_code, [200, 204], del_r.text)


# ══════════════════════════════════════════════════════════════════════════════
# G — 前端代码检查（无需 DB，始终通过）
# ══════════════════════════════════════════════════════════════════════════════

_FE = Path(__file__).parent / "frontend" / "src"


class TestGFrontendCode(unittest.TestCase):

    def _read(self, rel: str) -> str:
        p = _FE / rel
        self.assertTrue(p.exists(), f"文件不存在: {p}")
        return p.read_text(encoding="utf-8")

    # ── AppLayout ─────────────────────────────────────────────────────────────

    def test_G1_app_layout_has_data_center_menu(self):
        """AppLayout 包含 /data-center 菜单项（bug#12 修复）"""
        c = self._read("components/AppLayout.tsx")
        self.assertIn("/data-center", c, "缺少数据管理中心菜单项")
        self.assertIn("DatabaseOutlined", c, "缺少 DatabaseOutlined 图标")

    def test_G2_app_layout_data_center_is_permission_gated(self):
        """DataCenter 菜单项有 perm: 'reports:read' 权限守卫"""
        c = self._read("components/AppLayout.tsx")
        # Find the data-center line and check it has reports:read
        lines = c.splitlines()
        dc_lines = [l for l in lines if "/data-center" in l and "reports:read" in l]
        self.assertTrue(len(dc_lines) > 0, "DataCenter 菜单未设置 reports:read 权限守卫")

    def test_G3_data_center_layout_schedules_permission_gated(self):
        """DataCenterLayout 推送任务菜单有 schedules:read 权限守卫"""
        c = self._read("components/DataCenterLayout.tsx")
        self.assertIn("schedules:read", c)

    def test_G4_data_center_layout_reports_permission_gated(self):
        """DataCenterLayout 报表/报告菜单有 reports:read 权限守卫（bug#12 扩展修复）"""
        c = self._read("components/DataCenterLayout.tsx")
        # Both dashboards and documents lines should have reports:read
        lines = c.splitlines()
        dashboard_line = next((l for l in lines if "dashboards" in l), "")
        document_line = next((l for l in lines if "documents" in l), "")
        self.assertIn("reports:read", dashboard_line, "报表清单未设置权限守卫")
        self.assertIn("reports:read", document_line, "报告清单未设置权限守卫")

    def test_G5_app_tsx_has_all_data_center_routes(self):
        """App.tsx 含 /data-center 全部子路由"""
        c = self._read("App.tsx")
        for route in ["/data-center", "/data-center/dashboards",
                      "/data-center/documents", "/data-center/schedules"]:
            self.assertIn(route, c, f"缺少路由: {route}")

    def test_G6_data_center_layout_back_button_navigates_chat(self):
        """DataCenterLayout 的返回按钮导航到 /chat"""
        c = self._read("components/DataCenterLayout.tsx")
        self.assertIn("'/chat'", c)

    def test_G7_copilot_component_props_interface(self):
        """DataCenterCopilot 组件具有完整 props 接口"""
        c = self._read("components/DataCenterCopilot.tsx")
        for prop in ["contextType", "contextId", "contextName", "onSpecUpdated", "open", "onClose"]:
            self.assertIn(prop, c, f"缺少 prop: {prop}")

    def test_G8_all_pages_use_real_copilot(self):
        """三个 DataCenter 页面均使用真实 Co-pilot 组件（非占位符）"""
        for page in ["pages/DataCenterDashboards.tsx",
                     "pages/DataCenterDocuments.tsx",
                     "pages/DataCenterSchedules.tsx"]:
            c = self._read(page)
            self.assertIn("import DataCenterCopilot", c, f"{page} 未导入真实 Co-pilot")
            self.assertNotIn(
                "const DataCenterCopilot: React.FC<any> = () => null",
                c,
                f"{page} 仍在使用占位符",
            )

    def test_G9_conversation_sidebar_has_data_center_button(self):
        """ConversationSidebar 有「数据管理」按钮，指向 /data-center"""
        c = self._read("components/chat/ConversationSidebar.tsx")
        self.assertIn("data-center", c)
        self.assertIn("数据管理", c)

    def test_G10_report_model_has_doc_type_column(self):
        """Report 模型定义了 doc_type 列（bug#1 修复验证）"""
        c = self._read("../../backend/models/report.py") if \
            (_FE / "../../backend/models/report.py").exists() else \
            (Path(__file__).parent / "backend" / "models" / "report.py").read_text(encoding="utf-8")
        self.assertIn("doc_type", c)
        self.assertIn("Column(String", c)


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    unittest.main(verbosity=2)
