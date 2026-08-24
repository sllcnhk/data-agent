"""
test_data_export_csv_keyset.py — CSV keyset 续传集成测试（v2.16 L3 层）

被测：
    backend/services/data_export_service.py::_stream_sql_to_csv_file_keyset
    backend/services/data_export_service.py::_run_csv_export（路由）
    backend/services/export_clients/clickhouse.py::fetch_raw_keyset_window

核心不变量（本文件最重要的断言）：
    mock 数据的 cursor 取 1..N 连续整数，断流注入后最终 CSV 里的
    **cursor 集合必须恰好 == {1..N}，且无任何重复**。
    这是「无重无漏」唯一可验证的证明 —— 行数对不代表内容对。

设计要点：
    - 不连真 ClickHouse：伪造 `fetch_raw_keyset_window`，按 WHERE cursor > X
      的语义从内存数据集切窗口，并可在指定窗口的指定字节位置注入断流。
    - 断流注入点故意选在「引号内换行」「`""` 转义序列中间」这类最刁的位置，
      与 test_csv_tail.py 的 L1 用例联动。

覆盖维度：

  A (4)  — 正常完成
           A1: 3 窗口正常完成 → 行数/表头/BOM 唯一
           A2: 恰好整除窗口 → 末尾多一次空窗口即终止
           A3: 单窗口装得下全部数据
           A4: 零数据行（只有表头）

  B (6)  — 断流续传（核心）
           B1: 窗口 2 中途断流 → 续传后 cursor 集合完整无重复
           B2: 连续断 3 次后成功
           B3: 超过重试上限 → 抛出，异常可被 _humanize_error 识别
           B4: 断流点落在引号内换行处
           B5: 断流点落在 `""` 转义序列中间
           B6: 每个窗口都断一次 → 全部续传成功，数据仍完整

  C (4)  — keyset 致命状态
           C1: 游标列末行为 \\N → NULL 报错 + 可执行建议
           C2: 相邻窗口游标值相同 → 死循环 fast-fail
           C3: 游标列不在表头 → 报错提示填别名
           C4: 游标值含逗号（被引号包裹）→ 窗口推进正确

  D (4)  — 路由与取消
           D1: 不填游标列的 CSV → 走 stream_raw 单流（行为零变化）
           D2: 填了游标列的 CSV → 走 fetch_raw_keyset_window
           D3: 导出中途取消 → cancelled，文件截到完整记录
           D4: 退避期间取消 → cancelled

  E (3)  — csv_zip 与 batch_size
           E1: csv_zip + 游标列 → keyset 落盘后正常打包
           E2: batch_size 作为窗口大小生效（此前 CSV 下是死配置）
           E3: Code 202 → 退避重发同一窗口，不走截断逻辑

共计: 21 个测试用例

运行：
    /d/ProgramData/Anaconda3/envs/dataagent/python.exe -m pytest test_data_export_csv_keyset.py -v -s
"""
from __future__ import annotations

import csv as _csv
import io as _io
import os
import sys
import uuid
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(1, os.path.join(os.path.dirname(__file__), "backend"))
os.environ.setdefault("ENABLE_AUTH", "False")

import pytest  # noqa: E402
from requests.exceptions import ChunkedEncodingError  # noqa: E402

import backend.services.data_export_service as svc  # noqa: E402

_PREFIX = f"_t_cvk_{uuid.uuid4().hex[:6]}_"
BOM = b"\xef\xbb\xbf"


# ═════════════════════════════════════════════════════════════════════════════
# 假 ClickHouse：按 keyset 语义从内存数据集切窗口 + 可注入断流
# ═════════════════════════════════════════════════════════════════════════════

