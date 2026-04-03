"""
Agent系统模块

提供Agent创建、管理、路由功能
"""

from .base import (
    BaseAgent,
    AgentType,
    AgentStatus,
    Task,
    TaskPriority,
    TaskStatus,
    AgentCapability,
    AgentMetrics
)

from .impl import (
    DataAnalystAgent,
    SQLExpertAgent,
    ChartBuilderAgent,
    ETLEngineerAgent,
    GeneralistAgent
)

from .manager import (
    AgentManager,
    get_agent_manager,
    shutdown_agent_manager
)

from .router import AgentRouter

__all__ = [
    # Base
    "BaseAgent",
    "AgentType",
    "AgentStatus",
    "Task",
    "TaskPriority",
    "TaskStatus",
    "AgentCapability",
    "AgentMetrics",

    # Implementations
    "DataAnalystAgent",
    "SQLExpertAgent",
    "ChartBuilderAgent",
    "ETLEngineerAgent",
    "GeneralistAgent",

    # Manager
    "AgentManager",
    "get_agent_manager",
    "shutdown_agent_manager",

    # Router
    "AgentRouter",
]
