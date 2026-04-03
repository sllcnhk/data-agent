"""
模型适配器工厂

根据配置创建合适的模型适配器
"""
from typing import Dict, Any, Optional
from backend.core.model_adapters.base import BaseModelAdapter
from backend.core.model_adapters.claude import ClaudeAdapter
from backend.config.settings import settings

# 条件导入可选适配器
OpenAIAdapter = None
try:
    from backend.core.model_adapters.openai import OpenAIAdapter
except ImportError:
    pass

GeminiAdapter = None
try:
    from backend.core.model_adapters.gemini import GeminiAdapter
except ImportError:
    pass

QianwenAdapter = None
try:
    from backend.core.model_adapters.qianwen import QianwenAdapter
except ImportError:
    pass

DoubaoAdapter = None
try:
    from backend.core.model_adapters.doubao import DoubaoAdapter
except ImportError:
    pass


class ModelAdapterFactory:
    """模型适配器工厂"""

    @classmethod
    def _build_adapter_map(cls) -> Dict[str, type]:
        """动态构建适配器映射，只包含可用的适配器"""
        adapter_map = {
            "claude": ClaudeAdapter,  # 使用 HTTP 版本
            "anthropic": ClaudeAdapter,
        }

        # 添加可选适配器
        if OpenAIAdapter:
            adapter_map["openai"] = OpenAIAdapter
            adapter_map["gpt"] = OpenAIAdapter
            adapter_map["chatgpt"] = OpenAIAdapter

        if GeminiAdapter:
            adapter_map["gemini"] = GeminiAdapter
            adapter_map["google"] = GeminiAdapter

        if QianwenAdapter:
            adapter_map["qianwen"] = QianwenAdapter
            adapter_map["tongyi"] = QianwenAdapter

        if DoubaoAdapter:
            adapter_map["doubao"] = DoubaoAdapter

        return adapter_map

    # 默认模型配置
    DEFAULT_MODELS = {
        "claude": "claude-sonnet-4-6",
        "openai": "gpt-4-turbo-preview",
        "gemini": "gemini-pro",
        "qianwen": "qwen3-max",
        "doubao": "doubao-pro",
    }

    @classmethod
    def create_adapter(
        cls,
        provider: str,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        **kwargs
    ) -> BaseModelAdapter:
        """
        创建模型适配器

        Args:
            provider: 模型提供商(claude, openai, gemini)
            api_key: API密钥(可选,默认从settings读取)
            model: 模型名称(可选,使用默认模型)
            **kwargs: 其他配置参数

        Returns:
            模型适配器实例

        Raises:
            ValueError: 不支持的提供商或适配器不可用
        """
        provider_lower = provider.lower()

        # 动态获取可用的适配器映射
        adapter_map = cls._build_adapter_map()

        # 获取适配器类
        adapter_class = adapter_map.get(provider_lower)
        if not adapter_class:
            available_providers = list(adapter_map.keys())
            raise ValueError(
                f"不支持的模型提供商: {provider}. "
                f"当前可用的提供商: {available_providers}"
            )

        # 获取API密钥
        if not api_key:
            api_key = cls._get_api_key(provider_lower)

        # 获取模型名称
        if not model:
            model = cls._get_default_model(provider_lower)

        # 创建适配器
        return adapter_class(api_key=api_key, model=model, **kwargs)

    @classmethod
    def create_from_settings(
        cls,
        provider: Optional[str] = None,
        **kwargs
    ) -> BaseModelAdapter:
        """
        从settings创建适配器

        Args:
            provider: 模型提供商(可选,使用默认提供商)
            **kwargs: 其他配置参数

        Returns:
            模型适配器实例
        """
        if not provider:
            provider = settings.default_llm_model

        # 获取适配器默认配置并合并用户提供的参数
        config = cls.get_adapter_config(provider)
        config.update(kwargs)

        return cls.create_adapter(provider, **config)

    @classmethod
    def _get_api_key(cls, provider: str) -> str:
        """从settings获取API密钥"""
        # Claude/Anthropic: 优先使用 auth_token，然后使用 api_key
        if provider in ["claude", "anthropic"]:
            api_key = settings.anthropic_auth_token or settings.anthropic_api_key
        elif provider in ["openai", "gpt", "chatgpt"]:
            api_key = settings.openai_api_key
        elif provider in ["gemini", "google"]:
            api_key = settings.google_api_key
        else:
            api_key = None

        if not api_key:
            raise ValueError(f"未配置{provider}的API密钥")

        return api_key

    @classmethod
    def _get_default_model(cls, provider: str) -> str:
        """获取默认模型名称"""
        # 优先从settings读取
        model_map = {
            "claude": settings.anthropic_default_model,
            "anthropic": settings.anthropic_default_model,
            "openai": settings.openai_default_model,
            "gpt": settings.openai_default_model,
            "chatgpt": settings.openai_default_model,
            "gemini": settings.google_default_model,
            "google": settings.google_default_model,
        }

        return model_map.get(provider, cls.DEFAULT_MODELS.get(provider, ""))

    @classmethod
    def get_adapter_config(cls, provider: str) -> Dict[str, Any]:
        """
        获取适配器配置

        Args:
            provider: 模型提供商

        Returns:
            配置字典
        """
        provider_lower = provider.lower()

        if provider_lower in ["claude", "anthropic"]:
            # 解析备用模型列表
            fallback_models = []
            if settings.anthropic_fallback_models:
                fallback_models = [
                    m.strip() for m in settings.anthropic_fallback_models.split(",")
                    if m.strip()
                ]

            # 获取代理配置
            proxies = settings.get_proxy_config("claude")

            print(f"[FACTORY] Claude config: fallback_models={fallback_models}, enable_fallback={settings.anthropic_enable_fallback}, proxies={proxies}")

            config = {
                "temperature": settings.anthropic_temperature,
                "max_tokens": settings.anthropic_max_tokens,
                "base_url": settings.anthropic_base_url,
                "fallback_models": fallback_models,
                "enable_fallback": settings.anthropic_enable_fallback,
            }

            # 如果启用了代理，添加到配置中
            if proxies:
                config["proxies"] = proxies

            return config

        elif provider_lower in ["openai", "gpt", "chatgpt"]:
            # 获取代理配置
            proxies = settings.get_proxy_config("openai")

            config = {
                "temperature": settings.openai_temperature,
                "max_tokens": settings.openai_max_tokens,
                "organization": settings.openai_org_id,
            }

            if proxies:
                config["proxies"] = proxies

            return config

        elif provider_lower in ["gemini", "google"]:
            # 获取代理配置
            proxies = settings.get_proxy_config("google")

            config = {
                "temperature": settings.google_temperature,
                "max_tokens": settings.google_max_tokens,
            }

            if proxies:
                config["proxies"] = proxies

            return config

        else:
            return {}

    @classmethod
    def list_supported_providers(cls) -> list:
        """列出当前可用的提供商"""
        adapter_map = cls._build_adapter_map()
        return list(set(adapter_map.keys()))

    @classmethod
    def is_provider_configured(cls, provider: str) -> bool:
        """检查提供商是否已配置"""
        try:
            cls._get_api_key(provider.lower())
            return True
        except ValueError:
            return False

    @classmethod
    def is_provider_available(cls, provider: str) -> bool:
        """检查提供商的适配器是否可用"""
        adapter_map = cls._build_adapter_map()
        return provider.lower() in adapter_map


# 示例用法
if __name__ == "__main__":
    # 创建Claude适配器
    claude_adapter = ModelAdapterFactory.create_adapter(
        provider="claude",
        api_key="your-api-key"
    )
    print(f"Claude模型: {claude_adapter.get_model_name()}")

    # 从settings创建适配器
    default_adapter = ModelAdapterFactory.create_from_settings()
    print(f"默认模型: {default_adapter.get_model_name()}")

    # 列出支持的提供商
    providers = ModelAdapterFactory.list_supported_providers()
    print(f"当前可用的提供商: {providers}")

    # 检查配置
    for provider in ["claude", "openai", "gemini"]:
        available = ModelAdapterFactory.is_provider_available(provider)
        configured = ModelAdapterFactory.is_provider_configured(provider) if available else False
        print(f"{provider} - 适配器可用: {available}, 已配置: {configured}")
