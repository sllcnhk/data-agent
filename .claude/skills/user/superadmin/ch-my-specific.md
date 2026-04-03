---
name: ch-my-specific
version: "1.0"
description: MY（马来西亚） ClickHouse 环境特化配置——服务器名、特有表、特殊过滤条件
triggers:
  - my
  - malaysia
category: analytics
priority: high
always_inject: false
scope: env-my
layer: scenario
env_tags:
  - my
---

# MY（马来西亚） 环境特化说明

> 本 Skill 由 clickhouse-analyst 父 Skill 在检测到 MY（马来西亚） 环境相关查询时自动加载。

## 服务器配置

| 环境 | 服务器名 | 说明 |
|------|---------|------|
| MY（马来西亚） 只读 | `clickhouse-my-ro` | 推荐使用 |
| MY（马来西亚） 管理员 | `clickhouse-my` | 仅 DDL/DML 需要 |

工具调用示例：`clickhouse-my-ro__query`

## 知识库路径

MY（马来西亚） 环境专属知识库：`{CURRENT_USER}/db_knowledge/my/`

> 当前尚未建立，可通过 db-maintainer Skill（"更新知识库"）自动生成。

## 时区

MY（马来西亚） 时区：UTC+8，时间字段查询注意转换。

## 待补充内容

- MY（马来西亚） 环境特有表（相比 SG 环境的差异）
- MY（马来西亚） 特殊业务规则
- MY（马来西亚） 常用查询模板
