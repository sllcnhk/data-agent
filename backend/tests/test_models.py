"""
模型测试

测试数据库模型的创建、转换和基本功能
"""
import pytest
from uuid import uuid4
from backend.models.conversation import Conversation, Message, ContextSnapshot
from backend.models.task import Task, TaskType, TaskStatus
from backend.models.report import Report, Chart, ReportType, ChartType


class TestConversationModel:
    """测试Conversation模型"""

    def test_create_conversation(self, sample_conversation):
        """测试创建对话"""
        assert sample_conversation.title == "测试对话"
        assert sample_conversation.current_model == "claude-3-5-sonnet"
        assert sample_conversation.status == "active"
        assert sample_conversation.message_count == 0

    def test_conversation_to_dict(self, sample_conversation):
        """测试对话转换为字典"""
        # 添加UUID
        sample_conversation.id = uuid4()

        result = sample_conversation.to_dict()

        assert "id" in result
        assert result["title"] == "测试对话"
        assert result["current_model"] == "claude-3-5-sonnet"
        assert result["status"] == "active"


class TestMessageModel:
    """测试Message模型"""

    def test_create_message(self):
        """测试创建消息"""
        message = Message(
            role="user",
            content="测试消息",
            artifacts=[{"type": "sql", "content": "SELECT * FROM table"}],
            metadata={"test": "value"}
        )

        assert message.role == "user"
        assert message.content == "测试消息"
        assert message.artifacts is not None
        assert len(message.artifacts) == 1

    def test_message_to_dict(self):
        """测试消息转换为字典"""
        message = Message(
            role="assistant",
            content="测试回复"
        )
        message.id = uuid4()

        result = message.to_dict()

        assert result["role"] == "assistant"
        assert result["content"] == "测试回复"


class TestTaskModel:
    """测试Task模型"""

    def test_create_task(self, sample_task):
        """测试创建任务"""
        assert sample_task.name == "测试任务"
        assert sample_task.task_type == TaskType.DATA_EXPORT
        assert sample_task.status == TaskStatus.PENDING
        assert sample_task.priority == 0

    def test_task_start(self, sample_task):
        """测试任务开始"""
        assert sample_task.status == TaskStatus.PENDING

        sample_task.start()

        assert sample_task.status == TaskStatus.RUNNING
        assert sample_task.started_at is not None

    def test_task_complete(self, sample_task):
        """测试任务完成"""
        sample_task.start()

        result = {"status": "success", "data": [1, 2, 3]}
        sample_task.complete(result=result)

        assert sample_task.status == TaskStatus.COMPLETED
        assert sample_task.progress == 100
        assert sample_task.result == result
        assert sample_task.completed_at is not None

    def test_task_fail(self, sample_task):
        """测试任务失败"""
        sample_task.start()

        error_msg = "任务执行失败"
        sample_task.fail(error_msg, error_trace="Error details")

        assert sample_task.status == TaskStatus.FAILED
        assert sample_task.error_message == error_msg
        assert sample_task.error_trace == "Error details"
        assert sample_task.completed_at is not None

    def test_task_update_progress(self, sample_task):
        """测试任务进度更新"""
        sample_task.update_progress(50, "正在处理数据")

        assert sample_task.progress == 50
        assert sample_task.current_step == "正在处理数据"

    def test_task_to_dict(self, sample_task):
        """测试任务转换为字典"""
        sample_task.id = uuid4()

        result = sample_task.to_dict()

        assert result["name"] == "测试任务"
        assert result["task_type"] == "data_export"
        assert result["status"] == "pending"


class TestTaskHistoryModel:
    """测试TaskHistory模型"""

    def test_create_history(self):
        """测试创建历史记录"""
        task_id = uuid4()
        history = TaskHistory(
            task_id=task_id,
            event_type="created",
            message="任务已创建",
            new_status="pending"
        )

        assert history.task_id == task_id
        assert history.event_type == "created"
        assert history.message == "任务已创建"
        assert history.new_status == "pending"

    def test_history_to_dict(self):
        """测试历史记录转换为字典"""
        history = TaskHistory(
            task_id=uuid4(),
            event_type="started",
            message="任务已开始"
        )
        history.id = uuid4()

        result = history.to_dict()

        assert result["event_type"] == "started"
        assert result["message"] == "任务已开始"


class TestReportModel:
    """测试Report模型"""

    def test_create_report(self, sample_report):
        """测试创建报表"""
        assert sample_report.name == "测试报表"
        assert sample_report.report_type == ReportType.DASHBOARD
        assert sample_report.theme == "light"
        assert sample_report.share_scope == "private"
        assert sample_report.view_count == 0

    def test_report_increment_view_count(self, sample_report):
        """测试报表浏览次数增加"""
        assert sample_report.view_count == 0

        sample_report.increment_view_count()

        assert sample_report.view_count == 1
        assert sample_report.last_viewed_at is not None

    def test_report_to_dict(self, sample_report):
        """测试报表转换为字典"""
        sample_report.id = uuid4()

        result = sample_report.to_dict()

        assert result["name"] == "测试报表"
        assert result["report_type"] == "dashboard"
        assert result["theme"] == "light"
        assert result["view_count"] == 0


class TestChartModel:
    """测试Chart模型"""

    def test_create_chart(self):
        """测试创建图表"""
        chart = Chart(
            name="测试图表",
            chart_type=ChartType.LINE,
            query="SELECT * FROM data",
            chart_config={"type": "line"}
        )

        assert chart.name == "测试图表"
        assert chart.chart_type == ChartType.LINE
        assert chart.query == "SELECT * FROM data"
        assert chart.cached_data is None

    def test_chart_update_cache(self):
        """测试图表缓存更新"""
        chart = Chart(
            name="测试图表",
            chart_type=ChartType.BAR
        )

        data = {"x": [1, 2, 3], "y": [4, 5, 6]}
        chart.update_cache(data, ttl=300)

        assert chart.cached_data == data
        assert chart.cache_expires_at is not None

    def test_chart_is_cache_valid(self):
        """测试图表缓存有效性检查"""
        chart = Chart(
            name="测试图表",
            chart_type=ChartType.PIE
        )

        # 没有缓存
        assert not chart.is_cache_valid()

        # 有缓存
        from datetime import datetime, timedelta
        future_time = datetime.utcnow() + timedelta(seconds=300)
        chart.cache_expires_at = future_time
        chart.cached_data = {}

        assert chart.is_cache_valid()

    def test_chart_to_dict(self):
        """测试图表转换为字典"""
        chart = Chart(
            name="测试图表",
            chart_type=ChartType.SCATTER
        )
        chart.id = uuid4()

        result = chart.to_dict()

        assert result["name"] == "测试图表"
        assert result["chart_type"] == "scatter"
