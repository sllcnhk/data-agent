"""
RBAC 初始化脚本

创建 RBAC 数据表、种子数据（权限/角色），并创建默认 superadmin 账号。
现有对话数据的 owner_id 设为 superadmin。
现有 .claude/skills/user/*.md 文件移入 .claude/skills/user/superadmin/ 目录。

使用方法:
    python -m backend.scripts.init_rbac
    # 或
    cd data-agent && python backend/scripts/init_rbac.py
"""
import sys
import os
from pathlib import Path

# 确保 backend/ 在 sys.path
_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_ROOT))

import logging
from datetime import datetime

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


# ── 预置权限定义 ─────────────────────────────────────────────────────────────

PERMISSIONS = [
    ("chat",            "use",          "使用对话功能"),
    ("skills.user",     "read",         "查看自己的用户技能"),
    ("skills.user",     "write",        "创建/编辑/删除自己的用户技能"),
    ("skills.project",  "read",         "查看项目技能"),
    ("skills.project",  "write",        "创建/编辑/删除项目技能"),
    ("skills.system",   "read",         "查看系统技能"),
    ("models",          "read",         "查看 LLM 模型配置"),
    ("models",          "write",        "新增/修改/删除 LLM Config"),
    ("users",           "read",         "查看用户列表"),
    ("users",           "write",        "创建/停用/修改用户"),
    ("users",           "assign_role",  "分配/撤销角色"),
    ("settings",        "read",         "查看系统设置"),
    ("settings",        "write",        "修改系统设置"),
    ("data",            "import",       "Excel 数据导入 ClickHouse"),
    ("data",            "export",       "SQL 结果导出为 Excel"),
]


# ── 预置角色定义（角色名 → 权限 key 列表）────────────────────────────────────

ROLES = {
    "viewer": {
        "description": "只读访客，仅可使用对话",
        "permissions": ["chat:use"],
    },
    "analyst": {
        "description": "数据分析师（推荐默认角色）",
        "permissions": [
            "chat:use",
            "skills.user:read", "skills.user:write",
            "skills.project:read",
            "skills.system:read",
            "settings:read",   # 可查看已注册 MCP 服务器列表（MCPStatus 组件所需）
        ],
    },
    "admin": {
        "description": "项目管理员",
        "permissions": [
            "chat:use",
            "skills.user:read", "skills.user:write",
            "skills.project:read", "skills.project:write",
            "skills.system:read",
            "models:read", "models:write",
            "settings:read", "settings:write",
        ],
    },
    "superadmin": {
        "description": "超级管理员（拥有全部权限）",
        "permissions": [p[0] + ":" + p[1] for p in PERMISSIONS],
    },
}


