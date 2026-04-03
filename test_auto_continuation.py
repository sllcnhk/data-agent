"""
test_auto_continuation.py
测试 AgenticLoop 自动续写（auto-continuation）全场景：
  - 正常 end_turn 不受影响
  - 单次/多次 max_tokens 截断后正确拼接
  - 超出 MAX_CONTINUATION 上限时安全截断
  - 孤立 tool_use 块被过滤（防 API 400）
  - 仅有 tool_use 无 text 时的占位块
  - run() 非流式接口也正确组装
  - 对话历史在续写后得到保留
  - unknown stop_reason 分支与 accumulated_text 联动
  - 异常抛出时产生 error 事件
  - tool_use 迭代后命中 max_tokens 的完整场景
  - continuation 事件不出现在正常流程中
"""
import asyncio
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "backend"))

from backend.agents.agentic_loop import (
    AgenticLoop, AgentEvent, AgenticResult, MAX_CONTINUATION,
    MAX_LOOP_CONTEXT_CHARS, KEEP_RECENT_TOOL_PAIRS,
)


# ──────────────────────────────────────────────────────────
# Mock 依赖
# ──────────────────────────────────────────────────────────

class MockMCPManager:
    """MCP 管理器桩：无工具。"""
    servers = {}

    def list_servers(self):
        return []


class MockMCPManagerWithCallTool:
    """MCP 管理器桩：带一个可调用工具，用于模拟 tool_use 流程。"""
    class _FakeTool:
        name = "query"
        description = "Execute a query"
        input_schema = {"type": "object", "properties": {}}

    class _FakeServer:
        pass

    # format_mcp_tools_for_claude 会遍历 server.tools.items()，值需有 .description/.input_schema
    _fake_tool = _FakeTool()
    _fake_server = _FakeServer()
    _fake_server.tools = {"query": _fake_tool}
    servers = {"fake_ch": _fake_server}

    def list_servers(self):
        return [{"name": "fake_ch", "type": "clickhouse", "tool_count": 1}]

    async def call_tool(self, server_name, tool_name, tool_input):
        return {"success": True, "data": "42 rows"}


def _text_block(text: str) -> dict:
    return {"type": "text", "text": text}


def _tool_use_block(name="filesystem__write_file", tid="toolu_01"):
    return {
        "type": "tool_use",
        "id": tid,
        "name": name,
        "input": {"path": "backend/skills/test.md", "content": "..."},
    }


class MockLLMAdapter:
    """按 responses 列表顺序返回响应；耗尽后 raise RuntimeError。"""

    def __init__(self, responses: list):
        self._responses = list(responses)
        self.call_count = 0
        self.received_messages = []  # 记录每次调用收到的 messages

    async def chat_plain(self, messages, system_prompt, **kwargs) -> dict:
        if not self._responses:
            raise RuntimeError("MockLLMAdapter: unexpected extra call")
        self.call_count += 1
        self.received_messages.append(list(messages))
        return self._responses.pop(0)

    async def chat_with_tools(self, messages, system_prompt, tools, **kwargs) -> dict:
        return await self.chat_plain(messages, system_prompt, **kwargs)


def _make_loop(responses, mcp_manager=None):
    adapter = MockLLMAdapter(responses)
    mgr = mcp_manager or MockMCPManager()
    loop = AgenticLoop(llm_adapter=adapter, mcp_manager=mgr)
    return loop, adapter


async def collect_events(loop, message="hi", history=None):
    context = {"history": history or [], "system_prompt": ""}
    events = []
    async for ev in loop.run_streaming(message, context):
        events.append(ev)
    return events


# ──────────────────────────────────────────────────────────
# 基础场景
# ──────────────────────────────────────────────────────────

async def test_normal_end_turn():
    """正常 end_turn，不触发续写，无 continuation 事件。"""
    loop, adapter = _make_loop([
        {"stop_reason": "end_turn", "content": [_text_block("Hello World")]}
    ])
    events = await collect_events(loop)

    content_events = [e for e in events if e.type == "content"]
    cont_events    = [e for e in events if e.type == "continuation"]
    assert len(content_events) == 1
    assert content_events[0].data == "Hello World"
    assert len(cont_events) == 0, "正常流程不应产生 continuation 事件"
    assert adapter.call_count == 1
    print("[PASS] test_normal_end_turn")


