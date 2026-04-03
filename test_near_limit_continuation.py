"""
test_near_limit_continuation.py
================================
Tests for the near-limit synthesis + auto-continue multi-turn reasoning feature.

Layer A — _parse_synthesis_output (4 tests)
Layer B — AgenticLoop near-limit detection (5 tests)
Layer C — ConversationService auto-continue state helpers (3 tests)
Layer D — Auto-continuation loop in send_message_stream (3 tests)
"""

import asyncio
import sys
import os
import json
import uuid
from typing import Any, AsyncGenerator, Dict, List, Optional
from unittest.mock import MagicMock, patch, AsyncMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "backend"))

from backend.agents.agentic_loop import (
    AgenticLoop,
    AgentEvent,
    NEAR_LIMIT_THRESHOLD,
    MAX_ITERATIONS,
)


# ══════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════

def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


class MockLLMAdapter:
    """LLM adapter that returns a canned synthesis response."""

    def __init__(self, text: str = ""):
        self.text = text or (
            "### 阶段性分析结论\n已完成数据库表结构探索。\n\n"
            "### 待完成任务\n以下任务因轮次限制未能完成，将在下次对话中继续：\n"
            '```json\n["查询sales_2024表数据", "生成汇总报告"]\n```'
        )
        self.calls: List[Dict] = []

    async def chat_with_tools(self, messages, tools, system_prompt=""):
        self.calls.append({"method": "chat_with_tools", "messages": messages})
        # Simulate tool_use stop_reason for the first N calls, then end_turn
        if len(self.calls) <= 1:
            return {
                "content": [
                    {"type": "tool_use", "id": "t1", "name": "clickhouse__query",
                     "input": {"query": "SHOW TABLES"}},
                ],
                "stop_reason": "tool_use",
            }
        return {
            "content": [{"type": "text", "text": "完成分析。"}],
            "stop_reason": "end_turn",
        }

    async def chat_plain(self, messages, system_prompt=""):
        self.calls.append({"method": "chat_plain", "messages": messages})
        return {"content": [{"type": "text", "text": self.text}]}


class MockMCPManager:
    def __init__(self):
        self.servers = {}  # format_mcp_tools_for_claude iterates mcp_manager.servers.items()

    def list_servers(self):
        return []

    def list_tools(self):
        return []

    async def call_tool(self, server_name, tool_name, arguments):
        return {"success": True, "data": "mock result"}


def _make_loop(max_iterations: int = 10, llm_text: str = "") -> AgenticLoop:
    adapter = MockLLMAdapter(text=llm_text)
    manager = MockMCPManager()
    loop = AgenticLoop(adapter, manager, max_iterations=max_iterations)
    return loop, adapter


# ══════════════════════════════════════════════════════
# Layer A — _parse_synthesis_output
# ══════════════════════════════════════════════════════

def test_A1_parse_with_valid_json_block():
    """Parses conclusions and pending_tasks from well-formed synthesis output."""
    text = (
        "### 阶段性分析结论\n已探索3张表。发现orders表有10M行。\n\n"
        "### 待完成任务\n```json\n[\"分析orders表\", \"生成报告\"]\n```"
    )
    conclusions, tasks = AgenticLoop._parse_synthesis_output(text)
    assert "已探索3张表" in conclusions
    assert tasks == ["分析orders表", "生成报告"]
    print("[PASS] A1 parse_with_valid_json_block")


def test_A2_parse_empty_pending_tasks():
    """Returns empty list when no JSON block present."""
    text = "### 阶段性分析结论\n分析完毕，无待办。\n"
    conclusions, tasks = AgenticLoop._parse_synthesis_output(text)
    assert "分析完毕" in conclusions
    assert tasks == []
    print("[PASS] A2 parse_empty_pending_tasks")


def test_A3_parse_invalid_json_falls_back_gracefully():
    """Invalid JSON block does not raise; returns empty list."""
    text = (
        "### 阶段性分析结论\n结论。\n\n"
        "### 待完成任务\n```json\n[invalid json\n```"
    )
    conclusions, tasks = AgenticLoop._parse_synthesis_output(text)
    assert tasks == []
    print("[PASS] A3 parse_invalid_json_falls_back_gracefully")


