"""
RBAC 核心辅助函数 — 权限查询，供 auth.py 与 deps.py 共用，避免循环导入
"""
from typing import List
from sqlalchemy.orm import Session


def get_user_permissions(user, db: Session) -> List[str]:
    """
    返回用户的权限 key 列表，如 ['chat:use', 'skills.user:read', ...]
    superadmin 返回全部权限。
    """
    from backend.models.permission import Permission
    from backend.models.role import Role
    from backend.models.role_permission import RolePermission
    from backend.models.user_role import UserRole

    if getattr(user, "is_superadmin", False):
        return [p.key for p in db.query(Permission).all()]

    perms: List[str] = []
    for ur in db.query(UserRole).filter(UserRole.user_id == user.id).all():
        for rp in db.query(RolePermission).filter(RolePermission.role_id == ur.role_id).all():
            from backend.models.permission import Permission
            p = db.query(Permission).filter(Permission.id == rp.permission_id).first()
            if p and p.key not in perms:
                perms.append(p.key)
    return perms


def get_user_roles(user, db: Session) -> List[str]:
    """返回用户的角色名列表"""
    from backend.models.role import Role
    from backend.models.user_role import UserRole

    if getattr(user, "is_superadmin", False):
        return ["superadmin"]

    roles: List[str] = []
    for ur in db.query(UserRole).filter(UserRole.user_id == user.id).all():
        role = db.query(Role).filter(Role.id == ur.role_id).first()
        if role:
            roles.append(role.name)
    return roles
