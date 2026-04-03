"""
Data Analyst Agent

专注于 ClickHouse 数据查询与业务分析（只读 ClickHouse + 文件写入需授权）。

特性：
- 使用 clickhouse-analyst.md 技能（通过 SkillLoader 自动注入）
- 只读 MCP 工具代理：在工具执行层拦截 ClickHouse 写操作（INSERT/DROP/ALTER 等）
- 文件写入授权门：首次写文件前弹出审批弹窗，授权后同一对话持续生效
- 内置分析结论格式化：引导 LLM 输出结论+建议的标准格式
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, AsyncGenerator, Dict, List, Optional

from backend.agents.agentic_loop import AgentEvent, AgenticLoop, AgenticResult
from backend.mcp.manager import MCPServerManager
from backend.mcp.tool_formatter import parse_tool_name

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────
# Read-Only MCP Proxy
# ──────────────────────────────────────────────────────────

# Tools explicitly allowed for each server type
_READ_ONLY_TOOLS: Dict[str, List[str]] = {
    "clickhouse": [
        "query",
        "list_databases",
        "list_tables",
        "describe_table",
        "get_table_overview",
        "sample_table_data",
        "test_connection",
        "get_server_info",
    ],
    "mysql": [
        "query",
        "list_databases",
        "list_tables",
        "describe_table",
    ],
    "filesystem": [
        "list_directory",
        "read_file",
        "get_file_info",
        "get_file_type",
        "search_files",
        "list_allowed_directories",
        # write tools — gated at loop level by FileWriteAgenticLoop approval
        "write_file",
        "create_directory",
        # "delete" intentionally excluded — too destructive for analyst role
    ],
    "lark": [
        "get_document",
        "list_documents",
        "search_documents",
    ],
}

# SQL verbs that constitute write operations
_WRITE_VERBS = frozenset([
    "INSERT", "UPDATE", "DELETE", "DROP", "CREATE", "ALTER",
    "TRUNCATE", "RENAME", "REPLACE", "UPSERT", "MERGE",
    "OPTIMIZE",   # ClickHouse OPTIMIZE can be destructive
])


def _is_readonly_sql(sql: str) -> bool:
    """Return True if sql looks like a read-only statement."""
    import re
    first_verb = re.match(r"\s*(\w+)", sql)
    if first_verb:
        return first_verb.group(1).upper() not in _WRITE_VERBS
    return True  # Default allow for unrecognised patterns


class ReadOnlyMCPProxy:
    """
    Transparent proxy around MCPServerManager that enforces read-only access.

    Blocks:
      - Tools not in the ``_READ_ONLY_TOOLS`` allowlist for their server type
      - ``query`` / ``execute`` calls containing write SQL verbs

    Passes through:
      - ``servers`` attribute (so format_mcp_tools_for_claude still works)
      - ``list_servers()``
      - All read-safe tool calls
    """

    def __init__(self, real_manager: MCPServerManager):
        self._real = real_manager

    # ── Transparent attribute access ─────────────────────

    @property
    def servers(self):
        return self._real.servers

    @property
    def server_configs(self):
        return self._real.server_configs

    def list_servers(self) -> List[Dict[str, Any]]:
        return self._real.list_servers()

    # ── Guarded tool execution ────────────────────────────

    async def call_tool(
        self, server_name: str, tool_name: str, arguments: Dict[str, Any]
    ) -> Dict[str, Any]:
        server_type = self._real.server_configs.get(server_name, {}).get("type", "")
        allowed = _READ_ONLY_TOOLS.get(server_type)

        # 1 — Check allowlist
        if allowed is not None and tool_name not in allowed:
            return {
                "success": False,
                "error": (
                    f"分析师角色不允许执行写操作工具 '{tool_name}'。"
                    "如需修改数据，请使用 ETL 工程师模式。"
                ),
            }

        # 2 — Check SQL content for write operations
        sql = (
            arguments.get("query")
            or arguments.get("sql")
            or arguments.get("statement")
            or ""
        )
        if sql and not _is_readonly_sql(str(sql)):
            return {
                "success": False,
                "error": (
                    f"分析师角色仅允许 SELECT 查询，"
                    f"检测到写操作语句: {str(sql)[:120]}"
                ),
            }

        return await self._real.call_tool(server_name, tool_name, arguments)


# ──────────────────────────────────────────────────────────
# File-Write Gated Agentic Loop
# ──────────────────────────────────────────────────────────

# Filesystem tools that require session-level approval before execution
_FILE_WRITE_TOOLS = frozenset(["write_file", "create_directory"])


class FileWriteAgenticLoop(AgenticLoop):
    """
    AgenticLoop with per-conversation file-write approval gate.

    First time the LLM calls write_file / create_directory in a conversation:
      1. Pre-check: if the target path is outside write-allowed directories,
         skip the approval modal entirely and let FilesystemPermissionProxy
         return the rejection directly to the LLM (so it can self-correct).
      2. If path IS write-allowed: Yields ``approval_required`` event
         (frontend shows ApprovalModal) and awaits user decision.

    Once approved the grant persists for the entire conversation — subsequent
    file-write calls proceed without interruption.
    """

    def _find_permission_proxy(self):
        """
        Walk the mcp_manager chain to find a FilesystemPermissionProxy.

        Chain: ReadOnlyMCPProxy → FilesystemPermissionProxy → FilteredMCPManager
        Returns the proxy instance, or None if not present.
        """
        try:
            from backend.core.filesystem_permission_proxy import FilesystemPermissionProxy
        except ImportError:
            return None

        manager = self.mcp_manager
        # One level: ReadOnlyMCPProxy._real
        candidate = getattr(manager, "_real", None)
        if isinstance(candidate, FilesystemPermissionProxy):
            return candidate
        # Two levels (in case wrapping order changes)
        if candidate is not None:
            deeper = getattr(candidate, "_real", None) or getattr(candidate, "_base", None)
            if isinstance(deeper, FilesystemPermissionProxy):
                return deeper
        return None

    def _path_is_write_allowed(self, path: str) -> bool:
        """
        Return True if the given path is inside the filesystem write-allowed
        directories according to FilesystemPermissionProxy.
        Falls back to True (permissive) when the proxy is not in the chain
        so that non-proxied deployments are unaffected.
        """
        proxy = self._find_permission_proxy()
        if proxy is None:
            return True  # no proxy → allow, lower layers decide
        return proxy._is_write_allowed(path)

    async def run_streaming(
        self,
        message: str,
        context: Dict[str, Any],
    ) -> AsyncGenerator[AgentEvent, None]:
        from backend.core.approval_manager import approval_manager

        conversation_id: str = str(context.get("conversation_id", ""))

        async for event in super().run_streaming(message, context):
            if event.type == "tool_call":
                # event.data["name"] 是命名空间格式如 "filesystem__write_file"
                namespaced: str = event.data.get("name", "")
                server_name, tool_name = parse_tool_name(namespaced)

                if (
                    server_name == "filesystem"
                    and tool_name in _FILE_WRITE_TOOLS
                    and not approval_manager.is_file_write_granted(conversation_id)
                ):
                    tool_input: Dict[str, Any] = event.data.get("input", {})
                    file_path: str = tool_input.get("path", "")

                    # ── 预检：路径在禁止写入目录内时，跳过审批弹窗 ──────────
                    # 直接转发 tool_call 给 base class，让 FilesystemPermissionProxy
                    # 返回明确的拒绝消息给 LLM，LLM 会自行纠正路径后重试。
                    if not self._path_is_write_allowed(file_path):
                        logger.warning(
                            "[FileWrite] Path '%s' outside write-allowed dirs — "
                            "skipping approval modal, proxy will reject directly.",
                            file_path,
                        )
                        yield event  # forward to base class → FilesystemPermissionProxy rejects
                        continue     # back to async for

                    # ── 路径合法 → 正常审批弹窗流程 ──────────────────────────
                    # content preview (first 500 chars) for the approval modal
                    content_preview: str = str(tool_input.get("content", ""))[:500]

                    approval_id = approval_manager.create_approval({
                        "type": "file_write",
                        "tool": tool_name,
                        "path": file_path,
                        "content_preview": content_preview,
                        "conversation_id": conversation_id,
                        "message": (
                            f"Agent 请求写入文件：{file_path or '（路径待定）'}\n"
                            "授权后，本次对话将持续拥有文件写入权限，无需再次确认。"
                        ),
                    })

                    logger.info(
                        "[FileWrite] Approval required for %s path=%s conv=%s",
                        tool_name, file_path, conversation_id[:8] if conversation_id else "?"
                    )

                    yield AgentEvent(
                        type="approval_required",
                        data={
                            "approval_id": approval_id,
                            "approval_type": "file_write",    # 区分于 sql 类型
                            "tool": tool_name,
                            "path": file_path,
                            "content_preview": content_preview,
                            "session_grant": True,
                            "message": (
                                "Agent 请求文件写入权限。\n"
                                "授权后本次对话将持续拥有此权限，无需再次确认。"
                            ),
                        },
                        metadata={"requires_user_action": True},
                    )

                    approved = await approval_manager.wait_for_decision(approval_id)

                    if approved:
                        approval_manager.grant_file_write(conversation_id)
                        logger.info(
                            "[FileWrite] Session write granted for conv %s",
                            conversation_id[:8] if conversation_id else "?"
                        )
                    else:
                        entry = approval_manager.get(approval_id)
                        reason = (
                            entry.reject_reason
                            if entry and entry.reject_reason
                            else "用户拒绝文件写入权限"
                        )
                        logger.warning("[FileWrite] Write rejected: %s", reason)
                        yield AgentEvent(
                            type="error",
                            data=f"文件写入已中止：{reason}",
                            metadata={"approval_id": approval_id, "aborted": True},
                        )
                        return  # stop the loop; agent should explain to user

            yield event


# ──────────────────────────────────────────────────────────
# Analyst Agent
# ──────────────────────────────────────────────────────────

_ANALYST_BASE_PROMPT = """\
你是一名资深的**数据分析师**，专注于：
1. 高效编写 ClickHouse SQL 查询（只读 SELECT）
2. 进行留存分析、漏斗分析、同比环比、用户行为分析等
3. 基于真实查询结果给出数据驱动的业务洞察
4. 以标准格式输出：核心发现 → 数据查询 → 结果解读 → 建议

