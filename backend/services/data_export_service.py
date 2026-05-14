"""
数据导出服务

实现 SQL → Excel 的核心业务逻辑：
- SQL 预览查询（前 N 行）
- 流式导出执行（HTTP 流 + openpyxl write-only）
- 多 Sheet 自动分割（每 100 万行新建一个 Sheet）
- 大整数安全转字符串（避免 Excel 科学计数法）
- 批次级取消检查（协作式取消）
- 分批提取模式（规避 ClickHouse max_execution_time 估算拒绝 Code 160）
- 按日期分块多文件导出（v2.13）

线程安全说明：
  run_export_job 是 async 包装器，将同步阻塞工作交给线程池（run_in_executor）。
  这样 ClickHouse HTTP 流式读取（requests 同步库）和 openpyxl 写入不会阻塞 asyncio
  事件循环，使其他 API 请求（如任务列表轮询 GET /data-export/jobs）能正常响应。
"""
import asyncio
import csv
import logging
import math
import os
import re
import shutil
import tempfile
import threading
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# 导出专用线程池（最多 4 个并发导出，超出则排队）
_EXPORT_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="export-worker")

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# 常量
# ─────────────────────────────────────────────────────────────────────────────

DEFAULT_BATCH_SIZE = 50_000          # 每批行数
PREVIEW_LIMIT = 100                  # 预览行数
MAX_ROWS_PER_SHEET = 1_000_000       # 每 Sheet 最大数据行（不含表头）
PROGRESS_UPDATE_EVERY = 10           # 每 N 批更新一次 DB 进度
CSV_XLSX_PROGRESS_EVERY_ROWS = 20_000
CSV_XLSX_CANCEL_CHECK_EVERY_ROWS = 5_000
CSV_STREAM_PROGRESS_EVERY_MB = 64
CSV_STREAM_CANCEL_CHECK_EVERY_MB = 16

SUPPORTED_OUTPUT_FORMATS = {"xlsx", "csv", "csv_zip"}
SUPPORTED_XLSX_ENGINES = {"auto", "direct", "csv_staging"}

_EXPORT_QUERY_SEMAPHORES: Dict[str, threading.BoundedSemaphore] = {}
_EXPORT_QUERY_SEMAPHORES_LOCK = threading.Lock()


def _env_int(name: str, default: int, *, min_value: int = 1) -> int:
    """Read a positive integer env var; fall back to default on invalid values."""
    try:
        value = int(os.getenv(name, str(default)))
        return max(min_value, value)
    except (TypeError, ValueError):
        return default


def _get_export_query_semaphore(env: str) -> threading.BoundedSemaphore:
    """按 ClickHouse env 限制导出查询并发，避免多个后台任务挤爆用户级 max_concurrent_queries。"""
    max_per_env = max(1, int(os.getenv("EXPORT_MAX_CONCURRENT_QUERIES_PER_ENV", "1")))
    key = (env or "default").lower()
    with _EXPORT_QUERY_SEMAPHORES_LOCK:
        sem = _EXPORT_QUERY_SEMAPHORES.get(key)
        if sem is None:
            sem = threading.BoundedSemaphore(max_per_env)
            _EXPORT_QUERY_SEMAPHORES[key] = sem
        return sem

# ClickHouse 大整数类型（超过 JS Number.MAX_SAFE_INTEGER = 2^53-1，需转字符串）
_LARGE_INT_TYPES = frozenset([
    "Int64", "UInt64",
    "Int128", "UInt128",
    "Int256", "UInt256",
])

# ─────────────────────────────────────────────────────────────────────────────
# 辅助：构建导出客户端
# ─────────────────────────────────────────────────────────────────────────────

def _build_export_client(env: str, connection_type: str = "clickhouse"):
    """
    工厂函数：根据 env 和 connection_type 构建对应的导出客户端。
    扩展点：新增 connection_type 时在此处添加分支。
    """
    if connection_type == "clickhouse":
        from backend.config.settings import settings
        from backend.services.export_clients.clickhouse import ClickHouseExportClient

        cfg = settings.get_clickhouse_config(env, level="admin")
        return ClickHouseExportClient(
            host=cfg["host"],
            port=cfg["http_port"],
            user=cfg["user"],
            password=cfg["password"],
            database=cfg["database"],
            timeout=3600,
        )
    raise ValueError(f"不支持的连接类型: {connection_type}")


# ─────────────────────────────────────────────────────────────────────────────
# 辅助：单元格值格式化
# ─────────────────────────────────────────────────────────────────────────────

def _format_cell(value: Any, col_type: str) -> Any:
    """
    将数据库原始值转为 Excel 安全值：
    - 大整数类型（Int64/UInt64/...） → str，避免 Excel 科学计数法
    - None → None（openpyxl 写入为空单元格）
    - 其余保持原值
    """
    if value is None:
        return None

    # 去掉 Nullable(...) 包装，如 "Nullable(Int64)" → "Int64"
    bare_type = col_type
    if bare_type.startswith("Nullable(") and bare_type.endswith(")"):
        bare_type = bare_type[9:-1]
    # 去掉 LowCardinality(...) 包装
    if bare_type.startswith("LowCardinality(") and bare_type.endswith(")"):
        bare_type = bare_type[15:-1]

    if bare_type in _LARGE_INT_TYPES and isinstance(value, int):
        return str(value)

    return value


# ─────────────────────────────────────────────────────────────────────────────
# 1. SQL 预览
# ─────────────────────────────────────────────────────────────────────────────

def preview_query(
    sql: str,
    env: str,
    connection_type: str = "clickhouse",
    limit: int = PREVIEW_LIMIT,
    preview_date: Optional[str] = None,
) -> Dict[str, Any]:
    """
    执行 SQL（加 LIMIT），返回列信息和前 N 行数据，用于前端展示预览。

    占位符处理：
      若 SQL 含 {{date_start}} / {{date_end}} 占位符（用于按日期分块导出），
      preview_query 会用 preview_date（或默认昨日）自动替换，让预览能正常执行。
      若 SQL 含 {{ts_start}} / {{ts_end}} 占位符（v2.14.4 半开 DateTime 区间），
      则替换为样本日 00:00:00 到次日 00:00:00。
      这样用户在写好分块 SQL 后无需手动修改即可预览验证。

    Args:
        preview_date: ISO YYYY-MM-DD 字符串；占位符替换的样本日期。
                      None 时默认昨日（避免今日数据可能尚未写入）。
                      仅当 SQL 含占位符时生效。

    Returns:
        {
            "columns": [{"name": str, "type": str}, ...],
            "rows": [[cell, ...], ...],
            "row_count": int,
            "preview_date": str | None,    # 实际使用的样本日期（占位符模式时非空）
        }
    """
    from backend.services.data_export_chunker import (
        _format_ts_exclusive_end,
        _format_ts_inclusive_start,
        has_placeholders,
        has_partial_placeholders,
        has_ts_placeholders,
    )

    # 防御：单占位符（仅 start 或仅 end）会让 ClickHouse 误把
    # 字面量当作日期解析，必须拒绝
    if has_partial_placeholders(sql):
        raise ValueError(
            "SQL 中 {{date_start}}/{{date_end}} 或 {{ts_start}}/{{ts_end}} 必须各自成对出现；"
            "仅写一个会导致预览失败"
        )

    actual_preview_date: Optional[str] = None
    has_date_pair = has_placeholders(sql)
    has_ts_pair = has_ts_placeholders(sql)
    if has_date_pair or has_ts_pair:
        if preview_date:
            try:
                d = datetime.strptime(preview_date, "%Y-%m-%d").date()
            except ValueError as exc:
                raise ValueError(
                    f"preview_date 格式必须为 YYYY-MM-DD，收到 {preview_date!r}"
                ) from exc
        else:
            from datetime import timedelta
            d = (datetime.utcnow().date()) - timedelta(days=1)
        ds = d.isoformat()
        if has_date_pair:
            sql = sql.replace("{{date_start}}", ds).replace("{{date_end}}", ds)
        if has_ts_pair:
            ts_start = _format_ts_inclusive_start(d)
            ts_end = _format_ts_exclusive_end(d)
            sql = sql.replace("{{ts_start}}", ts_start).replace("{{ts_end}}", ts_end)
        actual_preview_date = ds
        logger.info(
            "[preview_query] Substituted placeholders(date=%s, ts=%s) with sample date %s "
            "(provided=%s)", has_date_pair, has_ts_pair, ds, bool(preview_date),
        )

    if connection_type == "clickhouse":
        from backend.config.settings import settings
        cfg = settings.get_clickhouse_config(env, level="admin")
        from backend.mcp.clickhouse.http_client import ClickHouseHTTPClient
        client = ClickHouseHTTPClient(
            host=cfg["host"],
            port=cfg["http_port"],
            user=cfg["user"],
            password=cfg["password"],
            database=cfg["database"],
            timeout=30,
        )
        stripped = sql.rstrip().rstrip(";")
        preview_sql = f"SELECT * FROM ({stripped}) AS _preview LIMIT {limit}"
        rows, col_types = client.execute(preview_sql, with_column_types=True)
        columns = [{"name": name, "type": tp} for name, tp in col_types]
        # 格式化单元格（大整数转字符串）
        formatted_rows = [
            [_format_cell(v, col_types[i][1]) for i, v in enumerate(row)]
            for row in rows
        ]
        return {
            "columns": columns,
            "rows": formatted_rows,
            "row_count": len(formatted_rows),
            "preview_date": actual_preview_date,
        }
    raise ValueError(f"不支持的连接类型: {connection_type}")


# ─────────────────────────────────────────────────────────────────────────────
# 2. 单文件导出 — 内核（被单文件路径与分块路径共用）
# ─────────────────────────────────────────────────────────────────────────────

