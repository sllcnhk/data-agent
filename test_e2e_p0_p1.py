"""
E2E Self-Test: P0 (Agentic Loop + SSE) + P1 (SKILL.md + Specialized Agents)
=============================================================================

用法:
  # Tier-1 快速测试（纯本地，不调 LLM，< 5s）
  python test_e2e_p0_p1.py

  # Tier-1 + Tier-2（需要后端运行 + 真实 LLM 调用，约 30-60s）
  python test_e2e_p0_p1.py --llm

后端启动方式（另开终端）:
  conda activate dataagent
  cd data-agent
  uvicorn backend.main:app --reload --port 8000

测试分区:
  A. 基础设施 — REST CRUD，无 LLM（Tier-1）
  B. SKILL.md 加载器 — 直接导入，无 LLM（Tier-1）
  C. 路由选择 — 直接导入，无 LLM（Tier-1）
  D. 安全防护 — 直接导入，无 LLM（Tier-1）
  E. SSE 流式管道 — 调后端 + LLM（Tier-2，需 --llm）
"""

import sys, json, asyncio, time
sys.path.insert(0, '.')
# Force UTF-8 output on Windows to avoid GBK encoding errors
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

BASE_URL = "http://localhost:8000"
API = f"{BASE_URL}/api/v1"

USE_LLM = "--llm" in sys.argv

errors = []
_section_errors = 0  # errors in current section


def check(label: str, cond, detail=""):
    global _section_errors
    if cond:
        print(f"  [OK]   {label}")
    else:
        print(f"  [FAIL] {label}" + (f": {detail}" if detail else ""))
        errors.append(label)
        _section_errors += 1


def section(title: str):
    global _section_errors
    _section_errors = 0
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print('=' * 60)


# ═══════════════════════════════════════════════════════════════
# Section A: 基础设施 REST (Tier-1, no LLM)
# ═══════════════════════════════════════════════════════════════

section("Section A: 基础设施 REST CRUD（无 LLM）")

import httpx

_conv_id = None
_backend_up = False

def _get(path, **kw):
    return httpx.get(f"{API}{path}", timeout=10, **kw)

def _post(path, **kw):
    return httpx.post(f"{API}{path}", timeout=10, **kw)

def _put(path, **kw):
    return httpx.put(f"{API}{path}", timeout=10, **kw)


# A1 — Health
try:
    r = httpx.get(f"{BASE_URL}/health", timeout=5)
    _backend_up = r.status_code == 200
    check("A1: GET /health → 200", r.status_code == 200, r.status_code)
    body = r.json()
    check("A1: health.status ok/healthy",
          body.get("status") in ("ok", "healthy"), body)
except Exception as e:
    print("  [SKIP] A1-A7: 后端未运行，跳过 REST 测试。启动方式：")
    print("         conda activate dataagent && uvicorn backend.main:app --reload --port 8000")
    print()
    USE_LLM = False  # also skip Tier-2

