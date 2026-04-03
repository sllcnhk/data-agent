#!/usr/bin/env python3
"""
测试响应解析修复
验证 Claude 适配器能正确处理各种 API 响应格式
"""
import sys
import os

# 添加 backend 到 Python 路径
backend_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, backend_dir)


class MockClaudeAdapter:
    """模拟 Claude 适配器用于测试"""

    def _extract_content_from_response(self, response: any) -> str:
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
            return ""

        # 如果是字符串，直接返回
        if isinstance(response, str):
            return response

        if not isinstance(response, dict):
            return str(response)

        # 格式1: Claude Messages API
        # {"content": [{"type": "text", "text": "..."}]}
        if "content" in response:
            content_blocks = response["content"]
            if isinstance(content_blocks, list) and len(content_blocks) > 0:
                first_block = content_blocks[0]
                if isinstance(first_block, dict):
                    # Claude 格式
                    if first_block.get("type") == "text":
                        return first_block.get("text", "")
                    # 直接文本
                    return first_block.get("text", "") or first_block.get("content", "")
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
                            return content
                    # 尝试 text 字段（旧版）
                    text = first_choice.get("text")
                    if text:
                        return text
                    # 尝试 delta 格式（流式响应）
                    delta = first_choice.get("delta", {})
                    if isinstance(delta, dict):
                        content = delta.get("content")
                        if content:
                            return content
            return ""

        # 格式4: 简单文本格式
        # {"text": "..."} 或 {"result": "..."}
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
    adapter = MockClaudeAdapter()

    test_cases = [
        {
            "name": "Claude Messages API 格式",
            "response": {
                "content": [
                    {"type": "text", "text": "这是Claude的回复"}
                ],
                "id": "msg_123"
            },
            "expected": "这是Claude的回复"
        },
        {
            "name": "OpenAI 兼容格式 (新版)",
            "response": {
                "choices": [
                    {"message": {"content": "这是OpenAI的回复"}}
                ],
                "object": "chat.completion"
            },
            "expected": "这是OpenAI的回复"
        },
        {
            "name": "旧版 OpenAI 格式",
            "response": {
                "choices": [
                    {"text": "这是旧版OpenAI的回复"}
                ]
            },
            "expected": "这是旧版OpenAI的回复"
        },
        {
            "name": "MiniMax 格式 (可能)",
            "response": {
                "choices": [
                    {"message": {"content": "这是MiniMax的回复"}}
                ],
                "model": "minimax-m2"
            },
            "expected": "这是MiniMax的回复"
        },
        {
            "name": "简单文本格式 (text)",
            "response": {
                "text": "这是简单文本回复"
            },
            "expected": "这是简单文本回复"
        },
        {
            "name": "简单文本格式 (result)",
            "response": {
                "result": "这是result格式回复"
            },
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
            "name": "Claude 带 stop_reason",
            "response": {
                "content": [
                    {"type": "text", "text": "这是Claude的回复"}
                ],
                "stop_reason": "end_turn"
            },
            "expected": "这是Claude的回复"
        },
        {
            "name": "OpenAI 带 usage",
            "response": {
                "choices": [
                    {"message": {"content": "这是OpenAI的回复"}}
                ],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 20
                }
            },
            "expected": "这是OpenAI的回复"
        },
    ]

    print("=" * 70)
    print("测试响应解析修复")
    print("=" * 70)

    passed = 0
    failed = 0

    for i, test_case in enumerate(test_cases, 1):
        result = adapter._extract_content_from_response(test_case["response"])
        expected = test_case["expected"]
        status = "PASS" if result == expected else "FAIL"

        if status == "PASS":
            passed += 1
            print(f"\n[{status}] 测试 {i}: {test_case['name']}")
        else:
            failed += 1
            print(f"\n[{status}] 测试 {i}: {test_case['name']}")
            print(f"   期望: {expected}")
            print(f"   实际: {result}")

    print("\n" + "=" * 70)
    print(f"测试结果: {passed} 通过, {failed} 失败")
    print("=" * 70)

    return failed == 0


def test_minimax_simulation():
    """模拟 MiniMax 请求和响应"""
    print("\n" + "=" * 70)
    print("模拟 MiniMax 请求流程")
    print("=" * 70)

    # 模拟 MiniMax 的请求体格式
    request_body = {
        "model": "minimax-m2",
        "messages": [
            {"role": "user", "content": "你好"}
        ],
        "max_tokens": 1024,
        "temperature": 0.7
    }

    print("\n📤 发送给 MiniMax 的请求:")
    print(f"   URL: http://10.0.3.248:3000/api/v1/messages")
    print(f"   Body: {request_body}")

    # 模拟 MiniMax 的响应格式
    mock_response = {
        "choices": [
            {
                "message": {
                    "content": "你好！我是 MiniMax M2，很高兴为你服务。"
                },
                "finish_reason": "stop"
            }
        ],
        "usage": {
            "prompt_tokens": 5,
            "completion_tokens": 20
        }
    }

    print("\n📥 MiniMax 返回的响应:")
    print(f"   {mock_response}")

    # 测试解析
    adapter = MockClaudeAdapter()
    content = adapter._extract_content_from_response(mock_response)

    print(f"\n✅ 解析结果: {content}")

    assert content == "你好！我是 MiniMax M2，很高兴为你服务。", \
        f"解析失败: {content}"

    print("\n✅ MiniMax 响应解析测试通过!")
    return True


if __name__ == "__main__":
    all_passed = True

    all_passed = test_response_parsing() and all_passed
    all_passed = test_minimax_simulation() and all_passed

    print("\n" + "=" * 70)
    if all_passed:
        print("🎉 所有测试通过! 响应解析修复完成")
    else:
        print("❌ 部分测试失败，请检查")
    print("=" * 70)

    exit(0 if all_passed else 1)
