"""
数据导入任务模型

存储 Excel → ClickHouse 导入任务的状态与进度，支持前端轮询查询。
"""
from sqlalchemy import Column, String, DateTime, Integer, Text, Index
from sqlalchemy.dialects.postgresql import UUID, JSONB
from datetime import datetime
import uuid

from backend.config.database import Base


class ImportJob(Base):
    """Excel 导入任务表"""

    __tablename__ = "import_jobs"

    # 主键
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)

    # 所属用户
    user_id = Column(String(64), nullable=False, index=True, comment="操作用户 ID")
    username = Column(String(100), nullable=False, comment="操作用户名（冗余，方便查询展示）")

    # 上传文件信息
    upload_id = Column(String(64), nullable=False, comment="文件上传 ID（对应临时文件路径）")
    filename = Column(String(500), nullable=False, comment="原始文件名")

    # 目标连接信息
    connection_env = Column(String(50), nullable=False, comment="ClickHouse 环境名（如 sg/idn）")

    # 任务状态
    # pending → running → completed / failed
    #                   ↘ cancelling → cancelled
    status = Column(String(20), nullable=False, default="pending", index=True, comment="任务状态")

    # Sheet 级别进度
    total_sheets = Column(Integer, default=0, comment="总 Sheet 数（已启用）")
    done_sheets = Column(Integer, default=0, comment="已完成 Sheet 数")
    current_sheet = Column(String(200), nullable=True, comment="当前正在导入的 Sheet 名")

    # 行级别进度
    total_rows = Column(Integer, default=0, comment="总行数（所有启用 Sheet 合计）")
    imported_rows = Column(Integer, default=0, comment="已成功导入行数")

    # 批次进度
    total_batches = Column(Integer, default=0, comment="总批次数")
    done_batches = Column(Integer, default=0, comment="已完成批次数")

    # 配置快照（JSON）：记录提交时的 sheet 配置，方便追溯
    config_snapshot = Column(JSONB, nullable=True, comment="提交时的导入配置快照")

    # 错误信息
    error_message = Column(Text, nullable=True, comment="终止错误信息（abort 策略时）")
    errors = Column(JSONB, nullable=True, comment="分批错误列表：[{sheet, batch, message}]")

    # 时间戳
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, comment="创建时间")
    started_at = Column(DateTime, nullable=True, comment="开始执行时间")
    finished_at = Column(DateTime, nullable=True, comment="完成时间")
    updated_at = Column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
        comment="最后更新时间",
    )

    __table_args__ = (
        Index("idx_import_jobs_user_id", "user_id"),
        Index("idx_import_jobs_status", "status"),
        Index("idx_import_jobs_created_at", "created_at"),
    )

    def to_dict(self) -> dict:
        return {
            "job_id": str(self.id),
            "user_id": self.user_id,
            "username": self.username,
            "upload_id": self.upload_id,
            "filename": self.filename,
            "connection_env": self.connection_env,
            "status": self.status,
            "total_sheets": self.total_sheets,
            "done_sheets": self.done_sheets,
            "current_sheet": self.current_sheet,
            "total_rows": self.total_rows,
            "imported_rows": self.imported_rows,
            "total_batches": self.total_batches,
            "done_batches": self.done_batches,
            "error_message": self.error_message,
            "errors": self.errors or [],
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
        }
