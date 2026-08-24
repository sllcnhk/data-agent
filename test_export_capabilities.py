"""
test_export_capabilities.py — 导出能力矩阵测试（v2.16 L2 层）

被测：backend/services/data_export_capabilities.py

## 这个文件为什么这么写

能力矩阵最容易变成「自己跟自己对账」的废测试：断言 `derive_capabilities` 返回的
flag 等于我在测试里手写的同一份 flag，两边一起错也测不出来。

所以本文件的核心不是校验矩阵内部自洽，而是**拿声明出来的能力去和真实实现对账**：
用 spy client 真的跑一遍 `_run_single_export`，记录它到底调了哪个底层数据通路
（stream_batches / stream_raw / fetch_raw_keyset_window / stream_batches_chunked），
再断言这些观测结果与矩阵声明一致。矩阵一旦和实现漂移，这里立刻红。

覆盖维度：

  A (5)  — 矩阵基本形态与入参校验
  B (4)  — auto 引擎解析（含「单文件 auto 从不等于 csv_staging」这个真实行为）
  C (20) — ★ 路由对账：穷举有意义组合，观测实际调用的数据通路
  D (5)  — batch_size 生效性对账（含它作为 keyset 窗口大小的新语义）
  E (4)  — 与 _path_has_stream_fallback 交叉验证（复用 _humanize_error 用的谓词）
  F (6)  — 警告只在真实风险组合上出现
  G (4)  — 「你将得到什么」摘要与产物形态

运行：
    /d/ProgramData/Anaconda3/envs/dataagent/python.exe -m pytest test_export_capabilities.py -v -s
"""
from __future__ import annotations

import os
import sys
import uuid
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(1, os.path.join(os.path.dirname(__file__), "backend"))
os.environ.setdefault("ENABLE_AUTH", "False")

import pytest  # noqa: E402

import backend.services.data_export_service as svc  # noqa: E402
from backend.services.data_export_capabilities import (  # noqa: E402
    EXPORT_MODES,
    OUTPUT_FORMATS,
    XLSX_ENGINES,
    build_capability_matrix,
    derive_capabilities,
    resolve_effective_engine,
)

_PREFIX = f"_t_cap_{uuid.uuid4().hex[:6]}_"

#: 有意义的组合：CSV 下 xlsx_engine 无意义，只取 auto 一份，避免重复跑 20+ 次导出
MEANINGFUL_COMBOS = [
    (mode, fmt, eng, cursor)
    for mode in EXPORT_MODES
    for fmt in OUTPUT_FORMATS
    for eng in (XLSX_ENGINES if fmt == "xlsx" else ("auto",))
    for cursor in (False, True)
]


# ═════════════════════════════════════════════════════════════════════════════
# Section A — 矩阵基本形态
# ═════════════════════════════════════════════════════════════════════════════

class TestMatrixShape:

    def test_A1_matrix_covers_all_combinations(self):
        """A1: 2 模式 × 3 格式 × 3 引擎 × 2 游标 = 36 条，无重复"""
        m = build_capability_matrix()
        assert len(m) == 36, f"应为 36 条，实际 {len(m)}"
        keys = {
            (c["export_mode"], c["output_format"], c["xlsx_engine"],
             c["has_cursor_column"])
            for c in m
        }
        assert len(keys) == 36, "组合键应唯一"

    def test_A2_every_entry_has_all_declared_fields(self):
        """A2: 每条都含全部字段（前端按字段名渲染，缺字段会静默显示 undefined）"""
        required = {
            "export_mode", "output_format", "xlsx_engine", "has_cursor_column",
            "effective_engine", "artifact", "sheet_splitting", "all_cells_text",
            "utf8_bom", "null_representation", "big_int_excel_safe",
            "batch_size_effective", "batch_size_role",
            "cursor_column_effective", "cursor_column_role",
            "prefer_chunked_effective", "stream_fallback",
            "resumable_on_disconnect", "order_by_risk",
            "cancel_partial_downloadable", "retry_failed_chunks",
            "warnings", "summary",
        }
        for c in build_capability_matrix():
            missing = required - set(c)
            assert not missing, f"{c['export_mode']}/{c['output_format']} 缺字段 {missing}"

    @pytest.mark.parametrize("bad", [
        ("nope", "xlsx", "auto"), ("single", "parquet", "auto"),
        ("single", "xlsx", "turbo"),
    ])
    def test_A3_invalid_inputs_raise(self, bad):
        """A3: 非法入参必须抛，不能静默返回一份错的能力声明"""
        with pytest.raises(ValueError):
            derive_capabilities(*bad)

    def test_A4_csv_has_no_xlsx_engine(self):
        """A4: CSV 格式的 effective_engine 为 None（不涉及 xlsx 引擎）"""
        for fmt in ("csv", "csv_zip"):
            for mode in EXPORT_MODES:
                c = derive_capabilities(mode, fmt, "auto")
                assert c["effective_engine"] is None, f"{mode}/{fmt}"

    def test_A5_sheet_splitting_only_for_xlsx(self):
        """A5: 只有 xlsx 有 Sheet 概念 —— 这是「100 万行分 Sheet」提示的显示条件"""
        for mode, fmt, eng, cur in MEANINGFUL_COMBOS:
            c = derive_capabilities(mode, fmt, eng, cur)
            assert c["sheet_splitting"] == (fmt == "xlsx"), f"{mode}/{fmt}"


