"""
修复 LLM 配置以适配中转服务
"""
from backend.config.database import get_db
from backend.models.llm_config import LLMConfig

print("=" * 70)
print("修复 LLM 配置")
print("=" * 70)
print()

db = next(get_db())

try:
    config = db.query(LLMConfig).filter(LLMConfig.model_key == 'claude').first()

    if config:
        print("当前配置:")
        print(f"  - Model Key: {config.model_key}")
        print(f"  - API Base URL: {config.api_base_url}")
        print(f"  - Default Model: {config.default_model}")
        print(f"  - API Key: {config.api_key[:20]}...")
        print()

        # 修改为中转服务兼容的配置
        print("修改配置...")

        # 使用环境变量中的 base_url
        config.api_base_url = "http://10.0.3.248:3000/api"

        # 使用中转服务支持的模型名称
        # 从输出中看到支持: 'claude-2', 'claude-instant-1'
        config.default_model = "claude-2"

        # 使用环境变量中的 auth token 作为 API key
        config.api_key = "cr_b1abe3aaa6cabb76eeb3c46c1f3c9294e8b825e679c2333be9e013bb129571f4"

        db.commit()

        print("✓ 配置已更新:")
        print(f"  - API Base URL: {config.api_base_url}")
        print(f"  - Default Model: {config.default_model}")
        print(f"  - API Key: {config.api_key[:20]}...")
        print()
        print("请重启后端并重新测试。")

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
