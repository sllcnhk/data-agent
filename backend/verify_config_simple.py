#!/usr/bin/env python3
"""
简化版配置验证 - 直接检查文件内容
"""
import re


def check_file(filepath, pattern, expected_value):
    """检查文件中的配置值"""
    print(f"\n📄 检查文件: {filepath}")

    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    matches = re.findall(pattern, content)

    if not matches:
        print(f"   ❌ 未找到匹配的模式")
        return False

    for match in matches:
        print(f"   找到: {match}")

    if expected_value in matches:
        print(f"   ✅ 配置正确: {expected_value}")
        return True
    else:
        print(f"   ❌ 配置错误，期望: {expected_value}")
        return False


def main():
    print("=" * 70)
    print("🔍 Claude 模型配置验证")
    print("=" * 70)

    results = []

    # 1. 检查 settings.py
    print("\n" + "=" * 70)
    print("1️⃣ 检查 settings.py")
    print("=" * 70)

    # anthropic_default_model
    result1 = check_file(
        "config/settings.py",
        r'anthropic_default_model:\s*str\s*=\s*Field\(default="([^"]+)"',
        "claude-sonnet-4-5"
    )
    results.append(("settings.py - anthropic_default_model", result1))

    # anthropic_fallback_models
    result2 = check_file(
        "config/settings.py",
        r'anthropic_fallback_models:\s*str\s*=\s*Field\(default="([^"]+)"',
        "claude-sonnet-4-5-20250929,claude-haiku-4-5-20251001,minimax-m2"
    )
    results.append(("settings.py - anthropic_fallback_models", result2))

    # 2. 检查 factory.py
    print("\n" + "=" * 70)
    print("2️⃣ 检查 factory.py")
    print("=" * 70)

    result3 = check_file(
        "core/model_adapters/factory.py",
        r'"claude":\s*"([^"]+)"',
        "claude-sonnet-4-5"
    )
    results.append(("factory.py - DEFAULT_MODELS['claude']", result3))

    # 3. 检查 llm_config.py
    print("\n" + "=" * 70)
    print("3️⃣ 检查 llm_config.py")
    print("=" * 70)

    result4 = check_file(
        "models/llm_config.py",
        r'"default_model":\s*"claude-sonnet-4-5"',
        '"default_model": "claude-sonnet-4-5"'
    )
    results.append(("llm_config.py - default_model", result4))

    # 总结
    print("\n" + "=" * 70)
    print("📊 验证结果总结")
    print("=" * 70)

    all_passed = True
    for name, result in results:
        status = "✅ 通过" if result else "❌ 失败"
        print(f"{status} - {name}")
        if not result:
            all_passed = False

    print("\n" + "=" * 70)
    if all_passed:
        print("✅ 所有配置验证通过!")
        print("\n🎯 故障转移顺序:")
        print("   1️⃣ claude-sonnet-4-5 (主模型)")
        print("   2️⃣ claude-sonnet-4-5-20250929")
        print("   3️⃣ claude-haiku-4-5-20251001")
        print("   4️⃣ minimax-m2 ⭐ (新增)")
        print("\n💡 下一步: 重启服务使配置生效")
        return 0
    else:
        print("❌ 部分配置验证失败，请检查!")
        return 1


if __name__ == "__main__":
    exit(main())
