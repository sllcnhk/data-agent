from __future__ import annotations

import copy
import re
from typing import Any, Dict, List, Optional, Tuple

_DATE_FILTER_BIND_START = "date_start"
_DATE_FILTER_BIND_END = "date_end"
_DEFAULT_DATE_FILTER_ID = "date_range"
_DATE_FILTER_TYPES = {"date_range"}
_TIME_KEYWORDS = (
    "近30天",
    "近 30 天",
    "最近30天",
    "最近 30 天",
    "近7天",
    "近 7 天",
    "最近7天",
    "最近 7 天",
    "每日",
    "按天",
    "日趋势",
    "趋势",
    "day",
    "daily",
    "trend",
)
_TIME_FIELD_KEYWORDS = ("day", "date", "dt", "s_day", "stat_date", "biz_date")
_SUMMARY_KEYWORDS = ("总结", "分析", "洞察", "建议", "结论", "summary", "analysis", "insight")
_STATIC_SUMMARY_SQL_HINTS = (
    "UNION ALL",
    "'统计区间'",
    "'风险提示'",
    "'峰值日期'",
    "'低点日期'",
    "'STAT_WINDOW'",
    "'RISK'",
)
# T-A2: 移除 WHERE/PREWHERE 前缀限制，支持日期条件出现在 SQL 任意位置
# 模式 1（双边界）: field >= today() - N AND field <= today()
_HARDCODED_DATE_RANGE_FULL_RE = re.compile(
    r"(?P<field>[A-Za-z_][\w\.]*)\s*>=\s*today\(\)\s*-\s*\d+\s+AND\s+"
    r"(?P=field)\s*<=?\s*today\(\)",
    flags=re.IGNORECASE,
)
# 模式 2（仅下界）: field >= today() - N （后面不跟 AND field <=? today()）
# 常见于 AI 只写了起始条件，未显式写 AND field <= today() 的情况
_HARDCODED_DATE_LOWER_RE = re.compile(
    r"(?P<field>[A-Za-z_][\w\.]*)\s*>=\s*today\(\)\s*-\s*\d+",
    flags=re.IGNORECASE,
)
# 保留旧名兼容（T-B 系列测试引用了 _HARDCODED_DATE_RANGE_RE）
_HARDCODED_DATE_RANGE_RE = _HARDCODED_DATE_RANGE_FULL_RE


def _default_ai_summary_chart(chart_id: str = "summary_ai", title: str = "AI 数据分析总结") -> Dict[str, Any]:
    return {
        "id": chart_id,
        "chart_type": "ai_analysis",
        "title": title,
        "width": "full",
    }


def has_ai_analysis_chart(charts: List[Dict[str, Any]]) -> bool:
    return any(
        isinstance(c, dict) and c.get("chart_type") == "ai_analysis"
        for c in (charts or [])
    )


def normalize_filter_binds(binds: Any) -> Dict[str, str]:
    if binds is None:
        return {}
    if isinstance(binds, dict):
        return {
            str(k): str(v)
            for k, v in binds.items()
            if k not in (None, "") and v not in (None, "")
        }
    if isinstance(binds, (list, tuple)):
        items = [str(x) for x in binds if x not in (None, "")]
        if len(items) >= 2:
            return {"start": items[0], "end": items[1]}
        if len(items) == 1:
            return {"value": items[0]}
    return {}


def normalize_filter_spec(filter_spec: Dict[str, Any]) -> Dict[str, Any]:
    out = copy.deepcopy(filter_spec or {})
    ftype = str(out.get("type") or "").strip().lower()

    # 自动补全 id — JS onFilterChange 通过 f.id 匹配，id 缺失会导致筛选器失效
    if not out.get("id"):
        out["id"] = ftype or "filter"

    if "binds" in out:
        out["binds"] = normalize_filter_binds(out.get("binds"))
    elif ftype in _DATE_FILTER_TYPES:
        out["binds"] = {
            "start": _DATE_FILTER_BIND_START,
            "end": _DATE_FILTER_BIND_END,
        }

    if ftype in _DATE_FILTER_TYPES and not out.get("label"):
        out["label"] = "日期范围"

    default_value = out.get("default")
    if ftype in _DATE_FILTER_TYPES and "default_days" not in out:
        if isinstance(default_value, (list, tuple)) and len(default_value) >= 2:
            m = re.search(r"today\(\)\s*-\s*(\d+)", str(default_value[0]), flags=re.IGNORECASE)
            if m:
                out["default_days"] = int(m.group(1))
        elif isinstance(default_value, str):
            m = re.search(r"(\d+)", default_value)
            if m:
                out["default_days"] = int(m.group(1))
    return out


