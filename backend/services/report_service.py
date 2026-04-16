"""
报表服务

提供报表的CRUD操作和业务逻辑
"""
from typing import List, Optional, Dict, Any
from sqlalchemy.orm import Session
from sqlalchemy import desc, asc, and_
from sqlalchemy.exc import SQLAlchemyError
from uuid import UUID
from datetime import datetime

from backend.models.report import Report, Chart, ReportType, ChartType, ShareScope


class ReportService:
    """报表服务"""

    def __init__(self, db: Session):
        """
        初始化报表服务

        Args:
            db: 数据库会话
        """
        self.db = db

    def create_report(
        self,
        name: str,
        report_type: ReportType,
        description: Optional[str] = None,
        conversation_id: Optional[str] = None,
        data_sources: Optional[List[Dict[str, Any]]] = None,
        layout: Optional[Dict[str, Any]] = None,
        charts: Optional[List[Dict[str, Any]]] = None,
        filters: Optional[Dict[str, Any]] = None,
        theme: str = "light",
        share_scope: ShareScope = ShareScope.PRIVATE,
        auto_refresh: bool = False,
        refresh_interval: Optional[int] = None,
        tags: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Report:
        """
        创建新报表

        Args:
            name: 报表名称
            report_type: 报表类型
            description: 报表描述
            conversation_id: 对话ID
            data_sources: 数据源列表
            layout: 布局配置
            charts: 图表列表
            filters: 过滤器
            theme: 主题
            share_scope: 分享范围
            auto_refresh: 是否自动刷新
            refresh_interval: 刷新间隔(秒)
            tags: 标签
            metadata: 元数据

        Returns:
            创建的报表对象

        Raises:
            SQLAlchemyError: 数据库错误
        """
        report = Report(
            name=name,
            report_type=report_type,
            description=description,
            conversation_id=conversation_id,
            data_sources=data_sources,
            layout=layout,
            charts=charts,
            filters=filters,
            theme=theme,
            share_scope=share_scope,
            auto_refresh=auto_refresh,
            refresh_interval=refresh_interval,
            tags=tags,
            metadata=metadata
        )

        try:
            self.db.add(report)
            self.db.commit()
            self.db.refresh(report)
            return report
        except SQLAlchemyError as e:
            self.db.rollback()
            raise

    def get_report(self, report_id: str) -> Optional[Report]:
        """
        获取报表

        Args:
            report_id: 报表ID

        Returns:
            报表对象或None
        """
        try:
            uuid_obj = UUID(report_id)
            return self.db.query(Report).filter(
                Report.id == uuid_obj
            ).first()
        except (ValueError, SQLAlchemyError):
            return None

    def list_reports(
        self,
        limit: int = 20,
        offset: int = 0,
        report_type: Optional[ReportType] = None,
        share_scope: Optional[ShareScope] = None,
        conversation_id: Optional[str] = None,
        tags: Optional[List[str]] = None,
        order_by: str = "created_at"
    ) -> List[Report]:
        """
        获取报表列表

        Args:
            limit: 限制数量
            offset: 偏移量
            report_type: 报表类型过滤
            share_scope: 分享范围过滤
            conversation_id: 对话ID过滤
            tags: 标签过滤
            order_by: 排序字段

        Returns:
            报表列表
        """
        query = self.db.query(Report)

        # 应用过滤条件
        if report_type:
            query = query.filter(Report.report_type == report_type)

        if share_scope:
            query = query.filter(Report.share_scope == share_scope)

        if conversation_id:
            try:
                uuid_obj = UUID(conversation_id)
                query = query.filter(Report.conversation_id == uuid_obj)
            except ValueError:
                pass

        if tags:
            # 过滤包含指定标签的报表
            for tag in tags:
                query = query.filter(Report.tags.contains([tag]))

        # 排序
        if order_by == "created_at":
            query = query.order_by(desc(Report.created_at))
        elif order_by == "updated_at":
            query = query.order_by(desc(Report.updated_at))
        elif order_by == "name":
            query = query.order_by(asc(Report.name))
        elif order_by == "view_count":
            query = query.order_by(desc(Report.view_count))

        return query.offset(offset).limit(limit).all()

    def update_report(
        self,
        report_id: str,
        **kwargs
    ) -> Optional[Report]:
        """
        更新报表

        Args:
            report_id: 报表ID
            **kwargs: 要更新的字段

        Returns:
            更新后的报表对象
        """
        report = self.get_report(report_id)
        if not report:
            return None

        try:
            for key, value in kwargs.items():
                if hasattr(report, key):
                    setattr(report, key, value)

            self.db.commit()
            self.db.refresh(report)
            return report
        except SQLAlchemyError as e:
            self.db.rollback()
            raise

    def delete_report(self, report_id: str) -> bool:
        """
        删除报表(级联删除图表)

        Args:
            report_id: 报表ID

        Returns:
            是否删除成功
        """
        report = self.get_report(report_id)
        if not report:
            return False

        try:
            self.db.delete(report)
            self.db.commit()
            return True
        except SQLAlchemyError as e:
            self.db.rollback()
            raise

    def increment_view_count(self, report_id: str) -> Optional[Report]:
        """
        增加报表浏览次数

        Args:
            report_id: 报表ID

        Returns:
            更新后的报表对象
        """
        report = self.get_report(report_id)
        if not report:
            return None

        try:
            report.increment_view_count()
            self.db.commit()
            self.db.refresh(report)
            return report
        except SQLAlchemyError as e:
            self.db.rollback()
            raise

    def create_chart(
        self,
        name: str,
        chart_type: ChartType,
        report_id: Optional[str] = None,
        description: Optional[str] = None,
        query: Optional[str] = None,
        data_config: Optional[Dict[str, Any]] = None,
        chart_config: Optional[Dict[str, Any]] = None,
        interactions: Optional[Dict[str, Any]] = None,
        width: Optional[int] = None,
        height: Optional[int] = None,
        style: Optional[Dict[str, Any]] = None
    ) -> Chart:
        """
        创建图表

        Args:
            name: 图表名称
            chart_type: 图表类型
            report_id: 报表ID(可选)
            description: 图表描述
            query: 数据查询SQL
            data_config: 数据配置
            chart_config: 图表配置
            interactions: 交互配置
            width: 宽度
            height: 高度
            style: 样式

        Returns:
            创建的图表对象

        Raises:
            SQLAlchemyError: 数据库错误
        """
        chart = Chart(
            name=name,
            chart_type=chart_type,
            report_id=report_id,
            description=description,
            query=query,
            data_config=data_config,
            chart_config=chart_config,
            interactions=interactions,
            width=width,
            height=height,
            style=style
        )

        try:
            self.db.add(chart)
            self.db.commit()
            self.db.refresh(chart)
            return chart
        except SQLAlchemyError as e:
            self.db.rollback()
            raise

    def get_chart(self, chart_id: str) -> Optional[Chart]:
        """
        获取图表

        Args:
            chart_id: 图表ID

        Returns:
            图表对象或None
        """
        try:
            uuid_obj = UUID(chart_id)
            return self.db.query(Chart).filter(
                Chart.id == uuid_obj
            ).first()
        except (ValueError, SQLAlchemyError):
            return None

    def list_charts(
        self,
        report_id: Optional[str] = None,
        chart_type: Optional[ChartType] = None,
        limit: int = 50,
        offset: int = 0
    ) -> List[Chart]:
        """
        获取图表列表

        Args:
            report_id: 报表ID过滤
            chart_type: 图表类型过滤
            limit: 限制数量
            offset: 偏移量

        Returns:
            图表列表
        """
        query = self.db.query(Chart)

        if report_id:
            try:
                uuid_obj = UUID(report_id)
                query = query.filter(Chart.report_id == uuid_obj)
            except ValueError:
                pass

        if chart_type:
            query = query.filter(Chart.chart_type == chart_type)

        return query.order_by(desc(Chart.created_at)).offset(offset).limit(limit).all()

    def update_chart(
        self,
        chart_id: str,
        **kwargs
    ) -> Optional[Chart]:
        """
        更新图表

        Args:
            chart_id: 图表ID
            **kwargs: 要更新的字段

        Returns:
            更新后的图表对象
        """
        chart = self.get_chart(chart_id)
        if not chart:
            return None

        try:
            for key, value in kwargs.items():
                if hasattr(chart, key):
                    setattr(chart, key, value)

            self.db.commit()
            self.db.refresh(chart)
            return chart
        except SQLAlchemyError as e:
            self.db.rollback()
            raise

    def delete_chart(self, chart_id: str) -> bool:
        """
        删除图表

        Args:
            chart_id: 图表ID

        Returns:
            是否删除成功
        """
        chart = self.get_chart(chart_id)
        if not chart:
            return False

        try:
            self.db.delete(chart)
            self.db.commit()
            return True
        except SQLAlchemyError as e:
            self.db.rollback()
            raise

    def get_charts_by_report(self, report_id: str) -> List[Chart]:
        """
        获取报表的所有图表

        Args:
            report_id: 报表ID

        Returns:
            图表列表
        """
        return self.list_charts(report_id=report_id, limit=10000)

    def update_chart_cache(
        self,
        chart_id: str,
        data: Dict[str, Any],
        ttl: int = 300
    ) -> Optional[Chart]:
        """
        更新图表缓存

        Args:
            chart_id: 图表ID
            data: 缓存数据
            ttl: 缓存过期时间(秒)

        Returns:
            更新后的图表对象
        """
        chart = self.get_chart(chart_id)
        if not chart:
            return None

        try:
            chart.update_cache(data, ttl)
            self.db.commit()
            self.db.refresh(chart)
            return chart
        except SQLAlchemyError as e:
            self.db.rollback()
            raise

    def get_reports_by_conversation(
        self,
        conversation_id: str,
        status: Optional[str] = None
    ) -> List[Report]:
        """
        获取对话相关的报表

        Args:
            conversation_id: 对话ID
            status: 状态过滤

        Returns:
            报表列表
        """
        try:
            uuid_obj = UUID(conversation_id)
            query = self.db.query(Report).filter(
                Report.conversation_id == uuid_obj
            )

            return query.order_by(desc(Report.created_at)).all()
        except (ValueError, SQLAlchemyError):
            return []

    def get_report_stats(
        self,
        conversation_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        获取报表统计信息

        Args:
            conversation_id: 对话ID(可选)

        Returns:
            统计信息字典
        """
        query = self.db.query(Report)

        if conversation_id:
            try:
                uuid_obj = UUID(conversation_id)
                query = query.filter(Report.conversation_id == uuid_obj)
            except ValueError:
                pass

        total_reports = query.count()
        dashboard_reports = query.filter(Report.report_type == ReportType.DASHBOARD).count()
        single_chart_reports = query.filter(Report.report_type == ReportType.SINGLE_CHART).count()
        table_reports = query.filter(Report.report_type == ReportType.TABLE).count()

        # 统计浏览次数
        total_views = self.db.query(Report).with_entities(
            Report.view_count
        ).all()
        total_view_count = sum([v[0] for v in total_views])

        return {
            "total_reports": total_reports,
            "dashboard_reports": dashboard_reports,
            "single_chart_reports": single_chart_reports,
            "table_reports": table_reports,
            "total_views": total_view_count,
            "avg_views_per_report": (total_view_count / total_reports) if total_reports > 0 else 0
        }

    def get_popular_reports(self, limit: int = 10) -> List[Report]:
        """
        获取热门报表(按浏览次数排序)

        Args:
            limit: 限制数量

        Returns:
            报表列表
        """
        return self.db.query(Report).order_by(
            desc(Report.view_count)
        ).limit(limit).all()

    def get_recent_reports(self, limit: int = 10) -> List[Report]:
        """
        获取最近创建的报表

        Args:
            limit: 限制数量

        Returns:
            报表列表
        """
        return self.db.query(Report).order_by(
            desc(Report.created_at)
        ).limit(limit).all()

    def search_reports(
        self,
        keyword: str,
        limit: int = 20
    ) -> List[Report]:
        """
        搜索报表(按名称和描述)

        Args:
            keyword: 搜索关键词
            limit: 限制数量

        Returns:
            报表列表
        """
        # 使用PostgreSQL的ILIKE进行不区分大小写的搜索
        return self.db.query(Report).filter(
            and_(
                Report.name.ilike(f"%{keyword}%"),
                Report.description.ilike(f"%{keyword}%")
            )
        ).order_by(desc(Report.created_at)).limit(limit).all()


# ─────────────────────────────────────────────────────────────────────────────
# Token 鉴权操作（供 MCP Tool Server 调用，无需 JWT）
# ─────────────────────────────────────────────────────────────────────────────

import json
import logging
import os
import re
import secrets as _secrets
from pathlib import Path as _Path
from uuid import UUID as _UUID

_svc_logger = logging.getLogger(__name__)


def _api_base_url() -> str:
    """推断后端 API 前缀（与 reports.py 保持一致）。

    无 PUBLIC_HOST 时返回空字符串，让前端 HTML 通过 window.location.origin 自动推断，
    避免 iframe 跨域问题（Vite dev proxy 下 iframe 同源为 localhost:3000）。
    """
    host = os.environ.get("PUBLIC_HOST", "")
    if host:
        return f"{host}/api/v1"
    return ""


def _get_customer_data_root() -> _Path:
    from backend.config.settings import settings
    return (
        _Path(settings.allowed_directories[0])
        if settings.allowed_directories
        else _Path("customer_data")
    )


def create_report_with_spec(spec: Dict[str, Any], username: str) -> Dict[str, Any]:
    """
    根据 spec 创建新报表：在 DB 注册记录 + 生成动态 HTML 文件。

    HTML 采用"动态加载模式"（build_report_html report_id != 'preview'），
    打开时自动调用 GET /reports/{id}/data 实时查询 ClickHouse，无嵌入静态数据。

    Args:
        spec:     完整报表 spec（title/subtitle/theme/charts[]/filters[]）
        username: 当前用户名（CURRENT_USER）

    Returns:
        {report_id, refresh_token, name, html_path, message}

    Raises:
        RuntimeError: HTML 生成或文件写入失败
    """
    from backend.config.database import get_db_context
    from backend.models.report import Report as _Report, ReportType as _ReportType
    from backend.services.report_builder_service import build_report_html, generate_refresh_token

    if not isinstance(spec, dict):
        raise ValueError("spec 必须是 JSON 对象")

    title = (spec.get("title") or "").strip() or "分析报告"
    doc_type = spec.get("doc_type", "dashboard")

    # ── 生成唯一文件名 ──────────────────────────────────────────────────────────
    import time as _time
    _ts = _time.strftime("%Y%m%d%H%M%S")
    _safe_title = re.sub(r"[^\w\u4e00-\u9fff-]", "_", title)[:40]
    file_name = f"{_safe_title}_{_ts}.html"
    relative_path = f"{username}/reports/{file_name}"

    with get_db_context() as db:
        token = generate_refresh_token()

        report = _Report(
            name=title,
            description=spec.get("subtitle", ""),
            report_type=_ReportType.DASHBOARD,
            doc_type=doc_type,
            username=username,
            refresh_token=token,
            report_file_path=relative_path,
            charts=spec.get("charts", []),
            filters=spec.get("filters", []),
            theme=spec.get("theme", "light"),
            data_sources=spec.get("data_sources", []),
        )
        db.add(report)
        db.flush()  # 获取自动生成的 UUID
        report_id = str(report.id)

        # ── 生成动态 HTML（report_id != "preview" → 不嵌入数据行） ──────────────
        try:
            html_content = build_report_html(
                spec=spec,
                report_id=report_id,
                refresh_token=token,
                api_base_url=_api_base_url(),
            )
        except Exception as e:
            raise RuntimeError(f"HTML 生成失败: {e}") from e

        customer_root = _get_customer_data_root()
        html_path = customer_root / relative_path
        html_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            html_path.write_text(html_content, encoding="utf-8")
        except Exception as e:
            raise RuntimeError(f"HTML 文件写入失败: {e}") from e

        db.commit()

    _svc_logger.info(
        "[create_report_with_spec] 报表已创建: id=%s name=%s user=%s path=%s",
        report_id, title, username, relative_path,
    )
    return {
        "report_id": report_id,
        "refresh_token": token,
        "name": title,
        "html_path": relative_path,
        "message": (
            f"报表《{title}》已创建（{len(spec.get('charts', []))} 个图表），"
            f"打开时自动实时查询数据库，无需嵌入静态数据。"
        ),
    }


def _extract_spec_via_report_spec_marker(content: str) -> "Dict[str, Any] | None":
    """
    策略 1：从 ``const REPORT_SPEC = {...}`` 标记提取（模板生成的 HTML）。
    内部函数，供 extract_spec_from_html_file 调用链使用。
    """
    try:
        m = re.search(r"const\s+REPORT_SPEC\s*=\s*", content)
        if not m:
            return None
        start = content.index("{", m.end())
        spec, _ = json.JSONDecoder().raw_decode(content, start)
        if not isinstance(spec, dict):
            return None
        if "charts" not in spec:
            spec["charts"] = []
        return spec
    except Exception:
        return None


def extract_spec_from_echarts_html(content_or_path: "str | _Path") -> "Dict[str, Any] | None":
    """
    策略 2：从自由书写的 ECharts HTML 中反向推断报表结构（无需 REPORT_SPEC 标记）。

    适用场景：LLM 直接生成 ECharts 代码，未使用 build_report_html() 模板，
    因此 HTML 中没有 ``const REPORT_SPEC`` 标记。

    提取逻辑：
    1. 报表标题：``<title>`` 标签 → 或 ``<h1>`` 第一个
    2. 图表 ID 列表：所有 ``echarts.init(document.getElementById('xxx'))`` 调用
    3. 图表标题：HTML 中 ``.chart-title`` / ``.chart-name`` / ``<h2>`` 按 DOM 顺序对应
    4. 图表类型：各 ``setOption`` 块内的 ``type: 'bar'/'line'/'pie'/...``
       （取最后一个 type 值，因为 series[0].type 在最内层）
    5. 配对策略：DOM 顺序 chart-title[i] ↔ echarts.init[i]（顺序通常一致）

    Returns:
        spec dict（含 title, charts list, filters=[], theme="light"），
        或 None（无法识别任何图表）
    """
    try:
        if isinstance(content_or_path, _Path) or (
            isinstance(content_or_path, str) and "\n" not in content_or_path and len(content_or_path) < 1000
        ):
            p = _Path(content_or_path)
            if p.exists():
                content = p.read_text(encoding="utf-8", errors="replace")
            else:
                content = content_or_path  # 当作字符串内容
        else:
            content = content_or_path

        # ── 1. 报表标题 ──────────────────────────────────────────────────────
        title = ""
        m_title = re.search(r"<title[^>]*>(.*?)</title>", content, re.IGNORECASE | re.DOTALL)
        if m_title:
            title = re.sub(r"<[^>]+>", "", m_title.group(1)).strip()
        if not title:
            m_h1 = re.search(r"<h1[^>]*>(.*?)</h1>", content, re.IGNORECASE | re.DOTALL)
            if m_h1:
                title = re.sub(r"<[^>]+>", "", m_h1.group(1)).strip()

        # ── 2. 图表 ID（按出现顺序）──────────────────────────────────────────
        chart_ids = re.findall(
            r"""echarts\.init\s*\(\s*document\.getElementById\s*\(\s*['"]([^'"]+)['"]\s*\)""",
            content,
        )
        if not chart_ids:
            # 备用：getElementById 单独出现（非 echarts.init 包裹）
            chart_ids = re.findall(
                r"""getElementById\s*\(\s*['"]([^'"]+)['"]\s*\)""", content
            )
            # 只保留看起来像图表容器的（含 chart）
            chart_ids = [c for c in chart_ids if "chart" in c.lower()]
        if not chart_ids:
            return None

        # 去重同时保序
        seen: "set[str]" = set()
        unique_ids = []
        for cid in chart_ids:
            if cid not in seen:
                seen.add(cid)
                unique_ids.append(cid)
        chart_ids = unique_ids

        # ── 3. 图表标题（按 DOM 出现顺序）──────────────────────────────────
        chart_title_pattern = re.compile(
            r'class=["\'][^"\']*(?:chart[-_]title|chart[-_]name|panel[-_]title)[^"\']*["\'][^>]*>(.*?)</\w+>',
            re.IGNORECASE | re.DOTALL,
        )
        chart_display_names = [
            re.sub(r"<[^>]+>", "", m.group(1)).strip()
            for m in chart_title_pattern.finditer(content)
        ]

        # ── 4. 图表类型（从各 echarts.init 块提取）──────────────────────────
        # 优先在 echarts.init[i] → echarts.init[i+1] 之间找类型
        # 这样能覆盖"先定义 var series，再 setOption()"的写法
        _chart_type_keywords = {"bar", "line", "pie", "scatter", "radar",
                                 "gauge", "heatmap", "funnel", "treemap", "sankey"}
        init_matches = list(re.finditer(
            r"""echarts\.init\s*\(\s*document\.getElementById\s*\(\s*['"][^'"]+['"]\s*\)""",
            content,
        ))
        chart_types: "list[str]" = []
        for i, m_init in enumerate(init_matches):
            # 本段：从当前 echarts.init 到下一个 echarts.init（或文件末尾）
            seg_end = init_matches[i + 1].start() if i + 1 < len(init_matches) else len(content)
            segment = content[m_init.start():seg_end]
            types_in_seg = re.findall(r"""type\s*:\s*['"]([^'"]+)['"]""", segment)
            if types_in_seg:
                # 优先取 chart_type_keywords 中的值；再次优先取 series 上下文中的
                chart_kw = [t for t in types_in_seg if t.lower() in _chart_type_keywords]
                chart_types.append(chart_kw[-1] if chart_kw else "bar")
            else:
                chart_types.append("bar")  # 默认

        # ── 5. 组装 charts 列表 ─────────────────────────────────────────────
        charts = []
        for i, cid in enumerate(chart_ids):
            display_name = (
                chart_display_names[i]
                if i < len(chart_display_names)
                else f"图表 {i + 1}"
            )
            ctype = chart_types[i] if i < len(chart_types) else "bar"
            charts.append({
                "id": cid,
                "chart_type": ctype,
                "title": display_name,
                # sql / connection_env 对自由 HTML 不可知，留空
                "sql": "",
                "connection_env": "",
                "_extracted": True,   # 标记为反向推断，非原始 spec
            })

        if not charts:
            return None

        _svc_logger.info(
            "[extract_spec_from_echarts_html] 成功提取 %d 个图表，标题=%r",
            len(charts), title,
        )
        return {
            "title": title,
            "subtitle": "",
            "charts": charts,
            "filters": [],
            "theme": "light",
            "_source": "echarts_html",   # 标记来源，供调用方判断
        }

    except Exception as e:
        _svc_logger.debug("[extract_spec_from_echarts_html] 提取失败: %s", e)
        return None


def extract_html_context(content_or_path: "str | _Path", max_chars: int = 1500) -> str:
    """
    从 HTML 文件提取可读文本摘要（终极兜底），供 Pilot system_prompt 注入。

    提取：标题 + h1/h2/h3 + 段落文本，剔除 script/style 标签，截断至 max_chars。
    """
    try:
        if isinstance(content_or_path, _Path) or (
            isinstance(content_or_path, str) and "\n" not in content_or_path and len(content_or_path) < 1000
        ):
            p = _Path(content_or_path)
            if p.exists():
                content = p.read_text(encoding="utf-8", errors="replace")
            else:
                content = content_or_path
        else:
            content = content_or_path

        # 去掉 script / style 块
        content = re.sub(r"<script[^>]*>.*?</script>", "", content, flags=re.DOTALL | re.IGNORECASE)
        content = re.sub(r"<style[^>]*>.*?</style>", "", content, flags=re.DOTALL | re.IGNORECASE)
        # 去掉剩余 HTML 标签
        text = re.sub(r"<[^>]+>", " ", content)
        # 压缩空白
        text = re.sub(r"\s+", " ", text).strip()
        return text[:max_chars]
    except Exception:
        return ""


def extract_spec_from_html_file(html_path: "_Path | str") -> "Dict[str, Any] | None":
    """
    从 HTML 报告文件中提取报表 spec（两段式提取链）。

    提取链：
      Step 1 — ``const REPORT_SPEC`` 标记（模板生成 HTML，精确）
      Step 2 — ECharts DOM 模式（LLM 自由书写 HTML，反向推断）
      Step 3 — 返回 None（两策略均失败）

    所有调用点（pin_report / spec-meta / get_spec_by_token）无需修改，
    自动获得增强能力。

    Args:
        html_path: HTML 文件的绝对或相对路径

    Returns:
        spec dict（至少含 "charts" key），或 None
    """
    try:
        html_path = _Path(html_path)
        if not html_path.exists():
            return None
        content = html_path.read_text(encoding="utf-8", errors="replace")

        # Step 1: REPORT_SPEC 标记（精确，优先）
        spec = _extract_spec_via_report_spec_marker(content)
        if spec is not None:
            return spec

        # Step 2: ECharts DOM 反向推断（自由书写 HTML）
        spec = extract_spec_from_echarts_html(content)
        if spec is not None:
            return spec

        return None

    except Exception as e:
        _svc_logger.debug("[extract_spec_from_html_file] 提取失败 path=%s err=%s", html_path, e)
        return None


def _verify_refresh_token(report: Report, refresh_token: str) -> None:
    """验证 refresh_token，不匹配时抛出 PermissionError。"""
    if not _secrets.compare_digest(report.refresh_token or "", refresh_token or ""):
        raise PermissionError("无效的访问令牌")


def get_spec_by_token(report_id: str, refresh_token: str) -> Dict[str, Any]:
    """
    通过 refresh_token 获取报表 spec（无需 JWT）。

    Returns:
        报表 spec 字典（含 id, name, charts, filters, theme,
        doc_type, data_sources, refresh_token, report_file_path, username）

    Raises:
        ValueError:      report_id 格式错误或报表不存在
        PermissionError: refresh_token 不匹配
    """
    from backend.config.database import get_db_context

    try:
        uid = _UUID(report_id)
    except (ValueError, AttributeError):
        raise ValueError(f"无效的报表 ID: {report_id!r}")

    with get_db_context() as db:
        report = db.query(Report).filter(Report.id == uid).first()
        if not report:
            raise ValueError(f"报表不存在: {report_id}")
        _verify_refresh_token(report, refresh_token)

        charts = list(report.charts or [])
        filters = list(report.filters or [])
        theme = report.theme or "light"

        # 若 DB 中 charts 为 NULL（历史 pin 记录），尝试从 HTML 文件提取并持久化
        if report.charts is None and report.report_file_path:
            try:
                customer_root = _get_customer_data_root()
                _abs = (customer_root / report.report_file_path).resolve()
                _spec = extract_spec_from_html_file(_abs)
                if _spec:
                    charts = _spec.get("charts", [])
                    filters = _spec.get("filters", [])
                    theme = _spec.get("theme", "light")
                    # 懒写回 DB
                    try:
                        from sqlalchemy.orm.attributes import flag_modified
                        report.charts = charts
                        report.filters = filters
                        report.theme = theme
                        flag_modified(report, "charts")
                        flag_modified(report, "filters")
                        db.commit()
                        _svc_logger.info(
                            "[get_spec_by_token] 懒更新 spec: report_id=%s charts=%d",
                            report_id, len(charts),
                        )
                    except Exception as _ce:
                        db.rollback()
                        _svc_logger.warning("[get_spec_by_token] 懒更新 DB 失败（非致命）: %s", _ce)
            except Exception as _e:
                _svc_logger.debug("[get_spec_by_token] 从 HTML 提取 spec 失败: %s", _e)

        return {
            "id": str(report.id),
            "name": report.name,
            "description": report.description or "",
            "doc_type": report.doc_type or "dashboard",
            "theme": theme,
            "charts": charts,
            "filters": filters,
            "data_sources": list(report.data_sources or []),
            "refresh_token": report.refresh_token,
            "report_file_path": report.report_file_path,
            "username": report.username,
        }


def update_spec_by_token(
    report_id: str,
    spec: Dict[str, Any],
    refresh_token: str,
) -> Dict[str, Any]:
    """
    通过 refresh_token 更新报表全量 spec 并重新生成 HTML（无需 JWT）。

    Args:
        report_id:     报表 UUID 字符串
        spec:          完整报表 spec（含 charts / filters / theme / title 等）
        refresh_token: 报表访问令牌

    Returns:
        {"report_id": str, "name": str, "updated_at": str}

    Raises:
        ValueError:      report_id 无效或报表不存在或缺少 HTML 路径
        PermissionError: refresh_token 不匹配
        RuntimeError:    HTML 生成失败
    """
    from backend.config.database import get_db_context
    from backend.services.report_builder_service import build_report_html

    try:
        uid = _UUID(report_id)
    except (ValueError, AttributeError):
        raise ValueError(f"无效的报表 ID: {report_id!r}")

    with get_db_context() as db:
        report = db.query(Report).filter(Report.id == uid).first()
        if not report:
            raise ValueError(f"报表不存在: {report_id}")
        _verify_refresh_token(report, refresh_token)

        if not report.report_file_path:
            raise ValueError("报表尚无 HTML 文件路径，无法覆写")

        try:
            html_content = build_report_html(
                spec=spec,
                report_id=str(report.id),
                refresh_token=report.refresh_token,
                api_base_url=_api_base_url(),
            )
        except Exception as e:
            raise RuntimeError(f"HTML 生成失败: {e}") from e

        customer_root = _get_customer_data_root()
        html_path = customer_root / report.report_file_path
        html_path.parent.mkdir(parents=True, exist_ok=True)
        html_path.write_text(html_content, encoding="utf-8")

        report.charts = spec.get("charts", report.charts)
        report.data_sources = spec.get("data_sources", report.data_sources)
        report.filters = spec.get("filters", report.filters)
        report.theme = spec.get("theme", report.theme)
        if spec.get("title"):
            report.name = spec["title"]
        report.updated_at = datetime.utcnow()

        return {
            "report_id": str(report.id),
            "name": report.name,
            "updated_at": report.updated_at.isoformat(),
        }


def update_single_chart_by_token(
    report_id: str,
    chart_id: str,
    chart_patch: Dict[str, Any],
    refresh_token: str,
) -> Dict[str, Any]:
    """
    通过 refresh_token 对单个图表做 merge 更新，不影响其他图表（无需 JWT）。

    Args:
        report_id:    报表 UUID 字符串
        chart_id:     要更新的图表 ID（如 "c1"）
        chart_patch:  只含需要变更字段的图表配置字典
        refresh_token: 报表访问令牌

    Returns:
        {"report_id": str, "chart_id": str, "found": bool,
         "total_charts": int, "updated_at": str}
    """
    from backend.config.database import get_db_context
    from backend.services.report_builder_service import build_report_html

    try:
        uid = _UUID(report_id)
    except (ValueError, AttributeError):
        raise ValueError(f"无效的报表 ID: {report_id!r}")

    with get_db_context() as db:
        report = db.query(Report).filter(Report.id == uid).first()
        if not report:
            raise ValueError(f"报表不存在: {report_id}")
        _verify_refresh_token(report, refresh_token)

        if not report.report_file_path:
            raise ValueError("报表尚无 HTML 文件路径，无法覆写")

        existing_charts: List[Dict[str, Any]] = list(report.charts or [])
        incoming = dict(chart_patch)
        incoming["id"] = chart_id  # chart_id 优先

        found = False
        merged_charts: List[Dict[str, Any]] = []
        for c in existing_charts:
            if c.get("id") == chart_id:
                merged_charts.append({**c, **incoming})
                found = True
            else:
                merged_charts.append(c)
        if not found:
            merged_charts.append(incoming)

        merged_spec: Dict[str, Any] = {
            "title": report.name,
            "subtitle": report.description or "",
            "theme": report.theme or "light",
            "filters": report.filters or [],
            "data_sources": report.data_sources or [],
            "charts": merged_charts,
            "include_summary": False,
            "data": {},
        }

        try:
            html_content = build_report_html(
                spec=merged_spec,
                report_id=str(report.id),
                refresh_token=report.refresh_token,
                api_base_url=_api_base_url(),
            )
        except Exception as e:
            raise RuntimeError(f"HTML 生成失败: {e}") from e

        customer_root = _get_customer_data_root()
        html_path = customer_root / report.report_file_path
        html_path.parent.mkdir(parents=True, exist_ok=True)
        html_path.write_text(html_content, encoding="utf-8")

        report.charts = merged_charts
        report.updated_at = datetime.utcnow()

        return {
            "report_id": str(report.id),
            "chart_id": chart_id,
            "found": found,
            "total_charts": len(merged_charts),
            "updated_at": report.updated_at.isoformat(),
        }
