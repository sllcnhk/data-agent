"""
按日期分块导出（date_chunked）迁移脚本 — v2.13

为 export_jobs 表新增三列（幂等）：
  - export_mode    VARCHAR(20)  NOT NULL DEFAULT 'single'
  - chunk_config   JSONB        NULL
  - output_files   JSONB        NULL

并将存量记录回填 export_mode='single'（兼容查询）。

使用方法：
    python -m backend.scripts.migrate_export_chunked
    # 或
    cd data-agent && python backend/scripts/migrate_export_chunked.py
"""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_ROOT))

import logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


_ALTER_STATEMENTS = [
    # PostgreSQL 9.6+ 支持 IF NOT EXISTS
    "ALTER TABLE export_jobs ADD COLUMN IF NOT EXISTS export_mode VARCHAR(20) NOT NULL DEFAULT 'single'",
    "ALTER TABLE export_jobs ADD COLUMN IF NOT EXISTS chunk_config JSONB",
    "ALTER TABLE export_jobs ADD COLUMN IF NOT EXISTS output_files JSONB",
]

_BACKFILL_SQL = "UPDATE export_jobs SET export_mode = 'single' WHERE export_mode IS NULL"


def run():
    from sqlalchemy import text
    from backend.config.database import engine
    import backend.models  # noqa — 注册模型

    logger.info("Migrating export_jobs for date_chunked export...")

    with engine.begin() as conn:
        for stmt in _ALTER_STATEMENTS:
            logger.info("  exec: %s", stmt)
            conn.execute(text(stmt))
        result = conn.execute(text(_BACKFILL_SQL))
        logger.info("  backfilled rows: %s", result.rowcount)

    logger.info("Migration complete.")


if __name__ == "__main__":
    run()
