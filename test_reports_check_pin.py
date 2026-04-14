"""
test_reports_check_pin.py — POST /reports/check-pin-status-batch 单元测试套件
=============================================================================

覆盖范围：
  A段 (6)  — 后端 API 功能测试（需 DB）
               A1: 未固定文件 → pinned=false
               A2: 固定后再检查 → pinned=true，含 report_id/refresh_token/doc_type/name
               A3: 路径越界 → 静默返回 pinned=false（不抛 4xx）
               A4: 非 superadmin 查他人文件 → 静默返回 pinned=false
               A5: superadmin 可查任意用户文件 → 返回真实状态
               A6: 无 Auth token（ENABLE_AUTH=true 场景） → 401
  B段 (4)  — 批量语义与边界
               B1: 批量请求 → 一次 IN 查询，结果与请求顺序一致
               B2: 部分命中（混合已固定/未固定） → 分别返回 true/false
               B3: 空列表 → 返回空 results
               B4: 同一文件 pin 后幂等检查 → 始终返回同一 report_id
  C段 (4)  — 前端代码静态分析（无需 DB）
               C1: ChatMessages.tsx 含 checkPinStatusBatch 调用
               C2: ChatMessages.tsx 含 pinnedOverrides state
               C3: DataCenterCopilot.tsx 含 PilotFilesDisplay 组件
               C4: DataCenterCopilot.tsx 含 attachFileCardToLastMessage 逻辑
               C5: ReportViewerPage.tsx 传 contextRefreshToken
               C6: ReportPreviewModal.tsx PilotContext 含 contextRefreshToken 字段

总计: 14 个测试用例
"""

from __future__ import annotations

import os
import sys
import uuid
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

# ── 路径 & 环境初始化 ────────────────────────────────────────────────────────
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "backend"))
os.environ.setdefault("POSTGRES_PASSWORD", "Sgp013013")
os.environ.setdefault("POSTGRES_HOST", "localhost")
os.environ.setdefault("ENABLE_AUTH", "False")

from test_utils import make_test_username  # noqa: E402

_PREFIX = f"_cp_{uuid.uuid4().hex[:6]}_"

# ── 前端文件路径 ──────────────────────────────────────────────────────────────
_FRONTEND_ROOT = Path(__file__).parent / "frontend" / "src"
_CHAT_MSGS_FILE = _FRONTEND_ROOT / "components" / "chat" / "ChatMessages.tsx"
_COPILOT_FILE = _FRONTEND_ROOT / "components" / "DataCenterCopilot.tsx"
_PREVIEW_FILE = _FRONTEND_ROOT / "components" / "chat" / "ReportPreviewModal.tsx"
_VIEWER_FILE = _FRONTEND_ROOT / "pages" / "ReportViewerPage.tsx"
_API_FILE = _FRONTEND_ROOT / "services" / "chatApi.ts"

# ── auth patcher ──────────────────────────────────────────────────────────────
_auth_patcher = None


def setup_module(_=None):
    global _auth_patcher
    from backend.config.settings import settings
    _auth_patcher = patch.object(settings, "enable_auth", False)
    _auth_patcher.start()


def teardown_module(_=None):
    global _auth_patcher
    if _auth_patcher:
        _auth_patcher.stop()
    _cleanup_test_data()


# ── DB helpers ────────────────────────────────────────────────────────────────

def _db():
    from backend.config.database import SessionLocal
    return SessionLocal()


_g_db = _db()


def _make_user(suffix="", is_superadmin=False, role_name="analyst"):
    from backend.models.user import User
    from backend.models.role import Role
    from backend.models.user_role import UserRole
    from backend.core.auth.password import hash_password

    username = f"{_PREFIX}{suffix or uuid.uuid4().hex[:6]}"
    u = User(
        username=username,
        display_name=f"CheckPin Test {suffix}",
        hashed_password=hash_password("Test1234!"),
        auth_source="local",
        is_active=True,
        is_superadmin=is_superadmin,
    )
    _g_db.add(u)
    _g_db.flush()
    role = _g_db.query(Role).filter(Role.name == role_name).first()
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


def _make_client():
    from backend.main import app
    from fastapi.testclient import TestClient
    return TestClient(app, raise_server_exceptions=False)


