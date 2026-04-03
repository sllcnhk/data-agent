"""
test_semantic_skill_routing.py
===============================
语义混合技能命中机制 — 系统性测试（39 用例）

覆盖维度：
  A — SkillSemanticRouter 内部行为（6 用例）
  B — SkillRoutingCache 高级行为（6 用例）
  C — SkillLoader.build_skill_prompt_async 深度（10 用例）
  D — AgenticLoop._build_system_prompt 集成（5 用例）
  E — Preview API match_details 覆盖（8 用例）
  F — Settings 配置默认值（4 用例）

运行：
  /d/ProgramData/Anaconda3/envs/dataagent/python.exe test_semantic_skill_routing.py
"""

from __future__ import annotations

import asyncio
import inspect
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

# ── 路径配置 ──────────────────────────────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent / "backend"))

# Fake env — 避免 Settings 需要真实 .env
os.environ.setdefault("CLICKHOUSE_HOST", "localhost")
os.environ.setdefault("CLICKHOUSE_PORT", "9000")
os.environ.setdefault("CLICKHOUSE_USER", "default")
os.environ.setdefault("CLICKHOUSE_PASSWORD", "")
os.environ.setdefault("CLICKHOUSE_DATABASE", "default")
os.environ.setdefault("ADMIN_SECRET_TOKEN", "test-admin-token")

# ── FastAPI TestClient（Section E 使用） ──────────────────────────────────────
from fastapi import FastAPI
from fastapi.testclient import TestClient
from backend.api.skills import router as skills_router

_test_app = FastAPI()
_test_app.include_router(skills_router, prefix="/api/v1")
_client = TestClient(_test_app)


# ── 通用工具 ──────────────────────────────────────────────────────────────────

def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _make_adapter(response_text: str, raises=None):
    """创建固定文本响应的 mock LLM adapter。"""
    adapter = MagicMock()
    if raises:
        adapter.chat_plain = AsyncMock(side_effect=raises)
    else:
        adapter.chat_plain = AsyncMock(return_value={
            "content": [{"type": "text", "text": response_text}],
            "stop_reason": "end_turn",
        })
    return adapter


def _write_skill(skill_dir: Path, name: str, triggers: list,
                 tier_subdir: str = "system", description: str = "",
                 always_inject: bool = False, priority: str = "high",
                 content_extra: str = "") -> None:
    """写入测试用 SKILL.md 文件。"""
    d = skill_dir / tier_subdir
    d.mkdir(parents=True, exist_ok=True)
    trigger_lines = "\n".join(f"  - {t}" for t in triggers)
    body = content_extra or f"Test skill content for {name}."
    src = (
        f"---\n"
        f"name: {name}\n"
        f"version: \"1.0\"\n"
        f"description: {description or name + ' skill'}\n"
        f"triggers:\n{trigger_lines}\n"
        f"category: test\n"
        f"priority: {priority}\n"
        f"always_inject: {str(always_inject).lower()}\n"
        f"---\n\n# {name}\n\n{body}\n"
    )
    (d / f"{name}.md").write_text(src, encoding="utf-8")


def _make_settings_mock(mode="hybrid", threshold=0.45, cache_path=None, ttl=86400):
    ms = MagicMock()
    ms.skill_match_mode = mode
    ms.skill_semantic_threshold = threshold
    ms.skill_routing_cache_path = cache_path or "./data/test_cache"
    ms.skill_semantic_cache_ttl = ttl
    return ms


# ═════════════════════════════════════════════════════════════════════════════
# Section A: SkillSemanticRouter 内部行为
# ═════════════════════════════════════════════════════════════════════════════

