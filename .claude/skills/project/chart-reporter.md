---
name: chart-reporter
description: 多图表HTML报告生成 — ECharts/AntV/D3.js 交互式网页报告，支持筛选器、数据刷新、PDF/PPTX导出和LLM总结
version: 1.0
triggers:
  - 图表报告
  - 图表分析
  - 生成报告
  - 可视化报告
  - echarts
  - 折线图
  - 柱状图
  - 饼图
  - dashboard
  - 仪表盘
  - 数据大屏
  - html报告
  - 报告导出
  - pdf报告
  - pptx报告
  - 图表类
  - 多图表
  - 可视化分析
always_inject: false
---

# 图表报告生成技能

## 能力说明

调用 `POST /api/v1/reports/build` 生成包含多图表的自包含 HTML 报告页面，支持：
- **ECharts 5.x**（主力，15+图表类型）
- **AntV G2 4.x**（高级统计图）
- **D3.js v7**（自定义复杂布局）
- **llm_custom**（LLM编写渲染JS，无限扩展）
- 筛选器（日期范围、下拉、多选、单选）
- 刷新按钮（重查数据库获取最新数据）
- PDF / PPTX 导出
- LLM 生成总结文字

---

## 工作流程

1. **理解需求** — 明确所需图表类型、数据维度、筛选器需求
2. **查询数据** — 通过 ClickHouse MCP 工具执行 SQL 获取原始数据
3. **构建 spec** — 按以下 JSON 格式组装报告规格
4. **生成报告文件** — 将 spec 通过 `POST /api/v1/reports/build`（spec-only 模式，仅生成 HTML 文件，不自动入库）写入 `{username}/reports/` 目录
5. **回复用户** — 说明文件已生成，用户可通过对话消息中的「**预览**」查看报告，点击「**生成固定报表/报告**」按钮将其固定到数据管理中心

> ⚠️ **重要**：报告 HTML 生成后**不会自动出现在数据管理中心的报表/报告清单**中。
> 用户需在对话消息的文件模块中主动点击「**生成固定报表**」（无 LLM 总结）或「**生成固定报告**」（含 LLM 总结）按钮，才会加入管理清单。
> `include_summary: true` 的报告将作为「报告」固定，否则作为「报表」固定。

---

## Chart Spec JSON 格式

```json
{
  "title": "报告标题",
  "subtitle": "副标题（可选）",
  "theme": "light",
  "charts": [...],
  "filters": [...],
  "data": {"chart_id": [...]},
  "include_summary": false,
  "llm_summary": ""
}
```

### charts[] 字段

```json
{
  "id": "唯一ID（英文数字）",
  "chart_lib": "echarts",
  "chart_type": "line",
  "title": "图表标题",
  "sql": "SELECT ...",
  "connection_env": "sg",
  "x_field": "date",
  "y_fields": ["metric1", "metric2"],
  "series_field": "category（分组字段，可选）",
  "series_names": {"metric1": "显示名称"},
  "value_format": {"metric1": "percent_raw"},
  "width": "full",
  "height": 320
}
```

**chart_lib 选项：**
- `echarts` — 主力，覆盖绝大部分场景
- `antv_g2` — 高级统计（分面、violin等）
- `d3` — 自定义复杂交互
- `kpi_card` — 数字展示卡
- `llm_custom` — 传入 `llm_option_js` 字段，LLM写完整 ECharts option JS

**chart_type（echarts）：**
`line` | `bar` | `pie` | `scatter` | `area` | `heatmap` | `funnel` |
`gauge` | `radar` | `treemap` | `sankey` | `dual_axis` | `waterfall` | `kpi_card`

**width 选项：** `full`（100%）| `half`（50%）| `third`（33%）| `two_thirds`（66%）

**value_format 选项：**
- `percent` — 乘100显示为 `85.2%`
- `percent_raw` — 原值已是百分比，直接显示 `85.2%`
- `currency` — 加 ¥ 前缀
- `short` — 缩短显示（万/亿）
- `number` — 原始数字（默认）

