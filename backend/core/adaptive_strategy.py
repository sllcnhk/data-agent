"""
Adaptive Strategy Selector - Phase 2.3

根据对话特征自动选择最优压缩策略

借鉴: LlamaIndex 的智能索引选择
"""
from typing import Dict, Any, List, Tuple, Optional
import logging

from backend.core.token_budget import get_token_budget_manager
from backend.core.dynamic_compression import get_dynamic_compression_adjuster
from backend.core.conversation_format import UnifiedConversation, UnifiedMessage

logger = logging.getLogger(__name__)


class AdaptiveStrategySelector:
    """自适应策略选择器"""

    def __init__(self):
        """初始化策略选择器"""
        self.token_budget_manager = get_token_budget_manager()
        self.dynamic_adjuster = get_dynamic_compression_adjuster()

        logger.info("AdaptiveStrategySelector initialized")

    def select_strategy(
        self,
        conversation: UnifiedConversation,
        model: str,
        system_prompt: str,
        current_message: str
    ) -> Tuple[str, Dict[str, Any]]:
        """
        自动选择最优策略

        Args:
            conversation: 对话对象
            model: 模型名称
            system_prompt: 系统提示
            current_message: 当前消息

        Returns:
            (strategy_name, strategy_params)
        """
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

        logger.debug(
            f"Strategy selected: {strategy} with params {params} "
            f"for conversation {conversation.conversation_id}"
        )

        return strategy, params

    def _analyze_conversation(self, conversation: UnifiedConversation) -> Dict[str, Any]:
        """
        分析对话特征

        Args:
            conversation: 对话对象

        Returns:
            特征字典
        """
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

        # 计算统计信息
        total_messages = len(messages)
        total_tokens = sum(
            msg.token_count or msg.metadata.get("tokens", 0)
            for msg in messages
        )
        avg_tokens_per_message = total_tokens / total_messages if total_messages > 0 else 0

        # 检测对话类型
        has_code = any("```" in msg.content for msg in messages)
        has_long_messages = any(len(msg.content) > 2000 for msg in messages)
        is_technical = self._is_technical_conversation(messages)

        # 检测工具调用
        has_tool_calls = any(msg.has_tool_calls() for msg in messages)

        # 最近消息数（最近10条）
        recent_message_count = min(10, len(messages))

        features = {
            "total_messages": total_messages,
            "total_tokens": total_tokens,
            "avg_tokens_per_message": avg_tokens_per_message,
            "has_code": has_code,
            "has_long_messages": has_long_messages,
            "is_technical": is_technical,
            "has_tool_calls": has_tool_calls,
            "recent_message_count": recent_message_count
        }

        logger.debug(f"Conversation features: {features}")

        return features

    def _select_based_on_features(
        self,
        budget: Dict[str, Any],
        features: Dict[str, Any]
    ) -> str:
        """
        基于特征选择策略

        Args:
            budget: token 预算信息
            features: 对话特征

        Returns:
            策略名称 (full/sliding_window/smart)
        """
        available = budget["available_for_history"]
        total_tokens = features["total_tokens"]

        # 规则 1: 空间充足（使用率 < 50%）
        if total_tokens < available * 0.5:
            logger.debug("Strategy: full (low utilization)")
            return "full"

        # 规则 2: 技术对话且有代码，需要保留上下文
        if features["is_technical"] and features["has_code"]:
            logger.debug("Strategy: smart (technical with code)")
            return "smart"

        # 规则 3: 有工具调用，需要保留完整上下文
        if features["has_tool_calls"]:
            logger.debug("Strategy: smart (has tool calls)")
            return "smart"

        # 规则 4: 消息很多但每条 token 不多，简单滑动窗口即可
        if features["total_messages"] > 50 and features["avg_tokens_per_message"] < 100:
            logger.debug("Strategy: sliding_window (many short messages)")
            return "sliding_window"

        # 规则 5: 有很长的消息，需要智能压缩
        if features["has_long_messages"]:
            logger.debug("Strategy: smart (has long messages)")
            return "smart"

        # 规则 6: 默认智能压缩
        logger.debug("Strategy: smart (default)")
        return "smart"

    def _is_technical_conversation(self, messages: List[UnifiedMessage]) -> bool:
        """
        判断是否为技术对话

        Args:
            messages: 消息列表

        Returns:
            是否为技术对话
        """
        technical_keywords = [
            "function", "class", "import", "def", "async",
            "error", "bug", "debug", "api", "database",
            "sql", "query", "table", "column", "index",
            "exception", "traceback", "stack", "code",
            "variable", "parameter", "return", "loop"
        ]

        technical_count = 0
        recent_messages = messages[-10:]  # 检查最近10条

        for msg in recent_messages:
            content_lower = msg.content.lower()
            if any(kw in content_lower for kw in technical_keywords):
                technical_count += 1

        # 如果最近10条中有3条以上包含技术关键词，则判定为技术对话
        is_technical = technical_count >= 3

        logger.debug(
            f"Technical check: {technical_count}/{len(recent_messages)} "
            f"messages with technical keywords -> {is_technical}"
        )

        return is_technical

    def get_recommendation_explanation(
        self,
        conversation: UnifiedConversation,
        model: str,
        system_prompt: str,
        current_message: str
    ) -> Dict[str, Any]:
        """
        获取策略推荐的详细解释（用于调试和可观测性）

        Args:
            conversation: 对话对象
            model: 模型名称
            system_prompt: 系统提示
            current_message: 当前消息

        Returns:
            包含策略、参数和解释的字典
        """
        budget = self.token_budget_manager.create_budget(
            model, system_prompt, current_message
        )
        features = self._analyze_conversation(conversation)
        strategy, params = self.select_strategy(
            conversation, model, system_prompt, current_message
        )

        return {
            "strategy": strategy,
            "params": params,
            "budget": budget,
            "features": features,
            "explanation": self._generate_explanation(budget, features, strategy)
        }

    def _generate_explanation(
        self,
        budget: Dict[str, Any],
        features: Dict[str, Any],
        strategy: str
    ) -> str:
        """
        生成策略选择的解释

        Args:
            budget: 预算信息
            features: 特征信息
            strategy: 选择的策略

        Returns:
            解释文本
        """
        utilization = (
            features["total_tokens"] / budget["available_for_history"]
            if budget["available_for_history"] > 0 else 0
        )

        explanation_parts = [
            f"Selected strategy: {strategy}",
            f"Token utilization: {utilization:.1%}",
            f"Total messages: {features['total_messages']}",
        ]

        if features["is_technical"]:
            explanation_parts.append("✓ Technical conversation detected")
        if features["has_code"]:
            explanation_parts.append("✓ Code blocks detected")
        if features["has_tool_calls"]:
            explanation_parts.append("✓ Tool calls detected")
        if features["has_long_messages"]:
            explanation_parts.append("✓ Long messages detected")

        return " | ".join(explanation_parts)


# 全局实例
_adaptive_strategy_selector = None


def get_adaptive_strategy_selector() -> AdaptiveStrategySelector:
    """
    获取 AdaptiveStrategySelector 单例

    Returns:
        AdaptiveStrategySelector 实例
    """
    global _adaptive_strategy_selector
    if _adaptive_strategy_selector is None:
        _adaptive_strategy_selector = AdaptiveStrategySelector()
    return _adaptive_strategy_selector
