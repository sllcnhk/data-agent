# crm.dim_automatic_task — 策略定义表

> **数据库**: crm | **层次**: ODS | **更新方式**: ReplacingMergeTree，查询需加 FINAL

---

## 一、业务含义

**自动化策略（Automation Strategy）**的定义表。策略是企业配置的一套多渠道、多节点的线索触达流程，可以包含外呼、短信、WhatsApp、条件分支、时间延迟等多种节点。

企业使用策略功能时，先在此表中定义策略，再通过 `dim_automatic_plan` 批量导入线索执行。

**不使用策略功能时**，企业可以直接创建外呼任务（`dim_call_task`，`is_automatic_task=0`）。

---

## 二、字段说明

| 字段名 | 类型 | 业务含义 | 备注 |
|--------|------|----------|------|
| `automatic_task_id` | Int64 | 策略唯一ID | ⭐ 主键，关联键 |
| `name` | String | 策略名称（用户自定义） | 如 "Lazada DPD2"、"Maypera DC Predue D-1" |
| `enterprise_id` | Int64 | 所属企业ID | |
| `user_id` | Int64 | 创建用户ID | |
| `catagory` | Int8 | 策略类别 | 0=普通策略, 1=高级策略(含AB Test等) |
| `type` | Int8 | 策略类型 | 目前 SG 数据大多为 null |
| `status` | Int8 | 策略状态 | |
| `alias_name` | String | 策略别名 | |
| `transfer_flow` | String | 转接流程配置 | |
| `is_delete` | Int8 | 软删除标志 | 0=正常, 1=已删除 |
| `create_time` | DateTime | 创建时间 | |
| `update_time` | DateTime | 更新时间 | |
| `_ts` | Int64 | 时间戳（毫秒） | |
| `_op` | String | 操作类型 | c/u/d |

---

## 三、catagory 枚举（策略类别）

| catagory | 含义 | SG 数量 | 示例名称 |
|----------|------|---------|---------|
| 0 | 普通策略 | 1,422 | "Lazada DPD2"、"Pesoflash DC Overdue1-5" |
| 1 | 高级策略（含 AB Test 等高级节点） | 36 | "DPD 1-3"、"BNPL-Overdue1-7分流（TikTok）" |
| null | 未设置 | 259 | 多为测试策略 |

---

## 四、关联关系

```
dim_automatic_task.automatic_task_id（1）
    → dim_automatic_action.automatic_task_id（N）：策略的所有节点定义
    → dim_automatic_plan.automatic_task_id（N）：策略的所有执行批次
    → dim_automatic_plan_action_task.automatic_task_id（N）：策略生成的所有任务
    → realtime_dwd_crm_call_record.automatic_task_id（N）：策略产生的所有呼叫记录
    → integrated_data.Dim_Automatic_Task.automatic_task_id：集成层维度
```

---

## 五、典型查询

```sql
-- 查某企业的所有策略
SELECT automatic_task_id, name, catagory, status, create_time
FROM crm.dim_automatic_task FINAL
WHERE enterprise_id = <企业ID>
  AND is_delete = 0
ORDER BY create_time DESC

-- 查策略及其节点数量
SELECT 
    at.automatic_task_id,
    at.name,
    at.catagory,
    count(DISTINCT aa.action_id) AS action_count,
    countIf(aa.type = 1) AS talkbot_nodes,
    countIf(aa.type = 2) AS sms_nodes,
    countIf(aa.type = 6) AS condition_nodes
FROM crm.dim_automatic_task at FINAL
LEFT JOIN crm.dim_automatic_action aa FINAL 
    ON aa.automatic_task_id = at.automatic_task_id AND aa.is_delete = 0
WHERE at.is_delete = 0
GROUP BY at.automatic_task_id, at.name, at.catagory
ORDER BY action_count DESC
LIMIT 20
```

---

## 六、注意事项

1. `catagory` 字段（注意拼写：catagory 非 category）
2. 策略名称中可能含有测试标志（test/demo/autotest），分析时建议关联 `integrated_data.Dim_Enterprise.test_flag` 过滤
3. `integrated_data.Dim_Automatic_Task` 是此表的加工版本，增加了 `Environment` 字段用于跨环境分析
