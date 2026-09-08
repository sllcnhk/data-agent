"""
小工具 - 合并CSV文件 单元测试

A · 引号奇偶扫描核心（含 6000 例 RFC4180 模糊测试 vs 独立三状态机）
B · 表头边界定位与 csv 模块交叉校验
C · 编码分类与一致性（UTF-8 / GBK / UTF-16 / 混合）
D · 结构校验（列数 / 表头文字 / has_header / 尾换行）
E · 排序（自然排序 vs 字典排序）
F · 合并正确性（BOM / 表头去重 / 补换行 / 锚定 size）
G · 取消语义（回退到文件边界、last_merged_file）
H · 失败与清理（引号未闭合 / 提前 EOF / 磁盘满）
I · 端到端与压力
J · service 层：路径边界、目录列举、export_jobs 反查、磁盘预检
K · REST API 端点（权限、状态码、上传、预检、取消、删除、下载）
L · 行数对账（matched / mismatched / unavailable）
M · RBAC 权限种子

运行：
    /d/ProgramData/Anaconda3/envs/dataagent/python.exe -m pytest test_merge_csv.py -v
"""
import csv
import io
import os
import random
import sys
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent))
os.environ.setdefault("ENABLE_AUTH", "False")

_PREFIX = f"_t_mcsv_{uuid.uuid4().hex[:6]}_"

from backend.services.csv_merge_core import (
    DEFAULT_CHUNK_SIZE,
    UTF8_BOM,
    MergeResult,
    QuoteParityScanner,
    check_encoding_consistency,
    classify_encoding,
    locate_header,
    merge_csv_files,
    natural_sort_key,
    probe_file,
    scan_bytes,
    sort_files,
    validate_batch,
)

CHUNKS = (1, 2, 3, 7, 64, 4096, DEFAULT_CHUNK_SIZE)


# ─────────────────────────────────────────────────────────────────────────────
# 工具
# ─────────────────────────────────────────────────────────────────────────────

def csv_records(data: bytes, encoding: str = "utf-8") -> int:
    """用 Python csv 模块数记录数（真值来源）。"""
    text = data.decode(encoding)
    if text.startswith("﻿"):
        text = text[1:]
    return sum(1 for _ in csv.reader(io.StringIO(text, newline="")))


def write_file(d: Path, name: str, data: bytes) -> Path:
    p = d / name
    p.write_bytes(data)
    return p


def ref_scan(data: bytes):
    """朴素 RFC4180 三状态机 —— 慢但显然正确，作为奇偶法的**独立**真值来源。

    它区分「`""` 转义」与「闭合引号」，而奇偶法刻意不区分（那正是奇偶法成立的
    原因）。两个实现互不借用任何逻辑，因此它们一致才有说服力；用 csv 模块去
    猜「前缀到哪算一条记录」是不可靠的（未闭合引号的前缀也会被解析成 1 行）。

    Returns: (records, first_record_end, in_quote)
    """
    OUT, IN, PENDING = 0, 1, 2
    st = OUT
    records = 0
    first_end = None
    for i, b in enumerate(data):
        if st == OUT:
            if b == 0x22:
                st = IN
            elif b == 0x0A:
                records += 1
                if first_end is None:
                    first_end = i + 1
        elif st == IN:
            if b == 0x22:
                st = PENDING            # 尚不能判定是闭合还是 "" 的前半
        else:                            # PENDING
            if b == 0x22:
                st = IN                  # "" → 转义的字面引号，仍在引号内
            else:
                st = OUT                 # 上一个引号是闭合引号
                if b == 0x0A:
                    records += 1
                    if first_end is None:
                        first_end = i + 1
    return records, first_end, st == IN


def entry(p: Path, **kw):
    """把 probe 结果转成 merge_csv_files 需要的清单项。"""
    pr = probe_file(str(p))
    e = {
        "filename": pr.filename,
        "file_path": pr.file_path,
        "size": pr.size,
        "bom_len": pr.encoding.bom_len if pr.encoding else 0,
        "header_end": pr.header.header_end if pr.header else 0,
    }
    e.update(kw)
    return e


# ═════════════════════════════════════════════════════════════════════════════
# A  引号奇偶扫描核心
# ═════════════════════════════════════════════════════════════════════════════

A_CASES = {
    "A1_plain":            b"a,b\n1,2\n3,4\n",
    "A2_quoted_comma":     b'"a,b",c\n"x,y",z\n',
    "A3_quoted_newline":   b'"line1\nline2",c\n"p\nq\nr",d\n',
    "A4_escaped_quote":    b'"he said ""hi""",c\n"a""""b",d\n',
    "A5_quote_then_nl":    b'"a""\nb",c\n',
    "A6_crlf":             b'a,b\r\n"x\r\ny",z\r\n',
    "A7_empty_fields":     b'"",""\n,,\n',
    "A8_bom":              UTF8_BOM + b"a,b\n1,2\n",
    "A9_all_quoted":       b'"a","b"\n"1","2"\n',
    "A10_nl_in_last_col":  b'a,"b\nc"\n',
    "A11_quad_quote":      b'""""\n""""\n',
}


@pytest.mark.parametrize("name,data", sorted(A_CASES.items()))
@pytest.mark.parametrize("chunk", CHUNKS)
def test_A_scanner_matches_csv_module(name, data, chunk):
    """A1-A11：记录数必须与 Python csv 模块一致，且对任意 chunk 分片等价。"""
    sc = scan_bytes(data, chunk)
    assert sc.records == csv_records(data), f"{name} chunk={chunk}"
    assert sc.in_quote is False, f"{name}: 引号应当闭合"


def test_A12_chunk_split_inside_escaped_quote():
    """A12：chunk 恰好切在 `""` 转义对的中间，跨 chunk 状态必须正确。"""
    data = b'"a""b",c\n"d""e",f\n'
    pos = data.index(b'""')
    for split in (pos, pos + 1, pos + 2):
        sc = QuoteParityScanner()
        sc.feed(data[:split])
        sc.feed(data[split:])
        assert sc.records == csv_records(data), f"split={split}"
        assert sc.in_quote is False


def test_A13_physical_vs_records():
    """A13：physical_lines - records == 字段内换行的条数。"""
    data = b'"a\nb",1\n"c\nd\ne",2\nplain,3\n'
    sc = scan_bytes(data, 3)
    assert sc.records == 3
    assert sc.physical_lines == 6          # 3 条记录 + 3 个字段内换行
    assert sc.physical_lines - sc.records == 3


def test_A14_unclosed_quote_detected():
    """A14：引号未闭合（文件被截断 / 仍在写入）必须被检测到。"""
    for chunk in CHUNKS:
        sc = scan_bytes(b'a,b\n"unterminated,c\n', chunk)
        assert sc.in_quote is True, f"chunk={chunk}"


def test_A15_first_record_end():
    """A15：first_record_end 指向首条记录终止符之后。"""
    data = b'"h1","h2"\nv1,v2\n'
    for chunk in CHUNKS:
        sc = scan_bytes(data, chunk)
        assert sc.first_record_end == len(b'"h1","h2"\n'), f"chunk={chunk}"

    # 首条记录含引号内换行时，不能停在第一个物理换行上
    data2 = b'"h\n1","h2"\nv1,v2\n'
    for chunk in CHUNKS:
        sc = scan_bytes(data2, chunk)
        assert sc.first_record_end == len(b'"h\n1","h2"\n'), f"chunk={chunk}"


def test_A16_empty_and_no_terminator():
    """A16：空输入 / 无终止符的输入。"""
    sc = scan_bytes(b"", 8)
    assert (sc.records, sc.physical_lines, sc.first_record_end) == (0, 0, None)
    sc = scan_bytes(b"a,b", 2)
    assert sc.records == 0 and sc.first_record_end is None


def _gen_rfc4180(rng: random.Random) -> bytes:
    ncol = rng.randint(1, 4)
    rows = []
    for _ in range(rng.randint(1, 5)):
        f = []
        for _ in range(ncol):
            if rng.random() < 0.35:
                f.append(rng.choice(["", "a", "12", "x y"]))
            else:
                inner = "".join(
                    rng.choice(["a", "b", ",", "\n", '"', "\r\n", " "])
                    for _ in range(rng.randint(0, 6))
                )
                f.append('"' + inner.replace('"', '""') + '"')
        rows.append(",".join(f))
    return ("\n".join(rows) + "\n").encode()


