"""
报告权限迁移脚本 (2026-04-13)

将 reports:read / reports:create / reports:delete 三个权限写入已有的 RBAC 数据库，
并将其授予 analyst、admin、superadmin 角色。

幂等执行（可重复运行）。

使用方法：
  cd data-agent
  /d/ProgramData/Anaconda3/envs/dataagent/python.exe backend/scripts/migrate_reports_permissions.py
"""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_ROOT))

import logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

NEW_PERMISSIONS = [
    ("reports", "read",   "查看/列出图表报告"),
    ("reports", "create", "生成图表报告"),
    ("reports", "delete", "删除图表报告"),
]

# 角色 → 授予的 reports 权限
ROLE_PERMISSIONS = {
    "analyst":    ["reports:read", "reports:create"],
    "admin":      ["reports:read", "reports:create", "reports:delete"],
    "superadmin": ["reports:read", "reports:create", "reports:delete"],
}


def run():
    from backend.config.database import SessionLocal
    from backend.models.permission import Permission
    from backend.models.role import Role
    from backend.models.role_permission import RolePermission

    db = SessionLocal()
    try:
        # 1. 写入权限
        perm_map = {}
        for resource, action, description in NEW_PERMISSIONS:
            perm = (
                db.query(Permission)
                .filter(Permission.resource == resource, Permission.action == action)
                .first()
            )
            if not perm:
                perm = Permission(resource=resource, action=action, description=description)
                db.add(perm)
                db.flush()
                logger.info("  [+] 权限: %s:%s", resource, action)
            else:
                logger.info("  [=] 已存在: %s:%s", resource, action)
            perm_map[f"{resource}:{action}"] = perm

        db.commit()

        # 2. 授权给角色
        for role_name, perm_keys in ROLE_PERMISSIONS.items():
            role = db.query(Role).filter(Role.name == role_name).first()
            if not role:
                logger.warning("  [!] 角色不存在，跳过: %s", role_name)
                continue

            for perm_key in perm_keys:
                perm = perm_map.get(perm_key)
                if not perm:
                    continue
                existing = (
                    db.query(RolePermission)
                    .filter(
                        RolePermission.role_id == role.id,
                        RolePermission.permission_id == perm.id,
                    )
                    .first()
                )
                if not existing:
                    db.add(RolePermission(role_id=role.id, permission_id=perm.id))
                    logger.info("  [+] 角色 %s ← %s", role_name, perm_key)
                else:
                    logger.info("  [=] 已有: 角色 %s ← %s", role_name, perm_key)

        db.commit()
        logger.info("\n迁移完成：reports 权限已写入并授权。")

    finally:
        db.close()


if __name__ == "__main__":
    run()
