"""
数据模型包

导出所有ORM模型供应用使用
"""

# RBAC 模型（必须在其他模型之前导入，确保 SQLAlchemy mapper 按依赖顺序注册）
from backend.models.permission import Permission       # noqa
from backend.models.role import Role                   # noqa
from backend.models.user import User                   # noqa
from backend.models.refresh_token import RefreshToken  # noqa
from backend.models.user_role import UserRole          # noqa
from backend.models.role_permission import RolePermission  # noqa

# Conversation模块
from backend.models.conversation import (
    Conversation,
    Message,
    ContextSnapshot
)
from backend.models.conversation_group import ConversationGroup

# Task模块
from backend.models.task import (
    Task,
    TaskHistory,
    TaskType,
    TaskStatus
)

# Report模块
from backend.models.report import (
    Report,
    Chart,
    ReportType,
    ChartType,
    ShareScope
)

__all__ = [
    # Conversation
    "Conversation",
    "Message",
    "ContextSnapshot",
    "ConversationGroup",

    # Task
    "Task",
    "TaskHistory",
    "TaskType",
    "TaskStatus",

    # Report
    "Report",
    "Chart",
    "ReportType",
    "ChartType",
    "ShareScope",
]
