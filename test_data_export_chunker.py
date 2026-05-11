"""
data_export_chunker 单元测试 — v2.13

A · split_date_range
B · inject_date_filter（占位符 + 包装双模式）
C · build_chunk_filename
D · validate_chunk_config

运行：
    /d/ProgramData/Anaconda3/envs/dataagent/python.exe -m pytest test_data_export_chunker.py -v -s
"""
import os
import sys
from datetime import date, datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
os.environ.setdefault("ENABLE_AUTH", "False")

from backend.services.data_export_chunker import (  # noqa: E402
    DateChunk,
    NormalizedChunkConfig,
    MAX_CHUNK_DAYS,
    MIN_CHUNK_DAYS,
    build_chunk_filename,
    has_placeholders,
    inject_date_filter,
    split_date_range,
    subdivide_date_range,
    validate_chunk_config,
)


# =============================================================================
# A · split_date_range
# =============================================================================

class TestSplitDateRange:

    def test_a1_clean_division(self):
        """A1: 整除 — 30 天 / 10 天 = 3 块整齐切分"""
        result = split_date_range(date(2025, 4, 1), date(2025, 4, 30), 10)
        assert len(result) == 3
        assert result[0] == DateChunk(0, date(2025, 4, 1), date(2025, 4, 10))
        assert result[1] == DateChunk(1, date(2025, 4, 11), date(2025, 4, 20))
        assert result[2] == DateChunk(2, date(2025, 4, 21), date(2025, 4, 30))

    def test_a2_uneven_last_chunk_shorter(self):
        """A2: 不整除 — 最后一块自适应缩短"""
        result = split_date_range(date(2025, 4, 1), date(2025, 4, 25), 10)
        assert len(result) == 3
        assert result[2].start == date(2025, 4, 21)
        assert result[2].end == date(2025, 4, 25)
        assert result[2].days == 5

    def test_a3_single_day(self):
        """A3: start == end → 单块单日"""
        result = split_date_range(date(2025, 4, 5), date(2025, 4, 5), 10)
        assert len(result) == 1
        assert result[0].days == 1
        assert result[0].start == result[0].end == date(2025, 4, 5)

    def test_a4_chunk_days_one(self):
        """A4: chunk_days=1 → 每天一块"""
        result = split_date_range(date(2025, 4, 1), date(2025, 4, 5), 1)
        assert len(result) == 5
        assert all(c.days == 1 for c in result)
        assert [c.start for c in result] == [
            date(2025, 4, 1), date(2025, 4, 2), date(2025, 4, 3),
            date(2025, 4, 4), date(2025, 4, 5),
        ]

    def test_a5_range_smaller_than_chunk(self):
        """A5: 范围小于 chunk_days → 单块覆盖全部"""
        result = split_date_range(date(2025, 4, 1), date(2025, 4, 5), 30)
        assert len(result) == 1
        assert result[0].start == date(2025, 4, 1)
        assert result[0].end == date(2025, 4, 5)

    def test_a6_cross_month(self):
        """A6: 跨月切分"""
        result = split_date_range(date(2025, 3, 25), date(2025, 4, 5), 5)
        assert len(result) == 3
        assert result[0] == DateChunk(0, date(2025, 3, 25), date(2025, 3, 29))
        assert result[1] == DateChunk(1, date(2025, 3, 30), date(2025, 4, 3))
        assert result[2] == DateChunk(2, date(2025, 4, 4), date(2025, 4, 5))

    def test_a7_cross_year(self):
        """A7: 跨年切分"""
        result = split_date_range(date(2024, 12, 28), date(2025, 1, 5), 5)
        assert len(result) == 2
        assert result[0].start == date(2024, 12, 28)
        assert result[1].end == date(2025, 1, 5)

    def test_a8_leap_year_feb(self):
        """A8: 闰年 2 月 29 日处理"""
        result = split_date_range(date(2024, 2, 25), date(2024, 3, 5), 5)
        assert len(result) == 2
        # 块 1: 2024-02-25 ~ 2024-02-29 (5 days)
        assert result[0].end == date(2024, 2, 29)
        # 块 2: 2024-03-01 ~ 2024-03-05 (5 days)
        assert result[1].start == date(2024, 3, 1)

    def test_a9_start_after_end_raises(self):
        """A9: start > end → ValueError"""
        with pytest.raises(ValueError, match="不能晚于"):
            split_date_range(date(2025, 4, 30), date(2025, 4, 1), 10)

    def test_a10_chunk_days_zero_raises(self):
        """A10: chunk_days=0 → ValueError"""
        with pytest.raises(ValueError, match=">="):
            split_date_range(date(2025, 4, 1), date(2025, 4, 30), 0)

    def test_a11_chunk_days_negative_raises(self):
        """A11: chunk_days 负数 → ValueError"""
        with pytest.raises(ValueError):
            split_date_range(date(2025, 4, 1), date(2025, 4, 30), -1)

    def test_a12_chunk_days_too_large_raises(self):
        """A12: chunk_days > MAX_CHUNK_DAYS → ValueError"""
        with pytest.raises(ValueError, match="<="):
            split_date_range(date(2025, 4, 1), date(2025, 4, 30), MAX_CHUNK_DAYS + 1)

    def test_a13_max_chunk_days_ok(self):
        """A13: chunk_days = MAX_CHUNK_DAYS → 通过"""
        result = split_date_range(date(2025, 1, 1), date(2025, 12, 31), MAX_CHUNK_DAYS)
        # 365 天 / 90 天 → 5 块（90+90+90+90+5）
        assert len(result) == 5

    def test_a14_string_dates_rejected(self):
        """A14: 字符串日期参数被拒绝（须传 date 类型）"""
        with pytest.raises(ValueError, match="必须是 date 类型"):
            split_date_range("2025-04-01", date(2025, 4, 30), 10)  # type: ignore

    def test_a15_index_monotonic(self):
        """A15: 切片索引连续且从 0 起"""
        result = split_date_range(date(2025, 1, 1), date(2025, 1, 25), 7)
        assert [c.index for c in result] == list(range(len(result)))

    def test_a16_no_gaps_no_overlaps(self):
        """A16: 切片之间无空隙、无重叠"""
        result = split_date_range(date(2025, 1, 1), date(2025, 6, 30), 13)
        for prev, cur in zip(result, result[1:]):
            from datetime import timedelta
            assert cur.start == prev.end + timedelta(days=1)
        # 总天数应等于原始范围
        total = sum(c.days for c in result)
        assert total == (date(2025, 6, 30) - date(2025, 1, 1)).days + 1


