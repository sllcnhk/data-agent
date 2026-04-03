"""
JWT 工具 — 签发与解析
"""
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
from jose import JWTError, jwt
import uuid


def create_access_token(
    data: Dict[str, Any],
    secret: str,
    algorithm: str = "HS256",
    expires_minutes: int = 480,
) -> str:
    """生成 access token（默认 8h 有效期）"""
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(minutes=expires_minutes)
    to_encode.update({
        "exp": expire,
        "iat": datetime.utcnow(),
        "jti": str(uuid.uuid4()),
    })
    return jwt.encode(to_encode, secret, algorithm=algorithm)


def decode_token(token: str, secret: str, algorithm: str = "HS256") -> Optional[Dict[str, Any]]:
    """解码并验证 token；失败或过期返回 None"""
    try:
        return jwt.decode(token, secret, algorithms=[algorithm])
    except JWTError:
        return None


def create_refresh_token_jti() -> str:
    """生成 refresh token 的唯一 ID (jti)"""
    return str(uuid.uuid4())
