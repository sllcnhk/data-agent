"""
导出能力矩阵 — 「这套配置到底会得到什么」的单一事实源（v2.16）

## 为什么需要这个模块

v2.15 之前，前端的每一条提示文案都是**硬编码的对后端行为的假设**，没有任何东西
保证它们同步。结果积累了一批错位（v2.16 排查出 9 处），典型如：

  · 「批次大小」输入框在 CSV 下照常可填，但 CSV 路径根本不读它 —— 死配置
  · 「若 SQL 未加 ORDER BY…可能重复或遗漏」这条警告在 CSV 单文件下显示（那里
    没有 LIMIT/OFFSET 回退，不存在该风险），却在分块+direct+无游标列下被隐藏
    （那里恰恰有该风险）—— 该显示的不显示，不该显示的显示
  · 「每超过 100 万行自动插入新 Sheet」无条件渲染，选 CSV 时照样出现
  · 「分块模式：每块单独生成一个 **Excel** 文件」在 csv 分块下是错的
  · 「导出过程可随时取消，已完成块/文件保留可下载」——单文件取消后下载接口直接拒绝
  · 游标列 / 首选分批模式在 CSV、以及 xlsx+csv_staging 下都是死配置

这些都不是"文案没写好"，而是**架构缺一个单一事实源**。所以把「某组配置的真实
能力」收敛到这里：由实现方（后端）计算，经 `GET /data-export/capabilities` 下发，
前端只负责渲染。前端不再猜后端行为，也就不会再漂移。

## 判定依据（全部可回溯到 data_export_service 的真实分支）

    _run_single_export()
      ├─ output_format ∈ {csv, csv_zip} ────────→ _run_csv_export()
      │     ├─ cursor_column 有值 → _stream_sql_to_csv_file_keyset()  ← 可续传，用 batch_size 当窗口
      │     └─ 否则               → _stream_sql_to_csv_file()         ← 单流，不读 batch_size，断流即失败
      ├─ xlsx + engine == csv_staging ──────────→ 落盘 + _csv_to_xlsx()
      │     ├─ cursor_column 有值 → keyset 落盘   ← 可续传（v2.16 起）
      │     └─ 否则               → 单流落盘      ← 断流即失败
      └─ xlsx + engine == direct ───────────────→ xlsxwriter 流式写
            └─ transient error → 回退：有游标列走 keyset，否则 LIMIT/OFFSET

    engine 解析（auto）：
      单文件 → direct        （_run_single_job_sync 不解析 auto，落到 direct 分支）
      分块   → csv_staging   （_run_chunked_export_sync:auto → csv_staging）

## 不在这里编造的事实

  · XLSX 所有单元格都是文本（含 Float/Date/小整数）—— 上游 `_parse_tsv_cell` 一律
    返回 str，xlsxwriter 又以 strings_to_numbers=False 打开。两条引擎行为一致。
    v2.16 在 IDN 上实测确认（data_type 全为 's'）。
  · CSV 的 NULL 是字面量 `\\N` —— IDN 实测 `format_csv_null_representation` = 默认 `\\N`。
  · CSV 遵循 RFC4180（含逗号/引号/换行的字段被引号包裹、`"` 转义为 `""`）—— IDN 实测。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

__all__ = [
    "EXPORT_MODES",
    "OUTPUT_FORMATS",
    "XLSX_ENGINES",
    "resolve_effective_engine",
    "derive_capabilities",
    "build_capability_matrix",
]

EXPORT_MODES = ("single", "date_chunked")
OUTPUT_FORMATS = ("xlsx", "csv", "csv_zip")
XLSX_ENGINES = ("auto", "direct", "csv_staging")

_CSV_FORMATS = frozenset({"csv", "csv_zip"})


def resolve_effective_engine(
    export_mode: str, output_format: str, xlsx_engine: str,
) -> Optional[str]:
    """把 `auto` 解析成实际生效的引擎。

    这是**行为事实**，不是设计意图：
      · 单文件模式下 `_run_single_job_sync` 把 xlsx_engine 原样透传，
        而 `_run_single_export` 只判断 `== "csv_staging"` → auto 落到 direct 分支。
      · 分块模式下 `_run_chunked_export_sync` 显式 `auto → csv_staging`。
    也就是说 UI 上那句「auto：系统判断」在单文件模式下从来没有"判断"过。

    Returns:
        xlsx → "direct" / "csv_staging"；csv / csv_zip → None（不涉及 xlsx 引擎）
    """
    if output_format in _CSV_FORMATS:
        return None
    if xlsx_engine == "auto":
        return "csv_staging" if export_mode == "date_chunked" else "direct"
    return xlsx_engine


def derive_capabilities(
    export_mode: str,
    output_format: str,
    xlsx_engine: str = "auto",
    has_cursor_column: bool = False,
) -> Dict[str, Any]:
    """推导一组配置的真实能力与提示文案。

    返回的每个字段都对应 data_export_service 里一个具体分支，
    对应关系见模块 docstring 的路径图。
    """
    if export_mode not in EXPORT_MODES:
        raise ValueError(f"export_mode 必须是 {EXPORT_MODES} 之一，收到 {export_mode!r}")
    if output_format not in OUTPUT_FORMATS:
        raise ValueError(
            f"output_format 必须是 {OUTPUT_FORMATS} 之一，收到 {output_format!r}"
        )
    if xlsx_engine not in XLSX_ENGINES:
        raise ValueError(f"xlsx_engine 必须是 {XLSX_ENGINES} 之一，收到 {xlsx_engine!r}")

    is_chunked = export_mode == "date_chunked"
    is_csv = output_format in _CSV_FORMATS
    engine = resolve_effective_engine(export_mode, output_format, xlsx_engine)
    is_direct = engine == "direct"
    is_staging = engine == "csv_staging"

    # ── 断流应对能力 ─────────────────────────────────────────────────────────
    # direct 路径进 _run_single_export 的 2-attempt 循环（transient → 分批重跑）
    stream_fallback = is_direct
    # keyset 窗口级续传：CSV 两种格式 + xlsx 的 staging 落盘阶段（v2.16 起），
    # 前提是填了游标列
    resumable = has_cursor_column and (is_csv or is_staging)
    # direct 路径回退时若无游标列 → LIMIT/OFFSET → 无 ORDER BY 时可能重复/漏行
    order_by_risk = is_direct and not has_cursor_column

    # ── batch_size 到底读不读 ────────────────────────────────────────────────
    if is_direct:
        batch_size_effective, batch_size_role = True, "每批从数据库读取的行数"
    elif resumable:
        batch_size_effective, batch_size_role = True, "每个 keyset 窗口的行数"
    else:
        batch_size_effective, batch_size_role = False, None

    # ── 游标列到底用不用 ─────────────────────────────────────────────────────
    if is_direct:
        cursor_role = "流式断开后的回退方式：填了走 keyset，不填走 LIMIT/OFFSET"
    elif is_csv:
        cursor_role = "填了即启用 keyset 多窗口，断流可基于已下载数据继续"
    elif is_staging:
        cursor_role = "CSV 落盘阶段启用 keyset 多窗口，断流可基于已下载数据继续"
    else:
        cursor_role = None
    cursor_column_effective = cursor_role is not None

    # ── 产物形态 ─────────────────────────────────────────────────────────────
    if output_format == "csv_zip":
        artifact = "1 个 .zip（内含全部 CSV 子文件）" if is_chunked else "1 个 .zip"
    elif output_format == "csv":
        artifact = "N 个 .csv（每个日期块一个）" if is_chunked else "1 个 .csv"
    else:
        artifact = "N 个 .xlsx（每个日期块一个）" if is_chunked else "1 个 .xlsx"

    caps: Dict[str, Any] = {
        "export_mode": export_mode,
        "output_format": output_format,
        "xlsx_engine": xlsx_engine,
        "has_cursor_column": has_cursor_column,
        "effective_engine": engine,

        "artifact": artifact,
        # 每满 100 万行新建 Sheet，只有 xlsx 有 Sheet 概念
        "sheet_splitting": not is_csv,
        # xlsx 两条引擎都把所有单元格写成文本（IDN 实测），无 UI 开关可改
        "all_cells_text": not is_csv,
        # CSV 文件带 UTF-8 BOM；xlsx 原生 Unicode（staging 的 BOM 只存在于临时文件）
        "utf8_bom": is_csv,
        "null_representation": "\\N（字面量）" if is_csv else "空单元格",
        # CSV 是 ClickHouse 原始字节直通，大整数原样输出 → Excel 双击会变科学计数法
        "big_int_excel_safe": not is_csv,

        "batch_size_effective": batch_size_effective,
        "batch_size_role": batch_size_role,
        "cursor_column_effective": cursor_column_effective,
        "cursor_column_role": cursor_role,
        # prefer_chunked 只影响 direct 路径的「是否跳过单流首试」
        "prefer_chunked_effective": is_direct,

        "stream_fallback": stream_fallback,
        "resumable_on_disconnect": resumable,
        "order_by_risk": order_by_risk,

        # 分块模式已完成的块可单独下载；单文件模式取消后 download 接口直接拒绝
        "cancel_partial_downloadable": is_chunked,
        "retry_failed_chunks": is_chunked,
    }
    caps["warnings"] = _build_warnings(caps)
    caps["summary"] = _build_summary(caps)
    return caps


def _build_warnings(c: Dict[str, Any]) -> List[str]:
    """按真实风险生成警告 —— 只在确实存在该风险的组合上出现。"""
    out: List[str] = []
    if c["order_by_risk"]:
        out.append(
            "当前路径在网络抖动后会回退到 LIMIT/OFFSET。若 SQL 未加 ORDER BY，"
            "ClickHouse 并行扫描下窗口间可能重叠或漏行 → 数据重复或遗漏。"
            "请在 SQL 末尾加 ORDER BY <主键列>，或填写下方「游标列名」改走 keyset。"
        )
    if not c["stream_fallback"] and not c["resumable_on_disconnect"]:
        out.append(
            "当前路径是 ClickHouse 原始流直写，既无 LIMIT/OFFSET 回退也无续传："
            "一旦断流，本次导出整体失败需重来。跨境/不稳网络下建议填写「游标列名」"
            "启用 keyset 多窗口续传。"
        )
    if c["all_cells_text"]:
        out.append(
            "XLSX 的所有单元格都是文本（含数值列、日期列），在 Excel 里不能直接求和/"
            "排序。两条写入引擎行为一致，没有开关可改。需要数值请在 Excel 里对目标列"
            "执行「数据 → 分列 → 完成」，或改导 CSV 后用「数据 → 从文本/CSV」向导逐列"
            "指定类型。"
        )
    if not c["big_int_excel_safe"]:
        out.append(
            "CSV 是原始字节直通，Int64/UInt64 原样输出。用 Excel 双击打开会变成科学"
            "计数法并丢失精度；喂给程序无此问题。要用 Excel 看请在 SQL 里 toString(列)。"
        )
    if c["null_representation"].startswith("\\N"):
        out.append(
            "CSV 里的 NULL 是字面量 \\N（不是空字段）。下游按数字列直接读会报错，"
            "需显式把 \\N 当缺失值处理（如 pandas 的 na_values=['\\\\N']）。"
        )
    if not c["cancel_partial_downloadable"]:
        out.append(
            "单文件模式取消后**不可下载**（下载接口只接受已完成任务），本次导出作废需"
            "重新提交。需要「取消后保留已完成部分」请用「按日期分块」模式。"
        )
    return out


def _build_summary(c: Dict[str, Any]) -> List[str]:
    """「你将得到什么」—— 面向小白的实时摘要，替代原先那段静态说明。"""
    out = [f"产物：{c['artifact']}"]
    if c["sheet_splitting"]:
        out.append("每满 100 万行自动新建一个 Sheet，每个 Sheet 都带标题行")
    out.append(
        "数值列在 Excel 里是**文本**" if c["all_cells_text"]
        else "字段含逗号/引号/换行时按 RFC4180 加引号转义，用标准 CSV 解析器读取"
    )
    if c["utf8_bom"]:
        out.append("CSV 带 UTF-8 BOM，Excel 直接打开中文不乱码")
    out.append(f"NULL 表现为：{c['null_representation']}")
    if c["resumable_on_disconnect"]:
        out.append("断流可从已下载位置继续（keyset 多窗口），已落盘数据不丢")
    elif c["stream_fallback"]:
        out.append("断流会自动回退重跑（" + (
            "keyset 分页" if c["has_cursor_column"] else "LIMIT/OFFSET 分批"
        ) + "）")
    else:
        out.append("断流后本次导出整体失败，需重新提交")
    out.append(
        "取消后已完成的块仍可单独下载" if c["cancel_partial_downloadable"]
        else "取消后不可下载，需重新提交"
    )
    return out


def build_capability_matrix() -> List[Dict[str, Any]]:
    """穷举全部组合，供 API 一次性下发给前端查表。

    组合数 = 2 模式 × 3 格式 × 3 引擎 × 2 游标列 = 36 条（xlsx_engine 对 CSV
    无意义但仍保留，便于前端直接按四元组查表，不必在前端再写归一化逻辑）。
    """
    return [
        derive_capabilities(mode, fmt, eng, cursor)
        for mode in EXPORT_MODES
        for fmt in OUTPUT_FORMATS
        for eng in XLSX_ENGINES
        for cursor in (False, True)
    ]
