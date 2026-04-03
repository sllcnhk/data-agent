"""
Unified Context Manager - Phase 2.4

提供简洁的 API，隐藏底层复杂性

借鉴: Semantic Kernel 的统一接口设计
"""
from typing import Dict, Any, List, Optional
import logging

from backend.core.context_manager import HybridContextManager
from backend.core.token_budget import get_token_budget_manager
from backend.core.adaptive_strategy import get_adaptive_strategy_selector
from backend.core.token_counter import get_token_counter
from backend.core.conversation_format import UnifiedConversation, UnifiedMessage, MessageRole
from backend.database.connection import get_db_context
from backend.services.conversation_service import ConversationService

logger = logging.getLogger(__name__)


class UnifiedContextManager:
    """统一上下文管理器 - 对外的简洁 API"""

    def __init__(self):
        """初始化统一上下文管理器"""
        self.hybrid_manager = HybridContextManager()
        self.token_budget_manager = get_token_budget_manager()
        self.adaptive_selector = get_adaptive_strategy_selector()
        self.token_counter = get_token_counter()

        logger.info("UnifiedContextManager initialized")

    def prepare_context(
        self,
        conversation_id: str,
        model: str,
        system_prompt: str,
        current_message: str,
        max_tokens: Optional[int] = None,
        strategy: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        准备对话上下文 - 统一入口

        Args:
            conversation_id: 对话ID
            model: 模型名称
            system_prompt: 系统提示
            current_message: 当前消息
            max_tokens: 最大token（可选）
            strategy: 指定策略（可选，默认自动选择）

        Returns:
            {
                "messages": [...],
                "system_prompt": "...",
                "context_info": {...},
                "budget_info": {...}
            }
        """
        logger.debug(
            f"Preparing context for conversation {conversation_id}, "
            f"model={model}, strategy={strategy}"
        )

        try:
            # 1. 获取对话和消息
            with get_db_context() as db:
                service = ConversationService(db)
                conversation = service.get_conversation(conversation_id)
                messages = service.get_messages(conversation_id, limit=10000)

            # 2. 转换为统一格式
            unified_conv = self._to_unified_conversation(conversation, messages)

            # 3. 使用统一的压缩逻辑
            result = self.prepare_context_from_unified(
                unified_conv=unified_conv,
                model=model,
                system_prompt=system_prompt,
                current_message=current_message,
                max_tokens=max_tokens,
                strategy=strategy
            )

            logger.debug(
                f"Context prepared: {result['context_info']['compressed_message_count']} messages, "
                f"strategy={result['context_info']['strategy']}"
            )

            return result

        except Exception as e:
            logger.error(f"Error preparing context: {e}", exc_info=True)
            raise

    def prepare_context_from_unified(
        self,
        unified_conv: UnifiedConversation,
        model: str,
        system_prompt: str,
        current_message: str,
        max_tokens: Optional[int] = None,
        strategy: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        从统一对话格式准备上下文（无需数据库访问）

        Args:
            unified_conv: 统一对话对象
            model: 模型名称
            system_prompt: 系统提示
            current_message: 当前消息
            max_tokens: 最大token（可选）
            strategy: 指定策略（可选，默认自动选择）

        Returns:
            上下文字典
        """
        # 1. 创建 token 预算
        budget = self.token_budget_manager.create_budget(
            model, system_prompt, current_message
        )

        # 2. 选择策略（自动或手动）
        if strategy is None:
            strategy, params = self.adaptive_selector.select_strategy(
                unified_conv, model, system_prompt, current_message
            )
            logger.debug(f"Auto-selected strategy: {strategy} with params {params}")
        else:
            # 手动指定策略时，使用默认参数
            params = {
                "strategy": strategy,
                "intensity": "normal"
            }
            logger.debug(f"Manually specified strategy: {strategy}")

        # 3. 应用压缩
        original_message_count = len(unified_conv.messages)

        # 设置策略并压缩
        self.hybrid_manager.set_strategy(strategy)

        # 根据参数应用压缩
        if strategy == "full":
            compressed_conv = unified_conv  # 不压缩
        elif strategy == "sliding_window":
            keep_first = params.get("keep_first", 3)
            keep_last = params.get("keep_last", 15)
            compressed_conv = self.hybrid_manager.compress_conversation(
                unified_conv,
                keep_first=keep_first,
                keep_last=keep_last
            )
        elif strategy == "smart":
            keep_first = params.get("keep_first", 2)
            keep_last = params.get("keep_last", 10)
            compressed_conv = self.hybrid_manager.compress_conversation(
                unified_conv,
                keep_first=keep_first,
                keep_last=keep_last,
                use_summary=params.get("use_summary", True)
            )
        else:
            # 未知策略，使用默认压缩
            logger.warning(f"Unknown strategy {strategy}, using smart compression")
            compressed_conv = self.hybrid_manager.compress_conversation(
                unified_conv,
                keep_first=2,
                keep_last=10
            )

        compressed_message_count = len(compressed_conv.messages)

        # 4. 估算 token 使用量
        estimated_tokens = self._estimate_context_tokens(
            system_prompt, compressed_conv.messages, model
        )

        # 5. 计算压缩率
        compression_ratio = (
            1 - compressed_message_count / original_message_count
            if original_message_count > 0 else 0
        )

        # 6. 构建返回结果
        return {
            "messages": [
                {
                    "role": msg.role.value if hasattr(msg.role, 'value') else str(msg.role),
                    "content": msg.content
                }
                for msg in compressed_conv.messages
            ],
            "system_prompt": system_prompt,
            "context_info": {
                "strategy": strategy,
                "strategy_params": params,
                "original_message_count": original_message_count,
                "compressed_message_count": compressed_message_count,
                "compression_ratio": compression_ratio,
                "estimated_tokens": estimated_tokens
            },
            "budget_info": budget
        }

    def _to_unified_conversation(
        self,
        conversation,
        messages: List
    ) -> UnifiedConversation:
        """
        转换为统一格式

        Args:
            conversation: 数据库对话对象
            messages: 数据库消息列表

        Returns:
            UnifiedConversation 对象
        """
        unified_conv = UnifiedConversation(
            conversation_id=str(conversation.id),
            title=conversation.title,
            system_prompt=conversation.metadata.get("system_prompt", "") if conversation.metadata else ""
        )

        for msg in messages:
            try:
                # 处理角色枚举
                role_value = msg.role if isinstance(msg.role, str) else msg.role.value
                role = MessageRole(role_value)
            except (ValueError, AttributeError) as e:
                logger.warning(f"Invalid role {msg.role}, defaulting to USER: {e}")
                role = MessageRole.USER

            unified_msg = UnifiedMessage(
                role=role,
                content=msg.content or "",
                metadata={"tokens": msg.total_tokens or 0}
            )

            # 添加 token_count 字段（兼容性）
            if msg.total_tokens:
                unified_msg.token_count = msg.total_tokens

            unified_conv.add_message(unified_msg)

        return unified_conv

    def _estimate_context_tokens(
        self,
        system_prompt: str,
        messages: List[UnifiedMessage],
        model: str
    ) -> int:
        """
        估算上下文 token 数

        Args:
            system_prompt: 系统提示
            messages: 消息列表
            model: 模型名称

        Returns:
            估算的 token 数
        """
        # 系统提示 tokens
        system_tokens = self.token_counter.count_tokens(system_prompt, model)

        # 消息 tokens
        message_tokens = sum(
            self.token_counter.count_tokens(msg.content, model)
            for msg in messages
        )

        return system_tokens + message_tokens

    def get_context_statistics(
        self,
        conversation_id: str,
        model: str
    ) -> Dict[str, Any]:
        """
        获取对话的上下文统计信息（不压缩）

        Args:
            conversation_id: 对话ID
            model: 模型名称

        Returns:
            统计信息字典
        """
        try:
            with get_db_context() as db:
                service = ConversationService(db)
                conversation = service.get_conversation(conversation_id)
                messages = service.get_messages(conversation_id, limit=10000)

            unified_conv = self._to_unified_conversation(conversation, messages)

            total_tokens = sum(
                msg.token_count or msg.metadata.get("tokens", 0)
                for msg in unified_conv.messages
            )

            return {
                "conversation_id": conversation_id,
                "total_messages": len(unified_conv.messages),
                "total_tokens": total_tokens,
                "avg_tokens_per_message": total_tokens / len(unified_conv.messages) if unified_conv.messages else 0,
                "model": model
            }

        except Exception as e:
            logger.error(f"Error getting context statistics: {e}", exc_info=True)
            return {
                "conversation_id": conversation_id,
                "total_messages": 0,
                "total_tokens": 0,
                "avg_tokens_per_message": 0,
                "model": model,
                "error": str(e)
            }


# 全局实例
_unified_context_manager = None


def get_unified_context_manager() -> UnifiedContextManager:
    """
    获取 UnifiedContextManager 单例

    Returns:
        UnifiedContextManager 实例
    """
    global _unified_context_manager
    if _unified_context_manager is None:
        _unified_context_manager = UnifiedContextManager()
    return _unified_context_manager