def test_A17_fuzz_6000_legal_rfc4180():
    """A17：6000 例合法 RFC4180 模糊测试，对比 Python csv 模块。

    这是整个设计的地基 —— 奇偶法的正确性只对合法 RFC4180 成立，
    必须用大样本钉死，而不是靠推理。
    """
    rng = random.Random(11)
    n_count = n_carry = n_first = n_ref = 0
    for _ in range(6000):
        data = _gen_rfc4180(rng)
        ref_rows, ref_first, ref_inq = ref_scan(data)

        # 先自证真值：参考实现必须与 Python csv 模块一致，否则真值本身不可信
        if ref_rows != csv_records(data):
            n_ref += 1
            continue

        for chunk in (1, 2, 3, 7, 64, 10 ** 6):
            sc = scan_bytes(data, chunk)
            if sc.records != ref_rows:
                n_count += 1
                break
            if sc.in_quote != ref_inq:
                n_carry += 1
                break
            if sc.first_record_end != ref_first:
                n_first += 1
                break
    assert (n_ref, n_count, n_carry, n_first) == (0, 0, 0, 0), (
        f"真值自证失败 {n_ref} / 行数错 {n_count} / "
        f"引号态错 {n_carry} / 首记录边界错 {n_first}"
    )


# ═════════════════════════════════════════════════════════════════════════════
# B  表头边界定位与交叉校验
# ═════════════════════════════════════════════════════════════════════════════

def test_B1_locate_header_basic():
    h = locate_header(b'"Call ID","name"\n1,a\n')
    assert h.fields == ["Call ID", "name"]
    assert h.col_count == 2
    assert h.header_end == len(b'"Call ID","name"\n')
    assert h.bom_len == 0


def test_B2_locate_header_with_bom():
    h = locate_header(UTF8_BOM + b"a,b\n1,2\n")
    assert h.fields == ["a", "b"]
    assert h.bom_len == 3
    assert h.header_end == 3 + len(b"a,b\n")


def test_B3_header_with_quoted_newline():
    """B3：列名里含引号内换行，边界不能停在第一个物理换行。"""
    h = locate_header(b'"col\nA","B"\n1,2\n')
    assert h.fields == ["col\nA", "B"]
    assert h.header_end == len(b'"col\nA","B"\n')


def test_B4_no_terminator_raises():
    with pytest.raises(ValueError, match="未找到记录终止符"):
        locate_header(b"a,b,c")


def test_B5_malformed_header_blocked():
    """B5：畸形表头（引号在非引号字段中间）必须被交叉校验挡住。

    这是唯一会造成真实数据损坏的失效模式：表头边界判错 → 后续文件多跳/少跳
    数据行。奇偶法自己发现不了，靠 csv 模块交叉校验硬挡。
    """
    # 奇偶法把 index 1 的引号当开引号 → index 3 的 \n 被判为"引号内"，
    # 于是首条记录延到 index 7 的 \n；而 csv 模块（宽容解读）认为那一段有 2 条记录。
    with pytest.raises(ValueError, match="表头交叉校验失败"):
        locate_header(b'a"b\nc"d\ne\n')

    # 另一种畸形：单个引号把后面全部吞进"引号内"，一个终止符都找不到
    with pytest.raises(ValueError, match="未找到记录终止符"):
        locate_header(b'ab"cd,e\nf,g\n')


def test_B6_header_only_file():
    """B6：只有表头没有数据行。"""
    h = locate_header(b"a,b\n")
    assert h.fields == ["a", "b"]
    assert h.header_end == 4


def test_B7_crlf_header():
    h = locate_header(b"a,b\r\n1,2\r\n")
    assert h.fields == ["a", "b"]
    assert h.header_end == len(b"a,b\r\n")


# ═════════════════════════════════════════════════════════════════════════════
# C  编码分类与一致性
# ═════════════════════════════════════════════════════════════════════════════

def test_C1_utf8_no_bom():
    ei = classify_encoding("列一,列二\n值,值\n".encode("utf-8"))
    assert (ei.kind, ei.has_bom) == ("utf8", False)


def test_C2_utf8_bom():
    ei = classify_encoding(UTF8_BOM + "列一,列二\n".encode("utf-8"))
    assert (ei.kind, ei.has_bom, ei.bom_len) == ("utf8", True, 3)


def test_C3_pure_ascii():
    ei = classify_encoding(b"a,b\n1,2\n")
    assert ei.kind == "ascii"


def test_C4_gbk_detected_as_other():
    ei = classify_encoding("列一,列二\n值一,值二\n".encode("gbk") * 40)
    assert ei.kind == "other"
    assert ei.has_bom is False


def test_C5_utf16_blocked():
    for enc, label in (("utf-16-le", "LE"), ("utf-16-be", "BE")):
        ei = classify_encoding("a,b\n1,2\n".encode(enc))
        assert ei.kind == "utf16", label
    # 带 BOM 的
    ei = classify_encoding("a,b\n".encode("utf-16"))
    assert ei.kind == "utf16"


def test_C6_utf32_before_utf16():
    """C6：UTF-32LE 的 BOM 前两字节与 UTF-16LE 相同，判定顺序不能错。"""
    ei = classify_encoding("a,b\n".encode("utf-32"))
    assert ei.kind == "utf32"


def test_C7_truncated_multibyte_not_misjudged():
    """C7：头部按字节截断，可能正好切在汉字中间 —— 不能因此误判成非 UTF-8。"""
    data = ("列" * 100).encode("utf-8")
    for cut in (1, 2, 3, 4, 5):
        ei = classify_encoding(data[:-cut] if cut < len(data) else data)
        assert ei.kind == "utf8", f"cut={cut}"


def test_C8_consistency_all_utf8():
    infos = [classify_encoding(UTF8_BOM + "列\n".encode()),
             classify_encoding("列\n".encode())]
    ok, errs, warns, out = check_encoding_consistency(infos, ["a.csv", "b.csv"])
    assert ok and not errs and out == "utf-8"


def test_C9_consistency_ascii_compatible():
    """C9：纯 ASCII 与任何编码兼容，不参与一致性判定。"""
    infos = [classify_encoding(b"a,b\n"), classify_encoding("列\n".encode("gbk") * 60)]
    ok, errs, warns, out = check_encoding_consistency(infos, ["a.csv", "b.csv"])
    assert ok, errs
    assert warns and "非 UTF-8" in warns[0]


def test_C10_consistency_all_gbk_allowed_with_warning():
    """C10：全非 UTF-8 且推测一致 → 放行，但必给 warning（推测不可当真）。"""
    g = "列一,列二\n值一,值二\n".encode("gbk") * 60
    infos = [classify_encoding(g), classify_encoding(g)]
    ok, errs, warns, out = check_encoding_consistency(infos, ["a.csv", "b.csv"])
    assert ok and not errs
    assert warns and "推测" in warns[0]
    assert out != "utf-8"


def test_C11_mixed_utf8_and_gbk_blocked():
    infos = [classify_encoding("列\n".encode("utf-8") * 60),
             classify_encoding("列\n".encode("gbk") * 60)]
    ok, errs, warns, out = check_encoding_consistency(infos, ["u.csv", "g.csv"])
    assert not ok
    assert "编码不一致" in errs[0]
    assert "u.csv" in errs[0] and "g.csv" in errs[0]


def test_C12_utf16_in_batch_blocked():
    infos = [classify_encoding(b"a,b\n"), classify_encoding("a,b\n".encode("utf-16"))]
    ok, errs, warns, out = check_encoding_consistency(infos, ["a.csv", "w.csv"])
    assert not ok
    assert "UTF-16" in errs[0] and "w.csv" in errs[0]


# ═════════════════════════════════════════════════════════════════════════════
# D  结构校验
# ═════════════════════════════════════════════════════════════════════════════

def test_D1_colcount_mismatch_blocked(tmp_path):
    write_file(tmp_path, "a.csv", b"c1,c2,c3\n1,2,3\n")
    write_file(tmp_path, "b.csv", b"c1,c2\n1,2\n")
    probes = [probe_file(str(tmp_path / n)) for n in ("a.csv", "b.csv")]
    r = validate_batch(probes)
    assert not r.ok
    assert "列数" in r.errors[0] and "b.csv（2 列）" in r.errors[0]