if _backend_up:  # only continue if backend is up
    # A2 — Create conversation
    try:
        r = _post("/conversations", json={"title": "e2e-test", "model_key": "claude"})
        check("A2: POST /conversations → 200", r.status_code == 200, r.status_code)
        data = r.json().get("data", {})
        _conv_id = data.get("id")
        check("A2: response has id", bool(_conv_id), data)
        check("A2: response has title", data.get("title") == "e2e-test", data)
    except Exception as e:
        check("A2: POST /conversations", False, str(e))

    # A3 — List conversations
    try:
        r = _get("/conversations")
        check("A3: GET /conversations → 200", r.status_code == 200, r.status_code)
        body = r.json()
        check("A3: response has conversations list", "conversations" in body, body)
        check("A3: total is int", isinstance(body.get("total"), int), body.get("total"))
    except Exception as e:
        check("A3: GET /conversations", False, str(e))

    # A4 — Get conversation detail
    if _conv_id:
        try:
            r = _get(f"/conversations/{_conv_id}")
            check("A4: GET /conversations/{id} → 200", r.status_code == 200, r.status_code)
            body = r.json()
            check("A4: response has conversation key", "conversation" in body, body)
            check("A4: response has messages list", "messages" in body, body)
        except Exception as e:
            check("A4: GET /conversations/{id}", False, str(e))

    # A5 — Update title
    if _conv_id:
        try:
            r = _put(f"/conversations/{_conv_id}", json={"title": "e2e-updated"})
            check("A5: PUT /conversations/{id} → 200", r.status_code == 200, r.status_code)
        except Exception as e:
            check("A5: PUT /conversations/{id}", False, str(e))

    # A6 — MCP server list
    try:
        r = _get("/mcp/servers")
        check("A6: GET /mcp/servers → 200", r.status_code == 200, r.status_code)
        body = r.json()
        check("A6: success == True", body.get("success") is True, body)
        servers = body.get("data", [])
        check("A6: server list is a list", isinstance(servers, list), servers)
    except Exception as e:
        check("A6: GET /mcp/servers", False, str(e))

    # A7 — Skills list (old SkillRegistry API)
    try:
        r = _get("/skills")
        check("A7: GET /skills → 200", r.status_code == 200, r.status_code)
        skills = r.json()
        check("A7: skills is a list", isinstance(skills, list), skills)
    except Exception as e:
        check("A7: GET /skills", False, str(e))


# ═══════════════════════════════════════════════════════════════
# Section B: SKILL.md Loader (Tier-1, 直接导入)
# ═══════════════════════════════════════════════════════════════

section("Section B: SKILL.md Loader（直接导入，无 LLM）")

from backend.skills.skill_loader import reload_skills, get_skill_loader

skills = reload_skills()
check("B1: reload_skills() 返回 ≥3 个技能", len(skills) >= 3,
      f"got {len(skills)}: {[s.name for s in skills]}")
print(f"  → 已加载技能: {[s.name for s in skills]}")

# B2 — Metadata completeness
for s in skills:
    check(f"B2: skill '{s.name}' 有 triggers",
          len(s.triggers) > 0, s.triggers)
    check(f"B2: skill '{s.name}' 有 version",
          bool(s.version), s.version)
    check(f"B2: skill '{s.name}' 有 content",
          len(s.content) > 50, f"len={len(s.content)}")

loader = get_skill_loader()

# B3 — ETL trigger
etl_hits = loader.find_triggered("帮我设计ETL宽表加工流程，建表语句怎么写")
check("B3: ETL关键词触发 etl-engineer 技能",
      any(s.name == "etl-engineer" for s in etl_hits),
      [s.name for s in etl_hits])

# B4 — Analyst trigger
analyst_hits = loader.find_triggered("统计DAU漏斗留存分析")
check("B4: 分析关键词触发 clickhouse-analyst 技能",
      any(s.name == "clickhouse-analyst" for s in analyst_hits),
      [s.name for s in analyst_hits])

# B5 — build_skill_prompt includes content
prompt = loader.build_skill_prompt("ETL宽表建表")
check("B5: build_skill_prompt 非空", len(prompt) > 0, f"len={len(prompt)}")
check("B5: prompt 包含技能内容关键词",
      "ETL" in prompt or "ClickHouse" in prompt or "MergeTree" in prompt,
      prompt[:200])

# B6 — Non-matching message → no skill
no_hits = loader.find_triggered("今天天气怎么样")
check("B6: 无关消息不触发任何技能", len(no_hits) == 0, [s.name for s in no_hits])


# ═══════════════════════════════════════════════════════════════
# Section C: Agent 路由选择 (Tier-1, 直接导入)
# ═══════════════════════════════════════════════════════════════

section("Section C: Agent 意图路由（直接导入，无 LLM）")

from backend.agents.orchestrator import MasterAgent
from backend.agents.etl_agent import ETLEngineerAgent
from backend.agents.analyst_agent import DataAnalystAgent

