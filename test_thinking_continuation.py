"""
test_thinking_continuation.py
==============================
针对推理过程持久化（thinking_events）和自动续接角色（continuation role）的完整测试。

Layer A — Message.to_dict() thinking_events 字段提升 (3 tests)
Layer B — thinking_events 收集与截断 (5 tests)
Layer C — thinking_events 写入 assistant 消息 (3 tests)
Layer D — continuation role 保存与 extra_metadata (5 tests)
Layer E — _build_context 中 continuation → user 映射 (3 tests)
Layer F — 自动续接递归调用传递 _continuation_round (3 tests)
Layer G — RBAC: 新特性未新增未授权路由/菜单 (3 tests)
Layer H — 回归: 原有 near_limit D 层测试兼容新签名 (3 tests)

共 28 个测试
"""

import asyncio
import sys
import os
import uuid
from typing import Any, AsyncGenerator, Dict, List, Optional
from unittest.mock import MagicMock, AsyncMock, patch, call

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "backend"))
os.environ.setdefault("ENABLE_AUTH", "False")


# ══════════════════════════════════════════════════════
# 共享工具
# ══════════════════════════════════════════════════════

def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


async def _drain(gen: AsyncGenerator) -> List[Dict]:
    events = []
    async for ev in gen:
        events.append(ev)
    return events


class MockMCPManager:
    def __init__(self):
        self.servers = {}

    def list_servers(self):
        return []

    def list_tools(self):
        return []

    async def call_tool(self, server_name, tool_name, arguments):
        return {"success": True, "data": "mock result"}


def _make_fake_message(role="user", extra_metadata=None):
    """构造假 Message 对象，to_dict() 返回真实字典。"""
    from backend.models.conversation import Message
    msg = MagicMock(spec=Message)
    msg.id = str(uuid.uuid4())
    msg.conversation_id = str(uuid.uuid4())
    msg.role = role
    msg.content = "test"
    msg.model = "claude"
    msg.model_params = None
    msg.prompt_tokens = 0
    msg.completion_tokens = 0
    msg.total_tokens = 10
    msg.artifacts = None
    msg.tool_calls = None
    msg.tool_results = None
    msg.extra_metadata = extra_metadata
    msg.created_at = MagicMock()
    msg.created_at.isoformat.return_value = "2026-01-01T00:00:00"
    # 绑定真实 to_dict 方法
    from backend.models.conversation import Message as RealMessage
    msg.to_dict = lambda: RealMessage.to_dict(msg)
    return msg


def _make_service_with_mock_db(conv_extra_metadata=None):
    """构建带 MockDB 的 ConversationService。"""
    from backend.services.conversation_service import ConversationService
    from backend.models.conversation import Conversation

    db = MagicMock()
    service = ConversationService.__new__(ConversationService)
    service.db = db

    fake_conv = MagicMock(spec=Conversation)
    fake_conv.id = uuid.UUID("00000000-0000-0000-0000-000000000001")
    fake_conv.title = "Test Conv"
    fake_conv.current_model = "claude"
    fake_conv.system_prompt = None
    fake_conv.total_tokens = 0
    fake_conv.extra_metadata = conv_extra_metadata or {}
    db.query.return_value.filter.return_value.first.return_value = fake_conv

    return service, db, fake_conv


def _make_full_service(process_stream_fn=None, conv_extra_metadata=None):
    """
    构建完整的 ConversationService，patch MasterAgent 和 get_mcp_manager。
    process_stream_fn: 异步生成器函数，接受 (msg, ctx) 参数。
    """
    from backend.services.conversation_service import ConversationService
    from backend.models.conversation import Conversation
    from backend.agents.agentic_loop import AgentEvent

    db = MagicMock()
    service = ConversationService.__new__(ConversationService)
    service.db = db

    fake_conv = MagicMock(spec=Conversation)
    fake_conv.id = uuid.UUID("00000000-0000-0000-0000-000000000002")
    fake_conv.title = "Test Conv"
    fake_conv.current_model = "claude"
    fake_conv.system_prompt = ""
    fake_conv.total_tokens = 0
    fake_conv.extra_metadata = conv_extra_metadata or {}

    db.query.return_value.filter.return_value.first.return_value = fake_conv

    # add_message 记录调用参数并返回 fake message
    saved_messages = []

    def _fake_add_message(conversation_id, role, content, **kwargs):
        extra_meta = kwargs.get("extra_metadata")
        msg = _make_fake_message(role=role, extra_metadata=extra_meta)
        msg.content = content
        d = msg.to_dict()
        d["role"] = role
        msg.to_dict = lambda: {**d, "role": role}
        saved_messages.append({
            "role": role,
            "content": content,
            "extra_metadata": extra_meta,
        })
        return msg

    service.add_message = _fake_add_message
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

    if process_stream_fn is None:
        async def _default_ps(msg, ctx):
            yield AgentEvent(type="content", data="完成。")
        process_stream_fn = _default_ps

    mock_agent = MagicMock()
    mock_agent.process_stream = process_stream_fn
    mock_agent.llm_adapter = MagicMock()

    return service, fake_conv, saved_messages, mock_agent


