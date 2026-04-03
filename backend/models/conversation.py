"""
对话模型

存储用户与Agent的对话历史
"""
from sqlalchemy import Column, String, DateTime, Text, Integer, JSON, Boolean, Index, ForeignKey
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from datetime import datetime
import uuid

from backend.config.database import Base


class Conversation(Base):
    """对话表"""

    __tablename__ = "conversations"

    # 主键
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)

    # 基本信息
    title = Column(String(500), nullable=False, comment="对话标题")
    description = Column(Text, nullable=True, comment="对话描述")

    # 模型信息
    current_model = Column(String(50), nullable=False, default="claude", comment="当前使用的模型")
    model_history = Column(JSONB, nullable=True, comment="模型切换历史")

    # 状态
    status = Column(
        String(20),
        nullable=False,
        default="active",
        comment="状态: active, archived, deleted"
    )
    is_pinned = Column(Boolean, default=False, comment="是否置顶")
    # 群组共享标志：True 时允许其他授权用户（如 superadmin）向该对话发送消息
    # 预留群组聊天扩展点，当前默认 False（仅查看模式）
    is_shared = Column(Boolean, default=False, comment="是否共享（群组聊天预留字段）")

    # 归属用户（nullable：ENABLE_AUTH=false 或迁移前数据时为 NULL）
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True, comment="归属用户ID")

    # 分组
    group_id = Column(UUID(as_uuid=True), ForeignKey("conversation_groups.id", ondelete="SET NULL"), nullable=True, comment="所属分组ID")

    # 统计信息
    message_count = Column(Integer, default=0, comment="消息数量")
    total_tokens = Column(Integer, default=0, comment="总token数")

    # 元数据
    extra_metadata = Column(JSONB, nullable=True, comment="额外元数据")
    tags = Column(JSONB, nullable=True, comment="标签列表")

    # 时间戳
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, comment="创建时间")
    updated_at = Column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
        comment="更新时间"
    )
    last_message_at = Column(DateTime, nullable=True, comment="最后消息时间")

    # 关系
    messages = relationship(
        "Message",
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="Message.created_at"
    )
    tasks = relationship(
        "Task",
        back_populates="conversation",
        cascade="all, delete-orphan"
    )
    group = relationship(
        "ConversationGroup",
        back_populates="conversations",
        foreign_keys=[group_id]
    )

    # 索引
    __table_args__ = (
        Index("idx_conversations_status", "status"),
        Index("idx_conversations_created_at", "created_at"),
        Index("idx_conversations_updated_at", "updated_at"),
        Index("idx_conversations_group_id", "group_id"),
    )

    @property
    def system_prompt(self):
        """从 extra_metadata 中获取 system_prompt"""
        if self.extra_metadata:
            return self.extra_metadata.get('system_prompt')
        return None

    @system_prompt.setter
    def system_prompt(self, value):
        """设置 system_prompt 到 extra_metadata"""
        if self.extra_metadata is None:
            self.extra_metadata = {}
        if value:
            self.extra_metadata['system_prompt'] = value
        elif 'system_prompt' in self.extra_metadata:
            del self.extra_metadata['system_prompt']

    def __repr__(self):
        return f"<Conversation(id={self.id}, title={self.title}, model={self.current_model})>"

    def to_dict(self):
        """转换为字典"""
        return {
            "id": str(self.id),
            "title": self.title,
            "description": self.description,
            "current_model": self.current_model,
            "model_history": self.model_history,
            "status": self.status,
            "is_pinned": self.is_pinned,
            "is_shared": self.is_shared or False,
            "user_id": str(self.user_id) if self.user_id else None,
            "group_id": str(self.group_id) if self.group_id else None,
            "message_count": self.message_count,
            "total_tokens": self.total_tokens,
            "system_prompt": self.system_prompt,  # 添加 system_prompt
            "extra_metadata": self.extra_metadata,
            "tags": self.tags,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "last_message_at": self.last_message_at.isoformat() if self.last_message_at else None,
        }


