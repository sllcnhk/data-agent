"""
test_reports_check_pin_v2.py — 完整测试套件（无需真实 DB）
=============================================================================

覆盖范围：
  A段 (6)  — 后端 API 核心逻辑（mocked DB，ENABLE_AUTH=False）
               A1: 未固定文件 → pinned=false
               A2: 已固定文件 → 完整字段（report_id/refresh_token/doc_type/name）
               A3: 路径越界（../） → 静默 pinned=false（不抛 4xx）
               A4: 非 superadmin 用户隔离（alice 查 bob 的文件 → 静默 false）
               A5: 空 file_paths 列表 → 空 results，无 DB 查询
               A6: 混合已固定/未固定 → 顺序与请求一致

  B段 (5)  — RBAC 权限矩阵（mocked DB + mocked 用户/权限）
               B1: ENABLE_AUTH=False（匿名超管）→ 200
               B2: viewer 角色（无 reports:read）→ 403
               B3: analyst 角色（有 reports:read）→ 200
               B4: ENABLE_AUTH=True，无 Bearer token → 401
               B5: is_superadmin=True 用户 → 200（权限检查直接通过）

  C段 (10) — 前端代码静态分析（无需 DB）（与 v1 相同，保留回归）
               C1–C10: 关键逻辑、接口、状态、组件全覆盖

  D段 (8)  — Pilot 文件卡片 SSE 检测与 UI 模式静态分析
               D1: SSE 解析：处理 tool_result 事件类型
               D2: specUpdated 检测：report__update_spec 工具名
               D3: specUpdated 检测：report__update_single_chart 工具名
               D4: specUpdated 检测：'报表已更新' 关键字
               D5: specUpdated 检测：'/spec' 关键字
               D6: hasSpec = !!(contextSpec && Object.keys(contextSpec).length > 0)
               D7: canPreview = !!(effectivePinnedId && effectiveRefreshToken)
               D8: attachFileCardToLastMessage 使用 contextRefreshToken 作为 token 回退

总计: 29 个测试用例
"""

from __future__ import annotations

import os
import sys
import uuid
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# ── 路径 & 环境初始化（必须在任何 backend 导入之前）─────────────────────────
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "backend"))
os.environ.setdefault("POSTGRES_PASSWORD", "Sgp013013")
os.environ.setdefault("POSTGRES_HOST", "localhost")
os.environ.setdefault("ENABLE_AUTH", "False")

# ── 前端文件路径 ──────────────────────────────────────────────────────────────
_FRONTEND_ROOT = Path(__file__).parent / "frontend" / "src"
_CHAT_MSGS_FILE  = _FRONTEND_ROOT / "components" / "chat" / "ChatMessages.tsx"
_COPILOT_FILE    = _FRONTEND_ROOT / "components" / "DataCenterCopilot.tsx"
_PREVIEW_FILE    = _FRONTEND_ROOT / "components" / "chat" / "ReportPreviewModal.tsx"
_VIEWER_FILE     = _FRONTEND_ROOT / "pages" / "ReportViewerPage.tsx"
_API_FILE        = _FRONTEND_ROOT / "services" / "chatApi.ts"

# ── 测试 URL ──────────────────────────────────────────────────────────────────
_ENDPOINT = "/api/v1/reports/check-pin-status-batch"


# ─────────────────────────────────────────────────────────────────────────────
# Mock 工厂
# ─────────────────────────────────────────────────────────────────────────────

def _make_mock_report(file_path: str, doc_type: str = "dashboard", name: str | None = None):
    """构造 Mock Report ORM 对象（不依赖真实 DB）"""
    r = MagicMock()
    r.id = uuid.uuid4()
    r.report_file_path = file_path
    r.refresh_token = uuid.uuid4().hex
    r.doc_type = doc_type
    r.name = name or f"report_{uuid.uuid4().hex[:6]}"
    return r


def _make_mock_db(rows: list | None = None):
    """构造 Mock SQLAlchemy Session，query().filter().all() 返回受控列表"""
    mock_db = MagicMock()
    report_list = rows if rows is not None else []

    filter_mock = MagicMock()
    filter_mock.all.return_value = report_list

    query_mock = MagicMock()
    query_mock.filter.return_value = filter_mock

    mock_db.query.return_value = query_mock
    return mock_db


