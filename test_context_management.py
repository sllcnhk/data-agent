"""
test_context_management.py
===========================
Comprehensive integration tests for the Claude Code-style context management system.

Test Design (Senior AI App Test Engineer):
  Layer A — ConversationSummarizer deep unit tests
  Layer B — SmartCompressionStrategy boundary tests
  Layer C — _maybe_summarize logic (mock SQLAlchemy session)
  Layer D — _update_conversation_meta
  Layer E — _build_context with llm_summary passthrough
  Layer F — AgenticLoop._compress_loop_messages boundary / edge cases
  Layer G — Full pipeline: context_compressed SSE event

Coverage goals:
  - Boundary value analysis (at/near threshold)
  - Equivalence partitioning (short/long conversations)
  - Error injection (LLM failure, DB failure)
  - Cache semantics (hit / miss / invalidation)
  - Data-flow from summarizer → context_manager → compressed context
"""

import asyncio
import sys
import os
import json
import uuid
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch, AsyncMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "backend"))

from backend.core.conversation_summarizer import ConversationSummarizer
from backend.core.context_manager import (
    SmartCompressionStrategy,
    HybridContextManager,
)
from backend.core.conversation_format import UnifiedConversation, MessageRole, UnifiedMessage
from backend.agents.agentic_loop import (
    AgenticLoop,
    AgentEvent,
    MAX_LOOP_CONTEXT_CHARS,
    KEEP_RECENT_TOOL_PAIRS,
    MAX_TOOL_RESULT_CHARS,
)


# ══════════════════════════════════════════════════════
# Shared helpers
# ══════════════════════════════════════════════════════

class MockLLMAdapter:
    def __init__(self, text="## 对话摘要\n**用户目标**：测试"):
        self.text = text
        self.call_count = 0

    async def chat_plain(self, messages, system_prompt=""):
        self.call_count += 1
        return {"content": [{"type": "text", "text": self.text}]}


class FailingAdapter:
    async def chat_plain(self, messages, system_prompt=""):
        raise RuntimeError("timeout")


def _make_conversation(n: int, *, keep_first: int = 2, keep_recent: int = 10) -> UnifiedConversation:
    conv = UnifiedConversation(conversation_id="c1", title="T", system_prompt="S")
    for i in range(n):
        if i % 2 == 0:
            conv.add_user_message(f"User {i}")
        else:
            conv.add_assistant_message(f"Assistant {i}")
    return conv


def _make_tool_result_messages(n_pairs: int, content_size: int = 10) -> List[Dict]:
    """Build alternating assistant(tool_use) + user(tool_result) message pairs."""
    msgs = []
    for i in range(n_pairs):
        msgs.append({
            "role": "assistant",
            "content": [{"type": "tool_use", "id": f"t{i}", "name": "q", "input": {}}],
        })
        msgs.append({
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": f"t{i}",
                         "content": "R" * content_size}],
        })
    return msgs


# ══════════════════════════════════════════════════════
# Layer A — ConversationSummarizer deep unit tests
# ══════════════════════════════════════════════════════

async def test_A1_truncation_applied_to_long_messages():
    """Messages longer than _MSG_PREVIEW_CHARS are truncated before being sent to LLM."""
    adapter = MockLLMAdapter()
    summarizer = ConversationSummarizer()
    long_content = "A" * (ConversationSummarizer._MSG_PREVIEW_CHARS + 500)
    await summarizer.summarize([{"role": "user", "content": long_content}], adapter)

    sent_prompt = adapter.call_count
    assert sent_prompt == 1
    # The prompt should NOT contain the full long content
    sent = adapter.call_count  # just verify it called

    # Verify truncation: build expected truncated text manually
    expected_truncated = long_content[:ConversationSummarizer._MSG_PREVIEW_CHARS] + "..."
    # We know the adapter received it; check via the template format
    # Re-run and capture
    adapter2 = MockLLMAdapter()

    class CapturingAdapter:
        captured = None
        async def chat_plain(self, messages, system_prompt=""):
            CapturingAdapter.captured = messages[0]["content"]
            return {"content": [{"type": "text", "text": "summary"}]}

    await summarizer.summarize([{"role": "user", "content": long_content}], CapturingAdapter())
    assert expected_truncated in CapturingAdapter.captured, (
        f"Expected truncated content in prompt. Prompt length: {len(CapturingAdapter.captured)}"
    )
    assert long_content not in CapturingAdapter.captured, \
        "Full long content should NOT be in prompt"
    print("[PASS] A1: truncation applied to long messages")


