"""
统一对话格式模块

提供统一的对话消息格式,支持不同LLM之间的消息转换
"""
from typing import List, Dict, Any, Optional
try:
    from typing import Literal
except ImportError:
    from typing_extensions import Literal
from pydantic import BaseModel, Field
from enum import Enum


class MessageRole(str, Enum):
    """消息角色枚举"""
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class ToolCallStatus(str, Enum):
    """工具调用状态"""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class ToolCall(BaseModel):
    """工具调用"""
    id: str = Field(..., description="工具调用ID")
    name: str = Field(..., description="工具名称")
    arguments: Dict[str, Any] = Field(default_factory=dict, description="工具参数")
    status: ToolCallStatus = Field(default=ToolCallStatus.PENDING, description="调用状态")

    class Config:
        use_enum_values = True


class ToolResult(BaseModel):
    """工具执行结果"""
    tool_call_id: str = Field(..., description="对应的工具调用ID")
    output: Any = Field(..., description="执行结果")
    error: Optional[str] = Field(None, description="错误信息")
    execution_time: Optional[float] = Field(None, description="执行时长(秒)")

    def is_success(self) -> bool:
        """是否执行成功"""
        return self.error is None


class Artifact(BaseModel):
    """消息附件/产物"""
    type: Literal["sql", "python", "chart_config", "etl_script", "file", "table_data"] = Field(
        ..., description="产物类型"
    )
    content: Any = Field(..., description="产物内容")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="元数据")

    class Config:
        use_enum_values = True


class UnifiedMessage(BaseModel):
    """统一消息格式"""
    role: MessageRole = Field(..., description="消息角色")
    content: str = Field(..., description="消息内容")

    # 可选字段
    name: Optional[str] = Field(None, description="发送者名称(用于tool角色)")
    tool_calls: Optional[List[ToolCall]] = Field(None, description="工具调用列表")
    tool_results: Optional[List[ToolResult]] = Field(None, description="工具执行结果")
    artifacts: Optional[List[Artifact]] = Field(None, description="附件/产物列表")

    # 元数据
    model: Optional[str] = Field(None, description="使用的模型")
    timestamp: Optional[str] = Field(None, description="时间戳")
    token_count: Optional[int] = Field(None, description="Token数量")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="其他元数据")

    class Config:
        use_enum_values = True

    def has_tool_calls(self) -> bool:
        """是否包含工具调用"""
        return self.tool_calls is not None and len(self.tool_calls) > 0

    def has_tool_results(self) -> bool:
        """是否包含工具执行结果"""
        return self.tool_results is not None and len(self.tool_results) > 0

    def has_artifacts(self) -> bool:
        """是否包含产物"""
        return self.artifacts is not None and len(self.artifacts) > 0

    def get_artifact_by_type(self, artifact_type: str) -> Optional[Artifact]:
        """根据类型获取产物"""
        if not self.artifacts:
            return None
        for artifact in self.artifacts:
            if artifact.type == artifact_type:
                return artifact
        return None

    def add_artifact(self, artifact: Artifact):
        """添加产物"""
        if self.artifacts is None:
            self.artifacts = []
        self.artifacts.append(artifact)

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return self.model_dump(exclude_none=True)


class UnifiedConversation(BaseModel):
    """统一对话格式"""
    messages: List[UnifiedMessage] = Field(default_factory=list, description="消息列表")
    system_prompt: Optional[str] = Field(None, description="系统提示词")

    # 对话元数据
    conversation_id: Optional[str] = Field(None, description="对话ID")
    title: Optional[str] = Field(None, description="对话标题")
    model: Optional[str] = Field(None, description="当前使用的模型")

    # 统计信息
    total_tokens: int = Field(default=0, description="总Token数")
    message_count: int = Field(default=0, description="消息数量")

    # 上下文配置
    max_context_length: int = Field(default=20, description="最大上下文长度")
    context_strategy: Literal["full", "sliding_window", "compressed"] = Field(
        default="sliding_window",
        description="上下文策略"
    )

    metadata: Dict[str, Any] = Field(default_factory=dict, description="其他元数据")

    class Config:
        use_enum_values = True

    def add_message(self, message: UnifiedMessage):
        """添加消息"""
        self.messages.append(message)
        self.message_count = len(self.messages)
        if message.token_count:
            self.total_tokens += message.token_count

    def add_user_message(self, content: str, **kwargs) -> UnifiedMessage:
        """添加用户消息"""
        message = UnifiedMessage(
            role=MessageRole.USER,
            content=content,
            **kwargs
        )
        self.add_message(message)
        return message

    def add_assistant_message(self, content: str, **kwargs) -> UnifiedMessage:
        """添加助手消息"""
        message = UnifiedMessage(
            role=MessageRole.ASSISTANT,
            content=content,
            **kwargs
        )
        self.add_message(message)
        return message

    def add_system_message(self, content: str, **kwargs) -> UnifiedMessage:
        """添加系统消息"""
        message = UnifiedMessage(
            role=MessageRole.SYSTEM,
            content=content,
            **kwargs
        )
        self.add_message(message)
        return message

    def get_messages_for_model(
        self,
        include_system: bool = True,
        max_messages: Optional[int] = None
    ) -> List[UnifiedMessage]:
        """
        获取用于模型调用的消息列表

        Args:
            include_system: 是否包含系统消息
            max_messages: 最大消息数量(用于滑动窗口)

        Returns:
            消息列表
        """
        messages = self.messages.copy()

        # 应用滑动窗口
        if max_messages and len(messages) > max_messages:
            messages = messages[-max_messages:]

        # 添加系统消息
        if include_system and self.system_prompt:
            system_msg = UnifiedMessage(
                role=MessageRole.SYSTEM,
                content=self.system_prompt
            )
            messages.insert(0, system_msg)

        return messages

    def get_last_message(self) -> Optional[UnifiedMessage]:
        """获取最后一条消息"""
        return self.messages[-1] if self.messages else None

    def get_last_user_message(self) -> Optional[UnifiedMessage]:
        """获取最后一条用户消息"""
        for message in reversed(self.messages):
            if message.role == MessageRole.USER:
                return message
        return None

    def get_last_assistant_message(self) -> Optional[UnifiedMessage]:
        """获取最后一条助手消息"""
        for message in reversed(self.messages):
            if message.role == MessageRole.ASSISTANT:
                return message
        return None

    def clear_messages(self):
        """清空消息"""
        self.messages = []
        self.message_count = 0
        self.total_tokens = 0

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return self.model_dump(exclude_none=True)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "UnifiedConversation":
        """从字典创建"""
        return cls(**data)


