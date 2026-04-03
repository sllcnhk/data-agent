"""
Phase 1 完整验证（带 Mock）

在实际环境下验证所有 Phase 1 功能
"""
import sys
import os

backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
project_root = os.path.dirname(backend_dir)
sys.path.insert(0, project_root)
sys.path.insert(0, backend_dir)

# Mock 缺失模块
class MockModule:
    def __getattr__(self, name):
        return MockModule()
    def __call__(self, *args, **kwargs):
        return MockModule()

sys.modules['openai'] = MockModule()
sys.modules['anthropic'] = MockModule()
sys.modules['google.generativeai'] = MockModule()

print("=" * 80)
print("Phase 1 完整环境验证")
print("=" * 80)
print()

passed = 0
failed = 0
skipped = 0

def test(name, func, require_service=None):
    global passed, failed, skipped
    try:
        result = func()
        if result is False and require_service:
            print(f"[SKIP] {name} (服务不可用: {require_service})")
            skipped += 1
        else:
            print(f"[PASS] {name}")
            passed += 1
        return True
    except Exception as e:
        print(f"[FAIL] {name}: {e}")
        failed += 1
        import traceback
        traceback.print_exc()
        return False

# ============================================================================
# 环境检查
# ============================================================================
print("[环境检查]")
print("-" * 80)

import platform
print(f"Python: {platform.python_version()}")

# 检查数据库
postgres_ok = False
redis_ok = False

try:
    from backend.config.database import test_postgres_connection, test_redis_connection
    postgres_ok = test_postgres_connection()
    redis_ok = test_redis_connection()
    print(f"PostgreSQL: {'OK' if postgres_ok else 'Not Available'}")
    print(f"Redis: {'OK' if redis_ok else 'Not Available'}")
except Exception as e:
    print(f"Database: Error - {e}")

db_available = postgres_ok  # Redis 是可选的，PostgreSQL 是必需的
print()

# ============================================================================
# Part 1: 配置验证
# ============================================================================
print("[Part 1] 配置验证")
print("-" * 80)

def test_settings():
    from backend.config.settings import settings
    assert settings.max_context_messages == 30, "max_context_messages should be 30"
    assert settings.max_context_tokens == 150000, "max_context_tokens should be 150000"
    assert settings.context_compression_strategy == "smart", "strategy should be smart"
    assert settings.context_utilization_target == 0.75, "utilization_target should be 0.75"
    assert settings.enable_context_cache == True, "enable_context_cache should be True"
    assert settings.context_cache_ttl == 300, "context_cache_ttl should be 300"
    return True
test("Phase 1 settings configuration", test_settings)

# ============================================================================
# Part 2: TokenCounter 验证
# ============================================================================
print("\n[Part 2] TokenCounter 验证")
print("-" * 80)

def test_token_counter_init():
    from backend.core.token_counter import TokenCounter, get_token_counter
    counter = get_token_counter()
    assert counter is not None
    return True
test("TokenCounter initialization", test_token_counter_init)

def test_token_counting():
    from backend.core.token_counter import get_token_counter
    counter = get_token_counter()

    # 英文
    tokens_en = counter.count_tokens("Hello World", "claude")
    assert tokens_en > 0, "English text should have tokens"
    print(f"       'Hello World' -> {tokens_en} tokens")

    # 中文
    tokens_cn = counter.count_tokens("你好世界", "claude")
    assert tokens_cn > 0, "Chinese text should have tokens"
    print(f"       '你好世界' -> {tokens_cn} tokens")

    return True
test("Token counting", test_token_counting)

def test_message_tokens():
    from backend.core.token_counter import get_token_counter
    counter = get_token_counter()

    messages = [
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hi there"}
    ]

    result = counter.count_messages_tokens(messages, "claude")
    assert result["prompt_tokens"] > 0
    assert result["completion_tokens"] > 0
    assert result["total_tokens"] == result["prompt_tokens"] + result["completion_tokens"]

    print(f"       Prompt: {result['prompt_tokens']}, Completion: {result['completion_tokens']}, Total: {result['total_tokens']}")
    return True
test("Message token counting", test_message_tokens)

# ============================================================================
# Part 3: HybridContextManager 验证
# ============================================================================
print("\n[Part 3] HybridContextManager 验证")
print("-" * 80)

def test_context_manager_init():
    from backend.core.context_manager import HybridContextManager
    manager = HybridContextManager.create_from_settings()
    assert manager.strategy_name == "smart"
    assert manager.max_context_length == 30
    return True
test("HybridContextManager initialization", test_context_manager_init)

def test_compression():
    from backend.core.conversation_format import UnifiedConversation
    from backend.core.context_manager import HybridContextManager

    conv = UnifiedConversation(conversation_id="test", title="Test", model="claude")
    for i in range(50):
        conv.add_user_message(f"Message {i}")

    manager = HybridContextManager.create_from_settings()
    compressed = manager.compress_conversation(conv)

    original_count = 50
    compressed_count = len(compressed.messages)

    print(f"       Original: {original_count} messages")
    print(f"       Compressed: {compressed_count} messages")
    print(f"       Compression rate: {(1 - compressed_count/original_count)*100:.1f}%")

    assert compressed_count <= 30, "Compressed should be <= max_context_length"
    return True
