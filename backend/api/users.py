"""
用户管理 API — /users/*

POST   /users                     创建本地账号（需 users:write）
GET    /users                     用户列表（需 users:read）
GET    /users/{id}                用户详情（需 users:read 或本人）
PUT    /users/{id}                修改 display_name / is_active（需 users:write 或本人）
PUT    /users/{id}/password       修改密码（本人，需提供旧密码）
POST   /users/{id}/roles          分配角色（需 users:assign_role）
DELETE /users/{id}/roles/{role_id} 撤销角色（需 users:assign_role）

GET    /roles                     角色列表（需 users:read）
"""
import logging
from datetime import datetime
from typing import List, Optional
from uuid import UUID

import uuid as _uuid_mod

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.api.deps import get_current_user, require_permission
from backend.config.database import get_db
from backend.core.auth.password import hash_password, verify_password
from backend.models.permission import Permission
from backend.models.role import Role
from backend.models.role_permission import RolePermission
from backend.models.user import User
from backend.models.user_role import UserRole

router = APIRouter(prefix="/users", tags=["用户管理"])
logger = logging.getLogger(__name__)


# ── Pydantic schemas ──────────────────────────────────────────────────────────

class UserCreate(BaseModel):
    username: str = Field(..., min_length=2, max_length=64, description="用户名（唯一）")
    password: str = Field(..., min_length=6, max_length=128, description="初始密码")
    display_name: Optional[str] = Field(default=None, max_length=128)
    email: Optional[str] = Field(default=None, max_length=256)
    role_names: List[str] = Field(default_factory=lambda: ["analyst"], description="初始角色列表")


class UserUpdate(BaseModel):
    display_name: Optional[str] = Field(default=None, max_length=128)
    is_active: Optional[bool] = None


class PasswordChange(BaseModel):
    old_password: str
    new_password: str = Field(..., min_length=6, max_length=128)


class RoleAssign(BaseModel):
    role_name: str = Field(..., description="角色名称，如 analyst / admin")


class PermissionOut(BaseModel):
    id: str
    resource: str
    action: str
    description: Optional[str]


class RoleOut(BaseModel):
    id: str
    name: str
    description: Optional[str]
    is_system: bool
    permissions: List[PermissionOut]


class UserOut(BaseModel):
    id: str
    username: str
    display_name: Optional[str]
    email: Optional[str]
    auth_source: str
    is_active: bool
    is_superadmin: bool
    roles: List[str]
    last_login_at: Optional[str]
    created_at: str


class UserListOut(BaseModel):
    total: int
    items: List[UserOut]


def _safe_assigned_by(user_id) -> Optional[UUID]:
    """Convert user id to UUID for assigned_by field; returns None for non-UUID ids (e.g. AnonymousUser)."""
    try:
        return _uuid_mod.UUID(str(user_id))
    except (ValueError, AttributeError):
        return None


def _user_out(user: User, db: Session) -> dict:
    roles = [
        db.query(Role).filter(Role.id == ur.role_id).first().name
        for ur in db.query(UserRole).filter(UserRole.user_id == user.id).all()
        if db.query(Role).filter(Role.id == ur.role_id).first()
    ]
    return {
        "id": str(user.id),
        "username": user.username,
        "display_name": user.display_name,
        "email": user.email,
        "auth_source": user.auth_source,
        "is_active": user.is_active,
        "is_superadmin": user.is_superadmin,
        "roles": roles,
        "last_login_at": user.last_login_at.isoformat() if user.last_login_at else None,
        "created_at": user.created_at.isoformat(),
    }


# ── 用户 CRUD ─────────────────────────────────────────────────────────────────

@router.post("", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def create_user(
    body: UserCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("users", "write")),
):
    """创建本地账号"""
    if db.query(User).filter(User.username == body.username).first():
        raise HTTPException(status_code=409, detail=f"用户名 '{body.username}' 已存在")
    if body.email and db.query(User).filter(User.email == body.email).first():
        raise HTTPException(status_code=409, detail=f"邮箱 '{body.email}' 已被使用")

    new_user = User(
        username=body.username,
        display_name=body.display_name,
        email=body.email,
        auth_source="local",
        hashed_password=hash_password(body.password),
    )
    db.add(new_user)
    db.flush()  # 获取 id

    # 分配初始角色
    for role_name in body.role_names:
        role = db.query(Role).filter(Role.name == role_name).first()
        if role:
            ur = UserRole(user_id=new_user.id, role_id=role.id,
                          assigned_by=_safe_assigned_by(current_user.id))
            db.add(ur)

    db.commit()
    db.refresh(new_user)
    logger.info("[users] Created user '%s' by '%s'", body.username, current_user.username)
    return _user_out(new_user, db)


