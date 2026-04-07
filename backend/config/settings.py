"""
应用配置模块

集中管理所有应用配置
"""
from pydantic_settings import BaseSettings
from pydantic import Field, field_validator
from typing import List, Optional
from pathlib import Path
import os

# 项目根目录（单一事实来源）
# settings.py 位于 backend/config/settings.py，向上三级即为项目根
_PROJECT_ROOT: Path = Path(__file__).parent.parent.parent.resolve()


class Settings(BaseSettings):
    """应用配置类"""

    # ================================
    # 应用基础配置
    # ================================
    app_name: str = Field(default="DataAgentSystem", env="APP_NAME")
    app_version: str = Field(default="0.1.0", env="APP_VERSION")
    debug: bool = Field(default=False, env="DEBUG")
    environment: str = Field(default="development", env="ENVIRONMENT")

    # 服务配置
    host: str = Field(default="0.0.0.0", env="HOST")
    port: int = Field(default=8000, env="PORT")
    frontend_url: str = Field(default="http://localhost:3000", env="FRONTEND_URL")

    # 日志配置
    log_level: str = Field(default="INFO", env="LOG_LEVEL")
    log_dir: str = Field(default="logs", env="LOG_DIR")

    # ================================
    # 数据库配置
    # ================================
    # PostgreSQL
    postgres_host: str = Field(default="localhost", env="POSTGRES_HOST")
    postgres_port: int = Field(default=5432, env="POSTGRES_PORT")
    postgres_db: str = Field(default="data_agent", env="POSTGRES_DB")
    postgres_user: str = Field(default="postgres", env="POSTGRES_USER")
    postgres_password: str = Field(default="", env="POSTGRES_PASSWORD")
    postgres_pool_size: int = Field(default=20, env="POSTGRES_POOL_SIZE")
    postgres_max_overflow: int = Field(default=0, env="POSTGRES_MAX_OVERFLOW")

    # Redis
    redis_host: str = Field(default="localhost", env="REDIS_HOST")
    redis_port: int = Field(default=6379, env="REDIS_PORT")
    redis_password: Optional[str] = Field(default=None, env="REDIS_PASSWORD")
    redis_db: int = Field(default=0, env="REDIS_DB")
    redis_max_connections: int = Field(default=50, env="REDIS_MAX_CONNECTIONS")

    # ================================
    # LLM API配置
    # ================================
    # Anthropic Claude
    anthropic_api_key: str = Field(default="", env="ANTHROPIC_API_KEY")
    anthropic_auth_token: str = Field(default="", env="ANTHROPIC_AUTH_TOKEN")  # 中转服务令牌
    anthropic_base_url: str = Field(
        default="https://api.anthropic.com", env="ANTHROPIC_BASE_URL"
    )  # 中转服务地址
    anthropic_default_model: str = Field(
        default="claude-sonnet-4-6", env="ANTHROPIC_DEFAULT_MODEL"
    )
    anthropic_fallback_models: str = Field(
        default="claude-sonnet-4-6,claude-sonnet-4-5-20250929,claude-haiku-4-5-20251001,minimax-m2",
        env="ANTHROPIC_FALLBACK_MODELS"
    )  # 备用模型列表（逗号分隔）
    anthropic_enable_fallback: bool = Field(
        default=True, env="ANTHROPIC_ENABLE_FALLBACK"
    )  # 是否启用自动故障转移
    anthropic_max_tokens: int = Field(default=8192, env="ANTHROPIC_MAX_TOKENS")
    anthropic_temperature: float = Field(default=0.7, env="ANTHROPIC_TEMPERATURE")

    # Claude 代理配置（针对中转服务）
    anthropic_enable_proxy: bool = Field(
        default=False, env="ANTHROPIC_ENABLE_PROXY"
    )  # 是否启用代理
    anthropic_proxy_http: str = Field(
        default="", env="ANTHROPIC_PROXY_HTTP"
    )  # HTTP 代理地址
    anthropic_proxy_https: str = Field(
        default="", env="ANTHROPIC_PROXY_HTTPS"
    )  # HTTPS 代理地址

    # OpenAI
    openai_api_key: str = Field(default="", env="OPENAI_API_KEY")
    openai_default_model: str = Field(
        default="gpt-4-turbo-preview", env="OPENAI_DEFAULT_MODEL"
    )
    openai_max_tokens: int = Field(default=4096, env="OPENAI_MAX_TOKENS")
    openai_temperature: float = Field(default=0.7, env="OPENAI_TEMPERATURE")
    openai_org_id: Optional[str] = Field(default=None, env="OPENAI_ORG_ID")

    # OpenAI 代理配置
    openai_enable_proxy: bool = Field(default=False, env="OPENAI_ENABLE_PROXY")
    openai_proxy_http: str = Field(default="", env="OPENAI_PROXY_HTTP")
    openai_proxy_https: str = Field(default="", env="OPENAI_PROXY_HTTPS")

    # Google Gemini
    google_api_key: str = Field(default="", env="GOOGLE_API_KEY")
    google_default_model: str = Field(default="gemini-pro", env="GOOGLE_DEFAULT_MODEL")
    google_max_tokens: int = Field(default=2048, env="GOOGLE_MAX_TOKENS")
    google_temperature: float = Field(default=0.7, env="GOOGLE_TEMPERATURE")

    # Google 代理配置
    google_enable_proxy: bool = Field(default=False, env="GOOGLE_ENABLE_PROXY")
    google_proxy_http: str = Field(default="", env="GOOGLE_PROXY_HTTP")
    google_proxy_https: str = Field(default="", env="GOOGLE_PROXY_HTTPS")

    # 默认模型
    default_llm_model: str = Field(default="claude", env="DEFAULT_LLM_MODEL")

    # ================================
    # ClickHouse配置
    # ================================
    # ClickHouse IDN
    clickhouse_idn_host: str = Field(default="", env="CLICKHOUSE_IDN_HOST")
    clickhouse_idn_port: int = Field(default=9000, env="CLICKHOUSE_IDN_PORT")
    clickhouse_idn_http_port: int = Field(default=8123, env="CLICKHOUSE_IDN_HTTP_PORT")
    clickhouse_idn_database: str = Field(default="default", env="CLICKHOUSE_IDN_DATABASE")
    clickhouse_idn_user: str = Field(default="default", env="CLICKHOUSE_IDN_USER")
    clickhouse_idn_password: str = Field(default="", env="CLICKHOUSE_IDN_PASSWORD")

    # ClickHouse SG
    clickhouse_sg_host: str = Field(default="", env="CLICKHOUSE_SG_HOST")
    clickhouse_sg_port: int = Field(default=9000, env="CLICKHOUSE_SG_PORT")
    clickhouse_sg_http_port: int = Field(default=8123, env="CLICKHOUSE_SG_HTTP_PORT")
    clickhouse_sg_database: str = Field(default="default", env="CLICKHOUSE_SG_DATABASE")
    clickhouse_sg_user: str = Field(default="default", env="CLICKHOUSE_SG_USER")
    clickhouse_sg_password: str = Field(default="", env="CLICKHOUSE_SG_PASSWORD")

    # ClickHouse MX
    clickhouse_mx_host: str = Field(default="", env="CLICKHOUSE_MX_HOST")
    clickhouse_mx_port: int = Field(default=9000, env="CLICKHOUSE_MX_PORT")
    clickhouse_mx_http_port: int = Field(default=8123, env="CLICKHOUSE_MX_HTTP_PORT")
    clickhouse_mx_database: str = Field(default="default", env="CLICKHOUSE_MX_DATABASE")
    clickhouse_mx_user: str = Field(default="default", env="CLICKHOUSE_MX_USER")
    clickhouse_mx_password: str = Field(default="", env="CLICKHOUSE_MX_PASSWORD")

    # ================================
    # ClickHouse 只读连接（Readonly）
    # HOST/PORT/DATABASE 留空时继承对应 admin 值，支持读副本独立 host
    # ================================
    # IDN ReadOnly
    clickhouse_idn_readonly_host: str = Field(default="", env="CLICKHOUSE_IDN_READONLY_HOST")
    clickhouse_idn_readonly_port: Optional[int] = Field(default=None, env="CLICKHOUSE_IDN_READONLY_PORT")
    clickhouse_idn_readonly_http_port: Optional[int] = Field(default=None, env="CLICKHOUSE_IDN_READONLY_HTTP_PORT")
    clickhouse_idn_readonly_database: str = Field(default="", env="CLICKHOUSE_IDN_READONLY_DATABASE")
    clickhouse_idn_readonly_user: str = Field(default="", env="CLICKHOUSE_IDN_READONLY_USER")
    clickhouse_idn_readonly_password: str = Field(default="", env="CLICKHOUSE_IDN_READONLY_PASSWORD")

    # SG ReadOnly
    clickhouse_sg_readonly_host: str = Field(default="", env="CLICKHOUSE_SG_READONLY_HOST")
    clickhouse_sg_readonly_port: Optional[int] = Field(default=None, env="CLICKHOUSE_SG_READONLY_PORT")
    clickhouse_sg_readonly_http_port: Optional[int] = Field(default=None, env="CLICKHOUSE_SG_READONLY_HTTP_PORT")
    clickhouse_sg_readonly_database: str = Field(default="", env="CLICKHOUSE_SG_READONLY_DATABASE")
    clickhouse_sg_readonly_user: str = Field(default="", env="CLICKHOUSE_SG_READONLY_USER")
    clickhouse_sg_readonly_password: str = Field(default="", env="CLICKHOUSE_SG_READONLY_PASSWORD")

    # MX ReadOnly
    clickhouse_mx_readonly_host: str = Field(default="", env="CLICKHOUSE_MX_READONLY_HOST")
    clickhouse_mx_readonly_port: Optional[int] = Field(default=None, env="CLICKHOUSE_MX_READONLY_PORT")
    clickhouse_mx_readonly_http_port: Optional[int] = Field(default=None, env="CLICKHOUSE_MX_READONLY_HTTP_PORT")
    clickhouse_mx_readonly_database: str = Field(default="", env="CLICKHOUSE_MX_READONLY_DATABASE")
    clickhouse_mx_readonly_user: str = Field(default="", env="CLICKHOUSE_MX_READONLY_USER")
    clickhouse_mx_readonly_password: str = Field(default="", env="CLICKHOUSE_MX_READONLY_PASSWORD")

    # ================================
    # MySQL配置
    # ================================
    mysql_prod_host: str = Field(default="", env="MYSQL_PROD_HOST")
    mysql_prod_port: int = Field(default=3306, env="MYSQL_PROD_PORT")
    mysql_prod_database: str = Field(default="", env="MYSQL_PROD_DATABASE")
    mysql_prod_user: str = Field(default="", env="MYSQL_PROD_USER")
    mysql_prod_password: str = Field(default="", env="MYSQL_PROD_PASSWORD")

    mysql_staging_host: str = Field(default="", env="MYSQL_STAGING_HOST")
    mysql_staging_port: int = Field(default=3306, env="MYSQL_STAGING_PORT")
    mysql_staging_database: str = Field(default="", env="MYSQL_STAGING_DATABASE")
    mysql_staging_user: str = Field(default="", env="MYSQL_STAGING_USER")
    mysql_staging_password: str = Field(default="", env="MYSQL_STAGING_PASSWORD")

    # ================================
    # Lark/飞书配置
    # ================================
    lark_verification_token: str = Field(default="", env="LARK_VERIFICATION_TOKEN")
    lark_encrypt_key: str = Field(default="", env="LARK_ENCRYPT_KEY")

    # ================================
    # MCP配置
    # ================================
    mcp_clickhouse_port: int = Field(default=50051, env="MCP_CLICKHOUSE_PORT")
    mcp_mysql_port: int = Field(default=50052, env="MCP_MYSQL_PORT")
    mcp_filesystem_port: int = Field(default=50053, env="MCP_FILESYSTEM_PORT")
    mcp_lark_port: int = Field(default=50054, env="MCP_LARK_PORT")

    # ── Filesystem MCP 目录权限 ──────────────────────────────────────────────
    # allowed_directories: FilesystemMCPServer 可访问的根目录列表（读 + 写范围上限）
    #   默认开放: customer_data/（数据输出区）+ .claude/skills/（技能文件只读）
    #   如需扩展可在 .env 中设置 ALLOWED_DIRECTORIES=dir1,dir2
    #
    # filesystem_write_allowed_dirs: FilesystemPermissionProxy 允许写入的目录列表
    #   在 allowed_directories 基础上进一步收窄写权限：
    #     customer_data/       — 数据文件读写
    #     .claude/skills/user/ — 用户自定义技能读写
    #   .claude/skills/ 根目录下的系统技能文件不可写
    allowed_directories: List[str] = Field(
        default_factory=lambda: [
            str(_PROJECT_ROOT / "customer_data"),
            str(_PROJECT_ROOT / ".claude" / "skills"),
        ],
        env="ALLOWED_DIRECTORIES"
    )

    filesystem_write_allowed_dirs: List[str] = Field(
        default_factory=lambda: [
            str(_PROJECT_ROOT / "customer_data"),
            str(_PROJECT_ROOT / ".claude" / "skills" / "user"),
        ],
        env="FILESYSTEM_WRITE_ALLOWED_DIRS"
    )

    # ── 文件输出按月子目录 ────────────────────────────────────────────────────
    # FILE_OUTPUT_DATE_SUBFOLDER=true 时，系统提示建议 Agent 将输出文件存入
    # customer_data/{username}/YYYY-MM/ 子目录，方便按月管理和批量清理历史数据。
    file_output_date_subfolder: bool = Field(
        default=False, env="FILE_OUTPUT_DATE_SUBFOLDER"
    )

    # ================================
    # 文件上传配置
    # ================================
    max_upload_size: str = Field(default="100MB", env="MAX_UPLOAD_SIZE")
    upload_dir: str = Field(default="uploads", env="UPLOAD_DIR")
    export_dir: str = Field(default="exports", env="EXPORT_DIR")

    # 导出任务 ClickHouse 查询设置（应用层 per-query 覆盖，不修改服务器配置）
    export_query_max_execution_time: int = Field(
        default=300,
        env="EXPORT_QUERY_MAX_EXECUTION_TIME",
    )
    export_chunk_size: int = Field(
        default=200_000,
        env="EXPORT_CHUNK_SIZE",
    )
    export_auto_chunk_threshold: int = Field(
        default=500_000,
        env="EXPORT_AUTO_CHUNK_THRESHOLD",
    )

    allowed_file_extensions: List[str] = Field(
        default_factory=lambda: [".csv", ".xlsx", ".xls", ".json", ".parquet", ".txt"],
        env="ALLOWED_FILE_EXTENSIONS"
    )

    # ================================
    # 会话配置
    # ================================
    session_secret_key: str = Field(default="change-this-in-production", env="SESSION_SECRET_KEY")
    session_expire_minutes: int = Field(default=60, env="SESSION_EXPIRE_MINUTES")
    jwt_algorithm: str = Field(default="HS256", env="JWT_ALGORITHM")

    # ================================
    # 上下文管理
    # ================================
    # Context 窗口配置
    max_context_messages: int = Field(default=30, env="MAX_CONTEXT_MESSAGES")
    max_context_tokens: int = Field(default=150000, env="MAX_CONTEXT_TOKENS")  # Claude 4 Sonnet 200K * 0.75

    # 压缩策略配置
    context_compression_strategy: str = Field(default="smart", env="CONTEXT_COMPRESSION_STRATEGY")
    context_utilization_target: float = Field(default=0.75, env="CONTEXT_UTILIZATION_TARGET")

    # 向量数据库配置
    vector_db_type: str = Field(default="chroma", env="VECTOR_DB_TYPE")
    vector_db_path: str = Field(default="./data/vector_db", env="VECTOR_DB_PATH")
    enable_semantic_compression: bool = Field(default=False, env="ENABLE_SEMANTIC_COMPRESSION")  # Phase 3 启用

    # 缓存配置
    enable_context_cache: bool = Field(default=True, env="ENABLE_CONTEXT_CACHE")
    context_cache_ttl: int = Field(default=300, env="CONTEXT_CACHE_TTL")  # 5分钟

    # ================================
    # Skill 语义路由配置
    # ================================
    skill_match_mode: str = Field(
        default="hybrid", env="SKILL_MATCH_MODE",
        description="技能命中模式: keyword(纯关键词) | llm(纯LLM语义) | hybrid(混合，推荐)"
    )
    skill_semantic_threshold: float = Field(
        default=0.45, env="SKILL_SEMANTIC_THRESHOLD",
        description="LLM 路由置信度最低阈值，低于此值的 skill 不注入"
    )
    skill_semantic_cache_ttl: int = Field(
        default=86400, env="SKILL_SEMANTIC_CACHE_TTL",
        description="路由结果缓存 TTL（秒），默认 24h"
    )
    skill_routing_cache_path: str = Field(
        default="./data/skill_routing_cache", env="SKILL_ROUTING_CACHE_PATH",
        description="ChromaDB 路由缓存持久化目录"
    )

    # ================================
    # Celery配置
    # ================================
    celery_broker_url: str = Field(default="redis://localhost:6379/1", env="CELERY_BROKER_URL")
    celery_result_backend: str = Field(default="redis://localhost:6379/2", env="CELERY_RESULT_BACKEND")

    # ================================
    # 监控配置
    # ================================
    enable_metrics: bool = Field(default=True, env="ENABLE_METRICS")
    metrics_port: int = Field(default=9090, env="METRICS_PORT")

    # ================================
    # CORS配置
    # ================================
    cors_origins: List[str] = Field(
        default_factory=lambda: ["http://localhost:3000", "http://localhost:3001"],
        env="CORS_ORIGINS"
    )
    cors_allow_credentials: bool = Field(default=True, env="CORS_ALLOW_CREDENTIALS")
    cors_allow_methods: List[str] = Field(
        default_factory=lambda: ["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        env="CORS_ALLOW_METHODS"
    )
    cors_allow_headers: str = Field(default="*", env="CORS_ALLOW_HEADERS")

    # ================================
    # API文档配置
    # ================================
    enable_docs: bool = Field(default=True, env="ENABLE_DOCS")
    docs_url: str = Field(default="/docs", env="DOCS_URL")
    redoc_url: str = Field(default="/redoc", env="REDOC_URL")

    # ================================
    # 功能开关
    # ================================
    enable_chat: bool = Field(default=True, env="ENABLE_CHAT")
    enable_database_agent: bool = Field(default=True, env="ENABLE_DATABASE_AGENT")
    enable_etl_agent: bool = Field(default=True, env="ENABLE_ETL_AGENT")
    enable_visualization_agent: bool = Field(default=True, env="ENABLE_VISUALIZATION_AGENT")
    enable_report_system: bool = Field(default=True, env="ENABLE_REPORT_SYSTEM")

    # MCP开关
    enable_mcp_clickhouse: bool = Field(default=True, env="ENABLE_MCP_CLICKHOUSE")
    enable_mcp_mysql: bool = Field(default=True, env="ENABLE_MCP_MYSQL")
    enable_mcp_filesystem: bool = Field(default=True, env="ENABLE_MCP_FILESYSTEM")
    enable_mcp_lark: bool = Field(default=False, env="ENABLE_MCP_LARK")

    # Skills开关
    enable_skill_database_query: bool = Field(default=True, env="ENABLE_SKILL_DATABASE_QUERY")
    enable_skill_data_analysis: bool = Field(default=True, env="ENABLE_SKILL_DATA_ANALYSIS")
    enable_skill_sql_generation: bool = Field(default=True, env="ENABLE_SKILL_SQL_GENERATION")
    enable_skill_chart_generation: bool = Field(default=True, env="ENABLE_SKILL_CHART_GENERATION")
    enable_skill_etl_design: bool = Field(default=True, env="ENABLE_SKILL_ETL_DESIGN")

    # ================================
    # 性能配置
    # ================================
    cache_ttl_short: int = Field(default=300, env="CACHE_TTL_SHORT")
    cache_ttl_medium: int = Field(default=1800, env="CACHE_TTL_MEDIUM")
    cache_ttl_long: int = Field(default=86400, env="CACHE_TTL_LONG")

    rate_limit_enabled: bool = Field(default=True, env="RATE_LIMIT_ENABLED")
    rate_limit_per_minute: int = Field(default=60, env="RATE_LIMIT_PER_MINUTE")

    # ================================
    # 安全配置
    # ================================
    enable_auth: bool = Field(default=False, env="ENABLE_AUTH")
    enable_https: bool = Field(default=False, env="ENABLE_HTTPS")
    ssl_cert_path: Optional[str] = Field(default=None, env="SSL_CERT_PATH")
    ssl_key_path: Optional[str] = Field(default=None, env="SSL_KEY_PATH")

    # Admin secret token for system-level operations.
    # Set ADMIN_SECRET_TOKEN=<secret> in .env to enable protected endpoints.
    # If not set, admin endpoints return 503 (disabled, safe by default).
    admin_secret_token: Optional[str] = Field(default=None, env="ADMIN_SECRET_TOKEN")

    # ================================
    # JWT / 用户认证配置（ENABLE_AUTH=true 时生效）
    # ================================
    # JWT 签名密钥（启用认证时请设为长随机字符串，默认复用 session_secret_key）
    jwt_secret: str = Field(default="change-this-in-production", env="JWT_SECRET")
    # access_token 有效期（分钟），默认 8h（建议与 session_idle_timeout_minutes 保持一致）
    access_token_expire_minutes: int = Field(default=480, env="ACCESS_TOKEN_EXPIRE_MINUTES")
    # refresh_token 有效期（天），默认 14 天
    refresh_token_expire_days: int = Field(default=14, env="REFRESH_TOKEN_EXPIRE_DAYS")
    # session 空闲超时（分钟），默认 120 min。超过此时间无 API 活动则 /auth/refresh 返回 401。
    # 注意：ACCESS_TOKEN_EXPIRE_MINUTES 须 <= 此值，否则空闲检测在 access_token 过期前无法触发。
    session_idle_timeout_minutes: int = Field(default=120, env="SESSION_IDLE_TIMEOUT_MINUTES")

    # ================================
    # Lark OAuth（预留，不启用时留空）
    # ================================
    lark_app_id: str = Field(default="", env="LARK_APP_ID")
    lark_app_secret: str = Field(default="", env="LARK_APP_SECRET")
    lark_redirect_uri: str = Field(
        default="http://localhost:3000/auth/lark/callback",
        env="LARK_REDIRECT_URI",
    )

    # ================================
    # 备份配置
    # ================================
    backup_enabled: bool = Field(default=True, env="BACKUP_ENABLED")
    backup_dir: str = Field(default="backups", env="BACKUP_DIR")
    backup_retention_days: int = Field(default=30, env="BACKUP_RETENTION_DAYS")

    # ── 文件系统目录路径：将相对路径解析为绝对路径（相对于项目根目录）─────────────
    # 设计目标：.env 可使用可移植的相对路径（如 customer_data,.claude/skills），
    #           部署到任意服务器无需修改路径配置。
    #           已是绝对路径的值保持不变，兼容旧有配置。
    @field_validator("allowed_directories", "filesystem_write_allowed_dirs", mode="after")
    @classmethod
    def _resolve_fs_paths(cls, v: List[str]) -> List[str]:
        """将相对路径解析为绝对路径（相对于 _PROJECT_ROOT）。绝对路径保持不变。"""
        result = []
        for d in v:
            p = Path(d)
            if p.is_absolute():
                result.append(str(p.resolve()))
            else:
                result.append(str((_PROJECT_ROOT / d).resolve()))
        return result

    # ── 只读整数端口字段：允许空字符串（pydantic v2 不接受 "" 作为 Optional[int]）
    @field_validator(
        "clickhouse_idn_readonly_port",
        "clickhouse_idn_readonly_http_port",
        "clickhouse_sg_readonly_port",
        "clickhouse_sg_readonly_http_port",
        "clickhouse_mx_readonly_port",
        "clickhouse_mx_readonly_http_port",
        mode="before",
    )
    @classmethod
    def _empty_str_to_none(cls, v):
        if v == "" or v is None:
            return None
        return v

    class Config:
        extra = "ignore"  # 忽略 .env 中未声明的字段（如额外 ClickHouse 环境），由 get_all_clickhouse_envs() 从 os.environ 读取
        # 尝试多个.env文件位置
        env_file = [
            ".env",  # 当前目录
            "../.env",  # 父目录(项目根目录)
            "../../.env",  # 祖父目录
        ]
        env_file_encoding = "utf-8"
        case_sensitive = False

    def get_database_url(self) -> str:
        """获取PostgreSQL数据库URL"""
        return f"postgresql://{self.postgres_user}:{self.postgres_password}@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"

    def get_redis_url(self) -> str:
        """获取Redis URL"""
        if self.redis_password:
            return f"redis://:{self.redis_password}@{self.redis_host}:{self.redis_port}/{self.redis_db}"
        return f"redis://{self.redis_host}:{self.redis_port}/{self.redis_db}"

    def get_all_clickhouse_envs(self) -> list:
        """
        自动发现所有已定义的 ClickHouse 环境名称。

        扫描两处来源，以支持任意新环境无需改动此文件：
          1. pydantic model_fields — 已声明 clickhouse_{env}_host 字段的已知环境
          2. os.environ — 直接在 .env 中添加 CLICKHOUSE_{ENV}_HOST 的新环境

        返回值不过滤 host 是否非空；是否实际注册由 initialize_all() 中的 host 检查决定。
        """
        import re as _re
        envs: set = set()

        # 1. Pydantic 字段（idn / sg / mx 等已声明的环境）
        for field_name in self.model_fields:
            m = _re.match(r'^clickhouse_(\w+)_host$', field_name)
            if m and 'readonly' not in field_name:
                envs.add(m.group(1))

        # 2. os.environ（支持无 pydantic 字段的新环境，如 jp / us / eu 等）
        for key in os.environ:
            m = _re.match(r'^CLICKHOUSE_(\w+)_HOST$', key)
            if m and 'READONLY' not in key:
                envs.add(m.group(1).lower())

        return sorted(envs)

    def get_clickhouse_config(self, env: str, level: str = "admin") -> dict:
        """
        获取 ClickHouse 配置（支持任意 env，无需在此文件中预声明）。

        Args:
            env:   环境名称（idn / sg / mx / 自定义）
            level: 连接权限级别（"admin" 高权限 | "readonly" 只读）
                   readonly 的 host/port/http_port/database 未填时自动继承 admin 值

        Returns:
            ClickHouse 配置字典，包含 host/port/http_port/database/user/password/level
        """
        env_l = env.lower()
        env_u = env_l.upper()
        level = level.lower()

        def _ga(suffix: str):
            """优先读 pydantic 字段，fallback 到 os.environ 原始值（支持无字段的新 env）。
            os.environ 查找同时尝试全大写和原始大小写，兼容 Linux 大小写敏感环境。"""
            val = getattr(self, f"clickhouse_{env_l}_{suffix}", None)
            if val is not None:
                return val
            key_upper = f"CLICKHOUSE_{env_u}_{suffix.upper()}"
            v = os.environ.get(key_upper, "")
            if v:
                return v
            # fallback: 遍历 os.environ 做大小写不敏感查找（兼容 SG_Azure 等混合大小写 env 名）
            key_lower = key_upper.lower()
            for k, kv in os.environ.items():
                if k.lower() == key_lower:
                    return kv
            return ""

        def _ga_ro(suffix: str):
            val = getattr(self, f"clickhouse_{env_l}_readonly_{suffix}", None)
            if val is not None:
                return val
            key_upper = f"CLICKHOUSE_{env_u}_READONLY_{suffix.upper()}"
            v = os.environ.get(key_upper, "")
            if v:
                return v
            key_lower = key_upper.lower()
            for k, kv in os.environ.items():
                if k.lower() == key_lower:
                    return kv
            return ""

        def _to_int(v, default: int) -> int:
            """将端口值转为 int；None / 空字符串 / 0 均视为"未配置"，使用 default。"""
            if v is None or v == "" or v == 0:
                return default
            try:
                return int(v)
            except (ValueError, TypeError):
                return default

        admin = {
            "host": _ga("host"),
            "port": _to_int(_ga("port"), 9000),
            "http_port": _to_int(_ga("http_port"), 8123),
            "database": _ga("database") or "default",
            "user": _ga("user"),
            "password": _ga("password"),
        }

        if level == "admin":
            return {**admin, "level": "admin"}

        # ── Readonly: 未填字段继承 admin 值（支持读副本同 host 的简化配置）──
        return {
            "host": _ga_ro("host") or admin["host"],
            "port": _to_int(_ga_ro("port"), admin["port"]),
            "http_port": _to_int(_ga_ro("http_port"), admin["http_port"]),
            "database": _ga_ro("database") or admin["database"],
            "user": _ga_ro("user"),
            "password": _ga_ro("password"),
            "level": "readonly",
        }

    def has_readonly_credentials(self, env: str) -> bool:
        """
        判断指定环境是否已配置只读凭据（user 字段非空即视为已配置）。
        支持无 pydantic 字段的新 env（直接查 os.environ，大小写不敏感）。
        """
        env_l = env.lower()
        val = getattr(self, f"clickhouse_{env_l}_readonly_user", None)
        if val is not None:
            return bool(val)
        key_upper = f"CLICKHOUSE_{env_l.upper()}_READONLY_USER"
        v = os.environ.get(key_upper, "")
        if v:
            return bool(v)
        # 大小写不敏感 fallback（兼容 Linux + 混合大小写 env 名）
        key_lower = key_upper.lower()
        for k, kv in os.environ.items():
            if k.lower() == key_lower:
                return bool(kv)
        return False

    def get_mysql_config(self, env: str) -> dict:
        """
        获取MySQL配置

        Args:
            env: 环境名称（prod, staging）

        Returns:
            MySQL配置字典
        """
        env = env.lower()
        if env == "prod":
            return {
                "host": self.mysql_prod_host,
                "port": self.mysql_prod_port,
                "database": self.mysql_prod_database,
                "user": self.mysql_prod_user,
                "password": self.mysql_prod_password,
            }
        elif env == "staging":
            return {
                "host": self.mysql_staging_host,
                "port": self.mysql_staging_port,
                "database": self.mysql_staging_database,
                "user": self.mysql_staging_user,
                "password": self.mysql_staging_password,
            }
        else:
            raise ValueError(f"未知的MySQL环境: {env}")

    def get_proxy_config(self, provider: str) -> Optional[dict]:
        """
        获取指定模型提供商的代理配置

        Args:
            provider: 模型提供商名称（claude/anthropic, openai, google/gemini）

        Returns:
            代理配置字典，如果未启用代理则返回 None
            格式: {"http://": "...", "https://": "..."}
        """
        provider_lower = provider.lower()

        # 映射提供商名称到配置前缀
        prefix_map = {
            "claude": "anthropic",
            "anthropic": "anthropic",
            "openai": "openai",
            "gpt": "openai",
            "chatgpt": "openai",
            "google": "google",
            "gemini": "google",
        }

        prefix = prefix_map.get(provider_lower)
        if not prefix:
            return None

        # 获取是否启用代理
        enable_proxy = getattr(self, f"{prefix}_enable_proxy", False)
        if not enable_proxy:
            return None

        # 获取代理地址
        http_proxy = getattr(self, f"{prefix}_proxy_http", "")
        https_proxy = getattr(self, f"{prefix}_proxy_https", "")

        # 如果都没有配置，返回 None
        if not http_proxy and not https_proxy:
            return None

        # 构建代理配置字典（httpx 格式）
        proxies = {}
        if http_proxy:
            proxies["http://"] = http_proxy
        if https_proxy:
            proxies["https://"] = https_proxy

        return proxies


# ── 将 .env 中所有键（含 pydantic 未声明字段）注入 os.environ ──────────────
# 背景：pydantic-settings 读取 .env 只映射已声明字段，未声明字段（如新增的
# CLICKHOUSE_THAI_*/CLICKHOUSE_BR_* 等任意环境）不会进入 os.environ。
# get_all_clickhouse_envs() 的 source-2 依赖 os.environ 发现这些环境，
# 因此必须在 Settings() 实例化之前显式调用 load_dotenv()。
# override=False 保证系统/容器已设置的环境变量优先级不变。
try:
    from dotenv import load_dotenv as _load_dotenv
    # 目的：将 .env 中 pydantic 未声明的字段（如 CLICKHOUSE_THAI_* 等任意新增环境）
    # 注入 os.environ，供 get_all_clickhouse_envs() / get_clickhouse_config() 发现。
    #
    # 加载策略：
    #   override=False — 已存在于 os.environ 的值不覆盖（系统环境变量 / 测试 mock 优先）
    #   加载顺序：cwd → backend/ → 项目根（高优先先加载，后续低优先只填充缺口）
    _config_dir = os.path.dirname(os.path.abspath(__file__))  # backend/config/
    _dotenv_candidates = [
        os.path.normpath(os.path.join(os.getcwd(), ".env")),                # cwd（最高优先）
        os.path.normpath(os.path.join(_config_dir, "..", ".env")),          # backend/
        os.path.normpath(os.path.join(_config_dir, "..", "..", ".env")),    # 项目根（最低优先）
    ]
    for _dotenv_path in _dotenv_candidates:
        if os.path.isfile(_dotenv_path):
            _load_dotenv(_dotenv_path, override=False)
except ImportError:
    pass  # python-dotenv 未安装时静默跳过，pydantic-settings 仍正常读取已声明字段

# 创建全局配置实例
settings = Settings()


if __name__ == "__main__":
    """测试配置加载"""
    print("应用配置:")
    print(f"  - 应用名称: {settings.app_name}")
    print(f"  - 版本: {settings.app_version}")
    print(f"  - 环境: {settings.environment}")
    print(f"  - Debug: {settings.debug}")
    print(f"\n数据库URL: {settings.get_database_url()}")
    print(f"Redis URL: {settings.get_redis_url()}")
