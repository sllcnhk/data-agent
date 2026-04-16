"""
test_pilot_visibility.py — Pilot 对话在 Chat 首页可见性测试套件
================================================================

覆盖范围：
  A段 (4)  — 后端 API（需 DB）
               A1: 新建 Pilot 对话标题包含 "Pilot" 关键字
               A2: 同报表同用户多次调用 /copilot → 复用同一 conversation_id
               A3: Pilot 对话在 GET /conversations 列表中出现（group_id=None）
               A4: Pilot 对话 extra_metadata 含 context_type=report + context_id

  B段 (3)  — 前端代码静态分析（无需 DB）
               B1: useChatStore.ts Conversation 接口含 extra_metadata 字段
               B2: ConversationSidebar.tsx 含 context_type 检测 + Pilot Tag 渲染
               B3: Chat.tsx loadInitialData 使用 limit ≥ 200

总计: 7 个测试用例
"""
from __future__ import annotations

import os
import sys
import uuid
import unittest
from pathlib import Path
from unittest.mock import patch

# ── 路径 & 环境初始化 ─────────────────────────────────────────────────────────
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "backend"))
os.environ.setdefault("POSTGRES_PASSWORD", "Sgp013013")
os.environ.setdefault("POSTGRES_HOST", "localhost")
os.environ.setdefault("ENABLE_AUTH", "False")

_PREFIX = f"_pv_{uuid.uuid4().hex[:6]}_"

# ── 前端文件路径 ──────────────────────────────────────────────────────────────
_CHAT_TSX         = Path(__file__).parent / "frontend" / "src" / "pages" / "Chat.tsx"
_STORE_FILE       = Path(__file__).parent / "frontend" / "src" / "store" / "useChatStore.ts"
_SIDEBAR_FILE     = Path(__file__).parent / "frontend" / "src" / "components" / "chat" / "ConversationSidebar.tsx"

# ── DB helpers ────────────────────────────────────────────────────────────────
_auth_patcher = None
_g_db = None


def setup_module(_=None):
    global _auth_patcher, _g_db
    try:
        from backend.config.settings import settings
        _auth_patcher = patch.object(settings, "enable_auth", False)
        _auth_patcher.start()
    except Exception:
        pass
    try:
        from backend.config.database import SessionLocal
        _g_db = SessionLocal()
    except Exception:
        pass


def teardown_module(_=None):
    global _auth_patcher, _g_db
    if _auth_patcher:
        _auth_patcher.stop()
    if _g_db:
        _cleanup()
        _g_db.close()


def _cleanup():
    if _g_db is None:
        return
    try:
        from backend.models import Conversation
        _g_db.query(Conversation).filter(
            Conversation.title.like(f"%{_PREFIX}%")
        ).delete(synchronize_session=False)
        _g_db.commit()
    except Exception:
        try:
            _g_db.rollback()
        except Exception:
            pass


def _make_client():
    from backend.main import app
    from fastapi.testclient import TestClient
    return TestClient(app, raise_server_exceptions=False)


def _make_stub_report(client, suffix="") -> dict:
    """
    创建一个最简 pinned report stub（直接插 DB），用于 /copilot 调用。
    因为 POST /reports/pin 需要真实 HTML 文件，这里直接操作 DB 插入最小记录。
    """
    if _g_db is None:
        return None
    try:
        from backend.models.report import Report
        r = Report(
            name=f"{_PREFIX}{suffix or uuid.uuid4().hex[:4]}",
            report_file_path=f"superadmin/reports/{_PREFIX}{suffix or 'stub'}.html",
            refresh_token=uuid.uuid4().hex,
            doc_type="dashboard",
        )
        _g_db.add(r)
        _g_db.commit()
        _g_db.refresh(r)
        return {"id": str(r.id), "name": r.name, "report_file_path": r.report_file_path}
    except Exception as e:
        _g_db.rollback()
        raise e


# ─────────────────────────────────────────────────────────────────────────────
# A段 — 后端 API 测试（需 DB）
# ─────────────────────────────────────────────────────────────────────────────

