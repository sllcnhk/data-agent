# 表：Fact_Daily_Call

**数据库**: `integrated_data`  
**层级**: DWS（汇总层）  
**描述**: 每日呼叫聚合事实表，按天、企业、模板维度汇总呼叫量、接通率、时长等核心指标

---

## 表结构

| 字段名 | 类型 | 业务含义 | 备注 |
|--------|------|----------|------|
| `SaaS` | String | 环境标识 | 如 IDN, THAI, AU 等，对应 `statistic_config.Environment` |
| `automation_flag` | String | 自动化标记 | `automation`=自动化任务, `non_automation`=手动任务 |
| `enterprise_id` | Int64 | 企业ID | 关联 `Dim_Enterprise.Enterprise_ID` |
| `template_code` | String | 话术模板编码 | 关联 `Dim_Dialogue.template_code` |
| `call_task_id` | Int64 | 呼叫任务ID | 关联 `dim_call_task.call_task_id` |
| `s_day` | Date | 统计日期 | 分区字段，Asia/Singapore 时区 |
| `call_type` | Int8 | 呼叫类型 | 1=外呼, 2=呼入 |
| `call_num` | Int64 | 呼叫数量 | 当日呼叫总次数 |
| `ring_duration` | Int64 | 振铃总时长 | 单位：秒 |
| `ai_duration` | Int64 | AI通话总时长 | 单位：秒 |
| `agent_duration` | Int64 | 人工通话总时长 | 单位：秒 |
| `responsed_calls` | Int64 | 接通呼叫数 | 客户接听的电话数量 |
| `valid_calls` | Int64 | 有效呼叫数 | 符合计费标准的呼叫数（需确认口径） |

---

## 业务语义

### 核心用途
- **日报监控**: 每日呼叫量、接通率、时长等核心指标的实时监控
- **趋势分析**: 按时间序列分析呼叫业务的变化趋势
- **成本效益分析**: 与 `Fact_Bill_Usage` 关联计算单位呼叫成本、ROI
- **自动化效果评估**: 通过 `automation_flag` 对比自动化任务与手动任务的效果差异

### 关键指标口径

1. **接通率 (Response Rate)**:
   ```
   接通率 = responsed_calls / call_num * 100%
   ```

2. **AI使用率**:
   ```
   AI使用率 = ai_duration / (ai_duration + agent_duration) * 100%
   ```

3. **平均通话时长**:
   ```
   平均时长 = (ai_duration + agent_duration) / responsed_calls
   ```

4. **单位呼叫成本** (需关联账单表):
   ```
   单位成本 = Bill_Amount / call_num
   ```

---

## 数据特征

- **数据量级**: 最近一个月约数十万行（取决于企业数量 × 模板数量 × 天数）
- **分区策略**: 按 `s_day` 日期分区
- **更新频率**: T+1 更新，每日凌晨生成前一天的聚合数据
- **数据粒度**: 天 × 企业 × 模板 × 任务类型（自动化/手动）

---

## 典型查询

### 1. 最近7天呼叫趋势（按自动化标记）
```sql
SELECT 
  s_day,
  automation_flag,
  sum(call_num) as total_calls,
  sum(responsed_calls) as responsed_calls,
  round(sum(responsed_calls) * 100.0 / sum(call_num), 2) as response_rate,
  sum(ai_duration) as total_ai_duration,
  sum(agent_duration) as total_agent_duration
FROM integrated_data.Fact_Daily_Call
PREWHERE s_day >= addDays(toDate(now()), -7)
WHERE s_day >= addDays(toDate(now()), -7)
GROUP BY s_day, automation_flag
ORDER BY s_day;
```

### 2. 企业呼叫绩效排名（最近30天）
```sql
SELECT 
  enterprise_id,
  sum(call_num) as total_calls,
  sum(responsed_calls) as responsed_calls,
  round(sum(responsed_calls) * 100.0 / sum(call_num), 2) as response_rate,
  round(sum(ai_duration) / 3600.0, 2) as total_ai_hours,
  round(sum(agent_duration) / 3600.0, 2) as total_agent_hours
FROM integrated_data.Fact_Daily_Call
PREWHERE s_day >= addMonths(toDate(now()), -1)
WHERE s_day >= addMonths(toDate(now()), -1)
GROUP BY enterprise_id
ORDER BY total_calls DESC
LIMIT 20;
```

