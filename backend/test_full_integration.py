"""
完整集成测试 - 测试 ClaudeAdapter 通过中转服务调用
"""
import asyncio
import sys
import os

# 添加项目根目录到 Python 路径
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

from backend.core.model_adapters.factory import ModelAdapterFactory
from backend.core.conversation_format import UnifiedConversation, UnifiedMessage, MessageRole
from backend.config.settings import settings

async def test_claude_adapter():
    """测试 Claude Adapter"""
    print("=" * 80)
    print("Claude Adapter Integration Test")
    print("=" * 80)
    print()

    # 显示配置
    print("Configuration:")
    print(f"  Base URL: {settings.anthropic_base_url}")
    print(f"  Auth Token: {settings.anthropic_auth_token[:20]}...")
    print(f"  Default Model: {settings.anthropic_default_model}")
    print()

    try:
        # 创建适配器
        print("Step 1: Creating Claude adapter...")
        adapter = ModelAdapterFactory.create_from_settings(provider="claude")
        print(f"  Model: {adapter.get_model_name()}")
        print(f"  Base URL: {adapter.base_url}")
        print("  [OK]")
        print()

        # 创建测试对话
        print("Step 2: Creating test conversation...")
        conversation = UnifiedConversation(
            system_prompt="You are a helpful assistant.",
            messages=[
                UnifiedMessage(
                    role=MessageRole.USER,
                    content="Please reply with only 'Hello, World!' in English."
                )
            ]
        )
        print("  [OK]")
        print()

        # 发送请求
        print("Step 3: Sending request to Claude API...")
        response = await adapter.chat(conversation)
        print(f"  Status: Success")
        print(f"  Response: {response.content}")
        print("  [OK]")
        print()

        print("=" * 80)
        print("All Tests Passed!")
        print("=" * 80)
        print()
        print("Your configuration is working correctly.")
        print("You can now start the backend server with:")
        print("  cd C:\\Users\\shiguangping\\data-agent")
        print("  start-all.bat")
        print()

        return True

    except Exception as e:
        print(f"  [FAILED]")
        print()
        print("=" * 80)
        print("Test Failed!")
        print("=" * 80)
        print()
        print(f"Error: {e}")
        print()

        import traceback
        print("Traceback:")
        traceback.print_exc()
        print()

        print("Troubleshooting:")
        print("1. Check if relay service is running at http://10.0.3.248:3000")
        print("2. Verify the auth token is correct")
        print("3. Check if the model 'claude-sonnet-4-5' is available")
        print()

        return False

async def main():
    """主函数"""
    success = await test_claude_adapter()
    sys.exit(0 if success else 1)

if __name__ == "__main__":
    asyncio.run(main())
