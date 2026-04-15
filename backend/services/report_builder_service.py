"""
ReportBuilderService — 多图表 HTML 报告生成引擎

支持图表库：
  - ECharts 5.x  (折线/柱状/饼图/散点/面积/热力/漏斗/仪表/雷达/矩形树/桑基/双轴…)
  - AntV G2 4.x  (小提琴/斜率/玫瑰等高级统计图，通过 CDN 按需加载)
  - D3.js v7      (弦图/力导向/自定义布局，LLM 生成 JS 片段)
  - LLM Custom   (LLM 编写任意 ECharts/D3 option 代码)

输入规格（chart spec JSON）:
  {
    "title": "报告标题",
    "subtitle": "副标题",
    "theme": "light|dark",
    "color_palette": ["#1677ff", ...],
    "charts": [
      {
        "id": "c1",
        "chart_lib": "echarts|antv_g2|d3|kpi|llm_custom",
        "chart_type": "line|bar|bar_horizontal|pie|donut|scatter|area|heatmap|funnel|gauge|radar|treemap|sankey|dual_axis|waterfall|kpi_card|llm_custom",
        "title": "图表标题",
        "sql": "SELECT ...",
        "connection_env": "sg-azure",
        "connection_type": "clickhouse",
        "x_field": "date",
        "y_fields": ["value"],
        "series_field": null,          // 分组字段 → 多 series
        "label_field": null,           // 饼/漏斗: 标签字段
        "value_field": null,           // 饼/漏斗: 数值字段
        "series_names": {"value": "接通率"},  // 字段友好名
        "value_format": {"value": "percent"}, // percent|number|currency|short
        "width": "half|full|third|two-thirds",
        "height": 300,
        "echarts_override": {},        // 合并到 ECharts option
        "llm_chart_js": "",            // chart_lib=llm_custom 时的 JS 代码片段
        "kpi_unit": "%",               // kpi_card 单位
        "kpi_trend": "up|down|flat"    // kpi_card 趋势标记
      }
    ],
    "filters": [
      {
        "id": "date_range",
        "type": "date_range|select|multi_select|radio",
        "label": "时间范围",
        "default_days": 30,             // date_range: 默认往前推 N 天
        "default_value": null,          // select/radio: 默认选中值
        "options": [...],               // select/multi_select/radio
        "placeholder": "选择...",
        "target_charts": ["c1","c2"],   // null = 所有图表
        "binds": {                      // SQL 参数绑定（关键字段！）
          "start": "date_start",        //   date_range: start → SQL 变量名
          "end":   "date_end",          //   date_range: end   → SQL 变量名
          "value": "enterprise_id",     //   select/radio: value → SQL 变量名
          "values": "tag_list"          //   multi_select: values → SQL 变量名
        },
        "client_side": false            // true = 仅客户端过滤，不触发后端重查
      }
    ],
    // 注意：有 binds 字段时，filter 变化会触发后端重查（服务端参数化）
    //       无 binds 字段（旧格式）时，filter 仍走客户端内存过滤（向后兼容）
    "data": {
      "c1": [{"date": "2026-03-01", "value": 0.85}, ...]
    },
    "include_summary": true,
    "llm_summary": "..."
  }
"""
from __future__ import annotations

import json
import logging
import os
import secrets
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# CDN 版本锁定（确保报告长期可打开）
# ─────────────────────────────────────────────────────────────────────────────
_ECHARTS_CDN = "https://cdn.jsdelivr.net/npm/echarts@5.4.3/dist/echarts.min.js"
_ANTV_G2_CDN = "https://cdn.jsdelivr.net/npm/@antv/g2@4.2.10/dist/g2.min.js"
_D3_CDN = "https://cdn.jsdelivr.net/npm/d3@7.8.5/dist/d3.min.js"
_DAYJS_CDN = "https://cdn.jsdelivr.net/npm/dayjs@1.11.10/dayjs.min.js"

# 默认调色板（ECharts 风格）
_DEFAULT_PALETTE = [
    "#1677ff", "#52c41a", "#faad14", "#ff4d4f", "#722ed1",
    "#13c2c2", "#fa8c16", "#eb2f96", "#2f54eb", "#a0d911",
]

# 宽度映射 → CSS class
_WIDTH_CLASS = {
    "full": "chart-full",
    "half": "chart-half",
    "third": "chart-third",
    "two-thirds": "chart-two-thirds",
}


# ─────────────────────────────────────────────────────────────────────────────
# 主函数
# ─────────────────────────────────────────────────────────────────────────────

def build_report_html(
    spec: Dict[str, Any],
    report_id: str,
    refresh_token: str,
    api_base_url: str = "",
) -> str:
    """
    根据 spec 生成完整的自包含 HTML 报告文件内容。

    Args:
        spec:           报告规格 JSON（见模块文档）
        report_id:      数据库中的 Report.id
        refresh_token:  Report.refresh_token（供 HTML 内部调用刷新 API）
        api_base_url:   后端 API 前缀（如 "http://localhost:8000/api/v1"）

    Returns:
        HTML 字符串
    """
    title = spec.get("title", "数据分析报告")
    subtitle = spec.get("subtitle", "")
    theme = spec.get("theme", "light")
    palette = spec.get("color_palette", _DEFAULT_PALETTE)
    charts: List[Dict] = spec.get("charts", [])
    filters: List[Dict] = spec.get("filters", [])
    data: Dict[str, List] = spec.get("data", {})
    llm_summary: str = spec.get("llm_summary", "")
    include_summary: bool = spec.get("include_summary", False)

    # 判断是否需要 AntV G2 / D3.js
    needs_antv = any(c.get("chart_lib") == "antv_g2" for c in charts)
    needs_d3 = any(c.get("chart_lib") == "d3" for c in charts)

    # 生成各区域 HTML 片段
    filter_html = _render_filter_html(filters)
    chart_divs = _render_chart_divs(charts)
    summary_html = _render_summary_html(llm_summary, include_summary)

    # 序列化 JSON（注意 datetime 处理）
    # 注：JSON 中的 </script> 必须转义，防止 HTML 解析器提前关闭 script 标签
    def _safe_json(obj) -> str:
        return json.dumps(obj, ensure_ascii=False, default=str).replace("</", "<\\/")

    spec_json = _safe_json({
        "title": title,
        "subtitle": subtitle,
        "theme": theme,
        "palette": palette,
        "charts": charts,
        "filters": filters,
    })

    # 动态加载模式：保存模式不 bake-in 数据（页面加载时调 /data API）
    # 预览模式（report_id = "preview"）：仍注入数据，避免 API 调用失败
    is_preview = report_id == "preview"
    data_json = _safe_json(data) if is_preview else _safe_json({})

    # 计算默认参数（从 filter.default_days / default_value / binds 推导）
    default_params: Dict[str, Any] = {}
    if not is_preview:
        try:
            from backend.services.report_params_service import extract_default_params
            default_params = extract_default_params({"filters": filters})
        except Exception as _pe:
            logger.warning("[ReportBuilder] extract_default_params 失败: %s", _pe)
    default_params_json = _safe_json(default_params)

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{_esc(title)}</title>
<script src="{_ECHARTS_CDN}"></script>
<script src="{_DAYJS_CDN}"></script>
{f'<script src="{_ANTV_G2_CDN}"></script>' if needs_antv else ''}
{f'<script src="{_D3_CDN}"></script>' if needs_d3 else ''}
<style>
{_css(theme)}
</style>
</head>
<body class="theme-{theme}">