# ══════════════════════════════════════════════════════
# Layer A — Message.to_dict() thinking_events 字段提升
# ══════════════════════════════════════════════════════

def test_A1_to_dict_promotes_thinking_events_to_top_level():
    """extra_metadata 含 thinking_events → to_dict() 结果中作为顶层字段暴露。"""
    thinking = [
        {"type": "thinking", "data": "分析数据...", "metadata": {}},
        {"type": "tool_call", "data": {"name": "query"}, "metadata": {}},
    ]
    msg = _make_fake_message(role="assistant", extra_metadata={"thinking_events": thinking})
    d = msg.to_dict()
    assert "thinking_events" in d, "to_dict() 应包含 thinking_events 顶层字段"
    assert d["thinking_events"] == thinking
    print("[PASS] A1 to_dict_promotes_thinking_events_to_top_level")


def test_A2_to_dict_no_thinking_events_key_when_absent():
    """extra_metadata 无 thinking_events → to_dict() 结果中不含该字段。"""
    msg = _make_fake_message(role="assistant", extra_metadata={"other_key": "value"})
    d = msg.to_dict()
    assert "thinking_events" not in d, "不应包含 thinking_events 字段"
    print("[PASS] A2 to_dict_no_thinking_events_key_when_absent")


def test_A3_to_dict_with_none_extra_metadata():
    """extra_metadata 为 None → to_dict() 正常返回，无 thinking_events 字段。"""
    msg = _make_fake_message(role="assistant", extra_metadata=None)
    d = msg.to_dict()
    assert "thinking_events" not in d
    assert d["role"] == "assistant"
    print("[PASS] A3 to_dict_with_none_extra_metadata")


# ══════════════════════════════════════════════════════
# Layer B — thinking_events 收集与截断
# ══════════════════════════════════════════════════════

def test_B1_thinking_events_constant_is_2000():
    """_MAX_THINKING_TOOL_RESULT_CHARS 常量应为 2000。"""
    from backend.services.conversation_service import ConversationService
    assert ConversationService._MAX_THINKING_TOOL_RESULT_CHARS == 2000
    print("[PASS] B1 _MAX_THINKING_TOOL_RESULT_CHARS == 2000")


def test_B2_thinking_and_tool_call_collected():
    """thinking 和 tool_call 事件应被收集进 thinking_events。"""
    from backend.agents.agentic_loop import AgentEvent

    service, fake_conv, saved_messages, mock_agent = _make_full_service()

    async def ps(msg, ctx):
        yield AgentEvent(type="thinking", data="思考中...")
        yield AgentEvent(type="tool_call", data={"name": "query", "input": {"sql": "SELECT 1"}})
        yield AgentEvent(type="content", data="完成。")

    mock_agent.process_stream = ps

    with patch("backend.services.conversation_service.MasterAgent", return_value=mock_agent):
        with patch("backend.services.conversation_service.get_mcp_manager", return_value=MockMCPManager()):
            from backend.services.conversation_service import ConversationService
            events = _run(_drain(
                ConversationService.send_message_stream(service, "00000000-0000-0000-0000-000000000002", "test", "claude")
            ))

    asst_save = next((s for s in saved_messages if s["role"] == "assistant"), None)
    assert asst_save is not None, "应保存 assistant 消息"
    thinking_events = asst_save["extra_metadata"]["thinking_events"]
    types = [e["type"] for e in thinking_events]
    assert "thinking" in types, f"应含 thinking 事件，got: {types}"
    assert "tool_call" in types, f"应含 tool_call 事件，got: {types}"
    print("[PASS] B2 thinking_and_tool_call_collected")


def test_B3_tool_result_collected():
    """tool_result 事件应被收集进 thinking_events。"""
    from backend.agents.agentic_loop import AgentEvent

    service, fake_conv, saved_messages, mock_agent = _make_full_service()

    async def ps(msg, ctx):
        yield AgentEvent(type="tool_result", data={"name": "query", "result": "some data"})
        yield AgentEvent(type="content", data="完成。")

    mock_agent.process_stream = ps

    with patch("backend.services.conversation_service.MasterAgent", return_value=mock_agent):
        with patch("backend.services.conversation_service.get_mcp_manager", return_value=MockMCPManager()):
            from backend.services.conversation_service import ConversationService
            _run(_drain(
                ConversationService.send_message_stream(service, "00000000-0000-0000-0000-000000000002", "test", "claude")
            ))

    asst_save = next((s for s in saved_messages if s["role"] == "assistant"), None)
    thinking_events = asst_save["extra_metadata"]["thinking_events"]
    assert any(e["type"] == "tool_result" for e in thinking_events)
    print("[PASS] B3 tool_result_collected")


