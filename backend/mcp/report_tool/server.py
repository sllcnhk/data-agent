"""
Report MCP Tool Server

? AI Agent ?????????????? refresh_token ????? JWT?
??? Pilot ??????? LLM?Claude?OpenAI ????????????????

????????tool_formatter ???server_name + __ + tool_name??
  report__create
  report__get_spec
  report__update_spec
  report__update_single_chart
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict

from backend.mcp.base import BaseMCPServer

logger = logging.getLogger(__name__)


class ReportToolMCPServer(BaseMCPServer):
    """???? MCP ????refresh_token ????? JWT??"""

    def __init__(self) -> None:
        super().__init__(name="Report Tool MCP Server", version="1.0.0")

    async def initialize(self) -> None:
        self._register_tools()

    def _register_tools(self) -> None:
        self.register_tool(
            name="create",
            description='????????????????????? HTML ???\nHTML ????????? ClickHouse?Jinja2 ??? SQL??????????\n????????????????????/???/?????????\n?? ??? filesystem__write_file ? HTML ?????????\n?? ?????N?/??/????????? date_range ?????? SQL ?? {{ date_start }}/{{ date_end }} ?????????????? SQL ??\n?? ??????????/??/???????? include_summary=true ? ai_analysis?????? table + UNION ALL ?????????\n????? report_id + refresh_token????????????????',
            input_schema={
                "type": "object",
                "properties": {
                    "spec": {
                        "type": "object",
                        "description": '???? spec??????\n  title: ????\n  charts[]: ?????????? id/chart_type/title/sql/connection_env/x_field/y_fields/series_field\n  filters[]: ???????N?/??/???????? date_range?? binds ???? date_start/date_end\n?????subtitle/theme(light|dark)/data_sources[]/include_summary\n????????? chart_type/sql/connection_env/x_field/y_fields/series_field????? type/dataset/xField/yField/seriesField ? legacy ???\n???????/??/??????? include_summary=true ??? ai_analysis?????? table + UNION ALL ?????',
                    },
                    "username": {
                        "type": "string",
                        "description": "???????????? CURRENT_USER ??",
                    },
                },
                "required": ["spec", "username"],
            },
            callback=self._create,
        )

        self.register_tool(
            name="get_spec",
            description='??????? spec???????????????????????????????????',
            input_schema={
                "type": "object",
                "properties": {
                    "report_id": {
                        "type": "string",
                        "description": "?? UUID????????? report_id?",
                    },
                    "token": {
                        "type": "string",
                        "description": "??????????????? refresh_token?",
                    },
                },
                "required": ["report_id", "token"],
            },
            callback=self._get_spec,
        )

        self.register_tool(
            name="update_spec",
            description='?????? spec ????? HTML ??????????/?????????/?????????????? ??????????????????????? ?N?/??/????????? date_range + date_start/date_end ??? SQL????????????? ???????????? include_summary=true ? ai_analysis?????? table ?????????? get_spec ??????????????',
            input_schema={
                "type": "object",
                "properties": {
                    "report_id": {
                        "type": "string",
                        "description": "?? UUID????????? report_id?",
                    },
                    "token": {
                        "type": "string",
                        "description": "??????????????? refresh_token?",
                    },
                    "spec": {
                        "type": "object",
                        "description": '???? spec JSON?????????????title, subtitle, theme, charts[], filters[], data_sources[], data{}, include_summary????????? date_range ???? date_start/date_end ??????????? include_summary=true ? ai_analysis?',
                    },
                },
                "required": ["report_id", "token", "spec"],
            },
            callback=self._update_spec,
        )

        self.register_tool(
            name="update_single_chart",
            description='?????????merge ??????????????????????????????????SQL????????????????????????????????????? ?????????? SQL ???????????????????? ai_analysis ???? table?',
            input_schema={
                "type": "object",
                "properties": {
                    "report_id": {
                        "type": "string",
                        "description": "?? UUID????????? report_id?",
                    },
                    "token": {
                        "type": "string",
                        "description": "??????????????? refresh_token?",
                    },
                    "chart_id": {
                        "type": "string",
                        "description": "?????? ID?? c1?c2?",
                    },
                    "chart_patch": {
                        "type": "object",
                        "description": '??????????????????????chart_type, sql, echarts_override, color, title???? SQL ?????? {{ date_start }}/{{ date_end }} ??????????? ai_analysis??????? table?',
                    },
                },
                "required": ["report_id", "token", "chart_id", "chart_patch"],
            },
            callback=self._update_single_chart,
        )

    async def _create(self, spec: Any, username: str) -> Dict[str, Any]:
        """???????? DB ?? + ?? HTML ????"""
        if isinstance(spec, str):
            try:
                spec = json.loads(spec)
            except json.JSONDecodeError as e:
                return {"success": False, "error": f"spec JSON \u89e3\u6790\u5931\u8d25: {e}"}

        if not isinstance(spec, dict):
            return {"success": False, "error": "spec \u5fc5\u987b\u662f JSON \u5bf9\u8c61"}

        if not username or not isinstance(username, str):
            return {"success": False, "error": "username \u4e0d\u80fd\u4e3a\u7a7a"}

        charts = spec.get("charts") or []
        if not charts:
            return {"success": False, "error": "spec.charts \u4e0d\u80fd\u4e3a\u7a7a\uff0c\u81f3\u5c11\u9700\u8981\u4e00\u4e2a\u56fe\u8868"}

        try:
            from backend.services.report_service import create_report_with_spec

            result = create_report_with_spec(spec=spec, username=username.strip())
            chart_count = len(charts)
            logger.info(
                "[ReportTool] create ??: report_id=%s user=%s charts=%d",
                result["report_id"], username, chart_count,
            )
            return {
                "success": True,
                "report_id": result["report_id"],
                "refresh_token": result["refresh_token"],
                "name": result["name"],
                "html_path": result["html_path"],
                "chart_count": chart_count,
                "message": result["message"],
            }
        except (ValueError, RuntimeError) as e:
            logger.error("[ReportTool] create ??: user=%s err=%s", username, e)
            return {"success": False, "error": str(e)}
        except Exception as e:
            logger.error("[ReportTool] create ????: %s", e, exc_info=True)
            return {"success": False, "error": f"\u521b\u5efa\u5931\u8d25\uff1a{e}"}

    async def _get_spec(self, report_id: str, token: str) -> Dict[str, Any]:
        """?????? spec?"""
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
                "message": f"????? spec?? {chart_count} ????{', '.join(chart_ids)}",
            }
        except PermissionError as e:
            logger.warning("[ReportTool] get_spec ????: report_id=%s err=%s", report_id, e)
            return {"success": False, "error": f"\u9274\u6743\u5931\u8d25\uff1a{e}"}
        except ValueError as e:
            logger.warning("[ReportTool] get_spec ????: report_id=%s err=%s", report_id, e)
            return {"success": False, "error": str(e)}
        except Exception as e:
            logger.error("[ReportTool] get_spec ????: %s", e, exc_info=True)
            return {"success": False, "error": f"\u83b7\u53d6\u5931\u8d25\uff1a{e}"}

    async def _update_spec(self, report_id: str, token: str, spec: Any) -> Dict[str, Any]:
        """?????? spec?"""
        if isinstance(spec, str):
            try:
                spec = json.loads(spec)
            except json.JSONDecodeError as e:
                return {"success": False, "error": f"spec JSON \u89e3\u6790\u5931\u8d25: {e}"}

        if not isinstance(spec, dict):
            return {"success": False, "error": "spec \u5fc5\u987b\u662f JSON \u5bf9\u8c61"}

        try:
            from backend.services.report_service import update_spec_by_token

            result = update_spec_by_token(report_id=report_id, spec=spec, refresh_token=token)
            chart_count = len(spec.get("charts") or [])
            logger.info("[ReportTool] update_spec ??: report_id=%s charts=%d", report_id, chart_count)
            return {
                "success": True,
                "report_id": result["report_id"],
                "refresh_token": token,
                "name": result["name"],
                "updated_at": result["updated_at"],
                "chart_count": chart_count,
                "message": f"\u62a5\u8868\u5df2\u66f4\u65b0\uff0c\u5171 {chart_count} \u4e2a\u56fe\u8868\uff0c\u8bf7\u67e5\u770b\u5de6\u4fa7\u9884\u89c8\u3002",
            }
        except PermissionError as e:
            return {"success": False, "error": f"\u9274\u6743\u5931\u8d25\uff1a{e}"}
        except (ValueError, RuntimeError) as e:
            logger.error("[ReportTool] update_spec ??: report_id=%s err=%s", report_id, e)
            return {"success": False, "error": str(e)}
        except Exception as e:
            logger.error("[ReportTool] update_spec ????: %s", e, exc_info=True)
            return {"success": False, "error": f"\u9274\u6743\u5931\u8d25\uff1a{e}"}

    async def _update_single_chart(
        self,
        report_id: str,
        token: str,
        chart_id: str,
        chart_patch: Any,
    ) -> Dict[str, Any]:
        """?? merge ???????"""
        if isinstance(chart_patch, str):
            try:
                chart_patch = json.loads(chart_patch)
            except json.JSONDecodeError as e:
                return {"success": False, "error": f"chart_patch JSON \u89e3\u6790\u5931\u8d25: {e}"}

        if not isinstance(chart_patch, dict):
            return {"success": False, "error": "chart_patch \u5fc5\u987b\u662f JSON \u5bf9\u8c61"}

        try:
            from backend.services.report_service import update_single_chart_by_token

            result = update_single_chart_by_token(
                report_id=report_id,
                chart_id=chart_id,
                chart_patch=chart_patch,
                refresh_token=token,
            )
            action = "\u5df2\u66f4\u65b0" if result["found"] else "\u5df2\u65b0\u589e"
            logger.info(
                "[ReportTool] update_single_chart ??: report_id=%s chart_id=%s found=%s",
                report_id, chart_id, result["found"],
            )
            return {
                "success": True,
                "report_id": result["report_id"],
                "refresh_token": token,
                "chart_id": chart_id,
                "found": result["found"],
                "total_charts": result["total_charts"],
                "updated_at": result["updated_at"],
                "message": (
                    f"\u56fe\u8868 {chart_id} {action}\uff0c\u62a5\u8868\u5df2\u66f4\u65b0\uff08\u5171 {result['total_charts']} \u4e2a\u56fe\u8868\uff09\uff0c"
                    f"\u8bf7\u67e5\u770b\u5de6\u4fa7\u9884\u89c8\u3002"
                ),
            }
        except PermissionError as e:
            return {"success": False, "error": f"\u66f4\u65b0\u5931\u8d25\uff1a{e}"}
        except (ValueError, RuntimeError) as e:
            logger.error("[ReportTool] update_single_chart ??: report_id=%s err=%s", report_id, e)
            return {"success": False, "error": str(e)}
        except Exception as e:
            logger.error("[ReportTool] update_single_chart ????: %s", e, exc_info=True)
            return {"success": False, "error": f"\u66f4\u65b0\u5931\u8d25\uff1a{e}"}
