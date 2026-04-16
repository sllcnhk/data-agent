---
name: ch-integrated-data
version: "1.0"
description: integrated_data 跨环境汇集库分析——新老客分析、全环境汇总、双路数据源查询
triggers:
  - integrated_data
  - 汇集库
  - 全环境
  - 新老客
  - Existing_Customer
  - 跨环境统计
  - 全渠道汇总
  - dialogue统计
category: analytics
priority: high
always_inject: false
layer: scenario
scope: global-integrated
---

# integrated_data 跨环境汇集库分析

> 独立触发技能（非 clickhouse-analyst 子技能）。当分析跨所有环境的汇总数据时触发。
> **服务器**：clickhouse-sg（SG 环境 ClickHouse）；**库名**：`integrated_data`。
> ⚠️ 该库**仅含统计聚合数据**，无明细呼叫记录，不支持行级追溯。

---

## 一、库架构说明

| 特征 | 说明 |
|------|------|
| **所在服务器** | clickhouse-sg（只需连接 SG 即可查全环境） |
| **数据范围** | 汇聚 SG / IDN / BR / MY / THAI / MX 等所有环境 |
| **环境区分字段** | `SaaS` 或 `Environment` 字段值（如 `IDN`, `THAI`, `SG-AZURE`） |
| **数据粒度** | 月度 / 日度聚合，**无明细行** |
| **软删除字段** | `statistic_is_delete = 0`（所有事实表必须过滤） |
| **维度表读取** | 必须加 `FINAL` 关键字（防 CollapsingMergeTree 读到删除标记） |

---

## 二、核心表速查

| 表名 | 用途 | 粒度 | 文档 |
|------|------|------|------|
| `Fact_Daily_Call` | 日度呼叫聚合（按 template_code × call_code_type）| 天×企业×话术 | [→](../db_knowledge/tables/Fact_Daily_Call.md) |
| `Fact_Call_Unique_Phone` | 唯一号码接触/接通统计 | 天×企业×话术 | [→]({SHARED_DATA_ROOT}/db_knowledge/tables/Fact_Call_Unique_Phone.md) |
| `Fact_Daily_Call_Contacts_Offline` | 离线对账数据（UNION ALL 补充）| 天×话术 | [→]({SHARED_DATA_ROOT}/db_knowledge/tables/Fact_Daily_Call_Contacts_Offline.md) |
| `Dim_Enterprise` | 企业维度（含 test_flag）| - | [→]({SHARED_DATA_ROOT}/db_knowledge/tables/Dim_Enterprise.md) |
| `Dim_Dialogue` | 话术/模板维度（含 IVR 判断字段）| - | [→]({SHARED_DATA_ROOT}/db_knowledge/tables/Dim_Dialogue.md) |
| `Dim_Unique_ID` | 项目属性维度（客户/销售/新老客）| - | [→]({SHARED_DATA_ROOT}/db_knowledge/tables/Dim_Unique_ID.md) |
| `Dim_Unique_ID_Sales_Split` | 销售归因分摊（多团队时使用）| - | [→]({SHARED_DATA_ROOT}/db_knowledge/tables/Dim_Unique_ID_Sales_Split.md) |

---

## 三、五条核心业务规则（必须遵守）

### 规则 1：IVR 判断逻辑（来源：Dim_Dialogue）

```sql
case
    when speech.switch = '0' then 'IVR'
    when match(LOWER(speech.speech_name), '(?i)[^a-zA-Z]ivr[^a-zA-Z]') = 1
      OR match(LOWER(speech.speech_name), '(?i)^ivr[^a-zA-Z]') = 1
      OR match(LOWER(speech.speech_name), '(?i)[^a-zA-Z]ivr$') = 1
      OR LOWER(speech.speech_name) = 'ivr' then 'IVR'
    else 'NON_IVR'
end as IVR_Flag
```

> `switch='0'` 优先；其次通过话术名称 regex 判断（处理历史数据 switch 未设置的情况）。

---

### 规则 2：新老客二分（来源：Dim_Unique_ID.Existing_Customer）

```sql
case
    when Existing_Customer not in ('Existing') then 'New'
    else Existing_Customer
end as Existing_Customer_Modified
```

> 字段值只有 `'Existing'` 视为老客，其余（包括 NULL、空串、其他值）全部归为 `'New'`。

---

### 规则 3：Sales_Team 回退链（三级优先级）

```sql
case
    when (uni.Sales_Team is null or uni.Sales_Team = '')
         and unp.ts = 1                          then unp.S_Sales_Team   -- ① 分摊表有唯一团队
    when (uni.Sales_Team is null or uni.Sales_Team = '')
         and bb.SaaS ilike '%IDN-Sampoerna%'     then 'Indonesia'        -- ② IDN-Sampoerna 特例
    else uni.Sales_Team                                                   -- ③ 正常取维度表值
end
```

> `unp.ts` = `count(distinct Sales_Team)`，等于 1 表示只有一个销售团队，可安全回退。
> 关联：`Dim_Unique_ID_Sales_Split JOIN Dim_Unique_ID ON Contract_Code` + 月份匹配。

---

### 规则 4：测试企业标准过滤（必须先关联企业维表，再过滤）

```sql
LEFT JOIN (
    SELECT
        Environment,
        Enterprise_ID,
        Enterprise_Name,
        Create_User,
        Unique_ID_Latest_Create,
        Name_Test_Flag AS test_flag
    FROM integrated_data.Dim_Enterprise ent FINAL
    WHERE ent.statistic_is_delete = 0
) ent
    ON ent.Environment = bb.SaaS
   AND ent.Enterprise_ID = bb.enterprise_id

WHERE ent.test_flag = 0
  AND ent.Enterprise_Name not ilike '%zhujinli%'
  AND ent.Enterprise_Name not ilike '%zhouli%'
  AND ent.Enterprise_Name not ilike '%ding yang%'
  AND ent.Enterprise_Name not ilike '%PM_XUNLIN%'
```