def test_A4_parse_no_conclusions_section():
    """Returns empty string when conclusions section header not present."""
    text = "只有一些文字，没有结构化格式。"
    conclusions, tasks = AgenticLoop._parse_synthesis_output(text)
    assert conclusions == ""
    assert tasks == []
    print("[PASS] A4 parse_no_conclusions_section")


# ══════════════════════════════════════════════════════
# Layer B — AgenticLoop near-limit detection
# ══════════════════════════════════════════════════════

_MOCK_TOOLS = [
    {
        "name": "clickhouse__query",
        "description": "Run a ClickHouse SQL query",
        "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
    }
]


async def _collect_events(loop: AgenticLoop, message: str = "探索数据库") -> List[AgentEvent]:
    events = []
    with patch("backend.agents.agentic_loop.format_mcp_tools_for_claude", return_value=_MOCK_TOOLS):
        async for ev in loop.run_streaming(message, {}):
            events.append(ev)
    return events


def test_B1_near_limit_emits_content_event():
    """When near limit, loop emits a content event with near_limit metadata."""
    # Use max_iterations=6 so NEAR_LIMIT_THRESHOLD(5) triggers at iteration 1
    loop, adapter = _make_loop(max_iterations=6)
    events = _run(_collect_events(loop))
    content_events = [e for e in events if e.type == "content"]
    assert content_events, "Expected at least one content event"
    near_limit_content = [e for e in content_events if e.metadata.get("near_limit")]
    assert near_limit_content, "Expected content event with near_limit=True metadata"
    print("[PASS] B1 near_limit_emits_content_event")


def test_B2_near_limit_emits_near_limit_event():
    """When pending tasks exist in synthesis, loop emits near_limit event."""
    synth = (
        "### 阶段性分析结论\n已找到表列表。\n\n"
        "### 待完成任务\n```json\n[\"任务A\", \"任务B\"]\n```"
    )
    loop, adapter = _make_loop(max_iterations=6, llm_text=synth)
    events = _run(_collect_events(loop))
    near_limit_events = [e for e in events if e.type == "near_limit"]
    assert near_limit_events, "Expected near_limit event"
    ev = near_limit_events[0]
    assert "pending_tasks" in ev.data
    assert "任务A" in ev.data["pending_tasks"]
    assert "任务B" in ev.data["pending_tasks"]
    print("[PASS] B2 near_limit_emits_near_limit_event")


def test_B3_no_near_limit_when_iterations_sufficient():
    """With max_iterations=2, loop ends by end_turn before near-limit threshold triggers."""
    # Tool-calling adapter that immediately returns end_turn (no tools)
    class ImmediateEndAdapter:
        async def chat_with_tools(self, messages, tools, system_prompt=""):
            return {
                "content": [{"type": "text", "text": "直接完成"}],
                "stop_reason": "end_turn",
            }
        async def chat_plain(self, messages, system_prompt=""):
            return {"content": [{"type": "text", "text": "summary"}]}

    loop = AgenticLoop(ImmediateEndAdapter(), MockMCPManager(), max_iterations=2)
    events = _run(_collect_events(loop))
    near_limit_events = [e for e in events if e.type == "near_limit"]
    # Should not trigger near-limit since loop ends before consuming iterations
    assert not near_limit_events
    content_events = [e for e in events if e.type == "content"]
    assert content_events
    print("[PASS] B3 no_near_limit_when_iterations_sufficient")


def test_B4_synthesis_fallback_on_llm_failure():
    """When synthesis LLM call fails, loop still yields content (fallback text)."""
    class FailSynthAdapter:
        call_count = 0

        async def chat_with_tools(self, messages, tools, system_prompt=""):
            return {
                "content": [
                    {"type": "tool_use", "id": "t1", "name": "q",
                     "input": {"query": "SELECT 1"}}
                ],
                "stop_reason": "tool_use",
            }

        async def chat_plain(self, messages, system_prompt=""):
            raise RuntimeError("synthesis LLM timeout")

    loop = AgenticLoop(FailSynthAdapter(), MockMCPManager(), max_iterations=6)
    events = _run(_collect_events(loop))
    # Should not raise; must yield at least a content or error event
    types = {e.type for e in events}
    assert "content" in types or "error" in types
    print("[PASS] B4 synthesis_fallback_on_llm_failure")