def _make_report(owner_username, doc_type="dashboard", file_path=None):
    """创建一个已固定的报表记录（直接写 DB，不调用 pin API）"""
    from backend.models.report import Report
    name = f"{_PREFIX}rpt_{uuid.uuid4().hex[:6]}"
    norm_path = file_path or f"{owner_username}/reports/{name}.html"
    r = Report(
        name=name,
        doc_type=doc_type,
        theme="light",
        charts=[{"type": "bar", "title": "测试"}],
        filters=[],
        username=owner_username,
        refresh_token=uuid.uuid4().hex,
        report_file_path=norm_path,
        summary_status="skipped",
    )
    _g_db.add(r)
    _g_db.commit()
    _g_db.refresh(r)
    return r


def _cleanup_test_data():
    try:
        from backend.models.user import User
        from backend.models.report import Report
        _g_db.query(User).filter(User.username.like(f"{_PREFIX}%")).delete(synchronize_session=False)
        _g_db.query(Report).filter(Report.name.like(f"{_PREFIX}%")).delete(synchronize_session=False)
        _g_db.commit()
    except Exception:
        try:
            _g_db.rollback()
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# A段 — 后端 API 功能（需 DB）
# ─────────────────────────────────────────────────────────────────────────────

class TestACheckPinStatusBatch(unittest.TestCase):
    """POST /reports/check-pin-status-batch 基础功能测试"""

    @classmethod
    def setUpClass(cls):
        cls.user = _make_user("alice")
        cls.other_user = _make_user("bob")
        cls.superadmin = _make_user("sa", is_superadmin=True, role_name="admin")
        cls.client = _make_client()

    def _post(self, file_paths, user=None):
        u = user or self.user
        return self.client.post(
            "/api/v1/reports/check-pin-status-batch",
            json={"file_paths": file_paths},
            headers=_auth(u),
        )

    def test_A1_unpinned_file_returns_pinned_false(self):
        """未固定文件 → pinned=false"""
        fake_path = f"{self.user.username}/reports/nonexistent_{uuid.uuid4().hex[:8]}.html"
        resp = self._post([fake_path])
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["success"])
        results = data["data"]["results"]
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["file_path"], fake_path)
        self.assertFalse(results[0]["pinned"])

    def test_A2_pinned_file_returns_full_info(self):
        """已固定文件 → pinned=true，含 report_id/refresh_token/doc_type/name"""
        rpt = _make_report(self.user.username, doc_type="dashboard")
        resp = self._post([rpt.report_file_path])
        self.assertEqual(resp.status_code, 200)
        results = resp.json()["data"]["results"]
        self.assertEqual(len(results), 1)
        r = results[0]
        self.assertTrue(r["pinned"], "已固定文件应返回 pinned=true")
        self.assertEqual(r["report_id"], str(rpt.id))
        self.assertEqual(r["refresh_token"], rpt.refresh_token)
        self.assertEqual(r["doc_type"], "dashboard")
        self.assertIn("name", r)

    def test_A3_path_traversal_returns_pinned_false_silently(self):
        """路径越界（目录穿越）→ 静默返回 pinned=false，不抛 4xx"""
        bad_paths = [
            "../../etc/passwd",
            "../../../secrets.txt",
            "%2e%2e/other/file.html",
        ]
        resp = self._post(bad_paths)
        self.assertEqual(resp.status_code, 200, "路径越界应静默处理，不返回错误")
        results = resp.json()["data"]["results"]
        for r in results:
            self.assertFalse(r["pinned"], f"越界路径应返回 pinned=false: {r['file_path']}")

    def test_A4_non_superadmin_cannot_check_other_users_files(self):
        """非 superadmin 查他人文件 → 静默返回 pinned=false（不泄露存在性）

        注意：ENABLE_AUTH=False 时所有请求以匿名 superadmin 运行，用户隔离不生效。
        此测试需局部开启 ENABLE_AUTH=True，让 JWT 中的 alice 身份被解析出来，
        才能触发 path_parts[0] != username 的用户隔离检查。
        """
        from unittest.mock import patch
        # 以 bob 的名义固定一个文件
        rpt = _make_report(self.other_user.username, doc_type="document")
        # 局部开启 auth，alice 的 JWT 会被解析为非超管用户
        with patch("backend.config.settings.settings.enable_auth", True):
            resp = self._post([rpt.report_file_path], user=self.user)
        self.assertEqual(resp.status_code, 200)
        results = resp.json()["data"]["results"]
        self.assertEqual(len(results), 1)
        self.assertFalse(results[0]["pinned"], "非 superadmin 不应看到他人文件的固定状态")

    def test_A5_superadmin_can_check_any_users_files(self):
        """superadmin 查询任意用户文件 → 返回真实状态"""
        rpt = _make_report(self.other_user.username, doc_type="dashboard")
        resp = self._post([rpt.report_file_path], user=self.superadmin)
        self.assertEqual(resp.status_code, 200)
        results = resp.json()["data"]["results"]
        self.assertTrue(results[0]["pinned"], "superadmin 应能看到任意用户文件的真实固定状态")
        self.assertEqual(results[0]["report_id"], str(rpt.id))

    def test_A6_requires_auth_when_auth_enabled(self):
        """ENABLE_AUTH=true 且无 token → 401"""
        with patch("backend.config.settings.settings.enable_auth", True):
            resp = self.client.post(
                "/api/v1/reports/check-pin-status-batch",
                json={"file_paths": ["default/reports/test.html"]},
            )
        # 401 或 403（无 token）
        self.assertIn(resp.status_code, [401, 403], "无 Auth token 时应返回 4xx")


