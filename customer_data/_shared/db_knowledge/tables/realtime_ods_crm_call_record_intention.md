# 表：crm.realtime_ods_crm_call_record_intention

## 基本信息

| 属性 | 值 |
|------|-----|
| 数据库 | crm |
| 表名 | realtime_ods_crm_call_record_intention |
| 数据量 | ~14.9亿行 |
| 数据层级 | ODS（原始数据层） |
| 业务域 | 呼叫业务 / 意图识别 |
| 更新方式 | 实时同步 |

## 业务语义

**通话意图识别结果表**，存储每次通话经 AI 意图识别后的分类标签。记录了通话的初始意图（init_label_name）和最终意图（final_label_name），两者可能不同（人工复核修改）。

**核心用途**：
- 通话意图分布统计
- AI意图识别准确率评估
- 客户意向分析

## 字段详情

| 字段名 | 类型 | 默认值 | 业务含义 | 备注 |
|--------|------|--------|---------|------|
| **id** | Int64 | - | 主键 | 雪花ID |
| **ent_id** | Int64 | - | 企业ID | 关联 Dim_Enterprise.Enterprise_ID |
| **call_record_id** | Int64 | - | 通话记录ID | 关联 consolidated_cdr |
| **create_time** | DateTime | - | 创建时间 | 意图识别完成时间 |
| **update_time** | DateTime | - | 更新时间 | 最后更新时间（人工修改时更新） |
| **final_label_name** | Nullable(String) | - | 最终意图标签 | 经人工审核后的最终分类 |
| **init_label_name** | Nullable(String) | - | 初始意图标签 | AI自动识别的原始分类 |
| **intent_label_id** | Int64 | - | 意图标签ID | 关联意图标签配置表 |
| **ods_call_task_id** | Nullable(Int64) | - | 呼叫任务ID | 关联 dim_call_task |
| **ods_call_type** | Nullable(Int8) | - | 通话类型 | 见 Fact_Daily_Call.call_type 枚举 |
| **ods_call_start_time** | Nullable(DateTime) | - | 通话开始时间 | 冗余字段，便于时间过滤 |
| **ods_automation_task_id** | Nullable(Int64) | - | 自动化任务ID | 关联 Dim_Automatic_Task |
| **ods_plan_id** | Nullable(Int32) | - | 计划ID | 关联呼叫计划 |

## 实际数据示例

```
id: 1676981019468148738
ent_id: 337114289470678650
call_record_id: 71020967
final_label_name: "Three"  ← 最终意图（可能是企业自定义标签）
init_label_name: "One"     ← AI初始识别（与最终不同，说明被人工修改）
intent_label_id: 0
create_time: 2023-07-06T23:46:46
```

> **注意**：意图标签名称（如 "One", "Three", "A", "Five"）是企业自定义的，不同企业有不同的意图分类体系。

## 关联关系

```
crm.realtime_ods_crm_call_record_intention
    ├── ent_id ──────────────→ integrated_data.Dim_Enterprise.Enterprise_ID
    ├── call_record_id ──────→ om_statistics.consolidated_cdr (间接关联)
    ├── call_record_id ──────→ crm.realtime_dwd_crm_call_record_dialog_detail.call_record_id
    ├── ods_call_task_id ────→ crm.dim_call_task.call_task_id
    └── ods_automation_task_id → integrated_data.Dim_Automatic_Task.automatic_task_id
```

## 注意事项

1. **数据量极大**（14.9亿行），查询必须带 `ent_id` 或 `create_time` 过滤
2. `final_label_name` 和 `init_label_name` 均为企业自定义标签，需结合企业上下文理解
3. 当 `final_label_name != init_label_name` 时，说明 AI 识别被人工修正
4. `intent_label_id = 0` 可能表示未关联到具体标签配置

## 典型查询

```sql
-- 统计某企业的意图分布
SELECT 
    final_label_name,
    count() AS call_count,
    count() / sum(count()) OVER () AS ratio
FROM crm.realtime_ods_crm_call_record_intention
WHERE ent_id = ?
    AND create_time >= today() - 30
GROUP BY final_label_name
ORDER BY call_count DESC;

-- 计算AI意图识别准确率（与人工审核一致的比例）
SELECT 
    countIf(final_label_name = init_label_name) / count() AS accuracy
FROM crm.realtime_ods_crm_call_record_intention
WHERE ent_id = ?
    AND create_time >= today() - 7;
```
