#!/usr/bin/env python3
"""
快速验证配置和响应解析
"""
import sys
import os

backend_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, backend_dir)

# 直接导入 settings
exec(open(os.path.join(backend_dir, 'config', 'settings.py')).read().replace('if __name__ == "__main__":', 'if False:'))

print("=" * 70)
print("Configuration Verification")
print("=" * 70)

print(f"\n[CONFIG] anthropic_default_model: {settings.anthropic_default_model}")
print(f"[CONFIG] anthropic_fallback_models: {settings.anthropic_fallback_models}")
print(f"[CONFIG] anthropic_enable_fallback: {settings.anthropic_enable_fallback}")

# 解析 fallback 模型
fallback_models = []
if settings.anthropic_fallback_models:
    fallback_models = [
        m.strip() for m in settings.anthropic_fallback_models.split(",")
        if m.strip()
    ]

print(f"\n[FALLBACK] Parsed fallback models ({len(fallback_models)}):")
for i, model in enumerate(fallback_models, 1):
    print(f"   {i}. {model}")

# 构建完整的模型列表
models_to_try = [settings.anthropic_default_model]
if settings.anthropic_enable_fallback:
    models_to_try.extend(fallback_models)

print(f"\n[FULL SEQUENCE] Complete failover sequence ({len(models_to_try)} models):")
for i, model in enumerate(models_to_try, 1):
    print(f"   {i}. {model}")

# 验证 minimax 在列表中
assert "minimax-m2" in models_to_try, "minimax-m2 not in fallback list!"
print("\n[PASS] minimax-m2 is in the fallback list")

# 测试响应解析
class ResponseParser:
    def _extract_content_from_response(self, response):
        import logging
        logger = logging.getLogger(__name__)

        if not response:
            return ""
        if isinstance(response, str):
            return response
        if not isinstance(response, dict):
            return str(response)

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

        for key in ["text", "result", "output", "response"]:
            if key in response:
                content = response.get(key)
                if isinstance(content, str):
                    return content

        for value in response.values():
            if isinstance(value, str) and len(value) > 0:
                return value

        return ""

parser = ResponseParser()

# 模拟中转服务可能返回的各种响应格式
test_responses = [
    ("Claude 格式", {"content": [{"type": "text", "text": "Hello from Claude"}]}),
    ("OpenAI 格式", {"choices": [{"message": {"content": "Hello from OpenAI"}}]}),
    ("带 usage", {"choices": [{"message": {"content": "Hello with usage"}}], "usage": {}}),
]

print("\n" + "=" * 70)
print("Response Parsing Tests")
print("=" * 70)

all_passed = True
for name, response in test_responses:
    result = parser._extract_content_from_response(response)
    expected = "Hello" in result
    status = "PASS" if expected else "FAIL"
    print(f"[{status}] {name}: '{result[:50]}...'")
    if not expected:
        all_passed = False

print("\n" + "=" * 70)
if all_passed:
    print("All verifications passed!")
    print("\nNext steps:")
    print("1. Run: restart_backend.bat")
    print("2. Test in browser")
    print("3. Check logs for [PARSE] messages")
else:
    print("Some verifications failed!")
print("=" * 70)