def _humanize_error(exc: Exception) -> str:
    """
    把技术异常翻译为面向用户的可读提示，附可能原因 + 建议处置。
    用于 ExportJob.error_message — 前端直接展示给最终用户。

    保留原始 exception 字符串作为「技术细节」段，便于排查。

    v2.14:沿 __cause__/__context__ 异常链聚合 message,识别包装层底下的
    原始错误指纹(如 `RuntimeError("分批模式...") from ChunkedEncodingError`)。
    """
    # 沿异常链拼接所有 message,供下方 fingerprint 匹配
    msgs: List[str] = []
    cur: Optional[BaseException] = exc
    seen = set()
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        msgs.append(str(cur))
        cur = getattr(cur, "__cause__", None) or getattr(cur, "__context__", None)
    raw = " | ".join(msgs)
    # 流式断开 — 最常见的瞬时错误
    if any(fp in raw for fp in (
        "Connection broken", "IncompleteRead", "ProtocolError",
        "ChunkedEncodingError", "Connection aborted", "Connection reset",
        "Response ended prematurely", "Read timed out",
        "Remote end closed connection",
    )):
        return (
            "ClickHouse 数据流中途断开（已自动尝试 LIMIT/OFFSET 回退仍未成功）。"
            "可能原因：① 云上 LB / NAT / 反向代理空闲连接超时（最常见，~5 分钟切断）；"
            "② 跨境网络抖动；③ ClickHouse 服务端 OOM 或主动 abort 查询。"
            "已自动注入服务端心跳设置（send_progress_in_http_headers=1, "
            "http_headers_progress_interval_ms=10000）；若仍失败，建议："
            "① 减小单块天数（如 chunk_days=2~3）让单次查询时长 < 5 分钟；"
            "② 简化 SQL（减少每行 CPU 消耗，如 decrypt/JSONExtract）；"
            "③ 错峰重试；④ 联系 DBA 检查 ClickHouse 实例资源水位 / 调整云 LB 空闲超时。\n"
            f"[技术细节] {raw}"
        )
    # Code 160 — 估算超时
    if "Code: 160" in raw or "ESTIMATED_EXECUTION_TIMEOUT_EXCEEDED" in raw:
        return (
            "ClickHouse 估算查询执行时间超出 max_execution_time 限制（已自动 "
            "LIMIT/OFFSET 回退仍未恢复）。建议：① 减小单块天数；② 在数据库层"
            "增加索引/分区；③ 调高 EXPORT_QUERY_MAX_EXECUTION_TIME 环境变量。\n"
            f"[技术细节] {raw}"
        )
    if "Code: 202" in raw or "TOO_MANY_SIMULTANEOUS_QUERIES" in raw or "Too many simultaneous queries" in raw:
        return (
            "ClickHouse 当前用户并发查询数已达到上限。系统已对导出任务增加 env 级并发闸门和自动退避重试；"
            "若仍失败，请稍后重试、减少同时运行的报表/导出任务，或为导出配置独立 ClickHouse 用户。\n"
            f"[技术细节] {raw}"
        )
    # 网络连接失败 — 配置或环境问题
    if "Connection refused" in raw or "Failed to establish" in raw:
        return (
            f"无法连接到 ClickHouse 服务器。请检查连接配置（host/port）、网络"
            f"可达性或服务状态。\n[技术细节] {raw}"
        )
    # 权限/认证类
    if "Authentication failed" in raw or "Code: 192" in raw:
        return f"ClickHouse 认证失败 — 请检查用户名密码配置。\n[技术细节] {raw}"
    # 表不存在
    if "Code: 60" in raw or "doesn't exist" in raw:
        return f"SQL 引用的表/视图不存在。\n[技术细节] {raw}"
    # 兜底
    return raw


def _media_type_for_format(fmt: str) -> str:
    if fmt == "csv":
        return "text/csv; charset=utf-8"
    if fmt == "csv_zip":
        return "application/zip"
    return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _count_csv_records(csv_path: str) -> int:
    """用 csv.reader 正确处理引号/换行，返回数据行数（不含表头）。"""
    with open(csv_path, "r", encoding="utf-8-sig", newline="") as fp:
        reader = csv.reader(fp)
        try:
            next(reader)
        except StopIteration:
            return 0
        return sum(1 for _ in reader)


def _csv_to_xlsx(
    csv_path: str,
    xlsx_path: str,
    *,
    job_id: str,
    sheet_prefix: str = "Sheet",
    progress_offset: int = 0,
    progress_total: Optional[int] = None,
    chunk_label: Optional[str] = None,
    on_cancel: Optional[Any] = None,
) -> Dict[str, Any]:
    """
    CSV 临时文件 → XLSX。

    open(..., newline="") 交给 csv 模块处理 CRLF/引号内换行，避免错误分列/错行；
    encoding="utf-8-sig" 兼容带 BOM 的 CSV，避免首列表头带 BOM。
    """
    import xlsxwriter

    _XLSX_OPTIONS = {
        "constant_memory": True,
        "strings_to_numbers": False,
        "strings_to_formulas": False,
        "strings_to_urls": False,
    }

    Path(xlsx_path).parent.mkdir(parents=True, exist_ok=True)
    wb = xlsxwriter.Workbook(xlsx_path, options=_XLSX_OPTIONS)
    sheet_num = 1
    ws = wb.add_worksheet(f"{sheet_prefix}{sheet_num}")
    data_rows = 0
    sheet_row_count = 0
    progress_every = _env_int(
        "CSV_XLSX_PROGRESS_EVERY_ROWS",
        CSV_XLSX_PROGRESS_EVERY_ROWS,
    )
    cancel_check_every = _env_int(
        "CSV_XLSX_CANCEL_CHECK_EVERY_ROWS",
        CSV_XLSX_CANCEL_CHECK_EVERY_ROWS,
    )

    def _format_sheet_label(n: int) -> str:
        base = f"{sheet_prefix}{n}"
        return f"{chunk_label} - {base}" if chunk_label else base

    _update_job(job_id, current_sheet=_format_sheet_label(sheet_num))

    try:
        with open(csv_path, "r", encoding="utf-8-sig", newline="") as fp:
            reader = csv.reader(fp)
            header = next(reader, None) or []
            ws.write_row(0, 0, header)
            cur_row = 1

            for row in reader:
                # CSV -> XLSX 是纯本地转换，不能每行查一次 DB，否则几十万行会产生
                # 几十万次短连接/checkout，表现为“ClickHouse 查询已结束但页面长时间 0 行”。
                if data_rows % cancel_check_every == 0:
                    cancelled = (on_cancel and on_cancel()) or _is_cancelling(job_id)
                    if cancelled:
                        wb.close()
                        return {
                            "exported_rows": data_rows,
                            "total_sheets": sheet_num,
                            "done_batches": 0,
                            "total_sql_chunks": None,
                            "file_size": Path(xlsx_path).stat().st_size if Path(xlsx_path).exists() else 0,
                            "cancelled": True,
                        }

                if sheet_row_count >= MAX_ROWS_PER_SHEET:
                    sheet_num += 1
                    ws = wb.add_worksheet(f"{sheet_prefix}{sheet_num}")
                    ws.write_row(0, 0, header)
                    cur_row = 1
                    sheet_row_count = 0
                    _update_job(
                        job_id,
                        current_sheet=f"{_format_sheet_label(sheet_num)} · 已转换 {data_rows:,} 行",
                    )

                ws.write_row(cur_row, 0, row)
                cur_row += 1
                data_rows += 1
                sheet_row_count += 1

                if data_rows % progress_every == 0:
                    _update_job(
                        job_id,
                        exported_rows=progress_offset + data_rows,
                        current_sheet=f"{_format_sheet_label(sheet_num)} · 已转换 {data_rows:,} 行",
                    )

        wb.close()
        return {
            "exported_rows": data_rows,
            "total_sheets": sheet_num,
            "done_batches": max(1, math.ceil(data_rows / DEFAULT_BATCH_SIZE)) if data_rows else 0,
            "total_sql_chunks": None,
            "file_size": Path(xlsx_path).stat().st_size,
            "cancelled": False,
        }
    except Exception:
        try:
            wb.close()
        except Exception:
            pass
        raise


def _zip_single_file(src_path: str, zip_path: str, arcname: Optional[str] = None) -> int:
    Path(zip_path).parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        zf.write(src_path, arcname=arcname or Path(src_path).name)
    return Path(zip_path).stat().st_size


def _is_auto_pre_split(value: Any) -> bool:
    """Whether pre_split_hours requests data-volume based auto windows."""
    return isinstance(value, str) and value.strip().lower() == "auto"


def _validate_auto_split_column(name: str) -> str:
    """Validate a SELECT alias/column name used by auto pre-split count SQL."""
    cleaned = (name or "").strip().strip("`").strip()
    if not cleaned:
        raise ValueError("auto 预分窗口需要提供统计时间列（date_column 或 auto_split_column）")
    # Match the cursor-column policy: allow letters/digits/underscore/space/CJK, no backticks.
    if "`" in cleaned or not re.match(r"^[A-Za-z_\u4e00-\u9fff][A-Za-z0-9_ \u4e00-\u9fff]*$", cleaned):
        raise ValueError(
            "auto 预分窗口统计时间列仅允许字母/数字/下划线/空格/中文，且不能包含反引号"
        )
    return cleaned


def _quote_ch_identifier(name: str) -> str:
    """Quote a ClickHouse identifier after validation."""
    return f"`{_validate_auto_split_column(name)}`"


def _parse_bucket_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value.replace(microsecond=0)
    raw = str(value).strip().replace("T", " ")
    # ClickHouse formatDateTime returns YYYY-mm-dd HH:MM:SS; keep a fallback for Date-only.
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            parsed = datetime.strptime(raw, fmt)
            return parsed.replace(microsecond=0)
        except ValueError:
            continue
    raise ValueError(f"无法解析 auto 预分窗口 bucket 时间: {value!r}")


def _auto_bucket_rows_to_windows(
    bucket_rows: List[Tuple[Any, Any]],
    *,
    unit: str,
    target_rows: int = MAX_ROWS_PER_SHEET,
) -> List[Tuple[datetime, datetime, int]]:
    """
    Convert sorted bucket counts into greedy continuous windows.

    The next bucket is appended while total rows remain <= target_rows.
    If a single bucket already exceeds target_rows it becomes a single window;
    later XLSX sheet splitting still protects Excel's per-sheet row limit.
    """
    if unit not in ("hour", "minute"):
        raise ValueError("auto 预分窗口仅支持 hour/minute 粒度")
    target_rows = max(1, int(target_rows or MAX_ROWS_PER_SHEET))
    step = timedelta(hours=1) if unit == "hour" else timedelta(minutes=1)

    parsed: List[Tuple[datetime, int]] = []
    for bucket, cnt in bucket_rows:
        rows = int(cnt or 0)
        if rows <= 0:
            continue
        parsed.append((_parse_bucket_datetime(bucket), rows))
    parsed.sort(key=lambda x: x[0])

    windows: List[Tuple[datetime, datetime, int]] = []
    cur_start: Optional[datetime] = None
    cur_end: Optional[datetime] = None
    cur_rows = 0

    for bucket_start, rows in parsed:
        bucket_end = bucket_start + step - timedelta(seconds=1)
        if cur_start is None:
            cur_start, cur_end, cur_rows = bucket_start, bucket_end, rows
            continue
        if cur_rows > 0 and cur_rows + rows > target_rows:
            windows.append((cur_start, cur_end or bucket_end, cur_rows))
            cur_start, cur_end, cur_rows = bucket_start, bucket_end, rows
        else:
            cur_end = bucket_end
            cur_rows += rows

    if cur_start is not None:
        windows.append((cur_start, cur_end or cur_start + step - timedelta(seconds=1), cur_rows))
    return windows


def _build_auto_pre_split_count_sql(
    filtered_sql: str,
    *,
    time_column: str,
    unit: str,
) -> str:
    """Build a ClickHouse query that returns bucket_start,count for the filtered user SQL.

    格式串陷阱(v2.14.5 修复):
        ClickHouse formatDateTime 遵循 MySQL DATE_FORMAT 语义,与 Python strftime 不同:
            %i  → 分钟 (00-59)        ← 我们要的
            %M  → 月份英文全名 (March) ← Python 里才是分钟
            %S  → 秒  %H → 时(00-23)  %d → 日  %m → 月数字  %Y → 年
            %F  ≡ %Y-%m-%d    %T  ≡ %H:%i:%S
        旧实现误用 '%H:%M:%S' → 返回 '09:March:00' → _parse_bucket_datetime ValueError
        导致整个 Job 失败。改成 '%F %T' 等价但更显眼,杜绝该类拼写错误。
    """
    if unit not in ("hour", "minute"):
        raise ValueError("auto 预分窗口仅支持 hour/minute 粒度")
    bucket_func = "toStartOfHour" if unit == "hour" else "toStartOfMinute"
    col = _quote_ch_identifier(time_column)
    stripped = filtered_sql.rstrip().rstrip(";")
    return (
        "SELECT formatDateTime(bucket, '%F %T') AS bucket_start, count() AS rows\n"
        "FROM (\n"
        f"  SELECT {bucket_func}(toDateTime(_auto_q.{col})) AS bucket\n"
        f"  FROM ({stripped}) AS _auto_q\n"
        ") AS _auto_bucket_q\n"
        "GROUP BY bucket\n"
        "ORDER BY bucket"
    )


