"""
FastAPI主应用

数据智能分析Agent系统入口
"""
from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
import uvicorn
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
from contextlib import asynccontextmanager

# 确保 logs 目录存在
logs_dir = Path(__file__).parent.parent / "logs"
logs_dir.mkdir(exist_ok=True)

# 配置日志 - 同时输出到控制台和文件
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        # 控制台处理器
        logging.StreamHandler(),
        # 文件处理器 - 轮转日志，最大10MB，保留5个备份
        RotatingFileHandler(
            logs_dir / "backend.log",
            maxBytes=10*1024*1024,  # 10MB
            backupCount=5,
            encoding='utf-8'
        )
    ]
)
logger = logging.getLogger(__name__)


# Lifespan 事件处理器
@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    应用生命周期管理
    """
    # 启动逻辑
    logger.info("Starting Data Agent System...")

    # 初始化MCP服务器
    logger.info("Initializing MCP servers...")
    try:
        from backend.mcp.manager import initialize_mcp_servers
        await initialize_mcp_servers()
        logger.info("MCP servers initialized successfully")
    except Exception as e:
        logger.error(f"Failed to initialize MCP servers: {e}")
        # 不阻断启动流程

    # 初始化Agent管理器
    from backend.agents import get_agent_manager
    manager = await get_agent_manager()

    logger.info("Agent Manager initialized")

    # 创建默认Agent
    from backend.agents import (
        DataAnalystAgent,
        SQLExpertAgent,
        ChartBuilderAgent,
        ETLEngineerAgent,
        GeneralistAgent
    )

    default_agents = [
        DataAnalystAgent("data-analyst-001"),
        SQLExpertAgent("sql-expert-001"),
        ChartBuilderAgent("chart-builder-001"),
        ETLEngineerAgent("etl-engineer-001"),
        GeneralistAgent("generalist-001")
    ]

    for agent in default_agents:
        await manager.register_agent(agent)
        logger.info(f"Created agent: {agent.agent_id}")

    # 恢复孤儿任务（服务重启后，非终态的 import/export 任务已无活跃协程）
    try:
        from backend.config.database import SessionLocal
        from backend.models.import_job import ImportJob
        from backend.models.export_job import ExportJob
        from datetime import datetime as _dt
        _db = SessionLocal()
        try:
            _now = _dt.utcnow()
            _total_interrupted = 0
            _total_cancelling = 0
            for _Model, _label in [(ImportJob, "import"), (ExportJob, "export")]:
                _interrupted = _db.query(_Model).filter(
                    _Model.status.in_(["pending", "running"])
                ).all()
                for _j in _interrupted:
                    _j.status = "failed"
                    _j.error_message = "服务重启，任务已中断"
                    _j.finished_at = _now
                    _j.updated_at = _now
                _cancelling = _db.query(_Model).filter(
                    _Model.status == "cancelling"
                ).all()
                for _j in _cancelling:
                    _j.status = "cancelled"
                    _j.finished_at = _now
                    _j.updated_at = _now
                _total_interrupted += len(_interrupted)
                _total_cancelling += len(_cancelling)
            if _total_interrupted or _total_cancelling:
                _db.commit()
                logger.info(
                    "Startup recovery: %d interrupted → failed, %d cancelling → cancelled",
                    _total_interrupted, _total_cancelling,
                )
        except Exception as _e:
            _db.rollback()
            logger.warning("Job startup recovery failed (non-critical): %s", _e)
        finally:
            _db.close()
    except Exception as _e:
        logger.warning("Could not run job startup recovery: %s", _e)

    # 启动 SKILL.md 热加载文件监视器
    try:
        from backend.skills.skill_loader import reload_skills
        from backend.skills.skill_watcher import start_skill_watcher
        _initial_skills = reload_skills()
        logger.info(f"Loaded {len(_initial_skills)} skill(s): {[s.name for s in _initial_skills]}")
        _skill_watcher = start_skill_watcher()
        if _skill_watcher.is_running:
            logger.info("Skill file hot-reload watcher started")
    except Exception as e:
        logger.warning(f"Skill watcher startup warning (non-critical): {e}")

    logger.info("System startup complete")

    yield

    # 关闭逻辑
    logger.info("Shutting down Data Agent System...")

    # 停止技能文件监视器
    try:
        from backend.skills.skill_watcher import stop_skill_watcher
        stop_skill_watcher()
    except Exception:
        pass

    # 关闭Agent管理器
    from backend.agents import shutdown_agent_manager
    await shutdown_agent_manager()

    logger.info("System shutdown complete")


# 创建FastAPI应用
app = FastAPI(
    title="数据智能分析Agent系统",
    description="基于多Agent的数据分析平台，提供数据库查询、数据分析、SQL生成、图表构建、ETL设计等功能",
    version="1.0.0",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    lifespan=lifespan
)

# 添加中间件
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 生产环境中应该限制具体域名
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(GZipMiddleware, minimum_size=1000)

# 导入路由
from api import agents, skills, conversations, llm_configs, mcp, groups, approvals, auth, users, files, data_import, data_export
from api.users import roles_router, permissions_router


# 健康检查端点
@app.get("/health", tags=["health"])
async def health_check():
    """
    系统健康检查
    """
    return {
        "status": "healthy",
        "version": "1.0.0",
        "timestamp": logging.currentframe().f_code.co_name
    }


# 注册路由
app.include_router(agents.router, prefix="/api/v1")
app.include_router(skills.router, prefix="/api/v1")
app.include_router(conversations.router, prefix="/api/v1")
app.include_router(groups.router, prefix="/api/v1")
app.include_router(llm_configs.router, prefix="/api/v1")
app.include_router(mcp.router, prefix="/api/v1")
app.include_router(approvals.router, prefix="/api/v1")
app.include_router(auth.router, prefix="/api/v1")
app.include_router(users.router, prefix="/api/v1")
app.include_router(roles_router, prefix="/api/v1")
app.include_router(permissions_router, prefix="/api/v1")
app.include_router(files.router, prefix="/api/v1")
app.include_router(data_import.router, prefix="/api/v1")
app.include_router(data_export.router, prefix="/api/v1")


# 全局异常处理器
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """
    全局异常处理
    """
    logger.error(f"Unhandled exception: {str(exc)}", exc_info=True)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "success": False,
            "error": "内部服务器错误",
            "detail": str(exc) if app.debug else "请查看服务器日志"
        }
    )


# 根路径
@app.get("/", tags=["root"])
async def root():
    """
    根路径
    """
    return {
        "name": "数据智能分析Agent系统",
        "version": "1.0.0",
        "description": "基于多Agent的数据分析平台",
        "docs": "/api/docs",
        "redoc": "/api/redoc",
        "health": "/health"
    }


# API信息
@app.get("/api", tags=["api"])
async def api_info():
    """
    API信息
    """
    return {
        "version": "1.0.0",
        "endpoints": {
            "agents": "/api/v1/agents",
            "skills": "/api/v1/skills",
            "health": "/health"
        },
        "documentation": {
            "swagger": "/api/docs",
            "redoc": "/api/redoc"
        }
    }


if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info"
    )
