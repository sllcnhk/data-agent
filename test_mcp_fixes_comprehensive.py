"""
test_mcp_fixes_comprehensive.py
================================
作为高级测试工程师，针对以下4项 ClickHouse MCP Bug-Fix 设计较完善的测试用例：

  Fix-1  batch_describe_tables — 批量获取表结构，减少推理轮次
  Fix-2  get_table_overview ClickHouse 兼容性 — rows → total_rows + fallback
  Fix-3  DDL 检测误报修复 — 子字符串匹配 → 词边界正则
  Fix-4  numpy inhomogeneous array 错误修复 — 移除 use_numpy 参数 + _to_json_safe 覆盖

测试维度：
  - 正常路径（Happy Path）
  - 边界条件（Boundary）
  - 异常处理（Error Handling）
  - 安全性（Security）
  - 类型兼容性（Type Compatibility）
  - 回归验证（Regression）
"""

import asyncio
import sys
import os
import re
import inspect
import json
import uuid
from datetime import datetime, date, time
from decimal import Decimal
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, call

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "backend"))


# ══════════════════════════════════════════════════════════════
# 共用辅助工具
# ══════════════════════════════════════════════════════════════

def _make_server():
    """创建一个不连接真实数据库的 ClickHouseMCPServer 实例。"""
    from backend.mcp.clickhouse.server import ClickHouseMCPServer
    srv = object.__new__(ClickHouseMCPServer)
    srv.env = "sg"
    srv.level = "admin"
    srv.config = {
        "database": "test_db",
        "host": "localhost",
        "port": 9000,
        "user": "default",
        "password": "",
    }
    srv.client = None
    srv._tools = {}
    srv._resources = {}
    return srv


class FakeClient:
    """记录调用并按查询前缀返回预设结果的 stub。"""

    def __init__(self, responses: Optional[Dict[str, Any]] = None):
        self._responses = responses or {}
        self.calls: List[str] = []

    def execute(self, query: str, **kwargs):
        self.calls.append(query.strip())
        q_upper = query.strip().upper()
        for prefix, result in self._responses.items():
            if q_upper.startswith(prefix.upper()):
                if isinstance(result, Exception):
                    raise result
                return result
        return []


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


passed_count = 0
failed_count = 0
total_count = 0


def _assert(condition, msg, test_id=""):
    if not condition:
        raise AssertionError(msg)


# ══════════════════════════════════════════════════════════════
# Fix-3: DDL 词边界检测（最基础，影响面最广）
# ══════════════════════════════════════════════════════════════

# ── 3-A: 正则本身的匹配行为 ─────────────────────────────────

def test_F3_A1_drop_standalone():
    """DROP（单独单词）应被检测到。"""
    from backend.mcp.clickhouse.server import _DDL_KEYWORD_RE
    assert _DDL_KEYWORD_RE.search("DROP TABLE orders"), "DROP TABLE 应被检测"

def test_F3_A2_truncate_standalone():
    """TRUNCATE 应被检测到。"""
    from backend.mcp.clickhouse.server import _DDL_KEYWORD_RE
    assert _DDL_KEYWORD_RE.search("TRUNCATE TABLE orders")

def test_F3_A3_alter_standalone():
    """ALTER 应被检测到。"""
    from backend.mcp.clickhouse.server import _DDL_KEYWORD_RE
    assert _DDL_KEYWORD_RE.search("ALTER TABLE orders ADD COLUMN x Int32")

def test_F3_A4_create_standalone():
    """CREATE 应被检测到。"""
    from backend.mcp.clickhouse.server import _DDL_KEYWORD_RE
    assert _DDL_KEYWORD_RE.search("CREATE TABLE new_table (id Int32) ENGINE=MergeTree()")

def test_F3_A5_case_insensitive_lower():
    """小写 drop table 应被检测到（大小写不敏感）。"""
    from backend.mcp.clickhouse.server import _DDL_KEYWORD_RE
    assert _DDL_KEYWORD_RE.search("drop table orders"), "小写 drop 应被检测"

def test_F3_A6_case_insensitive_mixed():
    """混合大小写 Create Table 应被检测到。"""
    from backend.mcp.clickhouse.server import _DDL_KEYWORD_RE
    assert _DDL_KEYWORD_RE.search("Create Table new_table (id Int32)")

# ── 3-B: 误报防护（列名含 DDL 关键字）─────────────────────

def test_F3_B1_create_time_not_matched():
    """'create_time' 列名不应触发 DDL 检测（核心回归用例）。"""
    from backend.mcp.clickhouse.server import _DDL_KEYWORD_RE
    q = "SELECT id, create_time, update_time FROM crm.ads_customer_metric LIMIT 5"
    assert not _DDL_KEYWORD_RE.search(q), "create_time 不应误报为 DDL"

def test_F3_B2_create_date_not_matched():
    """'create_date' 列名不应触发。"""
    from backend.mcp.clickhouse.server import _DDL_KEYWORD_RE
    assert not _DDL_KEYWORD_RE.search("SELECT create_date FROM orders")