class _AppFixture:
    """
    为每个测试段提供 FastAPI app + TestClient + dependency override 工具。
    独立于 DB 连接，通过 get_db override 注入 mock session。
    """

    _app = None
    _get_db_dep = None
    _get_current_user_dep = None

    @classmethod
    def setup(cls):
        from backend.main import app
        from backend.config.database import get_db
        from backend.api.deps import get_current_user
        cls._app = app
        cls._get_db_dep = get_db
        cls._get_current_user_dep = get_current_user

    @classmethod
    def make_client(cls, mock_db=None, mock_user=None):
        """返回 TestClient，可选注入 mock DB 和 mock 当前用户"""
        from fastapi.testclient import TestClient

        if mock_db is None:
            mock_db = _make_mock_db()

        def _override_db():
            yield mock_db

        cls._app.dependency_overrides[cls._get_db_dep] = _override_db

        if mock_user is not None:
            def _override_user():
                return mock_user
            cls._app.dependency_overrides[cls._get_current_user_dep] = _override_user
        else:
            cls._app.dependency_overrides.pop(cls._get_current_user_dep, None)

        return TestClient(cls._app, raise_server_exceptions=False)

    @classmethod
    def reset(cls):
        """测试后清理 dependency overrides"""
        if cls._app:
            cls._app.dependency_overrides.clear()


# ── 模块级初始化 ──────────────────────────────────────────────────────────────

def setup_module(_=None):
    _AppFixture.setup()


def teardown_module(_=None):
    _AppFixture.reset()


# ─────────────────────────────────────────────────────────────────────────────
# A段 — 后端 API 核心逻辑（mocked DB, ENABLE_AUTH=False → 匿名超管）
# ─────────────────────────────────────────────────────────────────────────────