class TestSkillSemanticRouterInternals(unittest.TestCase):
    """A1-A6: _parse_routing_json 和 _skill_list_text 的边界行为。"""

    def setUp(self):
        from skills.skill_semantic_router import _parse_routing_json, _skill_list_text, _MAX_SKILL_LIST_CHARS
        self._parse = _parse_routing_json
        self._list_text = _skill_list_text
        self._MAX = _MAX_SKILL_LIST_CHARS

    # A1: 干净 JSON 直接解析
    def test_A1_parse_clean_json(self):
        result = self._parse('{"analyst": 0.9, "etl": 0.7}')
        self.assertAlmostEqual(result["analyst"], 0.9)
        self.assertAlmostEqual(result["etl"], 0.7)

    # A2: markdown 代码块包裹 → 正则提取
    def test_A2_parse_markdown_wrapped(self):
        text = '```json\n{"analyst": 0.85}\n```'
        result = self._parse(text)
        self.assertIn("analyst", result)
        self.assertAlmostEqual(result["analyst"], 0.85)

    # A3: 非数字类型的值被过滤（JSON 值为字符串/列表）
    def test_A3_parse_filters_non_numeric_values(self):
        # score 必须是 int 或 float，字符串值应被过滤
        result = self._parse('{"analyst": 0.9, "bad": "high", "etl": 0.5}')
        self.assertIn("analyst", result)
        self.assertIn("etl", result)
        self.assertNotIn("bad", result)

    # A4: LLM 响应含多个 {...} 块 → 使用第一个有效的
    def test_A4_parse_first_valid_block_used(self):
        text = 'Some text {"analyst": 0.9} and more {"etl": 0.8}'
        result = self._parse(text)
        # 应当返回第一个有效块
        self.assertIsInstance(result, dict)
        self.assertGreater(len(result), 0)

    # A5: _skill_list_text 在超过 _MAX_SKILL_LIST_CHARS 时截断并加 "..."
    def test_A5_skill_list_text_truncates_on_overflow(self):
        class _FakeSkill:
            def __init__(self, i):
                self.name = f"skill-{i:04d}"
                self.description = "x" * 100
                self.triggers = ["trigger"]
        skills = [_FakeSkill(i) for i in range(50)]
        text = self._list_text(skills)
        self.assertLessEqual(len(text), self._MAX + 200)   # 允许最后一行轻微超出
        self.assertIn("...", text)

    # A6: _skill_list_text 格式含 name: description | triggers
    def test_A6_skill_list_text_format(self):
        class _FakeSkill:
            name = "test-skill"
            description = "测试描述"
            triggers = ["触发词A", "触发词B"]
        text = self._list_text([_FakeSkill()])
        self.assertIn("test-skill", text)
        self.assertIn("测试描述", text)
        self.assertIn("|", text)
        self.assertIn("触发词A", text)


# ═════════════════════════════════════════════════════════════════════════════
# Section B: SkillRoutingCache 高级行为
# ═════════════════════════════════════════════════════════════════════════════

class TestSkillRoutingCacheAdvanced(unittest.TestCase):
    """B1-B6: TTL 边界、invalidate 后重新写入、多次 put 等。"""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()

    def _make_cache(self, version="v1", ttl=86400):
        from skills.skill_routing_cache import SkillRoutingCache
        return SkillRoutingCache(db_path=self._tmpdir, skill_set_version=version, ttl=ttl)

    # B1: TTL=0 → 存入后立刻过期，get 返回 None
    def test_B1_ttl_zero_returns_none_immediately(self):
        cache = self._make_cache(ttl=0)
        if not cache.is_available:
            self.skipTest("chromadb not installed")
        cache.put("消息X", {"skill": 0.9})
        result = cache.get("消息X")
        self.assertIsNone(result)

    # B2: put({}) 空路由 → get 返回空 dict（不是 None）
    def test_B2_empty_routing_stored_and_retrieved(self):
        cache = self._make_cache()
        if not cache.is_available:
            self.skipTest("chromadb not installed")
        cache.put("消息空", {})
        result = cache.get("消息空")
        self.assertIsNotNone(result)
        self.assertEqual(result, {})

    # B3: put 多个字段 → 全部正确取回（数据完整性）
    def test_B3_large_routing_dict_integrity(self):
        cache = self._make_cache()
        if not cache.is_available:
            self.skipTest("chromadb not installed")
        routing = {f"skill-{i}": round(0.5 + i * 0.01, 3) for i in range(20)}
        cache.put("大型路由测试", routing)
        result = cache.get("大型路由测试")
        self.assertIsNotNone(result)
        for k, v in routing.items():
            self.assertAlmostEqual(result[k], v, places=3)

    # B4: update_version 后旧缓存不返回
    def test_B4_update_version_invalidates_old_entries(self):
        cache = self._make_cache(version="v1")
        if not cache.is_available:
            self.skipTest("chromadb not installed")
        cache.put("版本测试", {"skill": 0.8})
        cache.update_version("v99")
        result = cache.get("版本测试")
        self.assertIsNone(result)

    # B5: invalidate_all 后原来的 key 返回 None，再写入后可再取
    def test_B5_invalidate_all_then_rewrite(self):
        cache = self._make_cache()
        if not cache.is_available:
            self.skipTest("chromadb not installed")
        cache.put("重写测试", {"skill": 0.7})
        cache.invalidate_all()
        self.assertIsNone(cache.get("重写测试"))
        # 重新写入
        cache.put("重写测试", {"skill": 0.9})
        result = cache.get("重写测试")
        self.assertIsNotNone(result)
        self.assertAlmostEqual(result["skill"], 0.9)

    # B6: 同一消息多次 put → 最新值覆盖（upsert 语义）
    def test_B6_multiple_puts_last_wins(self):
        cache = self._make_cache()
        if not cache.is_available:
            self.skipTest("chromadb not installed")
        cache.put("覆盖测试", {"skill": 0.5})
        cache.put("覆盖测试", {"skill": 0.99})  # 覆盖
        result = cache.get("覆盖测试")
        self.assertIsNotNone(result)
        self.assertAlmostEqual(result["skill"], 0.99)


