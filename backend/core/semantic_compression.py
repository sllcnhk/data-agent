"""
Semantic Compression Strategy - Phase 3.3

基于语义相似度的智能压缩

借鉴: LlamaIndex 的语义检索
"""
from typing import List, Optional, Dict, Any
import logging
import numpy as np

from backend.core.conversation_format import UnifiedConversation, UnifiedMessage, MessageRole
from backend.core.embedding_service import EmbeddingService

logger = logging.getLogger(__name__)


class SemanticCompressionStrategy:
    """语义压缩策略 - 基于语义相似度"""

    def __init__(
        self,
        keep_first: int = 2,
        keep_last: int = 10,
        similarity_threshold: float = 0.7,
        min_keep_ratio: float = 0.3,
        embedding_provider: str = "mock"
    ):
        """
        初始化语义压缩策略

        Args:
            keep_first: 保留最前面的消息数
            keep_last: 保留最后的消息数
            similarity_threshold: 语义相似度阈值 (0-1)
            min_keep_ratio: 最小保留比例（防止过度压缩）
            embedding_provider: embedding 提供商 ("local", "openai", "mock")
        """
        self.keep_first = keep_first
        self.keep_last = keep_last
        self.similarity_threshold = similarity_threshold
        self.min_keep_ratio = min_keep_ratio

        # 初始化 embedding 服务
        try:
            self.embedding_service = EmbeddingService(provider=embedding_provider)
        except Exception as e:
            logger.warning(f"Failed to initialize embedding service: {e}, using mock")
            self.embedding_service = EmbeddingService(provider="mock")

        logger.info(
            f"SemanticCompressionStrategy initialized: "
            f"keep_first={keep_first}, keep_last={keep_last}, "
            f"threshold={similarity_threshold}"
        )

    def compress(
        self,
        conversation: UnifiedConversation,
        query_context: Optional[str] = None
    ) -> UnifiedConversation:
        """
        语义压缩

        策略:
        1. 保留最前面的 N 条消息（上下文建立）
        2. 保留最后的 M 条消息（最近对话）
        3. 中间消息：
           - 计算与当前查询的语义相似度
           - 保留高相关性的消息
           - 过滤低相关性的消息

        Args:
            conversation: 对话对象
            query_context: 查询上下文（可选，默认使用最近消息）

        Returns:
            压缩后的对话
        """
        messages = conversation.messages

        # 如果消息数量较少，无需压缩
        if len(messages) <= (self.keep_first + self.keep_last):
            logger.debug(f"Conversation too short ({len(messages)}), no compression needed")
            return conversation

        logger.debug(f"Compressing conversation: {len(messages)} messages")

        # 1. 构建查询上下文（使用最近的消息）
        if query_context is None:
            recent_messages = messages[-min(self.keep_last, len(messages)):]
            query_context = " ".join([msg.content for msg in recent_messages])

        # 2. 分段
        first_messages = messages[:self.keep_first]
        last_messages = messages[-self.keep_last:]
        middle_messages = messages[self.keep_first:-self.keep_last]

        # 3. 对中间消息进行语义筛选
        if middle_messages:
            relevant_messages = self._select_relevant_messages(
                middle_messages,
                query_context
            )

            logger.debug(
                f"Middle messages: {len(middle_messages)} -> {len(relevant_messages)} "
                f"(kept {len(relevant_messages)/len(middle_messages)*100:.1f}%)"
            )
        else:
            relevant_messages = []

        # 4. 组合压缩后的对话
        compressed = UnifiedConversation(
            conversation_id=conversation.conversation_id,
            title=conversation.title,
            system_prompt=conversation.system_prompt
        )

        # 添加消息
        for msg in first_messages:
            compressed.add_message(msg)

        # 添加摘要标记（如果有压缩）
        if len(relevant_messages) < len(middle_messages):
            omitted_count = len(middle_messages) - len(relevant_messages)
            summary_msg = UnifiedMessage(
                role=MessageRole.SYSTEM,
                content=f"[Context compressed: {omitted_count} less relevant messages omitted]"
            )
            compressed.add_message(summary_msg)

        for msg in relevant_messages:
            compressed.add_message(msg)

        for msg in last_messages:
            compressed.add_message(msg)

        logger.info(
            f"Compression complete: {len(messages)} -> {len(compressed.messages)} "
            f"({(1 - len(compressed.messages)/len(messages))*100:.1f}% reduction)"
        )

        return compressed

    def _select_relevant_messages(
        self,
        messages: List[UnifiedMessage],
        query_context: str
    ) -> List[UnifiedMessage]:
        """
        选择语义相关的消息

        Args:
            messages: 消息列表
            query_context: 查询上下文

        Returns:
            相关消息列表
        """
        if not messages:
            return []

        try:
            # 1. 计算查询上下文的 embedding
            query_embedding = self.embedding_service.embed(query_context)

            # 2. 计算每条消息的 embedding 和相似度
            message_scores = []

            # 批量计算 embeddings (更高效)
            message_contents = [msg.content for msg in messages]
            message_embeddings = self.embedding_service.embed_batch(message_contents)

            for msg, msg_embedding in zip(messages, message_embeddings):
                similarity = self.embedding_service.cosine_similarity(
                    query_embedding, msg_embedding
                )
                message_scores.append((msg, similarity))

            # 3. 按相似度排序
            message_scores.sort(key=lambda x: x[1], reverse=True)

            # 4. 筛选高相关性消息
            relevant = [
                msg for msg, score in message_scores
                if score >= self.similarity_threshold
            ]

            # 5. 确保至少保留最小比例
            min_keep = max(1, int(len(messages) * self.min_keep_ratio))
            if len(relevant) < min_keep:
                relevant = [msg for msg, _ in message_scores[:min_keep]]

            # 6. 恢复原始顺序
            relevant_ordered = []
            for msg in messages:
                if msg in relevant:
                    relevant_ordered.append(msg)

            return relevant_ordered

        except Exception as e:
            logger.error(f"Failed to select relevant messages: {e}", exc_info=True)
            # 降级：返回所有消息
            return messages

    def get_compression_stats(
        self,
        original_count: int,
        compressed_count: int
    ) -> Dict[str, Any]:
        """
        获取压缩统计信息

        Args:
            original_count: 原始消息数
            compressed_count: 压缩后消息数

        Returns:
            统计信息
        """
        reduction_ratio = (
            1 - compressed_count / original_count
            if original_count > 0 else 0
        )

        return {
            "original_count": original_count,
            "compressed_count": compressed_count,
            "reduction_ratio": reduction_ratio,
            "kept_count": compressed_count,
            "omitted_count": original_count - compressed_count
        }


