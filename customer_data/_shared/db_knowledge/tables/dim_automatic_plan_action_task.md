# dim_automatic_plan_action_task — 策略节点任务关联表

> **数据库**: crm | **数据层**: DWD（关联表）
> **更新时间**: 2025-07 | **版本**: v3.0（基于实际字段验证）

---

## 一、业务定位

这是策略外呼体系中的**核心桥梁表**，记录每个 Plan（批次）流转到每个 Action（节点）时生成的具体 Task（任务）。

```
dim_automatic_plan (批次)
    × dim_automatic_action (节点)
    → dim_automatic_plan_action_task (本表：Plan×Action 生成的 Task 记录)
    → dim_call_task / SMS任务 / WhatsApp任务 (具体执行任务)
```

**核心逻辑**：
- 一个 Plan 有多个 Action 节点（由策略定义）
- 线索流转到某个 Action 节点时，系统在本表生成一条记录，并创建对应的执行任务
- `type=1` 的记录关联 `dim_call_task`（外呼任务）
- `task_id` 与 `dim_call_task.call_task_id` **100% 匹配**（已验证）

---

## 二、完整字段说明

| 字段名 | 类型 | 业务含义 | 备注 |
|--------|------|---------|------|
| `id` | Int64 | **主键** | 本表自增 ID |
| `automatic_task_id` | Nullable(Int64) | 所属策略 ID | 关联 `dim_automatic_task.id` |
| `plan_id` | Nullable(Int64) | 所属批次 ID | 关联 `dim_automatic_plan.id` |
| `action_id` | Nullable(String) | 所属节点 ID | 关联 `dim_automatic_action.action_id` |
| `task_id` | Nullable(Int64) | **生成的任务 ID** ⭐ | 关联 `dim_call_task.call_task_id`（type=1时）|
| `process_id` | Nullable(Int64) | 流程 ID | 策略流程实例 ID |
| `name` | Nullable(String) | 任务名称 | 自动生成 |
| `type` | Nullable(Int8) | **任务类型** ⭐ | 见下方枚举 |
| `status` | Nullable(Int8) | 任务状态 | |
| `enterprise_id` | Nullable(Int64) | 企业 ID | |
| `is_delete` | Nullable(Int8) | 软删除标志 | **查询必须过滤 `is_delete = 0`** |
| `create_time` | Nullable(DateTime) | 记录创建时间 | |
| `create_by` | Nullable(Int64) | 创建人 ID | |
| `update_time` | Nullable(DateTime) | 更新时间 | |
| `update_by` | Nullable(Int64) | 更新人 ID | |
| `_ts` | DateTime64(3) | CDC 时间戳 | |
| `_op` | String | CDC 操作类型 | `c`/`u`/`d` |

---

## 三、type 字段枚举（已验证）

| type 值 | 含义 | 关联目标表 | 实际记录数 | 匹配率 |
|---------|------|-----------|-----------|--------|
| **1** | **外呼任务** | `dim_call_task.call_task_id` | 1,335,353 | **100%** ✅ |
| **2** | SMS 任务 | SMS 任务表 | 8,407 | 100% ✅ |
| **3** | WhatsApp 任务 | WA 任务表 | 6,141 | 100% ✅ |
| **4** | Email 任务 | Email 任务表 | 1 | 100% ✅ |
| **16** | Viber 任务 | Viber 任务表 | 9 | 100% ✅ |

> ✅ **验证结论**：`task_id → dim_call_task.call_task_id` 关联率 **100%**，可放心使用

---

## 四、表关联关系

```
dim_automatic_task.id
    → dim_automatic_plan_action_task.automatic_task_id

dim_automatic_plan.id
    → dim_automatic_plan_action_task.plan_id

dim_automatic_action.action_id
    → dim_automatic_plan_action_task.action_id

dim_automatic_plan_action_task.task_id（type=1）
    → dim_call_task.call_task_id（100% 匹配，已验证）

dim_call_task.call_task_id
    → realtime_dwd_crm_call_record.call_task_id
```

---

## 五、常用查询模板

### 5.1 查询某策略所有 Plan 的外呼任务

```sql
SELECT
    apt.plan_id,
    apt.action_id,
    apt.task_id,
    apt.type,
    apt.status,
    apt.create_time
FROM crm.dim_automatic_plan_action_task apt
WHERE apt.is_delete = 0
  AND apt.automatic_task_id = {automatic_task_id}
  AND apt.type = 1  -- 只看外呼任务
ORDER BY apt.create_time DESC;
```

### 5.2 策略外呼完整链路查询（策略→任务→呼叫记录）

```sql
-- 从策略 ID 追溯到呼叫记录
SELECT
    at.name        AS strategy_name,
    ap.name        AS plan_name,
    aa.name        AS action_name,
    ct.call_task_id,
    ct.name        AS task_name,
    ct.template_code,
    count(cr.call_record_id)                          AS total_calls,
    countIf(cr.call_duration > 0)                     AS connected_calls,
    round(avg(cr.call_duration), 1)                   AS avg_duration_sec
FROM crm.dim_automatic_plan_action_task apt
JOIN crm.dim_automatic_task  at ON apt.automatic_task_id = at.id
JOIN crm.dim_automatic_plan  ap ON apt.plan_id           = ap.id
JOIN crm.dim_automatic_action aa ON apt.action_id        = aa.action_id
JOIN crm.dim_call_task        ct ON apt.task_id          = ct.call_task_id
LEFT JOIN crm.realtime_dwd_crm_call_record cr
       ON ct.call_task_id = cr.call_task_id
      AND cr.is_delete = 0
      AND cr.call_start_time >= addDays(now(), -30)
WHERE apt.is_delete = 0
  AND ct.is_delete  = 0
  AND apt.type      = 1
  AND apt.automatic_task_id = {automatic_task_id}
GROUP BY
    at.name, ap.name, aa.name,
    ct.call_task_id, ct.name, ct.template_code
ORDER BY total_calls DESC;
```

### 5.3 统计某策略各渠道任务数量

```sql
SELECT
    apt.type,
    multiIf(
        apt.type = 1,  '外呼',
        apt.type = 2,  'SMS',
        apt.type = 3,  'WhatsApp',
        apt.type = 4,  'Email',
        apt.type = 16, 'Viber',
        '未知'
    ) AS channel,
    count() AS task_count
FROM crm.dim_automatic_plan_action_task apt
WHERE apt.is_delete = 0
  AND apt.automatic_task_id = {automatic_task_id}
GROUP BY apt.type
ORDER BY task_count DESC;
```

---

## 六、注意事项

1. **此表是策略外呼分析的必经之路**：从策略追溯到呼叫记录，必须经过此表
2. **type=1 才关联 dim_call_task**：其他 type 关联各自渠道的任务表
3. **task_id 与 call_task_id 100% 匹配**：无需担心关联丢失
4. **action_id 是 String 类型**：与 `dim_automatic_action.action_id` 关联时注意类型一致
