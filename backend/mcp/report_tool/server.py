"""
Report MCP Tool Server

为 AI Agent 提供报表读取与更新工具，使用 refresh_token 鉴权（无需 JWT）。
适用于 Pilot 对话场景：任何 LLM（Claude、OpenAI 等）均可通过此工具直接更新报表。

注册后工具命名（tool_formatter 规则：server_name → 下划线，+ __ + tool_name）：
  server "report" → 前缀 "report"
  report__get_spec           — 读取报表当前 spec
  report__update_spec        — 全量更新报表 spec（重建 HTML）
  report__update_single_chart — 局部 merge 更新单个图表（不影响其他图表）
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict

from backend.mcp.base import BaseMCPServer

logger = logging.getLogger(__name__)


class ReportToolMCPServer(BaseMCPServer):
    """报表操作 MCP 服务器（refresh_token 鉴权，无需 JWT）。"""

    def __init__(self) -> None:
        super().__init__(name="Report Tool MCP Server", version="1.0.0")

    async def initialize(self) -> None:
        self._register_tools()

    def _register_tools(self) -> None:
        # ── get_spec ──────────────────────────────────────────────────────────
        self.register_tool(
            name="get_spec",
            description=(
                "读取报表的当前 spec（含所有图表、筛选器、主题）。"
                "用于修改前先了解现有配置，避免遗漏图表。"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "report_id": {
                        "type": "string",
                        "description": "报表 UUID（来自系统提示中的 report_id）",
                    },
                    "token": {
                        "type": "string",
                        "description": "报表访问令牌（来自系统提示中的 refresh_token）",
                    },
                },
                "required": ["report_id", "token"],
            },
            callback=self._get_spec,
        )

        # ── update_spec ───────────────────────────────────────────────────────
        self.register_tool(
            name="update_spec",
            description=(
                "全量更新报表 spec 并重新生成 HTML 预览。"
                "适用场景：添加/删除图表、修改标题/主题、批量修改多个图表。"
                "⚠️ 必须包含所有图表，缺少的图表将被永久删除！"
                "建议先调用 get_spec 获取完整图表列表，再做修改。"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "report_id": {
                        "type": "string",
                        "description": "报表 UUID（来自系统提示中的 report_id）",
                    },
                    "token": {
                        "type": "string",
                        "description": "报表访问令牌（来自系统提示中的 refresh_token）",
                    },
                    "spec": {
                        "type": "object",
                        "description": (
                            "完整报表 spec JSON，必须包含所有图表。"
                            "字段：title, subtitle, theme, charts[], filters[], data_sources[], data{}"
                        ),
                    },
                },
                "required": ["report_id", "token", "spec"],
            },
            callback=self._update_spec,
        )

        # ── update_single_chart ───────────────────────────────────────────────
        self.register_tool(
            name="update_single_chart",
            description=(
                "局部更新单个图表（merge 操作，不影响报表中的其他图表）。"
                "适用场景：修改某一图表的类型、颜色、SQL、标题、平滑度等单一属性。"
                "只需传入要修改的字段，未传入的字段保持原值。"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "report_id": {
                        "type": "string",
                        "description": "报表 UUID（来自系统提示中的 report_id）",
                    },
                    "token": {
                        "type": "string",
                        "description": "报表访问令牌（来自系统提示中的 refresh_token）",
                    },
                    "chart_id": {
                        "type": "string",
                        "description": "要修改的图表 ID（如 'c1'、'c2'）",
                    },
                    "chart_patch": {
                        "type": "object",
                        "description": (
                            "图表配置补丁，只传需要修改的字段。"
                            "常用字段：chart_type, sql, echarts_override, color, title"
                        ),
                    },
                },
                "required": ["report_id", "token", "chart_id", "chart_patch"],
            },
            callback=self._update_single_chart,
        )

    # ── 工具实现 ───────────────────────────────────────────────────────────────

    async def _get_spec(self, report_id: str, token: str) -> Dict[str, Any]:
        """读取报表当前 spec。"""
        try:
            from backend.services.report_service import get_spec_by_token
            spec = get_spec_by_token(report_id=report_id, refresh_token=token)
            chart_count = len(spec.get("charts") or [])
            chart_ids = [c.get("id", "?") for c in (spec.get("charts") or [])]
            return {
                "success": True,
                "report_id": report_id,
                "name": spec.get("name", ""),
                "chart_count": chart_count,
                "chart_ids": chart_ids,
                "spec": spec,
                "message": f"已获取报表 spec，共 {chart_count} 个图表：{', '.join(chart_ids)}",
            }
        except PermissionError as e:
            logger.warning("[ReportTool] get_spec 鉴权失败: report_id=%s err=%s", report_id, e)
            return {"success": False, "error": f"鉴权失败：{e}"}
        except ValueError as e:
            logger.warning("[ReportTool] get_spec 参数错误: report_id=%s err=%s", report_id, e)
            return {"success": False, "error": str(e)}
        except Exception as e:
            logger.error("[ReportTool] get_spec 意外错误: %s", e, exc_info=True)
            return {"success": False, "error": f"获取失败：{e}"}

    async def _update_spec(
        self, report_id: str, token: str, spec: Any
    ) -> Dict[str, Any]:
        """全量更新报表 spec。"""
        # spec 有时会被模型传成 JSON 字符串，做容错解析
        if isinstance(spec, str):
            try:
                spec = json.loads(spec)
            except json.JSONDecodeError as e:
                return {"success": False, "error": f"spec JSON 解析失败: {e}"}

        if not isinstance(spec, dict):
            return {"success": False, "error": "spec 必须是 JSON 对象"}

        try:
            from backend.services.report_service import update_spec_by_token
            result = update_spec_by_token(
                report_id=report_id, spec=spec, refresh_token=token
            )
            chart_count = len(spec.get("charts") or [])
            logger.info("[ReportTool] update_spec 成功: report_id=%s charts=%d", report_id, chart_count)
            return {
                "success": True,
                "report_id": result["report_id"],
                "name": result["name"],
                "updated_at": result["updated_at"],
                "chart_count": chart_count,
                "message": f"报表已更新，共 {chart_count} 个图表，请查看左侧预览。",
            }
        except PermissionError as e:
            return {"success": False, "error": f"鉴权失败：{e}"}
        except (ValueError, RuntimeError) as e:
            logger.error("[ReportTool] update_spec 失败: report_id=%s err=%s", report_id, e)
            return {"success": False, "error": str(e)}
        except Exception as e:
            logger.error("[ReportTool] update_spec 意外错误: %s", e, exc_info=True)
            return {"success": False, "error": f"更新失败：{e}"}

    async def _update_single_chart(
        self,
        report_id: str,
        token: str,
        chart_id: str,
        chart_patch: Any,
    ) -> Dict[str, Any]:
        """局部 merge 更新单个图表。"""
        if isinstance(chart_patch, str):
            try:
                chart_patch = json.loads(chart_patch)
            except json.JSONDecodeError as e:
                return {"success": False, "error": f"chart_patch JSON 解析失败: {e}"}

        if not isinstance(chart_patch, dict):
            return {"success": False, "error": "chart_patch 必须是 JSON 对象"}

        try:
            from backend.services.report_service import update_single_chart_by_token
            result = update_single_chart_by_token(
                report_id=report_id,
                chart_id=chart_id,
                chart_patch=chart_patch,
                refresh_token=token,
            )
            action = "已更新" if result["found"] else "已添加"
            logger.info(
                "[ReportTool] update_single_chart 成功: report_id=%s chart_id=%s found=%s",
                report_id, chart_id, result["found"],
            )
            return {
                "success": True,
                "report_id": result["report_id"],
                "chart_id": chart_id,
                "found": result["found"],
                "total_charts": result["total_charts"],
                "updated_at": result["updated_at"],
                "message": (
                    f"图表 {chart_id} {action}，报表已更新（共 {result['total_charts']} 个图表），"
                    f"请查看左侧预览。"
                ),
            }
        except PermissionError as e:
            return {"success": False, "error": f"鉴权失败：{e}"}
        except (ValueError, RuntimeError) as e:
            logger.error("[ReportTool] update_single_chart 失败: report_id=%s err=%s", report_id, e)
            return {"success": False, "error": str(e)}
        except Exception as e:
            logger.error("[ReportTool] update_single_chart 意外错误: %s", e, exc_info=True)
            return {"success": False, "error": f"更新失败：{e}"}