async def test_single_continuation():
    """单次 max_tokens → end_turn，内容正确拼接。"""
    loop, adapter = _make_loop([
        {"stop_reason": "max_tokens", "content": [_text_block("Part 1 ")]},
        {"stop_reason": "end_turn",   "content": [_text_block("Part 2")]},
    ])
    events = await collect_events(loop)

    cont    = [e for e in events if e.type == "continuation"]
    content = [e for e in events if e.type == "content"]
    assert len(cont) == 1
    assert len(content) == 1
    assert content[0].data == "Part 1 Part 2"
    assert adapter.call_count == 2
    print("[PASS] test_single_continuation")


async def test_multiple_continuations():
    """多次 max_tokens，所有片段正确拼接，metadata 记录次数。"""
    loop, adapter = _make_loop([
        {"stop_reason": "max_tokens", "content": [_text_block("A")]},
        {"stop_reason": "max_tokens", "content": [_text_block("B")]},
        {"stop_reason": "max_tokens", "content": [_text_block("C")]},
        {"stop_reason": "end_turn",   "content": [_text_block("D")]},
    ])
    events = await collect_events(loop)

    content = [e for e in events if e.type == "content"]
    assert len(content) == 1
    assert content[0].data == "ABCD"
    assert content[0].metadata.get("continuation_count") == 3
    assert adapter.call_count == 4
    print("[PASS] test_multiple_continuations")


# ──────────────────────────────────────────────────────────
# 上限与截断
# ──────────────────────────────────────────────────────────

async def test_continuation_limit():
    """超出 MAX_CONTINUATION，停止续写，返回已积累内容并标记 truncated。"""
    responses = [
        {"stop_reason": "max_tokens", "content": [_text_block(f"P{i}")]}
        for i in range(MAX_CONTINUATION + 2)
    ]
    loop, _ = _make_loop(responses)
    events = await collect_events(loop)

    content = [e for e in events if e.type == "content"]
    assert len(content) == 1
    assert len(content[0].data) > 0
    assert content[0].metadata.get("truncated") is True
    print(f"[PASS] test_continuation_limit  accumulated={repr(content[0].data[:30])}")


async def test_continuation_metadata():
    """continuation 事件的 data 和 metadata 字段格式正确。"""
    loop, _ = _make_loop([
        {"stop_reason": "max_tokens", "content": [_text_block("X" * 100)]},
        {"stop_reason": "end_turn",   "content": [_text_block("Y")]},
    ])
    events = await collect_events(loop)

    cont = [e for e in events if e.type == "continuation"]
    assert len(cont) == 1
    assert cont[0].data["count"] == 1
    assert "正在继续生成" in cont[0].data["message"]
    assert cont[0].metadata["accumulated_len"] == 100
    print("[PASS] test_continuation_metadata")


# ──────────────────────────────────────────────────────────
# Messages 结构正确性
# ──────────────────────────────────────────────────────────

async def test_messages_contain_continuation_turn():
    """续写时，messages 末尾为 assistant(text) + user('请继续')。"""
    loop, adapter = _make_loop([
        {"stop_reason": "max_tokens", "content": [_text_block("Part 1")]},
        {"stop_reason": "end_turn",   "content": [_text_block("Part 2")]},
    ])
    await collect_events(loop, message="test message")

    first  = adapter.received_messages[0]
    second = adapter.received_messages[1]

    assert first[-1]["role"] == "user"
    assert first[-1]["content"] == "test message"

    roles = [m["role"] for m in second]
    assert "assistant" in roles
    assert second[-1]["role"] == "user"
    assert second[-1]["content"] == "请继续"
    print("[PASS] test_messages_contain_continuation_turn")


