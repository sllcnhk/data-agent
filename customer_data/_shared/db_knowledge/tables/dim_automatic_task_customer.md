# crm.dim_automatic_task_customer — 策略线索明细表

> **数据库**: crm | **层次**: ODS | **更新方式**: ReplacingMergeTree，查询需加 FINAL

---

## 一、业务含义

**策略执行过程中的线索明细**表。记录每条线索在策略流转过程中的状态和轨迹。

### 父子线索机制 ⭐

这是理解此表的核心：

```
导入线索时：创建父线索（parent_automatic_task_customer_id = 0）
    ↓ Plan 驱动，线索流转到 Action A（如外呼节点）
创建子线索 A（parent_automatic_task_customer_id = 父线索ID）
    ↓ 外呼完成，按意图条件分支，流转到 Action B（如SMS节点）
创建子线索 B（parent_automatic_task_customer_id = 子线索A的ID）
    ↓ ...依此类推，直到到达 Exit Node
```

**每次线索流转到一个新的执行节点，就会生成一条新的子线索记录。**
子线索记录了线索在该节点的执行状态，通过 `parent_automatic_task_customer_id` 可以还原完整的流转路径。

---

## 二、字段说明

| 字段名 | 类型 | 业务含义 | 备注 |
|--------|------|----------|------|
| `automatic_task_customer_id` | Int64 | 线索记录唯一ID | ⭐ 主键 |
| `plan_id` | Int64 | 所属批次ID | FK → dim_automatic_plan.id |
| `automatic_task_id` | Int64 | 所属策略ID | FK → dim_automatic_task |
| `parent_automatic_task_customer_id` | Int64 | 父线索ID | ⭐ **0=父线索（导入时创建），>0=子线索（流转时生成）** |
| `action_id` | String | 当前所在节点ID | FK → dim_automatic_action.action_id |
| `process_id` | String | 流程图ID | |
| `phone` | String | 电话号码 | |
| `customer_id` | Int64 | 客户ID | FK → realtime_ods_crm_customer |
| `enterprise_id` | Int64 | 所属企业ID | |
| `is_complete` | Int8 | 是否已完成流转 | 0=未完成, 1=已完成（到达 Exit Node）|
| `status` | Int8 | 当前状态 | |
| `create_time` | DateTime | 创建时间 | |
| `update_time` | DateTime | 更新时间 | |
| `is_delete` | Int8 | 软删除标志 | |
| `_ts` | Int64 | 时间戳（毫秒） | |
| `_op` | String | 操作类型 | c/u/d |

---

## 三、父子线索示例

```
plan_id=50, automatic_task_id=10（策略有3个节点：外呼→SMS→Exit）

记录1: automatic_task_customer_id=100, parent_id=0,       action_id=null    → 父线索（导入时）
记录2: automatic_task_customer_id=101, parent_id=100,     action_id="uuid_A" → 子线索（流转到外呼节点）
记录3: automatic_task_customer_id=102, parent_id=101,     action_id="uuid_B" → 子线索（流转到SMS节点）
记录4: automatic_task_customer_id=103, parent_id=102,     action_id="uuid_C" → 子线索（到达Exit Node，is_complete=1）
```

---

## 四、关联关系

```
dim_automatic_plan.id（1）
    → dim_automatic_task_customer.plan_id（N）

dim_automatic_action.action_id（1）
    → dim_automatic_task_customer.action_id（N）

dim_automatic_task_customer.automatic_task_customer_id（自关联）
    → dim_automatic_task_customer.parent_automatic_task_customer_id
```

---

## 五、典型查询

```sql
-- 统计某批次的父线索数（即实际导入线索数）
SELECT count() AS parent_lead_count
FROM crm.dim_automatic_task_customer FINAL
WHERE plan_id = <plan_id>
  AND parent_automatic_task_customer_id = 0
  AND is_delete = 0

-- 查某批次各节点的线索流转数量
SELECT 
    action_id,
    count() AS lead_count,
    countIf(is_complete = 1) AS complete_count
FROM crm.dim_automatic_task_customer FINAL
WHERE plan_id = <plan_id>
  AND parent_automatic_task_customer_id > 0  -- 只看子线索
  AND is_delete = 0
GROUP BY action_id

-- 查某线索的完整流转路径
SELECT 
    automatic_task_customer_id,
    parent_automatic_task_customer_id,
    action_id,
    is_complete,
    create_time
FROM crm.dim_automatic_task_customer FINAL
WHERE plan_id = <plan_id>
  AND phone = '<电话号码>'
  AND is_delete = 0
ORDER BY create_time
```

---

## 六、注意事项

1. **统计线索数时，应只统计父线索**（`parent_automatic_task_customer_id = 0`），子线索是流转过程中生成的，不代表独立线索
2. 此表数据量大，查询时必须带 `plan_id` 或 `automatic_task_id` 过滤条件
3. 与 `dim_call_task_customer` 的区别：此表是策略线索，`dim_call_task_customer` 是普通外呼任务线索