class FakeKeysetSource:
    """模拟 ClickHouse 的 keyset 窗口响应。

    数据集是 (cursor, payload) 列表，按 cursor 升序。每次请求返回
    `cursor > last_cursor` 的前 window_rows 行，编码为 RFC4180 CSV 字节。

    断流注入：`fail_plan` 形如 {window_idx: [(fail_at_byte, times), ...]}
    —— 在该窗口第 n 次请求写出 fail_at_byte 字节后抛 ChunkedEncodingError。
    """

    def __init__(
        self,
        rows: List[Tuple[Any, ...]],
        columns: List[str],
        *,
        cursor_col: str = "id",
        fail_plan: Optional[Dict[int, List[int]]] = None,
        exc_factory=None,
        misbehave: bool = False,
    ) -> None:
        self.rows = rows
        self.columns = columns
        self.cursor_idx = columns.index(cursor_col)
        #: {window_idx: [第1次请求失败字节数, 第2次..., ...]}；None 表示该次不失败
        self.fail_plan = fail_plan or {}
        self.exc_factory = exc_factory or (
            lambda: ChunkedEncodingError("simulated stream disconnect")
        )
        #: True → 无视 `WHERE cursor > last`，每次都返回同一批行。
        #: 用于测试死循环保护 —— 守规矩的服务端在数学上不可能触发它
        #: （所有返回行都 > last，故 max > last），该保护是防 ORDER BY 未真正生效
        #: 或游标列含重复导致排序不稳的兜底，只能靠"违约的服务端"来验证。
        self.misbehave = misbehave
        #: 每个 window_idx 被请求过几次
        self.attempts: Dict[int, int] = {}
        #: 记录每次请求的 (window_idx, last_cursor)，用于断言"重发的是同一个窗口"
        self.requests: List[Tuple[int, Optional[str]]] = []

    # ── CSV 编码（与 ClickHouse RFC4180 行为一致）──
    @staticmethod
    def _cell(v: Any) -> str:
        if v is None:
            return "\\N"          # ClickHouse CSV 的 NULL 字面量（IDN 实测）
        s = str(v)
        if any(ch in s for ch in (",", '"', "\n", "\r")):
            return '"' + s.replace('"', '""') + '"'
        return s

    def _encode(self, rows: List[Tuple], with_header: bool) -> bytes:
        out = _io.BytesIO()
        if with_header:
            out.write((",".join(self._cell(c) for c in self.columns) + "\n").encode())
        for r in rows:
            out.write((",".join(self._cell(v) for v in r) + "\n").encode())
        return out.getvalue()

    def _window_rows(self, last_cursor: Optional[str], n: int) -> List[Tuple]:
        if self.misbehave:
            return self.rows[:n]        # 违约：永远返回同一批
        if last_cursor is None:
            cand = self.rows
        else:
            # 模拟 WHERE cursor > {v:String}：按字符串比较会不稳，这里按数值/字符串
            # 自适应，保证测试语义就是"严格大于上一窗口末行"
            def _gt(v):
                a, b = v[self.cursor_idx], last_cursor
                try:
                    return float(a) > float(b)
                except (TypeError, ValueError):
                    return str(a) > str(b)
            cand = [r for r in self.rows if _gt(r)]
        return cand[:n]

    def fetch_raw_keyset_window(
        self, sql, cursor_column, *, last_cursor=None, window_rows=50_000,
        extra_settings=None, query_id_prefix=None, window_idx=0,
    ):
        attempt = self.attempts.get(window_idx, 0)
        self.attempts[window_idx] = attempt + 1
        self.requests.append((window_idx, last_cursor))

        rows = self._window_rows(last_cursor, window_rows)
        payload = self._encode(rows, with_header=(last_cursor is None))

        plan = self.fail_plan.get(window_idx) or []
        fail_at = plan[attempt] if attempt < len(plan) else None

        def _gen():
            if fail_at is None:
                # 分小块 yield，模拟真实流式
                step = max(1, len(payload) // 3 or 1)
                for i in range(0, len(payload), step):
                    yield payload[i:i + step]
                return
            yield payload[:fail_at]
            raise self.exc_factory()

        return _gen()

    def stream_raw(self, sql, *, format_name="CSVWithNames", chunk_bytes=1 << 20,
                   extra_settings=None, query_id_prefix=None):
        """单流路径（不填游标列时用）"""
        self.requests.append((-1, None))
        payload = self._encode(self.rows, with_header=True)
        yield payload


def _mk_rows(n: int, *, cursor_start: int = 1) -> List[Tuple]:
    """(id, name, note) — note 故意含逗号/引号/换行，逼出 RFC4180 路径"""
    out = []
    for i in range(cursor_start, cursor_start + n):
        note = {
            0: f"plain-{i}",
            1: f"has,comma-{i}",
            2: f'has "quote" {i}',
            3: f"has\nnewline-{i}",
        }[i % 4]
        out.append((i, f"name-{i}", note))
    return out


COLS = ["id", "name", "note"]


def _run_keyset(
    tmp_path: Path,
    source: FakeKeysetSource,
    *,
    window_rows: int = 10,
    cursor_column: str = "id",
    job_id: Optional[str] = None,
    on_cancel=None,
) -> Tuple[Dict[str, Any], Path]:
    """直接调 _stream_sql_to_csv_file_keyset（不经 DB job），返回 (result, csv 路径)"""
    csv_path = tmp_path / "out.csv"
    job_id = job_id or f"{_PREFIX}{uuid.uuid4().hex[:6]}"
    with patch.object(svc, "_build_export_client", return_value=source), \
         patch.object(svc, "_is_cancelling", return_value=False), \
         patch.object(svc, "_update_job", MagicMock()):
        result = svc._stream_sql_to_csv_file_keyset(
            job_id=job_id,
            sql="SELECT id, name, note FROM t",
            env="test", conn_type="clickhouse",
            csv_path=str(csv_path),
            query_id_prefix=f"test:{job_id}",
            cursor_column=cursor_column,
            window_rows=window_rows,
            on_cancel=on_cancel,
            progress_label="CSV",
        )
    return result, csv_path


def _read_csv(path: Path) -> Tuple[List[str], List[List[str]]]:
    raw = path.read_bytes()
    assert raw.startswith(BOM), "文件应以 UTF-8 BOM 开头"
    assert raw.count(BOM) == 1, f"BOM 只应出现 1 次，实际 {raw.count(BOM)}"
    text = raw.decode("utf-8-sig")
    rows = list(_csv.reader(_io.StringIO(text, newline="")))
    assert rows, "文件不应为空"
    return rows[0], rows[1:]


def _assert_exact_cursor_set(data: List[List[str]], expected_n: int) -> None:
    """本文件最核心的断言：cursor 集合恰好 == {1..N}，且无重复。"""
    cursors = [r[0] for r in data]
    assert len(cursors) == len(set(cursors)), (
        f"出现重复 cursor: "
        f"{sorted(c for c in set(cursors) if cursors.count(c) > 1)[:10]}"
    )
    assert set(cursors) == {str(i) for i in range(1, expected_n + 1)}, (
        f"cursor 集合不完整。缺失={sorted(set(str(i) for i in range(1, expected_n+1)) - set(cursors))[:10]} "
        f"多出={sorted(set(cursors) - set(str(i) for i in range(1, expected_n+1)))[:10]}"
    )
    assert len(data) == expected_n, f"行数应为 {expected_n}，实际 {len(data)}"


# ═════════════════════════════════════════════════════════════════════════════
# Section A — 正常完成
# ═════════════════════════════════════════════════════════════════════════════

class TestNormalCompletion:

    def test_A1_three_windows_complete(self, tmp_path):
        """A1: 25 行 / 窗口 10 → 3 个窗口；表头唯一、BOM 唯一、行数正确"""
        src = FakeKeysetSource(_mk_rows(25), COLS)
        result, path = _run_keyset(tmp_path, src, window_rows=10)
        header, data = _read_csv(path)
        assert header == COLS
        _assert_exact_cursor_set(data, 25)
        assert result["exported_rows"] == 25
        assert result["cancelled"] is False
        # 表头只能出现一次（后续窗口用 FORMAT CSV 无表头）
        assert path.read_bytes().count(b"id,name,note") == 1

    def test_A2_exact_multiple_of_window(self, tmp_path):
        """A2: 20 行 / 窗口 10 → 满窗后再发一次空窗口才终止"""
        src = FakeKeysetSource(_mk_rows(20), COLS)
        result, path = _run_keyset(tmp_path, src, window_rows=10)
        _, data = _read_csv(path)
        _assert_exact_cursor_set(data, 20)
        # 窗口 0 满 10 行、窗口 1 满 10 行 → 必须再问一次才知道没了
        assert len(src.attempts) == 3, f"应发 3 次请求，实际 {src.attempts}"

    def test_A3_single_window_covers_all(self, tmp_path):
        """A3: 5 行 / 窗口 10 → 一个窗口装完，不足整窗直接终止"""
        src = FakeKeysetSource(_mk_rows(5), COLS)
        result, path = _run_keyset(tmp_path, src, window_rows=10)
        _, data = _read_csv(path)
        _assert_exact_cursor_set(data, 5)
        assert len(src.attempts) == 1, "不足整窗应省掉多余的空查询"

    def test_A4_zero_data_rows(self, tmp_path):
        """A4: 只有表头，零数据行"""
        src = FakeKeysetSource([], COLS)
        result, path = _run_keyset(tmp_path, src, window_rows=10)
        header, data = _read_csv(path)
        assert header == COLS
        assert data == []
        assert result["exported_rows"] == 0


# ═════════════════════════════════════════════════════════════════════════════
# Section B — 断流续传（核心）
# ═════════════════════════════════════════════════════════════════════════════

class TestResumeOnDisconnect:

    def test_B1_mid_window_disconnect_resumes_without_loss_or_dup(self, tmp_path):
        """B1: 窗口 1 首次请求写 40 字节后断流 → 续传；cursor 集合完整无重复"""
        src = FakeKeysetSource(_mk_rows(25), COLS, fail_plan={1: [40]})
        with patch.object(svc, "_sleep_with_cancel_check_job", return_value=False):
            result, path = _run_keyset(tmp_path, src, window_rows=10)
        _, data = _read_csv(path)
        _assert_exact_cursor_set(data, 25)
        assert src.attempts[1] == 2, "窗口 1 应被请求 2 次（首次断流 + 重发）"
        # 重发的必须是同一个窗口（last_cursor 相同）
        w1 = [r for r in src.requests if r[0] == 1]
        assert w1[0][1] == w1[1][1], f"重发窗口的 last_cursor 应一致: {w1}"

    def test_B2_three_consecutive_failures_then_success(self, tmp_path):
        """B2: 窗口 1 连续断 3 次（= 上限）后成功"""
        src = FakeKeysetSource(_mk_rows(25), COLS, fail_plan={1: [30, 45, 20]})
        with patch.object(svc, "_sleep_with_cancel_check_job", return_value=False):
            result, path = _run_keyset(tmp_path, src, window_rows=10)
        _, data = _read_csv(path)
        _assert_exact_cursor_set(data, 25)
        assert src.attempts[1] == 4, "3 次失败 + 1 次成功"

    def test_B3_exceeding_retry_limit_raises(self, tmp_path):
        """B3: 断流次数超过上限 → 抛出，且能被 is_transient_stream_error 识别"""
        from backend.services.export_clients.clickhouse import is_transient_stream_error
        src = FakeKeysetSource(_mk_rows(25), COLS, fail_plan={1: [30, 30, 30, 30, 30]})
        with patch.object(svc, "_sleep_with_cancel_check_job", return_value=False):
            with pytest.raises(Exception) as ei:
                _run_keyset(tmp_path, src, window_rows=10)
        assert is_transient_stream_error(ei.value), (
            f"异常应可被识别为瞬时断流（外层才会给出正确建议），实际 {ei.value!r}"
        )

    def test_B4_disconnect_inside_quoted_newline(self, tmp_path):
        """B4: 断流点落在引号内的换行处 → 截断不留半条记录"""
        rows = _mk_rows(25)
        src = FakeKeysetSource(rows, COLS, fail_plan={1: [0]})
        # 先算出窗口 1 里第一个含换行字段的字节位置
        payload = src._encode(src._window_rows("10", 10), with_header=False)
        nl_in_quote = payload.index(b'"has\nnewline')
        src.fail_plan = {1: [nl_in_quote + 6]}   # 切进引号内换行之后
        src.attempts.clear(); src.requests.clear()
        with patch.object(svc, "_sleep_with_cancel_check_job", return_value=False):
            result, path = _run_keyset(tmp_path, src, window_rows=10)
        _, data = _read_csv(path)
        _assert_exact_cursor_set(data, 25)
        for r in data:
            assert len(r) == 3, f"出现残缺记录: {r!r}"

    def test_B5_disconnect_inside_escaped_quote(self, tmp_path):
        """B5: 断流点落在 `\"\"` 转义序列中间"""
        rows = _mk_rows(25)
        src = FakeKeysetSource(rows, COLS)
        payload = src._encode(src._window_rows("10", 10), with_header=False)
        idx = payload.index(b'""')
        src.fail_plan = {1: [idx + 1]}          # 切在两个 " 之间
        with patch.object(svc, "_sleep_with_cancel_check_job", return_value=False):
            result, path = _run_keyset(tmp_path, src, window_rows=10)
        _, data = _read_csv(path)
        _assert_exact_cursor_set(data, 25)

    def test_B6_every_window_fails_once(self, tmp_path):
        """B6: 每个窗口都断一次 → 全部续传成功，数据仍然完整"""
        src = FakeKeysetSource(
            _mk_rows(50), COLS,
            fail_plan={i: [25] for i in range(6)},
        )
        with patch.object(svc, "_sleep_with_cancel_check_job", return_value=False):
            result, path = _run_keyset(tmp_path, src, window_rows=10)
        _, data = _read_csv(path)
        _assert_exact_cursor_set(data, 50)
        assert all(v >= 2 for k, v in src.attempts.items() if k < 5), src.attempts


# ═════════════════════════════════════════════════════════════════════════════
# Section C — keyset 致命状态
# ═════════════════════════════════════════════════════════════════════════════

class TestKeysetFatalStates:

    def test_C1_null_cursor_gives_actionable_error(self, tmp_path):
        """C1: 窗口末行游标为 \\N → 报错含 IS NOT NULL 等可执行建议"""
        rows = _mk_rows(9) + [(None, "name-null", "plain")]
        src = FakeKeysetSource(rows, COLS)
        with pytest.raises(RuntimeError) as ei:
            _run_keyset(tmp_path, src, window_rows=10)
        msg = str(ei.value)
        assert "NULL" in msg
        assert "IS NOT NULL" in msg, f"应建议加 IS NOT NULL: {msg}"
        assert "主键" in msg or "时间戳" in msg
        # CSV 路径清空游标列回退的是"单流不可续传"，不是 LIMIT/OFFSET
        assert "单流" in msg, f"CSV 路径的回退提示应说单流模式: {msg}"
        assert "LIMIT/OFFSET" not in msg, (
            f"CSV 路径不该说回退 LIMIT/OFFSET（那是 xlsx 路径的行为）: {msg}"
        )

    def test_C2_stalled_cursor_fast_fails(self, tmp_path):
        """C2: 相邻窗口末行游标相同 → 死循环 fast-fail，不无限重发

        注意这里必须用 misbehave=True 的数据源：守约的服务端（返回行全部
        `> last_cursor`）在数学上不可能让 max(cursor) == last_cursor。这个保护是
        防「ORDER BY 未真正生效 / 游标列含重复导致排序不稳」的兜底，所以只能靠
        模拟一个违约的服务端来验证它确实会 fast-fail 而不是无限发同一个查询。
        """
        src = FakeKeysetSource(_mk_rows(30), COLS, misbehave=True)
        with pytest.raises(RuntimeError) as ei:
            _run_keyset(tmp_path, src, window_rows=10)
        assert "死循环" in str(ei.value), f"实际: {ei.value}"
        assert len(src.attempts) == 2, (
            f"应在第 2 个窗口就 fast-fail，不能无限重发，实际请求了 "
            f"{len(src.attempts)} 个窗口"
        )

    def test_C3_cursor_column_not_in_header(self, tmp_path):
        """C3: 游标列不在 CSV 表头 → 报错提示填 SELECT 别名"""
        src = FakeKeysetSource(_mk_rows(5), COLS)
        with pytest.raises(RuntimeError) as ei:
            _run_keyset(tmp_path, src, window_rows=10, cursor_column="no_such_col")
        msg = str(ei.value)
        assert "no_such_col" in msg
        assert "别名" in msg, f"应提示填别名: {msg}"

    def test_C4_cursor_value_with_comma_advances_correctly(self, tmp_path):
        """C4: 游标值本身含逗号（被引号包裹）→ 窗口仍能正确推进

        这个用例专门防「用 split(',') 取游标」的实现：那样会取到 '\"ORD' 之类的
        残片，窗口推进立刻错乱。
        """
        rows = [(f"ORD,{i:03d}", f"n{i}", "plain") for i in range(1, 26)]
        src = FakeKeysetSource(rows, COLS)
        result, path = _run_keyset(tmp_path, src, window_rows=10)
        _, data = _read_csv(path)
        cursors = [r[0] for r in data]
        assert len(cursors) == len(set(cursors)), f"重复: {cursors}"
        assert set(cursors) == {f"ORD,{i:03d}" for i in range(1, 26)}
        assert all("," in c for c in cursors), "游标值应保留逗号"


# ═════════════════════════════════════════════════════════════════════════════
# Section D — 路由与取消
# ═════════════════════════════════════════════════════════════════════════════

class TestRoutingAndCancel:

    def test_D1_no_cursor_column_uses_single_stream(self, tmp_path):
        """D1: 不填游标列的 CSV → 走 stream_raw 单流（老行为零变化）"""
        src = FakeKeysetSource(_mk_rows(25), COLS)
        out = tmp_path / "single.csv"
        with patch.object(svc, "_build_export_client", return_value=src), \
             patch.object(svc, "_is_cancelling", return_value=False), \
             patch.object(svc, "_update_job", MagicMock()):
            result = svc._run_csv_export(
                job_id=f"{_PREFIX}d1", sql="SELECT 1", env="test",
                conn_type="clickhouse", output_path=str(out),
                output_format="csv", query_id_prefix="t",
                cursor_column=None, window_rows=10,
            )
        assert src.attempts == {}, "不应调用 fetch_raw_keyset_window"
        assert (-1, None) in src.requests, "应走 stream_raw 单流"
        assert result["exported_rows"] == 25

    def test_D2_with_cursor_column_uses_keyset(self, tmp_path):
        """D2: 填了游标列的 CSV → 走 fetch_raw_keyset_window"""
        src = FakeKeysetSource(_mk_rows(25), COLS)
        out = tmp_path / "ks.csv"
        with patch.object(svc, "_build_export_client", return_value=src), \
             patch.object(svc, "_is_cancelling", return_value=False), \
             patch.object(svc, "_update_job", MagicMock()):
            result = svc._run_csv_export(
                job_id=f"{_PREFIX}d2", sql="SELECT 1", env="test",
                conn_type="clickhouse", output_path=str(out),
                output_format="csv", query_id_prefix="t",
                cursor_column="id", window_rows=10,
            )
        assert src.attempts, "应调用 fetch_raw_keyset_window"
        assert (-1, None) not in src.requests, "不应走 stream_raw"
        assert result["exported_rows"] == 25

    def test_D3_cancel_midway_truncates_to_complete_record(self, tmp_path):
        """D3: 中途取消 → cancelled，文件截到完整记录（仍是合法 CSV）"""
        src = FakeKeysetSource(_mk_rows(200), COLS)
        calls = {"n": 0}

        def _cancel():
            calls["n"] += 1
            return calls["n"] > 1        # 第二次检查时取消

        out = tmp_path / "c.csv"
        with patch.object(svc, "_build_export_client", return_value=src), \
             patch.object(svc, "_is_cancelling", return_value=False), \
             patch.object(svc, "_update_job", MagicMock()), \
             patch.object(svc, "_env_int", lambda name, default, **kw:
                          0 if "CANCEL_CHECK" in name else default):
            result = svc._stream_sql_to_csv_file_keyset(
                job_id=f"{_PREFIX}d3", sql="SELECT 1", env="test",
                conn_type="clickhouse", csv_path=str(out),
                query_id_prefix="t", cursor_column="id", window_rows=50,
                on_cancel=_cancel, progress_label="CSV",
            )
        assert result["cancelled"] is True
        # 文件必须是合法 CSV（不留半条记录）
        raw = out.read_bytes()
        if len(raw) > len(BOM):
            rows = list(_csv.reader(_io.StringIO(raw.decode("utf-8-sig"), newline="")))
            for r in rows:
                assert len(r) == 3, f"取消后留下残缺记录: {r!r}"

    def test_D4_cancel_during_backoff(self, tmp_path):
        """D4: 断流退避期间取消 → cancelled"""
        src = FakeKeysetSource(_mk_rows(25), COLS, fail_plan={1: [30]})
        with patch.object(svc, "_sleep_with_cancel_check_job", return_value=True):
            result, path = _run_keyset(tmp_path, src, window_rows=10)
        assert result["cancelled"] is True


# ═════════════════════════════════════════════════════════════════════════════
# Section E — csv_zip / batch_size / Code 202
# ═════════════════════════════════════════════════════════════════════════════

class TestZipAndBatchSize:

    def test_E1_csv_zip_with_cursor_column(self, tmp_path):
        """E1: csv_zip + 游标列 → keyset 落盘后正常打包，包内 CSV 数据完整"""
        src = FakeKeysetSource(_mk_rows(25), COLS)
        out = tmp_path / "out.zip"
        with patch.object(svc, "_build_export_client", return_value=src), \
             patch.object(svc, "_is_cancelling", return_value=False), \
             patch.object(svc, "_update_job", MagicMock()):
            result = svc._run_csv_export(
                job_id=f"{_PREFIX}e1", sql="SELECT 1", env="test",
                conn_type="clickhouse", output_path=str(out),
                output_format="csv_zip", query_id_prefix="t",
                cursor_column="id", window_rows=10,
            )
        assert out.exists() and zipfile.is_zipfile(out)
        with zipfile.ZipFile(out) as zf:
            names = zf.namelist()
            assert len(names) == 1
            content = zf.read(names[0])
        rows = list(_csv.reader(_io.StringIO(content.decode("utf-8-sig"), newline="")))
        assert rows[0] == COLS
        _assert_exact_cursor_set(rows[1:], 25)
        assert result["exported_rows"] == 25

    # 25 行数据：窗口 5 → 5 满窗 + 1 空窗 = 6；窗口 10 → 10/10/5 = 3；
    # 窗口 25 → 1 个满窗 + 1 个空窗 = 2（满窗时无法预知后面还有没有，必须再问一次）
    @pytest.mark.parametrize("window_rows,expected_windows", [(5, 6), (10, 3), (25, 2)])
    def test_E2_batch_size_drives_window_size(self, tmp_path, window_rows,
                                              expected_windows):
        """E2: batch_size 作为窗口大小真正生效 —— 此前 CSV 路径完全不读它"""
        src = FakeKeysetSource(_mk_rows(25), COLS)
        _run_keyset(tmp_path, src, window_rows=window_rows)
        assert len(src.attempts) == expected_windows, (
            f"window_rows={window_rows} 应产生 {expected_windows} 个窗口，"
            f"实际 {len(src.attempts)}"
        )

    def test_E3_too_many_queries_retries_same_window(self, tmp_path):
        """E3: Code 202 → 退避重发同一窗口（与断流走同一条续传路径）"""
        src = FakeKeysetSource(
            _mk_rows(25), COLS, fail_plan={1: [30]},
            exc_factory=lambda: RuntimeError(
                "ClickHouse 错误 500: Code: 202, Too many simultaneous queries"
            ),
        )
        with patch.object(svc, "_sleep_with_cancel_check_job", return_value=False):
            result, path = _run_keyset(tmp_path, src, window_rows=10)
        _, data = _read_csv(path)
        _assert_exact_cursor_set(data, 25)
        assert src.attempts[1] == 2