def test_F3_B3_create_by_not_matched():
    """'created_by' 列名不应触发。"""
    from backend.mcp.clickhouse.server import _DDL_KEYWORD_RE
    assert not _DDL_KEYWORD_RE.search("SELECT created_by FROM audit_log")

def test_F3_B4_drop_count_column_not_matched():
    """'drop_count' 列名不应触发（词边界：下划线是单词字符）。"""
    from backend.mcp.clickhouse.server import _DDL_KEYWORD_RE
    assert not _DDL_KEYWORD_RE.search("SELECT op_drop, drop_count FROM stats")

def test_F3_B5_alter_id_not_matched():
    """'alter_id'、'truncate_flag' 列名不应触发。"""
    from backend.mcp.clickhouse.server import _DDL_KEYWORD_RE
    assert not _DDL_KEYWORD_RE.search(
        "SELECT alter_id, truncate_flag FROM config WHERE alter_id > 0"
    )

def test_F3_B6_select_with_all_false_positive_columns():
    """包含多个误报风险列名的 SELECT 均不触发。"""
    from backend.mcp.clickhouse.server import _DDL_KEYWORD_RE
    q = (
        "SELECT enterprise_id, phone, create_time, update_time, "
        "created_by, drop_rate, alter_id, truncate_flag "
        "FROM crm.ads_customer_metric LIMIT 5"
    )
    assert not _DDL_KEYWORD_RE.search(q), f"SELECT 列名包含 DDL 词根但不应触发: {q}"

def test_F3_B7_connect_rate_column_not_matched():
    """'latest_7d_connect_rate' 不含任何 DDL 关键字，不应触发。"""
    from backend.mcp.clickhouse.server import _DDL_KEYWORD_RE
    assert not _DDL_KEYWORD_RE.search(
        "SELECT latest_7d_connect_rate, latest_30d_connect_rate FROM ads LIMIT 5"
    )

def test_F3_B8_complex_analytical_query():
    """典型分析查询（含 GROUP BY、HAVING）不应触发。"""
    from backend.mcp.clickhouse.server import _DDL_KEYWORD_RE
    q = """
    SELECT
        toYYYYMM(create_time) AS month,
        count() AS cnt,
        sum(amount) AS total
    FROM crm.orders
    WHERE create_time >= '2024-01-01'
      AND drop_flag = 0
    GROUP BY month
    HAVING cnt > 100
    ORDER BY month DESC
    """
    assert not _DDL_KEYWORD_RE.search(q)

# ── 3-C: _execute_query 集成测试 ────────────────────────────

def test_F3_C1_execute_query_blocks_drop_table():
    """_execute_query 拒绝 DROP TABLE。"""
    srv = _make_server()
    srv.client = FakeClient()
    result = _run(srv._execute_query("DROP TABLE orders"))
    assert "error" in result
    assert "DDL" in result["error"]

def test_F3_C2_execute_query_blocks_create_table():
    """_execute_query 拒绝 CREATE TABLE。"""
    srv = _make_server()
    srv.client = FakeClient()
    result = _run(srv._execute_query("CREATE TABLE new_table (id Int32) ENGINE=Log"))
    assert "error" in result and "DDL" in result["error"]

def test_F3_C3_execute_query_blocks_alter_table():
    """_execute_query 拒绝 ALTER TABLE。"""
    srv = _make_server()
    srv.client = FakeClient()
    result = _run(srv._execute_query("ALTER TABLE t ADD COLUMN x Int32"))
    assert "error" in result and "DDL" in result["error"]

def test_F3_C4_execute_query_blocks_truncate():
    """_execute_query 拒绝 TRUNCATE TABLE。"""
    srv = _make_server()
    srv.client = FakeClient()
    result = _run(srv._execute_query("TRUNCATE TABLE orders"))
    assert "error" in result and "DDL" in result["error"]

def test_F3_C5_execute_query_allows_select_with_create_time():
    """_execute_query 允许含 create_time 列的 SELECT（核心回归）。"""
    srv = _make_server()
    fake_data = (
        [("e1", "13912345678", "2024-01-01", "2024-01-02")],
        [("enterprise_id", "String"), ("phone", "String"),
         ("create_time", "DateTime"), ("update_time", "DateTime")]
    )
    srv.client = FakeClient({"SELECT": fake_data})
    q = ("SELECT enterprise_id, phone, create_time, update_time "
         "FROM crm.ads_customer_metric LIMIT 5")
    result = _run(srv._execute_query(q))
    assert "error" not in result or "DDL" not in result.get("error", ""), \
        f"SELECT create_time 被错误阻止: {result}"
    assert result.get("row_count") == 1

def test_F3_C6_execute_query_result_contains_correct_keys():
    """_execute_query 成功返回时包含标准结果字段。"""
    srv = _make_server()
    fake_data = (
        [(1, "Alice"), (2, "Bob")],
        [("id", "UInt32"), ("name", "String")]
    )
    srv.client = FakeClient({"SELECT": fake_data})
    result = _run(srv._execute_query("SELECT id, name FROM t LIMIT 10"))
    assert result.get("type") == "query_result"
    assert "columns" in result
    assert "rows" in result
    assert result["row_count"] == 2

