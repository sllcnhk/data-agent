"""
Test Semantic Cache System - Phase 3.5

测试语义缓存系统
"""
import sys
import os
import time

# 添加 backend 到路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'backend'))

from backend.core.semantic_cache import (
    SemanticCache,
    SemanticCacheDecorator,
    get_semantic_cache
)
from backend.core.embedding_service import EmbeddingService


def test_initialization():
    """测试初始化"""
    print("\n[TEST 1] Semantic Cache Initialization")
    print("-" * 60)

    try:
        cache = SemanticCache(
            similarity_threshold=0.85,
            max_cache_size=100,
            default_ttl=3600
        )

        assert cache is not None
        assert cache.similarity_threshold == 0.85
        assert cache.max_cache_size == 100
        assert cache.default_ttl == 3600

        print("[PASS] Initialization successful")
        print(f"  Similarity threshold: {cache.similarity_threshold}")
        print(f"  Max cache size: {cache.max_cache_size}")
        print(f"  Default TTL: {cache.default_ttl}s")

        return cache

    except Exception as e:
        print(f"[FAIL] Initialization failed: {e}")
        import traceback
        traceback.print_exc()
        return None


def test_cache_miss():
    """测试缓存未命中"""
    print("\n[TEST 2] Cache Miss")
    print("-" * 60)

    try:
        cache = SemanticCache()

        # 查询空缓存
        result = cache.get("What is machine learning?")

        assert result is None

        print("[PASS] Cache miss works correctly")
        print(f"  Result: {result}")

        stats = cache.get_stats()
        print(f"  Total queries: {stats['total_queries']}")
        print(f"  Cache misses: {stats['cache_misses']}")

        return True

    except Exception as e:
        print(f"[FAIL] Cache miss test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_cache_set_get():
    """测试缓存设置和获取"""
    print("\n[TEST 3] Cache Set and Get")
    print("-" * 60)

    try:
        cache = SemanticCache(similarity_threshold=0.8)

        # 设置缓存
        query = "How to connect to a database?"
        response = "You can use connection strings to connect to databases."

        cache_id = cache.set(query, response)
        assert cache_id is not None

        print(f"[PASS] Cache set: id={cache_id[:8]}...")

        # 获取缓存（完全相同）
        result = cache.get(query)
        assert result is not None

        cached_response, similarity = result
        assert cached_response == response
        assert similarity >= 0.99  # 应该非常相似

        print(f"[PASS] Cache hit (exact match)")
        print(f"  Similarity: {similarity:.4f}")
        print(f"  Response: {cached_response[:50]}...")

        # 获取缓存（语义相似）
        similar_query = "How can I connect to databases?"
        result2 = cache.get(similar_query)

        if result2:
            cached_response2, similarity2 = result2
            print(f"[PASS] Cache hit (similar query)")
            print(f"  Similarity: {similarity2:.4f}")
        else:
            print(f"[INFO] No hit for similar query (threshold too high)")

        return True

    except Exception as e:
        print(f"[FAIL] Set/Get test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_similarity_threshold():
    """测试相似度阈值"""
    print("\n[TEST 4] Similarity Threshold")
    print("-" * 60)

    try:
        # 使用较低的阈值
        cache = SemanticCache(similarity_threshold=0.6)

        # 添加缓存
        cache.set(
            "What is Python programming?",
            "Python is a high-level programming language."
        )

        # 测试不同相似度的查询
        queries = [
            ("What is Python programming?", "Exact match"),
            ("What is Python?", "Similar query"),
            ("Tell me about Python language", "Related query"),
            ("What is the weather?", "Unrelated query"),
        ]

        hit_count = 0
        for query, description in queries:
            result = cache.get(query)

            if result is not None:
                _, similarity = result
                print(f"  [HIT] {description}: similarity={similarity:.4f}")
                hit_count += 1
            else:
                print(f"  [MISS] {description}")

        # 至少精确匹配应该命中
        assert hit_count >= 1, "Expected at least exact match to hit"

        print("[PASS] Similarity threshold works correctly")

        return True

    except Exception as e:
        print(f"[FAIL] Similarity threshold test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_cache_stats():
    """测试统计信息"""
    print("\n[TEST 5] Cache Statistics")
    print("-" * 60)

    try:
        cache = SemanticCache(similarity_threshold=0.8)

        # 添加一些缓存
        cache.set("Query 1", "Response 1")
        cache.set("Query 2", "Response 2")
        cache.set("Query 3", "Response 3")

        # 执行一些查询
        cache.get("Query 1")  # Hit
        cache.get("Query 1")  # Hit
        cache.get("Query 2")  # Hit
        cache.get("Unknown")  # Miss
        cache.get("Another unknown")  # Miss

        # 获取统计
        stats = cache.get_stats()

        print("[PASS] Statistics retrieved")
        print(f"  Total queries: {stats['total_queries']}")
        print(f"  Cache hits: {stats['cache_hits']}")
        print(f"  Cache misses: {stats['cache_misses']}")
        print(f"  Hit rate: {stats['hit_rate']:.2%}")
        print(f"  Cache size: {stats['cache_size']}")

        assert stats['cache_hits'] == 3
        assert stats['cache_misses'] == 2
        assert stats['cache_size'] == 3

        return True

    except Exception as e:
        print(f"[FAIL] Statistics test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_ttl_expiration():
    """测试 TTL 过期"""
    print("\n[TEST 6] TTL Expiration")
    print("-" * 60)

    try:
        cache = SemanticCache(default_ttl=1)  # 1 second TTL

        # 添加缓存
        cache.set("Short-lived query", "This will expire soon")

        # 立即查询（应该命中）
        result = cache.get("Short-lived query")
        assert result is not None
        print("[PASS] Cache hit before expiration")

        # 等待过期
        time.sleep(1.5)

        # 再次查询（应该未命中）
        result2 = cache.get("Short-lived query")
        assert result2 is None
        print("[PASS] Cache miss after expiration")

        return True

    except Exception as e:
        print(f"[FAIL] TTL test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_lru_eviction():
    """测试 LRU 淘汰"""
    print("\n[TEST 7] LRU Eviction")
    print("-" * 60)

    try:
        cache = SemanticCache(
            max_cache_size=3,
            enable_lru=True
        )

        # 添加 3 个条目（填满缓存）
        cache.set("Query 1", "Response 1")
        cache.set("Query 2", "Response 2")
        cache.set("Query 3", "Response 3")

        stats = cache.get_stats()
        assert stats['cache_size'] == 3
        print(f"[PASS] Cache filled: {stats['cache_size']} entries")

        # 添加第 4 个条目（应该淘汰最旧的）
        cache.set("Query 4", "Response 4")

        stats = cache.get_stats()
        assert stats['cache_size'] == 3
        assert stats['evictions'] == 1

        print(f"[PASS] LRU eviction works")
        print(f"  Cache size: {stats['cache_size']}")
        print(f"  Evictions: {stats['evictions']}")

        return True

    except Exception as e:
        print(f"[FAIL] LRU test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_cost_savings():
    """测试成本节省估算"""
    print("\n[TEST 8] Cost Savings Estimation")
    print("-" * 60)

    try:
        cache = SemanticCache(similarity_threshold=0.8)

        # 模拟一些缓存命中
        cache.set("Query 1", "Response 1")
        cache.get("Query 1")  # Hit
        cache.get("Query 1")  # Hit
        cache.get("Query 1")  # Hit

        # 计算节省
        savings = cache.estimate_savings(
            avg_query_tokens=500,
            avg_response_tokens=500,
            cost_per_1k_tokens=0.01
        )

        print("[PASS] Cost savings calculated")
        print(f"  Cache hits: {savings['cache_hits']}")
        print(f"  Saved tokens: {savings['saved_tokens']}")
        print(f"  Saved cost: ${savings['saved_cost_usd']:.4f}")
        print(f"  Hit rate: {savings['hit_rate']:.2%}")

        assert savings['cache_hits'] == 3
        assert savings['saved_tokens'] == 3000  # 3 hits * 1000 tokens

        return True

    except Exception as e:
        print(f"[FAIL] Cost savings test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_cache_decorator():
    """测试缓存装饰器"""
    print("\n[TEST 9] Cache Decorator")
    print("-" * 60)

    try:
        cache = SemanticCache(similarity_threshold=0.8)

        # 模拟 LLM 调用
        call_count = [0]

        @SemanticCacheDecorator(cache)
        def mock_llm_call(query: str) -> str:
            call_count[0] += 1
            return f"LLM response to: {query}"

        # 第一次调用（缓存未命中）
        response1 = mock_llm_call("What is AI?")
        assert call_count[0] == 1
        print(f"[PASS] First call (cache miss): LLM called")

        # 第二次调用（缓存命中）
        response2 = mock_llm_call("What is AI?")
        assert call_count[0] == 1  # 不应该再次调用
        assert response2 == response1

        print(f"[PASS] Second call (cache hit): LLM not called")
        print(f"  Total LLM calls: {call_count[0]}")

        return True

    except Exception as e:
        print(f"[FAIL] Decorator test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    """运行所有测试"""
    print("=" * 60)
    print("Semantic Cache System Tests")
    print("=" * 60)

    all_passed = True

    try:
        # Test 1: Initialization
        cache = test_initialization()
        if cache is None:
            all_passed = False

        # Test 2: Cache Miss
        if not test_cache_miss():
            all_passed = False

        # Test 3: Set and Get
        if not test_cache_set_get():
            all_passed = False

        # Test 4: Similarity Threshold
        if not test_similarity_threshold():
            all_passed = False

        # Test 5: Statistics
        if not test_cache_stats():
            all_passed = False

        # Test 6: TTL Expiration
        if not test_ttl_expiration():
            all_passed = False

        # Test 7: LRU Eviction
        if not test_lru_eviction():
            all_passed = False

        # Test 8: Cost Savings
        if not test_cost_savings():
            all_passed = False

        # Test 9: Decorator
        if not test_cache_decorator():
            all_passed = False

        # 结果
        print("\n" + "=" * 60)
        if all_passed:
            print("[SUCCESS] All Semantic Cache tests passed!")
            print("=" * 60)
            print("\n[OK] Initialization")
            print("[OK] Cache Miss")
            print("[OK] Set and Get")
            print("[OK] Similarity Threshold")
            print("[OK] Statistics")
            print("[OK] TTL Expiration")
            print("[OK] LRU Eviction")
            print("[OK] Cost Savings")
            print("[OK] Cache Decorator")
            print("\nPhase 3.5 (Semantic Cache System) completed successfully.")
            return 0
        else:
            print("[PARTIAL] Some tests failed")
            print("=" * 60)
            return 1

    except Exception as e:
        print(f"\n[ERROR] Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
