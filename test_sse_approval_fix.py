#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_sse_approval_fix.py
========================
验证 SSE GZip 绕过 + 心跳机制 + approval_required 弹窗实时推送修复。

根本原因（2026-03-12 确认）：
  FastAPI GZipMiddleware 将所有 StreamingResponse 数据块在 zlib 内部缓冲区积压，
  直到流关闭时才一次性 flush，导致 approval_required 等 SSE 事件只有在 3 分钟
  Agent 等待期结束后才一次性抵达浏览器——前端弹窗因此严重滞后。

修复方案：
  StreamingResponse headers 中设置 Content-Encoding: identity，
  Starlette GZipMiddleware 检测到已有 Content-Encoding 时跳过 gzip 压缩，
  SSE 数据块逐条实时到达浏览器。心跳机制作为辅助保持连接活跃。

Sections:
  A  SSE 心跳机制（asyncio.Queue + 超时心跳）
  B  approval_required 事件在 wait_for_decision 之前被 yield
  C  ApprovalManager 阻塞/唤醒语义
  D  SSE 响应头 (Content-Encoding: identity + X-Accel-Buffering: no)
  E  路径预检：非法路径不弹窗，合法路径触发弹窗

运行：/d/ProgramData/Anaconda3/envs/dataagent/python.exe test_sse_approval_fix.py
"""
import asyncio
import json
import os
import sys
import time
from typing import AsyncGenerator, Dict, Any, List, Tuple
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

PASS = "[PASS]"
FAIL = "[FAIL]"
results: list = []


def check(name: str, condition: bool, detail: str = "") -> bool:
    status = PASS if condition else FAIL
    results.append((name, condition))
    msg = f"  {status} {name}"
    if detail:
        msg += f"  [{detail}]"
    print(msg)
    return condition


# ─────────────────────────────────────────────────────────────────────────────
# 复用 generate() 心跳逻辑（从 conversations.py 提取核心模式进行独立测试）
# ─────────────────────────────────────────────────────────────────────────────

async def _generate_with_heartbeat(
    mock_stream: AsyncGenerator[Dict[str, Any], None],
    heartbeat_interval: float = 10.0,
) -> AsyncGenerator[str, None]:
    """
    Mirror the generate() logic from conversations.py for unit testing.
    heartbeat_interval is configurable so tests can use a short interval.
    """
    event_queue: asyncio.Queue = asyncio.Queue()

    async def _producer():
        try:
            async for chunk in mock_stream:
                await event_queue.put(("data", chunk))
            await event_queue.put(("done", None))
        except Exception as exc:
            await event_queue.put(("error", str(exc)))

    producer_task = asyncio.create_task(_producer())

    try:
        while True:
            try:
                msg_type, payload = await asyncio.wait_for(
                    event_queue.get(), timeout=heartbeat_interval
                )
            except asyncio.TimeoutError:
                yield ": heartbeat\n\n"
                continue

            if msg_type == "done":
                yield f"data: {json.dumps({'type': 'done'})}\n\n"
                break
            elif msg_type == "error":
                yield f"data: {json.dumps({'type': 'error', 'error': payload})}\n\n"
                break
            else:
                chunk = payload
                yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
    finally:
        if not producer_task.done():
            producer_task.cancel()


# ─────────────────────────────────────────────────────────────────────────────
# Section A — SSE 心跳机制
# ─────────────────────────────────────────────────────────────────────────────

def test_section_a():
    print("\n=== Section A: SSE 心跳机制 ===")

    async def run():
        # A1: 快速事件不需要心跳，立即到达
        async def fast_stream():
            yield {"type": "thinking", "data": "第1轮"}
            yield {"type": "content", "data": "完成"}

        output = []
        t0 = time.monotonic()
        async for line in _generate_with_heartbeat(fast_stream(), heartbeat_interval=10.0):
            output.append(line)
        elapsed = time.monotonic() - t0

        check("A1 快速事件立即到达（无心跳）", elapsed < 1.0, f"elapsed={elapsed:.2f}s")
        check("A1 输出包含 thinking 事件", any('"thinking"' in l for l in output))
        check("A1 输出包含 content 事件", any('"content"' in l for l in output))
        check("A1 输出包含 done 事件", any('"done"' in l for l in output))
        check("A1 无心跳行", not any(l.startswith(": heartbeat") for l in output))

        # A2: 流中间有长暂停时触发心跳
        async def slow_stream():
            yield {"type": "approval_required", "data": {"approval_id": "test-abc"}}
            await asyncio.sleep(0.25)   # 超过 0.2s 心跳间隔
            yield {"type": "content", "data": "已完成"}

        received: List[Tuple[float, str]] = []
        t0 = time.monotonic()
        async for line in _generate_with_heartbeat(slow_stream(), heartbeat_interval=0.2):
            received.append((time.monotonic() - t0, line))

        lines_only = [r[1] for r in received]
        heartbeat_lines = [r for r in received if r[1].startswith(": heartbeat")]
        data_lines = [r for r in received if r[1].startswith("data:")]

        check("A2 有心跳行出现", len(heartbeat_lines) >= 1,
              f"heartbeat_count={len(heartbeat_lines)}")

        # approval_required 应在心跳前先到达
        first_data_t = next((r[0] for r in received if '"approval_required"' in r[1]), None)
        first_heartbeat_t = heartbeat_lines[0][0] if heartbeat_lines else None
        check("A2 approval_required 先于心跳到达",
              first_data_t is not None and (
                  first_heartbeat_t is None or first_data_t < first_heartbeat_t
              ),
              f"approval_t={first_data_t:.3f}s hb_t={first_heartbeat_t}")

        check("A2 content 事件最终到达", any('"content"' in l for l in lines_only))

        # A3: producer 异常时 generate 发出 error 帧
        async def error_stream():
            yield {"type": "thinking", "data": "..."}
            raise RuntimeError("模拟 producer 崩溃")

        output3 = []
        async for line in _generate_with_heartbeat(error_stream(), heartbeat_interval=10.0):
            output3.append(line)

        check("A3 producer 异常时发出 error 帧",
              any('"error"' in l for l in output3), str(output3))

        # A4: done 帧格式正确
        async def tiny_stream():
            yield {"type": "content", "data": "hi"}

        output4 = []
        async for line in _generate_with_heartbeat(tiny_stream(), heartbeat_interval=10.0):
            output4.append(line)

        done_lines = [l for l in output4 if '"done"' in l and l.startswith("data:")]
        check("A4 done 帧格式为 SSE data 行", len(done_lines) == 1,
              f"done_line={done_lines}")
        if done_lines:
            parsed = json.loads(done_lines[0][len("data: "):].strip())
            check("A4 done 帧 type 字段正确", parsed.get("type") == "done")

        # A5: 连续多个事件无乱序
        async def multi_stream():
            for i in range(5):
                yield {"type": "thinking", "data": f"第{i+1}轮"}

        output5 = []
        async for line in _generate_with_heartbeat(multi_stream(), heartbeat_interval=5.0):
            if line.startswith("data:"):
                parsed = json.loads(line[len("data: "):].strip())
                if parsed.get("type") == "thinking":
                    output5.append(parsed["data"])

        check("A5 多事件顺序正确", output5 == [f"第{i+1}轮" for i in range(5)],
              str(output5))

    asyncio.run(run())


# ─────────────────────────────────────────────────────────────────────────────
# Section B — approval_required 事件在 wait_for_decision 之前被 yield
# ─────────────────────────────────────────────────────────────────────────────

def test_section_b():
    print("\n=== Section B: approval_required 先于 wait_for_decision ===")

    async def run():
        from backend.core.approval_manager import ApprovalManager
        from backend.agents.agentic_loop import AgentEvent
        from backend.agents.analyst_agent import FileWriteAgenticLoop, _FILE_WRITE_TOOLS
        from backend.mcp.tool_formatter import parse_tool_name

        mgr = ApprovalManager()

        # 构造一个最小化的 FileWriteAgenticLoop，
        # 模拟 super().run_streaming() 产生一个 write_file tool_call 事件
        fake_tool_call_event = AgentEvent(
            type="tool_call",
            data={
                "name": "filesystem__write_file",
                "input": {
                    "path": "customer_data/report.md",
                    "content": "# 测试报告\n内容",
                }
            },
            metadata={"tool_id": "tc-001"},
        )

        async def fake_super_stream():
            """模拟 AgenticLoop.run_streaming() 产生一个 write_file 工具调用"""
            yield fake_tool_call_event
            # super() 在 tool_call yield 后等待被消费，然后继续执行工具
            # （实际 super() 在此 yield 后执行 _execute_tool，
            #  这里简单地 yield 一个 tool_result 代替）
            yield AgentEvent(
                type="tool_result",
                data={"name": "write_file", "result": {"success": True}},
                metadata={"tool_id": "tc-001"},
            )
            yield AgentEvent(
                type="content",
                data="文件已写入",
                metadata={},
            )

        # 跟踪事件顺序及时间戳
        events: List[Tuple[float, str]] = []
        t0 = time.monotonic()
        decision_set_at: float = 0.0

        async def approval_setter():
            """200ms 后批准，模拟用户点击批准"""
            nonlocal decision_set_at
            await asyncio.sleep(0.2)
            pending = [k for k, v in mgr._approvals.items() if v.status == "pending"]
            if pending:
                mgr.approve(pending[0])
                decision_set_at = time.monotonic() - t0

        # 创建 FileWriteAgenticLoop 并 mock 其 super().run_streaming()
        # 和 mcp_manager（不实际执行工具）
        mock_mcp = MagicMock()
        mock_mcp.call_tool = AsyncMock(return_value={"success": True})
        mock_mcp.server_configs = {}
        mock_mcp.servers = {}
        mock_mcp.list_servers = MagicMock(return_value=[])

        loop = FileWriteAgenticLoop(
            llm_adapter=MagicMock(),
            mcp_manager=mock_mcp,
        )

        # 注入 mock approval_manager 和 super().run_streaming()
        with patch(
            "backend.core.approval_manager.approval_manager", mgr
        ), patch.object(
            loop.__class__.__bases__[0], "run_streaming",
            return_value=fake_super_stream()
        ), patch.object(
            loop, "_path_is_write_allowed", return_value=True
        ):
            context = {"conversation_id": "conv-test-b1"}
            setter_task = asyncio.create_task(approval_setter())

            async for ev in loop.run_streaming("写报告", context):
                events.append((time.monotonic() - t0, ev.type))

            await setter_task

        # approval_required 必须在 decision_set_at 之前被 yield
        approval_times = [t for t, tp in events if tp == "approval_required"]
        check("B1 approval_required 事件被 yield",
              len(approval_times) == 1, f"events={events}")

        if approval_times:
            ar_time = approval_times[0]
            check("B2 approval_required 在决策前到达",
                  decision_set_at == 0 or ar_time < decision_set_at,
                  f"ar_t={ar_time:.3f}s decision_t={decision_set_at:.3f}s")

        # 批准后 tool_call 事件应该出现（工具被执行）
        event_types = [tp for _, tp in events]
        check("B3 批准后出现 tool_call 事件（工具继续执行）",
              "tool_call" in event_types, str(event_types))

        # B4: 拒绝场景 → error 事件被 yield，无 content
        mgr2 = ApprovalManager()
        events2: List[Tuple[float, str]] = []
        t0 = time.monotonic()

        async def fake_super_stream2():
            yield fake_tool_call_event

        loop2 = FileWriteAgenticLoop(llm_adapter=MagicMock(), mcp_manager=mock_mcp)

        async def rejector():
            await asyncio.sleep(0.1)
            pending = [k for k, v in mgr2._approvals.items() if v.status == "pending"]
            if pending:
                mgr2.reject(pending[0], "测试拒绝")

        with patch("backend.core.approval_manager.approval_manager", mgr2), \
             patch.object(loop2.__class__.__bases__[0], "run_streaming",
                          return_value=fake_super_stream2()), \
             patch.object(loop2, "_path_is_write_allowed", return_value=True):

            context2 = {"conversation_id": "conv-test-b4"}
            rej_task = asyncio.create_task(rejector())
            async for ev in loop2.run_streaming("写报告", context2):
                events2.append((time.monotonic() - t0, ev.type))
            await rej_task

        types2 = [tp for _, tp in events2]
        check("B4 拒绝场景：approval_required 被 yield", "approval_required" in types2)
        check("B4 拒绝场景：error 事件被 yield", "error" in types2, str(types2))
        check("B4 拒绝场景：无 content 事件", "content" not in types2, str(types2))

    asyncio.run(run())


# ─────────────────────────────────────────────────────────────────────────────
# Section C — ApprovalManager 阻塞/唤醒语义
# ─────────────────────────────────────────────────────────────────────────────

def test_section_c():
    print("\n=== Section C: ApprovalManager 阻塞/唤醒语义 ===")

    async def run():
        from backend.core.approval_manager import ApprovalManager

        # C1: create_approval 返回非空 UUID
        mgr = ApprovalManager()
        aid = mgr.create_approval({"tool": "write_file", "path": "test.md"})
        check("C1 create_approval 返回非空 UUID", bool(aid) and len(aid) == 36)
        entry = mgr.get(aid)
        check("C1 entry 状态为 pending", entry is not None and entry.status == "pending")

        # C2: approve() 使 wait_for_decision 返回 True
        async def approver():
            await asyncio.sleep(0.05)
            mgr.approve(aid)

        asyncio.create_task(approver())
        result = await mgr.wait_for_decision(aid, timeout=2.0)
        check("C2 approve() 使 wait_for_decision 返回 True", result is True)

        # C3: timeout → 返回 False，状态变为 timeout
        mgr3 = ApprovalManager()
        aid3 = mgr3.create_approval({"tool": "write_file", "path": "test.md"})
        t_start = time.monotonic()
        result3 = await mgr3.wait_for_decision(aid3, timeout=0.1)
        elapsed3 = time.monotonic() - t_start
        check("C3 超时返回 False", result3 is False)
        check("C3 超时在预期时间内", elapsed3 < 0.5, f"elapsed={elapsed3:.3f}s")
        entry3 = mgr3.get(aid3)
        check("C3 entry 状态变为 timeout", entry3 is not None and entry3.status == "timeout")

        # C4: reject() 使 wait_for_decision 返回 False，reject_reason 正确
        mgr4 = ApprovalManager()
        aid4 = mgr4.create_approval({"tool": "write_file", "path": "test.md"})

        async def rejecter():
            await asyncio.sleep(0.05)
            mgr4.reject(aid4, "用户拒绝")

        asyncio.create_task(rejecter())
        result4 = await mgr4.wait_for_decision(aid4, timeout=2.0)
        check("C4 reject() 使 wait_for_decision 返回 False", result4 is False)
        entry4 = mgr4.get(aid4)
        check("C4 entry 状态为 rejected", entry4 is not None and entry4.status == "rejected")
        check("C4 reject_reason 正确", entry4 is not None and entry4.reject_reason == "用户拒绝")

        # C5: 多个并发审批互不干扰
        mgr5 = ApprovalManager()
        aid5a = mgr5.create_approval({"tool": "write_file", "path": "a.md"})
        aid5b = mgr5.create_approval({"tool": "write_file", "path": "b.md"})

        async def approve_a():
            await asyncio.sleep(0.03)
            mgr5.approve(aid5a)

        async def reject_b():
            await asyncio.sleep(0.06)
            mgr5.reject(aid5b, "b 被拒绝")

        asyncio.create_task(approve_a())
        asyncio.create_task(reject_b())
        result5a = await mgr5.wait_for_decision(aid5a, timeout=2.0)
        result5b = await mgr5.wait_for_decision(aid5b, timeout=2.0)
        check("C5 并发：aid5a 批准", result5a is True)
        check("C5 并发：aid5b 拒绝", result5b is False)

        # C6: is_file_write_granted / grant_file_write / revoke 语义
        mgr6 = ApprovalManager()
        cid = "conv-xyz"
        check("C6 初始未授权", not mgr6.is_file_write_granted(cid))
        mgr6.grant_file_write(cid)
        check("C6 grant 后已授权", mgr6.is_file_write_granted(cid))
        mgr6.revoke_session_grants(cid)
        check("C6 revoke 后解除授权", not mgr6.is_file_write_granted(cid))

    asyncio.run(run())


# ─────────────────────────────────────────────────────────────────────────────
# Section D — SSE 响应头
# ─────────────────────────────────────────────────────────────────────────────

def test_section_d():
    print("\n=== Section D: SSE 响应头 ===")

    async def run():
        # 直接检查 conversations.py 里 StreamingResponse 的构造参数
        import inspect
        import ast

        conv_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "backend", "api", "conversations.py"
        )
        src = open(conv_path, encoding="utf-8").read()

        main_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "backend", "main.py"
        )
        main_src = open(main_path, encoding="utf-8").read()

        check("D1 含有 X-Accel-Buffering: no",
              "X-Accel-Buffering" in src and '"no"' in src)
        check("D2 含有 Cache-Control: no-cache",
              "Cache-Control" in src and "no-cache" in src)
        check("D3 media_type 为 text/event-stream",
              "text/event-stream" in src)
        check("D4 使用 asyncio.Queue",
              "asyncio.Queue" in src)
        check("D5 含有心跳超时逻辑 (wait_for + TimeoutError)",
              "asyncio.TimeoutError" in src or "TimeoutError" in src)
        check("D6 含有心跳注释行 (: heartbeat)",
              '": heartbeat' in src)
        check("D7 含有 producer_task.cancel() (清理逻辑)",
              "producer_task.cancel()" in src)

        # D8: GZip 绕过 — Content-Encoding: identity 存在于 SSE 响应头
        # 这是本次修复的核心：Starlette GZipMiddleware 检测到 Content-Encoding
        # 已有值时跳过 gzip 压缩，使 SSE 数据块逐条实时到达浏览器。
        check("D8 SSE 响应头含有 Content-Encoding: identity (GZip 绕过)",
              "Content-Encoding" in src and "identity" in src,
              "缺少 Content-Encoding: identity — GZipMiddleware 将缓冲所有 SSE 事件直到流关闭")

        # D9: GZipMiddleware 确实存在于 main.py（验证绕过是必要的）
        check("D9 main.py 使用了 GZipMiddleware (需要绕过)",
              "GZipMiddleware" in main_src,
              "GZipMiddleware 不存在于 main.py，绕过逻辑可能无需保留")

        # D10: Content-Encoding: identity 出现在 StreamingResponse 的 headers 字典中
        # 检查它与 X-Accel-Buffering 在同一个 headers dict 块内
        import re
        # 找到 StreamingResponse 构造调用的 headers 字典内容
        headers_block_match = re.search(
            r'StreamingResponse\(.*?headers=\{(.*?)\}',
            src, re.DOTALL
        )
        if headers_block_match:
            headers_block = headers_block_match.group(1)
            check("D10 Content-Encoding: identity 在 StreamingResponse.headers 字典中",
                  "Content-Encoding" in headers_block and "identity" in headers_block,
                  f"headers block: {headers_block[:200]}")
        else:
            check("D10 Content-Encoding: identity 在 StreamingResponse.headers 字典中",
                  False, "无法定位 StreamingResponse headers 字典")

    asyncio.run(run())


# ─────────────────────────────────────────────────────────────────────────────
# Section E — 路径预检：非法路径不触发弹窗
# ─────────────────────────────────────────────────────────────────────────────

def test_section_e():
    print("\n=== Section E: 路径预检 ===")

    async def run():
        from backend.agents.agentic_loop import AgentEvent
        from backend.agents.analyst_agent import FileWriteAgenticLoop
        from backend.core.approval_manager import ApprovalManager

        mock_mcp = MagicMock()
        mock_mcp.call_tool = AsyncMock(return_value={"success": True})
        mock_mcp.server_configs = {}
        mock_mcp.servers = {}
        mock_mcp.list_servers = MagicMock(return_value=[])

        # E1: 非法路径（.claude/skills/system.md）→ 不触发 approval_required
        forbidden_tool_call = AgentEvent(
            type="tool_call",
            data={
                "name": "filesystem__write_file",
                "input": {
                    "path": ".claude/skills/system.md",
                    "content": "# 非法写入",
                }
            },
            metadata={"tool_id": "tc-forbidden"},
        )

        async def forbidden_stream():
            yield forbidden_tool_call

        mgr_e1 = ApprovalManager()
        loop_e1 = FileWriteAgenticLoop(llm_adapter=MagicMock(), mcp_manager=mock_mcp)
        events_e1 = []

        with patch("backend.core.approval_manager.approval_manager", mgr_e1), \
             patch.object(loop_e1.__class__.__bases__[0], "run_streaming",
                          return_value=forbidden_stream()), \
             patch.object(loop_e1, "_path_is_write_allowed", return_value=False):

            async for ev in loop_e1.run_streaming("写系统技能", {"conversation_id": "e1"}):
                events_e1.append(ev.type)

        check("E1 非法路径：不产生 approval_required",
              "approval_required" not in events_e1, str(events_e1))
        check("E1 非法路径：tool_call 被透传（让代理层拦截）",
              "tool_call" in events_e1, str(events_e1))

        # E2: 合法路径（customer_data/report.md）→ 触发 approval_required
        allowed_tool_call = AgentEvent(
            type="tool_call",
            data={
                "name": "filesystem__write_file",
                "input": {
                    "path": "customer_data/report.md",
                    "content": "# 合法写入",
                }
            },
            metadata={"tool_id": "tc-allowed"},
        )

        async def allowed_stream():
            yield allowed_tool_call

        mgr_e2 = ApprovalManager()
        loop_e2 = FileWriteAgenticLoop(llm_adapter=MagicMock(), mcp_manager=mock_mcp)
        events_e2 = []

        # 批准操作以便生成器能完成
        async def auto_approve_e2():
            for _ in range(30):
                await asyncio.sleep(0.01)
                pending = [k for k, v in mgr_e2._approvals.items() if v.status == "pending"]
                if pending:
                    mgr_e2.approve(pending[0])
                    return

        with patch("backend.core.approval_manager.approval_manager", mgr_e2), \
             patch.object(loop_e2.__class__.__bases__[0], "run_streaming",
                          return_value=allowed_stream()), \
             patch.object(loop_e2, "_path_is_write_allowed", return_value=True):

            context_e2 = {"conversation_id": "e2"}
            approve_task = asyncio.create_task(auto_approve_e2())
            async for ev in loop_e2.run_streaming("写报告", context_e2):
                events_e2.append(ev.type)
            await approve_task

        check("E2 合法路径：产生 approval_required",
              "approval_required" in events_e2, str(events_e2))

        # E3: 已授权会话 → 不再弹窗（直接放行）
        mgr_e3 = ApprovalManager()
        mgr_e3.grant_file_write("conv-e3")   # 预授权
        loop_e3 = FileWriteAgenticLoop(llm_adapter=MagicMock(), mcp_manager=mock_mcp)
        events_e3 = []

        async def allowed_stream3():
            yield allowed_tool_call
            yield AgentEvent(type="content", data="完成", metadata={})

        with patch("backend.core.approval_manager.approval_manager", mgr_e3), \
             patch.object(loop_e3.__class__.__bases__[0], "run_streaming",
                          return_value=allowed_stream3()), \
             patch.object(loop_e3, "_path_is_write_allowed", return_value=True):

            async for ev in loop_e3.run_streaming("写报告2", {"conversation_id": "conv-e3"}):
                events_e3.append(ev.type)

        check("E3 已授权会话：不产生 approval_required",
              "approval_required" not in events_e3, str(events_e3))
        check("E3 已授权会话：tool_call 正常透传",
              "tool_call" in events_e3, str(events_e3))

        # E4: approval_required 事件数据字段完整
        mgr_e4 = ApprovalManager()
        loop_e4 = FileWriteAgenticLoop(llm_adapter=MagicMock(), mcp_manager=mock_mcp)
        approval_event_data = {}

        async def stream_for_e4():
            yield allowed_tool_call

        async def approve_e4():
            for _ in range(30):
                await asyncio.sleep(0.01)
                pending = [k for k, v in mgr_e4._approvals.items() if v.status == "pending"]
                if pending:
                    mgr_e4.approve(pending[0])
                    return

        with patch("backend.core.approval_manager.approval_manager", mgr_e4), \
             patch.object(loop_e4.__class__.__bases__[0], "run_streaming",
                          return_value=stream_for_e4()), \
             patch.object(loop_e4, "_path_is_write_allowed", return_value=True):

            approve_task4 = asyncio.create_task(approve_e4())
            async for ev in loop_e4.run_streaming("写报告3", {"conversation_id": "e4"}):
                if ev.type == "approval_required":
                    approval_event_data = ev.data
            await approve_task4

        check("E4 approval_required.approval_id 非空",
              bool(approval_event_data.get("approval_id")))
        check("E4 approval_required.approval_type == file_write",
              approval_event_data.get("approval_type") == "file_write")
        check("E4 approval_required.path == customer_data/report.md",
              approval_event_data.get("path") == "customer_data/report.md")
        check("E4 approval_required.session_grant == True",
              approval_event_data.get("session_grant") is True)

    asyncio.run(run())


# ─────────────────────────────────────────────────────────────────────────────
# 汇总结果
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("  test_sse_approval_fix.py")
    print("  SSE GZip 绕过 + 心跳机制 + approval 弹窗实时推送修复测试")
    print("=" * 60)

    test_section_a()
    test_section_b()
    test_section_c()
    test_section_d()
    test_section_e()

    print("\n" + "=" * 60)
    passed = sum(1 for _, ok in results if ok)
    failed = sum(1 for _, ok in results if not ok)
    total = len(results)
    print(f"  结果: {passed}/{total} 通过  ({failed} 失败)")
    print("=" * 60)

    if failed:
        print("\n失败项：")
        for name, ok in results:
            if not ok:
                print(f"  {FAIL} {name}")
        sys.exit(1)
    else:
        print("  全部通过！")
        sys.exit(0)
