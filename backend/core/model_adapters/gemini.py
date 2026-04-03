"""
Google Gemini模型适配器

支持Google Gemini系列模型
"""
from typing import Dict, Any, AsyncIterator
import asyncio
import google.generativeai as genai

from backend.core.model_adapters.base import BaseModelAdapter
from backend.core.conversation_format import (
    UnifiedConversation,
    UnifiedMessage,
    MessageRole
)


class GeminiAdapter(BaseModelAdapter):
    """Gemini模型适配器"""

    def __init__(self, api_key: str, **kwargs):
        super().__init__(api_key, **kwargs)
        genai.configure(api_key=api_key)
        self.model_name = kwargs.get("model", "gemini-pro")
        self.model = genai.GenerativeModel(self.model_name)

    def get_model_name(self) -> str:
        """获取模型名称"""
        return self.model_name

    def convert_to_native_format(
        self,
        conversation: UnifiedConversation
    ) -> Dict[str, Any]:
        """
        将统一格式转换为Gemini格式

        Gemini格式:
        - contents: [{"role": "user|model", "parts": [{"text": "..."}]}]
        """
        # Gemini的角色映射
        role_map = {
            MessageRole.USER: "user",
            MessageRole.ASSISTANT: "model",
            MessageRole.SYSTEM: "user",  # Gemini不支持system,映射为user
        }

        contents = []

        # 系统提示词将通过system_instruction参数传递,不添加到消息中

        # 转换消息
        for msg in conversation.messages:
            if msg.role == MessageRole.TOOL:
                # Gemini暂不支持工具调用,跳过
                continue

            gemini_role = role_map.get(msg.role, "user")
            contents.append({
                "role": gemini_role,
                "parts": [{"text": msg.content}]
            })

        # Gemini要求最后一条消息必须是user
        if contents and contents[-1]["role"] != "user":
            # 如果最后一条不是user,添加一个占位消息
            contents.append({
                "role": "user",
                "parts": [{"text": "请继续。"}]
            })

        return {"contents": contents}

    def convert_from_native_format(
        self,
        response: Any
    ) -> UnifiedMessage:
        """将Gemini响应转换为统一格式"""
        # 提取文本内容
        content = ""
        if hasattr(response, "text"):
            content = response.text
        elif hasattr(response, "parts"):
            content = "".join([part.text for part in response.parts if hasattr(part, "text")])

        # 估算token数
        token_count = self.estimate_tokens(content)

        message = UnifiedMessage(
            role=MessageRole.ASSISTANT,
            content=content,
            model=self.model_name,
            token_count=token_count,
            metadata={
                "usage": {
                    "output_tokens": token_count,
                }
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
            # 转换为Gemini格式
            request_params = self.convert_to_native_format(conversation)

            # 配置生成参数
            generation_config = {
                "temperature": kwargs.get("temperature", self.config.get("temperature", 0.7)),
                "max_output_tokens": kwargs.get("max_tokens", self.config.get("max_tokens", 2048)),
            }

            # 如果有系统提示词,需要使用带system_instruction的模型
            if conversation.system_prompt:
                model_with_system = genai.GenerativeModel(
                    self.model_name,
                    system_instruction=conversation.system_prompt
                )
                # 调用API
                response = await asyncio.to_thread(
                    model_with_system.generate_content,
                    contents=request_params["contents"],
                    generation_config=generation_config
                )
            else:
                # 调用API
                # 使用asyncio.to_thread()在线程池中执行同步调用,避免阻塞事件循环
                response = await asyncio.to_thread(
                    self.model.generate_content,
                    contents=request_params["contents"],
                    generation_config=generation_config
                )

            # 转换响应
            message = self.convert_from_native_format(response)

            return message
        except Exception as e:
            raise RuntimeError(f"Gemini API调用失败: {str(e)}") from e

    async def stream_chat(
        self,
        conversation: UnifiedConversation,
        **kwargs
    ) -> AsyncIterator[str]:
        """流式对话"""
        self.validate_config()

        try:
            # 转换为Gemini格式
            request_params = self.convert_to_native_format(conversation)

            # 配置生成参数
            generation_config = {
                "temperature": kwargs.get("temperature", self.config.get("temperature", 0.7)),
                "max_output_tokens": kwargs.get("max_tokens", self.config.get("max_tokens", 2048)),
            }

            # 如果有系统提示词,需要使用带system_instruction的模型
            if conversation.system_prompt:
                model_with_system = genai.GenerativeModel(
                    self.model_name,
                    system_instruction=conversation.system_prompt
                )
                # 调用流式API
                response_iter = await asyncio.to_thread(
                    model_with_system.generate_content,
                    contents=request_params["contents"],
                    generation_config=generation_config,
                    stream=True
                )
            else:
                # 调用流式API
                # 使用asyncio.to_thread()在线程池中执行同步流式调用
                response_iter = await asyncio.to_thread(
                    self.model.generate_content,
                    contents=request_params["contents"],
                    generation_config=generation_config,
                    stream=True
                )

            # 在线程中迭代同步流
            for chunk in response_iter:
                if hasattr(chunk, "text"):
                    yield chunk.text
        except Exception as e:
            raise RuntimeError(f"Gemini流式API调用失败: {str(e)}") from e

    def calculate_cost(self, input_tokens: int, output_tokens: int) -> float:
        """
        计算成本

        Gemini Pro定价(2024年):
        - 免费使用(有配额限制)
        - 付费版: $0.5 / 1M tokens (input和output相同)
        """
        if "pro" in self.model_name.lower():
            # Gemini Pro
            total_tokens = input_tokens + output_tokens
            return (total_tokens / 1_000_000) * 0.5
        else:
            # 默认使用Pro定价
            total_tokens = input_tokens + output_tokens
            return (total_tokens / 1_000_000) * 0.5


# 示例用法
if __name__ == "__main__":
    import asyncio
    from backend.core.conversation_format import UnifiedConversation

    async def test_gemini():
        # 创建对话
        conversation = UnifiedConversation(
            system_prompt="你是一个有帮助的AI助手。"
        )
        conversation.add_user_message("你好,请介绍一下自己。")

        # 创建适配器
        adapter = GeminiAdapter(
            api_key="your-api-key",
            model="gemini-pro"
        )

        # 同步对话
        response = await adapter.chat(conversation)
        print("Response:", response.content)
        print("Tokens:", response.token_count)

        # 流式对话
        conversation.add_user_message("请用一句话总结你的能力。")
        async for chunk in adapter.stream_chat(conversation):
            print(chunk, end="", flush=True)

    # asyncio.run(test_gemini())