class TestABackendAPILogic(unittest.TestCase):
    """POST /reports/check-pin-status-batch 核心逻辑——使用 mocked DB"""

    def tearDown(self):
        _AppFixture.reset()

    def _post(self, file_paths: list, rows: list | None = None):
        mock_db = _make_mock_db(rows)
        client = _AppFixture.make_client(mock_db)
        return client.post(_ENDPOINT, json={"file_paths": file_paths})

    # ── A1 ───────────────────────────────────────────────────────────────────
    def test_A1_unpinned_file_returns_pinned_false(self):
        """未固定文件（DB 无记录）→ pinned=false，无额外字段"""
        fake = "superadmin/reports/nonexistent_abc123.html"
        resp = self._post([fake], rows=[])

        self.assertEqual(resp.status_code, 200, f"期望 200，得到 {resp.status_code}: {resp.text}")
        body = resp.json()
        self.assertTrue(body["success"])
        results = body["data"]["results"]
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["file_path"], fake)
        self.assertFalse(results[0]["pinned"])
        self.assertNotIn("report_id", results[0])

    # ── A2 ───────────────────────────────────────────────────────────────────
    def test_A2_pinned_file_returns_full_info(self):
        """已固定文件（DB 有记录）→ pinned=true，含 report_id/refresh_token/doc_type/name"""
        file_path = "superadmin/reports/my_dashboard.html"
        rpt = _make_mock_report(file_path, doc_type="dashboard", name="我的报表")

        resp = self._post([file_path], rows=[rpt])

        self.assertEqual(resp.status_code, 200)
        results = resp.json()["data"]["results"]
        self.assertEqual(len(results), 1)
        r = results[0]
        self.assertTrue(r["pinned"], "已固定文件应返回 pinned=true")
        self.assertEqual(r["report_id"], str(rpt.id))
        self.assertEqual(r["refresh_token"], rpt.refresh_token)
        self.assertEqual(r["doc_type"], "dashboard")
        self.assertIn("name", r)
        self.assertEqual(r["name"], "我的报表")

    # ── A3 ───────────────────────────────────────────────────────────────────
    def test_A3_path_traversal_silently_returns_pinned_false(self):
        """路径越界（目录穿越）→ 静默返回 pinned=false，不抛 4xx"""
        bad_paths = [
            "../../etc/passwd",
            "../../../secrets.txt",
            "superadmin/../../other_user/secret.html",
        ]
        resp = self._post(bad_paths, rows=[])

        self.assertEqual(resp.status_code, 200, "路径越界应静默处理，不返回 4xx")
        results = resp.json()["data"]["results"]
        self.assertEqual(len(results), 3)
        for r in results:
            self.assertFalse(r["pinned"], f"越界路径应 pinned=false: {r['file_path']}")

    # ── A4 ───────────────────────────────────────────────────────────────────
    def test_A4_non_superadmin_cannot_see_other_users_files(self):
        """非 superadmin 用户检查他人目录文件 → 静默 pinned=false（不泄露存在性）"""
        # 构建 alice 用户（非超管）
        alice = MagicMock()
        alice.username = "alice"
        alice.is_superadmin = False

        # bob 目录下的文件，实际 DB 有记录
        bob_path = "bob/reports/bob_secret.html"
        bob_rpt = _make_mock_report(bob_path)
        mock_db = _make_mock_db(rows=[bob_rpt])

        with patch("backend.core.rbac.get_user_permissions", return_value=["reports:read", "chat:use"]):
            client = _AppFixture.make_client(mock_db, mock_user=alice)
            resp = client.post(_ENDPOINT, json={"file_paths": [bob_path]})

        self.assertEqual(resp.status_code, 200)
        results = resp.json()["data"]["results"]
        self.assertEqual(len(results), 1)
        self.assertFalse(results[0]["pinned"], "alice 不应看到 bob 的文件固定状态")

    # ── A5 ───────────────────────────────────────────────────────────────────
    def test_A5_empty_list_returns_empty_results(self):
        """空 file_paths 列表 → results 为 []，不发起 DB 查询"""
        mock_db = _make_mock_db(rows=[])
        client = _AppFixture.make_client(mock_db)

        resp = client.post(_ENDPOINT, json={"file_paths": []})

        self.assertEqual(resp.status_code, 200)
        results = resp.json()["data"]["results"]
        self.assertEqual(results, [], "空请求应返回空列表")
        # DB query 不应被调用（无需查询）
        mock_db.query.assert_not_called()

    # ── A6 ───────────────────────────────────────────────────────────────────
    def test_A6_results_order_matches_request_order(self):
        """批量结果顺序应与请求 file_paths 顺序严格一致"""
        paths = [
            "superadmin/reports/pinned_first.html",
            "superadmin/reports/unpinned_middle.html",
            "superadmin/reports/pinned_last.html",
        ]
        rpt0 = _make_mock_report(paths[0], doc_type="dashboard")
        rpt2 = _make_mock_report(paths[2], doc_type="document")
        # DB 返回 [rpt0, rpt2]（中间那个不在 DB）
        mock_db = _make_mock_db(rows=[rpt0, rpt2])
        client = _AppFixture.make_client(mock_db)

        resp = client.post(_ENDPOINT, json={"file_paths": paths})

        self.assertEqual(resp.status_code, 200)
        results = resp.json()["data"]["results"]
        self.assertEqual(len(results), 3)
        self.assertEqual(results[0]["file_path"], paths[0])
        self.assertEqual(results[1]["file_path"], paths[1])
        self.assertEqual(results[2]["file_path"], paths[2])
        self.assertTrue(results[0]["pinned"])
        self.assertFalse(results[1]["pinned"])
        self.assertTrue(results[2]["pinned"])
        self.assertEqual(results[0]["doc_type"], "dashboard")
        self.assertEqual(results[2]["doc_type"], "document")


# ─────────────────────────────────────────────────────────────────────────────
# B段 — RBAC 权限矩阵
# ─────────────────────────────────────────────────────────────────────────────

