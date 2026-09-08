"""
CSV 合并核心逻辑 —— 纯逻辑、零框架依赖（只用 numpy / csv / re / pathlib）

为什么单独成一个模块：
    合并 CSV 的正确性全部压在「记录边界在哪」这一个判断上，而这个判断跟
    FastAPI、SQLAlchemy、线程池毫无关系。把它隔离出来才能 100% 单测覆盖
    —— 包括 6000 例 RFC4180 模糊测试与 chunk 任意分片等价性。

核心手法：numpy 引号奇偶扫描
    RFC4180 的一个性质：`""` 转义是**两个**引号，不改变奇偶性。因此
        截至字节 i 的引号累计数为奇数  ⟺  字节 i 位于引号内
    恒成立。于是「引号外的 \\n」就是真正的记录终止符，而这个判断可以用
    `np.bitwise_xor.accumulate(bytes == 0x22)` 一次向量化算出来。
    实测 235 MB/s（对比 csv_tail.py 的 Python 逐字节循环 5-15 MB/s）。

    跨 chunk 只需携带 1 bit 状态（carry = 上一 chunk 结束时的引号奇偶），
    任意分片结果等价。

已知限制（务必如实告知使用者）：
    奇偶模型**无法区分**「`""` 转义」与「先闭合再重开」—— 两者奇偶序列同构。
    这正是记录计数仍然正确的原因，但也意味着「开引号位置是否合法」在该模型
    内不可表达。对**畸形 CSV**（双引号出现在非引号字段中间，如 `ab"cd,e`），
    本模块的计数会与 Python csv 模块的宽容解读分歧。

    处理策略：
      1. 字节拷贝永远字节精确，不受影响；
      2. 唯一有真实数据损坏风险的是「表头边界错位」→ 用 locate_header() 里的
         csv 模块精确交叉校验硬挡（必须恰好解析出 1 条记录）；
      3. 尾部奇偶必须闭合，挡住截断 / 仍在写入的文件。

    行数口径定义为「引号外 \\n 的个数，按 RFC4180」。ClickHouse
    FORMAT CSVWithNames 与 Excel 导出均符合 RFC4180，此口径对它们精确。
"""
from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np

__all__ = [
    "QuoteParityScanner",
    "FileProbe",
    "HeaderInfo",
    "EncodingInfo",
    "ValidationResult",
    "MergeResult",
    "scan_bytes",
    "locate_header",
    "classify_encoding",
    "check_encoding_consistency",
    "natural_sort_key",
    "lexicographic_sort_key",
    "sort_files",
    "probe_file",
    "validate_batch",
    "merge_csv_files",
    "UTF8_BOM",
    "DEFAULT_CHUNK_SIZE",
    "HEAD_PROBE_BYTES",
    "MAX_HEADER_BYTES",
]

# ── 常量 ──────────────────────────────────────────────────────────────────────

_QUOTE = 0x22   # "
_LF = 0x0A      # \n
_NUL = 0x00

UTF8_BOM = b"\xef\xbb\xbf"
_UTF16_LE_BOM = b"\xff\xfe"
_UTF16_BE_BOM = b"\xfe\xff"
_UTF32_LE_BOM = b"\xff\xfe\x00\x00"
_UTF32_BE_BOM = b"\x00\x00\xfe\xff"

DEFAULT_CHUNK_SIZE = 8 * 1024 * 1024    # 8 MB：拷贝与扫描的粒度，也是取消响应粒度
HEAD_PROBE_BYTES = 64 * 1024            # 提交时每文件探测的头部字节数
MAX_HEADER_BYTES = 16 * 1024 * 1024     # 表头长度上限；超过视为文件结构异常

# 无 BOM 的 UTF-16 侦测阈值：UTF-16LE 的 ASCII 文本有约 50% 是 NUL 字节，
# 而合法 UTF-8 CSV 里 NUL 应当一个都没有。25% 是很宽的安全线。
_NUL_RATIO_UTF16_SUSPECT = 0.25


# ─────────────────────────────────────────────────────────────────────────────
# 1. 引号奇偶扫描器
# ─────────────────────────────────────────────────────────────────────────────

