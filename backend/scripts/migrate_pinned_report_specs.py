"""
存量 pinned 报告 spec 回填脚本

背景
----
通过 POST /reports/pin 创建的报告记录（来自"生成固定报表"按钮）
在修复前 charts / filters / theme 字段均为 NULL。
本脚本扫描所有 charts IS NULL 的报告，尝试从对应 HTML 文件提取
window.REPORT_SPEC，将 charts / filters / theme 写回数据库。

使用方法
--------
cd data-agent
python backend/scripts/migrate_pinned_report_specs.py [--dry-run]

--dry-run  只打印计划，不写库
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# 确保项目根目录在 sys.path
_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(_ROOT))

import logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
logger = logging.getLogger("migrate_pinned_specs")


def main(dry_run: bool = False) -> None:
    from backend.config.database import get_db_context
    from backend.models.report import Report
    from backend.services.report_service import extract_spec_from_html_file
    from backend.config.settings import settings
    from sqlalchemy.orm.attributes import flag_modified

    customer_root = (
        Path(settings.allowed_directories[0])
        if settings.allowed_directories
        else Path("customer_data")
    )

    with get_db_context() as db:
        # 只处理 charts IS NULL 且有 HTML 路径的记录
        rows = (
            db.query(Report)
            .filter(Report.charts.is_(None), Report.report_file_path.isnot(None))
            .all()
        )

        logger.info("共找到 %d 条 charts=NULL 的报告记录", len(rows))

        updated = 0
        skipped_no_file = 0
        skipped_no_spec = 0

        for rpt in rows:
            abs_path = (customer_root / rpt.report_file_path).resolve()

            if not abs_path.exists():
                logger.warning(
                    "  [SKIP] id=%s  文件不存在: %s", rpt.id, rpt.report_file_path
                )
                skipped_no_file += 1
                continue

            spec = extract_spec_from_html_file(abs_path)
            if not spec:
                logger.warning(
                    "  [SKIP] id=%s  无法从 HTML 提取 spec: %s",
                    rpt.id, rpt.report_file_path,
                )
                skipped_no_spec += 1
                continue

            charts = spec.get("charts", [])
            filters = spec.get("filters", [])
            theme = spec.get("theme", "light")

            logger.info(
                "  [%s] id=%s  charts=%d  theme=%s  file=%s",
                "DRY" if dry_run else "UPDATE",
                rpt.id,
                len(charts),
                theme,
                rpt.report_file_path,
            )

            if not dry_run:
                rpt.charts = charts
                rpt.filters = filters
                rpt.theme = theme
                flag_modified(rpt, "charts")
                flag_modified(rpt, "filters")
                updated += 1

        if not dry_run:
            try:
                db.commit()
                logger.info("迁移完成: 更新=%d, 跳过(无文件)=%d, 跳过(无spec)=%d",
                            updated, skipped_no_file, skipped_no_spec)
            except Exception as e:
                db.rollback()
                logger.error("提交失败: %s", e)
                sys.exit(1)
        else:
            logger.info("DRY RUN 完成: 待更新=%d, 跳过(无文件)=%d, 跳过(无spec)=%d",
                        len(rows) - skipped_no_file - skipped_no_spec,
                        skipped_no_file, skipped_no_spec)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="回填 pinned 报告的 spec 数据")
    parser.add_argument("--dry-run", action="store_true", help="只打印计划，不写库")
    args = parser.parse_args()
    main(dry_run=args.dry_run)
