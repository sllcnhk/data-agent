"""
快速测试不同的 base_url 配置
"""
import anthropic

api_key = "cr_b1abe3aaa6cabb76eeb3c46c1f3c9294e8b825e679c2333be9e013bb129571f4"

# 测试不同的 base_url
base_urls = [
    "http://10.0.3.248:3000/api",
    "http://10.0.3.248:3000",
    "http://10.0.3.248:3000/api/v1",
]

print("=" * 70)
print("快速测试不同的 base_url")
print("=" * 70)
print()

for base_url in base_urls:
    print(f"[测试] base_url = {base_url}")

    try:
        client = anthropic.Anthropic(
            api_key=api_key,
            base_url=base_url
        )
        print(f"      客户端创建成功")
        print(f"      _base_url = {client._base_url}")

        # 尝试一个简单调用
        try:
            response = client.completions.create(
                prompt="Hi",
                model="claude",
                max_tokens_to_sample=5
            )
            print(f"      ✓ API 调用成功！")
            print(f"      回复: '{response.completion}'")
            print(f"      ✓ 这个配置有效！")
            print()
            print("=" * 70)
            print("找到有效配置!")
            print(f"base_url: {base_url}")
            print("=" * 70)
            break

        except Exception as e:
            error = str(e)
            print(f"      ✗ API 调用失败")
            print(f"      错误: {error[:100]}...")

    except Exception as e:
        print(f"      ✗ 客户端创建失败: {e}")

    print()

print("=" * 70)
