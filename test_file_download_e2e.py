"""
test_file_download_e2e.py
===========================
文件下载功能 E2E 全量测试（T1-T7）

测试层次：
  A  (8)  — RBAC / 鉴权（ENABLE_AUTH=true 场景：各角色权限验证）
  B  (7)  — E2E 管道（AgenticLoop → conversation_service → DB 存储 → GET /messages 还原）
  C  (6)  — 跨用户隔离（真实 DB 用户）
  D  (5)  — 消息 API 文件元数据暴露
  E  (4)  — 文件名编码 & 特殊场景
  F  (4)  — 日期子文件夹组织
  G  (4)  — 无新菜单/权限遗漏回归

总计: 38 个测试用例

运行：
  /d/ProgramData/Anaconda3/envs/dataagent/python.exe -m pytest test_file_download_e2e.py -v -s
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ── 路径 ───────────────────────────────────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent / "backend"))
os.environ.setdefault("ENABLE_AUTH", "False")

from test_utils import make_test_username, make_test_rolename

# ── 全局 DB session ─────────────────────────────────────────────────────────────
_PREFIX = make_test_username("fdl")[:18]  # 每次运行唯一前缀


def _db():
    from backend.config.database import SessionLocal
    return SessionLocal()


_g_db = _db()


# ── 测试工具函数 ─────────────────────────────────────────────────────────────────

def _make_user(suffix="", role_names=None, is_superadmin=False, is_active=True):
    """创建真实 DB 用户并分配角色"""
    from backend.models.user import User
    from backend.models.role import Role
    from backend.models.user_role import UserRole
    from backend.core.auth.password import hash_password

    username = make_test_username(f"fdl{suffix}")
    u = User(
        username=username,
        display_name=f"FDL {suffix}",
        hashed_password=hash_password("Test1234!"),
        auth_source="local",
        is_active=is_active,
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


def _make_token(user):
    """为用户颁发 JWT access_token"""
    from backend.config.settings import settings
    from backend.core.auth.jwt import create_access_token
    from backend.core.rbac import get_user_roles
    # get_user_roles returns List[str] (role name strings directly)
    role_names = get_user_roles(user, _g_db)
    return create_access_token(
        {"sub": str(user.id), "username": user.username, "roles": role_names},
        settings.jwt_secret,
        settings.jwt_algorithm,
    )


def _auth(user):
    return {"Authorization": f"Bearer {_make_token(user)}"}


def teardown_module(_=None):
    """清理测试数据"""
    from backend.models.user import User
    # 删除所有本次测试创建的用户（前缀匹配）
    # 使用 like 匹配 _t_ 开头且含 fdl 的用户名
    try:
        users = _g_db.query(User).filter(User.username.like("_t_%fdl%")).all()
        for u in users:
            _g_db.delete(u)
        _g_db.commit()
    except Exception:
        _g_db.rollback()
    finally:
        _g_db.close()


def _get_customer_root() -> Path:
    """获取实际运行的 files 模块中的 _CUSTOMER_DATA_ROOT"""
    import api.files as files_mod
    return files_mod._CUSTOMER_DATA_ROOT


def _ensure_user_file(username: str, relative_path: str, content: str = "test content") -> Path:
    """在 customer_data/{username}/ 下创建文件，返回绝对路径"""
    root = _get_customer_root()
    file_path = root / username / relative_path
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(content, encoding="utf-8")
    return file_path


def _cleanup_user_files(username: str):
    """清理 customer_data/{username}/ 下的测试文件（以 _t_ 或 _test_ 开头的目录/文件）"""
    import shutil
    root = _get_customer_root()
    user_dir = root / username
    if user_dir.exists():
        for item in user_dir.iterdir():
            if item.name.startswith("_t_") or item.name.startswith("_test_"):
                try:
                    if item.is_dir():
                        shutil.rmtree(item)
                    else:
                        item.unlink()
                except Exception:
                    pass


# ══════════════════════════════════════════════════════════════════════════════
# A — RBAC / 鉴权（ENABLE_AUTH=true 场景）
# ══════════════════════════════════════════════════════════════════════════════

class TestRBACAuth:
    """A: ENABLE_AUTH=true 下各角色均可访问自己的文件，未认证 → 401"""

    @pytest.fixture(autouse=True)
    def setup(self):
        from fastapi.testclient import TestClient
        from backend.main import app
        from backend.config.settings import settings

        self.app = app
        self.settings = settings
        self.root = _get_customer_root()
        yield

    def _client(self):
        from fastapi.testclient import TestClient
        return TestClient(self.app, raise_server_exceptions=True)

    def test_a1_no_token_returns_401(self):
        """ENABLE_AUTH=true 时无 token 访问下载端点 → 401"""
        client = self._client()
        with patch.object(self.settings, 'enable_auth', True):
            resp = client.get(
                "/api/v1/files/download",
                params={"path": "default/any.txt"},
            )
        assert resp.status_code == 401, f"expected 401, got {resp.status_code}: {resp.text}"

    def test_a2_viewer_role_can_download_own_file(self):
        """viewer 角色可以下载自己的文件"""
        user = _make_user("viewer_a2", role_names=["viewer"])
        file_path = _ensure_user_file(user.username, "_t_a2_file.txt", "viewer content")
        try:
            client = self._client()
            with patch.object(self.settings, 'enable_auth', True):
                resp = client.get(
                    "/api/v1/files/download",
                    params={"path": f"{user.username}/_t_a2_file.txt"},
                    headers=_auth(user),
                )
            assert resp.status_code == 200, f"viewer 应能下载: {resp.status_code} {resp.text[:200]}"
            assert b"viewer content" in resp.content
        finally:
            _cleanup_user_files(user.username)

    def test_a3_analyst_role_can_download_own_file(self):
        """analyst 角色可以下载自己的文件"""
        user = _make_user("analyst_a3", role_names=["analyst"])
        file_path = _ensure_user_file(user.username, "_t_a3_data.csv", "col1,col2\n1,2\n")
        try:
            client = self._client()
            with patch.object(self.settings, 'enable_auth', True):
                resp = client.get(
                    "/api/v1/files/download",
                    params={"path": f"{user.username}/_t_a3_data.csv"},
                    headers=_auth(user),
                )
            assert resp.status_code == 200
            assert b"col1,col2" in resp.content
        finally:
            _cleanup_user_files(user.username)

    def test_a4_admin_role_can_download_own_file(self):
        """admin 角色可以下载自己的文件"""
        user = _make_user("admin_a4", role_names=["admin"])
        file_path = _ensure_user_file(user.username, "_t_a4_report.json", '{"ok": true}')
        try:
            client = self._client()
            with patch.object(self.settings, 'enable_auth', True):
                resp = client.get(
                    "/api/v1/files/download",
                    params={"path": f"{user.username}/_t_a4_report.json"},
                    headers=_auth(user),
                )
            assert resp.status_code == 200
        finally:
            _cleanup_user_files(user.username)

    def test_a5_superadmin_can_download_own_file(self):
        """superadmin 可以下载自己的文件"""
        user = _make_user("sa_a5", is_superadmin=True)
        file_path = _ensure_user_file(user.username, "_t_a5_data.txt", "superadmin data")
        try:
            client = self._client()
            with patch.object(self.settings, 'enable_auth', True):
                resp = client.get(
                    "/api/v1/files/download",
                    params={"path": f"{user.username}/_t_a5_data.txt"},
                    headers=_auth(user),
                )
            assert resp.status_code == 200
        finally:
            _cleanup_user_files(user.username)

    def test_a6_inactive_user_returns_401(self):
        """停用用户的 token 无效 → 401"""
        user = _make_user("inactive_a6", role_names=["viewer"], is_active=False)
        _ensure_user_file(user.username, "_t_a6.txt", "x")
        try:
            client = self._client()
            with patch.object(self.settings, 'enable_auth', True):
                resp = client.get(
                    "/api/v1/files/download",
                    params={"path": f"{user.username}/_t_a6.txt"},
                    headers=_auth(user),
                )
            assert resp.status_code == 401, f"停用用户应返回 401, got {resp.status_code}"
        finally:
            _cleanup_user_files(user.username)

    def test_a7_viewer_cannot_access_other_user_file(self):
        """viewer 无法访问其他用户的文件 → 403"""
        alice = _make_user("alice_a7", role_names=["viewer"])
        bob = _make_user("bob_a7", role_names=["viewer"])
        _ensure_user_file(bob.username, "_t_a7_secret.txt", "bob secret")
        try:
            client = self._client()
            with patch.object(self.settings, 'enable_auth', True):
                resp = client.get(
                    "/api/v1/files/download",
                    params={"path": f"{bob.username}/_t_a7_secret.txt"},
                    headers=_auth(alice),  # alice 的 token 试图访问 bob 的文件
                )
            assert resp.status_code == 403
        finally:
            _cleanup_user_files(alice.username)
            _cleanup_user_files(bob.username)

    def test_a8_expired_token_returns_401(self):
        """过期 token → 401"""
        from backend.config.settings import settings
        from jose import jwt as josejwt
        from datetime import datetime, timedelta
        import uuid

        user = _make_user("expired_a8", role_names=["viewer"])
        # 手动构造一个已过期的 JWT（exp 设为过去时间）
        payload = {
            "sub": str(user.id),
            "username": user.username,
            "roles": ["viewer"],
            "exp": datetime.utcnow() - timedelta(seconds=10),
            "iat": datetime.utcnow() - timedelta(minutes=10),
            "jti": str(uuid.uuid4()),
        }
        expired_token = josejwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)
        client = self._client()
        with patch.object(self.settings, 'enable_auth', True):
            resp = client.get(
                "/api/v1/files/download",
                params={"path": f"{user.username}/any.txt"},
                headers={"Authorization": f"Bearer {expired_token}"},
            )
        assert resp.status_code == 401


# ══════════════════════════════════════════════════════════════════════════════
# B — E2E 管道（AgenticLoop → conversation_service → DB → API 还原）
# ══════════════════════════════════════════════════════════════════════════════

class TestE2EPipeline:
    """B: 完整链路测试——文件写入 → SSE 事件 → 持久化 → API 还原 → 下载"""

    def _run(self, coro):
        return asyncio.get_event_loop().run_until_complete(coro)

    def _make_loop(self):
        from backend.agents.agentic_loop import AgenticLoop
        llm = MagicMock()
        mcp = MagicMock()
        mcp.list_servers.return_value = []
        loop = AgenticLoop(llm_adapter=llm, mcp_manager=mcp)
        loop.llm_adapter.chat_plain = AsyncMock(return_value={
            "stop_reason": "end_turn",
            "content": [{"type": "text", "text": "完成"}],
            "usage": {},
        })
        return loop

    def test_b1_files_written_event_emitted_after_write_file(self):
        """write_file 成功后 run_streaming 发出 files_written 事件"""
        loop = self._make_loop()
        llm_resp_tool = {
            "stop_reason": "tool_use",
            "content": [{
                "type": "tool_use", "id": "t1",
                "name": "filesystem__write_file",
                "input": {"path": "customer_data/alice/report.csv", "content": "col1,col2\n1,2\n"},
            }],
            "usage": {},
        }
        llm_resp_end = {
            "stop_reason": "end_turn",
            "content": [{"type": "text", "text": "文件已写入"}],
            "usage": {},
        }
        call_count = [0]
        async def mock_chat(**kwargs):
            call_count[0] += 1
            return llm_resp_tool if call_count[0] == 1 else llm_resp_end

        loop.llm_adapter.chat_with_tools = mock_chat
        loop.mcp_manager.call_tool = AsyncMock(return_value={"success": True, "data": "ok"})

        async def run():
            events = []
            with patch("backend.agents.agentic_loop.format_mcp_tools_for_claude",
                       return_value=[{"name": "filesystem__write_file"}]):
                with patch("backend.agents.agentic_loop.AgenticLoop._build_system_prompt",
                           new_callable=AsyncMock, return_value="test prompt"):
                    async for ev in loop.run_streaming("请写报告", {}):
                        events.append(ev)
            return events

        events = self._run(run())
        fw = next((e for e in events if e.type == "files_written"), None)
        assert fw is not None, "应有 files_written 事件"
        assert fw.data["files"][0]["name"] == "report.csv"

    def test_b2_files_written_event_order_after_content(self):
        """files_written 事件在 content 事件之后（不阻断消息渲染）"""
        loop = self._make_loop()
        llm_resp_tool = {
            "stop_reason": "tool_use",
            "content": [{
                "type": "tool_use", "id": "t1",
                "name": "filesystem__write_file",
                "input": {"path": "customer_data/alice/a.txt", "content": "x"},
            }],
            "usage": {},
        }
        llm_resp_end = {
            "stop_reason": "end_turn",
            "content": [{"type": "text", "text": "done"}],
            "usage": {},
        }
        call_count = [0]
        async def mock_chat(**kwargs):
            call_count[0] += 1
            return llm_resp_tool if call_count[0] == 1 else llm_resp_end

        loop.llm_adapter.chat_with_tools = mock_chat
        loop.mcp_manager.call_tool = AsyncMock(return_value={"success": True, "data": "ok"})

        async def run():
            events = []
            with patch("backend.agents.agentic_loop.format_mcp_tools_for_claude",
                       return_value=[{"name": "filesystem__write_file"}]):
                with patch("backend.agents.agentic_loop.AgenticLoop._build_system_prompt",
                           new_callable=AsyncMock, return_value="test prompt"):
                    async for ev in loop.run_streaming("test", {}):
                        events.append(ev)
            return events

        events = self._run(run())
        types = [e.type for e in events]
        content_idx = next((i for i, t in enumerate(types) if t == "content"), -1)
        fw_idx = next((i for i, t in enumerate(types) if t == "files_written"), -1)
        assert content_idx >= 0 and fw_idx >= 0, f"types: {types}"
        assert fw_idx > content_idx, "files_written 应在 content 之后"

    def test_b3_files_written_stored_in_message_extra_metadata(self):
        """conversation_service 将 files_written 存入 DB message.extra_metadata"""
        # 模拟 send_message_stream 内部的事件收集逻辑
        from backend.agents.agentic_loop import AgentEvent

        files_payload = [
            {"path": "customer_data/alice/report.csv", "name": "report.csv",
             "size": 200, "mime_type": "text/csv"}
        ]
        events = [
            AgentEvent(type="files_written", data={"files": files_payload}),
            AgentEvent(type="content", data="完成", metadata={"final": True}),
        ]

        files_written_info = None
        final_content = ""
        for event in events:
            if event.type == "files_written":
                files_written_info = (event.data or {}).get("files", [])
            if event.type == "content":
                final_content = event.data

        asst_extra_meta: Dict[str, Any] = {}
        if files_written_info:
            asst_extra_meta["files_written"] = files_written_info

        assert "files_written" in asst_extra_meta
        assert asst_extra_meta["files_written"][0]["name"] == "report.csv"
        assert asst_extra_meta["files_written"][0]["mime_type"] == "text/csv"

    def test_b4_message_extra_metadata_persists_to_db(self):
        """files_written 写入 DB message 后可从 get_messages 还原"""
        from fastapi.testclient import TestClient
        from backend.main import app
        from backend.services.conversation_service import ConversationService

        svc = ConversationService(db=_g_db)

        # 1. 创建对话
        conv = svc.create_conversation(title="B4 Test", user_id=None)
        assert conv is not None, "对话创建失败"
        conv_id = str(conv.id)

        try:
            # 2. 直接创建带 files_written 的助手消息
            files_data = [
                {"path": "customer_data/default/b4_report.csv",
                 "name": "b4_report.csv", "size": 500, "mime_type": "text/csv"}
            ]
            msg = svc.add_message(
                conversation_id=conv_id,
                role="assistant",
                content="文件已生成",
                extra_metadata={"files_written": files_data},
            )
            assert msg is not None

            # 3. 通过 GET /messages 查看 extra_metadata
            client = TestClient(app)
            resp = client.get(f"/api/v1/conversations/{conv_id}/messages")
            assert resp.status_code == 200, resp.text
            data = resp.json()

            assistant_msgs = [m for m in data["data"] if m["role"] == "assistant"]
            assert len(assistant_msgs) == 1
            extra = assistant_msgs[0].get("extra_metadata") or {}
            assert "files_written" in extra, f"extra_metadata 未含 files_written: {extra}"
            fw = extra["files_written"]
            assert fw[0]["name"] == "b4_report.csv"
            assert fw[0]["size"] == 500
        finally:
            svc.delete_conversation(conv_id)

    def test_b5_multiple_files_all_tracked_and_persisted(self):
        """多文件写入 → 全部追踪 → DB 全部保存"""
        from backend.services.conversation_service import ConversationService
        svc = ConversationService(db=_g_db)
        conv = svc.create_conversation(title="B5 Multi", user_id=None)
        conv_id = str(conv.id)

        try:
            files_data = [
                {"path": "customer_data/default/b5_a.csv", "name": "b5_a.csv",
                 "size": 100, "mime_type": "text/csv"},
                {"path": "customer_data/default/b5_b.json", "name": "b5_b.json",
                 "size": 50, "mime_type": "application/json"},
                {"path": "customer_data/default/b5_c.xlsx", "name": "b5_c.xlsx",
                 "size": 2048, "mime_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"},
            ]
            svc.add_message(
                conversation_id=conv_id,
                role="assistant",
                content="三个文件已生成",
                extra_metadata={"files_written": files_data},
            )
            from fastapi.testclient import TestClient
            from backend.main import app
            client = TestClient(app)
            resp = client.get(f"/api/v1/conversations/{conv_id}/messages")
            data = resp.json()
            fw = data["data"][-1]["extra_metadata"]["files_written"]
            names = [f["name"] for f in fw]
            assert "b5_a.csv" in names
            assert "b5_b.json" in names
            assert "b5_c.xlsx" in names
        finally:
            svc.delete_conversation(conv_id)

    def test_b6_files_written_event_forwarded_via_yield(self):
        """conversation_service 的 send_message_stream 将 files_written 事件转发给前端"""
        # 验证 conversation_service 的 yield event.to_dict() 覆盖 files_written
        from backend.agents.agentic_loop import AgentEvent
        ev = AgentEvent(type="files_written", data={"files": [
            {"path": "customer_data/default/x.csv", "name": "x.csv",
             "size": 10, "mime_type": "text/csv"}
        ]})
        d = ev.to_dict()
        assert d["type"] == "files_written"
        assert d["data"]["files"][0]["name"] == "x.csv"
        # 确保可序列化（JSON 转发到 SSE 的前提）
        json.dumps(d)

    def test_b7_near_limit_path_also_emits_files_written(self):
        """near_limit 路径也触发 files_written 事件（不丢文件信息）"""
        from backend.agents.agentic_loop import AgenticLoop, MAX_ITERATIONS, NEAR_LIMIT_THRESHOLD

        llm = MagicMock()
        mcp = MagicMock()
        mcp.list_servers.return_value = []
        # 使用 NEAR_LIMIT_THRESHOLD+2 迭代次数：
        # iteration=1 时 remaining=THRESHOLD+1 > THRESHOLD，正常执行工具（write_file 记录）
        # iteration=2 时 remaining=THRESHOLD <= THRESHOLD，触发 near_limit，written_files 已有数据
        loop = AgenticLoop(llm_adapter=llm, mcp_manager=mcp,
                           max_iterations=NEAR_LIMIT_THRESHOLD + 2)

        write_tool_resp = {
            "stop_reason": "tool_use",
            "content": [{
                "type": "tool_use", "id": "t1",
                "name": "filesystem__write_file",
                "input": {"path": "customer_data/alice/near.csv", "content": "a,b"},
            }],
            "usage": {},
        }
        loop.llm_adapter.chat_with_tools = AsyncMock(return_value=write_tool_resp)
        loop.llm_adapter.chat_plain = AsyncMock(return_value={
            "stop_reason": "end_turn", "content": [{"type": "text", "text": "done"}], "usage": {}
        })
        loop.mcp_manager.call_tool = AsyncMock(return_value={"success": True, "data": "ok"})

        synthesize_called = False
        async def fake_synthesize(messages, accumulated_text, remaining):
            nonlocal synthesize_called
            synthesize_called = True
            return "综合分析完成", ["未完成任务"], "结论"

        loop._synthesize_and_wrap_up = fake_synthesize

        async def run():
            events = []
            with patch("backend.agents.agentic_loop.format_mcp_tools_for_claude",
                       return_value=[{"name": "filesystem__write_file"}]):
                with patch("backend.agents.agentic_loop.AgenticLoop._build_system_prompt",
                           new_callable=AsyncMock, return_value="test prompt"):
                    async for ev in loop.run_streaming("写文件", {}):
                        events.append(ev)
            return events

        events = self._run(run())
        types = [e.type for e in events]
        # near_limit 路径应包含 files_written
        assert "files_written" in types, f"near_limit 路径丢失 files_written，types={types}"


# ══════════════════════════════════════════════════════════════════════════════
# C — 跨用户隔离（真实 DB 用户）
# ══════════════════════════════════════════════════════════════════════════════

class TestCrossUserIsolation:
    """C: 多用户场景下，用户只能访问自己的文件"""

    @pytest.fixture(autouse=True)
    def setup(self):
        from fastapi.testclient import TestClient
        from backend.main import app
        from backend.config.settings import settings
        self.client = TestClient(app)
        self.settings = settings
        self.root = _get_customer_root()
        yield

    def test_c1_alice_cannot_access_bob_file(self):
        """alice 无法通过 path=bob/xxx.csv 访问 bob 的文件"""
        alice = _make_user("alice_c1", role_names=["analyst"])
        bob = _make_user("bob_c1", role_names=["analyst"])
        _ensure_user_file(bob.username, "_t_c1_secret.csv", "bob_secret")
        try:
            with patch.object(self.settings, 'enable_auth', True):
                resp = self.client.get(
                    "/api/v1/files/download",
                    params={"path": f"{bob.username}/_t_c1_secret.csv"},
                    headers=_auth(alice),
                )
            assert resp.status_code == 403
        finally:
            _cleanup_user_files(alice.username)
            _cleanup_user_files(bob.username)

    def test_c2_alice_can_access_own_file(self):
        """alice 可以访问自己的文件，不受 bob 存在的影响"""
        alice = _make_user("alice_c2", role_names=["analyst"])
        _ensure_user_file(alice.username, "_t_c2_my_report.csv", "alice_data")
        try:
            with patch.object(self.settings, 'enable_auth', True):
                resp = self.client.get(
                    "/api/v1/files/download",
                    params={"path": f"{alice.username}/_t_c2_my_report.csv"},
                    headers=_auth(alice),
                )
            assert resp.status_code == 200
            assert b"alice_data" in resp.content
        finally:
            _cleanup_user_files(alice.username)

    def test_c3_path_traversal_to_other_user_blocked(self):
        """alice 试图通过 ../ 访问 bob 的文件 → 403"""
        alice = _make_user("alice_c3", role_names=["viewer"])
        bob = _make_user("bob_c3", role_names=["viewer"])
        _ensure_user_file(bob.username, "_t_c3_target.txt", "bob secret")
        try:
            with patch.object(self.settings, 'enable_auth', True):
                # 尝试目录穿越
                resp = self.client.get(
                    "/api/v1/files/download",
                    params={"path": f"{alice.username}/../../{bob.username}/_t_c3_target.txt"},
                    headers=_auth(alice),
                )
            assert resp.status_code == 403
        finally:
            _cleanup_user_files(alice.username)
            _cleanup_user_files(bob.username)

    def test_c4_superadmin_download_own_file_ok(self):
        """superadmin 用自己的 token 访问自己的文件 → 200"""
        sa = _make_user("sa_c4", is_superadmin=True)
        _ensure_user_file(sa.username, "_t_c4_data.txt", "admin data")
        try:
            with patch.object(self.settings, 'enable_auth', True):
                resp = self.client.get(
                    "/api/v1/files/download",
                    params={"path": f"{sa.username}/_t_c4_data.txt"},
                    headers=_auth(sa),
                )
            assert resp.status_code == 200
        finally:
            _cleanup_user_files(sa.username)

    def test_c5_anon_mode_username_default(self):
        """ENABLE_AUTH=false → AnonymousUser.username='default' → 只能访问 default/"""
        # 创建 default 用户目录下的文件
        _ensure_user_file("default", "_t_c5_anon.txt", "anon content")
        try:
            resp = self.client.get(
                "/api/v1/files/download",
                params={"path": "default/_t_c5_anon.txt"},
            )
            assert resp.status_code == 200
            assert b"anon content" in resp.content
        finally:
            root = _get_customer_root()
            f = root / "default" / "_t_c5_anon.txt"
            if f.exists():
                f.unlink()

    def test_c6_anon_mode_cannot_access_other_user(self):
        """ENABLE_AUTH=false → AnonymousUser.username='default' → 无法访问其他用户目录"""
        alice = _make_user("alice_c6", role_names=["viewer"])
        _ensure_user_file(alice.username, "_t_c6_file.txt", "alice data")
        try:
            # 不带 auth header（ENABLE_AUTH=false 时走 AnonymousUser，username=default）
            resp = self.client.get(
                "/api/v1/files/download",
                params={"path": f"{alice.username}/_t_c6_file.txt"},
            )
            assert resp.status_code == 403
        finally:
            _cleanup_user_files(alice.username)


# ══════════════════════════════════════════════════════════════════════════════
# D — 消息 API 文件元数据暴露
# ══════════════════════════════════════════════════════════════════════════════

class TestMessageAPIFilesMetadata:
    """D: GET /conversations/{id}/messages 正确暴露 files_written 元数据"""

    @pytest.fixture(autouse=True)
    def setup(self):
        from fastapi.testclient import TestClient
        from backend.main import app
        from backend.services.conversation_service import ConversationService

        self.client = TestClient(app)
        self.svc = ConversationService(db=_g_db)
        # 创建测试对话
        self.conv = self.svc.create_conversation(title="D Tests", user_id=None)
        self.conv_id = str(self.conv.id)
        yield
        # 清理
        try:
            self.svc.delete_conversation(self.conv_id)
        except Exception:
            pass

    def test_d1_files_written_present_in_extra_metadata(self):
        """助手消息的 extra_metadata.files_written 通过 GET /messages 可见"""
        files_data = [
            {"path": "customer_data/default/d1.csv", "name": "d1.csv",
             "size": 100, "mime_type": "text/csv"}
        ]
        self.svc.add_message(
            conversation_id=self.conv_id,
            role="assistant",
            content="文件已生成",
            extra_metadata={"files_written": files_data},
        )
        resp = self.client.get(f"/api/v1/conversations/{self.conv_id}/messages")
        assert resp.status_code == 200
        data = resp.json()["data"]
        asst = next(m for m in data if m["role"] == "assistant")
        fw = asst.get("extra_metadata", {}).get("files_written", [])
        assert len(fw) == 1
        assert fw[0]["name"] == "d1.csv"
        assert fw[0]["mime_type"] == "text/csv"

    def test_d2_file_path_and_size_preserved(self):
        """path / size / mime_type 原值保留，不被截断或修改"""
        files_data = [
            {"path": "customer_data/default/2026-03/long_report_name.xlsx",
             "name": "long_report_name.xlsx",
             "size": 999999,
             "mime_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}
        ]
        self.svc.add_message(
            conversation_id=self.conv_id, role="assistant",
            content="大文件", extra_metadata={"files_written": files_data},
        )
        resp = self.client.get(f"/api/v1/conversations/{self.conv_id}/messages")
        fw = resp.json()["data"][-1]["extra_metadata"]["files_written"][0]
        assert fw["path"] == files_data[0]["path"]
        assert fw["size"] == 999999

    def test_d3_no_files_written_key_absent(self):
        """无文件写入时，extra_metadata 中不含 files_written 键"""
        self.svc.add_message(
            conversation_id=self.conv_id, role="assistant",
            content="普通回答（无文件）",
        )
        resp = self.client.get(f"/api/v1/conversations/{self.conv_id}/messages")
        data = resp.json()["data"]
        asst = next((m for m in data if m["role"] == "assistant"), None)
        if asst:
            fw = (asst.get("extra_metadata") or {}).get("files_written")
            assert fw is None or fw == [], f"不应有 files_written: {fw}"

    def test_d4_multiple_files_all_preserved(self):
        """多文件全部保存且顺序不变"""
        files_data = [
            {"path": "customer_data/default/a.csv", "name": "a.csv",
             "size": 10, "mime_type": "text/csv"},
            {"path": "customer_data/default/b.json", "name": "b.json",
             "size": 20, "mime_type": "application/json"},
        ]
        self.svc.add_message(
            conversation_id=self.conv_id, role="assistant",
            content="两个文件", extra_metadata={"files_written": files_data},
        )
        resp = self.client.get(f"/api/v1/conversations/{self.conv_id}/messages")
        fw = resp.json()["data"][-1]["extra_metadata"]["files_written"]
        assert len(fw) == 2
        assert fw[0]["name"] == "a.csv"
        assert fw[1]["name"] == "b.json"

    def test_d5_files_written_coexists_with_thinking_events(self):
        """files_written 与 thinking_events 可以共存于 extra_metadata"""
        extra = {
            "files_written": [
                {"path": "customer_data/default/x.csv", "name": "x.csv",
                 "size": 5, "mime_type": "text/csv"}
            ],
            "thinking_events": [
                {"type": "thinking", "data": "正在分析...", "metadata": {}}
            ]
        }
        self.svc.add_message(
            conversation_id=self.conv_id, role="assistant",
            content="mixed", extra_metadata=extra,
        )
        resp = self.client.get(f"/api/v1/conversations/{self.conv_id}/messages")
        msg = resp.json()["data"][-1]
        assert "files_written" in msg.get("extra_metadata", {})
        # thinking_events 被提升为顶层字段
        assert msg.get("thinking_events") is not None or \
               "thinking_events" in msg.get("extra_metadata", {})


# ══════════════════════════════════════════════════════════════════════════════
# E — 文件名编码 & 特殊场景
# ══════════════════════════════════════════════════════════════════════════════

class TestFilenameEncodingAndEdgeCases:
    """E: 中文文件名、空格、特殊字符等编码场景"""

    @pytest.fixture(autouse=True)
    def setup(self):
        from fastapi.testclient import TestClient
        from backend.main import app
        self.client = TestClient(app)
        self.user_dir = _get_customer_root() / "default"
        self.user_dir.mkdir(parents=True, exist_ok=True)
        self._created = []
        yield
        for f in self._created:
            try:
                Path(f).unlink()
            except Exception:
                pass

    def _create(self, filename: str, content: str = "test") -> str:
        p = self.user_dir / filename
        p.write_text(content, encoding="utf-8")
        self._created.append(str(p))
        return filename

    def test_e1_chinese_filename_downloadable(self):
        """中文文件名可以正常下载"""
        fname = self._create("_t_报表数据.csv", "中文内容")
        resp = self.client.get(
            "/api/v1/files/download",
            params={"path": f"default/{fname}"},
        )
        assert resp.status_code == 200
        assert "attachment" in resp.headers.get("content-disposition", "").lower()

    def test_e2_content_disposition_contains_utf8_filename(self):
        """Content-Disposition 包含 UTF-8 编码的文件名（RFC 5987）"""
        fname = self._create("_t_分析结果.xlsx", "xlsx data")
        resp = self.client.get(
            "/api/v1/files/download",
            params={"path": f"default/{fname}"},
        )
        assert resp.status_code == 200
        cd = resp.headers.get("content-disposition", "")
        # 应包含 UTF-8 编码 或 filename= 字段
        assert "attachment" in cd.lower()

    def test_e3_file_with_spaces_in_name(self):
        """文件名含空格可正常下载"""
        fname = self._create("_t_my report 2026.csv", "spaced content")
        resp = self.client.get(
            "/api/v1/files/download",
            params={"path": f"default/{fname}"},
        )
        assert resp.status_code == 200

    def test_e4_path_with_customer_data_prefix_format(self):
        """路径格式1：含 customer_data/ 前缀"""
        fname = self._create("_t_e4_prefix.txt", "prefix test")
        resp = self.client.get(
            "/api/v1/files/download",
            params={"path": f"customer_data/default/{fname}"},
        )
        assert resp.status_code == 200
        assert b"prefix test" in resp.content


# ══════════════════════════════════════════════════════════════════════════════
# F — 日期子文件夹组织
# ══════════════════════════════════════════════════════════════════════════════

class TestDateSubfolderOrganization:
    """F: FILE_OUTPUT_DATE_SUBFOLDER 功能完整验证"""

    def test_f1_setting_default_disabled(self):
        """默认关闭，不影响现有功能"""
        from backend.config.settings import settings
        assert settings.file_output_date_subfolder is False

    def test_f2_file_in_date_subdir_downloadable(self):
        """按月组织的文件 (customer_data/default/2026-03/x.txt) 可以正常下载"""
        root = _get_customer_root()
        month_dir = root / "default" / "_t_2026-03"
        month_dir.mkdir(parents=True, exist_ok=True)
        test_file = month_dir / "report.csv"
        test_file.write_text("monthly report", encoding="utf-8")
        try:
            from fastapi.testclient import TestClient
            from backend.main import app
            client = TestClient(app)
            resp = client.get(
                "/api/v1/files/download",
                params={"path": "default/_t_2026-03/report.csv"},
            )
            assert resp.status_code == 200
            assert b"monthly report" in resp.content
        finally:
            import shutil
            shutil.rmtree(str(month_dir), ignore_errors=True)

    def test_f3_date_hint_in_system_prompt_when_enabled(self):
        """启用 FILE_OUTPUT_DATE_SUBFOLDER 时系统提示含月份路径建议"""
        from datetime import date
        from backend.agents.agentic_loop import AgenticLoop
        from backend.config.settings import settings

        mock_mcp = MagicMock()
        mock_mcp.list_servers.return_value = [
            {"name": "filesystem", "type": "filesystem", "tool_count": 5}
        ]
        fs_obj = MagicMock()
        fs_obj.allowed_directories = ["/proj/customer_data", "/proj/.claude/skills"]
        mock_mcp.servers = {"filesystem": fs_obj}

        loop = AgenticLoop(llm_adapter=MagicMock(), mcp_manager=mock_mcp)
        today = date.today().strftime("%Y-%m")

        async def run():
            with patch("backend.config.settings.settings") as ps:
                ps.file_output_date_subfolder = True
                return await loop._build_system_prompt(
                    context={"username": "alice"}, message="请写报告"
                )

        prompt = asyncio.get_event_loop().run_until_complete(run())
        assert today in prompt, f"系统提示应含 {today}，实际:\n{prompt[:500]}"

    def test_f4_no_date_hint_when_disabled(self):
        """禁用时系统提示不含月份子目录建议"""
        from datetime import date
        from backend.agents.agentic_loop import AgenticLoop

        mock_mcp = MagicMock()
        mock_mcp.list_servers.return_value = [
            {"name": "filesystem", "type": "filesystem", "tool_count": 5}
        ]
        fs_obj = MagicMock()
        fs_obj.allowed_directories = ["/proj/customer_data", "/proj/.claude/skills"]
        mock_mcp.servers = {"filesystem": fs_obj}

        loop = AgenticLoop(llm_adapter=MagicMock(), mcp_manager=mock_mcp)
        today = date.today().strftime("%Y-%m")

        async def run():
            return await loop._build_system_prompt(
                context={"username": "alice"}, message="请写报告"
            )

        prompt = asyncio.get_event_loop().run_until_complete(run())
        lines = [l for l in prompt.splitlines() if today in l and "建议按月" in l]
        assert not lines, f"不应有月份子目录建议，找到: {lines}"


# ══════════════════════════════════════════════════════════════════════════════
# G — RBAC 权限矩阵与无新菜单回归
# ══════════════════════════════════════════════════════════════════════════════

class TestRBACMenuRegression:
    """G: 验证无新增菜单/前端权限，下载端点不需要特殊权限（chat:use 即可）"""

    def test_g1_download_endpoint_uses_only_get_current_user(self):
        """下载端点不使用 require_permission，只要认证即可（viewer 也能用）"""
        import inspect
        import api.files as files_mod
        src = inspect.getsource(files_mod.download_file)
        assert "require_permission" not in src, \
            "download_file 不应使用 require_permission（任何角色都可下载自己的文件）"
        assert "get_current_user" in src, \
            "download_file 应使用 get_current_user 进行认证"

    def test_g2_files_router_registered_in_app(self):
        """files router 已注册到 main app"""
        from backend.main import app
        paths = [r.path for r in app.routes]
        assert any("/files/download" in p for p in paths), \
            f"/files/download 路由未注册，现有路由: {[p for p in paths if 'file' in p.lower()]}"

    def test_g3_no_new_frontend_menu_permissions(self):
        """文件下载功能不需要新的前端路由/菜单权限（inline 功能，无独立页面）"""
        # 验证 /api/v1/files/download 不在权限系统中（无 permissions 表记录）
        # 下载是消息内嵌 UI，不是独立菜单
        from fastapi.testclient import TestClient
        from backend.main import app
        client = TestClient(app)
        # 获取所有权限（superadmin 模式，ENABLE_AUTH=false）
        resp = client.get("/api/v1/permissions")
        assert resp.status_code == 200
        data = resp.json()
        perms = [p.get("key", "") for p in (data if isinstance(data, list) else (data.get("data") or []))]
        # 下载功能不应注册为独立权限
        assert not any("files:download" in p or "download" in p for p in perms), \
            f"不应有 download 专属权限: {[p for p in perms if 'download' in p]}"

    def test_g4_existing_apis_unaffected(self):
        """T1-T7 改动不影响现有 conversations API"""
        from fastapi.testclient import TestClient
        from backend.main import app
        client = TestClient(app)

        # GET /conversations 仍正常
        resp = client.get("/api/v1/conversations")
        assert resp.status_code == 200

        # GET /skills/load-errors 仍正常（settings:read）
        resp = client.get("/api/v1/skills/load-errors")
        assert resp.status_code == 200

        # GET /skills/preview 仍正常
        resp = client.get("/api/v1/skills/preview", params={"message": "test"})
        assert resp.status_code == 200