# =============================================================================
# B · inject_date_filter
# =============================================================================

class TestInjectDateFilter:

    def test_b1_placeholder_substitution(self):
        """B1: 占位符模式 — 双占位符替换"""
        sql = "SELECT * FROM events WHERE dt >= '{{date_start}}' AND dt <= '{{date_end}}'"
        result, mode = inject_date_filter(sql, None, date(2025, 4, 1), date(2025, 4, 10))
        assert mode == "placeholder"
        assert "'2025-04-01'" in result
        assert "'2025-04-10'" in result
        assert "{{date_start}}" not in result
        assert "{{date_end}}" not in result

    def test_b2_placeholder_priority_over_date_column(self):
        """B2: SQL 含占位符时优先使用占位符模式（即使提供了 date_column）"""
        sql = "SELECT * FROM t WHERE d = '{{date_start}}' OR d = '{{date_end}}'"
        result, mode = inject_date_filter(
            sql, date_column="some_col",
            chunk_start=date(2025, 4, 1), chunk_end=date(2025, 4, 10),
        )
        assert mode == "placeholder"
        assert "(t)" not in result and "_chunk_q" not in result  # 未走包装路径

    def test_b3_wrapper_mode(self):
        """B3: 无占位符 + 提供 date_column → 包装子查询"""
        sql = "SELECT user_id, event FROM events"
        result, mode = inject_date_filter(
            sql, "event_date",
            date(2025, 4, 1), date(2025, 4, 10),
        )
        assert mode == "wrapper"
        assert "SELECT * FROM (" in result
        assert ") AS _chunk_q" in result
        assert "_chunk_q.event_date >= '2025-04-01'" in result
        assert "_chunk_q.event_date <= '2025-04-10'" in result

    def test_b4_wrapper_strips_trailing_semicolon(self):
        """B4: 包装模式去除尾部分号，避免 SQL 语法错误"""
        sql = "SELECT * FROM events;"
        result, _ = inject_date_filter(sql, "dt", date(2025, 4, 1), date(2025, 4, 10))
        # 子查询尾部不应残留分号
        assert ";)" not in result
        assert "; ) AS" not in result
        assert "SELECT * FROM events) AS _chunk_q" in result

    def test_b5_no_placeholder_no_column_raises(self):
        """B5: 无占位符 + 无 date_column → ValueError"""
        with pytest.raises(ValueError, match="必须提供 date_column"):
            inject_date_filter("SELECT 1", None, date(2025, 4, 1), date(2025, 4, 10))

    def test_b6_only_one_placeholder_falls_back_to_wrapper(self):
        """B6: 只有 date_start 占位符（缺 date_end）→ 不算占位符模式，走包装"""
        sql = "SELECT * FROM t WHERE d >= '{{date_start}}'"
        result, mode = inject_date_filter(sql, "dt", date(2025, 4, 1), date(2025, 4, 10))
        assert mode == "wrapper"

    def test_b7_invalid_date_column_rejected(self):
        """B7: date_column 含非法字符 → ValueError（防 SQL 注入）"""
        with pytest.raises(ValueError, match="非法字符"):
            inject_date_filter(
                "SELECT 1", "evil; DROP TABLE",
                date(2025, 4, 1), date(2025, 4, 10),
            )

    def test_b8_date_column_with_space_rejected(self):
        """B8: 列名含空格 → 拒绝"""
        with pytest.raises(ValueError, match="非法字符"):
            inject_date_filter("SELECT 1", "my col", date(2025, 4, 1), date(2025, 4, 10))

    def test_b9_date_column_with_dash_rejected(self):
        """B9: 列名含连字符（标识符不合法）→ 拒绝"""
        with pytest.raises(ValueError, match="非法字符"):
            inject_date_filter("SELECT 1", "my-col", date(2025, 4, 1), date(2025, 4, 10))

    def test_b10_date_column_underscore_ok(self):
        """B10: 列名含下划线 → OK"""
        result, mode = inject_date_filter(
            "SELECT 1", "event_date_utc",
            date(2025, 4, 1), date(2025, 4, 10),
        )
        assert mode == "wrapper"
        assert "_chunk_q.event_date_utc" in result

    def test_b11_has_placeholders(self):
        """B11: has_placeholders 工具函数行为"""
        assert has_placeholders("WHERE d >= '{{date_start}}' AND d <= '{{date_end}}'") is True
        assert has_placeholders("WHERE d = '{{date_start}}'") is False  # 缺一个
        assert has_placeholders("WHERE d = 'static'") is False
        # 大小写敏感
        assert has_placeholders("WHERE d = '{{Date_Start}}' AND d = '{{Date_End}}'") is False

    def test_b12_iso_date_format(self):
        """B12: 注入的日期是 ISO YYYY-MM-DD 格式"""
        sql = "WHERE d >= '{{date_start}}' AND d <= '{{date_end}}'"
        result, _ = inject_date_filter(sql, None, date(2025, 1, 5), date(2025, 12, 31))
        assert "'2025-01-05'" in result
        assert "'2025-12-31'" in result


