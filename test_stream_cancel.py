"""
test_stream_cancel.py — Stream Interrupt Feature Tests

Sections:
  A  CancelManager unit tests (5 cases)
  B  AgenticLoop cancellation (5 cases)
  C  conversation_service cancel integration (4 cases)
  D  REST cancel endpoint (3 cases)
  E  Integration: cancel then continue (2 cases)
  F  Regression smoke (2 run-validations)

Run:
  set PYTHONPATH=C:\\Users\\shiguangping\\data-agent
  d:\\ProgramData\\Anaconda3\\envs\\dataagent\\python.exe test_stream_cancel.py
"""

import asyncio
import os
import sys
import unittest
import unittest.mock
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault("ENABLE_AUTH", "False")

# ──────────────────────────────────────────────────────────────────────────────
# Section A: CancelManager
# ──────────────────────────────────────────────────────────────────────────────

class TestCancelManager(unittest.IsolatedAsyncioTestCase):
    """A1-A5: ConversationCancelManager unit tests"""

    def setUp(self):
        from backend.core.cancel_manager import ConversationCancelManager
        self.mgr = ConversationCancelManager()

    def test_A1_request_cancel_sets_event(self):
        """A1: request_cancel makes should_cancel return True"""
        self.mgr.request_cancel("conv-1")
        self.assertTrue(self.mgr.should_cancel("conv-1"))

    def test_A2_clear_resets_cancel(self):
        """A2: clear() resets should_cancel to False"""
        self.mgr.request_cancel("conv-2")
        self.mgr.clear("conv-2")
        self.assertFalse(self.mgr.should_cancel("conv-2"))

    def test_A3_should_cancel_false_without_request(self):
        """A3: should_cancel is False for unknown conv_id"""
        self.assertFalse(self.mgr.should_cancel("no-such-id"))

    def test_A4_idempotent_cancel(self):
        """A4: Multiple request_cancel calls are idempotent"""
        self.mgr.request_cancel("conv-3")
        self.mgr.request_cancel("conv-3")
        self.assertTrue(self.mgr.should_cancel("conv-3"))

    async def test_A5_get_event_returns_same_instance(self):
        """A5: get_event returns the same asyncio.Event on repeat calls"""
        e1 = self.mgr.get_event("conv-4")
        e2 = self.mgr.get_event("conv-4")
        self.assertIs(e1, e2)


# ──────────────────────────────────────────────────────────────────────────────
# Section B: AgenticLoop cancellation
# ──────────────────────────────────────────────────────────────────────────────

def _make_loop_with_cancel(cancel_event=None):
    """Helper: build an AgenticLoop with mocked LLM and MCP."""
    from backend.agents.agentic_loop import AgenticLoop

    llm = MagicMock()
    llm.chat_with_tools = AsyncMock()
    llm.chat_plain = AsyncMock()

    mcp = MagicMock()
    mcp.list_servers = MagicMock(return_value=[])
    mcp.servers = {}
    mcp.server_configs = {}

    loop = AgenticLoop(
        llm_adapter=llm,
        mcp_manager=mcp,
        max_iterations=5,
        cancel_event=cancel_event,
    )
    return loop, llm, mcp


