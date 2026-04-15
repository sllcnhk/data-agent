---
name: update-report
description: 修改/更新已生成的图表报表（仅在数据管理中心 Co-pilot/Pilot 场景下使用，不适用于生成新报表）
triggers:
  - 修改这个报表
  - 更新这个报表
  - 修改这个图表
  - 更新这个图表
  - 调整这个图表
  - 把这个图表改
  - 改为面积
  - 改为折线
  - 改为柱状
  - 不平滑
  - 堆积
  - smooth
  - 改主题
  - 修改颜色
  - 改一下时间范围
  - 换一种图表类型
  - 改成深色
  - 改成浅色
  - echarts_override
always_inject: false
---

# 报表修改助手 (update-report)

## ⚠️ 第零优先级规则：Pilot 上下文检测（最先执行）

**在做任何事之前，先检查 system prompt 中是否包含 `report_id` 和 `refresh_token`。**

### 情形 A：system prompt 中【有】report_id 和 refresh_token
→ 你正在 **数据管理中心 Co-pilot** 模式下运行，当前已绑定一个具体的报表，正常执行后续指令。

### 情形 B：system prompt 中【没有】report_id 或 refresh_token

根据用户意图分两种处理方式：

**B-1：用户想修改/更新某个已有报表**（如"把这个图表改成面积图"、"修改这个报表的颜色"）
→ 回复："当前对话未绑定报表，无法直接修改。请前往「数据管理中心 → 报表清单」，点击对应报表的 **AI Pilot** 按钮，进入专属对话后再发送修改指令。"
→ **严禁**向用户索要 report_id 或 token；**严禁**猜测 report_id；**严禁**调用任何 `report__` MCP 工具。

**B-2：用户想生成全新的报表**（如"帮我生成各环境接通率报表"、"做一个柱状图"）
→ **忽略本技能的修改指令**，回归正常报表生成流程：
  1. 调用 ClickHouse 工具查询数据
  2. 用 `filesystem__write_file` 将完整 HTML 写入文件，路径必须以 `{CURRENT_USER}/reports/` 开头
  3. **严禁**调用任何 `report__` MCP 工具（那些只用于 Pilot 修改已有报表）

> **意图判断**：说"修改/调整/更新**这个**报表"→ B-1；说"生成/创建/做一个报表"→ B-2。

---

## ⚠️ 最高优先级规则：图表数量守恒

**每次修改报表后，图表数量必须与修改前完全一致（除非用户明确要求添加或删除图表）。**

违反此规则会导致用户的其他图表被永久删除，这是不可接受的数据损坏行为。

---

## 两种修改模式

> **重要**：使用 MCP 工具直接操作，无需 HTTP 调用权限。
> report_id 和 refresh_token（即 token 参数）均在系统提示（system prompt）中。

### 模式 A：修改单个图表（优先使用）

当用户只想改某一个图表的**样式、类型、SQL、颜色**等属性时，
调用 **`report__update_single_chart`** MCP 工具：

```json
{
  "report_id": "<来自 system prompt 的 report_id>",
  "token": "<来自 system prompt 的 refresh_token>",
  "chart_id": "c1",
  "chart_patch": {
    "chart_type": "area",
    "echarts_override": {
      "series": [{ "smooth": false, "stack": "total", "areaStyle": {} }]
    }
  }
}
```

**优点**：后端 merge 操作，结构上不可能误删其他图表。只传需要改动的字段。

### 模式 B：添加/删除图表 或 批量修改

只有在以下情况才使用全量 spec 工具：
- 用户明确要求添加新图表
- 用户明确要求删除某个图表
- 同时修改 3 个以上图表
- 修改报表标题/主题（theme）

先调用 **`report__get_spec`** 获取完整现有 spec，再调用 **`report__update_spec`**：

```json
{
  "report_id": "<来自 system prompt 的 report_id>",
  "token": "<来自 system prompt 的 refresh_token>",
  "spec": {
    "title": "报表标题",
    "theme": "light",
    "charts": [ /* 必须包含所有图表 */ ],
    "filters": [],
    "data_sources": [],
    "data": {}
  }
}
```

**严禁**：`spec.charts` 数组必须包含原有的**所有**图表，
缺少任何图表将导致其被永久删除。

---

## 操作流程

1. **确认当前图表列表** — 从 system prompt 中读取图表列表（已含 id、title、type）
2. **理解用户意图** — 判断是「改单图」（模式 A）还是「改结构」（模式 B）
3. **向用户确认改动** — 简洁说明：改哪个图表（title + id）、改什么内容
4. **调用对应 MCP 工具** — 按模式 A 或模式 B，传入 report_id 和 token
5. **告知用户结果** — 说明改了什么，提示「报表已更新，请查看左侧预览」

---

## `echarts_override.series` 正确使用规范

> ⚠️ **关键约束**：`echarts_override.series` 是**样式模板**，不是数据数组。  
> 系统会把 `series[0]` 的样式属性应用到每一个数据驱动的 series 上，保留其 `name` 和 `data`。  
> **禁止**在模板 series 中设置 `data` 字段 — 设置了也会被忽略，且会令人困惑。

