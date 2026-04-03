"""
Migration: Add is_shared column to conversations table

Run:
    python backend/scripts/migrate_add_is_shared.py
"""
import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from backend.config.database import engine
from sqlalchemy import text
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def run():
    with engine.connect() as conn:
        # Check if column already exists
        result = conn.execute(text("""
            SELECT column_name
            FROM information_schema.columns
            WHERE table_name = 'conversations'
              AND column_name = 'is_shared'
        """))
        if result.fetchone():
            logger.info("Column 'is_shared' already exists, skipping.")
            return

        conn.execute(text("""
            ALTER TABLE conversations
            ADD COLUMN is_shared BOOLEAN NOT NULL DEFAULT FALSE
        """))
        conn.commit()
        logger.info("Added 'is_shared' column to conversations table.")


if __name__ == "__main__":
    run()
