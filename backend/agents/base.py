"""
Agent基础模块

定义Agent的核心抽象类和接口
"""
from typing import Dict, List, Any, Optional, Union
from abc import ABC, abstractmethod
from enum import Enum
from datetime import datetime
import asyncio
import json

from backend.skills.base import SkillInput, SkillOutput


class AgentType(Enum):
    """Agent类型"""
    DATA_ANALYST = "data_analyst"
    SQL_EXPERT = "sql_expert"
    CHART_BUILDER = "chart_builder"
    ETL_ENGINEER = "etl_engineer"
    GENERALIST = "generalist"


class AgentStatus(Enum):
    """Agent状态"""
    IDLE = "idle"
    BUSY = "busy"
    ERROR = "error"
    OFFLINE = "offline"


class TaskPriority(Enum):
    """任务优先级"""
    LOW = 1
    NORMAL = 2
    HIGH = 3
    URGENT = 4


class TaskStatus(Enum):
    """任务状态"""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Task:
    """任务类"""

    def __init__(
        self,
        task_id: str,
        agent_type: AgentType,
        priority: TaskPriority,
        input_data: Dict[str, Any],
        created_at: Optional[datetime] = None
    ):
        self.task_id = task_id
        self.agent_type = agent_type
        self.priority = priority
        self.input_data = input_data
        self.created_at = created_at or datetime.utcnow()
        self.started_at: Optional[datetime] = None
        self.completed_at: Optional[datetime] = None
        self.status = TaskStatus.PENDING
        self.result: Optional[SkillOutput] = None
        self.error: Optional[str] = None
        self.retry_count = 0
        self.max_retries = 3

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "task_id": self.task_id,
            "agent_type": self.agent_type.value,
            "priority": self.priority.value,
            "input_data": self.input_data,
            "status": self.status.value,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "error": self.error,
            "retry_count": self.retry_count
        }


