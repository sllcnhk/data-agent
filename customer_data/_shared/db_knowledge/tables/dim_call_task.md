# dim_call_task — 外呼任务表

> **数据库**: crm | **数据层**: DWD（维度表）
> **更新时间**: 2025-07 | **版本**: v3.0（基于实际字段验证）

---

## 一、业务定位

外呼任务的核心配置表，是**两种外呼模式的汇合点**：

| 来源 | 创建方式 | 标识字段 |
|------|---------|---------|
| **策略外呼** | 由 `dim_automatic_plan_action_task` 中 type=1 的节点自动生成 | `is_automatic_task = 1` |
| **手动外呼** | 企业用户直接在系统中创建 | `is_automatic_task = 0` |

一个外呼任务对应一批线索的呼叫执行计划，包含话术配置、重拨规则、并发设置、时间窗口等。

---

## 二、完整字段说明

### 2.1 核心标识字段

| 字段名 | 类型 | 业务含义 | 备注 |
|--------|------|---------|------|
| `call_task_id` | Int64 | **主键** | 关联 `dim_automatic_plan_action_task.task_id`（策略场景）|
| `enterprise_id` | Nullable(Int64) | 所属企业 ID | 所有查询必须带此条件 |
| `name` | Nullable(String) | 任务名称 | 用户自定义 |
| `is_automatic_task` | Nullable(Int8) | **是否策略生成任务** | **0=手动, 1=策略生成** ⭐ |
| `is_delete` | Nullable(Int8) | 软删除标志 | **查询必须过滤 is_delete = 0** |

### 2.2 任务类型字段

| 字段名 | 类型 | 业务含义 | 枚举值 |
|--------|------|---------|--------|
| `call_task_type` | Nullable(String) | 任务类型 | `AI`=正常AI外呼; `PERSIST`=TTS任务; `GROUP`=坐席组外呼; `TTS_AI`=真人+TTS |
| `call_type` | Int8 | 呼叫模式 | `0`=outbound（普通外呼）; `1`=predict（预测式外呼）|
| `call_center_task_model` | Nullable(Int8) | 呼叫中心模式 | `0`=current; `1`=forecast |
| `agent_type` | Nullable(String) | 坐席类型 | `SYSTEM`=系统内人工坐席; `THIRD`=第三方坐席 |

### 2.3 话术配置字段

| 字段名 | 类型 | 业务含义 |
|--------|------|---------|
| `template_code` | Nullable(String) | **话术模板 code** ⭐，关联 `Dim_Dialogue.template_code` |
| `template_name` | Nullable(String) | 话术模板名称（冗余） |
| `call_phones` | Nullable(String) | 主叫号码，多个用逗号分隔 |
| `call_phone_params` | Nullable(String) | 主叫号码详细信息（JSON） |

### 2.4 重拨配置字段（重要）

> 重拨是指同一任务内，对未接通线索按配置自动重新拨打

| 字段名 | 类型 | 业务含义 |
|--------|------|---------|
| `is_reboot` | Nullable(String) | 是否开启自动重拨：`YES`/`NO` |
| `reboot_status` | Nullable(String) | 触发重拨的通话结果状态（如未接通） |
| `reboot_num` | Nullable(Int32) | 配置的重拨次数上限 |
| `reboot_period` | Nullable(Int32) | 重拨时间间隔（分钟） |
| `reboot_time` | Nullable(String) | 重拨指定时间 |
| `already_reboot_num` | Nullable(Int32) | 已实际重拨次数 |
| `last_reboot_time` | Nullable(DateTime) | 最后一次重拨时间 |
| `is_wait_reboot` | Nullable(String) | 是否等待重拨：`YES`/`NO` |
| `again_call_times` | Nullable(Int32) | 重呼次数（另一套重呼配置） |
| `again_call_interval` | Nullable(Int32) | 重呼间隔（分钟） |
| `enhance_reboot` | Nullable(String) | 增强重呼开关：`YES`/`NO` |
| `enhance_reboot_type` | Nullable(String) | 增强重呼触发的状态 |
| `reboot_call_duration` | Nullable(Int32) | 重呼 AI 通话时长阈值（秒） |
| `chase_redial_config` | Nullable(String) | Chase Redial 配置（JSON） |

### 2.5 任务状态字段

| 字段名 | 类型 | 业务含义 | 枚举值 |
|--------|------|---------|--------|
| `status` | Nullable(Int8) | 任务状态 | `1`=未启动; `2`=进行中; `3`=已停止; `4`=已完成 |
| `status_info` | Nullable(String) | 任务状态描述 | |
| `start_time` | Nullable(DateTime) | 任务开始时间 | |
| `end_time` | Nullable(DateTime) | 任务结束时间 | |
| `complete_time` | Nullable(DateTime) | 任务完成时间 | |
| `persistent` | Nullable(String) | 是否永久有效：`YES`=持久; `NO`=有时间限制 | |

### 2.6 并发与性能配置

| 字段名 | 类型 | 业务含义 |
|--------|------|---------|
| `concurrent_rate` | Float64 | 并发比率 |
| `is_concurrent` | Nullable(Int8) | 是否开启并发：`0`=否; `1`=是 |
| `ai_count` | Nullable(Int32) | AI 数量 |
| `ai_count_setting` | Nullable(Int32) | AI 数量设置（v1.29.1+） |
| `advance_mode` | Nullable(String) | 高级模式配置 |
| `call_priority` | Nullable(Int8) | 呼叫优先级：`0`=重拨优先; `1`=新呼优先 |
| `init_connection_rate` | Nullable(Float64) | 初始接通率 |
| `max_abandon_rate` | Nullable(Float64) | 最大放弃率 |
| `sliding_window_interval` | Nullable(Int32) | 滑动窗口间隔（秒） |

