"""
test_etl_continuation.py
验证 ETLAgenticLoop（继承 AgenticLoop）对自动续写功能的传播：
  - max_tokens 触发的 continuation 事件能透传出来
  - 续写后 content 正确组装
  - 孤立 tool_use 块不会传播到续写 messages（防 API 400）
  - ETL 的 SQL 危险检测与续写逻辑共存不冲突
  - 非流式 process() 接口也能得到完整续写内容
"""
import asyncio
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "backend"))

from backend.agents.agentic_loop import AgentEvent, MAX_CONTINUATION
from backend.agents.etl_agent import ETLAgenticLoop, ETLEngineerAgent


# ──────────────────────────────────────────────────────────
# Mock 依赖
# ──────────────────────────────────────────────────────────

class MockMCPManager:
    servers = {}
    def list_servers(self):
        return []


class MockMCPManagerWithTool:
    class _Tool:
        name = "query"
        description = "Run SQL"
        input_schema = {"type": "object", "properties": {}}

    class _Server:
        pass

    _t = _Tool()
    _s = _Server()
    _s.tools = {"query": _t}
    servers = {"ch": _s}

    def list_servers(self):
        return [{"name": "ch", "type": "clickhouse", "tool_count": 1}]

    async def call_tool(self, server, tool, args):
        return {"success": True, "data": "rows=0"}


class MockLLMAdapter:
    def __init__(self, responses):
        self._responses = list(responses)
        self.call_count = 0
        self.received_messages = []

    async def chat_plain(self, messages, system_prompt, **kwargs):
        if not self._responses:
            raise RuntimeError("MockLLMAdapter: unexpected extra call")
        self.call_count += 1
        self.received_messages.append(list(messages))
        return self._responses.pop(0)

    async def chat_with_tools(self, messages, system_prompt, tools, **kwargs):
        return await self.chat_plain(messages, system_prompt, **kwargs)


def _text_block(text):
    return {"type": "text", "text": text}


def _tool_use_block(tid="t1", name="ch__query", sql="SELECT 1"):
    return {"type": "tool_use", "id": tid, "name": name, "input": {"query": sql}}


async def collect_etl_events(loop, message="hi"):
    context = {"history": [], "system_prompt": ""}
    events = []
    async for ev in loop.run_streaming(message, context):
        events.append(ev)
    return events


# ──────────────────────────────────────────────────────────
# 测试用例
# ──────────────────────────────────────────────────────────

async def test_etl_passes_through_continuation_event():
    """ETLAgenticLoop 将父类的 continuation 事件原样透传。"""
    adapter = MockLLMAdapter([
        {"stop_reason": "max_tokens", "content": [_text_block("前半")]},
        {"stop_reason": "end_turn",   "content": [_text_block("后半")]},
    ])
    loop = ETLAgenticLoop(llm_adapter=adapter, mcp_manager=MockMCPManager())
    events = await collect_etl_events(loop)

    cont    = [e for e in events if e.type == "continuation"]
    content = [e for e in events if e.type == "content"]

    assert len(cont) == 1, f"期望 1 个 continuation 事件，得到 {len(cont)}"
    assert len(content) == 1
    assert content[0].data == "前半后半"
    print("[PASS] test_etl_passes_through_continuation_event")


async def test_etl_content_concatenated():
    """ETLAgenticLoop 多次续写后，最终 content 事件包含完整拼接内容。"""
    adapter = MockLLMAdapter([
        {"stop_reason": "max_tokens", "content": [_text_block("A")]},
        {"stop_reason": "max_tokens", "content": [_text_block("B")]},
        {"stop_reason": "end_turn",   "content": [_text_block("C")]},
    ])
    loop = ETLAgenticLoop(llm_adapter=adapter, mcp_manager=MockMCPManager())
    events = await collect_etl_events(loop)

    content = [e for e in events if e.type == "content"]
    assert len(content) == 1
    assert content[0].data == "ABC"
    print("[PASS] test_etl_content_concatenated")


async def test_etl_orphan_tool_use_filtered():
    """ETLAgenticLoop 路径下，孤立 tool_use 块同样被过滤，不会导致 API 400。"""
    adapter = MockLLMAdapter([
        {"stop_reason": "max_tokens", "content": [
            _text_block("生成脚本中..."),
            {"type": "tool_use", "id": "toolu_etl_01", "name": "ch__query",
             "input": {"query": "CREATE TABLE ...（被截断）"}},
        ]},
        {"stop_reason": "end_turn", "content": [_text_block("脚本完成")]},
    ])
    loop = ETLAgenticLoop(llm_adapter=adapter, mcp_manager=MockMCPManager())
    events = await collect_etl_events(loop, message="生成建表脚本")

    # 第2次调用的 messages 不含 tool_use 块
    second_msgs = adapter.received_messages[1]
    for msg in second_msgs:
        c = msg.get("content", "")
        if isinstance(c, list):
            for blk in c:
                assert blk.get("type") != "tool_use", \
                    f"续写 messages 中不应有 tool_use 块: {blk}"

    content = [e for e in events if e.type == "content"]
    assert len(content) == 1
    assert "生成脚本中" in content[0].data
    assert "脚本完成" in content[0].data
    print("[PASS] test_etl_orphan_tool_use_filtered")