async def test_history_preserved_through_continuation():
    """对话历史在续写过程中不丢失。"""
    history = [
        {"role": "user",      "content": "先前问题"},
        {"role": "assistant", "content": "先前回答"},
    ]
    loop, adapter = _make_loop([
        {"stop_reason": "max_tokens", "content": [_text_block("新回复前半")]},
        {"stop_reason": "end_turn",   "content": [_text_block("新回复后半")]},
    ])
    await collect_events(loop, message="新问题", history=history)

    # 两次调用都应包含历史消息
    for call_msgs in adapter.received_messages:
        contents = [m["content"] for m in call_msgs]
        assert "先前问题" in contents, f"历史消息丢失，实际: {contents}"
        assert "先前回答" in contents
    print("[PASS] test_history_preserved_through_continuation")


# ──────────────────────────────────────────────────────────
# 孤立 tool_use 块过滤（本次修复核心）
# ──────────────────────────────────────────────────────────

async def test_empty_content_blocks():
    """max_tokens 且 content 为空时，不崩溃，续写后返回正确内容。"""
    loop, _ = _make_loop([
        {"stop_reason": "max_tokens", "content": []},
        {"stop_reason": "end_turn",   "content": [_text_block("Final")]},
    ])
    events = await collect_events(loop)

    content = [e for e in events if e.type == "content"]
    assert len(content) == 1
    assert content[0].data == "Final"
    print("[PASS] test_empty_content_blocks")


async def test_orphan_tool_use_filtered():
    """max_tokens 时含不完整 tool_use 块，续写 messages 中必须过滤掉。

    此场景正是日志中 HTTP 400 的根因：
      LLM 正在生成 write_file 调用被 max_tokens 截断，
      content_blocks = [text_block, tool_use_block(incomplete)]
    """
    loop, adapter = _make_loop([
        {"stop_reason": "max_tokens", "content": [
            _text_block("我来写文件..."),
            _tool_use_block(),
        ]},
        {"stop_reason": "end_turn", "content": [_text_block("文件写完了")]},
    ])
    events = await collect_events(loop, message="写个文件")

    # 续写 messages 不含 tool_use 块
    second_msgs = adapter.received_messages[1]
    for msg in second_msgs:
        c = msg.get("content", "")
        if isinstance(c, list):
            for blk in c:
                assert blk.get("type") != "tool_use", \
                    f"续写 messages 中不应有 tool_use 块: {blk}"

    content = [e for e in events if e.type == "content"]
    assert "我来写文件" in content[0].data
    assert "文件写完了" in content[0].data

    cont = [e for e in events if e.type == "continuation"]
    assert cont[0].metadata.get("has_orphan_tool_use") is True
    print("[PASS] test_orphan_tool_use_filtered")


async def test_only_orphan_tool_use_no_text():
    """max_tokens 时 content 仅有 tool_use 块（无 text），
    safe_blocks 为空，应补充占位 text 块防止 Anthropic 拒绝空 content。"""
    loop, adapter = _make_loop([
        {"stop_reason": "max_tokens", "content": [_tool_use_block()]},
        {"stop_reason": "end_turn",   "content": [_text_block("Done")]},
    ])
    events = await collect_events(loop)

    # 续写 messages 中 assistant 消息 content 不能为空列表
    second_msgs = adapter.received_messages[1]
    for msg in second_msgs:
        if msg.get("role") == "assistant":
            c = msg.get("content", [])
            assert len(c) > 0, "assistant content 不能为空"
            assert all(b.get("type") != "tool_use" for b in c if isinstance(b, dict)), \
                "不能含 tool_use 块"
            # 应有占位 text 块
            texts = [b for b in c if isinstance(b, dict) and b.get("type") == "text"]
            assert len(texts) >= 1, "应补充占位 text 块"

    content = [e for e in events if e.type == "content"]
    assert len(content) == 1
    assert "Done" in content[0].data
    print("[PASS] test_only_orphan_tool_use_no_text")


# ──────────────────────────────────────────────────────────
# unknown stop_reason & 异常
# ──────────────────────────────────────────────────────────

