"""
数据库配置模块

提供PostgreSQL和Redis的连接配置和会话管理
"""
from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session
from contextlib import contextmanager
import redis
from typing import Generator
import os
from dotenv import load_dotenv

# 加载环境变量
load_dotenv()

# ================================
# PostgreSQL配置
# ================================

# 构建数据库URL
POSTGRES_USER = os.getenv("POSTGRES_USER", "postgres")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD", "")
POSTGRES_HOST = os.getenv("POSTGRES_HOST", "localhost")
POSTGRES_PORT = os.getenv("POSTGRES_PORT", "5432")
POSTGRES_DB = os.getenv("POSTGRES_DB", "data_agent")

DATABASE_URL = f"postgresql://{POSTGRES_USER}:{POSTGRES_PASSWORD}@{POSTGRES_HOST}:{POSTGRES_PORT}/{POSTGRES_DB}"

# 创建引擎
engine = create_engine(
    DATABASE_URL,
    pool_size=int(os.getenv("POSTGRES_POOL_SIZE", "20")),
    max_overflow=int(os.getenv("POSTGRES_MAX_OVERFLOW", "0")),
    pool_pre_ping=True,  # 检查连接是否有效
    pool_recycle=3600,   # 1小时后回收连接
    echo=os.getenv("DEBUG", "false").lower() == "true",  # 开发环境打印SQL
)

# 创建会话工厂
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# ORM基类
Base = declarative_base()


def get_db() -> Generator[Session, None, None]:
    """
    获取数据库会话（用于FastAPI依赖注入）

    Yields:
        Session: SQLAlchemy会话对象

    Example:
        @app.get("/items")
        def get_items(db: Session = Depends(get_db)):
            return db.query(Item).all()
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def get_db_context():
    """
    获取数据库会话（用于非FastAPI场景）

    Example:
        with get_db_context() as db:
            items = db.query(Item).all()
    """
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def init_db():
    """
    初始化数据库（创建所有表）

    注意：在生产环境应该使用Alembic进行迁移
    """
    from backend.models import conversation, task, report  # noqa

    Base.metadata.create_all(bind=engine)
    print("✓ 数据库表已创建")


def drop_db():
    """
    删除所有表（仅用于开发/测试）

    警告：此操作会删除所有数据！
    """
    if os.getenv("ENVIRONMENT") == "production":
        raise RuntimeError("不能在生产环境执行此操作")

    Base.metadata.drop_all(bind=engine)
    print("✓ 数据库表已删除")


# ================================
# Redis配置
# ================================

REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", None)
REDIS_DB = int(os.getenv("REDIS_DB", "0"))

# 创建Redis连接池
redis_pool = redis.ConnectionPool(
    host=REDIS_HOST,
    port=REDIS_PORT,
    password=REDIS_PASSWORD if REDIS_PASSWORD else None,
    db=REDIS_DB,
    max_connections=int(os.getenv("REDIS_MAX_CONNECTIONS", "50")),
    decode_responses=True,  # 自动解码为字符串
)

# 创建Redis客户端
redis_client = redis.Redis(connection_pool=redis_pool)


def get_redis() -> redis.Redis:
    """
    获取Redis客户端

    Returns:
        redis.Redis: Redis客户端对象

    Example:
        r = get_redis()
        r.set("key", "value")
        value = r.get("key")
    """
    return redis_client


def test_redis_connection() -> bool:
    """
    测试Redis连接

    Returns:
        bool: 连接是否成功
    """
    try:
        redis_client.ping()
        return True
    except Exception as e:
        print(f"Redis连接失败: {e}")
        return False


def test_postgres_connection() -> bool:
    """
    测试PostgreSQL连接

    Returns:
        bool: 连接是否成功
    """
    try:
        with get_db_context() as db:
            db.execute("SELECT 1")
        return True
    except Exception as e:
        print(f"PostgreSQL连接失败: {e}")
        return False


# ================================
# 缓存辅助函数
# ================================

def cache_set(key: str, value: str, ttl: int = 300):
    """
    设置缓存

    Args:
        key: 缓存键
        value: 缓存值
        ttl: 过期时间（秒），默认5分钟
    """
    redis_client.setex(key, ttl, value)


def cache_get(key: str):
    """
    获取缓存

    Args:
        key: 缓存键

    Returns:
        缓存值，如果不存在返回None
    """
    return redis_client.get(key)


def cache_delete(key: str):
    """
    删除缓存

    Args:
        key: 缓存键
    """
    redis_client.delete(key)


def cache_exists(key: str) -> bool:
    """
    检查缓存是否存在

    Args:
        key: 缓存键

    Returns:
        是否存在
    """
    return redis_client.exists(key) > 0


# ================================
# 启动检查
# ================================

def check_database_connections():
    """
    检查所有数据库连接

    在应用启动时调用，确保所有数据库都可用
    """
    print("\n" + "=" * 60)
    print("数据库连接检查")
    print("=" * 60)

    # 检查PostgreSQL
    if test_postgres_connection():
        print("✓ PostgreSQL 连接成功")
        print(f"  - 地址: {POSTGRES_HOST}:{POSTGRES_PORT}")
        print(f"  - 数据库: {POSTGRES_DB}")
    else:
        print("✗ PostgreSQL 连接失败")
        raise RuntimeError("PostgreSQL连接失败，请检查配置")

    # 检查Redis
    if test_redis_connection():
        print("✓ Redis 连接成功")
        print(f"  - 地址: {REDIS_HOST}:{REDIS_PORT}")
        print(f"  - 数据库: {REDIS_DB}")
    else:
        print("✗ Redis 连接失败")
        raise RuntimeError("Redis连接失败，请检查配置")

    print("=" * 60 + "\n")


if __name__ == "__main__":
    """测试数据库连接"""
    check_database_connections()
