"""
测试中转服务支持的模型名称
"""
import httpx
import asyncio
import json
import sys

# 设置标准输出编码为 UTF-8
sys.stdout.reconfigure(encoding='utf-8')

# 中转服务配置
BASE_URL = "http://10.0.3.248:3000/api"
AUTH_TOKEN = "cr_b1abe3aaa6cabb76eeb3c46c1f3c9294e8b825e679c2333be9e013bb129571f4"
ENDPOINT = "/v1/messages"

# 测试不同的模型名称
TEST_MODELS = [
    "claude",                                    # 通用名称
    "claude-code",                               # Claude Code 特定
    "claude-3-5-sonnet",                         # 不带日期
    "claude-3-5-sonnet-20240620",               # 完整名称
    "claude-3-5-sonnet-20241022",               # 新版本日期
    "claude-3-opus-20240229",                    # Opus 模型
    "claude-3-sonnet-20240229",                  # Sonnet 3.0
    "claude-sonnet-4-5",                         # Sonnet 4.5
    "claude-sonnet-4-5-20250929",               # Sonnet 4.5 完整
    "anthropic.claude-3-5-sonnet-20240620-v1:0", # Bedrock 格式
]

async def test_model(model_name: str):
    """测试单个模型名称"""
    url = f"{BASE_URL}{ENDPOINT}"

    headers = {
        "Authorization": f"Bearer {AUTH_TOKEN}",
        "Content-Type": "application/json",
        "anthropic-version": "2023-06-01"
    }

    payload = {
        "model": model_name,
        "messages": [
            {
                "role": "user",
                "content": "请回复：OK"
            }
        ],
        "max_tokens": 50
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url, headers=headers, json=payload)

            result = {
                "model": model_name,
                "status": response.status_code,
                "success": False,
                "error": None,
                "response": None
            }

            try:
                response_json = response.json()

                if response.status_code == 200:
                    if "content" in response_json:
                        result["success"] = True
                        result["response"] = response_json.get("content", [{}])[0].get("text", "")
                        print(f"[OK] {model_name:50s} - Success!")
                    else:
                        result["error"] = "Unknown response format"
                        print(f"[??] {model_name:50s} - Unknown format")
                else:
                    error_msg = response_json.get("error", {}).get("message", "Unknown error")
                    result["error"] = error_msg
                    print(f"[ER] {model_name:50s} - {error_msg}")

            except Exception as e:
                result["error"] = str(e)
                print(f"[EX] {model_name:50s} - {str(e)}")

            return result

    except Exception as e:
        print(f"[FAIL] {model_name:50s} - {str(e)}")
        return {
            "model": model_name,
            "status": 0,
            "success": False,
            "error": str(e),
            "response": None
        }

async def main():
    """主函数"""
    print("="*80)
    print(f"Testing Claude Relay Service Models")
    print("="*80)
    print(f"Endpoint: {BASE_URL}{ENDPOINT}")
    print(f"Token: {AUTH_TOKEN[:20]}...")
    print("="*80)
    print()

    results = []

    # 测试所有模型
    for model in TEST_MODELS:
        result = await test_model(model)
        results.append(result)
        await asyncio.sleep(0.5)  # 避免请求过快

    # 打印总结
    print()
    print("="*80)
    print("Summary")
    print("="*80)

    successful = [r for r in results if r["success"]]

    if successful:
        print(f"\nFound {len(successful)} working model(s):\n")
        for r in successful:
            print(f"  Model: {r['model']}")
            print(f"  Response: {r['response'][:50]}...")
            print()

        # 推荐配置
        print("Recommended Configuration:")
        print("="*80)
        recommended = successful[0]["model"]
        print(f"ANTHROPIC_BASE_URL=http://10.0.3.248:3000/api")
        print(f"ANTHROPIC_AUTH_TOKEN={AUTH_TOKEN}")
        print(f"ANTHROPIC_DEFAULT_MODEL={recommended}")
        print()

    else:
        print("\nNo working models found.")
        print("\nCommon errors:")
        error_counts = {}
        for r in results:
            if r["error"]:
                err = r["error"]
                error_counts[err] = error_counts.get(err, 0) + 1

        for err, count in sorted(error_counts.items(), key=lambda x: -x[1]):
            print(f"  {count:2d}x: {err}")

        print("\nSuggestions:")
        print("1. Check if Claude Code relay service has models configured")
        print("2. Verify the auth token has access to Claude models")
        print("3. Check relay service logs for more details")

if __name__ == "__main__":
    asyncio.run(main())