class QuoteParityScanner:
    """流式统计 CSV 字节里的**真实记录数**（引号外的 \\n）。

    典型用法（与写文件同一遍完成，因此精确行数实质免费）：

        sc = QuoteParityScanner()
        while chunk := src.read(CHUNK):
            out.write(chunk)
            sc.feed(chunk)
        assert not sc.in_quote          # 引号必须闭合，否则文件被截断
        rows = sc.records

    偏移语义：
        `first_record_end` 是**绝对偏移**（由 start_offset 起算），指向第一条
        完整记录的终止符 \\n 之后的第一个字节 —— 可直接用作 seek() 的实参。
    """

    __slots__ = ("_pos", "_start", "_carry", "_records", "_physical", "_first_end")

    def __init__(self, start_offset: int = 0, carry: bool = False) -> None:
        self._pos = start_offset
        self._start = start_offset
        #: 引号奇偶状态：True = 当前处于引号内
        self._carry = carry
        self._records = 0
        self._physical = 0
        self._first_end: Optional[int] = None

    # ── 只读属性 ──

    @property
    def records(self) -> int:
        """完整记录条数（引号外 \\n 的个数）。含表头，如果表头也被喂进来了。"""
        return self._records

    @property
    def physical_lines(self) -> int:
        """物理行数（所有 \\n 的个数）。`physical_lines - records` = 字段内换行条数。"""
        return self._physical

    @property
    def in_quote(self) -> bool:
        """喂入的字节结束时是否仍处在引号内。True → 文件被截断或仍在写入。"""
        return self._carry

    @property
    def first_record_end(self) -> Optional[int]:
        """第一条完整记录之后的绝对偏移；尚无完整记录时为 None。"""
        return self._first_end

    @property
    def position(self) -> int:
        """已消费到的绝对偏移。"""
        return self._pos

    # ── 扫描 ──

    def feed(self, chunk: bytes) -> None:
        """喂入下一段字节。可任意分片，结果与一次性喂入等价。"""
        if not chunk:
            return

        a = np.frombuffer(chunk, dtype=np.uint8)
        nl = a == _LF
        isq = a == _QUOTE

        self._physical += int(np.count_nonzero(nl))

        if not isq.any():
            # 快路径：本 chunk 无引号 → 奇偶状态不变。
            # 纯数字 / 纯 ID 的 CSV 走这条，省掉 accumulate 与两次数组分配。
            if self._carry:
                # 整个 chunk 都在一个跨 chunk 的引号内 → 所有 \n 都是数据
                term = None
            else:
                term = nl
        else:
            parity = np.bitwise_xor.accumulate(isq)
            inside = np.bitwise_xor(parity, self._carry)
            term = nl & ~inside
            self._carry = bool(inside[-1])

        if term is not None:
            n = int(np.count_nonzero(term))
            if n:
                if self._first_end is None:
                    self._first_end = self._pos + int(np.flatnonzero(term)[0]) + 1
                self._records += n

        self._pos += a.size

    # ── 快照 / 回滚（失败重试用）──

    def snapshot(self) -> Tuple[int, bool, int, int, Optional[int]]:
        return (self._pos, self._carry, self._records, self._physical, self._first_end)

    def restore(self, snap: Tuple[int, bool, int, int, Optional[int]]) -> None:
        self._pos, self._carry, self._records, self._physical, self._first_end = snap


def scan_bytes(data: bytes, chunk_size: int = DEFAULT_CHUNK_SIZE) -> QuoteParityScanner:
    """便捷函数：对一段完整字节跑扫描器（主要给测试用）。"""
    sc = QuoteParityScanner()
    for i in range(0, len(data), chunk_size):
        sc.feed(data[i:i + chunk_size])
    return sc


# ─────────────────────────────────────────────────────────────────────────────
# 2. 表头定位与交叉校验
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class HeaderInfo:
    """第一条完整记录（表头，或 has_header=False 时的首条数据）的解析结果。"""
    fields: List[str]
    col_count: int
    #: 第一条记录终止符之后的绝对偏移（**含** bom_len），后续文件从这里开始拷贝
    header_end: int
    bom_len: int
    raw: bytes


