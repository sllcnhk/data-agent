"""
数据导出服务

实现 SQL → Excel 的核心业务逻辑：
- SQL 预览查询（前 N 行）
- 流式导出执行（HTTP 流 + openpyxl write-only）
- 多 Sheet 自动分割（每 100 万行新建一个 Sheet）
- 大整数安全转字符串（避免 Excel 科学计数法）
- 批次级取消检查（协作式取消）
- 分批提取模式（规避 ClickHouse max_execution_time 估算拒绝 Code 160）

线程安全说明：
  run_export_job 是 async 包装器，将同步阻塞工作交给线程池（run_in_executor）。
  这样 ClickHouse HTTP 流式读取（requests 同步库）和 openpyxl 写入不会阻塞 asyncio
  事件循环，使其他 API 请求（如任务列表轮询 GET /data-export/jobs）能正常响应。
"""
import asyncio
import logging
import math
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
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
) -> Dict[str, Any]:
    """
    执行 SQL（加 LIMIT），返回列信息和前 N 行数据，用于前端展示预览。

    Returns:
        {
            "columns": [{"name": str, "type": str}, ...],
            "rows": [[cell, ...], ...],
            "row_count": int,
        }
    """
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
        }
    raise ValueError(f"不支持的连接类型: {connection_type}")


# ─────────────────────────────────────────────────────────────────────────────
# 2. 核心导出协程
# ─────────────────────────────────────────────────────────────────────────────

async def run_export_job(job_id: str, config: Dict[str, Any]) -> None:
    """
    后台协程包装器：将同步阻塞的导出工作提交到线程池执行。

    ClickHouse HTTP 流式读取（requests 同步库）和 openpyxl 写入均为阻塞 I/O。
    若直接在协程中执行，会长时间占用 asyncio 事件循环，导致同期的 API 请求
    （如 GET /data-export/jobs 任务列表轮询）无法得到响应，触发 30s 超时。

    通过 run_in_executor 将阻塞工作移入独立线程，事件循环得以继续处理其他请求。
    """
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(_EXPORT_EXECUTOR, _run_export_job_sync, job_id, config)


