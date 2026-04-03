"""
测试自动故障转移功能
"""
import httpx
import asyncio
import json
import sys

# 设置标准输出编码为 UTF-8
sys.stdout.reconfigure(encoding='utf-8')

# 配置
BASE_URL = "http://10.0.3.248:3000/api"
AUTH_TOKEN = "cr_b1abe3aaa6cabb76eeb3c46c1f3c9294e8b825e679c2333be9e013bb129571f4"

# 模型列表
MODELS = [
    "claude-sonnet-4-5",
    "claude-sonnet-4-5-20250929",
    "claude-haiku-4-5-20251001",
]

async def test_single_model(model_name: str):
    """测试单个模型"""
    url = f"{BASE_URL}/v1/messages"
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
                "content": "Reply with only: OK"
            }
        ],
        "max_tokens": 50
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url, headers=headers, json=payload)

            if response.status_code == 200:
                response_json = response.json()
                content = response_json.get("content", [{}])[0].get("text", "")
                print(f"  [OK]  {model_name:45s} - {content}")
                return True
            else:
                error_msg = response.json().get("error", {}).get("message", "Unknown")
                print(f"  [ERR] {model_name:45s} - {error_msg}")
                return False

    except Exception as e:
        print(f"  [EXC] {model_name:45s} - {str(e)}")
        return False

async def test_failover_simulation():
    """模拟故障转移场景"""
    print("\n" + "=" * 80)
    print("Simulating Failover Scenario")
    print("=" * 80)
    print()
    print("Scenario: Primary model fails, automatically fallback to backup models")
    print()

    # 场景1: 使用一个不存在的模型作为主模型
    print("Test 1: Using invalid primary model with valid fallbacks")
    print("-" * 80)

    models_to_try = [
        "invalid-model-xxx",  # 不存在的模型
        "claude-sonnet-4-5",  # 备用1
        "claude-haiku-4-5-20251001",  # 备用2
    ]

    print(f"Models to try: {models_to_try}")
    print()

    errors = []
    for i, model in enumerate(models_to_try):
        print(f"[{i+1}/{len(models_to_try)}] Trying: {model}...")
        success = await test_single_model(model)

        if success:
            print()
            print(f"SUCCESS! Used model: {model}")
            if i > 0:
                print(f"Fallback triggered after {i} failed attempt(s)")
            break
        else:
            errors.append(model)
            print(f"Failed, trying next...")
            print()

    if len(errors) == len(models_to_try):
        print("All models failed!")

    print()

async def main():
    """主函数"""
    print("=" * 80)
    print("Claude Adapter Failover Test")
    print("=" * 80)
    print()
    print(f"Endpoint: {BASE_URL}/v1/messages")
    print(f"Token: {AUTH_TOKEN[:20]}...")
    print()

    # 测试所有模型的可用性
    print("Step 1: Testing individual models")
    print("-" * 80)
    available_models = []

    for model in MODELS:
        success = await test_single_model(model)
        if success:
            available_models.append(model)
        await asyncio.sleep(0.5)

    print()
    print(f"Available models: {len(available_models)}/{len(MODELS)}")
    for model in available_models:
        print(f"  - {model}")

    # 模拟故障转移
    await test_failover_simulation()

    # 显示建议配置
    print()
    print("=" * 80)
    print("Recommended Configuration")
    print("=" * 80)
    print()

    if available_models:
        primary = available_models[0]
        fallbacks = ",".join(available_models[1:]) if len(available_models) > 1 else ""

        print("Add to your .env file:")
        print(f"ANTHROPIC_DEFAULT_MODEL={primary}")
        if fallbacks:
            print(f"ANTHROPIC_FALLBACK_MODELS={fallbacks}")
        print(f"ANTHROPIC_ENABLE_FALLBACK=True")
        print()

        print("How it works:")
        print(f"1. Try primary model: {primary}")
        if fallbacks:
            for i, fb in enumerate(available_models[1:], 1):
                print(f"{i+1}. If failed, try backup: {fb}")
        print(f"{len(available_models)+1}. If all failed, raise error")
        print()

    print("Benefits:")
    print("- Automatic recovery when primary model is unavailable")
    print("- No manual intervention required")
    print("- Transparent to the application")
    print("- Logs show which model was actually used")

if __name__ == "__main__":
    asyncio.run(main())
