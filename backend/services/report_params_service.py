"""
report_params_service.py — SQL 参数渲染引擎

功能：
  - render_sql(template, params)               — Jinja2 沙盒渲染，防模板注入
  - extract_default_params(spec)               — 从 filter spec 计算初始参数值
  - compute_params_from_binds(specs, values)   — 运行时 filter 值 → SQL 变量映射
  - flatten_query_params(raw_params)           — FastAPI Query 原始参数展平

设计参考：Redash query parameters / Superset Jinja2 SQL templates
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# SQL 模板渲染
# ─────────────────────────────────────────────────────────────────────────────

def render_sql(template: str, params: Dict[str, Any]) -> str:
    """
    用 Jinja2 SandboxedEnvironment 渲染 SQL 模板。

    - 未定义变量 → 空字符串（不抛错，SQL 仍可执行）
    - 沙盒环境阻止 __class__ / __import__ 等模板注入攻击
    - 非 Jinja2 模板（无 {{ }}）直接原样返回，零开销

    示例：
        render_sql(
            "WHERE dt >= '{{ date_start }}' AND dt < '{{ date_end }}'",
            {"date_start": "2025-01-01", "date_end": "2025-12-31"}
        )
        → "WHERE dt >= '2025-01-01' AND dt < '2025-12-31'"
    """
    if "{{" not in template:
        return template  # 快速路径：非模板 SQL 不处理

    try:
        from jinja2.sandbox import SandboxedEnvironment
        from jinja2 import Undefined

        class _SilentUndefined(Undefined):
            """未定义变量 → 空字符串，不抛 UndefinedError。"""
            def __str__(self) -> str:
                return ""

            def __iter__(self):
                return iter([])

            def __len__(self) -> int:
                return 0

            def __call__(self, *a, **kw):
                return _SilentUndefined()

        env = SandboxedEnvironment(undefined=_SilentUndefined)
        return env.from_string(template).render(**params)

    except Exception as exc:
        logger.warning("[ReportParams] render_sql 失败，返回原模板: %s", exc)
        return template


# ─────────────────────────────────────────────────────────────────────────────
# binds 格式兼容
# ─────────────────────────────────────────────────────────────────────────────

def _normalize_binds(binds: Any) -> Dict[str, str]:
    """
    将 binds 统一转换为 dict 格式。

    AI 有时会传 list 格式（如 ["date_start", "date_end"]），正确格式是
    dict（如 {"start": "date_start", "end": "date_end"}）。
    此函数做容错兼容：
      list[0] → start, list[1] → end（date_range 惯例）
    """
    if isinstance(binds, dict):
        return binds
    if isinstance(binds, list):
        result: Dict[str, str] = {}
        if len(binds) >= 1:
            result["start"] = str(binds[0])
        if len(binds) >= 2:
            result["end"] = str(binds[1])
        if len(binds) >= 3:
            result["value"] = str(binds[2])
        return result
    return {}


# ─────────────────────────────────────────────────────────────────────────────
# 默认参数提取（页面初次加载）
# ─────────────────────────────────────────────────────────────────────────────

def extract_default_params(spec: Dict[str, Any]) -> Dict[str, Any]:
    """
    从 spec.filters 计算默认 SQL 参数，供页面初次加载时使用。

    Filter binds 字段说明：
      date_range:   {"start": "date_start", "end": "date_end"}
      select/radio: {"value": "enterprise_id"}
      multi_select: {"values": "tag_list"}

    若无 binds 字段，使用 filter.id 作为变量名前缀（向后兼容）。
    """
    params: Dict[str, Any] = {}
    today = date.today()

    for f in spec.get("filters", []):
        ftype = f.get("type", "select")
        fid = f.get("id", "")
        binds: Dict[str, str] = _normalize_binds(f.get("binds", {}))

        if ftype == "date_range":
            default_days = int(f.get("default_days", 30))
            start_val = (today - timedelta(days=default_days)).isoformat()
            end_val = today.isoformat()
            start_var = binds.get("start", f"{fid}_start")
            end_var = binds.get("end", f"{fid}_end")
            params[start_var] = start_val
            params[end_var] = end_val

        elif ftype in ("select", "radio"):
            default_val = f.get("default_value")
            if default_val is not None:
                val_var = binds.get("value", fid)
                if val_var:
                    params[val_var] = str(default_val)

        elif ftype == "multi_select":
            default_val = f.get("default_value", [])
            if default_val:
                val_var = binds.get("values", fid)
                if val_var:
                    params[val_var] = (
                        default_val if isinstance(default_val, list) else [str(default_val)]
                    )

    return params


# ─────────────────────────────────────────────────────────────────────────────
# 运行时参数映射（筛选器值 → SQL 变量）
# ─────────────────────────────────────────────────────────────────────────────

def compute_params_from_binds(
    filter_specs: List[Dict[str, Any]],
    filter_values: Dict[str, Any],
) -> Dict[str, Any]:
    """
    将前端提交的筛选器值，按 binds 字段映射为 SQL 模板变量字典。

    filter_values 示例：
        {"date_range": {"start": "2025-01-01", "end": "2025-12-31"},
         "env_select": "sg"}

    Returns 示例：
        {"date_start": "2025-01-01", "date_end": "2025-12-31", "env": "sg"}
    """
    params: Dict[str, Any] = {}

    for f in filter_specs:
        fid = f.get("id", "")
        ftype = f.get("type", "select")
        binds: Dict[str, str] = _normalize_binds(f.get("binds", {}))
        val = filter_values.get(fid)

        if val is None:
            continue

        if ftype == "date_range" and isinstance(val, dict):
            start_var = binds.get("start", f"{fid}_start")
            end_var = binds.get("end", f"{fid}_end")
            if val.get("start"):
                params[start_var] = val["start"]
            if val.get("end"):
                params[end_var] = val["end"]

        elif ftype in ("select", "radio"):
            # 只有显式配置了 binds.value 才映射到 SQL 参数
            val_var = binds.get("value")
            if val_var and val:
                params[val_var] = str(val)

        elif ftype == "multi_select":
            # 只有显式配置了 binds.values 才映射到 SQL 参数
            val_var = binds.get("values")
            if val_var and val:
                params[val_var] = val if isinstance(val, list) else [str(val)]

    return params


# ─────────────────────────────────────────────────────────────────────────────
# FastAPI Query 参数处理
# ─────────────────────────────────────────────────────────────────────────────

def flatten_query_params(raw: Dict[str, Any]) -> Dict[str, Any]:
    """
    FastAPI 将重复 key 的 Query 参数聚合为 list。
    此函数将单元素 list 展开为标量，保持 multi_select 多值为 list。

    示例：
        {"date_start": ["2025-01-01"], "tags": ["a", "b"]}
        → {"date_start": "2025-01-01", "tags": ["a", "b"]}
    """
    result: Dict[str, Any] = {}
    for k, v in raw.items():
        if isinstance(v, list) and len(v) == 1:
            result[k] = v[0]
        else:
            result[k] = v
    return result
