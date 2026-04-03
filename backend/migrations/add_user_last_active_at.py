"""
数据库迁移脚本：为 users 表添加 last_active_at 列

用途：支持 session 空闲超时功能（SESSION_IDLE_TIMEOUT_MINUTES）。
     get_current_user 依赖在每次认证请求后节流更新此字段，
     /auth/refresh 端点据此判断会话是否超时。
"""
import sys
import os
import logging

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

from sqlalchemy import text
from backend.config.database import engine

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def migrate_up():
    """执行迁移：添加 users.last_active_at 列"""
    logger.info("开始迁移：为 users 表添加 last_active_at 列")

    with engine.connect() as conn:
        try:
            # 检查列是否已存在
            result = conn.execute(text("""
                SELECT column_name
                FROM information_schema.columns
                WHERE table_name='users' AND column_name='last_active_at'
            """))
            if result.fetchone() is not None:
                logger.info("  last_active_at 列已存在，跳过")
            else:
                logger.info("Step 1: 添加 last_active_at 列...")
                conn.execute(text("""
                    ALTER TABLE users
                    ADD COLUMN last_active_at TIMESTAMP WITHOUT TIME ZONE
                """))
                conn.commit()
                logger.info("  last_active_at 列添加成功")

            logger.info("Step 2: 创建索引...")
            conn.execute(text("""
                CREATE INDEX IF NOT EXISTS ix_users_last_active_at ON users (last_active_at)
            """))
            conn.commit()
            logger.info("  索引创建成功")

            # 统计信息
            result = conn.execute(text("SELECT COUNT(*) FROM users"))
            user_count = result.fetchone()[0]
            logger.info(f"\n迁移完成！用户总数: {user_count}，last_active_at 初始值均为 NULL")

        except Exception as e:
            conn.rollback()
            logger.error(f"迁移失败: {e}")
            import traceback
            logger.error(traceback.format_exc())
            raise


def migrate_down():
    """回滚迁移：删除 users.last_active_at 列"""
    logger.info("开始回滚：删除 users.last_active_at 列")

    with engine.connect() as conn:
        try:
            conn.execute(text("DROP INDEX IF EXISTS ix_users_last_active_at"))
            conn.execute(text("ALTER TABLE users DROP COLUMN IF EXISTS last_active_at"))
            conn.commit()
            logger.info("回滚完成")
        except Exception as e:
            conn.rollback()
            logger.error(f"回滚失败: {e}")
            import traceback
            logger.error(traceback.format_exc())
            raise


def main():
    import argparse
    parser = argparse.ArgumentParser(description="users.last_active_at 迁移")
    parser.add_argument("action", choices=["up", "down"], help="up=执行 / down=回滚")
    args = parser.parse_args()
    migrate_up() if args.action == "up" else migrate_down()


if __name__ == "__main__":
    main()