async def test_unknown_stop_reason_with_accumulated():
    """unknown stop_reason 时，已积累的 accumulated_text 也应拼接到最终内容。"""
    loop, _ = _make_loop([
        {"stop_reason": "max_tokens",    "content": [_text_block("前半")]},
        {"stop_reason": "some_new_reason", "content": [_text_block("后半")]},
    ])
    events = await collect_events(loop)

    content = [e for e in events if e.type == "content"]
    assert len(content) == 1
    assert "前半" in content[0].data
    assert "后半" in content[0].data
    print("[PASS] test_unknown_stop_reason_with_accumulated")


async def test_exception_yields_error_event():
    """LLM 调用抛出异常时，产生 error 事件，循环停止。"""
    class BrokenAdapter:
        async def chat_plain(self, *a, **kw):
            raise ConnectionError("网络超时")
        async def chat_with_tools(self, *a, **kw):
            raise ConnectionError("网络超时")

    loop = AgenticLoop(llm_adapter=BrokenAdapter(), mcp_manager=MockMCPManager())
    events = await collect_events(loop)

    error_events = [e for e in events if e.type == "error"]
    assert len(error_events) >= 1
    assert "网络超时" in error_events[0].data
    # 发生异常后应停止，不会继续迭代
    assert not any(e.type == "content" for e in events)
    print("[PASS] test_exception_yields_error_event")


# ──────────────────────────────────────────────────────────
# 非流式 run() 接口
# ──────────────────────────────────────────────────────────

async def test_run_method_with_continuation():
    """非流式 run() 方法也能正确组装续写内容。"""
    loop, _ = _make_loop([
        {"stop_reason": "max_tokens", "content": [_text_block("首段")]},
        {"stop_reason": "end_turn",   "content": [_text_block("末段")]},
    ])
    context = {"history": [], "system_prompt": ""}
    result: AgenticResult = await loop.run("test", context)

    assert result.success is True
    assert "首段" in result.content
    assert "末段" in result.content
    # events 列表包含 continuation 事件
    cont = [e for e in result.events if e.type == "continuation"]
    assert len(cont) == 1
    print("[PASS] test_run_method_with_continuation")


async def test_run_method_normal():
    """非流式 run() 正常 end_turn，content 字段正确。"""
    loop, _ = _make_loop([
        {"stop_reason": "end_turn", "content": [_text_block("正常回答")]},
    ])
    context = {"history": [], "system_prompt": ""}
    result = await loop.run("query", context)
    assert result.success is True
    assert result.content == "正常回答"
    print("[PASS] test_run_method_normal")


# ──────────────────────────────────────────────────────────
# tool_use 迭代 + max_tokens 混合场景
# ──────────────────────────────────────────────────────────

async def test_max_tokens_after_tool_use_cycle():
    """真实场景：LLM 先调用工具，工具返回后回复中途命中 max_tokens，
    续写完成后内容正确。不得产生 API 400 错误。"""
    mgr = MockMCPManagerWithCallTool()
    responses = [
        # 第1次：调用工具
        {
            "stop_reason": "tool_use",
            "content": [
                _text_block("查询数据..."),
                {"type": "tool_use", "id": "t1", "name": "fake_ch__query", "input": {"query": "SELECT 1"}},
            ],
        },
        # 第2次（tool_result 后）：回复中途 max_tokens
        {"stop_reason": "max_tokens", "content": [_text_block("结果是")]},
        # 第3次（续写）：end_turn
        {"stop_reason": "end_turn",   "content": [_text_block(" 42 行数据。")]},
    ]
    loop, adapter = _make_loop(responses, mcp_manager=mgr)
    events = await collect_events(loop)

    error_events = [e for e in events if e.type == "error"]
    assert len(error_events) == 0, f"不应有错误事件: {[e.data for e in error_events]}"

    content = [e for e in events if e.type == "content"]
    assert len(content) == 1
    assert "结果是" in content[0].data
    assert "42 行数据" in content[0].data

    # 第3次调用的 messages 不含孤立 tool_use 块
    third_msgs = adapter.received_messages[2]
    for msg in third_msgs:
        c = msg.get("content", "")
        if isinstance(c, list):
            for blk in c:
                if isinstance(blk, dict) and blk.get("type") == "tool_use":
                    # 正常 tool_use（第1轮的 tool call）必须紧跟 tool_result
                    # 通过检查下一条消息来确认
                    idx = third_msgs.index(msg)
                    if idx + 1 < len(third_msgs):
                        next_msg = third_msgs[idx + 1]
                        next_c = next_msg.get("content", [])
                        has_result = any(
                            isinstance(b, dict) and b.get("type") == "tool_result"
                            for b in (next_c if isinstance(next_c, list) else [])
                        )
                        assert has_result, (
                            f"tool_use 之后必须有 tool_result: {msg}"
                        )
    print("[PASS] test_max_tokens_after_tool_use_cycle")