def locate_header(
    head: bytes,
    *,
    encoding: str = "utf-8",
) -> HeaderInfo:
    """定位并解析首条完整记录。

    两步走，第二步是关键的安全阀：
      1. 用奇偶法找第一个「引号外的 \\n」→ header_end
      2. 把 [bom_len, header_end) 这一段交给 **Python csv 模块精确解析**，
         结果必须**恰好 1 条记录**

    第 2 步存在的理由：奇偶模型对畸形 CSV（引号出现在非引号字段中间）会把
    记录边界判错，而表头边界判错会导致后续文件多跳 / 少跳数据行 —— 这是本
    工具唯一会造成真实数据损坏的失效模式。交叉校验把它变成硬报错。

    Raises:
        ValueError: 未找到记录终止符 / 表头过长 / 交叉校验不通过。
    """
    bom_len = 3 if head.startswith(UTF8_BOM) else 0

    sc = QuoteParityScanner()
    sc.feed(head)
    end = sc.first_record_end

    if end is None:
        if len(head) >= MAX_HEADER_BYTES:
            raise ValueError(
                f"前 {MAX_HEADER_BYTES // 1024 // 1024} MB 内未找到记录终止符，"
                f"文件结构异常（表头过长或不是 CSV）"
            )
        raise ValueError("未找到记录终止符（文件可能为空、只有一行且无换行符，或不是 CSV）")

    raw = head[bom_len:end]
    text = raw.decode(encoding, errors="replace")
    rows = list(csv.reader(io.StringIO(text, newline="")))

    if len(rows) != 1:
        raise ValueError(
            f"表头交叉校验失败：奇偶法定位的首条记录用 csv 模块解析得到 "
            f"{len(rows)} 条记录（应为 1 条），文件的 CSV 结构异常"
        )

    fields = rows[0]
    return HeaderInfo(
        fields=fields,
        col_count=len(fields),
        header_end=end,
        bom_len=bom_len,
        raw=raw,
    )


# ─────────────────────────────────────────────────────────────────────────────
# 3. 编码分类与一致性
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class EncodingInfo:
    #: utf8 | ascii | other | utf16 | utf32
    kind: str
    #: 具体编码名（utf-8 / gb18030 / ...）；utf16/utf32 时为族名
    name: str
    has_bom: bool
    bom_len: int
    #: 非 UTF-8 时，charset_normalizer 是否给出了推测（False → 名字不可信）
    guessed: bool = False


def _strict_utf8_ok(data: bytes) -> bool:
    """严格 UTF-8 解码测试，容忍尾部被截断的多字节序列。

    head 是按固定字节数截出来的，很可能正好切在一个 3 字节汉字中间。
    不做这个容忍，几乎每个含中文的文件都会被误判成非 UTF-8。
    """
    try:
        data.decode("utf-8")
        return True
    except UnicodeDecodeError as e:
        # 只有当错误发生在末尾 3 字节内，才可能是截断造成的
        if e.start >= len(data) - 3:
            try:
                data[:e.start].decode("utf-8")
                return True
            except UnicodeDecodeError:
                return False
        return False


