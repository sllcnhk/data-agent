"""
小工具 - 合并CSV文件 API — /tools/merge-csv/*

四个并列的选文件入口：
    GET    /tools/merge-csv/export-jobs        ① 按导出任务（含分块与对账行数）
    GET    /tools/merge-csv/dirs               ② 按目录
    GET    /tools/merge-csv/files              ③ 手工挑文件
    POST   /tools/merge-csv/upload             ④ 本地上传（上限 1 GB）

任务生命周期：
    POST   /tools/merge-csv/preview            提交前预检（跑完全部校验，不建 job）
    POST   /tools/merge-csv/execute            提交合并任务（后台串行执行）
    GET    /tools/merge-csv/jobs               历史任务列表（时间倒序，分页）
    GET    /tools/merge-csv/jobs/{job_id}      查询任务状态
    POST   /tools/merge-csv/jobs/{id}/cancel   取消（回退到上一个文件边界，保留结果）
    DELETE /tools/merge-csv/jobs/{job_id}      删除任务记录 + 结果文件
    GET    /tools/merge-csv/jobs/{id}/download 流式下载结果

所有端点均需 tools:merge_csv 权限（superadmin 专属）。
"""
import asyncio
import functools
import logging
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.api.deps import require_permission
from backend.config.database import get_db
from backend.services import csv_merge_service as svc

router = APIRouter(prefix="/tools/merge-csv", tags=["小工具-合并CSV"])
logger = logging.getLogger(__name__)

_UPLOAD_CHUNK_SIZE = 4 * 1024 * 1024              # 4 MB per chunk
_MAX_FILE_SIZE = 1024 * 1024 * 1024               # 1 GB / 文件（Excel 工具是 200 MB）
_TERMINAL = {"completed", "cancelled", "failed"}


# ─────────────────────────────────────────────────────────────────────────────
# Schemas
# ─────────────────────────────────────────────────────────────────────────────

class MergeCsvRequest(BaseModel):
    """预检与提交共用的请求体。

    server_paths 与 upload_ids 可以**同时**给 —— 需求方确认四个入口并列、
    已选清单允许混合来源。
    """
    server_paths: List[str] = Field(default_factory=list, description="服务器端 CSV 绝对路径（入口①②③）")
    upload_ids: List[str] = Field(default_factory=list, description="已上传文件的 upload_id（入口④）")
    has_header: bool = Field(default=True, description="源文件是否包含表头")
    strict_header: bool = Field(default=True, description="表头文字不一致时是否阻断")
    sort_mode: str = Field(default="natural", description="natural（默认）| lexicographic")
    job_name: Optional[str] = Field(default=None, description="任务名称（用于输出文件名）")
    allow_active_files: bool = Field(
        default=False, description="放行「来源未知且刚被修改」的文件（仅影响 warning）"
    )


async def _in_thread(fn, *args, **kwargs):
    """把阻塞调用丢到默认线程池。

    不用 `asyncio.to_thread` —— 它是 Python 3.9+ 才有的，本项目跑在 3.8。
    `run_in_executor` 不接受 kwargs，所以用 partial 包一层。
    """
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, functools.partial(fn, *args, **kwargs))


def _username(current_user) -> str:
    return getattr(current_user, "username", "default")


_UNSAFE_NAME_CHARS = '\\/:*?"<>|'


def _safe_upload_id(uid: str) -> str:
    """upload_id 必须是合法 UUID —— 它会被拼进文件系统路径。"""
    try:
        return str(uuid.UUID(str(uid)))
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=f"非法 upload_id: {uid}"
        )


def _safe_filename(name: str) -> str:
    """把客户端给的文件名收敛成一个安全的裸文件名。

    `Path(name).name` 先剥掉任何目录成分（含 `../`），再滤掉 Windows 非法字符。
    """
    base = Path(str(name or "")).name
    base = "".join(c for c in base if c not in _UNSAFE_NAME_CHARS).strip()
    if not base or not base.lower().endswith(".csv"):
        base = (base or "upload") + ".csv" if not base.lower().endswith(".csv") else base
    return base[:200]


def _resolve_sources(username: str, req: MergeCsvRequest) -> List[Dict[str, Any]]:
    """把请求里的两类来源统一成 source_files 清单。

    server 路径逐个过 resolve_user_path —— `..` 穿越、指向别的用户目录、
    符号链接跳出去全部在那里拦掉。
    """
    files: List[Dict[str, Any]] = []

    for raw in req.server_paths:
        try:
            p = svc.resolve_user_path(username, raw)
        except PermissionError as e:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
        if not p.is_file():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=f"文件不存在：{raw}"
            )
        if p.suffix.lower() != ".csv":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=f"不是 CSV 文件：{p.name}"
            )
        files.append({
            "filename": p.name,
            "file_path": str(p),
            "size": p.stat().st_size,
            "origin": "server",
        })

    up_dir = svc.upload_dir(username)
    for uid in req.upload_ids:
        # 上传文件存在 uploads/{upload_id}/{原文件名} 下 —— 一个 upload_id 一个子目录。
        # 这样 filename 就是用户的**原始文件名**，而不是 UUID。这不是美观问题：
        # 排序是按 filename 做的，存成 {uuid}.csv 会让上传路径变成**按 UUID 随机排序**，
        # 直接违背"按文件名排序依次合并"这个核心需求。
        sub = up_dir / _safe_upload_id(uid)
        matches = sorted(sub.glob("*.csv")) if sub.is_dir() else []
        if not matches:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"上传文件不存在或已过期: {uid}",
            )
        p = matches[0]
        files.append({
            "filename": p.name,
            "file_path": str(p),
            "size": p.stat().st_size,
            "origin": "upload",
        })

    if not files:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="未提供任何待合并文件",
        )
    return files


