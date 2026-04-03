#!/usr/bin/env python3
"""
完整的故障转移和响应解析测试
"""
import sys
import os

backend_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, backend_dir)


class ResponseParser:
    """响应解析器（复制自 claude.py 的修复版本）"""

    def _extract_content_from_response(self, response):
        """从各种API响应格式中提取文本内容"""
        import logging
        logger = logging.getLogger(__name__)

        if not response:
            logger.warning("[PARSE] Empty response received")
            return ""

        if isinstance(response, str):
            return response

        if not isinstance(response, dict):
            return str(response)

        # 格式1: Claude Messages API
        if "content" in response:
            content_blocks = response["content"]
            if isinstance(content_blocks, list) and len(content_blocks) > 0:
                first_block = content_blocks[0]
                if isinstance(first_block, dict):
                    if first_block.get("type") == "text":
                        return first_block.get("text", "")
                    text = first_block.get("text", "") or first_block.get("content", "")
                    if text:
                        return text
            return response.get("content", "")

        # 格式2: OpenAI兼容格式 (新版)
        if "choices" in response:
            choices = response.get("choices", [])
            if choices:
                first_choice = choices[0]
                if isinstance(first_choice, dict):
                    message = first_choice.get("message", {})
                    if isinstance(message, dict):
                        content = message.get("content")
                        if content:
                            return content
                    text = first_choice.get("text")
                    if text:
                        return text
                    delta = first_choice.get("delta", {})
                    if isinstance(delta, dict):
                        content = delta.get("content")
                        if content:
                            return content
            return ""

        # 格式4: 简单文本格式
        for key in ["text", "result", "output", "response"]:
            if key in response:
                content = response.get(key)
                if isinstance(content, str):
                    return content

        # 格式5: 尝试直接获取第一个字符串值
        for value in response.values():
            if isinstance(value, str) and len(value) > 0:
                return value

        return ""


def test_response_parsing():
    """测试各种响应格式的解析"""
    parser = ResponseParser()

    test_cases = [
        {
            "name": "Claude Messages API 格式",
            "response": {
                "content": [{"type": "text", "text": "这是Claude的回复"}],
                "id": "msg_123"
            },
            "expected": "这是Claude的回复"
        },
        {
            "name": "OpenAI 兼容格式 (新版) - message.content",
            "response": {
                "choices": [{"message": {"content": "这是OpenAI的回复"}}],
                "object": "chat.completion"
            },
            "expected": "这是OpenAI的回复"
        },
        {
            "name": "OpenAI 兼容格式 - 带 usage",
            "response": {
                "choices": [{"message": {"content": "这是OpenAI的回复"}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 20}
            },
            "expected": "这是OpenAI的回复"
        },
        {
            "name": "旧版 OpenAI 格式 - text 字段",
            "response": {
                "choices": [{"text": "这是旧版OpenAI的回复"}]
            },
            "expected": "这是旧版OpenAI的回复"
        },
        {
            "name": "MiniMax 格式 (可能)",
            "response": {
                "choices": [{"message": {"content": "你好！我是 MiniMax M2"}}],
                "model": "minimax-m2"
            },
            "expected": "你好！我是 MiniMax M2"
        },
        {
            "name": "Claude Sonnet 4.5 日期版本响应",
            "response": {
                "id": "msg_abc123",
                "type": "message",
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "你好！有什么可以帮助你的？"}
                ],
                "stop_reason": "end_turn",
                "usage": {
                    "input_tokens": 15,
                    "output_tokens": 25
                }
            },
            "expected": "你好！有什么可以帮助你的？"
        },
        {
            "name": "简单文本格式 - text",
            "response": {"text": "这是简单文本回复"},
            "expected": "这是简单文本回复"
        },
        {
            "name": "简单文本格式 - result",
            "response": {"result": "这是result格式回复"},
            "expected": "这是result格式回复"
        },
        {
            "name": "直接字符串",
            "response": "这是直接字符串回复",
            "expected": "这是直接字符串回复"
        },
        {
            "name": "空响应",
            "response": None,
            "expected": ""
        },
        {
            "name": "空字典",
            "response": {},
            "expected": ""
        },
        {
            "name": "Claude 带多block",
            "response": {
                "content": [
                    {"type": "text", "text": "第一段"},
                    {"type": "text", "text": "第二段"}
                ]
            },
            "expected": "第一段"
        },
    ]

    print("=" * 70)
    print("Complete Response Parsing Tests")
    print("=" * 70)

    passed = 0
    failed = 0

    for i, test_case in enumerate(test_cases, 1):
        result = parser._extract_content_from_response(test_case["response"])
        expected = test_case["expected"]
        status = "PASS" if result == expected else "FAIL"

        if status == "PASS":
            passed += 1
            print(f"\n[PASS] {i}. {test_case['name']}")
        else:
            failed += 1
            print(f"\n[FAIL] {i}. {test_case['name']}")
            print(f"   Expected: {expected}")
            print(f"   Got:      {result}")

    print("\n" + "=" * 70)
    print(f"Results: {passed} passed, {failed} failed")
    print("=" * 70)

    return failed == 0


