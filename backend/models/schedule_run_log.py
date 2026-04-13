"""
定时任务执行日志模型

记录每次 ScheduledReport 的执行结果，供历史追溯和告警使用。
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Column, DateTime, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID

from backend.config.database import Base


class ScheduleRunLog(Base):
    """定时任务执行日志表"""

    __tablename__ = "schedule_run_logs"

    # 主键
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)

    # 关联的定时任务
    scheduled_report_id = Column(
        UUID(as_uuid=True), nullable=False, index=True, comment="关联的 ScheduledReport ID"
    )

    # 本次生成的报告 ID（若成功）
    report_id = Column(UUID(as_uuid=True), nullable=True, comment="本次生成的 Report ID")

    # 执行结果：pending | running | success | failed
    status = Column(String(20), nullable=False, default="pending", comment="执行状态")

    # 错误信息（status=failed 时填写）
    error_msg = Column(Text, nullable=True, comment="错误信息")

    # 执行耗时（秒）
    duration_sec = Column(Integer, nullable=True, comment="执行耗时（秒）")

    # 通知发送结果概要
    # 格式：{"email": "success", "wecom": "failed: timeout", "total": 2, "ok": 1}
    notify_summary = Column(JSONB, nullable=True, comment="通知发送结果摘要")

    # 执行时间
    run_at = Column(DateTime, nullable=False, default=datetime.utcnow, comment="执行时间")
    finished_at = Column(DateTime, nullable=True, comment="完成时间")

    __table_args__ = (
        Index("idx_run_logs_scheduled_report_id", "scheduled_report_id"),
        Index("idx_run_logs_status", "status"),
        Index("idx_run_logs_run_at", "run_at"),
    )

    def __repr__(self) -> str:
        return (
            f"<ScheduleRunLog(id={self.id}, "
            f"scheduled_report_id={self.scheduled_report_id}, "
            f"status={self.status!r})>"
        )

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "scheduled_report_id": str(self.scheduled_report_id),
            "report_id": str(self.report_id) if self.report_id else None,
            "status": self.status,
            "error_msg": self.error_msg,
            "duration_sec": self.duration_sec,
            "notify_summary": self.notify_summary or {},
            "run_at": self.run_at.isoformat() if self.run_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
        }