class TestAgenticLoopCancel(unittest.IsolatedAsyncioTestCase):
    """B1-B5: AgenticLoop cancel_event tests"""

    async def test_B1_cancel_before_llm_call(self):
        """B1: Pre-set cancel_event → loop yields 'cancelled' and stops without 'content'"""
        event = asyncio.Event()
        event.set()

        loop, llm, _ = _make_loop_with_cancel(cancel_event=event)
        context = {"history": [], "system_prompt": ""}
        events = []
        async for e in loop.run_streaming("hello", context):
            events.append(e.type)

        self.assertIn("cancelled", events)
        # No final content should be produced when cancelled before the main LLM path
        self.assertNotIn("content", events)

    async def test_B2_cancel_during_llm_call(self):
        """B2: cancel_event fires while LLM coroutine is awaited → 'cancelled'"""
        event = asyncio.Event()
        loop, llm, _ = _make_loop_with_cancel(cancel_event=event)

        async def slow_llm(**kwargs):
            event.set()  # signal cancel mid-call
            await asyncio.sleep(0.1)
            return {"stop_reason": "end_turn", "content": [{"type": "text", "text": "done"}]}

        llm.chat_plain = slow_llm

        context = {"history": [], "system_prompt": ""}
        event_types = []
        async for e in loop.run_streaming("hello", context):
            event_types.append(e.type)

        self.assertIn("cancelled", event_types)
        self.assertNotIn("content", event_types)

    async def test_B3_no_cancel_event_completes_normally(self):
        """B3: Loop without cancel_event runs to completion normally"""
        loop, llm, _ = _make_loop_with_cancel(cancel_event=None)

        llm.chat_plain.return_value = {
            "stop_reason": "end_turn",
            "content": [{"type": "text", "text": "hello"}],
        }

        context = {"history": [], "system_prompt": ""}
        event_types = []
        async for e in loop.run_streaming("hi", context):
            event_types.append(e.type)

        self.assertIn("content", event_types)
        self.assertNotIn("cancelled", event_types)

    async def test_B4_cancelled_event_has_data_field(self):
        """B4: 'cancelled' AgentEvent carries partial content in .data"""
        event = asyncio.Event()
        event.set()
        loop, llm, _ = _make_loop_with_cancel(cancel_event=event)

        context = {"history": [], "system_prompt": ""}
        cancelled_events = []
        async for e in loop.run_streaming("hello", context):
            if e.type == "cancelled":
                cancelled_events.append(e)

        self.assertEqual(len(cancelled_events), 1)
        # data may be empty string but must exist (not raise AttributeError)
        _ = cancelled_events[0].data

    async def test_B5_cancel_after_tool_execution(self):
        """B5: cancel_event set after tool result → loop stops at check point"""
        event = asyncio.Event()
        # Use max_iterations=20 so near-limit synthesis doesn't fire at iteration 1
        from backend.agents.agentic_loop import AgenticLoop
        llm = unittest.mock.MagicMock()
        llm.chat_with_tools = AsyncMock()
        llm.chat_plain = AsyncMock()
        mcp = unittest.mock.MagicMock()
        mcp.list_servers = unittest.mock.MagicMock(return_value=[])
        mcp.servers = {}
        mcp.server_configs = {}
        loop = AgenticLoop(
            llm_adapter=llm, mcp_manager=mcp, max_iterations=20, cancel_event=event
        )

        # First LLM call: tool_use
        llm.chat_with_tools = AsyncMock(return_value={
            "stop_reason": "tool_use",
            "content": [
                {"type": "tool_use", "id": "t1", "name": "srv__tool", "input": {}}
            ],
        })

        async def fake_call_tool(srv, tool, inp):
            event.set()  # set cancel AFTER tool execution
            return {"success": True, "data": "result"}

        mcp.call_tool = fake_call_tool
        mcp.list_servers.return_value = [{"name": "srv", "type": "clickhouse", "tool_count": 1}]
        mcp.servers = {"srv": MagicMock(tools=[])}
        mcp.server_configs = {"srv": {"type": "clickhouse"}}

        # Patch the name as directly imported inside agentic_loop module
        with patch("backend.agents.agentic_loop.format_mcp_tools_for_claude",
                   return_value=[{"name": "srv__tool"}]):
            context = {"history": [], "system_prompt": ""}
            event_types = []
            async for e in loop.run_streaming("hello", context):
                event_types.append(e.type)

        self.assertIn("cancelled", event_types)


# ──────────────────────────────────────────────────────────────────────────────
# Section C: conversation_service integration
# ──────────────────────────────────────────────────────────────────────────────

