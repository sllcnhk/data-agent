# 表：data_statistics.call_task_entbot_num

## 基本信息

| 属性 | 值 |
|------|-----|
| 数据库 | data_statistics |
| 表名 | call_task_entbot_num |
| 数据量 | ~1.07亿行 |
| 磁盘大小 | 537 MiB |
| 数据层级 | DWS（汇总层） |
| 业务域 | 运营监控 / 机器人资源管理 |
| 更新粒度 | 按小时（每小时汇总） |

## 业务语义

**企业机器人呼叫任务数量统计表**，记录每个企业在每小时内各呼叫任务的机器人并发使用情况。用于监控企业机器人资源使用量，支持容量规划和超量预警。

**核心用途**：
- 企业机器人并发数监控
- 呼叫任务级资源使用分析
- 超量使用预警（实际并发 vs 购买并发 Ai_Number）
- 小时级使用量趋势

## 字段详情

| 字段名 | 类型 | 业务含义 | 备注 |
|--------|------|---------|------|
| **enterprise_id** | Int64 | 企业ID | 关联 Dim_Enterprise |
| **sql_date_hour** | DateTime | 统计小时时间戳 | 格式: 'YYYY-MM-DD HH:00:00' |
| **sql_time** | DateTime | 数据写入时间 | ETL处理时间 |
| **ai_number** | Int16 | 企业购买的AI并发数 | 与 Dim_Enterprise.Ai_Number 一致 |
| **call_task_id** | Int64 | 呼叫任务ID | 关联 dim_call_task |
| **task_name** | String | 任务名称 | 冗余字段 |
| **bot_num** | Int32 | 该小时内机器人使用数量 | 实际并发峰值 |
| **call_num** | Int32 | 该小时内总呼叫数 | 含接通和未接通 |
| **connected_num** | Int32 | 该小时内接通数 | |
| **ai_call_duration** | Int64 | AI通话时长（秒） | 机器人实际通话时长 |
| **template_code** | String | 话术模板编码 | |
| **call_type** | Int8 | 通话类型 | 1=AI外呼, 3=AI呼入 |

## 实际数据示例

```
enterprise_id: 337114289470678650
sql_date_hour: 2026-03-12 14:00:00
ai_number: 50         ← 企业购买了50路并发
call_task_id: 12345678
bot_num: 43           ← 该小时实际使用了43路并发
call_num: 1250        ← 发起了1250次呼叫
connected_num: 875    ← 875次接通
ai_call_duration: 52500 ← AI通话时长共52500秒
```

## 关联关系

```
data_statistics.call_task_entbot_num
    ├── enterprise_id ──→ integrated_data.Dim_Enterprise.Enterprise_ID
    ├── call_task_id ───→ crm.dim_call_task.call_task_id
    └── template_code ──→ om_statistics.consolidated_cdr.TemplateCode
```

## 注意事项

1. `bot_num` 是小时内的峰值并发数，不是累计值
2. `ai_number` 是企业购买的上限，`bot_num > ai_number` 表示超量使用
3. 查询时建议加 `sql_date_hour` 时间范围过滤
4. `call_type` 只有 1（AI外呼）和 3（AI呼入），不含人工通话

## 典型查询

```sql
-- 监控企业机器人使用率（超量预警）
SELECT 
    enterprise_id,
    sql_date_hour,
    ai_number AS purchased,
    max(bot_num) AS peak_concurrent,
    max(bot_num) / ai_number AS utilization_rate
FROM data_statistics.call_task_entbot_num
WHERE sql_date_hour >= today() - 7
GROUP BY enterprise_id, sql_date_hour, ai_number
HAVING utilization_rate > 0.9  -- 使用率超过90%
ORDER BY utilization_rate DESC;

-- 统计某企业某月的AI通话时长
SELECT 
    enterprise_id,
    toYYYYMM(sql_date_hour) AS year_month,
    sum(ai_call_duration) / 3600 AS ai_hours,
    sum(connected_num) AS total_connected
FROM data_statistics.call_task_entbot_num
WHERE enterprise_id = ?
    AND sql_date_hour >= '2026-03-01'
GROUP BY enterprise_id, year_month;
```
