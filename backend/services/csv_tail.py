"""
CSV 尾部边界扫描 — RFC4180 感知的记录边界定位与游标提取

用途（v2.16 CSV keyset 续传）：
    CSV 导出以 keyset 多窗口方式追加写同一个文件。某个窗口中途断流时，文件尾部
    极可能停在**一条记录的中间**。要想「基于已下载的数据继续」，必须能回答两件事：

      1. 已落盘的字节里，最后一条**完整**记录结束在哪个字节偏移？
         → 把文件 truncate 到那里，保证不留半条记录。
      2. 那条记录的游标列值是什么？
         → 作为下一个窗口 `WHERE cursor > {value}` 的起点。

为什么不能用 `split(b'\\n')` / `split(b',')`：
    ClickHouse 的 `FORMAT CSV*` 输出遵循 RFC4180（IDN 实测确认）：字段含逗号、
    双引号或换行时会被双引号包裹，内部 `"` 转义为 `""`。因此
      - 记录可以**跨多个物理行**（引号内的 `\\n` 是数据，不是记录结束）
      - 字段里的逗号不是分隔符
    按行/按逗号切会静默错位，产出重复或残缺数据 —— 这正是续传最怕的失效模式。

行尾假设：
    只把 `\\n` 当记录终止符。这同时覆盖 LF 与 CRLF（CRLF 的 `\\n` 在最后，切在它
    之后是正确的）；ClickHouse 默认 `output_format_csv_crlf_end_of_line=0`
    （IDN 实测），且不产生「纯 CR」行尾，故无需处理裸 `\\r` 作为终止符。

本模块无 IO、无外部依赖，可 100% 单测。
"""
from __future__ import annotations

import csv
import io
from typing import List, Optional, Tuple

__all__ = [
    "CsvRecordBoundaryScanner",
    "extract_cursor_from_record",
    "split_record_fields",
]

_QUOTE = 0x22   # "
_LF = 0x0A      # \n


