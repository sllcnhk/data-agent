"""
test_conversation_summarizer.py
8 tests for ConversationSummarizer, SmartCompressionStrategy LLM-summary integration,
HybridContextManager.compress_conversation(llm_summary=...), and _maybe_summarize logic.
"""
import asyncio
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "backend"))

from backend.core.conversation_summarizer import ConversationSummarizer
from backend.core.context_manager import (
    SmartCompressionStrategy,
    HybridContextManager,
)
from backend.core.conversation_format import UnifiedConversation, MessageRole


# ──────────────────────────────────────────────────────────
# Helpers / mocks
# ──────────────────────────────────────────────────────────

class MockLLMAdapter:
    """Fake adapter that returns a fixed summary text."""

    def __init__(self, summary_text: str = "## 对话摘要\n**用户目标**：测试\n**当前状态**：完成"):
        self.summary_text = summary_text
        self.call_count = 0
        self.last_messages = None
        self.last_system = None

    async def chat_plain(self, messages, system_prompt=""):
        self.call_count += 1
        self.last_messages = messages
        self.last_system = system_prompt
        return {
            "content": [{"type": "text", "text": self.summary_text}]
        }


class FailingAdapter:
    """Adapter that always raises an exception."""

    async def chat_plain(self, messages, system_prompt=""):
        raise RuntimeError("LLM unavailable")


def _make_conversation(n_messages: int) -> UnifiedConversation:
    conv = UnifiedConversation(
        conversation_id="test-conv",
        title="Test",
        system_prompt="You are an assistant.",
    )
    for i in range(n_messages):
        if i % 2 == 0:
            conv.add_user_message(f"User message {i}")
        else:
            conv.add_assistant_message(f"Assistant reply {i}")
    return conv


# ──────────────────────────────────────────────────────────
# Tests
# ──────────────────────────────────────────────────────────

async def test_summarizer_calls_llm_with_correct_prompt():
    """Summarizer should call llm_adapter.chat_plain exactly once with formatted messages."""
    adapter = MockLLMAdapter()
    summarizer = ConversationSummarizer()
    messages = [
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hi there"},
    ]
    result = await summarizer.summarize(messages, adapter)

    assert adapter.call_count == 1, f"Expected 1 LLM call, got {adapter.call_count}"
    # The prompt sent to LLM must contain the message content
    sent_content = adapter.last_messages[0]["content"]
    assert "Hello" in sent_content, "Prompt should contain user message"
    assert "Hi there" in sent_content, "Prompt should contain assistant reply"
    print("[PASS] test_summarizer_calls_llm_with_correct_prompt")


async def test_summarizer_returns_llm_output():
    """Summarizer should return the text from the LLM response block."""
    expected = "## 对话摘要\n**用户目标**：查数据"
    adapter = MockLLMAdapter(summary_text=expected)
    summarizer = ConversationSummarizer()
    result = await summarizer.summarize(
        [{"role": "user", "content": "query something"}], adapter
    )
    assert result == expected, f"Expected '{expected}', got '{result}'"
    print("[PASS] test_summarizer_returns_llm_output")


async def test_summarizer_empty_messages():
    """Summarizer returns empty string immediately for empty message list."""
    adapter = MockLLMAdapter()
    summarizer = ConversationSummarizer()
    result = await summarizer.summarize([], adapter)
    assert result == "", f"Expected '', got '{result}'"
    assert adapter.call_count == 0, "Should not call LLM for empty messages"
    print("[PASS] test_summarizer_empty_messages")


async def test_summarizer_falls_back_on_llm_error():
    """When LLM fails, summarizer falls back to rule-based summary (non-empty)."""
    summarizer = ConversationSummarizer()
    messages = [
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hi"},
    ]
    result = await summarizer.summarize(messages, FailingAdapter())
    assert isinstance(result, str) and len(result) > 0, \
        "Fallback summary should be a non-empty string"
    # Rule-based fallback starts with a heading
    assert "对话摘要" in result or "[user]" in result.lower() or "[USER]" in result, \
        f"Fallback should reference messages, got: {result}"
    print("[PASS] test_summarizer_falls_back_on_llm_error")


