"""
本地账号认证供应商 — 用户名 + 密码
"""
from typing import Optional, Dict, Any
from sqlalchemy.orm import Session

from backend.core.auth.base import AuthProvider
from backend.core.auth.password import verify_password
from backend.models.user import User


class LocalAuthProvider(AuthProvider):

    def __init__(self, db: Session) -> None:
        self._db = db

    async def authenticate(self, credentials: Dict[str, Any]) -> Optional[User]:
        username: str = credentials.get("username", "").strip()
        password: str = credentials.get("password", "")
        if not username or not password:
            return None

        user = (
            self._db.query(User)
            .filter(
                User.username == username,
                User.auth_source == "local",
                User.is_active == True,  # noqa: E712
            )
            .first()
        )
        if not user or not user.hashed_password:
            return None
        if not verify_password(password, user.hashed_password):
            return None
        return user

    def get_oauth_url(self, state: str) -> Optional[str]:
        return None  # 本地账号不需要 OAuth 跳转

    async def handle_callback(self, code: str, state: str) -> Optional[User]:
        raise NotImplementedError("Local provider does not support OAuth callback")