class TestConversationServiceCancel(unittest.IsolatedAsyncioTestCase):
    """C1-C4: send_message_stream cancel integration"""

    def _make_service(self, events_to_yield):
        """Build a ConversationService with mocked DB and agent."""
        from backend.services.conversation_service import ConversationService

        db = MagicMock()
        svc = ConversationService(db=db)

        # Stub out DB methods
        fake_msg = MagicMock()
        fake_msg.to_dict.return_value = {"id": "m1", "role": "user", "content": "hi"}
        svc.add_message = MagicMock(return_value=fake_msg)
        svc._build_context = MagicMock(return_value={"history": [], "system_prompt": ""})
        svc._get_llm_config = MagicMock(return_value={"model_type": "claude", "api_key": "", "api_base_url": "", "default_model": "claude", "temperature": 0.7, "max_tokens": 4096, "fallback_models": [], "enable_fallback": False})
        svc._maybe_summarize = AsyncMock(return_value="")
        svc._get_auto_continue_state = MagicMock(return_value={})
        svc._set_auto_continue_state = MagicMock()

        from backend.agents.agentic_loop import AgentEvent

        async def fake_process_stream(content, context, cancel_event=None):
            for ev in events_to_yield:
                yield ev

        mock_agent = MagicMock()
        mock_agent.process_stream = fake_process_stream
        mock_agent.llm_adapter = MagicMock()

        with patch("backend.services.conversation_service.MasterAgent", return_value=mock_agent):
            pass  # just to verify mock; we patch inline in tests

        return svc, mock_agent

    async def test_C1_cancelled_event_saves_partial_with_marker(self):
        """C1: 'cancelled' SSE event → assistant message saved with interruption marker"""
        from backend.agents.agentic_loop import AgentEvent
        from backend.services.conversation_service import ConversationService

        cancelled_ev = AgentEvent(type="cancelled", data="Partial answer")

        db = MagicMock()
        svc = ConversationService(db=db)

        saved_contents = []

        def capture_add_message(conversation_id, role, content, **kwargs):
            m = MagicMock()
            m.to_dict.return_value = {"id": "m1", "role": role, "content": content}
            if role == "assistant":
                saved_contents.append(content)
            return m

        svc.add_message = capture_add_message
        svc._build_context = MagicMock(return_value={"history": [], "system_prompt": ""})
        svc._get_llm_config = MagicMock(return_value={"model_type": "claude", "api_key": "", "api_base_url": "", "default_model": "claude", "temperature": 0.7, "max_tokens": 4096, "fallback_models": [], "enable_fallback": False})
        svc._maybe_summarize = AsyncMock(return_value="")
        svc._get_auto_continue_state = MagicMock(return_value={})
        svc._set_auto_continue_state = MagicMock()

        async def fake_process_stream(content, context, cancel_event=None):
            yield cancelled_ev

        mock_agent = MagicMock()
        mock_agent.process_stream = fake_process_stream
        mock_agent.llm_adapter = MagicMock()

        with patch("backend.services.conversation_service.MasterAgent", return_value=mock_agent):
            chunks = []
            async for chunk in svc.send_message_stream("conv-id", "hello", "claude"):
                chunks.append(chunk)

        self.assertTrue(len(saved_contents) == 1)
        self.assertIn("已被用户中断", saved_contents[0])
        self.assertIn("Partial answer", saved_contents[0])

    async def test_C2_normal_content_no_cancel_marker(self):
        """C2: Normal 'content' event → assistant message has no interruption marker"""
        from backend.agents.agentic_loop import AgentEvent
        from backend.services.conversation_service import ConversationService

        content_ev = AgentEvent(type="content", data="Full answer")

        db = MagicMock()
        svc = ConversationService(db=db)

        saved_contents = []

        def capture_add_message(conversation_id, role, content, **kwargs):
            m = MagicMock()
            m.to_dict.return_value = {"id": "m1", "role": role, "content": content}
            if role == "assistant":
                saved_contents.append(content)
            return m

        svc.add_message = capture_add_message
        svc._build_context = MagicMock(return_value={"history": [], "system_prompt": ""})
        svc._get_llm_config = MagicMock(return_value={"model_type": "claude", "api_key": "", "api_base_url": "", "default_model": "claude", "temperature": 0.7, "max_tokens": 4096, "fallback_models": [], "enable_fallback": False})
        svc._maybe_summarize = AsyncMock(return_value="")
        svc._get_auto_continue_state = MagicMock(return_value={})
        svc._set_auto_continue_state = MagicMock()

        async def fake_process_stream(content, context, cancel_event=None):
            yield content_ev

        mock_agent = MagicMock()
        mock_agent.process_stream = fake_process_stream
        mock_agent.llm_adapter = MagicMock()

        with patch("backend.services.conversation_service.MasterAgent", return_value=mock_agent):
            async for _ in svc.send_message_stream("conv-id", "hello", "claude"):
                pass

        self.assertEqual(len(saved_contents), 1)
        self.assertNotIn("中断", saved_contents[0])

    async def test_C3_cancel_manager_clear_on_start(self):
        """C3: send_message_stream calls cancel_manager.clear() at start"""
        from backend.core.cancel_manager import cancel_manager
        from backend.agents.agentic_loop import AgentEvent
        from backend.services.conversation_service import ConversationService

        conv_id = "conv-clear-test"
        # Pre-set a cancel signal
        cancel_manager.request_cancel(conv_id)
        self.assertTrue(cancel_manager.should_cancel(conv_id))

        db = MagicMock()
        svc = ConversationService(db=db)

        def capture_add_message(conversation_id, role, content, **kwargs):
            m = MagicMock()
            m.to_dict.return_value = {"id": "m1", "role": role, "content": content}
            return m

        svc.add_message = capture_add_message
        svc._build_context = MagicMock(return_value={"history": [], "system_prompt": ""})
        svc._get_llm_config = MagicMock(return_value={"model_type": "claude", "api_key": "", "api_base_url": "", "default_model": "claude", "temperature": 0.7, "max_tokens": 4096, "fallback_models": [], "enable_fallback": False})
        svc._maybe_summarize = AsyncMock(return_value="")
        svc._get_auto_continue_state = MagicMock(return_value={})
        svc._set_auto_continue_state = MagicMock()

        content_ev = AgentEvent(type="content", data="answer")

        async def fake_process_stream(content, context, cancel_event=None):
            yield content_ev

        mock_agent = MagicMock()
        mock_agent.process_stream = fake_process_stream
        mock_agent.llm_adapter = MagicMock()

        with patch("backend.services.conversation_service.MasterAgent", return_value=mock_agent):
            async for _ in svc.send_message_stream(conv_id, "hello", "claude"):
                pass

        # After send_message_stream starts, it clears the signal
        # (it was cleared at the beginning of the call, before process_stream)
        # The cancel event should be cleared now
        self.assertFalse(cancel_manager.should_cancel(conv_id))

    async def test_C4_cancel_event_passed_to_process_stream(self):
        """C4: send_message_stream passes cancel_event kwarg to agent.process_stream"""
        from backend.agents.agentic_loop import AgentEvent
        from backend.services.conversation_service import ConversationService

        db = MagicMock()
        svc = ConversationService(db=db)

        received_cancel_event = []

        def capture_add_message(conversation_id, role, content, **kwargs):
            m = MagicMock()
            m.to_dict.return_value = {"id": "m1", "role": role, "content": content}
            return m

        svc.add_message = capture_add_message
        svc._build_context = MagicMock(return_value={"history": [], "system_prompt": ""})
        svc._get_llm_config = MagicMock(return_value={"model_type": "claude", "api_key": "", "api_base_url": "", "default_model": "claude", "temperature": 0.7, "max_tokens": 4096, "fallback_models": [], "enable_fallback": False})
        svc._maybe_summarize = AsyncMock(return_value="")
        svc._get_auto_continue_state = MagicMock(return_value={})
        svc._set_auto_continue_state = MagicMock()

        content_ev = AgentEvent(type="content", data="answer")

        async def fake_process_stream(content, context, cancel_event=None):
            received_cancel_event.append(cancel_event)
            yield content_ev

        mock_agent = MagicMock()
        mock_agent.process_stream = fake_process_stream
        mock_agent.llm_adapter = MagicMock()

        with patch("backend.services.conversation_service.MasterAgent", return_value=mock_agent):
            async for _ in svc.send_message_stream("conv-ev-test", "hello", "claude"):
                pass

        self.assertEqual(len(received_cancel_event), 1)
        import asyncio as _asyncio
        self.assertIsInstance(received_cancel_event[0], _asyncio.Event)