class _MockMgr:
    servers = {}
    server_configs = {}
    def list_servers(self): return []

class _FakeBinder:
    def get_filtered_manager(self, agent_type, mcp_manager):
        return mcp_manager
    def get_max_iterations(self, agent_type):
        return 10

class _FakeMaster:
    llm_adapter = None
    mcp_manager = _MockMgr()
    model_key = "claude"
    _ETL_KEYWORDS = MasterAgent._ETL_KEYWORDS
    _ANALYST_KEYWORDS = MasterAgent._ANALYST_KEYWORDS
    _binder = _FakeBinder()

_fake = _FakeMaster()
_route = lambda msg: MasterAgent._select_agent(_fake, msg)

ETL_MSGS = [
    "帮我设计ETL宽表加工脚本",
    "数据管道 pipeline 设计",
    "建表语句 CREATE TABLE 怎么写",
    "增量全量数据整合",
]
for msg in ETL_MSGS:
    a = _route(msg)
    check(f"C1: ETL路由 → '{msg[:22]}...'",
          a is not None and type(a).__name__ == "ETLEngineerAgent",
          type(a).__name__ if a else "None")

ANALYST_MSGS = [
    "统计最近7天的DAU",
    "用户留存分析报表",
    "漏斗转化率统计",
    "环比同比趋势查询",
]
for msg in ANALYST_MSGS:
    a = _route(msg)
    check(f"C2: 分析路由 → '{msg[:22]}'",
          a is not None and type(a).__name__ == "DataAnalystAgent",
          type(a).__name__ if a else "None")

GENERAL_MSGS = ["你好，介绍一下自己", "天气怎么样", "帮我写一首诗"]
for msg in GENERAL_MSGS:
    a = _route(msg)
    check(f"C3: 通用消息 → None: '{msg[:18]}'", a is None,
          type(a).__name__ if a else "None")


# ═══════════════════════════════════════════════════════════════
# Section D: 安全防护 (Tier-1, 直接导入)
# ═══════════════════════════════════════════════════════════════

section("Section D: 安全防护（SQL 检测 + 只读代理，无 LLM）")

from backend.agents.etl_agent import _detect_dangerous_sql
from backend.agents.analyst_agent import ReadOnlyMCPProxy, _is_readonly_sql

# D1 — 危险 SQL 检测
DANGEROUS = [
    ("DROP TABLE events",           "DROP TABLE"),
    ("DROP DATABASE analytics",     "DROP DATABASE"),
    ("TRUNCATE TABLE t",            "TRUNCATE"),
    ("DELETE FROM orders WHERE 1",  "DELETE FROM"),
    ("ALTER TABLE t DROP COLUMN x", "ALTER...DROP"),
    ("OPTIMIZE TABLE events",       "OPTIMIZE"),
]
for sql, label in DANGEROUS:
    w = _detect_dangerous_sql(sql)
    check(f"D1: 检测危险 SQL '{label}'", len(w) > 0, f"warnings={w}")

# D2 — 安全 SQL 不误报
SAFE = [
    "SELECT count() FROM events WHERE dt = today()",
    "INSERT INTO wide_table SELECT * FROM src",  # ETL 角色允许 INSERT
    "CREATE TABLE IF NOT EXISTS t (id UInt64) ENGINE = MergeTree()",
    "WITH x AS (SELECT 1) SELECT * FROM x",
]
for sql in SAFE:
    w = _detect_dangerous_sql(sql)
    check(f"D2: 无误报 '{sql[:35]}...'", len(w) == 0, f"warnings={w}")

# D3 — _is_readonly_sql
check("D3: SELECT 是只读", _is_readonly_sql("SELECT count() FROM t"))
check("D3: WITH...SELECT 是只读", _is_readonly_sql("WITH x AS (...) SELECT *"))
check("D3: INSERT 是写操作", not _is_readonly_sql("INSERT INTO t SELECT 1"))
check("D3: DROP 是写操作",   not _is_readonly_sql("DROP TABLE t"))
check("D3: TRUNCATE 是写操作", not _is_readonly_sql("  TRUNCATE TABLE t"))