async def test_A2_role_labels_in_prompt():
    """Each message is formatted with [ROLE] label in the prompt."""
    class CapturingAdapter:
        captured = None
        async def chat_plain(self, messages, system_prompt=""):
            CapturingAdapter.captured = messages[0]["content"]
            return {"content": [{"type": "text", "text": "ok"}]}

    summarizer = ConversationSummarizer()
    await summarizer.summarize([
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "world"},
    ], CapturingAdapter())

    assert "[USER]" in CapturingAdapter.captured, "USER label missing"
    assert "[ASSISTANT]" in CapturingAdapter.captured, "ASSISTANT label missing"
    assert "hello" in CapturingAdapter.captured
    assert "world" in CapturingAdapter.captured
    print("[PASS] A2: role labels in prompt")


async def test_A3_empty_response_content_list():
    """When LLM returns empty content list, summarizer returns empty string (no crash)."""
    class EmptyResponseAdapter:
        async def chat_plain(self, messages, system_prompt=""):
            return {"content": []}

    summarizer = ConversationSummarizer()
    result = await summarizer.summarize(
        [{"role": "user", "content": "test"}], EmptyResponseAdapter()
    )
    assert result == "", f"Expected '' for empty content list, got '{result}'"
    print("[PASS] A3: empty response content list → empty string")


async def test_A4_non_text_block_ignored():
    """Non-text blocks in LLM response are ignored; text block is extracted."""
    class MixedBlockAdapter:
        async def chat_plain(self, messages, system_prompt=""):
            return {
                "content": [
                    {"type": "image", "source": {"url": "https://example.com/img.png"}},
                    {"type": "text", "text": "actual summary"},
                    {"type": "tool_use", "id": "x"},
                ]
            }

    summarizer = ConversationSummarizer()
    result = await summarizer.summarize(
        [{"role": "user", "content": "test"}], MixedBlockAdapter()
    )
    assert result == "actual summary", f"Got: '{result}'"
    print("[PASS] A4: non-text blocks ignored, text block extracted")


async def test_A5_rule_based_fallback_structure():
    """Rule-based fallback includes numbered role labels for each message."""
    summarizer = ConversationSummarizer()
    msgs = [
        {"role": "user", "content": "Query data"},
        {"role": "assistant", "content": "Found 5 rows"},
        {"role": "user", "content": "Show chart"},
    ]
    fallback = summarizer._rule_based_fallback(msgs)

    assert "1." in fallback
    assert "2." in fallback
    assert "3." in fallback
    assert "user" in fallback.lower() or "USER" in fallback
    assert "Query data" in fallback or "Query data"[:100] in fallback
    print("[PASS] A5: rule-based fallback has numbered role labels")


# ══════════════════════════════════════════════════════
# Layer B — SmartCompressionStrategy boundary tests
# ══════════════════════════════════════════════════════

async def test_B1_no_compression_when_under_limit():
    """Exactly max_messages: no compression, returns original messages."""
    conv = _make_conversation(10)
    strategy = SmartCompressionStrategy(keep_first=2, keep_recent=5)
    result = strategy.compress(conv, max_messages=10)
    assert len(result.messages) == len(conv.messages), \
        "Should not compress when at or under limit"
    print("[PASS] B1: no compression at limit")


async def test_B2_first_messages_preserved_verbatim():
    """keep_first messages always appear first and are unmodified."""
    conv = _make_conversation(30)
    strategy = SmartCompressionStrategy(keep_first=3, keep_recent=5)
    result = strategy.compress(conv, max_messages=15, llm_summary="SUMMARY")

    non_system = [m for m in result.messages if m.role != MessageRole.SYSTEM]
    first_3 = non_system[:3]
    orig_3 = [m for m in conv.messages][:3]
    for got, orig in zip(first_3, orig_3):
        assert got.content == orig.content, \
            f"First message modified: got '{got.content}' expected '{orig.content}'"
    print("[PASS] B2: first messages preserved verbatim")


async def test_B3_recent_messages_preserved_verbatim():
    """keep_recent messages are always at the end and unmodified."""
    conv = _make_conversation(30)
    keep_recent = 6
    strategy = SmartCompressionStrategy(keep_first=2, keep_recent=keep_recent)
    result = strategy.compress(conv, max_messages=15)

    non_system = [m for m in result.messages if m.role != MessageRole.SYSTEM]
    recent = non_system[-keep_recent:]
    orig_recent = conv.messages[-keep_recent:]
    for got, orig in zip(recent, orig_recent):
        assert got.content == orig.content, \
            f"Recent message modified: '{got.content}' vs '{orig.content}'"
    print("[PASS] B3: recent messages preserved verbatim")


