"""
测试新的 HTTP 适配器
"""
import asyncio
import sys
import os

# Set PYTHONPATH
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.core.model_adapters.claude import ClaudeAdapter
from backend.core.conversation_format import UnifiedConversation, UnifiedMessage, MessageRole

async def test_http_adapter():
    print("=" * 70)
    print("测试新的 HTTP 适配器")
    print("=" * 70)
    print()

    # 使用您的配置
    api_key = "cr_b1abe3aaa6cabb76eeb3c46c1f3c9294e8b825e679c2333be9e013bb129571f4"
    base_url = "http://10.0.3.248:3000"
    model = "claude"

    print(f"配置:")
    print(f"  - Base URL: {base_url}")
    print(f"  - Model: {model}")
    print(f"  - API Key: {api_key[:20]}...")
    print()

    try:
        # 创建适配器
        adapter = ClaudeAdapter(
            api_key=api_key,
            base_url=base_url,
            model=model
        )
        print(f"✓ 适配器创建成功")
        print()

        # 创建测试对话
        messages = [
            UnifiedMessage(
                role=MessageRole.USER,
                content="1+1等于多少？请用一句话回答。"
            )
        ]

        conversation = UnifiedConversation(
            messages=messages,
            system_prompt="你是一个测试助手，请简洁回答。"
        )

        print(f"开始调用 API...")
        print()

        # 调用 chat
        response = await adapter.chat(conversation)

        print(f"✓ API 调用成功！")
        print()
        print(f"回复内容:")
        print(f"  {response.content}")
        print()
        print(f"完整消息对象:")
        print(f"  - Role: {response.role}")
        print(f"  - Model: {response.model}")
        print(f"  - Content length: {len(response.content)}")

        return True

    except Exception as e:
        print(f"✗ 测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    success = asyncio.run(test_http_adapter())
    sys.exit(0 if success else 1)
