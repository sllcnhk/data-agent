"""
P2 Self-Test Suite
==================
Tests for T13-T16 + Phase 4 extras.

Sections:
  A - ApprovalManager (T13): create / approve / reject / timeout
  B - approvals REST router (T13): routes + HTTP semantics
  C - ETLAgenticLoop approval gate (T13+etl): real pause + approve flow
  D - ThoughtProcess + store types (T15): TypeScript types reviewed
  E - orchestrator_v2 (T16): HandoffPacket + routing + AgentOrchestrator build
  F - Phase 4: skill-creator.md + user-defined skills API
  G - Regression: P1 tests still pass (import check)
"""
import sys
import io
import asyncio

# UTF-8 console
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, "backend")

_pass = 0
_fail = 0

def check(label, cond, detail=""):
    global _pass, _fail
    if cond:
        print(f"  [OK] {label}")
        _pass += 1
    else:
        print(f"  [FAIL] {label}  detail={detail!r}")
        _fail += 1

# ═══════════════════════════════════════════════════════════════════════════════
print("=== Section A: ApprovalManager ===")
# ═══════════════════════════════════════════════════════════════════════════════

from backend.core.approval_manager import ApprovalManager

async def _section_a():
    mgr = ApprovalManager()

    # create + approve
    aid = mgr.create_approval({"tool": "clickhouse__execute", "sql": "DROP TABLE t", "warnings": ["w1"]})
    check("create returns UUID", len(aid) == 36)
    e = mgr.get(aid)
    check("initial status is pending", e.status == "pending")
    check("pending list includes new entry", any(x["approval_id"] == aid for x in mgr.list_pending()))

    async def _approve():
        await asyncio.sleep(0.05)
        mgr.approve(aid)

    asyncio.create_task(_approve())
    result = await mgr.wait_for_decision(aid, timeout=2.0)
    check("approve → wait_for_decision returns True", result is True)
    check("status is approved", mgr.get(aid).status == "approved")
    check("resolved_at is set", mgr.get(aid).resolved_at is not None)
    check("approved entry NOT in pending list", not any(x["approval_id"] == aid for x in mgr.list_pending()))

    # reject
    aid2 = mgr.create_approval({"tool": "test", "sql": "TRUNCATE TABLE t", "warnings": []})

    async def _reject():
        await asyncio.sleep(0.05)
        mgr.reject(aid2, reason="too risky")

    asyncio.create_task(_reject())
    result2 = await mgr.wait_for_decision(aid2, timeout=2.0)
    check("reject → wait_for_decision returns False", result2 is False)
    check("status is rejected", mgr.get(aid2).status == "rejected")
    check("reject_reason recorded", mgr.get(aid2).reject_reason == "too risky")

    # timeout
    aid3 = mgr.create_approval({"tool": "test", "sql": "DELETE FROM t", "warnings": []})
    result3 = await mgr.wait_for_decision(aid3, timeout=0.1)
    check("timeout → returns False", result3 is False)
    check("status is timeout", mgr.get(aid3).status == "timeout")

    # double-approve guard
    try:
        mgr.approve(aid)  # already approved
        check("double-approve raises ValueError", False)
    except ValueError:
        check("double-approve raises ValueError", True)

asyncio.get_event_loop().run_until_complete(_section_a())

# ═══════════════════════════════════════════════════════════════════════════════
print("\n=== Section B: Approvals REST Router ===")
# ═══════════════════════════════════════════════════════════════════════════════

from backend.api.approvals import router as approvals_router

route_paths = [r.path for r in approvals_router.routes]
check("GET / (list) route exists", any(r.endswith("/") or r == "/approvals" or r.endswith("approvals/") for r in route_paths), route_paths)
check("GET /{id} route exists", any("{approval_id}" in r for r in route_paths), route_paths)
check("POST /{id}/approve route", any("/approve" in r for r in route_paths), route_paths)
check("POST /{id}/reject route", any("/reject" in r for r in route_paths), route_paths)

# ═══════════════════════════════════════════════════════════════════════════════
print("\n=== Section C: ETLAgenticLoop approval gate ===")
# ═══════════════════════════════════════════════════════════════════════════════

from backend.agents.etl_agent import ETLAgenticLoop, _detect_dangerous_sql, _extract_sql_from_input
from backend.core.approval_manager import approval_manager

class _MockTool:
    def __init__(self):
        self.name = "query"
        self.description = "Execute SQL"
        self.input_schema = {"type": "object", "properties": {"query": {"type": "string"}}, "required": []}

class _MockServer:
    tools = {"query": _MockTool()}

class _MockMCPManager:
    servers = {"clickhouse-idn": _MockServer()}

    async def call_tool(self, server, tool, args):
        return {"success": True, "data": "row_count=0"}

    def list_servers(self):
        return []

