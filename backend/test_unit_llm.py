"""
Unit test for LLM chat without backend server
"""
import sys
import os

# Set PYTHONPATH
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncio
from backend.core.conversation_format import UnifiedConversation, UnifiedMessage, MessageRole
from backend.core.model_adapters.claude import ClaudeAdapter

async def test_message_role_enum():
    """Test MessageRole enum conversion"""
    print("=" * 70)
    print("Testing MessageRole Enum Conversion")
    print("=" * 70)
    print()

    # Test 1: Create MessageRole from string
    print("[Test 1] Creating MessageRole from string...")
    try:
        role1 = MessageRole("user")
        print(f"  - Created: {role1}")
        print(f"  - Type: {type(role1)}")
        print(f"  - Value: {role1.value}")
        print(f"  - Has .value attr: {hasattr(role1, 'value')}")
        print("  ✓ PASS")
    except Exception as e:
        print(f"  ✗ FAIL: {e}")
        return False

    print()

    # Test 2: Create UnifiedMessage with MessageRole enum
    print("[Test 2] Creating UnifiedMessage with MessageRole enum...")
    try:
        msg = UnifiedMessage(
            role=MessageRole("user"),
            content="Test message"
        )
        print(f"  - Message created: role={msg.role}, type={type(msg.role)}")
        print(f"  - msg.role.value: {msg.role.value if hasattr(msg.role, 'value') else 'NO VALUE ATTR'}")
        print("  ✓ PASS")
    except Exception as e:
        print(f"  ✗ FAIL: {e}")
        import traceback
        traceback.print_exc()
        return False

    print()

    # Test 3: Create UnifiedMessage with string (should auto-convert)
    print("[Test 3] Creating UnifiedMessage with string (Pydantic should convert)...")
    try:
        msg2 = UnifiedMessage(
            role="assistant",  # Pass string directly
            content="Test response"
        )
        print(f"  - Message created: role={msg2.role}, type={type(msg2.role)}")
        print(f"  - Is enum: {isinstance(msg2.role, MessageRole)}")
        print(f"  - msg.role.value: {msg2.role.value if hasattr(msg2.role, 'value') else 'NO VALUE ATTR'}")
        print("  ✓ PASS")
    except Exception as e:
        print(f"  ✗ FAIL: {e}")
        import traceback
        traceback.print_exc()
        return False

    print()

    # Test 4: Create UnifiedConversation and convert to native format
    print("[Test 4] Creating UnifiedConversation and converting...")
    try:
        messages = [
            UnifiedMessage(role=MessageRole.USER, content="Hello"),
            UnifiedMessage(role=MessageRole.ASSISTANT, content="Hi there")
        ]

        conversation = UnifiedConversation(
            messages=messages,
            system_prompt="You are a helpful assistant"
        )

        print(f"  - Conversation created with {len(conversation.messages)} messages")

        # Test convert_to_native_format (without actually calling API)
        adapter = ClaudeAdapter(api_key="dummy-key")
        native_format = adapter.convert_to_native_format(conversation)

        print(f"  - Native format created:")
        print(f"    - model: {native_format['model']}")
        print(f"    - messages count: {len(native_format['messages'])}")
        print(f"    - system: {native_format.get('system', 'None')[:50]}")
        print(f"    - first message role: {native_format['messages'][0]['role']}")
        print("  ✓ PASS")
    except Exception as e:
        print(f"  ✗ FAIL: {e}")
        import traceback
        traceback.print_exc()
        return False

    print()
    print("=" * 70)
    print("All unit tests PASSED!")
    print("=" * 70)
    return True

if __name__ == "__main__":
    success = asyncio.run(test_message_role_enum())
    sys.exit(0 if success else 1)
