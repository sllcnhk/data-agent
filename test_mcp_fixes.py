"""
test_mcp_fixes.py
=================
Tests for 4 ClickHouse MCP server bug fixes:
  Fix-1  batch_describe_tables tool (solves repeated describe_table = many iterations)
  Fix-2  get_table_overview ClickHouse version compatibility (rows → total_rows)
  Fix-3  DDL detection false positive (substring → word-boundary regex)
  Fix-4  numpy inhomogeneous array error (use_numpy removed)
"""

import asyncio
import sys
import os
import re

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "backend"))


# ── shared helpers ────────────────────────────────────────────────────────────

def _make_server():
    """Create a ClickHouseMCPServer with a mock client (no real DB required)."""
    from backend.mcp.clickhouse.server import ClickHouseMCPServer

    srv = object.__new__(ClickHouseMCPServer)
    srv.env = "sg"
    srv.level = "admin"
    srv.config = {"database": "test_db", "host": "localhost", "port": 9000,
                  "user": "default", "password": ""}
    srv.client = None  # replaced per-test
    srv._tools = {}
    srv._resources = {}
    return srv


class FakeClient:
    """Stub that records calls and returns pre-programmed responses."""

    def __init__(self, responses: dict = None):
        self._responses = responses or {}
        self.calls = []

    def execute(self, query: str, **kwargs):
        self.calls.append(query)
        # Match by keyword prefix
        for prefix, result in self._responses.items():
            if query.strip().upper().startswith(prefix.upper()):
                if isinstance(result, Exception):
                    raise result
                return result
        return []


# ── Fix-3: DDL detection word-boundary regex ─────────────────────────────────

async def test_F3_ddl_regex_blocks_drop():
    """_DDL_KEYWORD_RE must match standalone DROP."""
    from backend.mcp.clickhouse.server import _DDL_KEYWORD_RE
    assert _DDL_KEYWORD_RE.search("DROP TABLE foo"), "DROP TABLE should be detected"
    print("[PASS] F3-a: DROP TABLE detected")


async def test_F3_ddl_regex_blocks_create_table():
    """_DDL_KEYWORD_RE must match CREATE TABLE."""
    from backend.mcp.clickhouse.server import _DDL_KEYWORD_RE
    assert _DDL_KEYWORD_RE.search("CREATE TABLE foo (id Int32)"), \
        "CREATE TABLE should be detected"
    print("[PASS] F3-b: CREATE TABLE detected")


async def test_F3_ddl_regex_not_triggered_by_create_time():
    """_DDL_KEYWORD_RE must NOT match 'create_time' (column name)."""
    from backend.mcp.clickhouse.server import _DDL_KEYWORD_RE
    query = ("SELECT enterprise_id, phone, create_time, update_time "
             "FROM crm.ads_customer_metric LIMIT 5")
    assert not _DDL_KEYWORD_RE.search(query), \
        f"'create_time' column should NOT be detected as DDL"
    print("[PASS] F3-c: create_time column NOT detected as DDL")


async def test_F3_ddl_regex_not_triggered_by_alter_in_identifier():
    """_DDL_KEYWORD_RE must NOT match 'alter_id' (column name)."""
    from backend.mcp.clickhouse.server import _DDL_KEYWORD_RE
    query = "SELECT alter_id, drop_count FROM my_table WHERE truncate_flag = 1"
    assert not _DDL_KEYWORD_RE.search(query), \
        "Column names with DDL prefixes should not be detected"
    print("[PASS] F3-d: alter_id/drop_count/truncate_flag NOT detected as DDL")


async def test_F3_execute_query_blocks_ddl():
    """_execute_query returns error for real DDL."""
    srv = _make_server()
    srv.client = FakeClient()

    result = await srv._execute_query("DROP TABLE foo")
    assert "error" in result
    assert "DDL" in result["error"]
    print("[PASS] F3-e: _execute_query blocks DROP TABLE")


async def test_F3_execute_query_allows_select_with_create_time():
    """_execute_query passes SELECT with 'create_time' column."""
    srv = _make_server()
    query = ("SELECT enterprise_id, phone, create_time, update_time "
             "FROM crm.ads_customer_metric LIMIT 5")

    # Fake client returns 2 rows
    fake_data = (
        [("e1", "1234567890", "2024-01-01", "2024-01-02"),
         ("e2", "9876543210", "2024-01-03", "2024-01-04")],
        [("enterprise_id", "String"), ("phone", "String"),
         ("create_time", "DateTime"), ("update_time", "DateTime")]
    )
    srv.client = FakeClient({"SELECT": fake_data})

    result = await srv._execute_query(query)
    assert "error" not in result or "DDL" not in result.get("error", ""), \
        f"SELECT with create_time should not be blocked: {result}"
    assert result.get("row_count") == 2, f"Expected 2 rows, got: {result}"
    print("[PASS] F3-f: SELECT with create_time allowed through")


