---
name: ch-call-metrics
version: "2.0"
description: 外呼呼叫指标分析场景——接通率、AI通话时长、策略效率等
triggers:
  - 接通率
  - connect_rate
  - 通话时长
  - ai_duration
  - 策略效率
  - 线索转化
category: analytics
priority: high
always_inject: false
scope: global-ch
layer: scenario
---

# 外呼呼叫指标分析场景

> 本 Skill 由 clickhouse-analyst 父 Skill 自动加载，用于呼叫指标类分析场景。

## 适用场景

- 接通率统计与趋势分析
- AI 通话时长分布
- 策略线索流转效率
- 呼叫结果分类统计

## 指标知识库

详细口径参见共享知识库文件（文件系统根目录已指向 customer_data/）：

| 指标 | 知识库文件 |
|------|----------|
| 接通率 | `{SHARED_DATA_ROOT}/db_knowledge/metrics/connect_rate.md` |
| AI 通话时长 | `{SHARED_DATA_ROOT}/db_knowledge/metrics/ai_duration.md` |
| 每通费用 | `{SHARED_DATA_ROOT}/db_knowledge/metrics/cost_per_call.md` |

> ⚠️ 路径说明：`{SHARED_DATA_ROOT}` = `_shared`，完整路径为 `_shared/db_knowledge/metrics/...`。若当前用户在 `{CURRENT_USER}/db_knowledge/` 下有同名文件，可优先使用用户自定义版本。

## 核心表

- 主表：`crm.realtime_dwd_crm_call_record`（呼叫事实表）
- 详细文档：`{SHARED_DATA_ROOT}/db_knowledge/tables/realtime_dwd_crm_call_record.md`

## 待补充内容

- 更多自定义指标定义
- 行业基准对比数据
