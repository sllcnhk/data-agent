"""
Initialize chat database

Create conversation and model configuration related tables
"""
import sys
import os

# Add project root to Python path
current_dir = os.path.dirname(os.path.abspath(__file__))  # scripts directory
backend_dir = os.path.dirname(current_dir)  # backend directory
project_dir = os.path.dirname(backend_dir)  # project root directory
sys.path.insert(0, project_dir)

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from backend.config.database import Base
from backend.models.conversation import Conversation, Message, ContextSnapshot
from backend.models.llm_config import LLMConfig, DEFAULT_LLM_CONFIGS
from backend.config.settings import settings

def init_database():
    """Initialize database"""
    print("=" * 60)
    print("Initialize Chat Database")
    print("=" * 60)

    # Create database engine
    database_url = settings.get_database_url()
    print(f"\nConnecting to: {database_url.replace(settings.postgres_password, '***')}")
    engine = create_engine(database_url, echo=False)

    print("\n[1/2] Creating table structure...")
    Base.metadata.create_all(bind=engine)
    print("SUCCESS: Tables created")

    # Create session
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()

    try:
        print("\n[2/2] Initializing default LLM configurations...")

        # Check if configurations already exist
        existing_count = db.query(LLMConfig).count()
        if existing_count > 0:
            print(f"INFO: {existing_count} configurations already exist, skipping initialization")
        else:
            # Insert default configurations
            for config_data in DEFAULT_LLM_CONFIGS:
                config = LLMConfig(**config_data)
                db.add(config)

            db.commit()
            print(f"SUCCESS: Created {len(DEFAULT_LLM_CONFIGS)} default configurations")

            # Display configuration list
            print("\nDefault configurations:")
            configs = db.query(LLMConfig).all()
            for config in configs:
                status = "enabled" if config.is_enabled else "disabled"
                default = " [default]" if config.is_default else ""
                # Skip emoji to avoid encoding issues
                print(f"  - {config.model_name} ({config.model_key}) - {status}{default}")

        print("\n" + "=" * 60)
        print("Database initialization complete!")
        print("=" * 60)
        print("\nNext steps:")
        print("1. Update API keys in backend/models/llm_config.py DEFAULT_LLM_CONFIGS")
        print("2. Or configure models via frontend /model-config page")
        print("3. Start backend: python backend/main.py")
        print("4. Start frontend: cd frontend && npm run dev")

    except Exception as e:
        print(f"\nERROR: Initialization failed: {e}")
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    init_database()
