"""
数据导出 API — /data-export/*

GET  /data-export/connections                  可写连接列表
POST /data-export/preview                      SQL 预览（前 100 行）
POST /data-export/execute                      提交导出任务（后台执行）
GET  /data-export/jobs/{job_id}                查询任务状态
POST /data-export/jobs/{job_id}/cancel         取消任务
DELETE /data-export/jobs/{job_id}              删除任务记录（同时删除本地文件）
GET  /data-export/jobs                         历史任务列表（时间倒序，分页）
GET  /data-export/jobs/{job_id}/download       下载导出文件

所有端点均需 data:export 权限（superadmin 专属）。
"""
import asyncio
import logging
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.api.deps import get_current_user, require_permission
from backend.config.database import get_db
from backend.config.settings import settings
from backend.services.data_export_service import (
    DEFAULT_BATCH_SIZE,
    PREVIEW_LIMIT,
    preview_query,
    run_export_job,
)
from backend.services.data_import_service import list_writable_connections

router = APIRouter(prefix="/data-export", tags=["数据导出"])
logger = logging.getLogger(__name__)

# 导出文件根目录（customer_data/{username}/exports/）
_CUSTOMER_DATA_ROOT: Path = (
    Path(settings.allowed_directories[0])
    if settings.allowed_directories
    else Path("customer_data")
)


# ─────────────────────────────────────────────────────────────────────────────
# Pydantic schemas
# ─────────────────────────────────────────────────────────────────────────────

class PreviewRequest(BaseModel):
    query_sql: str = Field(..., description="SELECT SQL 语句")
    connection_env: str = Field(..., description="目标连接环境名")
    connection_type: str = Field(default="clickhouse", description="连接类型")
    limit: int = Field(default=PREVIEW_LIMIT, ge=1, le=500)


class ExecuteExportRequest(BaseModel):
    query_sql: str = Field(..., description="SELECT SQL 语句")
    connection_env: str = Field(..., description="目标连接环境名")
    connection_type: str = Field(default="clickhouse", description="连接类型")
    job_name: str = Field(default="", description="任务名称（用于文件名，留空则自动生成）")
    batch_size: int = Field(default=DEFAULT_BATCH_SIZE, ge=1000, le=200_000)


# ─────────────────────────────────────────────────────────────────────────────
# 1. 连接列表（复用 import 的 list_writable_connections）
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/connections")
async def get_connections(
    current_user=Depends(require_permission("data", "export")),
):
    """返回所有可写（非只读）连接列表"""
    try:
        conns = list_writable_connections()
        return {"success": True, "data": conns}
    except Exception as e:
        logger.error("list_writable_connections error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ─────────────────────────────────────────────────────────────────────────────
# 2. SQL 预览
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/preview")
async def preview(
    req: PreviewRequest,
    current_user=Depends(require_permission("data", "export")),
):
    """
    执行 SQL（加 LIMIT），返回列信息和前 N 行数据。
    在线程池中执行，避免阻塞事件循环。
    """
    import asyncio
    import concurrent.futures

    loop = asyncio.get_event_loop()
    try:
        result = await loop.run_in_executor(
            None,
            lambda: preview_query(
                sql=req.query_sql,
                env=req.connection_env,
                connection_type=req.connection_type,
                limit=req.limit,
            ),
        )
        return {"success": True, "data": result}
    except Exception as e:
        logger.error("preview_query error: %s", e)
        raise HTTPException(status_code=400, detail=str(e))


# ─────────────────────────────────────────────────────────────────────────────
# 3. 提交导出任务
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/execute")
async def execute_export(
    req: ExecuteExportRequest,
    current_user=Depends(require_permission("data", "export")),
    db: Session = Depends(get_db),
):
    """
    提交导出任务：创建 ExportJob 记录，后台启动 run_export_job 协程。
    Returns job_id 供前端轮询。
    """
    from backend.models.export_job import ExportJob

    username = getattr(current_user, "username", "default")
    user_id = str(getattr(current_user, "id", "default"))

    # 生成文件名
    safe_name = req.job_name.strip() if req.job_name.strip() else "export"
    # 去掉不安全字符
    safe_name = "".join(c for c in safe_name if c.isalnum() or c in "-_ ")[:50]
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    output_filename = f"{safe_name}_{timestamp}.xlsx"

    # 输出路径：customer_data/{username}/exports/{filename}
    export_dir = _CUSTOMER_DATA_ROOT / username / "exports"
    export_dir.mkdir(parents=True, exist_ok=True)
    output_path = export_dir / output_filename

    # 创建 ExportJob 记录
    job = ExportJob(
        user_id=user_id,
        username=username,
        job_name=req.job_name or None,
        query_sql=req.query_sql,
        connection_env=req.connection_env,
        connection_type=req.connection_type,
        status="pending",
        output_filename=output_filename,
        file_path=str(output_path),
        config_snapshot={
            "query_sql": req.query_sql,
            "connection_env": req.connection_env,
            "connection_type": req.connection_type,
            "batch_size": req.batch_size,
        },
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    job_id = str(job.id)

    # 后台启动导出协程
    config = {
        "query_sql": req.query_sql,
        "connection_env": req.connection_env,
        "connection_type": req.connection_type,
        "batch_size": req.batch_size,
        "output_path": str(output_path),
        "output_filename": output_filename,
    }
    task = asyncio.create_task(run_export_job(job_id, config))

    def _on_done(t: asyncio.Task):
        exc = t.exception()
        if exc:
            logger.error("[DataExport] Job %s background task failed: %s", job_id, exc, exc_info=exc)

    task.add_done_callback(_on_done)

    logger.info("[DataExport] Job %s created by %s, env=%s", job_id, username, req.connection_env)

    return {
        "success": True,
        "data": {"job_id": job_id, "status": "pending", "output_filename": output_filename},
    }


# ─────────────────────────────────────────────────────────────────────────────
# 4. 任务状态查询
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/jobs/{job_id}")
async def get_job_status(
    job_id: str,
    current_user=Depends(require_permission("data", "export")),
    db: Session = Depends(get_db),
):
    """轮询导出任务状态与进度"""
    from backend.models.export_job import ExportJob

    job = db.query(ExportJob).filter(ExportJob.id == job_id).first()
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"任务不存在: {job_id}")
    return {"success": True, "data": job.to_dict()}


# ─────────────────────────────────────────────────────────────────────────────
# 5. 取消任务
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/jobs/{job_id}/cancel")
async def cancel_job(
    job_id: str,
    current_user=Depends(require_permission("data", "export")),
    db: Session = Depends(get_db),
):
    """
    取消导出任务。
    - pending → 直接 cancelled（无活跃协程，无需经过 cancelling 中间态）
    - running → cancelling（后台协程下一批次检测后退出）
    """
    from backend.models.export_job import ExportJob

    job = db.query(ExportJob).filter(ExportJob.id == job_id).first()
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"任务不存在: {job_id}")
    if job.status not in ("pending", "running"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"任务当前状态 '{job.status}' 不可取消",
        )

    now = datetime.utcnow()
    if job.status == "pending":
        # pending 尚未被协程持有，直接终态
        job.status = "cancelled"
        job.finished_at = now
    else:
        # running → 通知协程取消
        job.status = "cancelling"

    job.updated_at = now
    db.commit()

    return {"success": True, "data": {"status": job.status}}


