"""
测试导入是否成功
"""
import sys
import os

# 添加项目根目录到 Python 路径
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
backend_root = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, project_root)
sys.path.insert(0, backend_root)

print(f"Python 版本: {sys.version}")
print(f"项目根目录: {project_root}")

try:
    import anthropic
    print(f"✓ anthropic 版本: {anthropic.__version__}")
except Exception as e:
    print(f"✗ anthropic 导入失败: {e}")

try:
    from anthropic import AsyncAnthropic
    print(f"✓ AsyncAnthropic 导入成功")
except Exception as e:
    print(f"✗ AsyncAnthropic 导入失败: {e}")

try:
    from backend.core.model_adapters.claude import ClaudeAdapter
    print(f"✓ ClaudeAdapter 导入成功")
except Exception as e:
    print(f"✗ ClaudeAdapter 导入失败: {e}")
    import traceback
    traceback.print_exc()

try:
    from api import agents, skills, conversations, llm_configs, mcp
    print(f"✓ 所有 API 模块导入成功")
except Exception as e:
    print(f"✗ API 模块导入失败: {e}")
    import traceback
    traceback.print_exc()

print("\n测试完成!")
