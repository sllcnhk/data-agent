#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_p4.py — Phase 4: Agent/Skill Info Visualization

Section A — orchestrator.process_stream() emits agent_start event
Section B — orchestrator_v2.run_streaming() emits agent_start per hop
Section C — SkillLoader.find_triggered() behaviour

Run: /d/ProgramData/Anaconda3/envs/dataagent/python.exe -X utf8 test_p4.py
"""
import asyncio
import os
import sys

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


# ──────────────────────────────────────────────────────────
# Section A: orchestrator.process_stream() agent_start event
# ──────────────────────────────────────────────────────────


def test_section_a():
    print("\n=== Section A: orchestrator.process_stream() agent_start event ===")
    from unittest.mock import AsyncMock, MagicMock, patch

    from backend.agents.agentic_loop import AgentEvent
    from backend.agents.orchestrator import MasterAgent

    def _make_mock_mgr():
        m = MagicMock()
        m.servers = {}
        m.server_configs = {}
        m.list_servers = lambda: []
        m.get_server = lambda name: None
        m.get_all_tools = MagicMock(return_value=[])
        m.get_all_resources = MagicMock(return_value=[])
        m.call_tool = AsyncMock(return_value={"success": True})
        return m

    mock_mgr = _make_mock_mgr()

    # Stub _select_agent and generic loop to not actually run LLM
    async def _fake_stream(*args, **kwargs):
        yield AgentEvent(type="content", data="done", metadata={})

    with patch("backend.agents.orchestrator.ModelAdapterFactory") as MockFactory, \
         patch.object(MasterAgent, "_select_agent", return_value=None), \
         patch("backend.agents.orchestrator.AgentMCPBinder"):
        MockFactory.create_adapter.return_value = MagicMock()

        # Patch AgenticLoop.run_streaming to return a single content event
        with patch("backend.agents.orchestrator.AgenticLoop") as MockLoop:
            MockLoop.return_value.run_streaming = _fake_stream
            agent = MasterAgent(
                mcp_manager=mock_mgr,
                model_key="claude",
                llm_config={"model_type": "claude", "temperature": 0.7, "max_tokens": 8192}
            )
            agent._binder = MagicMock()
            agent._binder.get_filtered_manager = MagicMock(return_value=mock_mgr)

            async def collect_events(message):
                events = []
                async for ev in agent.process_stream(message, {}):
                    events.append(ev)
                return events

            # ── ETL message ──
            etl_events = asyncio.run(collect_events("请帮我建表 create table users"))
            first = etl_events[0] if etl_events else None

            check("A1: first event is agent_start", first is not None and first.type == "agent_start",
                  f"got type={first.type if first else None}")
            check("A2: ETL message → agent_type=etl_engineer",
                  first is not None and first.data.get("agent_type") == "etl_engineer",
                  f"got {first.data.get('agent_type') if first else None}")
            check("A5: agent_start has agent_label field",
                  first is not None and "agent_label" in first.data)
            check("A6: agent_start.skills is list",
                  first is not None and isinstance(first.data.get("skills"), list))

            # ── Analyst message ──
            analyst_events = asyncio.run(collect_events("帮我分析用户留存趋势"))
            first_a = analyst_events[0] if analyst_events else None
            check("A3: analyst message → agent_type=analyst",
                  first_a is not None and first_a.data.get("agent_type") == "analyst",
                  f"got {first_a.data.get('agent_type') if first_a else None}")

            # ── General message ──
            gen_events = asyncio.run(collect_events("你好，今天天气怎么样"))
            first_g = gen_events[0] if gen_events else None
            check("A4: general message → agent_type=general",
                  first_g is not None and first_g.data.get("agent_type") == "general",
                  f"got {first_g.data.get('agent_type') if first_g else None}")

            # Check agent_label values
            check("A5b: ETL label is 数据加工工程师",
                  first is not None and first.data.get("agent_label") == "数据加工工程师")
            check("A5c: analyst label is 数据分析师",
                  first_a is not None and first_a.data.get("agent_label") == "数据分析师")
            check("A5d: general label is 通用助手",
                  first_g is not None and first_g.data.get("agent_label") == "通用助手")


# ──────────────────────────────────────────────────────────
# Section B: orchestrator_v2.run_streaming() agent_start event
# ──────────────────────────────────────────────────────────


def test_section_b():
    print("\n=== Section B: orchestrator_v2.run_streaming() agent_start event ===")
    from unittest.mock import AsyncMock, MagicMock, patch

    from backend.agents.agentic_loop import AgentEvent
    from backend.agents.orchestrator_v2 import AgentOrchestrator

    def _make_mock_mgr():
        m = MagicMock()
        m.servers = {}
        m.server_configs = {}
        m.list_servers = lambda: []
        m.get_server = lambda name: None
        m.get_all_tools = MagicMock(return_value=[])
        m.get_all_resources = MagicMock(return_value=[])
        m.call_tool = AsyncMock(return_value={"success": True})
        return m

    mock_llm = MagicMock()
    mock_mgr = _make_mock_mgr()

    async def _fake_agent_stream(*args, **kwargs):
        yield AgentEvent(type="content", data="analysis done", metadata={})

    # Mock _build_agent to return a fake agent with process_stream
    fake_agent = MagicMock()
    fake_agent.process_stream = _fake_agent_stream
    fake_agent.AGENT_TYPE = "analyst"

    with patch("backend.agents.orchestrator_v2.AgentMCPBinder"):
        orch = AgentOrchestrator(llm_adapter=mock_llm, mcp_manager=mock_mgr)
        orch._binder = MagicMock()
        orch._binder.get_filtered_manager = MagicMock(return_value=mock_mgr)
        orch._build_agent = MagicMock(return_value=fake_agent)

        async def collect_events(message):
            events = []
            async for ev in orch.run_streaming(message, {}):
                events.append(ev)
            return events

        events = asyncio.run(collect_events("帮我分析用户留存"))
        agent_start_events = [e for e in events if e.type == "agent_start"]

        check("B1: at least one agent_start event emitted",
              len(agent_start_events) >= 1,
              f"got {len(agent_start_events)} agent_start events")

        first_as = agent_start_events[0] if agent_start_events else None
        check("B2: agent_start metadata has hop key",
              first_as is not None and "hop" in first_as.metadata,
              f"got metadata={first_as.metadata if first_as else None}")
        check("B2b: hop=1 for first hop",
              first_as is not None and first_as.metadata.get("hop") == 1)
        check("B3: agent_start data has agent_label",
              first_as is not None and "agent_label" in first_as.data)
        check("B4: agent_start data has skills list",
              first_as is not None and isinstance(first_as.data.get("skills"), list))


# ──────────────────────────────────────────────────────────
# Section C: SkillLoader.find_triggered() behaviour
# ──────────────────────────────────────────────────────────


def test_section_c():
    print("\n=== Section C: SkillLoader.find_triggered() ===")
    from backend.skills.skill_loader import get_skill_loader

    loader = get_skill_loader()
    all_skills = loader.get_all()

    check("C0: skills loaded (>=1 skill available)", len(all_skills) >= 1,
          f"loaded {len(all_skills)} skills")

    if not all_skills:
        print("  (skipping C1-C3: no skills loaded)")
        for name in ["C1", "C2", "C3"]:
            results.append((name, False))
        return

    # Pick a trigger from the first skill to build a test message
    first_skill = all_skills[0]
    triggers = first_skill.triggers

    check("C0b: first skill has at least one trigger", len(triggers) >= 1,
          f"triggers={triggers}")

    if triggers:
        trigger_word = triggers[0]
        matched = loader.find_triggered(f"请帮我 {trigger_word} 操作")
        check("C1: find_triggered returns non-empty list for trigger word",
              len(matched) >= 1,
              f"trigger='{trigger_word}', matched={[s.name for s in matched]}")
        if matched:
            check("C2a: matched skill has .name attribute",
                  hasattr(matched[0], "name") and bool(matched[0].name))
            check("C2b: matched skill has .description attribute",
                  hasattr(matched[0], "description"))
    else:
        results.append(("C1", False))
        results.append(("C2a", False))
        results.append(("C2b", False))

    # No-match case
    no_match = loader.find_triggered("今天天气很好，明天出去玩吧 xyzzy42")
    check("C3: find_triggered returns empty list for unrelated message",
          len(no_match) == 0,
          f"got {[s.name for s in no_match]}")


# ──────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("test_p4.py - Agent/Skill Info Visualization")
    print("=" * 60)

    test_section_a()
    test_section_b()
    test_section_c()

    total = len(results)
    passed = sum(1 for _, ok in results if ok)
    failed = total - passed

    print("\n" + "=" * 60)
    print(f"Results: {passed}/{total} passed, {failed} failed")
    if failed:
        print("\nFailed checks:")
        for name, ok in results:
            if not ok:
                print(f"  {FAIL} {name}")
    print("=" * 60)

    sys.exit(0 if failed == 0 else 1)
