"""
清理测试数据脚本

删除由测试文件生成的残留用户、角色、导入任务和导出任务（按前缀过滤）。
保留系统角色（viewer/analyst/admin/superadmin）和 superadmin 用户。

使用方法:
    # 预览（不实际删除）
    python backend/scripts/cleanup_test_data.py --dry-run

    # 正式删除
    python backend/scripts/cleanup_test_data.py
"""
import sys
import os
import argparse
from pathlib import Path

# 确保 backend/ 在 sys.path
_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_ROOT))

import logging

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# 测试前缀（匹配这些前缀的用户/角色/任务视为测试数据）
# _t_ 是标准前缀；_def_/_die_/_dee_ 是旧前缀（历史遗留，向后兼容）
TEST_PREFIXES = ("_rbact_", "_e2e_", "_flow_", "_ulp_", "_t_", "_def_", "_die_", "_dee_")

# 受保护的系统角色（不可删除）
PROTECTED_ROLES = {"viewer", "analyst", "admin", "superadmin"}

# 受保护的用户（不可删除）
PROTECTED_USERS = {"superadmin"}


def is_test_name(name: str) -> bool:
    return any(name.startswith(p) for p in TEST_PREFIXES)


def _cleanup_skill_files(dry_run: bool = False) -> int:
    """
    清理 .claude/skills/user/ 下测试产生的 skill 文件和子目录。

    测试前缀（两类）：
    1. DB 测试前缀：^_[a-z][a-z0-9]*_（如 _t_、_si_、_rbact_）
    2. Slugified 测试前缀：^t-compat-、^t-rbac- 等（经 _slugify 后 _ 变 - ）
    """
    import re
    import shutil as _shutil
    _TEST_PATTERN = re.compile(r'^_[a-z][a-z0-9]*_')
    _SLUGIFIED_TEST_PREFIXES = ("t-compat-", "t-rbac-", "t-skill-", "t-test-", "compat-skill-")

    def _is_test_skill(name: str) -> bool:
        return bool(_TEST_PATTERN.match(name)) or any(name.startswith(p) for p in _SLUGIFIED_TEST_PREFIXES)

    user_skills_dir = _ROOT / ".claude" / "skills" / "user"
    if not user_skills_dir.is_dir():
        return 0

    mode = "[DRY-RUN] " if dry_run else ""
    deleted = 0
    for entry in user_skills_dir.iterdir():
        if entry.is_file() and entry.suffix == ".md" and _is_test_skill(entry.stem):
            logger.info("%sWould delete skill file: %s", mode, entry.name)
            if not dry_run:
                try:
                    entry.unlink()
                    deleted += 1
                except OSError as exc:
                    logger.warning("  Failed to delete %s: %s", entry, exc)
        elif entry.is_dir():
            if _is_test_skill(entry.name):
                logger.info("%sWould delete skill dir: %s/", mode, entry.name)
                if not dry_run:
                    try:
                        _shutil.rmtree(entry)
                        deleted += 1
                    except OSError as exc:
                        logger.warning("  Failed to delete %s: %s", entry, exc)
            else:
                # 扫描子目录内的测试遗留文件（如 superadmin/ 里残留的 compat-skill-*.md）
                for subfile in entry.glob("*.md"):
                    if _is_test_skill(subfile.stem):
                        logger.info("%sWould delete skill file: %s/%s", mode, entry.name, subfile.name)
                        if not dry_run:
                            try:
                                subfile.unlink()
                                deleted += 1
                            except OSError as exc:
                                logger.warning("  Failed to delete %s: %s", subfile, exc)

    if deleted:
        logger.info("Deleted %d test skill file/dir(s) from .claude/skills/user/.", deleted)
    elif not dry_run:
        logger.info("No test skill files/dirs found.")
    return deleted


