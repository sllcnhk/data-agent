"""
T1: SkillSemanticRouter 单元测试（15 用例）
==========================================
全部使用 mock，不调用真实 LLM。
"""
import asyncio
import json
import sys
import types
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, "backend")

# ---------------------------------------------------------------------------
# 最小化 SkillMD stub（避免依赖完整 skill_loader）
# ---------------------------------------------------------------------------
class _SkillMD:
    def __init__(self, name, description="", triggers=None):
        self.name = name
        self.description = description
        self.triggers = triggers or []


def _make_adapter(response_text: str, raises=None):
    """创建返回固定文本的 mock llm_adapter。"""
    adapter = MagicMock()
    if raises:
        adapter.chat_plain = AsyncMock(side_effect=raises)
    else:
        adapter.chat_plain = AsyncMock(return_value={
            "content": [{"type": "text", "text": response_text}],
            "stop_reason": "end_turn",
        })
    return adapter


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


class TestSkillSemanticRouter(unittest.TestCase):

    def setUp(self):
        from skills.skill_semantic_router import SkillSemanticRouter
        self.router = SkillSemanticRouter()
        self.skills = [
            _SkillMD("clickhouse-analyst", "ClickHouse数据分析", ["分析", "查询", "外呼"]),
            _SkillMD("etl-engineer", "ETL流程设计", ["ETL", "建表", "宽表"]),
            _SkillMD("schema-explorer", "数据库结构探索", ["表结构", "字段", "schema"]),
        ]

    # T1-1: 正常路由 — mock LLM 返回合法 JSON，验证解析结果
    def test_normal_routing_returns_parsed_scores(self):
        adapter = _make_adapter('{"clickhouse-analyst": 0.9, "schema-explorer": 0.5}')
        result = run(self.router.route("分析外呼数据", self.skills, adapter))
        self.assertAlmostEqual(result["clickhouse-analyst"], 0.9)
        self.assertAlmostEqual(result["schema-explorer"], 0.5)
        self.assertNotIn("etl-engineer", result)

    # T1-2: JSON 被 markdown 代码块包裹，验证正则提取仍能工作
    def test_json_wrapped_in_markdown_code_block(self):
        text = '```json\n{"clickhouse-analyst": 0.85}\n```'
        adapter = _make_adapter(text)
        result = run(self.router.route("看看数据", self.skills, adapter))
        self.assertIn("clickhouse-analyst", result)
        self.assertAlmostEqual(result["clickhouse-analyst"], 0.85)

    # T1-3: LLM 返回非 JSON 文本 → 返回 {}
    def test_non_json_response_returns_empty(self):
        adapter = _make_adapter("抱歉，我无法理解这个请求。")
        result = run(self.router.route("你好", self.skills, adapter))
        self.assertEqual(result, {})

    # T1-4: LLM 抛出异常 → 返回 {}，不传播异常
    def test_llm_exception_returns_empty_dict(self):
        adapter = _make_adapter("", raises=RuntimeError("API timeout"))
        result = run(self.router.route("分析数据", self.skills, adapter))
        self.assertEqual(result, {})

    # T1-5: 空候选 skill 列表 → 返回 {}，不调用 LLM
    def test_empty_candidate_skills_returns_empty(self):
        adapter = _make_adapter('{"anything": 0.9}')
        result = run(self.router.route("消息", [], adapter))
        self.assertEqual(result, {})
        adapter.chat_plain.assert_not_called()

    # T1-6: skill_list_text 格式验证
    def test_skill_list_text_format(self):
        from skills.skill_semantic_router import _skill_list_text
        text = _skill_list_text(self.skills)
        self.assertIn("clickhouse-analyst", text)
        self.assertIn("etl-engineer", text)
        # 格式：name: description | trigger1, trigger2
        self.assertIn("|", text)
        self.assertIn("分析", text)

    # T1-7: max_tokens 参数传给 chat_plain
    def test_max_tokens_passed_to_adapter(self):
        adapter = _make_adapter('{}')
        run(self.router.route("测试", self.skills, adapter))
        call_kwargs = adapter.chat_plain.call_args
        # max_tokens 应当以关键字参数传入
        self.assertIn("max_tokens", call_kwargs.kwargs)

    # T1-8: score < 0.3 的 skill 被过滤，不出现在结果中（LLM 返回了但我们的过滤由调用方做）
    #        router 本身返回原始分数，过滤由 SkillLoader 按 threshold 决定
    def test_low_score_still_returned_raw(self):
        adapter = _make_adapter('{"clickhouse-analyst": 0.1}')
        result = run(self.router.route("测试", self.skills, adapter))
        # router 层面不过滤，返回原始分数（阈值过滤在 SkillLoader 层）
        self.assertIn("clickhouse-analyst", result)
        self.assertAlmostEqual(result["clickhouse-analyst"], 0.1)

    # T1-9: 中文消息多语言场景
    def test_chinese_message_routing(self):
        adapter = _make_adapter('{"clickhouse-analyst": 0.92}')
        result = run(self.router.route("帮我统计上周各地区的外呼接通率", self.skills, adapter))
        self.assertIn("clickhouse-analyst", result)

    # T1-10: 多 skill 同时命中，scores 均返回
    def test_multiple_skills_all_returned(self):
        adapter = _make_adapter(
            '{"clickhouse-analyst": 0.9, "schema-explorer": 0.6, "etl-engineer": 0.4}'
        )
        result = run(self.router.route("设计ETL并分析结果", self.skills, adapter))
        self.assertEqual(len(result), 3)

    # T1-11: chat_plain 仅被调用一次（批量调用，非逐 skill）
    def test_llm_called_exactly_once(self):
        adapter = _make_adapter('{}')
        run(self.router.route("消息", self.skills, adapter))
        adapter.chat_plain.assert_called_once()

    # T1-12: 超长消息被截断，不超过 _MAX_MESSAGE_CHARS + "..."
    def test_long_message_is_truncated(self):
        from skills.skill_semantic_router import _MAX_MESSAGE_CHARS
        long_msg = "a" * (_MAX_MESSAGE_CHARS + 500)
        adapter = _make_adapter('{}')
        run(self.router.route(long_msg, self.skills, adapter))
        call_args = adapter.chat_plain.call_args
        prompt = call_args.kwargs["messages"][0]["content"]
        # 消息文本应出现在 prompt 中且被截断
        self.assertIn("...", prompt)

    # T1-13: skill description 含特殊字符不崩溃
    def test_special_chars_in_skill_description(self):
        special_skill = _SkillMD(
            "special", 'desc with "quotes" & <html> chars', ["触发词"]
        )
        adapter = _make_adapter('{}')
        result = run(self.router.route("测试", [special_skill], adapter))
        self.assertIsInstance(result, dict)

    # T1-14: llm_adapter=None 时返回 {}，不崩溃
    def test_none_adapter_returns_empty(self):
        result = run(self.router.route("分析", self.skills, None))
        self.assertEqual(result, {})

    # T1-15: LLM 返回幻觉 skill（不在候选列表），被过滤掉
    def test_hallucinated_skill_filtered_out(self):
        adapter = _make_adapter('{"nonexistent-skill": 0.95, "clickhouse-analyst": 0.8}')
        result = run(self.router.route("分析数据", self.skills, adapter))
        self.assertNotIn("nonexistent-skill", result)
        self.assertIn("clickhouse-analyst", result)


if __name__ == "__main__":
    import pytest, sys
    sys.exit(pytest.main([__file__, "-v", "-s"]))
