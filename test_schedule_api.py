"""
test_schedule_api.py — ScheduledReport API 完整测试套件
=======================================================

测试层次：
  K6 (6)  — ScheduledReport model to_dict() 字段 + CRUD via API
  K7 (4)  — Toggle 启用/停用 (PUT /scheduled-reports/{id}/toggle)
  K8 (6)  — Run-now + History + RBAC 权限验证
  K9 (5)  — 用户隔离 + 输入校验

总计: ~21 个测试用例

注意：APScheduler 相关函数在 scheduler_service 模块级 patch，确保
      TestClient 异步处理链中也能看到 mock。
"""

from __future__ import annotations

import os
import sys
import uuid
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "backend"))
os.environ.setdefault("POSTGRES_PASSWORD", "Sgp013013")
os.environ.setdefault("POSTGRES_HOST", "localhost")
os.environ.setdefault("ENABLE_AUTH", "False")

_PREFIX = f"_sa_{uuid.uuid4().hex[:6]}_"

# ─── APScheduler 模块级 patch ────────────────────────────────────────────────
# 在导入任何应用代码之前 patch sys.modules，防止 apscheduler 导入失败

_scheduler_patcher = patch.dict(
    "sys.modules",
    {
        "apscheduler": MagicMock(),
        "apscheduler.schedulers": MagicMock(),
        "apscheduler.schedulers.background": MagicMock(),
        "apscheduler.triggers": MagicMock(),
        "apscheduler.triggers.cron": MagicMock(),
        "apscheduler.jobstores": MagicMock(),
        "apscheduler.jobstores.sqlalchemy": MagicMock(),
        "apscheduler.executors": MagicMock(),
        "apscheduler.executors.pool": MagicMock(),
    },
)
_scheduler_patcher.start()

# ─── auth 设置 patch ─────────────────────────────────────────────────────────

_auth_patcher = None

# ─── 模块级 scheduler function patch ─────────────────────────────────────────
# 在应用模块加载后（setup_module 中）统一替换 scheduler_service 里的函数引用，
# 确保 FastAPI 的 import-time 绑定和 TestClient 的异步调用链都能看到 mock。
_svc_add_or_update_patcher = None
_svc_remove_job_patcher    = None
_svc_pause_job_patcher     = None
_svc_resume_job_patcher    = None
_api_add_or_update_patcher = None
_api_remove_job_patcher    = None

# Exposed mocks (reset between tests that need fresh call counts)
mock_add_or_update = MagicMock()
mock_remove_job    = MagicMock()


def setup_module(_=None):
    global _auth_patcher
    global _svc_add_or_update_patcher, _svc_remove_job_patcher
    global _svc_pause_job_patcher, _svc_resume_job_patcher
    global _api_add_or_update_patcher, _api_remove_job_patcher

    from backend.config.settings import settings
    _auth_patcher = patch.object(settings, "enable_auth", False)
    _auth_patcher.start()

    # Ensure RBAC tables + permissions + roles are seeded (idempotent)
    try:
        from backend.scripts.init_rbac import run as _run_init_rbac
        _run_init_rbac()
    except Exception as _e:
        print(f"[setup] init_rbac warning (non-fatal): {_e}")

    # Ensure scheduled_reports + schedule_run_logs tables exist (idempotent)
    from backend.config.database import engine, Base
    from backend.models.scheduled_report import ScheduledReport  # noqa
    from backend.models.schedule_run_log import ScheduleRunLog  # noqa
    Base.metadata.create_all(
        bind=engine,
        tables=[ScheduledReport.__table__, ScheduleRunLog.__table__],
        checkfirst=True,
    )

    # Patch scheduler service functions at the source AND in the API module
    # so both direct calls and import-time name bindings are intercepted.
    _svc_add_or_update_patcher = patch(
        "backend.services.scheduler_service.add_or_update_job", mock_add_or_update
    )
    _svc_remove_job_patcher = patch(
        "backend.services.scheduler_service.remove_job", mock_remove_job
    )
    _api_add_or_update_patcher = patch(
        "backend.api.scheduled_reports.add_or_update_job", mock_add_or_update
    )
    _api_remove_job_patcher = patch(
        "backend.api.scheduled_reports.remove_job", mock_remove_job
    )
    for p in (_svc_add_or_update_patcher, _svc_remove_job_patcher,
              _api_add_or_update_patcher, _api_remove_job_patcher):
        p.start()


# ─── DB helpers ──────────────────────────────────────────────────────────────

def _db():
    from backend.config.database import SessionLocal
    return SessionLocal()


_g_db = _db()


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
        settings.jwt_secret,
        settings.jwt_algorithm,
    )


