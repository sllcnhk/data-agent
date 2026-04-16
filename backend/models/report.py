"""
报表模型

存储用户创建的报表和图表配置
"""
from sqlalchemy import Column, String, DateTime, Text, Integer, Boolean, Index, Enum as SQLEnum
from sqlalchemy.dialects.postgresql import UUID, JSONB
from datetime import datetime
import uuid
import enum

from backend.config.database import Base


class ReportType(str, enum.Enum):
    """报表类型枚举"""
    DASHBOARD = "dashboard"           # 仪表板（多图表）
    SINGLE_CHART = "single_chart"     # 单图表
    TABLE = "table"                   # 数据表格
    PIVOT_TABLE = "pivot_table"       # 透视表
    CUSTOM = "custom"                 # 自定义报表


class ChartType(str, enum.Enum):
    """图表类型枚举"""
    LINE = "line"                     # 折线图
    BAR = "bar"                       # 柱状图
    PIE = "pie"                       # 饼图
    SCATTER = "scatter"               # 散点图
    AREA = "area"                     # 面积图
    HEATMAP = "heatmap"               # 热力图
    FUNNEL = "funnel"                 # 漏斗图
    GAUGE = "gauge"                   # 仪表盘
    RADAR = "radar"                   # 雷达图
    TREEMAP = "treemap"               # 矩形树图
    SANKEY = "sankey"                 # 桑基图
    TABLE = "table"                   # 表格


class ShareScope(str, enum.Enum):
    """分享范围枚举"""
    PRIVATE = "private"               # 私有
    TEAM = "team"                     # 团队内
    PUBLIC = "public"                 # 公开
    CUSTOM = "custom"                 # 自定义权限


