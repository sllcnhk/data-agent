"""
合并 CSV 文件任务模型

存储"小工具 - 合并CSV文件"的任务状态与进度，支持前端轮询查询。

与 MergeExcelJob 刻意独立（不共表）的原因：
    CSV 走字节级拼接、Excel 走 openpyxl 逐单元格，两条路径没有一行可复用的
    代码；字段也对不上（CSV 需要 total_bytes / total_rows / last_merged_file /
    对账字段，Excel 需要 current_sheet / total_sheets）。合表只会让一半字段
    对另一半场景永远是 NULL。
"""
from sqlalchemy import BigInteger, Boolean, Column, DateTime, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from datetime import datetime
import uuid

from backend.config.database import Base


class MergeCsvJob(Base):
    """合并 CSV 文件任务表"""

    __tablename__ = "merge_csv_jobs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)

    # 所属用户
    user_id = Column(String(64), nullable=False, index=True, comment="操作用户 ID")
    username = Column(String(100), nullable=False, comment="操作用户名（冗余，方便查询展示）")

    # ── 任务配置 ──
    job_name = Column(String(200), nullable=True, comment="任务名称（用户自定义，用于文件名）")
    has_header = Column(Boolean, nullable=False, default=True, comment="源文件是否包含表头")
    strict_header = Column(
        Boolean, nullable=False, default=True,
        comment="表头文字不一致时是否阻断（关闭则降级为 warning 并按位置合并）",
    )
    sort_mode = Column(
        String(20), nullable=False, default="natural",
        comment="排序方式：natural（数字感知，默认）| lexicographic",
    )

    # source_files: [{filename, file_path, size, origin, encoding,
    #                 rows, physical_lines, bytes_written,
    #                 export_job_id, expected_rows}]
    # origin: upload | server —— server 来源的文件在删除 job 时**绝不删除**
    source_files = Column(JSONB, nullable=True, comment="待合并源文件清单（已排序，运行中回填行数）")

    # ── 任务状态 ──
    # pending → running → completed / failed
    #                   ↘ cancelling → cancelled
    status = Column(String(20), nullable=False, default="pending", index=True, comment="任务状态")

    # ── 进度 ──
    total_files = Column(Integer, nullable=True, comment="待合并文件总数")
    done_files = Column(Integer, default=0, comment="已完整合入的文件数")
    # 字节进度是 100% 精确且免费的，前端进度条以此为准
    total_bytes = Column(BigInteger, nullable=True, comment="源文件字节合计")
    done_bytes = Column(BigInteger, default=0, comment="已处理字节")
    # 行数按 RFC4180「引号外的 \n」精确统计（numpy 奇偶扫描），不是估算
    total_rows = Column(BigInteger, default=0, comment="已合并数据行数（精确，不含表头）")
    total_physical_lines = Column(
        BigInteger, default=0,
        comment="物理行数；与 total_rows 的差 = 字段内换行条数（诊断用）",
    )

    last_merged_file = Column(
        String(500), nullable=True,
        comment="最后一个**完整**合入的源文件名 —— 取消/失败时据此判断续传起点",
    )

    # ── 输出 ──
    output_filename = Column(String(500), nullable=True, comment="合并结果文件名（含扩展名）")
    file_path = Column(String(1000), nullable=True, comment="服务器端绝对文件路径")
    file_size = Column(BigInteger, nullable=True, comment="合并结果文件大小（字节）")
    output_encoding = Column(String(32), nullable=True, comment="输出编码，如 utf-8-sig / gb18030")

    # ── 行数对账（来源为导出任务时可用）──
    expected_total_rows = Column(
        BigInteger, nullable=True,
        comment="导出侧自报行数合计（export_jobs.chunk_files[].rows）",
    )
    reconcile_status = Column(
        String(20), nullable=True,
        comment="matched | mismatched | unavailable",
    )
    reconcile_detail = Column(JSONB, nullable=True, comment="逐文件对账差异")

    # ── 提示与错误 ──
    warnings = Column(JSONB, nullable=True, comment="编码推测、补换行、跳过空文件等非阻断提示")
    error_message = Column(Text, nullable=True, comment="终止错误信息")

    # ── 时间戳 ──
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
        Index("idx_merge_csv_jobs_user_id", "user_id"),
        Index("idx_merge_csv_jobs_status", "status"),
        Index("idx_merge_csv_jobs_created_at", "created_at"),
    )

    def to_dict(self) -> dict:
        return {
            "job_id": str(self.id),
            "user_id": self.user_id,
            "username": self.username,
            "job_name": self.job_name,
            "has_header": self.has_header,
            "strict_header": self.strict_header,
            "sort_mode": self.sort_mode,
            "source_files": self.source_files,
            "status": self.status,
            "total_files": self.total_files,
            "done_files": self.done_files or 0,
            "total_bytes": self.total_bytes,
            "done_bytes": self.done_bytes or 0,
            "total_rows": self.total_rows or 0,
            "total_physical_lines": self.total_physical_lines or 0,
            "last_merged_file": self.last_merged_file,
            "output_filename": self.output_filename,
            "file_path": self.file_path,
            "file_size": self.file_size,
            "output_encoding": self.output_encoding,
            "expected_total_rows": self.expected_total_rows,
            "reconcile_status": self.reconcile_status,
            "reconcile_detail": self.reconcile_detail,
            "warnings": self.warnings,
            "error_message": self.error_message,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
        }