def classify_encoding(head: bytes) -> EncodingInfo:
    """按头部字节判定编码族。

    顺序很重要：UTF-32LE 的 BOM 前两字节与 UTF-16LE 的 BOM 相同，
    必须先判 UTF-32。
    """
    if head.startswith(_UTF32_LE_BOM) or head.startswith(_UTF32_BE_BOM):
        return EncodingInfo(kind="utf32", name="utf-32", has_bom=True, bom_len=4)
    if head.startswith(_UTF16_LE_BOM) or head.startswith(_UTF16_BE_BOM):
        return EncodingInfo(kind="utf16", name="utf-16", has_bom=True, bom_len=2)
    if head.startswith(UTF8_BOM):
        return EncodingInfo(kind="utf8", name="utf-8", has_bom=True, bom_len=3)

    if head and head.count(b"\x00") > len(head) * _NUL_RATIO_UTF16_SUSPECT:
        # 无 BOM 的宽字符编码。必须拦住：UTF-16 里 \n 是 0A 00，
        # 字节级记录边界与 BOM 剥离全部不成立。
        return EncodingInfo(kind="utf16", name="utf-16(no BOM)", has_bom=False, bom_len=0)

    if _strict_utf8_ok(head):
        if not head or max(head) < 0x80:
            # 纯 ASCII：与任何单/双字节编码兼容，不参与一致性判定
            return EncodingInfo(kind="ascii", name="ascii", has_bom=False, bom_len=0)
        return EncodingInfo(kind="utf8", name="utf-8", has_bom=False, bom_len=0)

    # 非 UTF-8 的 8 位编码：用 charset_normalizer 猜个名字（统计推测，不可当真）
    name, guessed = "unknown-8bit", False
    try:
        from charset_normalizer import from_bytes
        best = from_bytes(head).best()
        if best is not None and best.encoding:
            name, guessed = str(best.encoding).lower(), True
    except Exception:
        pass
    return EncodingInfo(kind="other", name=name, has_bom=False, bom_len=0, guessed=guessed)


def check_encoding_consistency(
    infos: Sequence[EncodingInfo],
    filenames: Sequence[str],
) -> Tuple[bool, List[str], List[str], str]:
    """判定一批文件的编码是否可以字节级拼接。

    规则（需求方确认：口径是「编码必须一致」，不是「必须 UTF-8」）：
      - 任一 UTF-16/32              → 硬阻断
      - 全 utf8 系（ascii 视为兼容）→ 放行，输出 UTF-8
      - 全非 utf8 且推测名一致       → 放行，输出同编码，但**必给 warning**
      - 混合                        → 阻断

    Returns:
        (ok, errors, warnings, output_encoding)
    """
    errors: List[str] = []
    warnings: List[str] = []

    wide = [
        f"{fn}（{ei.name}）"
        for fn, ei in zip(filenames, infos)
        if ei.kind in ("utf16", "utf32")
    ]
    if wide:
        errors.append(
            "以下文件是 UTF-16/UTF-32 宽字符编码，无法字节级合并（该编码下换行符是"
            " 0A 00，记录边界判定不成立），请先转成 UTF-8："
            + "；".join(wide)
        )
        return False, errors, warnings, ""

    non_ascii = [(fn, ei) for fn, ei in zip(filenames, infos) if ei.kind != "ascii"]
    if not non_ascii:
        return True, errors, warnings, "utf-8"      # 全 ASCII，当 UTF-8 处理

    kinds = {ei.kind for _, ei in non_ascii}

    if kinds == {"utf8"}:
        return True, errors, warnings, "utf-8"

    if kinds == {"other"}:
        names = {ei.name for _, ei in non_ascii}
        if len(names) == 1:
            nm = next(iter(names))
            warnings.append(
                f"所有文件均为非 UTF-8 编码（推测为 {nm}），已按原字节合并，"
                f"输出编码与源文件一致、不加 BOM。注意：编码推测基于统计特征，"
                f"「都推测为 {nm}」不等于「确实都是 {nm}」，请自行确认。"
            )
            return True, errors, warnings, nm
        detail = "；".join(f"{fn}（{ei.name}）" for fn, ei in non_ascii)
        errors.append(
            "以下文件被推测为**不同的**非 UTF-8 编码，混合拼接会产生乱码：" + detail
        )
        return False, errors, warnings, ""

    # utf8 与 other 混合
    detail = "；".join(f"{fn}（{ei.name}）" for fn, ei in non_ascii)
    errors.append(
        "以下文件编码不一致（UTF-8 与非 UTF-8 混合），字节级拼接会产生"
        "一半正常一半乱码的文件：" + detail
    )
    return False, errors, warnings, ""


# ─────────────────────────────────────────────────────────────────────────────
# 4. 排序
# ─────────────────────────────────────────────────────────────────────────────

_DIGITS_RE = re.compile(r"(\d+)")


