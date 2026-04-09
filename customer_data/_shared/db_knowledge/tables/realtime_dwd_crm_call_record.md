# realtime_dwd_crm_call_record — 外呼通话记录事实表

> **数据库**: crm | **数据层**: DWD（加工宽表）
> **更新时间**: 2025-07 | **版本**: v3.0（基于实际字段验证，78字段完整版）

---

## 一、业务定位

**外呼业务最核心的事实宽表**，每行代表一次完整的呼叫记录。

- 是一张**加工过的宽表**，冗余了任务名称、话术名称、意图标签等维度信息，减少 JOIN
- 同时支持**策略外呼**和**手动外呼**两种场景
- 通过 `automatic_task_id / plan_id / action_id` 字段关联策略维度
- 通过 `call_task_id / call_task_customer_id` 关联任务维度

### 策略字段填充率（近7天，已验证）

| 字段 | 有值记录数 | 总记录数 | 填充率 | 含义 |
|------|-----------|---------|--------|------|
| `automatic_task_id > 0` | 5,796,414 | 37,435,273 | **15.5%** | 策略外呼记录占比 |
| `plan_id > 0` | 5,796,414 | 37,435,273 | 15.5% | 与 automatic_task_id 完全一致 |
| `action_id != ''` | 5,796,414 | 37,435,273 | 15.5% | 三个策略字段同步填充 |
| `call_task_id > 0` | 37,434,840 | 37,435,273 | **~100%** | 几乎所有记录都有任务 ID |

> **结论**：近7天约 15.5% 的呼叫来自策略外呼，84.5% 来自手动外呼任务。

---

## 二、完整字段说明（78个字段）

### 2.1 核心标识字段

| 字段名 | 类型 | 业务含义 | 备注 |
|--------|------|---------|------|
| `call_record_id` | Int64 | **主键** | 全局唯一呼叫记录 ID |
| `enterprise_id` | Int64 | 企业 ID | **所有查询必须带此条件** |
| `call_task_id` | Nullable(Int64) | 外呼任务 ID | 关联 `dim_call_task.call_task_id`，~100% 填充 |
| `call_task_customer_id` | Nullable(Int64) | 任务线索 ID | 关联 `dim_call_task_customer.call_task_customer_id` |
| `customer_id` | Nullable(Int64) | 客户 ID | |
| `user_id` | Nullable(Int64) | 操作用户 ID | |
| `call_id` | Nullable(String) | 通话唯一 ID | 对接电话系统的通话标识 |
| `contact_id` | Nullable(String) | 联系人 ID | |

### 2.2 策略关联字段（策略外呼时填充）

| 字段名 | 类型 | 业务含义 | 填充率 |
|--------|------|---------|--------|
| `automatic_task_id` | - | 策略 ID | 15.5%（策略外呼时有值）|
| `plan_id` | - | 批次 ID | 15.5%（同上）|
| `action_id` | - | 节点 ID | 15.5%（同上）|
| `contact_batch_id` | - | 联系人批次 ID | |

> **区分策略 vs 手动外呼**：
> - 策略外呼：`automatic_task_id > 0`
> - 手动外呼：`automatic_task_id IS NULL OR automatic_task_id = 0`

### 2.3 呼叫时间字段

| 字段名 | 类型 | 业务含义 | 备注 |
|--------|------|---------|------|
| `call_start_time` | DateTime | **呼叫开始时间** ⭐ | **分区/过滤主字段，所有查询必须带** |
| `create_time` | Nullable(DateTime) | 记录创建时间 | |
| `update_time` | Nullable(DateTime) | 最后更新时间 | |

### 2.4 通话时长字段

| 字段名 | 类型 | 业务含义 | 单位 |
|--------|------|---------|------|
| `call_duration` | Nullable(Int32) | **总通话时长** ⭐ | 秒，`> 0` 表示接通 |
| `ai_call_duration` | Nullable(Int32) | AI 通话时长 | 秒 |
| `agent_call_duration` | Nullable(Int32) | 人工坐席通话时长 | 秒 |
| `ring_duration` | Nullable(Int32) | 振铃时长 | 秒 |
| `inbound_wait_time` | Nullable(Int32) | 呼入等待时长 | 秒 |

### 2.5 呼叫类型字段

| 字段名 | 类型 | 业务含义 | 枚举值（近7天实测）|
|--------|------|---------|-----------------|
| `call_type` | Nullable(Int8) | **通话类型** ⭐ | 见下方详细枚举 |
| `call_code` | Nullable(String) | 呼叫结果编码 | |
| `call_code_type` | Nullable(Int32) | 呼叫结果类型 | |
| `agent_type` | Nullable(String) | 坐席类型 | `SYSTEM`/`THIRD` |
| `transfer_status` | Nullable(Int8) | 转人工状态 | |

#### call_type 枚举（近7天实际分布）