class Message(Base):
    """消息表"""

    __tablename__ = "messages"

    # 主键
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)

    # 外键
    conversation_id = Column(
        UUID(as_uuid=True),
        ForeignKey('conversations.id'),
        nullable=False,
        index=True,
        comment="对话ID"
    )

    # 消息内容
    role = Column(String(20), nullable=False, comment="角色: user, assistant, system")
    content = Column(Text, nullable=False, comment="消息内容")

    # 模型信息
    model = Column(String(50), nullable=True, comment="使用的模型")
    model_params = Column(JSONB, nullable=True, comment="模型参数")

    # Token统计
    prompt_tokens = Column(Integer, default=0, comment="提示词token数")
    completion_tokens = Column(Integer, default=0, comment="完成token数")
    total_tokens = Column(Integer, default=0, comment="总token数")

    # 附加数据
    artifacts = Column(JSONB, nullable=True, comment="附件数据(SQL、脚本、图表配置等)")
    tool_calls = Column(JSONB, nullable=True, comment="工具调用记录")
    tool_results = Column(JSONB, nullable=True, comment="工具执行结果")

    # 元数据
    extra_metadata = Column(JSONB, nullable=True, comment="额外元数据")

    # 时间戳
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, comment="创建时间")

    # 关系
    conversation = relationship("Conversation", back_populates="messages")

    # 索引
    __table_args__ = (
        Index("idx_messages_conversation_id", "conversation_id"),
        Index("idx_messages_role", "role"),
        Index("idx_messages_created_at", "created_at"),
    )

    def __repr__(self):
        return f"<Message(id={self.id}, role={self.role}, conversation_id={self.conversation_id})>"

    def to_dict(self):
        """转换为字典"""
        d = {
            "id": str(self.id),
            "conversation_id": str(self.conversation_id),
            "role": self.role,
            "content": self.content,
            "model": self.model,
            "model_params": self.model_params,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "artifacts": self.artifacts,
            "tool_calls": self.tool_calls,
            "tool_results": self.tool_results,
            "extra_metadata": self.extra_metadata,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
        # 将 thinking_events 提升为顶层字段，方便前端加载历史推理过程
        if self.extra_metadata and "thinking_events" in self.extra_metadata:
            d["thinking_events"] = self.extra_metadata["thinking_events"]
        return d


class ContextSnapshot(Base):
    """上下文快照表（用于上下文压缩和恢复）"""

    __tablename__ = "context_snapshots"

    # 主键
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)

    # 外键
    conversation_id = Column(
        UUID(as_uuid=True),
        ForeignKey('conversations.id'),
        nullable=False,
        index=True,
        comment="对话ID"
    )

    # 快照信息
    snapshot_type = Column(
        String(20),
        nullable=False,
        comment="快照类型: full, compressed, summary"
    )
    message_count = Column(Integer, default=0, comment="包含的消息数")
    start_message_id = Column(UUID(as_uuid=True), nullable=True, comment="起始消息ID")
    end_message_id = Column(UUID(as_uuid=True), nullable=True, comment="结束消息ID")

    # 快照内容
    content = Column(JSONB, nullable=False, comment="快照内容")
    summary = Column(Text, nullable=True, comment="摘要")
    key_facts = Column(JSONB, nullable=True, comment="关键事实")
    artifacts = Column(JSONB, nullable=True, comment="生成的工件")

    # 元数据
    extra_metadata = Column(JSONB, nullable=True, comment="额外元数据")

    # 时间戳
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, comment="创建时间")

    # 索引
    __table_args__ = (
        Index("idx_context_snapshots_conversation_id", "conversation_id"),
        Index("idx_context_snapshots_type", "snapshot_type"),
        Index("idx_context_snapshots_created_at", "created_at"),
    )

    def __repr__(self):
        return f"<ContextSnapshot(id={self.id}, type={self.snapshot_type}, conversation_id={self.conversation_id})>"

    def to_dict(self):
        """转换为字典"""
        return {
            "id": str(self.id),
            "conversation_id": str(self.conversation_id),
            "snapshot_type": self.snapshot_type,
            "message_count": self.message_count,
            "start_message_id": str(self.start_message_id) if self.start_message_id else None,
            "end_message_id": str(self.end_message_id) if self.end_message_id else None,
            "content": self.content,
            "summary": self.summary,
            "key_facts": self.key_facts,
            "artifacts": self.artifacts,
            "extra_metadata": self.extra_metadata,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