def test_B5_near_limit_threshold_constant():
    """NEAR_LIMIT_THRESHOLD is 5 as designed."""
    assert NEAR_LIMIT_THRESHOLD == 5
    print("[PASS] B5 near_limit_threshold_constant")


# ══════════════════════════════════════════════════════
# Layer C — Auto-continue state helpers (unit-level)
# ══════════════════════════════════════════════════════

def _make_service_with_mock_db():
    """Build a ConversationService with a mock DB and a fake conversation."""
    from backend.services.conversation_service import ConversationService
    from backend.models.conversation import Conversation

    db = MagicMock()
    service = ConversationService.__new__(ConversationService)
    service.db = db

    fake_conv = MagicMock(spec=Conversation)
    fake_conv.id = uuid.UUID("00000000-0000-0000-0000-000000000001")
    fake_conv.extra_metadata = {}
    db.query.return_value.filter.return_value.first.return_value = fake_conv

    return service, db, fake_conv


def test_C1_get_auto_continue_state_returns_empty_when_missing():
    """_get_auto_continue_state returns {} when metadata has no key."""
    service, db, fake_conv = _make_service_with_mock_db()
    fake_conv.extra_metadata = {}
    state = service._get_auto_continue_state("00000000-0000-0000-0000-000000000001")
    assert state == {}
    print("[PASS] C1 get_auto_continue_state_returns_empty_when_missing")


def test_C2_set_auto_continue_state_persists():
    """_set_auto_continue_state merges into extra_metadata and commits."""
    service, db, fake_conv = _make_service_with_mock_db()
    fake_conv.extra_metadata = {}
    service._set_auto_continue_state(
        "00000000-0000-0000-0000-000000000001",
        {"count": 2, "pending_tasks": ["任务A"]},
    )
    db.commit.assert_called()
    # The metadata should have been updated
    assert fake_conv.extra_metadata.get("auto_continue_state", {}).get("count") == 2
    print("[PASS] C2 set_auto_continue_state_persists")


def test_C3_build_continuation_message_contains_tasks():
    """_build_continuation_message includes conclusions and tasks."""
    from backend.services.conversation_service import ConversationService
    msg = ConversationService._build_continuation_message(
        conclusions="已发现3张订单表",
        pending_tasks=["分析orders", "生成报告"],
    )
    assert "已发现3张订单表" in msg
    assert "分析orders" in msg
    assert "生成报告" in msg
    print("[PASS] C3 build_continuation_message_contains_tasks")


# ══════════════════════════════════════════════════════
# Layer D — Full auto-continuation flow (3 integration tests)
# ══════════════════════════════════════════════════════