<!-- ── 报告头部 ── -->
<div class="report-header">
  <div class="header-left">
    <h1 class="report-title">{_esc(title)}</h1>
    {f'<p class="report-subtitle">{_esc(subtitle)}</p>' if subtitle else ''}
  </div>
  <div class="header-right">
    <span class="report-meta">生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}</span>
    <button class="btn-refresh" onclick="refreshAllData()" id="btn-refresh">
      ↻ 刷新数据
    </button>
    <button class="btn-export" onclick="window.print()">🖨 打印/导出PDF</button>
  </div>
</div>

{summary_html}

{f'<div class="filter-bar" id="filter-bar">{filter_html}</div>' if filters else ''}

<!-- ── 图表区域 ── -->
<div class="charts-grid" id="charts-grid">
{chart_divs}
</div>

<!-- ── 数据注入 + 初始化脚本 ── -->
<script>
// ── 报告配置（由 Python 注入）──────────────────────────────────────────────
const REPORT_SPEC      = {spec_json};
const REPORT_DATA      = {data_json};        // 预览模式有数据；保存模式为 {{}}
const REPORT_ID        = "{report_id}";
const REFRESH_TOKEN    = "{refresh_token}";
const API_BASE         = "{api_base_url.rstrip('/')}";
const PALETTE          = REPORT_SPEC.palette;
const _DEFAULT_PARAMS  = {default_params_json};  // 从 filter 默认值计算的 SQL 参数

// ── 图表实例注册表 ──────────────────────────────────────────────────────────
const _charts    = {{}};   // id → ECharts instance
const _chartData = {{}};   // id → current data array (运行时缓存)

// ── 图表控件脚本通过 window 访问（const 不挂 window，需显式暴露）──────────
window.REPORT_SPEC     = REPORT_SPEC;
window.REPORT_ID       = REPORT_ID;
window.REFRESH_TOKEN   = REFRESH_TOKEN;
window.API_BASE        = API_BASE;
window._charts         = _charts;
window._chartData      = _chartData;
window._DEFAULT_PARAMS = _DEFAULT_PARAMS;

// ── 筛选器当前值 ──────────────────────────────────────────────────────────
const _filterValues = {{}};

{_js_engine()}
</script>
</body>
</html>"""
    return html


# ─────────────────────────────────────────────────────────────────────────────
# HTML 片段生成
# ─────────────────────────────────────────────────────────────────────────────

def _render_chart_divs(charts: List[Dict]) -> str:
    parts = []
    for c in charts:
        cid = c.get("id", f"c{len(parts)}")
        title = c.get("title", "")
        width_cls = _WIDTH_CLASS.get(c.get("width", "half"), "chart-half")
        height = c.get("height", 320)
        chart_lib = c.get("chart_lib", "echarts")
        parts.append(f"""  <div class="chart-card {width_cls}" id="card-{cid}">
    <div class="chart-card-title">{_esc(title)}</div>
    <div class="chart-loading" id="loading-{cid}">加载中…</div>
    <div class="chart-container {chart_lib}-chart" id="{cid}" style="height:{height}px"></div>
  </div>""")
    return "\n".join(parts)


def _render_filter_html(filters: List[Dict]) -> str:
    parts = []
    for f in filters:
        fid = f.get("id", "")
        label = f.get("label", "")
        ftype = f.get("type", "select")
        placeholder = f.get("placeholder", "请选择…")

        if ftype == "date_range":
            default_days = f.get("default_days", 30)
            parts.append(f"""
  <div class="filter-item">
    <label class="filter-label">{_esc(label)}</label>
    <div class="date-range-inputs">
      <input type="date" class="filter-date" id="filter-{fid}-start"
        onchange="onFilterChange('{fid}')" />
      <span class="date-sep">至</span>
      <input type="date" class="filter-date" id="filter-{fid}-end"
        onchange="onFilterChange('{fid}')" />
    </div>
    <button class="btn-quick-range" onclick="setDateRange('{fid}', 7)">近7天</button>
    <button class="btn-quick-range" onclick="setDateRange('{fid}', 30)">近30天</button>
    <button class="btn-quick-range" onclick="setDateRange('{fid}', 90)">近90天</button>
  </div>
  <script>
    // 初始化日期范围默认值
    (function() {{
      const end = dayjs().format('YYYY-MM-DD');
      const start = dayjs().subtract({default_days}, 'day').format('YYYY-MM-DD');
      document.getElementById('filter-{fid}-start').value = start;
      document.getElementById('filter-{fid}-end').value = end;
      _filterValues['{fid}'] = {{start, end}};
    }})();
  </script>""")

        elif ftype in ("select", "multi_select"):
            options = f.get("options", [])
            multiple = "multiple" if ftype == "multi_select" else ""
            opts_html = "".join(
                f'<option value="{_esc(str(o))}">{_esc(str(o))}</option>'
                for o in options
            )
            parts.append(f"""
  <div class="filter-item">
    <label class="filter-label">{_esc(label)}</label>
    <select class="filter-select" id="filter-{fid}" {multiple}
      onchange="onFilterChange('{fid}')">
      <option value="">全部</option>
      {opts_html}
    </select>
  </div>""")

        elif ftype == "radio":
            options = f.get("options", [])
            radios = "".join(
                f'<label class="radio-label"><input type="radio" name="filter-{fid}" '
                f'value="{_esc(str(o))}" onchange="onFilterChange(\'{fid}\')">{_esc(str(o))}</label>'
                for o in options
            )
            parts.append(f"""
  <div class="filter-item">
    <label class="filter-label">{_esc(label)}</label>
    <div class="radio-group">{radios}</div>
  </div>""")

    return "\n".join(parts)


def _render_summary_html(llm_summary: str, include_summary: bool) -> str:
    if not include_summary:
        return ""
    status_cls = "summary-loading" if not llm_summary else ""
    content = _esc(llm_summary) if llm_summary else "分析总结生成中，请稍候…"
    return f"""
