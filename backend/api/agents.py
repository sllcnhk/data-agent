"""
Agent API路由

提供Agent管理相关的REST API
"""
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel

from backend.agents import (
    AgentManager,
    get_agent_manager,
    AgentType,
    TaskPriority,
    DataAnalystAgent,
    SQLExpertAgent,
    ChartBuilderAgent,
    ETLEngineerAgent,
    GeneralistAgent
)

router = APIRouter(prefix="/agents", tags=["agents"])


class AgentCreateRequest(BaseModel):
    """创建Agent请求"""
    agent_type: str
    agent_id: Optional[str] = None
    name: Optional[str] = None
    description: Optional[str] = None


class TaskSubmitRequest(BaseModel):
    """提交任务请求"""
    query: str
    priority: str = "normal"
    context: Optional[Dict[str, Any]] = None


class TaskStatusResponse(BaseModel):
    """任务状态响应"""
    task_id: str
    status: str
    agent_type: Optional[str] = None
    priority: int
    created_at: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    error: Optional[str] = None


@router.get("", response_model=List[Dict[str, Any]])
async def list_agents(
    agent_type: Optional[str] = None
):
    """
    获取Agent列表

    Args:
        agent_type: 过滤的Agent类型

    Returns:
        Agent信息列表
    """
    try:
        manager = await get_agent_manager()

        if agent_type:
            agent_type_enum = AgentType(agent_type.lower())
            agents = manager.list_agents(agent_type_enum)
        else:
            agents = manager.list_agents()

        return agents
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("", response_model=Dict[str, Any])
async def create_agent(
    request: AgentCreateRequest
):
    """
    创建新Agent

    Args:
        request: 创建Agent请求
        manager: Agent管理器实例

    Returns:
        创建结果
    """
    try:
        manager = await get_agent_manager()

        # 生成Agent ID
        agent_id = request.agent_id or f"{request.agent_type}-{len(manager.agents) + 1}"

        # 创建Agent实例
        agent_type_enum = AgentType(request.agent_type.lower())

        if agent_type_enum == AgentType.DATA_ANALYST:
            agent = DataAnalystAgent(agent_id)
        elif agent_type_enum == AgentType.SQL_EXPERT:
            agent = SQLExpertAgent(agent_id)
        elif agent_type_enum == AgentType.CHART_BUILDER:
            agent = ChartBuilderAgent(agent_id)
        elif agent_type_enum == AgentType.ETL_ENGINEER:
            agent = ETLEngineerAgent(agent_id)
        elif agent_type_enum == AgentType.GENERALIST:
            agent = GeneralistAgent(agent_id)
        else:
            raise HTTPException(status_code=400, detail=f"不支持的Agent类型: {request.agent_type}")

        # 注册Agent
        success = await manager.register_agent(agent)

        if not success:
            raise HTTPException(status_code=400, detail="Agent创建失败")

        return {
            "success": True,
            "agent_id": agent_id,
            "message": "Agent创建成功"
        }

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{agent_id}", response_model=Dict[str, Any])
async def get_agent(
    agent_id: str
):
    """
    获取Agent信息

    Args:
        agent_id: Agent ID
        manager: Agent管理器实例

    Returns:
        Agent信息
    """
    try:
        manager = await get_agent_manager()

        agent = manager.get_agent(agent_id)

        if not agent:
            raise HTTPException(status_code=404, detail=f"Agent {agent_id} 不存在")

        return agent.get_info()

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{agent_id}", response_model=Dict[str, Any])
async def delete_agent(
    agent_id: str
):
    """
    删除Agent

    Args:
        agent_id: Agent ID
        manager: Agent管理器实例

    Returns:
        删除结果
    """
    try:
        manager = await get_agent_manager()

        success = await manager.unregister_agent(agent_id)

        if not success:
            raise HTTPException(status_code=404, detail=f"Agent {agent_id} 不存在")

        return {
            "success": True,
            "message": f"Agent {agent_id} 已删除"
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{agent_id}/metrics", response_model=Dict[str, Any])
async def get_agent_metrics(
    agent_id: str
):
    """
    获取Agent指标

    Args:
        agent_id: Agent ID
        manager: Agent管理器实例

    Returns:
        Agent指标信息
    """
    try:
        manager = await get_agent_manager()

        metrics = await manager.get_agent_metrics(agent_id)

        if not metrics:
            raise HTTPException(status_code=404, detail=f"Agent {agent_id} 不存在")

        return metrics

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{agent_id}/tasks", response_model=List[Dict[str, Any]])
async def get_agent_tasks(
    agent_id: str,
    status: Optional[str] = None,
    limit: Optional[int] = 10
):
    """
    获取Agent任务历史

    Args:
        agent_id: Agent ID
        status: 过滤状态
        limit: 返回数量限制
        manager: Agent管理器实例

    Returns:
        任务历史列表
    """
    try:
        manager = await get_agent_manager()

        agent = manager.get_agent(agent_id)

        if not agent:
            raise HTTPException(status_code=404, detail=f"Agent {agent_id} 不存在")

        from backend.agents.base import TaskStatus
        status_filter = TaskStatus(status) if status else None

        return agent.get_task_history(status=status_filter, limit=limit)

    except HTTPException:
        raise
    except ValueError:
        raise HTTPException(status_code=400, detail=f"无效的状态: {status}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/tasks", response_model=Dict[str, Any])
async def submit_task(
    request: TaskSubmitRequest,
    background_tasks: BackgroundTasks
):
    """
    提交任务

    Args:
        request: 任务请求
        background_tasks: 后台任务
        manager: Agent管理器实例

    Returns:
        任务ID和路由信息
    """
    try:
        manager = await get_agent_manager()

        # 使用路由器路由任务
        from backend.agents.router import AgentRouter
        router = AgentRouter(manager)

        agent_type, priority, task_params = await router.route_task(
            query=request.query,
            context=request.context
        )

        # 提交任务
        task_id = await manager.submit_task(
            agent_type=agent_type,
            priority=TaskPriority(request.priority.lower()),
            input_data=task_params
        )

        return {
            "success": True,
            "task_id": task_id,
            "agent_type": agent_type.value,
            "priority": priority.value,
            "message": "任务已提交"
        }

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/tasks/{task_id}/status", response_model=Dict[str, Any])
async def get_task_status(
    task_id: str
):
    """
    获取任务状态

    Args:
        task_id: 任务ID
        manager: Agent管理器实例

    Returns:
        任务状态
    """
    try:
        manager = await get_agent_manager()

        task_status = await manager.get_task_status(task_id)

        if not task_status:
            raise HTTPException(status_code=404, detail=f"任务 {task_id} 不存在")

        return task_status

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/tasks/{task_id}", response_model=Dict[str, Any])
async def cancel_task(
    task_id: str
):
    """
    取消任务

    Args:
        task_id: 任务ID
        manager: Agent管理器实例

    Returns:
        取消结果
    """
    try:
        manager = await get_agent_manager()

        success = await manager.cancel_task(task_id)

        if not success:
            raise HTTPException(status_code=404, detail=f"任务 {task_id} 不存在")

        return {
            "success": True,
            "message": f"任务 {task_id} 已取消"
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/tasks/{task_id}/retry", response_model=Dict[str, Any])
async def retry_task(
    task_id: str
):
    """
    重试任务

    Args:
        task_id: 任务ID
        manager: Agent管理器实例

    Returns:
        重试结果
    """
    try:
        manager = await get_agent_manager()

        success = await manager.retry_task(task_id)

        if not success:
            raise HTTPException(status_code=400, detail=f"任务 {task_id} 无法重试")

        return {
            "success": True,
            "message": f"任务 {task_id} 已重新提交"
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/health", response_model=Dict[str, Any])
async def health_check(
):
    """
    系统健康检查

    Args:
        manager: Agent管理器实例

    Returns:
        健康状态
    """
    try:
        manager = await get_agent_manager()

        health = await manager.health_check()

        return health

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/routing/suggestions", response_model=List[Dict[str, Any]])
async def get_routing_suggestions(
    query: str
):
    """
    获取路由建议

    Args:
        query: 查询文本
        manager: Agent管理器实例

    Returns:
        路由建议列表
    """
    try:
        manager = await get_agent_manager()

        router = AgentRouter(manager)
        suggestions = await router.get_routing_suggestions(query)

        return suggestions

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/routing/rules", response_model=List[Dict[str, Any]])
async def list_routing_rules(
):
    """
    列出所有路由规则

    Args:
        manager: Agent管理器实例

    Returns:
        路由规则列表
    """
    try:
        manager = await get_agent_manager()

        router = AgentRouter(manager)
        rules = router.list_routing_rules()

        return rules

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