# D4 — ReadOnlyMCPProxy 阻断测试

class _MockTool:
    def __init__(self, name):
        self.name = name
        self.description = f"Tool {name}"
        self.input_schema = {"type": "object", "properties": {}, "required": []}

class _MockSrv:
    tools = {
        "query": _MockTool("query"),
        "list_tables": _MockTool("list_tables"),
    }

class _MockRealMgr:
    servers = {"clickhouse-idn": _MockSrv()}
    server_configs = {"clickhouse-idn": {"type": "clickhouse"}}
    async def call_tool(self, s, t, a):
        return {"success": True, "data": "ok"}
    def list_servers(self):
        return [{"name": "clickhouse-idn", "type": "clickhouse"}]

proxy = ReadOnlyMCPProxy(_MockRealMgr())

async def _run_proxy():
    # 允许的只读工具
    r = await proxy.call_tool("clickhouse-idn", "query", {"query": "SELECT 1"})
    check("D4: SELECT 查询通过代理", r.get("success"), r)

    # 非白名单工具被拒绝
    r2 = await proxy.call_tool("clickhouse-idn", "execute_ddl", {"sql": "DROP TABLE x"})
    check("D4: 非白名单工具被阻断", not r2.get("success"), r2)
    check("D4: 阻断消息含'写操作'", "写操作" in r2.get("error", ""), r2)

    # 通过 query 工具传入写 SQL 被拒绝
    r3 = await proxy.call_tool("clickhouse-idn", "query", {"query": "INSERT INTO t SELECT 1"})
    check("D4: 写 SQL 通过 query 工具被拒绝", not r3.get("success"), r3)

asyncio.get_event_loop().run_until_complete(_run_proxy())


# ═══════════════════════════════════════════════════════════════
# Section E: SSE 流式管道 (Tier-2, 需要 --llm)
# ═══════════════════════════════════════════════════════════════

section("Section E: SSE 流式管道（Tier-2，需要 --llm 参数 + 后端运行）")

if not USE_LLM:
    print("  [SKIP] 未传入 --llm，跳过真实 LLM 测试")
    print("         如需运行: python test_e2e_p0_p1.py --llm")