# ─────────────────────────────────────────────────────────────────────────────
# 6. 删除任务记录
# ─────────────────────────────────────────────────────────────────────────────

@router.delete("/jobs/{job_id}")
async def delete_job(
    job_id: str,
    current_user=Depends(require_permission("data", "export")),
    db: Session = Depends(get_db),
):
    """删除导出任务记录，同时删除本地 Excel 文件（若存在）"""
    from backend.models.export_job import ExportJob

    job = db.query(ExportJob).filter(ExportJob.id == job_id).first()
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"任务不存在: {job_id}")

    # 删除本地文件
    if job.file_path:
        try:
            os.unlink(job.file_path)
        except FileNotFoundError:
            pass
        except Exception as e:
            logger.warning("[DataExport] Failed to delete file %s: %s", job.file_path, e)

    db.delete(job)
    db.commit()
    return {"success": True}


# ─────────────────────────────────────────────────────────────────────────────
# 7. 历史任务列表
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/jobs")
async def list_jobs(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=100),
    current_user=Depends(require_permission("data", "export")),
    db: Session = Depends(get_db),
):
    """历史导出任务列表，按创建时间倒序排列"""
    from backend.models.export_job import ExportJob

    offset = (page - 1) * page_size
    total = db.query(ExportJob).count()
    jobs = (
        db.query(ExportJob)
        .order_by(ExportJob.created_at.desc())
        .offset(offset)
        .limit(page_size)
        .all()
    )

    return {
        "success": True,
        "data": {
            "total": total,
            "page": page,
            "page_size": page_size,
            "items": [j.to_dict() for j in jobs],
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
# 8. 下载文件
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/jobs/{job_id}/download")
async def download_job(
    job_id: str,
    current_user=Depends(require_permission("data", "export")),
    db: Session = Depends(get_db),
):
    """
    下载已完成的导出文件。
    返回 FileResponse，浏览器触发另存为对话框。
    """
    from backend.models.export_job import ExportJob

    job = db.query(ExportJob).filter(ExportJob.id == job_id).first()
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"任务不存在: {job_id}")
    if job.status != "completed":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"任务尚未完成（当前状态: {job.status}），无法下载",
        )
    if not job.file_path or not Path(job.file_path).exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="导出文件不存在（可能已被清理）",
        )

    filename = job.output_filename or Path(job.file_path).name
    return FileResponse(
        path=job.file_path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=filename,
    )