def natural_sort_key(name: str) -> Tuple:
    """自然排序键（数字段按数值比较）。

    为什么它是默认：对定长日期文件名（`export_20260601_to_20260601.csv`）结果与
    字典排序**逐字节一致**；同时顺手修掉 `part1, part10, part11, part2` 这个
    字典排序的经典错序 —— 而错序不报任何错，产出的是静默乱序的数据。

    不做大小写折叠：折叠会让排序结果偏离字典排序，反而不可预测。
    """
    parts = _DIGITS_RE.split(name)
    key: List[Tuple[int, int, str]] = []
    for i, p in enumerate(parts):
        if i % 2 == 1:                      # split 的奇数位一定是数字段
            key.append((0, int(p), ""))
        else:
            key.append((1, 0, p))
    return tuple(key)


def lexicographic_sort_key(name: str) -> str:
    return name


def sort_files(files: Sequence[Dict[str, Any]], mode: str = "natural") -> List[Dict[str, Any]]:
    """按 filename 排序。mode: natural（默认）| lexicographic"""
    if mode == "lexicographic":
        return sorted(files, key=lambda f: lexicographic_sort_key(f["filename"]))
    if mode != "natural":
        raise ValueError(f"未知排序方式: {mode}")
    # 次级键用原始文件名，保证自然键相同时（如 'a01' vs 'a1'）顺序稳定可复现
    return sorted(files, key=lambda f: (natural_sort_key(f["filename"]), f["filename"]))


# ─────────────────────────────────────────────────────────────────────────────
# 5. 单文件探测
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class FileProbe:
    filename: str
    file_path: str
    size: int = 0
    origin: str = "server"
    mtime: float = 0.0
    encoding: Optional[EncodingInfo] = None
    header: Optional[HeaderInfo] = None
    ends_with_lf: bool = True
    is_empty: bool = False
    error: Optional[str] = None

    @property
    def col_count(self) -> int:
        return self.header.col_count if self.header else 0

    @property
    def header_fields(self) -> List[str]:
        return self.header.fields if self.header else []


def _read_header_region(fp, size: int) -> bytes:
    """读足以覆盖首条完整记录的头部字节。

    先读 HEAD_PROBE_BYTES；若其中没有「引号外的 \\n」，成倍扩读到
    MAX_HEADER_BYTES 为止 —— 表头本身可以很长（含引号内换行的列名），
    但不能无限长，否则说明这不是 CSV。
    """
    want = min(HEAD_PROBE_BYTES, size) if size else HEAD_PROBE_BYTES
    fp.seek(0)
    data = fp.read(want)
    while True:
        sc = QuoteParityScanner()
        sc.feed(data)
        if sc.first_record_end is not None:
            return data
        if len(data) >= min(MAX_HEADER_BYTES, size or MAX_HEADER_BYTES):
            return data
        more = fp.read(min(len(data) or HEAD_PROBE_BYTES, MAX_HEADER_BYTES - len(data)))
        if not more:
            return data
        data += more


def probe_file(
    file_path: str,
    filename: Optional[str] = None,
    origin: str = "server",
) -> FileProbe:
    """提交时的单文件探测：大小、mtime、编码、表头、尾部换行。

    只读头部（通常 64 KB）+ 尾部 1 字节，几十个文件的成本可忽略。

    注意：**尾部引号是否闭合无法在这里廉价判定**（奇偶性需从文件头累积），
    该检测在合并过程中作为副产品完成（见 merge_csv_files 的 R5）。
    """
    p = Path(file_path)
    pr = FileProbe(
        filename=filename or p.name,
        file_path=str(file_path),
        origin=origin,
    )

    try:
        st = p.stat()
    except OSError as e:
        pr.error = f"无法访问文件：{e}"
        return pr

    if not p.is_file():
        pr.error = "不是一个普通文件"
        return pr

    pr.size = st.st_size
    pr.mtime = st.st_mtime

    if pr.size == 0:
        pr.is_empty = True
        pr.error = "文件为空（0 字节）"
        return pr

    try:
        with p.open("rb") as fp:
            head = _read_header_region(fp, pr.size)
            fp.seek(max(0, pr.size - 1))
            tail = fp.read(1)
    except OSError as e:
        pr.error = f"读取失败：{e}"
        return pr

    pr.ends_with_lf = tail == b"\n"
    pr.encoding = classify_encoding(head)

    if pr.encoding.kind in ("utf16", "utf32"):
        # 宽字符编码下表头解析没有意义，交给一致性校验统一报错
        return pr

    dec = "utf-8" if pr.encoding.kind in ("utf8", "ascii") else pr.encoding.name
    try:
        pr.header = locate_header(head, encoding=dec)
    except ValueError as e:
        pr.error = str(e)
    except LookupError:
        pr.error = f"未知编码 {dec}，无法解析表头"
    return pr


