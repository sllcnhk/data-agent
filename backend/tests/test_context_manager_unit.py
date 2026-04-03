"""
HybridContextManager 单元测试

测试上下文管理器的所有功能
"""
import sys
import os
import unittest

# 添加项目路径
backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
project_root = os.path.dirname(backend_dir)
sys.path.insert(0, project_root)

# 直接导入避免依赖问题
import importlib.util

# 加载 conversation_format
spec1 = importlib.util.spec_from_file_location(
    "conversation_format",
    os.path.join(backend_dir, "core", "conversation_format.py")
)
conv_format_module = importlib.util.module_from_spec(spec1)
spec1.loader.exec_module(conv_format_module)

# 加载 context_manager
spec2 = importlib.util.spec_from_file_location(
    "context_manager",
    os.path.join(backend_dir, "core", "context_manager.py")
)
context_manager_module = importlib.util.module_from_spec(spec2)
spec2.loader.exec_module(context_manager_module)

UnifiedConversation = conv_format_module.UnifiedConversation
MessageRole = conv_format_module.MessageRole
HybridContextManager = context_manager_module.HybridContextManager
FullContextStrategy = context_manager_module.FullContextStrategy
SlidingWindowStrategy = context_manager_module.SlidingWindowStrategy
SmartCompressionStrategy = context_manager_module.SmartCompressionStrategy


class TestFullContextStrategy(unittest.TestCase):
    """Full Context Strategy 测试"""

    def test_no_compression(self):
        """测试完整保留策略不压缩"""
        strategy = FullContextStrategy()

        conversation = UnifiedConversation(
            conversation_id="test",
            title="Test",
            model="claude"
        )

        # 添加消息
        for i in range(20):
            conversation.add_user_message(f"Message {i}")
            conversation.add_assistant_message(f"Reply {i}")

        compressed = strategy.compress(conversation, max_messages=10)

        # 应该保留所有消息
        self.assertEqual(len(compressed.messages), 40)


class TestSlidingWindowStrategy(unittest.TestCase):
    """Sliding Window Strategy 测试"""

    def test_keep_recent_messages(self):
        """测试保留最近消息"""
        strategy = SlidingWindowStrategy()

        conversation = UnifiedConversation(
            conversation_id="test",
            title="Test",
            model="claude"
        )

        # 添加 30 条消息
        for i in range(30):
            conversation.add_user_message(f"Message {i}")

        compressed = strategy.compress(conversation, max_messages=10)

        # 应该只保留最近 10 条
        self.assertEqual(len(compressed.messages), 10)

        # 验证是最近的消息
        self.assertIn("Message 29", compressed.messages[-1].content)
        self.assertIn("Message 20", compressed.messages[0].content)

    def test_no_compression_if_under_limit(self):
        """测试消息少于限制时不压缩"""
        strategy = SlidingWindowStrategy()

        conversation = UnifiedConversation(
            conversation_id="test",
            title="Test",
            model="claude"
        )

        for i in range(5):
            conversation.add_user_message(f"Message {i}")

        compressed = strategy.compress(conversation, max_messages=10)

        # 应该保留所有消息
        self.assertEqual(len(compressed.messages), 5)


class TestSmartCompressionStrategy(unittest.TestCase):
    """Smart Compression Strategy 测试"""

    def test_smart_compression_structure(self):
        """测试智能压缩结构"""
        strategy = SmartCompressionStrategy(keep_first=2, keep_recent=8)

        conversation = UnifiedConversation(
            conversation_id="test",
            title="Test",
            model="claude"
        )

        # 添加 60 条消息 (30轮对话)
        for i in range(30):
            conversation.add_user_message(f"User message {i}")
            conversation.add_assistant_message(f"Assistant reply {i}")

        compressed = strategy.compress(conversation, max_messages=15)

        # 应该有: 2 首 + 1 摘要 + 8 尾 = 11 条
        self.assertEqual(len(compressed.messages), 11)

        # 检查是否包含摘要
        has_summary = any("[历史对话摘要]" in msg.content for msg in compressed.messages)
        self.assertTrue(has_summary, "应该包含历史摘要")

    def test_no_compression_if_under_limit(self):
        """测试消息少时不压缩"""
        strategy = SmartCompressionStrategy(keep_first=2, keep_recent=8)

        conversation = UnifiedConversation(
            conversation_id="test",
            title="Test",
            model="claude"
        )

        for i in range(5):
            conversation.add_user_message(f"Message {i}")

        compressed = strategy.compress(conversation, max_messages=15)

        # 少于限制，不压缩
        self.assertEqual(len(compressed.messages), 5)