class ConversationSummary(BaseModel):
    """对话摘要"""
    summary: str = Field(..., description="对话摘要")
    key_points: List[str] = Field(default_factory=list, description="关键要点")
    entities: Dict[str, List[str]] = Field(default_factory=dict, description="实体提取")
    topics: List[str] = Field(default_factory=list, description="主题标签")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="元数据")

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return self.model_dump()


# 辅助函数

def create_sql_artifact(sql: str, database: str, table: Optional[str] = None) -> Artifact:
    """创建SQL产物"""
    return Artifact(
        type="sql",
        content=sql,
        metadata={
            "database": database,
            "table": table,
            "query_type": _detect_query_type(sql)
        }
    )


def create_chart_artifact(
    chart_type: str,
    config: Dict[str, Any],
    data: Optional[Any] = None
) -> Artifact:
    """创建图表配置产物"""
    return Artifact(
        type="chart_config",
        content=config,
        metadata={
            "chart_type": chart_type,
            "has_data": data is not None
        }
    )


def create_etl_artifact(
    script: str,
    language: str = "python",
    description: Optional[str] = None
) -> Artifact:
    """创建ETL脚本产物"""
    return Artifact(
        type="etl_script",
        content=script,
        metadata={
            "language": language,
            "description": description
        }
    )


def create_table_data_artifact(
    data: List[Dict[str, Any]],
    columns: List[str],
    row_count: int
) -> Artifact:
    """创建表格数据产物"""
    return Artifact(
        type="table_data",
        content=data,
        metadata={
            "columns": columns,
            "row_count": row_count
        }
    )


def _detect_query_type(sql: str) -> str:
    """检测SQL查询类型"""
    sql_upper = sql.strip().upper()
    if sql_upper.startswith("SELECT"):
        return "SELECT"
    elif sql_upper.startswith("INSERT"):
        return "INSERT"
    elif sql_upper.startswith("UPDATE"):
        return "UPDATE"
    elif sql_upper.startswith("DELETE"):
        return "DELETE"
    elif sql_upper.startswith("CREATE"):
        return "CREATE"
    elif sql_upper.startswith("ALTER"):
        return "ALTER"
    elif sql_upper.startswith("DROP"):
        return "DROP"
    else:
        return "OTHER"


# 示例用法
if __name__ == "__main__":
    # 创建对话
    conversation = UnifiedConversation(
        conversation_id="conv_123",
        title="数据分析对话",
        model="claude-3-5-sonnet-20240620",
        system_prompt="你是一个专业的数据分析助手。"
    )

    # 添加用户消息
    conversation.add_user_message(
        "请帮我查询ClickHouse中user_events表的数据"
    )

    # 添加助手消息(带工具调用)
    assistant_msg = conversation.add_assistant_message(
        "好的,我将查询user_events表的数据。",
        tool_calls=[
            ToolCall(
                id="call_1",
                name="clickhouse_query",
                arguments={
                    "database": "default",
                    "query": "SELECT * FROM user_events LIMIT 10"
                }
            )
        ]
    )

    # 添加SQL产物
    assistant_msg.add_artifact(
        create_sql_artifact(
            sql="SELECT * FROM user_events LIMIT 10",
            database="default",
            table="user_events"
        )
    )

    # 打印对话
    print(conversation.to_dict())
