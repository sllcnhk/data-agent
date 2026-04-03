"""
模型适配器基类

定义所有模型适配器的统一接口
"""
from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional, AsyncIterator
from backend.core.conversation_format import UnifiedConversation, UnifiedMessage


class BaseModelAdapter(ABC):
    """模型适配器基类"""

    def __init__(self, api_key: str, **kwargs):
        """
        初始化适配器

        Args:
            api_key: API密钥
            **kwargs: 其他配置参数
        """
        self.api_key = api_key
        self.config = kwargs

    @abstractmethod
    def get_model_name(self) -> str:
        """获取模型名称"""
        pass

    @abstractmethod
    def convert_to_native_format(
        self,
        conversation: UnifiedConversation
    ) -> Dict[str, Any]:
        """
        将统一格式转换为模型的原生格式

        Args:
            conversation: 统一对话格式

        Returns:
            模型原生格式的请求参数
        """
        pass

    @abstractmethod
    def convert_from_native_format(
        self,
        response: Any
    ) -> UnifiedMessage:
        """
        将模型的原生响应转换为统一格式

        Args:
            response: 模型原生响应

        Returns:
            统一消息格式
        """
        pass

    @abstractmethod
    async def chat(
        self,
        conversation: UnifiedConversation,
        **kwargs
    ) -> UnifiedMessage:
        """
        同步对话

        Args:
            conversation: 对话历史
            **kwargs: 其他参数(temperature, max_tokens等)

        Returns:
            模型响应消息
        """
        pass

    @abstractmethod
    async def stream_chat(
        self,
        conversation: UnifiedConversation,
        **kwargs
    ) -> AsyncIterator[str]:
        """
        流式对话

        Args:
            conversation: 对话历史
            **kwargs: 其他参数

        Yields:
            流式响应的文本片段
        """
        pass

    def validate_config(self):
        """验证配置"""
        if not self.api_key:
            raise ValueError(f"{self.get_model_name()} API密钥未配置")

    def get_default_params(self) -> Dict[str, Any]:
        """获取默认参数"""
        # 确保类型正确（从配置读取的可能是字符串）
        temperature = self.config.get("temperature", 0.7)
        max_tokens = self.config.get("max_tokens", 8192)

        try:
            temperature = float(temperature) if not isinstance(temperature, float) else temperature
        except (ValueError, TypeError):
            temperature = 0.7

        try:
            max_tokens = int(max_tokens) if not isinstance(max_tokens, int) else max_tokens
        except (ValueError, TypeError):
            max_tokens = 8192

        return {
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

    def estimate_tokens(self, text: str) -> int:
        """
        估算Token数量(简单估算)

        Args:
            text: 文本内容

        Returns:
            估算的Token数量
        """
        # 简单估算: 1 token ≈ 4 characters (英文)
        # 中文大约 1 token ≈ 1.5-2 characters
        # 这里使用保守估算
        return len(text) // 3

    def calculate_cost(self, input_tokens: int, output_tokens: int) -> float:
        """
        计算成本

        Args:
            input_tokens: 输入Token数
            output_tokens: 输出Token数

        Returns:
            成本(美元)
        """
        # 子类应该覆盖此方法提供具体的定价
        return 0.0

    async def __aenter__(self):
        """异步上下文管理器入口"""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """异步上下文管理器出口"""
        pass