async def test_stagnation_detection_yields_error():
    """连续调用完全相同的工具 MAX_STAGNANT 次后，应触发停滞检测并产生 error 事件。"""
    responses = [
        {"stop_reason": "tool_use", "content": [
            {"type": "tool_use", "id": f"t{i}", "name": "fake_ch__query",
             "input": {"query": "SELECT 1"}},   # 完全相同的 name + input
        ]}
        for i in range(5)
    ]
    adapter = MockLLMAdapter(responses)
    loop = AgenticLoop(
        llm_adapter=adapter,
        mcp_manager=MockMCPManagerWithCallTool(),
        max_iterations=10,
    )
    events = await collect_events(loop)

    error_events = [e for e in events if e.type == "error"]
    content_events = [e for e in events if e.type == "content"]

    assert len(error_events) >= 1, f"应有 error 事件，得到: {[e.type for e in events]}"
    assert len(content_events) == 0, "停滞时无文本，不应有 content 事件"
    assert error_events[0].metadata.get("stagnation") is True, \
        f"error 事件应标记 stagnation=True，实际 metadata={error_events[0].metadata}"
    # 停滞检测应在 max_iterations 之前触发（节省配额）
    assert adapter.call_count < 10, \
        f"停滞应提前终止，实际调用 {adapter.call_count} 次"
    print("[PASS] test_stagnation_detection_yields_error")


async def test_stagnation_resets_on_different_call():
    """A→A→B→A→A→A 序列：B 调用重置计数器，第二个连续 A→A→A 才触发停滞。
    使用 max_iterations=20（远高于 NEAR_LIMIT_THRESHOLD=5），
    确保近限制综合模式不会在停滞检测之前触发。"""
    def _r(query, tid):
        return {"stop_reason": "tool_use", "content": [
            {"type": "tool_use", "id": tid, "name": "fake_ch__query",
             "input": {"query": query}},
        ]}

    responses = [
        _r("SELECT 1", "t1"),   # A → 设置 last_sigs (iteration 1, remaining=19)
        _r("SELECT 1", "t2"),   # A → stagnant=1（不触发）  (iteration 2)
        _r("SELECT 2", "t3"),   # B → 重置 stagnant=0       (iteration 3)
        _r("SELECT 1", "t4"),   # A → 设置 last_sigs        (iteration 4)
        _r("SELECT 1", "t5"),   # A → stagnant=1（不触发）  (iteration 5)
        _r("SELECT 1", "t6"),   # A → stagnant=2 → 触发停滞 (iteration 6)
        _r("SELECT 1", "t7"),   # 不应到达
    ]
    adapter = MockLLMAdapter(responses)
    loop = AgenticLoop(
        llm_adapter=adapter,
        mcp_manager=MockMCPManagerWithCallTool(),
        max_iterations=20,   # 高于 NEAR_LIMIT_THRESHOLD，避免近限制提前介入
    )
    events = await collect_events(loop)

    error_events = [e for e in events if e.type == "error"]
    assert len(error_events) >= 1, "B 之后再连续 A→A→A 应触发停滞"
    assert error_events[0].metadata.get("stagnation") is True
    # 应在第 6 次 LLM 调用后停止，不到第 7 次
    assert adapter.call_count <= 6, \
        f"停滞检测应在第 6 轮触发，实际调用 {adapter.call_count} 次"
    print("[PASS] test_stagnation_resets_on_different_call")


