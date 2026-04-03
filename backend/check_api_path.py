"""
直接检查 API 调用实际使用的路径
"""
import anthropic
import sys

base_url = "http://10.0.3.248:3000/api"
api_key = "cr_b1abe3aaa6cabb76eeb3c46c1f3c9294e8b825e679c2333be9e013bb129571f4"

print("=" * 70)
print("检查 Anthropic API 调用实际路径")
print("=" * 70)
print()

# 创建一个客户端并尝试捕获实际请求
print("创建客户端...")
client = anthropic.Anthropic(
    api_key=api_key,
    base_url=base_url
)

print(f"客户端 base_url: {client.base_url}")
print(f"客户端 _base_url: {client._base_url}")
print()

# 检查 anthropic 库使用的路径
print("Anthropic 0.7.7 默认使用的路径结构:")
print("  1. Base URL: " + str(client.base_url))
print("  2. API 端点: completions.create()")
print("  3. 完整路径: {base_url}/v1/complete")
print()

# 让我们手动构造一个请求来查看实际路径
print("手动测试路径...")

# 尝试不同的路径
paths_to_try = [
    "/v1/complete",
    "/v1/completions",
    "/complete",
    "/completions",
    "/api/v1/complete",
    "/api/v1/completions",
]

print("测试这些路径:")
for path in paths_to_try:
    full_url = f"{base_url.rstrip('/')}{path}"
    print(f"  {full_url}")

print()

print("根据错误信息 'Route /api/v1/complete not found'")
print("中转服务期望的路径可能是:")
print("  - http://10.0.3.248:3000/api/v1/completions (复数)")
print("  - 而不是 /complete (单数)")
print()

print("建议的修复方案:")
print("1. 在数据库中设置 api_base_url = 'http://10.0.3.248:3000/api/v1'")
print("2. 或者设置 base_url = 'http://10.0.3.248:3000'")
print("3. 让 anthropic 库自己添加 '/v1/complete' 后缀")
print()

# 测试方案 2: 使用不带 /api 的 base_url
print("测试方案: base_url = 'http://10.0.3.248:3000'")
try:
    client2 = anthropic.Anthropic(
        api_key=api_key,
        base_url="http://10.0.3.248:3000"
    )
    print(f"  客户端 _base_url: {client2._base_url}")

    # 尝试调用
    try:
        response = client2.completions.create(
            prompt="Hi",
            model="claude",
            max_tokens_to_sample=5
        )
        print(f"  ✓ 成功！")
        print(f"    回复: {response.completion}")
    except Exception as e:
        print(f"  ✗ 失败: {e}")

except Exception as e:
    print(f"  ✗ 客户端创建失败: {e}")

print()
print("=" * 70)
