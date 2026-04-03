"""
混合上下文管理器

提供多种上下文管理策略:
1. 完整保留(Full): 保留所有消息
2. 滑动窗口(Sliding Window): 保留最近N条消息
3. 智能压缩(Compressed): 保留重要消息,压缩历史
4. 语义压缩(Semantic): 使用向量数据库进行语义检索和压缩
"""
from typing import List, Optional, Dict, Any
from datetime import datetime
import json

from backend.core.conversation_format import (
    UnifiedConversation,
    UnifiedMessage,
    MessageRole,
    ConversationSummary
)
from backend.models.conversation import ContextSnapshot
from backend.config.settings import settings


class BaseContextStrategy:
    """上下文管理策略基类"""

    def compress(
        self,
        conversation: UnifiedConversation,
        max_messages: int
    ) -> UnifiedConversation:
        """
        压缩对话上下文

        Args:
            conversation: 原始对话
            max_messages: 最大消息数

        Returns:
            压缩后的对话
        """
        raise NotImplementedError


class FullContextStrategy(BaseContextStrategy):
    """完整保留策略 - 保留所有消息"""

    def compress(
        self,
        conversation: UnifiedConversation,
        max_messages: int
    ) -> UnifiedConversation:
        """不进行压缩,返回原对话"""
        return conversation


class SlidingWindowStrategy(BaseContextStrategy):
    """滑动窗口策略 - 保留最近N条消息"""

    def compress(
        self,
        conversation: UnifiedConversation,
        max_messages: int
    ) -> UnifiedConversation:
        """保留最近的max_messages条消息"""
        if len(conversation.messages) <= max_messages:
            return conversation

        # 创建新对话,保留最近的消息
        compressed = UnifiedConversation(
            conversation_id=conversation.conversation_id,
            title=conversation.title,
            model=conversation.model,
            system_prompt=conversation.system_prompt,
            max_context_length=max_messages,
            context_strategy="sliding_window"
        )

        # 保留最近的消息
        recent_messages = conversation.messages[-max_messages:]
        for msg in recent_messages:
            compressed.add_message(msg)

        return compressed


class SmartCompressionStrategy(BaseContextStrategy):
    """
    智能压缩策略

    策略:
    1. 保留首次对话(建立上下文)
    2. 保留最近N条消息
    3. 中间消息进行摘要压缩
    """

    def __init__(self, keep_first: int = 2, keep_recent: int = 10):
        """
        Args:
            keep_first: 保留最初的消息数
            keep_recent: 保留最近的消息数
        """
        self.keep_first = keep_first
        self.keep_recent = keep_recent

    def compress(
        self,
        conversation: UnifiedConversation,
        max_messages: int,
        llm_summary: str = "",
    ) -> UnifiedConversation:
        """智能压缩对话

        Args:
            conversation: 原始对话
            max_messages: 最大消息数限制
            llm_summary: 预先由 LLM 生成的摘要字符串（优先使用）。
                         为空时退化为规则生成摘要。
        """
        total_messages = len(conversation.messages)

        # 如果消息数不超过限制,不压缩
        if total_messages <= max_messages:
            return conversation

        # 创建压缩后的对话
        compressed = UnifiedConversation(
            conversation_id=conversation.conversation_id,
            title=conversation.title,
            model=conversation.model,
            system_prompt=conversation.system_prompt,
            max_context_length=max_messages,
            context_strategy="compressed"
        )

        messages = conversation.messages

        # 1. 保留最初的消息
        first_messages = messages[:self.keep_first]
        for msg in first_messages:
            compressed.add_message(msg)

        # 2. 压缩中间消息（优先用 LLM 摘要，否则规则摘要）
        middle_start = self.keep_first
        middle_end = total_messages - self.keep_recent

        if middle_end > middle_start:
            middle_messages = messages[middle_start:middle_end]
            summary = self._summarize_messages(middle_messages, llm_summary=llm_summary)

            # 添加摘要消息（注入格式与 Claude Code 风格对齐）
            preamble = (
                "本对话从之前较长的历史继续。"
                "以下摘要涵盖了较早部分的对话内容：\n\n"
            ) if llm_summary else "[历史对话摘要]\n"
            compressed.add_system_message(
                preamble + summary,
                metadata={
                    "type": "summary",
                    "message_count": len(middle_messages),
                    "llm_generated": bool(llm_summary),
                }
            )

        # 3. 保留最近的消息
        recent_messages = messages[-self.keep_recent:]
        for msg in recent_messages:
            compressed.add_message(msg)

        return compressed

    def _summarize_messages(
        self,
        messages: List[UnifiedMessage],
        llm_summary: str = "",
    ) -> str:
        """对消息进行摘要。

        若提供了 llm_summary（由 ConversationSummarizer 生成），直接使用；
        否则退化为规则摘要（第一行 + 截断）。
        """
        if llm_summary:
            return llm_summary

        # 规则摘要（fallback）：角色 + 内容前 100 字
        summary_parts = []
        for i, msg in enumerate(messages, 1):
            role = msg.role.value if hasattr(msg.role, 'value') else str(msg.role)
            content_preview = msg.content[:100] + "..." if len(msg.content) > 100 else msg.content
            if msg.has_tool_calls():
                tools = ", ".join([tc.name for tc in msg.tool_calls])
                summary_parts.append(f"{i}. [{role}] 调用工具: {tools}")
            else:
                summary_parts.append(f"{i}. [{role}] {content_preview}")

        return "\n".join(summary_parts)