async def test_F3_execute_query_allows_select_with_connect_rate():
    """_execute_query passes SELECT with latest_7d_connect_rate (contains 'CONNECT' not 'CREATE')."""
    srv = _make_server()
    query = ("SELECT latest_7d_connect_rate, latest_30d_connect_rate "
             "FROM crm.ads_customer_metric LIMIT 5")

    fake_data = (
        [(0.75, 0.80)],
        [("latest_7d_connect_rate", "Float64"), ("latest_30d_connect_rate", "Float64")]
    )
    srv.client = FakeClient({"SELECT": fake_data})

    result = await srv._execute_query(query)
    assert "error" not in result, f"connect_rate query should pass: {result}"
    print("[PASS] F3-g: latest_7d_connect_rate column allowed")


# ── Fix-2: get_table_overview compatibility ───────────────────────────────────

async def test_F2_overview_success_with_total_rows():
    """_get_table_overview works when system.tables has total_rows."""
    srv = _make_server()
    srv.client = FakeClient({
        "SELECT COUNT": [(100,)],
        "SELECT\n": [("MergeTree", "1.50 MiB", 1572864, 98)],
    })

    result = await srv._get_table_overview("my_table", "my_db")
    assert result.get("row_count") == 100
    assert result.get("engine") == "MergeTree"
    assert result.get("size") == "1.50 MiB"
    assert "error" not in result or result.get("error") is None
    print("[PASS] F2-a: get_table_overview succeeds with total_rows column")


async def test_F2_overview_graceful_when_rows_column_missing():
    """_get_table_overview falls back gracefully when meta query fails (old ClickHouse)."""
    srv = _make_server()

    call_count = [0]

    class OldVersionClient:
        def execute(self, query, **kwargs):
            call_count[0] += 1
            if "COUNT" in query.upper():
                return [(42,)]
            if "total_bytes" in query or "total_rows" in query:
                # Simulate old ClickHouse: no total_rows column
                raise Exception("Missing columns: 'total_rows'")
            if "engine" in query.lower():
                return [("ReplacingMergeTree",)]
            return []

    srv.client = OldVersionClient()
    result = await srv._get_table_overview("my_table", "my_db")

    # Should NOT return an error — should gracefully degrade
    assert result.get("row_count") == 42, f"row_count wrong: {result}"
    # engine may or may not be populated depending on fallback
    assert "error" not in result or result["error"] is None, \
        f"Should not propagate error to caller: {result}"
    print("[PASS] F2-b: get_table_overview degrades gracefully on old ClickHouse")


async def test_F2_overview_returns_dict_not_exception():
    """_get_table_overview always returns a dict (never raises), even on full failure.

    The refactored implementation swallows all sub-query errors gracefully;
    on total failure the outer except catches and returns an error-keyed dict.
    Partial failure (COUNT ok, meta fails) returns partial result with row_count.
    """
    srv = _make_server()

    class AlwaysFailClient:
        def execute(self, query, **kwargs):
            raise RuntimeError("connection refused")

    srv.client = AlwaysFailClient()

    # Should NOT raise — must return a dict
    try:
        result = await srv._get_table_overview("bad_table")
    except Exception as exc:
        assert False, f"_get_table_overview must not raise, but raised: {exc}"

    assert isinstance(result, dict), "Must always return dict"
    # Either an error dict OR a partial result dict — both are acceptable
    # The key invariant: no exception propagates out
    print("[PASS] F2-c: get_table_overview never raises, always returns dict")


# ── Fix-1: batch_describe_tables ──────────────────────────────────────────────

async def test_F1_batch_returns_schemas_for_all_tables():
    """_batch_describe_tables returns schema dict with one entry per table."""
    srv = _make_server()

    class DescribeClient:
        def execute(self, query, **kwargs):
            # DESCRIBE returns column rows
            return [
                ("id", "UInt64", "", "", None, None, None),
                ("name", "String", "", "", None, None, None),
            ]

    srv.client = DescribeClient()

    result = await srv._batch_describe_tables(["table_a", "table_b"], "my_db")
    assert result["type"] == "batch_table_schemas"
    assert "table_a" in result["schemas"]
    assert "table_b" in result["schemas"]
    assert result["table_count"] == 2
    assert not result["truncated"]
    print("[PASS] F1-a: batch_describe_tables returns schema for each table")


async def test_F1_batch_truncates_at_30_tables():
    """_batch_describe_tables limits to 30 tables and sets truncated=True."""
    srv = _make_server()

    class DescribeClient:
        def execute(self, query, **kwargs):
            return [("id", "UInt64", "", "", None, None, None)]

    srv.client = DescribeClient()
    tables = [f"table_{i}" for i in range(50)]
    result = await srv._batch_describe_tables(tables, "my_db")

    assert result["table_count"] == 30, f"Expected 30, got {result['table_count']}"
    assert result["truncated"] is True
    assert result["truncated_message"] is not None
    print("[PASS] F1-b: batch_describe_tables truncates to 30 and flags truncated=True")


