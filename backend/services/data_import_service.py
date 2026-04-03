"""
数据导入服务

实现 Excel → ClickHouse 的核心业务逻辑：
- 可写连接枚举
- Schema/Table 查询
- Excel 文件预览解析
- 分批导入执行（abort on first batch failure）
"""
import asyncio
import logging
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# 每批默认行数（TabSeparated 格式下 5000 行仍是小请求，HTTP 往返次数少 5 倍）
DEFAULT_BATCH_SIZE = 5000
# 文件大小上限 100 MB
MAX_FILE_SIZE = 100 * 1024 * 1024
# 预览行数
PREVIEW_ROWS = 5


# ─────────────────────────────────────────────────────────────────────────────
# 辅助：构建 ClickHouseHTTPClient
# ─────────────────────────────────────────────────────────────────────────────

def _build_ch_client(env: str):
    """根据 env 构建 ClickHouseHTTPClient（admin 级别）"""
    from backend.config.settings import settings
    from backend.mcp.clickhouse.http_client import ClickHouseHTTPClient

    cfg = settings.get_clickhouse_config(env, level="admin")
    return ClickHouseHTTPClient(
        host=cfg["host"],
        port=cfg["http_port"],
        user=cfg["user"],
        password=cfg["password"],
        database=cfg["database"],
        timeout=60,
    )


# ─────────────────────────────────────────────────────────────────────────────
# 1. 连接列表
# ─────────────────────────────────────────────────────────────────────────────

def list_writable_connections() -> List[Dict[str, Any]]:
    """
    返回所有可写（admin 级别）的 ClickHouse 连接信息。
    过滤依据：MCPServerManager 中名称不含 -ro 的 clickhouse-* 连接。
    """
    from backend.config.settings import settings
    from backend.mcp.manager import get_mcp_manager

    manager = get_mcp_manager()
    result = []

    for name, server in manager.servers.items():
        # 只取 ClickHouse 服务，排除只读副本
        if not name.startswith("clickhouse-"):
            continue
        if name.endswith("-ro"):
            continue

        # 从 name 反推 env（clickhouse-sg-azure → sg-azure → sg_azure）
        env_dash = name[len("clickhouse-"):]   # 去掉前缀
        env = env_dash.replace("-", "_")       # 连字符→下划线

        cfg = settings.get_clickhouse_config(env, level="admin")
        result.append({
            "env": env,
            "server_name": name,
            "host": cfg["host"],
            "http_port": cfg["http_port"],
            "database": cfg["database"],
            "display_name": name,
        })

    return result


# ─────────────────────────────────────────────────────────────────────────────
# 2. Schema / Table 查询
# ─────────────────────────────────────────────────────────────────────────────

def list_databases(env: str) -> List[str]:
    """查询指定环境的数据库列表（排除系统库）"""
    client = _build_ch_client(env)
    rows = client.execute(
        "SELECT name FROM system.databases "
        "WHERE name NOT IN ('system', 'information_schema', 'INFORMATION_SCHEMA') "
        "ORDER BY name"
    )
    return [r[0] for r in rows]


def list_tables(env: str, database: str) -> List[str]:
    """查询指定环境和数据库的表列表"""
    client = _build_ch_client(env)
    rows = client.execute(
        f"SELECT name FROM system.tables "
        f"WHERE database = '{database}' ORDER BY name"
    )
    return [r[0] for r in rows]


def describe_table(env: str, database: str, table: str) -> List[Dict[str, str]]:
    """获取表字段信息（name, type）"""
    client = _build_ch_client(env)
    rows, col_types = client.execute(
        f"DESCRIBE TABLE `{database}`.`{table}`",
        with_column_types=True,
    )
    return [{"name": r[0], "type": r[1]} for r in rows]


# ─────────────────────────────────────────────────────────────────────────────
# 3. Excel 解析预览
# ─────────────────────────────────────────────────────────────────────────────

def parse_excel_preview(file_path: str) -> List[Dict[str, Any]]:
    """
    用 openpyxl 流式模式读取 Excel，返回每个 Sheet 的预览信息。

    Returns:
        [{
            "sheet_name": str,
            "row_count_estimate": int,    # 估算总行数（含表头）
            "preview_rows": [[cell, ...]],  # 前 PREVIEW_ROWS 行原始值
        }]
    """
    import openpyxl

    wb = openpyxl.load_workbook(file_path, read_only=True, data_only=True)
    sheets = []
    try:
        for sheet_name in wb.sheetnames:
            ws = wb[sheet_name]
            preview_rows: List[List] = []
            row_count = 0

            for row in ws.iter_rows(values_only=True):
                row_count += 1
                if row_count <= PREVIEW_ROWS:
                    preview_rows.append([
                        str(cell) if cell is not None else ""
                        for cell in row
                    ])

            sheets.append({
                "sheet_name": sheet_name,
                "row_count_estimate": row_count,
                "preview_rows": preview_rows,
            })
    finally:
        wb.close()

    return sheets


