---
name: ch-call-metrics
version: "1.0"
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

详细口径参见本地文件：

| 指标 | 知识库文件 |
|------|----------|
| 接通率 | `{CURRENT_USER}/db_knowledge/metrics/connect_rate.md` |
| AI 通话时长 | `{CURRENT_USER}/db_knowledge/metrics/ai_duration.md` |
| 每通费用 | `{CURRENT_USER}/db_knowledge/metrics/cost_per_call.md` |

## 核心表

- 主表：`crm.realtime_dwd_crm_call_record`（呼叫事实表）
- 详细文档：`{CURRENT_USER}/db_knowledge/tables/realtime_dwd_crm_call_record.md`

## 待补充内容

- 更多自定义指标定义
- 行业基准对比数据