# ═════════════════════════════════════════════════════════════════════════════
# Section B — auto 引擎解析
# ═════════════════════════════════════════════════════════════════════════════

class TestEngineResolution:

    def test_B1_single_auto_resolves_to_direct(self):
        """B1: 单文件 auto → direct

        这是**行为事实**而非设计意图：_run_single_job_sync 把 auto 原样透传，
        _run_single_export 只判断 == 'csv_staging'，于是 auto 落到 direct 分支。
        UI 上原先那句「auto：系统判断…分块/大数据默认先落 CSV」在单文件模式下
        从来没有成立过。
        """
        assert resolve_effective_engine("single", "xlsx", "auto") == "direct"

    def test_B2_chunked_auto_resolves_to_csv_staging(self):
        """B2: 分块 auto → csv_staging（_run_chunked_export_sync 里显式解析）"""
        assert resolve_effective_engine("date_chunked", "xlsx", "auto") == "csv_staging"

    def test_B3_explicit_engine_passes_through(self):
        """B3: 显式指定的引擎不被改写"""
        for mode in EXPORT_MODES:
            for eng in ("direct", "csv_staging"):
                assert resolve_effective_engine(mode, "xlsx", eng) == eng

    def test_B4_resolution_matches_service_layer_behavior(self):
        """B4: 与 service 层的实际解析逻辑对账（防两处各自漂移）

        分块路径的解析写在 _run_chunked_export_sync 里，这里直接从源码断言那行还在，
        一旦有人改了默认值而忘了同步矩阵，测试立刻红。
        """
        import inspect
        src = inspect.getsource(svc._run_chunked_export_sync)
        assert 'xlsx_engine = "csv_staging" if output_format == "xlsx" else "direct"' in src, (
            "分块模式的 auto 解析逻辑变了，data_export_capabilities."
            "resolve_effective_engine 必须同步更新"
        )
        single_src = inspect.getsource(svc._run_single_job_sync)
        assert 'config.get("xlsx_engine", "direct")' in single_src, (
            "单文件模式的 engine 取值方式变了，请重新确认 auto 是否仍等价于 direct"
        )


# ═════════════════════════════════════════════════════════════════════════════
# Section C — ★ 路由对账（本文件的核心）
# ═════════════════════════════════════════════════════════════════════════════

