"""
检查当前数据库配置
"""
from backend.config.database import get_db
from backend.models.llm_config import LLMConfig
import os

print("=" * 70)
print("检查当前数据库配置")
print("=" * 70)
print()

# 检查环境变量
print("环境变量:")
print(f"  ANTHROPIC_BASE_URL: {os.environ.get('ANTHROPIC_BASE_URL', 'Not set')}")
print(f"  ANTHROPIC_AUTH_TOKEN: {os.environ.get('ANTHROPIC_AUTH_TOKEN', 'Not set')[:20]}...")
print()

db = next(get_db())

try:
    configs = db.query(LLMConfig).filter(LLMConfig.model_key == 'claude').all()

    if configs:
        print(f"找到 {len(configs)} 个 claude 配置:")
        print()
        for i, config in enumerate(configs):
            print(f"配置 {i+1}:")
            print(f"  - ID: {config.id}")
            print(f"  - Model Key: {config.model_key}")
            print(f"  - Default Model: {config.default_model}")
            print(f"  - API Base URL: {config.api_base_url}")
            print(f"  - API Key: {config.api_key[:20]}...")
            print(f"  - Enabled: {config.enabled}")
            print()

    else:
        print("✗ 未找到任何 claude 配置")

except Exception as e:
    print(f"✗ 错误: {e}")
    import traceback
    traceback.print_exc()

finally:
    db.close()

print("=" * 70)