_SORTABLE_COLS = {
    "username": User.username,
    "display_name": User.display_name,
    "auth_source": User.auth_source,
    "is_active": User.is_active,
    "is_superadmin": User.is_superadmin,
    "last_login_at": User.last_login_at,
    "created_at": User.created_at,
}


@router.get("", response_model=UserListOut)
async def list_users(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    sort_by: str = Query(default="created_at"),
    sort_order: str = Query(default="desc", pattern="^(asc|desc)$"),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("users", "read")),
):
    """用户列表（分页 + 排序）"""
    col = _SORTABLE_COLS.get(sort_by, User.created_at)
    order_expr = col.desc() if sort_order == "desc" else col.asc()
    total = db.query(User).count()
    offset = (page - 1) * page_size
    users = db.query(User).order_by(order_expr).offset(offset).limit(page_size).all()
    return {"total": total, "items": [_user_out(u, db) for u in users]}


@router.get("/{user_id}", response_model=UserOut)
async def get_user(
    user_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """用户详情（本人或具有 users:read 权限者可查）"""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")

    # 只有本人或有 users:read 权限的用户才可查看
    if str(current_user.id) != user_id:
        from backend.core.rbac import get_user_permissions
        perms = get_user_permissions(current_user, db)
        if "users:read" not in perms and not current_user.is_superadmin:
            raise HTTPException(status_code=403, detail="权限不足")

    return _user_out(user, db)


@router.put("/{user_id}", response_model=UserOut)
async def update_user(
    user_id: str,
    body: UserUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """修改 display_name / is_active（本人改自己无需权限；改别人需 users:write）"""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")

    if str(current_user.id) != user_id:
        from backend.core.rbac import get_user_permissions
        perms = get_user_permissions(current_user, db)
        if "users:write" not in perms and not current_user.is_superadmin:
            raise HTTPException(status_code=403, detail="权限不足: 需要 users:write")

    if body.display_name is not None:
        user.display_name = body.display_name
    if body.is_active is not None:
        user.is_active = body.is_active
    user.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(user)
    return _user_out(user, db)


@router.put("/{user_id}/password")
async def change_password(
    user_id: str,
    body: PasswordChange,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """修改密码（仅本人，需提供旧密码）"""
    if str(current_user.id) != user_id:
        raise HTTPException(status_code=403, detail="只能修改自己的密码")

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    if not user.hashed_password or not verify_password(body.old_password, user.hashed_password):
        raise HTTPException(status_code=400, detail="旧密码错误")

    user.hashed_password = hash_password(body.new_password)
    user.updated_at = datetime.utcnow()
    db.commit()
    return {"message": "密码修改成功"}


# ── 角色分配 ──────────────────────────────────────────────────────────────────

@router.post("/{user_id}/roles", response_model=UserOut)
async def assign_role(
    user_id: str,
    body: RoleAssign,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("users", "assign_role")),
):
    """分配角色"""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")

    role = db.query(Role).filter(Role.name == body.role_name).first()
    if not role:
        raise HTTPException(status_code=404, detail=f"角色 '{body.role_name}' 不存在")

    existing = (
        db.query(UserRole)
        .filter(UserRole.user_id == user.id, UserRole.role_id == role.id)
        .first()
    )
    if not existing:
        ur = UserRole(user_id=user.id, role_id=role.id,
                      assigned_by=_safe_assigned_by(current_user.id))
        db.add(ur)
        db.commit()

    return _user_out(user, db)


@router.delete("/{user_id}/roles/{role_id}", response_model=UserOut)
async def revoke_role(
    user_id: str,
    role_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("users", "assign_role")),
):
    """撤销角色"""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")

    ur = (
        db.query(UserRole)
        .filter(UserRole.user_id == user_id, UserRole.role_id == role_id)
        .first()
    )
    if ur:
        db.delete(ur)
        db.commit()

    return _user_out(user, db)


# ── 角色管理 ──────────────────────────────────────────────────────────────────

roles_router = APIRouter(prefix="/roles", tags=["角色管理"])

permissions_router = APIRouter(prefix="/permissions", tags=["权限管理"])


class RoleCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=64, description="角色名称（唯一）")
    description: Optional[str] = Field(default=None, max_length=255)


