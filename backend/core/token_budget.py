"""
Token Budget Manager - Phase 2.1

管理对话的 token 预算，防止超出上下文窗口限制

借鉴: LangChain 的 ConversationTokenBufferMemory
"""
from typing import Dict, Any, Optional
import logging

from backend.core.token_counter import get_token_counter

logger = logging.getLogger(__name__)


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
        """
        获取模型配置

        Args:
            model: 模型名称

        Returns:
            模型配置字典
        """
        # 尝试精确匹配
        if model in self.model_limits:
            return self.model_limits[model]

        # 尝试模糊匹配
        model_lower = model.lower()
        for key in self.model_limits.keys():
            if key in model_lower or model_lower in key:
                return self.model_limits[key]

        # 默认使用 Claude Sonnet 配置
        logger.warning(f"Unknown model {model}, using claude-sonnet-4-5 defaults")
        return self.model_limits["claude-sonnet-4-5"]

    def calculate_available_tokens(
        self,
        model: str,
        system_prompt_tokens: int,
        current_message_tokens: int
    ) -> int:
        """
        计算可用的上下文 token 数

        Args:
            model: 模型名称
            system_prompt_tokens: 系统提示的 token 数
            current_message_tokens: 当前消息的 token 数

        Returns:
            可用于历史消息的 token 数
        """
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
        """
        估算需要的压缩率

        Args:
            current_tokens: 当前历史消息的 token 数
            available_tokens: 可用的 token 数

        Returns:
            需要的压缩率 (0.0-0.95)
        """
        if current_tokens <= available_tokens:
            return 0.0  # 不需要压缩

        # 计算压缩率
        compression_ratio = 1 - (available_tokens / current_tokens)

        # 最多压缩 95%
        return min(compression_ratio, 0.95)


class TokenBudgetManager:
    """Token 预算管理器"""

    def __init__(self):
        """初始化管理器"""
        self.calculator = TokenBudgetCalculator()
        self.token_counter = get_token_counter()

    def create_budget(
        self,
        model: str,
        system_prompt: str,
        current_message: str
    ) -> Dict[str, Any]:
        """
        创建 token 预算

        Args:
            model: 模型名称
            system_prompt: 系统提示
            current_message: 当前消息

        Returns:
            预算信息字典
        """
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

        logger.debug(
            f"Token budget created: "
            f"available={available_tokens}/{config['context_window']}, "
            f"strategy={budget['compression_strategy']}"
        )

        return budget

    def check_budget(
        self,
        current_tokens: int,
        budget: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        检查当前 token 使用情况

        Args:
            current_tokens: 当前历史消息的 token 数
            budget: 预算信息

        Returns:
            检查结果
        """
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
        """
        估算最大消息数

        假设平均每条消息 200 tokens (user + assistant pair)
        """
        avg_tokens_per_message = 200
        return max(1, int(available_tokens / avg_tokens_per_message))

    def _recommend_strategy(self, available_tokens: int) -> str:
        """
        根据可用 token 推荐压缩策略

        Args:
            available_tokens: 可用 token 数

        Returns:
            推荐的策略名称
        """
        if available_tokens > 100000:
            return "full"  # 充足空间，不压缩
        elif available_tokens > 50000:
            return "sliding_window"  # 中等空间，滑动窗口
        elif available_tokens > 20000:
            return "smart"  # 空间紧张，智能压缩
        else:
            return "smart"  # 非常紧张，智能压缩

    def _recommend_action(self, current_tokens: int, available_tokens: int) -> str:
        """
        推荐行动

        Args:
            current_tokens: 当前 token 数
            available_tokens: 可用 token 数

        Returns:
            推荐的行动
        """
        utilization = current_tokens / available_tokens if available_tokens > 0 else 1.0

        if utilization <= 0.5:
            return "no_action"  # 使用率 < 50%，无需行动
        elif utilization <= 0.75:
            return "monitor"  # 使用率 50-75%，监控
        elif utilization <= 1.0:
            return "compress"  # 使用率 75-100%，需要压缩
        else:
            return "compress_aggressive"  # 超出预算，激进压缩


# 全局实例
_token_budget_manager = None


def get_token_budget_manager() -> TokenBudgetManager:
    """获取 TokenBudgetManager 单例"""
    global _token_budget_manager
    if _token_budget_manager is None:
        _token_budget_manager = TokenBudgetManager()
    return _token_budget_manager
