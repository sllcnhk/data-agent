# 指标：AI通话时长（AI Call Duration）

## 指标定义

**AI通话时长** = AI机器人实际与客户通话的总时长（秒），不含人工坐席通话时长。

---

## 计算口径

### 方法一：使用 `Fact_Daily_Call`（推荐）

```sql
-- 企业月度AI通话时长
SELECT 
    toYYYYMM(call_date) AS year_month,
    enterprise_id,
    sum(ai_call_duration) AS total_ai_seconds,
    sum(ai_call_duration) / 3600 AS total_ai_hours,
    sum(answered_calls) AS total_answered
FROM integrated_data.Fact_Daily_Call
WHERE call_type IN (1, 3)  -- 1=AI外呼, 3=AI呼入
    AND enterprise_id = ?
    AND call_date >= '2026-01-01'
GROUP BY year_month, enterprise_id
ORDER BY year_month;
```

### 方法二：使用 `call_task_entbot_num`（含并发信息）

```sql
-- 小时级AI通话时长 + 并发数
SELECT 
    sql_date_hour,
    enterprise_id,
    sum(ai_call_duration) AS ai_seconds,
    max(bot_num) AS peak_concurrent
FROM data_statistics.call_task_entbot_num
WHERE enterprise_id = ?
    AND sql_date_hour >= today() - 30
GROUP BY sql_date_hour, enterprise_id
ORDER BY sql_date_hour;
```

---

## 与计费的关系

AI通话时长是 `Line Charge`（线路费）的计费基础：

```
Bill_Amount = ai_call_duration / 60 × Unit_Price_Per_Minute
```

（具体计费规则参见 `Bill_Monthly.Unit_Price` 字段）

---

## 注意事项

1. `ai_call_duration` 单位为**秒**，转换为分钟需 ÷60，转换为小时需 ÷3600
2. 只统计 `call_type IN (1, 3)`（AI外呼+AI呼入），排除人工通话
3. 计费时长（`BillDuration`）可能与实际时长（`Duration`）不同，账单分析用 `BillDuration`
