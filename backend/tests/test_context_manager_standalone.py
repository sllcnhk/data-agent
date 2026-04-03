"""
HybridContextManager 独立测试

直接测试 context_manager 模块，避免依赖问题
"""
import sys
import os

# 添加项目路径
backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
project_root = os.path.dirname(backend_dir)
sys.path.insert(0, project_root)
sys.path.insert(0, backend_dir)

print("=" * 80)
print("HybridContextManager 单元测试")
print("=" * 80)

# 测试计数
total_tests = 0
passed_tests = 0
failed_tests = 0

def run_test(test_name, test_func):
    """运行单个测试"""
    global total_tests, passed_tests, failed_tests
    total_tests += 1
    try:
        test_func()
        print(f"[PASS] {test_name}")
        passed_tests += 1
        return True
    except AssertionError as e:
        print(f"[FAIL] {test_name}: {e}")
        failed_tests += 1
        return False
    except Exception as e:
        print(f"[ERROR] {test_name}: {e}")
        failed_tests += 1
        return False

# 导入需要的模块
try:
    from backend.core.conversation_format import UnifiedConversation, MessageRole
    from backend.core.context_manager import (
        HybridContextManager,
        FullContextStrategy,
        SlidingWindowStrategy,
        SmartCompressionStrategy,
        compress_conversation
    )
    from backend.config.settings import settings
    print("[OK] Modules imported successfully\n")
except Exception as e:
    print(f"[ERROR] Failed to import modules: {e}")
    sys.exit(1)

# Test 1: Full Context Strategy
def test_full_context_strategy():
    strategy = FullContextStrategy()

    conversation = UnifiedConversation(
        conversation_id="test",
        title="Test",
        model="claude"
    )

    for i in range(20):
        conversation.add_user_message(f"Message {i}")

    compressed = strategy.compress(conversation, max_messages=10)
    assert len(compressed.messages) == 20, "Full strategy should keep all messages"

run_test("Full Context Strategy - No compression", test_full_context_strategy)

# Test 2: Sliding Window Strategy
def test_sliding_window():
    strategy = SlidingWindowStrategy()

    conversation = UnifiedConversation(
        conversation_id="test",
        title="Test",
        model="claude"
    )

    for i in range(30):
        conversation.add_user_message(f"Message {i}")

    compressed = strategy.compress(conversation, max_messages=10)
    assert len(compressed.messages) == 10, "Should keep only 10 recent messages"
    assert "Message 29" in compressed.messages[-1].content, "Should keep most recent"

run_test("Sliding Window Strategy - Keep recent", test_sliding_window)

# Test 3: Sliding Window - Under limit
def test_sliding_window_under_limit():
    strategy = SlidingWindowStrategy()

    conversation = UnifiedConversation(
        conversation_id="test",
        title="Test",
        model="claude"
    )

    for i in range(5):
        conversation.add_user_message(f"Message {i}")

    compressed = strategy.compress(conversation, max_messages=10)
    assert len(compressed.messages) == 5, "Should keep all if under limit"

run_test("Sliding Window Strategy - Under limit", test_sliding_window_under_limit)

# Test 4: Smart Compression Strategy
def test_smart_compression():
    strategy = SmartCompressionStrategy(keep_first=2, keep_recent=8)

    conversation = UnifiedConversation(
        conversation_id="test",
        title="Test",
        model="claude"
    )

    for i in range(30):
        conversation.add_user_message(f"User message {i}")
        conversation.add_assistant_message(f"Assistant reply {i}")

    compressed = strategy.compress(conversation, max_messages=15)

    # Should have: 2 first + 1 summary + 8 recent = 11
    assert len(compressed.messages) == 11, f"Expected 11 messages, got {len(compressed.messages)}"

    # Check for summary
    has_summary = any("[历史对话摘要]" in msg.content for msg in compressed.messages)
    assert has_summary, "Should contain summary message"

run_test("Smart Compression Strategy - Structure", test_smart_compression)

# Test 5: Smart Compression - Under limit
def test_smart_compression_under_limit():
    strategy = SmartCompressionStrategy(keep_first=2, keep_recent=8)

    conversation = UnifiedConversation(
        conversation_id="test",
        title="Test",
        model="claude"
    )

    for i in range(5):
        conversation.add_user_message(f"Message {i}")

    compressed = strategy.compress(conversation, max_messages=15)
    assert len(compressed.messages) == 5, "Should keep all if under limit"

run_test("Smart Compression Strategy - Under limit", test_smart_compression_under_limit)

# Test 6: HybridContextManager - Create with full
def test_manager_full():
    manager = HybridContextManager(strategy="full", max_context_length=10)
    assert manager.strategy_name == "full"
    assert manager.max_context_length == 10

run_test("HybridContextManager - Create with full", test_manager_full)

# Test 7: HybridContextManager - Create with sliding_window
def test_manager_sliding_window():
    manager = HybridContextManager(strategy="sliding_window", max_context_length=20)
    assert manager.strategy_name == "sliding_window"
    assert manager.max_context_length == 20

