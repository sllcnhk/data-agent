"""
数据导入功能迁移脚本

执行内容：
1. 创建 import_jobs 表（幂等）
2. 种子 data:import 权限（幂等）
3. 将 data:import 权限分配给 superadmin 角色（幂等）

使用方法:
    python -m backend.scripts.migrate_data_import
    # 或
    cd data-agent && python backend/scripts/migrate_data_import.py
"""
import sys
import os
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_ROOT))

import logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def run():
    from backend.config.database import engine, SessionLocal, Base
    import backend.models  # noqa — registers all models

    # 1. 创建 import_jobs 表
    logger.info("Creating/verifying import_jobs table...")
    Base.metadata.create_all(bind=engine, checkfirst=True)
    logger.info("Table import_jobs: OK")

    db = SessionLocal()
    try:
        from backend.models.permission import Permission
        from backend.models.role import Role
        from backend.models.role_permission import RolePermission

        # 2. 种子 data:import 权限
        perm = (
            db.query(Permission)
            .filter(Permission.resource == "data", Permission.action == "import")
            .first()
        )
        if not perm:
            perm = Permission(
                resource="data",
                action="import",
                description="Excel 数据导入 ClickHouse",
            )
            db.add(perm)
            db.flush()
            logger.info("  + permission: data:import created")
        else:
            logger.info("  permission data:import already exists (id=%s)", perm.id)

        # 3. 将 data:import 分配给 superadmin 角色
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
                logger.info("  + data:import assigned to superadmin role")
            else:
                logger.info("  superadmin already has data:import permission")
        else:
            logger.warning("  superadmin role not found — run init_rbac.py first")

        db.commit()
        logger.info("Data import migration complete.")

    finally:
        db.close()


if __name__ == "__main__":
    run()
