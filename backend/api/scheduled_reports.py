"""
定时推送任务 API — /scheduled-reports/*

POST   /scheduled-reports/                          创建定时任务
GET    /scheduled-reports/                          任务列表（分页）
GET    /scheduled-reports/{schedule_id}             任务详情
PUT    /scheduled-reports/{schedule_id}             更新任务
DELETE /scheduled-reports/{schedule_id}             删除任务
PUT    /scheduled-reports/{schedule_id}/toggle      启用/停用切换
POST   /scheduled-reports/{schedule_id}/run-now     立即执行一次
GET    /scheduled-reports/{schedule_id}/history     执行历史（分页）
POST   /scheduled-reports/{schedule_id}/channels/test  测试单个通知渠道
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.api.deps import get_current_user, require_permission
from backend.config.database import get_db
from backend.models.scheduled_report import ScheduledReport
from backend.models.schedule_run_log import ScheduleRunLog
from backend.services.scheduler_service import add_or_update_job, remove_job, pause_job, resume_job

router = APIRouter(prefix="/scheduled-reports", tags=["定时推送任务"])
logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Pydantic 模型
# ─────────────────────────────────────────────────────────────────────────────

class CreateScheduleRequest(BaseModel):
    name: str
    description: Optional[str] = None
    doc_type: str = "dashboard"
    cron_expr: str  # e.g. "0 9 * * 1"
    timezone: str = "Asia/Shanghai"
    report_spec: Dict[str, Any]
    include_summary: bool = False
    notify_channels: Optional[List[Dict[str, Any]]] = None


class UpdateScheduleRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    cron_expr: Optional[str] = None
    timezone: Optional[str] = None
    report_spec: Optional[Dict[str, Any]] = None
    include_summary: Optional[bool] = None
    notify_channels: Optional[List[Dict[str, Any]]] = None


class TestChannelRequest(BaseModel):
    channel: Dict[str, Any]


# ─────────────────────────────────────────────────────────────────────────────
# 辅助函数
# ─────────────────────────────────────────────────────────────────────────────

def _get_sr_or_404(schedule_id: str, db: Session) -> ScheduledReport:
    """验证 UUID 格式，查询 DB，若不存在则抛出 404。"""
    try:
        uid = uuid.UUID(schedule_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="无效的定时任务 ID",
        )
    sr = db.query(ScheduledReport).filter(ScheduledReport.id == uid).first()
    if not sr:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="定时任务不存在",
        )
    return sr


def _check_sr_ownership(sr: ScheduledReport, username: str, is_superadmin: bool) -> None:
    """若不是任务所有者且非超管则抛出 403。"""
    if not is_superadmin and sr.owner_username != username:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="无权访问该定时任务",
        )


# ─────────────────────────────────────────────────────────────────────────────
# 1. 创建定时任务
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/")
async def create_schedule(
    req: CreateScheduleRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("schedules", "write")),
):
    """
    创建定时推送任务。
    - cron_expr 必须是标准 5-field 表达式（如 '0 9 * * 1'）
    - 创建成功后立即注册到 APScheduler
    """
    # 验证 cron 表达式
    if len(req.cron_expr.split()) != 5:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="cron_expr 必须是标准 5-field cron 表达式，如 '0 9 * * 1'",
        )

    username = getattr(current_user, "username", "default")

    sr = ScheduledReport(
        name=req.name,
        description=req.description,
        owner_username=username,
        doc_type=req.doc_type,
        cron_expr=req.cron_expr,
        timezone=req.timezone,
        report_spec=req.report_spec,
        include_summary=req.include_summary,
        notify_channels=req.notify_channels,
        is_active=True,
    )
    db.add(sr)
    db.commit()
    db.refresh(sr)

    # 注册到调度器
    add_or_update_job(sr)

    logger.info("[ScheduledReports] 任务已创建: id=%s, name=%r, owner=%s", sr.id, sr.name, username)
    return {"success": True, "data": sr.to_dict()}


# ─────────────────────────────────────────────────────────────────────────────
# 2. 任务列表（分页）
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/")
async def list_schedules(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    is_active: Optional[bool] = Query(None, description="过滤启用/停用状态"),
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("schedules", "read")),
):
    """
    获取定时任务列表（分页）。
    - 普通用户只能看到自己的任务
    - superadmin / 拥有 schedules:admin 权限的用户可看到全部
    """
    username = getattr(current_user, "username", "default")
    is_superadmin = getattr(current_user, "is_superadmin", False)

    # 判断是否有 schedules:admin 权限（非超管场景）
    has_admin_perm = False
    if not is_superadmin:
        try:
            from backend.core.rbac import get_user_permissions
            perms = get_user_permissions(current_user, db)
            has_admin_perm = "schedules:admin" in perms
        except Exception:
            pass

    q = db.query(ScheduledReport)
    if not is_superadmin and not has_admin_perm:
        q = q.filter(ScheduledReport.owner_username == username)
    if is_active is not None:
        q = q.filter(ScheduledReport.is_active == is_active)

    total = q.count()
    items = (
        q.order_by(ScheduledReport.created_at.desc())
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
            "items": [sr.to_dict() for sr in items],
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
# 3. 获取单个任务
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/{schedule_id}")
async def get_schedule(
    schedule_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("schedules", "read")),
):
    """获取单个定时任务详情（需所有权）。"""
    username = getattr(current_user, "username", "default")
    is_superadmin = getattr(current_user, "is_superadmin", False)

    sr = _get_sr_or_404(schedule_id, db)
    _check_sr_ownership(sr, username, is_superadmin)

    return {"success": True, "data": sr.to_dict()}


# ─────────────────────────────────────────────────────────────────────────────
# 4. 更新任务
# ─────────────────────────────────────────────────────────────────────────────

@router.put("/{schedule_id}")
async def update_schedule(
    schedule_id: str,
    req: UpdateScheduleRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("schedules", "write")),
):
    """
    更新定时任务（仅更新提供的字段）。
    - 若 cron_expr 或 is_active 发生变化，自动重新注册调度器 job
    """
    username = getattr(current_user, "username", "default")
    is_superadmin = getattr(current_user, "is_superadmin", False)

    sr = _get_sr_or_404(schedule_id, db)
    _check_sr_ownership(sr, username, is_superadmin)

    # 验证新 cron 表达式（若提供）
    if req.cron_expr is not None and len(req.cron_expr.split()) != 5:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="cron_expr 必须是标准 5-field cron 表达式，如 '0 9 * * 1'",
        )

    old_cron = sr.cron_expr
    old_is_active = sr.is_active

    # 仅更新提供的字段
    if req.name is not None:
        sr.name = req.name
    if req.description is not None:
        sr.description = req.description
    if req.cron_expr is not None:
        sr.cron_expr = req.cron_expr
    if req.timezone is not None:
        sr.timezone = req.timezone
    if req.report_spec is not None:
        sr.report_spec = req.report_spec
    if req.include_summary is not None:
        sr.include_summary = req.include_summary
    if req.notify_channels is not None:
        sr.notify_channels = req.notify_channels

    sr.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(sr)

    # 若 cron 或启用状态变化，重新注册 job
    cron_changed = req.cron_expr is not None and req.cron_expr != old_cron
    active_changed = False  # UpdateScheduleRequest 不含 is_active，仅 toggle 端点负责
    if cron_changed or active_changed:
        add_or_update_job(sr)

    logger.info("[ScheduledReports] 任务已更新: id=%s", sr.id)
    return {"success": True, "data": sr.to_dict()}


# ─────────────────────────────────────────────────────────────────────────────
# 5. 删除任务
# ─────────────────────────────────────────────────────────────────────────────

@router.delete("/{schedule_id}")
async def delete_schedule(
    schedule_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("schedules", "write")),
):
    """删除定时任务（同时从调度器中移除 job）。"""
    username = getattr(current_user, "username", "default")
    is_superadmin = getattr(current_user, "is_superadmin", False)

    sr = _get_sr_or_404(schedule_id, db)
    _check_sr_ownership(sr, username, is_superadmin)

    # 从调度器移除
    remove_job(str(sr.id))

    db.delete(sr)
    db.commit()

    logger.info("[ScheduledReports] 任务已删除: id=%s", schedule_id)
    return {"success": True, "message": "已删除"}


# ─────────────────────────────────────────────────────────────────────────────
# 6. 启用/停用切换
# ─────────────────────────────────────────────────────────────────────────────

@router.put("/{schedule_id}/toggle")
async def toggle_schedule(
    schedule_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("schedules", "write")),
):
    """切换定时任务的启用/停用状态。"""
    username = getattr(current_user, "username", "default")
    is_superadmin = getattr(current_user, "is_superadmin", False)

    sr = _get_sr_or_404(schedule_id, db)
    _check_sr_ownership(sr, username, is_superadmin)

    sr.is_active = not sr.is_active
    sr.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(sr)

    if sr.is_active:
        add_or_update_job(sr)
        logger.info("[ScheduledReports] 任务已启用: id=%s", sr.id)
    else:
        remove_job(str(sr.id))
        logger.info("[ScheduledReports] 任务已停用: id=%s", sr.id)

    return {
        "success": True,
        "data": {
            "is_active": sr.is_active,
            "id": str(sr.id),
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
# 7. 立即执行一次
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/{schedule_id}/run-now")
async def run_schedule_now(
    schedule_id: str,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("schedules", "write")),
):
    """立即触发一次定时任务执行（异步后台运行）。"""
    username = getattr(current_user, "username", "default")
    is_superadmin = getattr(current_user, "is_superadmin", False)

    sr = _get_sr_or_404(schedule_id, db)
    _check_sr_ownership(sr, username, is_superadmin)

    from backend.services.scheduler_service import _execute_scheduled_report

    background_tasks.add_task(_execute_scheduled_report, str(sr.id))

    logger.info("[ScheduledReports] 立即执行已触发: id=%s, by=%s", sr.id, username)
    return {"success": True, "message": "已触发立即执行，请稍后查看执行历史"}


# ─────────────────────────────────────────────────────────────────────────────
# 8. 执行历史
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/{schedule_id}/history")
async def get_schedule_history(
    schedule_id: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("schedules", "read")),
):
    """获取定时任务执行历史（按执行时间倒序分页）。"""
    username = getattr(current_user, "username", "default")
    is_superadmin = getattr(current_user, "is_superadmin", False)

    sr = _get_sr_or_404(schedule_id, db)
    _check_sr_ownership(sr, username, is_superadmin)

    q = db.query(ScheduleRunLog).filter(ScheduleRunLog.scheduled_report_id == sr.id)
    total = q.count()
    logs = (
        q.order_by(ScheduleRunLog.run_at.desc())
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
            "items": [log.to_dict() for log in logs],
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
# 9. 测试单个通知渠道
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/{schedule_id}/channels/test")
async def test_notify_channel(
    schedule_id: str,
    req: TestChannelRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("schedules", "write")),
):
    """
    向指定通知渠道发送测试消息（is_test=True）。

    请求体示例：
        {"channel": {"type": "email", "to": ["a@b.com"], "subject_tpl": "测试"}}
    """
    username = getattr(current_user, "username", "default")
    is_superadmin = getattr(current_user, "is_superadmin", False)

    sr = _get_sr_or_404(schedule_id, db)
    _check_sr_ownership(sr, username, is_superadmin)

    channel = req.channel
    if not channel:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="channel 不能为空",
        )

    try:
        from backend.services.notify_service import NotifyService  # noqa: PLC0415
    except ImportError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="通知服务模块未安装，无法发送测试消息",
        )

    try:
        notify_svc = NotifyService(db)
        await notify_svc.send_one(channel=channel, report=None, is_test=True)
    except Exception as e:
        logger.warning("[ScheduledReports] 测试通知发送失败: channel=%s, error=%s", channel.get("type"), e)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"测试发送失败: {e}",
        )

    return {"success": True, "message": "测试发送成功"}