def test_D2_header_text_mismatch_blocked_by_default(tmp_path):
    """D2：列数相同、表头文字不同 —— 默认阻断，并指出是第几列。

    这是最危险的静默失效：按位置合并会把 calls 和 cost 混进同一列，
    文件能正常打开、数字看着像数字，事后无法判断哪半个月是对的。
    """
    write_file(tmp_path, "a.csv", b"date,calls,cost\n1,2,3\n")
    write_file(tmp_path, "b.csv", b"date,cost,calls\n1,2,3\n")
    probes = [probe_file(str(tmp_path / n)) for n in ("a.csv", "b.csv")]
    r = validate_batch(probes, strict_header=True)
    assert not r.ok
    assert "第 2 列" in r.errors[0] and "第 3 列" in r.errors[0]


def test_D3_header_mismatch_warning_when_not_strict(tmp_path):
    write_file(tmp_path, "a.csv", b"date,calls\n1,2\n")
    write_file(tmp_path, "b.csv", b"date,COST\n1,2\n")
    probes = [probe_file(str(tmp_path / n)) for n in ("a.csv", "b.csv")]
    r = validate_batch(probes, strict_header=False)
    assert r.ok
    assert any("第 2 列" in w for w in r.warnings)


def test_D4_has_header_false_only_checks_colcount(tmp_path):
    write_file(tmp_path, "a.csv", b"1,2\n3,4\n")
    write_file(tmp_path, "b.csv", b"9,8\n7,6\n")
    probes = [probe_file(str(tmp_path / n)) for n in ("a.csv", "b.csv")]
    r = validate_batch(probes, has_header=False)
    assert r.ok and not r.warnings or r.ok


def test_D5_whitespace_in_header_tolerated(tmp_path):
    write_file(tmp_path, "a.csv", b"date, calls\n1,2\n")
    write_file(tmp_path, "b.csv", b"date,calls \n1,2\n")
    probes = [probe_file(str(tmp_path / n)) for n in ("a.csv", "b.csv")]
    assert validate_batch(probes, strict_header=True).ok


def test_D6_empty_file_blocked(tmp_path):
    write_file(tmp_path, "a.csv", b"h\n1\n")
    write_file(tmp_path, "b.csv", b"")
    probes = [probe_file(str(tmp_path / n)) for n in ("a.csv", "b.csv")]
    r = validate_batch(probes)
    assert not r.ok and "b.csv" in r.errors[0]


def test_D7_no_trailing_lf_warns(tmp_path):
    write_file(tmp_path, "a.csv", b"h\n1\n")
    write_file(tmp_path, "b.csv", b"h\n2")
    probes = [probe_file(str(tmp_path / n)) for n in ("a.csv", "b.csv")]
    r = validate_batch(probes)
    assert r.ok
    assert any("末尾无换行符" in w and "b.csv" in w for w in r.warnings)


def test_D8_output_bom_follows_first_file(tmp_path):
    write_file(tmp_path, "a.csv", UTF8_BOM + b"h\n1\n")
    write_file(tmp_path, "b.csv", b"h\n2\n")
    probes = [probe_file(str(tmp_path / n)) for n in ("a.csv", "b.csv")]
    assert validate_batch(probes).output_bom is True

    write_file(tmp_path, "c.csv", b"h\n1\n")
    probes = [probe_file(str(tmp_path / n)) for n in ("c.csv", "b.csv")]
    assert validate_batch(probes).output_bom is False


# ═════════════════════════════════════════════════════════════════════════════
# E  排序
# ═════════════════════════════════════════════════════════════════════════════

def test_E1_natural_sort_part10():
    names = [f"export_part{i}.csv" for i in (1, 2, 10, 11, 3, 20)]
    files = [{"filename": n} for n in names]
    got = [f["filename"] for f in sort_files(files, "natural")]
    assert got == [f"export_part{i}.csv" for i in (1, 2, 3, 10, 11, 20)]


def test_E2_natural_equals_lexicographic_for_fixed_width_dates():
    """E2：定长日期文件名下，自然排序与字典排序结果必须逐项一致。

    这是"自然排序做默认"这个决定的正当性依据 —— 它对真实场景零风险。
    """
    names = [f"export_2026{m:02d}{d:02d}_to_2026{m:02d}{d:02d}.csv"
             for m in (6, 7, 8) for d in range(1, 29)]
    shuffled = names[:]
    random.Random(3).shuffle(shuffled)
    files = [{"filename": n} for n in shuffled]
    nat = [f["filename"] for f in sort_files(files, "natural")]
    lex = [f["filename"] for f in sort_files(files, "lexicographic")]
    assert nat == lex == sorted(names)


def test_E3_lexicographic_mode_keeps_old_behaviour():
    files = [{"filename": n} for n in ("p1.csv", "p10.csv", "p2.csv")]
    got = [f["filename"] for f in sort_files(files, "lexicographic")]
    assert got == ["p1.csv", "p10.csv", "p2.csv"]


def test_E4_chinese_filenames():
    files = [{"filename": n} for n in ("报表2.csv", "报表10.csv", "报表1.csv")]
    got = [f["filename"] for f in sort_files(files, "natural")]
    assert got == ["报表1.csv", "报表2.csv", "报表10.csv"]


def test_E5_stable_for_equal_natural_keys():
    """E5：'a01' 与 'a1' 自然键相同，次级键保证顺序稳定可复现。"""
    files = [{"filename": n} for n in ("a1.csv", "a01.csv")]
    a = [f["filename"] for f in sort_files(files, "natural")]
    b = [f["filename"] for f in sort_files(list(reversed(files)), "natural")]
    assert a == b


def test_E6_unknown_mode_raises():
    with pytest.raises(ValueError):
        sort_files([{"filename": "a"}], "bogus")


# ═════════════════════════════════════════════════════════════════════════════
# F  合并正确性
# ═════════════════════════════════════════════════════════════════════════════

def test_F1_basic_merge(tmp_path):
    write_file(tmp_path, "a.csv", UTF8_BOM + b"h1,h2\n1,2\n3,4\n")
    write_file(tmp_path, "b.csv", UTF8_BOM + b"h1,h2\n5,6\n")
    write_file(tmp_path, "c.csv", UTF8_BOM + b"h1,h2\n7,8\n9,0\n")
    files = [entry(tmp_path / n) for n in ("a.csv", "b.csv", "c.csv")]
    out = tmp_path / "out.csv"

    r = merge_csv_files(files, str(out), output_bom=True)

    assert r.status == "completed", r.error
    assert r.total_rows == 5
    assert r.done_files == 3
    assert r.last_merged_file == "c.csv"

    data = out.read_bytes()
    assert data.startswith(UTF8_BOM)
    assert data.count(UTF8_BOM) == 1, "BOM 只能有一个，且在文件开头"
    assert data.count(b"h1,h2") == 1, "表头只能保留一份"
    assert csv_records(data) == 6            # 1 表头 + 5 数据行
    assert [e["rows"] for e in r.per_file] == [2, 1, 2]


def test_F2_output_reparses_cleanly(tmp_path):
    """F2：输出用 csv 模块完整重读，记录数与列数全对。"""
    a = UTF8_BOM + b'h1,h2\n"x,y",1\n"p\nq",2\n'
    b = UTF8_BOM + b'h1,h2\n"a""b",3\n'
    write_file(tmp_path, "a.csv", a)
    write_file(tmp_path, "b.csv", b)
    out = tmp_path / "out.csv"
    r = merge_csv_files([entry(tmp_path / "a.csv"), entry(tmp_path / "b.csv")], str(out))
    assert r.status == "completed"

    text = out.read_bytes().decode("utf-8-sig")
    rows = list(csv.reader(io.StringIO(text, newline="")))
    assert rows[0] == ["h1", "h2"]
    assert rows[1] == ["x,y", "1"]
    assert rows[2] == ["p\nq", "2"]
    assert rows[3] == ['a"b', "3"]
    assert len(rows) == 4
    assert r.total_rows == 3


def test_F3_missing_trailing_newline_no_glue(tmp_path):
    """F3：源文件缺尾换行 —— 必须补齐，否则上一文件末行与下一文件首行粘成一行。"""
    write_file(tmp_path, "a.csv", b"h1,h2\n1,2")          # 无尾换行
    write_file(tmp_path, "b.csv", b"h1,h2\n3,4\n")
    out = tmp_path / "out.csv"
    r = merge_csv_files([entry(tmp_path / "a.csv"), entry(tmp_path / "b.csv")], str(out),
                        output_bom=False)
    assert r.status == "completed"
    rows = list(csv.reader(io.StringIO(out.read_text(encoding="utf-8"), newline="")))
    assert rows == [["h1", "h2"], ["1", "2"], ["3", "4"]]
    assert r.total_rows == 2
    assert any("末尾无换行符" in w for w in r.warnings)


