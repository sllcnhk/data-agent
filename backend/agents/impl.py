"""
Agent具体实现

实现各种类型的Agent
"""
from typing import Dict, List, Any, Optional
import asyncio
import json
import logging

from .base import (
    BaseAgent,
    AgentType,
    Task,
    TaskPriority,
    AgentStatus,
    AgentCapability
)
from backend.skills.base import SkillRegistry, SkillInput, SkillOutput, create_skill_output

logger = logging.getLogger(__name__)


class DataAnalystAgent(BaseAgent):
    """数据分析师Agent"""

    def __init__(self, agent_id: str):
        super().__init__(
            agent_id=agent_id,
            agent_type=AgentType.DATA_ANALYST,
            name="Data Analyst",
            description="专业的数据分析Agent，能够执行数据查询、统计分析、趋势分析等任务"
        )
        self.skill_registry = SkillRegistry()

    async def initialize(self) -> bool:
        """初始化Agent"""
        try:
            # 注册所需的技能
            required_skills = [
                "database_query",
                "data_analysis",
                "trend_analysis",
                "outlier_detection"
            ]

            for skill_name in required_skills:
                if not self.skill_registry.get_skill(skill_name):
                    logger.warning(f"Skill {skill_name} not registered")

            logger.info(f"Data Analyst Agent {self.agent_id} initialized")
            return True
        except Exception as e:
            logger.error(f"Failed to initialize Data Analyst Agent: {str(e)}")
            return False

    async def execute_task(self, task: Task) -> SkillOutput:
        """执行数据分析任务"""
        try:
            action = task.input_data.get("action", "analyze")
            data = task.input_data.get("data")
            analysis_type = task.input_data.get("analysis_type", "summary")

            if action == "analyze":
                if analysis_type == "summary":
                    return await self._analyze_summary(data)
                elif analysis_type == "statistics":
                    return await self._analyze_statistics(data)
                elif analysis_type == "correlation":
                    return await self._analyze_correlation(data)
                elif analysis_type == "trend":
                    return await self._analyze_trend(data)
                elif analysis_type == "outliers":
                    return await self._detect_outliers(data)
                else:
                    return create_skill_output(
                        success=False,
                        error=f"不支持的分析类型: {analysis_type}"
                    )
            elif action == "query":
                return await self._query_database(task.input_data)
            else:
                return create_skill_output(
                    success=False,
                    error=f"不支持的操作: {action}"
                )

        except Exception as e:
            logger.error(f"Task execution failed: {str(e)}")
            return create_skill_output(
                success=False,
                error=f"任务执行失败: {str(e)}"
            )

    async def _analyze_summary(self, data: Any) -> SkillOutput:
        """执行摘要分析"""
        skill = self.skill_registry.get_skill("data_analysis")
        if not skill:
            return create_skill_output(success=False, error="data_analysis skill not found")

        input_data = SkillInput(parameters={
            "data": data,
            "analysis_type": "summary"
        })

        return await skill.execute(input_data)

    async def _analyze_statistics(self, data: Any) -> SkillOutput:
        """执行统计分析"""
        skill = self.skill_registry.get_skill("data_analysis")
        if not skill:
            return create_skill_output(success=False, error="data_analysis skill not found")

        input_data = SkillInput(parameters={
            "data": data,
            "analysis_type": "statistics"
        })

        return await skill.execute(input_data)

    async def _analyze_correlation(self, data: Any) -> SkillOutput:
        """执行相关性分析"""
        skill = self.skill_registry.get_skill("data_analysis")
        if not skill:
            return create_skill_output(success=False, error="data_analysis skill not found")

        input_data = SkillInput(parameters={
            "data": data,
            "analysis_type": "correlation"
        })

        return await skill.execute(input_data)

    async def _analyze_trend(self, data: Any) -> SkillOutput:
        """执行趋势分析"""
        skill = self.skill_registry.get_skill("trend_analysis")
        if not skill:
            return create_skill_output(success=False, error="trend_analysis skill not found")

        input_data = SkillInput(parameters={
            "data": data,
            "time_column": data[0].get("time_column") if data else None,
            "value_column": data[0].get("value_column") if data else None
        })

        return await skill.execute(input_data)

    async def _detect_outliers(self, data: Any) -> SkillOutput:
        """执行异常值检测"""
        skill = self.skill_registry.get_skill("outlier_detection")
        if not skill:
            return create_skill_output(success=False, error="outlier_detection skill not found")

        input_data = SkillInput(parameters={
            "data": data,
            "method": "zscore"
        })

        return await skill.execute(input_data)

    async def _query_database(self, params: Dict[str, Any]) -> SkillOutput:
        """执行数据库查询"""
        skill = self.skill_registry.get_skill("database_query")
        if not skill:
            return create_skill_output(success=False, error="database_query skill not found")

        input_data = SkillInput(parameters=params)
        return await skill.execute(input_data)

    def get_capabilities(self) -> List[str]:
        """获取能力列表"""
        return [
            "数据摘要分析",
            "描述性统计",
            "相关性分析",
            "趋势分析",
            "异常值检测",
            "数据库查询",
            "数据质量评估"
        ]


