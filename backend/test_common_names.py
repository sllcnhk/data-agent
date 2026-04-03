"""
测试中转服务可能支持的常见模型名称
"""
import anthropic
import os

# 使用您的配置
base_url = "http://10.0.3.248:3000/api"
api_key = "cr_b1abe3aaa6cabb76eeb3c46c1f3c9294e8b825e679c2333be9e013bb129571f4"

print("=" * 70)
print("测试常见模型名称")
print("=" * 70)
print()

# 常见的可能模型名称
test_models = [
    # 最简单的名称
    "claude",
    "claude-2",
    "claude-3",
    "claude-sonnet",
    "claude-opus",

    # 带版本号的
    "claude-20240229",
    "claude-20240620",

    # Anthropic 风格的
    "claude-2.0",
    "claude-2.1",
    "claude-3.5",

    # Instant 系列
    "claude-instant",
    "claude-instant-1",
    "claude-instant-v1",

    # 可能的中转自定义名称
    "gpt-3.5-turbo",  # OpenAI兼容
    "gpt-4",
    "default",
    "base",
    "general",
]

print("测试模型列表:")
for model in test_models:
    print(f"  - {model}")
print()

success_models = []

for model_name in test_models:
    try:
        print(f"测试: {model_name}")
        client = anthropic.Anthropic(
            api_key=api_key,
            base_url=base_url
        )

        # 使用极简单的prompt
        response = client.completions.create(
            prompt="Hi",  # 最简单的prompt
            model=model_name,
            max_tokens_to_sample=5
        )

        print(f"  ✓ 成功！")
        print(f"    回复: '{response.completion}'")
        print(f"    模型: '{response.model if hasattr(response, 'model') else 'unknown'}'")
        success_models.append((model_name, response.completion))

    except Exception as e:
        error_msg = str(e)
        print(f"  ✗ 失败")

    print()

print("=" * 70)
print("成功的模型:")
for model, reply in success_models:
    print(f"  ✓ {model} -> '{reply}'")
print("=" * 70)
