"""
OpenAI模型适配器

支持OpenAI GPT系列模型（含 OpenAI-compatible 代理）
"""
from typing import Dict, Any, AsyncIterator, Optional
import json
import httpx
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
        base_url = kwargs.get("base_url")
        self.base_url = base_url.rstrip("/") if base_url else "https://api.openai.com/v1"
        self.api_key = api_key
        self.client = AsyncOpenAI(
            api_key=api_key,
            organization=kwargs.get("organization"),
            base_url=base_url if base_url else None,
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
                "role": str(msg.role),  # MessageRole(str, Enum) 可能已反序列化为普通 str
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

    # ------------------------------------------------------------------
    # AgenticLoop 接口（与 QianwenAdapter / ClaudeAdapter 相同签名）
    # 内部全部转换为 OpenAI 格式，响应转回 Anthropic 格式
    # ------------------------------------------------------------------

    @staticmethod
    def _anthropic_tools_to_openai(tools: list) -> list:
        result = []
        for t in tools:
            result.append({
                "type": "function",
                "function": {
                    "name": t.get("name", ""),
                    "description": t.get("description", ""),
                    "parameters": t.get("input_schema", {"type": "object", "properties": {}}),
                },
            })
        return result

    @staticmethod
    def _anthropic_messages_to_openai(messages: list, system_prompt: str) -> list:
        openai_msgs: list = []
        if system_prompt:
            openai_msgs.append({"role": "system", "content": system_prompt})

        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")

            if isinstance(content, str):
                openai_msgs.append({"role": role, "content": content})
                continue

            tool_calls = []
            text_parts = []
            tool_results = []

            for block in content:
                if not isinstance(block, dict):
                    continue
                btype = block.get("type", "")
                if btype == "text":
                    text_parts.append(block.get("text", ""))
                elif btype == "tool_use":
                    args = block.get("input", {})
                    tool_calls.append({
                        "id": block.get("id", ""),
                        "type": "function",
                        "function": {
                            "name": block.get("name", ""),
                            "arguments": json.dumps(args, ensure_ascii=False),
                        },
                    })
                elif btype == "tool_result":
                    tool_results.append({
                        "role": "tool",
                        "tool_call_id": block.get("tool_use_id", ""),
                        "content": block.get("content", ""),
                    })

            if tool_results:
                openai_msgs.extend(tool_results)
            else:
                out: dict = {"role": role, "content": " ".join(text_parts) or None}
                if tool_calls:
                    out["tool_calls"] = tool_calls
                openai_msgs.append(out)

        return openai_msgs

    @staticmethod
    def _openai_response_to_anthropic(data: dict) -> dict:
        _finish_to_stop = {
            "stop": "end_turn",
            "tool_calls": "tool_use",
            "length": "max_tokens",
        }
        choices = data.get("choices", [])
        if not choices:
            return {"stop_reason": "end_turn", "content": []}

        choice = choices[0]
        stop_reason = _finish_to_stop.get(choice.get("finish_reason", "stop"), "end_turn")
        message = choice.get("message", {})
        content_blocks: list = []

        text = message.get("content") or ""
        if text:
            content_blocks.append({"type": "text", "text": text})

        for tc in message.get("tool_calls", []) or []:
            func = tc.get("function", {})
            try:
                input_args = json.loads(func.get("arguments", "{}"))
            except (json.JSONDecodeError, TypeError):
                input_args = {}
            content_blocks.append({
                "type": "tool_use",
                "id": tc.get("id", ""),
                "name": func.get("name", ""),
                "input": input_args,
            })

        return {"stop_reason": stop_reason, "content": content_blocks}

    async def _call_openai_http(self, body: dict) -> dict:
        """直接用 httpx 调用 OpenAI-compatible 接口，返回原始 JSON dict。"""
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(url, headers=headers, json=body)
            resp.raise_for_status()
            data = resp.json()
        if "error" in data:
            raise RuntimeError(f"API error: {data['error'].get('message', data['error'])}")
        return data

    async def chat_with_tools(
        self,
        messages: list,
        system_prompt: str,
        tools: list,
        **kwargs,
    ) -> dict:
        """AgenticLoop 接口 — 支持工具调用，输入输出均为 Anthropic 格式。"""
        self.validate_config()
        temperature = float(kwargs.get("temperature", self.config.get("temperature", 0.7)))
        max_tokens = int(kwargs.get("max_tokens", self.config.get("max_tokens", 4096)))

        openai_messages = self._anthropic_messages_to_openai(messages, system_prompt)
        openai_tools = self._anthropic_tools_to_openai(tools) if tools else []

        body: dict = {
            "model": self.model,
            "messages": openai_messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if openai_tools:
            body["tools"] = openai_tools

        data = await self._call_openai_http(body)
        return self._openai_response_to_anthropic(data)

    async def chat_plain(
        self,
        messages: list,
        system_prompt: str,
        **kwargs,
    ) -> dict:
        """AgenticLoop 接口 — 无工具纯文本调用，输入输出均为 Anthropic 格式。"""
        self.validate_config()
        temperature = float(kwargs.get("temperature", self.config.get("temperature", 0.7)))
        max_tokens = int(kwargs.get("max_tokens", self.config.get("max_tokens", 4096)))

        openai_messages = self._anthropic_messages_to_openai(messages, system_prompt)

        body: dict = {
            "model": self.model,
            "messages": openai_messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        data = await self._call_openai_http(body)
        return self._openai_response_to_anthropic(data)


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
