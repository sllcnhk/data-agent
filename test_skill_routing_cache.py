"""
T2: SkillRoutingCache 单元测试（12 用例）
==========================================
使用临时目录避免污染生产 ChromaDB。
"""
import sys
import tempfile
import time
import unittest

sys.path.insert(0, "backend")


def _make_cache(tmpdir, version="v1", ttl=86400):
    from skills.skill_routing_cache import SkillRoutingCache
    return SkillRoutingCache(db_path=tmpdir, skill_set_version=version, ttl=ttl)


class TestSkillRoutingCache(unittest.TestCase):

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()

    # T2-1: 精确命中 — 相同 message 返回缓存结果
    def test_cache_hit_same_message(self):
        cache = _make_cache(self._tmpdir)
        if not cache.is_available:
            self.skipTest("chromadb not installed")
        routing = {"clickhouse-analyst": 0.9}
        cache.put("分析外呼数据", routing)
        result = cache.get("分析外呼数据")
        self.assertIsNotNone(result)
        self.assertAlmostEqual(result["clickhouse-analyst"], 0.9)

    # T2-2: 不同消息 → 缓存未命中，返回 None
    def test_cache_miss_different_message(self):
        cache = _make_cache(self._tmpdir)
        if not cache.is_available:
            self.skipTest("chromadb not installed")
        cache.put("消息A", {"etl-engineer": 0.8})
        result = cache.get("完全不同的消息B")
        self.assertIsNone(result)

    # T2-3: TTL 过期后返回 None
    def test_ttl_expired_returns_none(self):
        cache = _make_cache(self._tmpdir, ttl=1)  # 1秒 TTL
        if not cache.is_available:
            self.skipTest("chromadb not installed")
        cache.put("TTL测试消息", {"skill": 0.7})
        time.sleep(1.1)
        result = cache.get("TTL测试消息")
        self.assertIsNone(result)

    # T2-4: skill_set_version 变化后旧缓存不返回（版本失效）
    def test_version_mismatch_returns_none(self):
        cache_v1 = _make_cache(self._tmpdir, version="v1")
        if not cache_v1.is_available:
            self.skipTest("chromadb not installed")
        cache_v1.put("版本测试消息", {"skill": 0.8})

        # 新版本的 cache 查同一条消息
        cache_v2 = _make_cache(self._tmpdir, version="v2")
        result = cache_v2.get("版本测试消息")
        self.assertIsNone(result)

    # T2-5: put 后立即 get 验证数据完整性（多个字段）
    def test_put_then_get_data_integrity(self):
        cache = _make_cache(self._tmpdir)
        if not cache.is_available:
            self.skipTest("chromadb not installed")
        routing = {"a": 0.9, "b": 0.6, "c": 0.45}
        cache.put("完整性测试", routing)
        result = cache.get("完整性测试")
        self.assertIsNotNone(result)
        self.assertAlmostEqual(result["a"], 0.9)
        self.assertAlmostEqual(result["b"], 0.6)
        self.assertAlmostEqual(result["c"], 0.45)

    # T2-6: invalidate_all 后 get 返回 None
    def test_invalidate_all_clears_cache(self):
        cache = _make_cache(self._tmpdir)
        if not cache.is_available:
            self.skipTest("chromadb not installed")
        cache.put("消息1", {"skill": 0.8})
        cache.put("消息2", {"skill": 0.7})
        cache.invalidate_all()
        self.assertIsNone(cache.get("消息1"))
        self.assertIsNone(cache.get("消息2"))

    # T2-7: ChromaDB 不可用时 get 返回 None，不抛出异常
    def test_unavailable_chroma_get_returns_none(self):
        from skills.skill_routing_cache import SkillRoutingCache
        cache = SkillRoutingCache.__new__(SkillRoutingCache)
        cache._available = False
        cache._collection = None
        result = cache.get("任意消息")
        self.assertIsNone(result)

    # T2-8: ChromaDB 不可用时 put 不抛出异常
    def test_unavailable_chroma_put_no_exception(self):
        from skills.skill_routing_cache import SkillRoutingCache
        cache = SkillRoutingCache.__new__(SkillRoutingCache)
        cache._available = False
        cache._collection = None
        # 不应抛出任何异常
        try:
            cache.put("任意消息", {"skill": 0.9})
        except Exception as e:
            self.fail(f"put raised unexpected exception: {e}")

    # T2-9: 空 routing dict 可以正常存取
    def test_empty_routing_stored_and_retrieved(self):
        cache = _make_cache(self._tmpdir)
        if not cache.is_available:
            self.skipTest("chromadb not installed")
        cache.put("空routing消息", {})
        result = cache.get("空routing消息")
        self.assertIsNotNone(result)
        self.assertEqual(result, {})

    # T2-10: update_version 后旧消息缓存失效
    def test_update_version_invalidates_old_cache(self):
        cache = _make_cache(self._tmpdir, version="v1")
        if not cache.is_available:
            self.skipTest("chromadb not installed")
        cache.put("版本更新测试", {"skill": 0.8})
        cache.update_version("v2")
        result = cache.get("版本更新测试")
        self.assertIsNone(result)

    # T2-11: 超长消息不崩溃（消息截断到 500 字符存储）
    def test_very_long_message_no_crash(self):
        cache = _make_cache(self._tmpdir)
        if not cache.is_available:
            self.skipTest("chromadb not installed")
        long_msg = "测试" * 1000
        cache.put(long_msg, {"skill": 0.5})
        result = cache.get(long_msg)
        self.assertIsNotNone(result)

    # T2-12: 消息含特殊字符（JSON 中的引号/换行）不崩溃
    def test_special_chars_in_message_no_crash(self):
        cache = _make_cache(self._tmpdir)
        if not cache.is_available:
            self.skipTest("chromadb not installed")
        special_msg = 'msg with "quotes"\nand newlines\t and tabs'
        cache.put(special_msg, {"skill": 0.7})
        result = cache.get(special_msg)
        self.assertIsNotNone(result)
        self.assertAlmostEqual(result["skill"], 0.7)


if __name__ == "__main__":
    import pytest, sys
    sys.exit(pytest.main([__file__, "-v", "-s"]))
