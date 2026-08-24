"""
test_csv_tail.py — CSV 尾部边界扫描单元测试（v2.16 CSV keyset 续传 L1 层）

被测：backend/services/csv_tail.py
      CsvRecordBoundaryScanner / extract_cursor_from_record / split_record_fields

为什么这一层最重要：
    CSV keyset 续传的正确性完全押在「断流后能否精确切到最后一条完整记录」上。
    切早了 → 丢数据；切晚了 → 文件里留半条残记录，下游解析炸或静默错列。
    而 ClickHouse CSV 是 RFC4180（IDN 实测确认），引号内可以有逗号、换行、`""`，
    所以按行/按逗号切一定错。本文件用真实的 ClickHouse 输出形态覆盖全部边界。

覆盖维度：

  A (6)  — 记录边界基础
           A1: 断在普通行中间 → 回退到上一个 \\n
           A2: 恰好切在记录边界 → 无残缺尾部
           A3: 空输入 / 只有 BOM+表头
           A4: 首窗口首行都没到 → 无完整记录
           A5: 整段无换行（单条超长未完成记录）
           A6: CRLF 行尾 → 切在 \\n 之后

  B (5)  — RFC4180 引号
           B1: 引号内逗号不算分隔符
           B2: 引号内换行不算记录结束
           B3: `\"\"` 转义引号
           B4: 断在引号内的换行处
           B5: 断在 `\"\"` 转义序列中间（跨 chunk）

  C (4)  — 跨 chunk 状态携带
           C1: 逐字节喂入 == 一次性喂入
           C2: chunk 切在引号中间
           C3: chunk 切在 `\"\"` 正中间
           C4: 任意随机分片下结果一致

  D (7)  — 游标提取
           D1: 普通字段
           D2: 游标值含逗号（被引号包裹）— split(',') 会错
           D3: 游标值含 `\"\"` 转义引号
           D4: 游标值含换行
           D5: 列名/游标含中文与空格
           D6: 空字段 / NULL 字面量 \\N
           D7: 列索引越界 / 空记录 → None

  E (3)  — 与真实 ClickHouse 输出形态对齐（P0 实测字节回放）
           E1: P0 探针的真实 CSV 字节 → 记录数、字段值正确
           E2: 物理行数 != 记录数（wc -l 不可信）
           E3: 真实字节任意分片下边界一致

共计: 25 个测试用例

运行：
    /d/ProgramData/Anaconda3/envs/dataagent/python.exe -m pytest test_csv_tail.py -v -s
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(1, os.path.join(os.path.dirname(__file__), "backend"))
os.environ.setdefault("ENABLE_AUTH", "False")

import pytest  # noqa: E402

from backend.services.csv_tail import (  # noqa: E402
    CsvRecordBoundaryScanner,
    extract_cursor_from_record,
    split_record_fields,
)

BOM = b"\xef\xbb\xbf"


def _scan(*chunks: bytes, start: int = 0) -> CsvRecordBoundaryScanner:
    s = CsvRecordBoundaryScanner(start_offset=start)
    for c in chunks:
        s.feed(c)
    return s


def _last_record(buf: bytes, s: CsvRecordBoundaryScanner, *, start: int = 0) -> bytes:
    """按 scanner 报告的区间从 buf 取出最后一条完整记录（buf 以 start 为偏移原点）。"""
    span = s.last_record_span
    assert span is not None, "期望存在完整记录"
    a, b = span
    return buf[a - start:b - start]


# ═════════════════════════════════════════════════════════════════════════════
# Section A — 记录边界基础
# ═════════════════════════════════════════════════════════════════════════════

class TestRecordBoundaryBasics:

    def test_A1_cut_mid_row_rewinds_to_previous_newline(self):
        """A1: 断在普通行中间 → last_complete_end 回退到上一个 \\n 之后"""
        buf = b"id,name\n1,alice\n2,bo"
        s = _scan(buf)
        assert s.record_count == 2, "表头 + 1 行数据 = 2 条完整记录"
        assert s.last_complete_end == len(b"id,name\n1,alice\n")
        assert s.has_incomplete_tail is True, "尾部 '2,bo' 是残缺记录"
        assert _last_record(buf, s) == b"1,alice\n"

    def test_A2_cut_exactly_on_boundary_has_no_incomplete_tail(self):
        """A2: 恰好切在记录边界 → 无残缺尾部"""
        buf = b"id,name\n1,alice\n"
        s = _scan(buf)
        assert s.record_count == 2
        assert s.last_complete_end == len(buf)
        assert s.has_incomplete_tail is False

    def test_A3_only_header_yields_one_record(self):
        """A3: 只有表头（含 BOM 由调用方跳过）→ 1 条完整记录"""
        header = b"id,name\n"
        s = _scan(header, start=len(BOM))
        assert s.record_count == 1
        assert s.last_complete_end == len(BOM) + len(header)
        assert s.has_incomplete_tail is False

    def test_A4_no_complete_record_yet(self):
        """A4: 首行都没写完 → 无完整记录，last_complete_end 停在起点"""
        s = _scan(b"id,na", start=3)
        assert s.record_count == 0
        assert s.last_record_span is None
        assert s.last_complete_end == 3, "应停在 start_offset（BOM 之后）"
        assert s.has_incomplete_tail is True

    def test_A5_single_huge_unterminated_record(self):
        """A5: 整段无换行 → 不产出任何完整记录，不留半条"""
        s = _scan(b"x" * 100_000)
        assert s.record_count == 0
        assert s.last_complete_end == 0

    def test_A6_crlf_line_endings_cut_after_lf(self):
        """A6: CRLF 行尾 → 边界落在 \\n 之后（不是 \\r 之前）"""
        buf = b"id,name\r\n1,alice\r\n2,bo"
        s = _scan(buf)
        assert s.record_count == 2
        assert s.last_complete_end == len(b"id,name\r\n1,alice\r\n")
        rec = _last_record(buf, s)
        assert rec == b"1,alice\r\n"
        assert split_record_fields(rec) == ["1", "alice"], "CRLF 不应污染字段值"


# ═════════════════════════════════════════════════════════════════════════════
# Section B — RFC4180 引号
# ═════════════════════════════════════════════════════════════════════════════

class TestQuoting:

    def test_B1_comma_inside_quotes_is_data(self):
        """B1: 引号内逗号不是分隔符"""
        buf = b'id,val\n1,"a,b"\n'
        s = _scan(buf)
        assert s.record_count == 2
        assert split_record_fields(_last_record(buf, s)) == ["1", "a,b"]

    def test_B2_newline_inside_quotes_is_not_record_end(self):
        """B2: 引号内换行不算记录结束 —— 一条记录跨两个物理行"""
        buf = b'id,val\n1,"line1\nline2"\n'
        s = _scan(buf)
        assert s.record_count == 2, f"应为 2 条记录（表头+1 行），实际 {s.record_count}"
        assert buf.count(b"\n") == 3, "但物理行数是 3 —— 这正是 wc -l 不可信的原因"
        assert split_record_fields(_last_record(buf, s)) == ["1", "line1\nline2"]

    def test_B3_escaped_double_quote(self):
        """B3: `\"\"` 转义为字面双引号"""
        buf = b'id,val\n1,"he said ""hi"""\n'
        s = _scan(buf)
        assert s.record_count == 2
        assert split_record_fields(_last_record(buf, s)) == ["1", 'he said "hi"']

    def test_B4_cut_inside_quoted_newline(self):
        """B4: 断流点恰好落在引号内的换行处 → 不能把它当记录结束"""
        # 引号未闭合，'\n' 是数据
        buf = b'id,val\n1,"line1\n'
        s = _scan(buf)
        assert s.in_quote is True, "应仍处于引号内"
        assert s.record_count == 1, "只有表头是完整记录"
        assert s.last_complete_end == len(b"id,val\n")
        assert s.has_incomplete_tail is True

    def test_B5_cut_inside_escaped_quote_sequence(self):
        """B5: 断流点落在 `\"\"` 正中间 → 引号状态必须挂起，不能提前判定闭合"""
        # ...,"ab"  —— 末尾这个 " 到底是闭合还是 "" 前半，尚不可知
        s = _scan(b'id,val\n1,"ab"')
        assert s.record_count == 1
        assert s.in_quote is True, "pending_quote 挂起期间仍应算引号内"
        # 续上一个 " → 是 `""` 转义，仍在引号内
        s.feed(b'"')
        assert s.in_quote is True
        # 再补 c" 与换行 → 闭合并结束记录
        s.feed(b'c"\n')
        assert s.record_count == 2
        assert s.in_quote is False


