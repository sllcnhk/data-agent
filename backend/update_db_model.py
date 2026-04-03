"""
更新数据库中的 Claude 模型配置
"""
import sys
import os

# 添加项目根目录到 Python 路径
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from backend.config.settings import settings
from backend.models.llm_config import LLMConfig

def main():
    """更新数据库中的模型配置"""
    # 创建数据库连接
    database_url = settings.get_database_url()
    engine = create_engine(database_url)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()

    try:
        # 查找 Claude 配置
        config = db.query(LLMConfig).filter(
            LLMConfig.model_key == "claude"
        ).first()

        if config:
            print(f"Found Claude config:")
            print(f"  Model Key: {config.model_key}")
            print(f"  Model Name: {config.model_name}")
            print(f"  Default Model: {config.default_model}")
            print(f"  API Base URL: {config.api_base_url}")
            print()

            # 更新配置
            config.default_model = "claude-sonnet-4-5"
            config.api_base_url = "http://10.0.3.248:3000/api"

            # 如果 api_key 为空，设置为 auth_token
            if not config.api_key:
                config.api_key = "cr_b1abe3aaa6cabb76eeb3c46c1f3c9294e8b825e679c2333be9e013bb129571f4"

            db.commit()

            print(f"Updated Claude config:")
            print(f"  Default Model: {config.default_model}")
            print(f"  API Base URL: {config.api_base_url}")
            print(f"  API Key: {config.api_key[:20]}...")
            print()
            print("✅ Database update successful!")

        else:
            print("❌ Claude config not found in database")
            print("Creating new config...")

            # 创建新配置
            new_config = LLMConfig(
                model_key="claude",
                model_name="Claude Code",
                model_type="claude",
                api_base_url="http://10.0.3.248:3000/api",
                api_key="cr_b1abe3aaa6cabb76eeb3c46c1f3c9294e8b825e679c2333be9e013bb129571f4",
                default_model="claude-sonnet-4-5",
                temperature="0.7",
                max_tokens="4096",
                is_enabled=True,
                is_default=True,
                description="Claude Code via relay service",
                icon="🤖"
            )

            db.add(new_config)
            db.commit()

            print("✅ Created new Claude config!")

    except Exception as e:
        print(f"❌ Error: {e}")
        db.rollback()
        import traceback
        traceback.print_exc()
    finally:
        db.close()

if __name__ == "__main__":
    main()
