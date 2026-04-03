"""
Multi-Agent Handoff Orchestrator (v2)

本模块实现多 Agent 协作的 Handoff 编排机制：

架构
----
  用户请求
    ↓
  AgentOrchestrator._route_message() — 关键词评分路由
    ↓
  Agent-A (ETLEngineerAgent 或 DataAnalystAgent 或通用 AgenticLoop)
    ↓ 流式 AgentEvent
  AgentOrchestrator._detect_handoff() — 检测 handoff 信号
    ↓ handoff 触发时
  构建 HandoffPacket（携带上游产物）
    ↓
  Agent-B (对应专业 Agent)
    ↓ 流式 AgentEvent
  合并输出

HandoffPacket
-------------
  from_agent   : 上游 Agent 类型标识
  to_agent     : 下游 Agent 类型标识
  task_summary : 上游任务完成摘要
  artifacts    : 上游产出物（query_result, schema, sql_script 等）
  next_action  : 给下游 Agent 的具体指令

Handoff 触发条件
---------------
  1. 上游 Agent 显式发出 ``type="handoff"`` AgentEvent
  2. ETL Agent 完成数据生成（content 包含 "建议分析" / "analysis" 等关键词）
  3. Analyst Agent 发现需要 ETL 预处理（content 包含 "需要建表" / "需要加工" 等）

注意
----
  orchestrator_v2 与 MasterAgent（orchestrator.py）并列存在，
  MasterAgent 继续处理简单路由，orchestrator_v2 专门负责跨 Agent 协作场景。
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, Dict, List, Optional

from backend.agents.agentic_loop import AgentEvent, AgenticLoop, AgenticResult
from backend.agents.etl_agent import ETLEngineerAgent
from backend.agents.analyst_agent import DataAnalystAgent
from backend.mcp.manager import MCPServerManager
from backend.core.agent_mcp_binder import AgentMCPBinder

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────
# Handoff detection patterns
# ──────────────────────────────────────────────────────────

# Signals that ETL Agent wants to pass work to Analyst
_ETL_TO_ANALYST_PATTERNS = [
    re.compile(r"建议(数据)?分析师", re.IGNORECASE),
    re.compile(r"(数据|查询)(结果|已(生成|完成|准备好))", re.IGNORECASE),
    re.compile(r"(可以|需要)(进行|做)分析", re.IGNORECASE),
    re.compile(r"analysis\s+(ready|complete|done)", re.IGNORECASE),
]

# Signals that Analyst wants to pass work to ETL
_ANALYST_TO_ETL_PATTERNS = [
    re.compile(r"(需要|建议)(建表|建立宽表|ETL(加工)?|数据加工)", re.IGNORECASE),
    re.compile(r"缺少.*(字段|表|数据)", re.IGNORECASE),
    re.compile(r"(需要|请|建议).*(生成|创建|建立).*(表|脚本)", re.IGNORECASE),
]


# ──────────────────────────────────────────────────────────
# HandoffPacket
# ──────────────────────────────────────────────────────────

@dataclass
class HandoffPacket:
    """
    Carries context from the upstream agent to the downstream agent.

    Fields
    ------
    from_agent     : Agent type identifier ("etl_engineer" | "analyst" | "general")
    to_agent       : Target agent type ("etl_engineer" | "analyst")
    task_summary   : Human-readable summary of what the upstream agent accomplished
    artifacts      : Key-value store for upstream outputs
                     e.g. {"query_result": [...], "schema": "...", "sql_script": "..."}
    next_action    : Instruction for the downstream agent
    conversation_id: Original conversation ID (for context)
    timestamp      : ISO-8601 creation time
    """
    from_agent: str
    to_agent: str
    task_summary: str
    artifacts: Dict[str, Any] = field(default_factory=dict)
    next_action: str = ""
    conversation_id: str = ""
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_context_prompt(self) -> str:
        """
        Render the packet as a system-level context string that is prepended
        to the downstream agent's conversation context.
        """
        lines = [
            "## 上游 Agent 移交信息",
            f"- **来源**: {self.from_agent}",
            f"- **目标**: {self.to_agent}",
            f"- **任务摘要**: {self.task_summary}",
        ]
        if self.next_action:
            lines.append(f"- **下一步指令**: {self.next_action}")
        if self.artifacts:
            lines.append("\n### 上游产出物")
            for key, val in self.artifacts.items():
                val_str = str(val)
                if len(val_str) > 500:
                    val_str = val_str[:500] + "\n…（已截断）"
                lines.append(f"**{key}**:\n```\n{val_str}\n```")
        return "\n".join(lines)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "from_agent": self.from_agent,
            "to_agent": self.to_agent,
            "task_summary": self.task_summary,
            "artifacts": self.artifacts,
            "next_action": self.next_action,
            "conversation_id": self.conversation_id,
            "timestamp": self.timestamp,
        }


# ──────────────────────────────────────────────────────────
# Agent routing helpers
# ──────────────────────────────────────────────────────────

_ETL_KEYWORDS = frozenset([
    "etl", "宽表", "数据加工", "合并表", "数据整合", "脚本生成",
    "建表", "create table", "insert into", "数据管道", "pipeline",
    "数据清洗", "增量", "全量", "分区表", "数据接入",
])

_ANALYST_KEYWORDS = frozenset([
    "分析", "统计", "留存", "漏斗", "趋势", "同比", "环比",
    "分布", "数据分析", "用户行为", "转化率", "dau", "mau",
    "retention", "funnel", "报表", "看数", "查询",
])

AgentType = str  # "etl_engineer" | "analyst" | "general"


def _score_routing(message: str) -> AgentType:
    """Keyword-score the message and return the best-fit agent type."""
    lower = message.lower()
    etl_score = sum(1 for kw in _ETL_KEYWORDS if kw in lower)
    analyst_score = sum(1 for kw in _ANALYST_KEYWORDS if kw in lower)

    if etl_score == 0 and analyst_score == 0:
        return "general"
    return "etl_engineer" if etl_score >= analyst_score else "analyst"


# ──────────────────────────────────────────────────────────
# AgentOrchestrator
# ──────────────────────────────────────────────────────────


class AgentOrchestrator:
    """
    Multi-agent orchestrator with Handoff support.

    Example use:
        orchestrator = AgentOrchestrator(llm_adapter, mcp_manager)
        async for event in orchestrator.run_streaming(message, context):
            ...  # forward to SSE

    The orchestrator:
    1. Routes the message to the most appropriate first agent.
    2. Streams all events from that agent (including thinking / tool calls).
    3. Monitors the agent's output for handoff signals.
    4. If a handoff is triggered:
       a. Emits a ``handoff`` AgentEvent (visible in ThoughtProcess).
       b. Builds a HandoffPacket from collected artifacts.
       c. Injects the packet into the next agent's context.
       d. Streams all events from the next agent.
    5. Emits a ``done`` sentinel at the end.
    """

    MAX_HOPS = 2  # Maximum number of agent transitions per conversation turn

    def __init__(
        self,
        llm_adapter,
        mcp_manager: MCPServerManager,
        max_iterations_per_agent: int = 12,
    ) -> None:
        self.llm_adapter = llm_adapter
        self.mcp_manager = mcp_manager
        self.max_iterations = max_iterations_per_agent
        self._binder = AgentMCPBinder()  # 加载 .claude/agent_config.yaml

    # ─────────────────────────────────────────────────────
    # Public API
    # ─────────────────────────────────────────────────────

    async def run_streaming(
        self,
        message: str,
        context: Dict[str, Any],
    ) -> AsyncGenerator[AgentEvent, None]:
        """
        Orchestrate one user turn, potentially across multiple agents.

        Yields AgentEvent instances that include:
          thinking, tool_call, tool_result   (from each agent)
          handoff                             (when transitioning between agents)
          content                             (final answer)
          error                               (if something goes wrong)
        """
        from backend.skills.skill_loader import get_skill_loader

        current_agent_type = _score_routing(message)
        current_message = message
        current_context = dict(context)
        hop = 0
        accumulated_artifacts: Dict[str, Any] = {}

        # 第一跳匹配 Skills（handoff 后 skills 复用，不重新匹配）
        matched_skills = get_skill_loader().find_triggered(message)
        first_hop_skills = [{"name": s.name, "title": s.description} for s in matched_skills]

        while hop < self.MAX_HOPS:
            hop += 1
            agent = self._build_agent(current_agent_type)
            agent_type_label = getattr(agent, "AGENT_TYPE", "general")

            logger.info(
                "[Orchestrator] Hop %d/%d: running %s agent",
                hop, self.MAX_HOPS, agent_type_label,
            )

            # 每跳开始时通知前端当前 Agent 及技能
            yield AgentEvent(
                type="agent_start",
                data={
                    "agent_type": current_agent_type,
                    "agent_label": self._agent_label(agent_type_label),
                    "skills": first_hop_skills if hop == 1 else [],
                },
                metadata={"hop": hop},
            )

            yield AgentEvent(
                type="thinking",
                data=f"[编排器] 将请求路由至 {self._agent_label(agent_type_label)}",
                metadata={"orchestrator": True, "hop": hop, "agent": agent_type_label},
            )

            # Collect events; watch for handoff signals
            collected_content = ""
            handoff_event: Optional[AgentEvent] = None

            if hasattr(agent, "process_stream"):
                stream = agent.process_stream(current_message, current_context)
            else:
                stream = agent.run_streaming(current_message, current_context)

            async for event in stream:
                # Collect tool results as artifacts for potential handoff
                if event.type == "tool_result":
                    result_data = event.data or {}
                    tool_name = result_data.get("name", f"tool_{len(accumulated_artifacts)}")
                    accumulated_artifacts[tool_name] = result_data.get("result", result_data)

                # Watch for explicit handoff request
                if event.type == "handoff":
                    handoff_event = event
                    # Don't yield the raw handoff event here; we'll yield a
                    # formatted one after processing
                    continue

                # Watch for implicit handoff signals in content
                if event.type == "content" and event.data:
                    collected_content += (event.data or "")
                    handoff_target = self._detect_implicit_handoff(
                        agent_type_label, event.data
                    )
                    if handoff_target and not handoff_event:
                        handoff_event = AgentEvent(
                            type="handoff",
                            data={
                                "from_agent": agent_type_label,
                                "to_agent": handoff_target,
                                "reason": "content_signal",
                            },
                        )

                yield event

            # ── Handle handoff ──────────────────────────────────────
            if handoff_event and hop < self.MAX_HOPS:
                from_agent = handoff_event.data.get("from_agent", agent_type_label)
                to_agent = handoff_event.data.get("to_agent", "analyst")
                reason = handoff_event.data.get("reason", "explicit")

                packet = HandoffPacket(
                    from_agent=from_agent,
                    to_agent=to_agent,
                    task_summary=collected_content[:800] if collected_content else "(无文本摘要)",
                    artifacts=dict(accumulated_artifacts),
                    next_action=handoff_event.data.get("next_action", ""),
                    conversation_id=context.get("conversation_id", ""),
                )

                yield AgentEvent(
                    type="handoff",
                    data={
                        "from_agent": from_agent,
                        "to_agent": to_agent,
                        "reason": reason,
                        "task_summary": packet.task_summary[:200],
                        "artifact_keys": list(accumulated_artifacts.keys()),
                    },
                    metadata={"hop": hop},
                )

                logger.info(
                    "[Orchestrator] Handoff: %s → %s (reason=%s, artifacts=%s)",
                    from_agent,
                    to_agent,
                    reason,
                    list(accumulated_artifacts.keys()),
                )

                # Inject handoff packet into next agent's context
                handoff_prompt = packet.to_context_prompt()
                existing_system = current_context.get("system_prompt", "")
                current_context = {
                    **current_context,
                    "system_prompt": (
                        existing_system + "\n\n" + handoff_prompt
                        if existing_system
                        else handoff_prompt
                    ),
                }
                # The user message for the next agent is the original message
                # enriched with the handoff context
                current_message = (
                    f"[继续上游 {self._agent_label(from_agent)} 的工作]\n\n"
                    + message
                )
                current_agent_type = to_agent
                # Continue outer while loop for next hop
            else:
                # No handoff (or max hops reached) — we're done
                break

    # ─────────────────────────────────────────────────────
    # Helpers
    # ─────────────────────────────────────────────────────

    def _build_agent(self, agent_type: AgentType):
        """Instantiate an agent by type string, with a permission-filtered MCP manager."""
        filtered = self._binder.get_filtered_manager(agent_type, self.mcp_manager)
        if agent_type == "etl_engineer":
            return ETLEngineerAgent(
                llm_adapter=self.llm_adapter,
                mcp_manager=filtered,
                max_iterations=self.max_iterations,
            )
        if agent_type == "analyst":
            return DataAnalystAgent(
                llm_adapter=self.llm_adapter,
                mcp_manager=filtered,
                max_iterations=self.max_iterations,
            )
        # Fallback: generic loop
        return AgenticLoop(
            llm_adapter=self.llm_adapter,
            mcp_manager=filtered,
            max_iterations=self.max_iterations,
        )

    @staticmethod
    def _agent_label(agent_type: str) -> str:
        labels = {
            "etl_engineer": "数据加工工程师",
            "analyst": "数据分析师",
            "general": "通用助手",
        }
        return labels.get(agent_type, agent_type)

    @staticmethod
    def _detect_implicit_handoff(
        current_agent: str, content: str
    ) -> Optional[str]:
        """
        Scan the agent's content output for implicit handoff signals.

        Returns the target agent type, or None if no handoff is signalled.
        """
        if current_agent == "etl_engineer":
            for pat in _ETL_TO_ANALYST_PATTERNS:
                if pat.search(content):
                    return "analyst"
        elif current_agent == "analyst":
            for pat in _ANALYST_TO_ETL_PATTERNS:
                if pat.search(content):
                    return "etl_engineer"
        return None


# ──────────────────────────────────────────────────────────
# Convenience factory
# ──────────────────────────────────────────────────────────


def create_orchestrator(
    llm_adapter,
    mcp_manager: MCPServerManager,
    max_iterations_per_agent: int = 12,
) -> AgentOrchestrator:
    """Convenience factory, matches the style of other agent factories."""
    return AgentOrchestrator(
        llm_adapter=llm_adapter,
        mcp_manager=mcp_manager,
        max_iterations_per_agent=max_iterations_per_agent,
    )