class SpyExportClient:
    """记录 _run_single_export 实际走了哪条数据通路。"""

    def __init__(self, columns: List[str]):
        self.columns = columns
        self.calls: List[str] = []
        self.batch_sizes: Dict[str, Any] = {}

    # 列预检
    def get_columns(self, sql):
        from backend.services.export_clients.base import ColumnInfo
        return [ColumnInfo(name=n, type="String") for n in self.columns]

    def _csv_bytes(self, with_header: bool, rows: int = 3) -> bytes:
        out = b""
        if with_header:
            out += (",".join(self.columns) + "\n").encode()
        for i in range(rows):
            out += (",".join(f"v{i}_{j}" for j in range(len(self.columns))) + "\n").encode()
        return out

    # ── 三条互斥的数据通路 ──
    def stream_batches(self, sql, batch_size=50_000, extra_settings=None,
                       query_id_prefix=None):
        self.calls.append("stream_batches")
        self.batch_sizes["stream_batches"] = batch_size
        yield [tuple(f"v{j}" for j in range(len(self.columns)))]

    def stream_raw(self, sql, *, format_name="CSVWithNames", chunk_bytes=1 << 20,
                   extra_settings=None, query_id_prefix=None):
        self.calls.append("stream_raw")
        yield self._csv_bytes(with_header=True)

    def fetch_raw_keyset_window(self, sql, cursor_column, *, last_cursor=None,
                                window_rows=50_000, extra_settings=None,
                                query_id_prefix=None, window_idx=0):
        self.calls.append("fetch_raw_keyset_window")
        self.batch_sizes["fetch_raw_keyset_window"] = window_rows
        # 第一个窗口给满行数以外的少量行 → 不足整窗，立即终止，避免无限循环
        yield self._csv_bytes(with_header=(last_cursor is None), rows=2)

    # ── 回退通路（正常路径不该被调）──
    def count_rows(self, sql, timeout=300):
        self.calls.append("count_rows")
        return 3

    def stream_batches_chunked(self, sql, chunk_size, total_rows, batch_size=50_000,
                               extra_settings=None, query_id_prefix=None):
        self.calls.append("stream_batches_chunked")
        yield [tuple(f"v{j}" for j in range(len(self.columns)))]

    def stream_batches_keyset(self, sql, cursor_column, batch_size=50_000,
                              extra_settings=None, query_id_prefix=None):
        self.calls.append("stream_batches_keyset")
        yield [tuple(f"v{j}" for j in range(len(self.columns)))]


def _drive_export(tmp_path, mode, fmt, eng, cursor, *, batch_size=7777):
    """真跑一次 _run_single_export，返回 (spy, result)。

    engine 按 capabilities 的解析结果传入 —— 模拟 service 层各自的解析行为，
    因为 _run_single_export 自己不解析 auto。
    """
    effective = resolve_effective_engine(mode, fmt, eng)
    ext = "zip" if fmt == "csv_zip" else ("csv" if fmt == "csv" else "xlsx")
    out = tmp_path / f"o.{ext}"
    spy = SpyExportClient(["id", "name", "note"])
    with patch.object(svc, "_build_export_client", return_value=spy), \
         patch.object(svc, "_is_cancelling", return_value=False), \
         patch.object(svc, "_update_job", MagicMock()):
        result = svc._run_single_export(
            job_id=f"{_PREFIX}{uuid.uuid4().hex[:6]}",
            sql="SELECT id, name, note FROM t",
            env="test", conn_type="clickhouse",
            batch_size=batch_size,
            output_path=str(out),
            output_format=fmt,
            xlsx_engine=effective or "direct",
            cursor_column="id" if cursor else None,
            query_id_prefix="cap",
        )
    return spy, result