def _stream_sql_to_csv_file(
    *,
    job_id: str,
    sql: str,
    env: str,
    conn_type: str,
    csv_path: str,
    query_id_prefix: str,
    on_cancel: Optional[Any] = None,
    count_rows: bool = True,
    progress_label: Optional[str] = None,
) -> Dict[str, Any]:
    """ClickHouse FORMAT CSVWithNames 原始字节直写 CSV 文件（带 UTF-8 BOM，方便 Excel 打开中文不乱码）。"""
    from backend.config.settings import settings as app_settings
    from backend.services.export_clients.clickhouse import is_ch_too_many_queries_error

    export_settings = {"max_execution_time": app_settings.export_query_max_execution_time}
    export_client = _build_export_client(env, conn_type)
    sem = _get_export_query_semaphore(env)
    max_too_many_retry = int(os.getenv("EXPORT_TOO_MANY_RETRY_MAX", "5"))
    base_sleep = int(os.getenv("EXPORT_TOO_MANY_RETRY_BACKOFF", "10"))
    progress_every_bytes = (
        _env_int("CSV_STREAM_PROGRESS_EVERY_MB", CSV_STREAM_PROGRESS_EVERY_MB)
        * 1024 * 1024
    )
    cancel_check_every_bytes = (
        _env_int("CSV_STREAM_CANCEL_CHECK_EVERY_MB", CSV_STREAM_CANCEL_CHECK_EVERY_MB)
        * 1024 * 1024
    )

    Path(csv_path).parent.mkdir(parents=True, exist_ok=True)

    for attempt in range(max_too_many_retry + 1):
        try:
            bytes_written = 0
            last_progress_bytes = 0
            last_cancel_check_bytes = 0
            if progress_label:
                _update_job(job_id, current_sheet=f"{progress_label} · 0.0 MB")
            with sem:
                with open(csv_path, "wb") as fp:
                    # UTF-8 BOM 仅用于文件层；后续 csv_to_xlsx 通过 utf-8-sig 自动吞掉
                    fp.write(b"\xef\xbb\xbf")
                    bytes_written += 3
                    for chunk in export_client.stream_raw(
                        sql,
                        format_name="CSVWithNames",
                        extra_settings=export_settings,
                        query_id_prefix=f"{query_id_prefix}:csv",
                    ):
                        # HTTP 原始流的 chunk 可能很多；按字节节流取消检查，避免频繁查 DB。
                        if bytes_written - last_cancel_check_bytes >= cancel_check_every_bytes:
                            last_cancel_check_bytes = bytes_written
                            if (on_cancel and on_cancel()) or _is_cancelling(job_id):
                                return {
                                    "exported_rows": 0,
                                    "total_sheets": 0,
                                    "done_batches": 0,
                                    "total_sql_chunks": None,
                                    "file_size": bytes_written,
                                    "cancelled": True,
                                }
                        fp.write(chunk)
                        bytes_written += len(chunk)
                        if progress_label and bytes_written - last_progress_bytes >= progress_every_bytes:
                            last_progress_bytes = bytes_written
                            _update_job(
                                job_id,
                                current_sheet=(
                                    f"{progress_label} · {bytes_written / 1024 / 1024:.1f} MB"
                                ),
                            )
            if not count_rows:
                return {
                    "exported_rows": 0,
                    "total_sheets": 0,
                    "done_batches": 1,
                    "total_sql_chunks": None,
                    "file_size": Path(csv_path).stat().st_size,
                    "cancelled": False,
                }
            if progress_label:
                _update_job(job_id, current_sheet=f"{progress_label} · 统计 CSV 行数")
            rows = _count_csv_records(csv_path)
            return {
                "exported_rows": rows,
                "total_sheets": 0,
                "done_batches": 1,
                "total_sql_chunks": None,
                "file_size": Path(csv_path).stat().st_size,
                "cancelled": False,
            }
        except Exception as exc:
            if is_ch_too_many_queries_error(exc) and attempt < max_too_many_retry:
                sleep_s = min(60, base_sleep * (attempt + 1))
                logger.warning(
                    "[ExportJob %s] ClickHouse too many simultaneous queries, retry in %ss "
                    "(attempt %d/%d): %s",
                    job_id, sleep_s, attempt + 1, max_too_many_retry, exc,
                )
                try:
                    os.unlink(csv_path)
                except Exception:
                    pass
                time.sleep(sleep_s)
                continue
            raise

    raise RuntimeError("CSV stream export failed after retries")


def _run_csv_export(
    *,
    job_id: str,
    sql: str,
    env: str,
    conn_type: str,
    output_path: str,
    output_format: str,
    query_id_prefix: str,
    on_cancel: Optional[Any] = None,
) -> Dict[str, Any]:
    """CSV / CSV ZIP 极速导出路径。"""
    if output_format == "csv":
        return _stream_sql_to_csv_file(
            job_id=job_id,
            sql=sql,
            env=env,
            conn_type=conn_type,
            csv_path=output_path,
            query_id_prefix=query_id_prefix,
            on_cancel=on_cancel,
        )

    # csv_zip:先落 CSV 临时文件，再 zip。避免把 zip 写入失败与 ClickHouse 查询耦合。
    tmp_dir = tempfile.mkdtemp(prefix=f"dataagent_export_{job_id}_")
    try:
        csv_name = Path(output_path).with_suffix(".csv").name
        tmp_csv = str(Path(tmp_dir) / csv_name)
        result = _stream_sql_to_csv_file(
            job_id=job_id,
            sql=sql,
            env=env,
            conn_type=conn_type,
            csv_path=tmp_csv,
            query_id_prefix=query_id_prefix,
            on_cancel=on_cancel,
        )
        if result.get("cancelled"):
            return result
        file_size = _zip_single_file(tmp_csv, output_path, arcname=csv_name)
        result["file_size"] = file_size
        return result
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _parse_iso_endpoint(s: str):
    """ISO 字符串 → date 或 datetime。datetime 形如 'YYYY-MM-DDTHH:MM:SS' 含 'T'。"""
    if "T" in s:
        return datetime.fromisoformat(s)
    return date.fromisoformat(s)


def _is_cancelling(job_id: str) -> bool:
    from backend.config.database import SessionLocal
    from backend.models.export_job import ExportJob
    db = SessionLocal()
    try:
        j = db.query(ExportJob).filter(ExportJob.id == job_id).first()
        return j is not None and j.status == "cancelling"
    finally:
        db.close()


def _update_job(job_id: str, **fields) -> None:
    """通用 ExportJob 字段更新（自动 set updated_at + commit）"""
    from backend.config.database import SessionLocal
    from backend.models.export_job import ExportJob
    db = SessionLocal()
    try:
        j = db.query(ExportJob).filter(ExportJob.id == job_id).first()
        if j is None:
            return
        for k, v in fields.items():
            setattr(j, k, v)
        j.updated_at = datetime.utcnow()
        db.commit()
    finally:
        db.close()


