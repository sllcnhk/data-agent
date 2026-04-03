"""
自测：文件写入授权门修复验证
"""
import sys, asyncio
sys.path.insert(0, 'backend')

PASS = 0
FAIL = 0

def check(label, ok, detail=""):
    global PASS, FAIL
    s = "PASS" if ok else "FAIL"
    print(f"  [{s}] {label}" + (f" — {detail}" if detail else ""))
    if ok: PASS += 1
    else:  FAIL += 1

# ─────────────────────────────────────────────
# 1. parse_tool_name parses filesystem tool correctly
# ─────────────────────────────────────────────
print("\n=== 1. parse_tool_name ===")
from backend.mcp.tool_formatter import parse_tool_name

server, tool = parse_tool_name("filesystem__write_file")
check("filesystem__write_file → server=filesystem", server == "filesystem", f"got '{server}'")
check("filesystem__write_file → tool=write_file",   tool == "write_file",   f"got '{tool}'")

server, tool = parse_tool_name("filesystem__create_directory")
check("filesystem__create_directory → server=filesystem", server == "filesystem")
check("filesystem__create_directory → tool=create_directory", tool == "create_directory")

server, tool = parse_tool_name("clickhouse_sg__query")
check("clickhouse_sg__query → server=clickhouse-sg", server == "clickhouse-sg", f"got '{server}'")
check("clickhouse_sg__query → tool=query", tool == "query")

server, tool = parse_tool_name("filesystem__read_file")
check("filesystem__read_file not in _FILE_WRITE_TOOLS",
      "read_file" not in __import__('backend.agents.analyst_agent', fromlist=['_FILE_WRITE_TOOLS'])._FILE_WRITE_TOOLS)

# ─────────────────────────────────────────────
# 2. FileWriteAgenticLoop detects write tools via parse_tool_name
# ─────────────────────────────────────────────
print("\n=== 2. FileWriteAgenticLoop detection logic ===")
from backend.agents.analyst_agent import _FILE_WRITE_TOOLS

# Simulate what the fixed loop does
def _detect(namespaced_name):
    srv, tl = parse_tool_name(namespaced_name)
    return srv == "filesystem" and tl in _FILE_WRITE_TOOLS

check("filesystem__write_file  → detected",    _detect("filesystem__write_file"))
check("filesystem__create_directory → detected", _detect("filesystem__create_directory"))
check("filesystem__read_file   → NOT detected", not _detect("filesystem__read_file"))
check("filesystem__list_directory → NOT detected", not _detect("filesystem__list_directory"))
check("filesystem__delete      → NOT detected", not _detect("filesystem__delete"))
check("clickhouse_sg__query    → NOT detected", not _detect("clickhouse_sg__query"))

# ─────────────────────────────────────────────
# 3. Approval flow: approve path → session granted + tool proceeds
# ─────────────────────────────────────────────
print("\n=== 3. Full approval flow simulation ===")

async def run_approve_flow():
    from backend.core.approval_manager import ApprovalManager
    mgr = ApprovalManager()
    conv = "conv-flow-test"

    # Before: not granted
    check("pre: not granted", not mgr.is_file_write_granted(conv))

    # Simulate agent creating approval
    aid = mgr.create_approval({
        "type": "file_write",
        "tool": "write_file",
        "path": ".claude/skills/user/sg_tables.md",
        "content_preview": "# SG Tables\n\nExplored tables...",
        "conversation_id": conv,
    })
    check("approval entry created", bool(aid))

    # entry has approval_type info in data
    entry = mgr.get(aid)
    check("entry.data has type=file_write", entry.data.get("type") == "file_write")
    check("entry.data has path", bool(entry.data.get("path")))
    check("entry.data has content_preview", bool(entry.data.get("content_preview")))

    # User approves
    async def user_approves():
        await asyncio.sleep(0.05)
        mgr.approve(aid)

    asyncio.create_task(user_approves())
    approved = await mgr.wait_for_decision(aid, timeout=3.0)
    check("approved=True after user approve", approved)

    if approved:
        mgr.grant_file_write(conv)

    check("session granted after flow", mgr.is_file_write_granted(conv))

    # Second write in same conversation: skip approval gate
    check("second call auto-passes (session granted)", mgr.is_file_write_granted(conv))

    # Different conversation still blocked
    check("other conversation NOT granted", not mgr.is_file_write_granted("other-conv"))

asyncio.run(run_approve_flow())

# ─────────────────────────────────────────────
# 4. Reject flow
# ─────────────────────────────────────────────
print("\n=== 4. Reject flow ===")
async def run_reject_flow():
    from backend.core.approval_manager import ApprovalManager
    mgr = ApprovalManager()
    conv = "conv-reject"
    aid = mgr.create_approval({"type": "file_write", "tool": "write_file"})

    async def user_rejects():
        await asyncio.sleep(0.05)
        mgr.reject(aid, reason="不需要写文件")

    asyncio.create_task(user_rejects())
    approved = await mgr.wait_for_decision(aid, timeout=3.0)
    check("approved=False after reject", not approved)
    check("session NOT granted after reject", not mgr.is_file_write_granted(conv))

asyncio.run(run_reject_flow())

# ─────────────────────────────────────────────
# 5. Verify FileWriteAgenticLoop uses parse_tool_name (source check)
# ─────────────────────────────────────────────
print("\n=== 5. Source code verification ===")
import inspect
from backend.agents.analyst_agent import FileWriteAgenticLoop

src = inspect.getsource(FileWriteAgenticLoop.run_streaming)
check("uses parse_tool_name", "parse_tool_name" in src)
check("no event.data.get('server')", "get(\"server\"" not in src and "get('server'" not in src,
      "OLD bug still present" if ("get(\"server\"" in src or "get('server'" in src) else "")
check("reads namespaced tool name", "get(\"name\"" in src or "get('name'" in src)
check("yields approval_type=file_write",
      "approval_type" in src and "file_write" in src)
check("yields content_preview", "content_preview" in src)

# ─────────────────────────────────────────────
print(f"\n{'='*45}")
print(f"Total: {PASS} PASS, {FAIL} FAIL")
print('='*45)
sys.exit(0 if FAIL == 0 else 1)