def _make_full_service(near_limit_text: str = ""):
    """Build ConversationService patched for send_message_stream tests."""
    from backend.services.conversation_service import ConversationService
    from backend.models.conversation import Conversation, Message

    db = MagicMock()
    service = ConversationService.__new__(ConversationService)
    service.db = db

    fake_conv = MagicMock(spec=Conversation)
    fake_conv.id = uuid.UUID("00000000-0000-0000-0000-000000000002")
    fake_conv.title = "Test Conv"
    fake_conv.current_model = "claude"
    fake_conv.system_prompt = ""
    fake_conv.total_tokens = 0
    fake_conv.extra_metadata = {}
    db.query.return_value.filter.return_value.first.return_value = fake_conv

    fake_msg = MagicMock(spec=Message)
    fake_msg.id = str(uuid.uuid4())
    fake_msg.role = "user"
    fake_msg.content = "test"
    fake_msg.created_at = "2026-01-01T00:00:00"
    fake_msg.to_dict.return_value = {"id": fake_msg.id, "content": "test"}

    service.add_message = MagicMock(return_value=fake_msg)
    service.get_conversation = MagicMock(return_value=fake_conv)
    service.get_messages = MagicMock(return_value=[])
    service._build_context = MagicMock(return_value={
        "conversation_id": "00000000-0000-0000-0000-000000000002",
        "history": [], "system_prompt": "", "metadata": {}
    })
    service._maybe_summarize = AsyncMock(return_value="")
    service._get_llm_config = MagicMock(return_value={
        "model_type": "claude", "api_key": "test-key",
        "api_base_url": "", "default_model": "claude-sonnet-4-6",
        "temperature": 0.7, "max_tokens": 8192,
        "fallback_models": [], "enable_fallback": False,
    })

    # Patch MasterAgent to yield a near_limit event
    default_synth = near_limit_text or (
        "### 阶段性分析结论\n阶段结论。\n\n"
        "### 待完成任务\n```json\n[\"待办任务X\"]\n```"
    )

    async def _fake_process_stream(msg, ctx):
        yield AgentEvent(type="content", data="初步分析完成。")
        yield AgentEvent(
            type="near_limit",
            data={
                "pending_tasks": ["待办任务X"],
                "conclusions": "阶段结论。",
                "iterations_used": 5,
                "max_iterations": 10,
            },
        )

    mock_agent = MagicMock()
    mock_agent.process_stream = _fake_process_stream
    mock_agent.llm_adapter = MagicMock()

    with patch("backend.services.conversation_service.MasterAgent", return_value=mock_agent):
        with patch("backend.services.conversation_service.get_mcp_manager", return_value=MockMCPManager()):
            return service, fake_conv


async def _drain(gen: AsyncGenerator) -> List[Dict]:
    events = []
    async for ev in gen:
        events.append(ev)
    return events


def test_D1_auto_continuing_event_emitted_on_first_near_limit():
    """First near_limit triggers auto_continuing event with count=1."""
    service, fake_conv = _make_full_service()
    conv_id = "00000000-0000-0000-0000-000000000002"
    fake_conv.extra_metadata = {}

    # Override send_message_stream to avoid recursive real call
    call_count = [0]

    async def patched_stream(cid, msg, model, _continuation_round=0, **kw):
        call_count[0] += 1
        if call_count[0] == 1:
            # First call: actually run original (but mock the agent again)
            from backend.services.conversation_service import ConversationService
            async for ev in ConversationService.send_message_stream(service, cid, msg, model):
                yield ev
        else:
            # Recursive continuation: yield nothing
            yield {"type": "content", "data": "续接完成"}

    with patch("backend.services.conversation_service.MasterAgent") as MockAgent:
        async def fake_ps(msg, ctx):
            yield AgentEvent(type="content", data="初步分析完成。")
            yield AgentEvent(
                type="near_limit",
                data={
                    "pending_tasks": ["待办任务X"],
                    "conclusions": "阶段结论。",
                    "iterations_used": 5,
                    "max_iterations": 10,
                },
            )

        inst = MagicMock()
        inst.process_stream = fake_ps
        inst.llm_adapter = MagicMock()
        MockAgent.return_value = inst

        with patch("backend.services.conversation_service.get_mcp_manager", return_value=MockMCPManager()):
            # Patch recursive call to avoid infinite loop
            service.send_message_stream = patched_stream
            events = _run(_drain(patched_stream(conv_id, "探索数据库", "claude")))

    ac_events = [e for e in events if isinstance(e, dict) and e.get("type") == "auto_continuing"]
    assert ac_events, f"Expected auto_continuing event; got: {[e.get('type') for e in events]}"
    assert ac_events[0]["data"]["continue_count"] == 1
    print("[PASS] D1 auto_continuing_event_emitted_on_first_near_limit")