class SemanticCompressionStrategy(BaseContextStrategy):
    """
    语义压缩策略

    使用向量数据库存储消息,根据当前查询检索相关历史
    """

    def __init__(self, vector_db_path: Optional[str] = None):
        """
        Args:
            vector_db_path: 向量数据库路径
        """
        self.vector_db_path = vector_db_path or settings.vector_db_path
        # TODO: 初始化向量数据库(Chroma, FAISS等)
        self.vector_db = None

    def compress(
        self,
        conversation: UnifiedConversation,
        max_messages: int
    ) -> UnifiedConversation:
        """
        基于语义相关性压缩对话

        目前实现简化版,返回滑动窗口结果
        完整实现需要集成向量数据库
        """
        # TODO: 实现真正的语义压缩
        # 1. 获取最后一条用户消息
        # 2. 在向量数据库中检索相关历史消息
        # 3. 组合最相关的历史 + 最近的消息

        # 暂时使用滑动窗口策略
        strategy = SlidingWindowStrategy()
        return strategy.compress(conversation, max_messages)


class HybridContextManager:
    """混合上下文管理器"""

    # 策略映射
    STRATEGIES = {
        "full": FullContextStrategy,
        "sliding_window": SlidingWindowStrategy,
        "compressed": SmartCompressionStrategy,
        "smart": SmartCompressionStrategy,  # 别名: smart = compressed
        "semantic": SemanticCompressionStrategy,
    }

    def __init__(
        self,
        strategy: str = "sliding_window",
        max_context_length: int = 20,
        **strategy_kwargs
    ):
        """
        初始化上下文管理器

        Args:
            strategy: 压缩策略名称
            max_context_length: 最大上下文长度
            **strategy_kwargs: 策略特定参数
        """
        self.strategy_name = strategy
        self.max_context_length = max_context_length

        # 创建策略实例
        strategy_class = self.STRATEGIES.get(strategy)
        if not strategy_class:
            raise ValueError(
                f"不支持的压缩策略: {strategy}. "
                f"支持的策略: {list(self.STRATEGIES.keys())}"
            )

        self.strategy = strategy_class(**strategy_kwargs)

    def compress_conversation(
        self,
        conversation: UnifiedConversation,
        llm_summary: str = "",
    ) -> UnifiedConversation:
        """
        压缩对话上下文

        Args:
            conversation: 原始对话
            llm_summary: 预先由 LLM 生成的历史摘要（优先使用）。
                         仅 SmartCompressionStrategy 会使用此参数；
                         其他策略忽略它。

        Returns:
            压缩后的对话
        """
        if hasattr(self.strategy, 'compress') and llm_summary:
            import inspect
            sig = inspect.signature(self.strategy.compress)
            if 'llm_summary' in sig.parameters:
                return self.strategy.compress(
                    conversation, self.max_context_length, llm_summary=llm_summary
                )
        return self.strategy.compress(conversation, self.max_context_length)

    def create_snapshot(
        self,
        conversation: UnifiedConversation,
        snapshot_type: str = "compressed"
    ) -> Dict[str, Any]:
        """
        创建上下文快照

        Args:
            conversation: 对话
            snapshot_type: 快照类型(full, compressed, summary)

        Returns:
            快照数据
        """
        if snapshot_type == "full":
            content = conversation.to_dict()
        elif snapshot_type == "compressed":
            compressed = self.compress_conversation(conversation)
            content = compressed.to_dict()
        elif snapshot_type == "summary":
            content = self._create_summary(conversation)
        else:
            raise ValueError(f"不支持的快照类型: {snapshot_type}")

        return {
            "snapshot_type": snapshot_type,
            "content": content,
            "message_count": len(conversation.messages),
            "total_tokens": conversation.total_tokens,
            "created_at": datetime.utcnow().isoformat(),
        }

    def restore_from_snapshot(
        self,
        snapshot_data: Dict[str, Any]
    ) -> UnifiedConversation:
        """
        从快照恢复对话

        Args:
            snapshot_data: 快照数据

        Returns:
            恢复的对话
        """
        content = snapshot_data["content"]

        if snapshot_data["snapshot_type"] == "summary":
            # 如果是摘要,创建新对话并添加摘要
            conversation = UnifiedConversation()
            conversation.add_system_message(
                f"[历史对话摘要]\n{content['summary']}"
            )
            return conversation
        else:
            # 完整或压缩快照,直接恢复
            return UnifiedConversation.from_dict(content)

    def _create_summary(self, conversation: UnifiedConversation) -> Dict[str, Any]:
        """创建对话摘要"""
        # 简单的摘要实现
        user_messages = [m for m in conversation.messages if m.role == MessageRole.USER]
        assistant_messages = [m for m in conversation.messages if m.role == MessageRole.ASSISTANT]

        summary_text = f"""
对话概要:
- 标题: {conversation.title or '未命名'}
- 消息数: {len(conversation.messages)}
- 用户消息: {len(user_messages)}
- 助手回复: {len(assistant_messages)}
- 总Token数: {conversation.total_tokens}
        """.strip()

        return {
            "summary": summary_text,
            "key_points": [],
            "message_count": len(conversation.messages),
        }

    @classmethod
    def create_from_settings(cls) -> "HybridContextManager":
        """从settings创建上下文管理器"""
        return cls(
            strategy=settings.context_compression_strategy,
            max_context_length=settings.max_context_messages
        )


