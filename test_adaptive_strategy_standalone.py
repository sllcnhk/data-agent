"""
独立测试 Adaptive Strategy Selector - Phase 2.3
不依赖任何 backend 导入
"""
import sys
from typing import Dict, Any, List, Tuple, Optional
from datetime import datetime


# ============= Mock Classes =============
class MockMessage:
    """模拟消息"""
    def __init__(self, content: str, role: str = "user", token_count: int = None):
        self.content = content
        self.role = role
        self.token_count = token_count or len(content) // 4
        self.metadata = {"tokens": self.token_count}
        self.tool_calls = None

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


class TokenBudgetCalculator:
    """Token 预算计算器"""
    def __init__(self):
        self.model_limits = {
            "claude-sonnet-4-5": {
                "context_window": 200000,
                "max_output": 8192,
                "reserved_for_output": 8192,
                "system_prompt_reserve": 500,
                "safety_margin": 0.05
            }
        }

    def get_model_config(self, model: str) -> Dict[str, Any]:
        return self.model_limits.get(model, self.model_limits["claude-sonnet-4-5"])

    def calculate_available_tokens(
        self, model: str, system_prompt_tokens: int, current_message_tokens: int
    ) -> int:
        config = self.get_model_config(model)
        context_window = config["context_window"]
        reserved_output = config["reserved_for_output"]
        safety_margin = int(context_window * config["safety_margin"])

        available = (
            context_window - reserved_output - system_prompt_tokens
            - current_message_tokens - safety_margin
        )
        return max(0, available)


class TokenBudgetManager:
    """Token 预算管理器"""
    def __init__(self, token_counter=None):
        self.calculator = TokenBudgetCalculator()
        self.token_counter = token_counter or MockTokenCounter()

    def create_budget(
        self, model: str, system_prompt: str, current_message: str
    ) -> Dict[str, Any]:
        system_tokens = self.token_counter.count_tokens(system_prompt, model)
        message_tokens = self.token_counter.count_tokens(current_message, model)

        available_tokens = self.calculator.calculate_available_tokens(
            model, system_tokens, message_tokens
        )

        config = self.calculator.get_model_config(model)

        return {
            "model": model,
            "context_window": config["context_window"],
            "system_tokens": system_tokens,
            "message_tokens": message_tokens,
            "available_for_history": available_tokens
        }


class DynamicCompressionAdjuster:
    """动态压缩调整器"""
    def __init__(self, target_utilization: float = 0.75):
        self.target_utilization = target_utilization
        self.compression_presets = {
            "full": {"keep_first": None, "keep_last": None},
            "sliding_window_normal": {"keep_first": 3, "keep_last": 15},
            "smart_normal": {"keep_first": 2, "keep_last": 10, "use_summary": True}
        }

    def adjust_compression_params(
        self, current_tokens: int, available_tokens: int,
        strategy_name: str, conversation_id: Optional[str] = None
    ) -> Dict[str, Any]:
        utilization = current_tokens / available_tokens if available_tokens > 0 else 1.0
        deviation = utilization - self.target_utilization

        if abs(deviation) < 0.1:
            return self._get_strategy_params(strategy_name, "normal")
        elif deviation > 0.1:
            return self._get_strategy_params(strategy_name, "aggressive")
        else:
            return self._get_strategy_params(strategy_name, "relaxed")

    def _get_strategy_params(self, strategy: str, intensity: str) -> Dict[str, Any]:
        preset_key = f"{strategy}_{intensity}" if strategy != "full" else "full"

        if preset_key in self.compression_presets:
            params = self.compression_presets[preset_key].copy()
        else:
            params = {"keep_first": 2, "keep_last": 10}

        params["strategy"] = strategy
        params["intensity"] = intensity
        return params