<div class="summary-section" id="summary-section">
  <div class="summary-header">
    <span class="summary-icon">📊</span>
    <span class="summary-title">智能分析总结</span>
    <span class="summary-badge">AI 生成</span>
  </div>
  <div class="summary-body {status_cls}" id="summary-body">{content}</div>
</div>"""


# ─────────────────────────────────────────────────────────────────────────────
# JavaScript 引擎（所有图表渲染逻辑内联到 HTML）
# ─────────────────────────────────────────────────────────────────────────────

def _js_engine() -> str:
    """返回完整的 JS 运行时，负责所有图表渲染、筛选器联动和数据刷新。"""
    return r"""
// ═══════════════════════════════════════════════════════════════════════════
// 初始化：动态加载模式
// ═══════════════════════════════════════════════════════════════════════════
window.addEventListener('DOMContentLoaded', () => {
  // 预创建图表容器（ECharts 实例，暂无数据）
  REPORT_SPEC.charts.forEach(spec => {
    const el = document.getElementById(spec.id);
    if (!el) return;
    const lib = (spec.chart_lib || 'echarts').toLowerCase();
    if (lib === 'echarts' || lib === 'llm_custom' || !lib) {
      const chart = echarts.init(
        el, REPORT_SPEC.theme === 'dark' ? 'dark' : null, {renderer: 'canvas'}
      );
      _charts[spec.id] = chart;
    }
  });

  if (REPORT_ID && REPORT_ID !== 'preview') {
    // 保存模式：动态加载数据
    _loadData(_DEFAULT_PARAMS);
  } else {
    // 预览模式：直接渲染 baked-in 数据
    for (const [id, rows] of Object.entries(REPORT_DATA)) {
      _chartData[id] = rows;
    }
    REPORT_SPEC.charts.forEach(spec => initChart(spec));
  }

  // 响应式
  window.addEventListener('resize', debounce(() => {
    Object.values(_charts).forEach(c => c && c.resize && c.resize());
  }, 250));
});

// ═══════════════════════════════════════════════════════════════════════════
// 动态数据加载（参数化 SQL 查询）
// ═══════════════════════════════════════════════════════════════════════════
async function _loadData(params) {
  const btn = document.getElementById('btn-refresh');
  if (btn) { btn.disabled = true; btn.textContent = '加载中…'; }

  // 显示每个图表的 loading 状态
  REPORT_SPEC.charts.forEach(spec => _showChartLoading(spec.id));

  // 构造 URL：token + 各 SQL 参数作为独立 query 参数
  const urlParams = new URLSearchParams({ token: REFRESH_TOKEN });
  for (const [k, v] of Object.entries(params || {})) {
    if (Array.isArray(v)) {
      v.forEach(vi => urlParams.append(k, vi));
    } else if (v !== null && v !== undefined && v !== '') {
      urlParams.set(k, String(v));
    }
  }

  try {
    const url = `${API_BASE}/reports/${REPORT_ID}/data?${urlParams}`;
    const res = await fetch(url, { headers: { 'Accept': 'application/json' } });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const json = await res.json();
    if (!json.success) throw new Error(json.error || '加载失败');

    // 更新数据缓存并重绘图表
    const newData = json.data || {};
    for (const [id, rows] of Object.entries(newData)) {
      REPORT_DATA[id] = rows;
      _chartData[id] = rows;
    }
    REPORT_SPEC.charts.forEach(spec => {
      _hideChartLoading(spec.id);
      _clearChartError(spec.id);
      if (_charts[spec.id]) {
        _charts[spec.id].setOption(buildEChartsOption(spec, REPORT_DATA[spec.id] || []), true);
      } else {
        initChart(spec);
      }
    });

    // 查询出错的图表显示错误提示（其他图表正常渲染）
    const errors = json.errors || {};
    for (const [id, msg] of Object.entries(errors)) {
      _showChartError(id, msg);
    }

    // 更新智能总结（如果有）
    if (json.llm_summary) {
      const el = document.getElementById('summary-body');
      if (el) { el.textContent = json.llm_summary; el.classList.remove('summary-loading'); }
    }

  } catch(e) {
    REPORT_SPEC.charts.forEach(spec => {
      _hideChartLoading(spec.id);
      _showChartError(spec.id, e.message);
    });
    showToast('加载失败: ' + e.message, 'error');
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = '↻ 刷新数据'; }
  }
}

const _debouncedLoadData = debounce(_loadData, 300);

// 从当前筛选器值 + binds 映射，构造 SQL 参数对象
function _currentParams() {
  const p = Object.assign({}, _DEFAULT_PARAMS);
  for (const fSpec of (REPORT_SPEC.filters || [])) {
    const binds = fSpec.binds || {};
    if (!binds || Object.keys(binds).length === 0) continue;
    const val = _filterValues[fSpec.id];
    if (!val) continue;
    if (fSpec.type === 'date_range' && typeof val === 'object') {
      if (binds.start && val.start) p[binds.start] = val.start;
      if (binds.end   && val.end)   p[binds.end]   = val.end;
    } else if (fSpec.type === 'multi_select' && Array.isArray(val)) {
      if (binds.values) p[binds.values] = val;
    } else {
      if (binds.value && val) p[binds.value] = String(val);
    }
  }
  return p;
}