def _get_job_or_404(db: Session, job_id: str):
    from backend.models.merge_csv_job import MergeCsvJob
    job = db.query(MergeCsvJob).filter(MergeCsvJob.id == job_id).first()
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"任务不存在: {job_id}")
    return job


# ─────────────────────────────────────────────────────────────────────────────
# 入口① 导出任务
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/export-jobs")
async def list_export_jobs(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    current_user=Depends(require_permission("tools", "merge_csv")),
    db: Session = Depends(get_db),
):
    """列出本用户产出过 CSV 的导出任务。

    每项带上：可用分块清单（含导出侧自报行数，用于完成后对账）、
    被自动排除的未完成分块、CSV 已被压缩的分块。
    """
    data = svc.list_export_jobs(db, _username(current_user), page, page_size)
    return {"success": True, "data": data}


# ─────────────────────────────────────────────────────────────────────────────
# 入口② 目录 / 入口③ 文件
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/dirs")
async def list_dirs(
    current_user=Depends(require_permission("tools", "merge_csv")),
):
    return {"success": True, "data": svc.list_csv_dirs(_username(current_user))}


@router.get("/files")
async def list_files(
    dir_path: str = Query(..., description="目录路径（必须在 customer_data/{username}/ 内）"),
    pattern: Optional[str] = Query(default=None, description="文件名 glob 过滤，如 *202606*"),
    current_user=Depends(require_permission("tools", "merge_csv")),
):
    try:
        data = svc.list_csv_files(_username(current_user), dir_path, pattern)
    except PermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    except FileNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    return {"success": True, "data": data}


# ─────────────────────────────────────────────────────────────────────────────
# 入口④ 上传
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/upload")
async def upload_csv(
    file: UploadFile = File(...),
    current_user=Depends(require_permission("tools", "merge_csv")),
):
    """流式落盘单个 CSV，上限 1 GB。

    比这更大的文件请走服务器端入口 —— 那是零拷贝的，而浏览器上传 2 GB
    本身就不是个好主意。
    """
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="仅支持 .csv 文件"
        )

    username = _username(current_user)
    upload_id = str(uuid.uuid4())
    # 一个 upload_id 一个子目录，文件保留**原始文件名** —— 排序依赖它
    sub_dir = svc.upload_dir(username) / upload_id
    sub_dir.mkdir(parents=True, exist_ok=True)
    tmp_path = sub_dir / _safe_filename(file.filename)

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
                        detail=f"文件大小超出限制（最大 {_MAX_FILE_SIZE // 1024 // 1024 // 1024} GB）",
                    )
                fp.write(chunk)
    except HTTPException:
        raise
    except Exception as e:
        tmp_path.unlink(missing_ok=True)
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
# 预检
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/preview")
async def preview_merge(
    req: MergeCsvRequest,
    current_user=Depends(require_permission("tools", "merge_csv")),
    db: Session = Depends(get_db),
):
    """跑完 V1–V10 全部校验并返回结构化结果，**不建 job**。

    存在的理由：排序对不对、表头是否一致、编码是否混合、磁盘够不够 —— 这些
    都应该在建 job 之前暴露，而不是让用户等十分钟才收到一条失败。
    """
    username = _username(current_user)
    sources = _resolve_sources(username, req)
    if req.sort_mode not in ("natural", "lexicographic"):
        raise HTTPException(status_code=400, detail=f"未知排序方式: {req.sort_mode}")

    data = await _in_thread(
        svc.run_preview,
        db, username, sources,
        has_header=req.has_header,
        strict_header=req.strict_header,
        sort_mode=req.sort_mode,
        allow_active_files=req.allow_active_files,
    )
    return {"success": True, "data": data}


