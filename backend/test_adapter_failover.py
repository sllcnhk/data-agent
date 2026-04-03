"""
测试 ClaudeAdapter 的自动故障转移功能
"""
import asyncio
import sys
import os
import logging

# 添加项目根目录到 Python 路径
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

from backend.core.model_adapters.claude import ClaudeAdapter
from backend.core.conversation_format import UnifiedConversation, UnifiedMessage, MessageRole

async def test_normal_operation():
    """测试1: 正常操作 - 主模型可用"""
    print("\n" + "=" * 80)
    print("Test 1: Normal Operation (Primary model available)")
    print("=" * 80)

    adapter = ClaudeAdapter(
        api_key="cr_b1abe3aaa6cabb76eeb3c46c1f3c9294e8b825e679c2333be9e013bb129571f4",
        model="claude-sonnet-4-5",
        base_url="http://10.0.3.248:3000/api",
        fallback_models=["claude-sonnet-4-5-20250929", "claude-haiku-4-5-20251001"],
        enable_fallback=True
    )

    conversation = UnifiedConversation(
        system_prompt="You are a helpful assistant.",
        messages=[
            UnifiedMessage(
                role=MessageRole.USER,
                content="Reply with only: Test1 OK"
            )
        ]
    )

    try:
        response = await adapter.chat(conversation)
        print(f"\nResult:")
        print(f"  Status: SUCCESS")
        print(f"  Model used: {response.model}")
        print(f"  Response: {response.content}")
        print(f"  Used fallback: {response.metadata.get('used_fallback', False)}")
        print(f"  Attempted models: {response.metadata.get('attempted_models', [])}")
        return True
    except Exception as e:
        print(f"\nResult:")
        print(f"  Status: FAILED")
        print(f"  Error: {e}")
        return False

async def test_primary_fails():
    """测试2: 主模型失败 - 自动切换到备用"""
    print("\n" + "=" * 80)
    print("Test 2: Primary Model Fails (Auto failover to backup)")
    print("=" * 80)

    adapter = ClaudeAdapter(
        api_key="cr_b1abe3aaa6cabb76eeb3c46c1f3c9294e8b825e679c2333be9e013bb129571f4",
        model="invalid-model-primary",  # 使用无效的主模型
        base_url="http://10.0.3.248:3000/api",
        fallback_models=["claude-sonnet-4-5", "claude-haiku-4-5-20251001"],
        enable_fallback=True
    )

    conversation = UnifiedConversation(
        system_prompt="You are a helpful assistant.",
        messages=[
            UnifiedMessage(
                role=MessageRole.USER,
                content="Reply with only: Test2 OK"
            )
        ]
    )

    try:
        response = await adapter.chat(conversation)
        print(f"\nResult:")
        print(f"  Status: SUCCESS (Failover worked!)")
        print(f"  Model used: {response.model}")
        print(f"  Response: {response.content}")
        print(f"  Used fallback: {response.metadata.get('used_fallback', False)}")
        print(f"  Attempted models: {response.metadata.get('attempted_models', [])}")
        return True
    except Exception as e:
        print(f"\nResult:")
        print(f"  Status: FAILED")
        print(f"  Error: {e}")
        return False

async def test_all_fail():
    """测试3: 所有模型失败"""
    print("\n" + "=" * 80)
    print("Test 3: All Models Fail")
    print("=" * 80)

    adapter = ClaudeAdapter(
        api_key="cr_b1abe3aaa6cabb76eeb3c46c1f3c9294e8b825e679c2333be9e013bb129571f4",
        model="invalid-model-1",
        base_url="http://10.0.3.248:3000/api",
        fallback_models=["invalid-model-2", "invalid-model-3"],
        enable_fallback=True
    )

    conversation = UnifiedConversation(
        system_prompt="You are a helpful assistant.",
        messages=[
            UnifiedMessage(
                role=MessageRole.USER,
                content="Reply with only: Test3 OK"
            )
        ]
    )

    try:
        response = await adapter.chat(conversation)
        print(f"\nResult:")
        print(f"  Status: UNEXPECTED SUCCESS")
        print(f"  Model used: {response.model}")
        return False
    except Exception as e:
        print(f"\nResult:")
        print(f"  Status: EXPECTED FAILURE")
        print(f"  Error message preview:")
        error_lines = str(e).split('\n')
        for line in error_lines[:5]:  # 只显示前5行
            print(f"    {line}")
        return True

async def test_fallback_disabled():
    """测试4: 禁用故障转移"""
    print("\n" + "=" * 80)
    print("Test 4: Fallback Disabled")
    print("=" * 80)

    adapter = ClaudeAdapter(
        api_key="cr_b1abe3aaa6cabb76eeb3c46c1f3c9294e8b825e679c2333be9e013bb129571f4",
        model="invalid-model",
        base_url="http://10.0.3.248:3000/api",
        fallback_models=["claude-sonnet-4-5"],  # 有可用的备用，但被禁用
        enable_fallback=False  # 禁用故障转移
    )

    conversation = UnifiedConversation(
        system_prompt="You are a helpful assistant.",
        messages=[
            UnifiedMessage(
                role=MessageRole.USER,
                content="Reply with only: Test4 OK"
            )
        ]
    )

    try:
        response = await adapter.chat(conversation)
        print(f"\nResult:")
        print(f"  Status: UNEXPECTED SUCCESS")
        return False
    except Exception as e:
        print(f"\nResult:")
        print(f"  Status: EXPECTED FAILURE (Fallback disabled)")
        print(f"  Error: {str(e)[:100]}...")
        return True

async def main():
    """运行所有测试"""
    print("=" * 80)
    print("ClaudeAdapter Auto Failover Integration Test")
    print("=" * 80)

    tests = [
        ("Normal Operation", test_normal_operation),
        ("Primary Fails, Use Fallback", test_primary_fails),
        ("All Models Fail", test_all_fail),
        ("Fallback Disabled", test_fallback_disabled),
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

    # 打印总结
    print("\n" + "=" * 80)
    print("Test Summary")
    print("=" * 80)

    passed = sum(1 for _, success in results if success)
    total = len(results)

    for test_name, success in results:
        status = "PASS" if success else "FAIL"
        symbol = "✓" if success else "✗"
        print(f"  {symbol} {test_name:40s} [{status}]")

    print()
    print(f"Results: {passed}/{total} tests passed")

    if passed == total:
        print("\nAll tests passed! The failover mechanism is working correctly.")
    else:
        print(f"\n{total - passed} test(s) failed. Please check the logs above.")

if __name__ == "__main__":
    asyncio.run(main())
