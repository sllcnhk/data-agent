"""
数据导入 API — /data-import/*

GET  /data-import/connections                          可写连接列表
GET  /data-import/connections/{env}/databases          数据库列表
GET  /data-import/connections/{env}/databases/{db}/tables  表列表
POST /data-import/upload                               上传 Excel，返回 Sheet 预览
POST /data-import/execute                              提交导入任务（后台执行）
GET  /data-import/jobs/{job_id}                        查询任务状态
GET  /data-import/jobs                                 历史任务列表（时间倒序，分页）

所有端点均需 data:import 权限（superadmin 专属）。
"""
import asyncio
import logging
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.api.deps import get_current_user, require_permission
from backend.config.database import get_db
from backend.config.settings import settings
from backend.services.data_import_service import (
    DEFAULT_BATCH_SIZE,
    MAX_FILE_SIZE,
    list_writable_connections,
    list_databases,
    list_tables,
    parse_excel_preview,
    run_import_job,
)

router = APIRouter(prefix="/data-import", tags=["数据导入"])
logger = logging.getLogger(__name__)

# 临时文件根目录（在 customer_data/{username}/imports/ 下）
_CUSTOMER_DATA_ROOT: Path = (
    Path(settings.allowed_directories[0])
    if settings.allowed_directories
    else Path("customer_data")
)


# ─────────────────────────────────────────────────────────────────────────────
# Pydantic schemas
# ─────────────────────────────────────────────────────────────────────────────

class SheetConfig(BaseModel):
    sheet_name: str
    database: str
    table: str
    has_header: bool = True
    enabled: bool = True


class ExecuteImportRequest(BaseModel):
    upload_id: str = Field(..., description="上传接口返回的 upload_id")
    connection_env: str = Field(..., description="目标 ClickHouse 环境名")
    batch_size: int = Field(default=DEFAULT_BATCH_SIZE, ge=100, le=50000)
    sheets: List[SheetConfig]


