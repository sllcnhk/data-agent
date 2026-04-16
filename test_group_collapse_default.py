"""
test_group_collapse_default.py — 分组默认折叠行为测试套件
==========================================================

覆盖范围：
  A段 (4)  — 后端 API：新建分组默认折叠（需 DB）
               A1: 新建分组 → is_expanded=False
               A2: PUT 更新 is_expanded=True → 可展开，刷新后保持
               A3: 再次 PUT 更新 is_expanded=False → 折叠，刷新后保持
               A4: 列表接口返回 is_expanded 字段

  B段 (3)  — 前端代码静态分析（无需 DB）
               B1: Chat.tsx handleToggleGroupExpand 调用 groupApi.updateGroup
               B2: Chat.tsx 使用 newExpanded 计算翻转后的状态
               B3: conversation_group.py 模型默认值为 False

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

_PREFIX = f"_gc_{uuid.uuid4().hex[:6]}_"

# ── 前后端文件路径 ────────────────────────────────────────────────────────────
_CHAT_TSX    = Path(__file__).parent / "frontend" / "src" / "pages" / "Chat.tsx"
_MODEL_FILE  = Path(__file__).parent / "backend" / "models" / "conversation_group.py"

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
        from backend.models import ConversationGroup
        _g_db.query(ConversationGroup).filter(
            ConversationGroup.name.like(f"{_PREFIX}%")
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


# ─────────────────────────────────────────────────────────────────────────────
# A段 — 后端 API 测试（需 DB）
# ─────────────────────────────────────────────────────────────────────────────

class TestAGroupDefaultCollapsed(unittest.TestCase):
    """新建分组默认折叠，展开/折叠状态可持久化"""

    @classmethod
    def setUpClass(cls):
        if _g_db is None:
            raise unittest.SkipTest("DB 未连接，跳过 A 段")
        cls.client = _make_client()

    def _create_group(self, suffix="") -> dict:
        name = f"{_PREFIX}{suffix or uuid.uuid4().hex[:4]}"
        resp = self.client.post("/api/v1/groups", json={"name": name})
        self.assertEqual(resp.status_code, 200, resp.text)
        return resp.json()["data"]

    def _cleanup_group(self, group_id: str):
        try:
            self.client.delete(f"/api/v1/groups/{group_id}")
        except Exception:
            pass

    # A1: 新建分组 → is_expanded=False -------------------------------------------
    def test_A1_new_group_is_collapsed_by_default(self):
        g = self._create_group("a1")
        try:
            self.assertIn("is_expanded", g, "响应应含 is_expanded 字段")
            self.assertFalse(g["is_expanded"],
                             "新建分组 is_expanded 应默认为 False（折叠）")
        finally:
            self._cleanup_group(g["id"])

    # A2: PUT is_expanded=True → 展开，刷新后保持 ----------------------------------
    def test_A2_update_to_expanded_persists(self):
        g = self._create_group("a2")
        try:
            # 展开
            resp = self.client.put(
                f"/api/v1/groups/{g['id']}",
                json={"is_expanded": True},
            )
            self.assertEqual(resp.status_code, 200, resp.text)

            # 重新获取验证持久化
            resp2 = self.client.get("/api/v1/groups")
            self.assertEqual(resp2.status_code, 200)
            groups = resp2.json().get("groups", [])
            match = next((x for x in groups if x["id"] == g["id"]), None)
            self.assertIsNotNone(match, "应能在列表中找到该分组")
            self.assertTrue(match["is_expanded"],
                            "更新为展开后，列表中 is_expanded 应为 True")
        finally:
            self._cleanup_group(g["id"])

    # A3: PUT is_expanded=False → 再次折叠，刷新后保持 ----------------------------
    def test_A3_update_back_to_collapsed_persists(self):
        g = self._create_group("a3")
        try:
            # 先展开
            self.client.put(f"/api/v1/groups/{g['id']}", json={"is_expanded": True})
            # 再折叠
            resp = self.client.put(
                f"/api/v1/groups/{g['id']}",
                json={"is_expanded": False},
            )
            self.assertEqual(resp.status_code, 200, resp.text)

            resp2 = self.client.get("/api/v1/groups")
            groups = resp2.json().get("groups", [])
            match = next((x for x in groups if x["id"] == g["id"]), None)
            self.assertIsNotNone(match)
            self.assertFalse(match["is_expanded"],
                             "更新为折叠后，列表中 is_expanded 应为 False")
        finally:
            self._cleanup_group(g["id"])

    # A4: 列表接口 is_expanded 字段存在 -------------------------------------------
    def test_A4_list_api_returns_is_expanded_field(self):
        g = self._create_group("a4")
        try:
            resp = self.client.get("/api/v1/groups")
            self.assertEqual(resp.status_code, 200)
            groups = resp.json().get("groups", [])
            if groups:
                self.assertIn("is_expanded", groups[0],
                              "分组列表每项应包含 is_expanded 字段")
        finally:
            self._cleanup_group(g["id"])


# ─────────────────────────────────────────────────────────────────────────────
# B段 — 前端 & 后端代码静态分析（无需 DB）
# ─────────────────────────────────────────────────────────────────────────────

class TestBCodeAnalysis(unittest.TestCase):
    """前端 Chat.tsx 和后端 model 改动的静态分析"""

    # B1: Chat.tsx handleToggleGroupExpand 调用 groupApi.updateGroup ------------
    def test_B1_chat_toggle_calls_update_group_api(self):
        self.assertTrue(_CHAT_TSX.exists(), f"文件不存在: {_CHAT_TSX}")
        content = _CHAT_TSX.read_text(encoding="utf-8")
        self.assertIn(
            "groupApi.updateGroup",
            content,
            "handleToggleGroupExpand 应调用 groupApi.updateGroup 持久化",
        )
        # 确认是在 handleToggleGroupExpand 函数体内
        idx_fn = content.find("handleToggleGroupExpand")
        idx_call = content.find("groupApi.updateGroup", idx_fn)
        self.assertGreater(idx_call, idx_fn,
                           "groupApi.updateGroup 调用应在 handleToggleGroupExpand 函数内")

    # B2: Chat.tsx 使用 newExpanded 翻转后传给 API ------------------------------
    def test_B2_chat_uses_new_expanded_value(self):
        content = _CHAT_TSX.read_text(encoding="utf-8")
        self.assertIn(
            "newExpanded",
            content,
            "应声明 newExpanded 变量存储翻转后的展开状态",
        )
        self.assertIn(
            "is_expanded: newExpanded",
            content,
            "updateGroup 应传入 is_expanded: newExpanded",
        )

    # B3: 后端 model 默认值为 False ----------------------------------------------
    def test_B3_model_default_is_false(self):
        self.assertTrue(_MODEL_FILE.exists(), f"文件不存在: {_MODEL_FILE}")
        content = _MODEL_FILE.read_text(encoding="utf-8")
        # 确认 is_expanded 列 default=False
        self.assertIn(
            "default=False",
            content,
            "ConversationGroup.is_expanded 默认值应为 False（折叠）",
        )
        # 确认 default=True 已不存在（或不在 is_expanded 行上）
        lines = content.splitlines()
        for line in lines:
            if "is_expanded" in line and "Column" in line:
                self.assertNotIn(
                    "default=True", line,
                    "is_expanded 列不应再使用 default=True",
                )


if __name__ == "__main__":
    unittest.main(verbosity=2)
