"""
test_datacenter_e2e.py — DataCenter (BI Platform) End-to-End Tests
===================================================================

Test sections:
  L1  (5)  — POST /reports/build → report in GET /reports list
  L2  (4)  — PUT /reports/{id}/spec updates report (HTML regenerated)
  L3  (5)  — POST /reports/{id}/copilot → conversation with context
  L4  (5)  — POST /scheduled-reports/ → task in GET /scheduled-reports/
  L5  (5)  — User isolation: User A's reports NOT visible to User B
  L6  (6)  — Frontend code inspection (always pass, no DB needed)

Total: ~30 tests

Notes:
- DB-dependent tests (L1-L5) will ERROR at setUpClass if PostgreSQL is unreachable.
  This is expected and matches other test files in this project.
- Report.doc_type column does NOT exist in the current model; the API catches
  AttributeError and skips persistence. L1 tests verify the report IS created but
  do NOT assert on doc_type in the list response (since filtering is also skipped).
- ScheduledReport.doc_type IS a real column; L4 tests verify it round-trips correctly.
"""

from __future__ import annotations

import os
import sys
import uuid
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "backend"))
os.environ.setdefault("POSTGRES_PASSWORD", "Sgp013013")
os.environ.setdefault("POSTGRES_HOST", "localhost")
os.environ.setdefault("ENABLE_AUTH", "False")

_PREFIX = f"_ci_{uuid.uuid4().hex[:6]}_"

# ── Module-level auth patch (kept off for most tests; L5 enables it locally) ──

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


# ── Global DB session ──────────────────────────────────────────────────────────

def _db():
    from backend.config.database import SessionLocal
    return SessionLocal()


try:
    _g_db = _db()
    _DB_AVAILABLE = True
except Exception:
    _g_db = None
    _DB_AVAILABLE = False


# ── Test data factories ────────────────────────────────────────────────────────

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


def _cleanup_test_data():
    """Remove all test artifacts created with _PREFIX."""
    if not _DB_AVAILABLE or _g_db is None:
        return
    try:
        from backend.models.user import User
        from backend.models.report import Report
        from backend.models.scheduled_report import ScheduledReport

        # Remove reports owned by test users
        _g_db.query(Report).filter(
            Report.username.like(f"{_PREFIX}%")
        ).delete(synchronize_session=False)

        # Remove scheduled reports owned by test users
        _g_db.query(ScheduledReport).filter(
            ScheduledReport.owner_username.like(f"{_PREFIX}%")
        ).delete(synchronize_session=False)

        # Remove test users (cascades UserRole etc.)
        _g_db.query(User).filter(
            User.username.like(f"{_PREFIX}%")
        ).delete(synchronize_session=False)

        _g_db.commit()
    except Exception as exc:
        print(f"[teardown] cleanup error: {exc}")
        _g_db.rollback()
    finally:
        _g_db.close()


# ── TestClient factory ────────────────────────────────────────────────────────

from fastapi.testclient import TestClient  # noqa: E402


def _make_client():
    from backend.main import app
    return TestClient(app, raise_server_exceptions=False)


# ── Minimal report spec ────────────────────────────────────────────────────────

def _minimal_spec(title: str = "Test Report") -> dict:
    return {
        "title": title,
        "subtitle": "CI subtitle",
        "theme": "light",
        "charts": [],
        "filters": [],
    }


# ══════════════════════════════════════════════════════════════════════════════
# Section L1 — POST /reports/build → appears in GET /reports list (5 tests)
# ══════════════════════════════════════════════════════════════════════════════