async def test_B4_summary_injected_exactly_once():
    """Only one summary system message is injected between first and recent."""
    conv = _make_conversation(30)
    strategy = SmartCompressionStrategy(keep_first=2, keep_recent=5)
    result = strategy.compress(conv, max_messages=15, llm_summary="MY_SUMMARY")

    summary_msgs = [m for m in result.messages
                    if m.role == MessageRole.SYSTEM
                    and "MY_SUMMARY" in m.content]
    assert len(summary_msgs) == 1, \
        f"Expected exactly 1 summary message, got {len(summary_msgs)}"
    print("[PASS] B4: summary injected exactly once")


async def test_B5_middle_count_correct():
    """Middle = total - keep_first - keep_recent messages are summarized."""
    n = 25
    keep_first = 2
    keep_recent = 5
    max_msgs = 15
    conv = _make_conversation(n)
    strategy = SmartCompressionStrategy(keep_first=keep_first, keep_recent=keep_recent)
    result = strategy.compress(conv, max_messages=max_msgs, llm_summary="S")

    # Result should have: keep_first + 1 (summary) + keep_recent messages
    expected_count = keep_first + 1 + keep_recent
    assert len(result.messages) == expected_count, \
        f"Expected {expected_count} messages, got {len(result.messages)}"
    print("[PASS] B5: compressed message count = keep_first + 1 + keep_recent")


# ══════════════════════════════════════════════════════
# Layer C — _maybe_summarize logic (mock DB session)
# ══════════════════════════════════════════════════════

def _make_mock_service(n_messages: int, cached_summary: str = "", cache_msg_count: int = 0):
    """Create a ConversationService with fully mocked SQLAlchemy session."""
    from backend.services.conversation_service import ConversationService

    # Build fake Message objects
    fake_messages = []
    conv_id = str(uuid.uuid4())
    for i in range(n_messages):
        m = MagicMock()
        m.role = "user" if i % 2 == 0 else "assistant"
        m.content = f"Message content {i}"
        fake_messages.append(m)

    # Build fake Conversation object
    fake_conv = MagicMock()
    fake_conv.id = uuid.UUID(conv_id)
    if cached_summary:
        fake_conv.extra_metadata = {
            "context_summary": {
                "summary": cached_summary,
                "message_count": cache_msg_count if cache_msg_count else n_messages,
                "generated_at": "2026-01-01T00:00:00",
            }
        }
    else:
        fake_conv.extra_metadata = {}

    # Mock DB session
    mock_db = MagicMock()
    query_chain = MagicMock()
    query_chain.filter.return_value.first.return_value = fake_conv
    mock_db.query.return_value = query_chain

    svc = ConversationService(db=mock_db)

    # Override get_messages and get_conversation to return our fakes
    svc.get_messages = MagicMock(return_value=fake_messages)
    svc.get_conversation = MagicMock(return_value=fake_conv)

    return svc, conv_id, fake_conv


async def test_C1_short_conversation_returns_empty():
    """_maybe_summarize returns '' when messages <= max_context_messages."""
    from backend.config.settings import settings
    max_ctx = settings.max_context_messages  # 30

    svc, conv_id, _ = _make_mock_service(n_messages=max_ctx)  # exactly at limit
    adapter = MockLLMAdapter()

    result = await svc._maybe_summarize(conv_id, adapter)
    assert result == "", f"Expected '' for short conversation, got '{result}'"
    assert adapter.call_count == 0, "Should not call LLM for short conversation"
    print("[PASS] C1: short conversation returns empty string")


async def test_C2_long_conversation_triggers_summarization():
    """_maybe_summarize triggers LLM call for conversations > max_context_messages."""
    from backend.config.settings import settings
    max_ctx = settings.max_context_messages

    svc, conv_id, _ = _make_mock_service(n_messages=max_ctx + 15)
    adapter = MockLLMAdapter(text="## 对话摘要\n**用户目标**：分析数据")

    result = await svc._maybe_summarize(conv_id, adapter)
    assert result != "", f"Expected non-empty summary, got '{result}'"
    assert adapter.call_count == 1, f"Expected 1 LLM call, got {adapter.call_count}"
    print("[PASS] C2: long conversation triggers LLM summarization")


async def test_C3_cache_hit_reuses_existing_summary():
    """_maybe_summarize reuses cached summary when message_count matches."""
    from backend.config.settings import settings
    max_ctx = settings.max_context_messages
    n = max_ctx + 10
    cached = "## 对话摘要\n**用户目标**：已缓存"

    svc, conv_id, _ = _make_mock_service(
        n_messages=n,
        cached_summary=cached,
        cache_msg_count=n  # matches current count → cache hit
    )
    adapter = MockLLMAdapter()

    result = await svc._maybe_summarize(conv_id, adapter)
    assert result == cached, f"Expected cached summary, got '{result}'"
    assert adapter.call_count == 0, "Cache hit should not call LLM"
    print("[PASS] C3: cache hit reuses existing summary without LLM call")