class Report(Base):
    """报表表"""

    __tablename__ = "reports"

    # 主键
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)

    # 外键（可选 - 报表可能源自对话，也可能直接创建）
    conversation_id = Column(
        UUID(as_uuid=True),
        nullable=True,
        index=True,
        comment="源对话ID"
    )

    # 基本信息
    name = Column(String(200), nullable=False, comment="报表名称")
    description = Column(Text, nullable=True, comment="报表描述")

    # 报表类型
    report_type = Column(
        SQLEnum(ReportType),
        nullable=False,
        default=ReportType.DASHBOARD,
        comment="报表类型"
    )

    # 数据源配置
    data_sources = Column(JSONB, nullable=True, comment="数据源配置列表")
    # 格式示例:
    # [
    #   {
    #     "id": "ds1",
    #     "type": "clickhouse",
    #     "env": "idn",
    #     "database": "default",
    #     "query": "SELECT ...",
    #     "refresh_interval": 300
    #   }
    # ]

    # 布局配置
    layout = Column(JSONB, nullable=True, comment="报表布局配置")
    # 格式示例:
    # {
    #   "type": "grid",
    #   "columns": 12,
    #   "rows": 6,
    #   "items": [
    #     {
    #       "chart_id": "chart1",
    #       "x": 0, "y": 0,
    #       "w": 6, "h": 3
    #     }
    #   ]
    # }

    # 图表列表
    charts = Column(JSONB, nullable=True, comment="图表配置列表")
    # 每个图表的配置参见Chart模型的配置格式

    # 过滤器
    filters = Column(JSONB, nullable=True, comment="全局过滤器配置")
    # 格式示例:
    # {
    #   "date_range": {
    #     "type": "date_range",
    #     "default": "last_7_days",
    #     "label": "时间范围"
    #   },
    #   "country": {
    #     "type": "select",
    #     "options": ["IDN", "SG", "MX"],
    #     "label": "国家"
    #   }
    # }

    # 样式主题
    theme = Column(String(50), default="light", comment="主题: light, dark, custom")
    style_config = Column(JSONB, nullable=True, comment="自定义样式配置")

    # 权限和分享
    share_scope = Column(
        SQLEnum(ShareScope),
        default=ShareScope.PRIVATE,
        comment="分享范围"
    )
    allowed_users = Column(JSONB, nullable=True, comment="允许访问的用户ID列表")
    allowed_teams = Column(JSONB, nullable=True, comment="允许访问的团队ID列表")

    # 刷新配置
    auto_refresh = Column(Boolean, default=False, comment="是否自动刷新")
    refresh_interval = Column(Integer, nullable=True, comment="刷新间隔(秒)")

    # ── 图表报告生成字段（2026-04-13 新增）──────────────────────────────────
    # 报告类型：dashboard（报表）| document（报告）
    doc_type = Column(String(20), nullable=False, default="dashboard", comment="报告类型: dashboard | document")
    # 所有者用户名（与 customer_data/{username}/ 路径对应）
    username = Column(String(100), nullable=True, index=True, comment="所有者用户名")
    # 数据刷新令牌（公开可用，无需登录即可刷新图表数据）
    refresh_token = Column(String(64), nullable=True, unique=True, index=True, comment="数据刷新令牌")
    # 生成的 HTML 报告文件相对路径（相对于 customer_data 根目录）
    report_file_path = Column(Text, nullable=True, comment="HTML报告文件路径")
    # LLM 生成的分析总结文字
    llm_summary = Column(Text, nullable=True, comment="LLM生成的分析总结")
    # 总结生成状态：pending / generating / done / failed
    summary_status = Column(String(20), nullable=True, default="pending", comment="总结生成状态")
    # ─────────────────────────────────────────────────────────────────────────

    # 统计
    view_count = Column(Integer, default=0, comment="浏览次数")
    last_viewed_at = Column(DateTime, nullable=True, comment="最后浏览时间")

    # 缓存配置
    cache_enabled = Column(Boolean, default=True, comment="是否启用缓存")
    cache_ttl = Column(Integer, default=300, comment="缓存过期时间(秒)")

    # 元数据
    tags = Column(JSONB, nullable=True, comment="标签")
    extra_metadata = Column(JSONB, nullable=True, comment="额外元数据")

    # 时间戳
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, comment="创建时间")
    updated_at = Column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
        comment="更新时间"
    )

    # 索引
    __table_args__ = (
        Index("idx_reports_conversation_id", "conversation_id"),
        Index("idx_reports_type", "report_type"),
        Index("idx_reports_created_at", "created_at"),
        Index("idx_reports_share_scope", "share_scope"),
    )

    def __repr__(self):
        return f"<Report(id={self.id}, name={self.name}, type={self.report_type})>"

    def to_dict(self):
        """转换为字典"""
        return {
            "id": str(self.id),
            "conversation_id": str(self.conversation_id) if self.conversation_id else None,
            "name": self.name,
            "description": self.description,
            "report_type": self.report_type.value if self.report_type else None,
            "data_sources": self.data_sources,
            "layout": self.layout,
            "charts": self.charts,
            "filters": self.filters,
            "theme": self.theme,
            "style_config": self.style_config,
            "share_scope": self.share_scope.value if self.share_scope else None,
            "allowed_users": self.allowed_users,
            "allowed_teams": self.allowed_teams,
            "auto_refresh": self.auto_refresh,
            "refresh_interval": self.refresh_interval,
            "view_count": self.view_count,
            "last_viewed_at": self.last_viewed_at.isoformat() if self.last_viewed_at else None,
            "cache_enabled": self.cache_enabled,
            "cache_ttl": self.cache_ttl,
            "tags": self.tags,
            "extra_metadata": self.extra_metadata,
            "doc_type": self.doc_type,
            "username": self.username,
            "refresh_token": self.refresh_token,
            "report_file_path": self.report_file_path,
            "llm_summary": self.llm_summary,
            "summary_status": self.summary_status,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    def increment_view_count(self):
        """增加浏览次数"""
        self.view_count = (self.view_count or 0) + 1
        self.last_viewed_at = datetime.utcnow()
        self.updated_at = datetime.utcnow()


class Chart(Base):
    """图表表（独立存储，可复用）"""

    __tablename__ = "charts"

    # 主键
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)

    # 外键
    report_id = Column(
        UUID(as_uuid=True),
        nullable=True,
        index=True,
        comment="所属报表ID（可为空表示独立图表）"
    )

    # 基本信息
    name = Column(String(200), nullable=False, comment="图表名称")
    description = Column(Text, nullable=True, comment="图表描述")

    # 图表类型
    chart_type = Column(
        SQLEnum(ChartType),
        nullable=False,
        comment="图表类型"
    )

    # 数据配置
    data_source_id = Column(String(100), nullable=True, comment="数据源ID")
    query = Column(Text, nullable=True, comment="数据查询SQL")
    data_config = Column(JSONB, nullable=True, comment="数据配置")
    # 格式示例:
    # {
    #   "x_field": "date",
    #   "y_field": "revenue",
    #   "series_field": "country",
    #   "aggregate": "sum",
    #   "sort": {"field": "date", "order": "asc"}
    # }

    # 图表配置
    chart_config = Column(JSONB, nullable=False, comment="图表配置")
    # 格式示例（ECharts/G2配置）:
    # {
    #   "title": {"text": "Revenue Trend"},
    #   "xAxis": {"type": "category"},
    #   "yAxis": {"type": "value"},
    #   "series": [{
    #     "type": "line",
    #     "smooth": true
    #   }]
    # }

    # 交互配置
    interactions = Column(JSONB, nullable=True, comment="交互配置")
    # 格式示例:
    # {
    #   "tooltip": {"enabled": true},
    #   "zoom": {"enabled": true},
    #   "drill_down": {"enabled": false}
    # }

    # 样式
    width = Column(Integer, nullable=True, comment="宽度(像素)")
    height = Column(Integer, nullable=True, comment="高度(像素)")
    style = Column(JSONB, nullable=True, comment="自定义样式")

    # 缓存
    cached_data = Column(JSONB, nullable=True, comment="缓存的数据")
    cache_expires_at = Column(DateTime, nullable=True, comment="缓存过期时间")

    # 时间戳
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, comment="创建时间")
    updated_at = Column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
        comment="更新时间"
    )

    # 索引
    __table_args__ = (
        Index("idx_charts_report_id", "report_id"),
        Index("idx_charts_type", "chart_type"),
        Index("idx_charts_created_at", "created_at"),
    )

    def __repr__(self):
        return f"<Chart(id={self.id}, name={self.name}, type={self.chart_type})>"

    def to_dict(self):
        """转换为字典"""
        return {
            "id": str(self.id),
            "report_id": str(self.report_id) if self.report_id else None,
            "name": self.name,
            "description": self.description,
            "chart_type": self.chart_type.value if self.chart_type else None,
            "data_source_id": self.data_source_id,
            "query": self.query,
            "data_config": self.data_config,
            "chart_config": self.chart_config,
            "interactions": self.interactions,
            "width": self.width,
            "height": self.height,
            "style": self.style,
            "cached_data": self.cached_data,
            "cache_expires_at": self.cache_expires_at.isoformat() if self.cache_expires_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    def is_cache_valid(self) -> bool:
        """检查缓存是否有效"""
        if not self.cached_data or not self.cache_expires_at:
            return False
        return datetime.utcnow() < self.cache_expires_at

    def update_cache(self, data: dict, ttl: int = 300):
        """更新缓存数据"""
        from datetime import timedelta
        self.cached_data = data
        self.cache_expires_at = datetime.utcnow() + timedelta(seconds=ttl)
        self.updated_at = datetime.utcnow()