// Per-chart loading/error 状态管理
function _showChartLoading(id) {
  const el = document.getElementById(`loading-${id}`);
  if (el) { el.style.display = 'flex'; el.textContent = '加载中…'; }
  _clearChartError(id);
}
function _hideChartLoading(id) {
  const el = document.getElementById(`loading-${id}`);
  if (el) el.style.display = 'none';
}
function _showChartError(id, msg) {
  _hideChartLoading(id);
  const card = document.getElementById(`card-${id}`);
  if (!card) return;
  let errEl = document.getElementById(`error-${id}`);
  if (!errEl) {
    errEl = document.createElement('div');
    errEl.id = `error-${id}`;
    errEl.className = 'chart-error';
    card.appendChild(errEl);
  }
  errEl.textContent = '查询失败: ' + msg;
}
function _clearChartError(id) {
  const errEl = document.getElementById(`error-${id}`);
  if (errEl) errEl.remove();
}

// ═══════════════════════════════════════════════════════════════════════════
// 图表初始化
// ═══════════════════════════════════════════════════════════════════════════
function initChart(spec) {
  const el = document.getElementById(spec.id);
  if (!el) return;
  hideLoading(spec.id);

  const lib = (spec.chart_lib || 'echarts').toLowerCase();
  const data = _chartData[spec.id] || [];

  if (lib === 'kpi' || spec.chart_type === 'kpi_card') {
    renderKpi(spec, data, el);
    return;
  }
  if (lib === 'antv_g2') {
    renderAntvG2(spec, data, el);
    return;
  }
  if (lib === 'd3') {
    renderD3(spec, data, el);
    return;
  }
  // default: ECharts
  const chart = echarts.init(el, REPORT_SPEC.theme === 'dark' ? 'dark' : null, {renderer: 'canvas'});
  _charts[spec.id] = chart;
  const option = buildEChartsOption(spec, data);
  chart.setOption(option);
}

// ═══════════════════════════════════════════════════════════════════════════
// ECharts option 构建器（支持 15+ 图表类型）
// ═══════════════════════════════════════════════════════════════════════════
function buildEChartsOption(spec, data) {
  let option;
  switch (spec.chart_type) {
    case 'line':           option = buildLine(spec, data); break;
    case 'area':           option = buildArea(spec, data); break;
    case 'bar':            option = buildBar(spec, data, false); break;
    case 'bar_horizontal': option = buildBar(spec, data, true); break;
    case 'pie':            option = buildPie(spec, data, false); break;
    case 'donut':          option = buildPie(spec, data, true); break;
    case 'scatter':        option = buildScatter(spec, data); break;
    case 'heatmap':        option = buildHeatmap(spec, data); break;
    case 'funnel':         option = buildFunnel(spec, data); break;
    case 'gauge':          option = buildGauge(spec, data); break;
    case 'radar':          option = buildRadar(spec, data); break;
    case 'treemap':        option = buildTreemap(spec, data); break;
    case 'sankey':         option = buildSankey(spec, data); break;
    case 'dual_axis':      option = buildDualAxis(spec, data); break;
    case 'waterfall':      option = buildWaterfall(spec, data); break;
    case 'llm_custom':     option = evalLlmOption(spec, data); break;
    default:               option = buildLine(spec, data);
  }
  // 合并用户自定义 override
  if (spec.echarts_override && typeof spec.echarts_override === 'object') {
    deepMerge(option, spec.echarts_override);
  }
  return option;
}

// ── 折线图 ─────────────────────────────────────────────────────────────────
function buildLine(spec, data) {
  const { xVals, series } = extractXYSeries(spec, data, 'line');
  return {
    color: PALETTE,
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    legend: { show: series.length > 1, bottom: 0 },
    grid: { top: 40, right: 30, bottom: series.length > 1 ? 40 : 20, left: 60, containLabel: true },
    xAxis: { type: 'category', data: xVals, axisLabel: { rotate: xVals.length > 12 ? 30 : 0 } },
    yAxis: { type: 'value', axisLabel: { formatter: v => formatVal(v, spec) } },
    series,
  };
}

// ── 面积图 ─────────────────────────────────────────────────────────────────
function buildArea(spec, data) {
  const opt = buildLine(spec, data);
  opt.series.forEach((s, i) => {
    s.areaStyle = { opacity: 0.25 };
    s.smooth = true;
  });
  return opt;
}

// ── 柱状图 ─────────────────────────────────────────────────────────────────
function buildBar(spec, data, horizontal) {
  const { xVals, series } = extractXYSeries(spec, data, 'bar');
  const cat = { type: 'category', data: xVals, axisLabel: { rotate: (!horizontal && xVals.length > 8) ? 30 : 0 } };
  const val = { type: 'value', axisLabel: { formatter: v => formatVal(v, spec) } };
  return {
    color: PALETTE,
    tooltip: { trigger: 'axis' },
    legend: { show: series.length > 1, bottom: 0 },
    grid: { top: 40, right: 30, bottom: series.length > 1 ? 40 : 20, left: 70, containLabel: true },
    xAxis: horizontal ? val : cat,
    yAxis: horizontal ? cat : val,
    series,
  };
}

// ── 饼图 / 环形图 ──────────────────────────────────────────────────────────
function buildPie(spec, data, isDonut) {
  const labelF = spec.label_field || spec.x_field;
  const valueF = spec.value_field || (spec.y_fields || [])[0];
  const pieData = data.map(row => ({ name: row[labelF], value: row[valueF] }));
  const radius = isDonut ? ['40%', '70%'] : '65%';
  return {
    color: PALETTE,
    tooltip: { trigger: 'item', formatter: '{b}: {c} ({d}%)' },
    legend: { orient: 'vertical', left: 10, top: 'center', type: 'scroll' },
    series: [{
      name: spec.title,
      type: 'pie',
      radius,
      center: ['60%', '50%'],
      data: pieData,
      label: { formatter: '{b}\n{d}%' },
      emphasis: { itemStyle: { shadowBlur: 10, shadowOffsetX: 0, shadowColor: 'rgba(0,0,0,.5)' } },
    }],
  };
}

// ── 散点图 ────────────────────────────────────────────────────────────────
function buildScatter(spec, data) {
  const xF = spec.x_field;
  const yF = (spec.y_fields || [])[0];
  return {
    color: PALETTE,
    tooltip: { trigger: 'item', formatter: p => `${p.name || ''}<br/>${xF}: ${p.value[0]}<br/>${yF}: ${p.value[1]}` },
    xAxis: { type: 'value', name: xF },
    yAxis: { type: 'value', name: yF },
    series: [{
      type: 'scatter',
      symbolSize: 8,
      data: data.map(r => [r[xF], r[yF]]),
    }],
  };
}

