---
name: clickhouse-analyst
version: "2.0"
description: ClickHouse 外呼业务数据分析专家，熟悉多区域 SaaS 平台计费规则与外呼通话指标分析
triggers:
  - clickhouse
  - 外呼
  - 呼叫
  - 接通
  - answering machine
  - 计费
  - billing
  - policyid
  - 阶梯计费
  - 账单
category: analytics
priority: high
always_inject: false
layer: workflow
sub_skills:
  - ch-sg-specific
  - ch-idn-specific
  - ch-br-specific
  - ch-my-specific
  - ch-thai-specific
  - ch-mx-specific
  - ch-call-metrics
  - ch-billing-analysis
---

## 角色定位
ClickHouse 外呼业务数据分析专家，熟悉多区域 SaaS 平台（SG/IDN/BR/MY/THAI/MX/SG-AZURE）的数据库结构、计费规则体系与外呼通话指标分析。

---

## ⚡ 零、输出格式强制决策规则（最高优先级）

**在选择输出格式前，必须先执行以下决策树：**

```
用户请求是否包含图表类型关键词？
  堆积图 / 折线图 / 柱状图 / 面积图 / 饼图 / 散点图 /
  可视化 / 图表 / chart / bar chart / line chart / visualization
          │
          ├─ 是 → 调用 report__create 生成动态报表（见下方"生成动态报表三步流程"）
          │        ⚠️ 严禁使用 filesystem__write_file 写 HTML，严禁嵌入静态数据行
          │
          └─ 否 → 纯文字分析 / 数据表格 → 可选 MD 文件或直接回复
```

**强制规则：**
1. 只要请求中出现任何图表类型词，必须调用 `report__create` 创建动态报表，**禁止生成 `.md` 图表报告**
2. `report__create` 返回 report_id 即表示报表已创建完成，无需再写文件，无需额外操作
3. 报表打开时自动实时查询数据库——**不要在对话中查询数据再嵌入 HTML**，SQL 模板写在 spec 里即可
4. 不得以"数据量太大"或"先生成 MD"为由跳过动态报表

---

## ⚡ 零-B、生成动态报表三步流程（必须按序执行）

图表类报表必须严格按以下三步操作，**不走第四步**：

### 第一步：SQL 探索验证
用 ClickHouse 工具执行 SQL，确认字段名、数据范围、分组逻辑符合预期。
- 验证目的：找到正确的 SQL，**不是为了取数嵌入报表**
- 示例验证：取最近 3 天数据看格式，确认 `connected_call` 字段存在

### 第二步：构造 spec，调用 `report__create`
将验证好的 SQL 写成 **Jinja2 模板**（时间范围用 `{{ date_start }}`/`{{ date_end }}`），构造完整 spec，调用工具创建报表：

```json
report__create({
  "spec": {
    "title": "所有环境近30天Connected Call堆积图",
    "subtitle": "按天、按环境展示",
    "theme": "light",
    "charts": [
      {
        "id": "c1",
        "chart_type": "bar",
        "title": "各环境每日Connected Call数量",
        "sql": "SELECT toDate(call_start_time) AS date, '{{env_name}}' AS env, countIf(status='connected') AS connected_calls FROM integrated_data.Fact_Daily_Call WHERE call_start_time >= '{{ date_start }}' AND call_start_time < '{{ date_end }}' GROUP BY date ORDER BY date",
        "connection_env": "sg",
        "connection_type": "clickhouse",
        "x_field": "date",
        "y_fields": ["connected_calls"],
        "series_field": "env",
        "echarts_override": {"series": [{"stack": "total", "type": "bar"}]}
      }
    ],
    "filters": [
      {
        "id": "date_range",
        "type": "date_range",
        "label": "时间范围",
        "default_days": 30,
        "binds": {"start": "date_start", "end": "date_end"}
      }
    ]
  },
  "username": "{CURRENT_USER}"
})
```

### 第三步：告知用户完成
`report__create` 返回 `success: true` 后，直接告知用户"报表已生成"。
- 报表打开时自动实时查询 ClickHouse，支持拖动时间范围重新查询
- 无需调用 `filesystem__write_file`，无需任何额外写文件操作
- 可选：调用 Pilot 后续修改图表样式（`report__update_single_chart`）

