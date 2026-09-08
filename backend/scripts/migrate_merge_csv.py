"""
小工具 - 合并CSV文件 迁移脚本

执行内容（全部幂等）：
1. 创建 merge_csv_jobs 表
2. 种子 tools:merge_csv 权限
3. 将 tools:merge_csv 分配给 superadmin 角色

DDL 是纯 additive 的，可以在服务运行时执行，不需要停机。

使用方法:
    python -m backend.scripts.migrate_merge_csv
    # 或
    cd data-agent && python backend/scripts/migrate_merge_csv.py
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
    import backend.models  # noqa — registers all models (including MergeCsvJob)

    logger.info("Creating/verifying merge_csv_jobs table...")
    Base.metadata.create_all(bind=engine, checkfirst=True)
    logger.info("Table merge_csv_jobs: OK")

    db = SessionLocal()
    try:
        from backend.models.permission import Permission
        from backend.models.role import Role
        from backend.models.role_permission import RolePermission

        perm = (
            db.query(Permission)
            .filter(Permission.resource == "tools", Permission.action == "merge_csv")
            .first()
        )
        if not perm:
            perm = Permission(
                resource="tools",
                action="merge_csv",
                description="合并CSV文件工具",
            )
            db.add(perm)
            db.flush()
            logger.info("  + permission: tools:merge_csv created")
        else:
            logger.info("  permission tools:merge_csv already exists (id=%s)", perm.id)

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
                logger.info("  + tools:merge_csv assigned to superadmin role")
            else:
                logger.info("  superadmin already has tools:merge_csv permission")
        else:
            logger.warning("  superadmin role not found — run init_rbac.py first")

        db.commit()
        logger.info("Merge CSV migration complete.")

    finally:
        db.close()


if __name__ == "__main__":
    run()
