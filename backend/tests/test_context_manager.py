"""
上下文管理器测试

测试上下文压缩和管理策略
"""
import pytest
from backend.core.context_manager import (
    HybridContextManager,
    FullContextStrategy,
    SlidingWindowStrategy,
    SmartCompressionStrategy,
    SemanticCompressionStrategy,
    compress_conversation
)
from backend.core.conversation_format import UnifiedConversation, MessageRole


class TestFullContextStrategy:
    """测试完整保留策略"""

    def test_compress_full(self):
        """测试完整保留不压缩"""
        conversation = UnifiedConversation()
        for i in range(5):
            conversation.add_user_message(f"消息 {i+1}")

        strategy = FullContextStrategy()
        compressed = strategy.compress(conversation, max_messages=3)

        # 应该保留所有消息
        assert len(compressed.messages) == 5


class TestSlidingWindowStrategy:
    """测试滑动窗口策略"""

    def test_compress_sliding_window(self):
        """测试滑动窗口压缩"""
        conversation = UnifiedConversation()
        for i in range(10):
            conversation.add_user_message(f"消息 {i+1}")

        strategy = SlidingWindowStrategy()
        compressed = strategy.compress(conversation, max_messages=5)

        # 应该保留最近5条消息
        assert len(compressed.messages) == 5
        assert compressed.messages[0].content == "消息 6"

    def test_compress_no_excess(self):
        """测试消息数量不超过限制"""
        conversation = UnifiedConversation()
        for i in range(3):
            conversation.add_user_message(f"消息 {i+1}")

        strategy = SlidingWindowStrategy()
        compressed = strategy.compress(conversation, max_messages=5)

        # 应该保留所有消息
        assert len(compressed.messages) == 3


class TestSmartCompressionStrategy:
    """测试智能压缩策略"""

    def test_compress_smart(self):
        """测试智能压缩"""
        conversation = UnifiedConversation()
        conversation.add_user_message("开始对话")
        conversation.add_assistant_message("好的,开始吧")

        # 添加中间消息
        for i in range(20):
            conversation.add_user_message(f"用户消息 {i+1}")
            conversation.add_assistant_message(f"助手回复 {i+1}")

        # 添加最后消息
        conversation.add_user_message("最后消息")
        conversation.add_assistant_message("最后回复")

        strategy = SmartCompressionStrategy(keep_first=2, keep_recent=5)
        compressed = strategy.compress(conversation, max_messages=50)

        # 应该有: 2条开始消息 + 1条摘要 + 5条最近消息 = 8条
        assert len(compressed.messages) == 8

    def test_compress_no_excess(self):
        """测试消息数量不超过限制"""
        conversation = UnifiedConversation()
        for i in range(5):
            conversation.add_user_message(f"消息 {i+1}")

        strategy = SmartCompressionStrategy()
        compressed = strategy.compress(conversation, max_messages=10)

        # 应该保留所有消息(不超过限制)
        assert len(compressed.messages) == 5

    def test_summary_creation(self):
        """测试摘要创建"""
        conversation = UnifiedConversation()
        conversation.add_user_message("查询用户数据")
        conversation.add_assistant_message("已查询,返回100条记录")
        conversation.add_user_message("导出为Excel")
        conversation.add_assistant_message("已导出,文件路径: /data/export.xlsx")

        strategy = SmartCompressionStrategy()
        summary = strategy._summarize_messages(conversation.messages)

        assert "查询用户数据" in summary
        assert "导出为Excel" in summary


class TestSemanticCompressionStrategy:
    """测试语义压缩策略"""

    def test_compress_semantic(self):
        """测试语义压缩(当前使用滑动窗口)"""
        conversation = UnifiedConversation()
        for i in range(10):
            conversation.add_user_message(f"消息 {i+1}")

        strategy = SemanticCompressionStrategy()
        compressed = strategy.compress(conversation, max_messages=5)

        # 目前实现使用滑动窗口
        assert len(compressed.messages) == 5


