"""
清理测试数据脚本

删除由测试文件生成的残留用户和角色（按前缀过滤）。
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

# 测试前缀（匹配这些前缀的用户/角色视为测试数据）
TEST_PREFIXES = ("_rbact_", "_e2e_", "_flow_", "_ulp_")

# 受保护的系统角色（不可删除）
PROTECTED_ROLES = {"viewer", "analyst", "admin", "superadmin"}

# 受保护的用户（不可删除）
PROTECTED_USERS = {"superadmin"}


def is_test_name(name: str) -> bool:
    return any(name.startswith(p) for p in TEST_PREFIXES)


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

    try:
        # ── 1. 清理测试用户 ──────────────────────────────────────────────────
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
                    # 先删 user_roles 关联
                    db.query(UserRole).filter(UserRole.user_id == u.id).delete()
                    db.delete(u)
                    deleted_users += 1
            if not dry_run:
                db.commit()
                logger.info("Deleted %d test user(s).", deleted_users)
        else:
            logger.info("%sNo test users found.", mode)

        # ── 2. 清理测试角色 ──────────────────────────────────────────────────
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
                    # 先删 role_permissions 关联
                    db.query(RolePermission).filter(RolePermission.role_id == r.id).delete()
                    # 再删 user_roles 关联
                    db.query(UserRole).filter(UserRole.role_id == r.id).delete()
                    db.delete(r)
                    deleted_roles += 1
            if not dry_run:
                db.commit()
                logger.info("Deleted %d test role(s).", deleted_roles)
        else:
            logger.info("%sNo test roles found.", mode)

        # ── 3. 汇总 ─────────────────────────────────────────────────────────
        if dry_run:
            logger.info(
                "DRY-RUN complete. Would delete %d user(s) and %d role(s). "
                "Run without --dry-run to apply.",
                len(test_users),
                len(test_roles),
            )
        else:
            logger.info(
                "Cleanup complete. Deleted %d user(s) and %d role(s).",
                deleted_users,
                deleted_roles,
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
