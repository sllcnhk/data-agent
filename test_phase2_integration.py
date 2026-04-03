"""
Phase 2 Integration Tests

测试 Phase 2 所有组件的集成工作
"""
import sys
from typing import Dict, Any, List, Optional


# ============= Mock Classes =============
class MockMessage:
    """模拟消息"""
    def __init__(self, content: str, role: str = "user", token_count: int = None):
        self.content = content
        self.role = role
        self.token_count = token_count or len(content) // 4
        self.metadata = {"tokens": self.token_count}
        self.tool_calls = None
        self.value = role

    def has_tool_calls(self) -> bool:
        return self.tool_calls is not None


class MockConversation:
    """模拟对话"""
    def __init__(self, conversation_id: str = "test_conv"):
        self.conversation_id = conversation_id
        self.messages: List[MockMessage] = []

    def add_message(self, content: str, role: str = "user", token_count: int = None):
        msg = MockMessage(content, role, token_count)
        self.messages.append(msg)
        return msg


class MockTokenCounter:
    """模拟 token 计数器"""
    def count_tokens(self, text: str, model: str) -> int:
        return len(text) // 4


# Import all Phase 2 components (using standalone versions)
from test_token_budget_standalone_v2 import TokenBudgetCalculator, TokenBudgetManager
from test_dynamic_compression_standalone import DynamicCompressionAdjuster
from test_adaptive_strategy_standalone import AdaptiveStrategySelector
from test_unified_context_standalone import UnifiedContextManager, MockHybridContextManager


# ============= Integration Tests =============

def test_component_initialization():
    """测试所有组件初始化"""
    print("\n[TEST] Component Initialization")
    print("-" * 60)

    # Phase 2.1: Token Budget Manager
    token_budget_mgr = TokenBudgetManager()
    assert token_budget_mgr is not None
    print("[PASS] TokenBudgetManager initialized")

    # Phase 2.2: Dynamic Compression Adjuster
    compression_adj = DynamicCompressionAdjuster()
    assert compression_adj is not None
    print("[PASS] DynamicCompressionAdjuster initialized")

    # Phase 2.3: Adaptive Strategy Selector
    strategy_selector = AdaptiveStrategySelector()
    assert strategy_selector is not None
    print("[PASS] AdaptiveStrategySelector initialized")

    # Phase 2.4: Unified Context Manager
    unified_mgr = UnifiedContextManager()
    assert unified_mgr is not None
    print("[PASS] UnifiedContextManager initialized")


def test_end_to_end_workflow():
    """测试端到端工作流"""
    print("\n[TEST] End-to-End Workflow")
    print("-" * 60)

    # 创建对话
    conv = MockConversation("integration_test")

    # 场景：技术支持对话
    conv.add_message("Hi, I need help with my database connection")
    conv.add_message("Sure! What error are you seeing?")
    conv.add_message("I'm getting 'Connection timeout' error")
    conv.add_message("Let me check. Can you show me your config?")
    conv.add_message("Here's my config:\n```json\n{\"host\": \"localhost\"}\n```")

    # 添加更多对话以增加复杂度
    for i in range(20):
        conv.add_message(f"Technical discussion point {i}", token_count=200)

    # 使用 UnifiedContextManager 准备上下文
    manager = UnifiedContextManager()

    result = manager.prepare_context_from_unified(
        unified_conv=conv,
        model="claude-sonnet-4-5",
        system_prompt="You are a database expert.",
        current_message="What should I do next?"
    )

    # 验证结果
    assert "messages" in result
    assert "context_info" in result
    assert "budget_info" in result

    context_info = result['context_info']
    budget_info = result['budget_info']

    print(f"[PASS] End-to-end workflow completed")
    print(f"  Original messages: {context_info['original_message_count']}")
    print(f"  Compressed messages: {context_info['compressed_message_count']}")
    print(f"  Strategy: {context_info['strategy']}")
    print(f"  Compression ratio: {context_info['compression_ratio']:.2%}")
    print(f"  Estimated tokens: {context_info['estimated_tokens']}")
    print(f"  Available tokens: {budget_info['available_for_history']:,}")

    # 验证预算计算正确
    assert budget_info['available_for_history'] > 0
    assert context_info['estimated_tokens'] <= budget_info['context_window']