# ──────────────────────────────────────────────────────────────────────────────
# Section D: REST cancel endpoint
# ──────────────────────────────────────────────────────────────────────────────

class TestCancelEndpoint(unittest.IsolatedAsyncioTestCase):
    """D1-D3: cancel REST endpoint"""

    def _make_app(self):
        from fastapi.testclient import TestClient
        from backend.api.conversations import router
        from fastapi import FastAPI
        app = FastAPI()
        app.include_router(router, prefix="/api/v1")
        return TestClient(app)

    def test_D1_cancel_returns_200(self):
        """D1: POST /conversations/{id}/cancel → 200 with status field"""
        client = self._make_app()
        import uuid
        conv_id = str(uuid.uuid4())
        resp = client.post(f"/api/v1/conversations/{conv_id}/cancel")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "cancellation_requested")

    def test_D2_cancel_sets_cancel_manager(self):
        """D2: cancel endpoint sets cancel_manager for the given conv_id"""
        from backend.core.cancel_manager import cancel_manager
        client = self._make_app()
        import uuid
        conv_id = str(uuid.uuid4())
        cancel_manager.clear(conv_id)  # start clean
        client.post(f"/api/v1/conversations/{conv_id}/cancel")
        self.assertTrue(cancel_manager.should_cancel(conv_id))
        cancel_manager.clear(conv_id)  # cleanup

    def test_D3_cancel_idempotent(self):
        """D3: Multiple cancel calls to same conv_id don't raise"""
        client = self._make_app()
        import uuid
        conv_id = str(uuid.uuid4())
        r1 = client.post(f"/api/v1/conversations/{conv_id}/cancel")
        r2 = client.post(f"/api/v1/conversations/{conv_id}/cancel")
        self.assertEqual(r1.status_code, 200)
        self.assertEqual(r2.status_code, 200)


