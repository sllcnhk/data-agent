"""
数据库初始化脚本

使用Alembic生成和应用数据库迁移
"""
import os
import sys

# 添加项目根目录到Python路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from backend.config.database import check_database_connections, init_db, drop_db
from backend.config.settings import settings


def main():
    """主函数"""
    print("=" * 80)
    print("数据分析Agent系统 - 数据库初始化")
    print("=" * 80)
    print()

    # 检查数据库连接
    print("步骤 1/3: 检查数据库连接...")
    try:
        check_database_connections()
    except Exception as e:
        print(f"✗ 数据库连接失败: {e}")
        print("\n请检查以下配置:")
        print(f"  - PostgreSQL: {settings.postgres_host}:{settings.postgres_port}/{settings.postgres_db}")
        print(f"  - Redis: {settings.redis_host}:{settings.redis_port}/{settings.redis_db}")
        print("\n请确保:")
        print("  1. PostgreSQL 和 Redis 服务已启动")
        print("  2. .env 文件中的配置正确")
        print("  3. 数据库用户有足够的权限")
        return 1

    # 询问是否要重置数据库
    print("\n步骤 2/3: 数据库初始化选项")
    if settings.environment != "production":
        print("警告: 当前环境为开发/测试环境")
        reset = input("是否要重置数据库（删除所有表并重建）？[y/N]: ").strip().lower()
        if reset == 'y':
            print("\n正在删除所有表...")
            try:
                drop_db()
            except Exception as e:
                print(f"✗ 删除表失败: {e}")
                return 1
    else:
        print("当前环境为生产环境，不允许重置数据库")

    # 创建数据库表
    print("\n步骤 3/3: 创建数据库表...")
    try:
        init_db()
        print("\n✓ 数据库初始化完成！")
    except Exception as e:
        print(f"\n✗ 数据库初始化失败: {e}")
        import traceback
        traceback.print_exc()
        return 1

    # 显示创建的表
    print("\n已创建的表:")
    print("  - conversations (对话表)")
    print("  - messages (消息表)")
    print("  - context_snapshots (上下文快照表)")
    print("  - tasks (任务表)")
    print("  - task_history (任务历史表)")
    print("  - reports (报表表)")
    print("  - charts (图表表)")

    print("\n" + "=" * 80)
    print("数据库初始化成功！")
    print("=" * 80)
    print()

    # 显示下一步操作
    print("下一步:")
    print("  1. 启动后端服务: python backend/main.py")
    print("  2. 启动前端服务: cd frontend && npm run dev")
    print("  3. 访问应用: http://localhost:3000")
    print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
