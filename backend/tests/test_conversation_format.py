"""
统一对话格式测试

测试统一对话格式的创建、转换和基本功能
"""
import pytest
from backend.core.conversation_format import (
    UnifiedConversation,
    UnifiedMessage,
    MessageRole,
    ToolCall,
    ToolResult,
    Artifact,
    create_sql_artifact,
    create_chart_artifact,
    create_etl_artifact,
    create_table_data_artifact
)


class TestUnifiedMessage:
    """测试UnifiedMessage"""

    def test_create_user_message(self):
        """测试创建用户消息"""
        message = UnifiedMessage(
            role=MessageRole.USER,
            content="你好",
            metadata={"source": "web"}
        )

        assert message.role == MessageRole.USER
        assert message.content == "你好"
        assert message.metadata["source"] == "web"
        assert not message.has_tool_calls()
        assert not message.has_tool_results()
        assert not message.has_artifacts()

    def test_create_assistant_message_with_tools(self):
        """测试创建带工具调用的助手消息"""
        tool_call = ToolCall(
            id="call_1",
            name="clickhouse_query",
            arguments={"query": "SELECT * FROM table"}
        )

        message = UnifiedMessage(
            role=MessageRole.ASSISTANT,
            content="我将查询数据",
            tool_calls=[tool_call]
        )

        assert message.has_tool_calls()
        assert len(message.tool_calls) == 1
        assert message.tool_calls[0].name == "clickhouse_query"

    def test_add_artifact(self):
        """测试添加产物"""
        message = UnifiedMessage(
            role=MessageRole.ASSISTANT,
            content="这是查询结果"
        )

        artifact = Artifact(
            type="sql",
            content="SELECT * FROM table",
            metadata={"table": "users"}
        )

        message.add_artifact(artifact)

        assert message.has_artifacts()
        assert len(message.artifacts) == 1

    def test_get_artifact_by_type(self):
        """测试根据类型获取产物"""
        message = UnifiedMessage(
            role=MessageRole.ASSISTANT,
            content="包含多种产物"
        )

        sql_artifact = Artifact(
            type="sql",
            content="SELECT * FROM table"
        )

        chart_artifact = Artifact(
            type="chart_config",
            content={"type": "bar"}
        )

        message.add_artifact(sql_artifact)
        message.add_artifact(chart_artifact)

        sql = message.get_artifact_by_type("sql")
        assert sql is not None
        assert sql.type == "sql"

        chart = message.get_artifact_by_type("chart_config")
        assert chart is not None
        assert chart.type == "chart_config"


class TestUnifiedConversation:
    """测试UnifiedConversation"""

    def test_create_conversation(self):
        """测试创建对话"""
        conversation = UnifiedConversation(
            conversation_id="conv_123",
            title="测试对话",
            model="claude-3-5-sonnet",
            system_prompt="你是一个AI助手"
        )

        assert conversation.conversation_id == "conv_123"
        assert conversation.title == "测试对话"
        assert conversation.model == "claude-3-5-sonnet"
        assert conversation.system_prompt == "你是一个AI助手"
        assert conversation.message_count == 0
        assert len(conversation.messages) == 0

    def test_add_messages(self):
        """测试添加消息"""
        conversation = UnifiedConversation()

        conversation.add_user_message("你好")
        conversation.add_assistant_message("你好,我是AI助手")
        conversation.add_user_message("请查询数据")

        assert conversation.message_count == 3
        assert len(conversation.messages) == 3

    def test_get_last_message(self):
        """测试获取最后一条消息"""
        conversation = UnifiedConversation()
        conversation.add_user_message("第一条")
        conversation.add_user_message("第二条")

        last = conversation.get_last_message()
        assert last is not None
        assert last.content == "第二条"

    def test_get_last_user_message(self):
        """测试获取最后一条用户消息"""
        conversation = UnifiedConversation()
        conversation.add_user_message("用户1")
        conversation.add_assistant_message("助手1")
        conversation.add_user_message("用户2")

        last_user = conversation.get_last_user_message()
        assert last_user is not None
        assert last_user.content == "用户2"

    def test_get_messages_for_model(self):
        """测试获取模型消息"""
        conversation = UnifiedConversation(
            system_prompt="系统提示"
        )
        conversation.add_user_message("用户消息")

        # 不包含系统消息
        messages = conversation.get_messages_for_model(include_system=False)
        assert len(messages) == 1
        assert messages[0].role == MessageRole.USER

        # 包含系统消息
        messages = conversation.get_messages_for_model(include_system=True)
        assert len(messages) == 2
        assert messages[0].role == MessageRole.SYSTEM
        assert messages[1].role == MessageRole.USER

    def test_sliding_window(self):
        """测试滑动窗口"""
        conversation = UnifiedConversation()

        # 添加10条消息
        for i in range(10):
            conversation.add_user_message(f"消息 {i+1}")

        # 设置最大消息数为5
        messages = conversation.get_messages_for_model(max_messages=5)
        assert len(messages) == 5
        assert messages[0].content == "消息 6"

    def test_clear_messages(self):
        """测试清空消息"""
        conversation = UnifiedConversation()
        conversation.add_user_message("消息1")
        conversation.add_user_message("消息2")

        conversation.clear_messages()

        assert conversation.message_count == 0
        assert len(conversation.messages) == 0

    def test_to_dict_and_from_dict(self):
        """测试序列化与反序列化"""
        conversation = UnifiedConversation(
            conversation_id="conv_123",
            title="测试对话"
        )
        conversation.add_user_message("测试消息")

        # 转换为字典
        data = conversation.to_dict()

        # 从字典创建
        restored = UnifiedConversation.from_dict(data)

        assert restored.conversation_id == "conv_123"
        assert restored.title == "测试对话"
        assert len(restored.messages) == 1


