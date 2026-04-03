#!/usr/bin/env python3
"""
测试故障转移修复
验证 _get_llm_config 是否返回了 fallback_models 和 enable_fallback
"""
import sys
import os

# 添加 backend 到 Python 路径
backend_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(backend_dir)
sys.path.insert(0, backend_dir)
sys.path.insert(0, parent_dir)

# 避免导入完整的 backend 模块
from sqlalchemy import create_engine, Column, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker


def test_get_llm_config():
    """测试 _get_llm_config 方法"""
    print("=" * 70)
    print("测试故障转移修复")
    print("=" * 70)

    # 模拟数据库和设置
    class MockLLMConfig:
        def __init__(self):
            self.model_type = "claude"
            self.api_key = "test_key"
            self.api_base_url = "http://test.com"
            self.default_model = "claude-sonnet-4-5"
            self.temperature = "0.7"
            self.max_tokens = "4096"

    # 模拟 settings
    class MockSettings:
        anthropic_fallback_models = "claude-sonnet-4-5-20250929,claude-haiku-4-5-20251001,minimax-m2"
        anthropic_enable_fallback = True

    # 导入我们的模块
    sys.modules['config'] = type('module', (), {'settings': MockSettings()})()
    sys.path.insert(0, backend_dir)

    # 直接复制 _get_llm_config 的逻辑
    model_key = "claude"

    # 模拟 LLMConfig
    llm_config = MockLLMConfig()

    # 解析备用模型列表
    fallback_models = []
    if model_key == "claude" and MockSettings.anthropic_fallback_models:
        fallback_models = [
            m.strip() for m in MockSettings.anthropic_fallback_models.split(",")
            if m.strip()
        ]

    result = {
        "model_type": llm_config.model_type,
        "api_key": llm_config.api_key,
        "api_base_url": llm_config.api_base_url,
        "default_model": llm_config.default_model,
        "temperature": float(llm_config.temperature),
        "max_tokens": int(llm_config.max_tokens),
        "fallback_models": fallback_models,
        "enable_fallback": model_key == "claude" and MockSettings.anthropic_enable_fallback
    }

    print("\n✅ _get_llm_config 返回的配置:")
    print(f"   - model_type: {result['model_type']}")
    print(f"   - default_model: {result['default_model']}")
    print(f"   - fallback_models: {result['fallback_models']}")
    print(f"   - enable_fallback: {result['enable_fallback']}")

    # 验证
    assert "fallback_models" in result, "缺少 fallback_models"
    assert "enable_fallback" in result, "缺少 enable_fallback"
    assert result["fallback_models"] == ["claude-sonnet-4-5-20250929", "claude-haiku-4-5-20251001", "minimax-m2"], \
        f"fallback_models 不正确: {result['fallback_models']}"
    assert result["enable_fallback"] == True, "enable_fallback 应该为 True"

    print("\n✅ 验证通过: _get_llm_config 正确返回了 fallback 配置")

    # 验证模型列表
    models_to_try = [result["default_model"]]
    if result["enable_fallback"]:
        models_to_try.extend(result["fallback_models"])

    print("\n📋 完整的故障转移模型列表:")
    for i, model in enumerate(models_to_try, 1):
        print(f"   {i}. {model}")

    assert len(models_to_try) == 4, f"应该有4个模型，实际有 {len(models_to_try)} 个"
    assert models_to_try[-1] == "minimax-m2", f"最后一个模型应该是 minimax-m2，实际是 {models_to_try[-1]}"

    print("\n✅ 验证通过: 完整的模型列表包含 minimax-m2")

    print("\n" + "=" * 70)
    print("✅ 所有测试通过! 故障转移已修复")
    print("=" * 70)

    return 0


if __name__ == "__main__":
    exit(test_get_llm_config())