class BaseAgent(ABC):
    """Agent基类"""

    def __init__(
        self,
        agent_id: str,
        agent_type: AgentType,
        name: str,
        description: str,
        version: str = "1.0.0"
    ):
        self.agent_id = agent_id
        self.agent_type = agent_type
        self.name = name
        self.description = description
        self.version = version
        self.status = AgentStatus.IDLE
        self.created_at = datetime.utcnow()
        self.last_active_at = datetime.utcnow()
        self.task_history: List[Task] = []
        self.current_task: Optional[Task] = None
        self.max_concurrent_tasks = 1

    @abstractmethod
    async def execute_task(self, task: Task) -> SkillOutput:
        """
        执行任务

        Args:
            task: 任务对象

        Returns:
            任务执行结果
        """
        pass

    @abstractmethod
    def get_capabilities(self) -> List[str]:
        """
        获取Agent能力列表

        Returns:
            能力列表
        """
        pass

    @abstractmethod
    async def initialize(self) -> bool:
        """
        初始化Agent

        Returns:
            初始化是否成功
        """
        pass

    async def start_task(self, task: Task) -> bool:
        """
        开始执行任务

        Args:
            task: 任务对象

        Returns:
            是否成功开始
        """
        if self.status == AgentStatus.BUSY:
            return False

        self.status = AgentStatus.BUSY
        self.current_task = task
        self.last_active_at = datetime.utcnow()
        task.started_at = datetime.utcnow()
        task.status = TaskStatus.RUNNING

        return True

    async def complete_task(
        self,
        task: Task,
        result: Optional[SkillOutput] = None,
        error: Optional[str] = None
    ):
        """
        完成任务

        Args:
            task: 任务对象
            result: 执行结果
            error: 错误信息
        """
        task.completed_at = datetime.utcnow()

        if error:
            task.status = TaskStatus.FAILED
            task.error = error
            self.status = AgentStatus.ERROR
        elif result:
            task.status = TaskStatus.COMPLETED
            task.result = result
            self.status = AgentStatus.IDLE

        self.task_history.append(task)
        self.current_task = None
        self.last_active_at = datetime.utcnow()

    async def cancel_task(self, task: Task):
        """取消任务"""
        task.status = TaskStatus.CANCELLED
        task.completed_at = datetime.utcnow()
        self.status = AgentStatus.IDLE
        self.current_task = None

    async def retry_task(self, task: Task) -> bool:
        """
        重试任务

        Args:
            task: 任务对象

        Returns:
            是否可以重试
        """
        if task.retry_count >= task.max_retries:
            return False

        task.retry_count += 1
        task.status = TaskStatus.PENDING
        task.started_at = None
        task.completed_at = None
        task.error = None

        return True

    def get_info(self) -> Dict[str, Any]:
        """
        获取Agent信息

        Returns:
            Agent信息字典
        """
        return {
            "agent_id": self.agent_id,
            "agent_type": self.agent_type.value,
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "status": self.status.value,
            "created_at": self.created_at.isoformat(),
            "last_active_at": self.last_active_at.isoformat(),
            "capabilities": self.get_capabilities(),
            "current_task": self.current_task.to_dict() if self.current_task else None,
            "completed_tasks": len([t for t in self.task_history if t.status == TaskStatus.COMPLETED]),
            "failed_tasks": len([t for t in self.task_history if t.status == TaskStatus.FAILED])
        }

    def get_task_history(
        self,
        status: Optional[TaskStatus] = None,
        limit: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """
        获取任务历史

        Args:
            status: 过滤状态
            limit: 返回数量限制

        Returns:
            任务历史列表
        """
        tasks = self.task_history

        if status:
            tasks = [t for t in tasks if t.status == status]

        tasks.sort(key=lambda t: t.created_at, reverse=True)

        if limit:
            tasks = tasks[:limit]

        return [t.to_dict() for t in tasks]

    async def health_check(self) -> Dict[str, Any]:
        """
        健康检查

        Returns:
            健康状态信息
        """
        return {
            "agent_id": self.agent_id,
            "status": self.status.value,
            "last_active": self.last_active_at.isoformat(),
            "current_task": self.current_task.task_id if self.current_task else None,
            "healthy": self.status not in [AgentStatus.ERROR, AgentStatus.OFFLINE]
        }


class AgentCapability:
    """Agent能力"""

    def __init__(
        self,
        name: str,
        description: str,
        required_skills: List[str],
        input_schema: Dict[str, Any],
        output_schema: Dict[str, Any]
    ):
        self.name = name
        self.description = description
        self.required_skills = required_skills
        self.input_schema = input_schema
        self.output_schema = output_schema

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "name": self.name,
            "description": self.description,
            "required_skills": self.required_skills,
            "input_schema": self.input_schema,
            "output_schema": self.output_schema
        }


class AgentMetrics:
    """Agent指标"""

    def __init__(self):
        self.total_tasks = 0
        self.completed_tasks = 0
        self.failed_tasks = 0
        self.cancelled_tasks = 0
        self.total_execution_time = 0.0
        self.average_execution_time = 0.0
        self.last_task_time: Optional[datetime] = None

    def record_task_completion(self, execution_time: float, success: bool):
        """记录任务完成"""
        self.total_tasks += 1
        self.last_task_time = datetime.utcnow()

        if success:
            self.completed_tasks += 1
        else:
            self.failed_tasks += 1

        self.total_execution_time += execution_time
        if self.completed_tasks + self.failed_tasks > 0:
            self.average_execution_time = (
                self.total_execution_time /
                (self.completed_tasks + self.failed_tasks)
            )

    def get_success_rate(self) -> float:
        """获取成功率"""
        total = self.completed_tasks + self.failed_tasks
        if total == 0:
            return 0.0
        return self.completed_tasks / total

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "total_tasks": self.total_tasks,
            "completed_tasks": self.completed_tasks,
            "failed_tasks": self.failed_tasks,
            "cancelled_tasks": self.cancelled_tasks,
            "success_rate": self.get_success_rate(),
            "total_execution_time": self.total_execution_time,
            "average_execution_time": self.average_execution_time,
            "last_task_time": self.last_task_time.isoformat() if self.last_task_time else None
        }