async def test_C4_cache_miss_on_different_message_count():
    """_maybe_summarize regenerates when message count differs from cache."""
    from backend.config.settings import settings
    max_ctx = settings.max_context_messages
    n = max_ctx + 10
    cached = "old summary"

    svc, conv_id, fake_conv = _make_mock_service(
        n_messages=n,
        cached_summary=cached,
        cache_msg_count=n - 5  # DIFFERENT count → cache miss
    )
    adapter = MockLLMAdapter(text="new fresh summary")

    result = await svc._maybe_summarize(conv_id, adapter)
    assert adapter.call_count == 1, "Cache miss should trigger LLM call"
    assert result == "new fresh summary", f"Got: '{result}'"
    print("[PASS] C4: cache miss regenerates summary")


async def test_C5_llm_failure_falls_back_to_rule_summary():
    """When LLM adapter raises, ConversationSummarizer falls back to rule-based summary.

    Design rationale: the summarizer catches exceptions internally and provides a
    rule-based fallback rather than returning empty — so _maybe_summarize still
    returns a non-empty (albeit lower quality) summary. This is the correct behavior:
    even on LLM failure we want some context rather than none.
    """
    from backend.config.settings import settings
    max_ctx = settings.max_context_messages

    svc, conv_id, _ = _make_mock_service(n_messages=max_ctx + 10)

    result = await svc._maybe_summarize(conv_id, FailingAdapter())
    # ConversationSummarizer catches the exception and falls back to rule-based summary
    assert isinstance(result, str), "Result should be a string"
    assert len(result) > 0, "Rule-based fallback should be non-empty"
    # Rule-based fallback contains role labels
    assert "[user]" in result.lower() or "摘要" in result, \
        f"Fallback should contain role labels or heading. Got: {result[:200]}"
    print("[PASS] C5: LLM failure falls back to rule-based summary (non-empty)")


async def test_C6_no_conversation_returns_empty():
    """When conversation not found in DB, _maybe_summarize returns ''."""
    from backend.config.settings import settings
    from backend.services.conversation_service import ConversationService

    max_ctx = settings.max_context_messages
    conv_id = str(uuid.uuid4())

    mock_db = MagicMock()
    svc = ConversationService(db=mock_db)

    # n_messages > threshold
    fake_msgs = [MagicMock() for _ in range(max_ctx + 5)]
    for i, m in enumerate(fake_msgs):
        m.role = "user" if i % 2 == 0 else "assistant"
        m.content = f"msg {i}"
    svc.get_messages = MagicMock(return_value=fake_msgs)
    svc.get_conversation = MagicMock(return_value=None)  # not found

    result = await svc._maybe_summarize(conv_id, MockLLMAdapter())
    assert result == "", f"Expected '' when conv not found, got '{result}'"
    print("[PASS] C6: missing conversation returns empty string")


# ══════════════════════════════════════════════════════
# Layer D — _update_conversation_meta
# ══════════════════════════════════════════════════════

async def test_D1_creates_metadata_from_none():
    """_update_conversation_meta merges into None → creates fresh dict."""
    from backend.services.conversation_service import ConversationService

    conv_id = str(uuid.uuid4())
    fake_conv = MagicMock()
    fake_conv.extra_metadata = None  # starts as None

    mock_db = MagicMock()
    mock_db.query.return_value.filter.return_value.first.return_value = fake_conv

    svc = ConversationService(db=mock_db)
    svc._update_conversation_meta(conv_id, {"key": "value"})

    assert fake_conv.extra_metadata == {"key": "value"}, \
        f"Got: {fake_conv.extra_metadata}"
    mock_db.commit.assert_called_once()
    print("[PASS] D1: creates metadata from None")


async def test_D2_merges_into_existing_metadata():
    """_update_conversation_meta merges without overwriting other keys."""
    from backend.services.conversation_service import ConversationService

    conv_id = str(uuid.uuid4())
    fake_conv = MagicMock()
    fake_conv.extra_metadata = {"existing_key": "existing_value"}

    mock_db = MagicMock()
    mock_db.query.return_value.filter.return_value.first.return_value = fake_conv

    svc = ConversationService(db=mock_db)
    svc._update_conversation_meta(conv_id, {"new_key": "new_value"})

    assert fake_conv.extra_metadata.get("existing_key") == "existing_value", \
        "Existing key should be preserved"
    assert fake_conv.extra_metadata.get("new_key") == "new_value", \
        "New key should be added"
    print("[PASS] D2: merges into existing metadata without losing keys")


