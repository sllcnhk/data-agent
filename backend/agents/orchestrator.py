"""
Master Agent (Orchestrator)

协调整个对话流程,理解用户意图,调用合适的工具或Sub-Agent
"""
import asyncio
from typing import Dict, List, Any, Optional
from uuid import UUID
import json
import logging

from backend.mcp.manager import MCPServerManager
from backend.core.model_adapters.factory import ModelAdapterFactory
from backend.core.conversation_format import UnifiedConversation, UnifiedMessage, MessageRole
from backend.agents.agentic_loop import AgenticLoop, AgentEvent
from backend.agents.etl_agent import ETLEngineerAgent
from backend.agents.analyst_agent import DataAnalystAgent
from backend.core.agent_mcp_binder import AgentMCPBinder

logger = logging.getLogger(__name__)


class IntentClassifier:
    """意图分类器"""

    INTENTS = {
        "database_connection": {
            "keywords": ["连接数据库", "connect database", "链接数据库", "连上数据库", "数据库连接", "连接clickhouse", "连接mysql"],
            "description": "连接数据库"
        },
        "schema_exploration": {
            "keywords": ["表结构", "字段", "列", "schema", "结构", "查看表", "有哪些表", "数据库列表"],
            "description": "查看数据库结构"
        },
        "data_sampling": {
            "keywords": ["示例数据", "看看数据", "数据样本", "前几行", "sample", "查看数据"],
            "description": "获取示例数据"
        },
        "data_query": {
            "keywords": ["查询", "query", "统计", "计算", "多少", "总计", "平均"],
            "description": "数据查询和统计"
        },
        "data_analysis": {
            "keywords": ["分析", "分布", "趋势", "关系", "相关性", "异常"],
            "description": "数据分析"
        },
        "etl_design": {
            "keywords": ["宽表", "ETL", "加工", "合并表", "数据整合", "脚本生成"],
            "description": "ETL设计和脚本生成"
        },
        "report_generation": {
            "keywords": ["报表", "图表", "可视化", "dashboard", "报告"],
            "description": "报表生成"
        },
        "file_operation": {
            "keywords": ["文件", "上传", "读取文件", "file"],
            "description": "文件操作"
        },
        "lark_document": {
            "keywords": ["飞书", "lark", "在线文档", "文档"],
            "description": "Lark文档操作"
        },
        "general_chat": {
            "keywords": [],
            "description": "一般对话"
        }
    }

    @classmethod
    async def classify(cls, message: str, context: Dict[str, Any]) -> str:
        """
        分类用户意图

        Args:
            message: 用户消息
            context: 对话上下文

        Returns:
            意图类型
        """
        message_lower = message.lower()

        # 基于关键词的简单匹配
        for intent, config in cls.INTENTS.items():
            keywords = config.get("keywords", [])
            if any(keyword.lower() in message_lower for keyword in keywords):
                logger.info(f"Intent classified as: {intent}")
                return intent

        # 默认为一般对话
        return "general_chat"


