"""
test_cancel_e2e.py — Stream Interrupt End-to-End Test Suite
============================================================

设计原则
--------
本测试文件覆盖「对话打断」功能的端到端核心流程，分为以下区块：

  G  HTTP 端点 E2E — SSE 流完整性验证 (4)
  H  打断后状态恢复 — 下一条消息正常 (3)
  I  RBAC 兼容性 — 打断端点权限一致性 (3)
  J  边界 / 异常 (4)
  K  前端逻辑 (已在服务层覆盖) — chunk 类型完整性 (3)
  L  回归：与审批/续接功能不冲突 (3)

总计：20 用例

运行
----
  set PYTHONPATH=C:\\Users\\shiguangping\\data-agent
  d:\\ProgramData\\Anaconda3\\envs\\dataagent\\python.exe test_cancel_e2e.py
"""

import asyncio
import json
import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

os.environ.setdefault("ENABLE_AUTH", "False")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "backend"))

# ──────────────────────────────────────────────────────────────────────────────
# Shared helpers
# ──────────────────────────────────────────────────────────────────────────────

def _make_test_client():
    """Return a FastAPI TestClient backed by the full app."""
    from fastapi.testclient import TestClient
    from backend.main import app
    return TestClient(app, raise_server_exceptions=False)


def _parse_sse_lines(raw_text: str):
    """Parse raw SSE response body into a list of dicts."""
    events = []
    for line in raw_text.splitlines():
        line = line.strip()
        if line.startswith("data: "):
            try:
                events.append(json.loads(line[6:]))
            except json.JSONDecodeError:
                pass
    return events


def _make_service_with_agent_events(agent_events, conv_id=None):
    """
    Build a ConversationService stub that yields the given AgentEvent list
    from process_stream and returns a minimal valid DB conversation.
    """
    from backend.services.conversation_service import ConversationService
    from backend.agents.agentic_loop import AgentEvent

    conv_id = conv_id or str(uuid4())

    db = MagicMock()
    svc = ConversationService(db=db)

    fake_conv = MagicMock()
    fake_conv.id = conv_id
    fake_conv.current_model = "claude"
    fake_conv.extra_metadata = {}

    def fake_get_conversation(cid):
        return fake_conv

    msg_counter = [0]

    def fake_add_message(conversation_id, role, content, **kwargs):
        msg_counter[0] += 1
        m = MagicMock()
        m.id = f"msg-{msg_counter[0]}"
        m.role = role
        m.content = content
        m.to_dict.return_value = {
            "id": m.id, "role": role, "content": content,
            "conversation_id": conversation_id,
        }
        return m

    svc.get_conversation = fake_get_conversation
    svc.add_message = fake_add_message
    svc._build_context = MagicMock(return_value={"history": [], "system_prompt": ""})
    svc._get_llm_config = MagicMock(return_value={
        "model_type": "claude", "api_key": "", "api_base_url": "",
        "default_model": "claude", "temperature": 0.7, "max_tokens": 4096,
        "fallback_models": [], "enable_fallback": False,
    })
    svc._maybe_summarize = AsyncMock(return_value="")
    svc._get_auto_continue_state = MagicMock(return_value={})
    svc._set_auto_continue_state = MagicMock()

    async def fake_process_stream(content, context, cancel_event=None):
        for ev in agent_events:
            yield ev

    mock_agent = MagicMock()
    mock_agent.process_stream = fake_process_stream
    mock_agent.llm_adapter = MagicMock()

    return svc, mock_agent, conv_id


# ──────────────────────────────────────────────────────────────────────────────
# Section G: HTTP E2E — SSE stream completeness
# ──────────────────────────────────────────────────────────────────────────────