# ─────────────────────────────────────────────────────────────────────────────
# B段 — 批量语义与边界
# ─────────────────────────────────────────────────────────────────────────────

class TestBBatchSemantics(unittest.TestCase):
    """批量检测语义：顺序一致性、混合结果、边界情况"""

    @classmethod
    def setUpClass(cls):
        cls.user = _make_user("charlie")
        cls.client = _make_client()

    def _post(self, file_paths):
        return self.client.post(
            "/api/v1/reports/check-pin-status-batch",
            json={"file_paths": file_paths},
            headers=_auth(self.user),
        )

    def test_B1_results_match_request_order(self):
        """results 顺序应与请求 file_paths 顺序一致"""
        rpt = _make_report(self.user.username)
        fake = f"{self.user.username}/reports/fake_{uuid.uuid4().hex[:6]}.html"
        # 顺序：已固定, 不存在, 已固定
        paths = [rpt.report_file_path, fake, rpt.report_file_path]
        resp = self._post(paths)
        self.assertEqual(resp.status_code, 200)
        results = resp.json()["data"]["results"]
        self.assertEqual(len(results), 3)
        self.assertEqual(results[0]["file_path"], paths[0])
        self.assertEqual(results[1]["file_path"], paths[1])
        self.assertEqual(results[2]["file_path"], paths[2])
        self.assertTrue(results[0]["pinned"])
        self.assertFalse(results[1]["pinned"])
        self.assertTrue(results[2]["pinned"])

    def test_B2_mixed_pinned_and_unpinned(self):
        """批量请求包含混合已固定/未固定 → 分别返回 true/false"""
        rpt1 = _make_report(self.user.username, doc_type="dashboard")
        rpt2 = _make_report(self.user.username, doc_type="document")
        fake = f"{self.user.username}/reports/notexist_{uuid.uuid4().hex[:6]}.html"

        resp = self._post([rpt1.report_file_path, fake, rpt2.report_file_path])
        self.assertEqual(resp.status_code, 200)
        results = resp.json()["data"]["results"]
        self.assertTrue(results[0]["pinned"])
        self.assertFalse(results[1]["pinned"])
        self.assertTrue(results[2]["pinned"])
        self.assertEqual(results[0]["doc_type"], "dashboard")
        self.assertEqual(results[2]["doc_type"], "document")

    def test_B3_empty_list_returns_empty_results(self):
        """空 file_paths → results 为空列表"""
        resp = self._post([])
        self.assertEqual(resp.status_code, 200)
        results = resp.json()["data"]["results"]
        self.assertEqual(results, [], "空列表应返回空 results")

    def test_B4_idempotent_report_id_consistent(self):
        """同一文件多次检查 → report_id 始终一致（幂等性）"""
        rpt = _make_report(self.user.username)
        resp1 = self._post([rpt.report_file_path])
        resp2 = self._post([rpt.report_file_path])
        r1 = resp1.json()["data"]["results"][0]
        r2 = resp2.json()["data"]["results"][0]
        self.assertEqual(r1["report_id"], r2["report_id"], "多次检查同一文件应返回相同 report_id")
        self.assertEqual(r1["refresh_token"], r2["refresh_token"])


# ─────────────────────────────────────────────────────────────────────────────
# C段 — 前端代码静态分析（无需 DB）
# ─────────────────────────────────────────────────────────────────────────────

