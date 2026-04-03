"""
Agent系统单元测试

测试Agent的各种功能
"""
import pytest
import asyncio
import json
from unittest.mock import Mock, patch, MagicMock
from datetime import datetime

from backend.agents import (
    BaseAgent,
    AgentType,
    Task,
    TaskPriority,
    TaskStatus,
    DataAnalystAgent,
    SQLExpertAgent,
    ChartBuilderAgent,
    ETLEngineerAgent,
    GeneralistAgent,
    AgentManager,
    AgentRouter,
    get_agent_manager,
    shutdown_agent_manager
)
from backend.skills.base import SkillInput, SkillOutput, create_skill_output


class TestTask:
    """测试任务类"""

    def test_task_creation(self):
        """测试任务创建"""
        task = Task(
            task_id="test-001",
            agent_type=AgentType.DATA_ANALYST,
            priority=TaskPriority.HIGH,
            input_data={"query": "test"}
        )

        assert task.task_id == "test-001"
        assert task.agent_type == AgentType.DATA_ANALYST
        assert task.priority == TaskPriority.HIGH
        assert task.status == TaskStatus.PENDING
        assert task.retry_count == 0

    def test_task_to_dict(self):
        """测试任务转换为字典"""
        task = Task(
            task_id="test-001",
            agent_type=AgentType.DATA_ANALYST,
            priority=TaskPriority.NORMAL,
            input_data={"query": "test"}
        )

        task_dict = task.to_dict()

        assert task_dict["task_id"] == "test-001"
        assert task_dict["agent_type"] == "data_analyst"
        assert task_dict["priority"] == 2
        assert task_dict["status"] == "pending"


class TestDataAnalystAgent:
    """测试数据分析师Agent"""

    @pytest.mark.asyncio
    async def test_agent_initialization(self):
        """测试Agent初始化"""
        agent = DataAnalystAgent("test-analyst-001")
        result = await agent.initialize()

        assert result is True
        assert agent.agent_id == "test-analyst-001"
        assert agent.agent_type == AgentType.DATA_ANALYST

    @pytest.mark.asyncio
    async def test_execute_summary_analysis(self):
        """测试执行摘要分析"""
        agent = DataAnalystAgent("test-analyst-002")
        await agent.initialize()

        task = Task(
            task_id="test-task-001",
            agent_type=AgentType.DATA_ANALYST,
            priority=TaskPriority.NORMAL,
            input_data={
                "action": "analyze",
                "data": [
                    {"name": "Alice", "age": 25},
                    {"name": "Bob", "age": 30}
                ],
                "analysis_type": "summary"
            }
        )

        with patch('backend.skills.base.SkillRegistry') as mock_registry:
            mock_skill = Mock()
            mock_skill.execute.return_value = create_skill_output(
                success=True,
                data={"result": "summary"}
            )
            mock_registry.return_value.get_skill.return_value = mock_skill

            result = await agent.execute_task(task)

            assert result.success is True

    @pytest.mark.asyncio
    async def test_get_capabilities(self):
        """测试获取能力"""
        agent = DataAnalystAgent("test-analyst-003")
        capabilities = agent.get_capabilities()

        assert len(capabilities) > 0
        assert "数据摘要分析" in capabilities
        assert "统计分析" in capabilities

    @pytest.mark.asyncio
    async def test_agent_info(self):
        """测试Agent信息"""
        agent = DataAnalystAgent("test-analyst-004")
        info = agent.get_info()

        assert info["agent_id"] == "test-analyst-004"
        assert info["agent_type"] == "data_analyst"
        assert "capabilities" in info


class TestSQLExpertAgent:
    """测试SQL专家Agent"""

    @pytest.mark.asyncio
    async def test_agent_initialization(self):
        """测试Agent初始化"""
        agent = SQLExpertAgent("test-sql-001")
        result = await agent.initialize()

        assert result is True
        assert agent.agent_type == AgentType.SQL_EXPERT

    @pytest.mark.asyncio
    async def test_generate_sql(self):
        """测试生成SQL"""
        agent = SQLExpertAgent("test-sql-002")
        await agent.initialize()

        task = Task(
            task_id="test-task-002",
            agent_type=AgentType.SQL_EXPERT,
            priority=TaskPriority.NORMAL,
            input_data={
                "action": "generate",
                "description": "查询所有用户",
                "table_schema": {
                    "table_name": "users",
                    "columns": [
                        {"name": "id", "type": "int"},
                        {"name": "name", "type": "string"}
                    ]
                }
            }
        )

        with patch('backend.skills.base.SkillRegistry') as mock_registry:
            mock_skill = Mock()
            mock_skill.execute.return_value = create_skill_output(
                success=True,
                data={"sql": "SELECT * FROM users"}
            )
            mock_registry.return_value.get_skill.return_value = mock_skill

            result = await agent.execute_task(task)

            assert result.success is True

    @pytest.mark.asyncio
    async def test_get_capabilities(self):
        """测试获取能力"""
        agent = SQLExpertAgent("test-sql-003")
        capabilities = agent.get_capabilities()

        assert "自然语言转SQL" in capabilities
        assert "SQL查询生成" in capabilities


