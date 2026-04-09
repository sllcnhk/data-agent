# 指标口径：单位呼叫成本 (Cost Per Call)

**指标编码**: `metric_cost_per_call`  
**业务域**: 计费分析  
**更新时间**: 2026-03-13

---

## 指标定义

**单位呼叫成本** = 总计费金额 / 总呼叫次数

衡量每次呼叫的平均成本，用于评估不同业务模式（自动化 vs 手动）的成本效益。

---

## 计算公式

```sql
成本 per 呼叫 = sum(Bill_Amount) / sum(call_num)
```

**细分维度**:
- 按任务类型：`automation_flag` (automation / non_automation)
- 按计费类型：`Charge_Type` (Line Charge / Bot Charge / SMS / Conversation)
- 按企业：`enterprise_id`
- 按话术模板：`template_code`
- 按时间：`s_day` / `Record_Date`

---

## 数据来源

| 字段 | 来源表 | 说明 |
|------|--------|------|
| `call_num` | `integrated_data.Fact_Daily_Call` | 呼叫次数 |
| `Bill_Amount` | `integrated_data.Fact_Bill_Usage` | 账单金额 |
| `enterprise_id` | 两表共有 | 关联键 1 |
| `template_code` / `Dialogue_Code` | 两表共有 | 关联键 2 |
| `s_day` / `Record_Date` | 两表共有 | 关联键 3 (日期) |

---

## 标准查询 SQL

```sql
SELECT 
  f.automation_flag,
  b.Charge_Type,
  count(DISTINCT f.enterprise_id) as enterprise_cnt,
  sum(f.call_num) as total_calls,
  sum(f.responsed_calls) as total_responsed,
  round(sum(f.responsed_calls) * 100.0 / sum(f.call_num), 2) as response_rate,
  sum(b.Bill_Amount) as total_amount,
  round(sum(b.Bill_Amount) / sum(f.call_num), 4) as cost_per_call,
  round(sum(b.Bill_Amount) / nullIf(sum(f.responsed_calls), 0), 4) as cost_per_response
FROM integrated_data.Fact_Daily_Call f
INNER JOIN integrated_data.Fact_Bill_Usage b
  ON f.enterprise_id = b.Enterprise_ID
  AND f.template_code = b.Dialogue_Code
  AND f.s_day = b.Record_Date
PREWHERE f.s_day >= addMonths(toDate(now()), -1)
WHERE f.s_day >= addMonths(toDate(now()), -1)
  AND f.call_num > 0
  AND b.Bill_Amount > 0
GROUP BY f.automation_flag, b.Charge_Type
ORDER BY total_calls DESC;
```

---

## 最近 30 天实际数据（2026-02-13 ~ 2026-03-13）

| automation_flag | Charge_Type | enterprise_cnt | total_calls | response_rate | cost_per_call | cost_per_response |
|-----------------|-------------|----------------|-------------|---------------|---------------|-------------------|
| automation | Line Charge | 97 | 3.61 亿 | 3.1% | 150.32 | 4849.0 |
| non_automation | Line Charge | 37 | 6018 万 | 2.49% | 48.61 | 1952.0 |
| automation | Bot Charge | 14 | 1098 万 | 7.4% | 0.69 | 9.36 |
| non_automation | Bot Charge | 6 | 27.8 万 | 44.35% | 150.68 | 339.8 |

---

## 数据解读

### 1. Line Charge 成本差异

- **自动化任务**: 150.32 / 次
- **手动任务**: 48.61 / 次
- **差异**: 自动化是手动的 **3.1 倍**

**可能原因**:
1. 自动化任务使用高质量线路（如专线），单价更高
2. 自动化任务包含长途/国际呼叫，费率更高
3. 手动任务主要是本地短途呼叫，费率较低

**建议**:
- 分析 `Fact_Bill_Usage.Charge_Subtype` 和 `Unit_Price` 分布
- 对比自动化和手动任务的平均通话时长
- 检查自动化任务的 `Region` 分布（是否包含高费率地区）

### 2. Bot Charge 性价比

- **自动化任务**: 0.69 / 次，接通率 7.4%
- **手动任务**: 150.68 / 次，接通率 44.35%
- **差异**: 手动成本是自动化的 **218 倍**，但接通率是 6 倍

**可能原因**:
1. 自动化 Bot Charge 按对话轮次计费，单价极低
2. 手动 Bot Charge 可能包含定制开发费或服务费
3. 手动任务针对高价值客户，使用高级 AI 功能

**建议**:
- 确认 `Bot Charge` 的计费逻辑（按轮次/时长/会话）
- 分析手动 Bot Charge 的 `Unit_Price` 是否异常高
- 评估高接通率是否能抵消高成本（计算 ROI）