# ═════════════════════════════════════════════════════════════════════════════
# Section C — 跨 chunk 状态携带
# ═════════════════════════════════════════════════════════════════════════════

_TRICKY = (
    b'id,name,note\n'
    b'1,"a,b","he said ""hi"""\n'
    b'2,"multi\nline\nvalue",plain\n'
    b'3,simple,"trailing,comma"\n'
    b'4,partial'          # 残缺尾部
)


class TestChunkCarry:

    def test_C1_bytewise_equals_single_shot(self):
        """C1: 逐字节喂入结果 == 一次性喂入"""
        whole = _scan(_TRICKY)
        per_byte = _scan(*[_TRICKY[i:i + 1] for i in range(len(_TRICKY))])
        assert per_byte.record_count == whole.record_count
        assert per_byte.last_complete_end == whole.last_complete_end
        assert per_byte.last_record_span == whole.last_record_span
        assert per_byte.in_quote == whole.in_quote

    def test_C2_chunk_split_inside_quotes(self):
        """C2: chunk 边界切在引号内容中间"""
        cut = _TRICKY.index(b"multi") + 3
        a = _scan(_TRICKY[:cut], _TRICKY[cut:])
        b = _scan(_TRICKY)
        assert a.last_complete_end == b.last_complete_end
        assert a.record_count == b.record_count

    def test_C3_chunk_split_exactly_between_escaped_quotes(self):
        """C3: chunk 恰好切在 `\"\"` 两个引号之间（最刁的一种）"""
        needle = b'""hi""'
        idx = _TRICKY.index(needle)
        cut = idx + 1          # 切在第一个 " 之后、第二个 " 之前
        a = _scan(_TRICKY[:cut], _TRICKY[cut:])
        b = _scan(_TRICKY)
        assert a.last_complete_end == b.last_complete_end
        assert a.record_count == b.record_count
        assert a.last_record_span == b.last_record_span

    @pytest.mark.parametrize("size", [1, 2, 3, 5, 7, 11, 13, 17, 29, 64, 4096])
    def test_C4_arbitrary_chunk_sizes_agree(self, size):
        """C4: 任意分片大小下结果一致"""
        ref = _scan(_TRICKY)
        got = _scan(*[_TRICKY[i:i + size] for i in range(0, len(_TRICKY), size)])
        assert got.last_complete_end == ref.last_complete_end, f"size={size}"
        assert got.record_count == ref.record_count, f"size={size}"
        assert got.last_record_span == ref.last_record_span, f"size={size}"