async def test_etl_safe_sql_with_continuation():
    """ETLAgenticLoop 先执行安全 SQL 工具，再命中 max_tokens，续写后正常。"""
    adapter = MockLLMAdapter([
        # 第1次：调用安全 SQL 工具
        {"stop_reason": "tool_use", "content": [
            _text_block("查询表结构..."),
            _tool_use_block(tid="t1", name="ch__query", sql="SELECT * FROM system.tables LIMIT 5"),
        ]},
        # 第2次（tool_result 后）：回复中途 max_tokens
        {"stop_reason": "max_tokens", "content": [_text_block("结果：")]},
        # 第3次（续写）：end_turn
        {"stop_reason": "end_turn",   "content": [_text_block("共 3 张表。")]},
    ])
    loop = ETLAgenticLoop(
        llm_adapter=adapter,
        mcp_manager=MockMCPManagerWithTool(),
    )
    events = await collect_etl_events(loop)

    error_events = [e for e in events if e.type == "error"]
    assert len(error_events) == 0, f"不应有错误: {[e.data for e in error_events]}"

    content = [e for e in events if e.type == "content"]
    assert len(content) == 1
    assert "结果：" in content[0].data
    assert "3 张表" in content[0].data
    print("[PASS] test_etl_safe_sql_with_continuation")


async def test_etl_dangerous_sql_aborted_before_continuation():
    """ETLAgenticLoop 拦截危险 SQL 并 reject，不触发续写流程。"""
    from backend.core.approval_manager import approval_manager

    adapter = MockLLMAdapter([
        {"stop_reason": "tool_use", "content": [
            _tool_use_block(tid="t_drop", name="ch__query", sql="DROP TABLE my_table"),
        ]},
    ])
    loop = ETLAgenticLoop(
        llm_adapter=adapter,
        mcp_manager=MockMCPManagerWithTool(),
    )

    context = {"history": [], "system_prompt": ""}
    events = []

    # 启动生成器，在另一个协程中快速 reject
    async def _run():
        async for ev in loop.run_streaming("删表", context):
            events.append(ev)
            if ev.type == "approval_required":
                aid = ev.data.get("approval_id")
                if aid:
                    approval_manager.reject(aid, "测试自动拒绝")

    await _run()

    approval_events = [e for e in events if e.type == "approval_required"]
    error_events    = [e for e in events if e.type == "error"]
    content_events  = [e for e in events if e.type == "content"]
    cont_events     = [e for e in events if e.type == "continuation"]

    assert len(approval_events) >= 1
    assert len(error_events) >= 1
    assert len(content_events) == 0, "被拒绝后不应有 content 事件"
    assert len(cont_events) == 0,    "被拒绝后不应触发续写"
    print("[PASS] test_etl_dangerous_sql_aborted_before_continuation")


async def test_etl_engineer_agent_process_stream():
    """ETLEngineerAgent.process_stream() 续写内容能正确传递到调用方。"""
    adapter = MockLLMAdapter([
        {"stop_reason": "max_tokens", "content": [_text_block("ETL 脚本第一段")]},
        {"stop_reason": "end_turn",   "content": [_text_block(" 第二段")]},
    ])
    agent = ETLEngineerAgent(
        llm_adapter=adapter,
        mcp_manager=MockMCPManager(),
    )

    context = {"history": [], "system_prompt": ""}
    events = []
    async for ev in agent.process_stream("生成 ETL", context):
        events.append(ev)

    content = [e for e in events if e.type == "content"]
    assert len(content) == 1
    assert "ETL 脚本第一段" in content[0].data
    assert "第二段" in content[0].data
    print("[PASS] test_etl_engineer_agent_process_stream")


# ──────────────────────────────────────────────────────────
# 入口
# ──────────────────────────────────────────────────────────

async def run_all():
    tests = [
        test_etl_passes_through_continuation_event,
        test_etl_content_concatenated,
        test_etl_orphan_tool_use_filtered,
        test_etl_safe_sql_with_continuation,
        test_etl_dangerous_sql_aborted_before_continuation,
        test_etl_engineer_agent_process_stream,
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
