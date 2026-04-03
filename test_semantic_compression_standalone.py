"""
独立测试 Semantic Compression Strategy - Phase 3.3

测试基于语义相似度的智能压缩
"""
import sys
import numpy as np
from typing import List
from enum import Enum


# ============= Mock Classes =============

class MessageRole(str, Enum):
    """消息角色"""
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"


class MockMessage:
    """模拟消息"""
    def __init__(self, role: str, content: str):
        self.role = MessageRole(role)
        self.content = content


class MockConversation:
    """模拟对话"""
    def __init__(self, conversation_id: str = "test"):
        self.conversation_id = conversation_id
        self.title = "Test Conversation"
        self.system_prompt = "System"
        self.messages: List[MockMessage] = []

    def add_message(self, msg: MockMessage):
        self.messages.append(msg)


class MockEmbeddingService:
    """模拟 Embedding Service"""
    def __init__(self, dimension: int = 384):
        self.dimension = dimension

    def embed(self, text: str) -> List[float]:
        hash_val = hash(text)
        np.random.seed(hash_val % (2**31))
        embedding = np.random.randn(self.dimension)
        embedding = embedding / np.linalg.norm(embedding)
        return embedding.tolist()

    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        return [self.embed(text) for text in texts]

    def cosine_similarity(self, emb1: List[float], emb2: List[float]) -> float:
        vec1 = np.array(emb1)
        vec2 = np.array(emb2)
        vec1_norm = vec1 / np.linalg.norm(vec1)
        vec2_norm = vec2 / np.linalg.norm(vec2)
        return float(np.dot(vec1_norm, vec2_norm))


# ============= Semantic Compression Strategy =============

class SemanticCompressionStrategy:
    """语义压缩策略"""

    def __init__(
        self,
        keep_first: int = 2,
        keep_last: int = 10,
        similarity_threshold: float = 0.7,
        min_keep_ratio: float = 0.3
    ):
        self.keep_first = keep_first
        self.keep_last = keep_last
        self.similarity_threshold = similarity_threshold
        self.min_keep_ratio = min_keep_ratio
        self.embedding_service = MockEmbeddingService()

    def compress(
        self,
        conversation: MockConversation,
        query_context: str = None
    ) -> MockConversation:
        """语义压缩"""
        messages = conversation.messages

        if len(messages) <= (self.keep_first + self.keep_last):
            return conversation

        # 构建查询上下文
        if query_context is None:
            recent_messages = messages[-min(self.keep_last, len(messages)):]
            query_context = " ".join([msg.content for msg in recent_messages])

        # 分段
        first_messages = messages[:self.keep_first]
        last_messages = messages[-self.keep_last:]
        middle_messages = messages[self.keep_first:-self.keep_last]

        # 语义筛选
        if middle_messages:
            relevant_messages = self._select_relevant_messages(
                middle_messages,
                query_context
            )
        else:
            relevant_messages = []

        # 组合
        compressed = MockConversation(conversation.conversation_id)
        compressed.title = conversation.title
        compressed.system_prompt = conversation.system_prompt

        for msg in first_messages:
            compressed.add_message(msg)

        # 添加压缩标记
        if len(relevant_messages) < len(middle_messages):
            omitted = len(middle_messages) - len(relevant_messages)
            summary = MockMessage(
                "system",
                f"[Context compressed: {omitted} less relevant messages omitted]"
            )
            compressed.add_message(summary)

        for msg in relevant_messages:
            compressed.add_message(msg)

        for msg in last_messages:
            compressed.add_message(msg)

        return compressed

    def _select_relevant_messages(
        self,
        messages: List[MockMessage],
        query_context: str
    ) -> List[MockMessage]:
        """选择相关消息"""
        if not messages:
            return []

        # 计算查询 embedding
        query_embedding = self.embedding_service.embed(query_context)

        # 计算消息 embeddings
        message_contents = [msg.content for msg in messages]
        message_embeddings = self.embedding_service.embed_batch(message_contents)

        # 计算相似度
        message_scores = []
        for msg, emb in zip(messages, message_embeddings):
            similarity = self.embedding_service.cosine_similarity(query_embedding, emb)
            message_scores.append((msg, similarity))

        # 排序
        message_scores.sort(key=lambda x: x[1], reverse=True)

        # 筛选
        relevant = [
            msg for msg, score in message_scores
            if score >= self.similarity_threshold
        ]

        # 最小保留比例
        min_keep = max(1, int(len(messages) * self.min_keep_ratio))
        if len(relevant) < min_keep:
            relevant = [msg for msg, _ in message_scores[:min_keep]]

        # 恢复顺序
        relevant_ordered = []
        for msg in messages:
            if msg in relevant:
                relevant_ordered.append(msg)

        return relevant_ordered