class TestL1BuildReport(unittest.TestCase):
    """L1-1 … L1-5: Build a report and verify it appears in the list."""

    @classmethod
    def setUpClass(cls):
        cls.client = _make_client()
        cls.user = _make_user("l1_user", role_names=["analyst"])

    # L1-1: POST /reports/build returns success with report_id
    def test_l1_1_build_returns_report_id(self):
        with patch("backend.api.reports.build_report_html", return_value="<html>test</html>"):
            resp = self.client.post(
                "/api/v1/reports/build",
                json={"spec": _minimal_spec("L1 Dashboard"), "doc_type": "dashboard"},
                headers=_auth(self.user),
            )
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertTrue(body.get("success"), body)
        self.assertIn("report_id", body["data"])

    # L1-2: Built report appears in GET /reports list
    def test_l1_2_report_in_list(self):
        report_name = f"{_PREFIX}l1_list_report"
        with patch("backend.api.reports.build_report_html", return_value="<html>test</html>"):
            build_resp = self.client.post(
                "/api/v1/reports/build",
                json={"spec": _minimal_spec(report_name), "doc_type": "dashboard"},
                headers=_auth(self.user),
            )
        self.assertEqual(build_resp.status_code, 200)
        report_id = build_resp.json()["data"]["report_id"]

        list_resp = self.client.get("/api/v1/reports", headers=_auth(self.user))
        self.assertEqual(list_resp.status_code, 200)
        items = list_resp.json()["data"]["items"]
        ids = [r["id"] for r in items]
        self.assertIn(report_id, ids, f"report_id={report_id} not found in list")

    # L1-3: GET /reports/{id} returns the report with correct name
    def test_l1_3_get_report_detail(self):
        report_name = f"{_PREFIX}l1_detail"
        with patch("backend.api.reports.build_report_html", return_value="<html>detail</html>"):
            build_resp = self.client.post(
                "/api/v1/reports/build",
                json={"spec": _minimal_spec(report_name), "doc_type": "dashboard"},
                headers=_auth(self.user),
            )
        self.assertEqual(build_resp.status_code, 200)
        report_id = build_resp.json()["data"]["report_id"]

        detail_resp = self.client.get(f"/api/v1/reports/{report_id}", headers=_auth(self.user))
        self.assertEqual(detail_resp.status_code, 200)
        data = detail_resp.json()["data"]
        self.assertEqual(data["name"], report_name)

    # L1-4: include_summary=False → summary_status is "skipped"
    def test_l1_4_summary_status_skipped(self):
        with patch("backend.api.reports.build_report_html", return_value="<html>x</html>"):
            resp = self.client.post(
                "/api/v1/reports/build",
                json={
                    "spec": _minimal_spec("L1 No Summary"),
                    "doc_type": "dashboard",
                    "include_summary": False,
                },
                headers=_auth(self.user),
            )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["data"]["summary_status"], "skipped")

    # L1-5: GET /reports list pagination fields are present
    def test_l1_5_list_pagination_fields(self):
        resp = self.client.get("/api/v1/reports?page=1&page_size=5", headers=_auth(self.user))
        self.assertEqual(resp.status_code, 200)
        data = resp.json()["data"]
        self.assertIn("total", data)
        self.assertIn("items", data)
        self.assertIn("page", data)
        self.assertIn("page_size", data)


# ══════════════════════════════════════════════════════════════════════════════
# Section L2 — PUT /reports/{id}/spec updates report (4 tests)
# ══════════════════════════════════════════════════════════════════════════════

