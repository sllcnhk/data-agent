"""
认证 API — /auth/*

POST /auth/login            本地账号登录，颁发 JWT
POST /auth/refresh          刷新 access_token（refresh_token 轮换）
POST /auth/logout           登出，撤销 refresh_token
GET  /auth/me               当前用户信息 + 权限列表
GET  /auth/lark/login       Lark OAuth 跳转（501 占位）
GET  /auth/lark/callback    Lark OAuth 回调（501 占位）
"""
import logging
from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, Cookie, Depends, HTTPException, Response, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.config.database import get_db
from backend.config.settings import settings
from backend.core.auth.jwt import (
    create_access_token,
    create_refresh_token_jti,
)
from backend.core.auth.providers.local import LocalAuthProvider
from backend.core.rbac import get_user_permissions, get_user_roles
from backend.models.refresh_token import RefreshToken
from backend.models.user import User

# 延迟导入 get_current_user 以避免循环导入（auth→deps→auth）
# deps.py 在模块加载时不会导入 auth.py，因此可以安全地在函数体内延迟导入

router = APIRouter(prefix="/auth", tags=["认证"])
logger = logging.getLogger(__name__)

_REFRESH_COOKIE = "refresh_token"


# ── Pydantic schemas ──────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class MeResponse(BaseModel):
    id: str
    username: str
    display_name: Optional[str]
    email: Optional[str]
    auth_source: str
    is_superadmin: bool
    roles: List[str]
    permissions: List[str]


# ── 内部辅助 ──────────────────────────────────────────────────────────────────

def _issue_tokens(user: User, db: Session, response: Response) -> dict:
    """签发 access_token + refresh_token（写入 httpOnly Cookie）"""
    roles = get_user_roles(user, db)
    access_token = create_access_token(
        data={"sub": str(user.id), "username": user.username, "roles": roles},
        secret=settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
        expires_minutes=settings.access_token_expire_minutes,
    )

    jti = create_refresh_token_jti()
    expires_at = datetime.utcnow() + timedelta(days=settings.refresh_token_expire_days)
    rt = RefreshToken(jti=jti, user_id=user.id, expires_at=expires_at)
    db.add(rt)

    user.last_login_at = datetime.utcnow()
    db.commit()

    response.set_cookie(
        key=_REFRESH_COOKIE,
        value=jti,
        httponly=True,
        samesite="lax",
        # 不设 max_age/expires → session cookie，浏览器关闭时自动清除
        path="/api/v1/auth",
    )

    return {
        "access_token": access_token,
        "token_type": "bearer",
        "expires_in": settings.access_token_expire_minutes * 60,
    }


# ── 端点 ──────────────────────────────────────────────────────────────────────

@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest, response: Response, db: Session = Depends(get_db)):
    """本地账号登录"""
    provider = LocalAuthProvider(db)
    user = await provider.authenticate({"username": body.username, "password": body.password})
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return _issue_tokens(user, db, response)


@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(
    response: Response,
    db: Session = Depends(get_db),
    refresh_token_cookie: Optional[str] = Cookie(default=None, alias=_REFRESH_COOKIE),
):
    """刷新 access_token（refresh_token 轮换）"""
    if not refresh_token_cookie:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="缺少 refresh_token")

    rt = db.query(RefreshToken).filter(RefreshToken.jti == refresh_token_cookie).first()
    if not rt or rt.revoked or rt.expires_at < datetime.utcnow():
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="refresh_token 无效或已过期")

    user = db.query(User).filter(User.id == rt.user_id, User.is_active == True).first()  # noqa: E712
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户不存在或已停用")

    # 空闲超时检测：若距最近活动超过 SESSION_IDLE_TIMEOUT_MINUTES → 吊销 token 并拒绝续期
    if settings.enable_auth:
        timeout_min = settings.session_idle_timeout_minutes
        activity_ts = user.last_active_at or user.last_login_at
        if activity_ts is not None:
            idle_min = (datetime.utcnow() - activity_ts).total_seconds() / 60
            if idle_min > timeout_min:
                rt.revoked = True
                db.commit()
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="会话已超时，请重新登录",
                )

    rt.revoked = True
    db.commit()

    return _issue_tokens(user, db, response)


@router.post("/logout")
async def logout(
    response: Response,
    db: Session = Depends(get_db),
    refresh_token_cookie: Optional[str] = Cookie(default=None, alias=_REFRESH_COOKIE),
):
    """登出 — 撤销 refresh_token"""
    if refresh_token_cookie:
        rt = db.query(RefreshToken).filter(RefreshToken.jti == refresh_token_cookie).first()
        if rt and not rt.revoked:
            rt.revoked = True
            db.commit()

    response.delete_cookie(key=_REFRESH_COOKIE, path="/api/v1/auth")
    return {"message": "已成功登出"}


def _me_endpoint():
    """
    返回 /auth/me 端点函数。
    延迟导入 get_current_user 避免循环导入。
    """
    from backend.api.deps import get_current_user

    async def _get_me(
        db: Session = Depends(get_db),
        current_user=Depends(get_current_user),
    ):
        """获取当前用户信息 + 权限列表"""
        return {
            "id": str(current_user.id),
            "username": current_user.username,
            "display_name": getattr(current_user, "display_name", None),
            "email": getattr(current_user, "email", None),
            "auth_source": getattr(current_user, "auth_source", "local"),
            "is_superadmin": current_user.is_superadmin,
            "roles": get_user_roles(current_user, db),
            "permissions": get_user_permissions(current_user, db),
        }

    return _get_me


# 注册 /me 端点（使用闭包延迟绑定 get_current_user）
router.add_api_route(
    "/me",
    _me_endpoint(),
    methods=["GET"],
    response_model=MeResponse,
    summary="获取当前用户信息 + 权限列表",
    tags=["认证"],
)


@router.get("/lark/login")
async def lark_login():
    """Lark OAuth 跳转（预留，未实现）"""
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Lark SSO 尚未实现，敬请期待",
    )


@router.get("/lark/callback")
async def lark_callback():
    """Lark OAuth 回调（预留，未实现）"""
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Lark SSO 尚未实现，敬请期待",
    )