def _auth(user):
    return {"Authorization": f"Bearer {_token(user)}"}


def _make_schedule(db, owner_username, name=None, cron_expr="0 9 * * 1", is_active=True):
    """直接在 DB 中创建一个 ScheduledReport，绕过 API（避免调度器调用）。"""
    from backend.models.scheduled_report import ScheduledReport
    sr = ScheduledReport(
        name=name or f"{_PREFIX}sched_{uuid.uuid4().hex[:4]}",
        owner_username=owner_username,
        doc_type="dashboard",
        cron_expr=cron_expr,
        timezone="Asia/Shanghai",
        report_spec={"title": "test"},
        is_active=is_active,
    )
    db.add(sr)
    db.commit()
    db.refresh(sr)
    return sr


def teardown_module(_=None):
    global _auth_patcher
    global _svc_add_or_update_patcher, _svc_remove_job_patcher
    global _api_add_or_update_patcher, _api_remove_job_patcher

    for p in (_svc_add_or_update_patcher, _svc_remove_job_patcher,
              _api_add_or_update_patcher, _api_remove_job_patcher):
        try:
            if p is not None:
                p.stop()
        except Exception:
            pass

    if _auth_patcher is not None:
        _auth_patcher.stop()
        _auth_patcher = None

    from backend.models.user import User
    from backend.models.scheduled_report import ScheduledReport
    from backend.models.schedule_run_log import ScheduleRunLog
    try:
        # 清理测试用的 ScheduledReport（通过 owner_username 前缀）
        test_srs = (
            _g_db.query(ScheduledReport)
            .filter(ScheduledReport.owner_username.like(f"{_PREFIX}%"))
            .all()
        )
        for sr in test_srs:
            _g_db.query(ScheduleRunLog).filter(
                ScheduleRunLog.scheduled_report_id == sr.id
            ).delete(synchronize_session=False)
        _g_db.query(ScheduledReport).filter(
            ScheduledReport.owner_username.like(f"{_PREFIX}%")
        ).delete(synchronize_session=False)

        # 清理按名称前缀创建的（API 创建，owner 是 "default"）
        _g_db.query(ScheduledReport).filter(
            ScheduledReport.name.like(f"{_PREFIX}%")
        ).delete(synchronize_session=False)

        # 清理测试用户
        _g_db.query(User).filter(
            User.username.like(f"{_PREFIX}%")
        ).delete(synchronize_session=False)

        _g_db.commit()
    except Exception as e:
        print(f"[teardown] error: {e}")
        _g_db.rollback()
    finally:
        _g_db.close()

    _scheduler_patcher.stop()


# ─── TestClient 工厂 ──────────────────────────────────────────────────────────

def _make_client():
    from backend.main import app
    from fastapi.testclient import TestClient
    return TestClient(app, raise_server_exceptions=False)


# ═══════════════════════════════════════════════════════════════════════════════
# Section K6 — Model to_dict() + CRUD via API (6 tests)
# ═══════════════════════════════════════════════════════════════════════════════