async def test_F1_batch_handles_individual_table_error():
    """_batch_describe_tables includes error entry for tables that fail."""
    srv = _make_server()

    class PartialFailClient:
        def execute(self, query, **kwargs):
            if "bad_table" in query:
                raise Exception("Table not found")
            return [("id", "UInt64", "", "", None, None, None)]

    srv.client = PartialFailClient()
    result = await srv._batch_describe_tables(["good_table", "bad_table"], "my_db")

    assert "good_table" in result["schemas"]
    assert "bad_table" in result["schemas"]
    good = result["schemas"]["good_table"]
    bad = result["schemas"]["bad_table"]
    assert "columns" in good, f"good_table should have columns: {good}"
    assert "error" in bad, f"bad_table should have error entry: {bad}"
    print("[PASS] F1-c: batch_describe_tables handles per-table errors gracefully")


async def test_F1_batch_tool_registered():
    """batch_describe_tables tool is registered in _register_tools()."""
    from backend.mcp.clickhouse.server import ClickHouseMCPServer

    srv = object.__new__(ClickHouseMCPServer)
    srv.env = "sg"
    srv.level = "admin"
    srv.config = {"database": "test_db", "host": "localhost", "port": 9000,
                  "user": "default", "password": ""}
    srv.client = None
    srv._tools = {}
    srv._resources = {}

    # Manually call register to avoid initializing a real client
    from unittest.mock import MagicMock
    srv.register_tool = MagicMock()
    srv.register_resource = MagicMock()
    srv._register_tools()

    registered_names = [call.kwargs.get("name") or call.args[0]
                        for call in srv.register_tool.call_args_list]
    assert "batch_describe_tables" in registered_names, \
        f"batch_describe_tables not in registered tools: {registered_names}"
    print("[PASS] F1-d: batch_describe_tables tool registered in _register_tools()")


# ── Fix-4: no use_numpy in client ─────────────────────────────────────────────

async def test_F4_client_created_without_use_numpy():
    """ClickHouseMCPServer.initialize() must NOT pass use_numpy=True to clickhouse-driver.

    We check that the settings dict passed to Client() does not contain the
    use_numpy key as a live argument (a comment mentioning it is fine).
    """
    import inspect
    from backend.mcp.clickhouse.server import ClickHouseMCPServer

    source = inspect.getsource(ClickHouseMCPServer.initialize)
    # The problematic pattern: settings={"use_numpy": True} or 'use_numpy': True
    # A comment mentioning use_numpy is acceptable — we check for the dict-literal form.
    import ast
    # Strip comments before checking
    lines = [ln for ln in source.splitlines() if not ln.strip().startswith("#")]
    code_only = "\n".join(lines)
    assert '"use_numpy"' not in code_only and "'use_numpy'" not in code_only, (
        "use_numpy must NOT be passed as a dict key in Client() settings. "
        "It causes inhomogeneous array errors on complex ClickHouse types."
    )
    print("[PASS] F4-a: use_numpy not passed as dict key in Client() settings")


# ── runner ────────────────────────────────────────────────────────────────────

async def run_all():
    all_tests = [
        ("F1-a", test_F1_batch_returns_schemas_for_all_tables),
        ("F1-b", test_F1_batch_truncates_at_30_tables),
        ("F1-c", test_F1_batch_handles_individual_table_error),
        ("F1-d", test_F1_batch_tool_registered),
        ("F2-a", test_F2_overview_success_with_total_rows),
        ("F2-b", test_F2_overview_graceful_when_rows_column_missing),
        ("F2-c", test_F2_overview_returns_dict_not_exception),
        ("F3-a", test_F3_ddl_regex_blocks_drop),
        ("F3-b", test_F3_ddl_regex_blocks_create_table),
        ("F3-c", test_F3_ddl_regex_not_triggered_by_create_time),
        ("F3-d", test_F3_ddl_regex_not_triggered_by_alter_in_identifier),
        ("F3-e", test_F3_execute_query_blocks_ddl),
        ("F3-f", test_F3_execute_query_allows_select_with_create_time),
        ("F3-g", test_F3_execute_query_allows_select_with_connect_rate),
        ("F4-a", test_F4_client_created_without_use_numpy),
    ]

    passed = failed = 0
    print("\n" + "=" * 60)
    print("ClickHouse MCP Bug-Fix Tests")
    print("=" * 60)

    for label, fn in all_tests:
        try:
            await fn()
            passed += 1
        except Exception as e:
            failed += 1
            import traceback
            print(f"[FAIL] {label} {fn.__name__}: {e}")
            traceback.print_exc()

    print(f"\n{'='*60}")
    print(f"Results: {passed} passed, {failed} failed / {len(all_tests)} total")
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