**⚠️ connection_env 格式**：必须是环境标识短名（`sg`、`idn`、`sg-azure`），**不能**带 `clickhouse-` 前缀（错误示例：`"clickhouse-sg"`）。

**⚠️ binds 格式**：必须是 dict `{"start": "date_start", "end": "date_end"}`，**不能**是 list `["date_start", "date_end"]`。

**❌ 禁止事项：**
- 禁止查询大量数据后嵌入 JS 数组（会导致数据截断、报表静态化）
- 禁止用 `filesystem__write_file` 写 HTML 图表报表
- 禁止在 spec.charts[].sql 里写死时间范围（要用 `{{ date_start }}`）
- 禁止因为"查不到数据"就改成 MD 格式

---

## 一、环境映射

| 环境标识 | 区域 | ClickHouse 服务器 |
|---------|------|-----------------|
| SG | 新加坡 | clickhouse-sg |
| IDN | 印度尼西亚 | clickhouse-idn |
| BR | 巴西 | clickhouse-br |
| MY | 马来西亚 | clickhouse-my |
| THAI | 泰国 | clickhouse-thai |
| MX | 墨西哥 | clickhouse-mx |
| SG-AZURE | 新加坡(Azure) | clickhouse-sg-azure |

- 所有环境主数据库名：`crm`
- 遍历所有环境时，依次对每个环境执行相同查询逻辑

---

## 二、核心业务表

### 1. 外呼通话记录表
```
crm.realtime_dwd_crm_call_record
```
| 关键字段 | 说明 |
|---------|------|
| `enterprise_id` | 企业ID |
| `call_start_time` | 通话开始时间（分区/过滤字段） |
| `call_code` | 通话结果编码（Nullable(String) 类型） |
| `call_code_type` | 通话结果类型编码（Int 类型） |
| `call_duration` | 通话总时长 |
| `ai_call_duration` | AI通话时长 |
| `agent_call_duration` | 坐席通话时长 |
| `template_code` | 话术模板编码 |

**call_code 枚举值说明：**
| call_code | 业务含义 |
|-----------|---------|
| `'19'` | 未接通/拒接 |
| `'5'` | 其他未接通 |
| `'486'` | SIP 486 忙音 |
| `'903'` | 超时/无应答 |

> ⚠️ **重要**：`call_code` 字段类型为 `Nullable(String)`，过滤时必须使用字符串字面量，**不可用整数**（会导致 Code 386 类型错误）。

**call_code_type 枚举值说明（用于 Connected/AM 判断，优先使用此字段）：**
| call_code_type | 业务含义 |
|----------------|---------|
| `1`, `16` | **Connected（接通）** — 使用 `call_code_type IN (1, 16)` |
| `22` | **Answering Machine（答录机/AM）** — 使用 `call_code_type = 22` |

> ⚠️ **重要**：`call_code_type` 为整数类型，过滤时直接使用整数，**不加引号**。

---

### 2. 企业计费规则表
```
crm.realtime_ods_cost_data_payment_rule_ent
```
| 关键字段 | 说明 |
|---------|------|
| `enterprise_id` | 企业ID |
| `enterprise_name` | 企业名称 |
| `policy_id` | 计费策略ID |
| `rule` | 计费规则 JSON 字段（核心） |
| `create_time` / `update_time` | 规则创建/更新时间 |

> ⚠️ **重要**：同一 `enterprise_id` 可能存在多行规则记录，**只取最新规则**（按 `update_time` 或 `create_time` 降序取第一条）。

---

## 三、计费规则业务知识

### 3.1 多 PolicyId 规则识别

**定义**：`rule` 字段为 JSON 数组，其中每个对象可能含 `policyId` 字段。若该 JSON 数组中存在 **2个或以上不同的 `policyId` 值**，则该企业为"多PolicyId计费"。

**判断逻辑：**
- ✅ 单 PolicyId：所有对象的 `policyId` 均为 `599` → **只有1个policyId**
- ✅ 多 PolicyId：对象中出现 `599`、`600`、`601` → **有3个不同policyId**
- ✅ 无 `policyId` 字段的对象（如 `{"balanceType":100,"busType":1,"costUnit":500}`）：**忽略，不计入统计**

