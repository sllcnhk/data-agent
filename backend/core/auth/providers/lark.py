"""
Lark OAuth 认证供应商 — 骨架实现（预留接口，尚未启用）
"""
from typing import Optional, Dict, Any

from backend.core.auth.base import AuthProvider


class LarkAuthProvider(AuthProvider):
    """
    飞书 OAuth 供应商。

    当前状态：骨架 + TODO，接口已预留。
    所有端点返回 501 Not Implemented。
    """

    async def authenticate(self, credentials: Dict[str, Any]) -> Optional[Any]:
        raise NotImplementedError("Lark SSO not yet implemented")

    def get_oauth_url(self, state: str) -> Optional[str]:
        # TODO: 读取 settings.lark_app_id / settings.lark_redirect_uri 构建跳转 URL
        return None

    async def handle_callback(self, code: str, state: str) -> Optional[Any]:
        # TODO:
        # 1. 用 code 换取 Lark access_token
        # 2. 调用 Lark /user/v3/me 获取 open_id + name + email
        # 3. 查找或创建 users 记录（auth_source='lark'）
        # 4. 返回 User 对象
        raise NotImplementedError("Lark OAuth callback not yet implemented")
