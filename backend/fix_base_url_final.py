"""
修复为正确的 base_url
"""
from backend.config.database import get_db
from backend.models.llm_config import LLMConfig

print("=" * 70)
print("修复 Claude 配置 - 基于测试结果")
print("=" * 70)
print()

db = next(get_db())

try:
    config = db.query(LLMConfig).filter(LLMConfig.model_key == 'claude').first()

    if config:
        print("当前配置:")
        print(f"  - Base URL: {config.api_base_url}")
        print(f"  - Model: {config.default_model}")
        print()

        # 根据测试结果设置正确的配置
        # 如果测试显示 http://10.0.3.248:3000 有效，则使用这个
        correct_base_url = "http://10.0.3.248:3000"

        print(f"更新为正确的配置:")
        config.api_base_url = correct_base_url

        # 设置一个通用的模型名称（如果之前的测试显示了可用的模型）
        config.default_model = "claude"

        db.commit()

        print(f"✓ 配置已更新!")
        print(f"  - Base URL: {config.api_base_url}")
        print(f"  - Model: {config.default_model}")
        print()
        print("请重启后端并测试。")

    else:
        print("✗ 未找到 claude 配置")

except Exception as e:
    print(f"✗ 错误: {e}")
    import traceback
    traceback.print_exc()

finally:
    db.close()

print()
print("=" * 70)