### filters[] 字段

```json
{"id": "dr", "type": "date_range", "label": "时间", "default_days": 30, "data_field": "date"}
{"id": "env", "type": "select", "label": "环境", "options": ["sg", "idn"]}
{"id": "env", "type": "multi_select", "label": "环境", "options": ["sg", "idn"]}
{"id": "gran", "type": "radio", "label": "粒度", "options": ["日", "周", "月"]}
```

---

## API 调用示例

```python
# POST /api/v1/reports/build
{
  "spec": {
    "title": "外呼接通率分析",
    "subtitle": "2026年Q1",
    "theme": "light",
    "charts": [
      {
        "id": "trend",
        "chart_lib": "echarts",
        "chart_type": "line",
        "title": "日接通率趋势",
        "sql": "SELECT date, connect_rate FROM t_daily WHERE date >= today()-30",
        "connection_env": "sg",
        "x_field": "date",
        "y_fields": ["connect_rate"],
        "value_format": {"connect_rate": "percent_raw"},
        "width": "full"
      }
    ],
    "filters": [
      {"id": "dr", "type": "date_range", "label": "时间", "default_days": 30, "data_field": "date"}
    ],
    "data": {
      "trend": [{"date": "2026-03-01", "connect_rate": 85.2}]
    },
    "include_summary": true
  }
}

# 响应：
{
  "success": true,
  "data": {
    "report_id": "uuid",
    "file_path": "customer_data/superadmin/reports/xxx.html",
    "refresh_token": "...",
    "summary_status": "generating"
  }
}
```

---

## 图表库选择指南

| 场景 | 推荐 chart_lib | chart_type |
|------|---------------|------------|
| 时间序列趋势 | echarts | line / area |
| 分组对比 | echarts | bar |
| 占比分析 | echarts | pie |
| 漏斗转化 | echarts | funnel |
| 多指标雷达 | echarts | radar |
| 热力矩阵 | echarts | heatmap |
| 流量流向 | echarts | sankey |
| 双轴指标 | echarts | dual_axis |
| 瀑布增减 | echarts | waterfall |
| 地图可视化 | llm_custom | — |
| 分面对比 | antv_g2 | — |
| 自定义力导向图 | d3 | — |
| 核心KPI数字 | kpi_card | — |
| 超出内置能力 | llm_custom | — |

---

## llm_custom 示例

当内置图表类型不满足需求时，可使用 `llm_custom` 并提供完整的 ECharts option JS 代码：

```json
{
  "id": "custom1",
  "chart_lib": "echarts",
  "chart_type": "llm_custom",
  "title": "自定义图表",
  "width": "full",
  "llm_option_js": "return { tooltip: {}, xAxis: {data: DATA.map(r=>r.x)}, yAxis: {}, series: [{type:'bar', data: DATA.map(r=>r.v)}] };"
}
```

`llm_option_js` 中可使用变量 `DATA`（当前图表数据数组）和 `FILTER`（当前筛选器状态）。

---

## 注意事项

- SQL 中的时间筛选字段名需与 `filters[].data_field` 一致，客户端刷新时会替换日期参数
- `data` 字典中的 chart_id 必须与 `charts[].id` 一一对应
- 调用接口前先用 ClickHouse MCP 工具执行 SQL 验证数据结构，再组装 `data`
- 报告 HTML 文件保存在 `customer_data/{username}/reports/` 目录下
- 生成后前端显示「预览」「下载」「生成固定报表/报告」三个按钮
- 「生成固定报表/报告」须用户主动点击，点后才进入数据管理中心清单；`include_summary:true` 入「报告」清单，否则入「报表」清单
- 每个 `charts[]` 条目的 `sql` 字段务必填写真实可重现图表数据的 SQL；系统会在报告预览页每个图表右上角注入「⋮」菜单，其中「View Query」功能直接读取该字段展示给用户，「Force Refresh」功能重新执行该 SQL 更新数据，`sql` 为空时两项功能降级处理