def run():
    from backend.config.database import engine, SessionLocal
    from backend.models import user as _u  # noqa – registers model
    from backend.models import role as _r  # noqa
    from backend.models import permission as _p  # noqa
    from backend.models import user_role as _ur  # noqa
    from backend.models import role_permission as _rp  # noqa
    from backend.models import refresh_token as _rt  # noqa
    from backend.config.database import Base

    from backend.models.user import User
    from backend.models.role import Role
    from backend.models.permission import Permission
    from backend.models.user_role import UserRole
    from backend.models.role_permission import RolePermission

    from backend.core.auth.password import hash_password

    # ── 1. 创建 RBAC 表 ──────────────────────────────────────────────────────
    logger.info("Creating RBAC tables...")
    Base.metadata.create_all(bind=engine, checkfirst=True)
    logger.info("Tables created (or already exist).")

    # ── 2. 添加 conversations.owner_id 列（如尚未存在）───────────────────────
    _add_owner_id_to_conversations(engine)

    db = SessionLocal()
    try:
        # ── 3. 种子权限 ───────────────────────────────────────────────────────
        logger.info("Seeding permissions...")
        perm_map: dict = {}
        for resource, action, description in PERMISSIONS:
            existing = (
                db.query(Permission)
                .filter(Permission.resource == resource, Permission.action == action)
                .first()
            )
            if not existing:
                p = Permission(resource=resource, action=action, description=description)
                db.add(p)
                db.flush()
                perm_map[f"{resource}:{action}"] = p
                logger.info("  + permission: %s:%s", resource, action)
            else:
                perm_map[f"{resource}:{action}"] = existing
        db.commit()
        logger.info("Permissions seeded: %d total", len(perm_map))

        # ── 4. 种子角色 ───────────────────────────────────────────────────────
        logger.info("Seeding roles...")
        role_map: dict = {}
        for role_name, role_def in ROLES.items():
            role = db.query(Role).filter(Role.name == role_name).first()
            if not role:
                role = Role(
                    name=role_name,
                    description=role_def["description"],
                    is_system=True,
                )
                db.add(role)
                db.flush()
                logger.info("  + role: %s", role_name)
            role_map[role_name] = role

            # 分配权限
            for perm_key in role_def["permissions"]:
                perm = perm_map.get(perm_key)
                if not perm:
                    continue
                existing_rp = (
                    db.query(RolePermission)
                    .filter(
                        RolePermission.role_id == role.id,
                        RolePermission.permission_id == perm.id,
                    )
                    .first()
                )
                if not existing_rp:
                    db.add(RolePermission(role_id=role.id, permission_id=perm.id))
        db.commit()
        logger.info("Roles seeded: %d total", len(role_map))

        # ── 5. 创建 superadmin 账号 ───────────────────────────────────────────
        SUPERADMIN_USERNAME = "superadmin"
        SUPERADMIN_PASSWORD = "Sgp013013"

        superadmin = db.query(User).filter(User.username == SUPERADMIN_USERNAME).first()
        if not superadmin:
            superadmin = User(
                username=SUPERADMIN_USERNAME,
                display_name="Super Administrator",
                auth_source="local",
                hashed_password=hash_password(SUPERADMIN_PASSWORD),
                is_active=True,
                is_superadmin=True,
            )
            db.add(superadmin)
            db.flush()

            # 分配 superadmin 角色
            superadmin_role = role_map.get("superadmin")
            if superadmin_role:
                db.add(UserRole(
                    user_id=superadmin.id,
                    role_id=superadmin_role.id,
                    assigned_by=superadmin.id,
                ))
            db.commit()
            logger.info("superadmin user created (id=%s)", superadmin.id)
        else:
            logger.info("superadmin already exists (id=%s), skipping creation", superadmin.id)

        # ── 6. 将现有对话归属 superadmin ────────────────────────────────────────
        _assign_conversations_to_superadmin(engine, str(superadmin.id))

        # ── 7. 迁移现有 user skills 到 superadmin 子目录 ─────────────────────────
        _migrate_user_skills_to_superadmin(SUPERADMIN_USERNAME)

    finally:
        db.close()

    logger.info("RBAC initialization complete.")


def _add_owner_id_to_conversations(engine):
    """为 conversations 表添加可空 owner_id 列（幂等）"""
    from sqlalchemy import text
    with engine.connect() as conn:
        try:
            conn.execute(text(
                "ALTER TABLE conversations ADD COLUMN IF NOT EXISTS "
                "owner_id UUID DEFAULT NULL"
            ))
            conn.commit()
            logger.info("conversations.owner_id column added (or already exists).")
        except Exception as e:
            logger.warning("Could not add owner_id column: %s", e)


def _assign_conversations_to_superadmin(engine, superadmin_id: str):
    """将 owner_id 为 NULL 的对话归属 superadmin"""
    from sqlalchemy import text
    with engine.connect() as conn:
        try:
            result = conn.execute(text(
                "UPDATE conversations SET owner_id = :uid "
                "WHERE owner_id IS NULL"
            ), {"uid": superadmin_id})
            conn.commit()
            logger.info(
                "Assigned %d conversation(s) to superadmin.",
                result.rowcount,
            )
        except Exception as e:
            logger.warning("Could not assign conversations: %s", e)


def _migrate_user_skills_to_superadmin(username: str):
    """
    将 .claude/skills/user/*.md（根目录 .md 文件）迁移到 .claude/skills/user/{username}/ 子目录。
    只移动直接位于 user/ 根目录的 .md 文件，不移动子目录中的文件。
    """
    user_skills_root = _ROOT / ".claude" / "skills" / "user"
    target_dir = user_skills_root / username
    target_dir.mkdir(parents=True, exist_ok=True)

    moved = 0
    for md_file in user_skills_root.glob("*.md"):
        if md_file.is_file():
            dest = target_dir / md_file.name
            if not dest.exists():
                md_file.rename(dest)
                logger.info("  Moved %s -> %s", md_file.name, dest)
                moved += 1
            else:
                logger.info("  Skip (already exists at target): %s", md_file.name)
    if moved:
        logger.info("Migrated %d skill file(s) to %s/", moved, target_dir)
    else:
        logger.info("No skill files to migrate (or all already in target dir).")


if __name__ == "__main__":
    run()
