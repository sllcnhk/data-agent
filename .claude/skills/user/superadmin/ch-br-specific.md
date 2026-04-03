---
name: ch-br-specific
version: "1.0"
description: BR（巴西） ClickHouse 环境特化配置——服务器名、特有表、特殊过滤条件
triggers:
  - br
  - brazil
category: analytics
priority: high
always_inject: false
scope: env-br
layer: scenario
env_tags:
  - br
---

# BR（巴西） 环境特化说明

> 本 Skill 由 clickhouse-analyst 父 Skill 在检测到 BR（巴西） 环境相关查询时自动加载。

## 服务器配置

| 环境 | 服务器名 | 说明 |
|------|---------|------|
| BR（巴西） 只读 | `clickhouse-br-ro` | 推荐使用 |
| BR（巴西） 管理员 | `clickhouse-br` | 仅 DDL/DML 需要 |

工具调用示例：`clickhouse-br-ro__query`

## 知识库路径

BR（巴西） 环境专属知识库：`{CURRENT_USER}/db_knowledge/br/`

> 当前尚未建立，可通过 db-maintainer Skill（"更新知识库"）自动生成。

## 时区

BR（巴西） 时区：UTC-3，时间字段查询注意转换。

## 待补充内容

- BR（巴西） 环境特有表（相比 SG 环境的差异）
- BR（巴西） 特殊业务规则
- BR（巴西） 常用查询模板