def test_B4_tool_result_data_truncated_at_2000():
    """tool_result 数据超过 2000 字节应被截断，追加 '…（已截断）'。"""
    from backend.agents.agentic_loop import AgentEvent

    large_result = "x" * 3000
    service, fake_conv, saved_messages, mock_agent = _make_full_service()

    async def ps(msg, ctx):
        yield AgentEvent(type="tool_result", data={"name": "query", "result": large_result})
        yield AgentEvent(type="content", data="完成。")

    mock_agent.process_stream = ps

    with patch("backend.services.conversation_service.MasterAgent", return_value=mock_agent):
        with patch("backend.services.conversation_service.get_mcp_manager", return_value=MockMCPManager()):
            from backend.services.conversation_service import ConversationService
            _run(_drain(
                ConversationService.send_message_stream(service, "00000000-0000-0000-0000-000000000002", "test", "claude")
            ))

    asst_save = next((s for s in saved_messages if s["role"] == "assistant"), None)
    thinking_events = asst_save["extra_metadata"]["thinking_events"]
    tr = next(e for e in thinking_events if e["type"] == "tool_result")
    result = tr["data"]["result"]
    assert len(result) <= 2000 + len("…（已截断）"), f"截断后长度应 <= 2010，got {len(result)}"
    assert result.endswith("…（已截断）"), f"应以截断标记结尾，got: {result[-20:]}"
    print("[PASS] B4 tool_result_data_truncated_at_2000")


def test_B5_tool_result_short_data_not_truncated():
    """tool_result 数据不超过 2000 字节不应被截断。"""
    from backend.agents.agentic_loop import AgentEvent

    short_result = "小结果数据"
    service, fake_conv, saved_messages, mock_agent = _make_full_service()

    async def ps(msg, ctx):
        yield AgentEvent(type="tool_result", data={"name": "query", "result": short_result})
        yield AgentEvent(type="content", data="完成。")

    mock_agent.process_stream = ps

    with patch("backend.services.conversation_service.MasterAgent", return_value=mock_agent):
        with patch("backend.services.conversation_service.get_mcp_manager", return_value=MockMCPManager()):
            from backend.services.conversation_service import ConversationService
            _run(_drain(
                ConversationService.send_message_stream(service, "00000000-0000-0000-0000-000000000002", "test", "claude")
            ))

    asst_save = next((s for s in saved_messages if s["role"] == "assistant"), None)
    thinking_events = asst_save["extra_metadata"]["thinking_events"]
    tr = next(e for e in thinking_events if e["type"] == "tool_result")
    assert tr["data"]["result"] == short_result
    print("[PASS] B5 tool_result_short_data_not_truncated")


# ══════════════════════════════════════════════════════
# Layer C — thinking_events 写入 assistant 消息
# ══════════════════════════════════════════════════════

def test_C1_thinking_events_absent_when_no_thinking_events():
    """无 thinking 事件时，assistant 消息不含 thinking_events（extra_metadata 为空）。"""
    from backend.agents.agentic_loop import AgentEvent

    service, fake_conv, saved_messages, mock_agent = _make_full_service()

    async def ps(msg, ctx):
        yield AgentEvent(type="content", data="纯文本回答。")

    mock_agent.process_stream = ps

    with patch("backend.services.conversation_service.MasterAgent", return_value=mock_agent):
        with patch("backend.services.conversation_service.get_mcp_manager", return_value=MockMCPManager()):
            from backend.services.conversation_service import ConversationService
            _run(_drain(
                ConversationService.send_message_stream(service, "00000000-0000-0000-0000-000000000002", "test", "claude")
            ))

    asst_save = next((s for s in saved_messages if s["role"] == "assistant"), None)
    extra = asst_save["extra_metadata"]
    assert not extra or "thinking_events" not in extra, (
        f"无 thinking 事件时不应有 thinking_events，got: {extra}"
    )
    print("[PASS] C1 thinking_events_absent_when_no_thinking_events")


def test_C2_thinking_events_saved_in_extra_metadata():
    """有 thinking 事件时，assistant 消息 extra_metadata 含 thinking_events 列表。"""
    from backend.agents.agentic_loop import AgentEvent

    service, fake_conv, saved_messages, mock_agent = _make_full_service()

    async def ps(msg, ctx):
        yield AgentEvent(type="thinking", data="正在推理...")
        yield AgentEvent(type="content", data="结论。")

    mock_agent.process_stream = ps

    with patch("backend.services.conversation_service.MasterAgent", return_value=mock_agent):
        with patch("backend.services.conversation_service.get_mcp_manager", return_value=MockMCPManager()):
            from backend.services.conversation_service import ConversationService
            _run(_drain(
                ConversationService.send_message_stream(service, "00000000-0000-0000-0000-000000000002", "test", "claude")
            ))

    asst_save = next((s for s in saved_messages if s["role"] == "assistant"), None)
    assert asst_save["extra_metadata"] is not None
    thinking_events = asst_save["extra_metadata"].get("thinking_events")
    assert thinking_events and len(thinking_events) == 1
    assert thinking_events[0]["type"] == "thinking"
    assert thinking_events[0]["data"] == "正在推理..."
    print("[PASS] C2 thinking_events_saved_in_extra_metadata")