class TestRoutingReconciliation:
    """把矩阵声明与真实调用的数据通路逐条对账。"""

    @pytest.mark.parametrize("mode,fmt,eng,cursor", MEANINGFUL_COMBOS)
    def test_C1_resumable_iff_keyset_window_used(self, tmp_path, mode, fmt, eng, cursor):
        """C1: 矩阵声明 resumable ⟺ 实际调用了 fetch_raw_keyset_window"""
        caps = derive_capabilities(mode, fmt, eng, cursor)
        spy, _ = _drive_export(tmp_path, mode, fmt, eng, cursor)
        used_keyset = "fetch_raw_keyset_window" in spy.calls
        assert used_keyset == caps["resumable_on_disconnect"], (
            f"{mode}/{fmt}/{eng}/cursor={cursor}: 矩阵声明可续传="
            f"{caps['resumable_on_disconnect']}，实际调用通路={spy.calls}"
        )

    @pytest.mark.parametrize("mode,fmt,eng,cursor", MEANINGFUL_COMBOS)
    def test_C2_stream_fallback_iff_row_batch_path_used(self, tmp_path, mode, fmt,
                                                       eng, cursor):
        """C2: 矩阵声明 stream_fallback ⟺ 实际走了逐行批次通路(stream_batches)

        只有这条通路进 _run_single_export 的 2-attempt 循环，也就是唯一具备
        transient → 分批重跑能力的路径。
        """
        caps = derive_capabilities(mode, fmt, eng, cursor)
        spy, _ = _drive_export(tmp_path, mode, fmt, eng, cursor)
        used_batches = "stream_batches" in spy.calls
        assert used_batches == caps["stream_fallback"], (
            f"{mode}/{fmt}/{eng}/cursor={cursor}: 矩阵声明有回退="
            f"{caps['stream_fallback']}，实际调用通路={spy.calls}"
        )

    @pytest.mark.parametrize("mode,fmt,eng,cursor", MEANINGFUL_COMBOS)
    def test_C3_single_stream_used_when_neither_capability(self, tmp_path, mode, fmt,
                                                          eng, cursor):
        """C3: 既无回退也不可续传 ⟺ 走 stream_raw 单流（最脆弱的那条路）"""
        caps = derive_capabilities(mode, fmt, eng, cursor)
        spy, _ = _drive_export(tmp_path, mode, fmt, eng, cursor)
        neither = not caps["stream_fallback"] and not caps["resumable_on_disconnect"]
        assert ("stream_raw" in spy.calls) == neither, (
            f"{mode}/{fmt}/{eng}/cursor={cursor}: 期望单流={neither}，"
            f"实际调用通路={spy.calls}"
        )

    @pytest.mark.parametrize("mode,fmt,eng,cursor", MEANINGFUL_COMBOS)
    def test_C4_exactly_one_primary_path_used(self, tmp_path, mode, fmt, eng, cursor):
        """C4: 三条主通路互斥，恰好用一条（防出现"两条都走"的重复读取）"""
        spy, _ = _drive_export(tmp_path, mode, fmt, eng, cursor)
        primaries = [
            c for c in spy.calls
            if c in ("stream_batches", "stream_raw", "fetch_raw_keyset_window")
        ]
        kinds = set(primaries)
        assert len(kinds) == 1, (
            f"{mode}/{fmt}/{eng}/cursor={cursor}: 应恰好用一条主通路，"
            f"实际 {spy.calls}"
        )


# ═════════════════════════════════════════════════════════════════════════════
# Section D — batch_size 生效性对账
# ═════════════════════════════════════════════════════════════════════════════

class TestBatchSizeReconciliation:

    @pytest.mark.parametrize("mode,fmt,eng,cursor", MEANINGFUL_COMBOS)
    def test_D1_batch_size_effective_matches_observed_usage(self, tmp_path, mode, fmt,
                                                            eng, cursor):
        """D1: 矩阵声明 batch_size 生效 ⟺ 底层通路真的收到了这个值

        这条专治「死配置」：CSV 单流路径压根不读 batch_size，UI 却照常让用户填。
        """
        caps = derive_capabilities(mode, fmt, eng, cursor)
        spy, _ = _drive_export(tmp_path, mode, fmt, eng, cursor, batch_size=7777)
        observed = 7777 in spy.batch_sizes.values()
        assert observed == caps["batch_size_effective"], (
            f"{mode}/{fmt}/{eng}/cursor={cursor}: 矩阵声明生效="
            f"{caps['batch_size_effective']}，实际底层收到={spy.batch_sizes}"
        )

    def test_D2_batch_size_role_present_iff_effective(self):
        """D2: 生效时必须有角色说明（UI 要显示"这个数字是干什么的"）"""
        for mode, fmt, eng, cur in MEANINGFUL_COMBOS:
            c = derive_capabilities(mode, fmt, eng, cur)
            assert (c["batch_size_role"] is not None) == c["batch_size_effective"], \
                f"{mode}/{fmt}/{eng}/cursor={cur}"

    def test_D3_csv_without_cursor_is_dead_config(self):
        """D3: CSV 不填游标列 → batch_size 是死配置（v2.16 前一直如此，UI 该置灰）"""
        for mode in EXPORT_MODES:
            for fmt in ("csv", "csv_zip"):
                c = derive_capabilities(mode, fmt, "auto", False)
                assert c["batch_size_effective"] is False, f"{mode}/{fmt}"

    def test_D4_csv_with_cursor_makes_batch_size_window_size(self, tmp_path):
        """D4: CSV + 游标列 → batch_size 变成 keyset 窗口大小（新语义，首次生效）"""
        c = derive_capabilities("single", "csv", "auto", True)
        assert c["batch_size_effective"] is True
        assert "窗口" in c["batch_size_role"], c["batch_size_role"]
        spy, _ = _drive_export(tmp_path, "single", "csv", "auto", True, batch_size=1234)
        assert spy.batch_sizes.get("fetch_raw_keyset_window") == 1234

    def test_D5_chunked_xlsx_default_is_dead_config_without_cursor(self):
        """D5: 分块 + XLSX + auto（默认组合）不填游标列 → batch_size 死配置

        这是最容易误导人的一格：分块导 Excel 是主路径，默认 auto→csv_staging 走
        单流落盘，batch_size 完全不参与。
        """
        c = derive_capabilities("date_chunked", "xlsx", "auto", False)
        assert c["effective_engine"] == "csv_staging"
        assert c["batch_size_effective"] is False


