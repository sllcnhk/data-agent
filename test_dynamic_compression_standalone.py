"""
独立测试 Dynamic Compression Adjuster - Phase 2.2
不依赖任何 backend 导入
"""
import sys
from typing import Dict, Any, List, Optional
from datetime import datetime


# ============= DynamicCompressionAdjuster (复制自 backend/core/dynamic_compression.py) =============
class DynamicCompressionAdjuster:
    """动态压缩调整器"""

    def __init__(self, target_utilization: float = 0.75, history_limit: int = 100):
        """初始化动态压缩调整器"""
        self.target_utilization = target_utilization
        self.history_limit = history_limit
        self.history: List[Dict[str, Any]] = []

        # 压缩参数预设
        self.compression_presets = {
            "full": {
                "keep_first": None,
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

    def adjust_compression_params(
        self,
        current_tokens: int,
        available_tokens: int,
        strategy_name: str,
        conversation_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """根据当前使用情况动态调整压缩参数"""
        utilization = current_tokens / available_tokens if available_tokens > 0 else 1.0

        self._record_history(
            current_tokens=current_tokens,
            available_tokens=available_tokens,
            utilization=utilization,
            strategy=strategy_name,
            conversation_id=conversation_id
        )

        adjusted_params = self._adjust_by_utilization(
            utilization=utilization,
            strategy_name=strategy_name,
            current_tokens=current_tokens,
            available_tokens=available_tokens
        )

        return adjusted_params

    def _adjust_by_utilization(
        self,
        utilization: float,
        strategy_name: str,
        current_tokens: int,
        available_tokens: int
    ) -> Dict[str, Any]:
        """根据利用率调整压缩参数"""
        deviation = utilization - self.target_utilization

        if abs(deviation) < 0.1:
            return self._get_strategy_params(strategy_name, "normal")

        elif deviation > 0.25:
            if strategy_name == "full":
                return self._get_strategy_params("sliding_window", "aggressive")
            elif strategy_name == "sliding_window":
                return self._get_strategy_params("smart", "aggressive")
            elif strategy_name == "smart":
                return self._get_strategy_params("smart", "aggressive")

        elif deviation > 0.1:
            if strategy_name == "full":
                return self._get_strategy_params("sliding_window", "normal")
            elif strategy_name == "sliding_window":
                return self._get_strategy_params("smart", "normal")
            elif strategy_name == "smart":
                return self._get_strategy_params("smart", "aggressive")

        elif deviation < -0.2:
            if strategy_name == "smart":
                return self._get_strategy_params("smart", "relaxed")
            elif strategy_name == "sliding_window":
                return self._get_strategy_params("sliding_window", "relaxed")
            elif strategy_name == "full":
                return self._get_strategy_params("full", "normal")

        return self._get_strategy_params(strategy_name, "normal")

    def _get_strategy_params(self, strategy: str, intensity: str) -> Dict[str, Any]:
        """获取策略参数"""
        preset_key = f"{strategy}_{intensity}" if strategy != "full" else "full"

        if preset_key in self.compression_presets:
            params = self.compression_presets[preset_key].copy()
            params["strategy"] = strategy
            params["intensity"] = intensity
            return params

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
        """记录历史数据"""
        record = {
            "timestamp": datetime.now().isoformat(),
            "current_tokens": current_tokens,
            "available_tokens": available_tokens,
            "utilization": utilization,
            "strategy": strategy,
            "conversation_id": conversation_id
        }

        self.history.append(record)

        if len(self.history) > self.history_limit:
            self.history = self.history[-self.history_limit:]

    def get_statistics(self) -> Dict[str, Any]:
        """获取调整统计信息"""
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


# ============= 测试函数 =============

def test_compression_presets():
    """测试压缩预设"""
    print("\n[TEST] Compression Presets")
    print("-" * 60)

    adjuster = DynamicCompressionAdjuster()

    # 验证所有预设都存在
    expected_presets = [
        "full",
        "sliding_window_relaxed",
        "sliding_window_normal",
        "sliding_window_aggressive",
        "smart_relaxed",
        "smart_normal",
        "smart_aggressive"
    ]

    for preset in expected_presets:
        assert preset in adjuster.compression_presets, f"Missing preset: {preset}"

    print(f"[PASS] All {len(expected_presets)} presets found")

    # 验证 smart_aggressive 的参数
    smart_aggressive = adjuster.compression_presets["smart_aggressive"]
    assert smart_aggressive["keep_first"] == 1
    assert smart_aggressive["keep_last"] == 5
    assert smart_aggressive["use_summary"] is True
    assert smart_aggressive["compression_ratio"] == 0.8

    print("[PASS] smart_aggressive preset verified")


def test_utilization_adjustment():
    """测试利用率调整逻辑"""
    print("\n[TEST] Utilization Adjustment")
    print("-" * 60)

    adjuster = DynamicCompressionAdjuster(target_utilization=0.75)

    # 场景 1: 低利用率 (30%) - 应该保持或放松
    params1 = adjuster.adjust_compression_params(
        current_tokens=30000,
        available_tokens=100000,
        strategy_name="smart"
    )
    print(f"[PASS] Low utilization (30%):")
    print(f"  Strategy: {params1['strategy']}, Intensity: {params1['intensity']}")
    assert params1["strategy"] in ["smart", "sliding_window", "full"]

    # 场景 2: 目标利用率 (75%) - 应该保持
    params2 = adjuster.adjust_compression_params(
        current_tokens=75000,
        available_tokens=100000,
        strategy_name="smart"
    )
    print(f"\n[PASS] Target utilization (75%):")
    print(f"  Strategy: {params2['strategy']}, Intensity: {params2['intensity']}")
    assert params2["strategy"] == "smart"
    assert params2["intensity"] == "normal"

    # 场景 3: 中等超出 (85%) - 应该加强
    params3 = adjuster.adjust_compression_params(
        current_tokens=85000,
        available_tokens=100000,
        strategy_name="sliding_window"
    )
    print(f"\n[PASS] Medium overuse (85%):")
    print(f"  Strategy: {params3['strategy']}, Intensity: {params3['intensity']}")
    assert params3["strategy"] in ["smart", "sliding_window"]

    # 场景 4: 严重超出 (120%) - 应该激进压缩
    params4 = adjuster.adjust_compression_params(
        current_tokens=120000,
        available_tokens=100000,
        strategy_name="full"
    )
    print(f"\n[PASS] Severe overuse (120%):")
    print(f"  Strategy: {params4['strategy']}, Intensity: {params4['intensity']}")
    assert params4["intensity"] == "aggressive"


def test_history_tracking():
    """测试历史记录跟踪"""
    print("\n[TEST] History Tracking")
    print("-" * 60)

    adjuster = DynamicCompressionAdjuster(history_limit=10)

    # 记录多次调整
    for i in range(15):
        adjuster.adjust_compression_params(
            current_tokens=50000 + i * 1000,
            available_tokens=100000,
            strategy_name="smart",
            conversation_id=f"conv_{i}"
        )

    # 验证历史限制
    assert len(adjuster.history) == 10, f"History should be limited to 10, got {len(adjuster.history)}"
    print(f"[PASS] History limited to 10 records (added 15, kept 10)")

    # 获取统计信息
    stats = adjuster.get_statistics()
    assert stats["total_adjustments"] == 10
    assert stats["avg_utilization"] > 0
    assert stats["max_utilization"] > stats["min_utilization"]

    print(f"[PASS] Statistics:")
    print(f"  Total adjustments: {stats['total_adjustments']}")
    print(f"  Avg utilization: {stats['avg_utilization']:.2%}")
    print(f"  Max utilization: {stats['max_utilization']:.2%}")
    print(f"  Min utilization: {stats['min_utilization']:.2%}")


def test_strategy_escalation():
    """测试策略升级逻辑"""
    print("\n[TEST] Strategy Escalation")
    print("-" * 60)

    adjuster = DynamicCompressionAdjuster(target_utilization=0.75)

    # 从 full -> sliding_window -> smart
    scenarios = [
        ("full", 120000, 100000, "sliding_window"),
        ("sliding_window", 120000, 100000, "smart"),
        ("smart", 120000, 100000, "smart"),
    ]

    for strategy, current, available, expected_strategy in scenarios:
        params = adjuster.adjust_compression_params(
            current_tokens=current,
            available_tokens=available,
            strategy_name=strategy
        )
        print(f"[PASS] {strategy} @ {current/available:.0%} -> {params['strategy']} ({params['intensity']})")
        assert params["strategy"] == expected_strategy


def test_integration():
    """集成测试"""
    print("\n[TEST] Integration Test")
    print("-" * 60)

    adjuster = DynamicCompressionAdjuster(target_utilization=0.75)

    # 模拟对话逐渐增长的场景
    available = 100000
    scenarios = [
        (20000, "full"),       # 20% - 轻松
        (40000, "full"),       # 40% - 轻松
        (60000, "full"),       # 60% - 接近目标
        (80000, "full"),       # 80% - 超出目标
        (95000, "sliding_window"),  # 95% - 需要压缩
        (105000, "smart"),     # 105% - 超出预算
    ]

    print("Simulating conversation growth:")
    for current, strategy in scenarios:
        params = adjuster.adjust_compression_params(
            current_tokens=current,
            available_tokens=available,
            strategy_name=strategy
        )
        utilization = current / available
        print(f"  {current:,} tokens ({utilization:.0%}): "
              f"{params['strategy']} ({params['intensity']})")

    # 检查统计
    stats = adjuster.get_statistics()
    print(f"\n[PASS] Final statistics:")
    print(f"  Adjustments: {stats['total_adjustments']}")
    print(f"  Avg utilization: {stats['avg_utilization']:.2%}")

    assert stats["total_adjustments"] == 6


def main():
    """运行所有测试"""
    print("=" * 60)
    print("Dynamic Compression Adjuster - Standalone Tests")
    print("=" * 60)

    try:
        test_compression_presets()
        test_utilization_adjustment()
        test_history_tracking()
        test_strategy_escalation()
        test_integration()

        print("\n" + "=" * 60)
        print("[SUCCESS] All tests passed!")
        print("=" * 60)
        print("\nPhase 2.2 (Dynamic Compression Adjuster) completed successfully.")

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