# ─────────────────────────────────────────────────────────────────────────────
# 1. 连接列表
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/connections")
async def get_connections(
    current_user=Depends(require_permission("data", "import")),
):
    """返回所有可写（非只读）ClickHouse 连接"""
    try:
        conns = list_writable_connections()
        return {"success": True, "data": conns}
    except Exception as e:
        logger.error("list_writable_connections error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ─────────────────────────────────────────────────────────────────────────────
# 2. Schema / Table 查询
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/connections/{env}/databases")
async def get_databases(
    env: str,
    current_user=Depends(require_permission("data", "import")),
):
    """查询指定环境的数据库列表"""
    try:
        dbs = list_databases(env)
        return {"success": True, "data": dbs}
    except Exception as e:
        logger.error("list_databases(%s) error: %s", env, e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/connections/{env}/databases/{db}/tables")
async def get_tables(
    env: str,
    db: str,
    current_user=Depends(require_permission("data", "import")),
):
    """查询指定环境和数据库的表列表"""
    try:
        tables = list_tables(env, db)
        return {"success": True, "data": tables}
    except Exception as e:
        logger.error("list_tables(%s, %s) error: %s", env, db, e)
        raise HTTPException(status_code=500, detail=str(e))


# ─────────────────────────────────────────────────────────────────────────────
# 3. Excel 上传 + 预览
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/upload")
async def upload_excel(
    file: UploadFile = File(...),
    current_user=Depends(require_permission("data", "import")),
):
    """
    接收 Excel 文件，保存为临时文件，返回 Sheet 预览信息。

    - 文件大小上限 100 MB
    - 保存路径：customer_data/{username}/imports/{upload_id}.xlsx
    - 返回 upload_id（供后续 execute 使用）
    """
    # 文件类型检查
    if not file.filename or not file.filename.lower().endswith((".xlsx", ".xls")):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="仅支持 .xlsx 或 .xls 文件",
        )

    # 读取文件内容（检查大小）
    content = await file.read()
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"文件大小超出限制（最大 {MAX_FILE_SIZE // 1024 // 1024} MB）",
        )

    # 保存到临时目录
    username = getattr(current_user, "username", "default")
    import_dir = _CUSTOMER_DATA_ROOT / username / "imports"
    import_dir.mkdir(parents=True, exist_ok=True)

    upload_id = str(uuid.uuid4())
    # 保留原始扩展名（统一为 .xlsx 或 .xls）
    suffix = Path(file.filename).suffix.lower()
    tmp_path = import_dir / f"{upload_id}{suffix}"

    try:
        tmp_path.write_bytes(content)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"文件保存失败: {e}")

    # 解析预览
    try:
        sheets = parse_excel_preview(str(tmp_path))
    except Exception as e:
        # 解析失败 → 删除临时文件
        try:
            tmp_path.unlink()
        except Exception:
            pass
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Excel 文件解析失败: {e}",
        )

    return {
        "success": True,
        "data": {
            "upload_id": upload_id,
            "filename": file.filename,
            "file_size": len(content),
            "sheets": sheets,
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
# 4. 提交导入任务
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/execute")
async def execute_import(
    req: ExecuteImportRequest,
    current_user=Depends(require_permission("data", "import")),
    db: Session = Depends(get_db),
):
    """
    提交导入任务：创建 ImportJob 记录，后台启动 run_import_job 协程。

    Returns job_id 供前端轮询。
    """
    from backend.models.import_job import ImportJob

    username = getattr(current_user, "username", "default")
    user_id = str(getattr(current_user, "id", "default"))

    # 定位临时文件
    import_dir = _CUSTOMER_DATA_ROOT / username / "imports"
    # 尝试 .xlsx / .xls
    file_path: Optional[Path] = None
    for suffix in (".xlsx", ".xls"):
        p = import_dir / f"{req.upload_id}{suffix}"
        if p.exists():
            file_path = p
            break

    if file_path is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"上传文件不存在（upload_id={req.upload_id}），请重新上传",
        )

    # 验证至少有一个启用的 sheet
    enabled_sheets = [s for s in req.sheets if s.enabled]
    if not enabled_sheets:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="至少需要启用一个 Sheet",
        )

    # 创建 ImportJob 记录
    job = ImportJob(
        user_id=user_id,
        username=username,
        upload_id=req.upload_id,
        filename=file_path.name,
        connection_env=req.connection_env,
        status="pending",
        config_snapshot={
            "connection_env": req.connection_env,
            "batch_size": req.batch_size,
            "sheets": [s.model_dump() for s in req.sheets],
        },
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    job_id = str(job.id)

    # 后台启动导入协程
    config = {
        "file_path": str(file_path),
        "connection_env": req.connection_env,
        "batch_size": req.batch_size,
        "sheets": [s.model_dump() for s in req.sheets],
    }
    task = asyncio.create_task(run_import_job(job_id, config))

    def _on_task_done(t: asyncio.Task):
        exc = t.exception()
        if exc:
            logger.error("[DataImport] Job %s background task failed: %s", job_id, exc, exc_info=exc)

    task.add_done_callback(_on_task_done)

    logger.info("[DataImport] Job %s created by %s for env=%s", job_id, username, req.connection_env)

    return {
        "success": True,
        "data": {
            "job_id": job_id,
            "status": "pending",
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
# 5. 任务状态查询
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/jobs/{job_id}")
async def get_job_status(
    job_id: str,
    current_user=Depends(require_permission("data", "import")),
    db: Session = Depends(get_db),
):
    """轮询导入任务状态与进度"""
    from backend.models.import_job import ImportJob

    job = db.query(ImportJob).filter(ImportJob.id == job_id).first()
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"任务不存在: {job_id}",
        )

    return {"success": True, "data": job.to_dict()}


# ─────────────────────────────────────────────────────────────────────────────
# 6. 删除任务记录
# ─────────────────────────────────────────────────────────────────────────────

@router.delete("/jobs/{job_id}")
async def delete_job(
    job_id: str,
    current_user=Depends(require_permission("data", "import")),
    db: Session = Depends(get_db),
):
    """删除指定导入任务记录（不影响已导入的数据）"""
    from backend.models.import_job import ImportJob

    job = db.query(ImportJob).filter(ImportJob.id == job_id).first()
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"任务不存在: {job_id}",
        )
    db.delete(job)
    db.commit()
    return {"success": True}


# ─────────────────────────────────────────────────────────────────────────────
# 7. 历史任务列表
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/jobs")
async def list_jobs(
    page: int = Query(default=1, ge=1, description="页码（从 1 开始）"),
    page_size: int = Query(default=10, ge=1, le=100, description="每页条数"),
    current_user=Depends(require_permission("data", "import")),
    db: Session = Depends(get_db),
):
    """
    历史导入任务列表，按创建时间倒序排列。

    默认第 1 页，每页 10 条。
    """
    from backend.models.import_job import ImportJob

    offset = (page - 1) * page_size
    total = db.query(ImportJob).count()
    jobs = (
        db.query(ImportJob)
        .order_by(ImportJob.created_at.desc())
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