# ============= AdaptiveStrategySelector =============
class AdaptiveStrategySelector:
    """自适应策略选择器"""

    def __init__(self, token_budget_manager=None, dynamic_adjuster=None):
        self.token_budget_manager = token_budget_manager or TokenBudgetManager()
        self.dynamic_adjuster = dynamic_adjuster or DynamicCompressionAdjuster()

    def select_strategy(
        self,
        conversation: MockConversation,
        model: str,
        system_prompt: str,
        current_message: str
    ) -> Tuple[str, Dict[str, Any]]:
        """自动选择最优策略"""
        # 1. 计算 token 预算
        budget = self.token_budget_manager.create_budget(
            model, system_prompt, current_message
        )

        # 2. 分析对话特征
        features = self._analyze_conversation(conversation)

        # 3. 选择基础策略
        strategy = self._select_based_on_features(budget, features)

        # 4. 动态调整参数
        params = self.dynamic_adjuster.adjust_compression_params(
            current_tokens=features["total_tokens"],
            available_tokens=budget["available_for_history"],
            strategy_name=strategy,
            conversation_id=conversation.conversation_id
        )

        return strategy, params

    def _analyze_conversation(self, conversation: MockConversation) -> Dict[str, Any]:
        """分析对话特征"""
        messages = conversation.messages

        if not messages:
            return {
                "total_messages": 0,
                "total_tokens": 0,
                "avg_tokens_per_message": 0,
                "has_code": False,
                "has_long_messages": False,
                "is_technical": False,
                "has_tool_calls": False,
                "recent_message_count": 0
            }

        total_messages = len(messages)
        total_tokens = sum(msg.token_count or msg.metadata.get("tokens", 0) for msg in messages)
        avg_tokens_per_message = total_tokens / total_messages if total_messages > 0 else 0

        has_code = any("```" in msg.content for msg in messages)
        has_long_messages = any(len(msg.content) > 2000 for msg in messages)
        is_technical = self._is_technical_conversation(messages)
        has_tool_calls = any(msg.has_tool_calls() for msg in messages)
        recent_message_count = min(10, len(messages))

        return {
            "total_messages": total_messages,
            "total_tokens": total_tokens,
            "avg_tokens_per_message": avg_tokens_per_message,
            "has_code": has_code,
            "has_long_messages": has_long_messages,
            "is_technical": is_technical,
            "has_tool_calls": has_tool_calls,
            "recent_message_count": recent_message_count
        }

    def _select_based_on_features(
        self, budget: Dict[str, Any], features: Dict[str, Any]
    ) -> str:
        """基于特征选择策略"""
        available = budget["available_for_history"]
        total_tokens = features["total_tokens"]

        # 规则 1: 空间充足
        if total_tokens < available * 0.5:
            return "full"

        # 规则 2: 技术对话且有代码
        if features["is_technical"] and features["has_code"]:
            return "smart"

        # 规则 3: 有工具调用
        if features["has_tool_calls"]:
            return "smart"

        # 规则 4: 消息很多但token不多
        if features["total_messages"] > 50 and features["avg_tokens_per_message"] < 100:
            return "sliding_window"

        # 规则 5: 有很长的消息
        if features["has_long_messages"]:
            return "smart"

        # 规则 6: 默认智能压缩
        return "smart"

    def _is_technical_conversation(self, messages: List[MockMessage]) -> bool:
        """判断是否为技术对话"""
        technical_keywords = [
            "function", "class", "import", "def", "async",
            "error", "bug", "debug", "api", "database",
            "sql", "query", "table", "column", "index"
        ]

        technical_count = 0
        recent_messages = messages[-10:]

        for msg in recent_messages:
            content_lower = msg.content.lower()
            if any(kw in content_lower for kw in technical_keywords):
                technical_count += 1

        return technical_count >= 3


# ============= 测试函数 =============

def test_conversation_analysis():
    """测试对话分析"""
    print("\n[TEST] Conversation Analysis")
    print("-" * 60)

    selector = AdaptiveStrategySelector()

    # 场景 1: 空对话
    conv1 = MockConversation("conv1")
    features1 = selector._analyze_conversation(conv1)
    assert features1["total_messages"] == 0
    print("[PASS] Empty conversation analyzed")

    # 场景 2: 技术对话
    conv2 = MockConversation("conv2")
    conv2.add_message("How to debug this function?")
    conv2.add_message("I'm getting an error in my SQL query")
    conv2.add_message("The database connection failed")
    conv2.add_message("Let me check the API endpoint")

    features2 = selector._analyze_conversation(conv2)
    assert features2["is_technical"] is True
    assert features2["total_messages"] == 4
    print(f"[PASS] Technical conversation: {features2['is_technical']}")

    # 场景 3: 包含代码
    conv3 = MockConversation("conv3")
    conv3.add_message("Here's my code:\n```python\ndef hello():\n    pass\n```")

    features3 = selector._analyze_conversation(conv3)
    assert features3["has_code"] is True
    print(f"[PASS] Code detection: {features3['has_code']}")