### 3. 成本效益矩阵

```
                   低成本 (< 50)     高成本 (> 100)
                ┌─────────────────┬─────────────────┐
  高接通率      │  手动 Bot       │  手动 Line      │
  (> 20%)       │  (44.35%, 150)  │  (2.49%, 48)    │  ← 需要优化
                │  ← 成本高       │  ← 接通率低     │
                ├─────────────────┼─────────────────┤
  低接通率      │  自动 Bot       │  自动 Line      │
  (< 10%)       │  (7.4%, 0.69)   │  (3.1%, 150)    │
                │  ← 最优潜力     │  ← 需要优化     │
                └─────────────────┴─────────────────┘
```

**最优策略**:
- **规模化触达**: 自动化 + Bot Charge（成本最低，需提升接通率）
- **高价值客户**: 手动 + Bot Charge（接通率最高，需降低成本）
- **避免使用**: 自动化 + Line Charge（成本高且接通率低）

---

## 注意事项

1. **关联准确性**:
   - 必须同时匹配 `enterprise_id`、`template_code`、`日期` 三个字段
   - 存在一对多关系（同一天同一模板可能有多条计费记录）

2. **数据完整性**:
   - `Fact_Daily_Call` 和 `Fact_Bill_Usage` 可能存在数据覆盖差异
   - 部分呼叫记录可能没有对应的计费记录（如免费额度内）

3. **币种影响**:
   - `Bill_Amount` 的币种由 `Currency` 字段决定
   - 跨币种分析时需统一转换（如转换为 USD）

4. **测试数据**:
   - 分析真实业务成本时需排除测试企业 (`test_flag = 1`)
   - 测试数据可能扭曲成本指标

---

## 扩展分析

### 按企业分组的成本分析

```sql
SELECT 
  f.enterprise_id,
  e.Enterprise_Name,
  sum(f.call_num) as total_calls,
  sum(b.Bill_Amount) as total_amount,
  round(sum(b.Bill_Amount) / sum(f.call_num), 4) as cost_per_call,
  round(avg(f.call_num), 0) as daily_avg_calls
FROM integrated_data.Fact_Daily_Call f
INNER JOIN integrated_data.Fact_Bill_Usage b
  ON f.enterprise_id = b.Enterprise_ID
  AND f.template_code = b.Dialogue_Code
  AND f.s_day = b.Record_Date
LEFT JOIN integrated_data.Dim_Enterprise e
  ON f.enterprise_id = e.Enterprise_ID
PREWHERE f.s_day >= addMonths(toDate(now()), -1)
WHERE f.s_day >= addMonths(toDate(now()), -1)
  AND f.call_num > 0
  AND b.Bill_Amount > 0
  AND (e.test_flag = 0 OR e.test_flag IS NULL)
GROUP BY f.enterprise_id, e.Enterprise_Name
HAVING total_calls >= 10000
ORDER BY cost_per_call DESC
LIMIT 20;
```

### 成本趋势分析

```sql
SELECT 
  f.s_day,
  sum(f.call_num) as total_calls,
  sum(b.Bill_Amount) as total_amount,
  round(sum(b.Bill_Amount) / sum(f.call_num), 4) as cost_per_call,
  round(sum(b.Bill_Amount) / sum(f.call_num) / 
    lagInFrame(sum(b.Bill_Amount) / sum(f.call_num)) OVER (ORDER BY f.s_day) - 1, 4) as wow_change
FROM integrated_data.Fact_Daily_Call f
INNER JOIN integrated_data.Fact_Bill_Usage b
  ON f.enterprise_id = b.Enterprise_ID
  AND f.template_code = b.Dialogue_Code
  AND f.s_day = b.Record_Date
PREWHERE f.s_day >= addMonths(toDate(now()), -1)
WHERE f.s_day >= addMonths(toDate(now()), -1)
  AND f.call_num > 0
  AND b.Bill_Amount > 0
GROUP BY f.s_day
ORDER BY f.s_day;
```

---

## 相关指标

- **单位响应成本** (`cost_per_response`): `Bill_Amount / responsed_calls`
- **ARPU** (`arpu`): `Bill_Amount / count(DISTINCT enterprise_id)`
- **毛利率** (`gross_margin`): `(Revenue - Bill_Amount) / Revenue` (需收入数据)

---

## 参考文档

- `tables/Fact_Daily_Call.md` - 呼叫事实表结构
- `tables/Fact_Bill_Usage.md` - 账单使用量表结构
- `relationships.md` - 表关联关系
- `_index.md` - 全局索引

---

**维护者**: Data Agent  
**最后更新**: 2026-03-13
