"""
核心模块

包含对话格式、模型适配器和上下文管理器
"""
from backend.core.conversation_format import (
    UnifiedConversation,
    UnifiedMessage,
    MessageRole,
    ToolCall,
    ToolResult,
    Artifact,
    ConversationSummary,
    create_sql_artifact,
    create_chart_artifact,
    create_etl_artifact,
    create_table_data_artifact,
)

from backend.core.model_adapters import (
    BaseModelAdapter,
    ClaudeAdapter,
    ModelAdapterFactory,
)
# Optional adapters — may be None if the underlying SDK is not installed
try:
    from backend.core.model_adapters import OpenAIAdapter
except (ImportError, AttributeError):
    OpenAIAdapter = None  # type: ignore[assignment]
try:
    from backend.core.model_adapters import GeminiAdapter
except (ImportError, AttributeError):
    GeminiAdapter = None  # type: ignore[assignment]

from backend.core.context_manager import (
    HybridContextManager,
    compress_conversation,
)

__all__ = [
    # Conversation Format
    "UnifiedConversation",
    "UnifiedMessage",
    "MessageRole",
    "ToolCall",
    "ToolResult",
    "Artifact",
    "ConversationSummary",
    "create_sql_artifact",
    "create_chart_artifact",
    "create_etl_artifact",
    "create_table_data_artifact",

    # Model Adapters
    "BaseModelAdapter",
    "ClaudeAdapter",
    "OpenAIAdapter",
    "GeminiAdapter",
    "ModelAdapterFactory",

    # Context Manager
    "HybridContextManager",
    "compress_conversation",
]