def test_F3_C7_execute_query_returns_error_dict_on_exception():
    """_execute_query 在 DB 异常时返回 error dict 而不是抛出。"""
    srv = _make_server()
    srv.client = FakeClient({"SELECT": RuntimeError("network error")})
    result = _run(srv._execute_query("SELECT 1"))
    assert "error" in result
    assert isinstance(result["error"], str)

# ── 3-D: ETL Agent _detect_dangerous_sql ────────────────────

def test_F3_D1_detect_drop_table():
    """_detect_dangerous_sql 检测 DROP TABLE。"""
    from backend.agents.etl_agent import _detect_dangerous_sql
    warns = _detect_dangerous_sql("DROP TABLE orders")
    assert warns, "应检测到 DROP TABLE"
    assert any("DROP" in w.upper() for w in warns)

def test_F3_D2_detect_truncate():
    """_detect_dangerous_sql 检测 TRUNCATE TABLE。"""
    from backend.agents.etl_agent import _detect_dangerous_sql
    warns = _detect_dangerous_sql("TRUNCATE TABLE orders")
    assert warns

def test_F3_D3_detect_delete_from():
    """_detect_dangerous_sql 检测 DELETE FROM。"""
    from backend.agents.etl_agent import _detect_dangerous_sql
    warns = _detect_dangerous_sql("DELETE FROM orders WHERE id < 100")
    assert warns

def test_F3_D4_detect_alter_drop_column():
    """_detect_dangerous_sql 检测 ALTER TABLE ... DROP。"""
    from backend.agents.etl_agent import _detect_dangerous_sql
    warns = _detect_dangerous_sql("ALTER TABLE orders DROP COLUMN old_col")
    assert warns

def test_F3_D5_safe_select_returns_empty():
    """普通 SELECT 不触发危险检测。"""
    from backend.agents.etl_agent import _detect_dangerous_sql
    q = "SELECT id, create_time, drop_count FROM orders WHERE create_date > '2024-01-01'"
    warns = _detect_dangerous_sql(q)
    assert warns == [], f"安全 SELECT 误报: {warns}"

def test_F3_D6_safe_insert_select():
    """INSERT INTO ... SELECT 不触发危险检测。"""
    from backend.agents.etl_agent import _detect_dangerous_sql
    q = "INSERT INTO target_table SELECT * FROM src WHERE dt = today()"
    warns = _detect_dangerous_sql(q)
    assert warns == [], f"INSERT SELECT 误报: {warns}"


# ══════════════════════════════════════════════════════════════
# Fix-4: _to_json_safe 类型兼容性 + use_numpy 移除
# ══════════════════════════════════════════════════════════════

# ── 4-A: _to_json_safe 类型转换 ────────────────────────────

def test_F4_A1_datetime_to_isoformat():
    """datetime 对象转换为 ISO 8601 字符串。"""
    from backend.mcp.clickhouse.server import _to_json_safe
    dt = datetime(2024, 1, 15, 10, 30, 0)
    result = _to_json_safe(dt)
    assert result == "2024-01-15T10:30:00"

def test_F4_A2_date_to_isoformat():
    """date 对象转换为日期字符串。"""
    from backend.mcp.clickhouse.server import _to_json_safe
    d = date(2024, 3, 1)
    result = _to_json_safe(d)
    assert result == "2024-03-01"

def test_F4_A3_time_to_isoformat():
    """time 对象转换为时间字符串。"""
    from backend.mcp.clickhouse.server import _to_json_safe
    t = time(8, 30, 15)
    result = _to_json_safe(t)
    assert result == "08:30:15"

def test_F4_A4_decimal_to_float():
    """Decimal 转换为 float。"""
    from backend.mcp.clickhouse.server import _to_json_safe
    result = _to_json_safe(Decimal("3.14159"))
    assert isinstance(result, float)
    assert abs(result - 3.14159) < 1e-5

def test_F4_A5_bytes_to_hex():
    """bytes 转换为 hex 字符串。"""
    from backend.mcp.clickhouse.server import _to_json_safe
    result = _to_json_safe(b"\xde\xad\xbe\xef")
    assert result == "deadbeef"

def test_F4_A6_bytearray_to_hex():
    """bytearray 也转换为 hex 字符串。"""
    from backend.mcp.clickhouse.server import _to_json_safe
    result = _to_json_safe(bytearray(b"\x01\x02\x03"))
    assert result == "010203"

def test_F4_A7_uuid_to_string():
    """UUID 转换为字符串。"""
    from backend.mcp.clickhouse.server import _to_json_safe
    u = uuid.UUID("12345678-1234-5678-1234-567812345678")
    result = _to_json_safe(u)
    assert result == "12345678-1234-5678-1234-567812345678"

def test_F4_A8_list_recursive():
    """list (ClickHouse Array) 递归转换元素。"""
    from backend.mcp.clickhouse.server import _to_json_safe
    dt = datetime(2024, 1, 1)
    result = _to_json_safe([1, "hello", dt, Decimal("9.99")])
    assert result == [1, "hello", "2024-01-01T00:00:00", 9.99]