# ─────────────────────────────────────────────────────────────────────────────
# 6. 批量校验
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ValidationResult:
    ok: bool = False
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    baseline_header: List[str] = field(default_factory=list)
    col_count: int = 0
    output_encoding: str = "utf-8"
    output_bom: bool = False
    total_bytes: int = 0


def validate_batch(
    probes: Sequence[FileProbe],
    *,
    has_header: bool = True,
    strict_header: bool = True,
) -> ValidationResult:
    """以排序后**首个文件**为基准做批量校验（V1–V6 的纯逻辑部分）。

    V7（export_jobs 反查）/ V8（mtime）/ V9（磁盘空间）/ V10（锚定 size）
    依赖 DB 与运行环境，留在 service 层。
    """
    res = ValidationResult()

    if not probes:
        res.errors.append("未提供任何待合并文件")
        return res
    if len(probes) < 2:
        res.warnings.append("只有 1 个文件，合并结果等同于原文件的一份拷贝")

    # ── V1 逐文件硬错误 ──
    hard = [f"{p.filename}：{p.error}" for p in probes if p.error]
    if hard:
        res.errors.append("以下文件无法读取或结构异常：" + "；".join(hard))
        return res

    res.total_bytes = sum(p.size for p in probes)

    # ── V2 编码一致性 ──
    ok_enc, enc_errors, enc_warnings, out_enc = check_encoding_consistency(
        [p.encoding for p in probes], [p.filename for p in probes]
    )
    res.warnings.extend(enc_warnings)
    if not ok_enc:
        res.errors.extend(enc_errors)
        return res
    res.output_encoding = out_enc
    # 输出是否带 BOM：跟随首文件（只对 UTF-8 有意义；非 UTF-8 一律不加）
    res.output_bom = bool(
        out_enc == "utf-8" and probes[0].encoding and probes[0].encoding.has_bom
    )

    base = probes[0]
    res.baseline_header = list(base.header_fields)
    res.col_count = base.col_count

    # ── V4 列数一致 ──
    mismatched = [
        f"{p.filename}（{p.col_count} 列）"
        for p in probes[1:]
        if p.col_count != res.col_count
    ]
    if mismatched:
        res.errors.append(
            f"以下文件列数与首文件 {base.filename}（{res.col_count} 列）不一致，无法合并："
            + "；".join(mismatched)
        )
        return res

    # ── V5 表头文字一致 ──
    if has_header:
        diffs: List[str] = []
        for p in probes[1:]:
            for i, (a, b) in enumerate(zip(res.baseline_header, p.header_fields)):
                if a.strip() != b.strip():
                    diffs.append(f"{p.filename} 第 {i + 1} 列：首文件 “{a}” vs “{b}”")
        if diffs:
            msg = (
                "以下文件的表头文字与首文件不一致。按位置合并会把不同语义的数据"
                "混进同一列，而且**不会报任何错**：" + "；".join(diffs)
            )
            if strict_header:
                res.errors.append(msg + "。确认列语义未变时可关闭「严格表头校验」后重试。")
                return res
            res.warnings.append(msg + "。已按「严格表头校验=关」放行，按位置合并。")

    # ── V6 尾部换行弱检测 ──
    no_lf = [p.filename for p in probes if not p.ends_with_lf]
    if no_lf:
        res.warnings.append(
            "以下文件末尾无换行符，合并时会自动补齐（否则会与下一个文件的首行粘成一行）；"
            "若该文件当时仍在写入，其末行可能残缺：" + "；".join(no_lf)
        )

    res.ok = True
    return res