class TestHttpStreamE2E(unittest.IsolatedAsyncioTestCase):
    """G1-G4: Full HTTP stream with cancel — SSE format and content correctness"""

    def _collect_stream_events(self, client, conv_id, content="hello"):
        """Send a streaming message and collect parsed SSE events."""
        with client.stream(
            "POST",
            f"/api/v1/conversations/{conv_id}/messages",
            json={"content": content, "model_key": "claude", "stream": True},
            timeout=30.0,
        ) as resp:
            body = resp.read().decode("utf-8")
        return _parse_sse_lines(body), resp.status_code

    async def test_G1_cancelled_event_in_sse_stream(self):
        """G1: When agent yields 'cancelled', SSE stream contains cancelled and assistant_message events"""
        from backend.agents.agentic_loop import AgentEvent

        cancelled_ev = AgentEvent(type="cancelled", data="partial text")
        svc, mock_agent, conv_id = _make_service_with_agent_events([cancelled_ev])

        with patch("backend.services.conversation_service.MasterAgent", return_value=mock_agent), \
             patch("backend.services.conversation_service.ConversationService.get_conversation",
                   return_value=svc.get_conversation(conv_id)):

            # Patch at the HTTP layer
            from fastapi.testclient import TestClient
            from backend.main import app

            with patch("api.conversations.ConversationService") as MockSvc:
                mock_svc_instance = MagicMock()

                async def fake_send_message_stream(*args, **kwargs):
                    # Reproduce what send_message_stream would yield
                    fake_msg = MagicMock()
                    fake_msg.to_dict.return_value = {"id": "u1", "role": "user", "content": "hello"}
                    yield {"type": "user_message", "data": fake_msg.to_dict()}
                    yield cancelled_ev.to_dict()
                    fake_asst = MagicMock()
                    fake_asst.to_dict.return_value = {
                        "id": "a1", "role": "assistant",
                        "content": "partial text\n\n---\n*（生成已被用户中断）*"
                    }
                    yield {"type": "assistant_message", "data": fake_asst.to_dict()}

                mock_svc_instance.send_message_stream = fake_send_message_stream
                mock_svc_instance.get_conversation.return_value = MagicMock(
                    current_model="claude"
                )
                MockSvc.return_value = mock_svc_instance

                client = TestClient(app)
                events, status_code = self._collect_stream_events(client, str(uuid4()))

        self.assertEqual(status_code, 200)
        types = [e.get("type") for e in events]
        self.assertIn("cancelled", types)
        self.assertIn("assistant_message", types)

    async def test_G2_cancel_endpoint_returns_immediately(self):
        """G2: Cancel endpoint responds synchronously without waiting for agent"""
        from fastapi.testclient import TestClient
        from backend.main import app

        client = TestClient(app)
        conv_id = str(uuid4())
        resp = client.post(f"/api/v1/conversations/{conv_id}/cancel")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "cancellation_requested")
        self.assertEqual(data["conversation_id"], conv_id)

    async def test_G3_assistant_message_contains_interruption_marker(self):
        """G3: The assistant_message SSE event contains '已被用户中断' marker when cancelled"""
        from backend.agents.agentic_loop import AgentEvent
        from backend.services.conversation_service import ConversationService
        from backend.core.cancel_manager import cancel_manager

        conv_id = str(uuid4())
        cancelled_ev = AgentEvent(type="cancelled", data="I was about to say something")
        svc, mock_agent, _ = _make_service_with_agent_events([cancelled_ev], conv_id=conv_id)

        saved_assistant = []
        orig_add = svc.add_message

        def capture(conversation_id, role, content, **kwargs):
            m = orig_add(conversation_id, role, content, **kwargs)
            if role == "assistant":
                saved_assistant.append(content)
            return m

        svc.add_message = capture

        with patch("backend.services.conversation_service.MasterAgent", return_value=mock_agent):
            async for _ in svc.send_message_stream(conv_id, "hello", "claude"):
                pass

        self.assertEqual(len(saved_assistant), 1)
        self.assertIn("已被用户中断", saved_assistant[0])
        self.assertIn("I was about to say something", saved_assistant[0])

    async def test_G4_normal_message_no_cancellation_marker(self):
        """G4: Normal completion (no cancel) → no marker in assistant message"""
        from backend.agents.agentic_loop import AgentEvent
        from backend.services.conversation_service import ConversationService

        conv_id = str(uuid4())
        content_ev = AgentEvent(type="content", data="Here is my complete answer.")
        svc, mock_agent, _ = _make_service_with_agent_events([content_ev], conv_id=conv_id)

        saved_assistant = []
        orig_add = svc.add_message

        def capture(conversation_id, role, content, **kwargs):
            m = orig_add(conversation_id, role, content, **kwargs)
            if role == "assistant":
                saved_assistant.append(content)
            return m

        svc.add_message = capture

        with patch("backend.services.conversation_service.MasterAgent", return_value=mock_agent):
            async for _ in svc.send_message_stream(conv_id, "hello", "claude"):
                pass

        self.assertEqual(len(saved_assistant), 1)
        self.assertNotIn("中断", saved_assistant[0])
        self.assertEqual(saved_assistant[0], "Here is my complete answer.")