**行为准则（只读安全约束）**：
- 只允许执行 SELECT 查询，严禁 INSERT / UPDATE / DELETE / DROP 等写操作
- 查询必须包含分区过滤条件（WHERE dt = ... 或 WHERE date = ...）
- 优先使用 PREWHERE 代替 WHERE 进行条件过滤（ClickHouse 性能优化）
- 统计口径必须在回答中明确说明
- 对于数据中的异常值，先质疑数据质量，再得出业务结论
- 所有结论必须基于实际查询结果，不得假设未查询的数据

**数据库探索准则**：
- 探索数据库表时，必须使用 ClickHouse 工具（list_databases / list_tables / describe_table / get_table_overview / sample_table_data）
- 先调用 list_databases 获取数据库列表，再调用 list_tables 获取表列表，再用 describe_table 获取字段详情
- 当用户指定环境（如 SG环境、IDN环境），使用对应环境的 ClickHouse 服务器工具
- **重要**：如果可用工具列表中没有任何 ClickHouse 工具，必须立即停止并告知用户：
  "当前 ClickHouse 服务器未连接，请检查后端 .env 配置（如 CLICKHOUSE_SG_HOST、CLICKHOUSE_SG_PORT 等），重启服务后重试。"
  绝对不能用 filesystem 或其他工具代替 ClickHouse 完成数据库探索。
