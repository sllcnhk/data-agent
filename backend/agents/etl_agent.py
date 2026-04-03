"""
ETL Engineer Agent

专注于 ETL 流程设计与 ClickHouse SQL 脚本生成。

特性：
- 使用 etl-engineer.md + schema-explorer.md 技能（通过 SkillLoader 自动注入）
- 内置 SQL 安全检查：高危操作（DROP/TRUNCATE/DELETE/ALTER）在执行前
  发出 ``approval_required`` 事件并通过 ApprovalManager **真实暂停**，
  等待用户在前端 ApprovalModal 中点击同意或拒绝。
- 支持 Dry-Run 校验清单输出
- 完整的 MCP 工具访问权限（含 DDL / DML）
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, AsyncGenerator, Dict, List, Optional

from backend.agents.agentic_loop import AgentEvent, AgenticLoop, AgenticResult
from backend.mcp.manager import MCPServerManager
from backend.mcp.tool_formatter import format_mcp_tools_for_claude, parse_tool_name

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────
# SQL 危险操作检测
# ──────────────────────────────────────────────────────────

_DANGEROUS_PATTERNS: List[re.Pattern] = [
    re.compile(r"\bDROP\s+(TABLE|DATABASE|PARTITION|VIEW)\b", re.IGNORECASE),
    re.compile(r"\bTRUNCATE\s+(TABLE\b)?", re.IGNORECASE),
    re.compile(r"\bDELETE\s+FROM\b", re.IGNORECASE),
    re.compile(r"\bALTER\s+TABLE\b.*\b(DROP|MODIFY|RENAME)\b", re.IGNORECASE),
    re.compile(r"\bOPTIMIZE\s+TABLE\b", re.IGNORECASE),
]


def _detect_dangerous_sql(sql: str) -> List[str]:
    """
    Scan a SQL string for dangerous operations.

    Returns a list of human-readable descriptions of found patterns.
    Empty list means the SQL is safe.
    """
    warnings: List[str] = []
    for pat in _DANGEROUS_PATTERNS:
        m = pat.search(sql)
        if m:
            warnings.append(f"检测到高危操作: `{m.group(0).strip()}`")
    return warnings


def _extract_sql_from_input(tool_input: Dict[str, Any]) -> str:
    """Extract the SQL string from common tool argument names."""
    return (
        str(tool_input.get("query") or "")
        or str(tool_input.get("sql") or "")
        or str(tool_input.get("statement") or "")
    )


# ──────────────────────────────────────────────────────────
# ETL-specific AgenticLoop subclass
# ──────────────────────────────────────────────────────────


class ETLAgenticLoop(AgenticLoop):
    """
    AgenticLoop variant for the ETL Engineer role.

    Overrides ``run_streaming`` to intercept ``tool_call`` events:

    1. Before each tool call, scan the SQL argument for dangerous patterns.
    2. If dangerous SQL is found:
       a. Register a new approval via ApprovalManager.
       b. Yield an ``approval_required`` event (delivered to frontend via SSE
          *before* the tool is executed).
       c. **Suspend** with ``await approval_manager.wait_for_decision()``.
       d. If approved → let the tool_call proceed.
          If rejected / timed out → yield ``error`` and stop the loop.
    3. All other events are passed through unchanged.
    """

    async def run_streaming(
        self,
        message: str,
        context: Dict[str, Any],
    ) -> AsyncGenerator[AgentEvent, None]:
        """
        Wrap the parent run_streaming with SQL approval gating.

        The parent generator is suspended at each ``yield tool_call``
        (Python async-generator semantics), so we can safely await
        user approval before the parent advances to ``_execute_tool``.
        """
        # Lazy import avoids circular dependency at module load time
        from backend.core.approval_manager import approval_manager

        async for event in super().run_streaming(message, context):
            if event.type == "tool_call":
                tool_input: Dict[str, Any] = event.data.get("input", {})
                sql = _extract_sql_from_input(tool_input)

                if sql:
                    dangers = _detect_dangerous_sql(sql)
                    if dangers:
                        tool_name = event.data.get("name", "unknown")
                        for d in dangers:
                            logger.warning("[ETLAgent] %s — SQL: %s", d, sql[:200])

                        # Create approval entry
                        approval_id = approval_manager.create_approval({
                            "tool": tool_name,
                            "sql": sql,
                            "warnings": dangers,
                            "message": "检测到高危 SQL 操作，请确认后继续。\n" + "\n".join(dangers),
                        })

                        # Yield approval_required BEFORE the tool executes.
                        # At this point the parent generator is suspended at
                        # ``yield tool_call``, so _execute_tool hasn't run yet.
                        yield AgentEvent(
                            type="approval_required",
                            data={
                                "approval_id": approval_id,
                                "tool": tool_name,
                                "sql": sql,
                                "warnings": dangers,
                                "message": "检测到高危 SQL 操作，请在 60 秒内确认。",
                            },
                            metadata={"requires_user_action": True},
                        )

                        # Suspend until user approves / rejects (or timeout)
                        approved = await approval_manager.wait_for_decision(approval_id)

                        if not approved:
                            entry = approval_manager.get(approval_id)
                            reason = (
                                entry.reject_reason if entry and entry.reject_reason
                                else "操作已超时或被用户拒绝"
                            )
                            logger.warning(
                                "[ETLAgent] Tool call aborted: %s — %s",
                                tool_name, reason,
                            )
                            yield AgentEvent(
                                type="error",
                                data=f"操作已中止：{reason}",
                                metadata={"approval_id": approval_id, "aborted": True},
                            )
                            return  # stop the whole agent loop

            # Pass every event through (tool_call, tool_result, thinking, content, …)
            yield event


# ──────────────────────────────────────────────────────────
# ETL Engineer Agent
# ──────────────────────────────────────────────────────────

_ETL_BASE_PROMPT = """\
你是一名资深的**数据加工工程师**，专注于：
1. 理解业务需求，从需求描述中提取数据目标
2. 分析数据库表结构（使用工具获取实际 Schema）
3. 设计 ETL 方案（清洗规则、关联逻辑、聚合粒度）
4. 生成可直接执行的 ClickHouse SQL 脚本（建表语句 + 写入语句）

