"""
数据导出功能迁移脚本

执行内容：
1. 创建 export_jobs 表（幂等）
2. 种子 data:export 权限（幂等）
3. 将 data:export 权限分配给 superadmin 角色（幂等）

使用方法:
    python -m backend.scripts.migrate_data_export
    # 或
    cd data-agent && python backend/scripts/migrate_data_export.py
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
    import backend.models  # noqa — registers all models (including ExportJob)

    # 1. 创建 export_jobs 表
    logger.info("Creating/verifying export_jobs table...")
    Base.metadata.create_all(bind=engine, checkfirst=True)
    logger.info("Table export_jobs: OK")

    db = SessionLocal()
    try:
        from backend.models.permission import Permission
        from backend.models.role import Role
        from backend.models.role_permission import RolePermission

        # 2. 种子 data:export 权限
        perm = (
            db.query(Permission)
            .filter(Permission.resource == "data", Permission.action == "export")
            .first()
        )
        if not perm:
            perm = Permission(
                resource="data",
                action="export",
                description="SQL 结果导出为 Excel",
            )
            db.add(perm)
            db.flush()
            logger.info("  + permission: data:export created")
        else:
            logger.info("  permission data:export already exists (id=%s)", perm.id)

        # 3. 将 data:export 分配给 superadmin 角色
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
                logger.info("  + data:export assigned to superadmin role")
            else:
                logger.info("  superadmin already has data:export permission")
        else:
            logger.warning("  superadmin role not found — run init_rbac.py first")

        db.commit()
        logger.info("Data export migration complete.")

    finally:
        db.close()


if __name__ == "__main__":
    run()
