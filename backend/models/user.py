"""
用户模型 — RBAC 基础
"""
from sqlalchemy import Column, String, Boolean, DateTime, Text, JSON, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from datetime import datetime
import uuid

from backend.config.database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    username = Column(String(64), unique=True, nullable=False, index=True)
    display_name = Column(String(128), nullable=True)
    email = Column(String(256), nullable=True, unique=True)

    # 认证来源: local | lark | wecom | dingtalk
    auth_source = Column(String(20), nullable=False, default="local")
    # OAuth 外部 ID（如 Lark open_id）；本地账号为 null
    external_id = Column(String(256), nullable=True)
    # bcrypt 哈希；SSO 账号为 null
    hashed_password = Column(String(256), nullable=True)

    is_active = Column(Boolean, default=True, nullable=False)
    # 超级管理员：绕过所有权限检查
    is_superadmin = Column(Boolean, default=False, nullable=False)

    last_login_at = Column(DateTime, nullable=True)
    last_active_at = Column(DateTime, nullable=True)   # 最近 API 活动时间（节流更新），用于 session 空闲超时检测
    # 扩展字段：头像 URL、部门、Lark 用户名等
    extra_meta = Column(JSON, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    # Relationships
    user_roles = relationship("UserRole", back_populates="user", cascade="all, delete-orphan")
    refresh_tokens = relationship("RefreshToken", back_populates="user", cascade="all, delete-orphan")