class TestArtifacts:
    """测试Artifact辅助函数"""

    def test_create_sql_artifact(self):
        """测试创建SQL产物"""
        artifact = create_sql_artifact(
            sql="SELECT * FROM users",
            database="default",
            table="users"
        )

        assert artifact.type == "sql"
        assert artifact.content == "SELECT * FROM users"
        assert artifact.metadata["database"] == "default"
        assert artifact.metadata["table"] == "users"
        assert artifact.metadata["query_type"] == "SELECT"

    def test_create_chart_artifact(self):
        """测试创建图表产物"""
        config = {
            "type": "bar",
            "x": "date",
            "y": "value"
        }

        artifact = create_chart_artifact(
            chart_type="bar",
            config=config
        )

        assert artifact.type == "chart_config"
        assert artifact.content == config
        assert artifact.metadata["chart_type"] == "bar"

    def test_create_etl_artifact(self):
        """测试创建ETL产物"""
        script = """
import pandas as pd
df = pd.read_csv('data.csv')
df.to_sql('output', connection)
        """.strip()

        artifact = create_etl_artifact(
            script=script,
            language="python",
            description="数据转换脚本"
        )

        assert artifact.type == "etl_script"
        assert artifact.content == script
        assert artifact.metadata["language"] == "python"
        assert artifact.metadata["description"] == "数据转换脚本"

    def test_create_table_data_artifact(self):
        """测试创建表格数据产物"""
        data = [
            {"id": 1, "name": "Alice"},
            {"id": 2, "name": "Bob"}
        ]

        artifact = create_table_data_artifact(
            data=data,
            columns=["id", "name"],
            row_count=2
        )

        assert artifact.type == "table_data"
        assert artifact.content == data
        assert artifact.metadata["columns"] == ["id", "name"]
        assert artifact.metadata["row_count"] == 2


class TestToolCall:
    """测试ToolCall"""

    def test_create_tool_call(self):
        """测试创建工具调用"""
        tool_call = ToolCall(
            id="call_1",
            name="clickhouse_query",
            arguments={"query": "SELECT * FROM table"},
            status="pending"
        )

        assert tool_call.id == "call_1"
        assert tool_call.name == "clickhouse_query"
        assert tool_call.status.value == "pending"


class TestToolResult:
    """测试ToolResult"""

    def test_create_tool_result_success(self):
        """测试创建成功的工具结果"""
        result = ToolResult(
            tool_call_id="call_1",
            output=[{"id": 1, "name": "Alice"}],
            execution_time=0.5
        )

        assert result.tool_call_id == "call_1"
        assert result.is_success() is True
        assert result.error is None

    def test_create_tool_result_failure(self):
        """测试创建失败的工具结果"""
        result = ToolResult(
            tool_call_id="call_1",
            output=None,
            error="Query timeout",
            execution_time=30.0
        )

        assert result.tool_call_id == "call_1"
        assert result.is_success() is False
        assert result.error == "Query timeout"
