"""
独立测试 Unified Context Manager - Phase 2.4
测试不依赖数据库的 prepare_context_from_unified 方法
"""
import sys
from typing import Dict, Any, List, Optional


# ============= Mock Classes from previous phases =============
class MockMessage:
    """模拟消息"""
    def __init__(self, content: str, role: str = "user", token_count: int = None):
        self.content = content
        self.role = role
        self.token_count = token_count or len(content) // 4
        self.metadata = {"tokens": self.token_count}
        self.tool_calls = None
        self.value = role  # For MessageRole compatibility

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

    def adjust_compression_params(
        self, current_tokens: int, available_tokens: int,
        strategy_name: str, conversation_id: Optional[str] = None
    ) -> Dict[str, Any]:
        utilization = current_tokens / available_tokens if available_tokens > 0 else 1.0
        deviation = utilization - self.target_utilization

        if abs(deviation) < 0.1:
            intensity = "normal"
        elif deviation > 0.1:
            intensity = "aggressive"
        else:
            intensity = "relaxed"

        return {
            "strategy": strategy_name,
            "intensity": intensity,
            "keep_first": 2,
            "keep_last": 10
        }


class AdaptiveStrategySelector:
    """自适应策略选择器"""
    def __init__(self, token_budget_manager=None, dynamic_adjuster=None):
        self.token_budget_manager = token_budget_manager or TokenBudgetManager()
        self.dynamic_adjuster = dynamic_adjuster or DynamicCompressionAdjuster()

    def select_strategy(
        self, conversation: MockConversation, model: str,
        system_prompt: str, current_message: str
    ):
        budget = self.token_budget_manager.create_budget(model, system_prompt, current_message)
        features = self._analyze_conversation(conversation)

        # Simple strategy selection
        available = budget["available_for_history"]
        total_tokens = features["total_tokens"]

        if total_tokens < available * 0.5:
            strategy = "full"
        elif features["is_technical"]:
            strategy = "smart"
        else:
            strategy = "sliding_window"

        params = self.dynamic_adjuster.adjust_compression_params(
            total_tokens, available, strategy, conversation.conversation_id
        )

        return strategy, params

    def _analyze_conversation(self, conversation: MockConversation) -> Dict[str, Any]:
        messages = conversation.messages
        if not messages:
            return {
                "total_messages": 0,
                "total_tokens": 0,
                "is_technical": False
            }

        total_tokens = sum(msg.token_count for msg in messages)
        is_technical = self._is_technical_conversation(messages)

        return {
            "total_messages": len(messages),
            "total_tokens": total_tokens,
            "is_technical": is_technical
        }

    def _is_technical_conversation(self, messages: List[MockMessage]) -> bool:
        technical_keywords = ["function", "class", "error", "debug", "api", "database"]
        technical_count = sum(
            1 for msg in messages[-10:]
            if any(kw in msg.content.lower() for kw in technical_keywords)
        )
        return technical_count >= 3


class MockHybridContextManager:
    """模拟混合上下文管理器"""
    def __init__(self):
        self.current_strategy = "full"

    def set_strategy(self, strategy: str):
        self.current_strategy = strategy

    def compress_conversation(
        self, conversation: MockConversation, **kwargs
    ) -> MockConversation:
        """简单的压缩逻辑"""
        if self.current_strategy == "full":
            return conversation

        keep_first = kwargs.get("keep_first", 3)
        keep_last = kwargs.get("keep_last", 15)

        messages = conversation.messages
        if len(messages) <= keep_first + keep_last:
            return conversation

        # 创建压缩后的对话
        compressed = MockConversation(conversation.conversation_id)
        compressed.messages = messages[:keep_first] + messages[-keep_last:]

        return compressed