# ──────────────────────────────────────────────────────────────────────────────
# Section E: Integration — cancel then send next message normally
# ──────────────────────────────────────────────────────────────────────────────

class TestCancelThenContinue(unittest.IsolatedAsyncioTestCase):
    """E1-E2: After cancel, next message works normally"""

    def _build_svc(self):
        from backend.services.conversation_service import ConversationService
        db = MagicMock()
        svc = ConversationService(db=db)

        def capture_add_message(conversation_id, role, content, **kwargs):
            m = MagicMock()
            m.to_dict.return_value = {"id": "m1", "role": role, "content": content}
            return m

        svc.add_message = capture_add_message
        svc._build_context = MagicMock(return_value={"history": [], "system_prompt": ""})
        svc._get_llm_config = MagicMock(return_value={"model_type": "claude", "api_key": "", "api_base_url": "", "default_model": "claude", "temperature": 0.7, "max_tokens": 4096, "fallback_models": [], "enable_fallback": False})
        svc._maybe_summarize = AsyncMock(return_value="")
        svc._get_auto_continue_state = MagicMock(return_value={})
        svc._set_auto_continue_state = MagicMock()
        return svc

    async def test_E1_next_message_after_cancel_completes(self):
        """E1: After a cancelled generation, next send_message_stream completes normally"""
        from backend.agents.agentic_loop import AgentEvent
        from backend.core.cancel_manager import cancel_manager

        conv_id = "conv-e1"
        svc = self._build_svc()

        # Simulate first message being cancelled
        cancel_manager.request_cancel(conv_id)

        cancelled_ev = AgentEvent(type="cancelled", data="partial")
        normal_ev = AgentEvent(type="content", data="full answer")

        call_count = [0]

        async def fake_process_stream(content, context, cancel_event=None):
            call_count[0] += 1
            if call_count[0] == 1:
                yield cancelled_ev
            else:
                yield normal_ev

        mock_agent = MagicMock()
        mock_agent.process_stream = fake_process_stream
        mock_agent.llm_adapter = MagicMock()

        with patch("backend.services.conversation_service.MasterAgent", return_value=mock_agent):
            # First call — cancelled
            chunks1 = []
            async for c in svc.send_message_stream(conv_id, "msg1", "claude"):
                chunks1.append(c)

            # Second call — should complete normally
            chunks2 = []
            async for c in svc.send_message_stream(conv_id, "msg2", "claude"):
                chunks2.append(c)

        # First call had a cancelled event
        types1 = [c.get("type") for c in chunks1]
        self.assertIn("cancelled", types1)

        # Second call had normal content
        types2 = [c.get("type") for c in chunks2]
        self.assertIn("content", types2)
        self.assertNotIn("cancelled", types2)

    async def test_E2_cancel_does_not_affect_different_conversation(self):
        """E2: Cancelling conv-A does not affect conv-B"""
        from backend.core.cancel_manager import cancel_manager

        cancel_manager.request_cancel("conv-A-only")
        self.assertTrue(cancel_manager.should_cancel("conv-A-only"))
        self.assertFalse(cancel_manager.should_cancel("conv-B-independent"))
        cancel_manager.clear("conv-A-only")


# ──────────────────────────────────────────────────────────────────────────────
# Section F: Regression smoke
# ──────────────────────────────────────────────────────────────────────────────

class TestRegression(unittest.TestCase):
    """F1-F2: Regression — key existing modules still importable / functional"""

    def test_F1_agentic_loop_import_ok(self):
        """F1: AgenticLoop still importable with new cancel_event param"""
        from backend.agents.agentic_loop import AgenticLoop, AgentEvent, AgenticResult
        loop = AgenticLoop.__new__(AgenticLoop)
        self.assertTrue(hasattr(loop, "_cancellable_await"))

    def test_F2_cancel_manager_singleton(self):
        """F2: cancel_manager module singleton is importable"""
        from backend.core.cancel_manager import cancel_manager
        from backend.core.cancel_manager import ConversationCancelManager
        self.assertIsInstance(cancel_manager, ConversationCancelManager)


# ──────────────────────────────────────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import pytest, sys
    sys.exit(pytest.main([__file__, "-v", "-s"]))