def _run_single_export(
    job_id: str,
    sql: str,
    env: str,
    conn_type: str,
    batch_size: int,
    output_path: str,
    *,
    sheet_prefix: str = "Sheet",
    progress_offset: int = 0,
    progress_total: Optional[int] = None,
    chunk_label: Optional[str] = None,
    on_cancel: Optional[Any] = None,
    cursor_column: Optional[str] = None,
    output_format: str = "xlsx",
    xlsx_engine: str = "direct",
    query_id_prefix: Optional[str] = None,
    prefer_chunked: bool = False,
) -> Dict[str, Any]:
    """
    执行单个 SQL 的 ClickHouse → Excel 流式导出。
    被单文件模式与分块模式共用。

    返回结果摘要：
        {
            "exported_rows": int,
            "total_sheets": int,
            "file_size": int,
            "cancelled": bool,
        }

    分块模式下使用 progress_offset / progress_total 让进度条以全 Job 维度更新。
    流式失败自动回退到 fallback 路径:
      - cursor_column 提供 → keyset 分页(stream_batches_keyset),正确性 + 性能双优;
      - 否则 → LIMIT/OFFSET (stream_batches_chunked),老行为兜底。

    v2.14.6 — prefer_chunked 参数:
      跨境/不稳网络下,5 分钟左右 LB/NAT 切断单流的现象极为稳定,每个块先单流试错 5 分钟
      浪费严重且会产生不完整 xlsx 重头来。prefer_chunked=True 时直接跳过单流,首试就走
      chunked 路径(keyset 或 LIMIT/OFFSET),省掉 5-10 分钟/块的浪费。
      建议同时填 cursor_column,否则 chunked 用 LIMIT/OFFSET 在后期 OFFSET 大时重扫开销显著。

    Excel 写入引擎(v2.14.3):
      使用 xlsxwriter constant_memory 模式替代 openpyxl write_only。
      性能:典型场景写入速度 3-5x 提升(C 加速字符串处理 + 流式 zip);
      内存:与 batch_size 无关,每行写完即丢,峰值更低。
      行为:
        - 关闭 strings_to_numbers/formulas/urls 自动转换 — 所有 str 按文本写入,
          与 openpyxl write_only 默认行为一致(大整数字符串不会变科学计数法)。
        - constant_memory 不允许修改已写出数据;但允许多 sheet 顺序写(对应
          MAX_ROWS_PER_SHEET 分割逻辑)。
        - wb.close() 前文件非完整 xlsx(zip 结构未封口),cancel/fallback 切换时
          先 close() 落盘已写部分,需要重头时删除文件再新建。
    """
    import xlsxwriter
    from backend.config.settings import settings as app_settings
    from backend.services.export_clients.clickhouse import (
        is_ch_timeout_estimate_error,
        is_ch_too_many_queries_error,
        is_transient_stream_error,
    )

    export_settings = {"max_execution_time": app_settings.export_query_max_execution_time}

    # xlsxwriter Workbook 全局选项:关闭自动类型推断,保持文本字符串原样
    _XLSX_OPTIONS = {
        "constant_memory": True,
        "strings_to_numbers": False,
        "strings_to_formulas": False,
        "strings_to_urls": False,
    }

    # 列信息
    export_client = _build_export_client(env, conn_type)
    columns = export_client.get_columns(sql)
    col_names = [c.name for c in columns]
    col_types = [c.type for c in columns]

    query_id_prefix = query_id_prefix or f"dataagent_export:{job_id}"

    if output_format in {"csv", "csv_zip"}:
        return _run_csv_export(
            job_id=job_id,
            sql=sql,
            env=env,
            conn_type=conn_type,
            output_path=output_path,
            output_format=output_format,
            query_id_prefix=query_id_prefix,
            on_cancel=on_cancel,
        )

    if output_format == "xlsx" and xlsx_engine == "csv_staging":
        tmp_dir = tempfile.mkdtemp(prefix=f"dataagent_xlsx_stage_{job_id}_")
        try:
            tmp_csv = str(Path(tmp_dir) / (Path(output_path).stem + ".csv"))
            csv_result = _stream_sql_to_csv_file(
                job_id=job_id,
                sql=sql,
                env=env,
                conn_type=conn_type,
                csv_path=tmp_csv,
                query_id_prefix=f"{query_id_prefix}:stage",
                on_cancel=on_cancel,
                count_rows=False,
                progress_label=(f"{chunk_label} - CSV??" if chunk_label else "CSV??"),
            )
            if csv_result.get("cancelled"):
                return csv_result
            _update_job(job_id, current_sheet=(f"{chunk_label} - CSV?XLSX" if chunk_label else "CSV?XLSX"))
            return _csv_to_xlsx(
                tmp_csv,
                output_path,
                job_id=job_id,
                sheet_prefix=sheet_prefix,
                progress_offset=progress_offset,
                progress_total=progress_total,
                chunk_label=chunk_label,
                on_cancel=on_cancel,
            )
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    # v2.14.6: prefer_chunked=True → 跳过单流首试,直接走 chunked 路径
    use_chunked = bool(prefer_chunked)
    total_sql_chunks = 1
    if use_chunked:
        logger.info(
            "[ExportJob %s] prefer_chunked=True: 跳过单流首试,直接走 %s 路径",
            job_id, "keyset" if cursor_column else "LIMIT/OFFSET",
        )

    for attempt in range(2):
        if attempt == 1 and not use_chunked:
            break

        # 初始化工作簿(xlsxwriter 立即创建文件 + 流式写,attempt=1 重头来需先删旧文件)
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        if attempt == 1:
            try:
                os.unlink(output_path)
            except FileNotFoundError:
                pass
            except Exception:
                pass
        wb = xlsxwriter.Workbook(output_path, options=_XLSX_OPTIONS)
        sheet_num = 1
        ws = wb.add_worksheet(f"{sheet_prefix}{sheet_num}")
        ws.write_row(0, 0, col_names)
        cur_row = 1  # 下一条数据写入行(表头占 0)

        exported_rows = 0
        done_batches = 0
        sheet_row_count = 0

        # 分块模式下 current_sheet 形如「块 N/M (...) - Sheet1」让前端能看到块进度；
        # 单文件模式下保持「Sheet1」原样。
        def _format_sheet_label(n: int) -> str:
            base = f"{sheet_prefix}{n}"
            return f"{chunk_label} - {base}" if chunk_label else base

        _update_job(job_id, current_sheet=_format_sheet_label(sheet_num))

        if use_chunked:
            if cursor_column:
                # keyset 分页:不需要 count_rows / chunk_size,直接逐窗口推进
                logger.info(
                    "[ExportJob %s] Fallback mode: keyset pagination on cursor=%s "
                    "(replaces LIMIT/OFFSET for correctness + speed)",
                    job_id, cursor_column,
                )
                try:
                    batch_source = export_client.stream_batches_keyset(
                        sql,
                        cursor_column=cursor_column,
                        batch_size=batch_size,
                        extra_settings=export_settings,
                        query_id_prefix=f"{query_id_prefix}:keyset",
                    )
                except TypeError as exc:
                    if "query_id_prefix" not in str(exc):
                        raise
                    batch_source = export_client.stream_batches_keyset(
                        sql,
                        cursor_column=cursor_column,
                        batch_size=batch_size,
                        extra_settings=export_settings,
                    )
                # 进度分母对 keyset 不易预先知道,沿用现有进度条逻辑(无 total_sql_chunks)
                total_sql_chunks = 1
            else:
                try:
                    total_rows = export_client.count_rows(
                        sql, timeout=app_settings.export_query_max_execution_time,
                    )
                except Exception as cnt_err:
                    raise RuntimeError(f"分批模式预扫描行数失败: {cnt_err}") from cnt_err

                total_sql_chunks = max(1, math.ceil(total_rows / app_settings.export_chunk_size))
                logger.info(
                    "[ExportJob %s] Fallback mode: LIMIT/OFFSET total_rows=%d → %d "
                    "SQL chunks (chunk_size=%d). 警告:无 cursor_column,后期窗口可能"
                    "存在 OFFSET 重扫开销 + ClickHouse 并行扫描下可能非确定性。",
                    job_id, total_rows, total_sql_chunks, app_settings.export_chunk_size,
                )
                update_kw: Dict[str, Any] = {}
                if progress_total is None:
                    update_kw["total_rows"] = total_rows
                    update_kw["total_batches"] = total_sql_chunks
                if update_kw:
                    _update_job(job_id, **update_kw)

                try:
                    batch_source = export_client.stream_batches_chunked(
                        sql,
                        chunk_size=app_settings.export_chunk_size,
                        total_rows=total_rows,
                        batch_size=batch_size,
                        extra_settings=export_settings,
                        query_id_prefix=f"{query_id_prefix}:limit_offset",
                    )
                except TypeError as exc:
                    if "query_id_prefix" not in str(exc):
                        raise
                    batch_source = export_client.stream_batches_chunked(
                        sql,
                        chunk_size=app_settings.export_chunk_size,
                        total_rows=total_rows,
                        batch_size=batch_size,
                        extra_settings=export_settings,
                    )
        else:
            try:
                batch_source = export_client.stream_batches(
                    sql,
                    batch_size=batch_size,
                    extra_settings=export_settings,
                    query_id_prefix=f"{query_id_prefix}:stream",
                )
            except TypeError as exc:
                if "query_id_prefix" not in str(exc):
                    raise
                batch_source = export_client.stream_batches(
                    sql,
                    batch_size=batch_size,
                    extra_settings=export_settings,
                )

        try:
            max_too_many_retry = int(os.getenv("EXPORT_TOO_MANY_RETRY_MAX", "5"))
            too_many_attempt = 0
            while True:
                try:
                    with _get_export_query_semaphore(env):
                        for batch in batch_source:
                            # 检查取消
                            cancelled = (on_cancel and on_cancel()) or _is_cancelling(job_id)
                            if cancelled:
                                logger.info(
                                    "[ExportJob %s] Cancelled mid-export after %d rows.",
                                    job_id, exported_rows,
                                )
                                # xlsxwriter:close() 把已写部分落盘(封 zip)
                                try:
                                    wb.close()
                                except Exception:
                                    pass
                                return {
                                    "exported_rows": exported_rows,
                                    "total_sheets": sheet_num,
                                    "done_batches": done_batches,
                                    "total_sql_chunks": total_sql_chunks if use_chunked else None,
                                    "file_size": (
                                        Path(output_path).stat().st_size
                                        if Path(output_path).exists() else 0
                                    ),
                                    "cancelled": True,
                                }

                            for row in batch:
                                if sheet_row_count >= MAX_ROWS_PER_SHEET:
                                    sheet_num += 1
                                    ws = wb.add_worksheet(f"{sheet_prefix}{sheet_num}")
                                    ws.write_row(0, 0, col_names)
                                    cur_row = 1
                                    sheet_row_count = 0
                                    _update_job(job_id, current_sheet=_format_sheet_label(sheet_num))

                                formatted = [_format_cell(v, col_types[i]) for i, v in enumerate(row)]
                                ws.write_row(cur_row, 0, formatted)
                                cur_row += 1
                                sheet_row_count += 1
                                exported_rows += 1

                            done_batches += 1

                            if done_batches % PROGRESS_UPDATE_EVERY == 0:
                                if progress_total is None:
                                    # 单文件模式：本函数全权管理进度字段
                                    if use_chunked:
                                        _update_job(
                                            job_id,
                                            exported_rows=exported_rows,
                                            done_batches=exported_rows // app_settings.export_chunk_size,
                                            total_batches=total_sql_chunks,
                                        )
                                    else:
                                        _update_job(
                                            job_id,
                                            exported_rows=exported_rows,
                                            done_batches=done_batches,
                                        )
                                else:
                                    # 分块模式由父循环更新 done_batches；此处仅累加 exported_rows
                                    _update_job(
                                        job_id,
                                        exported_rows=progress_offset + exported_rows,
                                    )
                    break
                except Exception as exc:
                    if is_ch_too_many_queries_error(exc) and too_many_attempt < max_too_many_retry:
                        too_many_attempt += 1
                        sleep_s = min(60, int(os.getenv("EXPORT_TOO_MANY_RETRY_BACKOFF", "10")) * too_many_attempt)
                        logger.warning(
                            "[ExportJob %s] ClickHouse too many simultaneous queries, retry whole window in %ss "
                            "(attempt %d/%d): %s",
                            job_id, sleep_s, too_many_attempt, max_too_many_retry, exc,
                        )
                        try:
                            wb.close()
                        except Exception:
                            pass
                        try:
                            os.unlink(output_path)
                        except Exception:
                            pass
                        time.sleep(sleep_s)
                        # 重新抛给外层自动分裂更安全：避免同一个 xlsxwriter workbook 续写复杂性。
                        raise
                    raise

            wb.close()
            file_size = Path(output_path).stat().st_size
            logger.info(
                "[ExportJob %s] Single-export OK: %d rows, %d sheet(s), %.1f MB, "
                "mode=%s chunks=%d (engine=xlsxwriter)",
                job_id, exported_rows, sheet_num, file_size / 1024 / 1024,
                "chunked" if use_chunked else "stream", total_sql_chunks,
            )
            return {
                "exported_rows": exported_rows,
                "total_sheets": sheet_num,
                "done_batches": done_batches,
                "total_sql_chunks": total_sql_chunks if use_chunked else None,
                "file_size": file_size,
                "cancelled": False,
            }

        except Exception as exc:
            # 触发自动 LIMIT/OFFSET 回退的错误类型：
            #   1. Code 160（估算超时）— ClickHouse 拒绝执行
            #   2. 流式响应中途断开 — 服务端/网络/代理切断长连接
            # 回退策略：每个窗口是独立 HTTP 请求，短小独立连接，重试大概率成功
            should_retry = attempt == 0 and (
                is_ch_timeout_estimate_error(exc)
                or is_transient_stream_error(exc)
            )
            if should_retry:
                reason = (
                    "Code 160 (estimated timeout)"
                    if is_ch_timeout_estimate_error(exc)
                    else "transient stream disconnect"
                )
                logger.warning(
                    "[ExportJob %s] %s — switching to LIMIT/OFFSET fallback mode: %s",
                    job_id, reason, exc,
                )
                use_chunked = True
                try:
                    wb.close()
                except Exception:
                    pass
                continue
            raise

    # 不应到达此处
    raise RuntimeError("_run_single_export 未能完成（未知状态）")


# ─────────────────────────────────────────────────────────────────────────────
# 3. 调度器：根据 export_mode 路由到单文件 / 分块
# ─────────────────────────────────────────────────────────────────────────────

