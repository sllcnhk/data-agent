"""
测试不同的 API 路径
"""
import anthropic
import os
import httpx

# 使用您的配置
base_urls = [
    "http://10.0.3.248:3000/api",
    "http://10.0.3.248:3000/api/",
    "http://10.0.3.248:3000",
    "http://10.0.3.248:3000/",
]

api_key = "cr_b1abe3aaa6cabb76eeb3c46c1f3c9294e8b825e679c2333be9e013bb129571f4"

print("=" * 70)
print("测试不同的 Base URL")
print("=" * 70)
print()

for base_url in base_urls:
    print(f"测试 Base URL: {base_url}")

    try:
        client = anthropic.Anthropic(
            api_key=api_key,
            base_url=base_url
        )
        print(f"  ✓ 客户端创建成功")
        print(f"    客户端 base_url: {client.base_url}")
        print(f"    客户端 _base_url: {client._base_url}")

        # 尝试一个简单的调用
        try:
            response = client.completions.create(
                prompt="Hi",
                model="claude",
                max_tokens_to_sample=5
            )
            print(f"  ✓ API 调用成功！")
            print(f"    回复: {response.completion}")
            print(f"    使用路径: {base_url}/v1/completions")
            break
        except Exception as e:
            if "complete not found" in str(e).lower():
                print(f"  ✗ API 路径错误 (complete not found)")
            else:
                print(f"  ✗ 其他错误: {e}")

    except Exception as e:
        print(f"  ✗ 客户端创建失败: {e}")

    print()

print()
print("=" * 70)
print("测试 HTTP 直接调用")
print("=" * 70)
print()

# 尝试直接 HTTP 调用，看看中转服务实际支持什么路径
for base_url in base_urls:
    for endpoint in ["complete", "completions", "v1/complete", "v1/completions"]:
        url = f"{base_url.rstrip('/')}/{endpoint}"
        print(f"测试路径: {url}")

        try:
            with httpx.Client() as client:
                # 尝试 OPTIONS 请求，看看支持什么
                response = client.options(
                    url,
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json"
                    },
                    timeout=5
                )
                print(f"  HTTP {response.status_code}")
                if response.status_code in [200, 204]:
                    print(f"  ✓ 路径可能可用")
        except Exception as e:
            if "timeout" in str(e).lower():
                print(f"  - 超时 (不可达)")
            else:
                print(f"  - 错误: {str(e)[:50]}...")

        print()

print("=" * 70)
print("基于 anthropic 库测试 API")
print("=" * 70)
print()

# 根据 anthropic 0.7.7 的源码，API 路径应该是 base_url + "/v1/complete"
# 但您的中转可能使用了不同的路径

# 让我们检查一下anthropic库实际使用的路径
print("检查 anthropic 0.7.7 默认路径...")
print("默认应该是: base_url + '/v1/complete'")
print("但您的中转可能使用:")
print("  - base_url + '/v1/completions'")
print("  - base_url + '/complete'")
print("  - base_url + '/completions'")
print()

print("建议解决方案:")
print("1. 检查中转服务的 API 文档")
print("2. 确认正确的 API 路径")
print("3. 可能需要修改 anthropic 库的 base_url")
print("=" * 70)