def _run_export_job_sync(job_id: str, config: Dict[str, Any]) -> None:
    """
    同步实现（在线程池中运行，不阻塞 asyncio 事件循环）。

    执行策略（双层防护）：
      Layer 1 — Per-query max_execution_time 覆盖（应用层，不修改服务器配置）：
        所有导出查询均附带 max_execution_time=EXPORT_QUERY_MAX_EXECUTION_TIME（默认 300s）。
        该设置通过 HTTP URL 参数传递给 ClickHouse，仅对本次请求生效。

      Layer 2 — LIMIT/OFFSET 分批提取（自动触发）：
        触发条件 A：ClickHouse 返回 Code 160（ESTIMATED_EXECUTION_TIMEOUT_EXCEEDED）
        触发后：先 count_rows() 获取总行数，再按 EXPORT_CHUNK_SIZE（默认 200K）分窗提取。
        每个窗口查询同样携带 per-query max_execution_time 设置。

    config 结构：
    {
        "query_sql": str,
        "connection_env": str,
        "connection_type": str,         # 默认 "clickhouse"
        "batch_size": int,              # 默认 50_000
        "output_path": str,             # 输出文件绝对路径
        "output_filename": str,         # 文件名（展示用）
    }
    """
    import openpyxl
    from backend.config.database import SessionLocal
    from backend.config.settings import settings as app_settings
    from backend.models.export_job import ExportJob
    from backend.services.export_clients.clickhouse import is_ch_timeout_estimate_error

    sql = config["query_sql"]
    env = config["connection_env"]
    conn_type = config.get("connection_type", "clickhouse")
    batch_size = config.get("batch_size", DEFAULT_BATCH_SIZE)
    output_path = config["output_path"]

    # Per-query ClickHouse 设置（应用层覆盖，不修改服务器配置）
    export_settings = {"max_execution_time": app_settings.export_query_max_execution_time}

    def _get_job(db) -> Optional[ExportJob]:
        return db.query(ExportJob).filter(ExportJob.id == job_id).first()

    def _save(db, job: ExportJob):
        job.updated_at = datetime.utcnow()
        db.commit()

    # ── 标记开始（竞态检查：若已被取消则直接退出）────────────────────────────
    db = SessionLocal()
    try:
        job = _get_job(db)
        if not job:
            logger.error("[ExportJob %s] Job not found, aborting.", job_id)
            return
        if job.status == "cancelling":
            job.status = "cancelled"
            job.finished_at = datetime.utcnow()
            _save(db, job)
            logger.info("[ExportJob %s] Cancelled before start.", job_id)
            return
        job.status = "running"
        job.started_at = datetime.utcnow()
        _save(db, job)
    finally:
        db.close()

    def _is_cancelling() -> bool:
        db2 = SessionLocal()
        try:
            j = _get_job(db2)
            return j is not None and j.status == "cancelling"
        finally:
            db2.close()

    def _mark_cancelled(exported: int, done_b: int, sheets: int):
        db2 = SessionLocal()
        try:
            j = _get_job(db2)
            if j:
                j.status = "cancelled"
                j.finished_at = datetime.utcnow()
                j.exported_rows = exported
                j.done_batches = done_b
                j.total_sheets = sheets
                j.updated_at = datetime.utcnow()
                db2.commit()
        finally:
            db2.close()

    def _mark_failed(msg: str, exported: int, done_b: int):
        db2 = SessionLocal()
        try:
            j = _get_job(db2)
            if j:
                j.status = "failed"
                j.finished_at = datetime.utcnow()
                j.error_message = msg
                j.exported_rows = exported
                j.done_batches = done_b
                j.updated_at = datetime.utcnow()
                db2.commit()
        finally:
            db2.close()

    # ── 获取列信息 ────────────────────────────────────────────────────────────
    try:
        export_client = _build_export_client(env, conn_type)
        columns = export_client.get_columns(sql)
        col_names = [c.name for c in columns]
        col_types = [c.type for c in columns]
    except Exception as e:
        msg = f"获取列信息失败: {e}"
        logger.error("[ExportJob %s] %s", job_id, msg)
        _mark_failed(msg, 0, 0)
        return

    # ── 流式读取 + 写 Excel（含 Code 160 自动重试分批模式）────────────────────
    # 最多两轮：第一轮正常流式，第二轮（若触发）分批提取
    use_chunked = False
    total_sql_chunks = 1   # 分批模式下的 SQL 分批总数（进度分母）

    for attempt in range(2):
        if attempt == 1 and not use_chunked:
            break  # 第一轮成功，无需重试

        # ── 初始化 / 重置工作簿 ───────────────────────────────────────────────
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        wb = openpyxl.Workbook(write_only=True)
        sheet_num = 1
        ws = wb.create_sheet(f"Sheet{sheet_num}")
        ws.append(col_names)  # 表头行
        exported_rows = 0
        done_batches = 0       # Python 级别批次计数（每 batch_size 行 +1）
        done_sql_chunks = 0    # SQL 级别分批计数（仅分批模式有意义）
        sheet_row_count = 0

        # 更新列信息到 DB
        db = SessionLocal()
        try:
            job = _get_job(db)
            if job:
                job.current_sheet = f"Sheet{sheet_num}"
                _save(db, job)
        finally:
            db.close()

        # ── 分批模式：预扫描行数 → 决定分批数 ───────────────────────────────
        if use_chunked:
            try:
                total_rows = export_client.count_rows(
                    sql,
                    timeout=app_settings.export_query_max_execution_time,
                )
            except Exception as cnt_err:
                msg = f"分批模式预扫描行数失败: {cnt_err}"
                logger.error("[ExportJob %s] %s", job_id, msg)
                _mark_failed(msg, 0, 0)
                return

            total_sql_chunks = max(1, math.ceil(total_rows / app_settings.export_chunk_size))
            logger.info(
                "[ExportJob %s] Chunked mode: total_rows=%d → %d SQL chunks (chunk_size=%d)",
                job_id, total_rows, total_sql_chunks, app_settings.export_chunk_size,
            )
            db = SessionLocal()
            try:
                j = _get_job(db)
                if j:
                    j.total_rows = total_rows
                    j.total_batches = total_sql_chunks
                    j.updated_at = datetime.utcnow()
                    db.commit()
            finally:
                db.close()

            batch_source = export_client.stream_batches_chunked(
                sql,
                chunk_size=app_settings.export_chunk_size,
                total_rows=total_rows,
                batch_size=batch_size,
                extra_settings=export_settings,
            )
        else:
            batch_source = export_client.stream_batches(
                sql,
                batch_size=batch_size,
                extra_settings=export_settings,
            )

        # ── 消费批次 → 写入 Excel ─────────────────────────────────────────────
        try:
            for batch in batch_source:
                # 批次前检查取消
                if _is_cancelling():
                    logger.info(
                        "[ExportJob %s] Cancelled mid-export after %d rows.",
                        job_id, exported_rows,
                    )
                    try:
                        wb.save(output_path)
                    except Exception:
                        pass
                    _mark_cancelled(exported_rows, done_batches, sheet_num)
                    return

                for row in batch:
                    # 检查是否需要新建 Sheet
                    if sheet_row_count >= MAX_ROWS_PER_SHEET:
                        sheet_num += 1
                        ws = wb.create_sheet(f"Sheet{sheet_num}")
                        ws.append(col_names)  # 每个 Sheet 都有表头
                        sheet_row_count = 0
                        db = SessionLocal()
                        try:
                            j = _get_job(db)
                            if j:
                                j.current_sheet = f"Sheet{sheet_num}"
                                j.updated_at = datetime.utcnow()
                                db.commit()
                        finally:
                            db.close()

                    formatted = [_format_cell(v, col_types[i]) for i, v in enumerate(row)]
                    ws.append(formatted)
                    sheet_row_count += 1
                    exported_rows += 1

                done_batches += 1

                # 每 N 批更新进度
                if done_batches % PROGRESS_UPDATE_EVERY == 0:
                    db = SessionLocal()
                    try:
                        j = _get_job(db)
                        if j:
                            j.exported_rows = exported_rows
                            j.updated_at = datetime.utcnow()
                            if use_chunked:
                                # 分批模式：done_batches 对应已完成的 SQL 分批数
                                j.done_batches = exported_rows // app_settings.export_chunk_size
                                j.total_batches = total_sql_chunks
                            else:
                                j.done_batches = done_batches
                            db.commit()
                    finally:
                        db.close()

            # ── 保存文件 ──────────────────────────────────────────────────────
            wb.save(output_path)
            file_size = Path(output_path).stat().st_size
            logger.info(
                "[ExportJob %s] Completed: %d rows, %d sheet(s), %.1f MB, "
                "mode=%s chunks=%d",
                job_id, exported_rows, sheet_num, file_size / 1024 / 1024,
                "chunked" if use_chunked else "stream", total_sql_chunks,
            )

            db = SessionLocal()
            try:
                job = _get_job(db)
                if job:
                    job.status = "completed"
                    job.finished_at = datetime.utcnow()
                    job.exported_rows = exported_rows
                    job.done_batches = total_sql_chunks if use_chunked else done_batches
                    job.total_batches = total_sql_chunks
                    job.total_sheets = sheet_num
                    job.file_size = file_size
                    job.updated_at = datetime.utcnow()
                    db.commit()
            finally:
                db.close()

            break  # 成功，退出重试循环

        except RuntimeError as exc:
            # ── Code 160：ClickHouse 预估超时，切换分批模式重试 ──────────────
            if attempt == 0 and is_ch_timeout_estimate_error(exc):
                logger.warning(
                    "[ExportJob %s] Code 160 (estimated timeout), switching to chunked mode: %s",
                    job_id, exc,
                )
                use_chunked = True
                # 关闭已开始但未完成的工作簿（不保存）
                try:
                    wb.close()
                except Exception:
                    pass
                continue  # 进入第二轮（分批模式）

            # 其他错误 → 标记失败
            msg = f"导出执行失败: {exc}"
            logger.error("[ExportJob %s] %s", job_id, msg, exc_info=True)
            _mark_failed(msg, exported_rows, done_batches)
            try:
                os.unlink(output_path)
            except Exception:
                pass
            return

        except Exception as exc:
            msg = f"导出执行失败: {exc}"
            logger.error("[ExportJob %s] %s", job_id, msg, exc_info=True)
            _mark_failed(msg, exported_rows, done_batches)
            try:
                os.unlink(output_path)
            except Exception:
                pass
            return