# ============= 测试函数 =============

def test_semantic_compression_initialization():
    """测试初始化"""
    print("\n[TEST] Semantic Compression Initialization")
    print("-" * 60)

    strategy = SemanticCompressionStrategy(
        keep_first=2,
        keep_last=10,
        similarity_threshold=0.7
    )

    assert strategy is not None
    assert strategy.keep_first == 2
    assert strategy.keep_last == 10
    assert strategy.similarity_threshold == 0.7

    print("[PASS] Strategy initialized")
    print(f"  keep_first: {strategy.keep_first}")
    print(f"  keep_last: {strategy.keep_last}")
    print(f"  threshold: {strategy.similarity_threshold}")


def test_short_conversation_no_compression():
    """测试短对话不压缩"""
    print("\n[TEST] Short Conversation (No Compression)")
    print("-" * 60)

    strategy = SemanticCompressionStrategy(keep_first=2, keep_last=10)

    # 创建短对话
    conv = MockConversation("test_short")
    for i in range(5):
        conv.add_message(MockMessage("user", f"Message {i}"))

    # 压缩
    compressed = strategy.compress(conv)

    # 短对话不应该被压缩
    assert len(compressed.messages) == len(conv.messages)
    print(f"[PASS] Short conversation not compressed")
    print(f"  Original: {len(conv.messages)} messages")
    print(f"  Compressed: {len(compressed.messages)} messages")


def test_long_conversation_compression():
    """测试长对话压缩"""
    print("\n[TEST] Long Conversation Compression")
    print("-" * 60)

    strategy = SemanticCompressionStrategy(
        keep_first=2,
        keep_last=5,
        similarity_threshold=0.7
    )

    # 创建长对话
    conv = MockConversation("test_long")

    # 初始对话
    conv.add_message(MockMessage("user", "I want to learn about databases"))
    conv.add_message(MockMessage("assistant", "Sure! What would you like to know?"))

    # 中间无关对话
    conv.add_message(MockMessage("user", "What's the weather like?"))
    conv.add_message(MockMessage("assistant", "I'm not sure about the weather"))
    conv.add_message(MockMessage("user", "Do you like music?"))
    conv.add_message(MockMessage("assistant", "I don't have preferences"))

    # 相关对话
    conv.add_message(MockMessage("user", "How does database indexing work?"))
    conv.add_message(MockMessage("assistant", "Database indexes improve query performance"))

    # 最近对话
    for i in range(5):
        conv.add_message(MockMessage("user", f"Tell me more about database optimization {i}"))

    original_count = len(conv.messages)

    # 压缩
    compressed = strategy.compress(conv)

    compressed_count = len(compressed.messages)

    print(f"[PASS] Long conversation compressed")
    print(f"  Original: {original_count} messages")
    print(f"  Compressed: {compressed_count} messages")
    print(f"  Reduction: {(1 - compressed_count/original_count)*100:.1f}%")

    # 应该有压缩
    assert compressed_count < original_count


def test_semantic_relevance_filtering():
    """测试语义相关性过滤"""
    print("\n[TEST] Semantic Relevance Filtering")
    print("-" * 60)

    strategy = SemanticCompressionStrategy(
        keep_first=1,
        keep_last=2,
        similarity_threshold=0.5
    )

    # 创建对话
    conv = MockConversation("test_relevance")

    # 关于数据库的对话
    conv.add_message(MockMessage("user", "How to connect to database?"))

    # 中间消息 - 混合主题
    conv.add_message(MockMessage("user", "What is database connection pooling?"))  # 相关
    conv.add_message(MockMessage("user", "I like pizza"))  # 不相关
    conv.add_message(MockMessage("user", "Database performance tuning tips?"))  # 相关
    conv.add_message(MockMessage("user", "The weather is nice"))  # 不相关
    conv.add_message(MockMessage("user", "SQL query optimization"))  # 相关

    # 最近对话 - 关于数据库
    conv.add_message(MockMessage("user", "How to create database indexes?"))
    conv.add_message(MockMessage("assistant", "Use CREATE INDEX statement"))

    # 压缩
    compressed = strategy.compress(conv)

    print(f"[PASS] Semantic filtering applied")
    print(f"  Original: {len(conv.messages)} messages")
    print(f"  Compressed: {len(compressed.messages)} messages")

    # 压缩后应该少于原始
    assert len(compressed.messages) < len(conv.messages)


