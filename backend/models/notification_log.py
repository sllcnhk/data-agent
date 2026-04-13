"""
通知发送日志模型

记录每条通知的发送情况，供运维追溯和渠道健康监控使用。
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Column, DateTime, Index, String, Text
from sqlalchemy.dialects.postgresql import UUID

from backend.config.database import Base


class NotificationLog(Base):
    """通知发送日志表"""

    __tablename__ = "notification_logs"

    # 主键
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)

    # 关联的定时任务（可为 None：手动触发的测试发送）
    scheduled_report_id = Column(
        UUID(as_uuid=True), nullable=True, index=True, comment="关联的 ScheduledReport ID"
    )

    # 关联的执行日志
    run_log_id = Column(
        UUID(as_uuid=True), nullable=True, index=True, comment="关联的 ScheduleRunLog ID"
    )

    # 渠道类型：email | wecom | feishu | webhook
    channel_type = Column(String(30), nullable=False, comment="渠道类型")

    # 接收方描述（邮件地址、群组名等）
    recipient = Column(String(500), nullable=True, comment="接收方描述")

    # 发送状态：success | failed
    status = Column(String(20), nullable=False, default="pending", comment="发送状态")

    # 错误信息（status=failed 时）
    error = Column(Text, nullable=True, comment="发送失败错误信息")

    # 发送时间
    sent_at = Column(DateTime, nullable=False, default=datetime.utcnow, comment="发送时间")

    __table_args__ = (
        Index("idx_notification_logs_scheduled_report_id", "scheduled_report_id"),
        Index("idx_notification_logs_channel_type", "channel_type"),
        Index("idx_notification_logs_status", "status"),
        Index("idx_notification_logs_sent_at", "sent_at"),
    )

    def __repr__(self) -> str:
        return (
            f"<NotificationLog(id={self.id}, channel={self.channel_type!r}, "
            f"status={self.status!r})>"
        )

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "scheduled_report_id": (
                str(self.scheduled_report_id) if self.scheduled_report_id else None
            ),
            "run_log_id": str(self.run_log_id) if self.run_log_id else None,
            "channel_type": self.channel_type,
            "recipient": self.recipient,
            "status": self.status,
            "error": self.error,
            "sent_at": self.sent_at.isoformat() if self.sent_at else None,
        }
