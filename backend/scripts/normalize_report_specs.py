from __future__ import annotations

import argparse
import json
from typing import Any, Dict, List

from sqlalchemy.orm.attributes import flag_modified

from backend.config.database import get_db_context
from backend.models.report import Report
from backend.report_spec_utils import normalize_report_spec, summary_requested
from backend.services.report_builder_service import build_report_html
from backend.services.report_service import _api_base_url, _get_customer_data_root


def _report_spec(report: Report) -> Dict[str, Any]:
    return {
        "title": report.name or "????",
        "subtitle": report.description or "",
        "theme": report.theme or "light",
        "charts": report.charts or [],
        "filters": report.filters or [],
        "llm_summary": report.llm_summary or "",
        "include_summary": bool((report.extra_metadata or {}).get("include_summary")),
        "data_sources": report.data_sources or [],
    }


def _scan_rows(reports: List[Report]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for report in reports:
        normalized = normalize_report_spec(_report_spec(report))
        include_summary = bool((report.extra_metadata or {}).get("include_summary"))
        inferred_summary = summary_requested(normalized)
        changed = (
            normalized.get("charts", []) != (report.charts or [])
            or normalized.get("filters", []) != (report.filters or [])
            or include_summary != inferred_summary
        )
        if changed:
            rows.append({
                "id": str(report.id),
                "name": report.name,
                "file": report.report_file_path,
                "normalized_include_summary": inferred_summary,
            })
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description="Normalize saved report specs and optionally rewrite HTML")
    parser.add_argument("--apply", action="store_true", help="Persist normalized charts/filters/include_summary and rebuild HTML")
    args = parser.parse_args()

    with get_db_context() as db:
        reports = db.query(Report).order_by(Report.created_at.asc()).all()
        rows = _scan_rows(reports)
        print(json.dumps({"total_reports": len(reports), "issue_count": len(rows), "issues": rows}, ensure_ascii=False, indent=2))
        if not args.apply:
            return

        customer_root = _get_customer_data_root()
        updated = 0
        for report in reports:
            normalized = normalize_report_spec(_report_spec(report))
            include_summary = summary_requested(normalized)
            extra_meta = dict(report.extra_metadata or {})
            changed = (
                normalized.get("charts", []) != (report.charts or [])
                or normalized.get("filters", []) != (report.filters or [])
                or bool(extra_meta.get("include_summary")) != include_summary
            )
            if not changed:
                continue

            report.charts = normalized.get("charts", [])
            report.filters = normalized.get("filters", [])
            report.theme = normalized.get("theme", report.theme)
            extra_meta["include_summary"] = include_summary
            report.extra_metadata = extra_meta
            report.summary_status = "done" if report.llm_summary else ("pending" if include_summary else "skipped")
            flag_modified(report, "charts")
            flag_modified(report, "filters")
            flag_modified(report, "extra_metadata")

            if report.report_file_path:
                html = build_report_html(
                    spec=normalized,
                    report_id=str(report.id),
                    refresh_token=report.refresh_token or "",
                    api_base_url=_api_base_url(),
                )
                html_path = customer_root / report.report_file_path
                html_path.parent.mkdir(parents=True, exist_ok=True)
                html_path.write_text(html, encoding="utf-8")

            updated += 1

        db.commit()
        print(json.dumps({"updated": updated}, ensure_ascii=False))


if __name__ == "__main__":
    main()