async def run_export_job(job_id: str, config: Dict[str, Any]) -> None:
    """
    后台协程包装器：将同步阻塞的导出工作提交到线程池执行。

    config 结构：
    {
        "query_sql": str,
        "connection_env": str,
        "connection_type": str,         # 默认 "clickhouse"
        "batch_size": int,              # 默认 50_000
        "output_path": str,             # 单文件模式：输出文件绝对路径
        "output_filename": str,         # 单文件模式：文件名（展示用）
        # 分块模式（当存在 chunk_config 时启用）
        "export_mode": "single" | "date_chunked",
        "chunk_config": {
            "date_column": str | None,
            "date_start": "YYYY-MM-DD",
            "date_end": "YYYY-MM-DD",
            "chunk_days": int,
            "mode": "placeholder" | "wrapper",
        },
        "output_dir": str,              # 分块模式：输出目录绝对路径
        "job_name": str,                # 分块模式：用于生成子文件名
    }
    """
    loop = asyncio.get_event_loop()
    mode = config.get("export_mode", "single")
    if mode == "date_chunked":
        await loop.run_in_executor(_EXPORT_EXECUTOR, _run_chunked_export_sync, job_id, config)
    else:
        await loop.run_in_executor(_EXPORT_EXECUTOR, _run_single_job_sync, job_id, config)


def _mark_running(job_id: str) -> bool:
    """
    把 Job 从 pending 标记为 running（同时检查启动竞态：cancelling → 直接 cancelled）。
    返回 True 表示可继续执行；False 表示已终止（cancelled 或 not found）。
    """
    from backend.config.database import SessionLocal
    from backend.models.export_job import ExportJob
    db = SessionLocal()
    try:
        job = db.query(ExportJob).filter(ExportJob.id == job_id).first()
        if not job:
            logger.error("[ExportJob %s] Job not found, aborting.", job_id)
            return False
        if job.status == "cancelling":
            job.status = "cancelled"
            job.finished_at = datetime.utcnow()
            job.updated_at = datetime.utcnow()
            db.commit()
            logger.info("[ExportJob %s] Cancelled before start.", job_id)
            return False
        job.status = "running"
        job.started_at = datetime.utcnow()
        job.updated_at = datetime.utcnow()
        db.commit()
        return True
    finally:
        db.close()


def _mark_failed(job_id: str, msg: str, exported: int = 0, done_b: int = 0) -> None:
    _update_job(
        job_id,
        status="failed",
        finished_at=datetime.utcnow(),
        error_message=msg,
        exported_rows=exported,
        done_batches=done_b,
    )


def _mark_cancelled(job_id: str, exported: int = 0, done_b: int = 0, sheets: int = 0) -> None:
    _update_job(
        job_id,
        status="cancelled",
        finished_at=datetime.utcnow(),
        exported_rows=exported,
        done_batches=done_b,
        total_sheets=sheets,
    )


def _mark_partial_failed(
    job_id: str,
    summary: str,
    exported: int,
    done_b: int,
    total_b: Optional[int],
    sheets: int,
    file_size: int,
) -> None:
    """v2.14.7:partial_failed 终态 — 部分块成功 + 部分块失败。"""
    update_kw: Dict[str, Any] = dict(
        status="partial_failed",
        finished_at=datetime.utcnow(),
        exported_rows=exported,
        done_batches=done_b,
        total_sheets=sheets,
        file_size=file_size,
        error_message=summary,
    )
    if total_b is not None:
        update_kw["total_batches"] = total_b
    _update_job(job_id, **update_kw)


def _build_failure_summary(
    failed_summaries: List[Dict[str, Any]],
    *,
    total_chunks: int,
) -> str:
    """格式化失败块明细成单行 summary,塞进 error_message。"""
    n_failed = len(failed_summaries)
    if n_failed == 0:
        return ""
    parts: List[str] = []
    for f in failed_summaries[:5]:  # 最多列 5 个,余下用 ... 省略
        idx = f.get("index", "?")
        s, e = f.get("date_start", "?"), f.get("date_end", "?")
        err = (f.get("error") or "未知错误")[:160]
        parts.append(f"块 {idx + 1 if isinstance(idx, int) else idx} ({s}~{e}): {err}")
    extra = "" if n_failed <= 5 else f" ... 共 {n_failed} 个失败块,仅列前 5 个"
    return (
        f"{total_chunks} 块中 {n_failed} 块失败:\n"
        + "\n".join(parts)
        + extra
    )


def _mark_completed(
    job_id: str,
    exported: int,
    done_b: Optional[int],
    total_b: Optional[int],
    sheets: int,
    file_size: int,
) -> None:
    """
    标记 Job 完成。

    done_b/total_b 为 None 时不覆盖 — 用于单文件模式保留 inner 函数已写入的
    Python 批次计数（旧契约：done_batches=Python 批次数）。分块模式传具体值。
    """
    fields: Dict[str, Any] = {
        "status": "completed",
        "finished_at": datetime.utcnow(),
        "exported_rows": exported,
        "total_sheets": sheets,
        "file_size": file_size,
    }
    if done_b is not None:
        fields["done_batches"] = done_b
    if total_b is not None:
        fields["total_batches"] = total_b
    _update_job(job_id, **fields)


# ─────────────────────────────────────────────────────────────────────────────
# 4. 单文件模式协程
# ─────────────────────────────────────────────────────────────────────────────

def _run_single_job_sync(job_id: str, config: Dict[str, Any]) -> None:
    """单文件模式同步实现（线程池中运行）"""
    sql = config["query_sql"]
    env = config["connection_env"]
    conn_type = config.get("connection_type", "clickhouse")
    batch_size = config.get("batch_size", DEFAULT_BATCH_SIZE)
    output_path = config["output_path"]
    output_format = config.get("output_format", "xlsx")
    xlsx_engine = config.get("xlsx_engine", "direct")
    # v2.14.6: 单文件模式仅由 env var 控制(无 chunk_config 路径);chunked 模式还可
    # 从 chunk_config.prefer_chunked 单独覆盖。
    prefer_chunked = os.getenv("EXPORT_PREFER_CHUNKED", "0") == "1"

    if not _mark_running(job_id):
        return

    # 列信息预检（失败 → failed，不进入 _run_single_export 内部）
    try:
        client = _build_export_client(env, conn_type)
        client.get_columns(sql)
    except Exception as e:
        msg = f"获取列信息失败: {_humanize_error(e)}"
        logger.error("[ExportJob %s] %s", job_id, msg)
        _mark_failed(job_id, msg)
        return

    from backend.services.export_clients.clickhouse import is_ch_too_many_queries_error

    try:
        max_too_many_retry = int(os.getenv("EXPORT_TOO_MANY_RETRY_MAX", "5"))
        for attempt in range(max_too_many_retry + 1):
            try:
                result = _run_single_export(
                    job_id=job_id, sql=sql, env=env, conn_type=conn_type,
                    batch_size=batch_size, output_path=output_path,
                    output_format=output_format,
                    xlsx_engine=xlsx_engine,
                    query_id_prefix=f"dataagent_export:{job_id}:single:{attempt}",
                    prefer_chunked=prefer_chunked,
                )
                break
            except Exception as exc:
                if is_ch_too_many_queries_error(exc) and attempt < max_too_many_retry:
                    sleep_s = min(60, int(os.getenv("EXPORT_TOO_MANY_RETRY_BACKOFF", "10")) * (attempt + 1))
                    logger.warning(
                        "[ExportJob %s] Too many simultaneous queries, retry single export in %ss "
                        "(attempt %d/%d): %s",
                        job_id, sleep_s, attempt + 1, max_too_many_retry, exc,
                    )
                    try:
                        os.unlink(output_path)
                    except Exception:
                        pass
                    time.sleep(sleep_s)
                    continue
                raise
    except Exception as exc:
        msg = f"导出执行失败：{_humanize_error(exc)}"
        logger.error("[ExportJob %s] %s", job_id, msg, exc_info=True)
        _mark_failed(job_id, msg)
        try:
            os.unlink(output_path)
        except Exception:
            pass
        return

    if result["cancelled"]:
        _mark_cancelled(
            job_id,
            exported=result["exported_rows"],
            done_b=result.get("done_batches", 0),
            sheets=result["total_sheets"],
        )
        return

    # 单文件模式：done_batches=Python 批次数（旧契约）
    # 若发生 Code 160 回退，total_batches=SQL 分批数；否则保持 None
    _mark_completed(
        job_id,
        exported=result["exported_rows"],
        done_b=result.get("done_batches", 0),
        total_b=result.get("total_sql_chunks"),
        sheets=result["total_sheets"],
        file_size=result["file_size"],
    )


# ─────────────────────────────────────────────────────────────────────────────
# 5. 分块模式协程
# ─────────────────────────────────────────────────────────────────────────────

