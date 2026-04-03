"""
实际测试中转服务支持的模型
"""
import anthropic
import os

# 使用您的配置
base_url = "http://10.0.3.248:3000/api"
api_key = "cr_b1abe3aaa6cabb76eeb3c46c1f3c9294e8b825e679c2333be9e013bb129571f4"

print("=" * 70)
print("实际测试中转服务支持的模型")
print("=" * 70)
print()

# 常见的模型名称列表（基于Claude历史版本）
test_models = [
    # Anthropic官方历史模型
    "claude-v1",
    "claude-v1-100k",
    "claude-v1.3",
    "claude-v2",
    "claude-v2.0",
    "claude-v2.1",
    "claude-instant-v1",
    "claude-instant-v1-100k",
    "claude-instant-1",

    # 新版模型（如果中转支持）
    "claude-3-opus-20240229",
    "claude-3-sonnet-20240229",
    "claude-3-haiku-20240307",
    "claude-3-5-sonnet-20240620",
    "claude-3-5-haiku-20240307",

    # 中转可能自定义的模型名
    "claude-2",
    "claude-instant",
    "claude-2.0",
    "claude-3",
    "claude-3-sonnet",
]

print("测试模型列表:")
for model in test_models:
    print(f"  - {model}")
print()

print("开始测试...")
print()

success_models = []
failed_models = []

for model_name in test_models:
    try:
        print(f"测试模型: {model_name}")
        client = anthropic.Anthropic(
            api_key=api_key,
            base_url=base_url
        )

        # 尝试调用 API
        response = client.completions.create(
            prompt="Human: 1+1等于多少？\n\nAssistant:",
            model=model_name,
            max_tokens_to_sample=10
        )

        print(f"  ✓ 成功！")
        print(f"    回复: {response.completion[:50]}...")
        success_models.append(model_name)

    except Exception as e:
        error_msg = str(e)
        if "404" in error_msg or "not found" in error_msg.lower():
            print(f"  ✗ 不支持此模型")
            failed_models.append(model_name)
        elif "model" in error_msg.lower() and ("not" in error_msg.lower() or "invalid" in error_msg.lower()):
            print(f"  ✗ 模型无效")
            failed_models.append(model_name)
        else:
            print(f"  ✗ 其他错误: {error_msg[:100]}")
            failed_models.append(model_name)

    print()

print("=" * 70)
print("测试结果汇总")
print("=" * 70)
print()

if success_models:
    print(f"✓ 成功的模型 ({len(success_models)}个):")
    for model in success_models:
        print(f"  - {model}")
    print()
else:
    print("✗ 没有找到任何支持的模型")
    print()

if failed_models:
    print(f"✗ 失败的模型 ({len(failed_models)}个):")
    for model in failed_models:
        print(f"  - {model}")
    print()

print("=" * 70)
