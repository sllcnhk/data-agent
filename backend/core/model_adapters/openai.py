"""
OpenAI模型适配器

支持OpenAI GPT系列模型
"""
from typing import Dict, Any, AsyncIterator
import json
from openai import AsyncOpenAI

from backend.core.model_adapters.base import BaseModelAdapter
from backend.core.conversation_format import (
    UnifiedConversation,
    UnifiedMessage,
    MessageRole,
    ToolCall
)


class OpenAIAdapter(BaseModelAdapter):
    """OpenAI模型适配器"""

    def __init__(self, api_key: str, **kwargs):
        super().__init__(api_key, **kwargs)
        self.client = AsyncOpenAI(
            api_key=api_key,
            organization=kwargs.get("organization")
        )
        self.model = kwargs.get("model", "gpt-4-turbo-preview")

    def get_model_name(self) -> str:
        """获取模型名称"""
        return self.model

    def convert_to_native_format(
        self,
        conversation: UnifiedConversation
    ) -> Dict[str, Any]:
        """
        将统一格式转换为OpenAI格式

        OpenAI格式:
        - messages: [{"role": "system|user|assistant|tool", "content": "..."}]
        """
        messages = []

        # 添加系统提示词
        if conversation.system_prompt:
            messages.append({
                "role": "system",
                "content": conversation.system_prompt
            })

        # 转换消息
        for msg in conversation.messages:
            openai_msg = {
                "role": msg.role.value,
                "content": msg.content
            }

            # 处理工具调用
            if msg.has_tool_calls():
                openai_msg["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": str(tc.arguments)
                        }
                    }
                    for tc in msg.tool_calls
                ]

            # 处理工具结果
            if msg.role == MessageRole.TOOL and msg.name:
                openai_msg["name"] = msg.name
                openai_msg["tool_call_id"] = msg.metadata.get("tool_call_id")

            messages.append(openai_msg)

        result = {
            "model": self.model,
            "messages": messages,
            **self.get_default_params()
        }

        return result

    def convert_from_native_format(
        self,
        response: Any
    ) -> UnifiedMessage:
        """将OpenAI响应转换为统一格式"""
        choice = response.choices[0]
        message = choice.message

        # 提取工具调用
        tool_calls = None
        if hasattr(message, "tool_calls") and message.tool_calls:
            tool_calls = [
                ToolCall(
                    id=tc.id,
                    name=tc.function.name,
                    arguments=json.loads(tc.function.arguments)  # 转换JSON字符串为dict
                )
                for tc in message.tool_calls
            ]

        unified_message = UnifiedMessage(
            role=MessageRole.ASSISTANT,
            content=message.content or "",
            tool_calls=tool_calls,
            model=response.model,
            token_count=response.usage.completion_tokens if response.usage else None,
            metadata={
                "usage": {
                    "prompt_tokens": response.usage.prompt_tokens if response.usage else 0,
                    "completion_tokens": response.usage.completion_tokens if response.usage else 0,
                    "total_tokens": response.usage.total_tokens if response.usage else 0,
                },
                "finish_reason": choice.finish_reason,
            }
        )

        return unified_message

    async def chat(
        self,
        conversation: UnifiedConversation,
        **kwargs
    ) -> UnifiedMessage:
        """同步对话"""
        self.validate_config()

        try:
            # 转换为OpenAI格式
            request_params = self.convert_to_native_format(conversation)
            request_params.update(kwargs)

            # 调用API
            response = await self.client.chat.completions.create(**request_params)

            # 转换响应
            message = self.convert_from_native_format(response)

            return message
        except Exception as e:
            raise RuntimeError(f"OpenAI API调用失败: {str(e)}") from e

    async def stream_chat(
        self,
        conversation: UnifiedConversation,
        **kwargs
    ) -> AsyncIterator[str]:
        """流式对话"""
        self.validate_config()

        try:
            # 转换为OpenAI格式
            request_params = self.convert_to_native_format(conversation)
            request_params.update(kwargs)
            request_params["stream"] = True

            # 调用流式API
            stream = await self.client.chat.completions.create(**request_params)

            async for chunk in stream:
                if chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content
        except Exception as e:
            raise RuntimeError(f"OpenAI流式API调用失败: {str(e)}") from e

    def calculate_cost(self, input_tokens: int, output_tokens: int) -> float:
        """
        计算成本

        GPT-4 Turbo定价(2024年):
        - Input: $10 / 1M tokens
        - Output: $30 / 1M tokens

        GPT-3.5 Turbo定价:
        - Input: $0.5 / 1M tokens
        - Output: $1.5 / 1M tokens
        """
        if "gpt-4" in self.model.lower():
            if "turbo" in self.model.lower() or "preview" in self.model.lower():
                # GPT-4 Turbo
                input_cost = (input_tokens / 1_000_000) * 10.0
                output_cost = (output_tokens / 1_000_000) * 30.0
            else:
                # GPT-4 标准版(更贵)
                input_cost = (input_tokens / 1_000_000) * 30.0
                output_cost = (output_tokens / 1_000_000) * 60.0
        elif "gpt-3.5" in self.model.lower():
            input_cost = (input_tokens / 1_000_000) * 0.5
            output_cost = (output_tokens / 1_000_000) * 1.5
        else:
            # 默认使用GPT-4 Turbo定价
            input_cost = (input_tokens / 1_000_000) * 10.0
            output_cost = (output_tokens / 1_000_000) * 30.0

        return input_cost + output_cost

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """关闭客户端"""
        await self.client.close()


# 示例用法
if __name__ == "__main__":
    import asyncio
    from backend.core.conversation_format import UnifiedConversation

    async def test_openai():
        # 创建对话
        conversation = UnifiedConversation(
            system_prompt="你是一个有帮助的AI助手。"
        )
        conversation.add_user_message("你好,请介绍一下自己。")

        # 创建适配器
        adapter = OpenAIAdapter(
            api_key="your-api-key",
            model="gpt-4-turbo-preview"
        )

        # 同步对话
        response = await adapter.chat(conversation)
        print("Response:", response.content)
        print("Tokens:", response.token_count)

        # 流式对话
        conversation.add_user_message("请用一句话总结你的能力。")
        async for chunk in adapter.stream_chat(conversation):
            print(chunk, end="", flush=True)

    # asyncio.run(test_openai())
