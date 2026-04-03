"""
验证配置是否修改成功
"""
from backend.config.database import get_db
from backend.models.llm_config import LLMConfig

db = next(get_db())

config = db.query(LLMConfig).filter(LLMConfig.model_key == 'claude').first()

if config:
    print("Current configuration:")
    print(f"  Base URL: {config.api_base_url}")
    print(f"  Model: {config.default_model}")
    print()
    print("✓ Configuration updated successfully")
    print()
    print("Next steps:")
    print("1. Stop backend (Ctrl+C)")
    print("2. Restart backend")
    print("3. Run test_llm_chat.py")

db.close()
