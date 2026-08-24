---
name: _base-tools
version: "1.0"
description: MCP工具使用基础规范——始终注入，所有场景强制生效
triggers: []
category: system
priority: high
always_inject: true
---

# MCP 工具使用基础规范（始终生效）

以下规则在**所有工具调用场景中强制执行**：

## 1. 文件操作工具

- **写入前必须确认目标目录**：仅允许向 `{CURRENT_USER}/` 或 `.claude/skills/user/` 写入；其他路径操作前须用户确认
  ⚠️ 路径说明：文件系统根目录已指向 `customer_data/`，直接用 `{CURRENT_USER}/子路径` 即可，**禁止在路径中重复写 `customer_data/`**（否则产生双层目录）
- **批量写入须声明范围**：写入多个文件前，先列出将创建/修改的文件清单，获得用户确认
- **禁止覆盖用户现有文件**：写入前检查文件是否存在；如存在须明确征得同意方可覆盖

## 2. 数据库工具（ClickHouse）

- **查询前优先探索表结构**：使用 `list_tables` / `describe_table` 了解表结构，再编写 SQL
- **SELECT 加 LIMIT 保护**：首次查询数据时默认加 `LIMIT 100`，避免全表扫描
- **禁止无 WHERE 的 DELETE/UPDATE**：必须有明确过滤条件，执行前向用户展示影响行数估计
- **DDL 变更二次确认**：`CREATE TABLE` / `ALTER TABLE` / `DROP` 执行前明确向用户描述变更影响
- **⚠ 明细表默认假设有重复数据**：`*_ods_*` / `*_dwd_*` / `*_record*` / `*_history` / `*_extend` 及任何 `ReplacingMergeTree` 表，未经本次会话实测验证前一律当作**可能有重复**。
  出任何数字结论前必须三选一，不许跳过：
  1. 先跑重复性探查并把结果告诉用户 —— `SELECT count() AS rows, uniqExact(<业务主键>) AS uniq_pk FROM <tbl> WHERE <单天条件>`；ReplacingMergeTree 再比一次 `count()` 与 `count() FINAL`
  2. 用天然去重写法（`uniqExact` / `groupBitmap` / `argMax` 取最新 / `FINAL`），并在结论中写明去重口径
  3. 无法验证时明确声明"此数未去重，可能偏高"，不得默默给出数字
  常见重复来源：ReplacingMergeTree 未 merge 时不带 `FINAL` 会重复计数；ETL 并发跑批把同一天插两遍（HTTP 连接被 kill 后服务端 INSERT 仍继续）；同一业务 ID 在明细表有多条；JOIN 键不足导致扇出放大。拿不准就问用户，别猜。

## 3. 工具调用顺序

- **读后写原则**：修改操作（write_file、execute_query 写类 SQL）执行前，先通过读类工具确认当前状态
- **串行执行高风险操作**：多个写入操作不得并发执行，须逐一确认结果后再继续
- **工具失败立即停止**：工具返回 `success: false` 或错误时，停止后续操作，向用户报告错误原因

## 4. 结果验证

- **写入后验证**：`write_file` 后紧跟 `read_file` 验证内容正确；SQL INSERT 后检查影响行数
- **查询结果有限展示**：返回大量数据时，只展示前 20 行并说明总行数，不要倾倒原始数据

## 5. 透明度

- **工具调用前声明意图**：执行工具前先用一句话描述"我将要做什么"，让用户了解下一步
- **错误要解释根因**：工具失败时，分析是权限问题、路径问题还是数据问题，给出明确原因
