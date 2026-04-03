"""
任务模型

存储用户创建的各种任务（数据导出、ETL脚本生成、报表创建等）
"""
from sqlalchemy import Column, String, DateTime, Text, Integer, JSON, Boolean, Index, Enum as SQLEnum, ForeignKey
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from datetime import datetime
import uuid
import enum

from backend.config.database import Base


class TaskType(str, enum.Enum):
    """任务类型枚举"""
    DATA_EXPORT = "data_export"           # 数据导出
    ETL_DESIGN = "etl_design"             # ETL设计
    SQL_GENERATION = "sql_generation"     # SQL生成
    DATA_ANALYSIS = "data_analysis"       # 数据分析
    REPORT_CREATION = "report_creation"   # 报表创建
    DATABASE_CONNECTION = "database_connection"  # 数据库连接
    FILE_ANALYSIS = "file_analysis"       # 文件分析
    CUSTOM = "custom"                     # 自定义任务


class TaskStatus(str, enum.Enum):
    """任务状态枚举"""
    PENDING = "pending"         # 待执行
    RUNNING = "running"         # 执行中
    COMPLETED = "completed"     # 已完成
    FAILED = "failed"           # 失败
    CANCELLED = "cancelled"     # 已取消
    PAUSED = "paused"           # 已暂停


class Task(Base):
    """任务表"""

    __tablename__ = "tasks"

    # 主键
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)

    # 外键（可选 - 不是所有任务都关联对话）
    conversation_id = Column(
        UUID(as_uuid=True),
        ForeignKey('conversations.id'),
        nullable=True,
        index=True,
        comment="关联的对话ID"
    )

    # 基本信息
    name = Column(String(200), nullable=False, comment="任务名称")
    description = Column(Text, nullable=True, comment="任务描述")

    # 任务类型和状态
    task_type = Column(
        SQLEnum(TaskType),
        nullable=False,
        default=TaskType.CUSTOM,
        comment="任务类型"
    )
    status = Column(
        SQLEnum(TaskStatus),
        nullable=False,
        default=TaskStatus.PENDING,
        comment="任务状态"
    )

    # 优先级
    priority = Column(Integer, default=0, comment="优先级(0-10, 10最高)")

    # 任务配置
    config = Column(JSONB, nullable=True, comment="任务配置参数")
    input_data = Column(JSONB, nullable=True, comment="输入数据")

    # 执行信息
    started_at = Column(DateTime, nullable=True, comment="开始执行时间")
    completed_at = Column(DateTime, nullable=True, comment="完成时间")
    execution_time = Column(Integer, nullable=True, comment="执行时长(秒)")

    # 结果
    result = Column(JSONB, nullable=True, comment="执行结果")
    output_files = Column(JSONB, nullable=True, comment="输出文件列表")
    error_message = Column(Text, nullable=True, comment="错误信息")
    error_trace = Column(Text, nullable=True, comment="错误堆栈")

    # 进度
    progress = Column(Integer, default=0, comment="进度(0-100)")
    current_step = Column(String(100), nullable=True, comment="当前步骤")
    total_steps = Column(Integer, nullable=True, comment="总步骤数")

    # 统计
    processed_rows = Column(Integer, default=0, comment="已处理行数")
    total_rows = Column(Integer, default=0, comment="总行数")

    # 元数据
    extra_metadata = Column(JSONB, nullable=True, comment="额外元数据")
    tags = Column(JSONB, nullable=True, comment="标签")

    # 时间戳
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, comment="创建时间")
    updated_at = Column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
        comment="更新时间"
    )

    # 关系
    conversation = relationship("Conversation", back_populates="tasks")

    # 索引
    __table_args__ = (
        Index("idx_tasks_conversation_id", "conversation_id"),
        Index("idx_tasks_status", "status"),
        Index("idx_tasks_type", "task_type"),
        Index("idx_tasks_created_at", "created_at"),
        Index("idx_tasks_priority", "priority"),
    )

    def __repr__(self):
        return f"<Task(id={self.id}, name={self.name}, type={self.task_type}, status={self.status})>"

    def to_dict(self):
        """转换为字典"""
        return {
            "id": str(self.id),
            "conversation_id": str(self.conversation_id) if self.conversation_id else None,
            "name": self.name,
            "description": self.description,
            "task_type": self.task_type.value if self.task_type else None,
            "status": self.status.value if self.status else None,
            "priority": self.priority,
            "config": self.config,
            "input_data": self.input_data,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "execution_time": self.execution_time,
            "result": self.result,
            "output_files": self.output_files,
            "error_message": self.error_message,
            "error_trace": self.error_trace,
            "progress": self.progress,
            "current_step": self.current_step,
            "total_steps": self.total_steps,
            "processed_rows": self.processed_rows,
            "total_rows": self.total_rows,
            "extra_metadata": self.extra_metadata,
            "tags": self.tags,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    def update_progress(self, progress: int, current_step: str = None):
        """更新任务进度"""
        self.progress = progress
        if current_step:
            self.current_step = current_step
        self.updated_at = datetime.utcnow()

    def start(self):
        """标记任务开始"""
        self.status = TaskStatus.RUNNING
        self.started_at = datetime.utcnow()
        self.updated_at = datetime.utcnow()

    def complete(self, result: dict = None, output_files: list = None):
        """标记任务完成"""
        self.status = TaskStatus.COMPLETED
        self.completed_at = datetime.utcnow()
        self.progress = 100

        if self.started_at:
            self.execution_time = int((self.completed_at - self.started_at).total_seconds())

        if result:
            self.result = result
        if output_files:
            self.output_files = output_files

        self.updated_at = datetime.utcnow()

    def fail(self, error_message: str, error_trace: str = None):
        """标记任务失败"""
        self.status = TaskStatus.FAILED
        self.completed_at = datetime.utcnow()
        self.error_message = error_message

        if error_trace:
            self.error_trace = error_trace

        if self.started_at:
            self.execution_time = int((self.completed_at - self.started_at).total_seconds())

        self.updated_at = datetime.utcnow()


class TaskHistory(Base):
    """任务历史表（用于审计和统计）"""

    __tablename__ = "task_history"

    # 主键
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)

    # 外键
    task_id = Column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
        comment="任务ID"
    )

    # 事件信息
    event_type = Column(
        String(50),
        nullable=False,
        comment="事件类型: created, started, progress, completed, failed, cancelled"
    )
    event_data = Column(JSONB, nullable=True, comment="事件数据")

    # 状态变更
    old_status = Column(String(20), nullable=True, comment="旧状态")
    new_status = Column(String(20), nullable=True, comment="新状态")

    # 消息
    message = Column(Text, nullable=True, comment="事件消息")

    # 时间戳
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, comment="创建时间")

    # 索引
    __table_args__ = (
        Index("idx_task_history_task_id", "task_id"),
        Index("idx_task_history_event_type", "event_type"),
        Index("idx_task_history_created_at", "created_at"),
    )

    def __repr__(self):
        return f"<TaskHistory(id={self.id}, task_id={self.task_id}, event={self.event_type})>"

    def to_dict(self):
        """转换为字典"""
        return {
            "id": str(self.id),
            "task_id": str(self.task_id),
            "event_type": self.event_type,
            "event_data": self.event_data,
            "old_status": self.old_status,
            "new_status": self.new_status,
            "message": self.message,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
