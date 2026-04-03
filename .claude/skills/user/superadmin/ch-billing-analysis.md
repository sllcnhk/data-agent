---
name: ch-billing-analysis
version: "1.0"
description: 账单分析场景——月账单、日账单、费用异常、对账分析
triggers:
  - 账单
  - bill
  - 计费
  - 费用
  - 月账单
  - 日账单
  - 线路费
  - 对账
  - gmv
category: analytics
priority: high
always_inject: false
scope: global-ch
layer: scenario
---

# 账单分析场景

> 本 Skill 由 clickhouse-analyst 父 Skill 自动加载，用于账单费用类分析场景。

## 适用场景

- 月账单汇总与趋势
- 日账单明细分析
- 有呼叫无账单异常排查
- 企业费用对账（呼叫量 vs 账单金额）
- GMV 统计

## 指标知识库

| 指标 | 知识库文件 |
|------|----------|
| 月账单 | `{CURRENT_USER}/db_knowledge/metrics/monthly_bill.md` |
| GMV | `{CURRENT_USER}/db_knowledge/metrics/gmv.md` |
| 每通费用 | `{CURRENT_USER}/db_knowledge/metrics/cost_per_call.md` |

## 核心表

| 表名 | 用途 |
|------|------|
| `crm.realtime_ods_cost_data_report_day_bill_record` | 日账单明细（分区键：`bill_date`） |
| `data_statistics.Bill_Monthly` | 月账单汇总（DWS 层） |
| `crm.realtime_dwd_crm_call_record` | 呼叫记录（对账用） |

## 关键过滤条件

```sql
-- 外呼账单过滤（bus_type）
WHERE bus_type IN (1, 5)   -- 1=线路费, 5=机器人费
  AND bill_date >= 'YYYY-MM-DD'  -- 必须指定日期范围
```

## 待补充内容

- 各环境账单差异（不同环境价格体系）
- 异常阈值定义