# =============================================================================
# C · build_chunk_filename
# =============================================================================

class TestBuildChunkFilename:

    def test_c1_basic(self):
        """C1: 基础格式 — name_YYYYMMDD_to_YYYYMMDD.xlsx"""
        result = build_chunk_filename("orders", date(2025, 4, 1), date(2025, 4, 10))
        assert result == "orders_20250401_to_20250410.xlsx"

    def test_c2_empty_name_defaults_to_export(self):
        """C2: 空名称 → 默认 'export'"""
        result = build_chunk_filename("", date(2025, 4, 1), date(2025, 4, 10))
        assert result == "export_20250401_to_20250410.xlsx"

    def test_c3_unsafe_chars_replaced(self):
        """C3: 不安全字符 → 下划线替换"""
        result = build_chunk_filename("a/b\\c:d", date(2025, 4, 1), date(2025, 4, 10))
        # 斜杠/反斜杠/冒号都被替换为下划线
        assert "/" not in result and "\\" not in result and ":" not in result

    def test_c4_long_name_truncated(self):
        """C4: 长名截断到 50 字符"""
        long_name = "x" * 100
        result = build_chunk_filename(long_name, date(2025, 4, 1), date(2025, 4, 10))
        # base 部分（_前）应 ≤ 50 字符
        base = result.split("_2025")[0]
        assert len(base) <= 50

    def test_c5_chinese_name_preserved(self):
        """C5: 中文名保留"""
        result = build_chunk_filename("用户行为", date(2025, 4, 1), date(2025, 4, 10))
        assert "用户行为" in result

    def test_c6_custom_extension(self):
        """C6: 支持自定义扩展名"""
        result = build_chunk_filename(
            "data", date(2025, 4, 1), date(2025, 4, 10),
            extension="csv",
        )
        assert result.endswith(".csv")

    def test_c7_only_unsafe_chars_falls_back(self):
        """C7: 全是不安全字符 → 回退 'export'"""
        result = build_chunk_filename("///***", date(2025, 4, 1), date(2025, 4, 10))
        assert result.startswith("export_")


# =============================================================================
# D · validate_chunk_config
# =============================================================================

