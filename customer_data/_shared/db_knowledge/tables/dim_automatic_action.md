# crm.dim_automatic_action — 策略节点定义表

> **数据库**: crm | **层次**: ODS | **更新方式**: ReplacingMergeTree，查询需加 FINAL

---

## 一、业务含义

策略（Automation）中每个**节点（Action）**的定义表。一个策略由多个有序节点组成，节点类型涵盖外呼、短信、条件分支、时间延迟、AB测试等 **18 种**。

节点定义了：
- **做什么**（type：外呼/SMS/条件分支等）
- **用什么模板**（template_code：话术/消息模板）
- **配置参数**（action_details：JSON 格式的详细配置）

---

## 二、字段说明

| 字段名 | 类型 | 业务含义 | 备注 |
|--------|------|----------|------|
| `id` | Int64 | 自增主键 | - |
| `action_id` | String | 节点唯一ID（UUID） | ⭐ 关联键，String 类型 |
| `automatic_task_id` | Int64 | 所属策略ID | FK → dim_automatic_task |
| `process_id` | String | 流程图ID | 同一策略的节点共享 process_id |
| `name` | String | 节点名称（用户自定义） | 如 "Talkbot"、"延迟至19:10" |
| `type` | Int8 | 节点类型 | 见下方枚举 ⭐ |
| `template_code` | String | 话术/模板编码 | 关联 Dim_Dialogue / Dim_Notice_Template |
| `action_details` | String | 节点详细配置（JSON） | 含条件规则、延迟时间、分流比例等 |
| `enterprise_id` | Int64 | 所属企业ID | |
| `is_delete` | Int8 | 软删除标志 | 0=正常, 1=已删除 |
| `create_time` | DateTime | 创建时间 | |
| `update_time` | DateTime | 更新时间 | |
| `_ts` | Int64 | 时间戳（毫秒） | |
| `_op` | String | 操作类型 | c=创建, u=更新, d=删除 |

---

## 三、节点类型枚举（type）⭐

| type | 名称 | 说明 | 是否生成 Task | 实际数量(SG) |
|------|------|------|--------------|-------------|
| 0 | Entry Point | 策略入口节点，每个策略有且只有一个 | 否 | 9,243 |
| 1 | Talkbot（外呼） | AI 外呼节点，使用 template_code 指定话术 | ✅ task_type=1 | 25,809 |
| 2 | SMS | 短信发送节点 | ✅ task_type=2 | 1,901 |
| 3 | WhatsApp | WhatsApp 消息节点 | ✅ task_type=3 | 447 |
| 4 | Email | 邮件发送节点 | ✅ task_type=4 | 7 |
| 5 | Time Delay | 时间延迟节点（延迟到指定时间再执行下一步） | 否 | 13,580 |
| 6 | Condition Split | 条件分支节点（按意图/标签/变量等条件分流） | 否 | 19,014 |
| 7 | Service Call | 外部服务调用节点（API 回调） | 否 | 28 |
| 8 | Split Contacts / A-B Test | 线索分流/AB测试节点（按比例分流） | 否 | 875 |
| 9 | Exit Node | 策略出口节点，线索到达此节点完成流转 | 否 | 17,428 |
| 10 | Wait for an Event | 等待事件节点（等待特定事件发生） | 否 | 34 |
| 11 | Add Tag | 打标签节点（给线索添加标签） | 否 | 70 |
| 12 | Evaluate Inbound WhatsApp | 评估 WhatsApp 呼入节点 | 否 | 14 |
| 14 | Assign | 分配节点（分配给指定坐席/团队） | 否 | 68 |
| 15 | Add to Strategy | 将线索加入另一个策略节点 | 否 | 389 |
| 16 | Viber | Viber 消息节点 | ✅ task_type=16 | 8 |
| 17 | Update Variable | 更新变量节点（更新线索的自定义变量） | 否 | 494 |
| 18 | Add to BlackList | 加入黑名单节点 | 否 | 4 |

> **关键规律**：只有 type=1/2/3/4/16 的节点会在 `dim_automatic_plan_action_task` 中生成实际 task。
> type=5/6/7/8/9/10/11/12/14/15/17/18 是**流程控制节点**，不直接产生通信行为。

---

## 四、关联关系

```
dim_automatic_task.automatic_task_id
    → dim_automatic_action.automatic_task_id（1:N）

dim_automatic_action.action_id
    → dim_automatic_plan_action_task.action_id（1:N）

dim_automatic_action.template_code
    → integrated_data.Dim_Dialogue.template_code（话术信息）
    → integrated_data.Dim_Notice_Template（消息模板，SMS/WhatsApp 等）
```

---

## 五、典型查询

```sql
-- 查某策略的所有节点配置
SELECT 
    action_id, name, type, template_code,
    action_details
FROM crm.dim_automatic_action FINAL
WHERE automatic_task_id = <策略ID>
  AND is_delete = 0
ORDER BY create_time

-- 统计各类型节点的使用分布
SELECT type, count() as cnt, groupArray(5)(name) as sample_names
FROM crm.dim_automatic_action FINAL
WHERE is_delete = 0
GROUP BY type ORDER BY type

-- 查使用特定话术的所有节点
SELECT action_id, automatic_task_id, name, type
FROM crm.dim_automatic_action FINAL
WHERE template_code = '<话术编码>'
  AND is_delete = 0
```

---

## 六、注意事项

1. `action_id` 是 **String 类型（UUID）**，关联时注意类型匹配
2. `action_details` 是 JSON 字符串，可用 `visitParamExtractRaw` 等函数解析
3. 条件分支节点（type=6）的分支规则存储在 `action_details` 中
4. 同一策略的节点通过 `process_id` 归属同一流程图