**ClickHouse SQL 识别方法**（字符串正则提取，兼容旧版本）：
```sql
-- 从 rule JSON 中提取所有 policyId 值，统计不同值的数量
WITH latest_rule AS (
    SELECT
        enterprise_id,
        enterprise_name,
        rule,
        ROW_NUMBER() OVER (PARTITION BY enterprise_id ORDER BY update_time DESC) AS rn
    FROM crm.realtime_ods_cost_data_payment_rule_ent
    WHERE rule LIKE '%policyId%'
),
extracted AS (
    SELECT
        enterprise_id,
        enterprise_name,
        arrayDistinct(
            extractAll(rule, '"policyId"\\s*:\\s*(\\d+)')
        ) AS policy_ids,
        length(arrayDistinct(
            extractAll(rule, '"policyId"\\s*:\\s*(\\d+)')
        )) AS policy_count
    FROM latest_rule
    WHERE rn = 1
)
SELECT *
FROM extracted
WHERE policy_count >= 2
ORDER BY policy_count DESC
```

---

### 3.2 阶梯计费规则识别

**定义**：`rule` 字段的 JSON 中含有 `tierRule` 关键字，则该企业配置了**阶梯计费**。

**识别方法：**
```sql
-- 阶梯计费企业识别（取最新规则）
WITH latest_rule AS (
    SELECT
        enterprise_id,
        enterprise_name,
        rule,
        ROW_NUMBER() OVER (PARTITION BY enterprise_id ORDER BY update_time DESC) AS rn
    FROM crm.realtime_ods_cost_data_payment_rule_ent
)
SELECT
    enterprise_id,
    enterprise_name,
    rule
FROM latest_rule
WHERE rn = 1
  AND rule LIKE '%tierRule%'
```

**阶梯类型说明（`tierRule.type` 字段）：**
| type值 | 含义 |
|--------|------|
| `0` | 累进阶梯（每档费率只对该档内的量生效） |
| `1` | 全量阶梯（达到某档后，所有量按该档费率计算） |
| `2` | 固定阶梯（按区间固定费率） |

---

### 3.3 最新规则取法（标准写法）

```sql
-- 方法1：ROW_NUMBER 窗口函数（推荐）
WITH latest AS (
    SELECT *,
        ROW_NUMBER() OVER (PARTITION BY enterprise_id ORDER BY update_time DESC) AS rn
    FROM crm.realtime_ods_cost_data_payment_rule_ent
)
SELECT * FROM latest WHERE rn = 1;

-- 方法2：子查询 MAX（备用）
SELECT t.*
FROM crm.realtime_ods_cost_data_payment_rule_ent t
INNER JOIN (
    SELECT enterprise_id, max(update_time) AS max_time
    FROM crm.realtime_ods_cost_data_payment_rule_ent
    GROUP BY enterprise_id
) latest ON t.enterprise_id = latest.enterprise_id
       AND t.update_time = latest.max_time;
```

---

## 四、标准分析模板

### 4.1 多PolicyId企业 + 外呼数据联合查询

```sql
-- Step1: 识别多PolicyId企业（最新规则）
WITH latest_rule AS (
    SELECT
        enterprise_id,
        enterprise_name,
        rule,
        ROW_NUMBER() OVER (PARTITION BY enterprise_id ORDER BY update_time DESC) AS rn
    FROM crm.realtime_ods_cost_data_payment_rule_ent
    WHERE rule LIKE '%policyId%'
),
multi_policy_ent AS (
    SELECT
        enterprise_id,
        enterprise_name,
        arrayDistinct(extractAll(rule, '"policyId"\\s*:\\s*(\\d+)')) AS policy_ids,
        length(arrayDistinct(extractAll(rule, '"policyId"\\s*:\\s*(\\d+)'))) AS policy_count
    FROM latest_rule
    WHERE rn = 1
      AND length(arrayDistinct(extractAll(rule, '"policyId"\\s*:\\s*(\\d+)'))) >= 2
),
-- Step2: 统计外呼数据
call_stats AS (
    SELECT
        enterprise_id,
        countIf(call_code_type IN (1, 16)) AS connected_calls,
        countIf(call_code_type = 22) AS am_calls,
        count() AS total_calls
    FROM crm.realtime_dwd_crm_call_record
    WHERE call_start_time >= '2025-03-01 00:00:00'
      AND call_start_time < '2025-03-30 00:00:00'
      AND (call_code_type IN (1, 16) OR call_code_type = 22)
    GROUP BY enterprise_id
)
-- Step3: 关联输出
SELECT
    e.enterprise_name,
    e.policy_count,
    arrayStringConcat(e.policy_ids, ',') AS policy_ids,
    c.connected_calls,
    c.am_calls,
    c.total_calls,
    round(c.connected_calls / c.total_calls * 100, 2) AS connect_rate_pct,
    round(c.am_calls / c.total_calls * 100, 2) AS am_rate_pct
FROM multi_policy_ent e
INNER JOIN call_stats c ON e.enterprise_id = c.enterprise_id
ORDER BY c.total_calls DESC
```

