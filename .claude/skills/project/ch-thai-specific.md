---
name: ch-thai-specific
version: "1.0"
description: THAI（泰国） ClickHouse 环境特化配置——服务器名、特有表、特殊过滤条件
triggers:
  - thai
  - thailand
category: analytics
priority: high
always_inject: false
scope: env-thai
layer: scenario
env_tags:
  - thai
---

# THAI（泰国） 环境特化说明

> 本 Skill 由 clickhouse-analyst 父 Skill 在检测到 THAI（泰国） 环境相关查询时自动加载。

## 服务器配置

| 环境 | 服务器名 | 说明 |
|------|---------|------|
| THAI（泰国） 只读 | `clickhouse-thai-ro` | 推荐使用 |
| THAI（泰国） 管理员 | `clickhouse-thai` | 仅 DDL/DML 需要 |

工具调用示例：`clickhouse-thai-ro__query`

## 知识库路径

THAI（泰国） 环境专属知识库：`{CURRENT_USER}/db_knowledge/thai/`

> 当前尚未建立，可通过 db-maintainer Skill（"更新知识库"）自动生成。

## 时区

THAI（泰国） 时区：UTC+7，时间字段查询注意转换。

## 待补充内容

- THAI（泰国） 环境特有表（相比 SG 环境的差异）
- THAI（泰国） 特殊业务规则
- THAI（泰国） 常用查询模板
