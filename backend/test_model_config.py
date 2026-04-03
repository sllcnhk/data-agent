#!/usr/bin/env python3
"""
测试模型配置更新
验证三个配置文件中的模型名称是否已统一更新为 claud-sonnet-4-5
"""
import sys
import os

# 添加 backend 到 Python 路径
backend_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(backend_dir)
sys.path.insert(0, backend_dir)
sys.path.insert(0, parent_dir)

from config.settings import settings
from core.model_adapters.factory import ModelAdapterFactory


def test_settings_config():
    """测试 settings.py 配置"""
    print("=" * 60)
    print("1. Settings 配置检查")
    print("=" * 60)

    print(f"✅ anthropic_default_model: {settings.anthropic_default_model}")
    print(f"✅ anthropic_fallback_models: {settings.anthropic_fallback_models}")
    print(f"✅ anthropic_enable_fallback: {settings.anthropic_enable_fallback}")

    # 验证主模型
    assert settings.anthropic_default_model == "claude-sonnet-4-5", \
        f"主模型配置错误: {settings.anthropic_default_model}"
    print("✅ 主模型配置正确: claude-sonnet-4-5")

    # 验证备用模型
    fallback_list = settings.anthropic_fallback_models.split(",")
    print(f"\n📋 故障转移模型列表 (共{len(fallback_list)}个):")
    for i, model in enumerate(fallback_list, 1):
        print(f"   {i}. {model.strip()}")

    expected_fallbacks = [
        "claude-sonnet-4-5-20250929",
        "claude-haiku-4-5-20251001",
        "minimax-m2"
    ]

    for expected in expected_fallbacks:
        assert expected in fallback_list, f"缺少备用模型: {expected}"
    print("\n✅ 备用模型配置完整，包含 minimax-m2")

    print("\n✅ Settings 配置验证通过!")
    return True


def test_factory_config():
    """测试 ModelAdapterFactory 配置"""
    print("\n" + "=" * 60)
    print("2. ModelAdapterFactory 配置检查")
    print("=" * 60)

    DEFAULT_MODELS = ModelAdapterFactory.DEFAULT_MODELS

    print(f"✅ DEFAULT_MODELS['claude']: {DEFAULT_MODELS['claude']}")

    assert DEFAULT_MODELS['claude'] == "claude-sonnet-4-5", \
        f"Factory 模型配置错误: {DEFAULT_MODELS['claude']}"

    print("✅ Factory 模型配置正确: claude-sonnet-4-5")

    print("\n📋 全部默认模型:")
    for provider, model in DEFAULT_MODELS.items():
        print(f"   - {provider}: {model}")

    print("\n✅ Factory 配置验证通过!")
    return True


def test_llm_config():
    """测试 LLMConfig 配置"""
    print("\n" + "=" * 60)
    print("3. LLMConfig 数据库配置检查")
    print("=" * 60)

    from models.llm_config import DEFAULT_LLM_CONFIGS

    # 查找 Claude 配置
    claude_config = None
    for config in DEFAULT_LLM_CONFIGS:
        if config['model_key'] == 'claude':
            claude_config = config
            break

    assert claude_config is not None, "未找到 Claude 配置"
    print(f"✅ default_model: {claude_config['default_model']}")

    assert claude_config['default_model'] == "claude-sonnet-4-5", \
        f"LLMConfig 模型配置错误: {claude_config['default_model']}"

    print("✅ LLMConfig 模型配置正确: claude-sonnet-4-5")

    print("\n📋 Claude 配置详情:")
    print(f"   - model_key: {claude_config['model_key']}")
    print(f"   - model_name: {claude_config['model_name']}")
    print(f"   - model_type: {claude_config['model_type']}")
    print(f"   - default_model: {claude_config['default_model']}")
    print(f"   - is_enabled: {claude_config['is_enabled']}")
    print(f"   - is_default: {claude_config['is_default']}")

    print("\n✅ LLMConfig 验证通过!")
    return True


def test_adapter_config():
    """测试适配器配置"""
    print("\n" + "=" * 60)
    print("4. 适配器配置检查")
    print("=" * 60)

    config = ModelAdapterFactory.get_adapter_config("claude")

    print("📋 Claude 适配器配置:")
    for key, value in config.items():
        if key == "fallback_models":
            print(f"   - {key}:")
            for model in value:
                print(f"      * {model}")
        else:
            print(f"   - {key}: {value}")

    # 验证配置
    assert "fallback_models" in config, "缺少 fallback_models 配置"
    assert "minimax-m2" in config["fallback_models"], "未找到 minimax-m2"

    print("\n✅ 适配器配置验证通过!")
    return True


def main():
    """主测试函数"""
    print("\n" + "🚀" * 30)
    print("Claude 模型配置验证测试")
    print("🚀" * 30 + "\n")

    try:
        # 测试各个配置
        test_settings_config()
        test_factory_config()
        test_llm_config()
        test_adapter_config()

        # 总结
        print("\n" + "=" * 60)
        print("📊 验证总结")
        print("=" * 60)
        print("✅ 所有配置验证通过!")
        print("\n🎯 模型故障转移顺序:")
        print("   1️⃣ claude-sonnet-4-5 (主模型)")
        print("   2️⃣ claude-sonnet-4-5-20250929")
        print("   3️⃣ claude-haiku-4-5-20251001")
        print("   4️⃣ minimax-m2 ⭐ (新增)")
        print("\n💡 建议: 重启服务使配置生效")

        return 0

    except AssertionError as e:
        print(f"\n❌ 验证失败: {e}")
        return 1
    except Exception as e:
        print(f"\n❌ 测试错误: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    exit(main())