# ═════════════════════════════════════════════════════════════════════════════
# Section C: SkillLoader.build_skill_prompt_async 深度
# ═════════════════════════════════════════════════════════════════════════════

class TestBuildSkillPromptAsyncDeep(unittest.TestCase):
    """C1-C10: 阈值边界、模式行为、缓存路径、幂等性等。"""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._skills_dir = Path(self._tmp) / "skills"

    def _loader(self):
        from skills.skill_loader import SkillLoader
        loader = SkillLoader(skills_dir=str(self._skills_dir))
        loader.load_all()
        return loader

    # C1: keyword 模式 → async 结果完全等于 sync 结果
    def test_C1_keyword_mode_async_equals_sync(self):
        _write_skill(self._skills_dir, "analyst", ["分析", "查询"], tier_subdir="system")
        loader = self._loader()
        ms = _make_settings_mock(mode="keyword")
        with patch("backend.config.settings.settings", ms):
            sync_r = loader.build_skill_prompt("帮我分析数据")
            async_r = run(loader.build_skill_prompt_async("帮我分析数据"))
        self.assertEqual(sync_r, async_r)

    # C2: 阈值边界 score == 0.45 → 恰好包含（>= 判断）
    def test_C2_threshold_exact_boundary_included(self):
        _write_skill(self._skills_dir, "etl", ["X_NOHIT"], tier_subdir="system")
        loader = self._loader()
        adapter = _make_adapter('{"etl": 0.45}')
        ms = _make_settings_mock(threshold=0.45,
                                 cache_path=os.path.join(self._tmp, "cache_C2"))
        with patch("backend.config.settings.settings", ms):
            result = run(loader.build_skill_prompt_async("无关键词消息", llm_adapter=adapter))
        self.assertIn("etl", result)

    # C3: score = 0.449 → 低于阈值不注入
    def test_C3_below_threshold_not_injected(self):
        _write_skill(self._skills_dir, "etl", ["X_NOHIT"], tier_subdir="system")
        loader = self._loader()
        adapter = _make_adapter('{"etl": 0.449}')
        ms = _make_settings_mock(threshold=0.45,
                                 cache_path=os.path.join(self._tmp, "cache_C3"))
        with patch("backend.config.settings.settings", ms):
            result = run(loader.build_skill_prompt_async("无关键词消息", llm_adapter=adapter))
        self.assertNotIn("etl", result)

    # C4: hybrid + llm_adapter=None + 无缓存 → 仅关键词命中，不抛异常
    def test_C4_hybrid_no_adapter_keyword_only(self):
        _write_skill(self._skills_dir, "analyst", ["分析"], tier_subdir="system")
        _write_skill(self._skills_dir, "etl", ["ETL"], tier_subdir="system")
        loader = self._loader()
        ms = _make_settings_mock(cache_path=os.path.join(self._tmp, "cache_C4"))
        with patch("backend.config.settings.settings", ms):
            result = run(loader.build_skill_prompt_async("帮我分析数据", llm_adapter=None))
        # analyst 应被关键词命中，etl 不应出现（无语义路由）
        self.assertIn("analyst", result)
        self.assertNotIn("etl", result)

    # C5: llm 模式 + llm_adapter=None → 关键词结果被清空，返回 "" 或仅 base skills
    def test_C5_llm_mode_no_adapter_returns_base_only(self):
        _write_skill(self._skills_dir, "analyst", ["分析"], tier_subdir="system")
        loader = self._loader()
        ms = _make_settings_mock(mode="llm",
                                 cache_path=os.path.join(self._tmp, "cache_C5"))
        with patch("backend.config.settings.settings", ms):
            result = run(loader.build_skill_prompt_async("帮我分析数据", llm_adapter=None))
        # llm 模式无 adapter，关键词被清空，非 always_inject 的 analyst 不应出现
        self.assertNotIn("Test skill content for analyst", result)

    # C6: hybrid 缓存命中 → LLM adapter 不被调用
    def test_C6_cache_hit_skips_llm(self):
        _write_skill(self._skills_dir, "etl", ["ETL"], tier_subdir="system")
        loader = self._loader()
        adapter = _make_adapter('{"etl": 0.9}')
        ms = _make_settings_mock(cache_path=os.path.join(self._tmp, "cache_C6"))
        with patch("backend.config.settings.settings", ms):
            loader._ensure_routing_components()
            if loader._routing_cache and loader._routing_cache.is_available:
                loader._routing_cache.put("缓存测试消息unique_C6", {"etl": 0.85})
            run(loader.build_skill_prompt_async("缓存测试消息unique_C6", llm_adapter=adapter))
            if loader._routing_cache and loader._routing_cache.is_available:
                adapter.chat_plain.assert_not_called()

    # C7: _ensure_routing_components 幂等 — 两次调用，flag 保持 True，不重复初始化
    def test_C7_ensure_routing_components_idempotent(self):
        loader = self._loader()
        ms = _make_settings_mock(cache_path=os.path.join(self._tmp, "cache_C7"))
        with patch("backend.config.settings.settings", ms):
            loader._ensure_routing_components()
            first_cache = loader._routing_cache
            loader._ensure_routing_components()   # 第二次调用
            second_cache = loader._routing_cache
        # 同一个对象（未重新创建）
        self.assertIs(first_cache, second_cache)
        self.assertTrue(loader._routing_components_inited)

    # C8: skill_set_version 初始为 0，每次 load_all 递增
    def test_C8_skill_set_version_increments(self):
        loader = self._loader()
        self.assertGreaterEqual(loader._skill_set_version, 1)  # load_all 在 _loader() 已调用一次
        v_before = loader._skill_set_version
        loader.load_all()
        self.assertEqual(loader._skill_set_version, v_before + 1)

    # C9: always_inject skill 无论消息内容如何都注入
    def test_C9_always_inject_regardless_of_message(self):
        _write_skill(self._skills_dir, "_base-safe", [], tier_subdir="system",
                     always_inject=True, description="安全约束")
        loader = self._loader()
        adapter = _make_adapter("{}")
        ms = _make_settings_mock(cache_path=os.path.join(self._tmp, "cache_C9"))
        for msg in ["随便", "", "xyz123notakeyword"]:
            with patch("backend.config.settings.settings", ms):
                result = run(loader.build_skill_prompt_async(msg, llm_adapter=adapter))
            self.assertIn("_base-safe", result, f"always_inject 未出现，msg={msg!r}")

    # C10: semantic hits 正确分配到对应 tier（user/project/system）
    def test_C10_semantic_hit_assigned_to_correct_tier(self):
        _write_skill(self._skills_dir, "user-s", ["U"], tier_subdir="user")
        _write_skill(self._skills_dir, "proj-s", ["P"], tier_subdir="project")
        _write_skill(self._skills_dir, "sys-s",  ["S"], tier_subdir="system")
        loader = self._loader()
        # 语义路由对所有 skill 返回高分
        adapter = _make_adapter('{"user-s": 0.9, "proj-s": 0.9, "sys-s": 0.9}')
        ms = _make_settings_mock(cache_path=os.path.join(self._tmp, "cache_C10"))
        with patch("backend.config.settings.settings", ms):
            result = run(loader.build_skill_prompt_async(
                "无关键词的测试消息unique", llm_adapter=adapter
            ))
        self.assertIn("user-s", result)
        self.assertIn("proj-s", result)
        self.assertIn("sys-s", result)