def _summary_text_from_chart(chart: Dict[str, Any]) -> str:
    parts = [
        chart.get("title", ""),
        chart.get("description", ""),
        chart.get("subtitle", ""),
        chart.get("sql", ""),
    ]
    return " ".join(str(p) for p in parts if p)


def is_summary_like_table(chart: Dict[str, Any]) -> bool:
    chart = copy.deepcopy(chart or {})
    if str(chart.get("chart_type") or "").strip().lower() != "table":
        return False
    text = _summary_text_from_chart(chart)
    text_lower = text.lower()
    if not any(keyword in text_lower or keyword in text for keyword in _SUMMARY_KEYWORDS):
        return False
    sql_upper = str(chart.get("sql") or "").upper()
    return any(hint in sql_upper for hint in _STATIC_SUMMARY_SQL_HINTS)


def _coerce_chart_to_ai_summary(chart: Dict[str, Any]) -> Dict[str, Any]:
    title = chart.get("title") or "AI 数据分析总结"
    return _default_ai_summary_chart(chart_id=chart.get("id") or "summary_ai", title=title)


def _normalize_y_fields(out: Dict[str, Any]) -> None:
    y_fields = out.get("y_fields")
    legacy_y = out.get("yField")
    singular_y = out.get("y_field")
    if not y_fields:
        source = legacy_y if legacy_y not in (None, "") else singular_y
        if source not in (None, ""):
            out["y_fields"] = [source] if not isinstance(source, list) else list(source)
    elif isinstance(y_fields, str):
        out["y_fields"] = [y_fields]
    out.pop("y_field", None)


def normalize_chart_spec(chart: Dict[str, Any]) -> Dict[str, Any]:
    out = copy.deepcopy(chart or {})
    dataset = out.get("dataset")
    if not isinstance(dataset, dict):
        dataset = {}

    if not out.get("chart_type") and out.get("type"):
        out["chart_type"] = out.get("type")

    if not out.get("sql") and dataset.get("query"):
        out["sql"] = dataset.get("query")

    if not out.get("connection_env") and dataset.get("server"):
        out["connection_env"] = dataset.get("server")

    if not out.get("connection_type"):
        source = dataset.get("source")
        if isinstance(source, str) and source.strip():
            out["connection_type"] = source.strip()

    if not out.get("x_field") and out.get("xField"):
        out["x_field"] = out.get("xField")

    _normalize_y_fields(out)

    if not out.get("series_field") and out.get("seriesField"):
        out["series_field"] = out.get("seriesField")

    if isinstance(out.get("chart_type"), str):
        out["chart_type"] = out["chart_type"].strip()
    if isinstance(out.get("connection_env"), str):
        env = out["connection_env"].strip()
        # 规范化：去掉 AI 有时生成的 "clickhouse-" 前缀（技能文档要求短名 "sg"/"idn"）
        if env.lower().startswith("clickhouse-"):
            env = env[len("clickhouse-"):]
        out["connection_env"] = env
    if isinstance(out.get("connection_type"), str):
        out["connection_type"] = out["connection_type"].strip() or "clickhouse"

    for legacy_key in ("type", "dataset", "xField", "yField", "seriesField"):
        out.pop(legacy_key, None)

    if is_summary_like_table(out):
        return _coerce_chart_to_ai_summary(out)
    return out


def _normalize_summary_charts(charts: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], bool]:
    normalized: List[Dict[str, Any]] = []
    include_summary = False
    ai_chart_seen = False

    for raw_chart in charts or []:
        chart = normalize_chart_spec(raw_chart) if isinstance(raw_chart, dict) else raw_chart
        if not isinstance(chart, dict):
            normalized.append(chart)
            continue

        if chart.get("chart_type") == "ai_analysis":
            include_summary = True
            if ai_chart_seen:
                continue
            ai_chart_seen = True
            normalized.append(chart)
            continue

        if is_summary_like_table(chart):
            include_summary = True
            if ai_chart_seen:
                continue
            normalized.append(_coerce_chart_to_ai_summary(chart))
            ai_chart_seen = True
            continue

        normalized.append(chart)

    return normalized, include_summary