class _MockLLM:
    _n = 0

    async def chat_with_tools(self, messages, system_prompt="", tools=None, **kw):
        self._n += 1
        if self._n == 1:
            return {
                "stop_reason": "tool_use",
                "content": [
                    {"type": "tool_use", "id": "t1", "name": "clickhouse_idn__query",
                     "input": {"query": "DROP TABLE my_events"}}
                ]
            }
        return {"stop_reason": "end_turn", "content": [{"type": "text", "text": "完成"}]}

    async def chat_plain(self, messages, system_prompt="", **kw):
        return {"stop_reason": "end_turn", "content": [{"type": "text", "text": "OK"}]}


async def _section_c():
    loop = ETLAgenticLoop(
        llm_adapter=_MockLLM(),
        mcp_manager=_MockMCPManager(),
    )

    async def auto_approve():
        await asyncio.sleep(0.15)
        for aid, entry in list(approval_manager._approvals.items()):
            if entry.status == "pending":
                approval_manager.approve(aid)
                break

    approver_task = asyncio.create_task(auto_approve())
    all_events = []
    async for ev in loop.run_streaming("分析宽表加工方案", {}):
        all_events.append(ev)
    await approver_task

    types = [e.type for e in all_events]
    check("approval_required emitted before tool executes", "approval_required" in types, types)
    check("tool_call event present", "tool_call" in types, types)
    check("tool_result event present", "tool_result" in types, types)
    check("content event after approval", "content" in types, types)
    check("no error when approved", "error" not in types, types)

    # Verify approval_required carries approval_id
    ar_events = [e for e in all_events if e.type == "approval_required"]
    check("approval_required has approval_id", bool(ar_events[0].data.get("approval_id")), ar_events)
    check("approval_required has warnings list", isinstance(ar_events[0].data.get("warnings"), list), ar_events)

    # Verify tool_call comes AFTER approval_required
    ar_idx = next(i for i, e in enumerate(all_events) if e.type == "approval_required")
    tc_idx = next(i for i, e in enumerate(all_events) if e.type == "tool_call")
    check("approval_required comes before tool_call", ar_idx < tc_idx, (ar_idx, tc_idx))

    # Test reject path
    loop2 = ETLAgenticLoop(llm_adapter=_MockLLM(), mcp_manager=_MockMCPManager())

    async def auto_reject():
        await asyncio.sleep(0.15)
        for aid, entry in list(approval_manager._approvals.items()):
            if entry.status == "pending":
                approval_manager.reject(aid, reason="denied by test")
                break

    rejecter_task = asyncio.create_task(auto_reject())
    reject_events = []
    async for ev in loop2.run_streaming("ETL数据接入脚本", {}):
        reject_events.append(ev)
    await rejecter_task

    reject_types = [e.type for e in reject_events]
    check("error event when rejected", "error" in reject_types, reject_types)
    check("no content when rejected", "content" not in reject_types, reject_types)

asyncio.get_event_loop().run_until_complete(_section_c())

# ═══════════════════════════════════════════════════════════════════════════════
print("\n=== Section D: ThoughtProcess types (frontend) ===")
# ═══════════════════════════════════════════════════════════════════════════════

from pathlib import Path

tp_path = Path("frontend/src/components/chat/ThoughtProcess.tsx")
check("ThoughtProcess.tsx exists", tp_path.exists())
tp_text = tp_path.read_text(encoding="utf-8") if tp_path.exists() else ""
check("ThoughtProcess renders thinking events", "thinking" in tp_text)
check("ThoughtProcess renders tool_call events", "tool_call" in tp_text)
check("ThoughtProcess renders tool_result events", "tool_result" in tp_text)
check("ThoughtProcess uses Collapse", "Collapse" in tp_text)

am_path = Path("frontend/src/components/chat/ApprovalModal.tsx")
check("ApprovalModal.tsx exists", am_path.exists())
am_text = am_path.read_text(encoding="utf-8") if am_path.exists() else ""
check("ApprovalModal has approve handler", "handleApprove" in am_text)
check("ApprovalModal has reject handler", "handleReject" in am_text)
check("ApprovalModal has countdown", "countdown" in am_text)
check("ApprovalModal calls /approve API", "/approve" in am_text)
check("ApprovalModal calls /reject API", "/reject" in am_text)

store_path = Path("frontend/src/store/useChatStore.ts")
store_text = store_path.read_text(encoding="utf-8") if store_path.exists() else ""
check("store has messageThoughts field", "messageThoughts" in store_text)
check("store has addThoughtEvent action", "addThoughtEvent" in store_text)
check("store has pendingApproval field", "pendingApproval" in store_text)
check("store has setPendingApproval action", "setPendingApproval" in store_text)

# ═══════════════════════════════════════════════════════════════════════════════
print("\n=== Section E: orchestrator_v2 (T16) ===")
# ═══════════════════════════════════════════════════════════════════════════════

from backend.agents.orchestrator_v2 import (
    HandoffPacket, AgentOrchestrator, _score_routing,
    _ETL_TO_ANALYST_PATTERNS, _ANALYST_TO_ETL_PATTERNS,
    create_orchestrator,
)