class TestValidateChunkConfig:

    def test_d1_valid_placeholder_mode(self):
        """D1: 占位符模式合法配置"""
        sql = "WHERE d >= '{{date_start}}' AND d <= '{{date_end}}'"
        result = validate_chunk_config(
            {"date_start": "2025-04-01", "date_end": "2025-04-30", "chunk_days": 10},
            sql=sql,
        )
        assert result.mode == "placeholder"
        assert result.date_start == date(2025, 4, 1)
        assert result.date_end == date(2025, 4, 30)
        assert result.chunk_days == 10
        assert result.date_column is None

    def test_d2_valid_wrapper_mode(self):
        """D2: 包装模式合法配置"""
        result = validate_chunk_config(
            {
                "date_column": "event_date",
                "date_start": "2025-04-01",
                "date_end": "2025-04-30",
                "chunk_days": 10,
            },
            sql="SELECT * FROM events",
        )
        assert result.mode == "wrapper"
        assert result.date_column == "event_date"

    def test_d3_default_chunk_days(self):
        """D3: chunk_days 缺省 → 默认 10"""
        sql = "WHERE d >= '{{date_start}}' AND d <= '{{date_end}}'"
        result = validate_chunk_config(
            {"date_start": "2025-04-01", "date_end": "2025-04-30"},
            sql=sql,
        )
        assert result.chunk_days == 10

    def test_d4_start_after_end_raises(self):
        """D4: start > end → ValueError"""
        sql = "WHERE d >= '{{date_start}}' AND d <= '{{date_end}}'"
        with pytest.raises(ValueError, match="不能晚于"):
            validate_chunk_config(
                {"date_start": "2025-04-30", "date_end": "2025-04-01", "chunk_days": 10},
                sql=sql,
            )

    def test_d5_invalid_date_format_raises(self):
        """D5: 日期格式非 ISO → ValueError"""
        sql = "WHERE d >= '{{date_start}}' AND d <= '{{date_end}}'"
        with pytest.raises(ValueError, match="格式必须为"):
            validate_chunk_config(
                {"date_start": "2025/04/01", "date_end": "2025/04/30"},
                sql=sql,
            )

    def test_d6_chunk_days_zero_raises(self):
        """D6: chunk_days=0 → ValueError"""
        sql = "WHERE d >= '{{date_start}}' AND d <= '{{date_end}}'"
        with pytest.raises(ValueError, match=r"\[1, 90\]"):
            validate_chunk_config(
                {"date_start": "2025-04-01", "date_end": "2025-04-30", "chunk_days": 0},
                sql=sql,
            )

    def test_d7_chunk_days_too_large_raises(self):
        """D7: chunk_days > 90 → ValueError"""
        sql = "WHERE d >= '{{date_start}}' AND d <= '{{date_end}}'"
        with pytest.raises(ValueError, match=r"\[1, 90\]"):
            validate_chunk_config(
                {"date_start": "2025-04-01", "date_end": "2025-04-30", "chunk_days": 91},
                sql=sql,
            )

    def test_d8_chunk_days_not_int_raises(self):
        """D8: chunk_days 非 int → ValueError"""
        sql = "WHERE d >= '{{date_start}}' AND d <= '{{date_end}}'"
        with pytest.raises(ValueError, match="必须是整数"):
            validate_chunk_config(
                {"date_start": "2025-04-01", "date_end": "2025-04-30", "chunk_days": "10"},
                sql=sql,
            )

    def test_d9_no_placeholder_no_column_raises(self):
        """D9: SQL 无占位符 + 无 date_column → ValueError"""
        with pytest.raises(ValueError, match="必须提供 date_column"):
            validate_chunk_config(
                {"date_start": "2025-04-01", "date_end": "2025-04-30", "chunk_days": 10},
                sql="SELECT * FROM events",
            )

    def test_d10_invalid_date_column_raises(self):
        """D10: date_column 含非法字符 → ValueError"""
        with pytest.raises(ValueError, match="非法字符"):
            validate_chunk_config(
                {
                    "date_column": "evil; DROP",
                    "date_start": "2025-04-01",
                    "date_end": "2025-04-30",
                    "chunk_days": 10,
                },
                sql="SELECT 1",
            )

    def test_d11_missing_date_start_raises(self):
        """D11: 缺失 date_start → ValueError"""
        sql = "WHERE d >= '{{date_start}}' AND d <= '{{date_end}}'"
        with pytest.raises(ValueError):
            validate_chunk_config(
                {"date_end": "2025-04-30", "chunk_days": 10},
                sql=sql,
            )

    def test_d12_non_dict_raises(self):
        """D12: chunk_config 非 dict → ValueError"""
        with pytest.raises(ValueError, match="必须是对象"):
            validate_chunk_config("not a dict", sql="SELECT 1")  # type: ignore

    def test_d13_date_column_empty_string_treated_as_none(self):
        """D13: date_column 为空字符串/纯空白 → 等效 None"""
        sql = "WHERE d >= '{{date_start}}' AND d <= '{{date_end}}'"
        result = validate_chunk_config(
            {
                "date_column": "   ",
                "date_start": "2025-04-01",
                "date_end": "2025-04-30",
            },
            sql=sql,
        )
        assert result.date_column is None
        assert result.mode == "placeholder"

    def test_d14_chunk_days_bool_rejected(self):
        """D14: chunk_days 为 bool（True/False 是 int 的子类）→ 拒绝"""
        sql = "WHERE d >= '{{date_start}}' AND d <= '{{date_end}}'"
        with pytest.raises(ValueError, match="必须是整数"):
            validate_chunk_config(
                {"date_start": "2025-04-01", "date_end": "2025-04-30", "chunk_days": True},
                sql=sql,
            )

    def test_d15_date_objects_accepted(self):
        """D15: 直接传 date 对象（非字符串）也可"""
        sql = "WHERE d >= '{{date_start}}' AND d <= '{{date_end}}'"
        result = validate_chunk_config(
            {"date_start": date(2025, 4, 1), "date_end": date(2025, 4, 30), "chunk_days": 10},
            sql=sql,
        )
        assert result.date_start == date(2025, 4, 1)

    def test_d16_datetime_truncates_to_date(self):
        """D16: datetime 输入截断为 date"""
        sql = "WHERE d >= '{{date_start}}' AND d <= '{{date_end}}'"
        result = validate_chunk_config(
            {
                "date_start": datetime(2025, 4, 1, 13, 45),
                "date_end": datetime(2025, 4, 30, 23, 59),
                "chunk_days": 10,
            },
            sql=sql,
        )
        assert result.date_start == date(2025, 4, 1)
        assert result.date_end == date(2025, 4, 30)


