# 指标：接通率（Connect Rate）

## 指标定义

**接通率** = 被叫方实际接听的通话数 / 总发起呼叫数

---

## 计算口径

### 方法一：使用 `Fact_Daily_Call`（推荐，性能好）

```sql
-- 企业日度接通率
SELECT 
    call_date,
    enterprise_id,
    sum(total_calls) AS total,
    sum(answered_calls) AS answered,
    sum(answered_calls) / sum(total_calls) AS connect_rate
FROM integrated_data.Fact_Daily_Call
WHERE enterprise_id = ?
    AND call_date >= today() - 30
GROUP BY call_date, enterprise_id
ORDER BY call_date;
```

### 方法二：使用 `consolidated_cdr`（数据最原始，适合精确分析）

```sql
-- 基于话单的接通率
SELECT 
    toDate(CallStartTime) AS call_date,
    EnterpriseId,
    count() AS total_calls,
    countIf(CallCode = 'Answered') AS answered_calls,
    countIf(CallCode = 'Answered') / count() AS connect_rate
FROM om_statistics.consolidated_cdr
WHERE EnterpriseId = ?
    AND CallStartTime >= today() - 30
GROUP BY call_date, EnterpriseId
ORDER BY call_date;
```

### 方法三：使用 `cdr_statistics_hourly`（实时监控，小时粒度）

```sql
-- 小时级接通率趋势
SELECT 
    hour,
    Region,
    -- 具体字段依据 cdr_statistics_hourly 实际字段
    answered_calls / total_calls AS connect_rate
FROM om_statistics.cdr_statistics_hourly
WHERE hour >= now() - INTERVAL 24 HOUR
ORDER BY hour;
```

---

## 维度拆分

| 维度 | 说明 |
|------|------|
| 企业 | 各企业的接通率差异 |
| 时间 | 按日/小时分析接通率波动 |
| 模板 | 不同话术模板的接通率对比 |
| 任务 | 不同呼叫任务的接通率 |
| 区域 | 不同国家/地区的接通率 |
| 通话类型 | AI外呼 vs 人工外呼 |

---

## 接通判断标准

| 数据源 | 接通标识 |
|--------|---------|
| `consolidated_cdr` | `CallCode = 'Answered'` |
| `dim_call_task_customer` | `connect_status = 2` |
| `Fact_Daily_Call` | `answered_calls` 字段 |

---

## 注意事项

1. **语音信箱**：`CallCode = 'Voicemail'` 和 `CallCode = 'AnswerMachine'` 是否算接通需根据业务需求决定
2. **分母选择**：`total_calls` 包含所有发起的呼叫（含号码无效等），有时用"有效拨打数"作分母
3. **时区影响**：`consolidated_cdr` 使用 Asia/Singapore 时区，跨区域分析需注意