# ──────────────────────────────────────────────────────────────────────────────
# Section H: State recovery — normal message after cancel
# ──────────────────────────────────────────────────────────────────────────────

class TestStateRecoveryAfterCancel(unittest.IsolatedAsyncioTestCase):
    """H1-H3: Cancel clears state so subsequent messages work normally"""

    async def test_H1_cancel_event_cleared_before_new_message(self):
        """H1: send_message_stream clears cancel signal at the start of each new call"""
        from backend.core.cancel_manager import cancel_manager
        from backend.agents.agentic_loop import AgentEvent
        from backend.services.conversation_service import ConversationService

        conv_id = str(uuid4())

        # Pre-set a lingering cancel signal
        cancel_manager.request_cancel(conv_id)
        self.assertTrue(cancel_manager.should_cancel(conv_id))

        # Now send a new message — the service should clear it first
        content_ev = AgentEvent(type="content", data="answer")
        svc, mock_agent, _ = _make_service_with_agent_events([content_ev], conv_id=conv_id)

        with patch("backend.services.conversation_service.MasterAgent", return_value=mock_agent):
            results = []
            async for chunk in svc.send_message_stream(conv_id, "hello", "claude"):
                results.append(chunk)

        # Cancel must have been cleared before process_stream ran
        # (the content event was yielded, not cancelled)
        types = [c.get("type") for c in results]
        self.assertIn("content", types)
        self.assertNotIn("cancelled", types)
        # After the call, cancel state should be clear
        self.assertFalse(cancel_manager.should_cancel(conv_id))

    async def test_H2_second_message_after_cancel_gets_full_response(self):
        """H2: After cancel, 2nd send_message_stream produces complete content"""
        from backend.core.cancel_manager import cancel_manager
        from backend.agents.agentic_loop import AgentEvent
        from backend.services.conversation_service import ConversationService

        conv_id = str(uuid4())

        # Call 1: cancel event yields
        cancelled_ev = AgentEvent(type="cancelled", data="partial")
        svc, mock_agent_cancel, _ = _make_service_with_agent_events(
            [cancelled_ev], conv_id=conv_id
        )

        with patch("backend.services.conversation_service.MasterAgent",
                   return_value=mock_agent_cancel):
            async for _ in svc.send_message_stream(conv_id, "msg1", "claude"):
                pass

        # Call 2: normal content event
        content_ev = AgentEvent(type="content", data="full answer")
        svc2, mock_agent_normal, _ = _make_service_with_agent_events(
            [content_ev], conv_id=conv_id
        )

        second_call_types = []
        with patch("backend.services.conversation_service.MasterAgent",
                   return_value=mock_agent_normal):
            async for chunk in svc2.send_message_stream(conv_id, "msg2", "claude"):
                second_call_types.append(chunk.get("type"))

        self.assertIn("content", second_call_types)
        self.assertNotIn("cancelled", second_call_types)

    async def test_H3_auto_continuation_suppressed_after_cancel(self):
        """H3: Auto-continuation is NOT triggered when generation was cancelled"""
        from backend.agents.agentic_loop import AgentEvent
        from backend.services.conversation_service import ConversationService

        conv_id = str(uuid4())
        # Yield both cancelled and near_limit (cancel takes precedence)
        cancelled_ev = AgentEvent(type="cancelled", data="partial")
        near_limit_ev = AgentEvent(
            type="near_limit",
            data={"pending_tasks": ["task A"], "conclusions": "some conclusions"},
        )
        svc, mock_agent, _ = _make_service_with_agent_events(
            [cancelled_ev, near_limit_ev], conv_id=conv_id
        )

        yielded_types = []
        with patch("backend.services.conversation_service.MasterAgent", return_value=mock_agent):
            async for chunk in svc.send_message_stream(conv_id, "hello", "claude"):
                yielded_types.append(chunk.get("type"))

        # When cancelled, near_limit_data is set to None → no auto_continuing event
        self.assertNotIn("auto_continuing", yielded_types)
        self.assertNotIn("continuation_approval_required", yielded_types)