class CsvRecordBoundaryScanner:
    """流式扫描 CSV 字节，跟踪最后一条完整记录的字节区间。

    按 chunk 增量喂入（`feed`），内部维护 RFC4180 引号状态，因此可以正确处理
    「引号内换行」「引号内逗号」「`\"\"` 转义」以及**这些结构横跨 chunk 边界**的情况。

    典型用法（与写文件同步推进）：

        scanner = CsvRecordBoundaryScanner(start_offset=fp.tell())
        for chunk in stream:
            fp.write(chunk)
            scanner.feed(chunk)
        # 断流后：把文件截到最后一条完整记录之后
        fp.truncate(scanner.last_complete_end)
        start, end = scanner.last_record_span      # 最后一条完整记录的字节区间

    偏移语义：
        所有偏移都是**绝对文件偏移**（由 `start_offset` 起算），`last_complete_end`
        指向最后一个记录终止符 `\\n` 之后的第一个字节 —— 即可以直接用作
        `truncate()` / `seek()` 的实参。
    """

    def __init__(self, start_offset: int = 0) -> None:
        #: 下一个待处理字节的绝对偏移
        self._pos = start_offset
        #: 本 scanner 起始偏移（用于判定「尚无任何完整记录」）
        self._start = start_offset

        # ── RFC4180 状态机 ──
        #: 当前是否处在引号内
        self._in_quote = False
        #: 上一个字节是引号内的 `"`，尚不能判定它是「闭合引号」还是「`""` 转义的前半」。
        #: 必须跨 chunk 携带，否则 chunk 恰好切在 `""` 中间就会误判。
        self._pending_quote = False

        #: 最后一条完整记录的结束偏移（终止符之后）
        self._last_complete_end = start_offset
        #: 最后一条完整记录的起始偏移
        self._last_record_start = start_offset
        #: **第一条**完整记录的结束偏移（用于定位 CSVWithNames 的表头行）。
        #: 不能用 last_complete_end 代替 —— 一次 feed 可能吞进几万条记录，
        #: 那时 last_* 指向的是最后一条，拿去当表头会静默取错列。
        self._first_record_end: Optional[int] = None
        #: 完整记录计数
        self._record_count = 0

    # ── 只读属性 ────────────────────────────────────────────────────────────

    @property
    def last_complete_end(self) -> int:
        """最后一条完整记录之后的绝对偏移；无完整记录时等于 start_offset。"""
        return self._last_complete_end

    @property
    def last_record_span(self) -> Optional[Tuple[int, int]]:
        """最后一条完整记录的 (start, end) 绝对偏移；无完整记录时为 None。

        end 为**含终止符**的位置，取字节时通常用 `buf[start:end]` 再 rstrip 换行。
        """
        if self._record_count == 0:
            return None
        return (self._last_record_start, self._last_complete_end)

    @property
    def first_record_end(self) -> Optional[int]:
        """第一条完整记录之后的绝对偏移；尚无完整记录时为 None。

        CSVWithNames 的第一条记录就是表头，用它定位游标列的列索引。
        """
        return self._first_record_end

    @property
    def record_count(self) -> int:
        """已见到的完整记录条数（含表头行，如果表头也被喂进来了）。"""
        return self._record_count

    @property
    def position(self) -> int:
        """已消费到的绝对偏移（= start_offset + 累计字节数）。"""
        return self._pos

    @property
    def in_quote(self) -> bool:
        """当前是否处在引号内（`pending_quote` 挂起时仍算引号内，尚未判定）。"""
        return self._in_quote

    @property
    def has_incomplete_tail(self) -> bool:
        """尾部是否存在未闭合的残缺记录（截断时会被丢弃的部分）。"""
        return self._pos > self._last_complete_end

    # ── 快照 / 回滚（断流重试用）────────────────────────────────────────────

    def snapshot(self) -> Tuple[int, bool, bool, int, int, int, Optional[int]]:
        """导出当前状态，供窗口重试时回滚。

        断流重试的语义是「把本窗口写入的字节全部作废，回到窗口起点重发」，
        所以扫描器也必须回到窗口起点的状态 —— 否则本窗口已计入的记录数会重复累加，
        最终行数偏大。
        """
        return (
            self._pos, self._in_quote, self._pending_quote,
            self._last_complete_end, self._last_record_start, self._record_count,
            self._first_record_end,
        )

    def restore(
        self, snap: Tuple[int, bool, bool, int, int, int, Optional[int]],
    ) -> None:
        """回滚到 `snapshot()` 取得的状态。"""
        (
            self._pos, self._in_quote, self._pending_quote,
            self._last_complete_end, self._last_record_start, self._record_count,
            self._first_record_end,
        ) = snap

    # ── 扫描 ────────────────────────────────────────────────────────────────

    def feed(self, chunk: bytes) -> None:
        """喂入下一段字节，更新状态。可任意分片，结果与一次性喂入等价。"""
        if not chunk:
            return

        pos = self._pos
        in_quote = self._in_quote
        pending_quote = self._pending_quote
        last_end = self._last_complete_end
        last_start = self._last_record_start
        count = self._record_count
        first_end = self._first_record_end

        for i in range(len(chunk)):
            b = chunk[i]

            if pending_quote:
                # 上一个字节是引号内的 `"`，现在才能判定它的含义
                pending_quote = False
                if b == _QUOTE:
                    # `""` → 转义的字面双引号，仍在引号内；本字节消费完毕
                    pos += 1
                    continue
                # 上一个 `"` 是闭合引号 → 退出引号态，本字节按引号外规则继续处理
                in_quote = False

            if in_quote:
                if b == _QUOTE:
                    # 可能是闭合引号，也可能是 `""` 的前半 → 挂起到下一字节判定
                    pending_quote = True
                # 引号内的 \n / \r / , 都是数据，不做任何边界判定
            else:
                if b == _QUOTE:
                    in_quote = True
                elif b == _LF:
                    # 记录终止（CRLF 时 \n 是最后一个字节，切在它之后正确）
                    last_start = last_end
                    last_end = pos + 1
                    count += 1
                    if first_end is None:
                        first_end = last_end

            pos += 1

        self._pos = pos
        self._in_quote = in_quote
        self._pending_quote = pending_quote
        self._last_complete_end = last_end
        self._last_record_start = last_start
        self._record_count = count
        self._first_record_end = first_end


# ── 单条记录的字段解析 ───────────────────────────────────────────────────────

def split_record_fields(record: bytes, *, encoding: str = "utf-8") -> List[str]:
    """把**一条**完整 CSV 记录的字节解析为字段列表。

    用 `csv.reader` 而非 `split(',')`：字段可能被引号包裹并含逗号 / 换行 / `""`。
    记录允许跨物理行（引号内换行），此时 csv.reader 仍归并为一条记录。

    Args:
        record: 一条完整记录的字节，可含尾部 `\\n` / `\\r\\n`（会被 csv 模块吸收）。
                允许带 UTF-8 BOM（用 utf-8-sig 容错，避免首字段带 \\ufeff）。

    Returns:
        字段字符串列表；空记录返回 []。
    """
    if not record:
        return []
    text = record.decode("utf-8-sig" if encoding == "utf-8" else encoding,
                         errors="replace")
    reader = csv.reader(io.StringIO(text, newline=""))
    for row in reader:
        return row
    return []


def extract_cursor_from_record(
    record: bytes,
    cursor_idx: int,
    *,
    encoding: str = "utf-8",
) -> Optional[str]:
    """从一条完整 CSV 记录里取出第 `cursor_idx` 个字段（游标值）。

    Returns:
        字段字符串；记录为空或列数不足时返回 None（由调用方决定如何报错）。

    注意：返回的是**原始字段文本**。ClickHouse CSV 里 NULL 是字面量 `\\N`
    （IDN 实测），调用方须自行识别并按「游标列为 NULL」处理 —— keyset 在
    `WHERE cursor > NULL` 下永远推进不动。
    """
    fields = split_record_fields(record, encoding=encoding)
    if not fields or cursor_idx < 0 or cursor_idx >= len(fields):
        return None
    return fields[cursor_idx]