# ═════════════════════════════════════════════════════════════════════════════
# Section D — 游标提取
# ═════════════════════════════════════════════════════════════════════════════

class TestCursorExtraction:

    def test_D1_plain_field(self):
        """D1: 普通字段按索引取值"""
        rec = b"1,alice,2026-08-24\n"
        assert extract_cursor_from_record(rec, 0) == "1"
        assert extract_cursor_from_record(rec, 2) == "2026-08-24"

    def test_D2_cursor_value_contains_comma(self):
        """D2: 游标值含逗号（被引号包裹）—— split(',') 会取错，这是本函数存在的理由"""
        rec = b'1,"ORD,2026,0001",x\n'
        assert extract_cursor_from_record(rec, 1) == "ORD,2026,0001"
        # 反证：朴素切分会得到错误值
        naive = rec.decode().rstrip("\n").split(",")[1]
        assert naive == '"ORD', f"朴素 split 应该出错，实际 {naive!r}"

    def test_D3_cursor_value_contains_escaped_quote(self):
        """D3: 游标值含 `\"\"` 转义引号"""
        rec = b'1,"a""b",x\n'
        assert extract_cursor_from_record(rec, 1) == 'a"b'

    def test_D4_cursor_value_contains_newline(self):
        """D4: 游标值含换行（记录跨物理行）"""
        rec = b'1,"line1\nline2",x\n'
        assert extract_cursor_from_record(rec, 1) == "line1\nline2"

    def test_D5_cjk_and_space_in_values(self):
        """D5: 中文 + 空格的游标值（对应 cursor_column 允许中文/空格的策略）"""
        rec = '1,"订单 编号 A",x\n'.encode("utf-8")
        assert extract_cursor_from_record(rec, 1) == "订单 编号 A"

    def test_D6_empty_and_null_literal(self):
        """D6: 空字段返回 ""；ClickHouse 的 NULL 字面量 \\N 原样返回，由上层判定"""
        rec = b"1,,x\n"
        assert extract_cursor_from_record(rec, 1) == ""
        rec_null = b"1,\\N,x\n"
        assert extract_cursor_from_record(rec_null, 1) == "\\N", (
            "NULL 必须原样返回，让上层触发 keyset NULL fast-fail"
        )

    def test_D7_out_of_range_and_empty_record(self):
        """D7: 索引越界 / 空记录 → None（不抛，由调用方决定报错）"""
        assert extract_cursor_from_record(b"1,2\n", 5) is None
        assert extract_cursor_from_record(b"1,2\n", -1) is None
        assert extract_cursor_from_record(b"", 0) is None

    def test_D7b_header_locates_cursor_index_with_bom(self):
        """D7b: 带 BOM 的表头解析出的首列名不应带 \\ufeff（否则列定位失败）"""
        header = BOM + b"call_record_id,name\n"
        fields = split_record_fields(header)
        assert fields[0] == "call_record_id", f"BOM 未被吸收: {fields[0]!r}"
        assert fields.index("name") == 1