class TestChartBuilderAgent:
    """测试图表构建Agent"""

    @pytest.mark.asyncio
    async def test_agent_initialization(self):
        """测试Agent初始化"""
        agent = ChartBuilderAgent("test-chart-001")
        result = await agent.initialize()

        assert result is True
        assert agent.agent_type == AgentType.CHART_BUILDER

    @pytest.mark.asyncio
    async def test_build_chart(self):
        """测试构建图表"""
        agent = ChartBuilderAgent("test-chart-002")
        await agent.initialize()

        task = Task(
            task_id="test-task-003",
            agent_type=AgentType.CHART_BUILDER,
            priority=TaskPriority.NORMAL,
            input_data={
                "action": "build",
                "data": [
                    {"category": "A", "value": 10},
                    {"category": "B", "value": 20}
                ],
                "chart_type": "bar",
                "library": "echarts"
            }
        )

        with patch('backend.skills.base.SkillRegistry') as mock_registry:
            mock_skill = Mock()
            mock_skill.execute.return_value = create_skill_output(
                success=True,
                data={"chart_type": "bar"}
            )
            mock_registry.return_value.get_skill.return_value = mock_skill

            result = await agent.execute_task(task)

            assert result.success is True

    @pytest.mark.asyncio
    async def test_get_capabilities(self):
        """测试获取能力"""
        agent = ChartBuilderAgent("test-chart-003")
        capabilities = agent.get_capabilities()

        assert "柱状图生成" in capabilities
        assert "图表类型推荐" in capabilities


class TestETLEngineerAgent:
    """测试ETL工程师Agent"""

    @pytest.mark.asyncio
    async def test_agent_initialization(self):
        """测试Agent初始化"""
        agent = ETLEngineerAgent("test-etl-001")
        result = await agent.initialize()

        assert result is True
        assert agent.agent_type == AgentType.ETL_ENGINEER

    @pytest.mark.asyncio
    async def test_design_etl(self):
        """测试设计ETL"""
        agent = ETLEngineerAgent("test-etl-002")
        await agent.initialize()

        task = Task(
            task_id="test-task-004",
            agent_type=AgentType.ETL_ENGINEER,
            priority=TaskPriority.NORMAL,
            input_data={
                "action": "design",
                "source_type": "database",
                "source_config": {
                    "database_type": "clickhouse",
                    "query": "SELECT * FROM events"
                },
                "target_config": {
                    "type": "database",
                    "table": "events_processed"
                }
            }
        )

        with patch('backend.skills.base.SkillRegistry') as mock_registry:
            mock_skill = Mock()
            mock_skill.execute.return_value = create_skill_output(
                success=True,
                data={"pipeline": {}}
            )
            mock_registry.return_value.get_skill.return_value = mock_skill

            result = await agent.execute_task(task)

            assert result.success is True

    @pytest.mark.asyncio
    async def test_get_capabilities(self):
        """测试获取能力"""
        agent = ETLEngineerAgent("test-etl-003")
        capabilities = agent.get_capabilities()

        assert "ETL管道设计" in capabilities
        assert "数据验证" in capabilities


class TestGeneralistAgent:
    """测试通用Agent"""

    @pytest.mark.asyncio
    async def test_agent_initialization(self):
        """测试Agent初始化"""
        agent = GeneralistAgent("test-general-001")
        result = await agent.initialize()

        assert result is True
        assert agent.agent_type == AgentType.GENERALIST
        assert len(agent.sub_agents) > 0

    @pytest.mark.asyncio
    async def test_get_capabilities(self):
        """测试获取能力"""
        agent = GeneralistAgent("test-general-002")
        capabilities = agent.get_capabilities()

        assert len(capabilities) > 0
        assert "智能任务路由" in capabilities


