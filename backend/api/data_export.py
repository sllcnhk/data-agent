"""
数据导出 API — /data-export/*

GET  /data-export/connections                  可写连接列表
POST /data-export/preview                      SQL 预览（前 100 行）
POST /data-export/execute                      提交导出任务（后台执行；含 chunk_config 启用按日期分块）
GET  /data-export/jobs/{job_id}                查询任务状态
POST /data-export/jobs/{job_id}/cancel         取消任务
DELETE /data-export/jobs/{job_id}              删除任务记录（同时删除本地文件/目录）
GET  /data-export/jobs                         历史任务列表（时间倒序，分页）
GET  /data-export/jobs/{job_id}/download       下载导出文件（分块模式必带 file_index）

所有端点均需 data:export 权限（superadmin 专属）。
"""
import asyncio
import logging
import os
import shutil
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
    preview_date: Optional[str] = Field(
        default=None,
        description=(
            "样本日期（ISO YYYY-MM-DD）；仅当 SQL 含 {{date_start}}/{{date_end}} "
            "占位符时使用，留空默认昨日"
        ),
    )


class ChunkConfigSchema(BaseModel):
    """按日期分块导出配置"""
    date_column: Optional[str] = Field(
        default=None,
        description=(
            "日期列名（包装模式必填）。SQL 含 {{date_start}}/{{date_end}} "
            "占位符时可省略。仅允许字母/数字/下划线。"
        ),
    )
    date_start: str = Field(..., description="起始日期（含），ISO YYYY-MM-DD")
    date_end: str = Field(..., description="结束日期（含），ISO YYYY-MM-DD")
    chunk_days: int = Field(default=10, ge=1, le=90, description="单块天数 [1-90]")
    min_subdivide_unit: Optional[str] = Field(
        default="day",
        description=(
            "块失败自动对半再细分的最小粒度:day(默认,不下钻到 sub-day)/hour/minute。"
            "选 hour/minute 时,1 天块失败可继续拆成 12h+12h→6h+6h... 直到 1 小时或 1 分钟。"
            "仅当过滤列为 DateTime 类型时启用 hour/minute 才有效;Date 列下 sub-day 字面量"
            "会被 ClickHouse 截到 Date,导致无效细分。"
        ),
    )
    cursor_column: Optional[str] = Field(
        default=None,
        description=(
            "游标列名(可选,启用键集分页代替 LIMIT/OFFSET)。提供后,流式断开自动回退"
            "时使用 WHERE cursor > last_value ORDER BY cursor LIMIT N 推进;对大数据集"
            "大幅提速且消除 LIMIT/OFFSET 非确定性。要求列可排序且趋势单调(主键 / 时间戳)。"
            "不适用于 GROUP BY/DISTINCT 等聚合 SQL。仅允许字母/数字/下划线。"
        ),
    )