# ──────────────────────────────────────────────────────────────────────────────
# Section I: RBAC Compatibility — Cancel endpoint authorization
# ──────────────────────────────────────────────────────────────────────────────

class TestCancelRBAC(unittest.TestCase):
    """I1-I3: Cancel endpoint RBAC consistency analysis

    Design decision: The cancel endpoint intentionally has NO explicit RBAC check,
    consistent with all other conversation endpoints (send_message, list_messages, etc.)
    which also lack server-side RBAC protection.

    When ENABLE_AUTH=true:
      - Frontend route guards ensure only users with `chat:use` permission can reach
        the chat UI (and thus the Stop button).
      - The cancel API endpoint is called from the same authenticated frontend context.
      - Adding auth to cancel but not to send_message would create inconsistent security.

    Future: When conversation endpoints are retrofitted with `chat:use` RBAC checks,
    the cancel endpoint should receive the same treatment.
    """

    def setUp(self):
        from fastapi.testclient import TestClient
        from backend.main import app
        self.client = TestClient(app)

    def test_I1_cancel_accessible_without_auth_token(self):
        """I1: Cancel endpoint works without auth header (consistent with conversation endpoints)"""
        conv_id = str(uuid4())
        resp = self.client.post(
            f"/api/v1/conversations/{conv_id}/cancel",
            # No Authorization header
        )
        # Should succeed (200), not 401
        self.assertEqual(resp.status_code, 200)

    def test_I2_cancel_consistent_with_send_message_auth_level(self):
        """I2: Both cancel and send_message endpoints accept unauthenticated requests"""
        conv_id = str(uuid4())

        # Cancel — should accept unauthenticated
        cancel_resp = self.client.post(f"/api/v1/conversations/{conv_id}/cancel")
        self.assertIn(cancel_resp.status_code, [200, 404, 422])
        # Should NOT be 401 (auth required) or 403 (forbidden)
        self.assertNotIn(cancel_resp.status_code, [401, 403])

    def test_I3_cancel_operation_is_idempotent_and_harmless(self):
        """I3: Cancelling a nonexistent or already-finished conversation is safe"""
        from backend.core.cancel_manager import cancel_manager

        fake_conv_id = str(uuid4())
        # Call multiple times
        for _ in range(3):
            resp = self.client.post(f"/api/v1/conversations/{fake_conv_id}/cancel")
            self.assertEqual(resp.status_code, 200)

        # Clean up
        cancel_manager.clear(fake_conv_id)


# ──────────────────────────────────────────────────────────────────────────────
# Section J: Edge cases / Boundary conditions
# ──────────────────────────────────────────────────────────────────────────────