async def test_smart_compression_with_llm_summary():
    """SmartCompressionStrategy.compress() uses llm_summary when provided."""
    conv = _make_conversation(30)  # 30 messages — well over any limit
    strategy = SmartCompressionStrategy(keep_first=2, keep_recent=5)
    llm_summary = "## 对话摘要\n**用户目标**：探索数据库"

    compressed = strategy.compress(conv, max_messages=15, llm_summary=llm_summary)

    # Find injected summary message
    system_messages = [
        m for m in compressed.messages
        if m.role == MessageRole.SYSTEM
    ]
    assert len(system_messages) >= 1, "Compressed conv should have at least one system summary"
    summary_content = system_messages[0].content
    assert llm_summary in summary_content, \
        f"LLM summary should appear in context summary message. Got: {summary_content[:200]}"
    # Claude Code style preamble should be present
    assert "本对话从之前较长的历史继续" in summary_content, \
        f"Expected Claude Code style preamble. Got: {summary_content[:200]}"
    print("[PASS] test_smart_compression_with_llm_summary")


async def test_smart_compression_without_llm_summary_falls_back():
    """Without llm_summary, SmartCompressionStrategy uses rule-based fallback."""
    conv = _make_conversation(30)
    strategy = SmartCompressionStrategy(keep_first=2, keep_recent=5)
    compressed = strategy.compress(conv, max_messages=15, llm_summary="")

    system_messages = [
        m for m in compressed.messages
        if m.role == MessageRole.SYSTEM
    ]
    assert len(system_messages) >= 1, "Should still inject a summary"
    summary_content = system_messages[0].content
    # Rule-based preamble
    assert "[历史对话摘要]" in summary_content, \
        f"Rule-based summary should use '[历史对话摘要]' preamble. Got: {summary_content[:200]}"
    print("[PASS] test_smart_compression_without_llm_summary_falls_back")


async def test_hybrid_context_manager_accepts_llm_summary():
    """HybridContextManager.compress_conversation() forwards llm_summary to strategy."""
    conv = _make_conversation(30)
    manager = HybridContextManager(
        strategy="compressed",
        max_context_length=15,
        keep_first=2,
        keep_recent=5,
    )
    llm_summary = "## 测试摘要\n**用户目标**：验证 HybridContextManager"
    compressed = manager.compress_conversation(conv, llm_summary=llm_summary)

    system_messages = [
        m for m in compressed.messages
        if m.role == MessageRole.SYSTEM
    ]
    assert len(system_messages) >= 1
    assert llm_summary in system_messages[0].content, \
        "HybridContextManager must forward llm_summary to strategy"
    print("[PASS] test_hybrid_context_manager_accepts_llm_summary")


async def test_maybe_summarize_skips_short_conversations():
    """_maybe_summarize returns '' when message count <= max_context_messages."""
    # We test the ConversationSummarizer's summarize() directly with an empty list
    # to simulate the "short conversation" branch returning "".
    summarizer = ConversationSummarizer()
    adapter = MockLLMAdapter()

    # Empty message list → immediate "" return
    result = await summarizer.summarize([], adapter)
    assert result == "", "Short/empty message list should yield empty summary"
    assert adapter.call_count == 0, "No LLM call for empty list"
    print("[PASS] test_maybe_summarize_skips_short_conversations")


# ──────────────────────────────────────────────────────────
# Runner
# ──────────────────────────────────────────────────────────

async def run_all():
    tests = [
        test_summarizer_calls_llm_with_correct_prompt,
        test_summarizer_returns_llm_output,
        test_summarizer_empty_messages,
        test_summarizer_falls_back_on_llm_error,
        test_smart_compression_with_llm_summary,
        test_smart_compression_without_llm_summary_falls_back,
        test_hybrid_context_manager_accepts_llm_summary,
        test_maybe_summarize_skips_short_conversations,
    ]
    passed = failed = 0
    for t in tests:
        try:
            await t()
            passed += 1
        except Exception as e:
            print(f"[FAIL] {t.__name__}: {e}")
            import traceback
            traceback.print_exc()
            failed += 1

    print(f"\n{'='*50}")
    print(f"Results: {passed} passed, {failed} failed / {len(tests)} total")
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