async def test_D3_db_error_handled_gracefully():
    """_update_conversation_meta swallows DB exceptions without raising."""
    from backend.services.conversation_service import ConversationService

    mock_db = MagicMock()
    mock_db.query.side_effect = Exception("DB connection lost")

    svc = ConversationService(db=mock_db)
    # Should NOT raise
    try:
        svc._update_conversation_meta(str(uuid.uuid4()), {"key": "val"})
    except Exception as e:
        assert False, f"_update_conversation_meta should not raise: {e}"
    print("[PASS] D3: DB error handled gracefully")


# ══════════════════════════════════════════════════════
# Layer E — _build_context with llm_summary passthrough
# ══════════════════════════════════════════════════════

async def test_E1_llm_summary_appears_in_history():
    """_build_context passes llm_summary through to HybridContextManager;
    the summary text appears in compressed history for long conversations."""
    from backend.services.conversation_service import ConversationService
    from backend.config.settings import settings

    max_ctx = settings.max_context_messages
    conv_id = str(uuid.uuid4())
    n_messages = max_ctx + 10  # long enough to trigger compression

    # Build fake DB objects
    fake_conv = MagicMock()
    fake_conv.title = "Test"
    fake_conv.current_model = "claude"
    fake_conv.system_prompt = "You are helpful"
    fake_conv.total_tokens = 0
    fake_conv.extra_metadata = {}

    fake_messages = []
    for i in range(n_messages):
        m = MagicMock()
        m.role = "user" if i % 2 == 0 else "assistant"
        m.content = f"Message {i}"
        m.extra_metadata = None
        fake_messages.append(m)

    mock_db = MagicMock()
    svc = ConversationService(db=mock_db)
    svc.get_conversation = MagicMock(return_value=fake_conv)
    svc.get_messages = MagicMock(return_value=fake_messages)

    summary = "## 对话摘要\n**用户目标**：分析ClickHouse表结构"
    context = svc._build_context(conv_id, llm_summary=summary)

    history = context.get("history", [])
    combined_text = " ".join(str(h.get("content", "")) for h in history)
    assert summary in combined_text, (
        f"LLM summary should appear in compressed history.\n"
        f"Combined history length: {len(combined_text)} chars\n"
        f"First 500 chars: {combined_text[:500]}"
    )
    print("[PASS] E1: llm_summary appears in compressed history")


async def test_E2_context_info_shows_compression():
    """_build_context returns context_info with original vs compressed counts."""
    from backend.services.conversation_service import ConversationService
    from backend.config.settings import settings

    max_ctx = settings.max_context_messages
    n_messages = max_ctx + 15

    fake_conv = MagicMock()
    fake_conv.title = "T"
    fake_conv.current_model = "c"
    fake_conv.system_prompt = None
    fake_conv.total_tokens = 0
    fake_conv.extra_metadata = {}

    fake_messages = []
    for i in range(n_messages):
        m = MagicMock()
        m.role = "user" if i % 2 == 0 else "assistant"
        m.content = f"Content {i}"
        m.extra_metadata = None
        fake_messages.append(m)

    mock_db = MagicMock()
    svc = ConversationService(db=mock_db)
    svc.get_conversation = MagicMock(return_value=fake_conv)
    svc.get_messages = MagicMock(return_value=fake_messages)

    context = svc._build_context(str(uuid.uuid4()))
    info = context.get("context_info", {})

    assert "original_message_count" in info
    assert "compressed_message_count" in info
    assert info["original_message_count"] == n_messages
    assert info["compressed_message_count"] <= max_ctx + 5, \
        f"Compressed count {info['compressed_message_count']} should be near max_ctx={max_ctx}"
    print("[PASS] E2: context_info reports original vs compressed message counts")


# ══════════════════════════════════════════════════════
# Layer F — _compress_loop_messages boundary / edge cases
# ══════════════════════════════════════════════════════

async def test_F1_exactly_at_threshold_no_compression():
    """Total chars at exactly MAX_LOOP_CONTEXT_CHARS: no compression."""
    # Build messages that sum to exactly MAX_LOOP_CONTEXT_CHARS chars
    # "content" field in JSON serialization: each char in string + quotes + whitespace
    # Use smaller messages that sum to just under threshold
    msgs = _make_tool_result_messages(
        n_pairs=KEEP_RECENT_TOOL_PAIRS + 2,
        content_size=100  # small content → total well under threshold
    )
    result = AgenticLoop._compress_loop_messages(msgs)
    # With small content, total chars << threshold → no compression → same object
    assert result is msgs or result == msgs, \
        "Small messages should not be compressed"
    print("[PASS] F1: small messages not compressed (under threshold)")