class TestAgentManager:
    """测试Agent管理器"""

    @pytest.mark.asyncio
    async def test_manager_initialization(self):
        """测试管理器初始化"""
        manager = AgentManager()
        await manager.initialize()

        assert manager.running is True
        assert len(manager._worker_tasks) == 5

        await manager.shutdown()

    @pytest.mark.asyncio
    async def test_register_agent(self):
        """测试注册Agent"""
        manager = AgentManager()
        await manager.initialize()

        agent = DataAnalystAgent("test-agent-001")
        result = await manager.register_agent(agent)

        assert result is True
        assert "test-agent-001" in manager.agents

        await manager.shutdown()

    @pytest.mark.asyncio
    async def test_submit_task(self):
        """测试提交任务"""
        manager = AgentManager()
        await manager.initialize()

        agent = DataAnalystAgent("test-agent-002")
        await manager.register_agent(agent)

        # 由于是异步任务，我们只检查是否能成功提交
        task_id = await manager.submit_task(
            agent_type=AgentType.DATA_ANALYST,
            priority=TaskPriority.NORMAL,
            input_data={"query": "test"}
        )

        assert task_id is not None
        assert len(task_id) > 0

        await manager.shutdown()

    @pytest.mark.asyncio
    async def test_get_agent_metrics(self):
        """测试获取Agent指标"""
        manager = AgentManager()
        await manager.initialize()

        agent = DataAnalystAgent("test-agent-003")
        await manager.register_agent(agent)

        metrics = await manager.get_agent_metrics("test-agent-003")

        assert metrics is not None
        assert metrics["agent_id"] == "test-agent-003"

        await manager.shutdown()

    @pytest.mark.asyncio
    async def test_health_check(self):
        """测试健康检查"""
        manager = AgentManager()
        await manager.initialize()

        health = await manager.health_check()

        assert "status" in health
        assert health["status"] == "healthy"
        assert "total_agents" in health

        await manager.shutdown()


class TestAgentRouter:
    """测试Agent路由器"""

    def test_router_initialization(self):
        """测试路由器初始化"""
        manager = AgentManager()
        router = AgentRouter(manager)

        assert router is not None
        assert len(router.routing_rules) > 0

    @pytest.mark.asyncio
    async def test_route_sql_query(self):
        """测试路由SQL查询"""
        manager = AgentManager()
        router = AgentRouter(manager)

        agent_type, priority, params = await router.route_task("生成一个SQL查询")

        assert agent_type == AgentType.SQL_EXPERT
        assert "query" in params

    @pytest.mark.asyncio
    async def test_route_chart_query(self):
        """测试路由图表查询"""
        manager = AgentManager()
        router = AgentRouter(manager)

        agent_type, priority, params = await router.route_task("创建一个柱状图")

        assert agent_type == AgentType.CHART_BUILDER

    @pytest.mark.asyncio
    async def test_route_analysis_query(self):
        """测试路由分析查询"""
        manager = AgentManager()
        router = AgentRouter(manager)

        agent_type, priority, params = await router.route_task("分析这些数据的趋势")

        assert agent_type == AgentType.DATA_ANALYST

    @pytest.mark.asyncio
    async def test_route_etl_query(self):
        """测试路由ETL查询"""
        manager = AgentManager()
        router = AgentRouter(manager)

        agent_type, priority, params = await router.route_task("设计一个ETL管道")

        assert agent_type == AgentType.ETL_ENGINEER

    @pytest.mark.asyncio
    async def test_route_general_query(self):
        """测试路由通用查询"""
        manager = AgentManager()
        router = AgentRouter(manager)

        agent_type, priority, params = await router.route_task("帮助我查询数据")

        assert agent_type in [AgentType.GENERALIST, AgentType.SQL_EXPERT]

    @pytest.mark.asyncio
    async def test_get_routing_suggestions(self):
        """测试获取路由建议"""
        manager = AgentManager()
        router = AgentRouter(manager)

        suggestions = await router.get_routing_suggestions("SQL查询")

        assert len(suggestions) > 0
        assert all("agent_type" in s for s in suggestions)

    def test_add_routing_rule(self):
        """测试添加路由规则"""
        manager = AgentManager()
        router = AgentRouter(manager)

        initial_count = len(router.routing_rules)

        router.add_routing_rule(
            keywords=["test"],
            agent_type=AgentType.GENERALIST,
            priority=TaskPriority.LOW
        )

        assert len(router.routing_rules) == initial_count + 1

    def test_list_routing_rules(self):
        """测试列出路由规则"""
        manager = AgentManager()
        router = AgentRouter(manager)

        rules = router.list_routing_rules()

        assert len(rules) > 0
        assert all("agent_type" in r for r in rules)


# 集成测试

class TestAgentIntegration:
    """Agent集成测试"""

    @pytest.mark.asyncio
    async def test_full_workflow(self):
        """测试完整工作流程"""
        # 创建管理器
        manager = AgentManager()
        await manager.initialize()

        # 创建路由器
        router = AgentRouter(manager)

        # 提交任务
        query = "分析销售数据的趋势"
        agent_type, priority, params = await router.route_task(query)

        # 提交任务到管理器
        task_id = await manager.submit_task(
            agent_type=agent_type,
            priority=priority,
            input_data=params
        )

        assert task_id is not None

        # 等待一段时间让任务处理
        await asyncio.sleep(0.1)

        # 检查健康状态
        health = await manager.health_check()
        assert health["status"] == "healthy"

        await manager.shutdown()


# 运行示例

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