class HybridSemanticCompressionStrategy:
    """混合语义压缩策略 - 结合规则和语义"""

    def __init__(
        self,
        semantic_threshold: float = 0.7,
        redundancy_threshold: float = 0.9,
        keep_first: int = 2,
        keep_last: int = 10,
        embedding_provider: str = "mock"
    ):
        """
        初始化混合压缩策略

        Args:
            semantic_threshold: 语义相关性阈值
            redundancy_threshold: 冗余检测阈值（消息间相似度）
            keep_first: 保留首部消息数
            keep_last: 保留尾部消息数
            embedding_provider: embedding 提供商
        """
        self.semantic_threshold = semantic_threshold
        self.redundancy_threshold = redundancy_threshold
        self.keep_first = keep_first
        self.keep_last = keep_last

        try:
            self.embedding_service = EmbeddingService(provider=embedding_provider)
        except Exception as e:
            logger.warning(f"Failed to initialize embedding service: {e}, using mock")
            self.embedding_service = EmbeddingService(provider="mock")

    def compress(
        self,
        conversation: UnifiedConversation,
        query_context: Optional[str] = None
    ) -> UnifiedConversation:
        """
        混合压缩

        策略:
        1. 语义相关性过滤（保留与主题相关的消息）
        2. 冗余检测（去除重复内容）
        3. 保留首尾重要消息

        Args:
            conversation: 对话对象
            query_context: 查询上下文

        Returns:
            压缩后的对话
        """
        messages = conversation.messages

        if len(messages) <= (self.keep_first + self.keep_last):
            return conversation

        # 分段
        first_messages = messages[:self.keep_first]
        last_messages = messages[-self.keep_last:]
        middle_messages = messages[self.keep_first:-self.keep_last]

        if not middle_messages:
            return conversation

        # 构建查询上下文
        if query_context is None:
            query_context = " ".join([msg.content for msg in last_messages])

        # 1. 语义相关性过滤
        relevant = self._filter_by_relevance(middle_messages, query_context)

        # 2. 冗余检测
        non_redundant = self._remove_redundancy(relevant)

        # 3. 组合
        compressed = UnifiedConversation(
            conversation_id=conversation.conversation_id,
            title=conversation.title,
            system_prompt=conversation.system_prompt
        )

        for msg in first_messages + non_redundant + last_messages:
            compressed.add_message(msg)

        return compressed

    def _filter_by_relevance(
        self,
        messages: List[UnifiedMessage],
        query_context: str
    ) -> List[UnifiedMessage]:
        """根据相关性过滤消息"""
        if not messages:
            return []

        try:
            query_embedding = self.embedding_service.embed(query_context)
            message_contents = [msg.content for msg in messages]
            message_embeddings = self.embedding_service.embed_batch(message_contents)

            relevant = []
            for msg, emb in zip(messages, message_embeddings):
                similarity = self.embedding_service.cosine_similarity(query_embedding, emb)
                if similarity >= self.semantic_threshold:
                    relevant.append(msg)

            # 至少保留 30%
            if len(relevant) < len(messages) * 0.3:
                scores = [
                    (msg, self.embedding_service.cosine_similarity(query_embedding, emb))
                    for msg, emb in zip(messages, message_embeddings)
                ]
                scores.sort(key=lambda x: x[1], reverse=True)
                relevant = [msg for msg, _ in scores[:max(1, len(messages) // 3)]]

            return relevant

        except Exception as e:
            logger.error(f"Relevance filtering failed: {e}")
            return messages

    def _remove_redundancy(
        self,
        messages: List[UnifiedMessage]
    ) -> List[UnifiedMessage]:
        """去除冗余消息"""
        if len(messages) <= 1:
            return messages

        try:
            # 计算所有消息的 embeddings
            message_contents = [msg.content for msg in messages]
            embeddings = self.embedding_service.embed_batch(message_contents)

            # 保留非冗余消息
            kept = [messages[0]]  # 第一条总是保留
            kept_embeddings = [embeddings[0]]

            for i in range(1, len(messages)):
                # 检查与已保留消息的相似度
                is_redundant = False
                for kept_emb in kept_embeddings:
                    similarity = self.embedding_service.cosine_similarity(
                        embeddings[i], kept_emb
                    )
                    if similarity >= self.redundancy_threshold:
                        is_redundant = True
                        break

                if not is_redundant:
                    kept.append(messages[i])
                    kept_embeddings.append(embeddings[i])

            return kept

        except Exception as e:
            logger.error(f"Redundancy removal failed: {e}")
            return messages


# 全局实例
_semantic_compression_strategy = None


def get_semantic_compression_strategy(
    similarity_threshold: float = 0.7,
    embedding_provider: str = "mock"
) -> SemanticCompressionStrategy:
    """
    获取 SemanticCompressionStrategy 单例

    Args:
        similarity_threshold: 相似度阈值
        embedding_provider: embedding 提供商

    Returns:
        SemanticCompressionStrategy 实例
    """
    global _semantic_compression_strategy
    if _semantic_compression_strategy is None:
        _semantic_compression_strategy = SemanticCompressionStrategy(
            similarity_threshold=similarity_threshold,
            embedding_provider=embedding_provider
        )
    return _semantic_compression_strategy