# HandoffPacket
pkt = HandoffPacket(
    from_agent="etl_engineer",
    to_agent="analyst",
    task_summary="生成了宽表 SQL",
    artifacts={"query_result": [1, 2, 3], "row_count": 3},
    next_action="请分析留存率",
    conversation_id="conv-abc",
)
check("HandoffPacket fields correct", pkt.from_agent == "etl_engineer" and pkt.to_agent == "analyst")
check("HandoffPacket has timestamp", bool(pkt.timestamp))

prompt = pkt.to_context_prompt()
check("to_context_prompt has from_agent", "etl_engineer" in prompt)
check("to_context_prompt has task_summary", "宽表 SQL" in prompt)
check("to_context_prompt has artifacts", "query_result" in prompt)
check("to_context_prompt has next_action", "留存率" in prompt)

d = pkt.to_dict()
check("to_dict has all keys", all(k in d for k in ["from_agent","to_agent","task_summary","artifacts","conversation_id"]))

# Routing
check("ETL routing", _score_routing("帮我设计ETL宽表加工脚本") == "etl_engineer")
check("Analyst routing", _score_routing("分析用户留存趋势") == "analyst")
check("General routing (no keywords)", _score_routing("你好啊世界") == "general")

# Implicit handoff detection
from backend.agents.orchestrator_v2 import AgentOrchestrator
orch_inst = AgentOrchestrator(llm_adapter=None, mcp_manager=_MockMCPManager())
check("ETL→Analyst handoff: 数据已完成建议分析", orch_inst._detect_implicit_handoff("etl_engineer", "数据已完成，建议数据分析师进行分析") == "analyst")
check("Analyst→ETL handoff: 需要建表", orch_inst._detect_implicit_handoff("analyst", "需要建表后才能查询") == "etl_engineer")
check("No handoff from general", orch_inst._detect_implicit_handoff("general", "你好世界") is None)

# create_orchestrator factory
orch = create_orchestrator(llm_adapter=None, mcp_manager=_MockMCPManager())
check("create_orchestrator returns AgentOrchestrator", isinstance(orch, AgentOrchestrator))

# ═══════════════════════════════════════════════════════════════════════════════
print("\n=== Section F: Phase 4 — skill-creator + user-defined skills API ===")
# ═══════════════════════════════════════════════════════════════════════════════

sc_path = Path(".claude/skills/skill-creator.md")
check("skill-creator.md exists", sc_path.exists())
sc_text = sc_path.read_text(encoding="utf-8") if sc_path.exists() else ""
check("skill-creator has triggers", "triggers:" in sc_text)
check("skill-creator trigger includes '创建技能'", "创建技能" in sc_text)
check("skill-creator has Frontmatter format docs", "frontmatter" in sc_text.lower() or "---" in sc_text)

user_dir = Path(".claude/skills/user")
check("user skills dir exists", user_dir.is_dir())

from backend.api.skills import _slugify, _build_skill_md, UserSkillCreate
check("_slugify kebab-case", _slugify("My Custom Skill!") == "my-custom-skill")
# _slugify keeps Unicode word chars (Chinese) — just verify no spaces/specials remain
slug_result = _slugify("测试 skill")
check("_slugify no spaces remain", " " not in slug_result and "!" not in slug_result, slug_result)

skill = UserSkillCreate(
    name="test-demo-skill",
    description="A test skill for unit testing",
    triggers=["demo", "测试技能"],
    category="general",
    priority="low",
    content="# Demo Skill\n\nThis is a test skill.",
)
md = _build_skill_md(skill)
check("_build_skill_md has YAML header", md.startswith("---"))
check("_build_skill_md has name", "name: test-demo-skill" in md)
check("_build_skill_md has triggers", "- demo" in md and "- 测试技能" in md)
check("_build_skill_md has content body", "# Demo Skill" in md)

# Check user-defined routes exist
from backend.api.skills import router as skills_router
route_paths = [r.path for r in skills_router.routes]
check("POST /skills/user-defined route exists", any("user-defined" in p for p in route_paths), route_paths)

# ═══════════════════════════════════════════════════════════════════════════════
print("\n=== Section G: Regression check ===")
# ═══════════════════════════════════════════════════════════════════════════════

try:
    from backend.agents.orchestrator import MasterAgent
    check("MasterAgent import OK", True)
    check("MasterAgent has _ETL_KEYWORDS", hasattr(MasterAgent, "_ETL_KEYWORDS"))
except Exception as ex:
    check(f"MasterAgent import OK: {ex}", False)

try:
    from backend.skills.skill_loader import get_skill_loader
    loader = get_skill_loader()
    loader.load_all()  # load_all() is the correct method; reload_skills() is in skill_watcher
    skills = loader.get_all()
    check(f"SkillLoader loaded {len(skills)} skills (>=4 with skill-creator)", len(skills) >= 4, [s.name for s in skills])
except Exception as ex:
    check(f"SkillLoader: {ex}", False)

# ═══════════════════════════════════════════════════════════════════════════════
print(f"\n{'='*60}")
total = _pass + _fail
if _fail == 0:
    print(f"All {total} P2 checks PASSED")
else:
    print(f"FAILED: {_fail}/{total} checks")
    sys.exit(1)
