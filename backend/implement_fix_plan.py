"""
实施修复方案 - 基于错误分析
"""
from backend.config.database import get_db
from backend.models.llm_config import LLMConfig

print("=" * 70)
print("实施修复方案")
print("=" * 70)
print()

print("基于错误分析:")
print("错误信息: Route /api/v1/complete not found")
print("问题: anthropic 0.7.7 调用 /v1/complete 但中转可能使用不同路径")
print()

db = next(get_db())

try:
    config = db.query(LLMConfig).filter(LLMConfig.model_key == 'claude').first()

    if config:
        print("当前配置:")
        print(f"  - Base URL: {config.api_base_url}")
        print(f"  - Model: {config.default_model}")
        print()

        print("方案 1: 尝试修改 base_url 为根路径")
        print("  前: " + config.api_base_url)
        config.api_base_url = "http://10.0.3.248:3000"
        print("  后: " + config.api_base_url)
        print("  结果: anthropic 会添加 /v1/complete")
        print("       完整路径: http://10.0.3.248:3000/v1/complete")
        print()

        # 同时设置一个通用的模型名
        if not config.default_model or "claude-3" in config.default_model:
            config.default_model = "claude"
            print("  模型名也改为: " + config.default_model)

        db.commit()

        print("✓ 配置已更新")
        print()
        print("请重启后端并运行测试:")
        print("  1. 停止后端 (Ctrl+C)")
        print("  2. 重启: python main.py")
        print("  3. 测试: python test_llm_chat.py")
        print()
        print("查看启动日志:")
        print("  应该看到: [INIT] Client created successfully")
        print("  查看 API 调用:")
        print("  type logs\\backend.log | findstr \"[DEBUG]\"")

    else:
        print("✗ 未找到配置")

except Exception as e:
    print(f"✗ 错误: {e}")
    import traceback
    traceback.print_exc()

finally:
    db.close()

print()
print("=" * 70)
print("备用方案")
print("=" * 70)
print()
print("如果方案1失败，尝试方案2:")
print("修改 claude.py 直接调用中转服务的路径")
print("使用 /v1/completions (复数) 替代 /v1/complete")
