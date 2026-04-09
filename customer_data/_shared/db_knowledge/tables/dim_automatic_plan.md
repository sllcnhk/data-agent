# crm.dim_automatic_plan — 线索导入批次表

> **数据库**: crm | **层次**: ODS | **更新方式**: ReplacingMergeTree，查询需加 FINAL

---

## 一、业务含义

**线索导入批次（Plan）**表。每次企业向策略中批量导入一批线索时，系统会创建一条 Plan 记录。

Plan 是策略执行的**驱动单元**：
- Plan 记录了这批线索属于哪个策略（`automatic_task_id`）
- Plan 驱动这批线索按策略定义的节点顺序和流转条件逐步执行
- 同一策略可以有多个 Plan（多次导入线索）

---

## 二、字段说明

| 字段名 | 类型 | 业务含义 | 备注 |
|--------|------|----------|------|
| `id` | Int64 | Plan 唯一ID | ⭐ 主键，**注意字段名是 id 而非 plan_id** |
| `automatic_task_id` | Int64 | 所属策略ID | FK → dim_automatic_task |
| `name` | String | 批次名称 | 用户自定义或系统自动生成 |
| `contact_batch_id` | Int64 | 线索批次ID | 关联线索导入批次 |
| `status` | Int8 | 批次状态 | 见下方枚举 |
| `enterprise_id` | Int64 | 所属企业ID | |
| `user_id` | Int64 | 操作用户ID | |
| `total_count` | Int64 | 总线索数 | 导入时的线索总数 |
| `complete_count` | Int64 | 已完成线索数 | 已完成流转的线索数 |
| `is_delete` | Int8 | 软删除标志 | 0=正常, 1=已删除 |
| `create_time` | DateTime | 创建时间（导入时间） | |
| `update_time` | DateTime | 更新时间 | |
| `_ts` | Int64 | 时间戳（毫秒） | |
| `_op` | String | 操作类型 | c/u/d |

---

## 三、status 枚举

| status | 含义 |
|--------|------|
| 0 | 待执行 |
| 1 | 执行中 |
| 2 | 已完成 |
| 3 | 已暂停 |
| 4 | 已取消 |

---

## 四、关联关系

```
dim_automatic_task.automatic_task_id（1）
    → dim_automatic_plan.automatic_task_id（N）

dim_automatic_plan.id（plan_id）（1）
    → dim_automatic_task_customer.plan_id（N）：批次内所有线索
    → dim_automatic_plan_action_task.plan_id（N）：批次生成的所有任务

⚠️ 注意：关联时使用 dim_automatic_plan.id（不是 plan_id）
```

---

## 五、典型查询

```sql
-- 查某策略的所有批次执行情况
SELECT 
    id AS plan_id,
    name,
    status,
    total_count,
    complete_count,
    round(complete_count / total_count * 100, 1) AS complete_rate_pct,
    create_time
FROM crm.dim_automatic_plan FINAL
WHERE automatic_task_id = <策略ID>
  AND is_delete = 0
ORDER BY create_time DESC
LIMIT 20

-- 查最近一个月各策略的批次数量
SELECT 
    automatic_task_id,
    count() AS plan_count,
    sum(total_count) AS total_leads,
    sum(complete_count) AS completed_leads
FROM crm.dim_automatic_plan FINAL
WHERE create_time >= addDays(now(), -30)
  AND is_delete = 0
GROUP BY automatic_task_id
ORDER BY total_leads DESC
LIMIT 20
```

---

## 六、注意事项

1. **plan_id 字段名**：此表的主键字段名是 `id`，不是 `plan_id`。在 JOIN 时需写 `dim_automatic_plan.id`
2. 其他表（如 `dim_automatic_task_customer`、`dim_automatic_plan_action_task`）中的 `plan_id` 字段关联的是此表的 `id`
