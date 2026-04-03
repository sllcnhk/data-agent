"""
Self-tests for T10-T12: ETL Agent, Analyst Agent, Skill Watcher.

Sections:
  1. SQL safety detector
  2. ReadOnlyMCPProxy — write-op blocking
  3. ETLAgenticLoop — safety event injection
  4. MasterAgent intent routing
  5. SkillWatcher — start/stop and hot-reload trigger
"""
import sys, json, asyncio, time, pathlib, tempfile, shutil
sys.path.insert(0, '.')

errors = []

def check(label, cond, detail=""):
    if cond:
        print(f"  [OK] {label}")
    else:
        print(f"  [FAIL] {label}" + (f": {detail}" if detail else ""))
        errors.append(label)


# ═══════════════════════════════════════════════════════════
# Section 1: SQL safety detector
# ═══════════════════════════════════════════════════════════

print("=== Section 1: SQL safety detector ===")

from backend.agents.etl_agent import _detect_dangerous_sql

dangerous = [
    "DROP TABLE my_table",
    "DROP DATABASE analytics",
    "TRUNCATE TABLE events",
    "DELETE FROM orders WHERE dt < '2020-01-01'",
    "ALTER TABLE t DROP COLUMN x",
    "OPTIMIZE TABLE events",
]
for sql in dangerous:
    w = _detect_dangerous_sql(sql)
    check(f"Detects '{sql[:30]}'", len(w) > 0, w)

safe = [
    "SELECT count() FROM events WHERE dt = today()",
    "INSERT INTO wide_table SELECT * FROM events",  # INSERT is allowed in ETL
    "CREATE TABLE IF NOT EXISTS t (id UInt64) ENGINE = MergeTree()",
]
for sql in safe:
    w = _detect_dangerous_sql(sql)
    check(f"No false positive: '{sql[:30]}'", len(w) == 0, w)

# ═══════════════════════════════════════════════════════════
# Section 2: ReadOnlyMCPProxy
# ═══════════════════════════════════════════════════════════

print("\n=== Section 2: ReadOnlyMCPProxy ===")

from backend.agents.analyst_agent import ReadOnlyMCPProxy, _is_readonly_sql

# _is_readonly_sql tests
check("SELECT is readonly", _is_readonly_sql("SELECT count() FROM t"))
check("WITH...SELECT is readonly", _is_readonly_sql("WITH x AS (...) SELECT * FROM x"))
check("INSERT is write", not _is_readonly_sql("INSERT INTO t SELECT 1"))
check("DROP is write",   not _is_readonly_sql("DROP TABLE t"))
check("TRUNCATE is write", not _is_readonly_sql("  TRUNCATE TABLE t"))
check("DELETE is write", not _is_readonly_sql("DELETE FROM t WHERE id=1"))

# Mock MCP manager for proxy tests
class MockTool:
    def __init__(self, name, desc):
        self.name = name
        self.description = desc
        self.input_schema = {"type": "object", "properties": {"query": {"type": "string"}}, "required": []}

class MockServer:
    tools = {
        "query": MockTool("query", "Execute SQL query on ClickHouse"),
        "list_tables": MockTool("list_tables", "List tables in database"),
    }

class MockManager:
    servers = {"clickhouse-idn": MockServer()}
    server_configs = {"clickhouse-idn": {"type": "clickhouse"}}

    async def call_tool(self, server_name, tool_name, arguments):
        return {"success": True, "data": f"executed {tool_name}"}

    def list_servers(self):
        return [{"name": "clickhouse-idn", "type": "clickhouse"}]

proxy = ReadOnlyMCPProxy(MockManager())

async def _run_proxy_tests():
    # Allowed tool
    r = await proxy.call_tool("clickhouse-idn", "query", {"query": "SELECT 1"})
    check("Allowed read query passes", r.get("success"), r)

    # Blocked tool (write tool not in allowlist)
    r2 = await proxy.call_tool("clickhouse-idn", "execute_ddl", {"sql": "DROP TABLE x"})
    check("Unknown write tool blocked", not r2.get("success"), r2)
    check("Blocked tool error message", "写操作" in r2.get("error", ""), r2)

    # Blocked by SQL content (write SQL via 'query' tool)
    r3 = await proxy.call_tool("clickhouse-idn", "query", {"query": "INSERT INTO t SELECT 1"})
    check("Write SQL blocked in query tool", not r3.get("success"), r3)
    check("SQL block error message", "SELECT" in r3.get("error", "") or "写操作" in r3.get("error", ""), r3)

