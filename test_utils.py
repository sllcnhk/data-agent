"""
测试工具模块 — 统一测试数据命名规范

所有测试文件应从此处 import TEST_PREFIX 和工厂函数，
不得在各自文件中 hardcode 前缀字符串。

命名规范：
  _t_{label}_{6位hex}_ — 例如 _t_user_a3f2c1_
  • _t_ 是统一标识符，conftest.py 正则 ^_[a-z][a-z0-9]*_ 可覆盖
  • 带随机尾缀保证并发测试不冲突
  • 同一测试文件使用相同 label，方便 grep 定位来源

现有存量前缀（向后兼容，conftest.py 正则已覆盖）：
  _rbact_、_e2e_、_flow_、_ulp_、_sk_、_cdi_
"""
import os
import uuid
from pathlib import Path

# ── 统一前缀 ────────────────────────────────────────────
TEST_PREFIX = "_t_"


def make_test_username(label: str = "user") -> str:
    """生成唯一测试用户名，格式：_t_{label}_{6位hex}_"""
    return f"_t_{label}_{uuid.uuid4().hex[:6]}_"


def make_test_rolename(label: str = "role") -> str:
    """生成唯一测试角色名，格式：_t_{label}_{6位hex}_"""
    return f"_t_{label}_{uuid.uuid4().hex[:6]}_"


def make_test_email(label: str = "user") -> str:
    """生成唯一测试邮箱"""
    return f"_t_{label}_{uuid.uuid4().hex[:6]}@test.local"


def make_test_skill_name(label: str = "skill") -> str:
    """生成唯一测试技能名，格式：_t_{label}_{6位hex}_（slug 化后：t-{label}-{hex}-）"""
    return f"_t_{label}_{uuid.uuid4().hex[:6]}_"


def is_test_entity(name: str) -> bool:
    """判断名称是否为测试数据（匹配 _xxx_ 前缀格式，与 conftest.py 逻辑一致）。"""
    import re
    return bool(re.match(r'^_[a-z][a-z0-9]*_', name))


# ── 标准化清理函数 ───────────────────────────────────────

def cleanup_test_reports(prefix: str, db, customer_data_root: str = "customer_data") -> int:
    """
    删除匹配 prefix 的 Report DB 记录，同时删除磁盘上对应的 HTML 文件。

    参数：
      prefix            — like-模式前缀，例如 "_ci_abc123_"（用于 Report.name.like）
      db                — SQLAlchemy Session
      customer_data_root — customer_data 根目录路径（相对或绝对）；
                          report_file_path 存储的是相对于 customer_data 父目录（项目根）的路径，
                          此处保持与 backend 一致：直接把 report_file_path 当绝对路径或相对于 CWD 处理

    返回删除的 Report 记录数量。
    """
    from backend.models.report import Report

    test_reports = (
        db.query(Report)
        .filter(Report.name.like(f"{prefix}%"))
        .all()
    )

    deleted_files = 0
    for report in test_reports:
        if report.report_file_path:
            try:
                p = Path(report.report_file_path)
                if not p.is_absolute():
                    p = Path(customer_data_root).parent / p
                if p.exists():
                    p.unlink()
                    deleted_files += 1
            except Exception:
                pass
        db.delete(report)

    count = len(test_reports)
    if count:
        db.commit()
    return count


def cleanup_test_schedules(prefix: str, db) -> int:
    """
    删除匹配 prefix 的 ScheduledReport 记录（含 ScheduleRunLog 外键数据）。

    参数：
      prefix — like-模式前缀，例如 "_ci_abc123_"
      db     — SQLAlchemy Session

    返回删除的 ScheduledReport 记录数量。
    """
    from backend.models.scheduled_report import ScheduledReport
    from backend.models.schedule_run_log import ScheduleRunLog

    test_schedules = (
        db.query(ScheduledReport)
        .filter(ScheduledReport.name.like(f"{prefix}%"))
        .all()
    )

    for sr in test_schedules:
        db.query(ScheduleRunLog).filter(
            ScheduleRunLog.scheduled_report_id == sr.id
        ).delete(synchronize_session=False)
        db.delete(sr)

    count = len(test_schedules)
    if count:
        db.commit()
    return count


def cleanup_test_conversations(prefix: str, db) -> int:
    """
    删除 title 匹配 prefix 的 Conversation 记录（cascade 删除关联 Message）。

    Conversation 关联了 Message（cascade="all, delete-orphan"），
    因此直接 delete(synchronize_session=False) 即可；SQLAlchemy 会先删子行。

    参数：
      prefix — like-模式前缀或 substring，例如 "_ci_abc123_"；
               使用 %prefix% 模式以覆盖 title 中间含前缀的情况
      db     — SQLAlchemy Session

    返回删除的 Conversation 记录数量。
    """
    from backend.models.conversation import Conversation, Message

    # 先按 conversation_id 批量删 Message（避免 ORM cascade 逐条删导致 N+1）
    test_conv_ids = [
        row[0]
        for row in db.query(Conversation.id)
        .filter(Conversation.title.like(f"%{prefix}%"))
        .all()
    ]

    if not test_conv_ids:
        return 0

    db.query(Message).filter(
        Message.conversation_id.in_(test_conv_ids)
    ).delete(synchronize_session=False)

    n = db.query(Conversation).filter(
        Conversation.id.in_(test_conv_ids)
    ).delete(synchronize_session=False)

    db.commit()
    return n
