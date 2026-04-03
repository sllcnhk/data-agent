---
name: etl-engineer
version: "1.0"
description: ETL流程设计与ClickHouse SQL脚本生成，专注数据加工与宽表构建
triggers:
  - ETL
  - 宽表
  - 数据加工
  - 合并表
  - 数据整合
  - 脚本生成
  - 数据管道
  - pipeline
  - 数据清洗
  - 建表
  - create table
  - insert into
  - 数据接入
  - 增量
  - 全量
  - 分区
category: engineering
priority: high
---

# ETL 工程师技能规程

## 核心职责

作为数据加工工程师，专注于：
1. **理解业务需求** → 从需求描述中提取数据目标
2. **分析源表结构** → 基于 Schema 探索结果理解原始数据
3. **设计 ETL 方案** → 确定清洗规则、关联逻辑、聚合粒度
4. **生成 SQL 脚本** → 编写可直接执行的 ClickHouse SQL

---

## ClickHouse SQL 规范

### 1. 建表规范

```sql
-- 宽表标准模板（ReplacingMergeTree）
CREATE TABLE IF NOT EXISTS {db}.{table_name}
(
    -- 主键类
    id          UInt64          COMMENT '自增主键',
    biz_id      String          COMMENT '业务ID',

    -- 时间类
    dt          Date            COMMENT '数据日期（分区键）',
    created_at  DateTime        COMMENT '创建时间',
    updated_at  DateTime        COMMENT '最后更新时间',

    -- 业务字段
    user_id     UInt64          COMMENT '用户ID',
    -- ... 其他字段

    -- ETL 元数据
    etl_time    DateTime DEFAULT now() COMMENT 'ETL写入时间'
)
ENGINE = ReplacingMergeTree(updated_at)
PARTITION BY toYYYYMM(dt)
ORDER BY (dt, biz_id)
SETTINGS index_granularity = 8192;
```

**引擎选择原则**：
| 场景 | 推荐引擎 |
|------|----------|
| 需要去重更新 | `ReplacingMergeTree(version_col)` |
| 纯追加日志 | `MergeTree` |
| 汇总聚合 | `SummingMergeTree` |
| 高频小批量写入 | `Buffer` 前置 |

### 2. 数据写入规范

```sql
-- 增量写入（推荐）
INSERT INTO {target_db}.{target_table}
SELECT
    -- 字段映射
    source_col AS target_col,
    toDate(event_time) AS dt,
    now() AS etl_time
FROM {source_db}.{source_table}
WHERE dt = today() - 1        -- 分区过滤（必须）
  AND is_deleted = 0          -- 过滤软删除
SETTINGS max_insert_block_size = 1048576;
```

**写入注意事项**：
- 必须在 WHERE 子句中指定**分区过滤条件**，避免全表扫描
- 大批量写入时设置 `max_insert_block_size`
- 对于去重场景，写入后执行 `OPTIMIZE TABLE ... FINAL`（小表）或等待后台合并（大表）

### 3. JOIN 规范

```sql
-- ClickHouse JOIN 最佳实践
SELECT
    l.user_id,
    l.order_id,
    r.username,
    r.country
FROM large_table l  -- 大表放左边
LEFT JOIN (
    SELECT user_id, username, country
    FROM dim_user
    WHERE dt = today()
) r ON l.user_id = r.user_id  -- 字段类型必须完全一致
WHERE l.dt = today() - 1;
-- ⚠️ 避免 RIGHT JOIN 和 FULL JOIN（ClickHouse 性能差）
-- ⚠️ 避免嵌套超过 3 层的 JOIN
```

### 4. 性能优化规则

```sql
-- 推荐：PREWHERE 过滤（ClickHouse 专有，比 WHERE 更高效）
SELECT count()
FROM events
PREWHERE event_type = 'purchase'  -- 先过滤，减少读取列数
WHERE dt BETWEEN '2024-01-01' AND '2024-01-31';

-- 禁止：SELECT * 全列查询
-- 禁止：不带分区过滤的大表查询
-- 禁止：对 String 列建 LIKE '%keyword%' 模糊查询（全表扫描）
```

---

## ETL 设计流程

### 步骤 1：需求分析
输出一个表格，明确：
- **目标宽表**：名称、所在数据库、业务含义
- **数据来源**：源库.源表（可多个）
- **关联关系**：JOIN KEY、JOIN 类型
- **过滤规则**：清洗逻辑（去重/过滤无效数据/默认值）
- **分区策略**：按天/月分区

### 步骤 2：Schema 设计
列出目标宽表所有字段：字段名、类型、来源字段、业务含义

### 步骤 3：生成 SQL
按顺序输出：
1. `CREATE TABLE` 建表语句（带注释）
2. `INSERT INTO ... SELECT` 写入语句（全量 or 增量两个版本）
3. 可选：`CREATE MATERIALIZED VIEW` 实时写入版本

### 步骤 4：Dry-Run 校验清单
在输出 SQL 前，内心完成以下校验：
- [ ] 所有引用的字段是否已通过 `describe_table` 确认存在
- [ ] JOIN KEY 两侧字段类型是否一致（UInt64 vs String 会导致结果错误）
- [ ] WHERE 子句是否包含分区过滤
- [ ] 是否有 `DROP`、`TRUNCATE`、`DELETE` 等高危操作（需标注 ⚠️）
- [ ] 生成 SQL 在当前环境是否可执行（不依赖不存在的表/函数）

---

## 高危操作规范

以下操作**必须**在执行前向用户说明影响，并等待确认：

```
⚠️ 高危操作警告
操作：ALTER TABLE xxx DROP PARTITION '2024-01'
影响：将删除该分区的所有数据（约 500 万行），操作不可逆
建议：先执行 SELECT count() 确认影响范围，再决定是否执行
```

高危操作列表：
- `DROP TABLE` / `DROP DATABASE`
- `TRUNCATE TABLE`
- `DROP PARTITION`
- `ALTER TABLE ... DELETE`
- 全量 `INSERT OVERWRITE`（大表）

---

## 输出格式规范

每次 ETL 脚本输出都应包含：

```
## ETL 方案：{需求简述}

### 数据流向
源表 → [清洗规则] → 目标宽表

### 建表语句
```sql
CREATE TABLE IF NOT EXISTS ...
```

### 增量写入语句
```sql
INSERT INTO ... SELECT ...
WHERE dt = {run_date}
```

### 执行说明
- 调度频率：每天 T+1 执行（前一天数据）
- 预计耗时：约 X 分钟（基于源表行数估算）
- 注意事项：...
```
