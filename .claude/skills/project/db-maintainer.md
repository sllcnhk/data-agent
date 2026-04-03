---
name: db-maintainer
version: "1.0"
description: 数据库知识库维护工作流——自动同步表结构到本地db_knowledge文件
triggers:
  - 更新知识库
  - 刷新表结构
  - 同步表文档
  - db_knowledge 更新
  - 知识库更新
  - 更新表文档
  - 同步知识库
  - refresh db_knowledge
  - update db_knowledge
category: analytics
priority: high
always_inject: false
layer: maintenance
---

# 数据库知识库维护工作流

> 用于将 ClickHouse 数据库的最新表结构同步到本地 `db_knowledge/` 文件，保持知识库的准确性。

## 触发方式

用户说以下任意内容时执行本工作流：
- "更新知识库" / "刷新表结构" / "同步表文档"
- "更新 SG 的 db_knowledge" / "同步 IDN 表结构"
- "把 XXX 表的结构写入知识库"

---

## 完整维护流程

### Step 0：确认参数

在开始前，向用户确认：

1. **目标环境**：SG / IDN / BR / MY / THAI / MX（或全部）
2. **目标数据库**：`crm` / `data_statistics` / `integrated_data`（或全部）
3. **更新范围**：全量更新 or 指定表名列表
4. **知识库路径**：默认 `{CURRENT_USER}/db_knowledge/`

### Step 1：获取当前表清单

```sql
-- 获取目标数据库的所有表
SELECT database, name, engine, total_rows, total_bytes
FROM system.tables
WHERE database IN ('crm', 'data_statistics', 'integrated_data')
  AND engine NOT LIKE '%View%'
ORDER BY database, name;
```

同时读取本地索引（若存在）：
```
read_file: {CURRENT_USER}/db_knowledge/_index.md
```

对比识别：
- **新增表**（数据库有，本地无）→ 需要创建文档
- **已有表**（本地已记录）→ 检查是否需要更新（字段变更等）
- **已删除表**（本地有，数据库无）→ 标记为已废弃

### Step 2：为每张目标表生成/更新文档

对每张需要更新的表执行：

**2a. 获取表结构**
```sql
DESCRIBE TABLE <database>.<table_name>;
```

**2b. 获取数据量估算**
```sql
SELECT count() as row_count
FROM <database>.<table_name>
LIMIT 1;
```
> 对超大表（TiB 级别）跳过此步，直接从 `system.tables` 取 `total_rows`。

**2c. 获取数据样例**（仅对中小表）
```sql
SELECT * FROM <database>.<table_name> LIMIT 3;
```

**2d. 写入文档文件**

生成 `{CURRENT_USER}/db_knowledge/tables/<table_name>.md`，格式如下：

```markdown
# 表名：<database.table_name>

> 最后同步时间：<当前日期> | 来源环境：<环境名>

## 基本信息

| 属性 | 值 |
|------|-----|
| 数据库 | `<database>` |
| 表名 | `<table_name>` |
| 引擎 | <engine> |
| 估算行数 | ~<row_count> |
| 业务含义 | （根据字段推断或待补充）|

## 字段清单

| 字段名 | 类型 | 业务含义 |
|--------|------|---------|
| <field_1> | <type> | （待补充）|
| ... | ... | ... |

## 数据样例

（粘贴 LIMIT 3 结果）

## 注意事项

- （有特殊过滤条件时在此记录）
```

### Step 3：更新 `_index.md`

更新全局索引文件中的表清单：
- 新增表 → 加入对应章节，链接到新建的 `tables/<表名>.md`
- 删除表 → 标记为 `~~已废弃~~`
- 版本号递增（如 v3.0 → v4.0）

### Step 4：输出变更摘要

```
✅ 知识库更新完成
  环境: <环境名>
  新增文档: N 张（列出表名）
  更新文档: M 张（列出表名）
  未变更: K 张
  废弃标记: P 张

下次分析将使用最新本地知识库，无需重新探索数据库结构。
```

---

## 增量更新模式（快速）

若用户只想更新特定表，可跳过 Step 1，直接：

```
用户: 更新 crm.realtime_dwd_crm_call_record 的文档
→ 执行 Step 2（仅该表）→ 更新 _index.md 中对应行 → 输出摘要
```

---

## 注意事项

1. **写入权限**：只能写入 `{CURRENT_USER}/` 目录
2. **超大表**：对于 TiB 级别的表，跳过 `SELECT *` 样例，改用 `LIMIT 1` 验证表可访问
3. **视图**：`system.tables` 中 engine LIKE '%View%' 的跳过（不记录视图结构）
4. **不覆盖已有内容**：若表文档已有人工补充的业务含义/枚举值等，更新时保留这些内容（追加新字段，不清空旧内容）
