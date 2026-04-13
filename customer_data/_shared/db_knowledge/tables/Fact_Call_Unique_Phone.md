# 表：integrated_data.Fact_Call_Unique_Phone

## 基本信息

| 属性 | 值 |
|------|-----|
| 数据库 | `integrated_data` |
| 表名 | `Fact_Call_Unique_Phone` |
| 数据层级 | DWS（汇总层）|
| 业务域 | 唯一号码接触/接通统计 |
| 更新频率 | T+1 |

## 业务语义

**唯一号码统计事实表**。以去重后的手机号为口径，统计每日各话术触达的**唯一号码数**和**接通唯一号码数**，用于计算**号码维度的接通率**（区别于通话次数维度的接通率）。

- `contact_phone_number_amount`：当日触达的不同手机号数量
- `connected_phone_number_amount`：当日接通的不同手机号数量
- 接通率（号码口径）= `connected / contact`

---

## 表结构

| 字段名 | 类型 | 业务含义 | 备注 |
|--------|------|----------|------|
| `SaaS` | String | 环境标识 | 如 IDN, THAI, SG 等 |
| `enterprise_id` | Int64 | 企业 ID | 关联 `Dim_Enterprise.Enterprise_ID` |
| `type_value` | String | 话术模板编码 | 对应 `Dim_Dialogue.template_code` |
| `s_day` | Date | 统计日期 | 分区字段 |
| `statistic_type` | String | 统计类型 | `'dialogue'`=话术维度；`'task'`=任务维度 |
| `call_type` | Int8 | 呼叫类型 | `1`=外呼，`3`=？（需确认），过滤条件：`call_type IN (1, 3)` |
| `contact_phone_number_amount` | Int64 | 触达唯一号码数 | 当日拨打的去重手机号数 |
| `connected_phone_number_amount` | Int64 | 接通唯一号码数 | 当日接通的去重手机号数 |
| `statistic_is_delete` | Int8 | 软删除标记 | `0`=有效，`1`=已删除；**必须过滤** `= 0` |

---

## 标准查询模式

```sql
SELECT
    adlc.SaaS,
    adlc.enterprise_id,
    adlc.type_value                                AS template_code,
    formatDateTime(toStartOfMonth(adlc.s_day), '%Y-%m') AS period,
    toStartOfMonth(adlc.s_day)                     AS period_start,
    sum(adlc.contact_phone_number_amount)           AS contact_phone_number,
    sum(adlc.connected_phone_number_amount)         AS connected_phone_number,
    sum(adlc.connected_phone_number_amount)
        / CAST(sum(adlc.contact_phone_number_amount) AS Float64) AS touch_rate
FROM integrated_data.Fact_Call_Unique_Phone adlc FINAL
WHERE adlc.statistic_is_delete = 0
  AND adlc.statistic_type = 'dialogue'
  AND adlc.call_type IN (1, 3)
  AND adlc.s_day >= toDate('2024-07-01')
  AND adlc.s_day <  toDate('2026-04-01')
GROUP BY adlc.SaaS, adlc.enterprise_id, adlc.type_value,
         formatDateTime(toStartOfMonth(adlc.s_day), '%Y-%m'),
         toStartOfMonth(adlc.s_day)
```

> ⚠️ 字段名 `type_value` 在 JOIN 时需别名为 `template_code` 以与其他表对齐。

---

## 关联关系

| 关联表 | 关联字段 | 说明 |
|--------|----------|------|
| `Fact_Daily_Call` | `SaaS + enterprise_id + period + template_code` | 月度汇总时一对一 JOIN |
| `Dim_Enterprise` | `SaaS + enterprise_id` | 企业维度 |
| `Dim_Dialogue` | `SaaS + enterprise_id + type_value` | 话术维度 |

---

## 注意事项

1. `type_value` ≠ `template_code`（字段名不同，语义相同），JOIN 时需注意别名
2. 月度分析时先按 `toStartOfMonth` 聚合，再与 `Fact_Daily_Call` 月度数据 JOIN
3. `call_type IN (1, 3)` 是标准过滤，只取外呼相关记录
