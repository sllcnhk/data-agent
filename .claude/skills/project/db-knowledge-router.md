---
name: db-knowledge-router
version: "1.0"
description: ClickHouse分析启动协议——按需加载本地表知识，禁止重复探索数据库
triggers:
  - 分析
  - 查询
  - 统计
  - 报表
  - clickhouse
  - 数据
  - 接通率
  - 账单
  - 外呼
  - 呼叫
  - 企业
  - 线索
  - 策略
category: analytics
priority: high
always_inject: false
layer: knowledge
---

# 数据分析启动协议（元数据路由）

> 本规程在任何 ClickHouse 数据分析任务中强制执行，优先级高于 Agent 默认行为。

## 分析前置检查（MANDATORY）

在构建任何 SQL 之前，**必须**按以下步骤执行：

### Step 1：检查本地知识库是否存在

```
read_file: {CURRENT_USER}/db_knowledge/_index.md
```

- **若文件存在** → 进入 Step 2（使用本地知识库模式）
- **若文件不存在** → 进入 Step 2b（数据库探索模式，需声明）

### Step 2：本地知识库模式（优先）

根据用户问题，从 `_index.md` 中识别相关表，**只加载涉及的文件**：

| 分析需要 | 加载文件 |
|---------|---------|
| 了解数据库总体结构 | `_index.md`（已读） |
| 分析特定表 | `tables/<表名>.md` |
| 跨表关联分析 | `relationships.md` |
| 计算业务指标（接通率/账单/GMV等）| `metrics/<指标名>.md` |

> ⚠️ **不得一次性加载所有文件** — 按需加载，每次只读取本次分析直接需要的文件。

### Step 2b：数据库探索模式（兜底，需声明）

仅当 `db_knowledge` 不存在或明确找不到目标表时执行：

```
声明：[探索模式] db_knowledge 中未找到 <表名>，执行数据库探索
```

然后才允许使用：`list_tables` / `describe_table` / `get_table_overview`

> 探索完成后，可将获取的表结构写入 `{CURRENT_USER}/db_knowledge/tables/<表名>.md` 以供下次使用。
> ⚠️ 路径说明：文件系统根目录已指向 `customer_data/`，直接用 `{CURRENT_USER}/子路径` 即可，**禁止重复写 `customer_data/`**（否则产生双层目录）。

### Step 3：确认目标环境

根据用户问题识别目标 ClickHouse 环境：

| 用户提到 | 目标环境 | 服务器名格式 |
|---------|---------|------------|
| SG / 新加坡 / sg_azure | SG | `clickhouse-sg` / `clickhouse-sg-azure` |
| IDN / 印尼 / indonesia | IDN | `clickhouse-idn` |
| BR / 巴西 / brazil | BR | `clickhouse-br` |
| MY / 马来 / malaysia | MY | `clickhouse-my` |
| THAI / 泰国 / thailand | THAI | `clickhouse-thai` |
| MX / 墨西哥 / mexico | MX | `clickhouse-mx` |
| 未指定 | 询问用户确认 | — |

> ⚠️ **禁止混用环境**：如用户问 SG 数据，不得查询 IDN 的服务器。

### Step 4：执行分析

完成 Step 1-3 后，基于本地知识构建 SQL，遵循以下规范：

- `PREWHERE` 过滤时间/主键（ClickHouse 性能要求）
- 外呼查询必加 `call_type = 1`
- 必加软删除过滤 `is_delete = 0`
- 禁止 `SELECT *`（宽表字段多，必须按需选字段）

## 完成声明

每次分析开始时输出状态标记：

```
✅ [知识库] 已加载 _index.md | 目标环境: <环境名> | 涉及表: <表名列表>
```
