"""
conversation_summarizer.py
LLM-powered conversation history summarizer.

Generates a coherent Chinese summary of the "middle" portion of a long
conversation, similar to how Claude Code summarizes prior sessions before
continuing.  The summary is injected into the context so the LLM can
understand what happened earlier without seeing every raw message.

Usage
-----
    summarizer = ConversationSummarizer()
    summary = await summarizer.summarize(middle_messages, llm_adapter)
    # summary is Markdown text describing the compressed conversation
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


class ConversationSummarizer:
    """LLM-powered conversation history summarizer.

    Standalone, stateless service.  Callers provide the LLM adapter so
    this class has no dependency on the application DI container.
    """

    SUMMARY_SYSTEM = (
        "你是一个对话摘要助手。请对提供的对话历史生成简洁、信息密集的摘要，"
        "保留所有对后续对话有用的关键信息：用户目标、已执行的操作、查询结果、关键发现。"
        "摘要应让后续对话能无缝继续。输出纯中文，格式清晰。"
    )

    SUMMARY_USER_TMPL = """\
请对以下历史对话生成摘要（用于上下文压缩，后续对话将以此继续）：

---
{messages_text}
---

按以下固定格式输出，每项一句话：

## 对话摘要
**用户目标**：（核心诉求，一句话）
**已完成的操作**：（逐条列出，每项以"- "开头）
**关键发现**：（数据结果、表结构、业务规律等重要结论，逐条列出）
**当前状态**：（任务进展到哪里，下一步需要做什么）
"""

    # 单条消息内容传给摘要 LLM 时的最大字符数（避免 prompt 过长）
    _MSG_PREVIEW_CHARS = 800

    async def summarize(
        self,
        messages: List[Dict[str, str]],  # [{"role": "user/assistant", "content": str}]
        llm_adapter: Any,
    ) -> str:
        """Call LLM to summarize a list of conversation messages.

        Args:
            messages: Flat list of {"role": ..., "content": ...} dicts.
                      Only user and assistant roles are expected.
            llm_adapter: Any adapter with a ``chat_plain()`` coroutine.

        Returns:
            Markdown summary string, or "" on error.
        """
        if not messages:
            return ""

        messages_text = "\n\n".join(
            f"[{m.get('role', 'unknown').upper()}] "
            f"{self._truncate(m.get('content', ''), self._MSG_PREVIEW_CHARS)}"
            for m in messages
        )

        prompt = self.SUMMARY_USER_TMPL.format(messages_text=messages_text)
        logger.info(
            f"[ConversationSummarizer] Summarizing {len(messages)} messages "
            f"({len(messages_text)} chars in prompt)"
        )

        try:
            response = await llm_adapter.chat_plain(
                messages=[{"role": "user", "content": prompt}],
                system_prompt=self.SUMMARY_SYSTEM,
            )
            summary = self._extract_text(response)
            logger.info(
                f"[ConversationSummarizer] Summary generated: {len(summary)} chars"
            )
            return summary
        except Exception as exc:
            logger.warning(
                f"[ConversationSummarizer] LLM summarization failed: {exc}. "
                "Falling back to rule-based summary."
            )
            return self._rule_based_fallback(messages)

    # ─────────────────────────────────────────────────────
    # Private helpers
    # ─────────────────────────────────────────────────────

    @staticmethod
    def _extract_text(response: Dict[str, Any]) -> str:
        """Extract plain text from an LLM response dict."""
        for block in response.get("content", []):
            if isinstance(block, dict) and block.get("type") == "text":
                return block.get("text", "").strip()
        return ""

    @staticmethod
    def _truncate(text: str, max_chars: int) -> str:
        return text[:max_chars] + "..." if len(text) > max_chars else text

    def _rule_based_fallback(self, messages: List[Dict[str, str]]) -> str:
        """Lightweight rule-based summary used when LLM call fails."""
        parts = ["## 对话摘要（规则生成）"]
        for i, m in enumerate(messages, 1):
            role = m.get("role", "unknown")
            preview = self._truncate(m.get("content", ""), 100)
            parts.append(f"{i}. [{role}] {preview}")
        return "\n".join(parts)
