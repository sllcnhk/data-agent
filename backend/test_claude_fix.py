"""
快速测试 Claude 适配器修复
这个脚本专门测试 anthropic 类型注解修复是否成功
"""
import sys
import os

# 设置正确的路径
backend_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(backend_dir)
sys.path.insert(0, project_root)
os.chdir(backend_dir)

print("=" * 60)
print("Claude 适配器修复验证")
print("=" * 60)
print(f"Python: {sys.version}")
print()

# 第一步：测试 anthropic 基础库
print("[1/3] 测试 anthropic 基础库...")
try:
    import anthropic
    print(f"✓ anthropic 版本: {anthropic.__version__}")

    from anthropic import AsyncAnthropic
    print(f"✓ AsyncAnthropic 类可用")
except Exception as e:
    print(f"✗ anthropic 库测试失败: {e}")
    sys.exit(1)

print()

# 第二步：测试模块导入
print("[2/3] 测试核心模块导入...")
try:
    from core.conversation_format import (
        UnifiedConversation,
        UnifiedMessage,
        MessageRole
    )
    print(f"✓ 对话格式模块导入成功")

    from core.model_adapters.base import BaseModelAdapter
    print(f"✓ 基础适配器导入成功")
except Exception as e:
    print(f"✗ 核心模块导入失败: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

print()

# 第三步：测试 Claude 适配器（关键测试）
print("[3/3] 测试 Claude 适配器（修复验证）...")
try:
    from core.model_adapters.claude import ClaudeAdapter
    print(f"✓ ClaudeAdapter 导入成功")

    # 检查类是否正确定义
    print(f"✓ ClaudeAdapter 类定义正确")

    # 检查关键方法
    methods = ['convert_from_native_format', 'convert_to_native_format', 'chat', 'stream_chat']
    for method in methods:
        if hasattr(ClaudeAdapter, method):
            print(f"✓ 方法 {method} 存在")
        else:
            raise AttributeError(f"方法 {method} 不存在")

    print()
    print("✓ Claude 适配器所有检查通过")

except AttributeError as e:
    if "anthropic.types" in str(e) and "Message" in str(e):
        print(f"✗ anthropic.types.Message 错误仍然存在！")
        print(f"   这意味着类型注解修复未生效")
        print(f"   错误详情: {e}")
    else:
        print(f"✗ 属性错误: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

except Exception as e:
    print(f"✗ Claude 适配器测试失败: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

print()
print("=" * 60)
print("✅ 修复验证成功！")
print("=" * 60)
print()
print("Claude 适配器的 anthropic 类型注解问题已解决")
print("系统应该可以正常启动了")
print()
print("下一步测试:")
print("  cd ..")
print("  python test_system.py    # 运行完整系统测试")
print("  start-all.bat             # 启动服务")
