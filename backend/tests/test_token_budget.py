"""
Test Token Budget Manager - Phase 2.1

测试 Token 预算管理功能
"""
import pytest
import sys
import os

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.core.token_budget import (
    TokenBudgetCalculator,
    TokenBudgetManager,
    get_token_budget_manager
)


class TestTokenBudgetCalculator:
    """测试 TokenBudgetCalculator"""

    def test_get_model_config(self):
        """测试获取模型配置"""
        calculator = TokenBudgetCalculator()

        # 测试精确匹配
        config = calculator.get_model_config("claude-sonnet-4-5")
        assert config["context_window"] == 200000
        assert config["max_output"] == 8192

        # 测试模糊匹配
        config = calculator.get_model_config("gpt-4")
        assert config["context_window"] == 8192

        # 测试未知模型（使用默认）
        config = calculator.get_model_config("unknown-model")
        assert config["context_window"] == 200000  # 默认 Claude

    def test_calculate_available_tokens(self):
        """测试计算可用 token"""
        calculator = TokenBudgetCalculator()

        # Claude Sonnet 4.5
        available = calculator.calculate_available_tokens(
            model="claude-sonnet-4-5",
            system_prompt_tokens=100,
            current_message_tokens=50
        )

        # 200000 - 8192(output) - 100(system) - 50(message) - 10000(5% margin)
        expected = 200000 - 8192 - 100 - 50 - 10000
        assert available == expected

    def test_estimate_compression_needed(self):
        """测试估算压缩率"""
        calculator = TokenBudgetCalculator()

        # 不需要压缩
        ratio = calculator.estimate_compression_needed(
            current_tokens=1000,
            available_tokens=2000
        )
        assert ratio == 0.0

        # 需要压缩 50%
        ratio = calculator.estimate_compression_needed(
            current_tokens=2000,
            available_tokens=1000
        )
        assert ratio == 0.5

        # 需要压缩 90%
        ratio = calculator.estimate_compression_needed(
            current_tokens=10000,
            available_tokens=1000
        )
        assert ratio == 0.9

        # 最多压缩 95%
        ratio = calculator.estimate_compression_needed(
            current_tokens=100000,
            available_tokens=1000
        )
        assert ratio == 0.95


class TestTokenBudgetManager:
    """测试 TokenBudgetManager"""

    def test_create_budget(self):
        """测试创建预算"""
        manager = TokenBudgetManager()

        budget = manager.create_budget(
            model="claude-sonnet-4-5",
            system_prompt="You are a helpful assistant.",
            current_message="Hello, how are you?"
        )

        # 检查关键字段
        assert "model" in budget
        assert budget["model"] == "claude-sonnet-4-5"
        assert "context_window" in budget
        assert budget["context_window"] == 200000
        assert "system_tokens" in budget
        assert budget["system_tokens"] > 0
        assert "message_tokens" in budget
        assert budget["message_tokens"] > 0
        assert "available_for_history" in budget
        assert budget["available_for_history"] > 0
        assert "recommended_max_messages" in budget
        assert budget["recommended_max_messages"] > 0
        assert "compression_strategy" in budget
        assert budget["compression_strategy"] in ["full", "sliding_window", "smart"]
        assert "utilization" in budget

    def test_check_budget(self):
        """测试检查预算"""
        manager = TokenBudgetManager()

        budget = manager.create_budget(
            model="claude-sonnet-4-5",
            system_prompt="Test",
            current_message="Test"
        )

        # 测试正常使用
        check = manager.check_budget(
            current_tokens=10000,
            budget=budget
        )

        assert "current_tokens" in check
        assert check["current_tokens"] == 10000
        assert "available_tokens" in check
        assert "over_budget" in check
        assert "utilization" in check
        assert "compression_needed" in check
        assert "action" in check
        assert check["action"] in ["no_action", "monitor", "compress", "compress_aggressive"]

    def test_recommend_strategy(self):
        """测试策略推荐"""
        manager = TokenBudgetManager()

        # 充足空间 -> full
        strategy = manager._recommend_strategy(120000)
        assert strategy == "full"

        # 中等空间 -> sliding_window
        strategy = manager._recommend_strategy(60000)
        assert strategy == "sliding_window"

        # 紧张空间 -> smart
        strategy = manager._recommend_strategy(30000)
        assert strategy == "smart"

        # 非常紧张 -> smart
        strategy = manager._recommend_strategy(10000)
        assert strategy == "smart"

    def test_recommend_action(self):
        """测试行动推荐"""
        manager = TokenBudgetManager()

        # 使用率 < 50% -> no_action
        action = manager._recommend_action(5000, 12000)
        assert action == "no_action"

        # 使用率 50-75% -> monitor
        action = manager._recommend_action(7000, 10000)
        assert action == "monitor"

        # 使用率 75-100% -> compress
        action = manager._recommend_action(9000, 10000)
        assert action == "compress"

        # 超出预算 -> compress_aggressive
        action = manager._recommend_action(15000, 10000)
        assert action == "compress_aggressive"

    def test_singleton(self):
        """测试单例模式"""
        manager1 = get_token_budget_manager()
        manager2 = get_token_budget_manager()

        assert manager1 is manager2


class TestIntegration:
    """集成测试"""

    def test_full_workflow(self):
        """测试完整工作流"""
        manager = get_token_budget_manager()

        # 1. 创建预算
        budget = manager.create_budget(
            model="claude-sonnet-4-5",
            system_prompt="You are a data analysis assistant.",
            current_message="Analyze the sales data for Q1 2024."
        )

        print("\n[PASS] Budget created:")
        print(f"  Model: {budget['model']}")
        print(f"  Context window: {budget['context_window']}")
        print(f"  System tokens: {budget['system_tokens']}")
        print(f"  Message tokens: {budget['message_tokens']}")
        print(f"  Available for history: {budget['available_for_history']}")
        print(f"  Recommended max messages: {budget['recommended_max_messages']}")
        print(f"  Recommended strategy: {budget['compression_strategy']}")

        # 2. 检查预算（模拟历史消息）
        current_tokens = 50000  # 模拟 50K tokens 的历史

        check = manager.check_budget(current_tokens, budget)

        print("\n[PASS] Budget check:")
        print(f"  Current tokens: {check['current_tokens']}")
        print(f"  Available tokens: {check['available_tokens']}")
        print(f"  Over budget: {check['over_budget']}")
        print(f"  Utilization: {check['utilization']:.2%}")
        print(f"  Compression needed: {check['compression_needed']:.2%}")
        print(f"  Recommended action: {check['action']}")

        # 验证
        assert budget["available_for_history"] > 0
        assert check["utilization"] >= 0


def run_all_tests():
    """运行所有测试"""
    print("=" * 60)
    print("Testing Token Budget Manager")
    print("=" * 60)

    # 运行测试
    pytest.main([__file__, "-v", "--tb=short"])


if __name__ == "__main__":
    run_all_tests()