# =============================================================================
# E · subdivide_date_range（v2.13 自动子块分裂辅助）
# =============================================================================

class TestSubdivideDateRange:

    def test_e1_five_days_halved_to_2_and_3(self):
        """E1: 5 天对半分 → 2 天 + 3 天（向下取整 5//2=2）"""
        result = subdivide_date_range(date(2026, 1, 1), date(2026, 1, 5))
        assert len(result) == 2
        assert result[0] == (date(2026, 1, 1), date(2026, 1, 2))
        assert result[1] == (date(2026, 1, 3), date(2026, 1, 5))

    def test_e2_two_days_halved_to_1_and_1(self):
        """E2: 2 天对半 → 1 天 + 1 天"""
        result = subdivide_date_range(date(2026, 1, 1), date(2026, 1, 2))
        assert len(result) == 2
        assert result[0] == (date(2026, 1, 1), date(2026, 1, 1))
        assert result[1] == (date(2026, 1, 2), date(2026, 1, 2))

    def test_e3_one_day_returns_self_no_split(self):
        """E3: 1 天单块 → 不可再分，返回自身"""
        result = subdivide_date_range(date(2026, 1, 1), date(2026, 1, 1))
        assert len(result) == 1
        assert result[0] == (date(2026, 1, 1), date(2026, 1, 1))

    def test_e4_three_days_halved(self):
        """E4: 3 天对半 → 1 天 + 2 天"""
        result = subdivide_date_range(date(2026, 1, 1), date(2026, 1, 3))
        # 3//2=1，前块 1 天，后块 2 天
        assert len(result) == 2
        assert result[0] == (date(2026, 1, 1), date(2026, 1, 1))
        assert result[1] == (date(2026, 1, 2), date(2026, 1, 3))

    def test_e5_no_gaps_no_overlaps_after_subdivide(self):
        """E5: 子块连续无空隙、无重叠"""
        from datetime import timedelta
        for days_total in range(1, 31):
            start = date(2026, 1, 1)
            end = start + timedelta(days=days_total - 1)
            subs = subdivide_date_range(start, end)
            # 总天数守恒
            total = sum((s[1] - s[0]).days + 1 for s in subs)
            assert total == days_total
            # 子块之间无空隙
            for prev, cur in zip(subs, subs[1:]):
                assert cur[0] == prev[1] + timedelta(days=1)

    def test_e6_recursive_subdivision_to_one_day(self):
        """E6: 递归对半分到 1 天块（模拟运行时多次失败的最坏情况）"""
        ranges = [(date(2026, 1, 1), date(2026, 1, 5))]
        max_iter = 10
        for _ in range(max_iter):
            new_ranges = []
            for s, e in ranges:
                if (e - s).days + 1 > 1:
                    new_ranges.extend(subdivide_date_range(s, e))
                else:
                    new_ranges.append((s, e))
            if new_ranges == ranges:
                break
            ranges = new_ranges
        # 最终所有子块都是 1 天
        assert all((e - s).days == 0 for s, e in ranges)
        # 5 个 1 天块（覆盖 5 天）
        assert len(ranges) == 5

    def test_e7_start_after_end_raises(self):
        """E7: start > end → ValueError"""
        with pytest.raises(ValueError, match="不能晚于"):
            subdivide_date_range(date(2026, 1, 5), date(2026, 1, 1))

    def test_e8_string_dates_rejected(self):
        """E8: 字符串日期参数被拒绝"""
        with pytest.raises(ValueError, match="必须是 date 类型"):
            subdivide_date_range("2026-01-01", date(2026, 1, 5))  # type: ignore


# =============================================================================
# F · subdivide_range（v2.14 通用化对半分裂,支持 date + datetime + min_unit）
# =============================================================================

from backend.services.data_export_chunker import subdivide_range  # noqa: E402


