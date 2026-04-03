---
name: ch-idn-specific
version: "1.0"
description: IDN（印尼） ClickHouse 环境特化配置——服务器名、特有表、特殊过滤条件
triggers:
  - idn
  - indonesia
category: analytics
priority: high
always_inject: false
scope: env-idn
layer: scenario
env_tags:
  - idn
---

# IDN（印尼） 环境特化说明

> 本 Skill 由 clickhouse-analyst 父 Skill 在检测到 IDN（印尼） 环境相关查询时自动加载。

## 服务器配置

| 环境 | 服务器名 | 说明 |
|------|---------|------|
| IDN（印尼） 只读 | `clickhouse-idn-ro` | 推荐使用 |
| IDN（印尼） 管理员 | `clickhouse-idn` | 仅 DDL/DML 需要 |

工具调用示例：`clickhouse-idn-ro__query`

## 知识库路径

IDN（印尼） 环境专属知识库：`{CURRENT_USER}/db_knowledge/idn/`

> 当前尚未建立，可通过 db-maintainer Skill（"更新知识库"）自动生成。

## 时区

IDN（印尼） 时区：UTC+7，时间字段查询注意转换。

## 待补充内容

- IDN（印尼） 环境特有表（相比 SG 环境的差异）
- IDN（印尼） 特殊业务规则
- IDN（印尼） 常用查询模板
