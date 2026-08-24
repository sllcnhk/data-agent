"""
小工具 - 合并Excel文件 API — /tools/merge-excel/*

POST   /tools/merge-excel/upload             上传源文件（多文件，逐个调用）
POST   /tools/merge-excel/execute            提交合并任务（后台执行）
GET    /tools/merge-excel/jobs/{job_id}      查询任务状态
POST   /tools/merge-excel/jobs/{job_id}/cancel  取消任务
DELETE /tools/merge-excel/jobs/{job_id}      删除任务记录（同时删除本地文件）
GET    /tools/merge-excel/jobs               历史任务列表（时间倒序，分页）
GET    /tools/merge-excel/jobs/{job_id}/download  下载合并结果文件

所有端点均需 tools:merge_excel 权限（superadmin 专属）。
"""
import asyncio
import logging
import uuid
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.api.deps import require_permission
from backend.config.database import get_db
from backend.config.settings import settings
from backend.services.excel_merge_service import run_merge_job

router = APIRouter(prefix="/tools/merge-excel", tags=["小工具-合并Excel"])
logger = logging.getLogger(__name__)

_CUSTOMER_DATA_ROOT: Path = (
    Path(settings.allowed_directories[0])
    if settings.allowed_directories
    else Path("customer_data")
)

_UPLOAD_CHUNK_SIZE = 1 * 1024 * 1024  # 1 MB per chunk
_MAX_FILE_SIZE = 200 * 1024 * 1024    # 200 MB / 文件


# ─────────────────────────────────────────────────────────────────────────────
# Pydantic schemas
# ─────────────────────────────────────────────────────────────────────────────

class ExecuteMergeRequest(BaseModel):
    file_ids: List[str] = Field(..., min_length=2, description="已上传文件的 upload_id 列表（至少 2 个）")
    has_header: bool = Field(default=True, description="源文件是否包含表头")
    job_name: Optional[str] = Field(default=None, description="任务名称（用于输出文件名）")


# ─────────────────────────────────────────────────────────────────────────────
# 1. 上传源文件
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/upload")
async def upload_excel(
    file: UploadFile = File(...),
    current_user=Depends(require_permission("tools", "merge_excel")),
):
    """
    接收单个 Excel 文件，保存到 customer_data/{username}/tools/merge_excel/uploads/。
    前端对多选的文件逐个调用本接口，汇总返回的 upload_id 供 /execute 使用。
    """
    if not file.filename or not file.filename.lower().endswith((".xlsx", ".xls")):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="仅支持 .xlsx 或 .xls 文件",
        )

    username = getattr(current_user, "username", "default")
    upload_dir = _CUSTOMER_DATA_ROOT / username / "tools" / "merge_excel" / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)

    upload_id = str(uuid.uuid4())
    suffix = Path(file.filename).suffix.lower()
    tmp_path = upload_dir / f"{upload_id}{suffix}"

    file_size = 0
    try:
        with tmp_path.open("wb") as fp:
            while True:
                chunk = await file.read(_UPLOAD_CHUNK_SIZE)
                if not chunk:
                    break
                file_size += len(chunk)
                if file_size > _MAX_FILE_SIZE:
                    fp.close()
                    tmp_path.unlink(missing_ok=True)
                    raise HTTPException(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail=f"文件大小超出限制（最大 {_MAX_FILE_SIZE // 1024 // 1024} MB）",
                    )
                fp.write(chunk)
    except HTTPException:
        raise
    except Exception as e:
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass
        raise HTTPException(status_code=500, detail=f"文件保存失败: {e}")

    return {
        "success": True,
        "data": {
            "upload_id": upload_id,
            "filename": file.filename,
            "file_path": str(tmp_path),
            "file_size": file_size,
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
# 2. 提交合并任务
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/execute")
async def execute_merge(
    req: ExecuteMergeRequest,
    current_user=Depends(require_permission("tools", "merge_excel")),
    db: Session = Depends(get_db),
):
    from backend.models.merge_excel_job import MergeExcelJob

    username = getattr(current_user, "username", "default")
    user_id = str(getattr(current_user, "id", username))
    upload_dir = _CUSTOMER_DATA_ROOT / username / "tools" / "merge_excel" / "uploads"

    source_files = []
    for upload_id in req.file_ids:
        matches = list(upload_dir.glob(f"{upload_id}.*"))
        if not matches:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"上传文件不存在或已过期: {upload_id}",
            )
        fpath = matches[0]
        source_files.append({
            "filename": fpath.name,
            "file_path": str(fpath),
            "size": fpath.stat().st_size,
        })

    job = MergeExcelJob(
        user_id=user_id,
        username=username,
        job_name=req.job_name,
        has_header=req.has_header,
        source_files=source_files,
        status="pending",
        total_files=len(source_files),
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    job_id = str(job.id)
    task = asyncio.create_task(run_merge_job(job_id))

    def _on_done(t: asyncio.Task):
        if t.cancelled():
            logger.warning("[MergeExcel] Job %s background task cancelled", job_id)
            return
        exc = t.exception()
        if exc:
            logger.error("[MergeExcel] Job %s background task failed: %s", job_id, exc, exc_info=exc)

    task.add_done_callback(_on_done)

    logger.info("[MergeExcel] Job %s created by %s, %d files", job_id, username, len(source_files))

    return {"success": True, "data": {"job_id": job_id, "status": "pending"}}


# ─────────────────────────────────────────────────────────────────────────────
# 3. 任务状态查询
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/jobs/{job_id}")
async def get_job_status(
    job_id: str,
    current_user=Depends(require_permission("tools", "merge_excel")),
    db: Session = Depends(get_db),
):
    from backend.models.merge_excel_job import MergeExcelJob

    job = db.query(MergeExcelJob).filter(MergeExcelJob.id == job_id).first()
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"任务不存在: {job_id}")
    return {"success": True, "data": job.to_dict()}


# ─────────────────────────────────────────────────────────────────────────────
# 4. 取消任务
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/jobs/{job_id}/cancel")
async def cancel_job(
    job_id: str,
    current_user=Depends(require_permission("tools", "merge_excel")),
    db: Session = Depends(get_db),
):
    from backend.models.merge_excel_job import MergeExcelJob

    job = db.query(MergeExcelJob).filter(MergeExcelJob.id == job_id).first()
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"任务不存在: {job_id}")
    if job.status not in ("pending", "running"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"任务当前状态 '{job.status}' 不可取消",
        )

    now = datetime.utcnow()
    if job.status == "pending":
        job.status = "cancelled"
        job.finished_at = now
    else:
        job.status = "cancelling"
    job.updated_at = now
    db.commit()

    return {"success": True, "data": {"status": job.status}}