// ── 热力图（日历型）────────────────────────────────────────────────────────
function buildHeatmap(spec, data) {
  const xF = spec.x_field;
  const yF = (spec.y_fields || [])[0];
  const vals = data.map(r => r[yF]).filter(v => v != null);
  const minV = Math.min(...vals), maxV = Math.max(...vals);
  const hData = data.map(r => [r[xF], r[yF]]);
  return {
    color: PALETTE,
    tooltip: { position: 'top', formatter: p => `${p.data[0]}: ${formatVal(p.data[1], spec)}` },
    visualMap: { min: minV, max: maxV, calculable: true, orient: 'horizontal', left: 'center', bottom: 0 },
    calendar: { range: data.length > 0 ? data[0][xF].slice(0, 7) : '', top: 60 },
    series: [{ type: 'heatmap', coordinateSystem: 'calendar', data: hData }],
  };
}

// ── 漏斗图 ────────────────────────────────────────────────────────────────
function buildFunnel(spec, data) {
  const labelF = spec.label_field || spec.x_field;
  const valueF = spec.value_field || (spec.y_fields || [])[0];
  return {
    color: PALETTE,
    tooltip: { trigger: 'item', formatter: '{b}: {c}' },
    legend: { bottom: 0 },
    series: [{
      type: 'funnel', left: '10%', width: '80%', top: 20, bottom: 30,
      data: data.map(r => ({ name: r[labelF], value: r[valueF] })).sort((a, b) => b.value - a.value),
      label: { formatter: '{b}: {c}' },
    }],
  };
}

// ── 仪表盘 ────────────────────────────────────────────────────────────────
function buildGauge(spec, data) {
  const valueF = spec.value_field || (spec.y_fields || [])[0];
  const val = data.length > 0 ? Number(data[0][valueF]) : 0;
  const max = spec.gauge_max || 100;
  return {
    series: [{
      type: 'gauge', center: ['50%', '70%'], radius: '90%',
      startAngle: 200, endAngle: -20, min: 0, max,
      data: [{ value: val, name: spec.title }],
      progress: { show: true, width: 14 },
      pointer: { show: true },
      detail: { valueAnimation: true, fontSize: 24, offsetCenter: [0, '20%'],
        formatter: v => formatVal(v, spec) },
      axisLabel: { distance: 15, color: '#999', fontSize: 12 },
    }],
  };
}

// ── 雷达图 ────────────────────────────────────────────────────────────────
function buildRadar(spec, data) {
  const labelF = spec.label_field || spec.x_field;
  const indicators = data.map(r => ({ name: r[labelF], max: spec.radar_max || 100 }));
  const values = (spec.y_fields || []).map(f => data.map(r => r[f] ?? 0));
  return {
    color: PALETTE,
    tooltip: {},
    radar: { indicator: indicators },
    series: [{
      type: 'radar',
      data: values.map((v, i) => ({ value: v, name: (spec.series_names || {})[spec.y_fields[i]] || spec.y_fields[i] })),
    }],
  };
}

// ── 矩形树图 ──────────────────────────────────────────────────────────────
function buildTreemap(spec, data) {
  const labelF = spec.label_field || spec.x_field;
  const valueF = spec.value_field || (spec.y_fields || [])[0];
  return {
    color: PALETTE,
    tooltip: { formatter: p => `${p.name}: ${formatVal(p.value, spec)}` },
    series: [{
      type: 'treemap', roam: false, nodeClick: false,
      data: data.map(r => ({ name: r[labelF], value: r[valueF] })),
      label: { show: true, position: 'insideTopLeft', formatter: '{b}\n{c}' },
    }],
  };
}

// ── 桑基图 ────────────────────────────────────────────────────────────────
function buildSankey(spec, data) {
  // 期望数据格式: [{source, target, value}]
  const nodes = [...new Set(data.flatMap(r => [r.source, r.target]))].map(n => ({ name: n }));
  const links = data.map(r => ({ source: r.source, target: r.target, value: r.value }));
  return {
    color: PALETTE,
    tooltip: { trigger: 'item', triggerOn: 'mousemove' },
    series: [{ type: 'sankey', left: '5%', right: '15%', data: nodes, links,
      label: { color: '#333', fontSize: 11 }, lineStyle: { color: 'gradient', opacity: 0.4 } }],
  };
}

// ── 双轴图（柱 + 线）─────────────────────────────────────────────────────
function buildDualAxis(spec, data) {
  const xVals = data.map(r => r[spec.x_field]);
  const barFields = spec.bar_fields || [(spec.y_fields || [])[0]];
  const lineFields = spec.line_fields || [(spec.y_fields || [])[1]].filter(Boolean);
  const series = [
    ...barFields.map(f => ({
      name: (spec.series_names || {})[f] || f, type: 'bar', yAxisIndex: 0,
      data: data.map(r => r[f]), barMaxWidth: 40,
    })),
    ...lineFields.map(f => ({
      name: (spec.series_names || {})[f] || f, type: 'line', yAxisIndex: 1,
      smooth: true, data: data.map(r => r[f]), symbolSize: 5,
    })),
  ];
  return {
    color: PALETTE,
    tooltip: { trigger: 'axis', axisPointer: { type: 'cross' } },
    legend: { bottom: 0 },
    grid: { top: 40, right: 60, bottom: 40, left: 60, containLabel: true },
    xAxis: { type: 'category', data: xVals },
    yAxis: [
      { type: 'value', name: spec.y_axis_name || '', position: 'left' },
      { type: 'value', name: spec.y2_axis_name || '', position: 'right',
        axisLabel: { formatter: v => formatVal(v, spec) } },
    ],
    series,
  };
}

// ── 瀑布图 ────────────────────────────────────────────────────────────────
function buildWaterfall(spec, data) {
  const xVals = data.map(r => r[spec.x_field]);
  const values = data.map(r => Number(r[(spec.y_fields || [])[0]] || 0));
  let cumulative = 0;
  const helpers = [], bars = [];
  values.forEach(v => { helpers.push(cumulative < 0 ? cumulative : (cumulative + (v < 0 ? v : 0))); bars.push(Math.abs(v)); cumulative += v; });
  return {
    color: PALETTE,
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    xAxis: { type: 'category', data: xVals },
    yAxis: { type: 'value' },
    series: [
      { type: 'bar', stack: 'total', itemStyle: { borderColor: 'transparent', color: 'transparent' }, emphasis: { itemStyle: { borderColor: 'transparent', color: 'transparent' } }, data: helpers },
      { type: 'bar', stack: 'total', data: bars, itemStyle: { color: (p) => p.dataIndex > 0 && values[p.dataIndex] < 0 ? PALETTE[3] : PALETTE[0] } },
    ],
  };
}