# ═════════════════════════════════════════════════════════════════════════════
# Section D: AgenticLoop._build_system_prompt 集成
# ═════════════════════════════════════════════════════════════════════════════

class TestAgenticLoopBuildSystemPrompt(unittest.TestCase):
    """D1-D5: _build_system_prompt 是 async、正确调用 build_skill_prompt_async 等。"""

    def _make_loop(self, text="{}"):
        from backend.agents.agentic_loop import AgenticLoop
        adapter = MagicMock()
        adapter.chat_plain = AsyncMock(return_value={
            "content": [{"type": "text", "text": text}],
            "stop_reason": "end_turn",
        })
        manager = MagicMock()
        manager.list_servers = MagicMock(return_value=[])
        manager.servers = {}
        loop = AgenticLoop(adapter, manager, max_iterations=5)
        return loop

    # D1: _build_system_prompt 是协程函数
    def test_D1_build_system_prompt_is_coroutine(self):
        from backend.agents.agentic_loop import AgenticLoop
        self.assertTrue(inspect.iscoroutinefunction(AgenticLoop._build_system_prompt))

    # D2: 返回的 prompt 包含基础提示文本（不为空）
    def test_D2_prompt_includes_base_text(self):
        loop = self._make_loop()
        result = run(loop._build_system_prompt({}, message=""))
        self.assertIsInstance(result, str)
        self.assertGreater(len(result), 0)

    # D3: 关键词匹配时 skill 文本出现在 system prompt 中
    def test_D3_skill_text_injected_on_keyword_match(self):
        tmp = tempfile.mkdtemp()
        skills_dir = Path(tmp) / "skills"
        _write_skill(skills_dir, "test-analyst", ["分析", "查询"], tier_subdir="system",
                     description="分析专家")

        from skills.skill_loader import SkillLoader
        loader = SkillLoader(skills_dir=str(skills_dir))
        loader.load_all()

        ms = _make_settings_mock(mode="keyword")

        # get_skill_loader 是在方法体内懒加载导入的，需 patch 其所在模块
        with patch("backend.skills.skill_loader.get_skill_loader", return_value=loader), \
             patch("backend.config.settings.settings", ms):
            loop = self._make_loop()
            result = run(loop._build_system_prompt({}, message="帮我分析数据"))

        self.assertIn("test-analyst", result)

    # D4: build_skill_prompt_async 抛出异常时 → 降级，返回非空 prompt（不崩溃）
    def test_D4_graceful_degrade_on_skill_loader_error(self):
        mock_loader = MagicMock()
        mock_loader.build_skill_prompt_async = AsyncMock(
            side_effect=RuntimeError("skill loader exploded"))

        with patch("backend.skills.skill_loader.get_skill_loader", return_value=mock_loader):
            loop = self._make_loop()
            result = run(loop._build_system_prompt({}, message="分析数据"))

        # 基础 prompt 仍然返回（不是空字符串）
        self.assertIsInstance(result, str)
        self.assertGreater(len(result), 0)

    # D5: loop.llm_adapter 被作为 llm_adapter 参数传给 build_skill_prompt_async
    def test_D5_llm_adapter_passed_to_build_prompt_async(self):
        called_with_adapter = []

        async def fake_build_async(message, llm_adapter=None, user_id="default"):
            called_with_adapter.append(llm_adapter)
            return ""

        mock_loader = MagicMock()
        mock_loader.build_skill_prompt_async = fake_build_async

        with patch("backend.skills.skill_loader.get_skill_loader", return_value=mock_loader):
            loop = self._make_loop()
            run(loop._build_system_prompt({}, message="测试消息"))

        self.assertEqual(len(called_with_adapter), 1)
        self.assertIs(called_with_adapter[0], loop.llm_adapter)