> 汇集库（`integrated_data`）查询企业维度时，必须按 `Environment + Enterprise_ID` 关联 `Dim_Enterprise`，并将 `Name_Test_Flag` 映射为 `test_flag`。
> `test_flag` 来源：`Dim_Enterprise.Name_Test_Flag`（字段别名 `test_flag`）。
> 4 个 hardcode 名称为历史测试企业，`test_flag` 未及时标记，需手动排除。

---

### 规则 5：软删除 + FINAL

```sql
-- 所有事实表
WHERE adlc.statistic_is_delete = 0

-- 所有维度表必须加 FINAL
FROM integrated_data.Dim_Enterprise ent FINAL
FROM integrated_data.Dim_Dialogue speech FINAL
FROM integrated_data.Dim_Unique_ID uni1 FINAL
FROM integrated_data.Dim_Unique_ID_Sales_Split usp1 FINAL
```

---

## 四、双路数据源模式

`integrated_data` 的汇总分析通常需要 **UNION ALL** 两路数据：

```
┌─── 在线路 (bb + cc JOIN) ───────────────────────────────────┐
│  Fact_Call_Unique_Phone (bb)                                 │
│    LEFT JOIN Fact_Daily_Call (cc)                            │
│    ON SaaS = SaaS AND enterprise_id = enterprise_id          │
│       AND period = period AND template_code = template_code  │
│  再 LEFT JOIN Dim_Enterprise / Dim_Dialogue / Dim_Unique_ID  │
└─────────────────────────────────────────────────────────────┘
         UNION ALL
┌─── 离线路 ──────────────────────────────────────────────────┐
│  Fact_Daily_Call_Contacts_Offline (dco)                      │
│    LEFT JOIN Dim_Unique_ID / Dim_Unique_ID_Sales_Split       │
│  无 statistic_is_delete 过滤，无 FINAL，直接按 s_day 过滤    │
└─────────────────────────────────────────────────────────────┘
```

> 在线路覆盖系统内实时产生的呼叫记录；离线路覆盖通过线下文件导入的外呼数据，两者互补。

---

## 五、月度聚合标准模式

```sql
-- period 字段（字符串，用于展示）
formatDateTime(toStartOfMonth(s_day), '%Y-%m') AS period

-- period_start 字段（Date 类型，用于聚合/计算）
toStartOfMonth(s_day) AS period_start

-- 时间范围过滤（两个 >= 条件取较大值，< 条件取月度边界）
WHERE s_day >= toDate('2024-07-01T00:00:00')
  AND s_day < toDate('2026-04-01T00:00:00')
```

> ⚠️ SQL 中出现 `s_day >= toDate('2023-09-01')` 的旧过滤是历史兜底，实际以较新的日期为准。

---

## 五-B、⚠️ 动态报表参数化规范（生成 report__create 时必须遵守）

**禁止在报表 SQL 中使用任何硬编码日期**（包括 `today() - N` 形式）。
必须使用 Jinja2 参数 + filter binds 双向绑定：

```json
// filter spec
{ "id": "date_range", "type": "date_range", "default_days": 30,
  "binds": { "start": "date_start", "end": "date_end" } }
```

```sql
-- 对应图表 SQL（integrated_data 日度表用 s_day 字段）
WHERE s_day >= toDate('{{ date_start }}') AND s_day <= toDate('{{ date_end }}')
-- 或月度聚合
WHERE s_day >= toStartOfMonth(toDate('{{ date_start }}'))
  AND s_day < toDate('{{ date_end }}')
```

**关键约束**：
- `binds.start = "date_start"` → SQL 必须用 `{{ date_start }}`（字符完全一致）
- `binds.end = "date_end"` → SQL 必须用 `{{ date_end }}`
- **严禁**将图表 ID（`c1`、`c2`）写入 binds.start/end（会导致日期为空 → Code 38）

---

## 六、典型分析查询框架

```sql
-- 新老客月度通话指标汇总
SELECT
    toStartOfMonth(Period_Start)                                    AS month,
    CASE WHEN Existing_Customer NOT IN ('Existing') THEN 'New'
         ELSE Existing_Customer END                                  AS customer_type,
    sum(Connected_Call)                                             AS connected,
    sum(Total_Call)                                                 AS total,
    round(sum(Connected_Call) * 100.0 / sum(Total_Call), 2)        AS connect_rate
FROM (
    -- 内层：在线路 + 离线路 UNION ALL（参考规则 1-5 构建）
    ...
) AS base
GROUP BY month, customer_type
ORDER BY month, customer_type;
```

---

## 七、常见分析维度

| 维度 | 来源表 | 字段 |
|------|--------|------|
| 环境 | Fact_*/Dim_* | `SaaS` / `Environment` |
| 新老客 | Dim_Unique_ID | `Existing_Customer`（需二分处理） |
| IVR/非IVR | Dim_Dialogue | `switch` + `speech_name` |
| 话术 | Dim_Dialogue | `template_code`, `speech_name` |
| 项目/客户 | Dim_Unique_ID | `Client_Name`, `Use_Case`, `Product_Line` |
| 销售归因 | Dim_Unique_ID + Sales_Split | `Sales_Team`, `Sales_Owner` |
| 国家 | Dim_Unique_ID | `Country` |
| 行业 | Dim_Unique_ID | `Industry`, `Business_Unit` |
