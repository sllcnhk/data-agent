"""
系统级测试脚本 - 从项目根目录运行
测试所有关键模块的导入和功能
"""
import sys
import os
from pathlib import Path

# 设置项目根目录
PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT))

print("=" * 60)
print("Data Agent System - 完整性测试")
print("=" * 60)
print(f"Python 版本: {sys.version}")
print(f"项目根目录: {PROJECT_ROOT}")
print(f"Python 搜索路径: {sys.path[:3]}")
print("=" * 60)
print()

# 测试计数
total_tests = 0
passed_tests = 0
failed_tests = []

def test(description, func):
    """测试装饰器"""
    global total_tests, passed_tests, failed_tests
    total_tests += 1
    try:
        func()
        print(f"✓ [{total_tests}] {description}")
        passed_tests += 1
        return True
    except Exception as e:
        print(f"✗ [{total_tests}] {description}")
        print(f"    错误: {e}")
        failed_tests.append((description, str(e)))
        return False

# ============== 第一阶段：基础库测试 ==============
print("第一阶段：基础依赖库测试")
print("-" * 60)

test("anthropic 库导入", lambda: __import__('anthropic'))
test("anthropic 版本检查", lambda: print(f"    版本: {__import__('anthropic').__version__}"))
test("AsyncAnthropic 导入", lambda: __import__('anthropic').AsyncAnthropic)
test("openai 库导入", lambda: __import__('openai'))
test("fastapi 库导入", lambda: __import__('fastapi'))
test("sqlalchemy 库导入", lambda: __import__('sqlalchemy'))
test("pydantic 库导入", lambda: __import__('pydantic'))
test("redis 库导入", lambda: __import__('redis'))

print()

# ============== 第二阶段：项目配置测试 ==============
print("第二阶段：项目配置模块测试")
print("-" * 60)

# 进入backend目录以便导入
os.chdir(PROJECT_ROOT / "backend")

test("配置模块导入", lambda: __import__('config.settings'))
test("数据库配置", lambda: __import__('config.settings').settings)
test("数据库模型导入", lambda: __import__('models'))

print()

# ============== 第三阶段：核心模块测试 ==============
print("第三阶段：核心业务模块测试")
print("-" * 60)

def test_core_adapters():
    from core.model_adapters.base import BaseModelAdapter
    from core.model_adapters.claude import ClaudeAdapter
    print(f"    ClaudeAdapter 类加载成功")

test("核心适配器基类", lambda: __import__('core.model_adapters.base'))
test("Claude 适配器", test_core_adapters)
test("对话格式化模块", lambda: __import__('core.conversation_format'))

print()

# ============== 第四阶段：API 模块测试 ==============
print("第四阶段：API 路由模块测试")
print("-" * 60)

test("API agents 模块", lambda: __import__('api.agents'))
test("API skills 模块", lambda: __import__('api.skills'))
test("API conversations 模块", lambda: __import__('api.conversations'))
test("API llm_configs 模块", lambda: __import__('api.llm_configs'))
test("API mcp 模块", lambda: __import__('api.mcp'))

print()

# ============== 第五阶段：服务模块测试 ==============
print("第五阶段：服务层模块测试")
print("-" * 60)

test("对话服务", lambda: __import__('services.conversation_service'))
test("MCP 管理器", lambda: __import__('mcp.manager'))

print()

# ============== 第六阶段：主应用测试 ==============
print("第六阶段：主应用模块测试")
print("-" * 60)

def test_main_app():
    import main
    print(f"    FastAPI app: {main.app.title}")
    print(f"    版本: {main.app.version}")

test("主应用导入", test_main_app)

print()

# ============== 测试总结 ==============
print("=" * 60)
print("测试总结")
print("=" * 60)
print(f"总测试数: {total_tests}")
print(f"通过: {passed_tests} ✓")
print(f"失败: {len(failed_tests)} ✗")
print()

if failed_tests:
    print("失败的测试:")
    for desc, error in failed_tests:
        print(f"  - {desc}")
        print(f"    {error}")
    print()
    print("❌ 测试未全部通过，请检查上述错误")
    sys.exit(1)
else:
    print("✅ 所有测试通过！系统模块完整性验证成功")
    print()
    print("下一步: 运行 start-all.bat 启动完整服务")
    sys.exit(0)
