"""
test_export_ui_hints.py — 导出提示文案与编码防回归（v2.16 L4 层）

这一层专治「悄悄回来」的缺陷。v2.16 排查出的问题里有两类靠常规测试抓不到：

  1. **编码破坏**：`CSV落盘` 被写成 `CSV??`（逐字符 `?` 替换）、`文件操作规则`
     被写成 `文���操作规则`（U+FFFD 硬编码进文件）。这些字符串会直接
     进到前端进度列和 LLM system prompt，但功能测试全绿 —— 因为程序照跑，只是
     人看到的是乱码。
  2. **文案与实现不一致**：错误信息声称「已自动尝试 LIMIT/OFFSET 回退」，而 CSV
     路径根本没有回退。这类问题不会让任何断言失败，只会把用户的排查方向带偏。

所以本文件断言的是「源码里不该出现什么字符」和「文案与实现是否自洽」。

覆盖维度：
  A (4)  — 编码完整性（U+FFFD、可疑 ? 替换、行尾一致性）
  B (5)  — _humanize_error 与真实路径自洽
  C (3)  — 前端文案不再声称已被推翻的行为
  D (3)  — 能力矩阵字段被前端真正消费（防「后端算了但前端没用」）

运行：
    /d/ProgramData/Anaconda3/envs/dataagent/python.exe -m pytest test_export_ui_hints.py -v -s
"""
from __future__ import annotations

import io
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(1, os.path.join(os.path.dirname(__file__), "backend"))
os.environ.setdefault("ENABLE_AUTH", "False")

import pytest  # noqa: E402

import backend.services.data_export_service as svc  # noqa: E402

REPO = Path(__file__).parent
BACKEND = REPO / "backend"
FRONTEND = REPO / "frontend" / "src"

#: U+FFFD REPLACEMENT CHARACTER —— 解码失败留下的痕迹，绝不该出现在源码里
_FFFD = "�"


def _py_sources():
    for p in sorted(BACKEND.rglob("*.py")):
        if "__pycache__" in p.parts:
            continue
        yield p


# ═════════════════════════════════════════════════════════════════════════════
# Section A — 编码完整性
# ═════════════════════════════════════════════════════════════════════════════

class TestEncodingIntegrity:

    def test_A1_no_replacement_character_in_backend_sources(self):
        """A1: backend/ 下任何 .py 都不得含 U+FFFD

        v2.16 修掉两处：
          agentic_loop.py:895  '文␦␦␦操作规则' → '文件操作规则'（在 LLM system prompt 里）
          files.py:39          '解␦␦␦后的绝对路径' → '解析后的绝对路径'
        这条断言防止同类问题再次以任何形式进入代码库。
        """
        hits = []
        for p in _py_sources():
            text = io.open(p, encoding="utf-8", errors="replace").read()
            for i, line in enumerate(text.split("\n"), 1):
                if _FFFD in line:
                    hits.append(f"{p.relative_to(REPO)}:{i}")
        assert not hits, (
            "源码里出现 U+FFFD（解码失败痕迹），通常是文件被非 UTF-8 工具改写过：\n  "
            + "\n  ".join(hits)
        )

    def test_A2_export_progress_labels_have_no_ascii_placeholder(self):
        """A2: 导出进度标签不含被 `?` 替换掉的中文

        v2.16 修掉 data_export_service.py:868/872 的 `CSV??` / `CSV?XLSX`
        （原意 `CSV落盘` / `CSV→XLSX`，在 5367856 提交里出生即损坏）。
        这些字符串写进 ExportJob.current_sheet，前端进度列直接展示。
        """
        text = io.open(
            BACKEND / "services" / "data_export_service.py", encoding="utf-8",
        ).read()
        bad = re.findall(r'"[^"\n]*CSV\?[^"\n]*"', text)
        assert not bad, f"进度标签里出现被 ? 替换的字符: {bad}"
        # 正向断言：正确的标签在
        assert "CSV落盘" in text, "缺少 CSV 落盘阶段标签"
        assert "CSV→XLSX" in text, "缺少 CSV→XLSX 转换阶段标签"

    def test_A3_no_ascii_question_mark_adjacent_to_cjk_in_export_modules(self):
        """A3: 导出相关模块里，中文紧邻 ASCII `?` 视为可疑（逐字符替换的特征）

        限定在导出模块，避免误伤别处「中文句尾用半角问号」的正常写法。
        """
        suspicious = []
        targets = [
            BACKEND / "services" / "data_export_service.py",
            BACKEND / "services" / "data_export_chunker.py",
            BACKEND / "services" / "data_export_capabilities.py",
            BACKEND / "services" / "csv_tail.py",
            BACKEND / "services" / "export_clients" / "clickhouse.py",
            BACKEND / "api" / "data_export.py",
        ]
        pat = re.compile(r"[一-鿿]\?|\?[一-鿿]")
        for p in targets:
            text = io.open(p, encoding="utf-8").read()
            for i, line in enumerate(text.split("\n"), 1):
                if pat.search(line):
                    suspicious.append(f"{p.name}:{i}: {line.strip()[:100]}")
        assert not suspicious, (
            "中文与 ASCII ? 相邻，疑似编码破坏：\n  " + "\n  ".join(suspicious)
        )

    def test_A4_export_modules_have_consistent_line_endings(self):
        """A4: 每个导出模块内部行尾一致（混合 CRLF/LF 是被不同工具改写过的信号）"""
        mixed = []
        for name in (
            "services/data_export_service.py",
            "services/data_export_chunker.py",
            "services/data_export_capabilities.py",
            "services/csv_tail.py",
            "services/export_clients/clickhouse.py",
            "api/data_export.py",
        ):
            raw = (BACKEND / name).read_bytes()
            crlf = raw.count(b"\r\n")
            lone_lf = raw.count(b"\n") - crlf
            if crlf and lone_lf:
                mixed.append(f"{name}: CRLF={crlf} LF={lone_lf}")
        assert not mixed, "行尾混用：\n  " + "\n  ".join(mixed)