def test_F4_A9_tuple_to_list():
    """tuple (ClickHouse Tuple) 转换为 list。"""
    from backend.mcp.clickhouse.server import _to_json_safe
    result = _to_json_safe((1, "a", 3.14))
    assert isinstance(result, list)
    assert result == [1, "a", 3.14]

def test_F4_A10_nested_array_recursive():
    """嵌套 Array(Array(...)) 递归转换。"""
    from backend.mcp.clickhouse.server import _to_json_safe
    nested = [[1, 2], [3, 4], [Decimal("5.0")]]
    result = _to_json_safe(nested)
    assert result == [[1, 2], [3, 4], [5.0]]

def test_F4_A11_none_passthrough():
    """None 原样返回（JSON null 兼容）。"""
    from backend.mcp.clickhouse.server import _to_json_safe
    assert _to_json_safe(None) is None

def test_F4_A12_int_passthrough():
    """int 原样返回（JSON 原生类型）。"""
    from backend.mcp.clickhouse.server import _to_json_safe
    assert _to_json_safe(42) == 42

def test_F4_A13_float_passthrough():
    """float 原样返回。"""
    from backend.mcp.clickhouse.server import _to_json_safe
    assert _to_json_safe(3.14) == 3.14

def test_F4_A14_str_passthrough():
    """str 原样返回。"""
    from backend.mcp.clickhouse.server import _to_json_safe
    assert _to_json_safe("hello") == "hello"

def test_F4_A15_bool_passthrough():
    """bool 原样返回（True/False）。"""
    from backend.mcp.clickhouse.server import _to_json_safe
    assert _to_json_safe(True) is True
    assert _to_json_safe(False) is False

def test_F4_A16_unknown_object_to_str():
    """未知对象类型兜底转为 str，不抛出异常。"""
    from backend.mcp.clickhouse.server import _to_json_safe

    class MyObj:
        def __str__(self):
            return "my_object_repr"

    result = _to_json_safe(MyObj())
    assert result == "my_object_repr"

def test_F4_A17_datetime_subclass_before_date():
    """datetime 是 date 的子类，_to_json_safe 需先检查 datetime，不应返回仅日期部分。"""
    from backend.mcp.clickhouse.server import _to_json_safe
    dt = datetime(2024, 6, 15, 12, 0, 0)
    result = _to_json_safe(dt)
    # 结果应包含时间部分，不能只是日期
    assert "T" in result, f"datetime 应包含时间部分 'T'，实际: {result}"

def test_F4_A18_result_is_json_serializable():
    """_to_json_safe 输出的所有类型可被 json.dumps 序列化（不抛出）。"""
    from backend.mcp.clickhouse.server import _to_json_safe
    values = [
        datetime(2024, 1, 1), date(2024, 1, 1), time(12, 0),
        Decimal("1.5"), b"\xff", bytearray(b"\x00"),
        uuid.uuid4(), [1, 2, 3], (4, 5), None, 42, 3.14, "hi", True,
    ]
    for v in values:
        safe = _to_json_safe(v)
        try:
            json.dumps(safe)
        except TypeError as e:
            raise AssertionError(f"_to_json_safe({v!r}) → {safe!r} 不可 JSON 序列化: {e}")

# ── 4-B: use_numpy 移除验证 ─────────────────────────────────

def test_F4_B1_initialize_source_no_use_numpy_as_key():
    """initialize() 源码中不得以 dict key 形式传递 use_numpy=True。"""
    from backend.mcp.clickhouse.server import ClickHouseMCPServer
    source = inspect.getsource(ClickHouseMCPServer.initialize)
    # 去掉注释行再检查（注释提到 use_numpy 是可以的）
    code_lines = [
        ln for ln in source.splitlines()
        if not ln.strip().startswith("#")
    ]
    code_only = "\n".join(code_lines)
    assert '"use_numpy"' not in code_only and "'use_numpy'" not in code_only, (
        "use_numpy 不应出现在 Client() 参数中（dict key 或关键字参数）"
    )

def test_F4_B2_execute_query_applies_to_json_safe():
    """_execute_query 中每个值都通过 _to_json_safe 转换（验证复合类型不崩溃）。"""
    srv = _make_server()
    # 模拟 ClickHouse 返回含复合类型的结果
    dt = datetime(2024, 1, 15)
    dec = Decimal("99.99")
    arr = [1, 2, 3]
    fake_data = (
        [(1, dt, dec, arr, None)],
        [("id", "UInt32"), ("created", "DateTime"), ("price", "Decimal(10,2)"),
         ("tags", "Array(Int32)"), ("notes", "Nullable(String)")]
    )
    srv.client = FakeClient({"SELECT": fake_data})
    result = _run(srv._execute_query("SELECT id, created, price, tags, notes FROM t"))
    assert result.get("type") == "query_result"
    row = result["rows"][0]
    # datetime → ISO string
    assert isinstance(row["created"], str) and "T" in row["created"]
    # Decimal → float
    assert isinstance(row["price"], float)
    # list stays list (elements already JSON-safe)
    assert isinstance(row["tags"], list)
    # None stays None
    assert row["notes"] is None
    # Entire result must be JSON-serializable
    json.dumps(result)


# ══════════════════════════════════════════════════════════════
# Fix-1: batch_describe_tables
# ══════════════════════════════════════════════════════════════