class TestL2UpdateSpec(unittest.TestCase):
    """L2-1 … L2-4: Update a report's spec and verify changes persist."""

    @classmethod
    def setUpClass(cls):
        cls.client = _make_client()
        cls.user = _make_user("l2_user", role_names=["analyst"])
        # Build a report to use for update tests
        with patch("backend.api.reports.build_report_html", return_value="<html>orig</html>"):
            resp = cls.client.post(
                "/api/v1/reports/build",
                json={"spec": _minimal_spec("L2 Original"), "doc_type": "dashboard"},
                headers=_auth(cls.user),
            )
        if resp.status_code != 200:
            raise RuntimeError(f"setUpClass: build report failed: {resp.text}")
        cls.report_id = resp.json()["data"]["report_id"]

    # L2-1: PUT /reports/{id}/spec returns success
    def test_l2_1_update_spec_returns_success(self):
        new_spec = _minimal_spec("L2 Updated Title")
        with patch("backend.api.reports.build_report_html", return_value="<html>updated</html>"):
            resp = self.client.put(
                f"/api/v1/reports/{self.report_id}/spec",
                json={"spec": new_spec},
                headers=_auth(self.user),
            )
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertTrue(body.get("success"), body)
        self.assertEqual(body["data"]["report_id"], self.report_id)

    # L2-2: After PUT /spec, report name is updated in DB
    def test_l2_2_name_updated_after_spec_put(self):
        new_title = f"{_PREFIX}l2_renamed"
        new_spec = _minimal_spec(new_title)
        with patch("backend.api.reports.build_report_html", return_value="<html>renamed</html>"):
            self.client.put(
                f"/api/v1/reports/{self.report_id}/spec",
                json={"spec": new_spec},
                headers=_auth(self.user),
            )
        detail = self.client.get(f"/api/v1/reports/{self.report_id}", headers=_auth(self.user))
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["data"]["name"], new_title)

    # L2-3: PUT /spec with invalid report_id → 400 or 404
    def test_l2_3_invalid_id_returns_error(self):
        with patch("backend.api.reports.build_report_html", return_value="<html>x</html>"):
            resp = self.client.put(
                "/api/v1/reports/not-a-uuid/spec",
                json={"spec": _minimal_spec("bad")},
                headers=_auth(self.user),
            )
        self.assertIn(resp.status_code, [400, 422], resp.text)

    # L2-4: build_report_html is called during spec update (HTML regenerated)
    def test_l2_4_html_regenerated_on_spec_update(self):
        """Verifies that build_report_html is invoked when updating spec."""
        new_spec = _minimal_spec("L2 HTML Regen Check")
        call_count = {"n": 0}

        def fake_build(**kwargs):
            call_count["n"] += 1
            return f"<html>regen-{call_count['n']}</html>"

        # Due to sys.path.insert(0, 'backend') at the top of this module, the
        # reports module may be loaded under both 'api.reports' and
        # 'backend.api.reports' as separate sys.modules entries. We must patch
        # the entry that the endpoint function's __globals__ actually refers to.
        _api_reports_mod = sys.modules.get("api.reports") or sys.modules.get("backend.api.reports")
        with patch.object(_api_reports_mod, "build_report_html", side_effect=fake_build):
            resp = self.client.put(
                f"/api/v1/reports/{self.report_id}/spec",
                json={"spec": new_spec},
                headers=_auth(self.user),
            )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertGreaterEqual(call_count["n"], 1, "build_report_html should be called at least once")


# ══════════════════════════════════════════════════════════════════════════════
# Section L3 — POST /reports/{id}/copilot creates conversation (5 tests)
# ══════════════════════════════════════════════════════════════════════════════