asyncio.get_event_loop().run_until_complete(_run_proxy_tests())

# ═══════════════════════════════════════════════════════════
# Section 3: ETLAgenticLoop safety events
# ═══════════════════════════════════════════════════════════

print("\n=== Section 3: ETLAgenticLoop safety events ===")

from backend.agents.etl_agent import ETLAgenticLoop, ETLEngineerAgent, _detect_dangerous_sql

# Test _detect_dangerous_sql integrates into tool execution
class MockLLM:
    """Returns a fake tool_use response then a text response."""
    _call = 0

    async def chat_with_tools(self, messages, system_prompt, tools, **kw):
        self._call += 1
        if self._call == 1:
            return {
                "stop_reason": "tool_use",
                "content": [
                    {"type": "tool_use", "id": "t1", "name": "clickhouse_idn__query",
                     "input": {"query": "DROP TABLE old_events"}}
                ]
            }
        return {
            "stop_reason": "end_turn",
            "content": [{"type": "text", "text": "操作已完成"}]
        }

    async def chat_plain(self, messages, system_prompt, **kw):
        return {"stop_reason": "end_turn", "content": [{"type": "text", "text": "OK"}]}

class MockMCPManager:
    # Non-empty tools so format_mcp_tools_for_claude returns tools → chat_with_tools used
    servers = {"clickhouse-idn": MockServer()}
    server_configs = {"clickhouse-idn": {"type": "clickhouse"}}

    async def call_tool(self, server, tool, args):
        return {"success": True, "data": "executed"}

    def list_servers(self):
        return []

async def _run_etl_loop_tests():
    # ETLAgenticLoop.run_streaming now intercepts tool_call events and
    # yields approval_required BEFORE calling _execute_tool.  It then
    # suspends on await approval_manager.wait_for_decision().
    # We run an auto-approver task in parallel to unblock the loop.
    from backend.core.approval_manager import approval_manager as mgr
    import asyncio

    loop = ETLAgenticLoop(
        llm_adapter=MockLLM(),
        mcp_manager=MockMCPManager(),
    )

    async def auto_approver():
        """Approve the first pending approval after a short delay."""
        await asyncio.sleep(0.15)
        for aid, entry in list(mgr._approvals.items()):
            if entry.status == "pending":
                mgr.approve(aid)
                break

    approver_task = asyncio.create_task(auto_approver())

    all_events = []
    async for ev in loop.run_streaming("DROP old_events table", {}):
        all_events.append(ev)

    await approver_task

    event_types = [e.type for e in all_events]
    print(f"  Events: {event_types}")

    check("approval_required event emitted before tool executes",
          "approval_required" in event_types, event_types)
    check("tool_call event present",
          "tool_call" in event_types, event_types)
    check("content event returned after approval",
          "content" in event_types, event_types)
    check("no error when approved",
          "error" not in event_types, event_types)

asyncio.get_event_loop().run_until_complete(_run_etl_loop_tests())

# ═══════════════════════════════════════════════════════════
# Section 4: MasterAgent intent routing
# ═══════════════════════════════════════════════════════════

print("\n=== Section 4: MasterAgent intent routing ===")

# Test _select_agent without building full MasterAgent (mock the parts we need)
from backend.agents.orchestrator import MasterAgent

class _FakeBinder:
    """Minimal stand-in for AgentMCPBinder."""
    def get_filtered_manager(self, agent_type, mcp_manager):
        return mcp_manager
    def get_max_iterations(self, agent_type):
        return 10


class _FakeMasterAgent:
    """Minimal stand-in to test _select_agent()."""
    llm_adapter = None
    mcp_manager = MockMCPManager()
    model_key = "claude"
    _ETL_KEYWORDS = MasterAgent._ETL_KEYWORDS
    _ANALYST_KEYWORDS = MasterAgent._ANALYST_KEYWORDS
    _binder = _FakeBinder()

