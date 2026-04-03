"""
数据库迁移脚本：添加对话分组功能

添加内容：
1. 创建 conversation_groups 表
2. 为 conversations 表添加 group_id 外键
3. 创建相关索引
"""
import sys
import os

# 添加项目根目录到 Python 路径
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

from sqlalchemy import text
from backend.config.database import engine, Base
from backend.models import ConversationGroup, Conversation
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def migrate_up():
    """执行迁移"""
    logger.info("开始迁移：添加对话分组功能")

    with engine.connect() as conn:
        try:
            # 1. 创建 conversation_groups 表
            logger.info("Step 1: 创建 conversation_groups 表...")
            Base.metadata.tables['conversation_groups'].create(engine, checkfirst=True)
            logger.info("  ✓ conversation_groups 表创建成功")

            # 2. 检查 conversations 表是否已有 group_id 列
            logger.info("Step 2: 检查 group_id 列...")
            result = conn.execute(text("""
                SELECT column_name
                FROM information_schema.columns
                WHERE table_name='conversations' AND column_name='group_id'
            """))

            if result.fetchone() is None:
                logger.info("  添加 group_id 列到 conversations 表...")

                # 添加 group_id 列
                conn.execute(text("""
                    ALTER TABLE conversations
                    ADD COLUMN group_id UUID REFERENCES conversation_groups(id) ON DELETE SET NULL
                """))
                conn.commit()
                logger.info("  ✓ group_id 列添加成功")

                # 创建索引
                logger.info("  创建索引...")
                conn.execute(text("""
                    CREATE INDEX IF NOT EXISTS idx_conversations_group_id ON conversations(group_id)
                """))
                conn.commit()
                logger.info("  ✓ 索引创建成功")
            else:
                logger.info("  ✓ group_id 列已存在")

            # 3. 创建 conversation_groups 表的索引
            logger.info("Step 3: 创建 conversation_groups 索引...")
            conn.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_conversation_groups_sort_order ON conversation_groups(sort_order)
            """))
            conn.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_conversation_groups_name ON conversation_groups(name)
            """))
            conn.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_conversation_groups_created_at ON conversation_groups(created_at)
            """))
            conn.commit()
            logger.info("  ✓ 所有索引创建成功")

            logger.info("\n✅ 迁移完成！对话分组功能已启用")

            # 打印统计信息
            result = conn.execute(text("SELECT COUNT(*) FROM conversations"))
            conv_count = result.fetchone()[0]

            result = conn.execute(text("SELECT COUNT(*) FROM conversation_groups"))
            group_count = result.fetchone()[0]

            logger.info(f"\n统计信息:")
            logger.info(f"  - 对话总数: {conv_count}")
            logger.info(f"  - 分组总数: {group_count}")
            logger.info(f"  - 未分组对话: {conv_count}")

        except Exception as e:
            conn.rollback()
            logger.error(f"❌ 迁移失败: {e}")
            import traceback
            logger.error(traceback.format_exc())
            raise


def migrate_down():
    """回滚迁移"""
    logger.info("开始回滚：删除对话分组功能")

    with engine.connect() as conn:
        try:
            # 1. 删除 group_id 列
            logger.info("Step 1: 删除 group_id 列...")
            conn.execute(text("""
                ALTER TABLE conversations DROP COLUMN IF EXISTS group_id
            """))
            conn.commit()
            logger.info("  ✓ group_id 列删除成功")

            # 2. 删除 conversation_groups 表
            logger.info("Step 2: 删除 conversation_groups 表...")
            conn.execute(text("""
                DROP TABLE IF EXISTS conversation_groups CASCADE
            """))
            conn.commit()
            logger.info("  ✓ conversation_groups 表删除成功")

            logger.info("\n✅ 回滚完成！")

        except Exception as e:
            conn.rollback()
            logger.error(f"❌ 回滚失败: {e}")
            import traceback
            logger.error(traceback.format_exc())
            raise


def main():
    """主函数"""
    import argparse

    parser = argparse.ArgumentParser(description="对话分组功能数据库迁移")
    parser.add_argument(
        "action",
        choices=["up", "down"],
        help="执行迁移(up)或回滚(down)"
    )

    args = parser.parse_args()

    if args.action == "up":
        migrate_up()
    else:
        migrate_down()


if __name__ == "__main__":
    main()