def test_minimum_keep_ratio():
    """测试最小保留比例"""
    print("\n[TEST] Minimum Keep Ratio")
    print("-" * 60)

    # 设置高阈值和低保留比例
    strategy = SemanticCompressionStrategy(
        keep_first=1,
        keep_last=1,
        similarity_threshold=0.99,  # 很高的阈值
        min_keep_ratio=0.5  # 至少保留 50%
    )

    # 创建对话
    conv = MockConversation("test_min_keep")
    for i in range(10):
        conv.add_message(MockMessage("user", f"Random message {i}"))

    # 压缩
    compressed = strategy.compress(conv)

    middle_original = len(conv.messages) - strategy.keep_first - strategy.keep_last
    middle_compressed = len(compressed.messages) - strategy.keep_first - strategy.keep_last - 1  # -1 for summary

    # 应该至少保留 50% 的中间消息
    expected_min = int(middle_original * 0.5)

    print(f"[PASS] Minimum keep ratio enforced")
    print(f"  Middle messages (original): {middle_original}")
    print(f"  Middle messages (kept): {middle_compressed}")
    print(f"  Expected minimum: {expected_min}")

    assert middle_compressed >= expected_min


def test_compression_summary_message():
    """测试压缩摘要消息"""
    print("\n[TEST] Compression Summary Message")
    print("-" * 60)

    strategy = SemanticCompressionStrategy(keep_first=1, keep_last=1)

    # 创建长对话
    conv = MockConversation("test_summary")
    for i in range(20):
        conv.add_message(MockMessage("user", f"Message {i}"))

    # 压缩
    compressed = strategy.compress(conv)

    # 查找摘要消息
    has_summary = any(
        "[Context compressed:" in msg.content and msg.role == MessageRole.SYSTEM
        for msg in compressed.messages
    )

    print(f"[PASS] Compression summary check")
    print(f"  Has summary message: {has_summary}")

    # 应该有摘要消息
    assert has_summary


def test_message_order_preservation():
    """测试消息顺序保持"""
    print("\n[TEST] Message Order Preservation")
    print("-" * 60)

    strategy = SemanticCompressionStrategy(keep_first=2, keep_last=2)

    # 创建对话
    conv = MockConversation("test_order")
    for i in range(10):
        conv.add_message(MockMessage("user", f"Message {i}"))

    # 压缩
    compressed = strategy.compress(conv)

    # 提取非系统消息
    non_system_msgs = [
        msg for msg in compressed.messages
        if msg.role != MessageRole.SYSTEM
    ]

    # 检查顺序
    # 首部消息应该是 0, 1
    assert non_system_msgs[0].content == "Message 0"
    assert non_system_msgs[1].content == "Message 1"

    # 尾部消息应该是 8, 9
    assert non_system_msgs[-2].content == "Message 8"
    assert non_system_msgs[-1].content == "Message 9"

    print("[PASS] Message order preserved")
    print(f"  First messages: {[m.content for m in non_system_msgs[:2]]}")
    print(f"  Last messages: {[m.content for m in non_system_msgs[-2:]]}")


def main():
    """运行所有测试"""
    print("=" * 60)
    print("Semantic Compression Strategy - Standalone Tests")
    print("=" * 60)

    try:
        test_semantic_compression_initialization()
        test_short_conversation_no_compression()
        test_long_conversation_compression()
        test_semantic_relevance_filtering()
        test_minimum_keep_ratio()
        test_compression_summary_message()
        test_message_order_preservation()

        print("\n" + "=" * 60)
        print("[SUCCESS] All tests passed!")
        print("=" * 60)
        print("\nPhase 3.3 (Semantic Compression Strategy) completed successfully.")

        return 0

    except AssertionError as e:
        print(f"\n[FAIL] Test failed: {e}")
        import traceback
        traceback.print_exc()
        return 1
    except Exception as e:
        print(f"\n[ERROR] Error: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