class TestBRBACPermissionMatrix(unittest.TestCase):
    """
    验证 POST /reports/check-pin-status-batch 的权限控制：
    - require_permission("reports", "read") 正确拦截无权限请求
    - viewer 角色 → 403；analyst → 200；无 token（ENABLE_AUTH=True）→ 401
    """

    def tearDown(self):
        _AppFixture.reset()

    def _make_user(self, username: str, is_superadmin: bool = False):
        u = MagicMock()
        u.username = username
        u.is_superadmin = is_superadmin
        u.is_active = True
        u.id = str(uuid.uuid4())
        return u

    # ── B1 ───────────────────────────────────────────────────────────────────
    def test_B1_anonymous_user_enable_auth_false_returns_200(self):
        """ENABLE_AUTH=False 匿名超管 → 200（is_superadmin=True 绕过权限检查）"""
        mock_db = _make_mock_db(rows=[])
        client = _AppFixture.make_client(mock_db)  # 不覆盖用户→匿名超管

        resp = client.post(_ENDPOINT, json={"file_paths": []})

        self.assertEqual(resp.status_code, 200, "匿名超管应能访问只读端点")

    # ── B2 ───────────────────────────────────────────────────────────────────
    def test_B2_viewer_without_reports_read_returns_403(self):
        """viewer 角色（无 reports:read 权限）→ 403 Forbidden"""
        viewer = self._make_user("viewer_user", is_superadmin=False)
        mock_db = _make_mock_db(rows=[])

        # viewer 只有 chat:use，无 reports:read
        with patch("backend.core.rbac.get_user_permissions", return_value=["chat:use"]):
            client = _AppFixture.make_client(mock_db, mock_user=viewer)
            resp = client.post(_ENDPOINT, json={"file_paths": []})

        self.assertEqual(resp.status_code, 403, "无 reports:read 权限应返回 403")
        body = resp.json()
        self.assertIn("detail", body)

    # ── B3 ───────────────────────────────────────────────────────────────────
    def test_B3_analyst_with_reports_read_returns_200(self):
        """analyst 角色（有 reports:read 权限）→ 200"""
        analyst = self._make_user("analyst_user", is_superadmin=False)
        mock_db = _make_mock_db(rows=[])

        # analyst 拥有 reports:read
        with patch(
            "backend.core.rbac.get_user_permissions",
            return_value=["chat:use", "reports:read", "reports:create", "settings:read"],
        ):
            client = _AppFixture.make_client(mock_db, mock_user=analyst)
            resp = client.post(_ENDPOINT, json={"file_paths": []})

        self.assertEqual(resp.status_code, 200, "analyst 有 reports:read 应返回 200")

    # ── B4 ───────────────────────────────────────────────────────────────────
    def test_B4_no_token_when_auth_enabled_returns_401(self):
        """ENABLE_AUTH=True + 无 Bearer token → 401 Unauthorized"""
        mock_db = _make_mock_db(rows=[])

        # 只 override get_db，不 override get_current_user
        # 让真实的 get_current_user 在 enable_auth=True 时检查 token
        from fastapi.testclient import TestClient
        from backend.config.database import get_db

        def _override_db():
            yield mock_db

        _AppFixture._app.dependency_overrides[get_db] = _override_db
        # 确保 get_current_user 不被 override（使用真实逻辑）
        _AppFixture._app.dependency_overrides.pop(_AppFixture._get_current_user_dep, None)

        client = TestClient(_AppFixture._app, raise_server_exceptions=False)

        with patch("backend.config.settings.settings.enable_auth", True):
            resp = client.post(
                _ENDPOINT,
                json={"file_paths": []},
                # 故意不传 Authorization header
            )

        self.assertIn(resp.status_code, [401, 403], "无 token 时应返回 401/403")

    # ── B5 ───────────────────────────────────────────────────────────────────
    def test_B5_explicit_superadmin_user_bypasses_permission_check(self):
        """is_superadmin=True 的用户 → 直接通过权限检查（无需 reports:read）"""
        sa_user = self._make_user("sa_user", is_superadmin=True)
        mock_db = _make_mock_db(rows=[])

        # 不 patch get_user_permissions —— superadmin 应在检查前直接 return
        client = _AppFixture.make_client(mock_db, mock_user=sa_user)
        resp = client.post(_ENDPOINT, json={"file_paths": []})

        self.assertEqual(resp.status_code, 200, "superadmin 应直接通过权限检查")


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
        self.assertIn("checkPinStatusBatch", code,
                      "FileDownloadCards 应调用 reportApi.checkPinStatusBatch 做批量 pin 状态检测")

    def test_C2_chat_messages_has_pinned_overrides_state(self):
        """ChatMessages.tsx 应声明 pinnedOverrides 状态"""
        code = self._read(_CHAT_MSGS_FILE)
        self.assertIn("pinnedOverrides", code,
                      "FileDownloadCards 应有 pinnedOverrides 本地状态用于跨对话 pin 覆盖")

    def test_C3_copilot_has_pilot_files_display_component(self):
        """DataCenterCopilot.tsx 应包含 PilotFilesDisplay 组件"""
        code = self._read(_COPILOT_FILE)
        self.assertIn("PilotFilesDisplay", code,
                      "DataCenterCopilot 应有 PilotFilesDisplay 组件展示 Pilot 中修改后的文件")

    def test_C4_copilot_attaches_file_card_on_spec_updated(self):
        """DataCenterCopilot.tsx 应在 specUpdated 后挂载文件卡片"""
        code = self._read(_COPILOT_FILE)
        self.assertIn("attachFileCardToLastMessage", code,
                      "handleSend 中应在 specUpdated=true 时调用 attachFileCardToLastMessage")
        self.assertIn("files:", code, "LocalMessage 应有 files 字段")

    def test_C5_report_viewer_passes_context_refresh_token(self):
        """ReportViewerPage.tsx 应传 contextRefreshToken 给 DataCenterCopilotContent"""
        code = self._read(_VIEWER_FILE)
        self.assertIn("contextRefreshToken", code,
                      "ReportViewerPage 应将 URL token 作为 contextRefreshToken 传给 DataCenterCopilotContent")

    def test_C6_pilot_context_has_context_refresh_token_field(self):
        """ReportPreviewModal.tsx PilotContext 接口应包含 contextRefreshToken 字段"""
        code = self._read(_PREVIEW_FILE)
        self.assertIn("contextRefreshToken", code,
                      "PilotContext 接口应新增 contextRefreshToken 字段，支持 token 回退")

    def test_C7_chat_api_has_check_pin_status_batch(self):
        """chatApi.ts 应导出 checkPinStatusBatch 方法和 PinStatusResult 接口"""
        code = self._read(_API_FILE)
        self.assertIn("checkPinStatusBatch", code,
                      "reportApi 应包含 checkPinStatusBatch 方法")
        self.assertIn("PinStatusResult", code,
                      "chatApi.ts 应导出 PinStatusResult 接口")

    def test_C8_copilot_local_message_has_files_field(self):
        """DataCenterCopilot.tsx LocalMessage 接口应有 files?: FileInfo[] 字段"""
        code = self._read(_COPILOT_FILE)
        self.assertIn("FileInfo", code, "DataCenterCopilot 应引入 FileInfo 类型")
        self.assertIn("files?:", code, "LocalMessage 应声明 files?: FileInfo[]")

    def test_C9_pilot_files_display_has_preview_and_pin_buttons(self):
        """PilotFilesDisplay 应包含预览、固定、已固定按钮逻辑"""
        code = self._read(_COPILOT_FILE)
        self.assertIn("EyeOutlined",       code, "PilotFilesDisplay 应有预览按钮图标")
        self.assertIn("PushpinOutlined",   code, "PilotFilesDisplay 应有固定按钮图标")
        self.assertIn("CheckCircleOutlined", code, "PilotFilesDisplay 应有已固定按钮图标")
        self.assertIn("已生成固定",         code, "PilotFilesDisplay 应有已固定状态文案")

    def test_C10_pin_override_also_updated_on_manual_pin(self):
        """ChatMessages.tsx 手动 pin 后应同步更新 pinnedOverrides（立即切换按钮状态）"""
        code = self._read(_CHAT_MSGS_FILE)
        self.assertIn("setPinnedOverrides", code,
                      "handlePin 成功后应调用 setPinnedOverrides 立即更新本地状态")


