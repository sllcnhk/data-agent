"""
scheduler_service.py
====================
基于 APScheduler 的定时报告调度服务。

功能：
  - 应用启动时加载所有 is_active=True 的 ScheduledReport，注册为 cron job
  - 支持动态增删改 job（配合 CRUD API）
  - cron job 执行时：生成报告 HTML → 写 DB → 触发通知发送 → 写 ScheduleRunLog
  - 使用 SQLAlchemyJobStore 持久化 job，进程重启后自动恢复

依赖：
  pip install apscheduler
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

_scheduler: Optional["BackgroundScheduler"] = None  # type: ignore[name-defined]


def get_scheduler():
    """返回全局 APScheduler 实例（懒初始化）。"""
    global _scheduler
    if _scheduler is None:
        try:
            from apscheduler.schedulers.background import BackgroundScheduler
            from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
            from apscheduler.executors.pool import ThreadPoolExecutor

            from backend.config.database import engine as _engine

            jobstores = {
                "default": SQLAlchemyJobStore(engine=_engine, tablename="apscheduler_jobs"),
            }
            executors = {"default": ThreadPoolExecutor(max_workers=4)}
            job_defaults = {"coalesce": True, "max_instances": 1, "misfire_grace_time": 300}

            _scheduler = BackgroundScheduler(
                jobstores=jobstores,
                executors=executors,
                job_defaults=job_defaults,
                timezone="Asia/Shanghai",
            )
        except ImportError:
            logger.warning(
                "[Scheduler] apscheduler 未安装，定时报告功能不可用。"
                " 请运行: pip install apscheduler"
            )
    return _scheduler


def start():
    """启动调度器并加载 DB 中所有活跃任务。"""
    sched = get_scheduler()
    if sched is None:
        return

    if not sched.running:
        sched.start()
        logger.info("[Scheduler] APScheduler started.")

    _load_all_active_jobs()


def shutdown():
    """优雅关闭调度器。"""
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("[Scheduler] APScheduler shut down.")


# ─────────────────────────────────────────────────────────────────────────────
# Job 管理
# ─────────────────────────────────────────────────────────────────────────────

def _job_id(scheduled_report_id: str) -> str:
    return f"sr_{scheduled_report_id}"


def add_or_update_job(scheduled_report) -> None:
    """将 ScheduledReport 注册/更新到 APScheduler。"""
    sched = get_scheduler()
    if sched is None:
        return
    if not scheduled_report.is_active:
        remove_job(str(scheduled_report.id))
        return

    jid = _job_id(str(scheduled_report.id))
    cron_parts = scheduled_report.cron_expr.split()
    if len(cron_parts) != 5:
        logger.error("[Scheduler] Invalid cron expression: %s", scheduled_report.cron_expr)
        return

    minute, hour, day, month, day_of_week = cron_parts

    try:
        from apscheduler.triggers.cron import CronTrigger

        trigger = CronTrigger(
            minute=minute,
            hour=hour,
            day=day,
            month=month,
            day_of_week=day_of_week,
            timezone=scheduled_report.timezone or "Asia/Shanghai",
        )

        sched.add_job(
            func=_execute_scheduled_report,
            trigger=trigger,
            id=jid,
            name=scheduled_report.name,
            args=[str(scheduled_report.id)],
            replace_existing=True,
        )
        logger.info("[Scheduler] Job registered: %s (%s)", jid, scheduled_report.cron_expr)

        # 更新 next_run_at
        _update_next_run_at(str(scheduled_report.id), jid)

    except Exception as e:
        logger.error("[Scheduler] Failed to add job %s: %s", jid, e)


def remove_job(scheduled_report_id: str) -> None:
    """从 APScheduler 移除 job（若存在）。"""
    sched = get_scheduler()
    if sched is None:
        return
    jid = _job_id(scheduled_report_id)
    try:
        sched.remove_job(jid)
        logger.info("[Scheduler] Job removed: %s", jid)
    except Exception:
        pass  # job 不存在时忽略


def pause_job(scheduled_report_id: str) -> None:
    sched = get_scheduler()
    if sched is None:
        return
    jid = _job_id(scheduled_report_id)
    try:
        sched.pause_job(jid)
        logger.info("[Scheduler] Job paused: %s", jid)
    except Exception:
        pass


def resume_job(scheduled_report_id: str) -> None:
    sched = get_scheduler()
    if sched is None:
        return
    jid = _job_id(scheduled_report_id)
    try:
        sched.resume_job(jid)
        logger.info("[Scheduler] Job resumed: %s", jid)
    except Exception:
        pass


def _update_next_run_at(scheduled_report_id: str, jid: str) -> None:
    """将 APScheduler 计算的 next_run_time 写回 DB。"""
    sched = get_scheduler()
    if sched is None:
        return
    try:
        job = sched.get_job(jid)
        if job and job.next_run_time:
            from backend.config.database import SessionLocal
            from backend.models.scheduled_report import ScheduledReport

            db = SessionLocal()
            try:
                uid = uuid.UUID(scheduled_report_id)
                sr = db.query(ScheduledReport).filter(ScheduledReport.id == uid).first()
                if sr:
                    sr.next_run_at = job.next_run_time.replace(tzinfo=None)
                    db.commit()
            finally:
                db.close()
    except Exception as e:
        logger.warning("[Scheduler] Could not update next_run_at: %s", e)


# ─────────────────────────────────────────────────────────────────────────────
# 执行体
# ─────────────────────────────────────────────────────────────────────────────

def _execute_scheduled_report(scheduled_report_id: str) -> None:
    """
    cron job 执行体（在 ThreadPoolExecutor 中运行，不是 async）。
    调用异步函数需通过 asyncio.run()。
    """
    import asyncio
    try:
        asyncio.run(_execute_async(scheduled_report_id))
    except RuntimeError:
        # 若事件循环已存在（少数情况），用 new_event_loop
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(_execute_async(scheduled_report_id))
        finally:
            loop.close()


async def _execute_async(scheduled_report_id: str) -> None:
    """异步执行定时报告生成 + 通知发送。"""
    from backend.config.database import SessionLocal
    from backend.models.scheduled_report import ScheduledReport
    from backend.models.schedule_run_log import ScheduleRunLog
    from backend.models.report import Report

    db = SessionLocal()
    run_log: Optional[ScheduleRunLog] = None
    start_ts = datetime.utcnow()

    try:
        uid = uuid.UUID(scheduled_report_id)
        sr = db.query(ScheduledReport).filter(ScheduledReport.id == uid).first()
        if not sr or not sr.is_active:
            logger.info("[Scheduler] ScheduledReport %s not found or inactive, skipping.", scheduled_report_id)
            return

        # 创建执行日志
        run_log = ScheduleRunLog(
            scheduled_report_id=uid,
            status="running",
            run_at=start_ts,
        )
        db.add(run_log)
        db.commit()
        db.refresh(run_log)

        # ── 生成报告 ──────────────────────────────────────────────────────
        from backend.services.report_builder_service import build_report_html, generate_refresh_token
        from backend.config.settings import settings
        from pathlib import Path
        import os

        report_id_str = str(uuid.uuid4())
        refresh_token = generate_refresh_token()
        spec = dict(sr.report_spec or {})
        spec["include_summary"] = sr.include_summary

        # 推断 api_base_url
        host = os.environ.get("PUBLIC_HOST", "")
        port = os.environ.get("PORT", "8000")
        api_base_url = f"{host}/api/v1" if host else f"http://localhost:{port}/api/v1"

        html_content = build_report_html(
            spec=spec,
            report_id=report_id_str,
            refresh_token=refresh_token,
            api_base_url=api_base_url,
        )

        # 写文件
        _root = (
            Path(settings.allowed_directories[0])
            if settings.allowed_directories
            else Path("customer_data")
        )
        report_dir = _root / sr.owner_username / "reports"
        report_dir.mkdir(parents=True, exist_ok=True)

        # 版本序号（同一 ScheduledReport 的历史版本）
        version_seq = sr.run_count + 1
        ts_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_name = _slugify(sr.name)[:30]
        filename = f"{safe_name}_v{version_seq}_{ts_str}.html"
        html_path = report_dir / filename
        html_path.write_text(html_content, encoding="utf-8")

        try:
            rel_path = str(html_path.relative_to(_root))
        except ValueError:
            rel_path = str(html_path)

        # 写 Report 记录
        report = Report(
            id=uuid.UUID(report_id_str),
            name=spec.get("title", sr.name),
            description=spec.get("subtitle", ""),
            username=sr.owner_username,
            refresh_token=refresh_token,
            report_file_path=rel_path,
            summary_status="pending" if sr.include_summary else "skipped",
            charts=spec.get("charts", []),
            data_sources=[],
            filters=spec.get("filters", []),
            theme=spec.get("theme", "light"),
            extra_metadata={"scheduled_report_id": scheduled_report_id, "version_seq": version_seq},
        )
        # 写入新字段（需数据库迁移后支持）
        try:
            report.doc_type = sr.doc_type  # type: ignore[attr-defined]
            report.scheduled_report_id = uid  # type: ignore[attr-defined]
            report.version_seq = version_seq  # type: ignore[attr-defined]
        except Exception:
            pass

        db.add(report)
        db.commit()
        db.refresh(report)

        # 更新 ScheduledReport 统计
        sr.run_count += 1
        sr.last_run_at = datetime.utcnow()
        db.commit()

        # ── 更新执行日志 ──────────────────────────────────────────────────
        duration = int((datetime.utcnow() - start_ts).total_seconds())
        run_log.status = "success"
        run_log.report_id = uuid.UUID(report_id_str)
        run_log.duration_sec = duration
        run_log.finished_at = datetime.utcnow()
        db.commit()

        logger.info(
            "[Scheduler] Report generated: sr=%s, report=%s, file=%s",
            scheduled_report_id, report_id_str, rel_path,
        )

        # ── 发送通知 ──────────────────────────────────────────────────────
        if sr.notify_channels:
            try:
                from backend.services.notify_service import NotifyService
                notify_svc = NotifyService(db)
                notify_summary = await notify_svc.send_all(
                    channels=sr.notify_channels,
                    report=report,
                    scheduled_report_id=str(uid),
                    run_log_id=str(run_log.id),
                )
                run_log.notify_summary = notify_summary
                db.commit()
            except Exception as ne:
                logger.warning("[Scheduler] Notification failed: %s", ne)

        # 更新 next_run_at
        _update_next_run_at(scheduled_report_id, _job_id(scheduled_report_id))

    except Exception as e:
        logger.error("[Scheduler] Execution failed for sr=%s: %s", scheduled_report_id, e, exc_info=True)
        if run_log:
            try:
                run_log.status = "failed"
                run_log.error_msg = str(e)
                run_log.finished_at = datetime.utcnow()
                run_log.duration_sec = int((datetime.utcnow() - start_ts).total_seconds())
                db.commit()
                # 更新 fail_count
                uid2 = uuid.UUID(scheduled_report_id)
                sr2 = db.query(ScheduledReport).filter(ScheduledReport.id == uid2).first()
                if sr2:
                    sr2.fail_count += 1
                    db.commit()
            except Exception:
                pass
    finally:
        db.close()


def _slugify(s: str) -> str:
    import re
    s = s.strip().lower()
    s = re.sub(r"[^\w\u4e00-\u9fff\-]", "_", s)
    s = re.sub(r"_+", "_", s)
    return s.strip("_") or "report"


# ─────────────────────────────────────────────────────────────────────────────
# 启动时加载
# ─────────────────────────────────────────────────────────────────────────────

def _load_all_active_jobs() -> None:
    """从 DB 加载所有 is_active=True 的 ScheduledReport 并注册到 APScheduler。"""
    try:
        from backend.config.database import SessionLocal
        from backend.models.scheduled_report import ScheduledReport

        db = SessionLocal()
        try:
            active = db.query(ScheduledReport).filter(ScheduledReport.is_active == True).all()  # noqa: E712
            logger.info("[Scheduler] Loading %d active scheduled reports...", len(active))
            for sr in active:
                add_or_update_job(sr)
        finally:
            db.close()
    except Exception as e:
        logger.warning("[Scheduler] Could not load scheduled reports on startup: %s", e)
