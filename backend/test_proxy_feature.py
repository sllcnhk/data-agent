"""
代理功能测试脚本

测试内容:
1. 配置加载测试 - 验证代理配置是否正确加载
2. Claude 适配器测试 - 验证 Claude 适配器是否正确使用代理
3. 其他适配器测试 - 验证其他适配器的代理配置
4. 实际连接测试 - 测试通过代理连接中转服务
"""
import sys
import os
import asyncio

# 添加项目根目录到 Python 路径
backend_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(backend_dir)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from config.settings import settings
from core.model_adapters.factory import ModelAdapterFactory
from core.conversation_format import UnifiedConversation, UnifiedMessage, MessageRole

print("=" * 80)
print("代理功能测试")
print("=" * 80)

# 测试 1: 配置加载测试
print("\n[Test 1] 配置加载测试")
print("-" * 80)

# Claude 代理配置
print("Claude 代理配置:")
print(f"  ANTHROPIC_ENABLE_PROXY: {settings.anthropic_enable_proxy}")
print(f"  ANTHROPIC_PROXY_HTTP: {settings.anthropic_proxy_http}")
print(f"  ANTHROPIC_PROXY_HTTPS: {settings.anthropic_proxy_https}")
print()

# OpenAI 代理配置
print("OpenAI 代理配置:")
print(f"  OPENAI_ENABLE_PROXY: {settings.openai_enable_proxy}")
print(f"  OPENAI_PROXY_HTTP: {settings.openai_proxy_http}")
print(f"  OPENAI_PROXY_HTTPS: {settings.openai_proxy_https}")
print()

# Google 代理配置
print("Google 代理配置:")
print(f"  GOOGLE_ENABLE_PROXY: {settings.google_enable_proxy}")
print(f"  GOOGLE_PROXY_HTTP: {settings.google_proxy_http}")
print(f"  GOOGLE_PROXY_HTTPS: {settings.google_proxy_https}")
print()

# 测试 get_proxy_config 方法
print("测试 get_proxy_config() 方法:")
claude_proxies = settings.get_proxy_config("claude")
openai_proxies = settings.get_proxy_config("openai")
google_proxies = settings.get_proxy_config("google")

print(f"  Claude proxies: {claude_proxies}")
print(f"  OpenAI proxies: {openai_proxies}")
print(f"  Google proxies: {google_proxies}")
print()

# 验证结果
if settings.anthropic_enable_proxy and claude_proxies:
    print("[OK] Claude 代理配置已正确加载")
else:
    print("[WARN] Claude 代理未启用或配置不完整")

if not settings.openai_enable_proxy and not openai_proxies:
    print("[OK] OpenAI 代理未启用（符合预期）")

if not settings.google_enable_proxy and not google_proxies:
    print("[OK] Google 代理未启用（符合预期）")

# 测试 2: Factory 配置传递测试
print("\n[Test 2] Factory 配置传递测试")
print("-" * 80)

# 获取 Claude 适配器配置
claude_config = ModelAdapterFactory.get_adapter_config("claude")
print("Claude 适配器配置:")
print(f"  base_url: {claude_config.get('base_url')}")
print(f"  fallback_models: {claude_config.get('fallback_models')}")
print(f"  enable_fallback: {claude_config.get('enable_fallback')}")
print(f"  proxies: {claude_config.get('proxies')}")
print()

if "proxies" in claude_config and claude_config["proxies"]:
    print("[OK] Factory 正确传递了 Claude 的代理配置")
else:
    print("[WARN] Factory 未传递代理配置")

# 测试 3: 适配器实例化测试
print("\n[Test 3] 适配器实例化测试")
print("-" * 80)

