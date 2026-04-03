"""
独立测试 Token Budget Manager
"""
import sys
import os

# 添加路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'backend'))

# 模拟 token_counter
class MockTokenCounter:
    def count_tokens(self, text, model):
        # 简单估算：每4个字符约1个token
        return len(text) // 4

# 替换全局 token_counter
import backend.core.token_counter as tc_module
tc_module._token_counter = MockTokenCounter()

from backend.core.token_budget import TokenBudgetCalculator, TokenBudgetManager


def test_token_budget_calculator():
    """测试 TokenBudgetCalculator"""
    print("\n[TEST] TokenBudgetCalculator")
    print("-" * 60)

    calculator = TokenBudgetCalculator()

    # 测试 1: 获取模型配置
    config = calculator.get_model_config("claude-sonnet-4-5")
    assert config["context_window"] == 200000, "Context window should be 200000"
    print("[PASS] get_model_config: Claude Sonnet 4.5 config correct")

    # 测试 2: 计算可用 tokens
    available = calculator.calculate_available_tokens(
        model="claude-sonnet-4-5",
        system_prompt_tokens=100,
        current_message_tokens=50
    )
    assert available > 0, "Available tokens should be positive"
    print(f"[PASS] calculate_available_tokens: {available} tokens available")

    # 测试 3: 估算压缩率
    compression = calculator.estimate_compression_needed(
        current_tokens=2000,
        available_tokens=1000
    )
    assert compression == 0.5, "Compression should be 0.5"
    print(f"[PASS] estimate_compression_needed: {compression:.2%}")

    print("✅ TokenBudgetCalculator: All tests passed")


def test_token_budget_manager():
    """测试 TokenBudgetManager"""
    print("\n[TEST] TokenBudgetManager")
    print("-" * 60)

    manager = TokenBudgetManager()

    # 测试 1: 创建预算
    budget = manager.create_budget(
        model="claude-sonnet-4-5",
        system_prompt="You are a helpful assistant.",
        current_message="Hello, how are you?"
    )

    assert "model" in budget, "Budget should have model field"
    assert "available_for_history" in budget, "Budget should have available_for_history"
    assert budget["available_for_history"] > 0, "Available should be positive"

    print(f"[PASS] create_budget:")
    print(f"  Model: {budget['model']}")
    print(f"  Context window: {budget['context_window']:,}")
    print(f"  System tokens: {budget['system_tokens']}")
    print(f"  Message tokens: {budget['message_tokens']}")
    print(f"  Available: {budget['available_for_history']:,}")
    print(f"  Max messages: {budget['recommended_max_messages']}")
    print(f"  Strategy: {budget['compression_strategy']}")

    # 测试 2: 检查预算
    check = manager.check_budget(
        current_tokens=50000,
        budget=budget
    )

    assert "over_budget" in check, "Check should have over_budget field"
    assert "action" in check, "Check should have action field"

    print(f"\n[PASS] check_budget (50K tokens):")
    print(f"  Over budget: {check['over_budget']}")
    print(f"  Utilization: {check['utilization']:.2%}")
    print(f"  Compression needed: {check['compression_needed']:.2%}")
    print(f"  Recommended action: {check['action']}")

    # 测试 3: 策略推荐
    strategies = {
        120000: "full",
        60000: "sliding_window",
        30000: "smart",
        10000: "smart"
    }

    print(f"\n[PASS] Strategy recommendations:")
    for tokens, expected in strategies.items():
        strategy = manager._recommend_strategy(tokens)
        assert strategy == expected, f"Strategy for {tokens} should be {expected}"
        print(f"  {tokens:,} tokens → {strategy}")

    print("\n✅ TokenBudgetManager: All tests passed")


def test_integration():
    """集成测试"""
    print("\n[TEST] Integration Test")
    print("-" * 60)

    manager = TokenBudgetManager()

    # 场景：长对话需要压缩
    budget = manager.create_budget(
        model="claude-sonnet-4-5",
        system_prompt="You are a data analyst assistant with expertise in SQL and Python.",
        current_message="Please analyze the customer churn data from last quarter."
    )

    print(f"Scenario: Long conversation")
    print(f"  Available tokens: {budget['available_for_history']:,}")
    print(f"  Recommended strategy: {budget['compression_strategy']}")

    # 模拟历史消息的 token 数
    history_tokens = 80000

    check = manager.check_budget(history_tokens, budget)

    print(f"\n  History tokens: {history_tokens:,}")
    print(f"  Utilization: {check['utilization']:.2%}")
    print(f"  Action: {check['action']}")

    if check['over_budget']:
        print(f"  ⚠️  Over budget! Compression needed: {check['compression_needed']:.2%}")
    else:
        print(f"  ✅ Within budget")

    print("\n✅ Integration test passed")


def main():
    """运行所有测试"""
    print("=" * 60)
    print("Token Budget Manager - Standalone Tests")
    print("=" * 60)

    try:
        test_token_budget_calculator()
        test_token_budget_manager()
        test_integration()

        print("\n" + "=" * 60)
        print("✅ All tests passed!")
        print("=" * 60)
        print("\nPhase 2.1 (Token Budget Manager) completed successfully.")

        return 0

    except AssertionError as e:
        print(f"\n❌ Test failed: {e}")
        return 1
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