# ─────────────────────────────────────────────────────────────────────────────
# 提交
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/execute")
async def execute_merge(
    req: MergeCsvRequest,
    current_user=Depends(require_permission("tools", "merge_csv")),
    db: Session = Depends(get_db),
):
    from backend.models.merge_csv_job import MergeCsvJob

    username = _username(current_user)
    user_id = str(getattr(current_user, "id", username))
    if req.sort_mode not in ("natural", "lexicographic"):
        raise HTTPException(status_code=400, detail=f"未知排序方式: {req.sort_mode}")

    sources = _resolve_sources(username, req)

    # 提交时先跑一次预检，硬错误直接 400 —— 不要建一个注定失败的 job
    pv = await _in_thread(
        svc.run_preview,
        db, username, sources,
        has_header=req.has_header,
        strict_header=req.strict_header,
        sort_mode=req.sort_mode,
        allow_active_files=req.allow_active_files,
    )
    if not pv["ok"]:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="；".join(pv["errors"]))

    job = MergeCsvJob(
        user_id=user_id,
        username=username,
        job_name=req.job_name,
        has_header=req.has_header,
        strict_header=req.strict_header,
        sort_mode=req.sort_mode,
        source_files=pv["sorted_files"],
        status="pending",
        total_files=len(pv["sorted_files"]),
        total_bytes=pv["total_bytes"],
        expected_total_rows=pv["expected_total_rows"],
        warnings=pv["warnings"] or None,
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    job_id = str(job.id)
    task = asyncio.create_task(svc.run_merge_job(job_id))

    def _on_done(t: asyncio.Task):
        if t.cancelled():
            logger.warning("[MergeCsv] Job %s background task cancelled", job_id)
            return
        exc = t.exception()
        if exc:
            logger.error("[MergeCsv] Job %s background task failed: %s", job_id, exc, exc_info=exc)

    task.add_done_callback(_on_done)

    logger.info(
        "[MergeCsv] Job %s created by %s, %d files, %.2f GB",
        job_id, username, len(sources), pv["total_bytes"] / 1024 ** 3,
    )
    return {"success": True, "data": {"job_id": job_id, "status": "pending"}}


# ─────────────────────────────────────────────────────────────────────────────
# 任务查询 / 取消 / 删除
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/jobs")
async def list_jobs(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=100),
    current_user=Depends(require_permission("tools", "merge_csv")),
    db: Session = Depends(get_db),
):
    from backend.models.merge_csv_job import MergeCsvJob

    total = db.query(MergeCsvJob).count()
    jobs = (
        db.query(MergeCsvJob)
        .order_by(MergeCsvJob.created_at.desc())
        .offset((page - 1) * page_size)
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


@router.get("/jobs/{job_id}")
async def get_job_status(
    job_id: str,
    current_user=Depends(require_permission("tools", "merge_csv")),
    db: Session = Depends(get_db),
):
    return {"success": True, "data": _get_job_or_404(db, job_id).to_dict()}


@router.post("/jobs/{job_id}/cancel")
async def cancel_job(
    job_id: str,
    current_user=Depends(require_permission("tools", "merge_csv")),
    db: Session = Depends(get_db),
):
    """请求取消。

    running 状态下置为 cancelling，由工作线程在 chunk 边界响应，
    并把输出回退到**上一个文件边界** —— 因此结果仍是有效 CSV，且会保留。
    """
    job = _get_job_or_404(db, job_id)
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


@router.delete("/jobs/{job_id}")
async def delete_job(
    job_id: str,
    current_user=Depends(require_permission("tools", "merge_csv")),
    db: Session = Depends(get_db),
):
    """删除任务记录与**合并结果文件**。

    源文件的处理是刻意区分的：
      - origin=server  → **绝不删除**（那是用户的导出成果）
      - origin=upload  → 清理（本工具自己收下的临时文件）
    """
    job = _get_job_or_404(db, job_id)
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
        except OSError as e:
            logger.warning("[MergeCsv] Failed to delete output %s: %s", job.file_path, e)

    for f in (job.source_files or []):
        if f.get("origin") != "upload":
            continue                       # server 来源一律保留
        try:
            p = Path(f["file_path"])
            if p.exists():
                p.unlink()
            # 上传文件独占一个 {upload_id}/ 子目录，文件删了目录也一起收掉
            parent = p.parent
            if parent.is_dir() and not any(parent.iterdir()):
                parent.rmdir()
        except OSError as e:
            logger.warning("[MergeCsv] Failed to delete upload %s: %s", f.get("file_path"), e)

    db.delete(job)
    db.commit()
    return {"success": True}


@router.get("/jobs/{job_id}/download")
async def download_job(
    job_id: str,
    current_user=Depends(require_permission("tools", "merge_csv")),
    db: Session = Depends(get_db),
):
    """流式下载结果文件。

    前端用原生 `<a href>` 触发，**不用 blob** —— 结果可能几十 GB，
    `URL.createObjectURL(blob)` 会把整个文件读进浏览器内存并打爆标签页。
    FileResponse 自带 Range 支持，断了能续。
    """
    job = _get_job_or_404(db, job_id)
    if job.status not in ("completed", "cancelled"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"任务尚未产出可下载的结果（当前状态: {job.status}）",
        )
    if not job.file_path or not Path(job.file_path).exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="合并结果文件不存在（可能已被清理）"
        )
    return FileResponse(
        path=job.file_path,
        filename=job.output_filename or Path(job.file_path).name,
        media_type="text/csv",
    )
