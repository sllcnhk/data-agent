"""
密码工具 — bcrypt 哈希与验证

优先使用 bcrypt 直接调用（避免 passlib 与 bcrypt 版本兼容问题），
降级到 passlib 作为备用。
"""
import bcrypt as _bcrypt


def hash_password(password: str) -> str:
    """返回 bcrypt 哈希值（$2b$ 格式）"""
    salt = _bcrypt.gensalt()
    return _bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """验证明文密码与哈希是否匹配"""
    try:
        return _bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False