class SQLExpertAgent(BaseAgent):
    """SQL专家Agent"""

    def __init__(self, agent_id: str):
        super().__init__(
            agent_id=agent_id,
            agent_type=AgentType.SQL_EXPERT,
            name="SQL Expert",
            description="专业的SQL Agent，能够生成SQL查询、优化查询性能"
        )
        self.skill_registry = SkillRegistry()

    async def initialize(self) -> bool:
        """初始化Agent"""
        try:
            required_skills = [
                "sql_generation",
                "sql_optimization",
                "database_query",
                "database_describe_table"
            ]

            for skill_name in required_skills:
                if not self.skill_registry.get_skill(skill_name):
                    logger.warning(f"Skill {skill_name} not registered")

            logger.info(f"SQL Expert Agent {self.agent_id} initialized")
            return True
        except Exception as e:
            logger.error(f"Failed to initialize SQL Expert Agent: {str(e)}")
            return False

    async def execute_task(self, task: Task) -> SkillOutput:
        """执行SQL任务"""
        try:
            action = task.input_data.get("action", "generate")
            params = task.input_data

            if action == "generate":
                return await self._generate_sql(params)
            elif action == "optimize":
                return await self._optimize_sql(params)
            elif action == "execute":
                return await self._execute_sql(params)
            else:
                return create_skill_output(
                    success=False,
                    error=f"不支持的操作: {action}"
                )

        except Exception as e:
            logger.error(f"Task execution failed: {str(e)}")
            return create_skill_output(
                success=False,
                error=f"任务执行失败: {str(e)}"
            )

    async def _generate_sql(self, params: Dict[str, Any]) -> SkillOutput:
        """生成SQL查询"""
        skill = self.skill_registry.get_skill("sql_generation")
        if not skill:
            return create_skill_output(success=False, error="sql_generation skill not found")

        input_data = SkillInput(parameters=params)
        return await skill.execute(input_data)

    async def _optimize_sql(self, params: Dict[str, Any]) -> SkillOutput:
        """优化SQL查询"""
        skill = self.skill_registry.get_skill("sql_optimization")
        if not skill:
            return create_skill_output(success=False, error="sql_optimization skill not found")

        input_data = SkillInput(parameters=params)
        return await skill.execute(input_data)

    async def _execute_sql(self, params: Dict[str, Any]) -> SkillOutput:
        """执行SQL查询"""
        skill = self.skill_registry.get_skill("database_query")
        if not skill:
            return create_skill_output(success=False, error="database_query skill not found")

        input_data = SkillInput(parameters=params)
        return await skill.execute(input_data)

    def get_capabilities(self) -> List[str]:
        """获取能力列表"""
        return [
            "自然语言转SQL",
            "SQL查询生成",
            "SQL性能优化",
            "查询执行",
            "数据库结构分析"
        ]