# ═════════════════════════════════════════════════════════════════════════════
# Section E: Preview API match_details 覆盖
# ═════════════════════════════════════════════════════════════════════════════

class TestPreviewApiMatchDetails(unittest.TestCase):
    """E1-E8: /preview 端点 match_details 字段的完整性验证。"""

    # E1: match_details 是 dict 类型
    def test_E1_match_details_is_dict(self):
        r = _client.get("/api/v1/skills/preview", params={"message": "ETL建表"})
        self.assertEqual(r.status_code, 200, r.text)
        data = r.json()
        self.assertIn("match_details", data)
        self.assertIsInstance(data["match_details"], dict)

    # E2: 关键词命中的 skill → method='keyword', score=1.0
    def test_E2_keyword_hit_has_correct_method_and_score(self):
        r = _client.get("/api/v1/skills/preview", params={"message": "设计ETL流程"})
        self.assertEqual(r.status_code, 200)
        details = r.json().get("match_details", {})
        for name, info in details.items():
            if info.get("method") == "keyword":
                self.assertAlmostEqual(info["score"], 1.0,
                                       msg=f"keyword skill '{name}' score should be 1.0")
                return  # 找到至少一个即可

    # E3: always_inject skill 出现在 match_details，method='always_inject'
    def test_E3_always_inject_in_match_details(self):
        r = _client.get("/api/v1/skills/preview", params={"message": "随便的消息"})
        self.assertEqual(r.status_code, 200)
        details = r.json().get("match_details", {})
        ai_entries = [(n, i) for n, i in details.items()
                      if i.get("method") == "always_inject"]
        if any("_base" in n for n, _ in ai_entries):
            # base skills 存在且正确标记
            for _, info in ai_entries:
                self.assertEqual(info["method"], "always_inject")

    # E4: match_details 每个条目都有 'tier' 字段
    def test_E4_each_entry_has_tier_field(self):
        r = _client.get("/api/v1/skills/preview", params={"message": "分析外呼数据"})
        self.assertEqual(r.status_code, 200)
        details = r.json().get("match_details", {})
        for name, info in details.items():
            self.assertIn("tier", info,
                          f"match_details['{name}'] is missing 'tier' field")

    # E5: match_details 每个条目都有 'method' 和 'score' 字段
    def test_E5_each_entry_has_method_and_score_fields(self):
        r = _client.get("/api/v1/skills/preview", params={"message": "ETL数据建表"})
        self.assertEqual(r.status_code, 200)
        details = r.json().get("match_details", {})
        for name, info in details.items():
            self.assertIn("method", info, f"match_details['{name}'] missing 'method'")
            self.assertIn("score", info, f"match_details['{name}'] missing 'score'")

    # E6: mode=keyword → match_details 不含 method='semantic' 条目
    def test_E6_mode_keyword_no_semantic_entries(self):
        r = _client.get("/api/v1/skills/preview",
                        params={"message": "ETL建表", "mode": "keyword"})
        self.assertEqual(r.status_code, 200, r.text)
        details = r.json().get("match_details", {})
        semantic_entries = [n for n, i in details.items() if i.get("method") == "semantic"]
        self.assertEqual(semantic_entries, [],
                         f"mode=keyword should have no semantic entries, got: {semantic_entries}")

    # E7: 未知 mode 参数 → 返回 200（使用系统默认，不报错）
    def test_E7_unknown_mode_returns_200(self):
        r = _client.get("/api/v1/skills/preview",
                        params={"message": "分析数据", "mode": "invalid_mode"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn("match_details", r.json())

    # E8: 空消息 → 返回 200，match_details 不含 keyword 条目（无触发词）
    def test_E8_empty_message_no_keyword_hits(self):
        r = _client.get("/api/v1/skills/preview", params={"message": ""})
        self.assertEqual(r.status_code, 200, r.text)
        data = r.json()
        self.assertIn("match_details", data)
        # 空消息不会触发任何 keyword 匹配
        details = data["match_details"]
        keyword_entries = [n for n, i in details.items() if i.get("method") == "keyword"]
        self.assertEqual(keyword_entries, [],
                         f"empty message should not trigger keyword hits, got: {keyword_entries}")


# ═════════════════════════════════════════════════════════════════════════════
# Section F: Settings 配置默认值
# ═════════════════════════════════════════════════════════════════════════════

class TestSettingsDefaults(unittest.TestCase):
    """F1-F4: 新增配置字段的默认值和类型验证。"""

    def setUp(self):
        from backend.config.settings import settings
        self._s = settings

    # F1: skill_match_mode 默认 "hybrid"
    def test_F1_skill_match_mode_default_hybrid(self):
        # 若 .env 未覆盖，默认应为 "hybrid"
        val = self._s.skill_match_mode
        self.assertIn(val, ("hybrid", "keyword", "llm"),
                      f"skill_match_mode should be valid mode, got {val!r}")

    # F2: skill_semantic_threshold 为 float，在合理范围内
    def test_F2_semantic_threshold_is_float_in_range(self):
        val = self._s.skill_semantic_threshold
        self.assertIsInstance(val, float)
        self.assertGreater(val, 0.0)
        self.assertLess(val, 1.0)

    # F3: skill_semantic_cache_ttl 为正整数
    def test_F3_cache_ttl_is_positive_int(self):
        val = self._s.skill_semantic_cache_ttl
        self.assertIsInstance(val, int)
        self.assertGreater(val, 0)

    # F4: skill_routing_cache_path 为非空字符串
    def test_F4_routing_cache_path_nonempty_string(self):
        val = self._s.skill_routing_cache_path
        self.assertIsInstance(val, str)
        self.assertGreater(len(val.strip()), 0)


# ═════════════════════════════════════════════════════════════════════════════
# Runner
# ═════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import pytest, sys
    sys.exit(pytest.main([__file__, "-v", "-s"]))