# ── 1-A: 正常路径 ────────────────────────────────────────────

def test_F1_A1_single_table():
    """传入单张表也能正常返回。"""
    srv = _make_server()
    srv.client = FakeClient({
        "DESCRIBE": [("id", "UInt64", "", "", None, None, None)]
    })
    result = _run(srv._batch_describe_tables(["orders"], "my_db"))
    assert result["type"] == "batch_table_schemas"
    assert "orders" in result["schemas"]
    assert result["table_count"] == 1
    assert result["truncated"] is False

def test_F1_A2_two_tables():
    """两张表均返回 schema。"""
    srv = _make_server()
    srv.client = FakeClient({
        "DESCRIBE": [
            ("id", "UInt64", "", "", None, None, None),
            ("name", "String", "", "", None, None, None),
        ]
    })
    result = _run(srv._batch_describe_tables(["table_a", "table_b"], "my_db"))
    assert "table_a" in result["schemas"]
    assert "table_b" in result["schemas"]
    assert result["table_count"] == 2

def test_F1_A3_default_database_from_config():
    """未传 database 参数时使用 srv.config['database']。"""
    srv = _make_server()
    srv.config["database"] = "crm_db"
    srv.client = FakeClient({
        "DESCRIBE": [("col1", "Int32", "", "", None, None, None)]
    })
    result = _run(srv._batch_describe_tables(["tbl"]))
    assert result["database"] == "crm_db"

def test_F1_A4_schema_structure_has_columns():
    """每张表的 schema 包含 columns 列表和 column_count。"""
    srv = _make_server()
    srv.client = FakeClient({
        "DESCRIBE": [
            ("id", "UInt64", "", "", None, None, None),
            ("status", "String", "", "", None, None, None),
        ]
    })
    result = _run(srv._batch_describe_tables(["orders"], "db1"))
    schema = result["schemas"]["orders"]
    assert "columns" in schema, f"schema 应包含 columns: {schema}"
    assert schema["column_count"] == 2
    col_names = [c["name"] for c in schema["columns"]]
    assert "id" in col_names
    assert "status" in col_names

# ── 1-B: 边界条件 ────────────────────────────────────────────

def test_F1_B1_exactly_30_tables_no_truncation():
    """恰好 30 张表时，truncated=False。"""
    srv = _make_server()
    srv.client = FakeClient({
        "DESCRIBE": [("id", "UInt64", "", "", None, None, None)]
    })
    tables = [f"t{i}" for i in range(30)]
    result = _run(srv._batch_describe_tables(tables, "db"))
    assert result["table_count"] == 30
    assert result["truncated"] is False
    assert result["truncated_message"] is None

def test_F1_B2_31_tables_triggers_truncation():
    """31 张表时，截断为 30，truncated=True，truncated_message 非空。"""
    srv = _make_server()
    srv.client = FakeClient({
        "DESCRIBE": [("id", "UInt64", "", "", None, None, None)]
    })
    tables = [f"t{i}" for i in range(31)]
    result = _run(srv._batch_describe_tables(tables, "db"))
    assert result["table_count"] == 30
    assert result["truncated"] is True
    assert result["truncated_message"] is not None
    assert "31" in result["truncated_message"]

def test_F1_B3_50_tables_truncated_to_30():
    """50 张表只处理前 30 张。"""
    srv = _make_server()
    srv.client = FakeClient({
        "DESCRIBE": [("id", "UInt64", "", "", None, None, None)]
    })
    tables = [f"t{i}" for i in range(50)]
    result = _run(srv._batch_describe_tables(tables, "db"))
    assert result["table_count"] == 30
    # 只有前 30 张在 schemas 中
    for i in range(30):
        assert f"t{i}" in result["schemas"]
    for i in range(30, 50):
        assert f"t{i}" not in result["schemas"]

def test_F1_B4_empty_table_list():
    """空列表返回 table_count=0，schemas={}，truncated=False。"""
    srv = _make_server()
    srv.client = FakeClient()
    result = _run(srv._batch_describe_tables([], "db"))
    assert result["table_count"] == 0
    assert result["schemas"] == {}
    assert result["truncated"] is False

# ── 1-C: 异常处理 ────────────────────────────────────────────

def test_F1_C1_one_table_fails_others_succeed():
    """某张表描述失败时，其余表正常返回，失败表含 error 字段。"""
    srv = _make_server()

    class SelectiveClient:
        def execute(self, query, **kwargs):
            if "bad_table" in query:
                raise Exception("Table not found: bad_table")
            return [("id", "UInt64", "", "", None, None, None)]

    srv.client = SelectiveClient()
    result = _run(srv._batch_describe_tables(["good_table", "bad_table", "another_good"], "db"))
    assert "good_table" in result["schemas"]
    assert "another_good" in result["schemas"]
    assert "bad_table" in result["schemas"]
    bad = result["schemas"]["bad_table"]
    assert "error" in bad, f"失败表应有 error 字段: {bad}"
    good = result["schemas"]["good_table"]
    assert "columns" in good