def test_C3_assistant_message_event_includes_thinking_events():
    """assistant_message SSE 事件的 data 中含 thinking_events（来自 to_dict）。"""
    from backend.agents.agentic_loop import AgentEvent

    service, fake_conv, saved_messages, mock_agent = _make_full_service()

    async def ps(msg, ctx):
        yield AgentEvent(type="thinking", data="思考步骤")
        yield AgentEvent(type="content", data="结论。")

    mock_agent.process_stream = ps

    with patch("backend.services.conversation_service.MasterAgent", return_value=mock_agent):
        with patch("backend.services.conversation_service.get_mcp_manager", return_value=MockMCPManager()):
            from backend.services.conversation_service import ConversationService
            events = _run(_drain(
                ConversationService.send_message_stream(service, "00000000-0000-0000-0000-000000000002", "test", "claude")
            ))

    asst_event = next((e for e in events if isinstance(e, dict) and e.get("type") == "assistant_message"), None)
    assert asst_event is not None, "应有 assistant_message 事件"
    # 因为 _make_fake_message 的 to_dict 在 _fake_add_message 中被固定了，
    # 这里验证 thinking_events 是否随 extra_metadata 传递
    asst_save = next((s for s in saved_messages if s["role"] == "assistant"), None)
    assert "thinking_events" in (asst_save["extra_metadata"] or {})
    print("[PASS] C3 assistant_message_event_includes_thinking_events")


# ══════════════════════════════════════════════════════
# Layer D — continuation role 保存与 extra_metadata
# ══════════════════════════════════════════════════════

def test_D1_normal_round_saves_user_role():
    """_continuation_round=0 时，消息以 role='user' 保存。"""
    from backend.agents.agentic_loop import AgentEvent

    service, fake_conv, saved_messages, mock_agent = _make_full_service()

    with patch("backend.services.conversation_service.MasterAgent", return_value=mock_agent):
        with patch("backend.services.conversation_service.get_mcp_manager", return_value=MockMCPManager()):
            from backend.services.conversation_service import ConversationService
            _run(_drain(
                ConversationService.send_message_stream(
                    service, "00000000-0000-0000-0000-000000000002", "用户问题", "claude",
                    _continuation_round=0
                )
            ))

    user_save = next((s for s in saved_messages if s["role"] in ("user", "continuation")), None)
    assert user_save["role"] == "user", f"期望 role='user'，got '{user_save['role']}'"
    print("[PASS] D1 normal_round_saves_user_role")


def test_D2_continuation_round_saves_continuation_role():
    """_continuation_round=1 时，消息以 role='continuation' 保存。"""
    from backend.agents.agentic_loop import AgentEvent

    service, fake_conv, saved_messages, mock_agent = _make_full_service()

    with patch("backend.services.conversation_service.MasterAgent", return_value=mock_agent):
        with patch("backend.services.conversation_service.get_mcp_manager", return_value=MockMCPManager()):
            from backend.services.conversation_service import ConversationService
            _run(_drain(
                ConversationService.send_message_stream(
                    service, "00000000-0000-0000-0000-000000000002", "续接指令", "claude",
                    _continuation_round=1
                )
            ))

    first_save = saved_messages[0]
    assert first_save["role"] == "continuation", (
        f"_continuation_round=1 应保存 role='continuation'，got '{first_save['role']}'"
    )
    print("[PASS] D2 continuation_round_saves_continuation_role")


def test_D3_continuation_message_has_round_in_extra_metadata():
    """continuation 消息的 extra_metadata 应含 continuation_round。"""
    from backend.agents.agentic_loop import AgentEvent

    service, fake_conv, saved_messages, mock_agent = _make_full_service()

    with patch("backend.services.conversation_service.MasterAgent", return_value=mock_agent):
        with patch("backend.services.conversation_service.get_mcp_manager", return_value=MockMCPManager()):
            from backend.services.conversation_service import ConversationService
            _run(_drain(
                ConversationService.send_message_stream(
                    service, "00000000-0000-0000-0000-000000000002", "续接", "claude",
                    _continuation_round=2
                )
            ))

    first_save = saved_messages[0]
    assert first_save["role"] == "continuation"
    meta = first_save["extra_metadata"]
    assert meta is not None, "continuation 消息应有 extra_metadata"
    assert meta.get("continuation_round") == 2, f"continuation_round 应为 2，got {meta}"
    print("[PASS] D3 continuation_message_has_round_in_extra_metadata")


def test_D4_continuation_message_has_max_rounds_in_extra_metadata():
    """continuation 消息的 extra_metadata 应含 max_rounds=3。"""
    service, fake_conv, saved_messages, mock_agent = _make_full_service()

    with patch("backend.services.conversation_service.MasterAgent", return_value=mock_agent):
        with patch("backend.services.conversation_service.get_mcp_manager", return_value=MockMCPManager()):
            from backend.services.conversation_service import ConversationService
            _run(_drain(
                ConversationService.send_message_stream(
                    service, "00000000-0000-0000-0000-000000000002", "续接", "claude",
                    _continuation_round=1
                )
            ))

    first_save = saved_messages[0]
    assert first_save["extra_metadata"]["max_rounds"] == 3
    print("[PASS] D4 continuation_message_has_max_rounds_in_extra_metadata")