def test_F4_empty_and_header_only_files(tmp_path):
    write_file(tmp_path, "a.csv", b"h\n1\n")
    write_file(tmp_path, "b.csv", b"h\n")                 # 只有表头
    write_file(tmp_path, "c.csv", b"h\n2\n")
    files = [entry(tmp_path / n) for n in ("a.csv", "b.csv", "c.csv")]
    out = tmp_path / "out.csv"
    r = merge_csv_files(files, str(out), output_bom=False)
    assert r.status == "completed"
    assert [e["rows"] for e in r.per_file] == [1, 0, 1]
    assert r.total_rows == 2
    assert any("只有表头" in w for w in r.warnings)


def test_F5_no_header_mode(tmp_path):
    write_file(tmp_path, "a.csv", b"1,2\n3,4\n")
    write_file(tmp_path, "b.csv", b"5,6\n")
    files = [entry(tmp_path / n) for n in ("a.csv", "b.csv")]
    out = tmp_path / "out.csv"
    r = merge_csv_files(files, str(out), has_header=False, output_bom=False)
    assert r.status == "completed"
    assert r.total_rows == 3                              # 一行都不跳
    assert out.read_bytes() == b"1,2\n3,4\n5,6\n"


def test_F6_single_file_degenerates_to_copy(tmp_path):
    src = UTF8_BOM + b"h\n1\n2\n"
    write_file(tmp_path, "a.csv", src)
    out = tmp_path / "out.csv"
    r = merge_csv_files([entry(tmp_path / "a.csv")], str(out), output_bom=True)
    assert r.status == "completed"
    assert out.read_bytes() == src
    assert r.total_rows == 2


def test_F7_mixed_bom_presence(tmp_path):
    """F7：部分文件带 BOM 部分不带 —— 每个源文件的 BOM 都要剥离，输出只写一次。"""
    write_file(tmp_path, "a.csv", b"h\n1\n")              # 无 BOM
    write_file(tmp_path, "b.csv", UTF8_BOM + b"h\n2\n")   # 有 BOM
    files = [entry(tmp_path / n) for n in ("a.csv", "b.csv")]
    out = tmp_path / "out.csv"
    r = merge_csv_files(files, str(out), output_bom=False)
    assert r.status == "completed"
    data = out.read_bytes()
    assert UTF8_BOM not in data, "后续文件的 BOM 必须剥离，否则会出现在数据行中间"
    assert data == b"h\n1\n2\n"


def test_F8_quoted_newline_across_chunk_boundary(tmp_path):
    """F8：字段内换行横跨 chunk 边界，行数仍必须精确。"""
    rows = b"".join(b'"a\nb",%d\n' % i for i in range(500))
    write_file(tmp_path, "a.csv", b"h1,h2\n" + rows)
    write_file(tmp_path, "b.csv", b"h1,h2\n" + rows)
    files = [entry(tmp_path / n) for n in ("a.csv", "b.csv")]
    out = tmp_path / "out.csv"
    for chunk in (7, 64, 997, 8192):
        r = merge_csv_files(files, str(out), output_bom=False, chunk_size=chunk)
        assert r.status == "completed", f"chunk={chunk}: {r.error}"
        assert r.total_rows == 1000, f"chunk={chunk}"
        assert csv_records(out.read_bytes()) == 1001, f"chunk={chunk}"


def test_F9_anchored_size_ignores_appended_bytes(tmp_path):
    """F9：锚定 size —— 校验后文件又被追加，只读锚定的字节数。

    这是防"勾中仍在写入的文件"的第三层：即使 export_jobs 反查漏了、
    mtime 也没拦住，锚定 size 保证不会读进新写入的半截数据。
    """
    p = write_file(tmp_path, "a.csv", b"h\n1\n2\n")
    e = entry(p)                                          # size 锚定在此刻
    with p.open("ab") as fp:
        fp.write(b'3,"unterminated')                      # 模拟仍在写入的残缺尾部
    out = tmp_path / "out.csv"
    r = merge_csv_files([e], str(out), output_bom=False)
    assert r.status == "completed", r.error
    assert out.read_bytes() == b"h\n1\n2\n"
    assert r.total_rows == 2


def test_F9b_physical_minus_rows_is_exactly_embedded_newlines(tmp_path):
    """F9b：`physical_lines - rows` 必须**恰好**等于字段内换行条数（回归）。

    早先的实现把首文件的表头也喂给扫描器、事后 records - 1，于是 physical_lines
    永远比 rows 多算一行表头 → 这个诊断值恒定虚高 1，是个假阳性。在 4.57 GB
    真实数据上表现为「1 处字段内换行」，而实际是 0 处。

    现在的实现是「表头只写不扫」，因此对**完全没有**字段内换行的数据，差必须是 0。
    """
    # 干净数据：零字段内换行
    write_file(tmp_path, "a.csv", UTF8_BOM + b"h1,h2\n1,2\n3,4\n")
    write_file(tmp_path, "b.csv", UTF8_BOM + b"h1,h2\n5,6\n")
    files = [entry(tmp_path / n) for n in ("a.csv", "b.csv")]
    r = merge_csv_files(files, str(tmp_path / "o1.csv"), output_bom=True)
    assert r.status == "completed", r.error
    assert r.total_rows == 3
    assert r.total_physical_lines - r.total_rows == 0, "干净数据不该报出字段内换行"
    assert all(e["physical_lines"] - e["rows"] == 0 for e in r.per_file)

    # 首文件的数据段含 2 处字段内换行，第二个文件含 1 处
    write_file(tmp_path, "c.csv", UTF8_BOM + b'h1,h2\n"x\ny",1\n"p\nq",2\n')
    write_file(tmp_path, "d.csv", UTF8_BOM + b'h1,h2\n"m\nn",3\n')
    files = [entry(tmp_path / n) for n in ("c.csv", "d.csv")]
    r = merge_csv_files(files, str(tmp_path / "o2.csv"), output_bom=True)
    assert r.status == "completed", r.error
    assert r.total_rows == 3
    assert r.total_physical_lines - r.total_rows == 3, "3 条记录各含 1 处字段内换行"
    assert [e["physical_lines"] - e["rows"] for e in r.per_file] == [2, 1]


def test_F9c_multiline_header_not_counted_as_data(tmp_path):
    """F9c：表头自身含引号内换行时，也不能被算进 rows / physical_lines。

    「表头只写不扫」对这种情况天然精确；旧的「减 1」写法在这里会少减。
    """
    hdr = b'"col\nA","B"\n'
    write_file(tmp_path, "a.csv", hdr + b"1,2\n3,4\n")
    write_file(tmp_path, "b.csv", hdr + b"5,6\n")
    files = [entry(tmp_path / n) for n in ("a.csv", "b.csv")]
    out = tmp_path / "out.csv"
    r = merge_csv_files(files, str(out), output_bom=False)
    assert r.status == "completed", r.error
    assert r.total_rows == 3
    assert r.total_physical_lines - r.total_rows == 0, "表头的换行不属于数据"
    # 输出里表头仍然完整保留一份
    rows = list(csv.reader(io.StringIO(out.read_text(encoding="utf-8"), newline="")))
    assert rows[0] == ["col\nA", "B"]
    assert len(rows) == 4


def test_F10_progress_callback_monotonic(tmp_path):
    write_file(tmp_path, "a.csv", b"h\n" + b"".join(b"%d\n" % i for i in range(2000)))
    write_file(tmp_path, "b.csv", b"h\n" + b"".join(b"%d\n" % i for i in range(1000)))
    files = [entry(tmp_path / n) for n in ("a.csv", "b.csv")]
    seen = []
    merge_csv_files(files, str(tmp_path / "out.csv"), output_bom=False,
                    chunk_size=64, on_progress=lambda b, f, r, p: seen.append((b, f, r)))
    assert seen, "on_progress 至少应被调用一次"
    assert [s[0] for s in seen] == sorted(s[0] for s in seen), "done_bytes 必须单调不减"
    assert [s[2] for s in seen] == sorted(s[2] for s in seen), "total_rows 必须单调不减"


# ═════════════════════════════════════════════════════════════════════════════
# G  取消语义
# ═════════════════════════════════════════════════════════════════════════════