### 2.7 DNC（勿扰名单）配置

| 字段名 | 类型 | 业务含义 |
|--------|------|---------|
| `dnc` | Nullable(String) | 是否开启 DNC 校验 |
| `dnc_type` | Nullable(Int8) | DNC 校验类型：`0`=B3线路; `1`=Wiz DNC |

### 2.8 坐席组配置（人工外呼）

| 字段名 | 类型 | 业务含义 |
|--------|------|---------|
| `call_group_ids` | Nullable(String) | 坐席组 ID 列表 |
| `call_group_params` | Nullable(String) | 坐席组详细参数（JSON） |
| `sms_template_id` | Nullable(Int64) | 挂机短信模板 ID |
| `sms_template_name` | Nullable(String) | 挂机短信模板名称 |

### 2.9 时间与操作字段

| 字段名 | 类型 | 业务含义 |
|--------|------|---------|
| `call_week_period` | Nullable(String) | 外呼时间段配置（JSON，如工作日 9-18 点） |
| `early_media_timeout` | Nullable(Int32) | 振铃等待时长（秒） |
| `create_time` | Nullable(DateTime) | 任务创建时间 |
| `update_time` | Nullable(DateTime) | 最后更新时间 |
| `user_id` | Nullable(Int64) | 任务创建者 ID |
| `import_type` | Nullable(Int32) | 线索导入方式：`0`=Excel; `1`=API |

---

## 三、关键验证数据

### 3.1 is_automatic_task 实际分布（全量）

| is_automatic_task | 含义 | 数量 | 占比 |
|-------------------|------|------|------|
| 1 | 策略生成任务 | 1,323,704 | ~96.9% |
| 0 | 手动创建任务 | 42,113 | ~3.1% |
| NULL | 历史数据（字段为空） | 3,293 | ~0.2% |

> ⚠️ **重要发现**: 策略生成任务占绝大多数（96.9%），手动任务仅占少数。
> 历史数据中 `is_automatic_task` 可能为 NULL，查询时需兼容：
> `WHERE is_automatic_task = 0 OR is_automatic_task IS NULL`（手动任务）

---

## 四、表关联关系

```
dim_automatic_plan_action_task.task_id → dim_call_task.call_task_id
                                         （策略外呼场景，is_automatic_task=1）

dim_call_task.call_task_id → dim_call_task_customer.call_task_id
                              （1:N，任务下的线索明细）

dim_call_task.call_task_id → realtime_dwd_crm_call_record.call_task_id
                              （1:N，任务产生的呼叫记录）

dim_call_task.template_code → Dim_Dialogue.template_code
                               （话术关联）
```

---

## 五、常用查询模板

### 5.1 区分策略外呼 vs 手动外呼任务

```sql
-- 策略外呼任务
SELECT call_task_id, name, template_code, create_time
FROM crm.dim_call_task
WHERE is_delete = 0
  AND is_automatic_task = 1
ORDER BY create_time DESC
LIMIT 100;

-- 手动外呼任务（兼容历史 NULL）
SELECT call_task_id, name, template_code, create_time
FROM crm.dim_call_task
WHERE is_delete = 0
  AND (is_automatic_task = 0 OR is_automatic_task IS NULL)
ORDER BY create_time DESC
LIMIT 100;
```

### 5.2 查询某企业近期任务状态分布

```sql
SELECT
    status,
    multiIf(status=1,'未启动', status=2,'进行中', status=3,'已停止', status=4,'已完成', '未知') AS status_name,
    count() AS cnt
FROM crm.dim_call_task
WHERE is_delete = 0
  AND enterprise_id = {enterprise_id}
  AND create_time >= addDays(now(), -30)
GROUP BY status
ORDER BY cnt DESC;
```

### 5.3 查询开启重拨的任务

```sql
SELECT
    call_task_id, name,
    reboot_num,
    reboot_period,
    already_reboot_num,
    is_automatic_task
FROM crm.dim_call_task
WHERE is_delete = 0
  AND is_reboot = 'YES'
  AND enterprise_id = {enterprise_id}
ORDER BY create_time DESC
LIMIT 50;
```

### 5.4 从策略追溯到外呼任务

```sql
-- 从策略 ID 找到该策略所有外呼任务
SELECT
    at.name AS strategy_name,
    ap.name AS plan_name,
    aa.name AS action_name,
    ct.call_task_id,
    ct.name AS task_name,
    ct.status,
    ct.template_code
FROM crm.dim_automatic_plan_action_task apt
JOIN crm.dim_call_task ct ON apt.task_id = ct.call_task_id
JOIN crm.dim_automatic_task at ON apt.automatic_task_id = at.id
JOIN crm.dim_automatic_plan ap ON apt.plan_id = ap.id
JOIN crm.dim_automatic_action aa ON apt.action_id = aa.action_id
WHERE apt.is_delete = 0
  AND ct.is_delete = 0
  AND apt.type = 1
  AND apt.automatic_task_id = {automatic_task_id}
ORDER BY ct.create_time DESC;
```

---

## 六、注意事项

1. **is_automatic_task 字段**：`1`=策略任务，`0`=手动任务，历史数据可能为 NULL
2. **重拨配置复杂**：存在两套重拨字段（`reboot_*` 和 `again_call_*`），使用时需确认业务场景
3. **call_task_type**：区分 AI 外呼（`AI`）和人工外呼（`GROUP`），影响计费逻辑
4. **template_code**：话术编码，是连接呼叫记录与账单的关键字段
5. **此表无时间分区**：查询必须带 `enterprise_id` 或 `create_time` 范围过滤，避免全表扫描