class TestSubdivideRange:

    def test_f1_date_days_gt_1_default_unit_day(self):
        """F1: 两端 date,days > 1,min_unit=day → 与老 subdivide_date_range 一致"""
        result = subdivide_range(date(2026, 1, 1), date(2026, 1, 5), min_unit="day")
        assert result == [
            (date(2026, 1, 1), date(2026, 1, 2)),
            (date(2026, 1, 3), date(2026, 1, 5)),
        ]

    def test_f2_date_day1_min_unit_day_no_split(self):
        """F2: 1 天块 + min_unit=day → 不可再分(老行为兜底)"""
        result = subdivide_range(date(2026, 1, 1), date(2026, 1, 1), min_unit="day")
        assert result == [(date(2026, 1, 1), date(2026, 1, 1))]

    def test_f3_date_day1_min_unit_hour_promotes_to_datetime(self):
        """F3: 1 天块 + min_unit=hour → 升级为 datetime [00:00:00, 23:59:59] 后对半"""
        result = subdivide_range(date(2026, 1, 1), date(2026, 1, 1), min_unit="hour")
        assert len(result) == 2
        # 第一半起点是 00:00:00,第二半终点是 23:59:59
        s1, e1 = result[0]
        s2, e2 = result[1]
        assert isinstance(s1, datetime) and isinstance(e2, datetime)
        assert s1 == datetime(2026, 1, 1, 0, 0, 0)
        assert e2 == datetime(2026, 1, 1, 23, 59, 59)
        # 中点应该在 12:00 左右(允许 ±2s 偏差因 floor 对秒取整)
        assert abs((e1 - datetime(2026, 1, 1, 11, 59, 59)).total_seconds()) <= 2
        # 两半连续,无 gap
        assert s2 == e1 + (e2 - e2.replace(microsecond=0)) + \
            (datetime(2026, 1, 1, 0, 0, 1) - datetime(2026, 1, 1, 0, 0, 0))

    def test_f4_datetime_12h_hour_unit_splits(self):
        """F4: datetime 12h 块 + min_unit=hour → 拆成两半(各 ~6h,远大于 1h floor)"""
        result = subdivide_range(
            datetime(2026, 1, 1, 0, 0, 0),
            datetime(2026, 1, 1, 11, 59, 59),
            min_unit="hour",
        )
        assert len(result) == 2

    def test_f5_datetime_1h_hour_unit_no_split(self):
        """F5: datetime 1h 块 + min_unit=hour → 不可再分(两半 < 1h floor)"""
        result = subdivide_range(
            datetime(2026, 1, 1, 0, 0, 0),
            datetime(2026, 1, 1, 0, 59, 59),
            min_unit="hour",
        )
        assert len(result) == 1
        assert result[0] == (
            datetime(2026, 1, 1, 0, 0, 0),
            datetime(2026, 1, 1, 0, 59, 59),
        )

    def test_f6_datetime_1h_minute_unit_splits(self):
        """F6: datetime 1h 块 + min_unit=minute → 拆成 ~30min+30min"""
        result = subdivide_range(
            datetime(2026, 1, 1, 0, 0, 0),
            datetime(2026, 1, 1, 0, 59, 59),
            min_unit="minute",
        )
        assert len(result) == 2

    def test_f7_recursive_subdivision_floors_at_unit(self):
        """F7: 反复细分,1 天 + min_unit=hour 最终止于 [1h, 2h) 区间的子块
        说明:floor=1h 的语义是「拆完两半各 < 1h 时停拆」,所以收敛后子块在 1h~2h"""
        ranges = [(datetime(2026, 1, 1, 0, 0, 0), datetime(2026, 1, 1, 23, 59, 59))]
        for _ in range(10):
            new_ranges = []
            for s, e in ranges:
                sub = subdivide_range(s, e, min_unit="hour")
                new_ranges.extend(sub)
            if len(new_ranges) == len(ranges):
                break
            ranges = new_ranges
        # 不变量:每个子块 total_seconds < 2 * floor(2*3600=7200s)
        # 否则它仍可继续拆。允许一点秒余量(原始 23:59:59 → 拆出来时有 +1s 边界)
        for s, e in ranges:
            assert (e - s).total_seconds() < 7200 + 60

    def test_f8_legacy_subdivide_date_range_equivalent(self):
        """F8: 老 subdivide_date_range == subdivide_range(min_unit=day)"""
        cases = [
            (date(2026, 1, 1), date(2026, 1, 5)),  # 5 天
            (date(2026, 1, 1), date(2026, 1, 2)),  # 2 天
            (date(2026, 1, 1), date(2026, 1, 1)),  # 1 天
        ]
        for s, e in cases:
            assert subdivide_date_range(s, e) == subdivide_range(s, e, min_unit="day")

    def test_f9_invalid_min_unit_raises(self):
        """F9: min_unit 非法值 → ValueError"""
        with pytest.raises(ValueError, match="min_unit"):
            subdivide_range(date(2026, 1, 1), date(2026, 1, 5), min_unit="second")  # type: ignore

    def test_f10_mixed_types_rejected(self):
        """F10: 一端 date 一端 datetime → ValueError"""
        with pytest.raises(ValueError, match="类型必须一致"):
            subdivide_range(date(2026, 1, 1), datetime(2026, 1, 1, 12, 0, 0), min_unit="hour")


# =============================================================================
# B 段补 · inject_date_filter datetime 支持
# =============================================================================