# ═════════════════════════════════════════════════════════════════════════════
# Section E — 与 _path_has_stream_fallback 交叉验证
# ═════════════════════════════════════════════════════════════════════════════

class TestCrossCheckWithHumanizeError:
    """矩阵的 stream_fallback 必须与 _humanize_error 用的谓词一致。

    否则会出现「错误信息说做过回退，但矩阵说没有」这类自相矛盾。
    """

    @pytest.mark.parametrize("mode,fmt,eng,cursor", MEANINGFUL_COMBOS)
    def test_E1_agrees_with_path_has_stream_fallback(self, mode, fmt, eng, cursor):
        caps = derive_capabilities(mode, fmt, eng, cursor)
        predicate = svc._path_has_stream_fallback(fmt, caps["effective_engine"])
        assert predicate == caps["stream_fallback"], (
            f"{mode}/{fmt}/{eng}: _path_has_stream_fallback={predicate} 但矩阵="
            f"{caps['stream_fallback']}"
        )

    def test_E2_humanize_error_does_not_claim_fallback_when_matrix_says_none(self):
        """E2: 矩阵说没有回退时，错误文案不得声称做过 LIMIT/OFFSET 回退"""
        from requests.exceptions import ChunkedEncodingError
        exc = ChunkedEncodingError("Connection broken")
        for mode, fmt, eng, cur in MEANINGFUL_COMBOS:
            caps = derive_capabilities(mode, fmt, eng, cur)
            msg = svc._humanize_error(
                exc, output_format=fmt, xlsx_engine=caps["effective_engine"],
                is_chunked=(mode == "date_chunked"),
            )
            claims = "已自动尝试 LIMIT/OFFSET 回退" in msg
            assert claims == caps["stream_fallback"], (
                f"{mode}/{fmt}/{eng}: 文案声称回退={claims}，矩阵={caps['stream_fallback']}"
            )

    def test_E3_chunked_advice_mentions_chunk_days_only_when_chunked(self):
        """E3: 「减小单块天数」只在分块模式出现（单文件没有这个概念）"""
        from requests.exceptions import ChunkedEncodingError
        exc = ChunkedEncodingError("Connection broken")
        single = svc._humanize_error(exc, output_format="csv", is_chunked=False)
        chunked = svc._humanize_error(exc, output_format="csv", is_chunked=True)
        assert "单块天数" not in single, single
        assert "单块天数" in chunked, chunked

    def test_E4_order_by_risk_implies_no_keyset(self):
        """E4: 有 ORDER BY 风险 ⟺ 走逐行批次通路且没填游标列"""
        for mode, fmt, eng, cur in MEANINGFUL_COMBOS:
            c = derive_capabilities(mode, fmt, eng, cur)
            assert c["order_by_risk"] == (c["stream_fallback"] and not cur), \
                f"{mode}/{fmt}/{eng}/cursor={cur}"


# ═════════════════════════════════════════════════════════════════════════════
# Section F — 警告只在真实风险组合上出现
# ═════════════════════════════════════════════════════════════════════════════

def _has_warning(caps: Dict[str, Any], keyword: str) -> bool:
    return any(keyword in w for w in caps["warnings"])