class TestHybridContextManager(unittest.TestCase):
    """HybridContextManager 测试"""

    def test_create_with_full_strategy(self):
        """测试创建 full 策略管理器"""
        manager = HybridContextManager(strategy="full", max_context_length=10)
        self.assertEqual(manager.strategy_name, "full")
        self.assertEqual(manager.max_context_length, 10)

    def test_create_with_sliding_window(self):
        """测试创建 sliding_window 策略管理器"""
        manager = HybridContextManager(strategy="sliding_window", max_context_length=20)
        self.assertEqual(manager.strategy_name, "sliding_window")

    def test_create_with_smart_compression(self):
        """测试创建 compressed 策略管理器"""
        manager = HybridContextManager(
            strategy="compressed",
            max_context_length=15,
            keep_first=2,
            keep_recent=8
        )
        self.assertEqual(manager.strategy_name, "compressed")

    def test_invalid_strategy_raises_error(self):
        """测试无效策略抛出错误"""
        with self.assertRaises(ValueError):
            HybridContextManager(strategy="invalid_strategy")

    def test_compress_conversation(self):
        """测试压缩对话"""
        manager = HybridContextManager(strategy="sliding_window", max_context_length=10)

        conversation = UnifiedConversation(
            conversation_id="test",
            title="Test",
            model="claude"
        )

        for i in range(20):
            conversation.add_user_message(f"Message {i}")

        compressed = manager.compress_conversation(conversation)

        self.assertEqual(len(compressed.messages), 10)

    def test_create_snapshot_full(self):
        """测试创建 full 快照"""
        manager = HybridContextManager(strategy="full", max_context_length=10)

        conversation = UnifiedConversation(
            conversation_id="test",
            title="Test Conversation",
            model="claude"
        )

        for i in range(5):
            conversation.add_user_message(f"Message {i}")

        snapshot = manager.create_snapshot(conversation, snapshot_type="full")

        self.assertEqual(snapshot["snapshot_type"], "full")
        self.assertEqual(snapshot["message_count"], 5)
        self.assertIn("created_at", snapshot)

    def test_create_snapshot_compressed(self):
        """测试创建 compressed 快照"""
        manager = HybridContextManager(strategy="sliding_window", max_context_length=5)

        conversation = UnifiedConversation(
            conversation_id="test",
            title="Test",
            model="claude"
        )

        for i in range(10):
            conversation.add_user_message(f"Message {i}")

        snapshot = manager.create_snapshot(conversation, snapshot_type="compressed")

        self.assertEqual(snapshot["snapshot_type"], "compressed")
        # 压缩后应该只有 5 条
        self.assertEqual(snapshot["content"]["message_count"], 5)

    def test_create_snapshot_summary(self):
        """测试创建 summary 快照"""
        manager = HybridContextManager(strategy="full", max_context_length=10)

        conversation = UnifiedConversation(
            conversation_id="test",
            title="Test Conversation",
            model="claude"
        )

        conversation.add_user_message("Hello")
        conversation.add_assistant_message("Hi")

        snapshot = manager.create_snapshot(conversation, snapshot_type="summary")

        self.assertEqual(snapshot["snapshot_type"], "summary")
        self.assertIn("summary", snapshot["content"])

    def test_restore_from_snapshot(self):
        """测试从快照恢复"""
        manager = HybridContextManager(strategy="full", max_context_length=10)

        conversation = UnifiedConversation(
            conversation_id="test",
            title="Test",
            model="claude"
        )

        for i in range(5):
            conversation.add_user_message(f"Message {i}")

        # 创建快照
        snapshot = manager.create_snapshot(conversation, snapshot_type="full")

        # 恢复
        restored = manager.restore_from_snapshot(snapshot)

        self.assertEqual(len(restored.messages), 5)


class TestConvenienceFunction(unittest.TestCase):
    """便捷函数测试"""

    def test_compress_conversation_function(self):
        """测试 compress_conversation 便捷函数"""
        from context_manager import compress_conversation

        conversation = UnifiedConversation(
            conversation_id="test",
            title="Test",
            model="claude"
        )

        for i in range(20):
            conversation.add_user_message(f"Message {i}")

        compressed = compress_conversation(
            conversation,
            strategy="sliding_window",
            max_messages=10
        )

        self.assertEqual(len(compressed.messages), 10)


if __name__ == "__main__":
    # 运行测试
    unittest.main(verbosity=2)