# ═════════════════════════════════════════════════════════════════════════════
# Section B — 错误文案与真实路径自洽
# ═════════════════════════════════════════════════════════════════════════════

_TRANSIENT = None


def _transient_exc():
    global _TRANSIENT
    if _TRANSIENT is None:
        from requests.exceptions import ChunkedEncodingError
        _TRANSIENT = ChunkedEncodingError(
            "Connection broken: IncompleteRead(0 bytes read, 2 more expected)"
        )
    return _TRANSIENT


class TestErrorMessageHonesty:

    @pytest.mark.parametrize("fmt,engine", [
        ("csv", None), ("csv_zip", None), ("xlsx", "csv_staging"),
    ])
    def test_B1_no_fallback_paths_do_not_claim_fallback(self, fmt, engine):
        """B1: 没有回退能力的路径，文案不得声称「已自动尝试 LIMIT/OFFSET 回退」

        旧文案是无条件写死的，在 CSV / CSV ZIP / xlsx+csv_staging 三条路径下
        描述了系统根本没做过的事。
        """
        msg = svc._humanize_error(_transient_exc(), output_format=fmt,
                                  xlsx_engine=engine, is_chunked=False)
        assert "已自动尝试 LIMIT/OFFSET 回退" not in msg, msg
        assert "没有" in msg and "LIMIT/OFFSET" in msg, (
            f"应明确告知该路径无回退，实际: {msg[:200]}"
        )

    def test_B2_direct_path_still_claims_fallback(self):
        """B2: 确实有回退的路径仍要说 —— 不能过度修正成一律不提"""
        msg = svc._humanize_error(_transient_exc(), output_format="xlsx",
                                  xlsx_engine="direct", is_chunked=False)
        assert "已自动尝试 LIMIT/OFFSET 回退" in msg, msg

    def test_B3_unknown_path_stays_neutral(self):
        """B3: 调用方没提供路径信息时保持中性，既不声称做过也不声称没做过"""
        msg = svc._humanize_error(_transient_exc())
        assert "已自动尝试 LIMIT/OFFSET 回退" not in msg
        assert "没有** LIMIT/OFFSET 回退" not in msg

    def test_B4_single_mode_advice_has_no_chunk_days(self):
        """B4: 单文件模式没有「单块天数」这个概念，不该这么建议

        旧文案不分模式地统一建议「减小单块天数（如 chunk_days=2~3）」。
        """
        single = svc._humanize_error(_transient_exc(), output_format="xlsx",
                                     xlsx_engine="direct", is_chunked=False)
        assert "单块天数" not in single, single
        assert "按日期分块" in single, "单文件模式应建议改用分块模式"

    def test_B5_chunked_mode_advice_mentions_retry_failed_chunks(self):
        """B5: 分块 + 无回退路径应告知「失败块可单独重跑」这个真实可用的手段"""
        msg = svc._humanize_error(_transient_exc(), output_format="csv_zip",
                                  is_chunked=True)
        assert "单块天数" in msg
        assert "重试失败子任务" in msg or "已完成的块" in msg, msg
        # 已经在分块了，不该再劝人「改用按日期分块」
        assert "改用「按日期分块」模式把单次查询切小" not in msg, msg