class MasterAgent:
    """
    Master Agent (主协调Agent)

    负责:
    1. 意图识别
    2. 任务分解
    3. 选择并调用MCP工具或Sub-Agent
    4. 聚合结果
    5. 生成回复
    """

    def __init__(
        self,
        mcp_manager: MCPServerManager,
        model_key: str,
        llm_config: Dict[str, Any]
    ):
        """
        初始化Master Agent

        Args:
            mcp_manager: MCP管理器
            model_key: 模型标识
            llm_config: LLM配置
        """
        self.mcp_manager = mcp_manager
        self.model_key = model_key

        # 准备适配器参数
        # 确保类型正确（从数据库读取的可能是字符串）
        temperature = llm_config.get("temperature", 0.7)
        max_tokens = llm_config.get("max_tokens", 8192)

        # 类型转换
        try:
            temperature = float(temperature) if not isinstance(temperature, float) else temperature
        except (ValueError, TypeError):
            temperature = 0.7

        try:
            max_tokens = int(max_tokens) if not isinstance(max_tokens, int) else max_tokens
        except (ValueError, TypeError):
            max_tokens = 8192

        adapter_kwargs = {
            "temperature": temperature,
            "max_tokens": max_tokens
        }

        # 为支持故障转移的模型添加故障转移配置
        if llm_config.get("fallback_models") or model_key == "claude":
            adapter_kwargs["fallback_models"] = llm_config.get("fallback_models", [])
            adapter_kwargs["enable_fallback"] = llm_config.get("enable_fallback", True)

        self.llm_adapter = ModelAdapterFactory.create_adapter(
            provider=llm_config.get("model_type", model_key),
            api_key=llm_config.get("api_key"),
            model=llm_config.get("default_model"),
            base_url=llm_config.get("api_base_url"),
            **adapter_kwargs
        )
        self._binder = AgentMCPBinder()  # 加载 .claude/agent_config.yaml

    # ── Intent-based agent routing ────────────────────────

    _ETL_KEYWORDS = frozenset([
        "etl", "宽表", "数据加工", "合并表", "数据整合", "脚本生成",
        "建表", "create table", "insert into", "数据管道", "pipeline",
        "数据清洗", "增量", "全量", "分区表", "数据接入",
    ])
    _ANALYST_KEYWORDS = frozenset([
        "分析", "统计", "留存", "漏斗", "趋势", "同比", "环比",
        "分布", "数据分析", "用户行为", "转化率", "dau", "mau",
        "retention", "funnel", "报表", "看数", "查询",
        # 表结构 / 数据库探索类关键词
        "探索", "有哪些表", "有哪些数据库", "表结构", "查看表",
        "数据库结构", "schema", "字段", "有哪些字段", "列出表",
    ])

    def _select_agent(self, message: str):
        """
        Pick the right specialized agent based on message keywords.

        Returns one of:
          - ETLEngineerAgent  — ETL / schema / DDL keywords
          - DataAnalystAgent  — analytics / query keywords
          - None              — use generic AgenticLoop
        """
        lower = message.lower()

        # Count matches for each domain
        etl_score = sum(1 for kw in self._ETL_KEYWORDS if kw in lower)
        analyst_score = sum(1 for kw in self._ANALYST_KEYWORDS if kw in lower)

        if etl_score == 0 and analyst_score == 0:
            return None  # General chat → no specialised agent

        if etl_score >= analyst_score:
            logger.info(
                f"[MasterAgent] Routing to ETL agent "
                f"(etl={etl_score}, analyst={analyst_score})"
            )
            filtered = self._binder.get_filtered_manager("etl_engineer", self.mcp_manager)
            return ETLEngineerAgent(
                llm_adapter=self.llm_adapter,
                mcp_manager=filtered,
                max_iterations=self._binder.get_max_iterations("etl_engineer"),
            )

        logger.info(
            f"[MasterAgent] Routing to Analyst agent "
            f"(etl={etl_score}, analyst={analyst_score})"
        )
        filtered = self._binder.get_filtered_manager("analyst", self.mcp_manager)
        return DataAnalystAgent(
            llm_adapter=self.llm_adapter,
            mcp_manager=filtered,
            max_iterations=self._binder.get_max_iterations("analyst"),
        )

    async def process(
        self,
        message: str,
        conversation_context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        处理用户消息（非流式）

        根据消息关键词选择合适的 Agent：
        - ETL 关键词 → ETLEngineerAgent（含 SQL 安全检查）
        - 分析关键词 → DataAnalystAgent（只读工具约束）
        - 其他 → 通用 AgenticLoop

        Args:
            message: 用户消息
            conversation_context: 对话上下文

        Returns:
            处理结果 dict，含 success / content / metadata / events
        """
        try:
            agent = self._select_agent(message)

            if agent is not None:
                result = await agent.process(message, conversation_context)
            else:
                filtered = self._binder.get_filtered_manager("general", self.mcp_manager)
                loop = AgenticLoop(
                    llm_adapter=self.llm_adapter,
                    mcp_manager=filtered,
                    max_iterations=self._binder.get_max_iterations("general"),
                )
                result = await loop.run(message, conversation_context)

            return {
                "success": result.success,
                "content": result.content,
                "metadata": {
                    **result.metadata,
                    "model": self.model_key,
                },
                "events": [e.to_dict() for e in result.events],
            }
        except Exception as exc:
            logger.error(f"Error in MasterAgent.process: {exc}", exc_info=True)
            return {
                "success": False,
                "error": str(exc),
                "content": f"处理消息时出错: {exc}",
            }

    async def process_stream(
        self,
        message: str,
        conversation_context: Dict[str, Any],
        cancel_event: Optional[asyncio.Event] = None,
    ):
        """
        处理用户消息（流式）

        根据消息关键词路由到对应 Agent，流式 yield AgentEvent。

        Args:
            message: 用户消息
            conversation_context: 对话上下文

        Yields:
            AgentEvent (agent_start / thinking / tool_call / tool_result / content / error /
                        approval_required)
        """
        from backend.skills.skill_loader import get_skill_loader

        # ── 计算路由分数（与 _select_agent 逻辑对应）──
        lower = message.lower()
        etl_score = sum(1 for kw in self._ETL_KEYWORDS if kw in lower)
        analyst_score = sum(1 for kw in self._ANALYST_KEYWORDS if kw in lower)

        if etl_score > 0 and etl_score >= analyst_score:
            _agent_type = "etl_engineer"
            _agent_label = "数据加工工程师"
        elif analyst_score > 0:
            _agent_type = "analyst"
            _agent_label = "数据分析师"
        else:
            _agent_type = "general"
            _agent_label = "通用助手"

        matched_skills = get_skill_loader().find_triggered(message)

        yield AgentEvent(
            type="agent_start",
            data={
                "agent_type": _agent_type,
                "agent_label": _agent_label,
                "skills": [{"name": s.name, "title": s.description} for s in matched_skills],
            },
            metadata={},
        )

        agent = self._select_agent(message)

        if agent is not None:
            async for event in agent.process_stream(
                message, conversation_context, cancel_event=cancel_event
            ):
                yield event
        else:
            filtered = self._binder.get_filtered_manager("general", self.mcp_manager)
            loop = AgenticLoop(
                llm_adapter=self.llm_adapter,
                mcp_manager=filtered,
                max_iterations=self._binder.get_max_iterations("general"),
                cancel_event=cancel_event,
            )
            async for event in loop.run_streaming(message, conversation_context):
                yield event

    async def _handle_database_connection(
        self,
        message: str,
        context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """处理数据库连接请求"""
        # 列出所有可用的数据库服务器
        servers = self.mcp_manager.list_servers()

        # 筛选数据库类型的服务器
        db_servers = [
            s for s in servers
            if s["type"] in ["clickhouse", "mysql"]
        ]

        if not db_servers:
            # 无已注册的数据库服务器时，回退到 LLM 对话，避免硬错误
            logger.info("No database servers registered, falling back to general chat")
            return await self._handle_general_chat(message, context)

        # 构建回复
        server_list = "\n".join([
            f"- {s['name']} ({s['type']}) - {s['tool_count']}个工具可用"
            for s in db_servers
        ])

        content = f"""我已经连接了以下数据库:

{server_list}

你可以:
1. 查看数据库列表: "有哪些数据库"
2. 查看表列表: "数据库X有哪些表"
3. 查看表结构: "表Y的结构是什么"
4. 查看示例数据: "给我看看表Z的数据"
"""

        return {
            "success": True,
            "content": content,
            "metadata": {
                "intent": "database_connection",
                "servers": db_servers
            }
        }

    async def _handle_schema_exploration(
        self,
        message: str,
        context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """处理数据库结构探索请求"""
        try:
            # 简单实现:列出第一个ClickHouse服务器的数据库
            server = self.mcp_manager.get_server("clickhouse-idn")
            if not server:
                return {
                    "success": False,
                    "content": "未找到ClickHouse服务器"
                }

            # 调用list_databases工具
            result = await self.mcp_manager.call_tool(
                "clickhouse-idn",
                "list_databases",
                {}
            )

            if result and result.get("success"):
                data = result.get("data", {})
                databases = data.get("databases", [])

                content = f"""ClickHouse (IDN环境) 有以下数据库:

{chr(10).join([f"- {db}" for db in databases])}

你可以继续问:
- "数据库X有哪些表?"
- "查看表Y的结构"
"""
                return {
                    "success": True,
                    "content": content,
                    "metadata": {
                        "intent": "schema_exploration",
                        "databases": databases
                    }
                }
            else:
                return {
                    "success": False,
                    "content": "获取数据库列表失败"
                }

        except Exception as e:
            logger.error(f"Error in schema exploration: {e}")
            return {
                "success": False,
                "content": f"探索数据库结构时出错: {str(e)}"
            }

    async def _handle_data_sampling(
        self,
        message: str,
        context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """处理数据采样请求"""
        # 这里需要解析用户消息,提取表名
        # 简化版本:假设用户提供了明确的表名
        content = """请提供具体的表名,例如:

"给我看看 database.table 的数据"
"查看 events 表的前1000行"

我会为你获取示例数据。"""

        return {
            "success": True,
            "content": content,
            "metadata": {
                "intent": "data_sampling",
                "requires_table_name": True
            }
        }

    async def _handle_data_query(
        self,
        message: str,
        context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """处理数据查询请求"""
        # 需要SQL生成能力,暂时回退到一般对话
        return await self._handle_general_chat(message, context)

    async def _handle_general_chat(
        self,
        message: str,
        context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """处理一般对话"""
        try:
            # 构建系统提示
            system_prompt = self._build_system_prompt(context)

            # 构建对话历史
            message_dicts = self._build_conversation_history(message, context)

            # 转换为 UnifiedMessage 列表
            unified_messages = [
                UnifiedMessage(
                    role=MessageRole(msg["role"]),  # 转换字符串为枚举
                    content=msg["content"]
                )
                for msg in message_dicts
            ]

            # 构建 UnifiedConversation
            conversation = UnifiedConversation(
                messages=unified_messages,
                system_prompt=system_prompt
            )

            # 调用LLM
            response = await self.llm_adapter.chat(conversation)

            content = response.content if hasattr(response, 'content') else str(response)

            return {
                "success": True,
                "content": content,
                "metadata": {
                    "intent": "general_chat",
                    "model": self.model_key
                }
            }

        except Exception as e:
            logger.error(f"Error in general chat: {e}")
            import traceback
            logger.error(f"Full traceback:\n{traceback.format_exc()}")
            return {
                "success": False,
                "content": f"调用LLM时出错: {str(e)}"
            }

    def _build_system_prompt(self, context: Dict[str, Any]) -> str:
        """构建系统提示"""
        # 获取可用的MCP服务器信息
        servers = self.mcp_manager.list_servers()

        server_info = "\n".join([
            f"- {s['name']} ({s['type']}): {s['tool_count']}个工具"
            for s in servers
        ])

        return f"""你是一个数据分析助手,可以帮助用户:
1. 连接和查询ClickHouse、MySQL数据库
2. 理解数据表结构和内容
3. 设计数据宽表和ETL脚本
4. 生成数据分析报表
5. 处理文件和Lark文档

当前可用的数据源:
{server_info}

请友好、专业地回答用户问题,在需要时主动引导用户提供必要信息。"""

    def _build_conversation_history(
        self,
        current_message: str,
        context: Dict[str, Any]
    ) -> List[Dict[str, str]]:
        """构建对话历史"""
        messages = []

        # 从context中提取历史消息
        history = context.get("history", [])

        # Phase 1.3: 不再硬编码限制历史消息数量
        # Context已经由HybridContextManager压缩过,直接使用
        for msg in history:
            # 获取消息内容并去除首尾空白
            content = msg.get("content", "").strip()

            # 只添加非空消息
            if content:
                messages.append({
                    "role": msg.get("role", "user"),
                    "content": content
                })
            else:
                logger.warning(f"Skipping empty message in conversation history: role={msg.get('role')}")

        # 添加当前消息
        if current_message and current_message.strip():
            messages.append({
                "role": "user",
                "content": current_message.strip()
            })
        else:
            logger.warning("Current message is empty, not adding to conversation")

        return messages


async def create_master_agent(
    mcp_manager: MCPServerManager,
    model_key: str,
    llm_config: Dict[str, Any]
) -> MasterAgent:
    """
    创建Master Agent实例

    Args:
        mcp_manager: MCP管理器
        model_key: 模型标识
        llm_config: LLM配置

    Returns:
        MasterAgent实例
    """
    return MasterAgent(mcp_manager, model_key, llm_config)