class TestInjectDateFilterDatetime:

    def test_b13_datetime_placeholder_mode(self):
        """B13: datetime 输入 placeholder 模式 → 字面量为 'YYYY-MM-DD HH:MM:SS'"""
        sql = "SELECT * FROM t WHERE ts >= '{{date_start}}' AND ts <= '{{date_end}}'"
        result, mode = inject_date_filter(
            sql, None,
            datetime(2026, 1, 1, 0, 0, 0),
            datetime(2026, 1, 1, 11, 59, 59),
        )
        assert mode == "placeholder"
        assert "'2026-01-01 00:00:00'" in result
        assert "'2026-01-01 11:59:59'" in result
        assert "{{date_start}}" not in result

    def test_b14_datetime_wrapper_mode(self):
        """B14: datetime 输入 wrapper 模式 → BETWEEN datetime 字面量"""
        sql = "SELECT * FROM t"
        result, mode = inject_date_filter(
            sql, "event_time",
            datetime(2026, 1, 1, 0, 0, 0),
            datetime(2026, 1, 1, 11, 59, 59),
        )
        assert mode == "wrapper"
        assert "event_time >= '2026-01-01 00:00:00'" in result
        assert "event_time <= '2026-01-01 11:59:59'" in result

    def test_b15_mixed_types_rejected(self):
        """B15: chunk_start=date, chunk_end=datetime → ValueError(类型不一致)"""
        sql = "SELECT * FROM t WHERE ts BETWEEN '{{date_start}}' AND '{{date_end}}'"
        with pytest.raises(ValueError, match="类型必须一致"):
            inject_date_filter(
                sql, None,
                date(2026, 1, 1),
                datetime(2026, 1, 1, 11, 59, 59),
            )


# =============================================================================
# C 段补 · build_chunk_filename datetime 支持
# =============================================================================

class TestBuildChunkFilenameDatetime:

    def test_c8_datetime_filename_contains_timestamp(self):
        """C8: datetime 输入 → 文件名含 YYYYMMDDTHHMMSS"""
        fn = build_chunk_filename(
            "job",
            datetime(2026, 1, 1, 0, 0, 0),
            datetime(2026, 1, 1, 11, 59, 59),
        )
        assert "20260101T000000" in fn
        assert "20260101T115959" in fn
        assert fn.endswith(".xlsx")

    def test_c9_datetime_different_timestamps(self):
        """C9: 不同 datetime 端点 → 文件名两端时间戳不同"""
        fn = build_chunk_filename(
            "job",
            datetime(2026, 1, 1, 0, 0, 0),
            datetime(2026, 1, 1, 1, 30, 0),
        )
        assert "_20260101T000000_to_20260101T013000.xlsx" in fn


# =============================================================================
# D 段补 · validate_chunk_config 新字段(min_subdivide_unit + cursor_column)
# =============================================================================