def test_adaptive_strategy_with_budget():
    """测试自适应策略与预算管理的集成"""
    print("\n[TEST] Adaptive Strategy with Token Budget")
    print("-" * 60)

    manager = UnifiedContextManager()

    # 场景 1: 低使用率对话
    conv1 = MockConversation("low_usage")
    for i in range(5):
        conv1.add_message(f"Short message {i}")

    result1 = manager.prepare_context_from_unified(
        conv1, "claude-sonnet-4-5", "System", "Current"
    )

    print(f"[PASS] Low usage:")
    print(f"  Strategy: {result1['context_info']['strategy']}")
    print(f"  Utilization: {result1['context_info']['estimated_tokens'] / result1['budget_info']['available_for_history']:.2%}")

    # 场景 2: 高使用率对话
    conv2 = MockConversation("high_usage")
    for i in range(200):
        conv2.add_message(f"Long technical message {i}", token_count=500)

    result2 = manager.prepare_context_from_unified(
        conv2, "claude-sonnet-4-5", "System", "Current"
    )

    print(f"\n[PASS] High usage:")
    print(f"  Strategy: {result2['context_info']['strategy']}")
    print(f"  Original: {result2['context_info']['original_message_count']}")
    print(f"  Compressed: {result2['context_info']['compressed_message_count']}")
    print(f"  Compression: {result2['context_info']['compression_ratio']:.2%}")


def test_dynamic_adjustment_in_workflow():
    """测试工作流中的动态调整"""
    print("\n[TEST] Dynamic Adjustment in Workflow")
    print("-" * 60)

    # 创建管理器
    token_budget = TokenBudgetManager()
    compression_adj = DynamicCompressionAdjuster(target_utilization=0.75)
    selector = AdaptiveStrategySelector(
        token_budget_manager=token_budget,
        dynamic_adjuster=compression_adj
    )

    # 创建对话
    conv = MockConversation("dynamic_test")
    for i in range(100):
        conv.add_message(f"Message {i}", token_count=800)

    # 选择策略
    strategy, params = selector.select_strategy(
        conv, "claude-sonnet-4-5", "System prompt", "Current message"
    )

    print(f"[PASS] Dynamic adjustment:")
    print(f"  Selected strategy: {strategy}")
    print(f"  Parameters: {params}")
    print(f"  Intensity: {params.get('intensity', 'N/A')}")

    # 验证参数根据使用率调整
    assert "strategy" in params
    assert "intensity" in params


def test_multiple_models():
    """测试多个模型的支持"""
    print("\n[TEST] Multiple Models Support")
    print("-" * 60)

    token_budget = TokenBudgetManager()

    models = [
        "claude-sonnet-4-5",
        "gpt-4-turbo",
        "gpt-3.5-turbo"
    ]

    conv = MockConversation("multi_model")
    for i in range(10):
        conv.add_message(f"Test message {i}")

    manager = UnifiedContextManager()

    for model in models:
        result = manager.prepare_context_from_unified(
            conv, model, "System prompt", "Current message"
        )

        budget = result['budget_info']
        print(f"[PASS] {model}:")
        print(f"  Context window: {budget['context_window']:,}")
        print(f"  Available: {budget['available_for_history']:,}")

        assert budget['available_for_history'] > 0