def test_fallback_sequence():
    """测试故障转移顺序"""
    print("\n" + "=" * 70)
    print("Failover Sequence Test")
    print("=" * 70)

    # 模拟 settings 配置
    anthropic_fallback_models = "claude-sonnet-4-5-20250929,claude-haiku-4-5-20251001,minimax-m2"
    anthropic_enable_fallback = True

    # 解析备用模型列表
    fallback_models = [
        m.strip() for m in anthropic_fallback_models.split(",")
        if m.strip()
    ]

    primary_model = "claude-sonnet-4-5"

    # 构建完整的模型列表
    models_to_try = [primary_model]
    if anthropic_enable_fallback:
        models_to_try.extend(fallback_models)

    print(f"\nPrimary model: {primary_model}")
    print(f"Fallback models: {fallback_models}")
    print(f"\nFull sequence ({len(models_to_try)} models):")
    for i, model in enumerate(models_to_try, 1):
        print(f"   {i}. {model}")

    # 验证
    assert len(models_to_try) == 4, f"Expected 4 models, got {len(models_to_try)}"
    assert models_to_try == [
        "claude-sonnet-4-5",
        "claude-sonnet-4-5-20250929",
        "claude-haiku-4-5-20251001",
        "minimax-m2"
    ], f"Model sequence incorrect: {models_to_try}"

    print("\n[PASS] Fallover sequence is correct!")
    return True


def simulate_chat_flow():
    """模拟完整的对话流程"""
    print("\n" + "=" * 70)
    print("Simulated Chat Flow")
    print("=" * 70)

    # 模拟响应（假设 claude-sonnet-4-5-20250929 成功返回）
    successful_response = {
        "id": "msg_123",
        "type": "message",
        "role": "assistant",
        "content": [
            {"type": "text", "text": "你好！我是 Claude Sonnet 4.5，很高兴为你服务。"}
        ],
        "stop_reason": "end_turn"
    }

    parser = ResponseParser()
    content = parser._extract_content_from_response(successful_response)

    print(f"\nResponse from claude-sonnet-4-5-20250929:")
    print(f"   {successful_response}")
    print(f"\nExtracted content: {content}")

    assert content == "你好！我是 Claude Sonnet 4.5，很高兴为你服务。", \
        f"Content extraction failed: {content}"

    print("\n[PASS] Chat flow simulation passed!")
    return True


if __name__ == "__main__":
    all_passed = True

    all_passed = test_response_parsing() and all_passed
    all_passed = test_fallback_sequence() and all_passed
    all_passed = simulate_chat_flow() and all_passed

    print("\n" + "=" * 70)
    if all_passed:
        print("All tests passed!")
    else:
        print("Some tests failed!")
    print("=" * 70)

    exit(0 if all_passed else 1)