| call_type | 含义 | 数量 | 占比 |
|-----------|------|------|------|
| **1** | **AI 外呼（主流）** | 37,402,736 | **99.91%** |
| 3 | 人工外呼 | 32,216 | 0.09% |
| 12 | WhatsApp Inbound | 433 | ~0% |
| 4 | 预测式外呼 | 73 | ~0% |
| 6 | 其他 | 7 | ~0% |

### 2.6 话术与意图字段

| 字段名 | 类型 | 业务含义 | 备注 |
|--------|------|---------|------|
| `template_code` | Nullable(String) | **话术模板 code** ⭐ | 关联计费，重要字段 |
| `template_name` | Nullable(String) | 话术模板名称（冗余）| |
| `template_version` | Nullable(String) | 话术版本 | |
| `intent_code` | Nullable(String) | **AI 识别意图编码** ⭐ | |
| `intent_name` | Nullable(String) | 意图名称（冗余）| |
| `intent_rule` | Nullable(String) | 意图规则 | |
| `artificial_intent_code` | Nullable(String) | 人工标注意图编码 | 人工复核后填写 |
| `level_rule_id` | Nullable(Int64) | 分级规则 ID | |
| `level_rule_name` | Nullable(String) | 分级规则名称 | |

### 2.7 标签字段

| 字段名 | 类型 | 业务含义 |
|--------|------|---------|
| `tag` | Nullable(String) | 通话标签（JSON 数组）|
| `customer_tag` | Nullable(String) | 客户标签 |
| `useful_tag` | Nullable(String) | 有效标签 |
| `exclude_tag` | Nullable(String) | 排除标签 |
| `tag_id_array` | Nullable(String) | 标签 ID 数组 |
| `tag_value_array` | Nullable(String) | 标签值数组 |

### 2.8 对话与录音字段

| 字段名 | 类型 | 业务含义 |
|--------|------|---------|
| `dialogue_audio_url` | Nullable(String) | 录音文件 URL |
| `dialog_extra` | Nullable(String) | 对话扩展信息（JSON）|
| `dialog_count` | Nullable(Int32) | 对话轮次数 |
| `dialog_round` | Nullable(Int32) | 对话轮数 |
| `node_path` | Nullable(String) | 话术节点路径 |
| `audio` | Nullable(String) | 音频信息 |

### 2.9 质检与评分字段

| 字段名 | 类型 | 业务含义 |
|--------|------|---------|
| `score` | Nullable(String) | 质检评分 |
| `score_detail` | Nullable(String) | 评分详情（JSON）|
| `inspection_result` | Nullable(String) | 质检结果 |
| `user_inspection_result` | Nullable(String) | 人工质检结果 |
| `issues_type` | Nullable(String) | 问题类型 |
| `rule_json` | Nullable(String) | 规则 JSON |
| `has_issue` | Nullable(String) | 是否有问题 |

### 2.10 重拨相关字段

| 字段名 | 类型 | 业务含义 |
|--------|------|---------|
| `is_reboot` | Nullable(String) | 是否重拨记录 |
| `parent_task_customer_id` | Nullable(Int64) | 父线索 ID（重拨场景）|
| `final_parent_task_customer_id` | Nullable(Int64) | 最终根父线索 ID |
| `customer_id_parent` | Nullable(Int64) | 父客户 ID |
| `chasing_redail` | Nullable(String) | Chase 重拨标志 |
| `chase_already_reboot_num` | Nullable(Int32) | Chase 已重拨次数 |
| `intent_times` | Nullable(String) | 意图次数记录 |

### 2.11 其他字段

| 字段名 | 类型 | 业务含义 |
|--------|------|---------|
| `call_task_name` | Nullable(String) | 任务名称（冗余）|
| `user_name` | Nullable(String) | 用户名（冗余）|
| `customer_phone` | Nullable(String) | 客户手机号 |
| `phone` | Nullable(String) | 实际拨打号码 |
| `is_delete` | Nullable(Int8) | 软删除标志 |
| `is_read` | Nullable(Int8) | 是否已读 |
| `is_callbacked` | Nullable(Int8) | 是否已回调 |
| `modify_id` | Nullable(Int64) | 修改记录 ID |
| `scene_id` | Nullable(Int64) | 场景 ID |
| `fellow_up` | Nullable(String) | 跟进信息 |
| `remark` | Nullable(String) | 备注 |
| `first_hangup_desc` | Nullable(String) | 首次挂机描述 |
| `inbound_prefix` | Nullable(String) | 呼入前缀 |
| `origin_inbound_prefix` | Nullable(String) | 原始呼入前缀 |
| `inbound_discard` | Nullable(Int8) | 呼入丢弃标志 |
| `keep_record_after_transfer_switch` | Nullable(Int8) | 转接后保留记录开关 |
| `server_info` | Nullable(String) | 服务器信息 |
| `ts` | Nullable(DateTime) | 时间戳 |

---