async def test_max_iterations_exceeded_no_content_yields_error():
    """所有迭代均为 tool_use（无文本输出），耗尽 max_iterations 后应产生 content 或 error 事件。

    注意：引入近限制综合模式（NEAR_LIMIT_THRESHOLD=5）后，当 max_iterations <= 5 时，
    近限制在第一次 tool_use 即触发综合模式，产生 content 事件而非 error。
    此测试验证：最终一定有 content 或 error 事件（任一即可），循环不会静默结束。
    """
    responses = [
        {"stop_reason": "tool_use", "content": [
            {"type": "tool_use", "id": f"t{i}", "name": "fake_ch__query",
             "input": {"query": f"SELECT {i}"}},  # 不同 SQL，避免触发停滞检测
        ]}
        for i in range(20)  # 远超 max_iterations
    ]
    adapter = MockLLMAdapter(responses)
    loop = AgenticLoop(
        llm_adapter=adapter,
        mcp_manager=MockMCPManagerWithCallTool(),
        max_iterations=3,  # 刻意设小（< NEAR_LIMIT_THRESHOLD）
    )
    events = await collect_events(loop)

    error_events = [e for e in events if e.type == "error"]
    content_events = [e for e in events if e.type == "content"]

    # 近限制特性：max_iterations=3 时，iteration 1 的 remaining=2 <= 5，
    # 触发综合模式，产生 content（而非 error）
    assert len(error_events) >= 1 or len(content_events) >= 1, \
        f"应有 error 或 content 事件，得到: {[e.type for e in events]}"
    print("[PASS] test_max_iterations_exceeded_no_content_yields_error")


async def test_max_iterations_exceeded_with_accumulated_yields_content():
    """先有 max_tokens 积累了文本，再因 tool_use 耗尽 max_iterations，应返回已积累内容而非错误。"""
    responses = [
        # 第1轮：max_tokens 截断，产生部分文本
        {"stop_reason": "max_tokens", "content": [{"type": "text", "text": "已完成分析，"}]},
        # 第2轮（续写）：进入 tool_use 循环
        {"stop_reason": "tool_use", "content": [
            {"type": "tool_use", "id": "t1", "name": "fake_ch__query",
             "input": {"query": "SELECT 1"}},
        ]},
        # 第3轮（tool_result 后）：又是 tool_use，耗尽 max_iterations
        {"stop_reason": "tool_use", "content": [
            {"type": "tool_use", "id": "t2", "name": "fake_ch__query",
             "input": {"query": "SELECT 2"}},
        ]},
    ]
    adapter = MockLLMAdapter(responses)
    loop = AgenticLoop(
        llm_adapter=adapter,
        mcp_manager=MockMCPManagerWithCallTool(),
        max_iterations=3,
    )
    events = await collect_events(loop)

    error_events = [e for e in events if e.type == "error"]
    content_events = [e for e in events if e.type == "content"]

    assert len(error_events) == 0, f"有已积累文本时不应产生 error 事件，得到: {[e.data for e in error_events]}"
    assert len(content_events) >= 1, f"应有 content 事件，得到 {len(content_events)}"
    assert "已完成分析" in content_events[0].data, \
        f"content 应包含积累的文本，实际: {content_events[0].data}"
    # 近限制特性：max_iterations=3 时，iteration 2 的 remaining=1 <= 5，
    # 触发近限制综合模式，metadata 为 near_limit=True（而非 exceeded_max=True）
    meta = content_events[0].metadata
    assert meta.get("exceeded_max") is True or meta.get("near_limit") is True, \
        f"content 应标记 exceeded_max 或 near_limit，实际: {meta}"
    print("[PASS] test_max_iterations_exceeded_with_accumulated_yields_content")