class ExecuteExportRequest(BaseModel):
    query_sql: str = Field(..., description="SELECT SQL 语句")
    connection_env: str = Field(..., description="目标连接环境名")
    connection_type: str = Field(default="clickhouse", description="连接类型")
    job_name: str = Field(default="", description="任务名称（用于文件名，留空则自动生成）")
    batch_size: int = Field(default=DEFAULT_BATCH_SIZE, ge=1000, le=200_000)
    chunk_config: Optional[ChunkConfigSchema] = Field(
        default=None,
        description="按日期分块配置；提供则启用 date_chunked 模式（多文件输出）",
    )


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
                preview_date=req.preview_date,
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

    单文件模式（不传 chunk_config）：
        输出 customer_data/{username}/exports/{name}_{ts}.xlsx

    分块模式（传 chunk_config）：
        输出目录 customer_data/{username}/exports/{job_id}/
        目录下每个日期块产出一个 xlsx 文件，文件清单写入 job.output_files

    Returns job_id 供前端轮询。
    """
    from backend.models.export_job import ExportJob
    # 提早校验 chunk_config（在创建 Job 之前抛 400，避免脏数据）
    from backend.services.data_export_chunker import validate_chunk_config

    username = getattr(current_user, "username", "default")
    user_id = str(getattr(current_user, "id", "default"))

    # 生成基础文件名（单文件模式与分块模式都基于此）
    safe_name = req.job_name.strip() if req.job_name.strip() else "export"
    safe_name = "".join(c for c in safe_name if c.isalnum() or c in "-_ ")[:50]
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")

    is_chunked = req.chunk_config is not None
    if is_chunked:
        # 提早校验 — 失败直接 400
        try:
            validate_chunk_config(
                req.chunk_config.dict(), sql=req.query_sql,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"chunk_config 校验失败: {exc}")

    # 输出路径
    export_dir = _CUSTOMER_DATA_ROOT / username / "exports"
    export_dir.mkdir(parents=True, exist_ok=True)

    if is_chunked:
        # 分块模式：每个 Job 单独一个目录（用 job_name + 时间戳避免冲突）
        sub_dir_name = f"{safe_name}_{timestamp}"
        chunked_output_dir = export_dir / sub_dir_name
        # 目录创建延后到 service 层（service 层 mkdir）
        output_filename = sub_dir_name              # 目录名（兼容现有 list 显示）
        output_path_for_job = str(chunked_output_dir)
    else:
        output_filename = f"{safe_name}_{timestamp}.xlsx"
        output_path_for_job = str(export_dir / output_filename)

    # 配置快照
    config_snapshot: Dict[str, Any] = {
        "query_sql": req.query_sql,
        "connection_env": req.connection_env,
        "connection_type": req.connection_type,
        "batch_size": req.batch_size,
    }
    if is_chunked:
        config_snapshot["chunk_config"] = req.chunk_config.dict()

    job = ExportJob(
        user_id=user_id,
        username=username,
        job_name=req.job_name or None,
        query_sql=req.query_sql,
        connection_env=req.connection_env,
        connection_type=req.connection_type,
        status="pending",
        output_filename=output_filename,
        file_path=output_path_for_job,
        export_mode="date_chunked" if is_chunked else "single",
        chunk_config=req.chunk_config.dict() if is_chunked else None,
        output_files=None,  # 分块模式由 service 层填充
        config_snapshot=config_snapshot,
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    job_id = str(job.id)

    # 后台启动导出协程
    if is_chunked:
        config: Dict[str, Any] = {
            "query_sql": req.query_sql,
            "connection_env": req.connection_env,
            "connection_type": req.connection_type,
            "batch_size": req.batch_size,
            "export_mode": "date_chunked",
            "chunk_config": req.chunk_config.dict(),
            "output_dir": output_path_for_job,
            "job_name": safe_name,
        }
    else:
        config = {
            "query_sql": req.query_sql,
            "connection_env": req.connection_env,
            "connection_type": req.connection_type,
            "batch_size": req.batch_size,
            "export_mode": "single",
            "output_path": output_path_for_job,
            "output_filename": output_filename,
        }
    task = asyncio.create_task(run_export_job(job_id, config))

    def _on_done(t: asyncio.Task):
        exc = t.exception()
        if exc:
            logger.error("[DataExport] Job %s background task failed: %s", job_id, exc, exc_info=exc)

    task.add_done_callback(_on_done)

    logger.info(
        "[DataExport] Job %s created by %s, env=%s, mode=%s",
        job_id, username, req.connection_env,
        "date_chunked" if is_chunked else "single",
    )

    return {
        "success": True,
        "data": {
            "job_id": job_id,
            "status": "pending",
            "output_filename": output_filename,
            "export_mode": "date_chunked" if is_chunked else "single",
        },
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

    # 只允许删除终态任务（完成/取消/失败），活跃任务须先取消
    _TERMINAL = {"completed", "cancelled", "failed"}
    if job.status not in _TERMINAL:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"无法删除状态为 '{job.status}' 的任务，请先取消后再删除",
        )

    # 删除本地文件 / 目录
    if job.file_path:
        target = Path(job.file_path)
        try:
            if target.is_dir():
                # 分块模式：递归删整个目录（含所有子文件）
                shutil.rmtree(target, ignore_errors=False)
            elif target.exists():
                target.unlink()
        except FileNotFoundError:
            pass
        except Exception as e:
            logger.warning("[DataExport] Failed to delete %s: %s", job.file_path, e)

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
    file_index: Optional[int] = Query(
        default=None, ge=0,
        description="分块模式下指定第 N 个文件（从 0 起）；单文件模式忽略",
    ),
    current_user=Depends(require_permission("data", "export")),
    db: Session = Depends(get_db),
):
    """
    下载导出文件。
      - 单文件模式：返回 job.file_path 对应文件
      - 分块模式：必须传 file_index 指向 output_files[i]，否则 400
    """
    from backend.models.export_job import ExportJob

    job = db.query(ExportJob).filter(ExportJob.id == job_id).first()
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"任务不存在: {job_id}")

    export_mode = getattr(job, "export_mode", None) or "single"

    # 分块模式：必须有 file_index，且块状态须为 completed
    if export_mode == "date_chunked":
        files = job.output_files or []
        if file_index is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="分块模式必须提供 file_index 查询参数（从 0 起）",
            )
        if file_index < 0 or file_index >= len(files):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"file_index {file_index} 超出范围（共 {len(files)} 个文件）",
            )
        entry = files[file_index]
        if entry.get("status") != "completed":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"该分块文件状态为 '{entry.get('status')}'，无法下载",
            )
        fpath = entry.get("file_path")
        if not fpath or not Path(fpath).exists():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="分块文件不存在（可能已被清理）",
            )
        filename = entry.get("filename") or Path(fpath).name
        return FileResponse(
            path=fpath,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            filename=filename,
        )

    # 单文件模式
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
