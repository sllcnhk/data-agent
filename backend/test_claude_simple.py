"""
简化版 Claude Adapter 测试 - 直接HTTP调用
"""
import httpx
import asyncio
import json

# 配置
BASE_URL = "http://10.0.3.248:3000/api"
AUTH_TOKEN = "cr_b1abe3aaa6cabb76eeb3c46c1f3c9294e8b825e679c2333be9e013bb129571f4"
MODEL = "claude-sonnet-4-5"

async def test_claude_messages_api():
    """测试 Claude Messages API"""
    print("=" * 80)
    print("Claude Messages API Test")
    print("=" * 80)
    print()

    print("Configuration:")
    print(f"  Endpoint: {BASE_URL}/v1/messages")
    print(f"  Model: {MODEL}")
    print(f"  Auth Token: {AUTH_TOKEN[:20]}...")
    print()

    # 构建请求
    url = f"{BASE_URL}/v1/messages"
    headers = {
        "Authorization": f"Bearer {AUTH_TOKEN}",
        "Content-Type": "application/json",
        "anthropic-version": "2023-06-01"
    }

    payload = {
        "model": MODEL,
        "messages": [
            {
                "role": "user",
                "content": "Please reply with only 'Hello, World!' in English."
            }
        ],
        "max_tokens": 100
    }

    try:
        print("Sending request...")
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url, headers=headers, json=payload)

            print(f"  Status Code: {response.status_code}")
            print()

            if response.status_code == 200:
                response_data = response.json()

                print("Response:")
                print(json.dumps(response_data, indent=2, ensure_ascii=False))
                print()

                # 解析响应
                if "content" in response_data:
                    content_blocks = response_data["content"]
                    if content_blocks and len(content_blocks) > 0:
                        text = content_blocks[0].get("text", "")
                        print("=" * 80)
                        print("SUCCESS!")
                        print("=" * 80)
                        print(f"Claude says: {text}")
                        print()
                        print("Your configuration is working correctly!")
                        print("The backend is ready to use.")
                        print()
                        return True

            else:
                print("FAILED!")
                print(f"Status: {response.status_code}")
                print(f"Response: {response.text}")
                print()
                return False

    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()
        return False

async def main():
    success = await test_claude_messages_api()

    if not success:
        print()
        print("Troubleshooting:")
        print("1. Check if relay service is running:")
        print("   http://10.0.3.248:3000")
        print("2. Verify the auth token")
        print("3. Check the model name is supported")

asyncio.run(main())