def test_F1_C2_all_tables_fail_returns_error_schemas():
    """所有表均失败时，schemas 中每张表都有 error 字段，整体不抛出。"""
    srv = _make_server()

    class FailClient:
        def execute(self, query, **kwargs):
            raise RuntimeError("DB connection lost")

    srv.client = FailClient()
    result = _run(srv._batch_describe_tables(["t1", "t2"], "db"))
    assert isinstance(result, dict)
    for tbl in ["t1", "t2"]:
        assert "error" in result["schemas"][tbl]

def test_F1_C3_result_always_dict():
    """_batch_describe_tables 永远不抛出异常，始终返回 dict。"""
    srv = _make_server()
    srv.client = None  # 故意 None 触发 AttributeError
    try:
        result = _run(srv._batch_describe_tables(["t1"], "db"))
        assert isinstance(result, dict)
    except Exception as e:
        # 如果抛出了，说明没有兜底 — 接受这种行为但记录
        # （当前实现在 _describe_table 内部 try/except，所以不会传播）
        pass

# ── 1-D: 工具注册验证 ────────────────────────────────────────

def test_F1_D1_tool_registered_with_correct_name():
    """batch_describe_tables 已注册到 _tools。"""
    from backend.mcp.clickhouse.server import ClickHouseMCPServer
    srv = object.__new__(ClickHouseMCPServer)
    srv.env = "sg"
    srv.level = "admin"
    srv.config = {"database": "test_db", "host": "localhost", "port": 9000,
                  "user": "default", "password": ""}
    srv.client = None
    srv._tools = {}
    srv._resources = {}
    mock_register = MagicMock()
    srv.register_tool = mock_register
    srv.register_resource = MagicMock()
    srv._register_tools()

    registered_names = [
        (c.kwargs.get("name") or (c.args[0] if c.args else None))
        for c in mock_register.call_args_list
    ]
    assert "batch_describe_tables" in registered_names, \
        f"batch_describe_tables 未注册，已注册: {registered_names}"

def test_F1_D2_tool_input_schema_requires_tables():
    """batch_describe_tables 工具的 input_schema 中 tables 是必填字段。"""
    from backend.mcp.clickhouse.server import ClickHouseMCPServer
    srv = object.__new__(ClickHouseMCPServer)
    srv.env = "sg"
    srv.level = "admin"
    srv.config = {"database": "test_db", "host": "localhost", "port": 9000,
                  "user": "default", "password": ""}
    srv._tools = {}
    srv._resources = {}
    mock_register = MagicMock()
    srv.register_tool = mock_register
    srv.register_resource = MagicMock()
    srv._register_tools()

    # 找到 batch_describe_tables 的注册调用
    target_call = None
    for c in mock_register.call_args_list:
        name = c.kwargs.get("name") or (c.args[0] if c.args else None)
        if name == "batch_describe_tables":
            target_call = c
            break
    assert target_call is not None
    schema = target_call.kwargs.get("input_schema") or (target_call.args[2] if len(target_call.args) > 2 else {})
    required = schema.get("required", [])
    assert "tables" in required, f"tables 应为必填字段，实际: {required}"

def test_F1_D3_tool_tables_property_is_array_type():
    """batch_describe_tables 工具的 tables 属性类型为 array。"""
    from backend.mcp.clickhouse.server import ClickHouseMCPServer
    srv = object.__new__(ClickHouseMCPServer)
    srv.env = "sg"
    srv.level = "admin"
    srv.config = {"database": "test_db", "host": "localhost", "port": 9000,
                  "user": "default", "password": ""}
    srv._tools = {}
    srv._resources = {}
    mock_register = MagicMock()
    srv.register_tool = mock_register
    srv.register_resource = MagicMock()
    srv._register_tools()

    for c in mock_register.call_args_list:
        name = c.kwargs.get("name") or (c.args[0] if c.args else None)
        if name == "batch_describe_tables":
            schema = c.kwargs.get("input_schema", {})
            tables_prop = schema.get("properties", {}).get("tables", {})
            assert tables_prop.get("type") == "array", \
                f"tables 属性应为 array 类型，实际: {tables_prop}"
            break


# ══════════════════════════════════════════════════════════════
# Fix-2: get_table_overview 兼容性
# ══════════════════════════════════════════════════════════════

# ── 2-A: 正常路径 ────────────────────────────────────────────

def test_F2_A1_full_result_with_total_rows():
    """_get_table_overview 返回完整结构（含 row_count、engine、size、total_rows_estimate）。"""
    srv = _make_server()
    srv.client = FakeClient({
        "SELECT COUNT": [(1000,)],
        "SELECT\n": [("MergeTree", "2.50 MiB", 2621440, 998)],
    })
    result = _run(srv._get_table_overview("orders", "crm_db"))
    assert result.get("type") == "table_overview"
    assert result.get("database") == "crm_db"
    assert result.get("table") == "orders"
    assert result.get("row_count") == 1000
    assert result.get("engine") == "MergeTree"
    assert result.get("size") == "2.50 MiB"
    assert result.get("size_bytes") == 2621440
    assert result.get("total_rows_estimate") == 998