else:
    def _parse_sse(raw: str):
        """Parse 'data: {json}\\n\\n' SSE lines into list of dicts."""
        events = []
        for line in raw.splitlines():
            line = line.strip()
            if line.startswith("data:"):
                payload = line[5:].strip()
                if payload and payload != "[DONE]":
                    try:
                        events.append(json.loads(payload))
                    except json.JSONDecodeError:
                        pass
        return events

    async def _stream_message(conv_id: str, content: str):
        """Send message with stream=true, collect all SSE events."""
        events = []
        async with httpx.AsyncClient(timeout=120) as client:
            async with client.stream(
                "POST",
                f"{API}/conversations/{conv_id}/messages",
                json={"content": content, "stream": True},
                headers={"Accept": "text/event-stream"},
            ) as resp:
                async for chunk in resp.aiter_text():
                    events.extend(_parse_sse(chunk))
        return events

    async def _run_sse_tests():
        if not _conv_id:
            print("  [SKIP] 无对话 ID (A2 失败)，跳过 SSE 测试")
            return

        # ── E1: General chat SSE event sequence ──────────────────
        print("\n  [E1] 通用对话 SSE 事件流（'你好，简单介绍一下你自己'）")
        try:
            t0 = time.time()
            events = await _stream_message(
                _conv_id, "你好，请用一句话介绍你自己（回答尽量简短）"
            )
            elapsed = time.time() - t0
            types = [e.get("type") for e in events]
            print(f"  → 耗时 {elapsed:.1f}s, 事件序列: {types}")

            check("E1: 收到 user_message 事件", "user_message" in types, types)
            check("E1: 收到 content 事件", "content" in types, types)
            check("E1: 收到 assistant_message 事件", "assistant_message" in types, types)
            check("E1: 无 error 事件", "error" not in types, types)

            # Event order: user_message → ... → content → assistant_message
            if "user_message" in types and "content" in types:
                idx_um = types.index("user_message")
                idx_ct = types.index("content")
                idx_am = types.index("assistant_message") if "assistant_message" in types else 999
                check("E1: user_message 先于 content", idx_um < idx_ct, types)
                check("E1: content 先于 assistant_message", idx_ct <= idx_am, types)

            # content event has text
            content_events = [e for e in events if e.get("type") == "content"]
            if content_events:
                text = content_events[-1].get("data", "")
                check("E1: content 事件有非空文本", len(str(text)) > 0, text[:100])

        except Exception as ex:
            check("E1: SSE 流请求成功", False, str(ex))

        # ── E2: ETL keyword → ETL agent routing evidence ─────────
        print("\n  [E2] ETL 关键词路由（'ETL宽表建表语句示例，非常简短'）")
        try:
            t0 = time.time()
            events = await _stream_message(
                _conv_id,
                "用一句话举例 ETL 宽表的 CREATE TABLE 建表语句（非常简短）"
            )
            elapsed = time.time() - t0
            types = [e.get("type") for e in events]
            print(f"  → 耗时 {elapsed:.1f}s, 事件序列: {types}")

            check("E2: 收到 content 事件", "content" in types, types)
            check("E2: 无 error 事件", "error" not in types, types)

            # Check content mentions ETL/建表/MergeTree
            content_text = " ".join(
                str(e.get("data", "")) for e in events if e.get("type") == "content"
            )
            check("E2: content 提到 CREATE TABLE 或 MergeTree",
                  any(kw.lower() in content_text.lower()
                      for kw in ["create table", "mergetree", "建表", "etl"]),
                  content_text[:200])

            # approval_required should NOT appear here (no dangerous SQL actually executed)
            check("E2: 无 approval_required 事件（未执行危险 SQL）",
                  "approval_required" not in types, types)

        except Exception as ex:
            check("E2: ETL 路由 SSE 请求成功", False, str(ex))

        # ── E3: Analyst keyword → DataAnalystAgent routing ───────
        print("\n  [E3] 分析师关键词路由（'统计 DAU 趋势，非常简短回答'）")
        try:
            t0 = time.time()
            events = await _stream_message(
                _conv_id,
                "请用一句话说明统计DAU留存率的常用SQL思路（非常简短）"
            )
            elapsed = time.time() - t0
            types = [e.get("type") for e in events]
            print(f"  → 耗时 {elapsed:.1f}s, 事件序列: {types}")

            check("E3: 收到 content 事件", "content" in types, types)
            check("E3: 无 error 事件", "error" not in types, types)

            content_text = " ".join(
                str(e.get("data", "")) for e in events if e.get("type") == "content"
            )
            check("E3: content 涉及数据分析关键词",
                  any(kw.lower() in content_text.lower()
                      for kw in ["dau", "留存", "select", "sql", "count"]),
                  content_text[:200])

        except Exception as ex:
            check("E3: 分析师路由 SSE 请求成功", False, str(ex))

    asyncio.get_event_loop().run_until_complete(_run_sse_tests())


# ═══════════════════════════════════════════════════════════════
# Summary
# ═══════════════════════════════════════════════════════════════

print()
print("=" * 60)
if errors:
    print(f"FAILED: {len(errors)} 项未通过:")
    for e in errors:
        print(f"  ✗ {e}")
    sys.exit(1)
else:
    tier = "Tier-1 + Tier-2" if USE_LLM else "Tier-1"
    print(f"All tests passed! ({tier})")
