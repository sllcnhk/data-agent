"""
Agentic Loop — 5-phase cognitive cycle

Phases:
  1. Perceive  — Build unified messages from conversation context
  2. Retrieve  — Gather available MCP tools
  3. Plan      — LLM generates plan (tool_use mode)
  4. Act       — Execute tool calls via MCP
  5. Observe   — Process results, decide to loop or terminate

The loop runs until the LLM produces a final text response
(stop_reason == "end_turn") or until MAX_ITERATIONS is reached.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any, AsyncGenerator, Dict, List, Optional
import uuid

from backend.mcp.manager import MCPServerManager
from backend.mcp.tool_formatter import format_mcp_tools_for_claude, parse_tool_name

logger = logging.getLogger(__name__)


class _SafeJSONEncoder(json.JSONEncoder):
    """兜底 JSON 编码器，确保 _format_result 永远不会因未知类型崩溃。

    覆盖顺序：datetime 必须在 date 之前（datetime 是 date 的子类）。
    """
    def default(self, obj):
        if isinstance(obj, datetime):
            return obj.isoformat()
        if isinstance(obj, (date, time)):
            return obj.isoformat()
        if isinstance(obj, Decimal):
            return float(obj)
        if isinstance(obj, (bytes, bytearray)):
            return obj.hex()
        if isinstance(obj, uuid.UUID):
            return str(obj)
        # 终极兜底
        return str(obj)

MAX_ITERATIONS = 10
MAX_CONTINUATION = 10   # 单次响应最大自动续写次数
MAX_STAGNANT = 2        # 连续完全相同的工具调用超过此次数视为停滞，提前终止
MAX_TOOL_RESULT_CHARS = 8000  # 单次工具返回结果最大字符数；超出则截断，防止 context 爆炸
MAX_LOOP_CONTEXT_CHARS = 60000   # loop 内累计 messages 字符数阈值，超出则压缩旧 tool_result
KEEP_RECENT_TOOL_PAIRS = 5       # 保留最近 N 个 tool_call/result 对，其余压缩为单行摘要
NEAR_LIMIT_THRESHOLD = 5         # 剩余迭代次数 <= 此值时切换到综合分析模式，不再调用工具


# ──────────────────────────────────────────────────────────
# Internal exceptions
# ──────────────────────────────────────────────────────────


class _CancelledByUser(Exception):
    """Raised internally when a cancel_event fires during an LLM call."""


# ──────────────────────────────────────────────────────────
# Data structures
# ──────────────────────────────────────────────────────────


@dataclass
class AgentEvent:
    """A single event emitted during loop execution.

    type values:
      thinking    — LLM is reasoning / intermediate text
      tool_call   — About to call an MCP tool
      tool_result — Result from an MCP tool
      content     — Final answer text chunk
      error       — Error occurred
      done        — Loop finished (sentinel)
    """
    type: str
    data: Any = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {"type": self.type, "data": self.data, "metadata": self.metadata}


@dataclass
class AgenticResult:
    """Final result returned after the loop terminates."""
    success: bool
    content: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    events: List[AgentEvent] = field(default_factory=list)


# ──────────────────────────────────────────────────────────
# AgenticLoop
# ──────────────────────────────────────────────────────────


class AgenticLoop:
    """
    Core agentic reasoning loop.

    Coordinates the Claude LLM with MCP tool execution using
    Claude's native tool_use API.  Every intermediate step
    (thinking, tool calls, results) is yielded as an AgentEvent,
    enabling real-time SSE streaming to the frontend.
    """

    def __init__(
        self,
        llm_adapter,
        mcp_manager: MCPServerManager,
        max_iterations: int = MAX_ITERATIONS,
        cancel_event: Optional[asyncio.Event] = None,
    ):
        self.llm_adapter = llm_adapter
        self.mcp_manager = mcp_manager
        self.max_iterations = max_iterations
        self._cancel_event = cancel_event

    # ─────────────────────────────────────────────────────
    # Streaming interface (primary API)
    # ─────────────────────────────────────────────────────

    async def run_streaming(
        self,
        message: str,
        context: Dict[str, Any],
    ) -> AsyncGenerator[AgentEvent, None]:
        """
        Execute the agentic loop, yielding events as they happen.

        Args:
            message: Current user message text
            context: Conversation context built by _build_context()

        Yields:
            AgentEvent instances in chronological order:
              thinking → (tool_call → tool_result)* → content
        """
        # Phase 1 — Perceive
        messages = self._perceive(message, context)
        system_prompt = await self._build_system_prompt(context, message=message)

        # Emit skill routing result so the frontend can display which skills loaded
        try:
            from backend.skills.skill_loader import get_skill_loader
            _match_info = get_skill_loader().get_last_match_info()
            if _match_info:
                yield AgentEvent(type="skill_matched", data=_match_info)
        except Exception:
            pass  # non-critical — never block the main loop

        # Phase 2 — Retrieve tools
        tools = format_mcp_tools_for_claude(self.mcp_manager)
        logger.info(f"[AgenticLoop] Available tools: {len(tools)}")

        # 自动续写状态
        accumulated_text: str = ""   # 已续写的累积文本
        continuation_count: int = 0  # 当前响应已续写次数

        # 停滞检测状态
        last_tool_signatures: frozenset = frozenset()  # 上一轮工具调用签名集合
        consecutive_stagnant: int = 0                  # 连续相同调用次数

        # 文件写入追踪：收集本轮对话中成功写入的文件路径
        written_files: List[dict] = []

        for iteration in range(1, self.max_iterations + 1):
            logger.info(
                f"[AgenticLoop] Iteration {iteration}/{self.max_iterations}"
            )

            yield AgentEvent(
                type="thinking",
                data=f"正在分析 (第 {iteration} 轮)...",
                metadata={"iteration": iteration},
            )

            try:
                # Phase 3 — Plan: call LLM (cancellable)
                if tools:
                    response = await self._cancellable_await(
                        self.llm_adapter.chat_with_tools(
                            messages=messages,
                            system_prompt=system_prompt,
                            tools=tools,
                        )
                    )
                else:
                    response = await self._cancellable_await(
                        self.llm_adapter.chat_plain(
                            messages=messages,
                            system_prompt=system_prompt,
                        )
                    )

                stop_reason = response.get("stop_reason", "end_turn")
                content_blocks = response.get("content", [])
                logger.info(
                    f"[AgenticLoop] stop_reason={stop_reason}, "
                    f"content_blocks={len(content_blocks)}"
                )

                if stop_reason == "end_turn":
                    # LLM finished — extract final text，拼接所有续写片段
                    text = self._extract_text(content_blocks)
                    if accumulated_text:
                        text = accumulated_text + text
                        accumulated_text = ""
                    yield AgentEvent(
                        type="content",
                        data=text,
                        metadata={
                            "iteration": iteration,
                            "final": True,
                            "continuation_count": continuation_count,
                        },
                    )
                    # 若本轮对话写入了文件，发出下载链接事件
                    if written_files:
                        yield AgentEvent(
                            type="files_written",
                            data={"files": written_files},
                        )
                    return

                elif stop_reason == "max_tokens":
                    # 模型因达到 max_tokens 而中断 — 自动续写
                    partial = self._extract_text(content_blocks)
                    accumulated_text += partial
                    continuation_count += 1

                    # 检测被截断的 content_blocks 中是否含 tool_use 块
                    # max_tokens 时 tool_use 块是不完整的（LLM 正在生成中被截断），
                    # 不能直接加入 messages，否则下次 API 调用会因缺少 tool_result 报 400
                    has_orphan_tool_use = any(
                        isinstance(b, dict) and b.get("type") == "tool_use"
                        for b in content_blocks
                    )

                    logger.info(
                        f"[AgenticLoop] max_tokens detected, "
                        f"auto-continuation #{continuation_count}, "
                        f"accumulated_len={len(accumulated_text)}, "
                        f"has_orphan_tool_use={has_orphan_tool_use}"
                    )

                    if continuation_count > MAX_CONTINUATION:
                        # 超出续写上限，返回已有内容
                        logger.warning(
                            f"[AgenticLoop] Exceeded MAX_CONTINUATION "
                            f"({MAX_CONTINUATION}), returning partial response"
                        )
                        yield AgentEvent(
                            type="content",
                            data=accumulated_text,
                            metadata={
                                "iteration": iteration,
                                "final": True,
                                "truncated": True,
                                "continuation_count": continuation_count,
                            },
                        )
                        return

                    # 通知前端正在自动续写（可选 UI 提示）
                    yield AgentEvent(
                        type="continuation",
                        data={
                            "count": continuation_count,
                            "message": f"正在继续生成 (第 {continuation_count} 次续写)...",
                        },
                        metadata={
                            "accumulated_len": len(accumulated_text),
                            "has_orphan_tool_use": has_orphan_tool_use,
                        },
                    )

                    # 将本轮部分回复 + 续写指令追加到对话历史，供下一次 LLM 调用
                    # 关键：只保留 text 块，过滤掉不完整的 tool_use 块
                    # 不完整的 tool_use 会导致下次 API 调用 400: tool_use without tool_result
                    safe_blocks = [
                        b for b in content_blocks
                        if isinstance(b, dict) and b.get("type") == "text"
                    ]
                    if not safe_blocks:
                        # 若截断后连文本都没有，补充一个占位块防止空 content 报错
                        safe_blocks = [{"type": "text", "text": partial or "(内容被截断，请继续)"}]

                    messages.append(
                        {"role": "assistant", "content": safe_blocks}
                    )
                    messages.append(
                        {"role": "user", "content": "请继续"}
                    )
                    # continue 回到循环顶部重新调用 LLM
                    continue

                elif stop_reason == "tool_use":
                    # Phase 4 — Act: execute tool calls
                    tool_use_blocks = [
                        b for b in content_blocks
                        if isinstance(b, dict) and b.get("type") == "tool_use"
                    ]
                    text_blocks = [
                        b for b in content_blocks
                        if isinstance(b, dict) and b.get("type") == "text"
                    ]

                    # Emit any intermediate thinking text from the LLM
                    if text_blocks:
                        thinking = "\n".join(
                            b.get("text", "") for b in text_blocks
                        ).strip()
                        if thinking:
                            yield AgentEvent(
                                type="thinking",
                                data=thinking,
                                metadata={"iteration": iteration},
                            )

                    # 停滞检测：若本轮工具调用与上一轮完全相同，则视为 LLM 陷入死循环
                    current_sigs = frozenset(
                        (b.get("name", ""), json.dumps(b.get("input", {}), sort_keys=True))
                        for b in tool_use_blocks
                    )
                    if current_sigs and current_sigs == last_tool_signatures:
                        consecutive_stagnant += 1
                        logger.warning(
                            f"[AgenticLoop] Stagnation #{consecutive_stagnant}: "
                            f"same tool calls repeated: {current_sigs}"
                        )
                        if consecutive_stagnant >= MAX_STAGNANT:
                            stagnation_msg = (
                                "检测到重复的工具调用，已停止推理。"
                                "请尝试更具体的问题或提供更多上下文。"
                            )
                            if accumulated_text:
                                yield AgentEvent(
                                    type="content",
                                    data=accumulated_text,
                                    metadata={
                                        "stagnation": True,
                                        "truncated": True,
                                        "iteration": iteration,
                                    },
                                )
                            else:
                                yield AgentEvent(
                                    type="error",
                                    data=stagnation_msg,
                                    metadata={"stagnation": True, "iteration": iteration},
                                )
                            return
                    else:
                        consecutive_stagnant = 0
                        last_tool_signatures = current_sigs

                    # Near-limit check: stop calling tools when close to max_iterations,
                    # synthesize findings and emit near_limit event with pending tasks.
                    remaining = self.max_iterations - iteration
                    if remaining <= NEAR_LIMIT_THRESHOLD:
                        logger.info(
                            f"[AgenticLoop] Near-limit at iteration {iteration}/"
                            f"{self.max_iterations}, switching to synthesis mode"
                        )
                        synth_text, pending_tasks, conclusions = await self._cancellable_await(
                            self._synthesize_and_wrap_up(messages, accumulated_text, remaining)
                        )
                        full_text = (
                            (accumulated_text + "\n\n" + synth_text).strip()
                            if accumulated_text else synth_text
                        )
                        yield AgentEvent(
                            type="content",
                            data=full_text,
                            metadata={
                                "near_limit": True,
                                "iteration": iteration,
                                "remaining_iterations": remaining,
                            },
                        )
                        if pending_tasks:
                            yield AgentEvent(
                                type="near_limit",
                                data={
                                    "pending_tasks": pending_tasks,
                                    "conclusions": conclusions,
                                    "iterations_used": iteration,
                                    "max_iterations": self.max_iterations,
                                },
                                metadata={"near_limit": True},
                            )
                        if written_files:
                            yield AgentEvent(
                                type="files_written",
                                data={"files": written_files},
                            )
                        return

                    # Append assistant's tool_use turn to history
                    messages.append(
                        {"role": "assistant", "content": content_blocks}
                    )

                    # Execute each requested tool call
                    tool_results: List[Dict[str, Any]] = []
                    for block in tool_use_blocks:
                        tool_id: str = block.get("id", "")
                        tool_name: str = block.get("name", "")
                        tool_input: Dict[str, Any] = block.get("input", {})

                        yield AgentEvent(
                            type="tool_call",
                            data={"name": tool_name, "input": tool_input},
                            metadata={"tool_id": tool_id},
                        )

                        raw_result = await self._execute_tool(
                            tool_name, tool_input
                        )

                        # Check cancel after each tool (tools are fast; check at boundary)
                        if self._cancel_event and self._cancel_event.is_set():
                            raise _CancelledByUser()

                        yield AgentEvent(
                            type="tool_result",
                            data={"name": tool_name, "result": raw_result},
                            metadata={
                                "tool_id": tool_id,
                                "success": raw_result.get("success", False),
                            },
                        )

                        # 检测文件写入：收集 write_file 成功调用的路径
                        if "write_file" in tool_name and raw_result.get("success", False):
                            file_path = tool_input.get("path", "")
                            if file_path:
                                # === 修复：将绝对路径转换为相对于 customer_data/ 的路径 ===
                                # 统一转换为相对路径格式，便于下载 API 使用
                                try:
                                    from pathlib import Path
                                    from backend.config.settings import settings

                                    path_obj = Path(file_path)

                                    # 尝试找到相对于 customer_data 根目录的路径
                                    customer_root = (
                                        Path(settings.allowed_directories[0])
                                        if settings.allowed_directories
                                        else Path("customer_data")
                                    )

                                    # 如果是绝对路径且在 customer_data 下，转换为相对路径
                                    if path_obj.is_absolute():
                                        try:
                                            original_path = file_path
                                            file_path = str(path_obj.relative_to(customer_root))
                                            logger.debug(
                                                f"[AgenticLoop] 文件路径转换: 绝对路径 '{original_path}' -> 相对路径 '{file_path}'"
                                            )
                                        except ValueError:
                                            # 不在 customer_data 下，保持原路径（可能是其他允许目录）
                                            logger.debug(
                                                f"[AgenticLoop] 文件路径不在 customer_data 下，保持原路径: {file_path}"
                                            )
                                except Exception as e:
                                    # 转换失败，保持原路径并记录
                                    logger.warning(
                                        f"[AgenticLoop] 文件路径转换失败（将使用原路径）: {file_path}, 错误: {e}"
                                    )

                                file_name = file_path.replace("\\", "/").split("/")[-1]
                                content_val = tool_input.get("content", "")
                                file_size = (
                                    len(content_val.encode("utf-8"))
                                    if isinstance(content_val, str)
                                    else len(content_val or b"")
                                )
                                written_files.append({
                                    "path": file_path,
                                    "name": file_name,
                                    "size": file_size,
                                    "mime_type": self._infer_mime_type(file_name),
                                })
                                logger.debug(
                                    f"[AgenticLoop] 记录写入文件: path={file_path}, name={file_name}, size={file_size}"
                                )

                        tool_results.append(
                            {
                                "type": "tool_result",
                                "tool_use_id": tool_id,
                                "content": self._format_result(raw_result),
                            }
                        )

                    # Phase 5 — Observe: feed results back to LLM
                    messages.append(
                        {"role": "user", "content": tool_results}
                    )
                    # In-loop context compression: prevent history from exploding
                    messages = self._compress_loop_messages(messages)
                    # Loop continues → back to Phase 3

                else:
                    # Unknown stop_reason — treat as final，同样拼接已续写内容
                    text = self._extract_text(content_blocks)
                    if accumulated_text:
                        text = accumulated_text + (text or "")
                        accumulated_text = ""
                    yield AgentEvent(
                        type="content",
                        data=text or "处理完成",
                        metadata={
                            "iteration": iteration,
                            "stop_reason": stop_reason,
                            "continuation_count": continuation_count,
                        },
                    )
                    return

            except _CancelledByUser:
                logger.info(f"[AgenticLoop] Cancelled by user at iteration {iteration}")
                partial = accumulated_text or ""
                yield AgentEvent(
                    type="cancelled",
                    data=partial,
                    metadata={"iteration": iteration, "cancelled": True},
                )
                return

            except Exception as exc:
                logger.error(
                    f"[AgenticLoop] Iteration {iteration} error: {exc}",
                    exc_info=True,
                )
                yield AgentEvent(
                    type="error",
                    data=str(exc),
                    metadata={"iteration": iteration},
                )
                return

        # Exceeded max iterations
        logger.warning(
            f"[AgenticLoop] Exceeded max iterations ({self.max_iterations})"
        )
        if accumulated_text:
            # 有已续写内容则优先返回，而不是丢弃
            yield AgentEvent(
                type="content",
                data=accumulated_text,
                metadata={
                    "truncated": True,
                    "continuation_count": continuation_count,
                    "exceeded_max": True,
                },
            )
        else:
            yield AgentEvent(
                type="error",
                data="已超出最大推理轮次，请简化您的请求。",
                metadata={"exceeded_max": True},
            )

    # ─────────────────────────────────────────────────────
    # Non-streaming interface (for backward compat)
    # ─────────────────────────────────────────────────────

    async def run(
        self,
        message: str,
        context: Dict[str, Any],
    ) -> AgenticResult:
        """
        Execute the agentic loop and collect all events into a result.

        Returns:
            AgenticResult with success flag and final content
        """
        events: List[AgentEvent] = []
        final_content = ""
        success = True

        async for event in self.run_streaming(message, context):
            events.append(event)
            if event.type == "content":
                final_content = event.data or ""
            elif event.type == "error":
                success = False
                if not final_content:
                    final_content = event.data or "发生错误"

        return AgenticResult(
            success=success,
            content=final_content,
            metadata={"event_count": len(events)},
            events=events,
        )

    # ─────────────────────────────────────────────────────
    # Private helpers
    # ─────────────────────────────────────────────────────

    async def _cancellable_await(self, coro):
        """Await a coroutine, raising _CancelledByUser if cancel_event fires first.

        When no cancel_event is set, behaves identically to plain `await`.
        Uses asyncio.wait(FIRST_COMPLETED) to interrupt an in-flight LLM call
        without waiting for the HTTP response to complete.
        """
        if not self._cancel_event:
            return await coro

        # Already cancelled before we even start
        if self._cancel_event.is_set():
            # Still need to schedule/close the coroutine to avoid RuntimeWarning
            task = asyncio.create_task(coro)
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
            raise _CancelledByUser()

        lm_task = asyncio.create_task(coro)
        cancel_task = asyncio.create_task(self._cancel_event.wait())
        done, pending = await asyncio.wait(
            {lm_task, cancel_task}, return_when=asyncio.FIRST_COMPLETED
        )
        for t in pending:
            t.cancel()
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        if cancel_task in done:
            raise _CancelledByUser()
        return lm_task.result()

    def _perceive(
        self, message: str, context: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """Build message list from conversation context + current message."""
        messages: List[Dict[str, Any]] = []

        for msg in context.get("history", []):
            role = msg.get("role", "user")
            content = (msg.get("content") or "").strip()
            if content and role in ("user", "assistant"):
                messages.append({"role": role, "content": content})

        current_attachments = context.get("current_attachments")
        if current_attachments:
            # Build multimodal content blocks for the current message
            content_blocks: List[Dict[str, Any]] = []
            if message and message.strip():
                content_blocks.append({"type": "text", "text": message.strip()})
            for att in current_attachments:
                mime = att.get("mime_type", "")
                name = att.get("name", "file")
                b64_data = att.get("data", "")
                if mime.startswith("image/"):
                    # Claude supports: image/jpeg, image/png, image/gif, image/webp
                    supported_image_types = {"image/jpeg", "image/png", "image/gif", "image/webp"}
                    media_type = mime if mime in supported_image_types else "image/jpeg"
                    content_blocks.append({
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": b64_data,
                        },
                    })
                elif mime == "application/pdf":
                    content_blocks.append({
                        "type": "document",
                        "source": {
                            "type": "base64",
                            "media_type": "application/pdf",
                            "data": b64_data,
                        },
                    })
                else:
                    # Text/code files: decode and embed as text
                    try:
                        text_content = base64.b64decode(b64_data).decode("utf-8", errors="replace")
                        content_blocks.append({
                            "type": "text",
                            "text": f"[文件: {name}]\n```\n{text_content}\n```",
                        })
                    except Exception:
                        content_blocks.append({
                            "type": "text",
                            "text": f"[附件: {name} ({mime}), 无法解码内容]",
                        })
            if content_blocks:
                messages.append({"role": "user", "content": content_blocks})
        elif message and message.strip():
            messages.append({"role": "user", "content": message.strip()})

        return messages

    async def _build_system_prompt(
        self, context: Dict[str, Any], message: str = ""
    ) -> str:
        """
        Build system prompt enriched with:
          1. MCP server info (available tools)
          2. SKILL.md injections for triggered skills
        """
        # 1 — Base prompt
        base = (context.get("system_prompt") or "").strip()
        if not base:
            base = (
                "你是一个专业的数据分析助手，能够帮助用户查询、分析数据，"
                "设计ETL流程，生成数据报告。"
                "当需要访问数据时，请使用提供的工具执行实际查询，而非假设结果。"
            )

        # 2 — MCP server info
        servers = self.mcp_manager.list_servers()
        if servers:
            lines = "\n".join(
                f"- {s['name']} ({s['type']}): {s['tool_count']} 个工具"
                for s in servers
            )
            tools_info = f"\n\n可用数据工具:\n{lines}"

            # 若 filesystem 服务器已注册，追加文件操作使用指引
            fs_servers = [s for s in servers if s.get("type") == "filesystem"]
            if fs_servers:
                # 从服务器实例中获取真实的允许目录，避免 LLM 先调用 list_allowed_directories 探索
                try:
                    fs_obj = self.mcp_manager.servers.get("filesystem")
                    dirs = getattr(fs_obj, "allowed_directories", []) if fs_obj else []
                except Exception:
                    dirs = []

                current_username = context.get("username", "anonymous") if context else "anonymous"

                # 从 allowed_directories 中识别两类根：技能根 vs 数据根
                # 规则：含 ".claude" 的为技能根，含 "customer_data" 的为数据根
                skills_root: str = ""
                data_root: str = ""
                for d in dirs:
                    d_norm = d.replace("\\", "/")
                    if ".claude" in d_norm and not skills_root:
                        skills_root = d
                    elif not data_root:
                        data_root = d

                # 拼装路径提示：给出两类文件的精确绝对路径模板
                if skills_root and data_root:
                    # 数据文件写入用户专属子目录，技能文件写入用户技能目录
                    user_data_root = f"{data_root}/{current_username}".replace("\\", "/")
                    skill_path_example = (
                        f"{skills_root}/user/{current_username}/my-skill.md"
                        .replace("\\", "/")
                    )
                    # 可选：按月存储子目录提示（FILE_OUTPUT_DATE_SUBFOLDER=true 时启用）
                    try:
                        from backend.config.settings import settings as _s
                        _date_subfolder = getattr(_s, "file_output_date_subfolder", False)
                    except Exception:
                        _date_subfolder = False
                    if _date_subfolder:
                        from datetime import date as _date
                        _month_dir = _date.today().strftime("%Y-%m")
                        _date_hint = (
                            f"  • 建议按月整理：数据文件优先写入 {user_data_root}/{_month_dir}/\n"
                            f"    （此规则可按需覆盖，方便历史数据按月批量清理）\n"
                        )
                    else:
                        _date_hint = ""
                    path_rule = (
                        f"- 路径规则（必须严格遵循，禁止混用两类根目录）：\n"
                        f"  • 数据文件（CSV/JSON/SQL/分析结果等）→ 写入 {user_data_root}/\n"
                        f"    （每位用户数据存储在独立子目录，禁止写入其他用户目录）\n"
                        f"{_date_hint}"
                        f"  • 用户技能文件（*.md SKILL格式）→ 写入 {skills_root}/user/{current_username}/\n"
                        f"    路径示例：{skill_path_example}\n"
                        f"  • 严禁将技能文件写入 customer_data/ 目录\n"
                        f"  • 严禁省略用户名层级（必须是 user/{current_username}/，不是 user/）\n"
                    )
                elif dirs:
                    path_rule = "- 已知允许的根目录：" + "、".join(dirs) + "\n"
                else:
                    path_rule = ""

                tools_info += (
                    "\n\n文���操作规则（重要）：\n"
                    "- 当用户要求将内容写入文件、保存文件、新建文件时，"
                    "必须调用 filesystem__write_file 工具，而不是在回复中直接输出文字。\n"
                    f"{path_rule}"
                    "- 路径格式说明：文件系统根目录已指向 customer_data/，路径中禁止重复写 customer_data/（否则产生双层目录）。\n"
                    "- 正确格式：{当前用户}/文件名.md 或 .claude/skills/user/{当前用户}/skill.md\n"
                    "- 错误格式：customer_data/{当前用户}/文件名.md（重复了 customer_data/ 前缀）；C:/... 或 / 开头的绝对路径。\n"
                    "- 直接调用 filesystem__write_file，无需先调用 list_allowed_directories"
                    " 或 list_directory 探索目录结构。\n"
                    "- 写完文件后，告知用户文件已创建、路径及字节数。\n"
                    f"- CURRENT_USER: {current_username}"
                )
        else:
            tools_info = ""

        # 3 — SKILL.md injection for triggered skills (hybrid semantic routing)
        skill_injection = ""
        if message:
            try:
                from backend.skills.skill_loader import get_skill_loader
                skill_injection = await get_skill_loader().build_skill_prompt_async(
                    message, llm_adapter=getattr(self, "llm_adapter", None)
                )
            except Exception as exc:
                logger.warning(f"[AgenticLoop] SkillLoader error: {exc}")

        return base + tools_info + skill_injection

    async def _execute_tool(
        self, namespaced_name: str, tool_input: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Dispatch a namespaced tool call to the appropriate MCP server."""
        server_name, tool_name = parse_tool_name(namespaced_name)
        if not server_name:
            return {
                "success": False,
                "error": f"无法解析工具名称: {namespaced_name}",
            }
        try:
            result = await self.mcp_manager.call_tool(
                server_name, tool_name, tool_input
            )
            return result or {"success": False, "error": "Empty result from MCP"}
        except Exception as exc:
            logger.error(
                f"[AgenticLoop] Tool error ({namespaced_name}): {exc}"
            )
            return {"success": False, "error": str(exc)}

    @staticmethod
    def _extract_text(content_blocks: List[Any]) -> str:
        """Extract and join text from Claude content blocks."""
        parts: List[str] = []
        for block in content_blocks:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
            elif isinstance(block, str):
                parts.append(block)
        return "\n".join(p for p in parts if p)

    @staticmethod
    def _compress_loop_messages(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Compress old tool_result blocks when the in-loop message list grows too large.

        When total character count exceeds MAX_LOOP_CONTEXT_CHARS:
        - Find all tool_result turns
        - Keep the last KEEP_RECENT_TOOL_PAIRS verbatim
        - Replace earlier tool_result contents with a 1-line summary

        This prevents context explosion across 20+ agentic iterations.
        """
        total_chars = sum(
            len(json.dumps(m.get("content", ""), ensure_ascii=False))
            for m in messages
        )
        if total_chars < MAX_LOOP_CONTEXT_CHARS:
            return messages

        logger.info(
            f"[AgenticLoop] Compressing loop context: {total_chars} chars "
            f"(threshold={MAX_LOOP_CONTEXT_CHARS})"
        )

        # Find indices of user turns that carry tool_result blocks
        tool_result_indices = [
            i for i, m in enumerate(messages)
            if isinstance(m.get("content"), list)
            and any(
                isinstance(b, dict) and b.get("type") == "tool_result"
                for b in m["content"]
            )
        ]
        if not tool_result_indices:
            return messages

        # Everything before this index gets compressed
        if len(tool_result_indices) > KEEP_RECENT_TOOL_PAIRS:
            compress_up_to = tool_result_indices[-KEEP_RECENT_TOOL_PAIRS]
        else:
            return messages  # Not enough history to compress

        compressed = []
        for i, m in enumerate(messages):
            if i < compress_up_to and isinstance(m.get("content"), list):
                new_content = []
                for b in m["content"]:
                    if isinstance(b, dict) and b.get("type") == "tool_result":
                        orig = b.get("content", "")
                        if isinstance(orig, str) and len(orig) > 120:
                            preview = orig[:120] + "..."
                        else:
                            preview = str(orig)
                        new_content.append({**b, "content": f"[历史结果已压缩] {preview}"})
                    else:
                        new_content.append(b)
                compressed.append({**m, "content": new_content})
            else:
                compressed.append(m)
        return compressed

    @staticmethod
    def _parse_synthesis_output(text: str):
        """Parse structured synthesis output from LLM.

        Returns (conclusions: str, pending_tasks: List[str])
        """
        import re
        conclusions = ""
        pending_tasks: List[str] = []

        conclusions_match = re.search(
            r"###\s*阶段性分析结论\s*\n(.*?)(?=###|\Z)",
            text,
            re.DOTALL,
        )
        if conclusions_match:
            conclusions = conclusions_match.group(1).strip()

        json_match = re.search(r"```json\s*(\[.*?\])\s*```", text, re.DOTALL)
        if json_match:
            try:
                raw = json.loads(json_match.group(1))
                pending_tasks = [str(t) for t in raw if t]
            except (json.JSONDecodeError, TypeError):
                pass

        return conclusions, pending_tasks

    async def _synthesize_and_wrap_up(
        self,
        messages: List[Dict[str, Any]],
        accumulated_text: str,
        remaining_iterations: int,
    ):
        """Synthesize findings when near iteration limit.

        Calls LLM without tools, asking for structured conclusions + pending tasks.
        Returns (synth_text: str, pending_tasks: List[str], conclusions: str).
        Falls back gracefully on any error.
        """
        SYNTHESIS_SYSTEM = (
            "你是一个智能数据分析助手。当前推理轮次即将耗尽，"
            "需要根据已收集的信息整理阶段性结论，并列出尚未完成的任务，"
            "便于在下次对话中继续。"
        )

        # Build condensed context from message history
        context_lines: List[str] = []
        for m in messages:
            role = m.get("role", "")
            content = m.get("content", "")
            if isinstance(content, str):
                context_lines.append(f"[{role.upper()}] {content[:800]}")
            elif isinstance(content, list):
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    btype = block.get("type", "")
                    if btype == "text":
                        txt = block.get("text", "")
                        if txt.strip():
                            context_lines.append(f"[ASSISTANT] {txt[:400]}")
                    elif btype == "tool_use":
                        context_lines.append(
                            f"[TOOL_CALL] {block.get('name','')} "
                            f"{json.dumps(block.get('input',{}), ensure_ascii=False)[:200]}"
                        )
                    elif btype == "tool_result":
                        context_lines.append(
                            f"[TOOL_RESULT] {str(block.get('content',''))[:400]}"
                        )

        context_text = "\n\n".join(context_lines[-40:])
        accumulated_hint = (
            f"\n\n已有初步结论：\n{accumulated_text}" if accumulated_text else ""
        )

        synthesis_prompt = (
            f"推理轮次即将耗尽（剩余 {remaining_iterations} 轮），"
            "请根据以下已知信息整理阶段性结论并列出待完成任务：\n\n"
            f"已收集的信息：\n{context_text}"
            f"{accumulated_hint}\n\n"
            "请严格按以下格式输出：\n\n"
            "### 阶段性分析结论\n"
            "（基于已收集信息的综合分析，尽可能完整）\n\n"
            "### 待完成任务\n"
            "以下任务因轮次限制未能完成，将在下次对话中继续：\n"
            "```json\n"
            '["任务描述1", "任务描述2"]\n'
            "```"
        )

        try:
            response = await self.llm_adapter.chat_plain(
                messages=[{"role": "user", "content": synthesis_prompt}],
                system_prompt=SYNTHESIS_SYSTEM,
            )
            synth_text = ""
            for block in response.get("content", []):
                if isinstance(block, dict) and block.get("type") == "text":
                    synth_text = block.get("text", "")
                    break

            if not synth_text:
                synth_text = accumulated_text or "已达推理轮次上限，无法生成完整分析。"

            conclusions, pending_tasks = self._parse_synthesis_output(synth_text)
            if not conclusions:
                conclusions = synth_text

            return synth_text, pending_tasks, conclusions

        except Exception as exc:
            logger.warning(f"[AgenticLoop] Synthesis failed: {exc}")
            fallback = accumulated_text or "已达推理上限，部分任务未完成。"
            return fallback, [], ""

    @staticmethod
    def _infer_mime_type(filename: str) -> str:
        """Infer MIME type from file extension."""
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        _MIME_MAP = {
            "csv": "text/csv",
            "tsv": "text/tab-separated-values",
            "txt": "text/plain",
            "md": "text/markdown",
            "json": "application/json",
            "jsonl": "application/x-ndjson",
            "xml": "application/xml",
            "html": "text/html",
            "htm": "text/html",
            "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "xls": "application/vnd.ms-excel",
            "pdf": "application/pdf",
            "zip": "application/zip",
            "gz": "application/gzip",
            "png": "image/png",
            "jpg": "image/jpeg",
            "jpeg": "image/jpeg",
            "svg": "image/svg+xml",
            "sql": "text/x-sql",
            "py": "text/x-python",
            "sh": "text/x-sh",
            "yaml": "application/x-yaml",
            "yml": "application/x-yaml",
            "parquet": "application/octet-stream",
        }
        return _MIME_MAP.get(ext, "application/octet-stream")

    @staticmethod
    def _format_result(result: Dict[str, Any]) -> str:
        """Serialize a tool result to a string for Claude.

        结果超过 MAX_TOOL_RESULT_CHARS 时截断，防止多轮工具调用后
        message history 膨胀导致 LLM context 溢出和响应超时。
        """
        if not result.get("success", False):
            return f"Error: {result.get('error', 'Unknown error')}"
        data = result.get("data")
        if data is None:
            return "Success (no data returned)"
        if isinstance(data, str):
            text = data
        else:
            text = json.dumps(data, ensure_ascii=False, indent=2, cls=_SafeJSONEncoder)
        if len(text) > MAX_TOOL_RESULT_CHARS:
            truncated = text[:MAX_TOOL_RESULT_CHARS]
            text = (
                truncated
                + f"\n\n... [结果已截断：原始长度 {len(text)} 字符，"
                f"仅保留前 {MAX_TOOL_RESULT_CHARS} 字符。"
                f"如需完整数据，请缩小查询范围或增加筛选条件。]"
            )
        return text