class TestValidateChunkConfigNewFields:

    def test_d17_default_min_subdivide_unit(self):
        """D17: 不传 min_subdivide_unit → 默认 'day'(老行为)"""
        sql = "SELECT * FROM t WHERE ts BETWEEN '{{date_start}}' AND '{{date_end}}'"
        cfg = {"date_start": "2026-01-01", "date_end": "2026-01-05", "chunk_days": 1}
        ncfg = validate_chunk_config(cfg, sql)
        assert ncfg.min_subdivide_unit == "day"

    def test_d18_invalid_min_subdivide_unit_rejected(self):
        """D18: min_subdivide_unit 非法值 → ValueError"""
        sql = "SELECT * FROM t WHERE ts BETWEEN '{{date_start}}' AND '{{date_end}}'"
        cfg = {
            "date_start": "2026-01-01", "date_end": "2026-01-05",
            "chunk_days": 1, "min_subdivide_unit": "second",
        }
        with pytest.raises(ValueError, match="min_subdivide_unit"):
            validate_chunk_config(cfg, sql)

    def test_d19_default_cursor_column_none(self):
        """D19: 不传 cursor_column → None"""
        sql = "SELECT * FROM t WHERE ts BETWEEN '{{date_start}}' AND '{{date_end}}'"
        cfg = {"date_start": "2026-01-01", "date_end": "2026-01-05", "chunk_days": 1}
        ncfg = validate_chunk_config(cfg, sql)
        assert ncfg.cursor_column is None

    def test_d20_cursor_column_valid_ident(self):
        """D20: cursor_column 合法标识符 → 解析为字段值"""
        sql = "SELECT * FROM t WHERE ts BETWEEN '{{date_start}}' AND '{{date_end}}'"
        cfg = {
            "date_start": "2026-01-01", "date_end": "2026-01-05",
            "chunk_days": 1, "cursor_column": "event_id",
        }
        ncfg = validate_chunk_config(cfg, sql)
        assert ncfg.cursor_column == "event_id"

    def test_d21_cursor_column_invalid_ident_rejected(self):
        """D21: cursor_column 含非法字符(空格/连字符)→ ValueError"""
        sql = "SELECT * FROM t WHERE ts BETWEEN '{{date_start}}' AND '{{date_end}}'"
        cfg = {
            "date_start": "2026-01-01", "date_end": "2026-01-05",
            "chunk_days": 1, "cursor_column": "evil col; DROP TABLE",
        }
        with pytest.raises(ValueError, match="cursor_column"):
            validate_chunk_config(cfg, sql)

    def test_d22_cursor_column_empty_string_normalized_to_none(self):
        """D22: cursor_column 空字符串/纯空白 → 视作未提供(None)"""
        sql = "SELECT * FROM t WHERE ts BETWEEN '{{date_start}}' AND '{{date_end}}'"
        cfg = {
            "date_start": "2026-01-01", "date_end": "2026-01-05",
            "chunk_days": 1, "cursor_column": "   ",
        }
        ncfg = validate_chunk_config(cfg, sql)
        assert ncfg.cursor_column is None

    def test_d23_min_subdivide_unit_persists(self):
        """D23: 传 min_subdivide_unit='hour' → 透传到 NormalizedChunkConfig"""
        sql = "SELECT * FROM t WHERE ts BETWEEN '{{date_start}}' AND '{{date_end}}'"
        cfg = {
            "date_start": "2026-01-01", "date_end": "2026-01-05",
            "chunk_days": 1, "min_subdivide_unit": "hour",
        }
        ncfg = validate_chunk_config(cfg, sql)
        assert ncfg.min_subdivide_unit == "hour"

    # ─ v2.14.1: cursor_column 校验放宽 — 支持空格/中文/反引号包裹 ─

    def test_d24_cursor_column_with_space_accepted(self):
        """D24: cursor_column='Call ID'(含空格,即用户 SELECT 别名)→ 接受"""
        sql = "SELECT * FROM t WHERE ts BETWEEN '{{date_start}}' AND '{{date_end}}'"
        cfg = {
            "date_start": "2026-01-01", "date_end": "2026-01-05",
            "chunk_days": 5, "cursor_column": "Call ID",
        }
        ncfg = validate_chunk_config(cfg, sql)
        assert ncfg.cursor_column == "Call ID"

    def test_d25_cursor_column_backtick_stripped(self):
        """D25: cursor_column='`Call ID`'(带反引号)→ 自动 strip 反引号 → 'Call ID'"""
        sql = "SELECT * FROM t WHERE ts BETWEEN '{{date_start}}' AND '{{date_end}}'"
        cfg = {
            "date_start": "2026-01-01", "date_end": "2026-01-05",
            "chunk_days": 5, "cursor_column": "`Call ID`",
        }
        ncfg = validate_chunk_config(cfg, sql)
        assert ncfg.cursor_column == "Call ID"

    def test_d26_cursor_column_chinese_accepted(self):
        """D26: 中文列名 '订单_id' 接受"""
        sql = "SELECT * FROM t WHERE ts BETWEEN '{{date_start}}' AND '{{date_end}}'"
        cfg = {
            "date_start": "2026-01-01", "date_end": "2026-01-05",
            "chunk_days": 5, "cursor_column": "订单_id",
        }
        ncfg = validate_chunk_config(cfg, sql)
        assert ncfg.cursor_column == "订单_id"

    def test_d27_cursor_column_digit_start_rejected(self):
        """D27: 数字起首 '1id' 仍拒(SQL 标识符规则)"""
        sql = "SELECT * FROM t WHERE ts BETWEEN '{{date_start}}' AND '{{date_end}}'"
        cfg = {
            "date_start": "2026-01-01", "date_end": "2026-01-05",
            "chunk_days": 5, "cursor_column": "1id",
        }
        with pytest.raises(ValueError, match="cursor_column"):
            validate_chunk_config(cfg, sql)

    def test_d28_cursor_column_semicolon_rejected(self):
        """D28: 注入尝试 'id; DROP TABLE' 仍拒(分号不在白名单)"""
        sql = "SELECT * FROM t WHERE ts BETWEEN '{{date_start}}' AND '{{date_end}}'"
        cfg = {
            "date_start": "2026-01-01", "date_end": "2026-01-05",
            "chunk_days": 5, "cursor_column": "id; DROP TABLE",
        }
        with pytest.raises(ValueError, match="cursor_column"):
            validate_chunk_config(cfg, sql)

    def test_d29_cursor_column_pure_backticks_normalized_to_none(self):
        """D29: cursor_column='``'(纯反引号)strip 后为空 → 视作未提供(None)"""
        sql = "SELECT * FROM t WHERE ts BETWEEN '{{date_start}}' AND '{{date_end}}'"
        cfg = {
            "date_start": "2026-01-01", "date_end": "2026-01-05",
            "chunk_days": 5, "cursor_column": "``",
        }
        ncfg = validate_chunk_config(cfg, sql)
        assert ncfg.cursor_column is None
