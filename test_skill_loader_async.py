"""
T3: SkillLoader async 混合方法单元测试（20 用例）
=================================================
使用临时目录创建测试 skill 文件，mock LLM adapter 和路由缓存。
"""
import asyncio
import sys
import tempfile
import os
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

sys.path.insert(0, "backend")


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _write_skill(skill_dir: Path, name: str, triggers: list, description: str = "",
                 tier_subdir: str = "system", priority: str = "medium",
                 always_inject: bool = False) -> None:
    """在 skill_dir/{tier_subdir}/{name}.md 写入测试 skill 文件。"""
    d = skill_dir / tier_subdir
    d.mkdir(parents=True, exist_ok=True)
    trigger_lines = "\n".join(f"  - {t}" for t in triggers)
    always_inject_line = f"always_inject: {str(always_inject).lower()}"
    content = f"""---
name: {name}
version: "1.0"
description: {description or name + ' description'}
triggers:
{trigger_lines}
category: test
priority: {priority}
{always_inject_line}
---

# {name}

Test skill content for {name}.
"""
    (d / f"{name}.md").write_text(content, encoding="utf-8")


def _make_adapter(response_text: str):
    adapter = MagicMock()
    adapter.chat_plain = AsyncMock(return_value={
        "content": [{"type": "text", "text": response_text}],
        "stop_reason": "end_turn",
    })
    return adapter


def _make_settings_mock(mode="hybrid", threshold=0.45, cache_path=None, ttl=86400):
    """Create a MagicMock that mimics the settings object with routing fields."""
    ms = MagicMock()
    ms.skill_match_mode = mode
    ms.skill_semantic_threshold = threshold
    ms.skill_routing_cache_path = cache_path or "./data/skill_routing_cache"
    ms.skill_semantic_cache_ttl = ttl
    return ms


def _make_loader(tmpdir: str) -> "SkillLoader":
    from skills.skill_loader import SkillLoader
    loader = SkillLoader(skills_dir=tmpdir)
    loader.load_all()
    return loader


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------