def _time_signal_from_chart(chart: Dict[str, Any]) -> bool:
    if not isinstance(chart, dict):
        return False
    sql = str(chart.get("sql") or "")
    sql_lower = sql.lower()
    text = " ".join(
        str(chart.get(k, ""))
        for k in ("title", "description", "subtitle", "x_field", "sql")
    )
    text_lower = text.lower()
    if any(keyword.lower() in text_lower for keyword in _TIME_KEYWORDS):
        return True
    x_field = str(chart.get("x_field") or "").lower()
    if any(keyword in x_field for keyword in _TIME_FIELD_KEYWORDS):
        return bool(
            "today()" in sql_lower
            or "{{ date_start }}" in sql_lower
            or "{{ date_end }}" in sql_lower
            or "todate(" in sql_lower
        )
    return False


def report_needs_date_range_filter(spec: Dict[str, Any]) -> bool:
    charts = spec.get("charts") or []
    return any(_time_signal_from_chart(chart) for chart in charts if isinstance(chart, dict))


def get_date_range_filter(filters: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    for filter_spec in filters or []:
        if not isinstance(filter_spec, dict):
            continue
        if str(filter_spec.get("type") or "").strip().lower() in _DATE_FILTER_TYPES:
            return filter_spec
    return None


def ensure_date_range_filter(spec: Dict[str, Any]) -> Dict[str, Any]:
    out = copy.deepcopy(spec or {})
    filters = list(out.get("filters") or [])
    if not report_needs_date_range_filter(out):
        out["filters"] = filters
        return out

    existing = get_date_range_filter(filters)
    if existing is None:
        filters.insert(0, {
            "id": _DEFAULT_DATE_FILTER_ID,
            "type": "date_range",
            "label": "日期范围",
            "default_days": 30,
            "binds": {
                "start": _DATE_FILTER_BIND_START,
                "end": _DATE_FILTER_BIND_END,
            },
        })
    else:
        normalized = normalize_filter_spec(existing)
        if "default_days" not in normalized:
            normalized["default_days"] = 30
        idx = filters.index(existing)
        filters[idx] = normalized
    out["filters"] = [normalize_filter_spec(f) if isinstance(f, dict) else f for f in filters]
    return out


def _parameterize_sql_date_range(sql: str) -> str:
    """
    T-A2: 将 SQL 中硬编码的 today()-N 日期条件替换为 Jinja2 参数。

    两步处理：
    1. 优先匹配双边界 (>= today()-N AND field <= today())，整体替换
    2. 若无双边界，再匹配仅下界 (>= today()-N)，补全上界参数
       ——常见于 AI 只写了起始条件（如 PREWHERE s_day >= today()-30 AND call_code_type IN (...)）
    """
    if not isinstance(sql, str) or "{{" in sql:
        return sql

    def _replace_both(match) -> str:
        field = match.group("field")
        return (
            f"{field} >= toDate('{{{{ {_DATE_FILTER_BIND_START} }}}}') "
            f"AND {field} <= toDate('{{{{ {_DATE_FILTER_BIND_END} }}}}')"
        )

    # 步骤 1：双边界替换
    result = _HARDCODED_DATE_RANGE_FULL_RE.sub(_replace_both, sql)
    if result != sql:
        return result

    # 步骤 2：仅下界替换（补全上界）
    result = _HARDCODED_DATE_LOWER_RE.sub(_replace_both, sql)
    return result


def parameterize_time_range_charts(spec: Dict[str, Any]) -> Dict[str, Any]:
    out = copy.deepcopy(spec or {})
    if not get_date_range_filter(out.get("filters") or []):
        return out

    charts: List[Dict[str, Any]] = []
    for chart in out.get("charts") or []:
        if not isinstance(chart, dict):
            charts.append(chart)
            continue
        normalized = normalize_chart_spec(chart)
        if normalized.get("chart_type") == "ai_analysis":
            charts.append(normalized)
            continue
        if _time_signal_from_chart(normalized):
            normalized["sql"] = _parameterize_sql_date_range(normalized.get("sql", ""))
        charts.append(normalized)
    out["charts"] = charts
    return out


def summary_requested(spec: Dict[str, Any]) -> bool:
    charts = spec.get("charts") or []
    return bool(
        spec.get("include_summary")
        or (spec.get("llm_summary") or "").strip()
        or has_ai_analysis_chart(charts)
        or any(is_summary_like_table(c) for c in charts if isinstance(c, dict))
    )


def ensure_summary_components(spec: Dict[str, Any]) -> Dict[str, Any]:
    out = copy.deepcopy(spec or {})
    charts, include_summary_from_charts = _normalize_summary_charts(list(out.get("charts") or []))
    include_summary = summary_requested({**out, "charts": charts}) or include_summary_from_charts
    out["include_summary"] = include_summary
    if include_summary and not has_ai_analysis_chart(charts):
        charts.append(_default_ai_summary_chart())
    out["charts"] = charts
    return out


def _extract_sql_template_vars(spec: Dict[str, Any]) -> set:
    """提取所有图表 SQL 中出现的 Jinja2 模板变量名。"""
    vars_found: set = set()
    for chart in spec.get("charts") or []:
        if not isinstance(chart, dict):
            continue
        sql = str(chart.get("sql") or "")
        for m in re.finditer(r"\{\{\s*(\w+)\s*\}\}", sql):
            vars_found.add(m.group(1))
    return vars_found


def _reconcile_filter_chart_binds(spec: Dict[str, Any]) -> Dict[str, Any]:
    """
    T-B1: 校正 filter binds 与 SQL 模板变量名的一致性。

    场景：AI 有时将 binds.start/end 设为图表 ID（如 "c1"、"c2"）而非 SQL 变量名
    （如 "date_start"、"date_end"），导致 _DEFAULT_PARAMS 计算错误，SQL 渲染为空字符串。

    算法：
    1. 收集所有图表 SQL 中出现的 {{ var }} 变量名
    2. 对每个 date_range filter：
       - 若 binds.start / binds.end 未出现在任何图表 SQL 中 → 可能错误
       - 检查标准名 "date_start"/"date_end" 是否出现在图表 SQL
       - 若是，则修正 binds 为 {"start": "date_start", "end": "date_end"}
    """
    out = copy.deepcopy(spec or {})
    sql_vars = _extract_sql_template_vars(out)
    if not sql_vars:
        return out  # 无 SQL 模板变量，无需修正

    filters = out.get("filters") or []
    modified = False
    for f in filters:
        if not isinstance(f, dict):
            continue
        if str(f.get("type") or "").strip().lower() not in _DATE_FILTER_TYPES:
            continue
        binds = f.get("binds") or {}
        if not isinstance(binds, dict):
            continue
        start_var = binds.get("start", "")
        end_var = binds.get("end", "")
        # 若 start/end 变量名均不在 SQL 模板变量中，且标准名在其中 → 修正
        if (start_var and end_var
                and start_var not in sql_vars
                and end_var not in sql_vars
                and _DATE_FILTER_BIND_START in sql_vars
                and _DATE_FILTER_BIND_END in sql_vars):
            import logging as _log
            _log.getLogger(__name__).warning(
                "[_reconcile_filter_chart_binds] filter id=%r binds %r→%r 与 SQL 变量不匹配，"
                "已自动修正为标准名 date_start/date_end",
                f.get("id"), binds,
                {"start": _DATE_FILTER_BIND_START, "end": _DATE_FILTER_BIND_END},
            )
            f["binds"] = {
                "start": _DATE_FILTER_BIND_START,
                "end": _DATE_FILTER_BIND_END,
            }
            modified = True

    if modified:
        out["filters"] = filters
    return out


def normalize_report_spec(spec: Dict[str, Any]) -> Dict[str, Any]:
    out = copy.deepcopy(spec or {})
    if not out.get("title") and out.get("name"):
        out["title"] = out.get("name")
    if not out.get("subtitle") and out.get("description"):
        out["subtitle"] = out.get("description")

    out["charts"] = [
        normalize_chart_spec(c) if isinstance(c, dict) else c
        for c in (out.get("charts") or [])
    ]
    out["filters"] = [
        normalize_filter_spec(f) if isinstance(f, dict) else f
        for f in (out.get("filters") or [])
    ]
    out = ensure_summary_components(out)
    out = ensure_date_range_filter(out)
    out = parameterize_time_range_charts(out)
    # T-B2: 校正 filter binds 与图表 SQL 模板变量名的一致性（防止 AI 用图表 ID 作变量名）
    out = _reconcile_filter_chart_binds(out)
    return out


def chart_validation_errors(chart: Dict[str, Any]) -> List[str]:
    chart = normalize_chart_spec(chart)
    errs: List[str] = []
    if not chart.get("id"):
        errs.append("缺少 id")
    if not chart.get("chart_type"):
        errs.append("缺少 chart_type")
    if chart.get("chart_type") == "ai_analysis":
        return errs
    if not chart.get("sql"):
        errs.append("缺少 sql")
    if not chart.get("connection_env"):
        errs.append("缺少 connection_env")
    return errs


def report_validation_errors(spec: Dict[str, Any]) -> List[str]:
    """返回阻塞创建的硬错误（不含 SQL 参数化警告）。"""
    errors: List[str] = []
    filters = spec.get("filters") or []
    date_filter = get_date_range_filter(filters)
    needs_date_filter = report_needs_date_range_filter(spec)
    if needs_date_filter and date_filter is None:
        errors.append("时间趋势类报表缺少 date_range 筛选器")

    if date_filter is not None:
        binds = normalize_filter_binds(date_filter.get("binds"))
        if not binds.get("start") or not binds.get("end"):
            errors.append("date_range 筛选器缺少 start/end 参数绑定")
    return errors


def report_validation_warnings(spec: Dict[str, Any]) -> List[str]:
    """T-A1: 返回非阻塞警告（SQL 未参数化等），仅记录日志，不阻塞创建。"""
    warnings: List[str] = []
    filters = spec.get("filters") or []
    date_filter = get_date_range_filter(filters)
    if date_filter is not None:
        for chart in spec.get("charts") or []:
            if not isinstance(chart, dict):
                continue
            if chart.get("chart_type") == "ai_analysis":
                continue
            if _time_signal_from_chart(chart):
                sql = str(chart.get("sql") or "")
                if "{{ date_start }}" not in sql or "{{ date_end }}" not in sql:
                    warnings.append(
                        f"{chart.get('id') or chart.get('title') or 'chart'}: "
                        "时间趋势图 SQL 未使用 date_start/date_end 参数（筛选器将不生效）"
                    )
    return warnings


def validate_report_spec(
    spec: Dict[str, Any],
    *,
    require_charts: bool = True,
) -> Tuple[Dict[str, Any], List[str]]:
    """
    验证并归一化报表 spec。

    返回 (normalized_spec, blocking_errors)。
    blocking_errors 为空时创建成功；SQL 未参数化等软警告通过
    report_validation_warnings() 单独获取（不阻塞创建）。
    """
    import logging as _logging
    _vlog = _logging.getLogger(__name__)

    normalized = normalize_report_spec(spec)
    errors: List[str] = []
    charts = normalized.get("charts") or []
    if not isinstance(charts, list):
        errors.append("spec.charts 必须是数组")
        return normalized, errors
    if require_charts and not charts:
        errors.append("spec.charts 不能为空")
        return normalized, errors

    for idx, chart in enumerate(charts):
        if not isinstance(chart, dict):
            errors.append(f"charts[{idx}] 不是对象")
            continue
        chart_id = chart.get("id") or f"charts[{idx}]"
        for err in chart_validation_errors(chart):
            errors.append(f"{chart_id}: {err}")

    errors.extend(report_validation_errors(normalized))

    # T-A1: SQL 未参数化为软警告，记录日志但不阻塞创建
    soft_warns = report_validation_warnings(normalized)
    if soft_warns:
        _vlog.warning("[validate_report_spec] 报表规范警告（不阻塞创建）: %s", "; ".join(soft_warns))

    return normalized, errors


def is_chart_executable(chart: Dict[str, Any]) -> bool:
    chart = normalize_chart_spec(chart)
    if chart.get("chart_type") == "ai_analysis":
        return False
    return bool(chart.get("sql") and chart.get("connection_env"))