def _make_three(tmp_path):
    write_file(tmp_path, "a.csv", b"h\n" + b"".join(b"a%d\n" % i for i in range(300)))
    write_file(tmp_path, "b.csv", b"h\n" + b"".join(b"b%d\n" % i for i in range(300)))
    write_file(tmp_path, "c.csv", b"h\n" + b"".join(b"c%d\n" % i for i in range(300)))
    return [entry(tmp_path / n) for n in ("a.csv", "b.csv", "c.csv")]


def test_G1_cancel_rolls_back_to_file_boundary(tmp_path):
    """G1：取消回退到**上一个文件边界**，结果文件仍是有效 CSV。"""
    files = _make_three(tmp_path)
    out = tmp_path / "out.csv"
    # 用 on_file_done 触发，而不是数 chunk 次数 —— 后者依赖 chunk 数，脆弱
    st = {"done": 0}

    r = merge_csv_files(
        files, str(out), output_bom=False, chunk_size=64,
        on_file_done=lambda i, e: st.__setitem__("done", st["done"] + 1),
        should_cancel=lambda: st["done"] >= 1,   # a.csv 拷完后，在 b.csv 中途取消
    )

    assert r.status == "cancelled"
    data = out.read_text(encoding="utf-8")
    rows = list(csv.reader(io.StringIO(data, newline="")))
    # 结果必须在完整文件边界截断：只含 a.csv 的完整内容
    assert rows[0] == ["h"]
    assert all(c[0].startswith("a") for c in rows[1:]), "不能出现来自 b.csv 的半截数据"
    assert len(rows) == 301
    assert r.done_files == 1
    assert r.last_merged_file == "a.csv", "必须告知成功合并到哪个文件"
    assert r.total_rows == 300


def test_G2_cancel_before_first_file(tmp_path):
    files = _make_three(tmp_path)
    out = tmp_path / "out.csv"
    r = merge_csv_files(files, str(out), output_bom=True, chunk_size=64,
                        should_cancel=lambda: True)
    assert r.status == "cancelled"
    assert r.done_files == 0
    assert r.last_merged_file is None
    assert out.read_bytes() == UTF8_BOM, "只剩已写入的 BOM"


def test_G3_cancel_result_is_valid_csv_with_quotes(tmp_path):
    """G3：源文件含引号内换行时，取消后的结果仍必须是有效 CSV。"""
    body = b"".join(b'"x\ny",%d\n' % i for i in range(200))
    write_file(tmp_path, "a.csv", b"h1,h2\n" + body)
    write_file(tmp_path, "b.csv", b"h1,h2\n" + body)
    files = [entry(tmp_path / n) for n in ("a.csv", "b.csv")]
    out = tmp_path / "out.csv"
    st = {"done": 0}
    r = merge_csv_files(
        files, str(out), output_bom=False, chunk_size=32,
        on_file_done=lambda i, e: st.__setitem__("done", st["done"] + 1),
        should_cancel=lambda: st["done"] >= 1,
    )
    assert r.status == "cancelled"
    rows = list(csv.reader(io.StringIO(out.read_text(encoding="utf-8"), newline="")))
    assert len(rows) == 201                # 表头 + a.csv 的 200 行
    assert all(len(x) == 2 for x in rows)  # 没有半截记录


def test_G4_no_cancel_completes(tmp_path):
    files = _make_three(tmp_path)
    r = merge_csv_files(files, str(tmp_path / "out.csv"), output_bom=False,
                        chunk_size=64, should_cancel=lambda: False)
    assert r.status == "completed"
    assert r.done_files == 3 and r.total_rows == 900


# ═════════════════════════════════════════════════════════════════════════════
# H  失败与清理
# ═════════════════════════════════════════════════════════════════════════════

def test_H1_unclosed_quote_fails_and_rolls_back(tmp_path):
    """H1：引号未闭合（截断 / 仍在写入）→ 失败，且回退到该文件开始前。"""
    write_file(tmp_path, "a.csv", b"h1,h2\n1,2\n3,4\n")
    write_file(tmp_path, "b.csv", b'h1,h2\n5,"unterminated\n')
    files = [entry(tmp_path / n) for n in ("a.csv", "b.csv")]
    out = tmp_path / "out.csv"
    r = merge_csv_files(files, str(out), output_bom=False)

    assert r.status == "failed"
    assert "引号未闭合" in r.error and "b.csv" in r.error
    # 回退后结果只含 a.csv，仍是有效 CSV
    assert out.read_bytes() == b"h1,h2\n1,2\n3,4\n"
    assert r.done_files == 1 and r.last_merged_file == "a.csv"


def test_H2_source_shorter_than_anchored_size(tmp_path):
    """H2：源文件在校验后被截短（锚定 size 大于实际）→ 提前 EOF，失败并回退。"""
    write_file(tmp_path, "a.csv", b"h\n1\n")
    p = write_file(tmp_path, "b.csv", b"h\n" + b"x\n" * 1000)
    files = [entry(tmp_path / "a.csv"), entry(p)]
    p.write_bytes(b"h\n2\n")                              # 被替换成更短的文件
    out = tmp_path / "out.csv"
    r = merge_csv_files(files, str(out), output_bom=False)
    assert r.status == "failed"
    assert "提前 EOF" in r.error
    assert out.read_bytes() == b"h\n1\n"


def test_H3_write_error_rolls_back(tmp_path, monkeypatch):
    """H3：写入中途 IOError（模拟磁盘满）→ 失败并回退到上一文件边界。"""
    write_file(tmp_path, "a.csv", b"h\n1\n")
    write_file(tmp_path, "b.csv", b"h\n" + b"y\n" * 500)
    files = [entry(tmp_path / n) for n in ("a.csv", "b.csv")]
    out = tmp_path / "out.csv"

    real_open = open
    state = {"n": 0}

    class FailingWriter:
        def __init__(self, fp):
            self._fp = fp

        def write(self, b):
            state["n"] += 1
            if state["n"] > 3:
                raise OSError(28, "No space left on device")
            return self._fp.write(b)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return self._fp.__exit__(*exc)

        def __getattr__(self, k):
            return getattr(self._fp, k)

    def fake_open(path, mode="r", *a, **kw):
        fp = real_open(path, mode, *a, **kw)
        if "w" in mode and str(path) == str(out):
            return FailingWriter(fp)
        return fp

    monkeypatch.setattr("builtins.open", fake_open)
    r = merge_csv_files(files, str(out), output_bom=False, chunk_size=32)
    monkeypatch.undo()

    assert r.status == "failed"
    assert "No space left" in r.error
    # 回退后不应留下 b.csv 的半截数据
    text = out.read_text(encoding="utf-8")
    assert "y" not in text


def test_H4_missing_source_file_fails(tmp_path):
    write_file(tmp_path, "a.csv", b"h\n1\n")
    p = write_file(tmp_path, "b.csv", b"h\n2\n")
    files = [entry(tmp_path / "a.csv"), entry(p)]
    p.unlink()
    r = merge_csv_files(files, str(tmp_path / "out.csv"), output_bom=False)
    assert r.status == "failed"
    assert r.done_files == 1


# ═════════════════════════════════════════════════════════════════════════════
# I  边界与一致性
# ═════════════════════════════════════════════════════════════════════════════

def test_I1_row_count_equals_csv_module_end_to_end(tmp_path):
    """I1：端到端 —— 合并结果的行数必须与 csv 模块重读一致（多种恶意内容混合）。"""
    payloads = [
        b'"a,b",1\n"c\nd",2\n',
        b'"e""f",3\nplain,4\n',
        b'"g\r\nh",5\n',
        b'"",6\n,,\n' if False else b'"",6\n',
        b"i,7\n",
    ]
    files = []
    for i, body in enumerate(payloads):
        files.append(entry(write_file(tmp_path, f"f{i}.csv", b"h1,h2\n" + body)))
    out = tmp_path / "out.csv"
    for chunk in (1, 3, 17, 4096):
        r = merge_csv_files(files, str(out), output_bom=False, chunk_size=chunk)
        assert r.status == "completed", f"chunk={chunk}: {r.error}"
        assert csv_records(out.read_bytes()) == r.total_rows + 1, f"chunk={chunk}"
        assert r.total_rows == sum(e["rows"] for e in r.per_file), f"chunk={chunk}"


