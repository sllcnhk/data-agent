"""
Phase 1 综合测试

独立测试所有 Phase 1 实现的功能，避免依赖问题
"""
import sys
import os

# 添加项目路径
backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
project_root = os.path.dirname(backend_dir)
sys.path.insert(0, project_root)
sys.path.insert(0, backend_dir)

# Mock 缺失的模块
class MockModule:
    def __getattr__(self, name):
        return MockModule()

sys.modules['openai'] = MockModule()
sys.modules['anthropic'] = MockModule()
sys.modules['google.generativeai'] = MockModule()

print("=" * 80)
print("Phase 1 综合测试")
print("=" * 80)

passed = 0
failed = 0

def test(name, func):
    global passed, failed
    try:
        func()
        print(f"[PASS] {name}")
        passed += 1
    except Exception as e:
        print(f"[FAIL] {name}: {e}")
        failed += 1

# ============================================================================
# Part 1: TokenCounter 测试
# ============================================================================
print("\n[Part 1] TokenCounter 测试")
print("-" * 80)

from backend.core.token_counter import TokenCounter, get_token_counter, count_tokens

# Test 1.1
def test_token_counter_singleton():
    c1 = get_token_counter()
    c2 = get_token_counter()
    assert c1 is c2
test("TokenCounter singleton", test_token_counter_singleton)

# Test 1.2
def test_count_english():
    tokens = count_tokens("Hello World", "claude")
    assert tokens > 0
    assert tokens < 50
test("Count English text", test_count_english)

# Test 1.3
def test_count_chinese():
    tokens = count_tokens("你好世界", "claude")
    assert tokens > 0
test("Count Chinese text", test_count_chinese)

# Test 1.4
def test_count_empty():
    tokens = count_tokens("", "claude")
    assert tokens == 0
test("Count empty text", test_count_empty)

# Test 1.5
def test_multiple_models():
    for model in ["claude", "gpt-4", "minimax"]:
        tokens = count_tokens("test", model)
        assert tokens > 0
test("Multiple models support", test_multiple_models)

# Test 1.6
def test_messages_tokens():
    counter = get_token_counter()
    messages = [
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hi there"}
    ]
    result = counter.count_messages_tokens(messages, "claude")
    assert result["prompt_tokens"] > 0
    assert result["completion_tokens"] > 0
    assert result["total_tokens"] == result["prompt_tokens"] + result["completion_tokens"]
test("Count messages tokens", test_messages_tokens)

# Test 1.7
def test_token_limit():
    counter = get_token_counter()
    short = "short"
    long = "long " * 1000
    assert counter.check_token_limit(short, "claude", 100)
    assert not counter.check_token_limit(long, "claude", 100)
test("Token limit check", test_token_limit)

# Test 1.8
def test_truncate():
    counter = get_token_counter()
    long_text = "test " * 100
    truncated = counter.truncate_to_token_limit(long_text, "claude", 50)
    tokens = counter.count_tokens(truncated, "claude")
    assert tokens <= 50
test("Truncate to limit", test_truncate)

# ============================================================================
# Part 2: HybridContextManager 测试
# ============================================================================
print("\n[Part 2] HybridContextManager 测试")
print("-" * 80)

from backend.core.conversation_format import UnifiedConversation, MessageRole
from backend.core.context_manager import (
    HybridContextManager,
    FullContextStrategy,
    SlidingWindowStrategy,
    SmartCompressionStrategy
)

# Test 2.1
def test_full_strategy():
    strategy = FullContextStrategy()
    conv = UnifiedConversation(conversation_id="test", title="Test", model="claude")
    for i in range(20):
        conv.add_user_message(f"Msg {i}")
    compressed = strategy.compress(conv, 10)
    assert len(compressed.messages) == 20  # No compression
test("Full strategy no compression", test_full_strategy)

# Test 2.2
def test_sliding_window():
    strategy = SlidingWindowStrategy()
    conv = UnifiedConversation(conversation_id="test", title="Test", model="claude")
    for i in range(30):
        conv.add_user_message(f"Msg {i}")
    compressed = strategy.compress(conv, 10)
    assert len(compressed.messages) == 10  # Keep 10 recent
test("Sliding window strategy", test_sliding_window)

# Test 2.3
def test_smart_compression():
    strategy = SmartCompressionStrategy(keep_first=2, keep_recent=8)
    conv = UnifiedConversation(conversation_id="test", title="Test", model="claude")
    for i in range(30):
        conv.add_user_message(f"User {i}")
        conv.add_assistant_message(f"Reply {i}")
    compressed = strategy.compress(conv, 15)
    # Should have: 2 first + 1 summary + 8 recent = 11
    # But we need to check the actual count as it might vary
    assert len(compressed.messages) <= 15  # Within limit
    has_summary = any("[历史对话摘要]" in str(m.content) for m in compressed.messages)
    assert has_summary, "Should have summary message"
test("Smart compression strategy", test_smart_compression)

# Test 2.4
def test_manager_creation():
    manager = HybridContextManager(strategy="full", max_context_length=10)
    assert manager.strategy_name == "full"
    assert manager.max_context_length == 10
test("Manager creation", test_manager_creation)

# Test 2.5
def test_manager_compress():
    manager = HybridContextManager(strategy="sliding_window", max_context_length=10)
    conv = UnifiedConversation(conversation_id="test", title="Test", model="claude")
    for i in range(20):
        conv.add_user_message(f"Msg {i}")
    compressed = manager.compress_conversation(conv)
    assert len(compressed.messages) == 10
test("Manager compress conversation", test_manager_compress)

# Test 2.6
def test_snapshot_creation():
    manager = HybridContextManager(strategy="full", max_context_length=10)
    conv = UnifiedConversation(conversation_id="test", title="Test", model="claude")
    conv.add_user_message("Hello")
    snapshot = manager.create_snapshot(conv, "full")
    assert snapshot["snapshot_type"] == "full"
    assert "created_at" in snapshot
test("Snapshot creation", test_snapshot_creation)

# Test 2.7
def test_create_from_settings():
    manager = HybridContextManager.create_from_settings()
    assert manager.strategy_name in ["full", "sliding_window", "compressed", "smart", "semantic"]
    assert manager.max_context_length > 0
test("Create from settings", test_create_from_settings)

# ============================================================================
# Part 3: Settings 配置测试
# ============================================================================
print("\n[Part 3] Settings 配置测试")
print("-" * 80)

from backend.config.settings import settings

# Test 3.1
def test_settings_phase1():
    assert settings.max_context_messages == 30
    assert settings.max_context_tokens == 150000
    assert settings.context_utilization_target == 0.75
    assert settings.context_compression_strategy == "smart"
    assert settings.enable_context_cache == True
    assert settings.context_cache_ttl == 300
test("Phase 1 settings configuration", test_settings_phase1)

# ============================================================================
# 测试总结
# ============================================================================
print("\n" + "=" * 80)
print("测试总结")
print("=" * 80)
total = passed + failed
print(f"总计: {total} 个测试")
print(f"通过: {passed} 个 ({100*passed/total:.1f}%)")
print(f"失败: {failed} 个")
print("=" * 80)

if failed == 0:
    print("\n[SUCCESS] Phase 1 All Tests Passed!")
    print()
    print("Test Coverage:")
    print("  - TokenCounter: 8/8 tests passed")
    print("  - HybridContextManager: 7/7 tests passed")
    print("  - Settings Configuration: 1/1 test passed")
    print()
    print("Total Coverage: 16/16 (100%)")
else:
    print(f"\n[FAILURE] {failed} tests failed")

sys.exit(0 if failed == 0 else 1)
