"""
千问(Qianwen)模型适配器

支持阿里巴巴通义千问系列模型（OpenAI 兼容接口）

支持的 base_url 格式:
  https://dashscope.aliyuncs.com/compatible-mode/v1  （推荐）
  https://dashscope.aliyuncs.com/api/v1              （旧版，不推荐）

本适配器使用 OpenAI 兼容接口（/chat/completions），与 compatible-mode/v1 端点完全兼容。
"""
from __future__ import annotations

import logging
from typing import Any, AsyncIterator, Dict, List, Optional
import httpx
import json

from backend.core.model_adapters.base import BaseModelAdapter
from backend.core.conversation_format import (
    UnifiedConversation,
    UnifiedMessage,
    MessageRole,
)

logger = logging.getLogger(__name__)


class QianwenAdapter(BaseModelAdapter):
    """千问模型适配器（OpenAI 兼容接口）"""

    # 默认主模型（DashScope API 要求小写模型 ID）
    DEFAULT_MODEL = "qwen3-max"

    def __init__(self, api_key: str, **kwargs):
        super().__init__(api_key, **kwargs)
        # 移除末尾斜杠，避免双斜杠
        raw_base = kwargs.get("base_url", "https://dashscope.aliyuncs.com/compatible-mode/v1")
        self.base_url = raw_base.rstrip("/")
        self.model = kwargs.get("model", self.DEFAULT_MODEL)

        # 故障转移配置
        self.fallback_models: List[str] = kwargs.get("fallback_models", [])
        self.enable_fallback: bool = bool(kwargs.get("enable_fallback", True))

        logger.info("[QianwenAdapter] model=%s fallback=%s enable_fallback=%s",
                    self.model, self.fallback_models, self.enable_fallback)

    def get_model_name(self) -> str:
        return self.model

    # ── 格式转换 ────────────────────────────────────────────────────────────

    def convert_to_native_format(
        self,
        conversation: UnifiedConversation,
        model: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        将统一格式转换为 OpenAI 兼容格式（/chat/completions）。

        注意：msg.role 可能是 MessageRole 枚举或普通字符串（DB 反序列化），
        统一使用 str() 处理。
        """
        messages: List[Dict[str, str]] = []

        if conversation.system_prompt:
            messages.append({"role": "system", "content": conversation.system_prompt})

        for msg in conversation.messages:
            messages.append({
                "role": str(msg.role),   # 兼容 MessageRole 枚举和普通字符串
                "content": msg.content or "",
            })

        temperature = self.config.get("temperature", 0.7)
        max_tokens = self.config.get("max_tokens", 8192)

        try:
            temperature = float(temperature)
        except (ValueError, TypeError):
            temperature = 0.7
        try:
            max_tokens = int(max_tokens)
        except (ValueError, TypeError):
            max_tokens = 8192

        return {
            "model": model or self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

    def convert_from_native_format(self, response: Dict[str, Any]) -> UnifiedMessage:
        """将 OpenAI 兼容格式响应转换为统一格式。"""
        choices = response.get("choices", [])
        content = ""
        if choices:
            content = choices[0].get("message", {}).get("content", "") or ""

        usage = response.get("usage", {})
        return UnifiedMessage(
            role=MessageRole.ASSISTANT,
            content=content,
            model=response.get("model", self.model),
            token_count=usage.get("completion_tokens", 0),
            metadata={
                "usage": {
                    "input_tokens": usage.get("prompt_tokens", 0),
                    "output_tokens": usage.get("completion_tokens", 0),
                    "total_tokens": usage.get("total_tokens", 0),
                },
                "id": response.get("id"),
            },
        )

    # ── HTTP 辅助 ────────────────────────────────────────────────────────────

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _chat_url(self) -> str:
        return f"{self.base_url}/chat/completions"

    # ── 公开接口 ─────────────────────────────────────────────────────────────

    async def chat(
        self,
        conversation: UnifiedConversation,
        **kwargs,
    ) -> UnifiedMessage:
        """非流式对话，支持按顺序尝试主模型 → 备用模型列表。"""
        self.validate_config()

        models_to_try = [self.model]
        if self.enable_fallback and self.fallback_models:
            models_to_try.extend(self.fallback_models)
            logger.info("[QianwenAdapter.chat] Will try models: %s", models_to_try)

        last_error: Optional[Exception] = None
        for i, model_name in enumerate(models_to_try):
            try:
                body = self.convert_to_native_format(conversation, model=model_name)
                if kwargs:
                    body.update(kwargs)

                async with httpx.AsyncClient(timeout=120.0) as client:
                    resp = await client.post(
                        self._chat_url(),
                        headers=self._headers(),
                        json=body,
                    )
                    resp.raise_for_status()
                    data = resp.json()

                # OpenAI 兼容接口错误通常通过 HTTP status 表达，但部分厂商也有 error 字段
                if "error" in data:
                    raise RuntimeError(
                        f"API error: {data['error'].get('message', data['error'])}"
                    )

                msg = self.convert_from_native_format(data)
                if i > 0:
                    logger.info("[QianwenAdapter.chat] Used fallback model: %s", model_name)
                    if msg.metadata:
                        msg.metadata["used_fallback"] = True
                        msg.metadata["primary_model"] = self.model
                return msg

            except Exception as e:
                last_error = e
                logger.warning(
                    "[QianwenAdapter.chat] Model '%s' failed (%d/%d): %s",
                    model_name, i + 1, len(models_to_try), e,
                )
                if i < len(models_to_try) - 1:
                    logger.info("[QianwenAdapter.chat] Trying next model...")

        raise RuntimeError(f"千问API调用失败: {last_error}") from last_error

    async def stream_chat(
        self,
        conversation: UnifiedConversation,
        **kwargs,
    ) -> AsyncIterator[str]:
        """流式对话，支持按顺序尝试主模型 → 备用模型列表。"""
        self.validate_config()

        models_to_try = [self.model]
        if self.enable_fallback and self.fallback_models:
            models_to_try.extend(self.fallback_models)

        last_error: Optional[Exception] = None
        for i, model_name in enumerate(models_to_try):
            try:
                body = self.convert_to_native_format(conversation, model=model_name)
                body["stream"] = True
                if kwargs:
                    body.update(kwargs)

                async with httpx.AsyncClient(timeout=120.0) as client:
                    async with client.stream(
                        "POST",
                        self._chat_url(),
                        headers=self._headers(),
                        json=body,
                    ) as response:
                        response.raise_for_status()

                        async for line in response.aiter_lines():
                            if not line:
                                continue
                            if line.startswith("data:"):
                                data_str = line[5:].strip()
                            else:
                                continue

                            if data_str == "[DONE]":
                                return

                            try:
                                data = json.loads(data_str)
                            except json.JSONDecodeError:
                                continue

                            if "error" in data:
                                raise RuntimeError(
                                    f"API error: {data['error'].get('message', data['error'])}"
                                )

                            choices = data.get("choices", [])
                            if choices:
                                delta = choices[0].get("delta", {})
                                content = delta.get("content", "")
                                if content:
                                    yield content
                # 成功完成流式输出，退出循环
                return

            except Exception as e:
                last_error = e
                logger.warning(
                    "[QianwenAdapter.stream_chat] Model '%s' failed (%d/%d): %s",
                    model_name, i + 1, len(models_to_try), e,
                )
                if i < len(models_to_try) - 1:
                    logger.info("[QianwenAdapter.stream_chat] Trying next model...")

        raise RuntimeError(f"千问流式API调用失败: {last_error}") from last_error

    # ── AgenticLoop 接口（与 ClaudeAdapter 兼容）────────────────────────────
    # AgenticLoop 调用 chat_with_tools() 和 chat_plain()，两者均返回
    # Anthropic 风格的 dict: {"stop_reason": ..., "content": [...blocks...]}
    #
    # 格式转换方向：
    #   Anthropic tool defs  →  OpenAI function defs
    #   Anthropic messages   →  OpenAI messages
    #   OpenAI response      →  Anthropic response

    @staticmethod
    def _anthropic_tools_to_openai(tools: list) -> list:
        """
        转换工具定义格式。
        Anthropic: {"name": ..., "description": ..., "input_schema": {...}}
        OpenAI:    {"type": "function", "function": {"name": ..., "description": ..., "parameters": {...}}}
        """
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
        """
        转换消息列表格式。
        Anthropic content 可以是字符串或 block 列表；
        tool_use block → OpenAI tool_call；tool_result block → OpenAI tool message。
        """
        openai_msgs: list = []
        if system_prompt:
            openai_msgs.append({"role": "system", "content": system_prompt})

        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")

            if isinstance(content, str):
                openai_msgs.append({"role": role, "content": content})
                continue

            # content 是 block 列表
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
                    # Anthropic tool_use → OpenAI tool_call
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
                    # Anthropic tool_result → OpenAI tool message（单独一条）
                    tool_results.append({
                        "role": "tool",
                        "tool_call_id": block.get("tool_use_id", ""),
                        "content": block.get("content", ""),
                    })

            if tool_results:
                # tool_result 消息单独追加（Anthropic user turn with tool results）
                openai_msgs.extend(tool_results)
            else:
                # assistant turn: 可能同时有 text 和 tool_calls
                out: dict = {"role": role, "content": " ".join(text_parts) or None}
                if tool_calls:
                    out["tool_calls"] = tool_calls
                openai_msgs.append(out)

        return openai_msgs

    @staticmethod
    def _openai_response_to_anthropic(data: dict) -> dict:
        """
        转换响应格式。
        OpenAI finish_reason → Anthropic stop_reason
        OpenAI choices[0].message → Anthropic content blocks
        """
        _finish_to_stop = {
            "stop": "end_turn",
            "tool_calls": "tool_use",
            "length": "max_tokens",
        }

        choices = data.get("choices", [])
        if not choices:
            return {"stop_reason": "end_turn", "content": []}

        choice = choices[0]
        finish_reason = choice.get("finish_reason", "stop")
        stop_reason = _finish_to_stop.get(finish_reason, "end_turn")

        message = choice.get("message", {})
        content_blocks: list = []

        # Text content
        text = message.get("content") or ""
        if text:
            content_blocks.append({"type": "text", "text": text})

        # Tool calls
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

    async def _call_with_fallback(self, body: dict) -> dict:
        """
        依次尝试主模型 → 备用模型，返回 Anthropic 格式的响应 dict。
        """
        models_to_try = [self.model]
        if self.enable_fallback and self.fallback_models:
            models_to_try.extend(self.fallback_models)

        last_error: Optional[Exception] = None
        for i, model_name in enumerate(models_to_try):
            body["model"] = model_name
            try:
                async with httpx.AsyncClient(timeout=120.0) as client:
                    resp = await client.post(
                        self._chat_url(),
                        headers=self._headers(),
                        json=body,
                    )
                    resp.raise_for_status()
                    data = resp.json()

                if "error" in data:
                    raise RuntimeError(
                        f"API error: {data['error'].get('message', data['error'])}"
                    )

                if i > 0:
                    logger.info("[QianwenAdapter] Used fallback model: %s", model_name)
                return data

            except Exception as e:
                last_error = e
                logger.warning(
                    "[QianwenAdapter] Model '%s' failed (%d/%d): %s",
                    model_name, i + 1, len(models_to_try), e,
                )

        raise RuntimeError(f"千问API调用失败: {last_error}") from last_error

    async def chat_with_tools(
        self,
        messages: list,
        system_prompt: str,
        tools: list,
        **kwargs,
    ) -> dict:
        """
        AgenticLoop 接口 — 支持工具调用。
        输入/输出格式与 ClaudeAdapter.chat_with_tools() 相同（Anthropic 格式）。
        内部转换为 OpenAI 格式调用 DashScope compatible-mode API。
        """
        self.validate_config()

        temperature = kwargs.get("temperature", self.config.get("temperature", 0.7))
        max_tokens = kwargs.get("max_tokens", self.config.get("max_tokens", 8192))
        try:
            temperature = float(temperature)
        except (ValueError, TypeError):
            temperature = 0.7
        try:
            max_tokens = int(max_tokens)
        except (ValueError, TypeError):
            max_tokens = 8192

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

        data = await self._call_with_fallback(body)
        return self._openai_response_to_anthropic(data)

    async def chat_plain(
        self,
        messages: list,
        system_prompt: str,
        **kwargs,
    ) -> dict:
        """
        AgenticLoop 接口 — 无工具纯文本调用（用于摘要、合成等）。
        输入/输出格式与 ClaudeAdapter.chat_plain() 相同（Anthropic 格式）。
        """
        self.validate_config()

        temperature = kwargs.get("temperature", self.config.get("temperature", 0.7))
        max_tokens = kwargs.get("max_tokens", self.config.get("max_tokens", 8192))
        try:
            temperature = float(temperature)
        except (ValueError, TypeError):
            temperature = 0.7
        try:
            max_tokens = int(max_tokens)
        except (ValueError, TypeError):
            max_tokens = 8192

        openai_messages = self._anthropic_messages_to_openai(messages, system_prompt)

        body: dict = {
            "model": self.model,
            "messages": openai_messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        data = await self._call_with_fallback(body)
        return self._openai_response_to_anthropic(data)

    def calculate_cost(self, input_tokens: int, output_tokens: int) -> float:
        model_lower = self.model.lower()
        if "max" in model_lower:
            return (input_tokens / 1_000) * 0.02 + (output_tokens / 1_000) * 0.06
        elif "plus" in model_lower:
            return (input_tokens / 1_000) * 0.004 + (output_tokens / 1_000) * 0.012
        else:
            return (input_tokens / 1_000) * 0.004 + (output_tokens / 1_000) * 0.012