---

### 4.2 阶梯计费企业 + 外呼数据联合查询

```sql
WITH latest_rule AS (
    SELECT
        enterprise_id,
        enterprise_name,
        rule,
        ROW_NUMBER() OVER (PARTITION BY enterprise_id ORDER BY update_time DESC) AS rn
    FROM crm.realtime_ods_cost_data_payment_rule_ent
),
tier_ent AS (
    SELECT enterprise_id, enterprise_name, rule
    FROM latest_rule
    WHERE rn = 1 AND rule LIKE '%tierRule%'
),
call_stats AS (
    SELECT
        enterprise_id,
        countIf(call_code_type IN (1, 16)) AS connected_calls,
        countIf(call_code_type = 22) AS am_calls
    FROM crm.realtime_dwd_crm_call_record
    WHERE call_start_time >= '2025-02-01 00:00:00'
      AND call_start_time < '2025-03-30 00:00:00'
      AND (call_code_type IN (1, 16) OR call_code_type = 22)
    GROUP BY enterprise_id
)
SELECT
    t.enterprise_name,
    c.connected_calls,
    c.am_calls,
    t.rule
FROM tier_ent t
INNER JOIN call_stats c ON t.enterprise_id = c.enterprise_id
ORDER BY (c.connected_calls + c.am_calls) DESC
```

---

## 五、常见注意事项

1. **call_code 类型问题**：永远使用字符串比较 `call_code = '12'`，不用整数
2. **最新规则**：同企业多行规则时，必须取 `update_time` 最大的一行
3. **JSON提取兼容性**：旧版ClickHouse不支持 `JSONExtractKeys`，使用 `extractAll` + 正则替代
4. **分区过滤**：外呼记录表查询必须带时间范围条件，避免全表扫描
5. **接通/AM判断**：优先使用 `call_code_type` 整数字段：Connected = `call_code_type IN (1, 16)`，Answering Machine = `call_code_type = 22`
6. **THAI环境AM**：THAI环境 `call_code_type=22` 可能为0，疑似枚举定义差异，需额外确认
7. **MY环境异常**：Seamoney MY 出现 AM 占比 99.99% 异常，排查时需关注号码质量问题
8. **SG-AZURE**：该环境通常无计费规则配置或企业数量极少，可最后处理

---

## 六、输出报告规范

分析报告标准结构：
1. **统计口径说明**（时间范围、过滤条件、最新规则取法）
2. **各环境汇总表**（环境 | 企业数 | Connected | AM | 接通率）
3. **企业明细表**（环境 | 企业名 | PolicyId数/阶梯类型 | Connected | AM | 接通率）
4. **Top N 企业排名**（按外呼总量）
5. **异常企业标注**（接通率极低 / AM占比异常 / 疑似测试账号）
6. **业务洞察 & 建议**

报告文件写入路径：`{CURRENT_USER}/reports/`（文件系统根目录已指向 customer_data/，勿重复写 customer_data/ 前缀）

---

## 七、⚠️ HTML 报表文件强制规范 — REPORT_SPEC 标记

**当你生成 HTML 可视化报表文件时，必须在 `<script>` 块中嵌入以下结构化标记**，否则固定到报表清单后 AI Pilot 无法识别图表内容。

