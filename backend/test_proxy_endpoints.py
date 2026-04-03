"""
测试中转服务的各种可能端点路径
"""
import httpx
import asyncio
import json

# 中转服务配置
BASE_HOST = "http://10.0.3.248:3000"
AUTH_TOKEN = "cr_b1abe3aaa6cabb76eeb3c46c1f3c9294e8b825e679c2333be9e013bb129571f4"

# 测试不同的端点路径组合
TEST_PATHS = [
    "/v1/messages",                    # 标准 Anthropic 路径
    "/api/v1/messages",                # 带 /api 前缀
    "/antigravity/api/v1/messages",    # CRS Antigravity 路径
    "/openai/v1/chat/completions",     # OpenAI 兼容路径
]

# 测试消息
TEST_MESSAGE = {
    "model": "claude-3-5-sonnet-20240620",
    "messages": [
        {
            "role": "user",
            "content": "请用一句话回复：你好"
        }
    ],
    "max_tokens": 100
}

async def test_endpoint(base_url: str, path: str, auth_token: str):
    """测试单个端点"""
    url = f"{base_url}{path}"
    print(f"\n{'='*60}")
    print(f"测试端点: {url}")
    print(f"{'='*60}")

    headers = {
        "Authorization": f"Bearer {auth_token}",
        "Content-Type": "application/json",
        "anthropic-version": "2023-06-01"
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url, headers=headers, json=TEST_MESSAGE)

            print(f"状态码: {response.status_code}")
            print(f"响应头: {dict(response.headers)}")

            try:
                response_json = response.json()
                print(f"响应体 (JSON):")
                print(json.dumps(response_json, indent=2, ensure_ascii=False))

                # 检查是否成功
                if response.status_code == 200:
                    if "content" in response_json:
                        print(f"\n✅ 成功! 使用 Claude Messages API 格式")
                        return True, "messages"
                    elif "choices" in response_json:
                        print(f"\n✅ 成功! 使用 OpenAI 兼容格式")
                        return True, "openai"
            except:
                print(f"响应体 (文本): {response.text}")

            return False, None

    except httpx.RequestError as e:
        print(f"❌ 请求错误: {e}")
        return False, None
    except Exception as e:
        print(f"❌ 其他错误: {e}")
        return False, None

async def main():
    """主函数"""
    print(f"开始测试中转服务端点...")
    print(f"中转服务地址: {BASE_HOST}")
    print(f"认证令牌: {AUTH_TOKEN[:20]}...")

    results = []

    # 测试所有路径
    for path in TEST_PATHS:
        success, format_type = await test_endpoint(BASE_HOST, path, AUTH_TOKEN)
        results.append({
            "path": path,
            "url": f"{BASE_HOST}{path}",
            "success": success,
            "format": format_type
        })
        await asyncio.sleep(1)  # 避免请求过快

    # 打印总结
    print(f"\n\n{'='*60}")
    print(f"测试总结")
    print(f"{'='*60}")

    successful = [r for r in results if r["success"]]

    if successful:
        print(f"\n✅ 找到 {len(successful)} 个可用端点:\n")
        for r in successful:
            print(f"  路径: {r['path']}")
            print(f"  完整URL: {r['url']}")
            print(f"  格式: {r['format']}")
            print()

        # 给出配置建议
        best = successful[0]
        base_path = best["path"].replace("/v1/messages", "").replace("/v1/chat/completions", "")

        print(f"\n💡 配置建议:")
        print(f"=" * 60)
        if base_path:
            print(f"ANTHROPIC_BASE_URL=http://10.0.3.248:3000{base_path}")
        else:
            print(f"ANTHROPIC_BASE_URL=http://10.0.3.248:3000")
        print(f"ANTHROPIC_AUTH_TOKEN={AUTH_TOKEN}")
        print()

        if best["format"] == "openai":
            print(f"⚠️ 注意: 该端点使用 OpenAI 兼容格式，需要修改代码以支持此格式")

    else:
        print(f"\n❌ 没有找到可用的端点")
        print(f"\n可能的原因:")
        print(f"1. 认证令牌无效")
        print(f"2. 中转服务未启动或地址不正确")
        print(f"3. 网络连接问题")
        print(f"4. 中转服务使用了不同的路径格式")

        print(f"\n💡 建议:")
        print(f"1. 检查 VS Code Claude Code 插件的配置文件")
        print(f"2. 查看中转服务的日志以了解正确的端点")
        print(f"3. 联系中转服务管理员确认端点路径")

if __name__ == "__main__":
    asyncio.run(main())
