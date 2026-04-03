"""
TokenCounter 单元测试

测试 Token 计数模块的所有功能
"""
import sys
import os
import unittest

# 添加项目路径
backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
project_root = os.path.dirname(backend_dir)
sys.path.insert(0, project_root)

# 直接导入模块避免依赖问题
import importlib.util
spec = importlib.util.spec_from_file_location(
    "token_counter",
    os.path.join(backend_dir, "core", "token_counter.py")
)
token_counter_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(token_counter_module)

TokenCounter = token_counter_module.TokenCounter
get_token_counter = token_counter_module.get_token_counter
count_tokens = token_counter_module.count_tokens


class TestTokenCounter(unittest.TestCase):
    """TokenCounter 单元测试"""

    def setUp(self):
        """测试前准备"""
        self.counter = TokenCounter()

    def test_singleton_pattern(self):
        """测试单例模式"""
        counter1 = get_token_counter()
        counter2 = get_token_counter()
        self.assertIs(counter1, counter2, "应该返回同一个实例")

    def test_count_empty_text(self):
        """测试空文本"""
        self.assertEqual(self.counter.count_tokens("", "claude"), 0)
        self.assertEqual(self.counter.count_tokens(None, "claude"), 0)

    def test_count_english_text(self):
        """测试英文文本计数"""
        text = "Hello, how are you?"
        tokens = self.counter.count_tokens(text, "claude")
        self.assertGreater(tokens, 0, "英文文本应该有 token")
        self.assertLess(tokens, 50, "简单英文不应超过50个token")

    def test_count_chinese_text(self):
        """测试中文文本计数"""
        text = "你好，今天天气怎么样？"
        tokens = self.counter.count_tokens(text, "claude")
        self.assertGreater(tokens, 0, "中文文本应该有 token")

    def test_count_mixed_text(self):
        """测试混合中英文"""
        text = "Hello 你好 World 世界"
        tokens = self.counter.count_tokens(text, "claude")
        self.assertGreater(tokens, 0, "混合文本应该有 token")

    def test_multiple_models(self):
        """测试多模型支持"""
        text = "Test message"
        models = ["claude", "gpt-4", "gpt-3.5-turbo", "minimax"]

        for model in models:
            tokens = self.counter.count_tokens(text, model)
            self.assertGreater(tokens, 0, f"{model} 应该能计数")

    def test_count_messages_tokens(self):
        """测试消息列表计数"""
        messages = [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "What is AI?"},
            {"role": "assistant", "content": "AI stands for Artificial Intelligence."}
        ]

        result = self.counter.count_messages_tokens(messages, "claude")

        self.assertIn("prompt_tokens", result)
        self.assertIn("completion_tokens", result)
        self.assertIn("total_tokens", result)

        self.assertGreater(result["prompt_tokens"], 0, "应该有 prompt tokens")
        self.assertGreater(result["completion_tokens"], 0, "应该有 completion tokens")
        self.assertEqual(
            result["total_tokens"],
            result["prompt_tokens"] + result["completion_tokens"],
            "总数应该等于 prompt + completion"
        )

    def test_check_token_limit(self):
        """测试 token 限制检查"""
        short_text = "Short text"
        long_text = "Long text " * 1000

        # 短文本应该在限制内
        self.assertTrue(
            self.counter.check_token_limit(short_text, "claude", max_tokens=100)
        )

        # 长文本应该超限
        self.assertFalse(
            self.counter.check_token_limit(long_text, "claude", max_tokens=100)
        )

    def test_truncate_to_limit(self):
        """测试截断到限制"""
        long_text = "This is a test. " * 100
        max_tokens = 50

        truncated = self.counter.truncate_to_token_limit(long_text, "claude", max_tokens)
        truncated_tokens = self.counter.count_tokens(truncated, "claude")

        self.assertLessEqual(truncated_tokens, max_tokens, "截断后应该在限制内")
        self.assertLess(len(truncated), len(long_text), "截断后应该更短")

    def test_estimate_conversation_tokens(self):
        """测试对话 token 估算"""
        system_prompt = "You are a helpful assistant."
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"}
        ]

        total = self.counter.estimate_conversation_tokens(system_prompt, messages, "claude")
        self.assertGreater(total, 0, "对话应该有 token")

    def test_fallback_estimation(self):
        """测试降级估算"""
        # 直接测试降级方法
        text = "Hello World"
        estimated = self.counter._estimate_tokens_fallback(text)

        self.assertGreater(estimated, 0, "估算应该返回正数")
        # 11 个字符，英文约 11/4 = 2-3 tokens
        self.assertGreater(estimated, 1)
        self.assertLess(estimated, 10)


class TestConvenienceFunctions(unittest.TestCase):
    """便捷函数测试"""

    def test_count_tokens_function(self):
        """测试 count_tokens 便捷函数"""
        text = "Test message"
        tokens = count_tokens(text, "claude")
        self.assertGreater(tokens, 0)

    def test_count_message_tokens_function(self):
        """测试 count_message_tokens 便捷函数"""
        count_message_tokens = token_counter_module.count_message_tokens

        message = {"role": "user", "content": "Hello"}
        tokens = count_message_tokens(message, "claude")
        self.assertGreater(tokens, 0)


if __name__ == "__main__":
    # 运行测试
    unittest.main(verbosity=2)