def test_I2_validate_then_merge_pipeline(tmp_path):
    """I2：probe → validate → sort → merge 全链路串起来跑一遍。"""
    for i in (2, 10, 1):
        write_file(tmp_path, f"part{i}.csv",
                   UTF8_BOM + b"h1,h2\n" + b"".join(b"%d,%d\n" % (i, j) for j in range(5)))
    raw = [{"filename": p.name, "file_path": str(p)} for p in tmp_path.glob("*.csv")]
    ordered = sort_files(raw, "natural")
    assert [f["filename"] for f in ordered] == ["part1.csv", "part2.csv", "part10.csv"]

    probes = [probe_file(f["file_path"]) for f in ordered]
    v = validate_batch(probes, has_header=True, strict_header=True)
    assert v.ok, v.errors
    assert v.col_count == 2 and v.output_bom is True

    files = [entry(Path(f["file_path"])) for f in ordered]
    out = tmp_path / "out.csv"
    r = merge_csv_files(files, str(out), has_header=True, output_bom=v.output_bom)
    assert r.status == "completed" and r.total_rows == 15

    text = out.read_bytes().decode("utf-8-sig")
    rows = list(csv.reader(io.StringIO(text, newline="")))
    assert rows[0] == ["h1", "h2"]
    # 顺序必须是 part1 → part2 → part10
    assert [r0[0] for r0 in rows[1::5]] == ["1", "2", "10"]


def test_I3_large_synthetic_throughput(tmp_path):
    """I3：~20 MB 合成数据，验证行数精确且 per_file 加总一致。"""
    row = b'"abc,def","g""h",12345,2026-08-24 10:00:00\n'
    per = 60_000
    for i in range(4):
        write_file(tmp_path, f"big{i}.csv", b"c1,c2,c3,c4\n" + row * per)
    files = [entry(tmp_path / f"big{i}.csv") for i in range(4)]
    out = tmp_path / "out.csv"
    r = merge_csv_files(files, str(out), output_bom=True)
    assert r.status == "completed", r.error
    assert r.total_rows == per * 4
    assert [e["rows"] for e in r.per_file] == [per] * 4
    assert out.read_bytes().count(b"c1,c2,c3,c4") == 1
    assert csv_records(out.read_bytes()) == per * 4 + 1


# ═════════════════════════════════════════════════════════════════════════════
# J  service 层：路径边界、目录列举、export_jobs 反查、磁盘预检
# ═════════════════════════════════════════════════════════════════════════════

from backend.services import csv_merge_service as svc   # noqa: E402


class _FakeExportJob:
    """模拟 ExportJob。字段名照实际 model：产物清单是 `output_files`，不是 chunk_files。"""

    def __init__(self, jid, username, status, output_files=None, file_path=None,
                 output_filename=None, file_size=None, job_name=None, total_rows=None):
        self.id = jid
        self.username = username
        self.status = status
        self.output_files = output_files or []
        self.file_path = file_path
        self.output_filename = output_filename
        self.file_size = file_size
        self.job_name = job_name
        self.total_rows = total_rows
        self.created_at = None


def test_J1_resolve_user_path_blocks_traversal(tmp_path, monkeypatch):
    """J1：`..` 穿越、指向别的用户目录 —— 必须被拦掉。"""
    root = tmp_path / "customer_data"
    (root / "alice").mkdir(parents=True)
    (root / "bob").mkdir(parents=True)
    (root / "bob" / "secret.csv").write_bytes(b"h\n1\n")
    monkeypatch.setattr(svc, "customer_data_root", lambda: root)

    ok = svc.resolve_user_path("alice", str(root / "alice"))
    assert ok == (root / "alice").resolve()

    for bad in ("../bob/secret.csv", str(root / "bob" / "secret.csv"), "../../etc/passwd"):
        with pytest.raises(PermissionError):
            svc.resolve_user_path("alice", bad)


def test_J2_list_csv_dirs_and_files(tmp_path, monkeypatch):
    """J2：目录列举只统计 .csv；.zip 等一律忽略。"""
    root = tmp_path / "cd"
    d1 = root / "u" / "exports" / "job1"
    d1.mkdir(parents=True)
    (d1 / "a.csv").write_bytes(b"h\n1\n")
    (d1 / "b.csv").write_bytes(b"h\n2\n")
    (d1 / "packed.zip").write_bytes(b"PK\x03\x04")
    monkeypatch.setattr(svc, "customer_data_root", lambda: root)

    dirs = svc.list_csv_dirs("u")
    assert len(dirs) == 1
    assert dirs[0]["csv_files"] == 2, "zip 不能被算进 CSV 数量"

    files = svc.list_csv_files("u", str(d1))
    assert [f["filename"] for f in files] == ["a.csv", "b.csv"]
    assert all(f["origin"] == "server" for f in files)

    assert [f["filename"] for f in svc.list_csv_files("u", str(d1), "a*")] == ["a.csv"]


def test_J3_list_files_rejects_outside_dir(tmp_path, monkeypatch):
    root = tmp_path / "cd"
    (root / "u").mkdir(parents=True)
    (root / "other").mkdir(parents=True)
    monkeypatch.setattr(svc, "customer_data_root", lambda: root)
    with pytest.raises(PermissionError):
        svc.list_csv_files("u", str(root / "other"))


def test_J4_iter_export_job_files_uses_output_files(tmp_path):
    """J4：从 export_job 取产物用的是 `output_files`，且带 expected_rows。"""
    p = tmp_path / "export_20260601_to_20260601.csv"
    p.write_bytes(b"h\n1\n")
    job = _FakeExportJob(
        "jid-1", "u", "completed",
        output_files=[{
            "index": 0, "filename": p.name, "file_path": str(p),
            "file_size": 4, "rows": 132102, "status": "completed",
        }],
        # date_chunked 模式下 job.file_path 是最终打包的 zip，不该被当成 CSV 产物
        file_path=str(tmp_path / "export_20260824.zip"),
    )
    out = svc._iter_export_job_files(job)
    assert len(out) == 1
    assert out[0]["expected_rows"] == 132102
    assert out[0]["file_path"] == str(p)
    assert out[0]["origin"] == "server"


def test_J5_active_export_blocks_preview(tmp_path, monkeypatch):
    """J5（V7 主防线）：文件属于**仍在进行中**的导出任务 → 预检阻断。"""
    root = tmp_path / "cd"
    d = root / "u" / "exports" / "j"
    d.mkdir(parents=True)
    f1 = d / "x_20260601.csv"
    f1.write_bytes(b"h1,h2\n1,2\n")
    monkeypatch.setattr(svc, "customer_data_root", lambda: root)

    running = _FakeExportJob("jid-run", "u", "running", output_files=[
        {"filename": f1.name, "file_path": str(f1), "file_size": f1.stat().st_size,
         "rows": None, "status": "running"},
    ])

    class FakeQuery:
        def __init__(self, rows): self._rows = rows
        def filter(self, *a, **k): return self
        def all(self): return self._rows
    class FakeDB:
        def query(self, *a, **k): return FakeQuery([running])

    svc._EXPORT_PATH_CACHE.clear()
    pv = svc.run_preview(FakeDB(), "u", [
        {"filename": f1.name, "file_path": str(f1), "size": f1.stat().st_size, "origin": "server"},
    ])
    assert pv["ok"] is False
    assert any("仍在进行中" in e for e in pv["errors"])


def test_J6_completed_export_passes(tmp_path, monkeypatch):
    """J6：已完成的导出**零误伤** —— 状态是 completed，不会被 V7 命中。"""
    root = tmp_path / "cd"
    d = root / "u" / "exports" / "j"
    d.mkdir(parents=True)
    files = []
    for i in (1, 2):
        p = d / f"x_2026060{i}.csv"
        p.write_bytes(b"h1,h2\n%d,%d\n" % (i, i))
        files.append(p)
    monkeypatch.setattr(svc, "customer_data_root", lambda: root)

    done = _FakeExportJob("jid-done", "u", "completed", output_files=[
        {"filename": p.name, "file_path": str(p), "file_size": p.stat().st_size,
         "rows": 1, "status": "completed"} for p in files
    ])

    class FakeQuery:
        def __init__(self, rows): self._rows = rows
        def filter(self, *a, **k): return self
        def all(self): return self._rows
    class FakeDB:
        def query(self, *a, **k): return FakeQuery([done])

    svc._EXPORT_PATH_CACHE.clear()
    pv = svc.run_preview(FakeDB(), "u", [
        {"filename": p.name, "file_path": str(p), "size": p.stat().st_size, "origin": "server"}
        for p in files
    ])
    assert pv["ok"] is True, pv["errors"]
    assert pv["expected_total_rows"] == 2, "对账基线应从 output_files[].rows 取到"
    assert pv["col_count"] == 2
    assert pv["estimated_seconds"] is not None


