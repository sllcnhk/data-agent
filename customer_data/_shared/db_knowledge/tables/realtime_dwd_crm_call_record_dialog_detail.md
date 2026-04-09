# 表：crm.realtime_dwd_crm_call_record_dialog_detail

## 基本信息

| 属性 | 值 |
|------|-----|
| 数据库 | crm |
| 表名 | realtime_dwd_crm_call_record_dialog_detail |
| 数据量 | ~23.6亿行 |
| 数据层级 | DWD（数据明细层） |
| 业务域 | 呼叫业务 / NLP分析 |
| 更新方式 | 实时写入 |

## 业务语义

**通话对话详情表**，存储每次通话的完整对话文本。每条通话记录（call_record_id）对应多条对话记录，按 `sequence` 排序还原完整对话流程。

**核心用途**：
- AI对话内容分析（NLP/意图识别）
- 通话质量评估
- 客服话术分析

## 字段详情

| 字段名 | 类型 | 默认值 | 业务含义 | 备注 |
|--------|------|--------|---------|------|
| **call_record_id** | Int64 | - | 通话记录ID | 主键之一，关联通话记录 |
| **text** | String | '' | 对话文本内容 | 实际语音转文字内容 |
| **role** | String | '' | 说话角色 | 见枚举值说明 |
| **sequence** | Int32 | 0 | 对话轮次序号 | 0=整通通话汇总, 1,2,3...=逐轮对话 |
| **intent_trace_id** | String | '' | 意图追踪ID | 关联意图识别系统 |
| **abandon_reason** | String | '' | 中断原因 | 见枚举值说明 |
| **_ts** | DateTime64(3) | now() | 数据写入时间戳 | 系统字段 |
| **issue_type** | Array(String) | - | 问题类型标签 | 数组类型，可多标签 |

## 枚举值说明

### role（说话角色）
| 值 | 含义 |
|----|------|
| `whole` | 整通通话汇总（sequence=0时使用） |
| `robot` | AI机器人发言 |
| `customer` | 客户发言 |
| `agent` | 人工坐席发言 |

### abandon_reason（中断原因）
| 值 | 含义 |
|----|------|
| `""` | 正常（未中断） |
| `Interruption is not allowed` | 打断不被允许（客户打断了AI） |
| `Timeout` | 超时 |

## 实际数据示例

```
call_record_id: 321412497
sequence=0: role=whole, text="" (整通汇总行)
sequence=1: role=robot,    text="Evening, this is JANE from GCash. This call is recorded for quality assurance purposes. Can I speak to MAROON CAMELLO SULIVA?"
sequence=2: role=customer, text="ayan" (abandon_reason="Interruption is not allowed")
sequence=3: role=customer, text="ko na trans yung ano ko" (abandon_reason="Interruption is not allowed")
```

## 关联关系

```
crm.realtime_dwd_crm_call_record_dialog_detail
    └── call_record_id ──→ om_statistics.consolidated_cdr (通过 CallerCallId/CalleeCallId 间接关联)
    └── call_record_id ──→ crm.realtime_ods_crm_call_record_intention.call_record_id
    └── intent_trace_id ──→ 意图识别系统（外部）
```

## 注意事项

1. **数据量极大**（23.6亿行），**禁止全表扫描**，必须通过 `call_record_id` 精确查询
2. `sequence = 0` 的记录是整通通话的汇总行，`text` 通常为空
3. `role = 'whole'` 时 text 为空，是系统生成的占位行
4. `issue_type` 是数组类型，查询时用 `has(issue_type, 'xxx')` 过滤
5. 该表不含 `enterprise_id`，需通过 `call_record_id` 关联其他表获取企业信息

## 典型查询

```sql
-- 获取某通话的完整对话记录
SELECT sequence, role, text, abandon_reason
FROM crm.realtime_dwd_crm_call_record_dialog_detail
WHERE call_record_id = ?
ORDER BY sequence;

-- 统计某批通话中客户打断AI的次数
SELECT 
    call_record_id,
    countIf(abandon_reason = 'Interruption is not allowed') AS interrupt_count
FROM crm.realtime_dwd_crm_call_record_dialog_detail
WHERE call_record_id IN (...)
GROUP BY call_record_id;
```