// ── LLM 自定义图表（eval 安全沙盒）────────────────────────────────────────
function evalLlmOption(spec, data) {
  try {
    // llm_chart_js 应返回一个 ECharts option 对象或 AntV G2 / D3 初始化函数
    // 变量 spec, data, PALETTE 均可使用
    /* jshint evil: true */
    const fn = new Function('spec', 'data', 'PALETTE', 'echarts', spec.llm_chart_js || 'return {}');
    return fn(spec, data, PALETTE, window.echarts) || {};
  } catch(e) {
    console.error('[LLM custom chart]', e);
    return { title: { text: '自定义图表渲染失败: ' + e.message } };
  }
}

// ═══════════════════════════════════════════════════════════════════════════
// AntV G2 渲染器
// ═══════════════════════════════════════════════════════════════════════════
function renderAntvG2(spec, data, el) {
  if (typeof G2 === 'undefined') {
    el.innerHTML = '<div class="chart-error">AntV G2 未加载</div>'; return;
  }
  try {
    // 期望 spec.llm_chart_js 提供初始化逻辑（LLM 编写）
    if (spec.llm_chart_js) {
      const fn = new Function('G2', 'spec', 'data', 'el', spec.llm_chart_js);
      fn(G2, spec, data, el);
    } else {
      // 简单回退：折线图
      const chart = new G2.Chart({ container: el, autoFit: true });
      chart.data(data);
      chart.line().position(`${spec.x_field}*${(spec.y_fields||[])[0]}`);
      chart.render();
    }
  } catch(e) {
    el.innerHTML = `<div class="chart-error">AntV G2 渲染失败: ${e.message}</div>`;
  }
}

// ═══════════════════════════════════════════════════════════════════════════
// D3.js 渲染器（LLM 提供代码）
// ═══════════════════════════════════════════════════════════════════════════
function renderD3(spec, data, el) {
  if (typeof d3 === 'undefined') {
    el.innerHTML = '<div class="chart-error">D3.js 未加载</div>'; return;
  }
  try {
    const fn = new Function('d3', 'spec', 'data', 'el', 'PALETTE', spec.llm_chart_js || '');
    fn(d3, spec, data, el, PALETTE);
  } catch(e) {
    el.innerHTML = `<div class="chart-error">D3 渲染失败: ${e.message}</div>`;
  }
}

// ═══════════════════════════════════════════════════════════════════════════
// KPI 卡片
// ═══════════════════════════════════════════════════════════════════════════
function renderKpi(spec, data, el) {
  const valueF = spec.value_field || (spec.y_fields || [])[0];
  const val = data.length > 0 ? data[0][valueF] : '--';
  const unit = spec.kpi_unit || '';
  const trend = spec.kpi_trend || '';
  const trendIcon = trend === 'up' ? '▲' : trend === 'down' ? '▼' : '';
  const trendColor = trend === 'up' ? '#52c41a' : trend === 'down' ? '#ff4d4f' : '#999';
  el.innerHTML = `
    <div class="kpi-card">
      <div class="kpi-value">${formatVal(val, spec)}<span class="kpi-unit">${unit}</span></div>
      <div class="kpi-trend" style="color:${trendColor}">${trendIcon} ${spec.kpi_diff || ''}</div>
    </div>`;
}

// ═══════════════════════════════════════════════════════════════════════════
// 辅助：提取 X 轴和 Series 数据（支持 series_field 分组）
// ═══════════════════════════════════════════════════════════════════════════
function extractXYSeries(spec, data, type) {
  const xF = spec.x_field;
  const yFields = spec.y_fields || [];
  const sField = spec.series_field;
  const names = spec.series_names || {};

  if (sField) {
    // 分组模式: series_field 的每个唯一值 → 一个 series
    const groups = {};
    data.forEach(r => { const g = r[sField]; if (!groups[g]) groups[g] = []; groups[g].push(r); });
    const xVals = [...new Set(data.map(r => r[xF]))];
    const yF = yFields[0];
    const series = Object.entries(groups).map(([g, rows]) => ({
      name: g, type, smooth: type === 'line',
      data: xVals.map(x => { const row = rows.find(r => r[xF] === x); return row ? row[yF] : null; }),
    }));
    return { xVals, series };
  }

  // 多字段模式
  const xVals = data.map(r => r[xF]);
  const series = yFields.map(f => ({
    name: names[f] || f, type, smooth: type === 'line',
    data: data.map(r => r[f] ?? null),
    symbolSize: type === 'line' ? 4 : undefined,
  }));
  return { xVals, series };
}

// ═══════════════════════════════════════════════════════════════════════════
// 值格式化
// ═══════════════════════════════════════════════════════════════════════════
function formatVal(v, spec) {
  if (v == null) return '--';
  const fmt = spec.value_format || {};
  const yField = (spec.y_fields || [])[0];
  const fmtType = fmt[yField] || fmt['*'] || 'number';
  switch (fmtType) {
    case 'percent': return (Number(v) * 100).toFixed(1) + '%';
    case 'percent_raw': return Number(v).toFixed(1) + '%';
    case 'currency': return '¥' + Number(v).toLocaleString('zh-CN', {minimumFractionDigits:2});
    case 'short':
      if (Math.abs(v) >= 1e8) return (v/1e8).toFixed(1) + '亿';
      if (Math.abs(v) >= 1e4) return (v/1e4).toFixed(1) + '万';
      return Number(v).toLocaleString('zh-CN');
    default: return Number(v).toLocaleString('zh-CN');
  }
}

