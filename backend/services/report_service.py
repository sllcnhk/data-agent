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

import os
import secrets as _secrets
from pathlib import Path as _Path
from uuid import UUID as _UUID


def _api_base_url() -> str:
    """推断后端 API 前缀（与 reports.py 保持一致）。"""
    host = os.environ.get("PUBLIC_HOST", "")
    port = os.environ.get("PORT", "8000")
    if host:
        return f"{host}/api/v1"
    return f"http://localhost:{port}/api/v1"


def _get_customer_data_root() -> _Path:
    from backend.config.settings import settings
    return (
        _Path(settings.allowed_directories[0])
        if settings.allowed_directories
        else _Path("customer_data")
    )


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
        return {
            "id": str(report.id),
            "name": report.name,
            "description": report.description or "",
            "doc_type": report.doc_type or "dashboard",
            "theme": report.theme or "light",
            "charts": list(report.charts or []),
            "filters": list(report.filters or []),
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