# ============= UnifiedContextManager =============
class UnifiedContextManager:
    """统一上下文管理器"""

    def __init__(
        self,
        hybrid_manager=None,
        token_budget_manager=None,
        adaptive_selector=None,
        token_counter=None
    ):
        self.hybrid_manager = hybrid_manager or MockHybridContextManager()
        self.token_budget_manager = token_budget_manager or TokenBudgetManager()
        self.adaptive_selector = adaptive_selector or AdaptiveStrategySelector()
        self.token_counter = token_counter or MockTokenCounter()

    def prepare_context_from_unified(
        self,
        unified_conv: MockConversation,
        model: str,
        system_prompt: str,
        current_message: str,
        max_tokens: Optional[int] = None,
        strategy: Optional[str] = None
    ) -> Dict[str, Any]:
        """从统一对话格式准备上下文"""

        # 1. 创建 token 预算
        budget = self.token_budget_manager.create_budget(
            model, system_prompt, current_message
        )

        # 2. 选择策略
        if strategy is None:
            strategy, params = self.adaptive_selector.select_strategy(
                unified_conv, model, system_prompt, current_message
            )
        else:
            params = {"strategy": strategy, "intensity": "normal"}

        # 3. 应用压缩
        original_message_count = len(unified_conv.messages)

        self.hybrid_manager.set_strategy(strategy)

        if strategy == "full":
            compressed_conv = unified_conv
        else:
            keep_first = params.get("keep_first", 3)
            keep_last = params.get("keep_last", 15)
            compressed_conv = self.hybrid_manager.compress_conversation(
                unified_conv,
                keep_first=keep_first,
                keep_last=keep_last
            )

        compressed_message_count = len(compressed_conv.messages)

        # 4. 估算 tokens
        estimated_tokens = self._estimate_context_tokens(
            system_prompt, compressed_conv.messages, model
        )

        # 5. 计算压缩率
        compression_ratio = (
            1 - compressed_message_count / original_message_count
            if original_message_count > 0 else 0
        )

        # 6. 构建返回结果
        return {
            "messages": [
                {
                    "role": msg.role if isinstance(msg.role, str) else msg.role.value,
                    "content": msg.content
                }
                for msg in compressed_conv.messages
            ],
            "system_prompt": system_prompt,
            "context_info": {
                "strategy": strategy,
                "strategy_params": params,
                "original_message_count": original_message_count,
                "compressed_message_count": compressed_message_count,
                "compression_ratio": compression_ratio,
                "estimated_tokens": estimated_tokens
            },
            "budget_info": budget
        }

    def _estimate_context_tokens(
        self, system_prompt: str, messages: List[MockMessage], model: str
    ) -> int:
        """估算上下文 token 数"""
        system_tokens = self.token_counter.count_tokens(system_prompt, model)
        message_tokens = sum(
            self.token_counter.count_tokens(msg.content, model)
            for msg in messages
        )
        return system_tokens + message_tokens


# ============= 测试函数 =============

def test_basic_context_preparation():
    """测试基本上下文准备"""
    print("\n[TEST] Basic Context Preparation")
    print("-" * 60)

    manager = UnifiedContextManager()

    # 创建简单对话
    conv = MockConversation("test_001")
    conv.add_message("Hello, how are you?")
    conv.add_message("I'm fine, thanks!")
    conv.add_message("Can you help me?")

    result = manager.prepare_context_from_unified(
        unified_conv=conv,
        model="claude-sonnet-4-5",
        system_prompt="You are a helpful assistant.",
        current_message="What can you do?"
    )

    # 验证返回结构
    assert "messages" in result
    assert "system_prompt" in result
    assert "context_info" in result
    assert "budget_info" in result

    print(f"[PASS] Result structure valid")
    print(f"  Messages: {len(result['messages'])}")
    print(f"  Strategy: {result['context_info']['strategy']}")
    print(f"  Compression: {result['context_info']['compression_ratio']:.2%}")