# 辅助函数

def compress_conversation(
    conversation: UnifiedConversation,
    strategy: str = "sliding_window",
    max_messages: int = 20
) -> UnifiedConversation:
    """
    便捷函数: 压缩对话

    Args:
        conversation: 原始对话
        strategy: 压缩策略
        max_messages: 最大消息数

    Returns:
        压缩后的对话
    """
    manager = HybridContextManager(strategy=strategy, max_context_length=max_messages)
    return manager.compress_conversation(conversation)


# 示例用法
if __name__ == "__main__":
    # 创建长对话
    conversation = UnifiedConversation(
        conversation_id="test_conv",
        title="测试对话",
        system_prompt="你是一个AI助手"
    )

    # 添加多条消息
    for i in range(30):
        conversation.add_user_message(f"用户消息 {i+1}")
        conversation.add_assistant_message(f"助手回复 {i+1}")

    print(f"原始消息数: {len(conversation.messages)}")

    # 测试滑动窗口策略
    manager_sliding = HybridContextManager(strategy="sliding_window", max_context_length=10)
    compressed_sliding = manager_sliding.compress_conversation(conversation)
    print(f"滑动窗口压缩后: {len(compressed_sliding.messages)}")

    # 测试智能压缩策略
    manager_smart = HybridContextManager(
        strategy="compressed",
        max_context_length=15,
        keep_first=2,
        keep_recent=8
    )
    compressed_smart = manager_smart.compress_conversation(conversation)
    print(f"智能压缩后: {len(compressed_smart.messages)}")

    # 创建快照
    snapshot = manager_smart.create_snapshot(conversation, snapshot_type="compressed")
    print(f"快照创建时间: {snapshot['created_at']}")

    # 恢复快照
    restored = manager_smart.restore_from_snapshot(snapshot)
    print(f"恢复后消息数: {len(restored.messages)}")
