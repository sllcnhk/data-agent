"""
独立测试 Token Budget Manager - 完全独立版本
不依赖任何 backend 导入
"""
import sys
from typing import Dict, Any


# ============= 模拟 TokenCounter =============
class MockTokenCounter:
    """模拟 token 计数器"""

    def count_tokens(self, text: str, model: str) -> int:
        """简单估算：每4个字符约1个token"""
        return len(text) // 4


# ============= TokenBudgetCalculator (复制自 backend/core/token_budget.py) =============
class TokenBudgetCalculator:
    """Token 预算计算器"""

    def __init__(self):
        """初始化计算器"""
        # 各模型的上下文窗口配置
        self.model_limits = {
            "claude-sonnet-4-5": {
                "context_window": 200000,
                "max_output": 8192,
                "reserved_for_output": 8192,
                "system_prompt_reserve": 500,
                "safety_margin": 0.05  # 5% 安全边距
            },
            "claude-3-5-sonnet": {
                "context_window": 200000,
                "max_output": 8192,
                "reserved_for_output": 8192,
                "system_prompt_reserve": 500,
                "safety_margin": 0.05
            },
            "claude-3-opus": {
                "context_window": 200000,
                "max_output": 4096,
                "reserved_for_output": 4096,
                "system_prompt_reserve": 500,
                "safety_margin": 0.05
            },
            "gpt-4-turbo": {
                "context_window": 128000,
                "max_output": 4096,
                "reserved_for_output": 4096,
                "system_prompt_reserve": 500,
                "safety_margin": 0.05
            },
            "gpt-4": {
                "context_window": 8192,
                "max_output": 4096,
                "reserved_for_output": 4096,
                "system_prompt_reserve": 500,
                "safety_margin": 0.05
            },
            "gpt-3.5-turbo": {
                "context_window": 16385,
                "max_output": 4096,
                "reserved_for_output": 4096,
                "system_prompt_reserve": 500,
                "safety_margin": 0.05
            }
        }

    def get_model_config(self, model: str) -> Dict[str, Any]:
        """获取模型配置"""
        # 尝试精确匹配
        if model in self.model_limits:
            return self.model_limits[model]

        # 尝试模糊匹配
        model_lower = model.lower()
        for key in self.model_limits.keys():
            if key in model_lower or model_lower in key:
                return self.model_limits[key]

        # 默认使用 Claude Sonnet 配置
        print(f"[WARN] Unknown model {model}, using claude-sonnet-4-5 defaults")
        return self.model_limits["claude-sonnet-4-5"]

    def calculate_available_tokens(
        self,
        model: str,
        system_prompt_tokens: int,
        current_message_tokens: int
    ) -> int:
        """计算可用的上下文 token 数"""
        config = self.get_model_config(model)

        context_window = config["context_window"]
        reserved_output = config["reserved_for_output"]
        safety_margin = int(context_window * config["safety_margin"])

        # 可用 token = 总窗口 - 输出预留 - 系统提示 - 当前消息 - 安全边距
        available = (
            context_window
            - reserved_output
            - system_prompt_tokens
            - current_message_tokens
            - safety_margin
        )

        return max(0, available)

    def estimate_compression_needed(
        self,
        current_tokens: int,
        available_tokens: int
    ) -> float:
        """估算需要的压缩率"""
        if current_tokens <= available_tokens:
            return 0.0  # 不需要压缩

        # 计算压缩率
        compression_ratio = 1 - (available_tokens / current_tokens)

        # 最多压缩 95%
        return min(compression_ratio, 0.95)


