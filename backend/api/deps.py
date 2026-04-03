"""
FastAPI 共享依赖 — 认证与权限门卫

访问控制层级
------------
ENABLE_AUTH=false（默认，单用户模式）:
    所有请求以内置 SuperAdminUser 身份运行，与现有行为完全兼容。

ENABLE_AUTH=true（多用户模式）:
    - get_current_user:    解析 Bearer JWT，返回 User 对象；401 if missing/expired
    - require_permission:  工厂函数，返回 FastAPI Depends；superadmin 直接通过
    - require_admin:       保留旧 X-Admin-Token 检查（向后兼容，逐步迁移）

Tier 0 — AnonymousUser（ENABLE_AUTH=false 时，无限权限）
Tier 1 — X-Admin-Token（旧版 admin 端点，逐步废弃）
Tier 2 — JWT（正式 RBAC）
"""

from datetime import datetime
from fastapi import BackgroundTasks, Depends, Header, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session
from typing import Optional, List

from backend.config.database import get_db
from backend.config.settings import settings
from backend.core.auth.jwt import decode_token

# OAuth2 scheme（ENABLE_AUTH=false 时 auto_error=False 避免 401）
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login", auto_error=False)


# ── 内置用户类型 ───────────────────────────────────────────────────────────────

class AnonymousUser:
    """
    ENABLE_AUTH=false 时使用的内置用户。
    is_superadmin=True → 所有权限检查均通过，维持旧行为。
    """
    is_authenticated: bool = False
    is_active: bool = True
    is_superadmin: bool = True
    id: str = "default"
    username: str = "default"
    display_name: Optional[str] = "Default User"
    role: str = "superadmin"


# ── Tier 1: Admin token（旧版，向后兼容）────────────────────────────────────────

async def require_admin(
    x_admin_token: Optional[str] = Header(default=None),
) -> None:
    """
    FastAPI dependency — require X-Admin-Token header for admin operations.

    Configuration:
        Set ``ADMIN_SECRET_TOKEN=<your-secret>`` in `.env`.

    Responses:
        - 503: ADMIN_SECRET_TOKEN not configured in .env
        - 401: Token present but incorrect
    """
    expected = settings.admin_secret_token
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Admin operations are disabled. "
                "Set ADMIN_SECRET_TOKEN in .env to enable."
            ),
        )
    if x_admin_token != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing X-Admin-Token header.",
            headers={"WWW-Authenticate": "Token"},
        )


# ── Tier 2: JWT（ENABLE_AUTH=true）──────────────────────────────────────────────

# 活动更新节流：每 5 分钟最多向 DB 写一次 last_active_at
_ACTIVITY_THROTTLE_SEC = 300


def _update_last_active(user_id: str) -> None:
    """后台任务：用独立 DB session 写 last_active_at，不阻塞响应"""
    from backend.config.database import SessionLocal
    from backend.models.user import User as UserModel
    db = SessionLocal()
    try:
        u = db.query(UserModel).filter(UserModel.id == user_id).first()
        if u:
            u.last_active_at = datetime.utcnow()
            db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()


async def get_current_user(
    token: Optional[str] = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
    background_tasks: BackgroundTasks = BackgroundTasks(),
):
    """
    解析 Bearer JWT，返回 User 对象。

    ENABLE_AUTH=false: 返回内置 AnonymousUser（所有权限）。
    ENABLE_AUTH=true:  验证 JWT，查询 users 表；401 if invalid/expired。
    """
    if not settings.enable_auth:
        return AnonymousUser()

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="未登录，请先登录",
            headers={"WWW-Authenticate": "Bearer"},
        )

    payload = decode_token(token, settings.jwt_secret, settings.jwt_algorithm)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token 无效或已过期",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token 格式错误")

    from backend.models.user import User
    user = db.query(User).filter(User.id == user_id, User.is_active == True).first()  # noqa: E712
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户不存在或已停用")

    # 节流更新 last_active_at（后台任务，不阻塞响应）
    last = user.last_active_at or datetime.min
    if (datetime.utcnow() - last).total_seconds() > _ACTIVITY_THROTTLE_SEC:
        background_tasks.add_task(_update_last_active, str(user.id))

    return user


def require_permission(resource: str, action: str):
    """
    权限检查工厂函数，返回 FastAPI Depends。

    - superadmin / AnonymousUser（ENABLE_AUTH=false）直接通过
    - 其他用户检查 {resource}:{action} 权限

    Usage::
        @router.post("/skills")
        async def create_skill(user = Depends(require_permission("skills.user", "write"))):
            ...

    Note: require_permission() returns the inner ``_check`` coroutine, so wrap it
    with ``Depends(...)`` at the call site as shown above.

    """
    async def _check(
        current_user=Depends(get_current_user),
        db: Session = Depends(get_db),
    ):
        # AnonymousUser 或 superadmin 直接通过
        if getattr(current_user, "is_superadmin", False):
            return current_user

        perm_key = f"{resource}:{action}"
        from backend.core.rbac import get_user_permissions
        user_perms: List[str] = get_user_permissions(current_user, db)
        if perm_key not in user_perms:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"权限不足: 需要 {perm_key}",
            )
        return current_user

    return _check
