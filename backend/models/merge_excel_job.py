"""
合并 Excel 文件任务模型

存储"小工具 - 合并Excel文件"的任务状态与进度，支持前端轮询查询。
"""
from sqlalchemy import Column, String, DateTime, Integer, BigInteger, Boolean, Text, Index
from sqlalchemy.dialects.postgresql import UUID, JSONB
from datetime import datetime
import uuid

from backend.config.database import Base


class MergeExcelJob(Base):
    """合并 Excel 文件任务表"""

    __tablename__ = "merge_excel_jobs"

    # 主键
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)

    # 所属用户
    user_id = Column(String(64), nullable=False, index=True, comment="操作用户 ID")
    username = Column(String(100), nullable=False, comment="操作用户名（冗余，方便查询展示）")

    # 任务配置
    job_name = Column(String(200), nullable=True, comment="任务名称（用户自定义，用于文件名）")
    has_header = Column(Boolean, nullable=False, default=True, comment="源文件是否包含表头")
    # source_files: [{filename, file_path, size}]，按文件名排序后的清单
    source_files = Column(JSONB, nullable=True, comment="待合并源文件清单（已按文件名排序）")

    # 任务状态
    # pending → running → completed / failed
    #                   ↘ cancelling → cancelled
    status = Column(String(20), nullable=False, default="pending", index=True, comment="任务状态")

    # 文件级进度
    total_files = Column(Integer, nullable=True, comment="待合并文件总数")
    done_files = Column(Integer, default=0, comment="已完成合并的文件数")

    # 行级进度
    total_rows = Column(Integer, nullable=True, comment="总数据行数（不含表头，未知则为空）")
    merged_rows = Column(Integer, default=0, comment="已合并数据行数")

    # Sheet 进度（Excel 多 sheet 自动分割）
    current_sheet = Column(String(200), nullable=True, comment="当前正在写入的 Sheet 名")
    total_sheets = Column(Integer, default=0, comment="最终生成的 Sheet 总数")

    # 输出文件
    output_filename = Column(String(500), nullable=True, comment="合并结果文件名（含扩展名）")
    file_path = Column(String(1000), nullable=True, comment="服务器端绝对文件路径")
    file_size = Column(BigInteger, nullable=True, comment="合并结果文件大小（字节）")

    # 列结构一致性检查提示（非阻断）
    warnings = Column(JSONB, nullable=True, comment="列结构不一致等非阻断性提示")

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
        Index("idx_merge_excel_jobs_user_id", "user_id"),
        Index("idx_merge_excel_jobs_status", "status"),
        Index("idx_merge_excel_jobs_created_at", "created_at"),
    )

    def to_dict(self) -> dict:
        return {
            "job_id": str(self.id),
            "user_id": self.user_id,
            "username": self.username,
            "job_name": self.job_name,
            "has_header": self.has_header,
            "source_files": self.source_files,
            "status": self.status,
            "total_files": self.total_files,
            "done_files": self.done_files,
            "total_rows": self.total_rows,
            "merged_rows": self.merged_rows,
            "current_sheet": self.current_sheet,
            "total_sheets": self.total_sheets,
            "output_filename": self.output_filename,
            "file_size": self.file_size,
            "warnings": self.warnings,
            "error_message": self.error_message,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
        }
