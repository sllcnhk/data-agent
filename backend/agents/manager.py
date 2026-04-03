"""
Agent管理器

负责Agent的创建、生命周期管理、任务分发和调度
"""
from typing import Dict, List, Any, Optional, Set
import asyncio
import uuid
from datetime import datetime
import logging

from .base import (
    BaseAgent,
    AgentType,
    Task,
    TaskStatus,
    TaskPriority,
    AgentStatus
)
from backend.skills.base import SkillRegistry, SkillType


logger = logging.getLogger(__name__)


class AgentManager:
    """Agent管理器"""

    def __init__(self):
        self.agents: Dict[str, BaseAgent] = {}
        self.agent_types: Dict[AgentType, List[str]] = {}
        self.task_queue: asyncio.Queue = asyncio.Queue()
        self.running = False
        self._lock = asyncio.Lock()
        self._worker_tasks: List[asyncio.Task] = []

    async def initialize(self):
        """初始化管理器"""
        self.running = True
        # 启动工作协程
        for i in range(5):  # 启动5个工作协程
            task = asyncio.create_task(self._worker(f"worker-{i}"))
            self._worker_tasks.append(task)

        logger.info("Agent Manager initialized with 5 workers")

    async def shutdown(self):
        """关闭管理器"""
        logger.info("Shutting down Agent Manager...")

        self.running = False

        # 取消所有工作协程
        for task in self._worker_tasks:
            task.cancel()

        # 等待所有任务完成
        if self._worker_tasks:
            await asyncio.gather(*self._worker_tasks, return_exceptions=True)

        logger.info("Agent Manager shutdown complete")

    async def _worker(self, worker_id: str):
        """工作协程"""
        logger.info(f"Worker {worker_id} started")

        while self.running:
            try:
                # 从队列获取任务
                task = await asyncio.wait_for(self.task_queue.get(), timeout=1.0)

                # 找到合适的Agent
                agent = await self._find_available_agent(task.agent_type)

                if agent:
                    logger.info(f"Worker {worker_id} assigned task {task.task_id} to agent {agent.agent_id}")
                    await self._execute_task(agent, task)
                else:
                    # 没有可用的Agent，重新放回队列
                    logger.warning(f"No available agent for task {task.task_id}, requeueing")
                    await self.task_queue.put(task)
                    await asyncio.sleep(1)

            except asyncio.TimeoutError:
                # 正常超时，继续循环
                continue
            except Exception as e:
                logger.error(f"Worker {worker_id} error: {str(e)}")

        logger.info(f"Worker {worker_id} stopped")

    async def _find_available_agent(self, agent_type: AgentType) -> Optional[BaseAgent]:
        """找到可用的Agent"""
        available_agents = [
            agent for agent in self.agents.values()
            if agent.agent_type == agent_type and agent.status == AgentStatus.IDLE
        ]

        if available_agents:
            # 选择第一个可用的Agent
            return available_agents[0]

        return None

    async def _execute_task(self, agent: BaseAgent, task: Task):
        """执行任务"""
        try:
            # 开始任务
            await agent.start_task(task)

            # 执行任务
            result = await agent.execute_task(task)

            # 完成任务
            await agent.complete_task(task, result=result)

            logger.info(f"Task {task.task_id} completed by agent {agent.agent_id}")

        except Exception as e:
            logger.error(f"Task {task.task_id} failed: {str(e)}")
            await agent.complete_task(task, error=str(e))

    async def register_agent(self, agent: BaseAgent) -> bool:
        """
        注册Agent

        Args:
            agent: Agent实例

        Returns:
            注册是否成功
        """
        async with self._lock:
            # 检查Agent ID是否已存在
            if agent.agent_id in self.agents:
                logger.warning(f"Agent {agent.agent_id} already registered")
                return False

            # 初始化Agent
            success = await agent.initialize()
            if not success:
                logger.error(f"Failed to initialize agent {agent.agent_id}")
                return False

            # 注册Agent
            self.agents[agent.agent_id] = agent

            # 更新类型映射
            if agent.agent_type not in self.agent_types:
                self.agent_types[agent.agent_type] = []
            self.agent_types[agent.agent_type].append(agent.agent_id)

            logger.info(f"Agent {agent.agent_id} registered successfully")
            return True

    async def unregister_agent(self, agent_id: str) -> bool:
        """
        注销Agent

        Args:
            agent_id: Agent ID

        Returns:
            注销是否成功
        """
        async with self._lock:
            if agent_id not in self.agents:
                return False

            agent = self.agents[agent_id]

            # 取消当前任务
            if agent.current_task:
                await agent.cancel_task(agent.current_task)

            # 从映射中移除
            if agent.agent_type in self.agent_types:
                self.agent_types[agent.agent_type].remove(agent_id)

            # 移除Agent
            del self.agents[agent_id]

            logger.info(f"Agent {agent_id} unregistered")
            return True

    async def submit_task(
        self,
        agent_type: AgentType,
        priority: TaskPriority,
        input_data: Dict[str, Any]
    ) -> str:
        """
        提交任务

        Args:
            agent_type: Agent类型
            priority: 任务优先级
            input_data: 输入数据

        Returns:
            任务ID
        """
        task_id = str(uuid.uuid4())

        task = Task(
            task_id=task_id,
            agent_type=agent_type,
            priority=priority,
            input_data=input_data
        )

        # 根据优先级放入队列
        if priority == TaskPriority.URGENT:
            # 紧急任务直接放到队列头部
            await self.task_queue.put(task)
        else:
            await self.task_queue.put(task)

        logger.info(f"Task {task_id} submitted with priority {priority.value}")
        return task_id

    async def get_task_status(self, task_id: str) -> Optional[Dict[str, Any]]:
        """
        获取任务状态

        Args:
            task_id: 任务ID

        Returns:
            任务状态
        """
        # 在所有Agent中查找任务
        for agent in self.agents.values():
            if agent.current_task and agent.current_task.task_id == task_id:
                return agent.current_task.to_dict()

            # 检查历史任务
            for task in agent.task_history:
                if task.task_id == task_id:
                    return task.to_dict()

        return None

    async def cancel_task(self, task_id: str) -> bool:
        """
        取消任务

        Args:
            task_id: 任务ID

        Returns:
            取消是否成功
        """
        # 在当前任务中查找
        for agent in self.agents.values():
            if agent.current_task and agent.current_task.task_id == task_id:
                await agent.cancel_task(agent.current_task)
                logger.info(f"Task {task_id} cancelled")
                return True

        # 在队列中查找（简单实现）
        queue_size = self.task_queue.qsize()
        temp_queue = asyncio.Queue()

        for _ in range(queue_size):
            task = await self.task_queue.get()
            if task.task_id != task_id:
                await temp_queue.put(task)

        self.task_queue = temp_queue
        logger.info(f"Task {task_id} removed from queue")
        return True

    async def retry_task(self, task_id: str) -> bool:
        """
        重试任务

        Args:
            task_id: 任务ID

        Returns:
            重试是否成功
        """
        # 找到任务
        task_dict = await self.get_task_status(task_id)
        if not task_dict:
            return False

        # 在Agent历史中找到任务
        for agent in self.agents.values():
            for task in agent.task_history:
                if task.task_id == task_id:
                    return await agent.retry_task(task)

        return False

    def get_agent(self, agent_id: str) -> Optional[BaseAgent]:
        """
        获取Agent实例

        Args:
            agent_id: Agent ID

        Returns:
            Agent实例
        """
        return self.agents.get(agent_id)

    def list_agents(self, agent_type: Optional[AgentType] = None) -> List[Dict[str, Any]]:
        """
        列出所有Agent

        Args:
            agent_type: 过滤类型

        Returns:
            Agent信息列表
        """
        agents = list(self.agents.values())

        if agent_type:
            agents = [a for a in agents if a.agent_type == agent_type]

        return [agent.get_info() for agent in agents]

    async def get_agent_metrics(self, agent_id: str) -> Optional[Dict[str, Any]]:
        """
        获取Agent指标

        Args:
            agent_id: Agent ID

        Returns:
            指标信息
        """
        agent = self.agents.get(agent_id)
        if not agent:
            return None

        return {
            "agent_id": agent_id,
            "info": agent.get_info(),
            "metrics": {
                "total_tasks": len(agent.task_history),
                "completed_tasks": len([t for t in agent.task_history if t.status == TaskStatus.COMPLETED]),
                "failed_tasks": len([t for t in agent.task_history if t.status == TaskStatus.FAILED])
            }
        }

    async def health_check(self) -> Dict[str, Any]:
        """
        健康检查

        Returns:
            健康状态
        """
        return {
            "status": "healthy" if self.running else "shutdown",
            "total_agents": len(self.agents),
            "agent_types": {t.value: len(ids) for t, ids in self.agent_types.items()},
            "queue_size": self.task_queue.qsize(),
            "active_workers": len(self._worker_tasks),
            "agents": [
                {
                    "agent_id": agent.agent_id,
                    "status": agent.status.value,
                    "current_task": agent.current_task.task_id if agent.current_task else None
                }
                for agent in self.agents.values()
            ]
        }

    async def auto_scale(self):
        """自动扩缩容（简单实现）"""
        queue_size = self.task_queue.qsize()

        # 如果队列中有超过10个任务，尝试启动更多Agent
        if queue_size > 10:
            logger.info(f"Queue size is {queue_size}, considering auto-scaling")
            # 这里可以实现实际的自动扩缩容逻辑


# 全局Agent管理器实例
_agent_manager: Optional[AgentManager] = None


async def get_agent_manager() -> AgentManager:
    """获取全局Agent管理器实例"""
    global _agent_manager

    if _agent_manager is None:
        _agent_manager = AgentManager()
        await _agent_manager.initialize()

    return _agent_manager


async def shutdown_agent_manager():
    """关闭全局Agent管理器"""
    global _agent_manager

    if _agent_manager:
        await _agent_manager.shutdown()
        _agent_manager = None
