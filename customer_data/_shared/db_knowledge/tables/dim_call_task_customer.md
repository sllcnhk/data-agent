# dim_call_task_customer — 外呼任务线索明细表

> **数据库**: crm | **数据层**: DWD（明细表）
> **更新时间**: 2025-07 | **版本**: v3.0（基于实际字段验证）

---

## 一、业务定位

记录进入**外呼任务（dim_call_task）的线索明细**，包含父线索和子线索：

| 线索类型 | 标识 | 创建时机 | 用途 |
|---------|------|---------|------|
| **父线索** | `parent_call_task_customer_id IS NULL` | 线索导入任务时创建 | 原始线索记录 |
| **子线索（重拨）** | `parent_call_task_customer_id > 0` | 任务重拨配置触发时生成 | 重拨轮次记录 |

> ⚠️ **验证发现**：父线索标识为 `IS NULL`（而非 `= 0`），近7天数据中：
> - `NULL`（父线索）：21,508,352 条（51.6%）
> - 有值（子线索/重拨）：20,177,359 条（48.4%）
> - `= 0`（极少量历史数据）：3,014 条

---

## 二、完整字段说明

| 字段名 | 类型 | 业务含义 | 备注 |
|--------|------|---------|------|
| `call_task_customer_id` | Int64 | **主键** | 关联 `realtime_dwd_crm_call_record.call_task_customer_id` |
| `call_task_id` | Int64 | **外呼任务 ID** | 关联 `dim_call_task.call_task_id` |
| `customer_id` | Int64 | 客户 ID | 关联客户维度 |
| `user_id` | Int64 | 操作用户 ID | |
| `parent_call_task_customer_id` | Nullable(Int64) | **父线索 ID** ⭐ | `NULL`=父线索；有值=子线索（重拨场景） |
| `contact_id` | Nullable(String) | 联系人 ID | 新版本字段 |
| `status` | Int8 | 线索状态 | 见下方枚举 |
| `connect_status` | Int8 | 接通状态 | 默认 1 |
| `bus_redial_status` | Nullable(Int8) | 业务重拨状态 | 重拨调度相关 |
| `is_delete` | Int8 | 软删除标志 | **查询必须过滤 `is_delete = 0`** |
| `is_dispense` | Int8 | 是否已分配 | `0`=未分配; `1`=已分配 |
| `origin_type` | Nullable(Int8) | 线索来源类型 | |
| `dnc_status` | Nullable(Int8) | DNC 校验状态 | |
| `number_status` | Nullable(Int8) | 号码状态 | |
| `tts_generate_state` | Nullable(String) | TTS 生成状态 | |
| `extra_json` | Nullable(String) | 扩展信息（JSON）| **TTL=15天**，超期自动清空 |
| `export_time` | Nullable(DateTime) | 导出时间 | |
| `checked_time` | Nullable(DateTime) | 校验时间 | |
| `txn_uuid` | Nullable(String) | 事务 UUID | |
| `create_time` | DateTime | 创建时间 | ⭐ 可用于时间范围过滤 |
| `update_time` | DateTime | 更新时间 | |
| `_ts` | DateTime64(3) | CDC 时间戳 | 数据同步时间 |
| `_op` | String | CDC 操作类型 | `c`=创建; `u`=更新; `d`=删除 |

---

## 三、父子线索机制详解

### 3.1 父子线索的产生

```
线索导入任务
    ↓
创建父线索（parent_call_task_customer_id = NULL）
    ↓
任务执行，呼叫父线索
    ↓
若配置了重拨（is_reboot='YES'）且满足重拨条件（如未接通）
    ↓
生成子线索（parent_call_task_customer_id = 父线索的 call_task_customer_id）
    ↓
子线索进入重拨队列，按 reboot_period 间隔重新呼叫
```

### 3.2 重拨子线索的数量

- `dim_call_task.reboot_num` 决定最大重拨次数
- 每次重拨生成一条子线索记录
- 子线索的 `parent_call_task_customer_id` 指向**直接父线索**（非根父线索）

### 3.3 统计口径说明

```sql
-- 统计实际线索数（去重，只数父线索）
SELECT count() AS lead_count
FROM crm.dim_call_task_customer
PREWHERE create_time >= addDays(now(), -30)
WHERE is_delete = 0
  AND parent_call_task_customer_id IS NULL  -- 只统计父线索
  AND call_task_id = {task_id};

-- 统计总呼叫次数（含重拨）
SELECT count() AS total_dials
FROM crm.dim_call_task_customer
PREWHERE create_time >= addDays(now(), -30)
WHERE is_delete = 0
  AND call_task_id = {task_id};
```

---

## 四、表关联关系

```
dim_call_task.call_task_id
    → dim_call_task_customer.call_task_id（1:N）

dim_call_task_customer.call_task_customer_id
    → realtime_dwd_crm_call_record.call_task_customer_id（1:N，一条线索可有多次呼叫）

dim_call_task_customer.parent_call_task_customer_id
    → dim_call_task_customer.call_task_customer_id（自关联，父子线索）
```

---

## 五、常用查询模板

### 5.1 查询某任务的父线索列表

```sql
SELECT
    call_task_customer_id,
    customer_id,
    contact_id,
    status,
    create_time
FROM crm.dim_call_task_customer
PREWHERE create_time >= addDays(now(), -30)
WHERE is_delete = 0
  AND call_task_id = {task_id}
  AND parent_call_task_customer_id IS NULL  -- 只取父线索
ORDER BY create_time DESC
LIMIT 100;
```

### 5.2 查询某线索的完整重拨链路

```sql
-- 找到某父线索的所有重拨子线索
SELECT
    call_task_customer_id,
    parent_call_task_customer_id,
    status,
    bus_redial_status,
    create_time
FROM crm.dim_call_task_customer
WHERE is_delete = 0
  AND (
      call_task_customer_id = {parent_id}  -- 父线索本身
      OR parent_call_task_customer_id = {parent_id}  -- 子线索
  )
ORDER BY create_time;
```

### 5.3 统计任务线索的父子比例（重拨率）

```sql
SELECT
    call_task_id,
    countIf(parent_call_task_customer_id IS NULL) AS parent_leads,
    countIf(parent_call_task_customer_id IS NOT NULL) AS redial_leads,
    round(countIf(parent_call_task_customer_id IS NOT NULL) * 100.0 /
          countIf(parent_call_task_customer_id IS NULL), 2) AS redial_rate_pct
FROM crm.dim_call_task_customer
PREWHERE create_time >= addDays(now(), -7)
WHERE is_delete = 0
GROUP BY call_task_id
ORDER BY parent_leads DESC
LIMIT 20;
```

---

## 六、注意事项

1. **父线索标识**：用 `IS NULL` 判断父线索，**不是 `= 0`**（极少量历史数据用 0，但主流是 NULL）
2. **extra_json TTL 15天**：扩展字段超过 15 天后自动清空，历史数据中该字段为空
3. **统计线索数时必须区分父子**：否则重拨会导致重复计数
4. **无时间分区字段**：使用 `create_time` 过滤，但不是分区键，大范围查询需谨慎
