"""
小工具 - 合并Excel文件 迁移脚本

执行内容：
1. 创建 merge_excel_jobs 表（幂等）
2. 种子 tools:merge_excel 权限（幂等）
3. 将 tools:merge_excel 权限分配给 superadmin 角色（幂等）

使用方法:
    python -m backend.scripts.migrate_merge_excel
    # 或
    cd data-agent && python backend/scripts/migrate_merge_excel.py
"""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_ROOT))

import logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def run():
    from backend.config.database import engine, SessionLocal, Base
    import backend.models  # noqa — registers all models (including MergeExcelJob)

    # 1. 创建 merge_excel_jobs 表
    logger.info("Creating/verifying merge_excel_jobs table...")
    Base.metadata.create_all(bind=engine, checkfirst=True)
    logger.info("Table merge_excel_jobs: OK")

    db = SessionLocal()
    try:
        from backend.models.permission import Permission
        from backend.models.role import Role
        from backend.models.role_permission import RolePermission

        # 2. 种子 tools:merge_excel 权限
        perm = (
            db.query(Permission)
            .filter(Permission.resource == "tools", Permission.action == "merge_excel")
            .first()
        )
        if not perm:
            perm = Permission(
                resource="tools",
                action="merge_excel",
                description="合并Excel文件工具",
            )
            db.add(perm)
            db.flush()
            logger.info("  + permission: tools:merge_excel created")
        else:
            logger.info("  permission tools:merge_excel already exists (id=%s)", perm.id)

        # 3. 将 tools:merge_excel 分配给 superadmin 角色
        superadmin_role = db.query(Role).filter(Role.name == "superadmin").first()
        if superadmin_role:
            existing_rp = (
                db.query(RolePermission)
                .filter(
                    RolePermission.role_id == superadmin_role.id,
                    RolePermission.permission_id == perm.id,
                )
                .first()
            )
            if not existing_rp:
                db.add(RolePermission(role_id=superadmin_role.id, permission_id=perm.id))
                logger.info("  + tools:merge_excel assigned to superadmin role")
            else:
                logger.info("  superadmin already has tools:merge_excel permission")
        else:
            logger.warning("  superadmin role not found — run init_rbac.py first")

        db.commit()
        logger.info("Merge Excel migration complete.")

    finally:
        db.close()


if __name__ == "__main__":
    run()