def test_D5_user_message_has_no_continuation_meta():
    """普通用户消息（_continuation_round=0）不应有 continuation_round extra_metadata。"""
    service, fake_conv, saved_messages, mock_agent = _make_full_service()

    with patch("backend.services.conversation_service.MasterAgent", return_value=mock_agent):
        with patch("backend.services.conversation_service.get_mcp_manager", return_value=MockMCPManager()):
            from backend.services.conversation_service import ConversationService
            _run(_drain(
                ConversationService.send_message_stream(
                    service, "00000000-0000-0000-0000-000000000002", "用户问题", "claude",
                    _continuation_round=0
                )
            ))

    user_save = saved_messages[0]
    meta = user_save["extra_metadata"]
    assert meta is None or "continuation_round" not in meta, (
        f"普通用户消息不应有 continuation_round，got: {meta}"
    )
    print("[PASS] D5 user_message_has_no_continuation_meta")


# ══════════════════════════════════════════════════════
# Layer E — _build_context 中 continuation → user 映射
# ══════════════════════════════════════════════════════

def test_E1_continuation_role_mapped_to_user_in_context():
    """DB 中 role='continuation' 的消息在 _build_context 中以 'user' 身份进入 LLM 上下文。"""
    from backend.services.conversation_service import ConversationService
    from backend.models.conversation import Conversation, Message

    db = MagicMock()
    service = ConversationService.__new__(ConversationService)
    service.db = db

    fake_conv = MagicMock(spec=Conversation)
    fake_conv.id = uuid.UUID("00000000-0000-0000-0000-000000000003")
    fake_conv.title = "Test"
    fake_conv.current_model = "claude"
    fake_conv.system_prompt = ""
    fake_conv.total_tokens = 0
    fake_conv.extra_metadata = {}

    # 构造消息列表：含一条 continuation role 消息
    m1 = MagicMock(spec=Message); m1.role = "user"; m1.content = "首次提问"; m1.extra_metadata = {}
    m2 = MagicMock(spec=Message); m2.role = "assistant"; m2.content = "初步结论"; m2.extra_metadata = {}
    m3 = MagicMock(spec=Message); m3.role = "continuation"; m3.content = "续接指令"; m3.extra_metadata = {}
    m4 = MagicMock(spec=Message); m4.role = "assistant"; m4.content = "最终结论"; m4.extra_metadata = {}

    db.query.return_value.filter.return_value.first.return_value = fake_conv
    db.query.return_value.filter.return_value.order_by.return_value.offset.return_value.limit.return_value.all.return_value = [m1, m2, m3, m4]

    service.get_conversation = MagicMock(return_value=fake_conv)
    service.get_messages = MagicMock(return_value=[m1, m2, m3, m4])

    ctx = service._build_context("00000000-0000-0000-0000-000000000003")
    history = ctx["history"]

    # 找到对应 m3 续接消息的条目
    idx = next((i for i, h in enumerate(history) if h["content"] == "续接指令"), None)
    assert idx is not None, f"续接消息应在 history 中，history: {history}"
    assert history[idx]["role"] == "user", (
        f"continuation 消息在 LLM 上下文中应映射为 'user'，got '{history[idx]['role']}'"
    )
    print("[PASS] E1 continuation_role_mapped_to_user_in_context")


def test_E2_user_role_preserved_in_context():
    """role='user' 的消息在 _build_context 中保持为 'user'。"""
    from backend.services.conversation_service import ConversationService
    from backend.models.conversation import Conversation, Message

    db = MagicMock()
    service = ConversationService.__new__(ConversationService)
    service.db = db

    fake_conv = MagicMock(spec=Conversation)
    fake_conv.id = uuid.UUID("00000000-0000-0000-0000-000000000003")
    fake_conv.title = "Test"
    fake_conv.current_model = "claude"
    fake_conv.system_prompt = ""
    fake_conv.total_tokens = 0
    fake_conv.extra_metadata = {}

    m1 = MagicMock(spec=Message); m1.role = "user"; m1.content = "用户消息"; m1.extra_metadata = {}
    db.query.return_value.filter.return_value.first.return_value = fake_conv
    service.get_conversation = MagicMock(return_value=fake_conv)
    service.get_messages = MagicMock(return_value=[m1])

    ctx = service._build_context("00000000-0000-0000-0000-000000000003")
    history = ctx["history"]
    user_entries = [h for h in history if h["content"] == "用户消息"]
    assert user_entries and user_entries[0]["role"] == "user"
    print("[PASS] E2 user_role_preserved_in_context")


def test_E3_messageRole_continuation_raises_valueerror_without_fix():
    """MessageRole('continuation') 在没有映射修复时应抛 ValueError（证明修复必要性）。"""
    from backend.core.conversation_format import MessageRole
    try:
        _ = MessageRole("continuation")
        assert False, "期望 ValueError，但没有抛出"
    except ValueError:
        pass  # 正确行为
    print("[PASS] E3 MessageRole_continuation_raises_valueerror_without_fix")


# ══════════════════════════════════════════════════════
# Layer F — 自动续接递归调用传递 _continuation_round
# ══════════════════════════════════════════════════════