// ═══════════════════════════════════════════════════════════════════════════
// 筛选器联动
// 有 binds 字段 → 重查后端（参数化）；无 binds → 客户端内存过滤（兼容旧格式）
// ═══════════════════════════════════════════════════════════════════════════
function onFilterChange(filterId) {
  const filterSpec = (REPORT_SPEC.filters || []).find(f => f.id === filterId);
  if (!filterSpec) return;
  const ftype = filterSpec.type;

  // 1. 读取当前筛选器值
  if (ftype === 'date_range') {
    const s = document.getElementById(`filter-${filterId}-start`)?.value;
    const e = document.getElementById(`filter-${filterId}-end`)?.value;
    _filterValues[filterId] = { start: s, end: e };
  } else if (ftype === 'multi_select') {
    const sel = document.getElementById(`filter-${filterId}`);
    _filterValues[filterId] = Array.from(sel.selectedOptions).map(o => o.value);
  } else {
    const el = document.getElementById(`filter-${filterId}`);
    _filterValues[filterId] = el ? el.value : null;
  }

  // 2. 路由：有 binds → 服务端重查；否则 → 客户端过滤
  const binds = filterSpec.binds || {};
  const isClientSide = filterSpec.client_side === true;
  const hasBinds = Object.keys(binds).length > 0;

  if (hasBinds && !isClientSide && REPORT_ID && REPORT_ID !== 'preview') {
    _debouncedLoadData(_currentParams());
  } else {
    const targets = filterSpec.target_charts || REPORT_SPEC.charts.map(c => c.id);
    targets.forEach(cid => applyFilterToChart(cid));
  }
}

function applyFilterToChart(chartId) {
  const spec = REPORT_SPEC.charts.find(c => c.id === chartId);
  if (!spec) return;
  let data = REPORT_DATA[chartId] || [];

  (REPORT_SPEC.filters || []).forEach(fSpec => {
    const val = _filterValues[fSpec.id];
    if (!val || (Array.isArray(val) && val.length === 0)) return;
    const targets = fSpec.target_charts || REPORT_SPEC.charts.map(c => c.id);
    if (!targets.includes(chartId)) return;
    const field = fSpec.data_field || fSpec.id;
    if (fSpec.type === 'date_range' && val.start && val.end) {
      data = data.filter(r => r[field] >= val.start && r[field] <= val.end);
    } else if (fSpec.type === 'multi_select' && Array.isArray(val)) {
      data = data.filter(r => val.includes(String(r[field])));
    } else if (val) {
      data = data.filter(r => String(r[field]) === String(val));
    }
  });

  const chart = _charts[chartId];
  if (chart) chart.setOption(buildEChartsOption(spec, data));
}

function setDateRange(filterId, days) {
  const end = dayjs().format('YYYY-MM-DD');
  const start = dayjs().subtract(days, 'day').format('YYYY-MM-DD');
  const startEl = document.getElementById(`filter-${filterId}-start`);
  const endEl = document.getElementById(`filter-${filterId}-end`);
  if (startEl) startEl.value = start;
  if (endEl) endEl.value = end;
  onFilterChange(filterId);
}

// ═══════════════════════════════════════════════════════════════════════════
// 刷新数据（使用当前筛选器参数重新查询）
// ═══════════════════════════════════════════════════════════════════════════
async function refreshAllData() {
  if (!REPORT_ID || REPORT_ID === 'preview') {
    alert('当前报告为预览模式，无法刷新数据'); return;
  }
  await _loadData(_currentParams());
  showToast('数据已更新');
}

// ═══════════════════════════════════════════════════════════════════════════
// 工具函数
// ═══════════════════════════════════════════════════════════════════════════
function hideLoading(id) {
  const el = document.getElementById(`loading-${id}`);
  if (el) el.style.display = 'none';
}

function showToast(msg, type='success') {
  const t = document.createElement('div');
  t.className = `toast toast-${type}`;
  t.textContent = msg;
  document.body.appendChild(t);
  setTimeout(() => t.remove(), 3000);
}

function debounce(fn, delay) {
  let timer;
  return (...args) => { clearTimeout(timer); timer = setTimeout(() => fn(...args), delay); };
}

function deepMerge(target, source) {
  for (const key of Object.keys(source)) {
    if (source[key] && typeof source[key] === 'object' && !Array.isArray(source[key])) {
      if (!target[key]) target[key] = {};
      deepMerge(target[key], source[key]);
    } else {
      target[key] = source[key];
    }
  }
}
"""


# ─────────────────────────────────────────────────────────────────────────────
# CSS
# ─────────────────────────────────────────────────────────────────────────────

def _css(theme: str = "light") -> str:
    is_dark = theme == "dark"
    bg = "#1a1a2e" if is_dark else "#f4f6fa"
    card_bg = "#16213e" if is_dark else "#ffffff"
    text = "#e0e0e0" if is_dark else "#1a1a2a"
    border = "#2a2a4a" if is_dark else "#e8ecf0"
    return f"""
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
  background: {bg}; color: {text}; min-height: 100vh; }}