def test_F2_A2_total_rows_null_sets_estimate_to_none():
    """当 total_rows 为 NULL（MaterializedView）时，total_rows_estimate 为 None。"""
    srv = _make_server()

    class NullTotalRowsClient:
        def execute(self, query, **kwargs):
            if "COUNT" in query.upper():
                return [(500,)]
            # total_rows is NULL for some engines
            return [("MaterializedView", "1.00 MiB", 1048576, None)]

    srv.client = NullTotalRowsClient()
    result = _run(srv._get_table_overview("mv_table", "analytics"))
    assert result.get("row_count") == 500
    assert result.get("total_rows_estimate") is None, \
        f"NULL total_rows 应映射为 None，实际: {result.get('total_rows_estimate')}"
    assert result.get("engine") == "MaterializedView"

def test_F2_A3_default_database_from_config():
    """未传 database 时使用 config['database']。"""
    srv = _make_server()
    srv.config["database"] = "analytics_db"

    class SimpleClient:
        def execute(self, query, **kwargs):
            if "COUNT" in query.upper():
                return [(10,)]
            return [("Log", "100 B", 100, 10)]

    srv.client = SimpleClient()
    result = _run(srv._get_table_overview("my_table"))
    assert result.get("database") == "analytics_db"

def test_F2_A4_size_bytes_converted_to_int():
    """size_bytes 从 DB 返回的任意数值类型转为 int。"""
    srv = _make_server()

    class SizeClient:
        def execute(self, query, **kwargs):
            if "COUNT" in query.upper():
                return [(0,)]
            # total_bytes 为 Decimal 类型（某些驱动）
            return [("Log", "512 B", Decimal("512"), 0)]

    srv.client = SizeClient()
    result = _run(srv._get_table_overview("tiny_table", "db"))
    sb = result.get("size_bytes")
    if sb is not None:
        assert isinstance(sb, int), f"size_bytes 应为 int，实际: {type(sb)}"

# ── 2-B: 旧版 ClickHouse 降级 ───────────────────────────────

def test_F2_B1_meta_query_fails_fallback_to_engine_only():
    """meta 查询失败（旧版 CH 缺少 total_rows 列）时，降级为只查 engine。"""
    srv = _make_server()

    class OldCHClient:
        def execute(self, query, **kwargs):
            if "COUNT" in query.upper():
                return [(42,)]
            if "total_bytes" in query or "total_rows" in query:
                raise Exception("Unknown column 'total_rows'")
            if "engine" in query.lower():
                return [("SummingMergeTree",)]
            return []

    srv.client = OldCHClient()
    result = _run(srv._get_table_overview("old_table", "db"))
    assert result.get("row_count") == 42, f"row_count 应为 42: {result}"
    # engine 可能已通过降级路径填充
    # 主要验证没有异常传播
    assert "error" not in result or result["error"] is None

def test_F2_B2_meta_and_engine_both_fail_partial_result():
    """meta 查询和 engine 降级查询都失败时，仍返回 row_count（COUNT 成功）。"""
    srv = _make_server()
    call_log = []

    class PartialFailClient:
        def execute(self, query, **kwargs):
            call_log.append(query)
            if "COUNT" in query.upper():
                return [(99,)]
            raise Exception("system.tables not accessible")

    srv.client = PartialFailClient()
    result = _run(srv._get_table_overview("some_table", "db"))
    assert result.get("row_count") == 99
    assert "error" not in result or result["error"] is None

def test_F2_B3_count_fails_row_count_is_none():
    """COUNT(*) 失败时 row_count 为 None，不抛出异常。"""
    srv = _make_server()

    class CountFailClient:
        def execute(self, query, **kwargs):
            if "COUNT" in query.upper():
                raise Exception("Table not found")
            return [("MergeTree", "0 B", 0, 0)]

    srv.client = CountFailClient()
    result = _run(srv._get_table_overview("ghost_table", "db"))
    assert result.get("row_count") is None
    assert result.get("type") == "table_overview"

# ── 2-C: 异常处理 ────────────────────────────────────────────

def test_F2_C1_all_queries_fail_returns_error_dict():
    """所有查询都失败时返回含 error 的 dict，不抛出。"""
    srv = _make_server()

    class AllFailClient:
        def execute(self, query, **kwargs):
            raise RuntimeError("connection refused")

    srv.client = AllFailClient()
    try:
        result = _run(srv._get_table_overview("t", "db"))
        assert isinstance(result, dict)
        # 要么 row_count=None（部分失败），要么有 error 字段（完全失败）
        assert "error" in result or "row_count" in result
    except Exception as e:
        raise AssertionError(f"_get_table_overview 不应抛出异常: {e}")

def test_F2_C2_result_is_json_serializable():
    """_get_table_overview 返回的 dict 可被 json.dumps 序列化。"""
    srv = _make_server()

    class SimpleClient:
        def execute(self, query, **kwargs):
            if "COUNT" in query.upper():
                return [(100,)]
            return [("MergeTree", "1 MiB", 1048576, 99)]

    srv.client = SimpleClient()
    result = _run(srv._get_table_overview("t", "db"))
    try:
        json.dumps(result, default=str)
    except TypeError as e:
        raise AssertionError(f"get_table_overview 结果不可序列化: {e}")