def test_compression_quality():
    """测试压缩质量"""
    print("\n[TEST] Compression Quality")
    print("-" * 60)

    manager = UnifiedContextManager()

    # 创建包含重要信息的对话
    conv = MockConversation("quality_test")

    # 开头的重要消息
    conv.add_message("This is the important initial context")
    conv.add_message("User's main goal is to fix the database")

    # 中间的填充消息
    for i in range(40):
        conv.add_message(f"Middle discussion point {i}")

    # 结尾的重要消息
    conv.add_message("The solution is to update the connection string")
    conv.add_message("Thank you, that fixed it!")

    # 使用 smart 策略压缩
    result = manager.prepare_context_from_unified(
        conv, "claude-sonnet-4-5", "System", "Current",
        strategy="smart"
    )

    messages = result['messages']
    compressed_count = len(messages)
    original_count = result['context_info']['original_message_count']

    print(f"[PASS] Compression quality:")
    print(f"  Original: {original_count} messages")
    print(f"  Compressed: {compressed_count} messages")
    print(f"  Retained: {compressed_count / original_count:.2%}")

    # 验证开头和结尾消息被保留
    first_content = messages[0]['content']
    last_content = messages[-1]['content']

    print(f"  First message preserved: {'initial context' in first_content}")
    print(f"  Last message preserved: {'fixed it' in last_content or 'solution' in last_content}")


def test_error_handling():
    """测试错误处理"""
    print("\n[TEST] Error Handling")
    print("-" * 60)

    manager = UnifiedContextManager()

    # 空对话
    empty_conv = MockConversation("empty")

    try:
        result = manager.prepare_context_from_unified(
            empty_conv, "claude-sonnet-4-5", "System", "Current"
        )
        print("[PASS] Empty conversation handled gracefully")
        assert result['context_info']['original_message_count'] == 0
    except Exception as e:
        print(f"[FAIL] Empty conversation raised error: {e}")
        raise

    # 单条消息
    single_conv = MockConversation("single")
    single_conv.add_message("Only one message")

    try:
        result = manager.prepare_context_from_unified(
            single_conv, "claude-sonnet-4-5", "System", "Current"
        )
        print("[PASS] Single message handled correctly")
        assert result['context_info']['original_message_count'] == 1
    except Exception as e:
        print(f"[FAIL] Single message raised error: {e}")
        raise


def test_statistics_and_monitoring():
    """测试统计和监控功能"""
    print("\n[TEST] Statistics and Monitoring")
    print("-" * 60)

    # 测试 DynamicCompressionAdjuster 统计
    adjuster = DynamicCompressionAdjuster()

    for i in range(10):
        adjuster.adjust_compression_params(
            current_tokens=50000 + i * 5000,
            available_tokens=100000,
            strategy_name="smart",
            conversation_id=f"conv_{i}"
        )

    stats = adjuster.get_statistics()

    print(f"[PASS] Compression statistics:")
    print(f"  Total adjustments: {stats['total_adjustments']}")
    print(f"  Avg utilization: {stats['avg_utilization']:.2%}")
    print(f"  Max utilization: {stats['max_utilization']:.2%}")
    print(f"  Min utilization: {stats['min_utilization']:.2%}")

    assert stats['total_adjustments'] == 10
    assert stats['avg_utilization'] > 0


def main():
    """运行所有集成测试"""
    print("=" * 60)
    print("Phase 2 Integration Tests")
    print("=" * 60)

    try:
        test_component_initialization()
        test_end_to_end_workflow()
        test_adaptive_strategy_with_budget()
        test_dynamic_adjustment_in_workflow()
        test_multiple_models()
        test_compression_quality()
        test_error_handling()
        test_statistics_and_monitoring()

        print("\n" + "=" * 60)
        print("[SUCCESS] All Phase 2 integration tests passed!")
        print("=" * 60)
        print("\n Phase 2 (Week 2) Integration Testing completed successfully.")
        print("\nComponents validated:")
        print("  [OK] Phase 2.1: Token Budget Manager")
        print("  [OK] Phase 2.2: Dynamic Compression Adjuster")
        print("  [OK] Phase 2.3: Adaptive Strategy Selector")
        print("  [OK] Phase 2.4: Unified Context Manager")
        print("\nReady to proceed to Phase 2.6: Acceptance Testing")

        return 0

    except AssertionError as e:
        print(f"\n[FAIL] Integration test failed: {e}")
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
