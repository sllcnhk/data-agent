#!/usr/bin/env python
"""Simple backend server without complex imports"""
from fastapi import FastAPI, APIRouter
import uvicorn

app = FastAPI(
    title="Data Agent System",
    version="1.0.0",
    docs_url="/api/docs",
    redoc_url="/api/redoc"
)

# Create API router
api_router = APIRouter(prefix="/api/v1")

# Root endpoints
@app.get("/")
async def root():
    return {
        "name": "Data Agent System",
        "version": "1.0.0",
        "status": "running"
    }

@app.get("/health")
async def health():
    return {"status": "healthy"}

# API endpoints (matching frontend expectations)
@api_router.get("/")
async def api_root():
    return {
        "version": "1.0.0",
        "message": "API is running"
    }

@api_router.get("/agents")
async def get_agents():
    """Get list of agents (simplified version)"""
    return {
        "agents": [
            {
                "agent_id": "data-analyst-001",
                "agent_type": "data_analyst",
                "status": "idle",
                "completed_tasks": 0,
                "failed_tasks": 0
            },
            {
                "agent_id": "sql-expert-001",
                "agent_type": "sql_expert",
                "status": "idle",
                "completed_tasks": 0,
                "failed_tasks": 0
            },
            {
                "agent_id": "chart-builder-001",
                "agent_type": "chart_builder",
                "status": "idle",
                "completed_tasks": 0,
                "failed_tasks": 0
            }
        ]
    }

@api_router.get("/skills")
async def get_skills():
    """Get list of available skills"""
    return {
        "skills": [
            {
                "name": "数据分析",
                "description": "分析数据并生成报告",
                "category": "analysis"
            },
            {
                "name": "SQL查询",
                "description": "生成和执行SQL查询",
                "category": "database"
            },
            {
                "name": "图表生成",
                "description": "创建各种类型的图表",
                "category": "visualization"
            }
        ]
    }

@api_router.get("/tasks")
async def get_tasks():
    """Get list of tasks"""
    return {
        "tasks": []
    }

# Include API router
app.include_router(api_router)

if __name__ == "__main__":
    print("=" * 60)
    print("Data Agent System v1.0.0 - Simple Mode")
    print("=" * 60)
    print()
    print("Access URLs:")
    print("  - Frontend: http://localhost:3000")
    print("  - Backend: http://localhost:8000")
    print("  - API Docs: http://localhost:8000/api/docs")
    print()
    print("=" * 60)
    print()

    uvicorn.run(
        "run_simple:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
        log_level="info"
    )
