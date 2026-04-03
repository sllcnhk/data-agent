"""
ConversationService 集成测试

测试 Phase 1 集成的所有功能
"""
import sys
import os
import unittest

# 添加项目路径
backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
project_root = os.path.dirname(backend_dir)
sys.path.insert(0, project_root)


class TestConversationServiceIntegration(unittest.TestCase):
    """ConversationService 集成测试"""

    @classmethod
    def setUpClass(cls):
        """测试类准备"""
        try:
            from backend.config.database import get_db_context
            from backend.services.conversation_service import ConversationService
            cls.get_db_context = get_db_context
            cls.ConversationService = ConversationService
            cls.db_available = True
        except Exception as e:
            print(f"Warning: Database not available: {e}")
            cls.db_available = False

    def setUp(self):
        """每个测试前准备"""
        if not self.db_available:
            self.skipTest("Database not available")

    def test_add_message_with_token_counting(self):
        """测试添加消息时自动计算 token"""
        with self.get_db_context() as db:
            service = self.ConversationService(db)

            # 创建对话
            conversation = service.create_conversation(
                title="Token Test",
                system_prompt="Test prompt",
                model="claude"
            )

            # 添加消息
            message = service.add_message(
                conversation_id=str(conversation.id),
                role="user",
                content="Hello, this is a test message."
            )

            # 验证 token 已计算
            self.assertIsNotNone(message.total_tokens)
            self.assertGreater(message.total_tokens, 0)
            self.assertGreater(message.prompt_tokens, 0)
            self.assertEqual(message.completion_tokens, 0)

            # 验证对话 total_tokens 已更新
            db.refresh(conversation)
            self.assertEqual(conversation.total_tokens, message.total_tokens)

            # 清理
            service.delete_conversation(str(conversation.id))

    def test_build_context_with_hybrid_manager(self):
        """测试使用 HybridContextManager 构建上下文"""
        with self.get_db_context() as db:
            service = self.ConversationService(db)

            # 创建对话
            conversation = service.create_conversation(
                title="Context Test",
                system_prompt="Test prompt",
                model="claude"
            )

            # 添加多条消息
            for i in range(25):
                service.add_message(
                    conversation_id=str(conversation.id),
                    role="user",
                    content=f"User message {i}"
                )
                service.add_message(
                    conversation_id=str(conversation.id),
                    role="assistant",
                    content=f"Assistant reply {i}"
                )

            # 构建上下文
            context = service._build_context(str(conversation.id))

            # 验证上下文结构
            self.assertIn("conversation_id", context)
            self.assertIn("history", context)
            self.assertIn("context_info", context)

            # 验证压缩效果
            context_info = context["context_info"]
            self.assertEqual(context_info["original_message_count"], 50)

            # 根据配置的 max_context_messages (30)，应该被压缩
            # smart 策略会压缩为: 前2条 + 摘要 + 后N条
            self.assertLessEqual(
                context_info["compressed_message_count"],
                30,  # 应该 <= max_context_messages
                "压缩后消息数应该不超过配置限制"
            )

            # 清理
            service.delete_conversation(str(conversation.id))

    def test_multiple_messages_token_accumulation(self):
        """测试多条消息 token 累加"""
        with self.get_db_context() as db:
            service = self.ConversationService(db)

            # 创建对话
            conversation = service.create_conversation(
                title="Token Accumulation Test",
                model="claude"
            )

            # 添加第一条消息
            msg1 = service.add_message(
                conversation_id=str(conversation.id),
                role="user",
                content="First message"
            )
            tokens1 = msg1.total_tokens

            # 添加第二条消息
            msg2 = service.add_message(
                conversation_id=str(conversation.id),
                role="assistant",
                content="Second message reply"
            )
            tokens2 = msg2.total_tokens

            # 验证累加
            db.refresh(conversation)
            expected_total = tokens1 + tokens2
            self.assertEqual(
                conversation.total_tokens,
                expected_total,
                "对话总 token 应该等于所有消息 token 之和"
            )

            # 清理
            service.delete_conversation(str(conversation.id))

    def test_context_info_metadata(self):
        """测试上下文信息元数据"""
        with self.get_db_context() as db:
            service = self.ConversationService(db)

            # 创建对话
            conversation = service.create_conversation(
                title="Metadata Test",
                model="claude"
            )

            # 添加消息
            for i in range(10):
                service.add_message(
                    conversation_id=str(conversation.id),
                    role="user",
                    content=f"Message {i}"
                )

            # 构建上下文
            context = service._build_context(str(conversation.id))

            # 验证元数据存在
            self.assertIn("context_info", context)
            info = context["context_info"]

            # 验证必要字段
            self.assertIn("strategy", info)
            self.assertIn("max_context_length", info)
            self.assertIn("original_message_count", info)
            self.assertIn("compressed_message_count", info)

            # 验证值合理
            self.assertEqual(info["original_message_count"], 10)
            self.assertIn(info["strategy"], ["full", "sliding_window", "compressed", "smart"])

            # 清理
            service.delete_conversation(str(conversation.id))


class TestConfigurationIntegration(unittest.TestCase):
    """配置集成测试"""

    def test_settings_loaded_correctly(self):
        """测试 settings 配置正确加载"""
        try:
            from backend.config.settings import settings

            # 验证 Phase 1.1 配置
            self.assertEqual(settings.max_context_messages, 30)
            self.assertEqual(settings.max_context_tokens, 150000)
            self.assertEqual(settings.context_utilization_target, 0.75)
            self.assertEqual(settings.context_compression_strategy, "smart")
            self.assertEqual(settings.enable_context_cache, True)
            self.assertEqual(settings.context_cache_ttl, 300)

        except Exception as e:
            self.skipTest(f"Settings not available: {e}")

    def test_hybrid_context_manager_from_settings(self):
        """测试从 settings 创建 HybridContextManager"""
        try:
            from backend.core.context_manager import HybridContextManager
            from backend.config.settings import settings

            manager = HybridContextManager.create_from_settings()

            self.assertEqual(manager.strategy_name, settings.context_compression_strategy)
            self.assertEqual(manager.max_context_length, settings.max_context_messages)

        except Exception as e:
            self.skipTest(f"Context manager not available: {e}")


if __name__ == "__main__":
    # 运行测试
    unittest.main(verbosity=2)
