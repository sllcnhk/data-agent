"""
Claude模型适配器 - 直接HTTP调用

使用HTTP客户端直接调用中转服务（绕过anthropic库）
"""
from typing import Dict, Any, AsyncIterator, Tuple
import httpx
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
    """Claude模型适配器 - 直接HTTP调用"""

    def __init__(self, api_key: str, **kwargs):
        super().__init__(api_key, **kwargs)

        # 获取 base_url
        base_url = kwargs.get("base_url", "https://api.anthropic.com")

        logger.info(f"[INIT] Initializing Claude adapter (HTTP mode)")
        logger.info(f"[INIT] base_url: {base_url}")
        logger.info(f"[INIT] api_key: {api_key[:20]}..." if api_key else "[INIT] api_key: None")

        # 使用 HTTP 直接调用
        self.base_url = base_url.rstrip('/')
        self.api_key = api_key
        self.model = kwargs.get("model", "claude")

        # 故障转移配置
        self.fallback_models = kwargs.get("fallback_models", [])
        self.enable_fallback = kwargs.get("enable_fallback", True)

        # 代理配置
        self.proxies = kwargs.get("proxies", None)

        logger.info(f"[INIT] Model: {self.model}")
        logger.info(f"[INIT] Fallback models: {self.fallback_models}")
        logger.info(f"[INIT] Fallback enabled: {self.enable_fallback}")
        logger.info(f"[INIT] Proxies: {self.proxies}")
        logger.info(f"[INIT] Using direct HTTP client (bypassing anthropic library)")

    def get_model_name(self) -> str:
        """获取模型名称"""
        return self.model

    def convert_to_native_format(
        self,
        conversation: UnifiedConversation
    ) -> Dict[str, Any]:
        """
        将统一格式转换为Claude格式（用于日志记录）
        """
        messages = []
        logger.info(f"[DEBUG] convert_to_native_format: conversation has {len(conversation.messages)} messages")

        for i, msg in enumerate(conversation.messages):
            logger.info(f"[DEBUG] Processing message {i}: role={msg.role}")

            # 跳过system角色
            if msg.role == MessageRole.SYSTEM:
                continue

            # 跳过空内容的消息
            if not msg.content or not msg.content.strip():
                logger.warning(f"[DEBUG] Skipping message {i} with empty content, role={msg.role}")
                continue

            # 获取 role 字符串值
            if isinstance(msg.role, MessageRole):
                role_str = msg.role.value
            elif isinstance(msg.role, str):
                role_str = msg.role
            else:
                role_str = str(msg.role)

            claude_msg = {
                "role": role_str,
                "content": msg.content.strip()
            }

            messages.append(claude_msg)
            logger.info(f"[DEBUG] Added message with role={claude_msg['role']}, content_length={len(msg.content)}")

        result = {
            "model": self.model,
            "messages": messages,
            "system_prompt": conversation.system_prompt
        }

        logger.info(f"[DEBUG] Converted {len(messages)} messages (skipped empty messages)")

        return result

    def convert_from_native_format(
        self,
        response: Any
    ) -> UnifiedMessage:
        """将响应转换为统一格式"""
        content = self._extract_content_from_response(response)

        message = UnifiedMessage(
            role=MessageRole.ASSISTANT,
            content=content,
            model=self.model,
            metadata={
                "raw_response": response
            }
        )

        return message

    def _extract_content_from_response(self, response: Any) -> str:
        """
        从各种API响应格式中提取文本内容

        支持的格式:
        1. Claude Messages API: {"content": [{"type": "text", "text": "..."}]}
        2. OpenAI兼容格式: {"choices": [{"message": {"content": "..."}}]}
        3. 旧版OpenAI格式: {"choices": [{"text": "..."}]}
        4. 简单文本格式: {"text": "..."} 或 {"result": "..."}
        5. 直接字符串响应
        """
        if not response:
            logger.warning("[PARSE] Empty response received")
            return ""

        # 如果是字符串，直接返回
        if isinstance(response, str):
            logger.info(f"[PARSE] Response is string: {response[:100]}...")
            return response

        if not isinstance(response, dict):
            logger.warning(f"[PARSE] Response is not dict or string: {type(response)}")
            return str(response)

        # 记录响应结构用于调试
        logger.info(f"[PARSE] Response keys: {list(response.keys())}")
        if "choices" in response:
            choices = response["choices"]
            if choices and len(choices) > 0:
                first_choice = choices[0]
                logger.info(f"[PARSE] First choice keys: {list(first_choice.keys()) if isinstance(first_choice, dict) else 'not dict'}")
                if isinstance(first_choice, dict) and "message" in first_choice:
                    logger.info(f"[PARSE] Message keys: {list(first_choice['message'].keys()) if isinstance(first_choice['message'], dict) else 'not dict'}")

        # 格式1: Claude Messages API
        # {"content": [{"type": "text", "text": "..."}]}
        if "content" in response:
            content_blocks = response["content"]
            logger.info(f"[PARSE] Claude format detected, content_blocks type: {type(content_blocks)}")
            if isinstance(content_blocks, list) and len(content_blocks) > 0:
                first_block = content_blocks[0]
                logger.info(f"[PARSE] First block: {first_block}")
                if isinstance(first_block, dict):
                    # Claude 格式
                    if first_block.get("type") == "text":
                        return first_block.get("text", "")
                    # 直接文本
                    text = first_block.get("text", "") or first_block.get("content", "")
                    if text:
                        return text
            return response.get("content", "")

        # 格式2: OpenAI兼容格式 (新版)
        # {"choices": [{"message": {"content": "..."}}]}
        if "choices" in response:
            choices = response.get("choices", [])
            if choices:
                first_choice = choices[0]
                # 尝试 message.content 格式
                if isinstance(first_choice, dict):
                    message = first_choice.get("message", {})
                    if isinstance(message, dict):
                        content = message.get("content")
                        if content:
                            logger.info(f"[PARSE] OpenAI format (new) extracted: {content[:100]}...")
                            return content
                    # 尝试 text 字段（旧版）
                    text = first_choice.get("text")
                    if text:
                        logger.info(f"[PARSE] OpenAI format (old) extracted: {text[:100]}...")
                        return text
                    # 尝试 delta 格式（流式响应）
                    delta = first_choice.get("delta", {})
                    if isinstance(delta, dict):
                        content = delta.get("content")
                        if content:
                            logger.info(f"[PARSE] Streaming format extracted: {content[:100]}...")
                            return content
            logger.warning("[PARSE] choices found but no content extracted")
            return ""

        # 格式4: 简单文本格式
        # {"text": "..."} 或 {"result": "..."}
        for key in ["text", "result", "output", "response"]:
            if key in response:
                content = response.get(key)
                if isinstance(content, str):
                    logger.info(f"[PARSE] Simple format ({key}) extracted: {content[:100]}...")
                    return content

        # 格式5: 尝试直接获取第一个字符串值
        for value in response.values():
            if isinstance(value, str) and len(value) > 0:
                logger.info(f"[PARSE] First string value extracted: {value[:100]}...")
                return value

        logger.warning(f"[PARSE] No content found in response, returning empty string. Response: {str(response)[:200]}")
        return ""

    async def _try_model_request(
        self,
        model_name: str,
        messages: list,
        system_prompt: str,
        **kwargs
    ) -> Tuple[bool, Any]:
        """
        尝试使用指定模型发送请求

        Returns:
            (成功标志, 响应数据或错误信息)
        """
        url = f"{self.base_url}/v1/messages"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "anthropic-version": "2023-06-01"
        }

        # 确保参数类型正确
        max_tokens = kwargs.get('max_tokens', 8192)
        temperature = kwargs.get('temperature', 0.7)

        # 类型转换（从配置读取的可能是字符串）
        try:
            max_tokens = int(max_tokens) if not isinstance(max_tokens, int) else max_tokens
        except (ValueError, TypeError):
            max_tokens = 8192  # Claude Sonnet 4.5 最大输出

        try:
            temperature = float(temperature) if not isinstance(temperature, float) else temperature
        except (ValueError, TypeError):
            temperature = 0.7

        # 构建请求体
        request_body = {
            "model": model_name,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature
        }

        # 如果有 system prompt，添加到请求中
        if system_prompt:
            request_body["system"] = system_prompt

        try:
            # 创建 httpx 客户端，如果配置了代理则使用代理
            client_kwargs = {"timeout": 120.0}
            if self.proxies:
                client_kwargs["proxies"] = self.proxies
                logger.info(f"[TRY_MODEL] Using proxies: {self.proxies}")

            async with httpx.AsyncClient(**client_kwargs) as client:
                logger.info(f"[TRY_MODEL] Attempting with model: {model_name}")
                response = await client.post(url, headers=headers, json=request_body)

                logger.info(f"[TRY_MODEL] Status: {response.status_code}")

                if response.status_code == 200:
                    response_data = response.json()
                    logger.info(f"[TRY_MODEL] ✅ Success with model: {model_name}")
                    return True, response_data
                else:
                    error_text = response.text
                    logger.warning(f"[TRY_MODEL] ❌ Failed with model: {model_name} - {response.status_code}: {error_text}")
                    return False, f"HTTP {response.status_code}: {error_text}"

        except httpx.RequestError as e:
            logger.warning(f"[TRY_MODEL] ❌ Request error with model: {model_name} - {str(e)}")
            return False, f"Request error: {str(e)}"
        except Exception as e:
            logger.warning(f"[TRY_MODEL] ❌ Unexpected error with model: {model_name} - {str(e)}")
            return False, f"Error: {str(e)}"

    async def chat(
        self,
        conversation: UnifiedConversation,
        **kwargs
    ) -> UnifiedMessage:
        """同步对话（支持自动故障转移）"""
        self.validate_config()

        try:
            # 转换为Claude格式
            request_params = self.convert_to_native_format(conversation)
            logger.info(f"[CHAT] Messages count: {len(request_params.get('messages', []))}")

            # 合并用户提供的参数
            request_params.update(kwargs)

            # 构建 Claude Messages API 格式的请求
            messages = request_params['messages']
            system_prompt = request_params.get('system_prompt', '')

            logger.info(f"[CHAT] Base URL: {self.base_url}")
            logger.info(f"[CHAT] Primary model: {request_params['model']}")

            # 构建要尝试的模型列表
            models_to_try = [request_params['model']]

            # 如果启用了故障转移，添加备用模型
            if self.enable_fallback and self.fallback_models:
                models_to_try.extend(self.fallback_models)
                logger.info(f"[CHAT] Fallback enabled, will try {len(models_to_try)} models: {models_to_try}")
            else:
                logger.info(f"[CHAT] Fallback disabled, will only try primary model")

            # 依次尝试每个模型
            errors = []
            for i, model_name in enumerate(models_to_try):
                success, result = await self._try_model_request(
                    model_name=model_name,
                    messages=messages,
                    system_prompt=system_prompt,
                    **kwargs
                )

                if success:
                    # 成功！解析响应
                    response_data = result
                    logger.info(f"[CHAT] ✅ Successfully used model: {model_name}")

                    # 使用统一的响应解析方法
                    completion_text = self._extract_content_from_response(response_data)

                    logger.info(f"[CHAT] Completion text length: {len(completion_text)}")

                    message = UnifiedMessage(
                        role=MessageRole.ASSISTANT,
                        content=completion_text,
                        model=model_name,  # 使用实际成功的模型名称
                        metadata={
                            "raw_response": response_data,
                            "attempted_models": models_to_try[:i+1],  # 记录尝试过的模型
                            "used_fallback": i > 0  # 是否使用了备用模型
                        }
                    )

                    return message
                else:
                    # 失败，记录错误并尝试下一个模型
                    errors.append(f"Model {model_name}: {result}")
                    logger.warning(f"[CHAT] Model {model_name} failed, trying next...")

            # 所有模型都失败了
            logger.error(f"[CHAT] ❌ All models failed!")
            error_summary = "\n".join([f"  {i+1}. {err}" for i, err in enumerate(errors)])
            last_model = models_to_try[-1] if models_to_try else "unknown"
            raise RuntimeError(
                f"Claude API调用失败: 所有 {len(models_to_try)} 个模型均失败\n"
                f"尝试的模型: {models_to_try}\n"
                f"最后一个失败的模型: {last_model}\n"
                f"错误详情:\n{error_summary}"
            )

        except Exception as e:
            logger.error(f"[CHAT] Exception: {e}")
            import traceback
            logger.error(f"[CHAT] Traceback:\n{traceback.format_exc()}")
            raise RuntimeError(f"Claude API调用失败: {str(e)}") from e

    async def chat_with_tools(
        self,
        messages: list,
        system_prompt: str,
        tools: list,
        **kwargs
    ) -> dict:
        """
        Call Claude API in tool_use mode.

        Args:
            messages:      Raw message list [{"role": ..., "content": ...}]
            system_prompt: System prompt string
            tools:         Claude-format tool definitions
            **kwargs:      Optional overrides for max_tokens / temperature

        Returns:
            Raw API response dict with keys: stop_reason, content, model, ...
        """
        url = f"{self.base_url}/v1/messages"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "anthropic-version": "2023-06-01"
        }

        max_tokens = kwargs.get("max_tokens", self.config.get("max_tokens", 8192))
        temperature = kwargs.get("temperature", self.config.get("temperature", 0.7))

        try:
            max_tokens = int(max_tokens)
        except (ValueError, TypeError):
            max_tokens = 8192
        try:
            temperature = float(temperature)
        except (ValueError, TypeError):
            temperature = 0.7

        request_body = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "tools": tools,
        }
        if system_prompt:
            request_body["system"] = system_prompt

        models_to_try = [self.model]
        if self.enable_fallback and self.fallback_models:
            models_to_try.extend(self.fallback_models)

        errors = []
        for model_name in models_to_try:
            request_body["model"] = model_name
            try:
                client_kwargs = {"timeout": 120.0}
                if self.proxies:
                    client_kwargs["proxies"] = self.proxies

                async with httpx.AsyncClient(**client_kwargs) as client:
                    logger.info(
                        f"[TOOLS] Calling {model_name} with {len(tools)} tools"
                    )
                    response = await client.post(
                        url, headers=headers, json=request_body
                    )
                    if response.status_code == 200:
                        logger.info(f"[TOOLS] Success with model: {model_name}")
                        return response.json()
                    else:
                        err = f"Model {model_name}: HTTP {response.status_code}: {response.text}"
                        errors.append(err)
                        logger.warning(f"[TOOLS] {err}")

            except Exception as exc:
                errors.append(f"Model {model_name}: {exc}")
                logger.warning(f"[TOOLS] Error with model {model_name}: {exc}")

        raise RuntimeError(
            f"chat_with_tools failed for all {len(models_to_try)} model(s). "
            f"Errors: {'; '.join(errors)}"
        )

    async def chat_plain(
        self,
        messages: list,
        system_prompt: str,
        **kwargs
    ) -> dict:
        """
        Call Claude API without tools, returning a raw response dict in the
        same format as chat_with_tools() (with stop_reason and content blocks).

        Args:
            messages:      Raw message list
            system_prompt: System prompt string
            **kwargs:      Optional overrides for max_tokens / temperature

        Returns:
            Raw API response dict
        """
        models_to_try = [self.model]
        if self.enable_fallback and self.fallback_models:
            models_to_try.extend(self.fallback_models)

        errors = []
        for model_name in models_to_try:
            success, result = await self._try_model_request(
                model_name=model_name,
                messages=messages,
                system_prompt=system_prompt,
                **kwargs
            )
            if success:
                # If the proxy returns a raw Claude response, return as-is
                if "stop_reason" in result and "content" in result:
                    return result
                # Otherwise wrap extracted text into standard format
                text = self._extract_content_from_response(result)
                return {
                    "stop_reason": "end_turn",
                    "content": [{"type": "text", "text": text}],
                }
            errors.append(f"Model {model_name}: {result}")

        raise RuntimeError(
            f"chat_plain failed for all {len(models_to_try)} model(s). "
            f"Errors: {'; '.join(errors)}"
        )

    async def stream_chat(
        self,
        conversation: UnifiedConversation,
        **kwargs
    ) -> AsyncIterator[str]:
        """流式对话（简化版本，返回完整响应）"""
        message = await self.chat(conversation, **kwargs)
        yield message.content

    def calculate_cost(self, input_tokens: int, output_tokens: int) -> float:
        """计算成本"""
        # 简化版本，实际应从配置中获取
        return 0.0