def run(dry_run: bool = False):
    from backend.config.database import SessionLocal
    from backend.models.user import User
    from backend.models.role import Role
    from backend.models.user_role import UserRole
    from backend.models.role_permission import RolePermission

    db = SessionLocal()
    mode = "[DRY-RUN] " if dry_run else ""
    deleted_users = 0
    deleted_roles = 0
    deleted_import_jobs = 0
    deleted_export_jobs = 0

    try:
        # ── 1. 清理测试导入任务 ───────────────────────────────────────────────
        try:
            from backend.models.import_job import ImportJob
            test_import_jobs = [
                j for j in db.query(ImportJob).all()
                # 匹配条件：username 有测试前缀，或 username="default"（ENABLE_AUTH=false 下全是测试产物）
                if is_test_name(j.username) or j.username == "default"
            ]
            if test_import_jobs:
                logger.info("%sFound %d test import job(s) to delete:", mode, len(test_import_jobs))
                for j in test_import_jobs:
                    logger.info("  - import_job: id=%s user=%s status=%s", j.id, j.username, j.status)
                    if not dry_run:
                        db.delete(j)
                        deleted_import_jobs += 1
                if not dry_run:
                    db.commit()
                    logger.info("Deleted %d test import job(s).", deleted_import_jobs)
            else:
                logger.info("%sNo test import jobs found.", mode)
        except Exception as exc:
            logger.warning("ImportJob cleanup skipped (non-fatal): %s", exc)

        # ── 2. 清理测试导出任务 ───────────────────────────────────────────────
        try:
            from backend.models.export_job import ExportJob
            test_export_jobs = [
                j for j in db.query(ExportJob).all()
                # 匹配条件：username 有测试前缀，或 job_name 有测试前缀
                # （ENABLE_AUTH=false 时 username="default"，通过 job_name 识别测试任务）
                # 匹配条件：username 有测试前缀，或 job_name 有测试前缀，或 username="default"
                if is_test_name(j.username) or is_test_name(j.job_name or "") or j.username == "default"
            ]
            if test_export_jobs:
                logger.info("%sFound %d test export job(s) to delete:", mode, len(test_export_jobs))
                for j in test_export_jobs:
                    logger.info("  - export_job: id=%s user=%s status=%s file=%s",
                                j.id, j.username, j.status, j.output_filename)
                    if not dry_run:
                        # 同时删除磁盘上的 xlsx 文件
                        if j.file_path:
                            try:
                                os.unlink(j.file_path)
                            except OSError:
                                pass
                        db.delete(j)
                        deleted_export_jobs += 1
                if not dry_run:
                    db.commit()
                    logger.info("Deleted %d test export job(s).", deleted_export_jobs)
            else:
                logger.info("%sNo test export jobs found.", mode)
        except Exception as exc:
            logger.warning("ExportJob cleanup skipped (non-fatal): %s", exc)

        # ── 3. 清理测试用户 ──────────────────────────────────────────────────
        test_users = (
            db.query(User)
            .filter(User.username.notin_(list(PROTECTED_USERS)))
            .all()
        )
        test_users = [u for u in test_users if is_test_name(u.username)]

        if test_users:
            logger.info("%sFound %d test user(s) to delete:", mode, len(test_users))
            for u in test_users:
                logger.info("  - user: %s (id=%s, display=%s)", u.username, u.id, u.display_name)
                if not dry_run:
                    db.query(UserRole).filter(UserRole.user_id == u.id).delete()
                    db.delete(u)
                    deleted_users += 1
            if not dry_run:
                db.commit()
                logger.info("Deleted %d test user(s).", deleted_users)
        else:
            logger.info("%sNo test users found.", mode)

        # ── 4. 清理测试角色 ──────────────────────────────────────────────────
        test_roles = (
            db.query(Role)
            .filter(Role.name.notin_(list(PROTECTED_ROLES)))
            .all()
        )
        test_roles = [r for r in test_roles if is_test_name(r.name)]

        if test_roles:
            logger.info("%sFound %d test role(s) to delete:", mode, len(test_roles))
            for r in test_roles:
                logger.info("  - role: %s (id=%s)", r.name, r.id)
                if not dry_run:
                    db.query(RolePermission).filter(RolePermission.role_id == r.id).delete()
                    db.query(UserRole).filter(UserRole.role_id == r.id).delete()
                    db.delete(r)
                    deleted_roles += 1
            if not dry_run:
                db.commit()
                logger.info("Deleted %d test role(s).", deleted_roles)
        else:
            logger.info("%sNo test roles found.", mode)

        # ── 5. 清理 skill 文件（不依赖 DB，单独处理）────────────────────────────
        deleted_skills = _cleanup_skill_files(dry_run=dry_run)

        # ── 6. 汇总 ─────────────────────────────────────────────────────────
        if dry_run:
            logger.info(
                "DRY-RUN complete. Would delete %d import job(s), %d export job(s), "
                "%d user(s), %d role(s), %d skill file/dir(s). Run without --dry-run to apply.",
                len(test_import_jobs) if "test_import_jobs" in dir() else 0,
                len(test_export_jobs) if "test_export_jobs" in dir() else 0,
                len(test_users),
                len(test_roles),
                deleted_skills,
            )
        else:
            logger.info(
                "Cleanup complete. Deleted %d import job(s), %d export job(s), "
                "%d user(s), %d role(s), %d skill file/dir(s).",
                deleted_import_jobs,
                deleted_export_jobs,
                deleted_users,
                deleted_roles,
                deleted_skills,
            )

    except Exception:
        db.rollback()
        logger.exception("Cleanup failed, rolled back.")
        sys.exit(1)
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="清理测试残留数据")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="预览模式：列出将被删除的数据但不实际执行",
    )
    args = parser.parse_args()
    run(dry_run=args.dry_run)