# ═════════════════════════════════════════════════════════════════════════════
# Section E — 真实 ClickHouse 输出字节回放（P0 IDN 实测）
# ═════════════════════════════════════════════════════════════════════════════

# 下面这串是 P0 探针从 IDN 真实取回的 CSVWithNames 字节（原样，未加工）。
# 用它做回放，保证本模块对齐真实服务端行为而非我们臆想的格式。
_REAL_CH_CSV = (
    b'"f_comma","f_quote","f_newline","f_tab","f_empty","f_null_str","f_null_int",'
    b'"f_uint64_max","f_int64_min","f_int64_17digit","f_float","f_date",'
    b'"f_datetime","f_both"\n'
    b'"a,b","he said ""hi""","line1\nline2","tab\there","",\\N,\\N,'
    b'18446744073709551615,-9223372036854775808,12345678901234567,1.5,'
    b'"2026-08-24","2026-08-24 13:45:06","quoted,and ""both"""\n'
)


class TestRealClickHouseBytes:

    def test_E1_real_bytes_parse_correctly(self):
        """E1: 真实 ClickHouse 字节 → 记录数与字段值全部正确"""
        s = _scan(_REAL_CH_CSV)
        assert s.record_count == 2, f"表头 + 1 行数据，实际 {s.record_count}"
        assert s.has_incomplete_tail is False
        assert s.last_complete_end == len(_REAL_CH_CSV)

        header_end = s.last_record_span[0]
        header = split_record_fields(_REAL_CH_CSV[:header_end])
        row = split_record_fields(_last_record(_REAL_CH_CSV, s))
        assert len(header) == 14
        d = dict(zip(header, row))
        assert d["f_comma"] == "a,b"
        assert d["f_quote"] == 'he said "hi"'
        assert d["f_newline"] == "line1\nline2"
        assert d["f_tab"] == "tab\there", "CSV 里制表符不转义"
        assert d["f_empty"] == ""
        assert d["f_null_str"] == "\\N", "IDN 实测 NULL 字面量为 \\N"
        assert d["f_uint64_max"] == "18446744073709551615", "大整数原样，无科学计数法"
        assert d["f_both"] == 'quoted,and "both"'

    def test_E2_physical_lines_exceed_record_count(self):
        """E2: 物理行数 > 记录数 —— 手册里「wc -l 不可信」这条有实测依据"""
        s = _scan(_REAL_CH_CSV)
        physical = _REAL_CH_CSV.rstrip(b"\n").count(b"\n") + 1
        assert physical == 3, f"物理行 3（f_newline 含换行），实际 {physical}"
        assert s.record_count == 2
        assert physical > s.record_count

    @pytest.mark.parametrize("size", [1, 3, 8, 37, 128, 1024])
    def test_E3_real_bytes_any_chunking(self, size):
        """E3: 真实字节任意分片下边界一致"""
        ref = _scan(_REAL_CH_CSV)
        got = _scan(*[_REAL_CH_CSV[i:i + size]
                      for i in range(0, len(_REAL_CH_CSV), size)])
        assert got.record_count == ref.record_count, f"size={size}"
        assert got.last_complete_end == ref.last_complete_end, f"size={size}"
        assert got.last_record_span == ref.last_record_span, f"size={size}"


# ═════════════════════════════════════════════════════════════════════════════
# 断流→截断→续传 的端到端字节级演练（不涉 IO，纯逻辑验证切点正确）
# ═════════════════════════════════════════════════════════════════════════════

class TestTruncateAndResumeSemantics:

    @pytest.mark.parametrize("cut", list(range(1, len(_REAL_CH_CSV))))
    def test_F1_truncate_at_any_cut_point_never_leaves_partial_record(self, cut):
        """F1: 在**任意**字节位置模拟断流，按 scanner 截断后：
        剩余内容必须能被 csv.reader 完整解析，且不多不少。

        这是整个续传方案的核心不变量 —— 穷举所有断点。
        """
        import csv as _csv
        import io as _io

        partial = _REAL_CH_CSV[:cut]
        s = _scan(partial)
        truncated = partial[:s.last_complete_end]

        # 截断后的内容要么为空，要么是若干条完整记录
        rows = list(_csv.reader(_io.StringIO(
            truncated.decode("utf-8-sig"), newline="")))
        assert len(rows) == s.record_count, (
            f"cut={cut}: scanner 报 {s.record_count} 条，csv.reader 解出 {len(rows)} 条"
        )
        # 每条完整记录都必须是满 14 列（真实数据的列数）
        for r in rows:
            assert len(r) == 14, f"cut={cut}: 出现残缺记录 {r!r}"
        # 截断只会丢弃尾部，不会改动已确认的字节
        assert _REAL_CH_CSV.startswith(truncated)