def test_J7_disk_precheck_blocks(tmp_path, monkeypatch):
    """J7（V9）：磁盘剩余不足 → 阻断，且错误里要有具体数字。"""
    root = tmp_path / "cd"
    d = root / "u" / "x"
    d.mkdir(parents=True)
    p = d / "a.csv"
    p.write_bytes(b"h\n" + b"1\n" * 5000)
    monkeypatch.setattr(svc, "customer_data_root", lambda: root)

    class FakeUsage:
        free = 10          # 10 字节，肯定不够
    monkeypatch.setattr(svc.shutil, "disk_usage", lambda _p: FakeUsage())

    class FakeQuery:
        def filter(self, *a, **k): return self
        def all(self): return []
    class FakeDB:
        def query(self, *a, **k): return FakeQuery()

    svc._EXPORT_PATH_CACHE.clear()
    pv = svc.run_preview(FakeDB(), "u", [
        {"filename": "a.csv", "file_path": str(p), "size": p.stat().st_size, "origin": "server"},
    ])
    assert pv["ok"] is False
    assert any("磁盘空间不足" in e and "需要约" in e for e in pv["errors"])


def test_J8_preview_reports_sorted_order(tmp_path, monkeypatch):
    """J8：预检返回的 sorted_files 必须是**自然排序**后的合并顺序。"""
    root = tmp_path / "cd"
    d = root / "u" / "x"
    d.mkdir(parents=True)
    paths = []
    for i in (2, 10, 1):
        p = d / f"part{i}.csv"
        p.write_bytes(b"h1,h2\n%d,0\n" % i)
        paths.append(p)
    monkeypatch.setattr(svc, "customer_data_root", lambda: root)

    class FakeQuery:
        def filter(self, *a, **k): return self
        def all(self): return []
    class FakeDB:
        def query(self, *a, **k): return FakeQuery()

    svc._EXPORT_PATH_CACHE.clear()
    pv = svc.run_preview(FakeDB(), "u", [
        {"filename": p.name, "file_path": str(p), "size": p.stat().st_size, "origin": "server"}
        for p in paths
    ])
    assert pv["ok"] is True, pv["errors"]
    assert [f["filename"] for f in pv["sorted_files"]] == ["part1.csv", "part2.csv", "part10.csv"]
    # 预检要把锚定 size / 编码 / 表头边界回填进清单，供后续拼接直接用
    assert all(f["header_end"] > 0 for f in pv["sorted_files"])
    assert all(f["size"] > 0 for f in pv["sorted_files"])


# ═════════════════════════════════════════════════════════════════════════════
# K  REST API 端点
# ═════════════════════════════════════════════════════════════════════════════

class TestMergeCsvAPI:
    @pytest.fixture
    def client(self):
        os.environ["ENABLE_AUTH"] = "False"
        from fastapi.testclient import TestClient
        sys.path.insert(0, str(Path(__file__).parent / "backend"))
        from main import app
        with TestClient(app) as c:
            yield c

    def test_K1_upload_rejects_non_csv(self, client):
        """K1：非 .csv → 400"""
        r = client.post(
            "/api/v1/tools/merge-csv/upload",
            files={"file": ("a.xlsx", b"PK\x03\x04", "application/octet-stream")},
        )
        assert r.status_code == 400

    def test_K2_upload_accepts_csv(self, client):
        """K2：合法 CSV 上传成功，返回 upload_id"""
        r = client.post(
            "/api/v1/tools/merge-csv/upload",
            files={"file": ("up.csv", b"h1,h2\n1,2\n", "text/csv")},
        )
        assert r.status_code == 200
        d = r.json()["data"]
        assert d["upload_id"] and d["filename"] == "up.csv"
        Path(d["file_path"]).unlink(missing_ok=True)

    def test_K3_execute_unknown_upload_id_404(self, client):
        r = client.post("/api/v1/tools/merge-csv/execute", json={
            "upload_ids": [str(uuid.uuid4())], "has_header": True,
            "strict_header": True, "sort_mode": "natural",
        })
        assert r.status_code == 404

    def test_K4_execute_no_sources_400(self, client):
        r = client.post("/api/v1/tools/merge-csv/execute", json={
            "has_header": True, "strict_header": True, "sort_mode": "natural",
        })
        assert r.status_code == 400
        assert "未提供任何待合并文件" in r.json()["detail"]

    def test_K5_bad_sort_mode_400(self, client):
        r = client.post("/api/v1/tools/merge-csv/preview", json={
            "upload_ids": [], "server_paths": [], "sort_mode": "bogus",
            "has_header": True, "strict_header": True,
        })
        assert r.status_code == 400

    def test_K6_files_path_traversal_403(self, client):
        r = client.get("/api/v1/tools/merge-csv/files",
                       params={"dir_path": "../../../etc"})
        assert r.status_code == 403

    def test_K7_dirs_and_export_jobs_ok(self, client):
        assert client.get("/api/v1/tools/merge-csv/dirs").status_code == 200
        r = client.get("/api/v1/tools/merge-csv/export-jobs")
        assert r.status_code == 200
        assert "items" in r.json()["data"]

    def test_K8_job_not_found_404(self, client):
        jid = str(uuid.uuid4())
        assert client.get(f"/api/v1/tools/merge-csv/jobs/{jid}").status_code == 404
        assert client.post(f"/api/v1/tools/merge-csv/jobs/{jid}/cancel").status_code == 404
        assert client.delete(f"/api/v1/tools/merge-csv/jobs/{jid}").status_code == 404
        assert client.get(f"/api/v1/tools/merge-csv/jobs/{jid}/download").status_code == 404

    def test_K9_jobs_list_shape(self, client):
        r = client.get("/api/v1/tools/merge-csv/jobs", params={"page": 1, "page_size": 5})
        assert r.status_code == 200
        d = r.json()["data"]
        assert set(d) >= {"total", "page", "page_size", "items"}

    def test_K10_preview_then_execute_end_to_end(self, client):
        """K10：上传两个文件 → 预检 → 提交 → 轮询到完成 → 结果可下载。"""
        import time

        ids = []
        for i in (1, 2):
            r = client.post(
                "/api/v1/tools/merge-csv/upload",
                files={"file": (f"{_PREFIX}p{i}.csv",
                                b"h1,h2\n" + b"".join(b"%d,%d\n" % (i, j) for j in range(3)),
                                "text/csv")},
            )
            assert r.status_code == 200
            ids.append(r.json()["data"]["upload_id"])

        body = {
            "upload_ids": ids, "has_header": True, "strict_header": True,
            "sort_mode": "natural", "job_name": f"{_PREFIX}job",
        }

        pv = client.post("/api/v1/tools/merge-csv/preview", json=body)
        assert pv.status_code == 200
        pvd = pv.json()["data"]
        assert pvd["ok"] is True, pvd["errors"]
        assert pvd["col_count"] == 2
        assert len(pvd["sorted_files"]) == 2

        ex = client.post("/api/v1/tools/merge-csv/execute", json=body)
        assert ex.status_code == 200
        job_id = ex.json()["data"]["job_id"]

        job = None
        for _ in range(60):
            job = client.get(f"/api/v1/tools/merge-csv/jobs/{job_id}").json()["data"]
            if job["status"] in ("completed", "failed", "cancelled"):
                break
            time.sleep(0.25)

        assert job["status"] == "completed", job.get("error_message")
        assert job["total_rows"] == 6, "两个文件各 3 行"
        assert job["done_files"] == 2
        assert job["last_merged_file"].endswith("p2.csv")
        assert job["file_path"] and Path(job["file_path"]).exists()
        assert job["reconcile_status"] == "unavailable", "上传来源没有导出侧行数，无法对账"
        # 每个源文件的贡献行数都要回填
        assert [f["rows"] for f in job["source_files"]] == [3, 3]

        dl = client.get(f"/api/v1/tools/merge-csv/jobs/{job_id}/download")
        assert dl.status_code == 200
        text = dl.content.decode("utf-8-sig")
        rows = list(csv.reader(io.StringIO(text, newline="")))
        assert rows[0] == ["h1", "h2"]
        assert len(rows) == 7, "1 表头 + 6 数据行"

        assert client.delete(f"/api/v1/tools/merge-csv/jobs/{job_id}").status_code == 200
        assert not Path(job["file_path"]).exists(), "删除 job 应删掉结果文件"

    def test_K10b_upload_preserves_original_filename_for_sorting(self, client):
        """K10b：上传路径的排序必须按**原始文件名**，不是上传顺序、更不是 UUID。

        回归用例。上传文件曾经被存成 `{upload_id}.csv`，而排序是按 filename 做的
        —— 那等于让上传路径变成「按 UUID 随机排序」，直接违背「按文件名排序依次
        合并」这个核心需求，而且不报任何错。
        """
        # 故意**倒序**上传：先 part10，再 part2，最后 part1
        ids = []
        for i in (10, 2, 1):
            r = client.post(
                "/api/v1/tools/merge-csv/upload",
                files={"file": (f"{_PREFIX}part{i}.csv", b"h1,h2\n%d,0\n" % i, "text/csv")},
            )
            assert r.status_code == 200
            d = r.json()["data"]
            assert d["filename"] == f"{_PREFIX}part{i}.csv"
            ids.append(d["upload_id"])

        pv = client.post("/api/v1/tools/merge-csv/preview", json={
            "upload_ids": ids, "has_header": True, "strict_header": True,
            "sort_mode": "natural",
        })
        assert pv.status_code == 200
        d = pv.json()["data"]
        assert d["ok"] is True, d["errors"]
        names = [f["filename"] for f in d["sorted_files"]]
        assert names == [f"{_PREFIX}part{i}.csv" for i in (1, 2, 10)], (
            f"必须按原始文件名自然排序，实际: {names}"
        )
        assert not any(n.count("-") == 4 for n in names), "文件名不该是 UUID"

    def test_K10c_upload_rejects_path_traversal_in_filename(self, client):
        """K10c：客户端给的文件名含 `../` → 必须被剥成裸文件名，不能写到目录外。"""
        r = client.post(
            "/api/v1/tools/merge-csv/upload",
            files={"file": ("../../evil.csv", b"h\n1\n", "text/csv")},
        )
        assert r.status_code == 200
        d = r.json()["data"]
        p = Path(d["file_path"])
        assert p.name == "evil.csv"
        assert "tools" in p.parts and "merge_csv" in p.parts, f"落盘位置越界: {p}"
        try:
            p.unlink(missing_ok=True)
            p.parent.rmdir()
        except OSError:
            pass

    def test_K10d_bad_upload_id_400(self, client):
        """K10d：upload_id 会被拼进文件系统路径 —— 非 UUID 一律 400。"""
        r = client.post("/api/v1/tools/merge-csv/preview", json={
            "upload_ids": ["../../../etc"], "has_header": True,
            "strict_header": True, "sort_mode": "natural",
        })
        assert r.status_code == 400
        assert "非法 upload_id" in r.json()["detail"]

    def test_K11_execute_blocks_mismatched_header(self, client):
        """K11：表头文字不一致 → execute 直接 400，不建注定失败的 job。"""
        ids = []
        for body in (b"date,calls\n1,2\n", b"date,cost\n1,2\n"):
            r = client.post("/api/v1/tools/merge-csv/upload",
                            files={"file": (f"{_PREFIX}h{len(ids)}.csv", body, "text/csv")})
            ids.append(r.json()["data"]["upload_id"])
        r = client.post("/api/v1/tools/merge-csv/execute", json={
            "upload_ids": ids, "has_header": True, "strict_header": True,
            "sort_mode": "natural",
        })
        assert r.status_code == 400
        assert "第 2 列" in r.json()["detail"]

        # strict_header=false → 放行
        r2 = client.post("/api/v1/tools/merge-csv/preview", json={
            "upload_ids": ids, "has_header": True, "strict_header": False,
            "sort_mode": "natural",
        })
        assert r2.status_code == 200 and r2.json()["data"]["ok"] is True

    def test_K12_cancel_pending_only_when_active(self, client):
        """K12：终态任务不可取消 → 400。"""
        ids = []
        for i in (1, 2):
            r = client.post("/api/v1/tools/merge-csv/upload",
                            files={"file": (f"{_PREFIX}c{i}.csv", b"h\n%d\n" % i, "text/csv")})
            ids.append(r.json()["data"]["upload_id"])
        body = {"upload_ids": ids, "has_header": True, "strict_header": True,
                "sort_mode": "natural"}
        job_id = client.post("/api/v1/tools/merge-csv/execute", json=body).json()["data"]["job_id"]

        import time
        for _ in range(60):
            st = client.get(f"/api/v1/tools/merge-csv/jobs/{job_id}").json()["data"]["status"]
            if st in ("completed", "failed", "cancelled"):
                break
            time.sleep(0.25)

        assert client.post(f"/api/v1/tools/merge-csv/jobs/{job_id}/cancel").status_code == 400
        client.delete(f"/api/v1/tools/merge-csv/jobs/{job_id}")


