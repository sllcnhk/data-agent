#!/usr/bin/env python
"""
独立测试 - 直接HTTP调用中转服务
"""
import asyncio
import httpx

async def test_proxy_service():
    print("=" * 70)
    print("独立测试 - 直接HTTP调用中转服务")
    print("=" * 70)
    print()

    # 您的配置
    base_url = "http://10.0.3.248:3000"
    api_key = "cr_b1abe3aaa6cabb76eeb3c46c1f3c9294e8b825e679c2333be9e013bb129571f4"
    model = "claude"

    print(f"配置:")
    print(f"  - Base URL: {base_url}")
    print(f"  - Model: {model}")
    print(f"  - API Key: {api_key[:20]}...")
    print()

    # 构建 prompt
    prompt = """Human: 1+1等于多少？请用一句话回答。

Assistant:"""

    print(f"开始调用 API...")
    print(f"  URL: {base_url}/v1/completions")
    print(f"  Model: {model}")
    print()

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(
                f"{base_url}/v1/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": model,
                    "prompt": prompt,
                    "max_tokens": 1024,
                    "temperature": 0.7
                }
            )

            print(f"HTTP 状态码: {response.status_code}")
            print()

            if response.status_code != 200:
                print(f"错误响应:")
                print(f"  {response.text}")
                return False

            response_data = response.json()
            print(f"响应数据:")
            print(f"  Keys: {list(response_data.keys())}")
            print()

            if "choices" in response_data and response_data["choices"]:
                completion_text = response_data["choices"][0].get("text", "")
                print(f"✓ 成功！")
                print()
                print(f"回复内容:")
                print(f"  {completion_text}")
                return True
            else:
                print(f"✗ 响应格式错误:")
                print(f"  {response_data}")
                return False

    except httpx.RequestError as e:
        print(f"✗ 请求失败: {e}")
        return False
    except Exception as e:
        print(f"✗ 其他错误: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    success = asyncio.run(test_proxy_service())
    print()
    print("=" * 70)
    if success:
        print("✓ 测试成功！中转服务可以正常调用")
    else:
        print("✗ 测试失败")
    print("=" * 70)