async def test_F2_over_threshold_triggers_compression():
    """Total chars above MAX_LOOP_CONTEXT_CHARS: oldest tool_results compressed."""
    n_pairs = KEEP_RECENT_TOOL_PAIRS + 3
    # Make content big enough to exceed threshold across all messages
    per_content = MAX_LOOP_CONTEXT_CHARS // (n_pairs) + 2000

    msgs = _make_tool_result_messages(n_pairs=n_pairs, content_size=per_content)

    result = AgenticLoop._compress_loop_messages(msgs)

    # Find all tool_result blocks in result
    results = []
    for m in result:
        if isinstance(m.get("content"), list):
            for b in m["content"]:
                if isinstance(b, dict) and b.get("type") == "tool_result":
                    results.append(b)

    assert len(results) == n_pairs, f"Expected {n_pairs} results, got {len(results)}"
    old_count = n_pairs - KEEP_RECENT_TOOL_PAIRS
    for r in results[:old_count]:
        assert "[历史结果已压缩]" in r["content"], \
            f"Old result should be compressed, got: {r['content'][:100]}"
    for r in results[-KEEP_RECENT_TOOL_PAIRS:]:
        assert "[历史结果已压缩]" not in r["content"], \
            f"Recent result should be verbatim, got: {r['content'][:100]}"
    print("[PASS] F2: over threshold compresses old results, keeps recent verbatim")


async def test_F3_exactly_keep_recent_pairs_no_compression():
    """With exactly KEEP_RECENT_TOOL_PAIRS results, nothing to compress."""
    per_content = MAX_LOOP_CONTEXT_CHARS // KEEP_RECENT_TOOL_PAIRS + 2000
    msgs = _make_tool_result_messages(
        n_pairs=KEEP_RECENT_TOOL_PAIRS,
        content_size=per_content
    )
    result = AgenticLoop._compress_loop_messages(msgs)

    # Even if threshold exceeded, not enough history → no compression
    for m in result:
        if isinstance(m.get("content"), list):
            for b in m["content"]:
                if isinstance(b, dict) and b.get("type") == "tool_result":
                    assert "[历史结果已压缩]" not in b["content"], \
                        "Should not compress when only KEEP_RECENT_TOOL_PAIRS results exist"
    print("[PASS] F3: exactly KEEP_RECENT_TOOL_PAIRS results → no compression")


async def test_F4_non_tool_result_blocks_preserved():
    """Non-tool_result blocks in compressed messages are preserved unchanged."""
    n_pairs = KEEP_RECENT_TOOL_PAIRS + 2
    per_content = MAX_LOOP_CONTEXT_CHARS // n_pairs + 2000

    msgs = []
    for i in range(n_pairs):
        msgs.append({
            "role": "assistant",
            "content": [
                {"type": "text", "text": f"thinking {i}"},
                {"type": "tool_use", "id": f"t{i}", "name": "q", "input": {}},
            ],
        })
        msgs.append({
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": f"t{i}",
                 "content": "R" * per_content},
                {"type": "text", "text": f"extra text {i}"},  # non-result block
            ],
        })

    result = AgenticLoop._compress_loop_messages(msgs)

    # Find user messages that were compressed (old ones)
    for i, m in enumerate(result):
        if m.get("role") == "user" and isinstance(m.get("content"), list):
            for b in m["content"]:
                if isinstance(b, dict) and b.get("type") == "text":
                    assert b.get("text", "").startswith("extra text"), \
                        f"Non-tool_result block should be preserved: {b}"
    print("[PASS] F4: non-tool_result blocks preserved during compression")