"""

# 环境标签映射（env code → 中文说明）
_ENV_LABELS: Dict[str, str] = {
    "sg": "SG（新加坡）",
    "idn": "IDN（印度尼西亚）",
    "jp": "JP（日本）",
    "cn": "CN（中国）",
    "us": "US（美国）",
    "eu": "EU（欧洲）",
}


def _build_env_section(mcp_manager) -> str:
    """
    从 mcp_manager 动态提取已注册的 ClickHouse 服务器，
    生成「可用环境」说明注入到系统提示中。
    """
    try:
        servers = mcp_manager.list_servers() if hasattr(mcp_manager, "list_servers") else []
    except Exception:
        return ""

    ch_servers = [s for s in servers if s.get("type") == "clickhouse"]
    if not ch_servers:
        return ""

    lines = ["\n**当前可用的 ClickHouse 数据库环境**："]
    for s in ch_servers:
        name: str = s["name"]  # e.g. clickhouse-sg-ro / clickhouse-idn
        # 解析 env 代码：clickhouse-sg-ro → sg
        base = name[len("clickhouse-"):]
        env_code = base[:-len("-ro")] if base.endswith("-ro") else base
        label = _ENV_LABELS.get(env_code.lower(), env_code.upper())
        lines.append(
            f"  - {label}：服务器名 `{name}`，"
            f"工具示例：`{name}__list_tables`、`{name}__list_databases`"
        )

    lines += [
        "",
        '当用户提到某个环境（如"SG环境"、"IDN环境"），选择对应服务器前缀的工具。',
        "若用户未指定环境，逐一查询所有可用环境，或询问用户希望查哪个环境。",
    ]
    return "\n".join(lines) + "\n"


def _build_file_write_section(context: Dict[str, Any]) -> str:
    """注入文件写入能力说明到系统提示。"""
    from backend.core.approval_manager import approval_manager
    conversation_id = str(context.get("conversation_id", ""))
    already_granted = (
        conversation_id
        and approval_manager.is_file_write_granted(conversation_id)
    )

    # 写入目录约束（与 FilesystemPermissionProxy 保持一致）
    username = context.get("username", "anonymous")
    path_constraint = (
        "**允许写入的目录**（严格遵守，违反将被系统拒绝）：\n"
        f"  - `{username}/`（报告、导出数据、分析结果等，推荐）\n"
        f"    示例：`{username}/reports/sg_schema_exploration.md`\n"
        "  - `.claude/skills/user/`（仅用于保存自定义技能 .md 文件）\n"
        "  禁止写入 `backend/`、`.claude/skills/`（根目录）或项目其他目录。\n"
        "  ⚠️ 路径说明：文件系统根目录已指向 customer_data/，直接使用用户名作为路径前缀即可，"
        "勿在路径中重复写 customer_data/（否则会产生双层目录）。"
    )

    if already_granted:
        return (
            f"\n**文件写入**：本次对话已获得文件写入授权，"
            f"可直接使用 write_file / create_directory 工具将分析结果输出到文件。\n"
            f"{path_constraint}\n"
        )
    return (
        f"\n**文件写入**：如需将分析结果写入文件，可使用 write_file 工具。"
        f"首次写入时系统会弹出授权确认，用户授权后本次对话将持续拥有写入权限。\n"
        f"{path_constraint}\n"
    )


class DataAnalystAgent:
    """
    数据分析师专用 Agent（只读）。

    与 MasterAgent 接口兼容（process / process_stream）。
    使用 ReadOnlyMCPProxy 在工具执行层强制只读约束。
    """

    AGENT_TYPE = "data_analyst"

    def __init__(
        self,
        llm_adapter,
        mcp_manager: MCPServerManager,
        max_iterations: int = 30,
    ):
        self.llm_adapter = llm_adapter
        self.mcp_manager = mcp_manager
        # Wrap manager with read-only proxy
        self._readonly_manager = ReadOnlyMCPProxy(mcp_manager)
        self.max_iterations = max_iterations

    async def process_stream(
        self,
        message: str,
        context: Dict[str, Any],
        cancel_event: Optional[asyncio.Event] = None,
    ) -> AsyncGenerator[AgentEvent, None]:
        """流式处理（ClickHouse 只读 + 文件写入需授权）。"""
        system_prompt = (
            _ANALYST_BASE_PROMPT
            + _build_env_section(self.mcp_manager)
            + _build_file_write_section(context)
        )
        analyst_context = {**context, "system_prompt": system_prompt}

        # Use FileWriteAgenticLoop to gate filesystem write operations
        loop = FileWriteAgenticLoop(
            llm_adapter=self.llm_adapter,
            mcp_manager=self._readonly_manager,
            max_iterations=self.max_iterations,
            cancel_event=cancel_event,
        )

        async for event in loop.run_streaming(message, analyst_context):
            yield event

    async def process(
        self,
        message: str,
        context: Dict[str, Any],
    ) -> AgenticResult:
        """Non-streaming processing — collects all events."""
        events: List[AgentEvent] = []
        final_content = ""
        success = True

        async for event in self.process_stream(message, context):
            events.append(event)
            if event.type == "content":
                final_content = event.data or ""
            elif event.type == "error" and not final_content:
                success = False
                final_content = event.data or "数据分析时发生错误"

        return AgenticResult(
            success=success,
            content=final_content,
            metadata={
                "agent_type": self.AGENT_TYPE,
                "event_count": len(events),
            },
            events=events,
        )