**正确写法**（只含样式属性）：
```json
"echarts_override": {
  "series": [{ "smooth": false, "stack": "total", "areaStyle": { "opacity": 0.75 }, "lineStyle": { "width": 1.5 }, "symbol": "none" }]
}
```

**错误写法**（含 data，会被丢弃）：
```json
"echarts_override": {
  "series": [{ "type": "line", "data": [1, 2, 3] }]  ← data 无效，不要写
}
```

---

## 常见修改示例（模式 A）

| 用户说 | 你做什么 |
|---|---|
| "把 X 图表改为面积堆积不平滑" | 模式 A：`chart_type: "area"`，`echarts_override.series[0]: {smooth: false, stack: "total", areaStyle: {opacity: 0.75}}` |
| "X 图的折线改为不圆滑" | 模式 A：`echarts_override.series[0]: {smooth: false}` |
| "时间范围改为近30天" | 模式 A：只修改该图表 `sql` 字段的 WHERE 条件 |
| "X 图改为深色" | 模式 A：`echarts_override.color: [...]`（不要动 series） |

## 常见修改示例（模式 B）

| 用户说 | 你做什么 |
|---|---|
| "添加一个饼图" | 模式 B：在 charts 数组末尾追加新 chart，保留所有已有图表 |
| "删除第三个图表" | 模式 B：从 charts 中移除指定图表，保留其他所有图表 |
| "改成深色主题" | 模式 B：`theme: "dark"`，保留全部图表 |
| "主标题改为 XX" | 模式 B：`title: "XX"`，保留全部图表 |

---

## 注意事项

- SQL 修改时保持原有的 `connection_env` 不变（除非用户明确要改）
- 如果 system prompt 中没有 `report_id` 或 `refresh_token`，说明 Co-pilot 未绑定报表，提示用户"请先在报表清单中打开一个报表，再使用 AI 助手"
- 修改前**必须**向用户确认图表 title 和 id，避免改错图表
- MCP 工具调用失败时，将工具返回的 `error` 字段内容告知用户，不要假装成功
- `echarts_override.series` 只能包含**样式属性**（smooth/areaStyle/lineStyle/stack/symbol/type 等），**禁止设置 data**——数据由后端 SQL 动态加载，写 data 无效且会引起误解

---

## 参数化报表修改指南

### 识别参数化报表

**判断标志**：system prompt 中：
- 图表列表含 `★参数化` 标注
- 出现"参数化查询说明"段落
- `图表配置（详细）`的 sql 字段含 `{{ }}` 语法

### 参数化报表的修改规则

| 用户说 | 正确做法 | 禁止做法 |
|---|---|---|
| "默认显示近7天数据" | 模式 B：`report__update_spec`，将 filter 的 `default_days` 改为 `7` | 把 SQL 中 `{{ date_start }}` 改为硬编码日期 |
| "添加企业维度筛选" | 模式 B：在 spec.filters 中新增 select filter + binds，在 SQL 中添加 `{% if enterprise_id %}AND enterprise_id='{{ enterprise_id }}'{% endif %}` | 只改 SQL 不加 filter |
| "把折线图改成面积图" | 模式 A：`chart_patch: {"chart_type": "area"}` | 无 |
| "修改查询时间字段" | 模式 A：更新 chart.sql，保持 `{{ date_start }}` 变量不变，仅换字段名 | 替换成固定日期 |

### 修改 filter 默认范围（模式 B 示例）

```json
// 调用 report__update_spec，spec.filters 中修改 default_days
{
  "id": "date_range",
  "type": "date_range",
  "label": "时间范围",
  "default_days": 7,
  "binds": { "start": "date_start", "end": "date_end" }
}
```

### 重要原则

- **参数化 SQL 中的 `{{ variable }}` 绝对不可替换为硬编码值**
- 若用户要求"固定查询 2025 年全年"，应改 filter 的 `default_value`（startdate/enddate），而不改 SQL
- 新增图表时，SQL 中**必须**使用与现有 filter binds 一致的变量名

---

## 自由格式 HTML 报表的处理方式

当 system prompt 中出现以下情况时，说明当前报表是**自由格式 HTML**（非结构化 spec 生成），图表无法通过 MCP 工具直接编辑：

- `图表数量：0` 且存在 `报表 HTML 内容摘要` 部分
- system prompt 提示"该报表为自由格式"

**此时的正确处理流程**：

1. **先理解报表内容**：从 `报表 HTML 内容摘要` 读取报表标题、图表名称、数据结构
2. **告知用户限制**：说明"此报表为自由格式 HTML，AI Pilot 暂时无法直接修改图表数据"
3. **提供两个选项**：
   - **选项 A**：调用 `report__get_spec` 工具查看系统是否已识别到图表结构
   - **选项 B**：建议用户点击报表详情页的「重建规格」按钮，或通知管理员调用 `POST /reports/{id}/rebuild-spec` 接口，待规格重建后重新打开 Pilot
4. **不要**尝试通过 MCP 工具修改自由格式报表，会因 spec 为空而失败

**若 `report__get_spec` 返回了图表列表**：说明系统已从 HTML 提取到结构，可正常按模式 A/B 操作。