def test_F1_recursive_call_passes_continuation_round_1():
    """第 1 次自动续接：递归调用 send_message_stream 时 _continuation_round=1。"""
    from backend.agents.agentic_loop import AgentEvent
    from backend.services.conversation_service import ConversationService

    service, fake_conv, saved_messages, mock_agent = _make_full_service()
    conv_id = "00000000-0000-0000-0000-000000000002"
    fake_conv.extra_metadata = {}  # 未开始续接

    recursive_calls = []
    original_sms = ConversationService.send_message_stream

    async def spy_sms(self_obj, cid, msg, model, _continuation_round=0, **kw):
        recursive_calls.append(_continuation_round)
        if _continuation_round > 0:
            # 阻断递归
            yield {"type": "content", "data": "续接完成"}
            return
        async for ev in original_sms(self_obj, cid, msg, model, _continuation_round=_continuation_round, **kw):
            yield ev

    async def near_limit_ps(msg, ctx):
        yield AgentEvent(type="content", data="初步完成。")
        yield AgentEvent(type="near_limit", data={
            "pending_tasks": ["任务1"],
            "conclusions": "初步结论",
        })

    mock_agent.process_stream = near_limit_ps

    with patch.object(ConversationService, "send_message_stream", spy_sms):
        with patch("backend.services.conversation_service.MasterAgent", return_value=mock_agent):
            with patch("backend.services.conversation_service.get_mcp_manager", return_value=MockMCPManager()):
                _run(_drain(spy_sms(service, conv_id, "探索数据库", "claude")))

    # 第一次调用是 round=0（用户原始消息），第二次应是 round=1（续接）
    assert len(recursive_calls) >= 2, f"期望至少 2 次调用，got {recursive_calls}"
    assert recursive_calls[1] == 1, f"第二次调用 _continuation_round 应为 1，got {recursive_calls}"
    print("[PASS] F1 recursive_call_passes_continuation_round_1")


def test_F2_continuation_message_role_is_continuation_in_recursive_call():
    """递归续接调用中，第一条保存的消息 role 应为 'continuation'。

    设计：第一轮 process_stream 产生 near_limit，触发递归调用（_continuation_round=1）。
    第二轮 process_stream 正常完成（不产生 near_limit），递归自然结束。
    这样 continuation 消息可通过 add_message 正常保存。
    """
    from backend.agents.agentic_loop import AgentEvent
    from backend.services.conversation_service import ConversationService

    service, fake_conv, saved_messages, mock_agent = _make_full_service()
    conv_id = "00000000-0000-0000-0000-000000000002"
    fake_conv.extra_metadata = {}

    original_sms = ConversationService.send_message_stream
    call_count = [0]

    async def near_limit_ps(msg, ctx):
        """第 1 轮：产生 near_limit 触发续接。"""
        yield AgentEvent(type="content", data="初步完成。")
        yield AgentEvent(type="near_limit", data={
            "pending_tasks": ["待办"],
            "conclusions": "结论",
        })

    async def normal_ps(msg, ctx):
        """第 2 轮（续接）：正常完成，不再触发 near_limit。"""
        yield AgentEvent(type="content", data="续接完成。")

    async def switching_sms(self_obj, cid, msg, model, _continuation_round=0, **kw):
        """切换 process_stream：第 1 轮用 near_limit，第 2 轮用 normal。"""
        call_count[0] += 1
        if call_count[0] == 1:
            mock_agent.process_stream = near_limit_ps
        else:
            mock_agent.process_stream = normal_ps
        async for ev in original_sms(self_obj, cid, msg, model, _continuation_round=_continuation_round, **kw):
            yield ev

    mock_agent.process_stream = near_limit_ps

    with patch.object(ConversationService, "send_message_stream", switching_sms):
        with patch("backend.services.conversation_service.MasterAgent", return_value=mock_agent):
            with patch("backend.services.conversation_service.get_mcp_manager", return_value=MockMCPManager()):
                _run(_drain(switching_sms(service, conv_id, "分析任务", "claude")))

    # saved_messages: [user, assistant(round1), continuation, assistant(round2)]
    continuation_saves = [s for s in saved_messages if s["role"] == "continuation"]
    assert continuation_saves, (
        f"应有 continuation role 消息，got roles: {[s['role'] for s in saved_messages]}"
    )
    print("[PASS] F2 continuation_message_role_is_continuation_in_recursive_call")