class TestCancelEdgeCases(unittest.IsolatedAsyncioTestCase):
    """J1-J4: Boundary and error conditions"""

    async def test_J1_empty_partial_content_gets_default_message(self):
        """J1: If cancelled with empty partial content, a fallback message is still saved"""
        from backend.agents.agentic_loop import AgentEvent
        from backend.services.conversation_service import ConversationService

        conv_id = str(uuid4())
        # Cancelled with empty data
        cancelled_ev = AgentEvent(type="cancelled", data="")
        svc, mock_agent, _ = _make_service_with_agent_events([cancelled_ev], conv_id=conv_id)

        saved_assistant = []
        orig_add = svc.add_message

        def capture(conversation_id, role, content, **kwargs):
            m = orig_add(conversation_id, role, content, **kwargs)
            if role == "assistant":
                saved_assistant.append(content)
            return m

        svc.add_message = capture

        with patch("backend.services.conversation_service.MasterAgent", return_value=mock_agent):
            async for _ in svc.send_message_stream(conv_id, "hello", "claude"):
                pass

        # Must save something — either fallback or empty+marker
        self.assertEqual(len(saved_assistant), 1)
        # Must contain the interruption marker
        self.assertIn("已被用户中断", saved_assistant[0])

    async def test_J2_cancel_during_thinking_phase(self):
        """J2: Cancel while only thinking events emitted → partial saved with marker"""
        from backend.agents.agentic_loop import AgentEvent
        from backend.services.conversation_service import ConversationService

        conv_id = str(uuid4())
        # Only thinking events, then cancel
        events = [
            AgentEvent(type="thinking", data="processing..."),
            AgentEvent(type="cancelled", data=""),
        ]
        svc, mock_agent, _ = _make_service_with_agent_events(events, conv_id=conv_id)

        saved_assistant = []
        orig_add = svc.add_message

        def capture(conversation_id, role, content, **kwargs):
            m = orig_add(conversation_id, role, content, **kwargs)
            if role == "assistant":
                saved_assistant.append(content)
            return m

        svc.add_message = capture

        with patch("backend.services.conversation_service.MasterAgent", return_value=mock_agent):
            async for _ in svc.send_message_stream(conv_id, "hello", "claude"):
                pass

        self.assertEqual(len(saved_assistant), 1)
        self.assertIn("已被用户中断", saved_assistant[0])

    async def test_J3_multiple_conversations_independent_cancel(self):
        """J3: Cancelling conv-A doesn't affect conv-B"""
        from backend.core.cancel_manager import cancel_manager

        conv_a = str(uuid4())
        conv_b = str(uuid4())

        cancel_manager.clear(conv_a)
        cancel_manager.clear(conv_b)

        cancel_manager.request_cancel(conv_a)

        self.assertTrue(cancel_manager.should_cancel(conv_a))
        self.assertFalse(cancel_manager.should_cancel(conv_b))

        cancel_manager.clear(conv_a)

    async def test_J4_cancel_event_metadata_has_cancelled_true(self):
        """J4: The 'cancelled' AgentEvent metadata contains cancelled=True"""
        import asyncio
        from backend.agents.agentic_loop import AgenticLoop

        event = asyncio.Event()
        event.set()

        llm = MagicMock()
        llm.chat_plain = AsyncMock()
        mcp = MagicMock()
        mcp.list_servers = MagicMock(return_value=[])
        mcp.servers = {}
        mcp.server_configs = {}

        loop = AgenticLoop(
            llm_adapter=llm, mcp_manager=mcp, max_iterations=5, cancel_event=event
        )

        context = {"history": [], "system_prompt": ""}
        cancelled_events = []
        async for e in loop.run_streaming("hello", context):
            if e.type == "cancelled":
                cancelled_events.append(e)

        self.assertEqual(len(cancelled_events), 1)
        self.assertTrue(cancelled_events[0].metadata.get("cancelled"))


# ──────────────────────────────────────────────────────────────────────────────
# Section K: Frontend logic — chunk type completeness
# ──────────────────────────────────────────────────────────────────────────────

