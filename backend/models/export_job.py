"""
数据导出任务模型

存储 SQL → Excel 导出任务的状态与进度，支持前端轮询查询。
"""
from sqlalchemy import Column, String, DateTime, Integer, BigInteger, Text, Index
from sqlalchemy.dialects.postgresql import UUID, JSONB
from datetime import datetime
import uuid

from backend.config.database import Base


class ExportJob(Base):
    """SQL 导出任务表"""

    __tablename__ = "export_jobs"

    # 主键
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)

    # 所属用户
    user_id = Column(String(64), nullable=False, index=True, comment="操作用户 ID")
    username = Column(String(100), nullable=False, comment="操作用户名（冗余，方便查询展示）")

    # 导出配置
    job_name = Column(String(200), nullable=True, comment="任务名称（用户自定义，用于文件名）")
    query_sql = Column(Text, nullable=False, comment="导出 SQL 语句")
    connection_env = Column(String(50), nullable=False, comment="连接环境名（如 sg/idn）")
    connection_type = Column(
        String(20), nullable=False, default="clickhouse",
        comment="连接类型（clickhouse/mysql/...），扩展点"
    )
    db_name = Column(String(200), nullable=True, comment="目标数据库名（可选，仅用于上下文展示）")

    # 任务状态
    # pending → running → completed / failed
    #                   ↘ cancelling → cancelled
    status = Column(String(20), nullable=False, default="pending", index=True, comment="任务状态")

    # 行级别进度
    total_rows = Column(Integer, nullable=True, comment="总行数（查询后已知）")
    exported_rows = Column(Integer, default=0, comment="已成功导出行数")

    # 批次进度
    total_batches = Column(Integer, nullable=True, comment="总批次数（估算）")
    done_batches = Column(Integer, default=0, comment="已完成批次数")

    # Sheet 进度（Excel 多 sheet 自动分割）
    current_sheet = Column(String(200), nullable=True, comment="当前正在写入的 Sheet 名")
    total_sheets = Column(Integer, default=0, comment="最终生成的 Sheet 总数")

    # 输出文件
    output_filename = Column(String(500), nullable=True, comment="导出文件名（含扩展名）")
    file_path = Column(String(1000), nullable=True, comment="服务器端绝对文件路径")
    file_size = Column(BigInteger, nullable=True, comment="导出文件大小（字节）")

    # 配置快照（JSON）：记录提交时的配置，方便追溯
    config_snapshot = Column(JSONB, nullable=True, comment="提交时的导出配置快照")

    # 错误信息
    error_message = Column(Text, nullable=True, comment="终止错误信息")

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
        Index("idx_export_jobs_user_id", "user_id"),
        Index("idx_export_jobs_status", "status"),
        Index("idx_export_jobs_created_at", "created_at"),
    )

    def to_dict(self) -> dict:
        return {
            "job_id": str(self.id),
            "user_id": self.user_id,
            "username": self.username,
            "job_name": self.job_name,
            "query_sql": self.query_sql,
            "connection_env": self.connection_env,
            "connection_type": self.connection_type,
            "db_name": self.db_name,
            "status": self.status,
            "total_rows": self.total_rows,
            "exported_rows": self.exported_rows,
            "total_batches": self.total_batches,
            "done_batches": self.done_batches,
            "current_sheet": self.current_sheet,
            "total_sheets": self.total_sheets,
            "output_filename": self.output_filename,
            "file_size": self.file_size,
            "error_message": self.error_message,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
        }
