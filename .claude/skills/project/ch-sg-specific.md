---
name: ch-sg-specific
version: "1.0"
description: SG（新加坡）ClickHouse 环境特化配置——服务器名、特有表、特殊过滤条件
triggers:
  - sg
  - singapore
  - sg_azure
  - azure
category: analytics
priority: high
always_inject: false
scope: env-sg
layer: scenario
env_tags:
  - sg
---

# SG 环境特化说明

> 本 Skill 由 clickhouse-analyst 父 Skill 在检测到 SG 环境相关查询时自动加载。

## 服务器配置

| 环境 | 服务器名 | 说明 |
|------|---------|------|
| SG 主库（只读）| `clickhouse-sg-ro` | 推荐使用，SELECT 安全 |
| SG 主库（管理员）| `clickhouse-sg` | 仅 DDL/DML 需要 |
| SG Azure（只读）| `clickhouse-sg-azure-ro` | Azure 部署环境 |

工具调用示例：`clickhouse-sg-ro__query`、`clickhouse-sg-ro__list_tables`

## 知识库路径

SG 环境知识库：`{CURRENT_USER}/db_knowledge/`（当前仅 SG 有完整文档）

## 待补充内容

- SG 环境特有表（相比其他环境的差异）
- SG 特殊业务规则
- SG 时区处理（UTC+8）