.report-header {{
  display: flex; justify-content: space-between; align-items: flex-start;
  padding: 20px 28px 16px; background: {card_bg}; border-bottom: 1px solid {border};
  position: sticky; top: 0; z-index: 100; box-shadow: 0 2px 8px rgba(0,0,0,.08);
}}
.report-title {{ font-size: 22px; font-weight: 700; }}
.report-subtitle {{ font-size: 13px; color: #888; margin-top: 4px; }}
.header-right {{ display: flex; align-items: center; gap: 10px; flex-shrink: 0; }}
.report-meta {{ font-size: 12px; color: #aaa; }}

.btn-refresh, .btn-export, .btn-quick-range {{
  padding: 6px 14px; border-radius: 6px; border: 1px solid #1677ff;
  background: {'#1677ff' if not is_dark else 'transparent'}; color: {'#fff' if not is_dark else '#1677ff'};
  cursor: pointer; font-size: 13px; transition: .2s; white-space: nowrap;
}}
.btn-refresh:hover {{ background: #0958d9; color: #fff; }}
.btn-export {{ background: transparent; color: {'#595959' if not is_dark else '#aaa'}; border-color: {border}; }}
.btn-export:hover {{ background: {'#f0f0f0' if not is_dark else '#2a2a4a'}; }}
.btn-quick-range {{ border-color: {border}; background: transparent;
  color: {'#595959' if not is_dark else '#aaa'}; font-size: 12px; padding: 4px 10px; }}
.btn-quick-range:hover {{ border-color: #1677ff; color: #1677ff; }}

.summary-section {{
  margin: 16px 28px; padding: 16px 20px; background: {card_bg};
  border-radius: 10px; border: 1px solid {border}; border-left: 4px solid #1677ff;
}}
.summary-header {{ display: flex; align-items: center; gap: 8px; margin-bottom: 10px; }}
.summary-icon {{ font-size: 18px; }}
.summary-title {{ font-size: 15px; font-weight: 600; }}
.summary-badge {{
  font-size: 11px; background: #e6f4ff; color: #1677ff;
  border: 1px solid #91caff; border-radius: 4px; padding: 1px 7px;
}}
.summary-body {{ font-size: 14px; line-height: 1.8; color: {'#ccc' if is_dark else '#444'}; white-space: pre-wrap; }}
.summary-loading {{ color: #aaa; font-style: italic; }}

.filter-bar {{
  display: flex; flex-wrap: wrap; gap: 12px; align-items: flex-end;
  padding: 12px 28px; background: {card_bg}; border-bottom: 1px solid {border};
  margin-bottom: 8px;
}}
.filter-item {{ display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }}
.filter-label {{ font-size: 13px; font-weight: 500; white-space: nowrap; }}
.filter-select, .filter-date {{
  border: 1px solid {border}; border-radius: 6px; padding: 5px 10px;
  font-size: 13px; background: {bg}; color: {text}; outline: none;
}}
.filter-select:focus, .filter-date:focus {{ border-color: #1677ff; }}
.date-range-inputs {{ display: flex; align-items: center; gap: 6px; }}
.date-sep {{ color: #aaa; }}
.radio-group {{ display: flex; gap: 10px; }}
.radio-label {{ display: flex; align-items: center; gap: 4px; font-size: 13px; cursor: pointer; }}

.charts-grid {{
  display: flex; flex-wrap: wrap; gap: 16px; padding: 16px 28px 28px;
}}
.chart-card {{
  background: {card_bg}; border-radius: 10px; border: 1px solid {border};
  padding: 16px; box-shadow: 0 1px 4px rgba(0,0,0,.06); transition: box-shadow .2s;
}}
.chart-card:hover {{ box-shadow: 0 4px 16px rgba(0,0,0,.12); }}
.chart-full {{ flex: 0 0 100%; width: 100%; }}
.chart-half {{ flex: 1 1 calc(50% - 8px); min-width: 320px; }}
.chart-third {{ flex: 1 1 calc(33.33% - 11px); min-width: 260px; }}
.chart-two-thirds {{ flex: 1 1 calc(66.66% - 8px); min-width: 420px; }}
.chart-card-title {{ font-size: 14px; font-weight: 600; margin-bottom: 10px; color: {'#e0e0e0' if is_dark else '#262626'}; }}
.chart-loading {{ font-size: 13px; color: #aaa; padding: 40px 0; text-align: center; display: flex; align-items: center; justify-content: center; }}
.chart-container {{ width: 100%; }}
.chart-error {{ color: #ff4d4f; font-size: 13px; padding: 20px; text-align: center; }}

.kpi-card {{ display: flex; flex-direction: column; align-items: center; justify-content: center; height: 100%; }}
.kpi-value {{ font-size: 42px; font-weight: 700; color: #1677ff; }}
.kpi-unit {{ font-size: 18px; margin-left: 4px; }}
.kpi-trend {{ font-size: 14px; margin-top: 6px; }}

.toast {{
  position: fixed; bottom: 24px; right: 24px; padding: 10px 18px;
  border-radius: 8px; font-size: 14px; z-index: 9999; animation: fadeIn .3s;
}}
.toast-success {{ background: #f6ffed; border: 1px solid #b7eb8f; color: #52c41a; }}
.toast-error {{ background: #fff2f0; border: 1px solid #ffa39e; color: #ff4d4f; }}
@keyframes fadeIn {{ from {{ opacity:0; transform:translateY(10px) }} to {{ opacity:1; transform:translateY(0) }} }}

@media print {{
  .report-header .header-right {{ display: none; }}
  .filter-bar {{ display: none; }}
  .chart-card {{ break-inside: avoid; box-shadow: none; border: 1px solid #ddd; }}
  body {{ background: #fff; }}
}}
@media (max-width: 768px) {{
  .chart-half, .chart-third, .chart-two-thirds {{ flex: 0 0 100%; }}
  .report-header {{ flex-direction: column; gap: 10px; }}
}}"""


# ─────────────────────────────────────────────────────────────────────────────
# 辅助函数
# ─────────────────────────────────────────────────────────────────────────────

def _esc(s: Any) -> str:
    """HTML 转义（防止 XSS）。"""
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def generate_refresh_token() -> str:
    """生成 48 字节 URL-safe 随机令牌。"""
    return secrets.token_urlsafe(48)


# ─────────────────────────────────────────────────────────────────────────────
# LLM 总结生成（异步，调用 LLM Adapter）
# ─────────────────────────────────────────────────────────────────────────────

async def generate_llm_summary(
    spec: Dict[str, Any],
    llm_adapter,
    max_tokens: int = 600,
) -> str:
    """
    调用 LLM 为报告生成中文分析总结。

    Args:
        spec:        报告规格（含 charts 配置 + data 数据样本）
        llm_adapter: 项目 LLM adapter（需支持 chat_with_messages 或 chat 方法）
        max_tokens:  总结最大 token 数

    Returns:
        总结文字（200-500 字中文），失败时返回空字符串
    """
    try:
        # 构建数据摘要（每个图表前 5 行）
        data_preview = {}
        for cid, rows in spec.get("data", {}).items():
            data_preview[cid] = rows[:5]
        chart_titles = [c.get("title", c.get("id")) for c in spec.get("charts", [])]

        prompt = f"""你是一名数据分析师。以下是一份名为《{spec.get('title', '数据报告')}》的报告数据摘要，请生成简洁、专业的中文分析总结（200-500字）。

报告图表：{', '.join(chart_titles)}

数据摘要（每图前5行）：
{json.dumps(data_preview, ensure_ascii=False, indent=2, default=str)}

要求：
1. 指出关键数据趋势（高峰/低谷、环比变化等）
2. 点明业务含义或潜在问题
3. 给出简短结论或建议
4. 用简洁段落，避免使用 Markdown 标题
"""
        # 兼容不同 adapter 接口
        if hasattr(llm_adapter, "chat_with_messages"):
            resp = await llm_adapter.chat_with_messages(
                messages=[{"role": "user", "content": prompt}],
                max_tokens=max_tokens,
            )
            return resp.get("content", "") or ""
        elif hasattr(llm_adapter, "chat"):
            resp = await llm_adapter.chat(prompt, max_tokens=max_tokens)
            return resp or ""
        return ""
    except Exception as e:
        logger.warning("[ReportBuilder] LLM 总结生成失败: %s", e)
        return ""
