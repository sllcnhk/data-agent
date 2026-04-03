"""
对话分组模型

提供对话的分组管理功能，类似文件夹
"""
from sqlalchemy import Column, String, Text, Integer, Boolean, DateTime, Index, ForeignKey
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.postgresql import UUID
from datetime import datetime
import uuid

from backend.config.database import Base


class ConversationGroup(Base):
    """对话分组模型"""

    __tablename__ = "conversation_groups"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, comment="分组ID")

    # 归属用户（nullable：ENABLE_AUTH=false 或迁移前数据时为 NULL）
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True, comment="归属用户ID")

    name = Column(String(100), nullable=False, comment="分组名称")
    description = Column(Text, comment="分组描述")
    icon = Column(String(50), comment="图标（emoji或图标名）")
    color = Column(String(20), comment="颜色标识")
    sort_order = Column(Integer, default=0, comment="排序顺序，数字越小越靠前")
    is_expanded = Column(Boolean, default=True, comment="是否展开（前端状态）")
    conversation_count = Column(Integer, default=0, comment="对话数量（冗余字段）")
    created_at = Column(DateTime, default=datetime.utcnow, comment="创建时间")
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, comment="更新时间")

    # 关系
    conversations = relationship(
        "Conversation",
        back_populates="group",
        foreign_keys="[Conversation.group_id]"
    )

    # 索引
    __table_args__ = (
        Index("idx_conversation_groups_sort_order", "sort_order"),
        Index("idx_conversation_groups_name", "name"),
        Index("idx_conversation_groups_created_at", "created_at"),
    )

    def to_dict(self, include_conversations=False):
        """
        转换为字典

        Args:
            include_conversations: 是否包含对话列表

        Returns:
            字典表示
        """
        data = {
            "id": str(self.id),
            "user_id": str(self.user_id) if self.user_id else None,
            "name": self.name,
            "description": self.description,
            "icon": self.icon,
            "color": self.color,
            "sort_order": self.sort_order,
            "is_expanded": self.is_expanded,
            "conversation_count": self.conversation_count,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None
        }

        if include_conversations and self.conversations:
            data["conversations"] = [conv.to_dict() for conv in self.conversations]

        return data

    def __repr__(self):
        return f"<ConversationGroup(id={self.id}, name={self.name}, count={self.conversation_count})>"
