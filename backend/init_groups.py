"""
简化的初始化脚本：创建对话分组相关表
"""
import sys
import os

# 确保能找到backend模块
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
sys.path.insert(0, parent_dir)

import logging
from sqlalchemy import text, inspect

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def init_groups():
    """初始化对话分组功能"""
    logger.info("=" * 80)
    logger.info("初始化对话分组功能")
    logger.info("=" * 80)

    try:
        # 导入必要的模块
        from backend.config.database import engine
        from backend.models import ConversationGroup

        inspector = inspect(engine)

        # 检查表是否已存在
        if 'conversation_groups' in inspector.get_table_names():
            logger.info("✓ conversation_groups 表已存在")
        else:
            logger.info("创建 conversation_groups 表...")
            ConversationGroup.__table__.create(engine, checkfirst=True)
            logger.info("✓ conversation_groups 表创建成功")

        # 检查 conversations 表的 group_id 列
        columns = [col['name'] for col in inspector.get_columns('conversations')]

        if 'group_id' in columns:
            logger.info("✓ group_id 列已存在")
        else:
            logger.info("添加 group_id 列...")
            with engine.begin() as conn:
                conn.execute(text("""
                    ALTER TABLE conversations
                    ADD COLUMN group_id UUID REFERENCES conversation_groups(id) ON DELETE SET NULL
                """))

                # 创建索引
                conn.execute(text("""
                    CREATE INDEX IF NOT EXISTS idx_conversations_group_id
                    ON conversations(group_id)
                """))

            logger.info("✓ group_id 列添加成功")

        logger.info("\n" + "=" * 80)
        logger.info("✅ 对话分组功能初始化完成！")
        logger.info("=" * 80)

    except Exception as e:
        logger.error(f"❌ 初始化失败: {e}")
        import traceback
        traceback.print_exc()
        raise


if __name__ == "__main__":
    init_groups()