# ─────────────────────────────────────────────────────────────────────────────
# 4. 核心导入逻辑
# ─────────────────────────────────────────────────────────────────────────────

def _rows_to_values_clause(rows: List[Tuple]) -> str:
    """将行列表转换为 INSERT VALUES 子句"""
    def _fmt(v) -> str:
        if v is None:
            return "NULL"
        if isinstance(v, bool):
            return "1" if v else "0"
        if isinstance(v, (int, float)):
            return str(v)
        # 字符串：转义单引号
        return "'" + str(v).replace("'", "\\'") + "'"

    parts = []
    for row in rows:
        cols = ", ".join(_fmt(cell) for cell in row)
        parts.append(f"({cols})")
    return ", ".join(parts)


async def run_import_job(job_id: str, config: Dict[str, Any]) -> None:
    """
    后台协程：逐 Sheet 分批读取 Excel 并插入 ClickHouse。

    config 结构：
    {
        "file_path": str,
        "connection_env": str,
        "batch_size": int,
        "sheets": [
            {
                "sheet_name": str,
                "database": str,
                "table": str,
                "has_header": bool,
                "enabled": bool,
            }
        ],
        "db_session_factory": callable,   # SessionLocal
    }
    """
    import openpyxl
    from backend.config.database import SessionLocal
    from backend.models.import_job import ImportJob

    file_path = config["file_path"]
    env = config["connection_env"]
    batch_size = config.get("batch_size", DEFAULT_BATCH_SIZE)
    sheet_configs = [s for s in config["sheets"] if s.get("enabled", True)]

    def _get_job(db) -> Optional[ImportJob]:
        return db.query(ImportJob).filter(ImportJob.id == job_id).first()

    def _save(db, job: ImportJob):
        job.updated_at = datetime.utcnow()
        db.commit()

    # ── 标记开始 ──────────────────────────────────────────────────────────────
    db = SessionLocal()
    try:
        job = _get_job(db)
        if not job:
            logger.error("[ImportJob %s] Job not found, aborting.", job_id)
            return
        job.status = "running"
        job.started_at = datetime.utcnow()
        job.total_sheets = len(sheet_configs)
        _save(db, job)
    finally:
        db.close()

    client = _build_ch_client(env)
    errors: List[Dict] = []
    total_imported = 0
    total_batches_all = 0
    done_batches_all = 0

    # ── 快速估算总批次（用 max_row，不全量遍历文件）────────────────────────
    try:
        import openpyxl as _ox
        wb_scan = _ox.load_workbook(file_path, read_only=True, data_only=True)
        for sc in sheet_configs:
            ws = wb_scan[sc["sheet_name"]]
            row_count = ws.max_row or 0
            data_rows = max(row_count - (1 if sc.get("has_header", True) else 0), 0)
            batches = (data_rows + batch_size - 1) // batch_size if data_rows else 0
            total_batches_all += batches
        wb_scan.close()
    except Exception as e:
        logger.warning("[ImportJob %s] Pre-scan failed: %s", job_id, e)

    db = SessionLocal()
    try:
        job = _get_job(db)
        job.total_batches = total_batches_all
        _save(db, job)
    finally:
        db.close()

    # ── 逐 Sheet 导入 ─────────────────────────────────────────────────────────
    done_sheets = 0
    abort_flag = False

    def _is_cancelling() -> bool:
        """检查任务是否被请求取消（每批次调用一次）"""
        db = SessionLocal()
        try:
            j = _get_job(db)
            return j is not None and j.status == "cancelling"
        finally:
            db.close()

    def _mark_cancelled():
        db = SessionLocal()
        try:
            j = _get_job(db)
            if j:
                j.status = "cancelled"
                j.finished_at = datetime.utcnow()
                j.imported_rows = total_imported
                j.done_batches = done_batches_all
                _save(db, j)
        finally:
            db.close()

    for sc in sheet_configs:
        if abort_flag:
            break

        sheet_name = sc["sheet_name"]
        database = sc["database"]
        table = sc["table"]
        has_header = sc.get("has_header", True)

        # 每个 sheet 开始前检查取消
        if _is_cancelling():
            logger.info("[ImportJob %s] Cancelled before sheet '%s'.", job_id, sheet_name)
            _mark_cancelled()
            return

        # 更新当前 sheet
        db = SessionLocal()
        try:
            job = _get_job(db)
            job.current_sheet = sheet_name
            _save(db, job)
        finally:
            db.close()

        logger.info("[ImportJob %s] Starting sheet '%s' → %s.%s", job_id, sheet_name, database, table)

        try:
            wb = openpyxl.load_workbook(file_path, read_only=True, data_only=True)
            ws = wb[sheet_name]
            batch_rows: List[Tuple] = []
            sheet_imported = 0
            sheet_batch_num = 0
            is_first_row = True
            _row_iter = ws.iter_rows(values_only=True)

            for row in _row_iter:
                # 跳过表头
                if is_first_row and has_header:
                    is_first_row = False
                    continue
                is_first_row = False

                batch_rows.append(row)

                if len(batch_rows) >= batch_size:
                    sheet_batch_num += 1
                    try:
                        client.insert_tsv(database, table, batch_rows)
                        sheet_imported += len(batch_rows)
                        total_imported += len(batch_rows)
                        done_batches_all += 1
                    except Exception as e:
                        err_msg = f"Sheet '{sheet_name}' 第 {sheet_batch_num} 批插入失败: {e}"
                        logger.error("[ImportJob %s] %s", job_id, err_msg)
                        errors.append({
                            "sheet": sheet_name,
                            "batch": sheet_batch_num,
                            "message": str(e),
                        })
                        abort_flag = True
                        # abort 策略：立即终止
                        db = SessionLocal()
                        try:
                            job = _get_job(db)
                            job.status = "failed"
                            job.error_message = err_msg
                            job.errors = errors
                            job.imported_rows = total_imported
                            job.done_batches = done_batches_all
                            job.finished_at = datetime.utcnow()
                            _save(db, job)
                        finally:
                            db.close()
                        _row_iter.close()  # 显式关闭生成器，释放 Windows 文件锁
                        wb.close()
                        break

                    batch_rows = []

                    # 每 10 批更新一次进度（减少 PostgreSQL round-trip）
                    if done_batches_all % 10 == 0:
                        db = SessionLocal()
                        try:
                            job = _get_job(db)
                            job.imported_rows = total_imported
                            job.done_batches = done_batches_all
                            _save(db, job)
                        finally:
                            db.close()

                    # 让出事件循环，避免阻塞
                    await asyncio.sleep(0)

                    # 每批检查是否被取消
                    if _is_cancelling():
                        logger.info("[ImportJob %s] Cancelled mid-sheet '%s' after %d rows.",
                                    job_id, sheet_name, sheet_imported)
                        _row_iter.close()
                        wb.close()
                        _mark_cancelled()
                        return

            if abort_flag:
                break

            # 尾部剩余行
            if batch_rows:
                sheet_batch_num += 1
                try:
                    client.insert_tsv(database, table, batch_rows)
                    sheet_imported += len(batch_rows)
                    total_imported += len(batch_rows)
                    done_batches_all += 1
                except Exception as e:
                    err_msg = f"Sheet '{sheet_name}' 第 {sheet_batch_num} 批插入失败: {e}"
                    logger.error("[ImportJob %s] %s", job_id, err_msg)
                    errors.append({"sheet": sheet_name, "batch": sheet_batch_num, "message": str(e)})
                    abort_flag = True
                    db = SessionLocal()
                    try:
                        job = _get_job(db)
                        job.status = "failed"
                        job.error_message = err_msg
                        job.errors = errors
                        job.imported_rows = total_imported
                        job.done_batches = done_batches_all
                        job.finished_at = datetime.utcnow()
                        _save(db, job)
                    finally:
                        db.close()
                    wb.close()
                    break

            wb.close()

        except Exception as e:
            err_msg = f"Sheet '{sheet_name}' 解析失败: {e}"
            logger.error("[ImportJob %s] %s", job_id, err_msg)
            errors.append({"sheet": sheet_name, "batch": 0, "message": str(e)})
            abort_flag = True
            db = SessionLocal()
            try:
                job = _get_job(db)
                job.status = "failed"
                job.error_message = err_msg
                job.errors = errors
                job.imported_rows = total_imported
                job.done_batches = done_batches_all
                job.finished_at = datetime.utcnow()
                _save(db, job)
            finally:
                db.close()
            break

        if not abort_flag:
            done_sheets += 1
            logger.info("[ImportJob %s] Sheet '%s' done: %d rows", job_id, sheet_name, sheet_imported)
            db = SessionLocal()
            try:
                job = _get_job(db)
                job.done_sheets = done_sheets
                job.imported_rows = total_imported
                job.done_batches = done_batches_all
                _save(db, job)
            finally:
                db.close()

    # ── 最终状态 ──────────────────────────────────────────────────────────────
    if not abort_flag:
        db = SessionLocal()
        try:
            job = _get_job(db)
            job.status = "completed"
            job.done_sheets = done_sheets
            job.imported_rows = total_imported
            job.done_batches = done_batches_all
            job.finished_at = datetime.utcnow()
            job.errors = errors if errors else None
            _save(db, job)
        finally:
            db.close()
        logger.info("[ImportJob %s] Completed: %d rows imported.", job_id, total_imported)

    # 清理临时文件
    try:
        os.unlink(file_path)
        logger.info("[ImportJob %s] Temp file removed: %s", job_id, file_path)
    except Exception:
        pass