# ═════════════════════════════════════════════════════════════════════════════
# Section C — 前端文案不再声称已被推翻的行为
# ═════════════════════════════════════════════════════════════════════════════

def _frontend_text(rel: str) -> str:
    p = FRONTEND / rel
    if not p.exists():
        pytest.skip(f"前端文件不存在: {rel}")
    return io.open(p, encoding="utf-8").read()


class TestFrontendCopy:

    def test_C1_auto_engine_copy_no_longer_claims_size_based_decision(self):
        """C1: auto 引擎文案不得再说「大数据默认先落 CSV」

        实测：单文件模式 auto 永远等于 direct，从不按数据量判断。
        旧文案「auto：系统判断。分块/大数据默认先极速落 CSV 临时文件」在单文件
        模式下是假的。
        """
        text = _frontend_text("pages/DataExport.tsx")
        assert "auto：系统判断" not in text, "auto 文案仍声称「系统判断」"
        assert "分块/大数据默认先极速落 CSV 临时文件" not in text
        # 正向：必须说清 auto 是按模式固定选择
        assert "单文件 → 直接写 XLSX" in text, "应明确 auto 在单文件模式下的实际行为"

    def test_C2_static_export_notes_replaced(self):
        """C2: 那段 5 条静态「导出说明」已被实时摘要取代

        其中 4 条在某些组合下是错的：CSV 下说分 Sheet、csv 分块下说「每块一个
        Excel 文件」、单文件取消后其实下载不了。
        """
        text = _frontend_text("pages/DataExport.tsx")
        stale = [
            "每超过 100 万行自动插入新 Sheet，每 Sheet 均含标题行",
            "分块模式：每块单独生成一个 Excel 文件",
            "导出过程可随时取消，已完成块/文件保留可下载",
        ]
        found = [s for s in stale if s in text]
        assert not found, f"仍存在无条件渲染的过时说明: {found}"
        assert "你将得到什么" in text, "应改为实时摘要"

    def test_C3_order_by_warning_no_longer_keyed_on_export_mode(self):
        """C3: ORDER BY 警告不再用 export_mode 当条件

        旧条件 `export_mode !== 'date_chunked'` 恰好两头都错：CSV 单文件（无该
        风险）显示、分块+direct+无游标列（有该风险）隐藏。现在改由后端
        order_by_risk 判定。
        """
        text = _frontend_text("pages/DataExport.tsx")
        assert "普通导出模式下，若 SQL 未加 ORDER BY" not in text, (
            "仍在用旧的按 export_mode 判定的 ORDER BY 警告"
        )


# ═════════════════════════════════════════════════════════════════════════════
# Section D — 能力矩阵确实被前端消费
# ═════════════════════════════════════════════════════════════════════════════

class TestCapabilityConsumption:
    """防「后端算得挺好，前端没用上」—— 那等于白做。"""

    def test_D1_frontend_fetches_capability_matrix(self):
        text = _frontend_text("services/dataExportApi.ts")
        assert "/data-export/capabilities" in text, "前端未调用能力矩阵端点"
        assert "ExportCapability" in text, "缺少能力矩阵类型定义"

    def test_D2_key_capability_fields_are_rendered(self):
        """D2: 关键字段必须在页面里被实际读取"""
        text = _frontend_text("pages/DataExport.tsx")
        for field in (
            "batch_size_effective",     # 决定 batch_size 是否置灰
            "prefer_chunked_effective",  # 决定首选分批是否置灰
            "summary",                   # 「你将得到什么」
            "warnings",                  # 按真实风险显示的警告
        ):
            assert field in text, f"能力字段 {field} 未被前端消费"

    def test_D3_single_mode_has_cursor_column_field(self):
        """D3: 单文件模式必须有游标列输入框（v2.16 新增）

        此前只有分块模式有，导致「千万行单文件 CSV」这个最容易被 5 分钟断流打死
        的场景完全无药可救。
        """
        text = _frontend_text("pages/DataExport.tsx")
        assert text.count('name="cursor_column"') >= 2, (
            "应有两处游标列输入框（分块模式 + 单文件模式）"
        )
        api = _frontend_text("services/dataExportApi.ts")
        assert "cursor_column" in api.split("ExecuteExportRequest")[1][:600], (
            "ExecuteExportRequest 缺少顶层 cursor_column"
        )
