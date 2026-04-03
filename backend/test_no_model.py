"""
测试是否可以不指定模型
"""
import anthropic
import os

# 使用您的配置
base_url = "http://10.0.3.248:3000/api"
api_key = "cr_b1abe3aaa6cabb76eeb3c46c1f3c9294e8b825e679c2333be9e013bb129571f4"

print("=" * 70)
print("测试是否可以不指定模型")
print("=" * 70)
print()

try:
    print("创建客户端...")
    client = anthropic.Anthropic(
        api_key=api_key,
        base_url=base_url
    )
    print("✓ 客户端创建成功")
    print()

    print("测试1: 不传递 model 参数")
    try:
        response = client.completions.create(
            prompt="Human: 1+1等于多少？\n\nAssistant:",
            max_tokens_to_sample=10
        )
        print("✓ 成功！")
        print(f"  回复: {response.completion}")
        print(f"  模型: {response.model if hasattr(response, 'model') else 'unknown'}")
    except Exception as e:
        print(f"✗ 失败: {e}")
    print()

    print("测试2: 传递空字符串作为 model")
    try:
        response = client.completions.create(
            prompt="Human: 1+1等于多少？\n\nAssistant:",
            model="",  # 空字符串
            max_tokens_to_sample=10
        )
        print("✓ 成功！")
        print(f"  回复: {response.completion}")
        print(f"  模型: {response.model if hasattr(response, 'model') else 'unknown'}")
    except Exception as e:
        print(f"✗ 失败: {e}")
    print()

    print("测试3: 传递 None 作为 model")
    try:
        response = client.completions.create(
            prompt="Human: 1+1等于多少？\n\nAssistant:",
            model=None,  # None
            max_tokens_to_sample=10
        )
        print("✓ 成功！")
        print(f"  回复: {response.completion}")
        print(f"  模型: {response.model if hasattr(response, 'model') else 'unknown'}")
    except Exception as e:
        print(f"✗ 失败: {e}")
    print()

    print("测试4: 检查默认模型")
    # 尝试获取客户端的默认模型
    try:
        if hasattr(client, 'default_model'):
            print(f"  客户端默认模型: {client.default_model}")
        if hasattr(client, '_default_model'):
            print(f"  客户端内部默认模型: {client._default_model}")

        # 尝试查看completions对象
        if hasattr(client, 'completions'):
            print(f"  completions 对象类型: {type(client.completions)}")

    except Exception as e:
        print(f"  检查失败: {e}")

except Exception as e:
    print(f"✗ 客户端创建失败: {e}")
    import traceback
    traceback.print_exc()

print()
print("=" * 70)