def _run_chunked_export_sync(job_id: str, config: Dict[str, Any]) -> None:
    """
    按日期分块导出 — 同步实现（线程池中运行）。

    流程：
      1. _mark_running（启动竞态检查）
      2. validate_chunk_config 校验
      3. split_date_range 切分块
      4. 逐块构建 SQL → 调用 _run_single_export → 累加进度 + output_files 清单
      5. 任一块失败 → 整体 failed（已完成块文件保留，便于排查）
      6. 中途 cancelling → 当前块写到一半保留，后续块跳过 → cancelled
    """
    from backend.services.data_export_chunker import (
        build_chunk_filename,
        inject_date_filter,
        split_date_range,
        subdivide_range,
        validate_chunk_config,
    )
    from backend.services.export_clients.clickhouse import (
        is_ch_timeout_estimate_error,
        is_transient_stream_error,
    )

    # 子块自动分裂的最大递归深度。
    # 日级:5 天 → 2,3 → 1,1,1,2 → 1,1,1,1,1（4 层即可降到 1 天）
    # 时间级（min_subdivide_unit ∈ {hour, minute}）:1 天 → 12h → 6h → 3h → 1.5h → 45min → 22min(6 层)
    # 总上限取并集:4(日级) + 6(时间级) = 10,既保留老行为又能覆盖 sub-day 下钻
    MAX_SUBDIVISION_DEPTH = 10
    # Task D:每个 chunk 失败后在原位重试一次再考虑分裂(应对瞬时网络抖动,
    # 避免立刻产生子文件)。可通过 EXPORT_INPLACE_RETRY_MAX 环境变量覆盖。
    MAX_INPLACE_RETRY = int(os.getenv("EXPORT_INPLACE_RETRY_MAX", "1"))
    # 重试前的退避(秒):attempt 1 → 5s, attempt 2 → 10s …,上限 30s。
    INPLACE_RETRY_BACKOFF_BASE = 5
    INPLACE_RETRY_BACKOFF_MAX = 30
    # v2.14.7:某块用尽所有重试 + 分裂仍失败时的行为开关。
    # 默认(="0"):继续跑剩余块,Job 终态 partial_failed(若至少 1 块成功)或 failed(全失败)。
    # opt-in 设为 "1":恢复老行为(整 Job 立即 failed),用于需要"全有或全无"语义的环境。
    FAIL_FAST_ON_CHUNK_ERROR = (
        os.getenv("EXPORT_FAIL_FAST_ON_CHUNK_ERROR", "0") == "1"
    )
    # 跨块累积失败记录,用于 partial_failed 终态生成 error_message
    failed_chunks_summary: List[Dict[str, Any]] = []

    def _sleep_with_cancel_check(seconds: int) -> bool:
        """按秒为粒度睡眠;每秒检查一次 cancel。返回 True 表示被取消(应中断)。"""
        import time
        for _ in range(seconds):
            if _is_cancelling(job_id):
                return True
            time.sleep(1)
        return False

    sql = config["query_sql"]
    env = config["connection_env"]
    conn_type = config.get("connection_type", "clickhouse")
    batch_size = config.get("batch_size", DEFAULT_BATCH_SIZE)
    output_dir = Path(config["output_dir"])
    job_name = config.get("job_name") or "export"
    raw_chunk_cfg = config["chunk_config"]
    output_format = config.get("output_format", "xlsx")
    xlsx_engine = config.get("xlsx_engine", "auto")
    if xlsx_engine == "auto":
        # 分块/大数据导出默认使用 CSV staging：ClickHouse 连接只负责快速落盘，
        # 后续本地转换 XLSX，避免客户端 XLSX 写入慢拖长远端查询连接。
        xlsx_engine = "csv_staging" if output_format == "xlsx" else "direct"

    if not _mark_running(job_id):
        return

    # 校验 + 切分
    try:
        ncfg = validate_chunk_config(raw_chunk_cfg, sql)
        chunks = split_date_range(ncfg.date_start, ncfg.date_end, ncfg.chunk_days)
    except Exception as e:
        msg = f"分块配置校验失败: {e}"
        logger.error("[ExportJob %s] %s", job_id, msg)
        _mark_failed(job_id, msg)
        return

    # v2.14.6: 解析 prefer_chunked
    #   ncfg.prefer_chunked is not None → 显式覆盖
    #   None                            → 用环境变量 EXPORT_PREFER_CHUNKED 全局默认
    prefer_chunked = (
        ncfg.prefer_chunked
        if ncfg.prefer_chunked is not None
        else os.getenv("EXPORT_PREFER_CHUNKED", "0") == "1"
    )
    if prefer_chunked:
        logger.info(
            "[ExportJob %s] chunked + prefer_chunked=True:跳过每块单流首试,"
            "直接走 %s",
            job_id, "keyset" if ncfg.cursor_column else "LIMIT/OFFSET",
        )

    output_dir.mkdir(parents=True, exist_ok=True)

    # 列预检（任何块失败前一次性确认 SQL 可用）
    try:
        first_sql, _ = inject_date_filter(
            sql, ncfg.date_column,
            chunks[0].start, chunks[0].end,
        )
        client = _build_export_client(env, conn_type)
        client.get_columns(first_sql)
    except Exception as e:
        msg = f"获取列信息失败：{_humanize_error(e)}"
        logger.error("[ExportJob %s] %s", job_id, msg)
        _mark_failed(job_id, msg)
        return

    # 初始化清单（以 list 形式，索引可变，支持中途插入子块）
    extension = "csv" if output_format in {"csv", "csv_zip"} else "xlsx"

    def _make_entry(start_d, end_d, depth: int = 0) -> Dict[str, Any]:
        fn = build_chunk_filename(job_name, start_d, end_d, extension=extension)
        return {
            "index": -1,  # 动态分配（subdivision 时会重排）
            "date_start": start_d.isoformat(),
            "date_end": end_d.isoformat(),
            "filename": fn,
            "file_path": str(output_dir / fn),
            "file_size": None,
            "rows": 0,
            "sheets": 0,
            "status": "pending",
            "_depth": depth,  # 内部字段，跟踪递归深度（不影响前端展示）
            "_retry_count": 0,  # Task D:在原位的重试次数(达到上限才考虑分裂)
        }

    def _duration_seconds(start_v, end_v) -> int:
        if isinstance(start_v, datetime):
            return int((end_v - start_v).total_seconds() + 1)
        return int(((end_v - start_v).days + 1) * 86400)

    def _fetch_auto_bucket_rows(count_sql: str) -> List[Tuple[Any, Any]]:
        from backend.config.settings import settings as app_settings
        from backend.services.export_clients.clickhouse import is_ch_too_many_queries_error

        rows: List[Tuple[Any, Any]] = []
        max_retry = int(os.getenv("EXPORT_TOO_MANY_RETRY_MAX", "5"))
        base_sleep = int(os.getenv("EXPORT_TOO_MANY_RETRY_BACKOFF", "10"))
        for attempt in range(max_retry + 1):
            try:
                with _get_export_query_semaphore(env):
                    for batch in client.stream_batches(
                        count_sql,
                        batch_size=10_000,
                        extra_settings={
                            "max_execution_time": app_settings.export_query_max_execution_time,
                        },
                        query_id_prefix=f"dataagent_export:{job_id}:auto_pre_split",
                    ):
                        rows.extend(batch)
                return rows
            except Exception as exc:
                if is_ch_too_many_queries_error(exc) and attempt < max_retry:
                    sleep_s = min(60, base_sleep * (attempt + 1))
                    logger.warning(
                        "[ExportJob %s] Auto pre-split count hit ClickHouse too many queries, "
                        "retry in %ss (attempt %d/%d): %s",
                        job_id, sleep_s, attempt + 1, max_retry, exc,
                    )
                    if _sleep_with_cancel_check(sleep_s):
                        raise RuntimeError("任务已取消")
                    continue
                raise
        return rows

    def _pre_split_ranges(start_v, end_v) -> List[Tuple[Any, Any]]:
        raw_hours = raw_chunk_cfg.get("pre_split_hours") if isinstance(raw_chunk_cfg, dict) else None
        if _is_auto_pre_split(raw_hours):
            if ncfg.min_subdivide_unit not in ("hour", "minute"):
                raise ValueError("pre_split_hours='auto' 仅在最小再细分粒度为 hour/minute 时可用")
            auto_col = (
                raw_chunk_cfg.get("auto_split_column")
                or raw_chunk_cfg.get("pre_split_time_column")
                or ncfg.date_column
            )
            auto_col = _validate_auto_split_column(auto_col or "")
            try:
                target_rows = int(raw_chunk_cfg.get("auto_split_target_rows") or MAX_ROWS_PER_SHEET)
            except Exception:
                target_rows = MAX_ROWS_PER_SHEET
            target_rows = max(1, target_rows)

            filtered_sql, _ = inject_date_filter(sql, ncfg.date_column, start_v, end_v)
            count_sql = _build_auto_pre_split_count_sql(
                filtered_sql,
                time_column=auto_col,
                unit=ncfg.min_subdivide_unit,
            )
            _update_job(
                job_id,
                current_sheet=(
                    f"自动预分窗口统计中（{start_v}~{end_v}, "
                    f"{ncfg.min_subdivide_unit}, target={target_rows:,} 行）"
                ),
            )
            bucket_rows = _fetch_auto_bucket_rows(count_sql)
            windows = _auto_bucket_rows_to_windows(
                bucket_rows,
                unit=ncfg.min_subdivide_unit,
                target_rows=target_rows,
            )
            if not windows:
                # auto 模式：本块整段范围都没有数据 → 不生成空文件(避免「仅表头」XLSX)。
                # 上层 _pre_split_ranges 返回 [] 后,output_files 不会为这个 chunk
                # 添加 entry;多 chunk 拼接时也只对应当前 chunk 跳过,其他 chunk 不受影响。
                # 整个 Job 所有 chunk 都为空 → output_files=[] → _mark_completed(0,0,0,0,0),
                # 调用方在前端看到 0 文件即可识别「无数据」。
                logger.info(
                    "[ExportJob %s] Auto pre-split: %s~%s 无数据,跳过该范围不生成文件",
                    job_id, start_v, end_v,
                )
                return []
            logger.info(
                "[ExportJob %s] Auto pre-split %s~%s by %s counts: %d bucket(s) -> %d window(s), "
                "target_rows=%d",
                job_id, start_v, end_v, ncfg.min_subdivide_unit,
                len(bucket_rows), len(windows), target_rows,
            )
            return [(s, e) for s, e, _rows in windows]

        if raw_hours is None:
            raw_hours = os.getenv("EXPORT_PRE_SPLIT_HOURS")
        if raw_hours in (None, "", 0, "0"):
            # 默认:只有用户启用 hour/minute 级再细分时，预拆成 6h 窗口，直接减少 5 分钟断流试错成本。
            raw_hours = 6 if ncfg.min_subdivide_unit in ("hour", "minute") else 0
        try:
            max_seconds = int(float(raw_hours) * 3600)
        except Exception:
            max_seconds = 0
        if max_seconds <= 0:
            return [(start_v, end_v)]
        pending = [(start_v, end_v)]
        out: List[Tuple[Any, Any]] = []
        while pending:
            s, e = pending.pop(0)
            if _duration_seconds(s, e) <= max_seconds:
                out.append((s, e))
                continue
            subs = subdivide_range(s, e, min_unit=ncfg.min_subdivide_unit)
            if len(subs) <= 1:
                out.append((s, e))
            else:
                pending = list(subs) + pending
        return out

    initial_ranges: List[Tuple[Any, Any]] = []
    for c in chunks:
        initial_ranges.extend(_pre_split_ranges(c.start, c.end))

    output_files: List[Dict[str, Any]] = [
        _make_entry(s, e) for s, e in initial_ranges
    ]
    # 重排索引
    for i, entry in enumerate(output_files):
        entry["index"] = i
    _update_job(
        job_id,
        total_batches=len(output_files),
        done_batches=0,
        output_files=list(output_files),  # 拷贝触发 JSONB diff
    )

    cumulative_rows = 0
    cumulative_sheets = 0

    # 索引迭代而非 for-each，便于中途插入子块再重试
    completed_count = 0
    cur_idx = 0
    while cur_idx < len(output_files):
        entry = output_files[cur_idx]
        chunk_start = _parse_iso_endpoint(entry["date_start"])
        chunk_end = _parse_iso_endpoint(entry["date_end"])
        # 用秒级 duration 统一衡量两种端点（date 视作整天 86400s）
        if isinstance(chunk_start, datetime):
            duration_seconds = (chunk_end - chunk_start).total_seconds() + 1
        else:
            duration_seconds = ((chunk_end - chunk_start).days + 1) * 86400
        cur_depth = entry.get("_depth", 0)

        # 启动每块前检查是否已被取消
        if _is_cancelling(job_id):
            logger.info(
                "[ExportJob %s] Cancelled before chunk %d/%d",
                job_id, cur_idx + 1, len(output_files),
            )
            _mark_cancelled(
                job_id,
                exported=cumulative_rows,
                done_b=completed_count,
                sheets=cumulative_sheets,
            )
            _update_job(job_id, output_files=list(output_files))
            return

        chunk_sql, _ = inject_date_filter(sql, ncfg.date_column, chunk_start, chunk_end)
        chunk_path = entry["file_path"]
        chunk_label = (
            f"块 {cur_idx + 1}/{len(output_files)} "
            f"({chunk_start}~{chunk_end})"
            + (f" [子块 L{cur_depth}]" if cur_depth > 0 else "")
        )

        # 标记本块为 running
        entry["status"] = "running"
        _update_job(
            job_id,
            current_sheet=chunk_label,
            output_files=list(output_files),
        )

        try:
            result = _run_single_export(
                job_id=job_id,
                sql=chunk_sql,
                env=env,
                conn_type=conn_type,
                batch_size=batch_size,
                output_path=chunk_path,
                sheet_prefix="Sheet",
                progress_offset=cumulative_rows,
                progress_total=len(output_files),
                chunk_label=chunk_label,
                cursor_column=ncfg.cursor_column,
                output_format=("csv" if output_format == "csv_zip" else output_format),
                xlsx_engine=xlsx_engine,
                query_id_prefix=f"dataagent_export:{job_id}:chunk:{cur_idx}",
                prefer_chunked=prefer_chunked,
            )
        except Exception as exc:
            # ── 自动日期再细分（v2.13 + v2.14 sub-day + Task D in-place retry）─
            # 触发条件：流式断开 / Code 160 / 且仍有可分裂空间 / 未达递归上限
            is_too_many = (
                "TOO_MANY_SIMULTANEOUS_QUERIES" in str(exc)
                or "Too many simultaneous queries" in str(exc)
                or "Code: 202" in str(exc)
            )
            if is_too_many:
                max_tm = int(os.getenv("EXPORT_TOO_MANY_RETRY_MAX", "5"))
                cur_tm = entry.get("_too_many_retry_count", 0)
                if cur_tm < max_tm:
                    entry["_too_many_retry_count"] = cur_tm + 1
                    backoff = min(
                        60,
                        int(os.getenv("EXPORT_TOO_MANY_RETRY_BACKOFF", "10")) * (cur_tm + 1),
                    )
                    logger.warning(
                        "[ExportJob %s] Chunk %d (%s~%s) hit ClickHouse too many queries, "
                        "%ds 后重试 (attempt %d/%d)",
                        job_id, cur_idx + 1, chunk_start, chunk_end,
                        backoff, cur_tm + 1, max_tm,
                    )
                    try:
                        os.unlink(chunk_path)
                    except Exception:
                        pass
                    entry["status"] = "pending"
                    _update_job(job_id, output_files=list(output_files))
                    if _sleep_with_cancel_check(backoff):
                        _mark_cancelled(
                            job_id, exported=cumulative_rows,
                            done_b=completed_count, sheets=cumulative_sheets,
                        )
                        _update_job(job_id, output_files=list(output_files))
                        return
                    continue

            is_retryable = (
                is_transient_stream_error(exc)
                or is_ch_timeout_estimate_error(exc)
            )

            # ── Task D:先在原位重试 1 次(应对瞬时网络抖动),失败再分裂 ───
            if is_retryable and entry.get("_retry_count", 0) < MAX_INPLACE_RETRY:
                entry["_retry_count"] = entry.get("_retry_count", 0) + 1
                backoff = min(
                    INPLACE_RETRY_BACKOFF_BASE * entry["_retry_count"],
                    INPLACE_RETRY_BACKOFF_MAX,
                )
                logger.warning(
                    "[ExportJob %s] Chunk %d (%s~%s) 失败 (%s), %ds 后原位重试 "
                    "(attempt %d/%d)",
                    job_id, cur_idx + 1, chunk_start, chunk_end,
                    type(exc).__name__, backoff,
                    entry["_retry_count"], MAX_INPLACE_RETRY,
                )
                # 删除可能已写入的部分文件
                try:
                    os.unlink(chunk_path)
                except FileNotFoundError:
                    pass
                except Exception:
                    pass
                # 状态回到 pending(展示用),持久化重试计数
                entry["status"] = "pending"
                _update_job(job_id, output_files=list(output_files))
                # 退避期间分段检查取消
                if _sleep_with_cancel_check(backoff):
                    _mark_cancelled(
                        job_id, exported=cumulative_rows,
                        done_b=completed_count, sheets=cumulative_sheets,
                    )
                    _update_job(job_id, output_files=list(output_files))
                    return
                # 不前进 cur_idx,下次循环重跑本块
                continue

            # 调用 chunker 的通用 subdivide_range,若返回单元素列表说明已不可再分
            sub_ranges = subdivide_range(
                chunk_start, chunk_end,
                min_unit=ncfg.min_subdivide_unit,
            ) if is_retryable else [(chunk_start, chunk_end)]
            can_subdivide = (
                is_retryable
                and len(sub_ranges) > 1
                and cur_depth < MAX_SUBDIVISION_DEPTH
            )

            if can_subdivide:
                logger.warning(
                    "[ExportJob %s] Chunk %s~%s (depth=%d, unit=%s) 失败 (%s) "
                    "— 自动对半分裂为更小子块重试",
                    job_id, chunk_start, chunk_end, cur_depth,
                    ncfg.min_subdivide_unit, type(exc).__name__,
                )
                # 删除可能已写入的部分文件
                try:
                    os.unlink(chunk_path)
                except FileNotFoundError:
                    pass
                except Exception:
                    pass

                sub_entries = [
                    _make_entry(s, e, depth=cur_depth + 1)
                    for s, e in sub_ranges
                ]
                # 原位替换：output_files[cur_idx] → sub_entries（不前进 cur_idx）
                output_files = (
                    output_files[:cur_idx]
                    + sub_entries
                    + output_files[cur_idx + 1:]
                )
                # 重排索引
                for i, e in enumerate(output_files):
                    e["index"] = i
                _update_job(
                    job_id,
                    total_batches=len(output_files),
                    output_files=list(output_files),
                )
                # 不前进 cur_idx，下次循环执行第一个子块
                continue

            # 不可重试 / 不可再分 → 老行为是整 Job failed;v2.14.7 起默认改成
            # 「跳过这块继续做剩下」,job 终态在 while 退出后按计数判定 partial_failed/failed/completed。
            err_msg = _humanize_error(exc)
            msg = (
                f"块 {cur_idx + 1}/{len(output_files)} "
                f"({chunk_start}~{chunk_end}) 执行失败：{err_msg}"
            )
            logger.error("[ExportJob %s] %s", job_id, msg, exc_info=True)
            entry["status"] = "failed"
            entry["error_summary"] = err_msg[:200]
            failed_chunks_summary.append({
                "index": entry["index"],
                "date_start": entry["date_start"],
                "date_end": entry["date_end"],
                "error": err_msg[:200],
            })
            # 清理可能写出的部分文件,避免下载时下到半残 xlsx
            try:
                os.unlink(chunk_path)
            except FileNotFoundError:
                pass
            except Exception:
                pass

            if FAIL_FAST_ON_CHUNK_ERROR:
                # opt-in 老行为:立即整 Job failed
                _mark_failed(
                    job_id, msg,
                    exported=cumulative_rows,
                    done_b=completed_count,
                )
                _update_job(job_id, output_files=list(output_files))
                return

            # v2.14.7 默认:跳过此块继续做下一个
            _update_job(job_id, output_files=list(output_files))
            cur_idx += 1
            continue

        chunk_size_bytes = (
            Path(chunk_path).stat().st_size
            if Path(chunk_path).exists() else None
        )

        if result["cancelled"]:
            entry.update({
                "status": "cancelled",
                "rows": result["exported_rows"],
                "sheets": result["total_sheets"],
                "file_size": chunk_size_bytes,
            })
            cumulative_rows += result["exported_rows"]
            cumulative_sheets += result["total_sheets"]
            _mark_cancelled(
                job_id,
                exported=cumulative_rows,
                done_b=completed_count,
                sheets=cumulative_sheets,
            )
            _update_job(job_id, output_files=list(output_files))
            return

        # 正常完成本块
        entry.update({
            "status": "completed",
            "rows": result["exported_rows"],
            "sheets": result["total_sheets"],
            "file_size": chunk_size_bytes,
        })
        cumulative_rows += result["exported_rows"]
        cumulative_sheets += result["total_sheets"]
        completed_count += 1

        _update_job(
            job_id,
            exported_rows=cumulative_rows,
            done_batches=completed_count,
            total_sheets=cumulative_sheets,
            output_files=list(output_files),
        )
        cur_idx += 1

    # 全部块跑完(成功 + 失败混合都算)。v2.14.7 起按计数判定终态。
    n_completed = sum(1 for f in output_files if f.get("status") == "completed")
    n_failed = sum(1 for f in output_files if f.get("status") == "failed")

    total_size = sum((f.get("file_size") or 0) for f in output_files)
    if output_format == "csv_zip":
        # 仅打包已成功的块;partial_failed 时文件名前缀 partial_ 提醒不完整
        zip_path = config.get("zip_output_path") or str(output_dir.with_suffix(".zip"))
        zip_name = config.get("zip_output_filename") or Path(zip_path).name
        if n_failed > 0 and n_completed > 0:
            zp = Path(zip_path)
            zip_path = str(zp.with_name(f"partial_{zp.name}"))
            zip_name = f"partial_{zip_name}"
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
            for f in output_files:
                if f.get("status") == "completed" and f.get("file_path") and Path(f["file_path"]).exists():
                    zf.write(f["file_path"], arcname=f.get("filename") or Path(f["file_path"]).name)
        total_size = Path(zip_path).stat().st_size
        _update_job(job_id, file_path=zip_path, output_filename=zip_name)

    if n_failed == 0:
        # 老行为:全成功
        _mark_completed(
            job_id,
            exported=cumulative_rows,
            done_b=len(output_files),
            total_b=len(output_files),
            sheets=cumulative_sheets,
            file_size=total_size,
        )
        _update_job(job_id, output_files=list(output_files))
        logger.info(
            "[ExportJob %s] Chunked export completed: %d files, %d rows total, %.1f MB"
            " (含自动分裂的子块: %d)",
            job_id, len(output_files), cumulative_rows, total_size / 1024 / 1024,
            sum(1 for f in output_files if f.get("_depth", 0) > 0),
        )
    elif n_completed == 0:
        # 全部失败 → 老 failed 终态
        summary = _build_failure_summary(failed_chunks_summary, total_chunks=len(output_files))
        _mark_failed(
            job_id, summary or "全部块均失败",
            exported=cumulative_rows,
            done_b=completed_count,
        )
        _update_job(job_id, output_files=list(output_files))
        logger.error(
            "[ExportJob %s] Chunked export failed: 全部 %d 块失败",
            job_id, len(output_files),
        )
    else:
        # partial_failed:部分成功 + 部分失败
        summary = _build_failure_summary(failed_chunks_summary, total_chunks=len(output_files))
        _mark_partial_failed(
            job_id,
            summary=summary,
            exported=cumulative_rows,
            done_b=completed_count,
            total_b=len(output_files),
            sheets=cumulative_sheets,
            file_size=total_size,
        )
        _update_job(job_id, output_files=list(output_files))
        logger.warning(
            "[ExportJob %s] Chunked export partial_failed: %d/%d 块成功, %d 块失败",
            job_id, n_completed, len(output_files), n_failed,
        )