run_test("HybridContextManager - Create with sliding_window", test_manager_sliding_window)

# Test 8: HybridContextManager - Create with compressed
def test_manager_compressed():
    manager = HybridContextManager(
        strategy="compressed",
        max_context_length=15,
        keep_first=2,
        keep_recent=8
    )
    assert manager.strategy_name == "compressed"

run_test("HybridContextManager - Create with compressed", test_manager_compressed)

# Test 9: HybridContextManager - Invalid strategy
def test_manager_invalid_strategy():
    try:
        manager = HybridContextManager(strategy="invalid")
        assert False, "Should raise ValueError for invalid strategy"
    except ValueError:
        pass  # Expected

run_test("HybridContextManager - Invalid strategy raises error", test_manager_invalid_strategy)

# Test 10: HybridContextManager - Compress conversation
def test_manager_compress():
    manager = HybridContextManager(strategy="sliding_window", max_context_length=10)

    conversation = UnifiedConversation(
        conversation_id="test",
        title="Test",
        model="claude"
    )

    for i in range(20):
        conversation.add_user_message(f"Message {i}")

    compressed = manager.compress_conversation(conversation)
    assert len(compressed.messages) == 10

run_test("HybridContextManager - Compress conversation", test_manager_compress)

# Test 11: Create snapshot - full
def test_snapshot_full():
    manager = HybridContextManager(strategy="full", max_context_length=10)

    conversation = UnifiedConversation(
        conversation_id="test",
        title="Test Conversation",
        model="claude"
    )

    for i in range(5):
        conversation.add_user_message(f"Message {i}")

    snapshot = manager.create_snapshot(conversation, snapshot_type="full")

    assert snapshot["snapshot_type"] == "full"
    assert snapshot["message_count"] == 5
    assert "created_at" in snapshot

run_test("Create snapshot - full", test_snapshot_full)

# Test 12: Create snapshot - compressed
def test_snapshot_compressed():
    manager = HybridContextManager(strategy="sliding_window", max_context_length=5)

    conversation = UnifiedConversation(
        conversation_id="test",
        title="Test",
        model="claude"
    )

    for i in range(10):
        conversation.add_user_message(f"Message {i}")

    snapshot = manager.create_snapshot(conversation, snapshot_type="compressed")

    assert snapshot["snapshot_type"] == "compressed"
    assert snapshot["content"]["message_count"] == 5  # compressed to 5

run_test("Create snapshot - compressed", test_snapshot_compressed)

# Test 13: Create snapshot - summary
def test_snapshot_summary():
    manager = HybridContextManager(strategy="full", max_context_length=10)

    conversation = UnifiedConversation(
        conversation_id="test",
        title="Test Conversation",
        model="claude"
    )

    conversation.add_user_message("Hello")
    conversation.add_assistant_message("Hi")

    snapshot = manager.create_snapshot(conversation, snapshot_type="summary")

    assert snapshot["snapshot_type"] == "summary"
    assert "summary" in snapshot["content"]

run_test("Create snapshot - summary", test_snapshot_summary)

# Test 14: Restore from snapshot
def test_restore_snapshot():
    manager = HybridContextManager(strategy="full", max_context_length=10)

    conversation = UnifiedConversation(
        conversation_id="test",
        title="Test",
        model="claude"
    )

    for i in range(5):
        conversation.add_user_message(f"Message {i}")

    snapshot = manager.create_snapshot(conversation, snapshot_type="full")
    restored = manager.restore_from_snapshot(snapshot)

    assert len(restored.messages) == 5

run_test("Restore from snapshot", test_restore_snapshot)

# Test 15: Create from settings
def test_create_from_settings():
    manager = HybridContextManager.create_from_settings()

    assert manager.strategy_name == settings.context_compression_strategy
    assert manager.max_context_length == settings.max_context_messages

run_test("Create from settings", test_create_from_settings)

# Test 16: Compress conversation function
def test_compress_conversation_function():
    conversation = UnifiedConversation(
        conversation_id="test",
        title="Test",
        model="claude"
    )

    for i in range(20):
        conversation.add_user_message(f"Message {i}")

    compressed = compress_conversation(
        conversation,
        strategy="sliding_window",
        max_messages=10
    )

    assert len(compressed.messages) == 10

run_test("Compress conversation convenience function", test_compress_conversation_function)

# 测试总结
print("\n" + "=" * 80)
print("测试总结")
print("=" * 80)
print(f"总计: {total_tests} 个测试")
print(f"通过: {passed_tests} 个")
print(f"失败: {failed_tests} 个")
print(f"通过率: {100 * passed_tests / total_tests:.1f}%")
print("=" * 80)

if failed_tests == 0:
    print("\n✅ 所有测试通过！")
    sys.exit(0)
else:
    print(f"\n❌ {failed_tests} 个测试失败")
    sys.exit(1)