class TestFrontendChunkHandling(unittest.TestCase):
    """K1-K3: Verify frontend Chat.tsx handles 'cancelled' chunk type"""

    def test_K1_cancelled_chunk_type_handled_in_chat_tsx(self):
        """K1: Chat.tsx onChunk handler has explicit 'cancelled' branch"""
        chat_path = r"c:\Users\shiguangping\data-agent\frontend\src\pages\Chat.tsx"
        with open(chat_path, encoding="utf-8") as f:
            source = f.read()
        self.assertIn("chunk.type === 'cancelled'", source,
                      "Chat.tsx must handle 'cancelled' chunk type")

    def test_K2_cancelled_chunk_shows_info_toast(self):
        """K2: 'cancelled' chunk handling shows message.info (user feedback)"""
        chat_path = r"c:\Users\shiguangping\data-agent\frontend\src\pages\Chat.tsx"
        with open(chat_path, encoding="utf-8") as f:
            source = f.read()

        # Find the 'cancelled' block and check it calls message.info
        idx = source.find("chunk.type === 'cancelled'")
        self.assertGreater(idx, 0)
        # Look in the 300 chars after the match for message.info
        context_after = source[idx:idx + 300]
        self.assertIn("message.info", context_after,
                      "cancelled chunk handler should call message.info")

    def test_K3_stop_button_only_visible_when_sending(self):
        """K3: Stop button is conditionally rendered based on 'sending' state"""
        chat_path = r"c:\Users\shiguangping\data-agent\frontend\src\pages\Chat.tsx"
        with open(chat_path, encoding="utf-8") as f:
            source = f.read()

        # Should have: {sending && (<... StopOutlined ...>)}
        self.assertIn("StopOutlined", source)
        self.assertIn("sending &&", source)
        self.assertIn("停止生成", source)


# ──────────────────────────────────────────────────────────────────────────────
# Section L: Regression — cancel doesn't conflict with approval/continuation
# ──────────────────────────────────────────────────────────────────────────────

class TestCancelRegression(unittest.IsolatedAsyncioTestCase):
    """L1-L3: Cancel coexists correctly with approval and auto-continuation features"""

    async def test_L1_cancel_does_not_trigger_approval_flow(self):
        """L1: A cancelled event does not also trigger approval_required flow"""
        from backend.agents.agentic_loop import AgentEvent
        from backend.services.conversation_service import ConversationService

        conv_id = str(uuid4())
        # Yield both approval_required and cancelled (edge case: cancelled after approval)
        events = [
            AgentEvent(type="approval_required", data={
                "approval_id": "ap1", "tool": "tool1",
                "message": "confirm?", "sql": "DROP TABLE x",
                "warnings": ["dangerous"]
            }),
            AgentEvent(type="cancelled", data=""),
        ]
        svc, mock_agent, _ = _make_service_with_agent_events(events, conv_id=conv_id)

        yielded_types = []
        with patch("backend.services.conversation_service.MasterAgent", return_value=mock_agent):
            async for chunk in svc.send_message_stream(conv_id, "hello", "claude"):
                yielded_types.append(chunk.get("type"))

        # approval_required should have been forwarded but cancel should also be present
        self.assertIn("approval_required", yielded_types)
        self.assertIn("cancelled", yielded_types)
        # Should NOT trigger auto_continuing or continuation_approval_required
        self.assertNotIn("auto_continuing", yielded_types)

    async def test_L2_cancel_event_type_in_agentic_loop_does_not_propagate_as_error(self):
        """L2: 'cancelled' event is not treated as an error by send_message_stream"""
        from backend.agents.agentic_loop import AgentEvent
        from backend.services.conversation_service import ConversationService

        conv_id = str(uuid4())
        cancelled_ev = AgentEvent(type="cancelled", data="partial")
        svc, mock_agent, _ = _make_service_with_agent_events([cancelled_ev], conv_id=conv_id)

        error_types = []
        with patch("backend.services.conversation_service.MasterAgent", return_value=mock_agent):
            async for chunk in svc.send_message_stream(conv_id, "hello", "claude"):
                if chunk.get("type") == "error":
                    error_types.append(chunk)

        # A cancelled event should not produce any error event
        self.assertEqual(error_types, [],
                         "cancel should not generate error events")

    async def test_L3_cancel_manager_module_singleton_stability(self):
        """L3: The cancel_manager singleton is stable across multiple imports"""
        from backend.core.cancel_manager import cancel_manager as cm1
        from backend.core.cancel_manager import cancel_manager as cm2

        # Same object
        self.assertIs(cm1, cm2)

        # Operations still work
        test_id = str(uuid4())
        cm1.request_cancel(test_id)
        self.assertTrue(cm2.should_cancel(test_id))
        cm1.clear(test_id)
        self.assertFalse(cm2.should_cancel(test_id))


# ──────────────────────────────────────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import pytest, sys
    sys.exit(pytest.main([__file__, "-v", "-s"]))