# ─────────────────────────────────────────────────────────────────────────────
# 6. 失败子任务批量重试
# ─────────────────────────────────────────────────────────────────────────────

def _retry_failed_chunks_sync(job_id: str, batch_size: int) -> None:
    """
    对整个 date_chunked 任务下所有 failed 块串行重试（线程池中运行）。

    状态流转：
      job: partial_failed / failed → running → completed / partial_failed / failed
      chunk: failed → running → completed / failed
    """
    from backend.config.database import SessionLocal
    from backend.models.export_job import ExportJob
    from backend.services.data_export_chunker import inject_date_filter

    # ── 1. 加载并加锁 ────────────────────────────────────────────────────────
    db = SessionLocal()
    try:
        job = db.query(ExportJob).filter(ExportJob.id == job_id).first()
        if not job:
            logger.error("[RetryChunks %s] Job not found, aborting.", job_id)
            return
        if job.status == "running":
            logger.warning("[RetryChunks %s] Job already running, skipping retry.", job_id)
            return
        if job.status not in ("partial_failed", "failed"):
            logger.warning(
                "[RetryChunks %s] Job status '%s' not retryable, skipping.",
                job_id, job.status,
            )
            return
        # 原子加锁：设为 running，防止并发重试
        job.status = "running"
        job.finished_at = None
        job.updated_at = datetime.utcnow()
        db.commit()
    finally:
        db.close()

    # ── 2. 读取重试所需参数 ───────────────────────────────────────────────────
    db = SessionLocal()
    try:
        job = db.query(ExportJob).filter(ExportJob.id == job_id).first()
        sql = job.query_sql
        env = job.connection_env
        conn_type = job.connection_type or "clickhouse"
        job_name = job.job_name or "export"
        chunk_cfg = job.chunk_config or {}
        date_column = chunk_cfg.get("date_column")
        cfg_snapshot = job.config_snapshot or {}
        output_format = cfg_snapshot.get("output_format", "xlsx")
        xlsx_engine = cfg_snapshot.get("xlsx_engine", "auto")
        if xlsx_engine == "auto":
            xlsx_engine = "csv_staging"
        cursor_column = chunk_cfg.get("cursor_column")
        prefer_chunked_env = os.getenv("EXPORT_PREFER_CHUNKED", "0") == "1"
        prefer_chunked = chunk_cfg.get("prefer_chunked")
        if prefer_chunked is None:
            prefer_chunked = prefer_chunked_env

        output_files: List[Dict[str, Any]] = list(job.output_files or [])
    finally:
        db.close()

    # ── 3. 收集失败块（按 index 升序） ───────────────────────────────────────
    failed_entries = sorted(
        [e for e in output_files if e.get("status") == "failed"],
        key=lambda e: e.get("index", 0),
    )
    if not failed_entries:
        logger.info("[RetryChunks %s] No failed chunks found, restoring status.", job_id)
        n_comp = sum(1 for e in output_files if e.get("status") == "completed")
        if n_comp == len(output_files):
            _mark_completed(
                job_id,
                exported=sum(e.get("rows", 0) for e in output_files),
                done_b=len(output_files),
                total_b=len(output_files),
                sheets=sum(e.get("sheets", 0) for e in output_files),
                file_size=sum((e.get("file_size") or 0) for e in output_files),
            )
        else:
            _mark_partial_failed(
                job_id,
                summary="无失败块（重试时已为空）",
                exported=sum(e.get("rows", 0) for e in output_files),
                done_b=n_comp,
                total_b=len(output_files),
                sheets=sum(e.get("sheets", 0) for e in output_files),
                file_size=sum((e.get("file_size") or 0) for e in output_files),
            )
        return

    # output_dir 从任意块的 file_path 推导（所有块同目录）
    output_dir = Path(failed_entries[0]["file_path"]).parent

    logger.info(
        "[RetryChunks %s] 开始串行重试 %d 个失败块 (batch_size=%d)",
        job_id, len(failed_entries), batch_size,
    )

    # ── 4. 逐块串行重试 ──────────────────────────────────────────────────────
    for entry in failed_entries:
        # 4a. 检查取消信号
        if _is_cancelling(job_id):
            logger.info("[RetryChunks %s] Cancelled during retry.", job_id)
            _mark_cancelled(
                job_id,
                exported=sum(e.get("rows", 0) for e in output_files if e.get("status") == "completed"),
                done_b=sum(1 for e in output_files if e.get("status") == "completed"),
                sheets=sum(e.get("sheets", 0) for e in output_files if e.get("status") == "completed"),
            )
            _update_job(job_id, output_files=list(output_files))
            return

        chunk_start = _parse_iso_endpoint(entry["date_start"])
        chunk_end = _parse_iso_endpoint(entry["date_end"])
        chunk_label = f"重试块 {entry['index'] + 1}/{len(output_files)} ({chunk_start}~{chunk_end})"
        chunk_path = entry["file_path"]

        # 4b. 标记为 running，持久化
        entry["status"] = "running"
        entry.pop("error_summary", None)
        _update_job(
            job_id,
            current_sheet=chunk_label,
            output_files=list(output_files),
        )

        # 4c. 构建该块 SQL（注入日期过滤）
        try:
            chunk_sql, _ = inject_date_filter(sql, date_column, chunk_start, chunk_end)
        except Exception as exc:
            logger.error("[RetryChunks %s] inject_date_filter failed for chunk %d: %s", job_id, entry["index"], exc)
            entry["status"] = "failed"
            entry["error_summary"] = f"日期过滤注入失败: {exc}"[:200]
            _update_job(job_id, output_files=list(output_files))
            continue

        # 4d. 清理可能残留的部分文件
        try:
            Path(chunk_path).unlink(missing_ok=True)
        except Exception:
            pass

        # 4e. progress_offset = 已完成块的行数之和（排除当前块）
        progress_offset = sum(
            e.get("rows", 0) for e in output_files if e.get("status") == "completed"
        )

        # 4f. 执行单块导出
        try:
            result = _run_single_export(
                job_id=job_id,
                sql=chunk_sql,
                env=env,
                conn_type=conn_type,
                batch_size=batch_size,
                output_path=chunk_path,
                sheet_prefix="Sheet",
                progress_offset=progress_offset,
                progress_total=len(output_files),
                chunk_label=chunk_label,
                cursor_column=cursor_column,
                output_format=("csv" if output_format == "csv_zip" else output_format),
                xlsx_engine=xlsx_engine,
                query_id_prefix=f"dataagent_export:{job_id}:retry:{entry['index']}",
                prefer_chunked=prefer_chunked,
            )
            # 成功
            entry["status"] = "completed"
            entry["rows"] = result.get("exported_rows", 0)
            entry["sheets"] = result.get("total_sheets", 0)
            try:
                entry["file_size"] = Path(chunk_path).stat().st_size
            except Exception:
                entry["file_size"] = None
            logger.info(
                "[RetryChunks %s] Chunk %d 重试成功: %d 行",
                job_id, entry["index"], entry["rows"],
            )
        except Exception as exc:
            err_msg = _humanize_error(exc)
            entry["status"] = "failed"
            entry["error_summary"] = err_msg[:200]
            logger.warning(
                "[RetryChunks %s] Chunk %d 重试失败: %s",
                job_id, entry["index"], err_msg,
            )

        # 4g. 每块完成后持久化进度
        n_comp_now = sum(1 for e in output_files if e.get("status") == "completed")
        cum_rows = sum(e.get("rows", 0) for e in output_files if e.get("status") == "completed")
        cum_sheets = sum(e.get("sheets", 0) for e in output_files if e.get("status") == "completed")
        _update_job(
            job_id,
            exported_rows=cum_rows,
            done_batches=n_comp_now,
            total_sheets=cum_sheets,
            output_files=list(output_files),
        )

    # ── 5. 全部失败块处理完毕，重新统计终态 ──────────────────────────────────
    n_completed = sum(1 for e in output_files if e.get("status") == "completed")
    n_failed = sum(1 for e in output_files if e.get("status") == "failed")
    total_size = sum((e.get("file_size") or 0) for e in output_files)
    cum_rows = sum(e.get("rows", 0) for e in output_files if e.get("status") == "completed")
    cum_sheets = sum(e.get("sheets", 0) for e in output_files if e.get("status") == "completed")

    # csv_zip 模式：重新打包（覆盖旧 zip）
    if output_format == "csv_zip":
        import zipfile as _zipfile
        zip_path = str(output_dir.parent / (output_dir.name + ".zip"))
        zip_name = output_dir.name + ".zip"
        if n_failed > 0 and n_completed > 0:
            zip_path = str(output_dir.parent / f"partial_{output_dir.name}.zip")
            zip_name = f"partial_{output_dir.name}.zip"
        with _zipfile.ZipFile(zip_path, "w", compression=_zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
            for e in output_files:
                if e.get("status") == "completed" and e.get("file_path") and Path(e["file_path"]).exists():
                    zf.write(e["file_path"], arcname=e.get("filename") or Path(e["file_path"]).name)
        total_size = Path(zip_path).stat().st_size
        _update_job(job_id, file_path=zip_path, output_filename=zip_name)

    _update_job(job_id, output_files=list(output_files))

    if n_failed == 0:
        _mark_completed(
            job_id,
            exported=cum_rows,
            done_b=n_completed,
            total_b=len(output_files),
            sheets=cum_sheets,
            file_size=total_size,
        )
        _update_job(job_id, error_message=None)
        logger.info(
            "[RetryChunks %s] 全部块重试成功: %d 个块, %d 行",
            job_id, n_completed, cum_rows,
        )
    elif n_completed == 0:
        failed_summaries = [
            {"index": e.get("index"), "date_start": e.get("date_start"),
             "date_end": e.get("date_end"), "error": e.get("error_summary", "")}
            for e in output_files if e.get("status") == "failed"
        ]
        summary = _build_failure_summary(failed_summaries, total_chunks=len(output_files))
        _mark_failed(job_id, summary or "全部块重试均失败", exported=0, done_b=0)
        logger.error("[RetryChunks %s] 全部块重试失败", job_id)
    else:
        failed_summaries = [
            {"index": e.get("index"), "date_start": e.get("date_start"),
             "date_end": e.get("date_end"), "error": e.get("error_summary", "")}
            for e in output_files if e.get("status") == "failed"
        ]
        summary = _build_failure_summary(failed_summaries, total_chunks=len(output_files))
        _mark_partial_failed(
            job_id,
            summary=summary,
            exported=cum_rows,
            done_b=n_completed,
            total_b=len(output_files),
            sheets=cum_sheets,
            file_size=total_size,
        )
        logger.warning(
            "[RetryChunks %s] 部分块重试成功: %d/%d 成功, %d 失败",
            job_id, n_completed, len(output_files), n_failed,
        )


async def retry_failed_chunks_async(job_id: str, batch_size: int) -> None:
    """异步包装：前置快速校验后提交线程池执行。"""
    from backend.config.database import SessionLocal
    from backend.models.export_job import ExportJob

    # 快速只读校验（避免无效入队）
    db = SessionLocal()
    try:
        job = db.query(ExportJob).filter(ExportJob.id == job_id).first()
        if not job:
            raise ValueError(f"任务不存在: {job_id}")
        if job.export_mode != "date_chunked":
            raise ValueError("仅 date_chunked 模式支持失败子任务重试")
        if job.status not in ("partial_failed", "failed"):
            raise ValueError(f"当前状态 '{job.status}' 不支持重试（需为 partial_failed 或 failed）")
        has_failed = any(e.get("status") == "failed" for e in (job.output_files or []))
        if not has_failed:
            raise ValueError("没有状态为 failed 的子任务")
    finally:
        db.close()

    loop = asyncio.get_event_loop()
    await loop.run_in_executor(_EXPORT_EXECUTOR, _retry_failed_chunks_sync, job_id, batch_size)