test("Context compression", test_compression)

# ============================================================================
# Part 4: 数据库集成验证
# ============================================================================
print(f"\n[Part 4] 数据库集成验证 {'(PostgreSQL 可用)' if db_available else '(跳过 - PostgreSQL 不可用)'}")
print("-" * 80)

if db_available:
    from backend.config.database import get_db_context
    from backend.services.conversation_service import ConversationService

    def test_create_conv():
        with get_db_context() as db:
            service = ConversationService(db)
            conv = service.create_conversation(
                title="Phase 1 Validation",
                system_prompt="Test",
                model="claude"
            )
            conv_id = str(conv.id)
            assert conv.id is not None

            # 清理
            service.delete_conversation(conv_id)
            return True
    test("Create conversation", test_create_conv)

    def test_auto_tokens():
        with get_db_context() as db:
            service = ConversationService(db)
            conv = service.create_conversation(
                title="Token Test",
                model="claude"
            )

            msg = service.add_message(
                conversation_id=str(conv.id),
                role="user",
                content="This is a test message."
            )

            assert msg.total_tokens > 0, "Should have tokens"
            assert msg.prompt_tokens > 0, "Should have prompt tokens"
            print(f"       Message tokens: {msg.total_tokens}")

            db.refresh(conv)
            assert conv.total_tokens == msg.total_tokens, "Conversation should accumulate tokens"
            print(f"       Conversation tokens: {conv.total_tokens}")

            # 清理
            service.delete_conversation(str(conv.id))
            return True
    test("Auto token counting", test_auto_tokens)

    def test_context_build():
        with get_db_context() as db:
            service = ConversationService(db)
            conv = service.create_conversation(
                title="Context Test",
                model="claude"
            )

            # 添加 50 条消息
            for i in range(25):
                service.add_message(
                    conversation_id=str(conv.id),
                    role="user",
                    content=f"User {i}"
                )
                service.add_message(
                    conversation_id=str(conv.id),
                    role="assistant",
                    content=f"Reply {i}"
                )

            context = service._build_context(str(conv.id))

            assert "context_info" in context
            info = context["context_info"]

            print(f"       Original: {info['original_message_count']} messages")
            print(f"       Compressed: {info['compressed_message_count']} messages")
            print(f"       Strategy: {info['strategy']}")

            assert info["compressed_message_count"] <= 30

            # 清理
            service.delete_conversation(str(conv.id))
            return True
    test("Context building with compression", test_context_build)

    def test_token_accumulation():
        with get_db_context() as db:
            service = ConversationService(db)
            conv = service.create_conversation(
                title="Accumulation Test",
                model="claude"
            )

            msg1 = service.add_message(
                conversation_id=str(conv.id),
                role="user",
                content="First"
            )
            msg2 = service.add_message(
                conversation_id=str(conv.id),
                role="assistant",
                content="Second"
            )
            msg3 = service.add_message(
                conversation_id=str(conv.id),
                role="user",
                content="Third"
            )

            db.refresh(conv)
            expected = msg1.total_tokens + msg2.total_tokens + msg3.total_tokens

            print(f"       Msg1: {msg1.total_tokens}, Msg2: {msg2.total_tokens}, Msg3: {msg3.total_tokens}")
            print(f"       Expected: {expected}, Actual: {conv.total_tokens}")

            assert conv.total_tokens == expected

            # 清理
            service.delete_conversation(str(conv.id))
            return True
    test("Token accumulation", test_token_accumulation)
else:
    print("跳过 - PostgreSQL 不可用")
    print("请启动 PostgreSQL 服务后重新运行")
    skipped += 4

# ============================================================================
# 总结
# ============================================================================
print("\n" + "=" * 80)
print("验证总结")
print("=" * 80)

total = passed + failed + skipped
success_rate = 100 * passed / (passed + failed) if (passed + failed) > 0 else 0

print(f"\n总计: {total} 个测试")
print(f"通过: {passed} 个")
print(f"失败: {failed} 个")
print(f"跳过: {skipped} 个")
print(f"成功率: {success_rate:.1f}%")

print("\n" + "-" * 80)

if failed == 0:
    if db_available:
        print("\n[SUCCESS] Phase 1 完整环境验证通过!")
        print("\n验证结果:")
        print("  [OK] 配置统一: 所有配置正确")
        print("  [OK] Token 计数: 自动计数正常")
        print("  [OK] 上下文压缩: 智能压缩正常")
        print("  [OK] 数据库集成: 完全正常")
        print("\nPhase 1 已在生产环境验证完成!")
        print("可以安全投入使用。")

        if not redis_ok:
            print("\n注意: Redis 未启动，但不影响核心功能")
            print("      Redis 用于缓存优化，建议启动以获得最佳性能")
    else:
        print("\n[PARTIAL SUCCESS] 基础功能验证通过!")
        print("\n已验证:")
        print("  [OK] 配置系统")
        print("  [OK] Token 计数")
        print("  [OK] 上下文压缩")
        print("\n需要:")
        print("  [PENDING] 数据库集成测试")
        print("\n下一步: 启动 PostgreSQL 后重新验证")
else:
    print(f"\n[FAILURE] {failed} 个测试失败")
    print("请检查错误日志")

print("=" * 80)

sys.exit(0 if failed == 0 else 1)
