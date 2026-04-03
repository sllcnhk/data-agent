"""
Claude模型适配器

支持Anthropic Claude系列模型
"""
from typing import Dict, Any, AsyncIterator
import httpx
import asyncio
import logging

from backend.core.model_adapters.base import BaseModelAdapter
from backend.core.conversation_format import (
    UnifiedConversation,
    UnifiedMessage,
    MessageRole,
    ToolCall,
    Artifact
)

logger = logging.getLogger(__name__)


class ClaudeAdapter(BaseModelAdapter):
    """Claude模型适配器"""

    def __init__(self, api_key: str, **kwargs):
        super().__init__(api_key, **kwargs)

        # 获取 base_url，默认使用 Anthropic 官方 API
        base_url = kwargs.get("base_url", "https://api.anthropic.com")

        logger.info(f"[INIT] Initializing Claude adapter (HTTP mode)")
        logger.info(f"[INIT] base_url: {base_url}")
        logger.info(f"[INIT] api_key: {api_key[:20]}..." if api_key else "[INIT] api_key: None")

        # 使用 HTTP 客户端直接调用（绕过 anthropic 库）
        self.base_url = base_url.rstrip('/')
        self.api_key = api_key
        self.model = kwargs.get("model", "claude")
        logger.info(f"[INIT] Model: {self.model}")
        logger.info(f"[INIT] Using direct HTTP client (bypassing anthropic library)")

    def get_model_name(self) -> str:
        """获取模型名称"""
        return self.model

    def convert_to_native_format(
        self,
        conversation: UnifiedConversation
    ) -> Dict[str, Any]:
        """
        将统一格式转换为Claude格式

        Claude格式:
        - system: 单独的系统提示词
        - messages: [{"role": "user|assistant", "content": "..."}]
        """
        messages = []
        logger.info(f"[DEBUG] convert_to_native_format: conversation has {len(conversation.messages)} messages")

        for i, msg in enumerate(conversation.messages):
            logger.info(f"[DEBUG] Processing message {i}: role type={type(msg.role)}, role={msg.role}")

            # 跳过system角色(单独处理)
            if msg.role == MessageRole.SYSTEM:
                continue

            # 获取 role 字符串值
            # 如果是枚举，使用 .value；如果已经是字符串，直接使用
            if isinstance(msg.role, MessageRole):
                role_str = msg.role.value
                logger.info(f"[DEBUG] msg.role is MessageRole enum, value={role_str}")
            elif isinstance(msg.role, str):
                role_str = msg.role
                logger.info(f"[DEBUG] msg.role is already string: {role_str}")
            else:
                logger.error(f"[DEBUG] Unexpected role type: {type(msg.role)}, value={msg.role}")
                role_str = str(msg.role)

            claude_msg = {
                "role": role_str,
                "content": msg.content
            }

            # 处理工具调用
            if msg.has_tool_calls():
                # Claude使用tool_use content block
                content_blocks = [{"type": "text", "text": msg.content}]
                for tool_call in msg.tool_calls:
                    content_blocks.append({
                        "type": "tool_use",
                        "id": tool_call.id,
                        "name": tool_call.name,
                        "input": tool_call.arguments
                    })
                claude_msg["content"] = content_blocks

            messages.append(claude_msg)
            logger.info(f"[DEBUG] Added claude_msg with role={claude_msg['role']}")

        logger.info(f"[DEBUG] Converted {len(messages)} messages to Claude format")

        result = {
            "model": self.model,
            "messages": messages,
            **self.get_default_params()
        }

        # 添加系统提示词
        if conversation.system_prompt:
            result["system"] = conversation.system_prompt

        return result

    def convert_from_native_format(
        self,
        response: Any
    ) -> UnifiedMessage:
        """将Claude响应转换为统一格式"""
        # 提取文本内容
        content = ""
        tool_calls = []
        artifacts = []

        for block in response.content:
            if block.type == "text":
                content += block.text
            elif block.type == "tool_use":
                # 转换工具调用
                tool_calls.append(
                    ToolCall(
                        id=block.id,
                        name=block.name,
                        arguments=block.input
                    )
                )

        message = UnifiedMessage(
            role=MessageRole.ASSISTANT,
            content=content,
            tool_calls=tool_calls if tool_calls else None,
            artifacts=artifacts if artifacts else None,
            model=response.model,
            token_count=response.usage.output_tokens if response.usage else None,
            metadata={
                "usage": {
                    "input_tokens": response.usage.input_tokens if response.usage else 0,
                    "output_tokens": response.usage.output_tokens if response.usage else 0,
                },
                "stop_reason": response.stop_reason,
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
            # 转换为Claude格式
            request_params = self.convert_to_native_format(conversation)
            logger.info(f"[DEBUG] Request params keys: {request_params.keys()}")
            logger.info(f"[DEBUG] Model: {request_params.get('model')}")
            logger.info(f"[DEBUG] Messages count: {len(request_params.get('messages', []))}")

            # 合并用户提供的参数
            request_params.update(kwargs)

            # anthropic 0.7.7 使用同步 API，需要在异步函数中调用
            # 使用 run_in_executor 来运行同步代码
            import asyncio
            loop = asyncio.get_event_loop()

            # anthropic 0.7.7 的 API 是 client.completion() 或 client.messages()
            # 让我们尝试检测可用的方法
            logger.info(f"[DEBUG] Client type: {type(self.client)}")
            logger.info(f"[DEBUG] Client attributes: {dir(self.client)}")

            if hasattr(self.client, 'messages'):
                logger.info("[DEBUG] Using client.messages API")
                response = await loop.run_in_executor(
                    None,
                    lambda: self.client.messages.create(**request_params)
                )
            elif hasattr(self.client, 'completions'):
                logger.info("[DEBUG] Using client.completions API (0.7.7)")
                # anthropic 0.7.7 使用 completions.create()
                # 格式不同，需要转换参数
                prompt = self._convert_messages_to_prompt(request_params['messages'])
                system_prompt = request_params.get('system', '')
                if system_prompt:
                    prompt = system_prompt + prompt

                logger.info(f"[DEBUG] Calling completions.create with prompt length: {len(prompt)}")
                logger.info(f"[DEBUG] Model: {request_params['model']}")
                logger.info(f"[DEBUG] Client base_url: {getattr(self.client, 'base_url', 'not set')}")
                logger.info(f"[DEBUG] Client _base_url: {getattr(self.client, '_base_url', 'not set')}")

                # 检查 completions 对象
                if hasattr(self.client.completions, '__dict__'):
                    logger.info(f"[DEBUG] Completions attributes: {self.client.completions.__dict__}")

                # 准备 API 调用参数
                api_params = {
                    "prompt": prompt,
                    "model": request_params['model'],
                    "max_tokens_to_sample": request_params.get('max_tokens', 1024),
                    "temperature": request_params.get('temperature', 0.7)
                }
                logger.info(f"[DEBUG] API params: {api_params}")

                # 转换为anthropic 0.7.7支持的格式
                api_params_77 = {
                    "prompt": prompt,
                    "model": request_params['model'],
                    "max_tokens_to_sample": request_params.get('max_tokens', 1024),
                    "temperature": request_params.get('temperature', 0.7)
                }
                logger.info(f"[DEBUG] Calling anthropic 0.7.7 API with params: {api_params_77}")

                try:
                    response = await loop.run_in_executor(
                        None,
                        lambda: self.client.completions.create(**api_params_77)
                    )
                    logger.info(f"[DEBUG] API call successful, response type: {type(response)}")
                except Exception as api_error:
                    logger.error(f"[DEBUG] API call failed: {type(api_error).__name__}: {api_error}")
                    logger.error(f"[DEBUG] API error details: {getattr(api_error, '__dict__', {})}")

                    # 尝试获取更多错误信息
                    try:
                        import json
                        if hasattr(api_error, 'response'):
                            logger.error(f"[DEBUG] Response status: {api_error.response.status_code}")
                            logger.error(f"[DEBUG] Response body: {api_error.response.text}")
                    except:
                        pass

                    raise
                logger.info(f"[DEBUG] Completions response type: {type(response)}")
                # 将旧格式响应转换为新格式
                response = self._convert_old_response(response)
            else:
                raise RuntimeError(f"Claude client does not have 'messages' or 'completions' method. Available: {[m for m in dir(self.client) if not m.startswith('_')]}")

            logger.info(f"[DEBUG] Response received: type={type(response)}")

            # 转换响应
            message = self.convert_from_native_format(response)

            return message
        except Exception as e:
            logger.error(f"[DEBUG] Exception type: {type(e)}")
            logger.error(f"[DEBUG] Exception: {e}")
            import traceback
            logger.error(f"[DEBUG] Traceback:\n{traceback.format_exc()}")
            raise RuntimeError(f"Claude API调用失败: {str(e)}") from e

    def _convert_messages_to_prompt(self, messages: list) -> str:
        """将消息列表转换为旧版 API 的 prompt 格式"""
        prompt_parts = []
        for msg in messages:
            role = msg['role']
            content = msg['content']
            if role == 'user':
                prompt_parts.append(f"\n\nHuman: {content}")
            elif role == 'assistant':
                prompt_parts.append(f"\n\nAssistant: {content}")
        prompt_parts.append("\n\nAssistant:")
        return "".join(prompt_parts)

    def _convert_old_response(self, response: Any) -> Any:
        """将旧版 API 响应转换为新格式"""
        logger.info(f"[DEBUG] Converting old response, type: {type(response)}")
        logger.info(f"[DEBUG] Response attributes: {dir(response)}")

        # 创建一个模拟新格式的响应对象
        class MockResponse:
            def __init__(self, completion_text, model="claude", stop_reason="end_turn"):
                self.content = [type('obj', (object,), {'type': 'text', 'text': completion_text})]
                self.model = model
                self.usage = None
                self.stop_reason = stop_reason

        # anthropic 0.7.7 返回对象，有 completion 属性
        if hasattr(response, 'completion'):
            completion_text = response.completion
            logger.info(f"[DEBUG] Extracted completion text: {completion_text[:100]}...")
        elif isinstance(response, dict):
            completion_text = response.get('completion', '')
            logger.info(f"[DEBUG] Extracted from dict: {completion_text[:100]}...")
        else:
            logger.error(f"[DEBUG] Unknown response format: {response}")
            completion_text = str(response)

        # 提取 model 和 stop_reason (如果有)
        model = getattr(response, 'model', 'claude')
        stop_reason = getattr(response, 'stop_reason', 'end_turn')

        return MockResponse(completion_text, model, stop_reason)

    async def stream_chat(
        self,
        conversation: UnifiedConversation,
        **kwargs
    ) -> AsyncIterator[str]:
        """流式对话"""
        self.validate_config()

        try:
            # 转换为Claude格式
            request_params = self.convert_to_native_format(conversation)
            request_params.update(kwargs)
            request_params["stream"] = True

            # 调用流式API
            async with self.client.messages.stream(**request_params) as stream:
                async for text in stream.text_stream:
                    yield text
        except Exception as e:
            raise RuntimeError(f"Claude流式API调用失败: {str(e)}") from e

    def calculate_cost(self, input_tokens: int, output_tokens: int) -> float:
        """
        计算成本

        Claude 3.5 Sonnet定价(2024年6月):
        - Input: $3 / 1M tokens
        - Output: $15 / 1M tokens
        """
        if "sonnet" in self.model.lower():
            input_cost = (input_tokens / 1_000_000) * 3.0
            output_cost = (output_tokens / 1_000_000) * 15.0
        elif "opus" in self.model.lower():
            # Opus定价更高
            input_cost = (input_tokens / 1_000_000) * 15.0
            output_cost = (output_tokens / 1_000_000) * 75.0
        elif "haiku" in self.model.lower():
            # Haiku定价更低
            input_cost = (input_tokens / 1_000_000) * 0.25
            output_cost = (output_tokens / 1_000_000) * 1.25
        else:
            # 默认使用Sonnet定价
            input_cost = (input_tokens / 1_000_000) * 3.0
            output_cost = (output_tokens / 1_000_000) * 15.0

        return input_cost + output_cost

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """关闭客户端"""
        await self.client.close()


# 示例用法
if __name__ == "__main__":
    import asyncio
    from backend.core.conversation_format import UnifiedConversation

    async def test_claude():
        # 创建对话
        conversation = UnifiedConversation(
            system_prompt="你是一个有帮助的AI助手。"
        )
        conversation.add_user_message("你好,请介绍一下自己。")

        # 创建适配器
        adapter = ClaudeAdapter(
            api_key="your-api-key",
            model="claude-3-5-sonnet-20240620"
        )

        # 同步对话
        response = await adapter.chat(conversation)
        print("Response:", response.content)
        print("Tokens:", response.token_count)

        # 流式对话
        conversation.add_user_message("请用一句话总结你的能力。")
        async for chunk in adapter.stream_chat(conversation):
            print(chunk, end="", flush=True)

    # asyncio.run(test_claude())
