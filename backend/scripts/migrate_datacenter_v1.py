"""
migrate_datacenter_v1.py
========================
数据管理中心 v1 — 幂等 DB 迁移脚本

变更内容：
  1. reports 表新增字段：doc_type / scheduled_report_id / version_seq
  2. 新建 scheduled_reports 表
  3. 新建 schedule_run_logs 表
  4. 新建 notification_logs 表

执行方式（幂等，可重复运行）：
    cd data-agent
    /d/ProgramData/Anaconda3/envs/dataagent/python.exe backend/scripts/migrate_datacenter_v1.py
"""
from __future__ import annotations

import sys
import logging
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_ROOT))

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def _col_exists(conn, table: str, column: str) -> bool:
    from sqlalchemy import text
    result = conn.execute(
        text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = :t AND column_name = :c"
        ),
        {"t": table, "c": column},
    )
    return result.fetchone() is not None


def _table_exists(conn, table: str) -> bool:
    from sqlalchemy import text
    result = conn.execute(
        text(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name = :t"
        ),
        {"t": table},
    )
    return result.fetchone() is not None


def run():
    from backend.config.database import engine, Base

    # 注册所有模型（确保新表的 metadata 已导入）
    import backend.models  # noqa

    # 新模型单独导入（models/__init__ 已包含）
    from backend.models.scheduled_report import ScheduledReport   # noqa
    from backend.models.schedule_run_log import ScheduleRunLog   # noqa
    from backend.models.notification_log import NotificationLog  # noqa

    with engine.begin() as conn:
        from sqlalchemy import text

        # ── 1. reports 表新增字段 ────────────────────────────────────────────
        logger.info("[1/4] Patching 'reports' table ...")
        if not _col_exists(conn, "reports", "doc_type"):
            conn.execute(text(
                "ALTER TABLE reports ADD COLUMN doc_type VARCHAR(20) DEFAULT 'dashboard' NOT NULL"
            ))
            logger.info("  + reports.doc_type")
        else:
            logger.info("  ~ reports.doc_type already exists")

        if not _col_exists(conn, "reports", "scheduled_report_id"):
            conn.execute(text(
                "ALTER TABLE reports ADD COLUMN scheduled_report_id UUID"
            ))
            logger.info("  + reports.scheduled_report_id")
        else:
            logger.info("  ~ reports.scheduled_report_id already exists")

        if not _col_exists(conn, "reports", "version_seq"):
            conn.execute(text(
                "ALTER TABLE reports ADD COLUMN version_seq INTEGER DEFAULT 1"
            ))
            logger.info("  + reports.version_seq")
        else:
            logger.info("  ~ reports.version_seq already exists")

        # ── 2-4. 新建三张表（若不存在）─────────────────────────────────────
        logger.info("[2/4] Creating 'scheduled_reports' table ...")
        Base.metadata.tables["scheduled_reports"].create(bind=engine, checkfirst=True)
        logger.info("  scheduled_reports OK")

        logger.info("[3/4] Creating 'schedule_run_logs' table ...")
        Base.metadata.tables["schedule_run_logs"].create(bind=engine, checkfirst=True)
        logger.info("  schedule_run_logs OK")

        logger.info("[4/4] Creating 'notification_logs' table ...")
        Base.metadata.tables["notification_logs"].create(bind=engine, checkfirst=True)
        logger.info("  notification_logs OK")

    logger.info("Migration migrate_datacenter_v1 completed successfully.")


if __name__ == "__main__":
    run()