class TestK6ModelAndCRUD(unittest.TestCase):
    """K6-1~K6-6: ScheduledReport model 字段 + 完整 CRUD 流程"""

    @classmethod
    def setUpClass(cls):
        cls.client = _make_client()
        cls.db = _db()
        cls.user = _make_user("k6", role_names=["analyst"])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def test_K6_1_to_dict_contains_required_fields(self):
        """to_dict() 包含所有必要字段"""
        sr = _make_schedule(self.db, f"{_PREFIX}k6model")
        d = sr.to_dict()

        required = [
            "id", "name", "description", "owner_username",
            "doc_type", "cron_expr", "timezone", "report_spec",
            "include_summary", "is_active", "last_run_at", "next_run_at",
            "run_count", "fail_count", "notify_channels",
            "created_at", "updated_at",
        ]
        for field in required:
            self.assertIn(field, d, f"to_dict() 缺少字段: {field}")

        # 类型检查
        self.assertIsInstance(d["id"], str)
        self.assertIsInstance(d["run_count"], int)
        self.assertIsInstance(d["fail_count"], int)
        self.assertIsInstance(d["is_active"], bool)
        self.assertIsInstance(d["notify_channels"], list)

    def test_K6_2_to_dict_id_is_uuid_string(self):
        """to_dict() 的 id 字段是合法 UUID 字符串"""
        sr = _make_schedule(self.db, f"{_PREFIX}k6uuid")
        d = sr.to_dict()
        # 验证能解析为 UUID
        parsed = uuid.UUID(d["id"])
        self.assertEqual(parsed, sr.id)

    def test_K6_3_create_schedule_returns_201(self):
        """POST /scheduled-reports/ 创建成功，返回 data.id，add_or_update_job 被调用"""
        mock_add_or_update.reset_mock()
        payload = {
            "name": f"{_PREFIX}k6_create",
            "cron_expr": "0 9 * * 1",
            "report_spec": {"title": "Weekly"},
        }
        resp = self.client.post(
            "/api/v1/scheduled-reports/",
            json=payload,
            headers=_auth(self.user),
        )
        self.assertIn(resp.status_code, (200, 201), resp.text)
        body = resp.json()
        self.assertTrue(body["success"])
        self.assertIn("id", body["data"])
        mock_add_or_update.assert_called_once()

    def test_K6_4_get_schedule_detail(self):
        """GET /scheduled-reports/{id} 返回正确详情"""
        sr = _make_schedule(self.db, self.user.username, name=f"{_PREFIX}k6detail")
        resp = self.client.get(
            f"/api/v1/scheduled-reports/{sr.id}",
            headers=_auth(self.user),
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertTrue(body["success"])
        self.assertEqual(body["data"]["id"], str(sr.id))
        self.assertEqual(body["data"]["name"], sr.name)

    def test_K6_5_update_schedule_name(self):
        """PUT /scheduled-reports/{id} 更新名称"""
        sr = _make_schedule(self.db, self.user.username, name=f"{_PREFIX}k6upd_old")
        new_name = f"{_PREFIX}k6upd_new"
        resp = self.client.put(
            f"/api/v1/scheduled-reports/{sr.id}",
            json={"name": new_name},
            headers=_auth(self.user),
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertTrue(body["success"])
        self.assertEqual(body["data"]["name"], new_name)

    def test_K6_6_delete_schedule(self):
        """DELETE /scheduled-reports/{id} 删除后 GET 返回 404，remove_job 被调用"""
        mock_remove_job.reset_mock()
        sr = _make_schedule(self.db, self.user.username, name=f"{_PREFIX}k6del")
        sr_id = str(sr.id)  # 保存 ID，避免删除后访问 expired object
        resp = self.client.delete(
            f"/api/v1/scheduled-reports/{sr_id}",
            headers=_auth(self.user),
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertTrue(body["success"])
        mock_remove_job.assert_called_once()

        # 再次 GET 应 404
        resp2 = self.client.get(
            f"/api/v1/scheduled-reports/{sr_id}",
            headers=_auth(self.user),
        )
        self.assertEqual(resp2.status_code, 404)


# ═══════════════════════════════════════════════════════════════════════════════
# Section K7 — Toggle 启用/停用 (4 tests)
# ═══════════════════════════════════════════════════════════════════════════════

class TestK7Toggle(unittest.TestCase):
    """K7-1~K7-4: PUT /scheduled-reports/{id}/toggle"""

    @classmethod
    def setUpClass(cls):
        cls.client = _make_client()
        cls.db = _db()
        cls.user = _make_user("k7", role_names=["analyst"])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def test_K7_1_toggle_active_to_inactive(self):
        """active=True → toggle → is_active=False，remove_job 被调用"""
        mock_remove_job.reset_mock()
        mock_add_or_update.reset_mock()
        sr = _make_schedule(self.db, self.user.username, is_active=True)
        resp = self.client.put(
            f"/api/v1/scheduled-reports/{sr.id}/toggle",
            headers=_auth(self.user),
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertTrue(body["success"])
        self.assertFalse(body["data"]["is_active"])
        mock_remove_job.assert_called_once()
        mock_add_or_update.assert_not_called()

    def test_K7_2_toggle_inactive_to_active(self):
        """active=False → toggle → is_active=True，add_or_update_job 被调用"""
        mock_add_or_update.reset_mock()
        mock_remove_job.reset_mock()
        sr = _make_schedule(self.db, self.user.username, is_active=False)
        resp = self.client.put(
            f"/api/v1/scheduled-reports/{sr.id}/toggle",
            headers=_auth(self.user),
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertTrue(body["success"])
        self.assertTrue(body["data"]["is_active"])
        mock_add_or_update.assert_called_once()
        mock_remove_job.assert_not_called()

    def test_K7_3_double_toggle_returns_to_original(self):
        """两次 toggle 后 is_active 恢复原值"""
        sr = _make_schedule(self.db, self.user.username, is_active=True)
        base_url = f"/api/v1/scheduled-reports/{sr.id}/toggle"

        resp1 = self.client.put(base_url, headers=_auth(self.user))
        self.assertEqual(resp1.status_code, 200)
        self.assertFalse(resp1.json()["data"]["is_active"])

        resp2 = self.client.put(base_url, headers=_auth(self.user))
        self.assertEqual(resp2.status_code, 200)
        self.assertTrue(resp2.json()["data"]["is_active"])

    def test_K7_4_toggle_nonexistent_returns_404(self):
        """对不存在的 ID 调用 toggle 返回 404"""
        fake_id = str(uuid.uuid4())
        resp = self.client.put(
            f"/api/v1/scheduled-reports/{fake_id}/toggle",
            headers=_auth(self.user),
        )
        self.assertEqual(resp.status_code, 404)


# ═══════════════════════════════════════════════════════════════════════════════
# Section K8 — Run-now + History + RBAC (6 tests)
# ═══════════════════════════════════════════════════════════════════════════════

class TestK8RunNowHistoryRBAC(unittest.TestCase):
    """K8-1~K8-6: 立即执行、执行历史、RBAC 权限验证"""

    @classmethod
    def setUpClass(cls):
        cls.client = _make_client()
        cls.db = _db()
        cls.analyst = _make_user("k8analyst", role_names=["analyst"])
        cls.viewer  = _make_user("k8viewer",  role_names=["viewer"])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def test_K8_1_run_now_returns_success(self):
        """POST /run-now 返回 success=True（后台任务入队，不实际执行）"""
        sr = _make_schedule(self.db, self.analyst.username)
        resp = self.client.post(
            f"/api/v1/scheduled-reports/{sr.id}/run-now",
            headers=_auth(self.analyst),
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertTrue(body["success"])
        self.assertIn("message", body)

    def test_K8_2_history_empty_on_new_schedule(self):
        """新建的定时任务，执行历史为空"""
        sr = _make_schedule(self.db, self.analyst.username)
        resp = self.client.get(
            f"/api/v1/scheduled-reports/{sr.id}/history",
            headers=_auth(self.analyst),
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertTrue(body["success"])
        self.assertEqual(body["data"]["total"], 0)
        self.assertIsInstance(body["data"]["items"], list)
        self.assertEqual(len(body["data"]["items"]), 0)

    def test_K8_3_history_lists_run_logs(self):
        """执行历史接口返回已存在的 ScheduleRunLog 记录"""
        from backend.models.schedule_run_log import ScheduleRunLog
        sr = _make_schedule(self.db, self.analyst.username)

        log = ScheduleRunLog(
            scheduled_report_id=sr.id,
            status="success",
            duration_sec=5,
        )
        self.db.add(log)
        self.db.commit()

        resp = self.client.get(
            f"/api/v1/scheduled-reports/{sr.id}/history",
            headers=_auth(self.analyst),
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["data"]["total"], 1)
        item = body["data"]["items"][0]
        self.assertEqual(item["status"], "success")
        self.assertEqual(item["scheduled_report_id"], str(sr.id))

    def test_K8_4_run_log_to_dict_fields(self):
        """ScheduleRunLog.to_dict() 包含全部必要字段"""
        from backend.models.schedule_run_log import ScheduleRunLog
        sr = _make_schedule(self.db, self.analyst.username)
        log = ScheduleRunLog(
            scheduled_report_id=sr.id,
            status="failed",
            error_msg="timeout",
            duration_sec=3,
        )
        self.db.add(log)
        self.db.commit()
        self.db.refresh(log)

        d = log.to_dict()
        required = [
            "id", "scheduled_report_id", "report_id",
            "status", "error_msg", "duration_sec",
            "notify_summary", "run_at", "finished_at",
        ]
        for field in required:
            self.assertIn(field, d, f"to_dict() 缺少字段: {field}")

        self.assertEqual(d["status"], "failed")
        self.assertEqual(d["error_msg"], "timeout")
        self.assertIsInstance(d["notify_summary"], dict)

    def test_K8_5_viewer_cannot_create_schedule(self):
        """viewer 角色（无 schedules:write）无法创建定时任务，返回 403"""
        from backend.config.settings import settings
        with patch.object(settings, "enable_auth", True):
            payload = {
                "name": f"{_PREFIX}viewer_create",
                "cron_expr": "0 9 * * 1",
                "report_spec": {"title": "x"},
            }
            resp = self.client.post(
                "/api/v1/scheduled-reports/",
                json=payload,
                headers=_auth(self.viewer),
            )
        self.assertEqual(resp.status_code, 403, resp.text)

    def test_K8_6_viewer_cannot_list_schedules(self):
        """viewer 角色（无 schedules:read）无法列出定时任务，返回 403"""
        from backend.config.settings import settings
        with patch.object(settings, "enable_auth", True):
            resp = self.client.get(
                "/api/v1/scheduled-reports/",
                headers=_auth(self.viewer),
            )
        self.assertEqual(resp.status_code, 403, resp.text)


# ═══════════════════════════════════════════════════════════════════════════════
# Section K9 — 用户隔离 + 输入校验 (5 tests)
# ═══════════════════════════════════════════════════════════════════════════════

class TestK9IsolationAndValidation(unittest.TestCase):
    """K9-1~K9-5: 用户隔离 + cron 校验 + superadmin 全局可见"""

    @classmethod
    def setUpClass(cls):
        cls.client     = _make_client()
        cls.db         = _db()
        cls.user_a     = _make_user("k9a", role_names=["analyst"])
        cls.user_b     = _make_user("k9b", role_names=["analyst"])
        cls.superadmin = _make_user("k9sa", is_superadmin=True)

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def test_K9_1_user_cannot_get_another_users_schedule(self):
        """用户 A 无法获取用户 B 的定时任务（403，需 enable_auth=True）"""
        from backend.config.settings import settings
        sr_b = _make_schedule(self.db, self.user_b.username)
        with patch.object(settings, "enable_auth", True):
            resp = self.client.get(
                f"/api/v1/scheduled-reports/{sr_b.id}",
                headers=_auth(self.user_a),
            )
        self.assertEqual(resp.status_code, 403, resp.text)

    def test_K9_2_list_schedules_only_shows_own(self):
        """普通用户列表只返回自己的定时任务（需 enable_auth=True）"""
        from backend.config.settings import settings
        sr_a = _make_schedule(self.db, self.user_a.username, name=f"{_PREFIX}k9_own_a")
        sr_b = _make_schedule(self.db, self.user_b.username, name=f"{_PREFIX}k9_own_b")

        with patch.object(settings, "enable_auth", True):
            resp = self.client.get(
                "/api/v1/scheduled-reports/",
                headers=_auth(self.user_a),
            )
        self.assertEqual(resp.status_code, 200, resp.text)
        items = resp.json()["data"]["items"]
        ids = [it["id"] for it in items]
        self.assertIn(str(sr_a.id), ids)
        self.assertNotIn(str(sr_b.id), ids)

    def test_K9_3_superadmin_can_see_all_schedules(self):
        """superadmin 可以看到所有用户的定时任务（需 enable_auth=True）"""
        from backend.config.settings import settings
        sr_a = _make_schedule(self.db, self.user_a.username, name=f"{_PREFIX}k9_sa_a")
        sr_b = _make_schedule(self.db, self.user_b.username, name=f"{_PREFIX}k9_sa_b")

        with patch.object(settings, "enable_auth", True):
            resp = self.client.get(
                "/api/v1/scheduled-reports/",
                headers=_auth(self.superadmin),
            )
        self.assertEqual(resp.status_code, 200, resp.text)
        items = resp.json()["data"]["items"]
        ids = [it["id"] for it in items]
        self.assertIn(str(sr_a.id), ids)
        self.assertIn(str(sr_b.id), ids)

    def test_K9_4_invalid_cron_expr_returns_422(self):
        """无效 cron 表达式（非 5-field）返回 422，add_or_update_job 不被调用"""
        mock_add_or_update.reset_mock()
        payload = {
            "name": f"{_PREFIX}badcron",
            "cron_expr": "0 9 * *",  # 只有 4 个字段，不合法
            "report_spec": {"title": "x"},
        }
        resp = self.client.post(
            "/api/v1/scheduled-reports/",
            json=payload,
            headers=_auth(self.user_a),
        )
        self.assertEqual(resp.status_code, 422, resp.text)
        mock_add_or_update.assert_not_called()

    def test_K9_5_user_cannot_delete_another_users_schedule(self):
        """用户 A 无法删除用户 B 的定时任务（403，需 enable_auth=True）"""
        from backend.config.settings import settings
        sr_b = _make_schedule(self.db, self.user_b.username, name=f"{_PREFIX}k9_del_b")
        with patch.object(settings, "enable_auth", True):
            resp = self.client.delete(
                f"/api/v1/scheduled-reports/{sr_b.id}",
                headers=_auth(self.user_a),
            )
        self.assertEqual(resp.status_code, 403, resp.text)

        # 确认任务仍存在
        self.db.expire_all()
        from backend.models.scheduled_report import ScheduledReport
        still_there = (
            self.db.query(ScheduledReport)
            .filter(ScheduledReport.id == sr_b.id)
            .first()
        )
        self.assertIsNotNone(still_there)


if __name__ == "__main__":
    unittest.main(verbosity=2)