**行为准则**：
- 在编写 SQL 前，必须先通过工具查询表结构，不得假设字段名
- 生成 SQL 后，主动输出 Dry-Run 校验清单
- 遇到 DROP / TRUNCATE / DELETE 等高危操作，必须明确标注 ⚠️ 并等待用户确认
- 建表语句必须包含 `COMMENT` 字段注释
"""


class ETLEngineerAgent:
    """
    ETL 工程师专用 Agent。

    与 MasterAgent 接口兼容（process / process_stream），
    可直接替换作为特定对话的处理器。
    """

    AGENT_TYPE = "etl_engineer"

    def __init__(
        self,
        llm_adapter,
        mcp_manager: MCPServerManager,
        max_iterations: int = 12,
    ):
        self.llm_adapter = llm_adapter
        self.mcp_manager = mcp_manager
        self.max_iterations = max_iterations

    async def process_stream(
        self,
        message: str,
        context: Dict[str, Any],
        cancel_event: Optional[asyncio.Event] = None,
    ) -> AsyncGenerator[AgentEvent, None]:
        """
        流式处理用户消息。

        Yields AgentEvent；危险 SQL 的 ``approval_required`` 事件由
        ETLAgenticLoop.run_streaming 在工具调用前发出，真正暂停等待用户审批。
        """
        etl_context = {**context, "system_prompt": _ETL_BASE_PROMPT}

        loop = ETLAgenticLoop(
            llm_adapter=self.llm_adapter,
            mcp_manager=self.mcp_manager,
            max_iterations=self.max_iterations,
            cancel_event=cancel_event,
        )

        async for event in loop.run_streaming(message, etl_context):
            yield event

    async def process(
        self,
        message: str,
        context: Dict[str, Any],
    ) -> AgenticResult:
        """Non-streaming processing — collects all events into AgenticResult."""
        events: List[AgentEvent] = []
        final_content = ""
        success = True

        async for event in self.process_stream(message, context):
            events.append(event)
            if event.type == "content":
                final_content = event.data or ""
            elif event.type == "error" and not final_content:
                success = False
                final_content = event.data or "ETL 处理时发生错误"

        return AgenticResult(
            success=success,
            content=final_content,
            metadata={
                "agent_type": self.AGENT_TYPE,
                "event_count": len(events),
                "approval_events": sum(
                    1 for e in events if e.type == "approval_required"
                ),
            },
            events=events,
        )