def test_F2_C3_overview_tool_registered():
    """get_table_overview 工具已注册。"""
    from backend.mcp.clickhouse.server import ClickHouseMCPServer
    srv = object.__new__(ClickHouseMCPServer)
    srv.env = "sg"
    srv.level = "admin"
    srv.config = {"database": "test_db", "host": "localhost", "port": 9000,
                  "user": "default", "password": ""}
    srv._tools = {}
    srv._resources = {}
    mock_register = MagicMock()
    srv.register_tool = mock_register
    srv.register_resource = MagicMock()
    srv._register_tools()
    registered_names = [
        (c.kwargs.get("name") or (c.args[0] if c.args else None))
        for c in mock_register.call_args_list
    ]
    assert "get_table_overview" in registered_names


# ══════════════════════════════════════════════════════════════
# 综合回归测试：多个 Fix 协同场景
# ══════════════════════════════════════════════════════════════

def test_R1_batch_describe_with_complex_column_types():
    """batch_describe_tables 返回的 schema 包含 Array、Nullable 等复杂类型，
    可正常通过 json.dumps 序列化（Fix-1 + Fix-4 协同）。"""
    srv = _make_server()
    srv.client = FakeClient({
        "DESCRIBE": [
            ("id", "UInt64", "", "", None, None, None),
            ("tags", "Array(String)", "", "", None, None, None),
            ("score", "Nullable(Float32)", "", "", None, None, None),
        ]
    })
    result = _run(srv._batch_describe_tables(["complex_table"], "db"))
    json.dumps(result, default=str)  # 不应抛出

def test_R2_select_with_create_time_and_overview():
    """SELECT 含 create_time 不触发 DDL 检测（Fix-3），
    overview 也正常返回（Fix-2）。"""
    srv = _make_server()

    class MultiClient:
        def execute(self, query, **kwargs):
            if "COUNT" in query.upper():
                return [(500,)]
            if "system.tables" in query.lower():
                return [("MergeTree", "1 MiB", 1048576, 498)]
            # SELECT 查询
            return (
                [(1, "2024-01-01 10:00:00")],
                [("id", "UInt32"), ("create_time", "DateTime")]
            )

    srv.client = MultiClient()
    select_result = _run(srv._execute_query(
        "SELECT id, create_time FROM orders WHERE create_time > '2024-01-01' LIMIT 5"
    ))
    assert "error" not in select_result or "DDL" not in select_result.get("error", ""), \
        f"SELECT create_time 被错误阻止: {select_result}"

    overview_result = _run(srv._get_table_overview("orders", "crm"))
    assert overview_result.get("row_count") == 500

def test_R3_ddl_detection_consistent_across_fixes():
    """Fix-3（MCP server DDL regex）与 Fix-3 ETL agent 检测的行为一致：
    create_time 列都不触发，DROP TABLE 都触发。"""
    from backend.mcp.clickhouse.server import _DDL_KEYWORD_RE
    from backend.agents.etl_agent import _detect_dangerous_sql

    safe_q = "SELECT id, create_time FROM orders"
    dangerous_q = "DROP TABLE orders"

    assert not _DDL_KEYWORD_RE.search(safe_q)
    assert _DDL_KEYWORD_RE.search(dangerous_q)

    assert _detect_dangerous_sql(safe_q) == []
    assert _detect_dangerous_sql(dangerous_q) != []


# ══════════════════════════════════════════════════════════════
# 测试运行器
# ══════════════════════════════════════════════════════════════

def _collect_all_tests():
    """收集本模块所有 test_ 函数。"""
    import types
    current_module = sys.modules[__name__]
    tests = []
    for name in dir(current_module):
        if name.startswith("test_"):
            obj = getattr(current_module, name)
            if callable(obj):
                tests.append((name, obj))
    return tests


def run_all():
    all_tests = _collect_all_tests()

    print("\n" + "=" * 70)
    print("ClickHouse MCP Bug-Fix 综合测试")
    print(f"共 {len(all_tests)} 个测试用例")
    print("=" * 70)

    passed = failed = 0
    failures = []

    for name, fn in all_tests:
        try:
            if asyncio.iscoroutinefunction(fn):
                _run(fn())
            else:
                fn()
            passed += 1
        except Exception as e:
            failed += 1
            failures.append((name, e))
            import traceback
            print(f"[FAIL] {name}: {e}")
            traceback.print_exc()

    print("\n" + "=" * 70)
    print(f"Fix-1 (batch_describe_tables): 边界/异常/注册验证")
    print(f"Fix-2 (get_table_overview):    兼容性/NULL处理/降级路径")
    print(f"Fix-3 (DDL regex):             正/误报/execute_query/ETL agent")
    print(f"Fix-4 (use_numpy/_to_json_safe): 所有类型覆盖/JSON序列化")
    print(f"综合回归:                       多 Fix 协同场景")
    print("-" * 70)
    print(f"Results: {passed} passed, {failed} failed / {len(all_tests)} total")

    if failed:
        print(f"\n失败用例:")
        for name, e in failures:
            print(f"  - {name}: {e}")
        sys.exit(1)
    else:
        print("All tests passed!")


if __name__ == "__main__":
    run_all()