### 3. 自动化 vs 手动任务效果对比
```sql
SELECT 
  automation_flag,
  count(DISTINCT enterprise_id) as enterprise_cnt,
  sum(call_num) as total_calls,
  sum(responsed_calls) as responsed_calls,
  round(sum(responsed_calls) * 100.0 / sum(call_num), 2) as response_rate,
  round(sum(ai_duration) * 100.0 / nullIf(sum(ai_duration) + sum(agent_duration), 0), 2) as ai_rate
FROM integrated_data.Fact_Daily_Call
PREWHERE s_day >= addMonths(toDate(now()), -1)
WHERE s_day >= addMonths(toDate(now()), -1)
GROUP BY automation_flag;
```

### 4. 模板效果分析
```sql
SELECT 
  template_code,
  d.speech_name,
  sum(call_num) as total_calls,
  sum(responsed_calls) as responsed_calls,
  round(sum(responsed_calls) * 100.0 / sum(call_num), 2) as response_rate,
  round(sum(ai_duration) / nullIf(sum(responsed_calls), 0), 2) as avg_ai_duration
FROM integrated_data.Fact_Daily_Call f
LEFT JOIN integrated_data.Dim_Dialogue d 
  ON f.template_code = d.template_code 
  AND f.enterprise_id = d.enterprise_id
PREWHERE s_day >= addMonths(toDate(now()), -1)
WHERE s_day >= addMonths(toDate(now()), -1)
GROUP BY template_code, d.speech_name
HAVING total_calls >= 100
ORDER BY total_calls DESC;
```

---

## 与账单表关联分析

### 单位呼叫成本分析
```sql
SELECT 
  f.automation_flag,
  b.Charge_Type,
  count(DISTINCT f.enterprise_id) as enterprise_cnt,
  sum(f.call_num) as total_calls,
  sum(f.responsed_calls) as total_responsed,
  round(sum(f.responsed_calls) * 100.0 / sum(f.call_num), 2) as response_rate,
  sum(b.Bill_Amount) as total_amount,
  round(sum(b.Bill_Amount) / sum(f.call_num), 4) as avg_cost_per_call
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

**查询结果解读** (基于最近一个月数据):
| automation_flag | Charge_Type | enterprise_cnt | total_calls | response_rate | avg_cost_per_call |
|-----------------|-------------|----------------|-------------|---------------|-------------------|
| automation | Line Charge | 97 | 3.6亿 | 3.1% | 150.32 |
| non_automation | Line Charge | 37 | 6018万 | 2.49% | 48.61 |
| automation | Bot Charge | 14 | 1098万 | 7.4% | 0.69 |
| non_automation | Bot Charge | 6 | 27.8万 | 44.35% | 150.68 |

**关键发现**:
- 自动化任务的 `Line Charge` 呼叫量远高于手动任务（6倍），但单位成本也更高（150 vs 48）
- 自动化任务的 `Bot Charge` 接通率（7.4%）低于手动任务（44.35%），但单位成本极低（0.69）
- 建议进一步分析 `Bot Charge` 的计费逻辑，可能是按对话次数而非通话时长计费

---

## 注意事项

1. **数据延迟**: 
   - T+1 更新，当天数据可能不完整
   - 查询时建议使用 `s_day < toDate(now())` 避免部分数据

2. **关联键匹配**:
   - 与 `Fact_Bill_Usage` 关联时需同时匹配 `enterprise_id`、`template_code`、`日期`
   - 存在一对多关系（一天内同一模板可能有多条计费记录）

3. **空值处理**:
   - `automation_flag` 可能为空，统计时需考虑
   - 时长字段为 0 表示无对应类型的通话

4. **性能优化**:
   - 必须使用 `PREWHERE s_day >= ...` 进行分区过滤
   - 避免全表扫描，聚合查询时限制时间范围

---

## 关联关系

| 关联表 | 关联字段 | 关联类型 | 说明 |
|--------|----------|----------|------|
| `Dim_Enterprise` | `enterprise_id` | N:1 | 企业维度 |
| `Dim_Dialogue` | `template_code` + `enterprise_id` | N:1 | 话术模板维度 |
| `dim_call_task` | `call_task_id` | N:1 | 呼叫任务维度 |
| `Fact_Bill_Usage` | `enterprise_id` + `template_code` + `日期` | 1:N | 计费明细关联 |