# ─────────────────────────────────────────────────────────────────────────────
# 7. 合并
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class MergeResult:
    #: completed | cancelled | failed
    status: str = "completed"
    total_rows: int = 0
    total_physical_lines: int = 0
    done_files: int = 0
    done_bytes: int = 0
    output_size: int = 0
    last_merged_file: Optional[str] = None
    warnings: List[str] = field(default_factory=list)
    error: Optional[str] = None
    #: 回填后的源文件清单（含每文件贡献行数）
    per_file: List[Dict[str, Any]] = field(default_factory=list)


class MergeCancelled(Exception):
    """内部信号：协作式取消。"""


def merge_csv_files(
    sorted_files: Sequence[Dict[str, Any]],
    output_path: str,
    *,
    has_header: bool = True,
    output_bom: bool = True,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    on_progress: Optional[Callable[[int, int, int, int], None]] = None,
    on_file_done: Optional[Callable[[int, Dict[str, Any]], None]] = None,
    should_cancel: Optional[Callable[[], bool]] = None,
) -> MergeResult:
    """字节级拼接多个 CSV 为一个文件。

    Args:
        sorted_files: **已排序**的文件清单，每项需含
            ``{filename, file_path, size, header_end, bom_len}``。
            ``size`` 是提交时**锚定**的字节数 —— 只读这么多，多出来的一律不读，
            这样即使文件在校验与拷贝之间又被追加，也绝不会读进新写入的半截数据。
        output_bom: 输出是否以 UTF-8 BOM 开头（每个源文件自身的 BOM 一律剥离）。
        on_progress: ``(done_bytes, done_files, total_rows, total_physical)``，
            每 chunk 调用一次；DB 写入节流由调用方负责。
        should_cancel: 每 chunk 调用一次；返回 True 则回退到**上一个文件边界**
            并以 ``cancelled`` 结束。节流同样由调用方负责。

    合并语义：
        - 每个源文件的 BOM 都剥离；输出的 BOM（如需）由本函数统一写一次
        - has_header=True 时，首文件保留表头，后续文件从 ``header_end`` 开始
        - 每个源文件拷完后：引号必须闭合（否则失败并回退），末尾必须是 \\n
          （否则补一个并记 warning）
        - 取消 / 失败一律 truncate 回**当前文件开始前**的偏移，因此结果文件
          永远在完整文件边界上截断，是有效 CSV
    """
    res = MergeResult()
    per_file: List[Dict[str, Any]] = []

    out_dir = Path(output_path).parent
    out_dir.mkdir(parents=True, exist_ok=True)

    with open(output_path, "wb") as out:
        if output_bom:
            out.write(UTF8_BOM)

        # 回退点。先给个初值，避免"循环体第一句就抛异常"时 except 里未绑定。
        offset_before = out.tell()

        try:
            for idx, f in enumerate(sorted_files):
                offset_before = out.tell()
                filename = f["filename"]
                size = int(f["size"])
                bom_len = int(f.get("bom_len", 0))
                header_end = int(f.get("header_end", bom_len))

                # 三个偏移，把「写什么」和「扫什么」彻底分开：
                #   copy_start  从哪里开始**写**（一律跳过本文件自己的 BOM）
                #   scan_start  从哪里开始**扫**（有表头时一律跳过表头那条记录）
                #
                # 首文件是唯一 copy_start < scan_start 的情况：表头要写出去，但
                # **不喂给扫描器**。这样 rows 与 physical_lines 都是纯数据口径，
                # 二者之差就是真正的「字段内换行条数」。
                # 早先的写法是「表头也扫、事后 records - 1」，那让 physical_lines
                # 永远比 rows 多算一行表头 → 诊断字段恒定虚高 1（假阳性），
                # 而且表头自身含引号内换行时减 1 还不够。
                copy_start = min(bom_len if (idx == 0 or not has_header) else header_end, size)
                scan_start = min(max(header_end if has_header else bom_len, copy_start), size)

                header_len = scan_start - copy_start    # 只写不扫
                body_len = size - scan_start            # 写 + 扫

                entry: Dict[str, Any] = dict(f)
                entry.update(rows=0, physical_lines=0, bytes_written=0)

                if header_len <= 0 and body_len <= 0:
                    # 空文件，或（非首文件的）只有表头没有数据行
                    if size == 0:
                        res.warnings.append(f"{filename}：文件为空，已跳过")
                    else:
                        res.warnings.append(f"{filename}：只有表头没有数据行，贡献 0 行")
                    per_file.append(entry)
                    res.done_files += 1
                    res.done_bytes += size
                    res.last_merged_file = filename
                    if on_file_done:
                        on_file_done(idx, entry)
                    continue

                sc = QuoteParityScanner()
                written = 0
                # 实际写出的最后一个字节。用它判断要不要补换行，而**不是**信任
                # 调用方传进来的 ends_with_lf —— 调用方漏传就会让文件粘行，
                # 而这里是免费且权威的。
                last_byte: Optional[int] = None

                with open(f["file_path"], "rb") as src:
                    # ── 表头段：只写不扫（仅首文件走到这里）──
                    if header_len > 0:
                        src.seek(copy_start)
                        head_bytes = src.read(header_len)
                        if len(head_bytes) != header_len:
                            raise OSError(
                                f"{filename}：读表头时提前 EOF"
                                f"（{len(head_bytes)}/{header_len} 字节）"
                            )
                        out.write(head_bytes)
                        written += header_len
                        last_byte = head_bytes[-1]

                    # ── 数据段：写 + 扫 ──
                    src.seek(scan_start)
                    remaining = body_len
                    while remaining > 0:
                        if should_cancel is not None and should_cancel():
                            raise MergeCancelled()
                        buf = src.read(min(chunk_size, remaining))
                        if not buf:
                            # 文件比锚定 size 短了（被截断 / 被替换）
                            raise OSError(
                                f"{filename}：读到 {written}/{header_len + body_len} 字节时"
                                f"提前 EOF，文件可能已被截断或替换"
                            )
                        remaining -= len(buf)
                        out.write(buf)
                        sc.feed(buf)
                        written += len(buf)
                        last_byte = buf[-1]
                        if on_progress:
                            on_progress(
                                res.done_bytes + copy_start + written,
                                res.done_files,
                                res.total_rows + sc.records,
                                res.total_physical_lines + sc.physical_lines,
                            )

                # ── R5 引号必须闭合 ──
                if sc.in_quote:
                    raise ValueError(
                        f"{filename}：CSV 引号未闭合（扫完 {written} 字节仍处于引号内），"
                        f"该文件疑似被截断或仍在写入，已终止合并"
                    )

                # ── R6 末尾必须是记录终止符 ──
                extra_rows = 0
                if last_byte != _LF:
                    out.write(b"\n")
                    written += 1
                    extra_rows = 1
                    res.warnings.append(
                        f"{filename}：末尾无换行符，已补齐（否则会与下一个文件首行粘成一行）；"
                        f"若该文件当时仍在写入，其末行可能残缺"
                    )

                file_rows = sc.records + extra_rows
                entry.update(
                    rows=file_rows,
                    physical_lines=sc.physical_lines + extra_rows,
                    bytes_written=written,
                )
                per_file.append(entry)

                res.total_rows += file_rows
                res.total_physical_lines += sc.physical_lines + extra_rows
                res.done_files += 1
                res.done_bytes += size
                res.last_merged_file = filename
                if on_file_done:
                    on_file_done(idx, entry)

            res.status = "completed"

        except MergeCancelled:
            out.truncate(offset_before)
            out.seek(offset_before)
            res.status = "cancelled"
        except (OSError, ValueError) as e:
            out.truncate(offset_before)
            out.seek(offset_before)
            res.status = "failed"
            res.error = str(e)

        out.flush()
        res.output_size = out.tell()

    res.per_file = per_file
    return res


