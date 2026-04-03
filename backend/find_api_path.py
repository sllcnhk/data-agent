#!/usr/bin/env python
"""
测试中转服务的实际API路径
"""
import asyncio
import httpx

async def test_api_paths():
    print("=" * 70)
    print("测试中转服务的实际API路径")
    print("=" * 70)
    print()

    base_url = "http://10.0.3.248:3000"
    api_key = "cr_b1abe3aaa6cabb76eeb3c46c1f3c9294e8b825e679c2333be9e013bb129571f4"

    # 可能的路径
    possible_paths = [
        # Anthropic 风格
        "/v1/complete",  # anthropic 0.7.7 默认
        "/v1/completions",  # OpenAI 兼容
        "/api/v1/complete",
        "/api/v1/completions",

        # 其他可能
        "/complete",
        "/completions",
        "/api/complete",
        "/api/completions",

        # OpenAI 兼容
        "/openai/v1/completions",
        "/v1/chat/completions",  # Chat Completions

        # 代理可能自定义
        "/proxy/completions",
        "/claude/complete",
        "/claude/completions",
        "/anthropic/complete",
        "/anthropic/completions",
    ]

    # 测试每个路径
    for path in possible_paths:
        url = f"{base_url}{path}"
        print(f"测试路径: {path}")

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                # 发送 OPTIONS 请求检查是否支持
                response = await client.options(
                    url,
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json"
                    }
                )

                if response.status_code in [200, 204]:
                    print(f"  ✓ 支持此路径 (status: {response.status_code})")
                    print(f"  URL: {url}")

                    # 尝试POST调用
                    try:
                        post_response = await client.post(
                            url,
                            headers={
                                "Authorization": f"Bearer {api_key}",
                                "Content-Type": "application/json"
                            },
                            json={
                                "model": "claude",
                                "prompt": "Hi",
                                "max_tokens": 10
                            },
                            timeout=30.0
                        )

                        if post_response.status_code == 200:
                            print(f"  ✓✓ 调用成功！")
                            print(f"     响应: {post_response.json()}")
                            return path
                        else:
                            print(f"  - 调用失败 (status: {post_response.status_code})")
                            print(f"    响应: {post_response.text[:100]}")
                    except Exception as e:
                        print(f"  - 调用异常: {str(e)[:50]}")

                elif response.status_code == 404:
                    print(f"  ✗ 不支持此路径 (404)")
                else:
                    print(f"  - 未知状态 (status: {response.status_code})")

        except httpx.RequestError as e:
            if "timeout" in str(e).lower():
                print(f"  - 超时 (不可达)")
            else:
                print(f"  - 请求错误: {str(e)[:50]}")
        except Exception as e:
            print(f"  - 错误: {str(e)[:50]}")

        print()

    print("=" * 70)
    print("未找到有效的API路径")
    print("建议:")
    print("1. 检查中转服务的文档")
    print("2. 确认正确的API路径")
    print("3. 确认认证方式")
    print("=" * 70)

    return None

if __name__ == "__main__":
    result = asyncio.run(test_api_paths())
    if result:
        print(f"\n✓ 找到有效路径: {result}")