class TestCFrontendCodeAnalysis(unittest.TestCase):
    """前端代码静态分析：验证关键逻辑与接口已按设计实现"""

    def _read(self, path: Path) -> str:
        return path.read_text(encoding="utf-8")

    def test_C1_chat_messages_calls_check_pin_status_batch(self):
        """ChatMessages.tsx 应包含 checkPinStatusBatch 调用"""
        code = self._read(_CHAT_MSGS_FILE)
        self.assertIn(
            "checkPinStatusBatch",
            code,
            "FileDownloadCards 应调用 reportApi.checkPinStatusBatch 做批量 pin 状态检测",
        )

    def test_C2_chat_messages_has_pinned_overrides_state(self):
        """ChatMessages.tsx 应声明 pinnedOverrides 状态"""
        code = self._read(_CHAT_MSGS_FILE)
        self.assertIn(
            "pinnedOverrides",
            code,
            "FileDownloadCards 应有 pinnedOverrides 本地状态用于跨对话 pin 覆盖",
        )

    def test_C3_copilot_has_pilot_files_display_component(self):
        """DataCenterCopilot.tsx 应包含 PilotFilesDisplay 组件"""
        code = self._read(_COPILOT_FILE)
        self.assertIn(
            "PilotFilesDisplay",
            code,
            "DataCenterCopilot 应有 PilotFilesDisplay 组件展示 Pilot 中修改后的文件",
        )

    def test_C4_copilot_attaches_file_card_on_spec_updated(self):
        """DataCenterCopilot.tsx 应在 specUpdated 后挂载文件卡片"""
        code = self._read(_COPILOT_FILE)
        self.assertIn(
            "attachFileCardToLastMessage",
            code,
            "handleSend 中应在 specUpdated=true 时调用 attachFileCardToLastMessage",
        )
        # 确认 files 字段挂载到 LocalMessage
        self.assertIn("files:", code, "LocalMessage 应有 files 字段")

    def test_C5_report_viewer_passes_context_refresh_token(self):
        """ReportViewerPage.tsx 应传 contextRefreshToken 给 DataCenterCopilotContent"""
        code = self._read(_VIEWER_FILE)
        self.assertIn(
            "contextRefreshToken",
            code,
            "ReportViewerPage 应将 URL token 作为 contextRefreshToken 传给 DataCenterCopilotContent",
        )

    def test_C6_pilot_context_has_context_refresh_token_field(self):
        """ReportPreviewModal.tsx PilotContext 接口应包含 contextRefreshToken 字段"""
        code = self._read(_PREVIEW_FILE)
        self.assertIn(
            "contextRefreshToken",
            code,
            "PilotContext 接口应新增 contextRefreshToken 字段，支持 token 回退",
        )

    def test_C7_chat_api_has_check_pin_status_batch(self):
        """chatApi.ts 应导出 checkPinStatusBatch 方法"""
        code = self._read(_API_FILE)
        self.assertIn(
            "checkPinStatusBatch",
            code,
            "reportApi 应包含 checkPinStatusBatch 方法",
        )
        self.assertIn(
            "PinStatusResult",
            code,
            "chatApi.ts 应导出 PinStatusResult 接口",
        )

    def test_C8_copilot_local_message_has_files_field(self):
        """DataCenterCopilot.tsx LocalMessage 接口应有 files?: FileInfo[] 字段"""
        code = self._read(_COPILOT_FILE)
        # 检查 FileInfo 引入
        self.assertIn("FileInfo", code, "DataCenterCopilot 应引入 FileInfo 类型")
        # 检查 files 字段声明
        self.assertIn("files?:", code, "LocalMessage 应声明 files?: FileInfo[]")

    def test_C9_pilot_files_display_has_preview_and_pin_buttons(self):
        """PilotFilesDisplay 应包含预览按钮和固定按钮逻辑"""
        code = self._read(_COPILOT_FILE)
        self.assertIn("EyeOutlined", code, "PilotFilesDisplay 应有预览按钮图标")
        self.assertIn("PushpinOutlined", code, "PilotFilesDisplay 应有固定按钮图标")
        self.assertIn("CheckCircleOutlined", code, "PilotFilesDisplay 应有已固定按钮图标")
        self.assertIn("已生成固定", code, "PilotFilesDisplay 应有已固定状态文案")

    def test_C10_pin_override_also_updated_on_manual_pin(self):
        """ChatMessages.tsx 手动 pin 后应同步更新 pinnedOverrides（立即切换按钮状态）"""
        code = self._read(_CHAT_MSGS_FILE)
        # setPinnedOverrides 应在 handlePin 成功路径中调用
        self.assertIn(
            "setPinnedOverrides",
            code,
            "handlePin 成功后应调用 setPinnedOverrides 立即更新本地状态",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
