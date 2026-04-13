"""
报告 API — /reports/*

POST   /reports/build                  从 spec 生成 HTML 报告
GET    /reports/{id}/refresh-data      重新查询数据（HTML 内刷新按钮调用，Bearer或token认证）
GET    /reports                        报告列表（分页）
GET    /reports/{id}                   报告详情
DELETE /reports/{id}                   删除报告 + 本地 HTML 文件
POST   /reports/{id}/export            异步导出 PDF/PPTX
GET    /reports/{id}/export-status     查询导出任务状态
GET    /reports/{id}/summary-status    查询 LLM 总结生成状态
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

import secrets

from backend.api.deps import get_current_user, require_permission
from backend.core.auth.jwt import decode_token
from backend.config.database import get_db
from backend.config.settings import settings
from backend.models.report import Report
from backend.services.report_builder_service import (
    build_report_html,
    generate_llm_summary,
    generate_refresh_token,
)

router = APIRouter(prefix="/reports", tags=["报告"])
logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# 常量
# ─────────────────────────────────────────────────────────────────────────────
_CUSTOMER_DATA_ROOT: Path = (
    Path(settings.allowed_directories[0])
    if settings.allowed_directories
    else Path("customer_data")
)

# 内存中的导出任务状态（简单 KV，生产可换 Redis）
_export_jobs: Dict[str, Dict] = {}


# ─────────────────────────────────────────────────────────────────────────────
# Pydantic 模型
# ─────────────────────────────────────────────────────────────────────────────

class BuildReportRequest(BaseModel):
    spec: Dict[str, Any] = Field(..., description="报告规格 JSON（见 ReportBuilderService 文档）")
    conversation_id: Optional[str] = Field(None, description="来源对话 ID")
    include_summary: bool = Field(False, description="是否异步生成 LLM 总结")
    doc_type: str = Field("dashboard", description="报告类型: dashboard | document")


class ExportReportRequest(BaseModel):
    format: str = Field("pdf", description="导出格式: pdf | pptx")


class UpdateSpecRequest(BaseModel):
    spec: Dict[str, Any] = Field(..., description="完整报告规格 JSON")


class UpdateShareRequest(BaseModel):
    share_scope: str = Field("private", description="共享范围: public | team | private")
    allowed_users: List[str] = Field(default_factory=list, description="允许访问的用户名列表")


class CopilotRequest(BaseModel):
    title: Optional[str] = Field(None, description="对话标题（可选）")


# ─────────────────────────────────────────────────────────────────────────────
# 辅助
# ─────────────────────────────────────────────────────────────────────────────

def _get_report_or_404(report_id: str, db: Session) -> Report:
    try:
        uid = uuid.UUID(report_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="无效的报告 ID")
    report = db.query(Report).filter(Report.id == uid).first()
    if not report:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="报告不存在")
    return report


def _check_ownership(report: Report, username: str, is_superadmin: bool = False) -> None:
    if is_superadmin:
        return  # superadmin can access all reports
    if report.username and report.username != username:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="无权访问该报告")


def _report_dir(username: str) -> Path:
    d = _CUSTOMER_DATA_ROOT / username / "reports"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _api_base_url() -> str:
    """推断后端 API 前缀（供 HTML 内部刷新调用）。"""
    host = os.environ.get("PUBLIC_HOST", "")
    port = os.environ.get("PORT", "8000")
    if host:
        return f"{host}/api/v1"
    return f"http://localhost:{port}/api/v1"


# ─────────────────────────────────────────────────────────────────────────────
# 1. 构建报告 HTML
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/build")
async def build_report(
    req: BuildReportRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("reports", "create")),
):
    """
    根据 spec 生成 HTML 报告文件，存入 customer_data/{username}/reports/，
    并在数据库中创建 Report 记录。

    如果 include_summary=true，则异步触发 LLM 总结生成。
    """
    username = getattr(current_user, "username", "default")
    spec = req.spec

    # 生成 UUID 和刷新令牌
    report_id = str(uuid.uuid4())
    refresh_token = generate_refresh_token()

    # 注入 include_summary 到 spec（供 HTML 渲染总结区域）
    spec["include_summary"] = req.include_summary

    # 生成 HTML
    try:
        html_content = build_report_html(
            spec=spec,
            report_id=report_id,
            refresh_token=refresh_token,
            api_base_url=_api_base_url(),
        )
    except Exception as e:
        logger.error("[Reports] HTML 生成失败: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"HTML 生成失败: {e}")

    # 写入文件
    report_dir = _report_dir(username)
    title_slug = _slugify(spec.get("title", "report"))[:40]
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{title_slug}_{ts}.html"
    html_path = report_dir / filename
    html_path.write_text(html_content, encoding="utf-8")

    # 相对路径（用于下载 API）
    try:
        rel_path = str(html_path.relative_to(_CUSTOMER_DATA_ROOT))
    except ValueError:
        rel_path = str(html_path)

    # 写入数据库
    conv_id = None
    if req.conversation_id:
        try:
            conv_id = uuid.UUID(req.conversation_id)
        except ValueError:
            pass

    report = Report(
        id=uuid.UUID(report_id),
        conversation_id=conv_id,
        name=spec.get("title", "数据报告"),
        description=spec.get("subtitle", ""),
        username=username,
        refresh_token=refresh_token,
        report_file_path=rel_path,
        summary_status="pending" if req.include_summary else "skipped",
        # 将原始 spec 中的 charts + data_sources 存入已有字段
        charts=spec.get("charts", []),
        data_sources=_build_data_sources(spec),
        filters=spec.get("filters", []),
        theme=spec.get("theme", "light"),
        extra_metadata={"spec_version": "1.0", "file_name": filename},
    )
    try:
        report.doc_type = req.doc_type
    except AttributeError:
        pass  # doc_type 列尚未迁移，跳过
    db.add(report)
    db.commit()
    db.refresh(report)

    # 异步生成 LLM 总结
    if req.include_summary:
        background_tasks.add_task(_async_generate_summary, report_id, spec, db)

    logger.info("[Reports] 报告已生成: id=%s, file=%s", report_id, rel_path)
    return {
        "success": True,
        "data": {
            "report_id": report_id,
            "file_path": rel_path,
            "file_name": filename,
            "refresh_token": refresh_token,
            "summary_status": report.summary_status,
        },
    }


def _build_data_sources(spec: Dict) -> List[Dict]:
    """从 charts 提取 data_sources 列表（存入 Report.data_sources）。"""
    sources = []
    seen = set()
    for c in spec.get("charts", []):
        key = (c.get("connection_env", ""), c.get("connection_type", "clickhouse"))
        if key not in seen and c.get("sql"):
            seen.add(key)
            sources.append({
                "id": c.get("id"),
                "type": c.get("connection_type", "clickhouse"),
                "env": c.get("connection_env", ""),
                "query": c.get("sql", ""),
            })
    return sources


def _slugify(s: str) -> str:
    import re
    s = s.strip().lower()
    s = re.sub(r"[^\w\u4e00-\u9fff\-]", "_", s)
    s = re.sub(r"_+", "_", s)
    return s.strip("_") or "report"


async def _async_generate_summary(report_id: str, spec: Dict, db: Session) -> None:
    """后台任务：调用 LLM 生成总结并更新 DB + 本地 HTML 文件。"""
    try:
        from backend.agents.factory import get_default_llm_adapter
        llm_adapter = get_default_llm_adapter()

        # 更新状态为 generating
        rpt = db.query(Report).filter(Report.id == uuid.UUID(report_id)).first()
        if rpt:
            rpt.summary_status = "generating"
            db.commit()

        summary = await generate_llm_summary(spec, llm_adapter)

        if rpt:
            rpt.llm_summary = summary
            rpt.summary_status = "done"
            db.commit()

        # 同步更新 HTML 文件中的总结（简单替换占位符）
        if rpt and rpt.report_file_path and summary:
            html_path = _CUSTOMER_DATA_ROOT / rpt.report_file_path
            if html_path.exists():
                html = html_path.read_text(encoding="utf-8")
                html = html.replace(
                    "分析总结生成中，请稍候…",
                    summary.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"),
                    1,
                )
                html_path.write_text(html, encoding="utf-8")
        logger.info("[Reports] LLM 总结生成完成: report_id=%s", report_id)
    except Exception as e:
        logger.error("[Reports] LLM 总结生成失败: %s", e, exc_info=True)
        try:
            rpt = db.query(Report).filter(Report.id == uuid.UUID(report_id)).first()
            if rpt:
                rpt.summary_status = "failed"
                db.commit()
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# 2. 数据刷新（HTML 内 JS 调用）
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/{report_id}/refresh-data")
async def refresh_report_data(
    report_id: str,
    token: str = Query(..., description="Report.refresh_token"),
    db: Session = Depends(get_db),
):
    """
    重新执行报告中每个图表的 SQL 查询，返回最新数据。

    认证方式：refresh_token（无需登录，适合已生成的 HTML 文件内调用）。
    """
    try:
        uid = uuid.UUID(report_id)
    except ValueError:
        raise HTTPException(400, "无效的报告 ID")

    report = db.query(Report).filter(Report.id == uid).first()
    if not report:
        raise HTTPException(404, "报告不存在")
    if not secrets.compare_digest(report.refresh_token or "", token):
        raise HTTPException(403, "无效的刷新令牌")

    # 重新执行每个图表的 SQL
    new_data: Dict[str, Any] = {}
    errors: Dict[str, str] = {}
    charts = report.charts or []
    data_sources = {ds["id"]: ds for ds in (report.data_sources or [])}

    for chart in charts:
        cid = chart.get("id")
        sql = chart.get("sql", "")
        env = chart.get("connection_env", "")
        conn_type = chart.get("connection_type", "clickhouse")
        if not sql or not env:
            continue
        try:
            rows = await _run_query(sql, env, conn_type)
            new_data[cid] = rows
        except Exception as e:
            errors[cid] = str(e)
            logger.warning("[Reports] 刷新查询失败 chart=%s: %s", cid, e)

    # 更新 view_count
    report.increment_view_count()
    db.commit()

    return {
        "success": True,
        "data": new_data,
        "errors": errors,
        "llm_summary": report.llm_summary,
        "refreshed_at": datetime.utcnow().isoformat(),
    }


async def _run_query(sql: str, env: str, conn_type: str = "clickhouse") -> List[Dict]:
    """执行查询并返回行列表（dict 格式）。"""
    if conn_type == "clickhouse":
        from backend.mcp.clickhouse.server import _get_or_init_client
        client = await _get_or_init_client(env)
        if hasattr(client, "execute"):
            rows, cols = client.execute(sql, with_column_types=True)
            col_names = [c[0] for c in cols]
            return [dict(zip(col_names, row)) for row in rows]
        else:
            # HTTP client
            result = client.execute(sql)
            return result if isinstance(result, list) else []
    elif conn_type == "mysql":
        from backend.mcp.mysql.server import get_mysql_client
        conn = get_mysql_client(env)
        cursor = conn.cursor(dictionary=True)
        cursor.execute(sql)
        return cursor.fetchall()
    return []


# ─────────────────────────────────────────────────────────────────────────────
# 3. 报告列表 & 详情 & 删除
# ─────────────────────────────────────────────────────────────────────────────

@router.get("")
async def list_reports(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    doc_type: Optional[str] = Query(None, description="过滤类型: dashboard | document"),
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("reports", "read")),
):
    username = getattr(current_user, "username", "default")
    is_superadmin = getattr(current_user, "is_superadmin", False)

    q = db.query(Report)
    if not is_superadmin:
        q = q.filter(Report.username == username)
    if doc_type is not None:
        try:
            q = q.filter(Report.doc_type == doc_type)
        except AttributeError:
            pass  # doc_type 列尚未迁移，跳过过滤
    total = q.count()
    reports = q.order_by(Report.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()

    return {
        "success": True,
        "data": {
            "total": total,
            "page": page,
            "page_size": page_size,
            "items": [_report_to_dict(r) for r in reports],
        },
    }


@router.get("/html-serve")
async def serve_report_html_by_path(
    path: str = Query(..., description="文件路径（含或不含 customer_data/ 前缀均可）"),
    token: str = Query("", description="JWT access token（iframe 场景下无法发 Authorization header，以此替代）"),
    db: Session = Depends(get_db),
):
    """
    为 iframe 预览提供的报告 HTML 服务端点（JWT 以 query param 方式鉴权）。

    浏览器加载 iframe src 时不发送自定义 Authorization header，因此以 query param
    传递 JWT 进行验证。路径必须属于当前用户的 customer_data/{username}/ 目录。

    ENABLE_AUTH=false 时跳过 token 验证（单用户模式）。
    """
    if settings.enable_auth:
        if not token:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="未登录，请先登录")
        payload = decode_token(token, settings.jwt_secret, settings.jwt_algorithm)
        if not payload:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token 无效或已过期")

        from backend.models.user import User
        user_id = payload.get("sub")
        user = db.query(User).filter(User.id == user_id, User.is_active == True).first()  # noqa: E712
        if not user:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户不存在")

        username = user.username
    else:
        username = "default"

    # 解析路径（兼容含/不含 customer_data/ 前缀两种格式）
    normalized = path.replace("\\", "/")
    customer_data_name = _CUSTOMER_DATA_ROOT.name
    if normalized.startswith(customer_data_name + "/"):
        rel = normalized[len(customer_data_name) + 1:]
    else:
        rel = normalized

    abs_path = (_CUSTOMER_DATA_ROOT / rel).resolve()

    # 所有权验证：文件必须在 customer_data/{username}/ 下
    if settings.enable_auth:
        user_root = (_CUSTOMER_DATA_ROOT / username).resolve()
        try:
            abs_path.relative_to(user_root)
        except ValueError:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="无权访问此文件")

    if not abs_path.exists() or not abs_path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="文件不存在")

    try:
        content = abs_path.read_text(encoding="utf-8")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"文件读取失败: {e}")

    return HTMLResponse(content=content)


@router.get("/{report_id}/html")
async def serve_report_html_by_token(
    report_id: str,
    token: str = Query(..., description="Report.refresh_token（无需登录）"),
    download: bool = Query(False, description="True 时以附件方式下载而非内嵌预览"),
    db: Session = Depends(get_db),
):
    """
    通过 refresh_token 访问报告 HTML（无需 JWT）。

    适用场景：
    - 报告列表页「预览」按钮（refresh_token 来自 GET /reports 响应）
    - 报告列表页「下载 HTML」按钮（download=true）
    - 将报告嵌入邮件/文档，允许无账号人员访问
    """
    try:
        uid = uuid.UUID(report_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="无效的报告 ID")

    report = db.query(Report).filter(Report.id == uid).first()
    if not report:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="报告不存在")

    # Public reports: skip token check
    try:
        _share_scope_val = report.share_scope
        if hasattr(_share_scope_val, "value"):
            _share_scope_val = _share_scope_val.value
        if _share_scope_val and _share_scope_val == "public":
            pass  # Public access — no token needed, fall through to file serving
        elif not secrets.compare_digest(report.refresh_token or "", token):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="无效的刷新令牌")
    except AttributeError:
        if not secrets.compare_digest(report.refresh_token or "", token):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="无效的刷新令牌")

    if not report.report_file_path:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="报告文件路径未记录")

    file_path = (_CUSTOMER_DATA_ROOT / report.report_file_path).resolve()
    if not file_path.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="报告文件不存在（可能已被删除）")

    try:
        content = file_path.read_text(encoding="utf-8")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"文件读取失败: {e}")

    headers = {}
    if download:
        filename = file_path.name
        import urllib.parse
        encoded = urllib.parse.quote(filename)
        headers["Content-Disposition"] = f"attachment; filename*=UTF-8''{encoded}"

    return HTMLResponse(content=content, headers=headers)


@router.put("/{report_id}/spec")
async def update_report_spec(
    report_id: str,
    req: UpdateSpecRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("reports", "create")),
):
    """
    用新的 spec 重新生成报告 HTML，并更新数据库中的 charts/filters/theme 等字段。
    """
    username = getattr(current_user, "username", "default")
    report = _get_report_or_404(report_id, db)
    _check_ownership(report, username, getattr(current_user, "is_superadmin", False))

    try:
        html_content = build_report_html(
            spec=req.spec,
            report_id=str(report.id),
            refresh_token=report.refresh_token,
            api_base_url=_api_base_url(),
        )
    except Exception as e:
        logger.error("[Reports] spec 更新 HTML 生成失败: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"HTML 生成失败: {e}")

    if not report.report_file_path:
        raise HTTPException(status_code=400, detail="报告尚无 HTML 文件路径，无法覆写")

    html_path = _CUSTOMER_DATA_ROOT / report.report_file_path
    html_path.parent.mkdir(parents=True, exist_ok=True)
    html_path.write_text(html_content, encoding="utf-8")

    # 更新数据库字段
    report.charts = req.spec.get("charts", report.charts)
    report.data_sources = _build_data_sources(req.spec) or report.data_sources
    report.filters = req.spec.get("filters", report.filters)
    report.theme = req.spec.get("theme", report.theme)
    if req.spec.get("title"):
        report.name = req.spec["title"]
    db.commit()

    logger.info("[Reports] spec 更新完成: id=%s", report_id)
    return {
        "success": True,
        "data": {
            "report_id": str(report.id),
            "updated_at": datetime.utcnow().isoformat(),
        },
    }


@router.put("/{report_id}/share")
async def update_report_share(
    report_id: str,
    req: UpdateShareRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("reports", "create")),
):
    """
    更新报告的共享范围（public / team / private）和允许访问的用户列表。
    """
    username = getattr(current_user, "username", "default")
    report = _get_report_or_404(report_id, db)
    _check_ownership(report, username, getattr(current_user, "is_superadmin", False))

    try:
        report.share_scope = req.share_scope
        report.allowed_users = req.allowed_users
        db.commit()
    except AttributeError:
        # share_scope / allowed_users 列尚未迁移，跳过
        pass

    return {"success": True}


@router.post("/{report_id}/copilot")
async def create_report_copilot(
    report_id: str,
    req: CopilotRequest = CopilotRequest(),
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("reports", "read")),
):
    """
    为指定报告创建一个 Co-pilot 对话，系统提示中注入报告上下文。
    返回新建对话 ID，前端跳转到对话页面。
    """
    from backend.services.conversation_service import ConversationService

    username = getattr(current_user, "username", "default")
    report = _get_report_or_404(report_id, db)
    _check_ownership(report, username, getattr(current_user, "is_superadmin", False))

    copilot_system_prompt = (
        f"[Co-pilot 模式] 当前报表：{report.name}\n"
        f"图表数量：{len(report.charts or [])}\n"
        f"图表配置：{json.dumps(report.charts or [], ensure_ascii=False)[:2000]}\n"
        f"过滤器：{json.dumps(report.filters or [], ensure_ascii=False)[:500]}\n"
        f"主题：{report.theme}\n\n"
        "请基于以上报表信息协助用户修改报表。"
    )

    user_id = getattr(current_user, "id", None)
    if user_id is not None and str(user_id) == "default":
        user_id = None

    svc = ConversationService(db)
    conv_title = req.title or f"报表助手 — {report.name}"
    conv = svc.create_conversation(
        title=conv_title,
        system_prompt=copilot_system_prompt,
        metadata={"context_type": "report", "context_id": str(report.id)},
        user_id=user_id,
    )

    logger.info("[Reports] co-pilot 对话已创建: report_id=%s, conv_id=%s", report_id, conv.id)
    return {
        "success": True,
        "data": {"conversation_id": str(conv.id)},
    }


@router.get("/{report_id}")
async def get_report(
    report_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("reports", "read")),
):
    username = getattr(current_user, "username", "default")
    report = _get_report_or_404(report_id, db)
    _check_ownership(report, username, getattr(current_user, "is_superadmin", False))
    return {"success": True, "data": _report_to_dict(report)}


@router.delete("/{report_id}")
async def delete_report(
    report_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("reports", "delete")),
):
    username = getattr(current_user, "username", "default")
    report = _get_report_or_404(report_id, db)
    _check_ownership(report, username, getattr(current_user, "is_superadmin", False))

    # 删除本地 HTML 文件
    if report.report_file_path:
        html_path = _CUSTOMER_DATA_ROOT / report.report_file_path
        if html_path.exists():
            html_path.unlink(missing_ok=True)
            logger.info("[Reports] 删除 HTML 文件: %s", html_path)

    db.delete(report)
    db.commit()
    return {"success": True, "message": "报告已删除"}


@router.get("/{report_id}/summary-status")
async def get_summary_status(
    report_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("reports", "read")),
):
    username = getattr(current_user, "username", "default")
    report = _get_report_or_404(report_id, db)
    _check_ownership(report, username, getattr(current_user, "is_superadmin", False))
    return {
        "success": True,
        "data": {
            "status": report.summary_status,
            "llm_summary": report.llm_summary if report.summary_status == "done" else None,
        },
    }


def _report_to_dict(r: Report) -> Dict:
    d = r.to_dict()
    # 补充预览/下载 URL（前端通过 refresh_token 访问，无需 JWT）
    if r.report_file_path and r.refresh_token:
        d["html_url"] = f"/api/v1/reports/{r.id}/html?token={r.refresh_token}"
        d["download_url"] = f"/api/v1/reports/{r.id}/html?token={r.refresh_token}&download=true"
    elif r.report_file_path:
        # 兜底：无 refresh_token 的旧记录仍提供文件路径下载
        d["download_url"] = f"/api/v1/files/download?path={r.report_file_path}"
    return d


# ─────────────────────────────────────────────────────────────────────────────
# 4. PDF / PPTX 导出
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/{report_id}/export")
async def export_report(
    report_id: str,
    req: ExportReportRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("reports", "read")),
):
    """
    异步导出 PDF 或 PPTX。返回 job_id，前端轮询 /export-status。
    """
    username = getattr(current_user, "username", "default")
    report = _get_report_or_404(report_id, db)
    _check_ownership(report, username, getattr(current_user, "is_superadmin", False))

    if req.format not in ("pdf", "pptx"):
        raise HTTPException(400, "format 必须是 pdf 或 pptx")

    if not report.report_file_path:
        raise HTTPException(400, "报告尚未生成 HTML 文件，无法导出")

    job_id = str(uuid.uuid4())
    _export_jobs[job_id] = {
        "status": "pending",
        "report_id": report_id,
        "format": req.format,
        "created_at": datetime.utcnow().isoformat(),
        "output_path": None,
        "error": None,
    }

    html_path = str(_CUSTOMER_DATA_ROOT / report.report_file_path)
    output_dir = str(_CUSTOMER_DATA_ROOT / username / "exports")
    title = report.name or "report"

    background_tasks.add_task(
        _run_export_job, job_id, req.format, html_path, output_dir, title, report.llm_summary or ""
    )

    return {"success": True, "data": {"job_id": job_id}}


@router.get("/{report_id}/export-status")
async def get_export_status(
    report_id: str,
    job_id: str = Query(...),
    db: Session = Depends(get_db),
    current_user=Depends(require_permission("reports", "read")),
):
    username = getattr(current_user, "username", "default")
    # 验证报告归属（防止跨用户查询）
    report = _get_report_or_404(report_id, db)
    _check_ownership(report, username, getattr(current_user, "is_superadmin", False))

    job = _export_jobs.get(job_id)
    if not job or job["report_id"] != report_id:
        raise HTTPException(404, "导出任务不存在")

    resp: Dict[str, Any] = {"success": True, "data": job}
    if job["status"] == "done" and job["output_path"]:
        # 返回下载 URL（通过 files API）
        try:
            rel = str(Path(job["output_path"]).relative_to(_CUSTOMER_DATA_ROOT))
        except ValueError:
            rel = job["output_path"]
        resp["data"]["download_url"] = f"/api/v1/files/download?path={rel}"
    return resp


async def _run_export_job(
    job_id: str,
    fmt: str,
    html_path: str,
    output_dir: str,
    title: str,
    llm_summary: str,
) -> None:
    _export_jobs[job_id]["status"] = "running"
    try:
        Path(output_dir).mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        title_slug = _slugify(title)[:40]

        if fmt == "pdf":
            from backend.services.pdf_export_service import html_to_pdf
            out = os.path.join(output_dir, f"{title_slug}_{ts}.pdf")
            await html_to_pdf(html_path, out)
        else:
            from backend.services.pptx_export_service import html_to_pptx
            out = os.path.join(output_dir, f"{title_slug}_{ts}.pptx")
            await html_to_pptx(html_path, out, title=title, summary=llm_summary)

        _export_jobs[job_id]["status"] = "done"
        _export_jobs[job_id]["output_path"] = out
        logger.info("[Reports] 导出完成: job=%s, file=%s", job_id, out)
    except Exception as e:
        _export_jobs[job_id]["status"] = "failed"
        _export_jobs[job_id]["error"] = str(e)
        logger.error("[Reports] 导出失败: job=%s, error=%s", job_id, e, exc_info=True)