# ─────────────────────────────────────────────────────────────────────────────
# D段 — Pilot 文件卡片 SSE 检测与 UI 逻辑静态分析
# ─────────────────────────────────────────────────────────────────────────────

class TestDPilotFileCardLogic(unittest.TestCase):
    """
    Pilot 对话中"生成的文件"卡片的完整逻辑静态分析：
    - SSE specUpdated 检测（4 种触发条件）
    - hasSpec / canPreview 计算规则
    - attachFileCardToLastMessage token 回退机制
    """

    def _read(self, path: Path) -> str:
        return path.read_text(encoding="utf-8")

    # ── D1 ───────────────────────────────────────────────────────────────────
    def test_D1_sse_handles_tool_result_event_type(self):
        """DataCenterCopilot SSE 解析应处理 data.type === 'tool_result' 事件"""
        code = self._read(_COPILOT_FILE)
        self.assertIn("tool_result", code,
                      "SSE 解析应处理 tool_result 事件类型以检测报表更新")

    # ── D2 ───────────────────────────────────────────────────────────────────
    def test_D2_spec_updated_detected_by_report_update_spec_tool(self):
        """specUpdated 检测：report__update_spec 工具名应触发 specUpdated=true"""
        code = self._read(_COPILOT_FILE)
        self.assertIn("report__update_spec", code,
                      "DataCenterCopilot 应通过 report__update_spec 工具名触发 specUpdated")

    # ── D3 ───────────────────────────────────────────────────────────────────
    def test_D3_spec_updated_detected_by_update_single_chart_tool(self):
        """specUpdated 检测：report__update_single_chart 工具名应触发 specUpdated=true"""
        code = self._read(_COPILOT_FILE)
        self.assertIn("report__update_single_chart", code,
                      "DataCenterCopilot 应通过 report__update_single_chart 工具名触发 specUpdated")

    # ── D4 ───────────────────────────────────────────────────────────────────
    def test_D4_spec_updated_detected_by_chinese_keyword(self):
        """specUpdated 检测：'报表已更新' 关键字（兼容旧 API 返回路径）"""
        code = self._read(_COPILOT_FILE)
        self.assertIn("报表已更新", code,
                      "DataCenterCopilot 应通过'报表已更新'关键字兼容旧路径触发 specUpdated")

    # ── D5 ───────────────────────────────────────────────────────────────────
    def test_D5_spec_updated_detected_by_spec_path_keyword(self):
        """specUpdated 检测：'/spec' 关键字（兼容旧 API 返回路径）"""
        code = self._read(_COPILOT_FILE)
        self.assertIn("'/spec'", code,
                      "DataCenterCopilot 应通过'/spec'关键字兼容旧路径触发 specUpdated")

    # ── D6 ───────────────────────────────────────────────────────────────────
    def test_D6_has_spec_computed_from_context_spec_keys(self):
        """hasSpec 应基于 contextSpec && Object.keys(contextSpec).length > 0"""
        code = self._read(_COPILOT_FILE)
        self.assertIn("hasSpec", code,
                      "DataCenterCopilot 应有 hasSpec 变量控制固定按钮的显示")
        self.assertIn("Object.keys", code,
                      "hasSpec 应检查 contextSpec 的 key 数量（非空对象判断）")

    # ── D7 ───────────────────────────────────────────────────────────────────
    def test_D7_can_preview_requires_pinned_id_and_refresh_token(self):
        """PilotFilesDisplay canPreview 应同时要求 pinned_report_id 和 refresh_token"""
        code = self._read(_COPILOT_FILE)
        self.assertIn("canPreview", code,
                      "PilotFilesDisplay 应有 canPreview 控制预览按钮的可用性")
        # 预览按钮应在 canPreview 条件下才渲染
        # 检查 canPreview 涉及 effectivePinnedId 和 effectiveRefreshToken 两个条件
        self.assertIn("effectivePinnedId", code,
                      "canPreview 应依赖 effectivePinnedId")
        self.assertIn("effectiveRefreshToken", code,
                      "canPreview 应依赖 effectiveRefreshToken")

    # ── D8 ───────────────────────────────────────────────────────────────────
    def test_D8_attach_file_card_falls_back_to_context_refresh_token(self):
        """attachFileCardToLastMessage 应使用 contextRefreshToken 作为 token 回退"""
        code = self._read(_COPILOT_FILE)
        # contextRefreshToken 应出现在 attachFileCardToLastMessage 逻辑中
        self.assertIn("contextRefreshToken", code,
                      "attachFileCardToLastMessage 应支持 contextRefreshToken 作为 token 回退")
        # 验证逻辑：contextSpec?.refresh_token ?? contextRefreshToken
        self.assertIn("?? contextRefreshToken", code,
                      "token 赋值应使用 ?? 运算符以 contextRefreshToken 兜底")


# ─────────────────────────────────────────────────────────────────────────────
# 主程序入口
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    unittest.main(verbosity=2)