class TestHybridContextManager:
    """测试混合上下文管理器"""

    def test_create_manager(self):
        """测试创建管理器"""
        manager = HybridContextManager(
            strategy="sliding_window",
            max_context_length=20
        )

        assert manager.strategy_name == "sliding_window"
        assert manager.max_context_length == 20

    def test_invalid_strategy(self):
        """测试无效策略"""
        with pytest.raises(ValueError) as exc_info:
            HybridContextManager(strategy="invalid_strategy")

        assert "不支持的压缩策略" in str(exc_info.value)

    def test_compress_conversation(self):
        """测试压缩对话"""
        conversation = UnifiedConversation()
        for i in range(10):
            conversation.add_user_message(f"消息 {i+1}")

        manager = HybridContextManager(
            strategy="sliding_window",
            max_context_length=5
        )
        compressed = manager.compress_conversation(conversation)

        assert len(compressed.messages) == 5

    def test_create_snapshot(self):
        """测试创建快照"""
        conversation = UnifiedConversation()
        conversation.add_user_message("测试消息")

        manager = HybridContextManager(strategy="sliding_window")
        snapshot = manager.create_snapshot(conversation, snapshot_type="full")

        assert snapshot["snapshot_type"] == "full"
        assert "content" in snapshot
        assert snapshot["message_count"] == 1

    def test_create_compressed_snapshot(self):
        """测试创建压缩快照"""
        conversation = UnifiedConversation()
        for i in range(10):
            conversation.add_user_message(f"消息 {i+1}")

        manager = HybridContextManager(
            strategy="sliding_window",
            max_context_length=5
        )
        snapshot = manager.create_snapshot(conversation, snapshot_type="compressed")

        assert snapshot["snapshot_type"] == "compressed"
        assert "content" in snapshot

    def test_restore_from_snapshot(self):
        """测试从快照恢复"""
        conversation = UnifiedConversation()
        conversation.add_user_message("测试消息")

        manager = HybridContextManager(strategy="sliding_window")
        snapshot = manager.create_snapshot(conversation, snapshot_type="full")

        restored = manager.restore_from_snapshot(snapshot)

        assert len(restored.messages) == 1
        assert restored.messages[0].content == "测试消息"

    def test_restore_summary_snapshot(self):
        """测试从摘要快照恢复"""
        summary_data = {
            "summary": "这是一个测试对话的摘要",
            "key_points": ["要点1", "要点2"],
            "message_count": 10
        }

        snapshot = {
            "snapshot_type": "summary",
            "content": summary_data,
            "message_count": 10,
            "created_at": "2024-01-01"
        }

        manager = HybridContextManager(strategy="sliding_window")
        restored = manager.restore_from_snapshot(snapshot)

        assert len(restored.messages) == 1
        assert "历史对话摘要" in restored.messages[0].content
        assert "这是一个测试对话的摘要" in restored.messages[0].content

    def test_create_summary(self):
        """测试创建摘要"""
        conversation = UnifiedConversation(
            title="测试对话"
        )
        conversation.add_user_message("用户消息1")
        conversation.add_user_message("用户消息2")
        conversation.add_assistant_message("助手回复1")

        manager = HybridContextManager(strategy="sliding_window")
        summary = manager._create_summary(conversation)

        assert "测试对话" in summary["summary"]
        assert summary["message_count"] == 3


class TestConvenienceFunction:
    """测试便捷函数"""

    def test_compress_conversation_function(self):
        """测试便捷压缩函数"""
        conversation = UnifiedConversation()
        for i in range(10):
            conversation.add_user_message(f"消息 {i+1}")

        compressed = compress_conversation(
            conversation,
            strategy="sliding_window",
            max_messages=5
        )

        assert len(compressed.messages) == 5


class TestManagerFromSettings:
    """测试从Settings创建管理器"""

    def test_create_from_settings(self):
        """测试从settings创建管理器"""
        # 注意: 这个测试需要mock settings
        # 这里只测试方法存在
        assert hasattr(HybridContextManager, 'create_from_settings')
