"""
豆包(Doubao)模型适配器

支持字节跳动豆包系列模型
"""
from typing import Dict, Any, AsyncIterator
import httpx
import json

from backend.core.model_adapters.base import BaseModelAdapter
from backend.core.conversation_format import (
    UnifiedConversation,
    UnifiedMessage,
    MessageRole,
)


class DoubaoAdapter(BaseModelAdapter):
    """豆包模型适配器"""

    def __init__(self, api_key: str, **kwargs):
        super().__init__(api_key, **kwargs)
        self.base_url = kwargs.get("base_url", "https://ark.cn-beijing.volces.com/api/v3")
        self.model = kwargs.get("model", "doubao-pro")
        self.api_secret = kwargs.get("api_secret", "")  # 部分接口可能需要

    def get_model_name(self) -> str:
        """获取模型名称"""
        return self.model

    def convert_to_native_format(
        self,
        conversation: UnifiedConversation
    ) -> Dict[str, Any]:
        """
        将统一格式转换为豆包格式

        豆包格式类似OpenAI:
        - messages: [{"role": "system|user|assistant", "content": "..."}]
        """
        messages = []

        for msg in conversation.messages:
            doubao_msg = {
                "role": msg.role.value,
                "content": msg.content
            }
            messages.append(doubao_msg)

        # 如果有系统提示词,添加到第一条消息
        if conversation.system_prompt:
            messages.insert(0, {
                "role": "system",
                "content": conversation.system_prompt
            })

        # 确保参数类型正确
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

        result = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        return result

    def convert_from_native_format(
        self,
        response: Dict[str, Any]
    ) -> UnifiedMessage:
        """将豆包响应转换为统一格式"""
        usage = response.get("usage", {})

        # 提取消息内容
        choices = response.get("choices", [])
        content = ""
        if choices:
            message = choices[0].get("message", {})
            content = message.get("content", "")

        message = UnifiedMessage(
            role=MessageRole.ASSISTANT,
            content=content,
            model=self.model,
            token_count=usage.get("completion_tokens", 0),
            metadata={
                "usage": {
                    "input_tokens": usage.get("prompt_tokens", 0),
                    "output_tokens": usage.get("completion_tokens", 0),
                    "total_tokens": usage.get("total_tokens", 0),
                },
                "request_id": response.get("id"),
                "finish_reason": choices[0].get("finish_reason") if choices else None,
            }
        )

        return message

    async def chat(
        self,
        conversation: UnifiedConversation,
        **kwargs
    ) -> UnifiedMessage:
        """同步对话"""
        self.validate_config()

        try:
            # 转换为豆包格式
            request_body = self.convert_to_native_format(conversation)

            # 合并用户提供的参数
            request_body.update(kwargs)

            # 调用API
            url = f"{self.base_url}/chat/completions"
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"
            }

            async with httpx.AsyncClient() as client:
                response = await client.post(
                    url,
                    headers=headers,
                    json=request_body,
                    timeout=60.0
                )
                response.raise_for_status()
                result = response.json()

            # 检查错误
            if "error" in result:
                error_msg = result["error"].get("message", "Unknown error")
                raise Exception(f"Doubao API Error: {error_msg}")

            # 转换响应
            message = self.convert_from_native_format(result)

            return message
        except Exception as e:
            raise RuntimeError(f"豆包API调用失败: {str(e)}") from e

    async def stream_chat(
        self,
        conversation: UnifiedConversation,
        **kwargs
    ) -> AsyncIterator[str]:
        """流式对话"""
        self.validate_config()

        try:
            # 转换为豆包格式
            request_body = self.convert_to_native_format(conversation)
            request_body.update(kwargs)
            request_body["stream"] = True

            # 调用流式API
            url = f"{self.base_url}/chat/completions"
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"
            }

            async with httpx.AsyncClient() as client:
                async with client.stream(
                    "POST",
                    url,
                    headers=headers,
                    json=request_body,
                    timeout=60.0
                ) as response:
                    response.raise_for_status()

                    async for line in response.aiter_lines():
                        if not line or not line.startswith("data:"):
                            continue

                        try:
                            # 解析SSE数据
                            data_str = line[5:].strip()  # 去掉 "data:" 前缀
                            if data_str == "[DONE]":
                                break

                            data = json.loads(data_str)

                            # 检查错误
                            if "error" in data:
                                error_msg = data["error"].get("message", "Unknown error")
                                raise Exception(f"Doubao API Error: {error_msg}")

                            # 提取文本内容
                            choices = data.get("choices", [])
                            if choices:
                                delta = choices[0].get("delta", {})
                                content = delta.get("content", "")
                                if content:
                                    yield content

                        except json.JSONDecodeError:
                            continue
        except Exception as e:
            raise RuntimeError(f"豆包流式API调用失败: {str(e)}") from e

    def calculate_cost(self, input_tokens: int, output_tokens: int) -> float:
        """
        计算成本

        豆包定价(示例,需要根据实际情况调整):
        - doubao-pro: Input $0.008/1K, Output $0.024/1K
        """
        input_cost = (input_tokens / 1_000) * 0.008
        output_cost = (output_tokens / 1_000) * 0.024

        return input_cost + output_cost


# 示例用法
if __name__ == "__main__":
    import asyncio
    from backend.core.conversation_format import UnifiedConversation

    async def test_doubao():
        # 创建对话
        conversation = UnifiedConversation(
            system_prompt="你是一个有帮助的AI助手。"
        )
        conversation.add_user_message("你好,请介绍一下自己。")

        # 创建适配器
        adapter = DoubaoAdapter(
            api_key="your-api-key",
            model="doubao-pro"
        )

        # 同步对话
        response = await adapter.chat(conversation)
        print("Response:", response.content)
        print("Tokens:", response.token_count)

    # asyncio.run(test_doubao())