# ═════════════════════════════════════════════════════════════════════════════
# L  行数对账
# ═════════════════════════════════════════════════════════════════════════════

def _mr(per_file, total_rows):
    r = MergeResult(status="completed", total_rows=total_rows,
                    done_files=len(per_file), per_file=per_file)
    return r


def test_L1_reconcile_matched():
    """L1：导出侧自报行数与实际统计一致 → matched。

    真实基线：导出侧 rows=132102，本工具扫出 132103 条记录（含表头），
    减去表头恰好 132102 —— 两条独立计数路径精确一致。
    """
    per = [
        {"filename": "a.csv", "rows": 132102, "expected_rows": 132102},
        {"filename": "b.csv", "rows": 151000, "expected_rows": 151000},
    ]
    st, detail, exp = svc._reconcile(_mr(per, 283102), {})
    assert st == "matched"
    assert exp == 283102
    assert all(d["diff"] == 0 for d in detail)


def test_L2_reconcile_mismatched_points_at_the_file():
    per = [
        {"filename": "a.csv", "rows": 100, "expected_rows": 100},
        {"filename": "b.csv", "rows": 97, "expected_rows": 100},
    ]
    st, detail, exp = svc._reconcile(_mr(per, 197), {})
    assert st == "mismatched"
    assert exp == 200
    bad = [d for d in detail if d["diff"] != 0]
    assert len(bad) == 1 and bad[0]["filename"] == "b.csv" and bad[0]["diff"] == -3


def test_L3_reconcile_unavailable_when_any_expected_missing():
    """L3：任一文件没有导出侧行数（目录/上传来源）→ unavailable，不能假装对上了。"""
    per = [
        {"filename": "a.csv", "rows": 100, "expected_rows": 100},
        {"filename": "b.csv", "rows": 50, "expected_rows": None},
    ]
    st, detail, exp = svc._reconcile(_mr(per, 150), {})
    assert st == "unavailable" and exp is None


def test_L4_reconcile_empty():
    st, detail, exp = svc._reconcile(_mr([], 0), {})
    assert st == "unavailable" and detail == []


# ═════════════════════════════════════════════════════════════════════════════
# M  RBAC 权限种子
# ═════════════════════════════════════════════════════════════════════════════

def test_M1_permission_exists():
    """M1：tools:merge_csv 权限已入库"""
    from backend.config.database import SessionLocal
    from backend.models.permission import Permission
    db = SessionLocal()
    try:
        perm = (
            db.query(Permission)
            .filter(Permission.resource == "tools", Permission.action == "merge_csv")
            .first()
        )
        assert perm is not None, "请先跑 backend/scripts/migrate_merge_csv.py"
    finally:
        db.close()


def test_M2_assigned_to_superadmin():
    """M2：tools:merge_csv 已分配给 superadmin 角色"""
    from backend.config.database import SessionLocal
    from backend.models.permission import Permission
    from backend.models.role import Role
    from backend.models.role_permission import RolePermission
    db = SessionLocal()
    try:
        perm = (
            db.query(Permission)
            .filter(Permission.resource == "tools", Permission.action == "merge_csv")
            .first()
        )
        role = db.query(Role).filter(Role.name == "superadmin").first()
        assert perm is not None and role is not None
        rp = (
            db.query(RolePermission)
            .filter(RolePermission.role_id == role.id,
                    RolePermission.permission_id == perm.id)
            .first()
        )
        assert rp is not None
    finally:
        db.close()


def test_M3_merge_csv_jobs_table_exists():
    """M3：merge_csv_jobs 表已建"""
    from sqlalchemy import inspect
    from backend.config.database import engine
    assert "merge_csv_jobs" in inspect(engine).get_table_names()
