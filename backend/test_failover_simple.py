"""
简化版自动故障转移测试 - 模拟 ClaudeAdapter 的逻辑
"""
import httpx
import asyncio
import json
import sys

sys.stdout.reconfigure(encoding='utf-8')

# 配置
BASE_URL = "http://10.0.3.248:3000/api"
AUTH_TOKEN = "cr_b1abe3aaa6cabb76eeb3c46c1f3c9294e8b825e679c2333be9e013bb129571f4"

async def try_model_request(model_name: str, prompt: str):
    """尝试使用指定模型发送请求"""
    url = f"{BASE_URL}/v1/messages"
    headers = {
        "Authorization": f"Bearer {AUTH_TOKEN}",
        "Content-Type": "application/json",
        "anthropic-version": "2023-06-01"
    }

    request_body = {
        "model": model_name,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 50
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            print(f"  [TRY] Attempting: {model_name}")
            response = await client.post(url, headers=headers, json=request_body)

            if response.status_code == 200:
                response_data = response.json()
                text = response_data["content"][0]["text"]
                print(f"  [OK]  Success: {text}")
                return True, response_data
            else:
                error_text = response.text[:100]
                print(f"  [ERR] Failed: {response.status_code} - {error_text}")
                return False, error_text

    except Exception as e:
        print(f"  [EXC] Error: {str(e)[:50]}")
        return False, str(e)

async def chat_with_failover(primary_model: str, fallback_models: list, prompt: str):
    """模拟 ClaudeAdapter.chat() 的故障转移逻辑"""
    models_to_try = [primary_model] + fallback_models

    print(f"Models to try: {models_to_try}")
    print()

    errors = []
    for i, model_name in enumerate(models_to_try):
        print(f"[{i+1}/{len(models_to_try)}] Trying model: {model_name}")

        success, result = await try_model_request(model_name, prompt)

        if success:
            print()
            print(f"SUCCESS!")
            print(f"  Used model: {model_name}")
            print(f"  Fallback triggered: {i > 0}")
            print(f"  Attempted models: {models_to_try[:i+1]}")
            return {
                "model": model_name,
                "response": result,
                "used_fallback": i > 0,
                "attempted_models": models_to_try[:i+1]
            }
        else:
            errors.append(f"Model {model_name}: {result}")
            print()

    # 所有模型都失败了
    print("FAILED!")
    print(f"All {len(models_to_try)} models failed")
    print("Errors:")
    for err in errors:
        print(f"  - {err[:80]}")
    return None

async def test_scenario_1():
    """场景1: 正常操作 - 主模型可用"""
    print("\n" + "=" * 80)
    print("Test 1: Normal Operation")
    print("=" * 80)
    print()

    result = await chat_with_failover(
        primary_model="claude-sonnet-4-5",
        fallback_models=["claude-sonnet-4-5-20250929", "claude-haiku-4-5-20251001"],
        prompt="Reply: Test1 OK"
    )

    return result is not None and not result["used_fallback"]

async def test_scenario_2():
    """场景2: 主模型失败，自动切换"""
    print("\n" + "=" * 80)
    print("Test 2: Primary Fails, Auto Failover")
    print("=" * 80)
    print()

    result = await chat_with_failover(
        primary_model="invalid-primary-xxx",
        fallback_models=["claude-sonnet-4-5", "claude-haiku-4-5-20251001"],
        prompt="Reply: Test2 OK"
    )

    return result is not None and result["used_fallback"]

async def test_scenario_3():
    """场景3: 所有模型都失败"""
    print("\n" + "=" * 80)
    print("Test 3: All Models Fail")
    print("=" * 80)
    print()

    result = await chat_with_failover(
        primary_model="invalid-1",
        fallback_models=["invalid-2", "invalid-3"],
        prompt="Reply: Test3 OK"
    )

    return result is None  # 期望失败

async def main():
    print("=" * 80)
    print("Auto Failover Mechanism Test")
    print("=" * 80)
    print()
    print("Configuration:")
    print(f"  Endpoint: {BASE_URL}/v1/messages")
    print(f"  Token: {AUTH_TOKEN[:20]}...")
    print()

    tests = [
        ("Normal Operation", test_scenario_1),
        ("Auto Failover", test_scenario_2),
        ("All Fail", test_scenario_3),
    ]

    results = []

    for test_name, test_func in tests:
        try:
            success = await test_func()
            results.append((test_name, success))
            await asyncio.sleep(1)
        except Exception as e:
            print(f"\nTest crashed: {e}")
            results.append((test_name, False))

    # 总结
    print("\n" + "=" * 80)
    print("Test Summary")
    print("=" * 80)

    passed = sum(1 for _, s in results if s)
    total = len(results)

    for test_name, success in results:
        status = "PASS" if success else "FAIL"
        symbol = "✓" if success else "✗"
        print(f"  {symbol} {test_name:40s} [{status}]")

    print()
    print(f"Results: {passed}/{total} tests passed")

    if passed == total:
        print("\n✅ All tests passed!")
        print()
        print("Your configuration in .env:")
        print("  ANTHROPIC_DEFAULT_MODEL=claude-sonnet-4-5")
        print("  ANTHROPIC_FALLBACK_MODELS=claude-sonnet-4-5-20250929,claude-haiku-4-5-20251001")
        print("  ANTHROPIC_ENABLE_FALLBACK=True")
        print()
        print("This will automatically switch to backup models if primary fails!")

if __name__ == "__main__":
    asyncio.run(main())
