"""
数据导出服务

实现 SQL → Excel 的核心业务逻辑：
- SQL 预览查询（前 N 行）
- 流式导出执行（HTTP 流 + openpyxl write-only）
- 多 Sheet 自动分割（每 100 万行新建一个 Sheet）
- 大整数安全转字符串（避免 Excel 科学计数法）
- 批次级取消检查（协作式取消）
"""
import asyncio
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

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
    后台协程：流式读取 SQL 结果，写入 Excel，支持取消和多 Sheet 分割。

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
    from backend.models.export_job import ExportJob

    sql = config["query_sql"]
    env = config["connection_env"]
    conn_type = config.get("connection_type", "clickhouse")
    batch_size = config.get("batch_size", DEFAULT_BATCH_SIZE)
    output_path = config["output_path"]

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

    # ── 创建 openpyxl Write-Only Workbook ────────────────────────────────────
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    wb = openpyxl.Workbook(write_only=True)

    sheet_num = 1
    ws = wb.create_sheet(f"Sheet{sheet_num}")
    ws.append(col_names)  # 表头行

    exported_rows = 0
    done_batches = 0
    sheet_row_count = 0  # 当前 Sheet 已写数据行数（不含表头）

    # 更新列信息到 DB
    db = SessionLocal()
    try:
        job = _get_job(db)
        job.current_sheet = f"Sheet{sheet_num}"
        _save(db, job)
    finally:
        db.close()

    # ── 流式读取 + 写 Excel ───────────────────────────────────────────────────
    try:
        for batch in export_client.stream_batches(sql, batch_size=batch_size):
            # 批次前检查取消
            if _is_cancelling():
                logger.info("[ExportJob %s] Cancelled mid-export after %d rows.", job_id, exported_rows)
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

            # 让出事件循环
            await asyncio.sleep(0)

            # 每 N 批更新进度
            if done_batches % PROGRESS_UPDATE_EVERY == 0:
                db = SessionLocal()
                try:
                    j = _get_job(db)
                    if j:
                        j.exported_rows = exported_rows
                        j.done_batches = done_batches
                        j.updated_at = datetime.utcnow()
                        db.commit()
                finally:
                    db.close()

        # ── 保存文件 ──────────────────────────────────────────────────────────
        wb.save(output_path)
        file_size = Path(output_path).stat().st_size
        logger.info(
            "[ExportJob %s] Completed: %d rows, %d sheet(s), %.1f MB",
            job_id, exported_rows, sheet_num, file_size / 1024 / 1024,
        )

        db = SessionLocal()
        try:
            job = _get_job(db)
            job.status = "completed"
            job.finished_at = datetime.utcnow()
            job.exported_rows = exported_rows
            job.done_batches = done_batches
            job.total_sheets = sheet_num
            job.file_size = file_size
            job.updated_at = datetime.utcnow()
            db.commit()
        finally:
            db.close()

    except Exception as e:
        msg = f"导出执行失败: {e}"
        logger.error("[ExportJob %s] %s", job_id, msg, exc_info=True)
        _mark_failed(msg, exported_rows, done_batches)
        # 清理不完整的文件
        try:
            os.unlink(output_path)
        except Exception:
            pass