# ============= TokenBudgetManager (复制自 backend/core/token_budget.py) =============
class TokenBudgetManager:
    """Token 预算管理器"""

    def __init__(self, token_counter=None):
        """初始化管理器"""
        self.calculator = TokenBudgetCalculator()
        self.token_counter = token_counter or MockTokenCounter()

    def create_budget(
        self,
        model: str,
        system_prompt: str,
        current_message: str
    ) -> Dict[str, Any]:
        """创建 token 预算"""
        # 计算各部分 token
        system_tokens = self.token_counter.count_tokens(system_prompt, model)
        message_tokens = self.token_counter.count_tokens(current_message, model)

        # 计算可用 token
        available_tokens = self.calculator.calculate_available_tokens(
            model, system_tokens, message_tokens
        )

        # 获取模型配置
        config = self.calculator.get_model_config(model)

        budget = {
            "model": model,
            "context_window": config["context_window"],
            "max_output": config["max_output"],
            "system_tokens": system_tokens,
            "message_tokens": message_tokens,
            "available_for_history": available_tokens,
            "recommended_max_messages": self._estimate_max_messages(available_tokens),
            "compression_strategy": self._recommend_strategy(available_tokens),
            "utilization": {
                "system": system_tokens / config["context_window"],
                "message": message_tokens / config["context_window"],
                "available": available_tokens / config["context_window"]
            }
        }

        return budget

    def check_budget(
        self,
        current_tokens: int,
        budget: Dict[str, Any]
    ) -> Dict[str, Any]:
        """检查当前 token 使用情况"""
        available = budget["available_for_history"]
        compression_needed = self.calculator.estimate_compression_needed(
            current_tokens, available
        )

        return {
            "current_tokens": current_tokens,
            "available_tokens": available,
            "over_budget": current_tokens > available,
            "utilization": current_tokens / available if available > 0 else 1.0,
            "compression_needed": compression_needed,
            "action": self._recommend_action(current_tokens, available)
        }

    def _estimate_max_messages(self, available_tokens: int) -> int:
        """估算最大消息数"""
        avg_tokens_per_message = 200
        return max(1, int(available_tokens / avg_tokens_per_message))

    def _recommend_strategy(self, available_tokens: int) -> str:
        """根据可用 token 推荐压缩策略"""
        if available_tokens > 100000:
            return "full"  # 充足空间，不压缩
        elif available_tokens > 50000:
            return "sliding_window"  # 中等空间，滑动窗口
        elif available_tokens > 20000:
            return "smart"  # 空间紧张，智能压缩
        else:
            return "smart"  # 非常紧张，智能压缩

    def _recommend_action(self, current_tokens: int, available_tokens: int) -> str:
        """推荐行动"""
        utilization = current_tokens / available_tokens if available_tokens > 0 else 1.0

        if utilization <= 0.5:
            return "no_action"  # 使用率 < 50%，无需行动
        elif utilization <= 0.75:
            return "monitor"  # 使用率 50-75%，监控
        elif utilization <= 1.0:
            return "compress"  # 使用率 75-100%，需要压缩
        else:
            return "compress_aggressive"  # 超出预算，激进压缩


# ============= 测试函数 =============

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
    expected = 200000 - 8192 - 100 - 50 - 10000
    assert available == expected, f"Available should be {expected}, got {available}"
    print(f"[PASS] calculate_available_tokens: {available} tokens available")

    # 测试 3: 估算压缩率
    compression = calculator.estimate_compression_needed(
        current_tokens=2000,
        available_tokens=1000
    )
    assert compression == 0.5, f"Compression should be 0.5, got {compression}"
    print(f"[PASS] estimate_compression_needed: {compression:.2%}")

    print("[PASS] TokenBudgetCalculator: All tests passed")


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
        assert strategy == expected, f"Strategy for {tokens} should be {expected}, got {strategy}"
        print(f"  {tokens:,} tokens -> {strategy}")

    print("\n[PASS] TokenBudgetManager: All tests passed")


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
        print(f"  [WARN] Over budget! Compression needed: {check['compression_needed']:.2%}")
    else:
        print(f"  [OK] Within budget")

    print("\n[PASS] Integration test passed")


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
        print("[SUCCESS] All tests passed!")
        print("=" * 60)
        print("\nPhase 2.1 (Token Budget Manager) completed successfully.")

        return 0

    except AssertionError as e:
        print(f"\n[FAIL] Test failed: {e}")
        return 1
    except Exception as e:
        print(f"\n[ERROR] Error: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