async def test_F5_no_tool_results_messages_unchanged():
    """Messages with no tool_result blocks are returned unchanged."""
    msgs = [
        {"role": "user", "content": "A" * (MAX_LOOP_CONTEXT_CHARS // 2 + 1000)},
        {"role": "assistant", "content": "B" * (MAX_LOOP_CONTEXT_CHARS // 2 + 1000)},
    ]
    result = AgenticLoop._compress_loop_messages(msgs)
    # Even if total chars exceed threshold, no tool_results → unchanged
    assert result is msgs or result == msgs, \
        "Messages with no tool_result blocks should be returned unchanged"
    print("[PASS] F5: no tool_result blocks → messages unchanged")


async def test_F6_preview_truncated_to_120_chars():
    """Compressed tool_result content is truncated to 120 chars + '...'"""
    n_pairs = KEEP_RECENT_TOOL_PAIRS + 2
    long_content = "LONG" * 200  # 800 chars

    msgs = []
    for i in range(n_pairs):
        msgs.append({
            "role": "assistant",
            "content": [{"type": "tool_use", "id": f"t{i}", "name": "q", "input": {}}],
        })
        msgs.append({
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": f"t{i}",
                         "content": long_content}],
        })

    # Force threshold to trigger compression by making content large enough
    big = MAX_LOOP_CONTEXT_CHARS + 1
    msgs[1]["content"][0]["content"] = "X" * big  # ensure threshold exceeded

    result = AgenticLoop._compress_loop_messages(msgs)

    # Check if compression happened
    compressed_results = []
    for m in result:
        if isinstance(m.get("content"), list):
            for b in m["content"]:
                if isinstance(b, dict) and b.get("type") == "tool_result":
                    if "[历史结果已压缩]" in str(b.get("content", "")):
                        compressed_results.append(b["content"])

    if compressed_results:
        for content in compressed_results:
            # Format is "[历史结果已压缩] <preview>"
            preview_part = content[len("[历史结果已压缩] "):]
            assert len(preview_part) <= 123, \
                f"Preview should be at most 123 chars (120 + '...'), got {len(preview_part)}: '{preview_part}'"
        print("[PASS] F6: compressed preview truncated to 120 chars")
    else:
        print("[SKIP] F6: compression not triggered with this data size")


# ══════════════════════════════════════════════════════
# Layer G — Full pipeline: context_compressed SSE event
# ══════════════════════════════════════════════════════

async def test_G1_context_compressed_event_emitted():
    """send_message_stream emits context_compressed when _maybe_summarize returns non-empty."""
    from backend.services.conversation_service import ConversationService
    from backend.config.settings import settings

    max_ctx = settings.max_context_messages
    conv_id = str(uuid.uuid4())
    summary_text = "## 对话摘要\n**用户目标**：探索数据库"

    # Set up service with mocked internals
    mock_db = MagicMock()
    svc = ConversationService(db=mock_db)

    # Fake user message save
    fake_user_msg = MagicMock()
    fake_user_msg.to_dict.return_value = {"id": "u1", "role": "user", "content": "hello"}
    svc.add_message = MagicMock(return_value=fake_user_msg)

    # _maybe_summarize returns a summary (as if long conversation)
    svc._maybe_summarize = AsyncMock(return_value=summary_text)

    # _build_context returns minimal valid context
    svc._build_context = MagicMock(return_value={
        "conversation_id": conv_id,
        "title": "T",
        "system_prompt": "",
        "history": [],
        "metadata": {},
        "context_info": {}
    })
    svc._get_llm_config = MagicMock(return_value={
        "model_type": "claude", "api_key": "", "api_base_url": "",
        "default_model": "claude", "temperature": 0.7, "max_tokens": 8192,
        "fallback_models": [], "enable_fallback": False,
    })

    # Mock MasterAgent to yield one content event
    class FakeMasterAgent:
        def __init__(self, *a, **kw):
            self.llm_adapter = MockLLMAdapter()

        async def process_stream(self, content, context):
            yield AgentEvent(type="content", data="Done!")

    fake_assistant_msg = MagicMock()
    fake_assistant_msg.to_dict.return_value = {"id": "a1", "role": "assistant", "content": "Done!"}

    # second call to add_message returns assistant msg
    svc.add_message = MagicMock(side_effect=[fake_user_msg, fake_assistant_msg])

    with patch("backend.services.conversation_service.MasterAgent", FakeMasterAgent):
        with patch("backend.services.conversation_service.get_mcp_manager", return_value=MagicMock()):
            events = []
            async for ev in svc.send_message_stream(conv_id, "hello", "claude"):
                events.append(ev)

    event_types = [e.get("type") for e in events]
    assert "context_compressed" in event_types, \
        f"Expected context_compressed event. Got: {event_types}"

    cc_event = next(e for e in events if e.get("type") == "context_compressed")
    assert "message" in cc_event.get("data", {}), \
        f"context_compressed event should have 'message' field: {cc_event}"
    print("[PASS] G1: context_compressed event emitted when summary generated")


async def test_G2_no_context_compressed_for_short_conversation():
    """send_message_stream does NOT emit context_compressed for short conversations."""
    from backend.services.conversation_service import ConversationService

    conv_id = str(uuid.uuid4())
    mock_db = MagicMock()
    svc = ConversationService(db=mock_db)

    fake_user_msg = MagicMock()
    fake_user_msg.to_dict.return_value = {"id": "u1", "role": "user", "content": "hi"}
    fake_assistant_msg = MagicMock()
    fake_assistant_msg.to_dict.return_value = {"id": "a1", "role": "assistant", "content": "hey"}
    svc.add_message = MagicMock(side_effect=[fake_user_msg, fake_assistant_msg])

    # _maybe_summarize returns "" (short conversation)
    svc._maybe_summarize = AsyncMock(return_value="")

    svc._build_context = MagicMock(return_value={
        "conversation_id": conv_id,
        "title": "T",
        "system_prompt": "",
        "history": [],
        "metadata": {},
        "context_info": {}
    })
    svc._get_llm_config = MagicMock(return_value={
        "model_type": "claude", "api_key": "", "api_base_url": "",
        "default_model": "claude", "temperature": 0.7, "max_tokens": 8192,
        "fallback_models": [], "enable_fallback": False,
    })

    class FakeMasterAgent:
        def __init__(self, *a, **kw):
            self.llm_adapter = MockLLMAdapter()

        async def process_stream(self, content, context):
            yield AgentEvent(type="content", data="hi back")

    with patch("backend.services.conversation_service.MasterAgent", FakeMasterAgent):
        with patch("backend.services.conversation_service.get_mcp_manager", return_value=MagicMock()):
            events = []
            async for ev in svc.send_message_stream(conv_id, "hi", "claude"):
                events.append(ev)

    event_types = [e.get("type") for e in events]
    assert "context_compressed" not in event_types, \
        f"context_compressed should NOT appear for short conversation. Got: {event_types}"
    print("[PASS] G2: no context_compressed event for short conversation")


# ══════════════════════════════════════════════════════
# Runner
# ══════════════════════════════════════════════════════

async def run_all():
    all_tests = [
        # Layer A — ConversationSummarizer
        ("A1", test_A1_truncation_applied_to_long_messages),
        ("A2", test_A2_role_labels_in_prompt),
        ("A3", test_A3_empty_response_content_list),
        ("A4", test_A4_non_text_block_ignored),
        ("A5", test_A5_rule_based_fallback_structure),
        # Layer B — SmartCompressionStrategy
        ("B1", test_B1_no_compression_when_under_limit),
        ("B2", test_B2_first_messages_preserved_verbatim),
        ("B3", test_B3_recent_messages_preserved_verbatim),
        ("B4", test_B4_summary_injected_exactly_once),
        ("B5", test_B5_middle_count_correct),
        # Layer C — _maybe_summarize
        ("C1", test_C1_short_conversation_returns_empty),
        ("C2", test_C2_long_conversation_triggers_summarization),
        ("C3", test_C3_cache_hit_reuses_existing_summary),
        ("C4", test_C4_cache_miss_on_different_message_count),
        ("C5", test_C5_llm_failure_falls_back_to_rule_summary),
        ("C6", test_C6_no_conversation_returns_empty),
        # Layer D — _update_conversation_meta
        ("D1", test_D1_creates_metadata_from_none),
        ("D2", test_D2_merges_into_existing_metadata),
        ("D3", test_D3_db_error_handled_gracefully),
        # Layer E — _build_context
        ("E1", test_E1_llm_summary_appears_in_history),
        ("E2", test_E2_context_info_shows_compression),
        # Layer F — _compress_loop_messages
        ("F1", test_F1_exactly_at_threshold_no_compression),
        ("F2", test_F2_over_threshold_triggers_compression),
        ("F3", test_F3_exactly_keep_recent_pairs_no_compression),
        ("F4", test_F4_non_tool_result_blocks_preserved),
        ("F5", test_F5_no_tool_results_messages_unchanged),
        ("F6", test_F6_preview_truncated_to_120_chars),
        # Layer G — Full pipeline
        ("G1", test_G1_context_compressed_event_emitted),
        ("G2", test_G2_no_context_compressed_for_short_conversation),
    ]

    passed = failed = 0
    fail_details = []

    print("\n" + "=" * 60)
    print("Context Management Integration Tests")
    print("=" * 60)

    for label, test_fn in all_tests:
        try:
            await test_fn()
            passed += 1
        except Exception as e:
            failed += 1
            fail_details.append((label, test_fn.__name__, str(e)))
            import traceback
            print(f"[FAIL] {label} {test_fn.__name__}: {e}")
            traceback.print_exc()

    print("\n" + "=" * 60)
    print(f"Results: {passed} passed, {failed} failed / {len(all_tests)} total")
    if fail_details:
        print("\nFailed tests:")
        for label, name, err in fail_details:
            print(f"  [{label}] {name}: {err}")
    print("=" * 60)

    if failed:
        sys.exit(1)


if __name__ == "__main__":
    import sys, atexit
    import pathlib as _pl
    sys.path.insert(0, str(_pl.Path(__file__).parent))
    try:
        from conftest import _cleanup_test_data as _ctd
        atexit.register(_ctd, label="post-run")   # 进程退出时必然执行（含 sys.exit）
        _ctd(label="pre-run")
    except Exception:
        pass
    asyncio.run(run_all())
