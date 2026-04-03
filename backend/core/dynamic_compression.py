"""
Dynamic Compression Adjuster - Phase 2.2

根据实际 token 使用情况动态调整压缩强度

借鉴: LangChain 的动态 memory 管理
"""
from typing import Dict, Any, List, Optional
import logging
from datetime import datetime

logger = logging.getLogger(__name__)


class DynamicCompressionAdjuster:
    """动态压缩调整器"""

    def __init__(self, target_utilization: float = 0.75, history_limit: int = 100):
        """
        初始化动态压缩调整器

        Args:
            target_utilization: 目标利用率，默认 75%
            history_limit: 最多保留的历史记录数
        """
        self.target_utilization = target_utilization
        self.history_limit = history_limit
        self.history: List[Dict[str, Any]] = []  # 历史压缩记录

        # 压缩参数预设
        self.compression_presets = {
            "full": {
                "keep_first": None,  # 保留全部
                "keep_last": None,
                "compression_ratio": 0.0
            },
            "sliding_window_relaxed": {
                "keep_first": 5,
                "keep_last": 20,
                "compression_ratio": 0.3
            },
            "sliding_window_normal": {
                "keep_first": 3,
                "keep_last": 15,
                "compression_ratio": 0.5
            },
            "sliding_window_aggressive": {
                "keep_first": 2,
                "keep_last": 10,
                "compression_ratio": 0.6
            },
            "smart_relaxed": {
                "keep_first": 3,
                "keep_last": 15,
                "use_summary": True,
                "compression_ratio": 0.5
            },
            "smart_normal": {
                "keep_first": 2,
                "keep_last": 10,
                "use_summary": True,
                "compression_ratio": 0.7
            },
            "smart_aggressive": {
                "keep_first": 1,
                "keep_last": 5,
                "use_summary": True,
                "compression_ratio": 0.8
            }
        }

        logger.info(
            f"DynamicCompressionAdjuster initialized: "
            f"target_utilization={target_utilization}, "
            f"history_limit={history_limit}"
        )

    def adjust_compression_params(
        self,
        current_tokens: int,
        available_tokens: int,
        strategy_name: str,
        conversation_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        根据当前使用情况动态调整压缩参数

        Args:
            current_tokens: 当前历史消息的 token 数
            available_tokens: 可用的 token 数
            strategy_name: 当前策略名称
            conversation_id: 对话 ID（用于跟踪）

        Returns:
            调整后的压缩参数
        """
        # 计算当前利用率
        utilization = current_tokens / available_tokens if available_tokens > 0 else 1.0

        # 记录历史
        self._record_history(
            current_tokens=current_tokens,
            available_tokens=available_tokens,
            utilization=utilization,
            strategy=strategy_name,
            conversation_id=conversation_id
        )

        # 根据利用率调整策略强度
        adjusted_params = self._adjust_by_utilization(
            utilization=utilization,
            strategy_name=strategy_name,
            current_tokens=current_tokens,
            available_tokens=available_tokens
        )

        logger.debug(
            f"Compression adjusted: "
            f"utilization={utilization:.2%}, "
            f"strategy={strategy_name}, "
            f"adjusted={adjusted_params}"
        )

        return adjusted_params

    def _adjust_by_utilization(
        self,
        utilization: float,
        strategy_name: str,
        current_tokens: int,
        available_tokens: int
    ) -> Dict[str, Any]:
        """
        根据利用率调整压缩参数

        Args:
            utilization: 当前利用率
            strategy_name: 策略名称
            current_tokens: 当前 token 数
            available_tokens: 可用 token 数

        Returns:
            调整后的参数
        """
        # 1. 计算与目标利用率的偏差
        deviation = utilization - self.target_utilization

        # 2. 根据偏差调整压缩强度
        if abs(deviation) < 0.1:
            # 偏差 < 10%，保持当前策略
            return self._get_strategy_params(strategy_name, "normal")

        elif deviation > 0.25:
            # 严重超出（> 25%），需要激进压缩
            if strategy_name == "full":
                return self._get_strategy_params("sliding_window", "aggressive")
            elif strategy_name == "sliding_window":
                return self._get_strategy_params("smart", "aggressive")
            elif strategy_name == "smart":
                return self._get_strategy_params("smart", "aggressive")

        elif deviation > 0.1:
            # 中等超出（10-25%），需要正常压缩
            if strategy_name == "full":
                return self._get_strategy_params("sliding_window", "normal")
            elif strategy_name == "sliding_window":
                return self._get_strategy_params("smart", "normal")
            elif strategy_name == "smart":
                return self._get_strategy_params("smart", "aggressive")

        elif deviation < -0.2:
            # 使用率过低（< target - 20%），可以放松压缩
            if strategy_name == "smart":
                return self._get_strategy_params("smart", "relaxed")
            elif strategy_name == "sliding_window":
                return self._get_strategy_params("sliding_window", "relaxed")
            elif strategy_name == "full":
                return self._get_strategy_params("full", "normal")

        # 默认保持当前策略
        return self._get_strategy_params(strategy_name, "normal")

    def _get_strategy_params(self, strategy: str, intensity: str) -> Dict[str, Any]:
        """
        获取策略参数

        Args:
            strategy: 策略名称 (full/sliding_window/smart)
            intensity: 强度 (relaxed/normal/aggressive)

        Returns:
            策略参数
        """
        preset_key = f"{strategy}_{intensity}" if strategy != "full" else "full"

        if preset_key in self.compression_presets:
            params = self.compression_presets[preset_key].copy()
            params["strategy"] = strategy
            params["intensity"] = intensity
            return params

        # 如果没有找到预设，返回默认参数
        logger.warning(f"Preset not found: {preset_key}, using defaults")
        return {
            "strategy": strategy,
            "intensity": "normal",
            "keep_first": 2,
            "keep_last": 10
        }

    def _record_history(
        self,
        current_tokens: int,
        available_tokens: int,
        utilization: float,
        strategy: str,
        conversation_id: Optional[str] = None
    ):
        """
        记录历史数据

        Args:
            current_tokens: 当前 token 数
            available_tokens: 可用 token 数
            utilization: 利用率
            strategy: 策略名称
            conversation_id: 对话 ID
        """
        record = {
            "timestamp": datetime.now().isoformat(),
            "current_tokens": current_tokens,
            "available_tokens": available_tokens,
            "utilization": utilization,
            "strategy": strategy,
            "conversation_id": conversation_id
        }

        self.history.append(record)

        # 限制历史记录长度
        if len(self.history) > self.history_limit:
            self.history = self.history[-self.history_limit:]

    def get_statistics(self) -> Dict[str, Any]:
        """
        获取调整统计信息

        Returns:
            统计信息字典
        """
        if not self.history:
            return {
                "total_adjustments": 0,
                "avg_utilization": 0.0,
                "max_utilization": 0.0,
                "min_utilization": 0.0
            }

        utilizations = [record["utilization"] for record in self.history]

        return {
            "total_adjustments": len(self.history),
            "avg_utilization": sum(utilizations) / len(utilizations),
            "max_utilization": max(utilizations),
            "min_utilization": min(utilizations),
            "recent_strategies": [
                record["strategy"] for record in self.history[-10:]
            ]
        }

    def reset_history(self):
        """清空历史记录"""
        self.history = []
        logger.info("Compression adjuster history reset")


# 全局实例
_dynamic_compression_adjuster = None


def get_dynamic_compression_adjuster(
    target_utilization: float = 0.75,
    history_limit: int = 100
) -> DynamicCompressionAdjuster:
    """
    获取 DynamicCompressionAdjuster 单例

    Args:
        target_utilization: 目标利用率
        history_limit: 历史记录限制

    Returns:
        DynamicCompressionAdjuster 实例
    """
    global _dynamic_compression_adjuster
    if _dynamic_compression_adjuster is None:
        _dynamic_compression_adjuster = DynamicCompressionAdjuster(
            target_utilization=target_utilization,
            history_limit=history_limit
        )
    return _dynamic_compression_adjuster