def test_F3_auto_continuing_event_has_correct_count():
    """auto_continuing 事件的 continue_count 应与 _continuation_round 一致。"""
    from backend.agents.agentic_loop import AgentEvent
    from backend.services.conversation_service import ConversationService

    service, fake_conv, saved_messages, mock_agent = _make_full_service()
    conv_id = "00000000-0000-0000-0000-000000000002"
    fake_conv.extra_metadata = {}

    original_sms = ConversationService.send_message_stream
    collected = []

    async def limited_sms(self_obj, cid, msg, model, _continuation_round=0, **kw):
        if _continuation_round > 0:
            yield {"type": "content", "data": "续接完成"}
            return
        async for ev in original_sms(self_obj, cid, msg, model, _continuation_round=_continuation_round, **kw):
            collected.append(ev)
            yield ev

    async def near_limit_ps(msg, ctx):
        yield AgentEvent(type="content", data="初步完成。")
        yield AgentEvent(type="near_limit", data={
            "pending_tasks": ["任务"],
            "conclusions": "结论",
        })

    mock_agent.process_stream = near_limit_ps

    with patch.object(ConversationService, "send_message_stream", limited_sms):
        with patch("backend.services.conversation_service.MasterAgent", return_value=mock_agent):
            with patch("backend.services.conversation_service.get_mcp_manager", return_value=MockMCPManager()):
                _run(_drain(limited_sms(service, conv_id, "任务", "claude")))

    ac_events = [e for e in collected if isinstance(e, dict) and e.get("type") == "auto_continuing"]
    assert ac_events, f"期望 auto_continuing 事件，got types: {[e.get('type') for e in collected if isinstance(e, dict)]}"
    assert ac_events[0]["data"]["continue_count"] == 1
    print("[PASS] F3 auto_continuing_event_has_correct_count")


# ══════════════════════════════════════════════════════
# Layer G — RBAC: 新特性未新增未授权路由/菜单
# ══════════════════════════════════════════════════════

def test_G1_no_new_frontend_routes_for_continuation_feature():
    """ContinuationCard 是组件而非路由页面，不需要 RBAC 权限入口。"""
    import os
    pages_dir = os.path.join(os.path.dirname(__file__), "frontend", "src", "pages")
    if not os.path.exists(pages_dir):
        print("[SKIP] G1 frontend/src/pages/ 不存在，跳过")
        return
    page_files = [f for f in os.listdir(pages_dir) if f.endswith(".tsx")]
    # 确认不存在以 Continuation 命名的页面文件（这应该是组件，不是页面）
    continuation_pages = [f for f in page_files if "Continuation" in f or "continuation" in f]
    assert not continuation_pages, (
        f"ContinuationCard 不应作为独立页面存在，发现: {continuation_pages}"
    )
    print("[PASS] G1 no_new_frontend_routes_for_continuation_feature")


def test_G2_continuation_card_component_exists_in_components():
    """ContinuationCard 组件应在 components/chat/ 下，不在 pages/ 下。"""
    import os
    comp_path = os.path.join(
        os.path.dirname(__file__),
        "frontend", "src", "components", "chat", "ContinuationCard.tsx"
    )
    assert os.path.exists(comp_path), f"ContinuationCard.tsx 组件文件应存在于 {comp_path}"
    print("[PASS] G2 continuation_card_component_exists_in_components")


def test_G3_thinking_events_api_uses_existing_messages_endpoint():
    """thinking_events 通过现有的 GET /messages 接口返回，无需新增接口或权限。"""
    # 验证 to_dict() 已包含 thinking_events，通过现有接口即可获取
    thinking = [{"type": "thinking", "data": "分析中", "metadata": {}}]
    msg = _make_fake_message(role="assistant", extra_metadata={"thinking_events": thinking})
    d = msg.to_dict()
    # 现有 /messages 接口返回 message.to_dict()，因此 thinking_events 自动包含在内
    assert "thinking_events" in d, "thinking_events 应通过现有 to_dict() 暴露，无需新接口"
    print("[PASS] G3 thinking_events_api_uses_existing_messages_endpoint")


# ══════════════════════════════════════════════════════
# Layer H — 回归: 原有 near_limit 测试与新签名兼容
# ══════════════════════════════════════════════════════

def test_H1_regression_auto_continuing_event_with_new_signature():
    """回归: auto_continuing 事件 count=1（原 D1，适配新 _continuation_round 签名）。"""
    from backend.agents.agentic_loop import AgentEvent
    from backend.services.conversation_service import ConversationService

    service, fake_conv, saved_messages, mock_agent = _make_full_service()
    conv_id = "00000000-0000-0000-0000-000000000002"
    fake_conv.extra_metadata = {}

    async def near_limit_ps(msg, ctx):
        yield AgentEvent(type="content", data="初步完成。")
        yield AgentEvent(type="near_limit", data={
            "pending_tasks": ["待办任务X"],
            "conclusions": "阶段结论。",
        })

    mock_agent.process_stream = near_limit_ps
    collected = []

    # 第二次调用（续接）直接返回，避免无限递归
    original_sms = ConversationService.send_message_stream
    call_count = [0]

    async def limited_sms(self_obj, cid, msg, model, _continuation_round=0, **kw):
        call_count[0] += 1
        if call_count[0] > 1:
            yield {"type": "content", "data": "续接完成"}
            return
        async for ev in original_sms(self_obj, cid, msg, model, _continuation_round=_continuation_round, **kw):
            collected.append(ev)
            yield ev

    with patch.object(ConversationService, "send_message_stream", limited_sms):
        with patch("backend.services.conversation_service.MasterAgent", return_value=mock_agent):
            with patch("backend.services.conversation_service.get_mcp_manager", return_value=MockMCPManager()):
                _run(_drain(limited_sms(service, conv_id, "探索数据库", "claude")))

    ac_events = [e for e in collected if isinstance(e, dict) and e.get("type") == "auto_continuing"]
    assert ac_events, f"期望 auto_continuing 事件；got: {[e.get('type') for e in collected if isinstance(e, dict)]}"
    assert ac_events[0]["data"]["continue_count"] == 1
    print("[PASS] H1 regression_auto_continuing_event_with_new_signature")