## 三、接通率计算口径

```sql
-- 标准接通率口径：call_duration > 0 表示接通
SELECT
    count()                              AS total_calls,
    countIf(call_duration > 0)           AS connected_calls,
    round(countIf(call_duration > 0) * 100.0 / count(), 2) AS connect_rate_pct,
    round(avg(if(call_duration > 0, call_duration, NULL)), 1) AS avg_duration_sec
FROM crm.realtime_dwd_crm_call_record
PREWHERE call_start_time >= addDays(now(), -7)
WHERE is_delete = 0
  AND enterprise_id = {enterprise_id};
```

---

## 四、表关联关系

```
realtime_dwd_crm_call_record.call_task_id
    → dim_call_task.call_task_id

realtime_dwd_crm_call_record.call_task_customer_id
    → dim_call_task_customer.call_task_customer_id

realtime_dwd_crm_call_record.automatic_task_id（策略外呼时有值）
    → dim_automatic_task.id

realtime_dwd_crm_call_record.plan_id（策略外呼时有值）
    → dim_automatic_plan.id

realtime_dwd_crm_call_record.action_id（策略外呼时有值）
    → dim_automatic_action.action_id

realtime_dwd_crm_call_record.call_record_id
    → realtime_ods_call_record_dialog_detail.call_record_id（对话明细）
    → realtime_ods_crm_call_record_intention.call_record_id（意图标签）

realtime_dwd_crm_call_record.template_code
    → Fact_Bill_Usage.template_code（计费关联）
```

---

## 五、常用查询模板

### 5.1 策略外呼 vs 手动外呼对比分析

```sql
SELECT
    if(automatic_task_id > 0, '策略外呼', '手动外呼') AS call_mode,
    count()                                        AS total_calls,
    countIf(call_duration > 0)                     AS connected_calls,
    round(countIf(call_duration > 0) * 100.0 / count(), 2) AS connect_rate_pct,
    round(avg(if(call_duration > 0, call_duration, NULL)), 1) AS avg_duration_sec
FROM crm.realtime_dwd_crm_call_record
PREWHERE call_start_time >= addDays(now(), -30)
WHERE is_delete = 0
  AND enterprise_id = {enterprise_id}
GROUP BY call_mode;
```

### 5.2 按策略维度聚合呼叫效果

```sql
SELECT
    cr.automatic_task_id,
    at.name                                        AS strategy_name,
    count()                                        AS total_calls,
    countIf(cr.call_duration > 0)                  AS connected,
    round(countIf(cr.call_duration > 0) * 100.0 / count(), 2) AS connect_rate_pct,
    round(avg(if(cr.call_duration > 0, cr.call_duration, NULL)), 1) AS avg_duration
FROM crm.realtime_dwd_crm_call_record cr
LEFT JOIN crm.dim_automatic_task at ON cr.automatic_task_id = at.id
PREWHERE cr.call_start_time >= addDays(now(), -30)
WHERE cr.is_delete = 0
  AND cr.automatic_task_id > 0
  AND cr.enterprise_id = {enterprise_id}
GROUP BY cr.automatic_task_id, at.name
ORDER BY total_calls DESC
LIMIT 20;
```

### 5.3 意图分布分析

```sql
SELECT
    intent_code,
    intent_name,
    count()                                     AS cnt,
    round(count() * 100.0 / sum(count()) OVER(), 2) AS pct
FROM crm.realtime_dwd_crm_call_record
PREWHERE call_start_time >= addDays(now(), -7)
WHERE is_delete = 0
  AND call_duration > 0
  AND enterprise_id = {enterprise_id}
GROUP BY intent_code, intent_name
ORDER BY cnt DESC
LIMIT 20;
```

### 5.4 每日呼叫趋势

```sql
SELECT
    toDate(call_start_time)                    AS call_date,
    count()                                    AS total_calls,
    countIf(call_duration > 0)                 AS connected_calls,
    round(countIf(call_duration > 0) * 100.0 / count(), 2) AS connect_rate_pct
FROM crm.realtime_dwd_crm_call_record
PREWHERE call_start_time >= addDays(now(), -30)
WHERE is_delete = 0
  AND enterprise_id = {enterprise_id}
GROUP BY call_date
ORDER BY call_date;
```

---

## 六、注意事项

1. **`call_start_time` 是查询性能关键**：所有查询必须用 `PREWHERE call_start_time >= ...` 过滤
2. **接通判断用 `call_duration > 0`**：不要用 `call_code` 或其他字段
3. **策略外呼判断用 `automatic_task_id > 0`**：三个策略字段（automatic_task_id/plan_id/action_id）同步填充
4. **is_delete 必须过滤**：`WHERE is_delete = 0`
5. **此表字段极多（78个）**：查询时只 SELECT 需要的字段，禁止 SELECT *
6. **宽表已冗余维度信息**：简单分析直接用本表字段，无需 JOIN 维度表
