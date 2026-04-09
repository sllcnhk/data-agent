---
name: ch-mx-specific
version: "1.0"
description: MX（墨西哥） ClickHouse 环境特化配置——服务器名、特有表、特殊过滤条件
triggers:
  - mx
  - mexico
category: analytics
priority: high
always_inject: false
scope: env-mx
layer: scenario
env_tags:
  - mx
---

# MX（墨西哥） 环境特化说明

> 本 Skill 由 clickhouse-analyst 父 Skill 在检测到 MX（墨西哥） 环境相关查询时自动加载。

## 服务器配置

| 环境 | 服务器名 | 说明 |
|------|---------|------|
| MX（墨西哥） 只读 | `clickhouse-mx-ro` | 推荐使用 |
| MX（墨西哥） 管理员 | `clickhouse-mx` | 仅 DDL/DML 需要 |

工具调用示例：`clickhouse-mx-ro__query`

## 知识库路径

MX（墨西哥） 环境专属知识库：`{CURRENT_USER}/db_knowledge/mx/`

> 当前尚未建立，可通过 db-maintainer Skill（"更新知识库"）自动生成。

## 时区

MX（墨西哥） 时区：UTC-6，时间字段查询注意转换。

## 待补充内容

- MX（墨西哥） 环境特有表（相比 SG 环境的差异）
- MX（墨西哥） 特殊业务规则
- MX（墨西哥） 常用查询模板