class ChartBuilderAgent(BaseAgent):
    """图表构建Agent"""

    def __init__(self, agent_id: str):
        super().__init__(
            agent_id=agent_id,
            agent_type=AgentType.CHART_BUILDER,
            name="Chart Builder",
            description="专业的图表构建Agent，能够根据数据自动生成各类图表"
        )
        self.skill_registry = SkillRegistry()

    async def initialize(self) -> bool:
        """初始化Agent"""
        try:
            required_skills = [
                "chart_generation",
                "chart_type_recommendation",
                "data_analysis"
            ]

            for skill_name in required_skills:
                if not self.skill_registry.get_skill(skill_name):
                    logger.warning(f"Skill {skill_name} not registered")

            logger.info(f"Chart Builder Agent {self.agent_id} initialized")
            return True
        except Exception as e:
            logger.error(f"Failed to initialize Chart Builder Agent: {str(e)}")
            return False

    async def execute_task(self, task: Task) -> SkillOutput:
        """执行图表构建任务"""
        try:
            action = task.input_data.get("action", "build")
            data = task.input_data.get("data")

            if action == "build":
                return await self._build_chart(task.input_data)
            elif action == "recommend":
                return await self._recommend_chart(data)
            else:
                return create_skill_output(
                    success=False,
                    error=f"不支持的操作: {action}"
                )

        except Exception as e:
            logger.error(f"Task execution failed: {str(e)}")
            return create_skill_output(
                success=False,
                error=f"任务执行失败: {str(e)}"
            )

    async def _build_chart(self, params: Dict[str, Any]) -> SkillOutput:
        """构建图表"""
        skill = self.skill_registry.get_skill("chart_generation")
        if not skill:
            return create_skill_output(success=False, error="chart_generation skill not found")

        input_data = SkillInput(parameters=params)
        return await skill.execute(input_data)

    async def _recommend_chart(self, data: Any) -> SkillOutput:
        """推荐图表类型"""
        skill = self.skill_registry.get_skill("chart_type_recommendation")
        if not skill:
            return create_skill_output(success=False, error="chart_type_recommendation skill not found")

        input_data = SkillInput(parameters={
            "data": data
        })

        return await skill.execute(input_data)

    def get_capabilities(self) -> List[str]:
        """获取能力列表"""
        return [
            "柱状图生成",
            "折线图生成",
            "饼图生成",
            "散点图生成",
            "面积图生成",
            "图表类型推荐",
            "图表配置优化"
        ]


class ETLEngineerAgent(BaseAgent):
    """ETL工程师Agent"""

    def __init__(self, agent_id: str):
        super().__init__(
            agent_id=agent_id,
            agent_type=AgentType.ETL_ENGINEER,
            name="ETL Engineer",
            description="专业的ETL Agent，能够设计ETL管道、验证和清洗数据"
        )
        self.skill_registry = SkillRegistry()

    async def initialize(self) -> bool:
        """初始化Agent"""
        try:
            required_skills = [
                "etl_design",
                "data_validation",
                "data_cleaning"
            ]

            for skill_name in required_skills:
                if not self.skill_registry.get_skill(skill_name):
                    logger.warning(f"Skill {skill_name} not registered")

            logger.info(f"ETL Engineer Agent {self.agent_id} initialized")
            return True
        except Exception as e:
            logger.error(f"Failed to initialize ETL Engineer Agent: {str(e)}")
            return False

    async def execute_task(self, task: Task) -> SkillOutput:
        """执行ETL任务"""
        try:
            action = task.input_data.get("action", "design")
            params = task.input_data

            if action == "design":
                return await self._design_etl(params)
            elif action == "validate":
                return await self._validate_data(params)
            elif action == "clean":
                return await self._clean_data(params)
            else:
                return create_skill_output(
                    success=False,
                    error=f"不支持的操作: {action}"
                )

        except Exception as e:
            logger.error(f"Task execution failed: {str(e)}")
            return create_skill_output(
                success=False,
                error=f"任务执行失败: {str(e)}"
            )

    async def _design_etl(self, params: Dict[str, Any]) -> SkillOutput:
        """设计ETL管道"""
        skill = self.skill_registry.get_skill("etl_design")
        if not skill:
            return create_skill_output(success=False, error="etl_design skill not found")

        input_data = SkillInput(parameters=params)
        return await skill.execute(input_data)

    async def _validate_data(self, params: Dict[str, Any]) -> SkillOutput:
        """验证数据"""
        skill = self.skill_registry.get_skill("data_validation")
        if not skill:
            return create_skill_output(success=False, error="data_validation skill not found")

        input_data = SkillInput(parameters=params)
        return await skill.execute(input_data)

    async def _clean_data(self, params: Dict[str, Any]) -> SkillOutput:
        """清洗数据"""
        skill = self.skill_registry.get_skill("data_cleaning")
        if not skill:
            return create_skill_output(success=False, error="data_cleaning skill not found")

        input_data = SkillInput(parameters=params)
        return await skill.execute(input_data)

    def get_capabilities(self) -> List[str]:
        """获取能力列表"""
        return [
            "ETL管道设计",
            "数据提取",
            "数据转换",
            "数据加载",
            "数据验证",
            "数据清洗",
            "数据质量评估"
        ]