class TestAPilotVisibilityAPI(unittest.TestCase):
    """Pilot 对话在 Chat 首页可见性 — 后端 API 验证"""

    @classmethod
    def setUpClass(cls):
        if _g_db is None:
            raise unittest.SkipTest("DB 未连接，跳过 A 段")
        cls.client = _make_client()

    def _call_copilot(self, report_id: str) -> dict:
        resp = self.client.post(f"/api/v1/reports/{report_id}/copilot", json={})
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()
        self.assertTrue(data.get("success"), f"copilot 接口返回失败: {data}")
        return data["data"]

    def _cleanup_conv(self, conv_id: str):
        try:
            self.client.delete(f"/api/v1/conversations/{conv_id}")
        except Exception:
            pass

    def _cleanup_report(self, report_id: str):
        try:
            from backend.models.report import Report
            _g_db.query(Report).filter(Report.id == report_id).delete(synchronize_session=False)
            _g_db.commit()
        except Exception:
            _g_db.rollback()

    # A1: Pilot 对话标题包含 "Pilot" 关键字 ------------------------------------
    def test_A1_pilot_conversation_title_contains_pilot(self):
        report = _make_stub_report(self.client, "a1")
        self.assertIsNotNone(report, "无法创建 stub report")
        try:
            data = self._call_copilot(report["id"])
            conv_id = data["conversation_id"]
            try:
                # 获取对话详情，验证标题
                resp = self.client.get(f"/api/v1/conversations/{conv_id}")
                self.assertEqual(resp.status_code, 200, resp.text)
                conv = resp.json().get("conversation") or resp.json()
                title = conv.get("title", "")
                self.assertIn(
                    "Pilot", title,
                    f"Pilot 对话标题应包含 'Pilot' 关键字，实际: {title!r}",
                )
            finally:
                self._cleanup_conv(conv_id)
        finally:
            self._cleanup_report(report["id"])

    # A2: 同报表同用户多次调用 /copilot → 复用同一 conversation_id -------------
    def test_A2_copilot_upsert_returns_same_conversation(self):
        report = _make_stub_report(self.client, "a2")
        self.assertIsNotNone(report)
        conv_ids = []
        try:
            for _ in range(3):
                data = self._call_copilot(report["id"])
                conv_ids.append(data["conversation_id"])

            self.assertEqual(
                len(set(conv_ids)), 1,
                f"3 次调用应复用同一 conversation_id，实际: {conv_ids}",
            )
            # 第 2、3 次应返回 created=False（或缺省为 False）
            data2 = self._call_copilot(report["id"])
            self.assertFalse(
                data2.get("created", False),
                "重复调用 /copilot 应返回 created=False（复用已有对话）",
            )
        finally:
            if conv_ids:
                self._cleanup_conv(conv_ids[0])
            self._cleanup_report(report["id"])

    # A3: Pilot 对话在 GET /conversations 列表中出现，group_id=None ----------
    def test_A3_pilot_conversation_appears_in_list(self):
        report = _make_stub_report(self.client, "a3")
        self.assertIsNotNone(report)
        try:
            data = self._call_copilot(report["id"])
            conv_id = data["conversation_id"]
            try:
                resp = self.client.get("/api/v1/conversations", params={"status": "active", "limit": 200})
                self.assertEqual(resp.status_code, 200)
                convs = resp.json().get("conversations", [])
                found = next((c for c in convs if c["id"] == conv_id), None)
                self.assertIsNotNone(
                    found,
                    f"Pilot 对话 {conv_id} 应出现在 GET /conversations 列表中",
                )
                self.assertIsNone(
                    found.get("group_id"),
                    "Pilot 对话 group_id 应为 None（未分组）",
                )
            finally:
                self._cleanup_conv(conv_id)
        finally:
            self._cleanup_report(report["id"])

    # A4: Pilot 对话 extra_metadata 含 context_type=report + context_id ------
    def test_A4_pilot_conversation_extra_metadata(self):
        report = _make_stub_report(self.client, "a4")
        self.assertIsNotNone(report)
        try:
            data = self._call_copilot(report["id"])
            conv_id = data["conversation_id"]
            try:
                resp = self.client.get(f"/api/v1/conversations/{conv_id}")
                self.assertEqual(resp.status_code, 200)
                conv = resp.json().get("conversation") or resp.json()
                meta = conv.get("extra_metadata") or {}
                self.assertEqual(
                    meta.get("context_type"), "report",
                    "extra_metadata.context_type 应为 'report'",
                )
                self.assertEqual(
                    meta.get("context_id"), report["id"],
                    f"extra_metadata.context_id 应为 report.id={report['id']!r}",
                )
            finally:
                self._cleanup_conv(conv_id)
        finally:
            self._cleanup_report(report["id"])