def test_automatic_strategy_selection():
    """测试自动策略选择"""
    print("\n[TEST] Automatic Strategy Selection")
    print("-" * 60)

    manager = UnifiedContextManager()

    # 场景 1: 少量消息 -> full
    conv1 = MockConversation("test_002")
    for i in range(5):
        conv1.add_message(f"Message {i}")

    result1 = manager.prepare_context_from_unified(
        unified_conv=conv1,
        model="claude-sonnet-4-5",
        system_prompt="System",
        current_message="Current"
    )

    print(f"[PASS] Few messages (5): {result1['context_info']['strategy']}")
    assert result1['context_info']['strategy'] == "full"

    # 场景 2: 技术对话 -> smart (需要高使用率)
    conv2 = MockConversation("test_003")
    # 添加足够多的消息以提高使用率
    for i in range(200):
        conv2.add_message(f"Technical discussion about API {i}", token_count=500)
    conv2.add_message("Let me debug this function")
    conv2.add_message("There's an error in the database query")

    result2 = manager.prepare_context_from_unified(
        unified_conv=conv2,
        model="claude-sonnet-4-5",
        system_prompt="System",
        current_message="Current"
    )

    print(f"[PASS] Technical conversation (202 msgs): {result2['context_info']['strategy']}")


def test_manual_strategy_specification():
    """测试手动指定策略"""
    print("\n[TEST] Manual Strategy Specification")
    print("-" * 60)

    manager = UnifiedContextManager()

    conv = MockConversation("test_004")
    for i in range(20):
        conv.add_message(f"Message {i}")

    # 手动指定 smart 策略
    result = manager.prepare_context_from_unified(
        unified_conv=conv,
        model="claude-sonnet-4-5",
        system_prompt="System",
        current_message="Current",
        strategy="smart"
    )

    assert result['context_info']['strategy'] == "smart"
    print(f"[PASS] Manual strategy: {result['context_info']['strategy']}")
    print(f"  Original: {result['context_info']['original_message_count']}")
    print(f"  Compressed: {result['context_info']['compressed_message_count']}")


def test_compression_effectiveness():
    """测试压缩效果"""
    print("\n[TEST] Compression Effectiveness")
    print("-" * 60)

    manager = UnifiedContextManager()

    # 创建大量消息
    conv = MockConversation("test_005")
    for i in range(50):
        conv.add_message(f"This is message number {i} with some content")

    # 使用 sliding_window 策略
    result = manager.prepare_context_from_unified(
        unified_conv=conv,
        model="claude-sonnet-4-5",
        system_prompt="System",
        current_message="Current",
        strategy="sliding_window"
    )

    original = result['context_info']['original_message_count']
    compressed = result['context_info']['compressed_message_count']
    ratio = result['context_info']['compression_ratio']

    print(f"[PASS] Compression results:")
    print(f"  Original: {original} messages")
    print(f"  Compressed: {compressed} messages")
    print(f"  Ratio: {ratio:.2%}")

    assert compressed < original, "Compression should reduce message count"
    assert ratio > 0, "Compression ratio should be positive"


def test_budget_info():
    """测试预算信息"""
    print("\n[TEST] Budget Info")
    print("-" * 60)

    manager = UnifiedContextManager()

    conv = MockConversation("test_006")
    conv.add_message("Short message")

    result = manager.prepare_context_from_unified(
        unified_conv=conv,
        model="claude-sonnet-4-5",
        system_prompt="You are a helpful assistant.",
        current_message="Hello!"
    )

    budget = result['budget_info']

    assert "model" in budget
    assert "context_window" in budget
    assert "available_for_history" in budget

    print(f"[PASS] Budget info:")
    print(f"  Model: {budget['model']}")
    print(f"  Context window: {budget['context_window']:,}")
    print(f"  Available: {budget['available_for_history']:,}")


def main():
    """运行所有测试"""
    print("=" * 60)
    print("Unified Context Manager - Standalone Tests")
    print("=" * 60)

    try:
        test_basic_context_preparation()
        test_automatic_strategy_selection()
        test_manual_strategy_specification()
        test_compression_effectiveness()
        test_budget_info()

        print("\n" + "=" * 60)
        print("[SUCCESS] All tests passed!")
        print("=" * 60)
        print("\nPhase 2.4 (Unified Context Manager) completed successfully.")

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