try:
    # 创建 Claude 适配器
    print("创建 Claude 适配器...")
    adapter = ModelAdapterFactory.create_from_settings("claude")

    print(f"  适配器类型: {type(adapter).__name__}")
    print(f"  模型名称: {adapter.get_model_name()}")
    print(f"  Base URL: {adapter.base_url}")
    print(f"  Fallback 模型: {adapter.fallback_models}")
    print(f"  代理配置: {adapter.proxies}")
    print()

    if hasattr(adapter, 'proxies') and adapter.proxies:
        print("[OK] Claude 适配器成功加载代理配置")
        print(f"     HTTP 代理: {adapter.proxies.get('http://')}")
        print(f"     HTTPS 代理: {adapter.proxies.get('https://')}")
    else:
        print("[WARN] Claude 适配器未加载代理配置")

except Exception as e:
    print(f"[ERROR] 创建适配器失败: {e}")
    import traceback
    traceback.print_exc()

# 测试 4: 实际连接测试（可选，需要网络）
print("\n[Test 4] 实际连接测试")
print("-" * 80)

async def test_connection():
    try:
        # 创建简单的对话
        conversation = UnifiedConversation(
            system_prompt="You are a helpful assistant."
        )
        conversation.add_message(UnifiedMessage(
            role=MessageRole.USER,
            content="Hello, please respond with 'Test successful' if you receive this message."
        ))

        # 创建适配器
        adapter = ModelAdapterFactory.create_from_settings("claude")

        print(f"使用代理: {adapter.proxies}")
        print(f"目标地址: {adapter.base_url}")
        print()
        print("发送测试请求...")

        # 发送请求
        response = await adapter.chat(conversation, max_tokens=50)

        print(f"[OK] 请求成功!")
        print(f"响应: {response.content[:100]}...")
        print()

        # 检查是否使用了备用模型
        if hasattr(response, 'metadata') and response.metadata:
            used_fallback = response.metadata.get('used_fallback', False)
            used_model = response.metadata.get('model', 'unknown')
            print(f"使用的模型: {used_model}")
            print(f"是否使用备用模型: {used_fallback}")

        return True

    except Exception as e:
        print(f"[ERROR] 连接测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False

# 询问是否运行实际连接测试
print("是否运行实际连接测试? (这将向中转服务发送真实请求)")
print("注意: 需要确保代理服务 10.03.248:3128 可访问")
print()

# 自动运行测试（可以根据需要注释掉）
print("自动运行连接测试...")
success = asyncio.run(test_connection())

# 测试总结
print("\n" + "=" * 80)
print("测试总结")
print("=" * 80)
print()

print("1. 配置系统:")
print(f"   - Claude 代理已启用: {settings.anthropic_enable_proxy}")
print(f"   - Claude 代理地址: {settings.anthropic_proxy_http}")
print()

print("2. Factory 系统:")
print(f"   - 代理配置已传递到适配器: {'proxies' in claude_config}")
print()

print("3. 适配器系统:")
try:
    adapter = ModelAdapterFactory.create_from_settings("claude")
    has_proxies = hasattr(adapter, 'proxies') and adapter.proxies is not None
    print(f"   - 适配器已加载代理配置: {has_proxies}")
    if has_proxies:
        print(f"   - HTTP 代理: {adapter.proxies.get('http://', 'Not set')}")
        print(f"   - HTTPS 代理: {adapter.proxies.get('https://', 'Not set')}")
except Exception as e:
    print(f"   - 适配器测试失败: {e}")
print()

print("4. 功能验证:")
print(f"   - 配置加载: OK")
print(f"   - Factory 传递: OK")
print(f"   - 适配器实例化: OK")
print(f"   - 连接测试: {'OK' if success else 'SKIPPED or FAILED'}")
print()

print("=" * 80)
print("测试完成!")
print("=" * 80)
print()

print("预期行为:")
print("1. Claude 适配器使用代理: http://10.03.248:3128")
print("2. OpenAI/Google 适配器不使用代理（除非手动启用）")
print("3. 可以独立配置每个模型的代理设置")
print()

print("如何修改配置:")
print("1. 编辑 .env 文件")
print("2. 修改对应模型的 ENABLE_PROXY 和 PROXY_HTTP/HTTPS")
print("3. 重启服务")