def test_D2_continuation_approval_required_after_max_auto_continues():
    """After 3 auto-continues, continuation_approval_required is emitted."""
    from backend.services.conversation_service import ConversationService

    service, fake_conv = _make_full_service()
    conv_id = "00000000-0000-0000-0000-000000000002"
    # Pre-set count to MAX_AUTO_CONTINUES (3)
    fake_conv.extra_metadata = {"auto_continue_state": {"count": 3}}

    collected = []

    async def fake_ps(msg, ctx):
        yield AgentEvent(type="content", data="分析完成。")
        yield AgentEvent(
            type="near_limit",
            data={
                "pending_tasks": ["剩余任务"],
                "conclusions": "结论。",
                "iterations_used": 5,
                "max_iterations": 10,
            },
        )

    with patch("backend.services.conversation_service.MasterAgent") as MockAgent:
        inst = MagicMock()
        inst.process_stream = fake_ps
        inst.llm_adapter = MagicMock()
        MockAgent.return_value = inst

        with patch("backend.services.conversation_service.get_mcp_manager", return_value=MockMCPManager()):
            gen = ConversationService.send_message_stream(service, conv_id, "继续分析", "claude")
            collected = _run(_drain(gen))

    approval_events = [e for e in collected if isinstance(e, dict) and e.get("type") == "continuation_approval_required"]
    assert approval_events, f"Expected continuation_approval_required; got types: {[e.get('type') for e in collected if isinstance(e, dict)]}"
    data = approval_events[0]["data"]
    assert "pending_tasks" in data
    assert "剩余任务" in data["pending_tasks"]
    print("[PASS] D2 continuation_approval_required_after_max_auto_continues")


def test_D3_auto_continue_state_reset_on_normal_completion():
    """When no near_limit event, auto_continue_state.count is reset to 0."""
    from backend.services.conversation_service import ConversationService

    service, fake_conv = _make_full_service()
    conv_id = "00000000-0000-0000-0000-000000000002"
    fake_conv.extra_metadata = {"auto_continue_state": {"count": 2}}

    async def normal_ps(msg, ctx):
        yield AgentEvent(type="content", data="任务完成！")

    with patch("backend.services.conversation_service.MasterAgent") as MockAgent:
        inst = MagicMock()
        inst.process_stream = normal_ps
        inst.llm_adapter = MagicMock()
        MockAgent.return_value = inst

        with patch("backend.services.conversation_service.get_mcp_manager", return_value=MockMCPManager()):
            collected = _run(_drain(
                ConversationService.send_message_stream(service, conv_id, "任务", "claude")
            ))

    # After normal completion, auto_continue_state should be reset
    state = service._get_auto_continue_state(conv_id)
    assert state.get("count", 0) == 0, f"Expected count=0, got {state}"
    print("[PASS] D3 auto_continue_state_reset_on_normal_completion")


# ══════════════════════════════════════════════════════
# Runner
# ══════════════════════════════════════════════════════

def run_all():
    tests = [
        # Layer A
        test_A1_parse_with_valid_json_block,
        test_A2_parse_empty_pending_tasks,
        test_A3_parse_invalid_json_falls_back_gracefully,
        test_A4_parse_no_conclusions_section,
        # Layer B
        test_B1_near_limit_emits_content_event,
        test_B2_near_limit_emits_near_limit_event,
        test_B3_no_near_limit_when_iterations_sufficient,
        test_B4_synthesis_fallback_on_llm_failure,
        test_B5_near_limit_threshold_constant,
        # Layer C
        test_C1_get_auto_continue_state_returns_empty_when_missing,
        test_C2_set_auto_continue_state_persists,
        test_C3_build_continuation_message_contains_tasks,
        # Layer D
        test_D1_auto_continuing_event_emitted_on_first_near_limit,
        test_D2_continuation_approval_required_after_max_auto_continues,
        test_D3_auto_continue_state_reset_on_normal_completion,
    ]

    passed = 0
    failed = 0
    for test in tests:
        try:
            if asyncio.iscoroutinefunction(test):
                _run(test())
            else:
                test()
            passed += 1
        except Exception as e:
            import traceback
            print(f"[FAIL] {test.__name__}: {e}")
            traceback.print_exc()
            failed += 1

    print(f"\n{'='*50}")
    print(f"Results: {passed}/{len(tests)} passed, {failed} failed")
    if failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    run_all()