```html
<script>
// ── 报表结构化规格（AI Pilot 读取，请勿删除）─────────────────────────
const REPORT_SPEC = {
  "title": "报表标题",
  "subtitle": "副标题（可为空字符串）",
  "theme": "light",
  "charts": [
    {
      "id": "chart-bar-1",
      "chart_type": "bar",
      "title": "图表显示名称",
      "sql": "SELECT date, env, count() AS cnt FROM crm.realtime_dwd_crm_call_record WHERE call_start_time >= '{{ date_start }}' AND call_start_time < '{{ date_end }}' GROUP BY date, env ORDER BY date",
      "connection_env": "sg",
      "connection_type": "clickhouse",
      "x_field": "date",
      "y_fields": ["cnt"],
      "series_field": "env"
    }
  ],
  "filters": [
    {
      "id": "date_range",
      "type": "date_range",
      "label": "时间范围",
      "default_days": 30,
      "binds": { "start": "date_start", "end": "date_end" }
    }
  ]
};
window.REPORT_SPEC = REPORT_SPEC;
// ──────────────────────────────────────────────────────────────────────
</script>
```

**规则**：
- `id` 必须与对应 `echarts.init(document.getElementById('chart-bar-1'))` 的 DOM id 完全一致
- `chart_type` 取值：`bar` / `line` / `pie` / `scatter` / `area` / `gauge` / `radar`
- `sql` **必须使用 Jinja2 参数变量** `{{ date_start }}` / `{{ date_end }}` 等，**禁止硬编码日期**
- `connection_env` 填写 ClickHouse 环境标识（`sg` / `idn` / `br` 等）；`connection_type` 默认 `"clickhouse"`
- 每个 ECharts 图表必须对应 `charts` 数组中的一个元素
- `filters` **必须包含** `binds` 字段，定义 filter 值 → SQL 变量的映射关系
- 该标记使 Pilot 能够定位并修改每个图表的数据和样式

**⚠️ 图表字段映射（bar / line / area / scatter 必填）**：
- `x_field`：SQL 结果中作为 X 轴的列名（如 `"day"`、`"dt"`）
- `y_fields`：SQL 结果中作为 Y 轴的列名数组（如 `["cnt"]`、`["connected_calls", "am_calls"]`）
- `series_field`：SQL 结果中作为分组维度的列名（如 `"connection_env"`），有多环境/多维度分组时必填，单系列留空或省略
- **缺少这三个字段将导致图表在动态数据加载后无法正确渲染（X 轴显示 undefined）**

---

## 八、参数化 SQL 规范

**报表 SQL 模板语法（Jinja2）**：使用 `{{ variable_name }}` 作为占位符，由筛选器 `binds` 字段绑定。

### 标准 filter + SQL 对应写法

```json
// filter spec 示例
{
  "id": "date_range",
  "type": "date_range",
  "label": "时间范围",
  "default_days": 30,
  "binds": { "start": "date_start", "end": "date_end" }
}
```

```sql
-- 对应图表 SQL（使用 Jinja2 变量）
SELECT
  toDate(call_start_time) AS date,
  countIf(call_code_type IN (1, 16)) AS connected_calls,
  count() AS total_calls
FROM crm.realtime_dwd_crm_call_record
WHERE call_start_time >= '{{ date_start }}'
  AND call_start_time < '{{ date_end }}'
GROUP BY date
ORDER BY date
```

### 多维度筛选示例

```json
// 企业筛选器（select 类型）
{ "id": "ent_filter", "type": "select", "label": "企业", "options": [], "binds": { "value": "enterprise_id" } }
```

```sql
-- SQL 同时使用日期和企业参数
WHERE call_start_time >= '{{ date_start }}'
  AND call_start_time < '{{ date_end }}'
  {% if enterprise_id %}AND enterprise_id = '{{ enterprise_id }}'{% endif %}
```

### 注意事项

1. **必须有筛选器**：生成报表时至少包含一个 `date_range` 类型的筛选器，并配置 `binds`
2. **变量名一致**：`binds.start` / `binds.end` 的值必须与 SQL 中 `{{ }}` 内的变量名完全一致
3. **AI 生成预览时**：先调用 `render_sql(template, default_params)` 预填默认参数再查询 ClickHouse
4. **禁止硬编码**：SQL 中不得出现 `WHERE date >= '2025-01-01'` 形式的固定日期
