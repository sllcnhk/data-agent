"""
报告表增强迁移脚本 (2026-04-13)

新增字段（均为 nullable，可幂等执行）：
  reports.username           VARCHAR(100)
  reports.refresh_token      VARCHAR(64)  UNIQUE
  reports.report_file_path   TEXT
  reports.llm_summary        TEXT
  reports.summary_status     VARCHAR(20)

执行方式：
  cd data-agent
  /d/ProgramData/Anaconda3/envs/dataagent/python.exe backend/scripts/migrate_reports_enhancement.py
"""
import os
import sys
from pathlib import Path

# 确保 backend 在 sys.path
_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "backend"))

import sqlalchemy as sa
from sqlalchemy import text
from backend.config.database import engine


COLUMNS = [
    ("username",          "VARCHAR(100)"),
    ("refresh_token",     "VARCHAR(64)"),
    ("report_file_path",  "TEXT"),
    ("llm_summary",       "TEXT"),
    ("summary_status",    "VARCHAR(20) DEFAULT 'pending'"),
]

INDEXES = [
    ("idx_reports_username",      "CREATE INDEX IF NOT EXISTS idx_reports_username ON reports (username)"),
    ("idx_reports_refresh_token", "CREATE UNIQUE INDEX IF NOT EXISTS idx_reports_refresh_token ON reports (refresh_token)"),
]


def run():
    with engine.connect() as conn:
        # 查询现有列
        existing_cols = {
            row[0]
            for row in conn.execute(text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'reports'"
            ))
        }

        added = []
        for col_name, col_def in COLUMNS:
            if col_name not in existing_cols:
                conn.execute(text(f"ALTER TABLE reports ADD COLUMN {col_name} {col_def}"))
                added.append(col_name)
                print(f"  [+] 新增列: reports.{col_name}")
            else:
                print(f"  [=] 已存在: reports.{col_name}")

        # 索引
        for idx_name, idx_sql in INDEXES:
            try:
                conn.execute(text(idx_sql))
                print(f"  [+] 索引: {idx_name}")
            except Exception as e:
                print(f"  [!] 索引创建跳过 ({idx_name}): {e}")

        conn.commit()

    print(f"\n迁移完成。新增列: {added or '无（已是最新）'}")


if __name__ == "__main__":
    run()