class GeneralistAgent(BaseAgent):
    """通用Agent"""

    def __init__(self, agent_id: str):
        super().__init__(
            agent_id=agent_id,
            agent_type=AgentType.GENERALIST,
            name="Generalist",
            description="通用的数据Agent，能够处理各种数据分析任务"
        )
        self.skill_registry = SkillRegistry()
        self.sub_agents: List[BaseAgent] = []

    async def initialize(self) -> bool:
        """初始化Agent"""
        try:
            # 注册所有技能
            all_skills = [
                "database_query",
                "data_analysis",
                "trend_analysis",
                "outlier_detection",
                "sql_generation",
                "sql_optimization",
                "chart_generation",
                "chart_type_recommendation",
                "etl_design",
                "data_validation",
                "data_cleaning"
            ]

            for skill_name in all_skills:
                if not self.skill_registry.get_skill(skill_name):
                    logger.warning(f"Skill {skill_name} not registered")

            # 创建子Agent
            self.sub_agents = [
                DataAnalystAgent(f"{self.agent_id}-analyst"),
                SQLExpertAgent(f"{self.agent_id}-sql"),
                ChartBuilderAgent(f"{self.agent_id}-chart"),
                ETLEngineerAgent(f"{self.agent_id}-etl")
            ]

            logger.info(f"Generalist Agent {self.agent_id} initialized with {len(self.sub_agents)} sub-agents")
            return True
        except Exception as e:
            logger.error(f"Failed to initialize Generalist Agent: {str(e)}")
            return False

    async def execute_task(self, task: Task) -> SkillOutput:
        """执行通用任务"""
        try:
            # 根据任务类型委派给子Agent
            task_type = task.input_data.get("task_type", "general")

            if task_type == "analysis":
                agent = self._get_agent(AgentType.DATA_ANALYST)
            elif task_type == "sql":
                agent = self._get_agent(AgentType.SQL_EXPERT)
            elif task_type == "chart":
                agent = self._get_agent(AgentType.CHART_BUILDER)
            elif task_type == "etl":
                agent = self._get_agent(AgentType.ETL_ENGINEER)
            else:
                # 通用任务，自己处理
                return await self._handle_general_task(task)

            if agent:
                # 创建子任务
                sub_task = Task(
                    task_id=f"{task.task_id}-{agent.agent_type.value}",
                    agent_type=agent.agent_type,
                    priority=task.priority,
                    input_data=task.input_data
                )

                return await agent.execute_task(sub_task)
            else:
                return create_skill_output(
                    success=False,
                    error="找不到合适的子Agent"
                )

        except Exception as e:
            logger.error(f"Task execution failed: {str(e)}")
            return create_skill_output(
                success=False,
                error=f"任务执行失败: {str(e)}"
            )

    def _get_agent(self, agent_type: AgentType) -> Optional[BaseAgent]:
        """获取指定类型的Agent"""
        for agent in self.sub_agents:
            if agent.agent_type == agent_type:
                return agent
        return None

    async def _handle_general_task(self, task: Task) -> SkillOutput:
        """处理通用任务"""
        # 尝试智能选择合适的技能
        task_data = task.input_data

        if "sql" in task_data or "query" in task_data:
            return await self._execute_skill("sql_generation", task_data)
        elif "chart" in task_data or "plot" in task_data:
            return await self._execute_skill("chart_generation", task_data)
        elif "analyze" in task_data or "analysis" in task_data:
            return await self._execute_skill("data_analysis", task_data)
        elif "etl" in task_data or "pipeline" in task_data:
            return await self._execute_skill("etl_design", task_data)
        else:
            return create_skill_output(
                success=False,
                error="无法识别任务类型"
            )

    async def _execute_skill(self, skill_name: str, params: Dict[str, Any]) -> SkillOutput:
        """执行指定技能"""
        skill = self.skill_registry.get_skill(skill_name)
        if not skill:
            return create_skill_output(success=False, error=f"skill {skill_name} not found")

        input_data = SkillInput(parameters=params)
        return await skill.execute(input_data)

    def get_capabilities(self) -> List[str]:
        """获取能力列表"""
        capabilities = [
            "智能任务路由",
            "多技能协调",
            "复合任务处理"
        ]

        # 收集所有子Agent的能力
        for agent in self.sub_agents:
            capabilities.extend(agent.get_capabilities())

        return capabilities