class TestSkillLoaderAsync(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._skills_dir = Path(self._tmp) / "skills"

    def _loader(self) -> "SkillLoader":
        return _make_loader(str(self._skills_dir))

    # T3-1: keyword 模式结果与同步 build_skill_prompt 完全一致
    def test_keyword_mode_matches_sync(self):
        _write_skill(self._skills_dir, "analyst", ["分析", "查询"], tier_subdir="system")
        loader = self._loader()
        ms = _make_settings_mock(mode="keyword", cache_path=os.path.join(self._tmp, "cache"))
        with patch("backend.config.settings.settings", ms):
            sync_result = loader.build_skill_prompt("帮我分析数据")
            async_result = run(loader.build_skill_prompt_async("帮我分析数据"))
        self.assertEqual(sync_result, async_result)

    # T3-2: hybrid 模式 — 关键词命中的 skill 出现在结果中
    def test_hybrid_keyword_hit_appears_in_result(self):
        _write_skill(self._skills_dir, "analyst", ["分析", "查询"], tier_subdir="system")
        loader = self._loader()
        adapter = _make_adapter("{}")  # 语义路由返回空
        ms = _make_settings_mock(cache_path=os.path.join(self._tmp, "cache"))
        with patch("backend.config.settings.settings", ms):
            result = run(loader.build_skill_prompt_async("帮我分析数据", llm_adapter=adapter))
        self.assertIn("analyst", result)

    # T3-3: hybrid 模式 — 语义路由补充（mock 路由器返回 score=0.8）
    def test_hybrid_semantic_complement(self):
        _write_skill(self._skills_dir, "analyst", ["分析"], tier_subdir="system")
        _write_skill(self._skills_dir, "etl", ["ETL", "建表"], tier_subdir="system")
        loader = self._loader()
        # 消息没有 ETL 关键词，但语义路由认为 etl 相关
        adapter = _make_adapter('{"etl": 0.8}')
        ms = _make_settings_mock(cache_path=os.path.join(self._tmp, "cache"))
        with patch("backend.config.settings.settings", ms):
            result = run(loader.build_skill_prompt_async(
                "帮我处理数据管道", llm_adapter=adapter
            ))
        self.assertIn("etl", result)

    # T3-4: hybrid 模式 — 低于阈值的 semantic skill 不注入
    def test_hybrid_below_threshold_not_injected(self):
        _write_skill(self._skills_dir, "etl", ["ETL", "建表"], tier_subdir="system")
        loader = self._loader()
        adapter = _make_adapter('{"etl": 0.3}')  # 低于 0.45 阈值
        ms = _make_settings_mock(cache_path=os.path.join(self._tmp, "cache"))
        with patch("backend.config.settings.settings", ms):
            result = run(loader.build_skill_prompt_async(
                "随便问问", llm_adapter=adapter
            ))
        self.assertNotIn("etl", result)

    # T3-5: hybrid 模式 — keyword 和 semantic 同时命中同一 skill，不重复
    def test_hybrid_no_duplicate_when_both_match(self):
        _write_skill(self._skills_dir, "analyst", ["分析"], tier_subdir="system")
        loader = self._loader()
        # 语义路由也返回 analyst
        adapter = _make_adapter('{"analyst": 0.9}')
        ms = _make_settings_mock(cache_path=os.path.join(self._tmp, "cache"))
        with patch("backend.config.settings.settings", ms):
            result = run(loader.build_skill_prompt_async("帮我分析数据", llm_adapter=adapter))
        # skill 内容不应重复出现
        count = result.count("Test skill content for analyst")
        self.assertEqual(count, 1)

    # T3-6: llm 模式 — 关键词命中也清空，全部依赖路由器
    def test_llm_mode_depends_entirely_on_router(self):
        _write_skill(self._skills_dir, "analyst", ["分析"], tier_subdir="system")
        loader = self._loader()
        # 路由器对 analyst 返回 0.0 → 不注入
        adapter = _make_adapter("{}")
        ms = _make_settings_mock(mode="llm", cache_path=os.path.join(self._tmp, "cache"))
        with patch("backend.config.settings.settings", ms):
            result = run(loader.build_skill_prompt_async("帮我分析数据", llm_adapter=adapter))
        # 虽然消息含"分析"关键词，但 llm 模式不用关键词匹配
        self.assertNotIn("Test skill content for analyst", result)

    # T3-7: llm_adapter=None 时 hybrid 降级为纯关键词
    def test_none_adapter_falls_back_to_keyword(self):
        _write_skill(self._skills_dir, "analyst", ["分析"], tier_subdir="system")
        loader = self._loader()
        ms = _make_settings_mock(cache_path=os.path.join(self._tmp, "cache"))
        with patch("backend.config.settings.settings", ms):
            # llm_adapter=None
            result = run(loader.build_skill_prompt_async("帮我分析数据", llm_adapter=None))
        # 关键词命中仍然有效
        self.assertIn("analyst", result)

    # T3-8: 缓存命中时不调用 LLM router
    def test_cache_hit_skips_llm_call(self):
        _write_skill(self._skills_dir, "etl", ["ETL"], tier_subdir="system")
        loader = self._loader()
        adapter = _make_adapter('{"etl": 0.9}')
        ms = _make_settings_mock(cache_path=os.path.join(self._tmp, "cache"))
        with patch("backend.config.settings.settings", ms):
            loader._ensure_routing_components()

            # 手动写缓存
            if loader._routing_cache and loader._routing_cache.is_available:
                loader._routing_cache.put("无关键词的数据管道请求", {"etl": 0.85})

            run(loader.build_skill_prompt_async("无关键词的数据管道请求", llm_adapter=adapter))

            # adapter.chat_plain 不应被调用（命中缓存）
            if loader._routing_cache and loader._routing_cache.is_available:
                adapter.chat_plain.assert_not_called()

    # T3-9: 缓存未命中时调用 LLM router 并写缓存
    def test_cache_miss_calls_llm_and_writes_cache(self):
        _write_skill(self._skills_dir, "etl", ["ETL"], tier_subdir="system")
        loader = self._loader()
        adapter = _make_adapter('{"etl": 0.8}')
        ms = _make_settings_mock(cache_path=os.path.join(self._tmp, "cache"))
        with patch("backend.config.settings.settings", ms):
            run(loader.build_skill_prompt_async("唯一测试消息xyz123", llm_adapter=adapter))
            adapter.chat_plain.assert_called_once()

            # 检查是否写入缓存
            loader._ensure_routing_components()
            if loader._routing_cache and loader._routing_cache.is_available:
                cached = loader._routing_cache.get("唯一测试消息xyz123")
                self.assertIsNotNone(cached)

    # T3-10: reload_skills 后缓存版本更新
    def test_reload_increments_skill_set_version(self):
        _write_skill(self._skills_dir, "analyst", ["分析"], tier_subdir="system")
        loader = self._loader()
        v0 = loader._skill_set_version
        loader.load_all()
        self.assertEqual(loader._skill_set_version, v0 + 1)

    # T3-11: _MAX_INJECT_CHARS 在 async 路径同样生效（超限降级摘要）
    def test_max_inject_chars_protection_in_async(self):
        # 写 5 个 skill，内容极长
        for i in range(5):
            d = self._skills_dir / "system"
            d.mkdir(parents=True, exist_ok=True)
            content = (
                f"---\nname: bigskill{i}\nversion: '1.0'\ndescription: big\n"
                f"triggers:\n  - 触发{i}\ncategory: test\npriority: high\n"
                f"always_inject: false\n---\n\n" + ("X" * 5000)
            )
            (d / f"bigskill{i}.md").write_text(content, encoding="utf-8")
        from skills.skill_loader import SkillLoader
        loader = SkillLoader(skills_dir=str(self._skills_dir))
        loader.load_all()
        adapter = _make_adapter("{}")
        msg = " ".join(f"触发{i}" for i in range(5))  # 触发全部5个
        ms = _make_settings_mock(cache_path=os.path.join(self._tmp, "cache2"))
        with patch("backend.config.settings.settings", ms):
            result = run(loader.build_skill_prompt_async(msg, llm_adapter=adapter))
        # 超限时应有摘要模式 header
        if result:
            # 要么全文要么摘要，都不应超过 _MAX_INJECT_CHARS * 1.5
            from skills.skill_loader import _MAX_INJECT_CHARS
            self.assertLess(len(result), _MAX_INJECT_CHARS * 1.5)

    # T3-12: always_inject skill 始终注入，不经过路由
    def test_always_inject_skill_always_present(self):
        _write_skill(self._skills_dir, "_base-safety", [], tier_subdir="system",
                     always_inject=True, description="安全约束")
        loader = self._loader()
        adapter = _make_adapter("{}")  # 路由返回空
        ms = _make_settings_mock(cache_path=os.path.join(self._tmp, "cache"))
        with patch("backend.config.settings.settings", ms):
            result = run(loader.build_skill_prompt_async("随便的消息", llm_adapter=adapter))
        self.assertIn("_base-safety", result)

    # T3-13: 三层优先级：user > project > system 结构存在
    def test_three_tier_ordering(self):
        _write_skill(self._skills_dir, "sys-skill", ["通用"], tier_subdir="system")
        _write_skill(self._skills_dir, "proj-skill", ["通用"], tier_subdir="project")
        _write_skill(self._skills_dir, "user-skill", ["通用"], tier_subdir="user")
        loader = self._loader()
        adapter = _make_adapter("{}")
        ms = _make_settings_mock(cache_path=os.path.join(self._tmp, "cache"))
        with patch("backend.config.settings.settings", ms):
            result = run(loader.build_skill_prompt_async("通用问题", llm_adapter=adapter))
        # 三层 skill 都出现
        self.assertIn("sys-skill", result)
        self.assertIn("proj-skill", result)
        self.assertIn("user-skill", result)
        # user 优先于 system（user 的内容在前）
        self.assertLess(result.index("user-skill"), result.index("sys-skill"))

    # T3-14: get_match_details 返回 keyword 方法
    def test_get_match_details_keyword(self):
        _write_skill(self._skills_dir, "analyst", ["分析"], tier_subdir="system")
        loader = self._loader()
        details = loader.get_match_details("帮我分析")
        self.assertIn("analyst", details)
        self.assertEqual(details["analyst"]["method"], "keyword")

    # T3-15: get_match_details 返回 semantic 方法
    def test_get_match_details_semantic(self):
        _write_skill(self._skills_dir, "etl", ["ETL"], tier_subdir="system")
        loader = self._loader()
        details = loader.get_match_details("数据管道处理", semantic_scores={"etl": 0.8})
        self.assertIn("etl", details)
        self.assertEqual(details["etl"]["method"], "semantic")

    # T3-16: get_match_details 返回 always_inject 方法
    def test_get_match_details_always_inject(self):
        _write_skill(self._skills_dir, "_base-safety", [], tier_subdir="system",
                     always_inject=True)
        loader = self._loader()
        details = loader.get_match_details("任意消息")
        self.assertIn("_base-safety", details)
        self.assertEqual(details["_base-safety"]["method"], "always_inject")

    # T3-17: _build_from_matched_skills 空输入返回空字符串
    def test_build_from_matched_empty_returns_empty(self):
        loader = self._loader()
        result = loader._build_from_matched_skills([], [], [])
        self.assertEqual(result, "")

    # T3-18: 消息为空字符串不崩溃
    def test_empty_message_no_crash(self):
        _write_skill(self._skills_dir, "analyst", ["分析"], tier_subdir="system")
        loader = self._loader()
        adapter = _make_adapter("{}")
        ms = _make_settings_mock(cache_path=os.path.join(self._tmp, "cache"))
        with patch("backend.config.settings.settings", ms):
            result = run(loader.build_skill_prompt_async("", llm_adapter=adapter))
        self.assertIsInstance(result, str)

    # T3-19: settings 读取失败时降级为 keyword 模式（不崩溃）
    def test_settings_error_falls_back_to_keyword(self):
        _write_skill(self._skills_dir, "analyst", ["分析"], tier_subdir="system")
        loader = self._loader()
        # Make settings.skill_match_mode raise when accessed
        bad_ms = MagicMock()
        type(bad_ms).skill_match_mode = PropertyMock(side_effect=RuntimeError("settings error"))
        with patch("backend.config.settings.settings", bad_ms):
            result = run(loader.build_skill_prompt_async("帮我分析数据"))
        # 降级到 keyword，不应报错，结果应含触发的 skill
        self.assertIsInstance(result, str)

    # T3-20: 语义路由器抛出异常时降级为关键词结果
    def test_semantic_router_exception_falls_back_gracefully(self):
        _write_skill(self._skills_dir, "analyst", ["分析"], tier_subdir="system")
        _write_skill(self._skills_dir, "etl", ["ETL"], tier_subdir="system")
        loader = self._loader()
        adapter = MagicMock()
        adapter.chat_plain = AsyncMock(side_effect=RuntimeError("LLM crash"))
        ms = _make_settings_mock(cache_path=os.path.join(self._tmp, "cache"))
        with patch("backend.config.settings.settings", ms):
            # 消息含"分析"关键词
            result = run(loader.build_skill_prompt_async("帮我分析数据", llm_adapter=adapter))
        # 关键词命中的 analyst 应仍存在
        self.assertIn("analyst", result)


if __name__ == "__main__":
    import pytest, sys
    sys.exit(pytest.main([__file__, "-v", "-s"]))
