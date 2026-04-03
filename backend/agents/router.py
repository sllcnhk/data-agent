"""
Agent路由器

智能路由任务到最合适的Agent
"""
from typing import Dict, List, Any, Optional, Tuple
import re
import json
import logging

from .base import AgentType, TaskPriority
from .manager import AgentManager

logger = logging.getLogger(__name__)


class AgentRouter:
    """Agent路由器"""

    def __init__(self, agent_manager: AgentManager):
        self.agent_manager = agent_manager
        self.routing_rules: List[Dict[str, Any]] = []
        self._initialize_default_rules()

    def _initialize_default_rules(self):
        """初始化默认路由规则"""
        # SQL相关关键词
        self.routing_rules.append({
            "keywords": ["sql", "查询", "select", "insert", "update", "delete", "数据库"],
            "agent_type": AgentType.SQL_EXPERT,
            "priority": TaskPriority.NORMAL
        })

        # 图表相关关键词
        self.routing_rules.append({
            "keywords": ["图表", "chart", "plot", "可视化", "柱状图", "折线图", "饼图", "散点图"],
            "agent_type": AgentType.CHART_BUILDER,
            "priority": TaskPriority.NORMAL
        })

        # 分析相关关键词
        self.routing_rules.append({
            "keywords": ["分析", "analysis", "统计", "趋势", "correlation", "outlier"],
            "agent_type": AgentType.DATA_ANALYST,
            "priority": TaskPriority.NORMAL
        })

        # ETL相关关键词
        self.routing_rules.append({
            "keywords": ["etl", "pipeline", "管道", "提取", "转换", "加载", "清洗", "验证"],
            "agent_type": AgentType.ETL_ENGINEER,
            "priority": TaskPriority.NORMAL
        })

        # 通用任务
        self.routing_rules.append({
            "keywords": ["通用", "general", "帮助", "help", "查询"],
            "agent_type": AgentType.GENERALIST,
            "priority": TaskPriority.LOW
        })

    async def route_task(
        self,
        query: str,
        context: Optional[Dict[str, Any]] = None
    ) -> Tuple[AgentType, TaskPriority, Dict[str, Any]]:
        """
        路由任务到合适的Agent

        Args:
            query: 用户查询
            context: 上下文信息

        Returns:
            Agent类型、优先级和增强的参数
        """
        try:
            # 预处理查询
            processed_query = self._preprocess_query(query)

            # 智能分析查询
            agent_type, priority = await self._analyze_query(processed_query, context)

            # 构建任务参数
            task_params = await self._build_task_params(query, context, agent_type)

            logger.info(f"Routed query to {agent_type.value} with priority {priority.value}")
            return agent_type, priority, task_params

        except Exception as e:
            logger.error(f"Routing failed: {str(e)}")
            # 默认路由到通用Agent
            return AgentType.GENERALIST, TaskPriority.NORMAL, {
                "action": "general",
                "query": query,
                "context": context
            }

    def _preprocess_query(self, query: str) -> str:
        """预处理查询"""
        # 转换为小写
        processed = query.lower()

        # 移除多余空格
        processed = re.sub(r'\s+', ' ', processed).strip()

        return processed

    async def _analyze_query(
        self,
        query: str,
        context: Optional[Dict[str, Any]]
    ) -> Tuple[AgentType, TaskPriority]:
        """
        分析查询并确定路由

        Args:
            query: 处理后的查询
            context: 上下文信息

        Returns:
            Agent类型和优先级
        """
        # 检查上下文中的明确指示
        if context and "agent_type" in context:
            return context["agent_type"], context.get("priority", TaskPriority.NORMAL)

        # 计算每个规则的匹配度
        rule_scores = []
        for rule in self.routing_rules:
            score = self._calculate_rule_score(query, rule)
            rule_scores.append((rule, score))

        # 按分数排序
        rule_scores.sort(key=lambda x: x[1], reverse=True)

        # 选择最佳匹配规则
        best_rule, best_score = rule_scores[0]

        # 如果分数太低，使用通用Agent
        if best_score < 0.3:
            return AgentType.GENERALIST, TaskPriority.LOW

        # 根据分数调整优先级
        priority = best_rule["priority"]
        if best_score > 0.8:
            priority = TaskPriority.HIGH

        return best_rule["agent_type"], priority

    def _calculate_rule_score(self, query: str, rule: Dict[str, Any]) -> float:
        """
        计算规则匹配分数

        Args:
            query: 查询文本
            rule: 路由规则

        Returns:
            匹配分数 (0-1)
        """
        keywords = rule["keywords"]
        score = 0.0

        for keyword in keywords:
            if keyword in query:
                # 直接匹配加分
                score += 0.3

                # 检查是否是完整词匹配
                pattern = r'\b' + re.escape(keyword) + r'\b'
                if re.search(pattern, query):
                    score += 0.2

        # 正则表达式匹配
        if "pattern" in rule:
            if re.search(rule["pattern"], query):
                score += 0.5

        # 规范化分数到0-1范围
        return min(score, 1.0)

    async def _build_task_params(
        self,
        query: str,
        context: Optional[Dict[str, Any]],
        agent_type: AgentType
    ) -> Dict[str, Any]:
        """
        构建任务参数

        Args:
            query: 原始查询
            context: 上下文
            agent_type: Agent类型

        Returns:
            任务参数
        """
        params = {
            "query": query,
            "context": context or {}
        }

        # 根据Agent类型添加特定参数
        if agent_type == AgentType.SQL_EXPERT:
            params.update(await self._extract_sql_params(query, context))
        elif agent_type == AgentType.CHART_BUILDER:
            params.update(await self._extract_chart_params(query, context))
        elif agent_type == AgentType.DATA_ANALYST:
            params.update(await self._extract_analysis_params(query, context))
        elif agent_type == AgentType.ETL_ENGINEER:
            params.update(await self._extract_etl_params(query, context))
        else:
            params.update(await self._extract_general_params(query, context))

        return params

    async def _extract_sql_params(self, query: str, context: Optional[Dict]) -> Dict[str, Any]:
        """提取SQL相关参数"""
        params = {
            "action": "generate"
        }

        # 尝试提取表名
        table_match = re.search(r'(?:from|into|update)\s+(\w+)', query, re.IGNORECASE)
        if table_match:
            params["table_name"] = table_match.group(1)

        # 尝试提取列名
        column_matches = re.findall(r'(?:select|where)\s+(\w+)', query, re.IGNORECASE)
        if column_matches:
            params["columns"] = column_matches

        # 尝试提取数据库类型
        if "clickhouse" in query.lower():
            params["database_type"] = "clickhouse"
        elif "mysql" in query.lower():
            params["database_type"] = "mysql"

        return params

    async def _extract_chart_params(self, query: str, context: Optional[Dict]) -> Dict[str, Any]:
        """提取图表相关参数"""
        params = {
            "action": "build"
        }

        # 尝试提取图表类型
        if "柱状图" in query or "bar" in query.lower():
            params["chart_type"] = "bar"
        elif "折线图" in query or "line" in query.lower():
            params["chart_type"] = "line"
        elif "饼图" in query or "pie" in query.lower():
            params["chart_type"] = "pie"
        elif "散点图" in query or "scatter" in query.lower():
            params["chart_type"] = "scatter"
        elif "面积图" in query or "area" in query.lower():
            params["chart_type"] = "area"
        else:
            params["chart_type"] = "auto"

        # 尝试提取标题
        title_match = re.search(r'(?:标题|title)[:：]\s*([^\s,，]+)', query, re.IGNORECASE)
        if title_match:
            params["title"] = title_match.group(1)

        return params

    async def _extract_analysis_params(self, query: str, context: Optional[Dict]) -> Dict[str, Any]:
        """提取分析相关参数"""
        params = {
            "action": "analyze"
        }

        # 尝试提取分析类型
        if "统计" in query or "statistics" in query.lower():
            params["analysis_type"] = "statistics"
        elif "相关" in query or "correlation" in query.lower():
            params["analysis_type"] = "correlation"
        elif "趋势" in query or "trend" in query.lower():
            params["analysis_type"] = "trend"
        elif "异常" in query or "outlier" in query.lower():
            params["analysis_type"] = "outliers"
        else:
            params["analysis_type"] = "summary"

        return params

    async def _extract_etl_params(self, query: str, context: Optional[Dict]) -> Dict[str, Any]:
        """提取ETL相关参数"""
        params = {
            "action": "design"
        }

        # 尝试提取源类型
        if "数据库" in query or "database" in query.lower():
            params["source_type"] = "database"
        elif "文件" in query or "file" in query.lower():
            params["source_type"] = "file"
        elif "api" in query.lower():
            params["source_type"] = "api"
        else:
            params["source_type"] = "database"

        # 尝试提取管道类型
        if "增量" in query or "incremental" in query.lower():
            params["pipeline_type"] = "incremental"
        else:
            params["pipeline_type"] = "full"

        return params

    async def _extract_general_params(self, query: str, context: Optional[Dict]) -> Dict[str, Any]:
        """提取通用参数"""
        params = {
            "action": "general",
            "task_type": "general"
        }

        return params

    def add_routing_rule(
        self,
        keywords: List[str],
        agent_type: AgentType,
        priority: TaskPriority = TaskPriority.NORMAL,
        pattern: Optional[str] = None
    ):
        """
        添加路由规则

        Args:
            keywords: 关键词列表
            agent_type: Agent类型
            priority: 优先级
            pattern: 正则表达式模式
        """
        rule = {
            "keywords": keywords,
            "agent_type": agent_type,
            "priority": priority
        }

        if pattern:
            rule["pattern"] = pattern

        self.routing_rules.append(rule)
        logger.info(f"Added routing rule for {keywords} -> {agent_type.value}")

    def remove_routing_rule(self, keywords: List[str]):
        """
        移除路由规则

        Args:
            keywords: 关键词列表
        """
        self.routing_rules = [
            rule for rule in self.routing_rules
            if rule["keywords"] != keywords
        ]
        logger.info(f"Removed routing rule for {keywords}")

    def list_routing_rules(self) -> List[Dict[str, Any]]:
        """列出所有路由规则"""
        return [
            {
                "keywords": rule["keywords"],
                "agent_type": rule["agent_type"].value,
                "priority": rule["priority"].value
            }
            for rule in self.routing_rules
        ]

    async def get_routing_suggestions(self, query: str) -> List[Dict[str, Any]]:
        """
        获取路由建议

        Args:
            query: 查询文本

        Returns:
            建议列表
        """
        processed_query = self._preprocess_query(query)
        suggestions = []

        for rule in self.routing_rules:
            score = self._calculate_rule_score(processed_query, rule)
            if score > 0.1:  # 只返回有意义的建议
                suggestions.append({
                    "agent_type": rule["agent_type"].value,
                    "priority": rule["priority"].value,
                    "confidence": score,
                    "keywords": rule["keywords"]
                })

        # 按置信度排序
        suggestions.sort(key=lambda x: x["confidence"], reverse=True)

        return suggestions