async def test_loop_compression_triggered_on_large_context():
    """当 loop 内累积消息超过 MAX_LOOP_CONTEXT_CHARS 时应压缩旧 tool_result。

    构造一个 messages 列表，其中包含超过阈值的 tool_result 内容，
    调用 _compress_loop_messages 后旧结果内容应被替换为单行摘要。
    """
    import json

    # 构造一条包含超大 tool_result 的 user 消息
    big_content = "X" * (MAX_LOOP_CONTEXT_CHARS // 2 + 1000)
    messages = []

    # 超过 KEEP_RECENT_TOOL_PAIRS + 2 条 tool_result 消息，以便有内容被压缩
    for i in range(KEEP_RECENT_TOOL_PAIRS + 2):
        messages.append({
            "role": "assistant",
            "content": [{"type": "tool_use", "id": f"t{i}", "name": "q", "input": {}}],
        })
        messages.append({
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": f"t{i}",
                         "content": big_content}],
        })

    compressed = AgenticLoop._compress_loop_messages(messages)

    # 找出所有 tool_result 块
    all_results = []
    for m in compressed:
        if isinstance(m.get("content"), list):
            for b in m["content"]:
                if isinstance(b, dict) and b.get("type") == "tool_result":
                    all_results.append(b)

    # 最旧的应被压缩
    old_results = all_results[:len(all_results) - KEEP_RECENT_TOOL_PAIRS]
    for r in old_results:
        assert "[历史结果已压缩]" in r["content"], (
            f"旧 tool_result 应被压缩，实际内容: {r['content'][:100]}"
        )

    # 最近的 KEEP_RECENT_TOOL_PAIRS 应保留原样
    recent_results = all_results[-KEEP_RECENT_TOOL_PAIRS:]
    for r in recent_results:
        assert r["content"] == big_content, "最近的 tool_result 不应被压缩"

    print("[PASS] test_loop_compression_triggered_on_large_context")


async def test_loop_compression_keeps_recent_pairs_verbatim():
    """当消息总量不足压缩阈值时，_compress_loop_messages 应原样返回。"""
    messages = [
        {"role": "assistant", "content": [{"type": "tool_use", "id": "t1", "name": "q", "input": {}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "small"}]},
    ]
    result = AgenticLoop._compress_loop_messages(messages)
    assert result is messages or result == messages, \
        "短消息列表不应被压缩，应原样返回"
    print("[PASS] test_loop_compression_keeps_recent_pairs_verbatim")


# ──────────────────────────────────────────────────────────
# 入口
# ──────────────────────────────────────────────────────────

async def run_all():
    tests = [
        test_normal_end_turn,
        test_single_continuation,
        test_multiple_continuations,
        test_continuation_limit,
        test_continuation_metadata,
        test_messages_contain_continuation_turn,
        test_history_preserved_through_continuation,
        test_empty_content_blocks,
        test_orphan_tool_use_filtered,
        test_only_orphan_tool_use_no_text,
        test_unknown_stop_reason_with_accumulated,
        test_exception_yields_error_event,
        test_run_method_with_continuation,
        test_run_method_normal,
        test_max_tokens_after_tool_use_cycle,
        test_stagnation_detection_yields_error,
        test_stagnation_resets_on_different_call,
        test_max_iterations_exceeded_no_content_yields_error,
        test_max_iterations_exceeded_with_accumulated_yields_content,
        test_loop_compression_triggered_on_large_context,
        test_loop_compression_keeps_recent_pairs_verbatim,
    ]
    passed = failed = 0
    for t in tests:
        try:
            await t()
            passed += 1
        except Exception as e:
            print(f"[FAIL] {t.__name__}: {e}")
            import traceback
            traceback.print_exc()
            failed += 1

    print(f"\n{'='*50}")
    print(f"结果: {passed} 通过, {failed} 失败 / 共 {len(tests)} 个测试")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    import sys, atexit
    import pathlib as _pl
    sys.path.insert(0, str(_pl.Path(__file__).parent))
    try:
        from conftest import _cleanup_test_data as _ctd
        atexit.register(_ctd, label="post-run")   # 进程退出时必然执行（含 sys.exit）
        _ctd(label="pre-run")
    except Exception:
        pass
    asyncio.run(run_all())