class TestWarnings:

    def test_F1_order_by_warning_only_on_real_risk(self):
        """F1: ORDER BY 警告 ⟺ order_by_risk

        这是 v2.16 修的核心错位：旧条件是 `export_mode !== 'date_chunked'`，
        导致 CSV 单文件（无风险）显示、分块+direct+无游标列（有风险）隐藏。
        """
        for mode, fmt, eng, cur in MEANINGFUL_COMBOS:
            c = derive_capabilities(mode, fmt, eng, cur)
            assert _has_warning(c, "ORDER BY") == c["order_by_risk"], \
                f"{mode}/{fmt}/{eng}/cursor={cur}: warnings={c['warnings']}"

    def test_F2_csv_single_file_has_no_order_by_warning(self):
        """F2: CSV 单文件不该有 ORDER BY 警告（该路径没有 LIMIT/OFFSET 回退）"""
        c = derive_capabilities("single", "csv", "auto", False)
        assert not _has_warning(c, "ORDER BY"), c["warnings"]

    def test_F3_chunked_direct_without_cursor_has_order_by_warning(self):
        """F3: 分块 + direct + 无游标列**必须**有 ORDER BY 警告（旧 UI 恰好藏了它）"""
        c = derive_capabilities("date_chunked", "xlsx", "direct", False)
        assert _has_warning(c, "ORDER BY"), c["warnings"]

    def test_F4_no_fallback_no_resume_warns_about_total_loss(self):
        """F4: 既无回退也不可续传 → 必须警告"断流整体失败" """
        c = derive_capabilities("single", "csv", "auto", False)
        assert _has_warning(c, "断流"), c["warnings"]
        # 反面：可续传的组合不该再这么警告
        c2 = derive_capabilities("single", "csv", "auto", True)
        assert not _has_warning(c2, "整体失败"), c2["warnings"]

    def test_F5_xlsx_always_warns_all_text_cells(self):
        """F5: 所有 xlsx 组合都要警告"单元格是文本"（两条引擎行为一致，无开关可改）"""
        for mode in EXPORT_MODES:
            for eng in XLSX_ENGINES:
                c = derive_capabilities(mode, "xlsx", eng, False)
                assert _has_warning(c, "文本"), f"{mode}/{eng}: {c['warnings']}"

    def test_F6_csv_warns_null_literal_and_bigint(self):
        """F6: CSV 必须警告 \\N 字面量与大整数科学计数法（IDN 实测行为）"""
        for fmt in ("csv", "csv_zip"):
            c = derive_capabilities("single", fmt, "auto", False)
            assert _has_warning(c, "NULL"), c["warnings"]
            assert _has_warning(c, "科学计数法"), c["warnings"]
        # xlsx 不该出现这两条
        cx = derive_capabilities("single", "xlsx", "direct", False)
        assert not _has_warning(cx, "科学计数法"), cx["warnings"]


# ═════════════════════════════════════════════════════════════════════════════
# Section G — 摘要与产物形态
# ═════════════════════════════════════════════════════════════════════════════

class TestSummary:

    @pytest.mark.parametrize("mode,fmt,expected", [
        ("single", "xlsx", "1 个 .xlsx"),
        ("single", "csv", "1 个 .csv"),
        ("single", "csv_zip", "1 个 .zip"),
        ("date_chunked", "xlsx", "N 个 .xlsx"),
        ("date_chunked", "csv", "N 个 .csv"),
        ("date_chunked", "csv_zip", "1 个 .zip"),
    ])
    def test_G1_artifact_shape(self, mode, fmt, expected):
        """G1: 产物形态正确 —— 旧 UI 在 csv 分块下写"每块一个 Excel 文件"是错的"""
        c = derive_capabilities(mode, fmt, "auto")
        assert c["artifact"].startswith(expected), c["artifact"]

    def test_G2_summary_never_empty(self):
        for mode, fmt, eng, cur in MEANINGFUL_COMBOS:
            c = derive_capabilities(mode, fmt, eng, cur)
            assert c["summary"], f"{mode}/{fmt}/{eng}"

    def test_G3_summary_mentions_sheet_only_for_xlsx(self):
        """G3: 「100 万行分 Sheet」只出现在 xlsx —— CSV 没有 Sheet 概念"""
        for mode, fmt, eng, cur in MEANINGFUL_COMBOS:
            c = derive_capabilities(mode, fmt, eng, cur)
            mentions = any("Sheet" in s for s in c["summary"])
            assert mentions == (fmt == "xlsx"), f"{mode}/{fmt}: {c['summary']}"

    def test_G4_summary_states_cancel_semantics_per_mode(self):
        """G4: 取消语义按模式说真话（单文件取消后确实下不了）"""
        s = derive_capabilities("single", "xlsx", "direct")["summary"]
        assert any("不可下载" in x for x in s), s
        ch = derive_capabilities("date_chunked", "xlsx", "direct")["summary"]
        assert any("可单独下载" in x for x in ch), ch