class RoleUpdate(BaseModel):
    description: Optional[str] = Field(default=None, max_length=255)


class PermissionAssign(BaseModel):
    permission_id: str = Field(..., description="权限 ID")


def _role_out_dict(role: Role, db: Session) -> dict:
    perms = []
    for rp in db.query(RolePermission).filter(RolePermission.role_id == role.id).all():
        p = db.query(Permission).filter(Permission.id == rp.permission_id).first()
        if p:
            perms.append({
                "id": str(p.id),
                "resource": p.resource,
                "action": p.action,
                "description": p.description,
            })
    return {
        "id": str(role.id),
        "name": role.name,
        "description": role.description,
        "is_system": role.is_system,
        "permissions": perms,
    }


@roles_router.get("", response_model=List[RoleOut])
async def list_roles(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("users", "read")),
):
    """角色列表（含权限详情）"""
    roles = db.query(Role).order_by(Role.name).all()
    return [_role_out_dict(r, db) for r in roles]


@roles_router.post("", response_model=RoleOut, status_code=status.HTTP_201_CREATED)
async def create_role(
    body: RoleCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("users", "write")),
):
    """创建自定义角色"""
    if db.query(Role).filter(Role.name == body.name).first():
        raise HTTPException(status_code=409, detail=f"角色 '{body.name}' 已存在")
    role = Role(name=body.name, description=body.description, is_system=False)
    db.add(role)
    db.commit()
    db.refresh(role)
    logger.info("[roles] Created role '%s' by '%s'", body.name, current_user.username)
    return _role_out_dict(role, db)


@roles_router.put("/{role_id}", response_model=RoleOut)
async def update_role(
    role_id: str,
    body: RoleUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("users", "write")),
):
    """更新角色描述"""
    role = db.query(Role).filter(Role.id == role_id).first()
    if not role:
        raise HTTPException(status_code=404, detail="角色不存在")
    if body.description is not None:
        role.description = body.description
    db.commit()
    db.refresh(role)
    return _role_out_dict(role, db)


@roles_router.delete("/{role_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_role(
    role_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("users", "write")),
):
    """删除自定义角色（系统预置角色不可删除）"""
    role = db.query(Role).filter(Role.id == role_id).first()
    if not role:
        raise HTTPException(status_code=404, detail="角色不存在")
    if role.is_system:
        raise HTTPException(status_code=403, detail="系统预置角色不可删除")
    db.delete(role)
    db.commit()
    logger.info("[roles] Deleted role '%s' by '%s'", role.name, current_user.username)


@roles_router.post("/{role_id}/permissions", response_model=RoleOut)
async def assign_permission_to_role(
    role_id: str,
    body: PermissionAssign,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("users", "assign_role")),
):
    """为角色分配权限"""
    role = db.query(Role).filter(Role.id == role_id).first()
    if not role:
        raise HTTPException(status_code=404, detail="角色不存在")
    perm = db.query(Permission).filter(Permission.id == body.permission_id).first()
    if not perm:
        raise HTTPException(status_code=404, detail="权限不存在")
    existing = db.query(RolePermission).filter(
        RolePermission.role_id == role.id, RolePermission.permission_id == perm.id
    ).first()
    if not existing:
        rp = RolePermission(role_id=role.id, permission_id=perm.id)
        db.add(rp)
        db.commit()
    return _role_out_dict(role, db)


@roles_router.delete("/{role_id}/permissions/{perm_id}", response_model=RoleOut)
async def remove_permission_from_role(
    role_id: str,
    perm_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("users", "assign_role")),
):
    """移除角色的某个权限"""
    role = db.query(Role).filter(Role.id == role_id).first()
    if not role:
        raise HTTPException(status_code=404, detail="角色不存在")
    rp = db.query(RolePermission).filter(
        RolePermission.role_id == role_id, RolePermission.permission_id == perm_id
    ).first()
    if rp:
        db.delete(rp)
        db.commit()
    return _role_out_dict(role, db)


# ── 权限列表 ──────────────────────────────────────────────────────────────────

@permissions_router.get("", response_model=List[PermissionOut])
async def list_permissions(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("users", "read")),
):
    """所有权限列表"""
    perms = db.query(Permission).order_by(Permission.resource, Permission.action).all()
    return [
        {"id": str(p.id), "resource": p.resource, "action": p.action, "description": p.description}
        for p in perms
    ]