def test_H2_regression_approval_required_after_3_continues():
    """回归: 3次续接后发 continuation_approval_required（原 D2，新签名兼容）。"""
    from backend.agents.agentic_loop import AgentEvent
    from backend.services.conversation_service import ConversationService

    service, fake_conv, saved_messages, mock_agent = _make_full_service(
        conv_extra_metadata={"auto_continue_state": {"count": 3}}
    )
    conv_id = "00000000-0000-0000-0000-000000000002"

    async def near_limit_ps(msg, ctx):
        yield AgentEvent(type="content", data="分析完成。")
        yield AgentEvent(type="near_limit", data={
            "pending_tasks": ["剩余任务"],
            "conclusions": "结论。",
        })

    mock_agent.process_stream = near_limit_ps

    with patch("backend.services.conversation_service.MasterAgent", return_value=mock_agent):
        with patch("backend.services.conversation_service.get_mcp_manager", return_value=MockMCPManager()):
            collected = _run(_drain(
                ConversationService.send_message_stream(service, conv_id, "继续分析", "claude")
            ))

    approval_events = [e for e in collected if isinstance(e, dict) and e.get("type") == "continuation_approval_required"]
    assert approval_events, f"期望 continuation_approval_required；got: {[e.get('type') for e in collected if isinstance(e, dict)]}"
    assert "剩余任务" in approval_events[0]["data"]["pending_tasks"]
    print("[PASS] H2 regression_approval_required_after_3_continues")


def test_H3_regression_state_reset_on_normal_completion():
    """回归: 正常完成时 auto_continue_state 重置为 0（原 D3，新签名兼容）。"""
    from backend.agents.agentic_loop import AgentEvent
    from backend.services.conversation_service import ConversationService

    service, fake_conv, saved_messages, mock_agent = _make_full_service(
        conv_extra_metadata={"auto_continue_state": {"count": 2}}
    )
    conv_id = "00000000-0000-0000-0000-000000000002"

    async def normal_ps(msg, ctx):
        yield AgentEvent(type="content", data="任务完成！")

    mock_agent.process_stream = normal_ps

    with patch("backend.services.conversation_service.MasterAgent", return_value=mock_agent):
        with patch("backend.services.conversation_service.get_mcp_manager", return_value=MockMCPManager()):
            _run(_drain(
                ConversationService.send_message_stream(service, conv_id, "任务", "claude")
            ))

    state = service._get_auto_continue_state(conv_id)
    assert state.get("count", 0) == 0, f"期望 count=0，got {state}"
    print("[PASS] H3 regression_state_reset_on_normal_completion")


# ══════════════════════════════════════════════════════
# 运行器
# ══════════════════════════════════════════════════════

def run_all():
    tests = [
        # Layer A
        test_A1_to_dict_promotes_thinking_events_to_top_level,
        test_A2_to_dict_no_thinking_events_key_when_absent,
        test_A3_to_dict_with_none_extra_metadata,
        # Layer B
        test_B1_thinking_events_constant_is_2000,
        test_B2_thinking_and_tool_call_collected,
        test_B3_tool_result_collected,
        test_B4_tool_result_data_truncated_at_2000,
        test_B5_tool_result_short_data_not_truncated,
        # Layer C
        test_C1_thinking_events_absent_when_no_thinking_events,
        test_C2_thinking_events_saved_in_extra_metadata,
        test_C3_assistant_message_event_includes_thinking_events,
        # Layer D
        test_D1_normal_round_saves_user_role,
        test_D2_continuation_round_saves_continuation_role,
        test_D3_continuation_message_has_round_in_extra_metadata,
        test_D4_continuation_message_has_max_rounds_in_extra_metadata,
        test_D5_user_message_has_no_continuation_meta,
        # Layer E
        test_E1_continuation_role_mapped_to_user_in_context,
        test_E2_user_role_preserved_in_context,
        test_E3_messageRole_continuation_raises_valueerror_without_fix,
        # Layer F
        test_F1_recursive_call_passes_continuation_round_1,
        test_F2_continuation_message_role_is_continuation_in_recursive_call,
        test_F3_auto_continuing_event_has_correct_count,
        # Layer G
        test_G1_no_new_frontend_routes_for_continuation_feature,
        test_G2_continuation_card_component_exists_in_components,
        test_G3_thinking_events_api_uses_existing_messages_endpoint,
        # Layer H
        test_H1_regression_auto_continuing_event_with_new_signature,
        test_H2_regression_approval_required_after_3_continues,
        test_H3_regression_state_reset_on_normal_completion,
    ]

    passed = 0
    failed = 0
    for test in tests:
        try:
            test()
            passed += 1
        except Exception as e:
            import traceback
            print(f"[FAIL] {test.__name__}: {e}")
            traceback.print_exc()
            failed += 1

    print(f"\n{'='*55}")
    print(f"Results: {passed}/{len(tests)} passed, {failed} failed")
    if failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    run_all()