# ─────────────────────────────────────────────────────────────────────────────
# 5. 删除任务记录
# ─────────────────────────────────────────────────────────────────────────────

@router.delete("/jobs/{job_id}")
async def delete_job(
    job_id: str,
    current_user=Depends(require_permission("tools", "merge_excel")),
    db: Session = Depends(get_db),
):
    from backend.models.merge_excel_job import MergeExcelJob

    job = db.query(MergeExcelJob).filter(MergeExcelJob.id == job_id).first()
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"任务不存在: {job_id}")

    _TERMINAL = {"completed", "cancelled", "failed"}
    if job.status not in _TERMINAL:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"无法删除状态为 '{job.status}' 的任务，请先取消后再删除",
        )

    if job.file_path:
        try:
            target = Path(job.file_path)
            if target.exists():
                target.unlink()
        except Exception as e:
            logger.warning("[MergeExcel] Failed to delete %s: %s", job.file_path, e)

    db.delete(job)
    db.commit()
    return {"success": True}


# ─────────────────────────────────────────────────────────────────────────────
# 6. 历史任务列表
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/jobs")
async def list_jobs(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=100),
    current_user=Depends(require_permission("tools", "merge_excel")),
    db: Session = Depends(get_db),
):
    from backend.models.merge_excel_job import MergeExcelJob

    offset = (page - 1) * page_size
    total = db.query(MergeExcelJob).count()
    jobs = (
        db.query(MergeExcelJob)
        .order_by(MergeExcelJob.created_at.desc())
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
# 7. 下载合并结果文件
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/jobs/{job_id}/download")
async def download_job(
    job_id: str,
    current_user=Depends(require_permission("tools", "merge_excel")),
    db: Session = Depends(get_db),
):
    from backend.models.merge_excel_job import MergeExcelJob

    job = db.query(MergeExcelJob).filter(MergeExcelJob.id == job_id).first()
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"任务不存在: {job_id}")
    if job.status != "completed":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"任务尚未完成（当前状态: {job.status}），无法下载",
        )
    if not job.file_path or not Path(job.file_path).exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="合并结果文件不存在（可能已被清理）")

    return FileResponse(
        path=job.file_path,
        filename=job.output_filename or Path(job.file_path).name,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
