"""
AuthProvider 抽象基类 — 可插拔认证供应商接口
"""
from abc import ABC, abstractmethod
from typing import Optional, Dict, Any


class AuthProvider(ABC):
    """所有认证供应商的基类（本地账号 / Lark / 企微等）"""

    @abstractmethod
    async def authenticate(self, credentials: Dict[str, Any]) -> Optional[Any]:
        """
        验证凭据，返回 User 对象；验证失败返回 None。

        本地账号: credentials = {"username": ..., "password": ...}
        Lark:     credentials = {"code": ..., "state": ...}
        """

    @abstractmethod
    def get_oauth_url(self, state: str) -> Optional[str]:
        """
        生成 OAuth 跳转 URL。
        本地账号返回 None（不需要跳转）。
        """

    @abstractmethod
    async def handle_callback(self, code: str, state: str) -> Optional[Any]:
        """
        处理 OAuth 回调，返回 User 对象。
        本地账号不实现（抛出 NotImplementedError）。
        """
