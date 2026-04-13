"""
定时报告任务模型

存储用户配置的「固定推送任务」，由 APScheduler 定时触发生成报表/报告并发送通知。
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID

from backend.config.database import Base


class ScheduledReport(Base):
    """定时报告任务表"""

    __tablename__ = "scheduled_reports"

    # 主键
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)

    # 基本信息
    name = Column(String(200), nullable=False, comment="任务名称，如「每周销售报告」")
    description = Column(Text, nullable=True, comment="任务描述")

    # 所有者
    owner_username = Column(String(100), nullable=False, index=True, comment="所有者用户名")

    # 报告类型：dashboard（报表）| document（报告）
    doc_type = Column(String(20), nullable=False, default="dashboard", comment="报告类型")

    # 调度配置
    cron_expr = Column(String(100), nullable=False, comment="标准 5-field cron 表达式，如 '0 9 * * 1'")
    timezone = Column(String(60), nullable=False, default="Asia/Shanghai", comment="时区")

    # 报告规格（完整 spec，供 build_report_html 使用）
    report_spec = Column(JSONB, nullable=False, comment="报告规格 JSON（含图表配置、SQL 等）")

    # 选项
    include_summary = Column(Boolean, default=False, comment="是否触发 LLM 总结")

    # 状态
    is_active = Column(Boolean, default=True, nullable=False, comment="是否启用")

    # 执行统计
    last_run_at = Column(DateTime, nullable=True, comment="上次执行时间")
    next_run_at = Column(DateTime, nullable=True, comment="下次执行时间（APScheduler 写入）")
    run_count = Column(Integer, default=0, nullable=False, comment="累计成功执行次数")
    fail_count = Column(Integer, default=0, nullable=False, comment="累计失败次数")

    # 通知渠道配置（JSONB 数组）
    # 格式：[
    #   {"type": "email",   "to": ["a@b.com"], "subject_tpl": "{{name}} - {{date}}"},
    #   {"type": "wecom",   "webhook_url": "https://qyapi.weixin.qq.com/..."},
    #   {"type": "feishu",  "webhook_url": "https://open.feishu.cn/..."},
    #   {"type": "webhook", "url": "https://xxx", "method": "POST"}
    # ]
    notify_channels = Column(JSONB, nullable=True, comment="通知渠道配置列表")

    # 时间戳
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, comment="创建时间")
    updated_at = Column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
        comment="更新时间",
    )

    __table_args__ = (
        Index("idx_scheduled_reports_owner", "owner_username"),
        Index("idx_scheduled_reports_active", "is_active"),
        Index("idx_scheduled_reports_created_at", "created_at"),
    )

    def __repr__(self) -> str:
        return (
            f"<ScheduledReport(id={self.id}, name={self.name!r}, "
            f"cron={self.cron_expr!r}, active={self.is_active})>"
        )

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "name": self.name,
            "description": self.description,
            "owner_username": self.owner_username,
            "doc_type": self.doc_type,
            "cron_expr": self.cron_expr,
            "timezone": self.timezone,
            "report_spec": self.report_spec,
            "include_summary": self.include_summary,
            "is_active": self.is_active,
            "last_run_at": self.last_run_at.isoformat() if self.last_run_at else None,
            "next_run_at": self.next_run_at.isoformat() if self.next_run_at else None,
            "run_count": self.run_count,
            "fail_count": self.fail_count,
            "notify_channels": self.notify_channels or [],
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