# ─────────────────────────────────────────────────────────────────────────────
# B段 — 前端代码静态分析（无需 DB）
# ─────────────────────────────────────────────────────────────────────────────

class TestBFrontendCodeAnalysis(unittest.TestCase):
    """前端改动静态分析"""

    # B1: useChatStore.ts Conversation 接口含 extra_metadata 字段 -------------
    def test_B1_conversation_type_has_extra_metadata(self):
        self.assertTrue(_STORE_FILE.exists(), f"文件不存在: {_STORE_FILE}")
        content = _STORE_FILE.read_text(encoding="utf-8")
        self.assertIn(
            "extra_metadata",
            content,
            "useChatStore.ts Conversation 接口应包含 extra_metadata 字段",
        )
        # 确认在 Conversation interface 内（精确匹配 "Conversation {" 区分 ConversationGroup）
        idx_iface = content.find("export interface Conversation {")
        self.assertGreater(idx_iface, 0, "应存在 export interface Conversation { 声明")
        # 找到该 interface 的关闭位置（下一个空行后的 interface 或 export 声明）
        idx_next_block = content.find("\ninterface ", idx_iface + 1)
        idx_field = content.find("extra_metadata", idx_iface)
        self.assertGreater(idx_field, idx_iface, "extra_metadata 应在 Conversation interface 声明之后")
        if idx_next_block > 0:
            self.assertLess(
                idx_field, idx_next_block,
                "extra_metadata 字段应在 Conversation interface 范围内",
            )

    # B2: ConversationSidebar.tsx 含 context_type 检测 + Pilot Tag 渲染 ------
    def test_B2_sidebar_has_pilot_indicator(self):
        self.assertTrue(_SIDEBAR_FILE.exists(), f"文件不存在: {_SIDEBAR_FILE}")
        content = _SIDEBAR_FILE.read_text(encoding="utf-8")

        # 应检测 context_type === 'report'
        self.assertIn(
            "context_type",
            content,
            "ConversationSidebar.tsx 应通过 context_type 识别 Pilot 对话",
        )
        self.assertIn(
            "isPilot",
            content,
            "应声明 isPilot 变量标识 Pilot 对话",
        )
        # 应渲染 Pilot tag
        self.assertIn(
            "Pilot",
            content,
            "ConversationSidebar.tsx 应渲染 Pilot 标识（Tag 或文字）",
        )
        # Tag 组件已引入
        self.assertIn(
            "Tag",
            content,
            "ConversationSidebar.tsx 应从 antd 引入 Tag 组件",
        )

    # B3: Chat.tsx loadInitialData 使用 limit ≥ 200 ---------------------------
    def test_B3_chat_uses_large_limit(self):
        self.assertTrue(_CHAT_TSX.exists(), f"文件不存在: {_CHAT_TSX}")
        content = _CHAT_TSX.read_text(encoding="utf-8")

        # 确认 limit 不再是 50
        self.assertNotIn(
            "limit: 50",
            content,
            "Chat.tsx loadInitialData 不应再使用 limit: 50（已提高上限）",
        )
        # 确认已使用 200 或更大
        import re
        matches = re.findall(r"limit:\s*(\d+)", content)
        limits = [int(m) for m in matches]
        self.assertTrue(
            any(v >= 200 for v in limits),
            f"Chat.tsx 应至少有一处 limit ≥ 200，实际找到: {limits}",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