class TestL3Copilot(unittest.TestCase):
    """L3-1 … L3-5: Copilot endpoint creates a conversation with report context."""

    @classmethod
    def setUpClass(cls):
        cls.client = _make_client()
        cls.user = _make_user("l3_user", role_names=["analyst"])
        # Build a report to attach copilot to
        report_name = f"{_PREFIX}l3_copilot_report"
        with patch("backend.api.reports.build_report_html", return_value="<html>cp</html>"):
            resp = cls.client.post(
                "/api/v1/reports/build",
                json={"spec": _minimal_spec(report_name), "doc_type": "dashboard"},
                headers=_auth(cls.user),
            )
        if resp.status_code != 200:
            raise RuntimeError(f"setUpClass: build failed: {resp.text}")
        cls.report_id = resp.json()["data"]["report_id"]
        cls.report_name = report_name

    # L3-1: POST /reports/{id}/copilot returns success with conversation_id
    def test_l3_1_copilot_returns_conversation_id(self):
        resp = self.client.post(
            f"/api/v1/reports/{self.report_id}/copilot",
            json={},
            headers=_auth(self.user),
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertTrue(body.get("success"), body)
        self.assertIn("conversation_id", body["data"])
        conv_id = body["data"]["conversation_id"]
        # Valid UUID
        uuid.UUID(conv_id)

    # L3-2: Conversation created by copilot is retrievable via GET /conversations/{id}
    def test_l3_2_copilot_conversation_retrievable(self):
        resp = self.client.post(
            f"/api/v1/reports/{self.report_id}/copilot",
            json={},
            headers=_auth(self.user),
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        conv_id = resp.json()["data"]["conversation_id"]

        get_resp = self.client.get(f"/api/v1/conversations/{conv_id}", headers=_auth(self.user))
        self.assertEqual(get_resp.status_code, 200, get_resp.text)

    # L3-3: Conversation system_prompt contains the report name
    def test_l3_3_system_prompt_contains_report_name(self):
        resp = self.client.post(
            f"/api/v1/reports/{self.report_id}/copilot",
            json={},
            headers=_auth(self.user),
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        conv_id = resp.json()["data"]["conversation_id"]

        get_resp = self.client.get(f"/api/v1/conversations/{conv_id}", headers=_auth(self.user))
        self.assertEqual(get_resp.status_code, 200)
        conv_data = get_resp.json()
        # GET /conversations/{id} returns {"conversation": {...}, "messages": [...]}
        if isinstance(conv_data, dict) and "conversation" in conv_data:
            conv_data = conv_data["conversation"]
        elif isinstance(conv_data, dict) and "data" in conv_data:
            conv_data = conv_data["data"]
        system_prompt = conv_data.get("system_prompt", "")
        self.assertIn(self.report_name, system_prompt,
                      f"Expected report name {self.report_name!r} in system_prompt={system_prompt!r}")

    # L3-4: Custom title is used when creating a fresh copilot conversation
    def test_l3_4_custom_title(self):
        # The copilot endpoint has upsert semantics: it reuses an existing conversation
        # when one already exists for the same report+user. To test custom title we
        # must build a fresh report that has no pilot conversation yet.
        fresh_name = f"{_PREFIX}l3_custom_title_rpt"
        _api_mod = sys.modules.get("api.reports") or sys.modules.get("backend.api.reports")
        with patch.object(_api_mod, "build_report_html", return_value="<html>fresh</html>"):
            br = self.client.post(
                "/api/v1/reports/build",
                json={"spec": _minimal_spec(fresh_name), "doc_type": "dashboard"},
                headers=_auth(self.user),
            )
        self.assertEqual(br.status_code, 200, br.text)
        fresh_id = br.json()["data"]["report_id"]

        custom_title = f"{_PREFIX}custom_copilot_title"
        resp = self.client.post(
            f"/api/v1/reports/{fresh_id}/copilot",
            json={"title": custom_title},
            headers=_auth(self.user),
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        conv_id = resp.json()["data"]["conversation_id"]

        get_resp = self.client.get(f"/api/v1/conversations/{conv_id}", headers=_auth(self.user))
        self.assertEqual(get_resp.status_code, 200)
        conv_data = get_resp.json()
        # GET /conversations/{id} returns {"conversation": {...}, "messages": [...]}
        if isinstance(conv_data, dict) and "conversation" in conv_data:
            conv_data = conv_data["conversation"]
        elif isinstance(conv_data, dict) and "data" in conv_data:
            conv_data = conv_data["data"]
        self.assertEqual(conv_data.get("title"), custom_title)

    # L3-5: Copilot on non-existent report returns 404
    def test_l3_5_copilot_unknown_report_404(self):
        fake_id = str(uuid.uuid4())
        resp = self.client.post(
            f"/api/v1/reports/{fake_id}/copilot",
            json={},
            headers=_auth(self.user),
        )
        self.assertEqual(resp.status_code, 404, resp.text)


# ══════════════════════════════════════════════════════════════════════════════
# Section L4 — POST /scheduled-reports/ → task in list (5 tests)
# ══════════════════════════════════════════════════════════════════════════════

class TestL4ScheduledReports(unittest.TestCase):
    """L4-1 … L4-5: Create a scheduled report task and verify it persists."""

    @classmethod
    def setUpClass(cls):
        cls.client = _make_client()
        cls.user = _make_user("l4_user", role_names=["analyst"])

    def _create_schedule(self, name: str, doc_type: str = "dashboard",
                         cron: str = "0 9 * * 1",
                         channels=None) -> dict:
        payload = {
            "name": name,
            "doc_type": doc_type,
            "cron_expr": cron,
            "timezone": "Asia/Shanghai",
            "report_spec": _minimal_spec(name),
        }
        if channels is not None:
            payload["notify_channels"] = channels
        with patch("backend.api.scheduled_reports.add_or_update_job"):
            resp = self.client.post(
                "/api/v1/scheduled-reports/",
                json=payload,
                headers=_auth(self.user),
            )
        return resp

    # L4-1: POST /scheduled-reports/ returns success with id
    def test_l4_1_create_returns_id(self):
        name = f"{_PREFIX}l4_create"
        resp = self._create_schedule(name)
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertTrue(body.get("success"), body)
        self.assertIn("id", body["data"])
        uuid.UUID(body["data"]["id"])  # valid UUID

    # L4-2: Task appears in GET /scheduled-reports/ list
    def test_l4_2_task_in_list(self):
        name = f"{_PREFIX}l4_list"
        create_resp = self._create_schedule(name)
        self.assertEqual(create_resp.status_code, 200)
        schedule_id = create_resp.json()["data"]["id"]

        with patch("backend.api.scheduled_reports.add_or_update_job"):
            list_resp = self.client.get("/api/v1/scheduled-reports/", headers=_auth(self.user))
        self.assertEqual(list_resp.status_code, 200)
        items = list_resp.json()["data"]["items"]
        ids = [s["id"] for s in items]
        self.assertIn(schedule_id, ids)

    # L4-3: doc_type, cron_expr, and notify_channels are stored correctly
    def test_l4_3_fields_persisted(self):
        name = f"{_PREFIX}l4_fields"
        channels = [{"type": "email", "to": "test@example.com"}]
        resp = self._create_schedule(
            name,
            doc_type="document",
            cron="0 18 * * 5",
            channels=channels,
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()["data"]
        self.assertEqual(data["doc_type"], "document")
        self.assertEqual(data["cron_expr"], "0 18 * * 5")
        self.assertIsNotNone(data["notify_channels"])
        # channels should have at least one entry
        self.assertTrue(len(data["notify_channels"]) >= 1)

    # L4-4: Invalid cron expression returns 422
    def test_l4_4_invalid_cron_rejected(self):
        resp = self._create_schedule(f"{_PREFIX}l4_badcron", cron="not-a-cron")
        self.assertEqual(resp.status_code, 422, resp.text)

    # L4-5: DELETE /scheduled-reports/{id} removes the task
    def test_l4_5_delete_removes_task(self):
        name = f"{_PREFIX}l4_delete"
        create_resp = self._create_schedule(name)
        self.assertEqual(create_resp.status_code, 200)
        schedule_id = create_resp.json()["data"]["id"]

        with patch("backend.api.scheduled_reports.remove_job"):
            del_resp = self.client.delete(
                f"/api/v1/scheduled-reports/{schedule_id}",
                headers=_auth(self.user),
            )
        self.assertEqual(del_resp.status_code, 200, del_resp.text)
        self.assertTrue(del_resp.json().get("success"))

        # Verify it's gone from the list
        list_resp = self.client.get("/api/v1/scheduled-reports/", headers=_auth(self.user))
        ids = [s["id"] for s in list_resp.json()["data"]["items"]]
        self.assertNotIn(schedule_id, ids)


# ══════════════════════════════════════════════════════════════════════════════
# Section L5 — User isolation: User A's reports NOT visible to User B (5 tests)
# ══════════════════════════════════════════════════════════════════════════════

class TestL5UserIsolation(unittest.TestCase):
    """L5-1 … L5-5: Verify reports are isolated per user when ENABLE_AUTH=True."""

    @classmethod
    def setUpClass(cls):
        from backend.config.settings import settings
        cls.client = _make_client()
        # Both users have analyst role (which has reports:read/create)
        cls.user_a = _make_user("l5_user_a", role_names=["analyst"])
        cls.user_b = _make_user("l5_user_b", role_names=["analyst"])

        # Build a report as User A — MUST enable auth so the JWT is validated and
        # the report gets username=user_a.username (not the anonymous "default").
        cls.report_name_a = f"{_PREFIX}l5_report_a"
        _api_mod = sys.modules.get("api.reports") or sys.modules.get("backend.api.reports")
        with patch.object(settings, "enable_auth", True):
            with patch.object(_api_mod, "build_report_html", return_value="<html>a</html>"):
                resp = cls.client.post(
                    "/api/v1/reports/build",
                    json={"spec": _minimal_spec(cls.report_name_a), "doc_type": "dashboard"},
                    headers=_auth(cls.user_a),
                )
        if resp.status_code != 200:
            raise RuntimeError(f"setUpClass: could not build user_a report: {resp.text}")
        cls.report_id_a = resp.json()["data"]["report_id"]

    # L5-1: User A can see their own report in GET /reports
    def test_l5_1_user_a_sees_own_report(self):
        from backend.config.settings import settings
        # Auth must be enabled so the JWT is honoured and user_a's own reports are returned
        with patch.object(settings, "enable_auth", True):
            resp = self.client.get("/api/v1/reports", headers=_auth(self.user_a))
        self.assertEqual(resp.status_code, 200)
        ids = [r["id"] for r in resp.json()["data"]["items"]]
        self.assertIn(self.report_id_a, ids)

    # L5-2: User B does NOT see User A's report (with auth enabled)
    def test_l5_2_user_b_cannot_see_user_a_report(self):
        from backend.config.settings import settings
        with patch.object(settings, "enable_auth", True):
            resp = self.client.get("/api/v1/reports", headers=_auth(self.user_b))
        self.assertEqual(resp.status_code, 200)
        ids = [r["id"] for r in resp.json()["data"]["items"]]
        self.assertNotIn(
            self.report_id_a, ids,
            "User B should not see User A's report when auth is enabled",
        )

    # L5-3: User B gets 403 when accessing User A's report by ID (auth enabled)
    def test_l5_3_user_b_cannot_access_user_a_report_directly(self):
        from backend.config.settings import settings
        with patch.object(settings, "enable_auth", True):
            resp = self.client.get(
                f"/api/v1/reports/{self.report_id_a}",
                headers=_auth(self.user_b),
            )
        self.assertEqual(resp.status_code, 403, resp.text)

    # L5-4: User A can still access own report directly (auth enabled)
    def test_l5_4_user_a_can_access_own_report(self):
        from backend.config.settings import settings
        with patch.object(settings, "enable_auth", True):
            resp = self.client.get(
                f"/api/v1/reports/{self.report_id_a}",
                headers=_auth(self.user_a),
            )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["data"]["id"], self.report_id_a)

    # L5-5: User B gets 403 when trying to PUT /spec on User A's report
    def test_l5_5_user_b_cannot_update_user_a_spec(self):
        from backend.config.settings import settings
        with patch.object(settings, "enable_auth", True):
            with patch("backend.api.reports.build_report_html", return_value="<html>hack</html>"):
                resp = self.client.put(
                    f"/api/v1/reports/{self.report_id_a}/spec",
                    json={"spec": _minimal_spec("hacked")},
                    headers=_auth(self.user_b),
                )
        self.assertEqual(resp.status_code, 403, resp.text)


# ══════════════════════════════════════════════════════════════════════════════
# Section L6 — Frontend code inspection (6 tests, always pass, no DB)
# ══════════════════════════════════════════════════════════════════════════════

_FE_ROOT = os.path.join(os.path.dirname(__file__), "frontend", "src")


class TestL6FrontendCode(unittest.TestCase):
    """L6-1 … L6-6: Verify DataCenter frontend files and route registration."""

    # L6-1: DataCenterLayout.tsx exists and has sidebar menu items
    def test_l6_1_datacenter_layout_exists_with_menu(self):
        path = os.path.join(_FE_ROOT, "components", "DataCenterLayout.tsx")
        self.assertTrue(os.path.isfile(path), f"Missing: {path}")
        content = open(path, encoding="utf-8").read()
        # Should have the three nav items
        self.assertIn("/data-center/dashboards", content)
        self.assertIn("/data-center/documents", content)
        self.assertIn("/data-center/schedules", content)

    # L6-2: All three DataCenter page components exist
    def test_l6_2_datacenter_page_files_exist(self):
        pages_dir = os.path.join(_FE_ROOT, "pages")
        for fname in [
            "DataCenterDashboards.tsx",
            "DataCenterDocuments.tsx",
            "DataCenterSchedules.tsx",
        ]:
            path = os.path.join(pages_dir, fname)
            self.assertTrue(os.path.isfile(path), f"Missing page file: {path}")

    # L6-3: App.tsx registers all 4 /data-center routes
    def test_l6_3_app_routes_registered(self):
        app_path = os.path.join(_FE_ROOT, "App.tsx")
        self.assertTrue(os.path.isfile(app_path), f"Missing: {app_path}")
        content = open(app_path, encoding="utf-8").read()
        for route in [
            "/data-center",
            "/data-center/dashboards",
            "/data-center/documents",
            "/data-center/schedules",
        ]:
            self.assertIn(route, content, f"Route {route!r} not found in App.tsx")

    # L6-4: App.tsx imports all DataCenter components
    def test_l6_4_app_imports_datacenter_components(self):
        app_path = os.path.join(_FE_ROOT, "App.tsx")
        content = open(app_path, encoding="utf-8").read()
        for component in [
            "DataCenterLayout",
            "DataCenterDashboards",
            "DataCenterDocuments",
            "DataCenterSchedules",
        ]:
            self.assertIn(component, content, f"{component} not imported in App.tsx")

    # L6-5: DataCenterDashboards.tsx has "AI 助手" button
    def test_l6_5_dashboards_has_ai_assistant_button(self):
        path = os.path.join(_FE_ROOT, "pages", "DataCenterDashboards.tsx")
        content = open(path, encoding="utf-8").read()
        self.assertIn("AI 助手", content, "Expected 'AI 助手' button text in DataCenterDashboards.tsx")

    # L6-6: ConversationSidebar.tsx has 数据管理 button navigating to /data-center
    def test_l6_6_conversation_sidebar_has_datacenter_button(self):
        path = os.path.join(_FE_ROOT, "components", "chat", "ConversationSidebar.tsx")
        self.assertTrue(os.path.isfile(path), f"Missing: {path}")
        content = open(path, encoding="utf-8").read()
        self.assertIn("数据管理", content, "Expected '数据管理' in ConversationSidebar.tsx")
        self.assertIn("/data-center", content, "Expected '/data-center' link in ConversationSidebar.tsx")
        # Sidebar may navigate via window.open OR React Router navigate() — accept either
        self.assertTrue(
            "window.open" in content or "navigate" in content,
            "Expected navigation call (window.open or navigate) in ConversationSidebar.tsx",
        )


# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    unittest.main(verbosity=2)
