"""
后端启动测试 - 测试所有关键模块是否可以正常导入
"""
import sys
import os

# 设置正确的路径
backend_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(backend_dir)
sys.path.insert(0, project_root)
os.chdir(backend_dir)

print("=" * 60)
print("后端启动完整性测试")
print("=" * 60)
print(f"Python: {sys.version}")
print(f"工作目录: {os.getcwd()}")
print()

test_count = 0
pass_count = 0

def test(description, func):
    """测试函数"""
    global test_count, pass_count
    test_count += 1
    try:
        func()
        print(f"✓ [{test_count}] {description}")
        pass_count += 1
        return True
    except Exception as e:
        print(f"✗ [{test_count}] {description}")
        print(f"    错误: {e}")
        return False

# 第一阶段：基础库测试
print("=" * 60)
print("阶段 1: 基础依赖测试")
print("=" * 60)

test("anthropic 库", lambda: __import__('anthropic'))
test("fastapi 库", lambda: __import__('fastapi'))
test("sqlalchemy 库", lambda: __import__('sqlalchemy'))
print()

# 第二阶段：配置和模型测试
print("=" * 60)
print("阶段 2: 配置和数据库模型测试")
print("=" * 60)

test("配置模块", lambda: __import__('config.settings'))
test("数据库模型", lambda: __import__('models'))
print()

# 第三阶段：核心适配器测试（关键）
print("=" * 60)
print("阶段 3: 模型适配器测试（关键）")
print("=" * 60)

def test_adapters():
    from core.model_adapters import BaseModelAdapter, ClaudeAdapter, OpenAIAdapter
    print(f"    ✓ 核心适配器导入成功")

    # 检查可选适配器
    try:
        from core.model_adapters import GeminiAdapter
        if GeminiAdapter:
            print(f"    ✓ GeminiAdapter 可用")
        else:
            print(f"    ⚠ GeminiAdapter 不可用（正常，依赖库缺失）")
    except ImportError:
        print(f"    ⚠ GeminiAdapter 不可用（正常，依赖库缺失）")

test("核心适配器", test_adapters)

def test_factory():
    from core.model_adapters.factory import ModelAdapterFactory
    providers = ModelAdapterFactory.list_supported_providers()
    print(f"    可用提供商: {providers}")

test("适配器工厂", test_factory)
print()

# 第四阶段：服务层测试
print("=" * 60)
print("阶段 4: 服务层测试")
print("=" * 60)

test("对话服务", lambda: __import__('services.conversation_service'))
test("MCP 管理器", lambda: __import__('mcp.manager'))
print()

# 第五阶段：API 路由测试
print("=" * 60)
print("阶段 5: API 路由测试")
print("=" * 60)

test("agents API", lambda: __import__('api.agents'))
test("skills API", lambda: __import__('api.skills'))
test("conversations API", lambda: __import__('api.conversations'))
test("llm_configs API", lambda: __import__('api.llm_configs'))
test("mcp API", lambda: __import__('api.mcp'))
print()

# 第六阶段：主应用测试
print("=" * 60)
print("阶段 6: 主应用启动测试（最终验证）")
print("=" * 60)

def test_main():
    import main
    print(f"    ✓ 应用标题: {main.app.title}")
    print(f"    ✓ 应用版本: {main.app.version}")

test("主应用", test_main)
print()

# 总结
print("=" * 60)
print("测试总结")
print("=" * 60)
print(f"总测试: {test_count}")
print(f"通过: {pass_count} ✓")
print(f"失败: {test_count - pass_count} ✗")
print()

if pass_count == test_count:
    print("✅ 所有测试通过！")
    print()
    print("后端可以正常启动。")
    print()
    print("下一步:")
    print("  cd C:\\Users\\shiguangping\\data-agent")
    print("  start-all.bat")
    sys.exit(0)
else:
    print("❌ 部分测试失败")
    print()
    print("请检查失败的模块并修复")
    sys.exit(1)
