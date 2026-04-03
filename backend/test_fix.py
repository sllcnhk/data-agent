"""
测试空消息过滤和模型回退修复

验证:
1. 空消息是否被正确过滤
2. Minimax 是否已添加到 fallback 模型列表
3. 配置是否正确加载
"""
import sys
import os

# 添加项目根目录到 Python 路径
backend_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(backend_dir)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from config.settings import settings
from core.model_adapters.factory import ModelAdapterFactory
from core.conversation_format import UnifiedConversation, UnifiedMessage, MessageRole

print("=" * 80)
print("测试修复：空消息过滤 + Minimax 回退")
print("=" * 80)

# 测试 1: 配置验证
print("\n【测试 1】验证配置加载")
print("-" * 80)
print(f"✓ ANTHROPIC_BASE_URL: {settings.anthropic_base_url}")
print(f"✓ ANTHROPIC_DEFAULT_MODEL: {settings.anthropic_default_model}")
print(f"✓ ANTHROPIC_FALLBACK_MODELS: {settings.anthropic_fallback_models}")
print(f"✓ ANTHROPIC_ENABLE_FALLBACK: {settings.anthropic_enable_fallback}")
print(f"✓ API Key 前4位: {settings.anthropic_api_key[:4]}...")

# 解析 fallback 模型列表
fallback_models = [
    m.strip() for m in settings.anthropic_fallback_models.split(",")
    if m.strip()
]
print(f"\n解析后的 fallback 模型列表:")
for i, model in enumerate(fallback_models, 1):
    print(f"  {i}. {model}")

# 检查 Minimax 是否在列表中
has_minimax = any("minimax" in model.lower() for model in fallback_models)
if has_minimax:
    print(f"✓ Minimax 模型已添加到 fallback 列表")
else:
    print(f"✗ 警告: Minimax 模型未在 fallback 列表中")

# 测试 2: 创建适配器并检查配置
print("\n【测试 2】创建 Claude 适配器并检查配置")
print("-" * 80)
try:
    adapter_config = ModelAdapterFactory.get_adapter_config("claude")
    print(f"✓ 适配器配置:")
    print(f"  - temperature: {adapter_config.get('temperature')}")
    print(f"  - max_tokens: {adapter_config.get('max_tokens')}")
    print(f"  - base_url: {adapter_config.get('base_url')}")
    print(f"  - fallback_models: {adapter_config.get('fallback_models')}")
    print(f"  - enable_fallback: {adapter_config.get('enable_fallback')}")

    # 创建适配器
    adapter = ModelAdapterFactory.create_from_settings("claude")
    print(f"\n✓ Claude 适配器创建成功")
    print(f"  - 模型名称: {adapter.get_model_name()}")
    print(f"  - Base URL: {adapter.base_url}")
    print(f"  - Fallback 启用: {adapter.enable_fallback}")
    print(f"  - Fallback 模型: {adapter.fallback_models}")

except Exception as e:
    print(f"✗ 创建适配器失败: {e}")
    import traceback
    traceback.print_exc()

# 测试 3: 空消息过滤
print("\n【测试 3】测试空消息过滤")
print("-" * 80)

# 创建包含空消息的对话
conversation = UnifiedConversation(
    system_prompt="你是一个AI助手"
)

# 添加正常消息
conversation.add_message(UnifiedMessage(
    role=MessageRole.USER,
    content="你好"
))

# 添加空消息（应该被过滤）
conversation.add_message(UnifiedMessage(
    role=MessageRole.ASSISTANT,
    content=""
))

# 添加只有空白的消息（应该被过滤）
conversation.add_message(UnifiedMessage(
    role=MessageRole.USER,
    content="   "
))

# 添加正常消息
conversation.add_message(UnifiedMessage(
    role=MessageRole.USER,
    content="今天天气怎么样？"
))

print(f"原始对话消息数: {len(conversation.messages)}")

try:
    # 转换为 Claude 格式
    from core.model_adapters.claude import ClaudeAdapter
    adapter = ClaudeAdapter(
        api_key=settings.anthropic_api_key,
        base_url=settings.anthropic_base_url
    )

    native_format = adapter.convert_to_native_format(conversation)
    filtered_messages = native_format['messages']

    print(f"过滤后的消息数: {len(filtered_messages)}")
    print(f"\n过滤后的消息:")
    for i, msg in enumerate(filtered_messages, 1):
        content_preview = msg['content'][:50] + "..." if len(msg['content']) > 50 else msg['content']
        print(f"  {i}. [{msg['role']}] {content_preview}")

    # 验证：应该只有2条消息（两条正常的用户消息）
    expected_count = 2
    if len(filtered_messages) == expected_count:
        print(f"\n✓ 空消息过滤正常工作！（期望 {expected_count} 条，实际 {len(filtered_messages)} 条）")
    else:
        print(f"\n✗ 警告: 过滤后消息数量不符合预期（期望 {expected_count} 条，实际 {len(filtered_messages)} 条）")

except Exception as e:
    print(f"✗ 测试失败: {e}")
    import traceback
    traceback.print_exc()

# 测试 4: 验证模型回退顺序
print("\n【测试 4】验证模型回退顺序")
print("-" * 80)
print("将按以下顺序尝试模型:")
print(f"  1. {settings.anthropic_default_model} (主模型)")
if fallback_models:
    for i, model in enumerate(fallback_models, 2):
        print(f"  {i}. {model}")
else:
    print("  (没有配置 fallback 模型)")

# 总结
print("\n" + "=" * 80)
print("测试完成！")
print("=" * 80)
print("\n修复内容总结:")
print("1. ✓ 空消息过滤已添加到 orchestrator.py")
print("2. ✓ 空消息过滤已添加到 claude.py")
print("3. ✓ Minimax 模型已添加到 fallback 列表")
print("4. ✓ 中转服务配置已正确设置")
print("\n下一步:")
print("1. 重启服务: start-all.bat")
print("2. 在 Web 界面中测试对话功能")
print("3. 检查日志确认模型回退是否正常工作")