# In Python 3, unbound methods are plain functions; call directly via __get__
agent_proxy = _FakeMasterAgent()
_select = lambda msg: MasterAgent._select_agent(agent_proxy, msg)

etl_msgs = [
    "帮我设计ETL宽表加工脚本",
    "建表语句生成",
    "CREATE TABLE 怎么写",
    "数据管道 pipeline 设计",
]
for msg in etl_msgs:
    a = _select(msg)
    check(f"ETL routing: '{msg[:20]}'",
          a is not None and type(a).__name__ == "ETLEngineerAgent",
          type(a).__name__ if a else "None")

analyst_msgs = [
    "统计最近7天的DAU",
    "用户留存分析报表",
    "环比同比趋势查询",
    "漏斗转化率统计",
]
for msg in analyst_msgs:
    a = _select(msg)
    check(f"Analyst routing: '{msg[:20]}'",
          a is not None and type(a).__name__ == "DataAnalystAgent",
          type(a).__name__ if a else "None")

general_msgs = ["你好，请介绍一下你自己", "天气怎么样", "帮我写一首诗"]
for msg in general_msgs:
    a = _select(msg)
    check(f"General → no agent: '{msg[:15]}'", a is None, type(a).__name__ if a else "None")

# ═══════════════════════════════════════════════════════════
# Section 5: SkillWatcher hot-reload
# ═══════════════════════════════════════════════════════════

print("\n=== Section 5: SkillWatcher ===")

from backend.skills.skill_watcher import SkillWatcher, _WATCHDOG_AVAILABLE

check("watchdog available", _WATCHDOG_AVAILABLE)

if _WATCHDOG_AVAILABLE:
    # Create a temporary skills directory
    tmp_dir = pathlib.Path(tempfile.mkdtemp())
    (tmp_dir / "test-skill.md").write_text("""\
---
name: test-skill
version: "1.0"
description: A test skill
triggers:
  - testword
category: general
priority: medium
---

# Test Skill

This is a test skill content.
""", encoding="utf-8")

    reload_log = []

    def _on_change():
        reload_log.append(time.time())

    watcher = SkillWatcher(
        skills_dir=str(tmp_dir),
        on_change=_on_change,
        debounce_delay=0.3,
    )
    started = watcher.start()
    check("watcher.start() returns True", started, started)
    check("watcher.is_running is True", watcher.is_running)

    # Trigger a file modification
    time.sleep(0.1)
    (tmp_dir / "test-skill.md").write_text("""\
---
name: test-skill
version: "2.0"
description: Updated test skill
triggers:
  - testword
  - newword
category: general
priority: medium
---

# Test Skill v2
""", encoding="utf-8")

    # Wait for debounce + callback
    time.sleep(1.0)
    check("reload callback triggered on file modify",
          len(reload_log) > 0, f"reload_log={reload_log}")

    # Create a new file
    (tmp_dir / "new-skill.md").write_text("""\
---
name: new-skill
version: "1.0"
description: Newly added skill
triggers:
  - freshword
category: general
priority: low
---

# New Skill
""", encoding="utf-8")
    time.sleep(1.0)
    check("reload callback triggered on new file",
          len(reload_log) >= 2, f"reload_log count={len(reload_log)}")

    watcher.stop()
    check("watcher.is_running is False after stop", not watcher.is_running)

    # Cleanup temp dir
    shutil.rmtree(tmp_dir, ignore_errors=True)

    # Test actual skills directory hot-reload (round-trip)
    from backend.skills.skill_loader import get_skill_loader, reload_skills
    skills_before = {s.name: s.version for s in reload_skills()}
    print(f"  Skills loaded: {list(skills_before.keys())}")
    check("Real skills dir has >=3 skills", len(skills_before) >= 3, skills_before)

else:
    print("  [SKIP] watchdog not available, skipping hot-reload tests")

# ═══════════════════════════════════════════════════════════
# Summary
# ═══════════════════════════════════════════════════════════

print()
if errors:
    print(f"FAILED: {len(errors)} test(s): {errors}")
    sys.exit(1)
else:
    print("All tests passed!")