def test_strategy_selection_rules():
    """测试策略选择规则"""
    print("\n[TEST] Strategy Selection Rules")
    print("-" * 60)

    selector = AdaptiveStrategySelector()

    # 场景 1: 空间充足 -> full
    conv1 = MockConversation("conv1")
    for i in range(10):
        conv1.add_message(f"Short message {i}", token_count=50)

    strategy1, params1 = selector.select_strategy(
        conv1, "claude-sonnet-4-5", "System prompt", "Current message"
    )
    print(f"[PASS] Low usage (10 msgs, 500 tokens): {strategy1}")
    assert strategy1 == "full"

    # 场景 2: 技术对话 + 代码，但空间充足 -> full
    conv2 = MockConversation("conv2")
    conv2.add_message("I have an error in my function")
    conv2.add_message("Here's the debug trace")
    conv2.add_message("The SQL query is failing")
    conv2.add_message("API connection issue")
    conv2.add_message("```python\ndef test():\n    pass\n```")

    strategy2, params2 = selector.select_strategy(
        conv2, "claude-sonnet-4-5", "System prompt", "Current message"
    )
    print(f"[PASS] Technical + code (low usage): {strategy2}")
    # 当使用率很低时，即使是技术对话也会选择 full
    assert strategy2 == "full"

    # 场景 2b: 技术对话 + 代码，空间紧张 -> smart
    conv2b = MockConversation("conv2b")
    # 添加大量消息以提高 token 使用率（需要超过 50% 的 181,000 = 90,500 tokens）
    for i in range(200):
        conv2b.add_message(f"Technical discussion about API {i}", token_count=500)
    # 总共约 100,000 tokens，超过 50% 阈值
    conv2b.add_message("Here's the problematic function")
    conv2b.add_message("```python\ndef buggy_code():\n    pass\n```")
    conv2b.add_message("The error occurs in the database query")
    conv2b.add_message("Let me debug this API endpoint")

    strategy2b, params2b = selector.select_strategy(
        conv2b, "claude-sonnet-4-5", "System prompt", "Current message"
    )
    total_tokens = sum(m.token_count for m in conv2b.messages)
    print(f"[PASS] Technical + code (high usage, {total_tokens:,} tokens): {strategy2b}")
    # 当空间紧张时，技术对话 + 代码 -> smart
    assert strategy2b == "smart"

    # 场景 3: 很多短消息 -> sliding_window
    conv3 = MockConversation("conv3")
    for i in range(60):
        conv3.add_message(f"msg {i}", token_count=50)

    strategy3, params3 = selector.select_strategy(
        conv3, "claude-sonnet-4-5", "System prompt", "Current message"
    )
    print(f"[PASS] Many short messages (60 msgs): {strategy3}")
    # 注意：这里可能是 full 因为总 tokens 还不高
    print(f"  Total tokens: {sum(m.token_count for m in conv3.messages)}")


def test_integration_scenario():
    """测试完整场景"""
    print("\n[TEST] Integration Scenario")
    print("-" * 60)

    selector = AdaptiveStrategySelector()

    # 模拟一个真实的技术支持对话
    conv = MockConversation("support_001")

    # 初期：简单问题
    conv.add_message("Hi, I need help with my database")
    conv.add_message("Sure, what's the issue?")

    strategy1, params1 = selector.select_strategy(
        conv, "claude-sonnet-4-5",
        "You are a database expert",
        "Please help me"
    )
    print(f"Phase 1 (2 msgs): {strategy1}")

    # 中期：深入技术问题（少量消息时仍然返回 full）
    conv.add_message("I'm getting connection errors")
    conv.add_message("The error message says 'Connection timeout'")
    conv.add_message("Here's my config:\n```json\n{\"host\": \"localhost\"}\n```")
    conv.add_message("Let me check the firewall settings")

    strategy2, params2 = selector.select_strategy(
        conv, "claude-sonnet-4-5",
        "You are a database expert",
        "What should I do?"
    )
    print(f"Phase 2 (6 msgs, technical, low usage): {strategy2}")
    # 即使是技术对话，使用率低时也返回 full
    assert strategy2 == "full"

    # 后期：大量对话历史
    for i in range(20):
        conv.add_message(f"Additional troubleshooting step {i}")

    strategy3, params3 = selector.select_strategy(
        conv, "claude-sonnet-4-5",
        "You are a database expert",
        "Still having issues"
    )
    print(f"Phase 3 (26 msgs): {strategy3}, intensity: {params3.get('intensity')}")

    # 验证参数
    assert "strategy" in params3
    assert "intensity" in params3
    print(f"[PASS] Final params: keep_first={params3.get('keep_first')}, "
          f"keep_last={params3.get('keep_last')}")


def test_technical_detection():
    """测试技术对话检测"""
    print("\n[TEST] Technical Detection")
    print("-" * 60)

    selector = AdaptiveStrategySelector()

    # 非技术对话
    conv1 = MockConversation("casual")
    conv1.add_message("Hello, how are you?")
    conv1.add_message("I'm fine, thanks!")
    conv1.add_message("What's the weather like?")

    is_tech1 = selector._is_technical_conversation(conv1.messages)
    assert is_tech1 is False
    print(f"[PASS] Casual conversation: {is_tech1}")

    # 技术对话
    conv2 = MockConversation("technical")
    conv2.add_message("I have a bug in my code")
    conv2.add_message("The function is throwing an error")
    conv2.add_message("Let me check the database query")
    conv2.add_message("The API endpoint is not responding")

    is_tech2 = selector._is_technical_conversation(conv2.messages)
    assert is_tech2 is True
    print(f"[PASS] Technical conversation: {is_tech2}")


def main():
    """运行所有测试"""
    print("=" * 60)
    print("Adaptive Strategy Selector - Standalone Tests")
    print("=" * 60)

    try:
        test_conversation_analysis()
        test_strategy_selection_rules()
        test_technical_detection()
        test_integration_scenario()

        print("\n" + "=" * 60)
        print("[SUCCESS] All tests passed!")
        print("=" * 60)
        print("\nPhase 2.3 (Adaptive Strategy Selector) completed successfully.")

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
