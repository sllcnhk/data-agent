# 数据智能体系统 — 核心使用手册

**文档版本**: v2.7
**适用系统版本**: P0 + P1 + P2 + P3 + 3-Tier Skill System + Semantic Skill Routing + RBAC + 角色管理页面 + 推理过程持久化 + ContinuationCard + ClickHouse 动态多区域配置 + Session 过期管理 + 对话打断（停止生成）+ 对话附件上传 + 用户技能目录隔离修复 + 对话用户隔离 + 侧边栏 Tab UI + 只读模式 + is_shared 群组框架 + 技能路由可视化 + 文件写入下载 + **Excel → ClickHouse 数据导入**（2026-04-05）+ **Skill 用户使用权限隔离 T1–T6**（2026-04-08）+ **多图表 HTML 报告生成**（2026-04-13）
**读者对象**: 数据工程师、数据分析师、系统管理员

---

## 目录

1. [系统整体架构概览](#1-系统整体架构概览)
2. [如何判断任务类型（ETL / 分析 / 普通问答）](#2-如何判断任务类型)
3. [何时进入任务列表 & 任务生命周期](#3-任务列表与生命周期)
4. [在哪查看任务进度](#4-查看任务进度)
5. [查看工具调用过程（MCP Skills）](#5-查看工具调用过程)（含 5.4 推理过程持久化、5.5 续接提示横幅、5.6 停止生成、5.7 发送附件、5.8 技能路由可视化、5.9 查看与下载 Agent 生成文件）
6. [查看当前对话/任务用了哪些 Agent](#6-查看使用了哪些-agent)
7. [三层技能体系（Skills）管理](#7-管理和更新-skills)（含 7.6 语义混合命中）
8. [Human-in-the-Loop 高危操作审批](#8-高危操作审批流程)
9. [多模型支持与对话内切换](#9-多模型支持与对话内切换)
10. [用户认证与多用户管理（RBAC）](#10-用户认证与多用户管理rbac)（含 10.9 Session 过期行为说明）
11. [快速参考卡](#11-快速参考卡)
12. [Excel → ClickHouse 数据导入（superadmin 专属）](#12-excel--clickhouse-数据导入superadmin-专属)
13. [SQL → Excel 数据导出（superadmin 专属）](#13-sql--excel-数据导出superadmin-专属)
14. [多图表 HTML 报告生成](#14-多图表-html-报告生成)

---

## 1. 系统整体架构概览

```
用户输入（聊天框 / Tasks 页面）
            │
            ▼
    MasterAgent（意图路由）
    ┌─────────┬──────────┬──────────┐
    │ ETL     │  数据分析  │  普通问答  │
    │ Agent   │  Agent   │  (通用)  │
    └────┬────┴────┬─────┴────┬─────┘
         │         │          │
         ▼         ▼          ▼
    MCP 工具调用   只读 MCP    直接 LLM
   (完整 DDL 权限)  (SQL 查询)
         │         │
         └────┬────┘
              │
              ▼
    ThoughtProcess（推理过程折叠面板）
              │
              ▼  ← 检测到 DROP / TRUNCATE / DELETE
    ApprovalModal（60 秒人工审批弹窗）
              │
              ▼
    最终回答（SSE 流式输出至聊天界面）
```

**核心组件说明**

| 组件 | 位置 | 职责 |
|------|------|------|
| MasterAgent | `backend/agents/orchestrator.py` | 意图路由，选择合适的专业 Agent |
| ETLEngineerAgent | `backend/agents/etl_agent.py` | 数据加工、建表、ETL 脚本生成 |
| DataAnalystAgent | `backend/agents/analyst_agent.py` | 数据查询、统计分析、报表生成 |
| AgenticLoop | `backend/agents/agentic_loop.py` | Think → Act → Observe 推理循环 |
| AgentOrchestrator | `backend/agents/orchestrator_v2.py` | 多 Agent 协作 Handoff 编排 |
| ApprovalManager | `backend/core/approval_manager.py` | 高危操作审批队列 |
| SkillLoader | `backend/skills/skill_loader.py` | SKILL.md 文件加载与热更新 |

---

## 2. 如何判断任务类型

### 2.1 路由机制：关键词评分

系统在您发送消息的瞬间**自动评分路由，无需手动选择 Agent**。

核心逻辑（`MasterAgent._select_agent()`）：

```
消息文本
  │
  ├─ 统计 ETL 关键词命中数   → etl_score
  ├─ 统计 分析关键词命中数   → analyst_score
  │
  ├─ 两者均为 0            → 通用 AgenticLoop（不调 MCP）
  ├─ etl_score >= analyst_score → ETL 工程师 Agent
  └─ analyst_score > etl_score  → 数据分析师 Agent
```

**ETL 路由关键词**（`_ETL_KEYWORDS`）

> etl、宽表、数据加工、合并表、数据整合、脚本生成、建表、create table、
> insert into、数据管道、pipeline、数据清洗、增量、全量、分区表、数据接入

**分析路由关键词**（`_ANALYST_KEYWORDS`）

> 分析、统计、留存、漏斗、趋势、同比、环比、分布、数据分析、用户行为、
> 转化率、dau、mau、retention、funnel、报表、看数、查询

### 2.2 实际示例对比

```
✅ 路由 → ETL 工程师 Agent
   "帮我设计一个用户行为宽表的 ETL 加工脚本"
   "建一张 ClickHouse 分区表，增量接入订单数据"
   "生成全量同步脚本，合并三张表到 dwd_order_wide"
   "写 CREATE TABLE 和 INSERT INTO 语句"

✅ 路由 → 数据分析师 Agent
   "统计最近 7 天的 DAU 趋势"
   "分析用户留存漏斗，看各环节转化率"
   "月环比对比订单量，生成报表"
   "查询 VIP 用户地域分布"

✅ 保持普通问答（通用 AgenticLoop，不调用 MCP）
   "什么是 ClickHouse 的 MergeTree？"
   "解释一下 ETL 和 ELT 的区别"
   "如何优化 SQL 查询性能？"（无数据库上下文时）
   "帮我写一首诗"
```

### 2.3 前端可见标识

消息被路由至专业 Agent 时，**推理过程面板**的第一条事件会显示：

```
💡 [编排器] 将请求路由至 数据加工工程师
```
或
```
💡 [编排器] 将请求路由至 数据分析师
```

> 展开助手消息上方的「推理过程 🔵N 🟠M」折叠面板即可看到。

### 2.4 多 Agent 协作（Handoff）

当单条消息同时涉及 ETL 和分析，AgentOrchestrator 会自动编排**两跳流程**：

```
用户: "建立用户留存宽表并分析最近 30 天的留存趋势"

跳 1 → ETL 工程师 Agent
         → 查表结构 → 生成宽表 SQL → 触发 Handoff 信号

[Handoff] 数据加工工程师 → 数据分析师
          移交产出: schema, sql_script

跳 2 → 数据分析师 Agent
         → 执行留存查询 → 生成分析结论
```

推理面板会完整展示两个 Agent 的所有执行步骤。

---

## 3. 任务列表与生命周期

### 3.1 哪些操作会创建任务

系统存在**两类任务入口**，行为不同：

#### 入口 A：Tasks 页面手动提交（显式任务，推荐用于长时间处理）

```
导航栏 → Tasks → 填写任务描述 → 选择优先级 → 提交
  ↓
POST /api/v1/agents/tasks
  ↓
立即创建持久化 Task 记录（有唯一 task_id）
  ↓
显示在任务列表，可随时查进度、历史、结果
  ↓
5 个并发 Worker 拾取并执行
```

#### 入口 B：聊天框发送消息（对话内任务，适合即时交互）

聊天消息本身**不直接创建任务记录**，而是创建一条 Message，但：

| 场景 | 是否进任务列表 |
|------|--------------|
| 普通问答 | ❌ 不进 |
| ETL / 分析（无 MCP 工具调用） | ❌ 不进 |
| ETL / 分析（调用了 MCP 工具执行 SQL） | ✅ 自动关联至 Message 记录 |
| Tasks 页面明确提交的任务 | ✅ 完整任务记录 |

> **实践建议**：复杂 ETL 脚本生成、批量数据处理 → 用 **Tasks 页面**，享有完整进度跟踪、失败重试和历史记录；简单临时查询/问答 → 用**聊天框**即可。

### 3.2 任务状态机

```
创建
  │
  ▼
PENDING（等待中）──── 手动取消 ────→ CANCELLED
  │
  ▼  Worker 拾取
RUNNING（执行中）──── 异常 ────→ FAILED ──── 重试（≤3次）──→ PENDING
  │
  ▼  执行完成
COMPLETED（已完成）
```

### 3.3 任务类型与对应 Agent

| task_type | 说明 | 执行 Agent |
|-----------|------|-----------|
| `etl_design` | ETL 方案设计 + SQL 生成 | ETL_ENGINEER |
| `sql_generation` | SQL 脚本生成 | ETL_ENGINEER |
| `data_analysis` | 数据统计分析 | DATA_ANALYST |
| `data_export` | 数据导出 | DATA_ANALYST |
| `report_creation` | 报表生成 | DATA_ANALYST |
| `database_connection` | 数据库连接测试 | GENERALIST |
| `custom` | 自定义任务（按关键词路由） | 自动判断 |

---

## 4. 查看任务进度

### 4.1 Tasks 页面（最直观）

```
导航栏 → Tasks
```

任务列表列说明：

| 列名 | 内容 |
|------|------|
| **Task ID** | 唯一标识（可复制用于 API 查询） |
| **Status** | ⏱️ 等待 / 🔄 运行中 / ✅ 完成 / ❌ 失败 / ❌ 取消 |
| **Agent Type** | 蓝色标签，执行该任务的 Agent 类型 |
| **Priority** | low / normal / high / urgent（颜色区分） |
| **Created At** | 创建时间 |
| **Completed At** | 完成时间（运行中显示 "-"） |

点击「详情」弹窗，可查看：

```
Task ID:      abc-123-def
Status:       RUNNING        Agent: DATA_ANALYST
Priority:     NORMAL         Retry: 0 / 3

Started:      2026-03-01 14:30:00
Input Data:   {"query": "统计DAU趋势", "context": {...}}

──── 实时进度 ────
当前步骤:  查询数据库
进度:      45%
```

### 4.2 REST API 查询（适合集成/自动化）

```bash
# 查询单个任务状态
GET /api/v1/agents/tasks/{task_id}/status

# 响应示例
{
  "task_id": "abc-123",
  "status": "running",
  "agent_type": "DATA_ANALYST",
  "progress": 45,
  "current_step": "查询数据库",
  "error": null,
  "result": null
}

# 查询特定 Agent 的所有任务历史
GET /api/v1/agents/{agent_id}/tasks?status=completed&limit=20

# 查看全局系统健康（队列大小、Agent 状态）
GET /api/v1/agents/health
```

---

## 5. 查看工具调用过程

### 5.1 推理过程面板（ThoughtProcess）

每条助手消息上方均有折叠面板，**显示本次回答的完整推理步骤**：

```
┌──────────────────────────────────────────────────────┐
│  推理过程   🔵4   🟠2                      ▼ 展开    │
└──────────────────────────────────────────────────────┘
```

| 徽标 | 含义 |
|------|------|
| 🔵N | 总推理步骤数（skill_matched + thinking + tool_call + tool_result） |
| 🟠M | MCP 工具调用次数 |

**展开后的层级结构：**

```
▼ 推理过程  🔵5  🟠2
  │
  ├─ 🧠 技能路由  [hybrid]  ← 新增（最先出现）
  │     命中技能: clickhouse-analyst (user · keyword · "sg","账单")
  │     始终注入: _base-safety, _base-tools
  │     注入字符: 4,320
  │
  ├─ 💡 思考中…
  │     "正在分析 (第 1 轮)..."
  │
  ├─ 🔧 调用工具: clickhouse_idn__list_tables
  │     参数: {"database": "default"}
  │
  ├─ ✅ 工具返回: clickhouse_idn__list_tables
  │     {"tables": ["orders", "users", "events"]}
  │
  ├─ 🔧 调用工具: clickhouse_idn__query
  │     参数: {"query": "SELECT toDate(created_at) AS dt,
  │                       count() AS cnt FROM orders
  │                       GROUP BY dt ORDER BY dt"}
  │
  └─ ✅ 工具返回: clickhouse_idn__query
        {"rows": [...], "row_count": 30}
```

### 5.2 MCP 工具命名规则

工具名格式：`{MCP服务器名（下划线）}__{工具名}`

| 工具名示例 | 说明 |
|-----------|------|
| `clickhouse_idn__query` | ClickHouse 服务器 → 执行 SQL 查询 |
| `clickhouse_idn__list_tables` | ClickHouse → 列出所有表 |
| `clickhouse_idn__describe_table` | ClickHouse → 查看表结构（Schema） |
| `filesystem__read_file` | Filesystem MCP → 读取本地文件 |
| `filesystem__write_file` | Filesystem MCP → 写入文件 |
| `lark__create_doc` | Lark MCP → 创建飞书文档 |
| `mysql_prod__query` | MySQL 生产库 → 执行查询 |

> **数据分析师安全限制**：DataAnalystAgent 使用 `ReadOnlyMCPProxy`，
> 仅允许 `query`、`list_tables`、`describe_table` 等只读工具，
> 任何写操作（INSERT / UPDATE / DROP）会被自动拦截并报错。

### 5.3 REST API 查看 MCP 配置

```bash
# 查看已连接的 MCP 服务器列表
GET /api/v1/mcp/servers

# 查看特定服务器的可用工具列表
GET /api/v1/mcp/servers/{server_name}/tools

# 测试服务器连通性
POST /api/v1/mcp/servers/{server_name}/test
```

---

### 5.4 推理过程持久化（刷新后历史可查）

每条助手消息的推理过程（thinking / tool_call / tool_result 事件）**会随消息一起持久化到数据库**，页面刷新或重新进入对话后依然可以展开查看。

**工作原理**：

```
流式响应期间（AgenticLoop 推理中）
  ↓ conversation_service 收集所有 thinking/tool_call/tool_result 事件
    （工具返回内容 > 2000 字符时自动截断，保留前 2000 字符 + "…（已截断）"）
  ↓ 推理结束后，事件列表写入助手消息的 extra_metadata['thinking_events']

GET /api/v1/conversations/{id}/messages
  ↓ 每条助手消息返回顶层字段 thinking_events: [...]

前端 loadMessages()
  ↓ 遍历 thinking_events → 调用 addThoughtEvent() 还原 messageThoughts 状态
  ↓ 用户刷新页面后，ThoughtProcess 面板照常展开
```

**使用场景**：

| 场景 | 说明 |
|------|------|
| 刷新页面 | 历史消息的推理面板自动还原，无需重新生成 |
| 排查问题 | 长时间运行的任务完成后，可随时回看具体调用了哪些工具及其返回值 |
| 工具结果过大 | 超过 2000 字符的返回值自动截断，避免数据库存储过大 |

```bash
# 查看某条消息的持久化推理事件
GET /api/v1/conversations/{conv_id}/messages

# 响应中每条 assistant 消息包含（如有推理事件）：
{
  "role": "assistant",
  "content": "...",
  "thinking_events": [
    {"type": "thinking", "data": "正在分析 (第 1 轮)..."},
    {"type": "tool_call", "data": {"tool": "clickhouse_idn__list_tables", "input": {...}}},
    {"type": "tool_result", "data": {"content": "...", "is_truncated": false}}
  ]
}
```

---

### 5.5 续接提示横幅（ContinuationCard）

当 Agent 推理轮次接近上限、自动开启下一轮推理时，聊天界面会插入一条**续接提示横幅**（而非普通用户/助手气泡），清晰标注系统行为。

**横幅样式**：

```
┌ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ┐
  🔄  Agent 自动续接   [1/3]                         展开详情 ▼
└ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ┘
```

点击「展开详情」后：

```
┌ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ┐
  🔄  Agent 自动续接   [1/3]                         收起 ▲
  ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─
  📄 上一轮结论摘要
  ┌──────────────────────────────────────────────┐
  │ 已完成用户行为宽表结构分析，共发现 8 张关联表… │
  └──────────────────────────────────────────────┘

  📋 待完成任务（3 项）
  • 设计 ETL 合并逻辑（source_db.events → dwd_user_wide）
  • 编写 CREATE TABLE 语句（MergeTree，按日期分区）
  • 生成 INSERT INTO ... SELECT 增量脚本
└ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ┘
```

**关键行为说明**：

| 属性 | 说明 |
|------|------|
| 显示位置 | 两轮 AI 回复之间，跳过头像区域（左缩进 48px）|
| 轮次标签 | `[N/3]` 表示当前是第 N 次续接，最多 3 次 |
| 详情内容 | 可折叠；包含上一轮结论摘要 + 待完成任务列表 |
| 数据来源 | 从消息的 `extra_metadata` 读取（兼容旧格式：从 content 正则提取）|
| 与用户消息的区别 | 这是**系统行为记录**，不是用户发送的消息，也不是 AI 回复的气泡 |

**3 次续接耗尽后**：弹出「是否继续」确认 Modal，展示剩余待完成任务列表，用户可选择继续（系统再送一轮续接提示）或暂停。

### 5.6 停止生成（中断正在进行的生成）

当 Agent 正在进行推理时，如果发现消息有误或想补充信息，可以随时点击**「停止生成」**按钮中断生成。

**触发条件**：发送消息后（`sending=true`），输入框上方会出现「停止生成」红色按钮。

**交互流程**：

```
① 点击「停止生成」按钮
   → 按钮进入 loading 状态（防止重复点击）
   → 前端发送 POST /api/v1/conversations/{id}/cancel

② 后端接收取消信号
   → LLM 调用被中途中止（不等待完整 AI 响应）
   → 已生成的部分文本被保留

③ 已生成内容保存到数据库
   → 助手消息末尾追加中断标记：
     「---
      *（生成已被用户中断）*」

④ 前端恢复可输入状态
   → toast 提示「已停止生成」
   → 用户可正常发送下一条消息
```

**中断后的消息样式**：

```
┌──────────────────────────────────────────────┐
│ 助手                                          │
│                                              │
│ 根据您的数据，IDN 区域 3 月份 DAU 为 125 万，  │
│ 环比增长 8.3%。以下是各渠道的详细拆分：        │
│                                              │
│ 1. 有机渠道：68 万 (+12%)                     │
│ 2. 付费渠道：                                 │
│                                              │
│ ---                                          │
│ *（生成已被用户中断）*                         │
└──────────────────────────────────────────────┘
```

**注意事项**：

| 场景 | 行为 |
|------|------|
| 生成刚开始，AI 还未输出任何文字 | 保存一条含中断标记的空助手消息 |
| 生成进行中，已有部分输出 | 保存已输出部分 + 中断标记 |
| ETL 工具正在执行时点击停止 | 等当前工具调用完成后再响应取消（约 <5s 延迟） |
| 发送下一条消息 | 取消状态自动清除，正常生成 |
| 多对话并发 | 取消信号精确匹配 conversation_id，不影响其他对话 |

**API 说明**（程序化场景）：

```bash
# 停止正在进行的生成（幂等，多次调用无副作用）
POST /api/v1/conversations/{id}/cancel

# 响应
{"status": "cancellation_requested", "conversation_id": "..."}
```

> **权限说明**：取消端点与其他对话端点保持一致，无独立鉴权依赖。`ENABLE_AUTH=true` 时，前端停止按钮仅在持有 `chat:use` 权限的已登录用户界面中可见。

---

### 5.7 发送附件（图片 / 文档 / 文件）

您可以在发送消息时附带文件附件，AI 会直接读取附件内容进行分析。支持**图片、PDF、文本文件、CSV、JSON** 等多种格式。

**支持的文件类型**：

| 类型 | MIME 类型 | 说明 |
|------|----------|------|
| 图片 | `image/jpeg`, `image/png`, `image/gif`, `image/webp` | AI 可识别图片内容（截图、照片、图表等）|
| PDF | `application/pdf` | AI 可读取 PDF 文本内容（不含图片提取）|
| 文本 | `text/plain`, `text/csv`, `text/markdown` | 纯文本、CSV、Markdown |
| JSON | `application/json` | JSON 配置或数据文件 |

**限制**：
- 单文件最大 **20MB**
- 单条消息最多附件数：无硬性限制（建议 ≤ 5 个，避免请求过大）

**使用方式**：

#### 方式 A：点击回形针按钮

```
① 点击输入框右侧「📎 回形针」按钮
② 在文件选择器中选择一个或多个文件
③ 发送前可在输入框上方预览附件（文件名 + 图标）
④ 点击「×」可移除单个附件
⑤ 输入文字（可选）后点击「发送」
```

#### 方式 B：粘贴图片（仅限图片文件）

```
① 截图或复制图片到剪贴板
② 在输入框中按 Ctrl+V（Windows/Linux）或 Cmd+V（Mac）
③ 自动识别并显示附件预览
④ 点击「发送」
```

**附件在对话历史中的呈现**：

| 位置 | 显示方式 | 说明 |
|------|----------|------|
| 发送前预览 | Tag chips（可删除） | 输入框上方显示，蓝色背景 + 文件名 + × 按钮 |
| 当前消息（发送后） | 无特殊标记 | AI 已读取内容，无需额外标注 |
| 历史消息（刷新后） | 紧凑 Tag chips | 消息气泡下方显示小尺寸标签，仅文件名 + 类型图标 |

**AI 如何处理附件**：

| 附件类型 | AI 行为 |
|----------|---------|
| 图片 | 通过 Claude API 视觉能力识别图片内容（图表、文字、截图等）|
| PDF | 提取文本内容，作为上下文注入（图片部分不提取）|
| 文本/CSV/JSON | 解码后作为纯文本注入 AI 上下文 |

**示例对话**：

```
用户: [📎 screenshot.png] + 文字：「这个报错怎么解决？」
助手: （分析截图）从错误信息看，是 ClickHouse 连接超时……
```

**注意事项**：

1. **编码问题**：文本文件必须是 UTF-8 编码，否则可能出现乱码（AI 会尝试修复，但无法保证）
2. **文件大小**：20MB 限制是前端校验，超出会显示错误提示（红色 banner）
3. **附件存储**：附件元数据（文件名、类型、大小）存入数据库，base64 内容不持久化（历史消息不会重复传输）
4. **MIME 推断**：Windows 系统某些文件（如 `.md`）的 `file.type` 为空，系统会按扩展名自动推断类型

---

### 5.8 技能路由可视化（Skill Match Visibility）

每次回答生成前，推理面板会在**最顶部**展示一个 🧠 **技能路由** 折叠项，告诉您系统自动加载了哪些 Skill、为什么加载，以及有无加载失败的技能文件。

#### 🧠 技能路由面板展开后的内容

```
🧠 技能路由  [hybrid]
├─ 命中技能
│   ├─ 📗 clickhouse-analyst  [user · keyword]
│   │      命中触发词: "sg", "账单", "呼叫"
│   └─ 📘 schema-explorer     [system · semantic · 0.82]
├─ 始终注入: _base-safety, _base-tools
├─ 注入字符: 8,640（⚠ > 12,000 时橙色警告；摘要模式时红色警告）
└─ 加载错误: 无（有错误时列出问题文件及原因）
```

| 字段 | 含义 |
|------|------|
| **模式标签** | `keyword` / `hybrid` / `llm` — 当前 `SKILL_MATCH_MODE` 配置 |
| **tier 徽标** | `user`（绿）/ `project`（蓝）/ `system`（灰）— 技能所在层 |
| **method** | `keyword` = 关键词命中；`semantic` = LLM 语义打分命中（显示分数） |
| **hit_triggers** | 关键词命中时列出具体触发词，帮助确认触发原因 |
| **注入字符** | 本次注入进 System Prompt 的字符总量；> 16,000 时降为摘要模式 |
| **加载错误** | Skill 文件格式错误（如缺少 YAML frontmatter）时列出，方便排查 |

#### 常见问题排查

| 现象 | 可能原因 | 排查方法 |
|------|---------|---------|
| 🧠 面板显示「命中技能: 无」 | 消息未命中任何触发词，且语义分 < 0.45 | 查看 `hit_triggers` 是否为空；尝试 `GET /api/v1/skills/preview?message=xxx` |
| 技能未加载，面板显示加载错误 | Skill 文件缺少 YAML frontmatter（`---` 头部）| 打开对应 `.md` 文件，确保首行为 `---` 并有完整 `name/triggers` 字段 |
| 注入字符数显示橙色/红色 | 命中技能内容过多，接近或超出 16,000 字符上限 | 减少触发技能数量，或将部分内容移入 `db_knowledge/` 按需读取 |
| hybrid 模式未触发语义路由 | `llm_adapter` 不可用或 LLM 调用失败 | 面板会显示 method=keyword；检查 LLM 配置是否正常 |

> **提示**：`GET /api/v1/skills/load-errors` 可直接查询所有加载失败的技能文件，无需等到实际对话触发（需 analyst 及以上角色）。

---

### 5.9 查看与下载 Agent 生成文件

当 Agent 在对话中执行数据分析或 ETL 任务时，可能会将结果写入 `customer_data/` 目录（CSV、JSON、Excel 报告等）。系统会在**助手消息末尾**自动渲染文件下载卡片，无需额外操作即可找到生成的文件。

#### 下载卡片外观

```
📎 生成的文件（点击下载）
┌──────────────────────────────────────────────┐
│ 📊  monthly_report.xlsx          1.2 MB  [下载] │
│ 📄  summary.csv                   45 KB  [下载] │
└──────────────────────────────────────────────┘
```

#### 操作步骤

```
① 等待 AI 完成回复
② 在助手消息底部（文字内容之下）查看「📎 生成的文件」卡片
③ 点击「下载」按钮 → 浏览器弹出保存对话框 → 选择保存路径
```

#### 文件类型图标说明

| 图标颜色 | 文件类型 |
|---------|---------|
| 绿色表格图标 | Excel（`.xlsx`/`.xls`）|
| 蓝色文档图标 | 文本/CSV/JSON/SQL 等 |
| 红色 PDF 图标 | PDF 文件 |
| 绿色图片图标 | 图片文件 |
| 橙色压缩图标 | ZIP/GZ 压缩包 |
| 灰色通用图标 | 其他格式 |

#### 历史对话中的文件

刷新页面后，历史消息中的文件下载卡片**仍然可见**（文件元数据已持久化到数据库）。但请注意：
- **下载依赖文件实体存在**：若服务器上的文件已被删除或清理，下载按钮点击后会收到 404 错误。
- **文件按用户隔离**：您只能下载自己对话中由 Agent 生成的文件，无法访问其他用户的文件（返回 403）。

#### 月份子文件夹（管理员可选）

系统管理员可在 `.env` 中配置 `FILE_OUTPUT_DATE_SUBFOLDER=true`，启用后 Agent 会自动将文件写入当月子目录（如 `2026-03/`）。这便于按月归档和批量清理历史数据，对用户的下载体验无影响。

#### 示例对话

```
用户: 帮我统计 IDN 地区上个月的销售汇总，导出 Excel
助手: 正在查询 ClickHouse…（推理过程折叠）
      已生成销售汇总，主要发现：……（正文）

📎 生成的文件（点击下载）
┌───────────────────────────────────────────┐
│ 📊  idn_sales_2026-02.xlsx    256 KB  [下载] │
└───────────────────────────────────────────┘
```

---

## 6. 查看使用了哪些 Agent

### 6.1 在聊天对话中

**方式 A — 推理过程面板（最直观，无需额外操作）**

展开助手消息上方的推理面板，第一条 `thinking` 事件显示路由结果：

```
💡 [编排器] 将请求路由至 数据加工工程师
```

多 Agent 协作场景下还会显示 Handoff 事件：

```
[Handoff] 数据加工工程师 → 数据分析师
  移交摘要: 宽表 SQL 已完成
  产出物: schema, sql_script, row_count
```

**方式 B — 消息元数据（开发者调试）**

SSE 流的最后一条 `assistant_message` 事件中包含：

```json
{
  "type": "assistant_message",
  "data": {
    "id": "msg-abc",
    "metadata": {
      "agent_type": "etl_engineer",
      "event_count": 8,
      "approval_events": 1
    }
  }
}
```

**方式 C — API 查询对话消息**

```bash
GET /api/v1/conversations/{conv_id}/messages
# 每条 assistant 消息的 metadata 字段包含 agent_type
```

### 6.2 在任务列表中

任务列表每行直接显示 **Agent Type** 蓝色标签：

| 标签 | 说明 |
|------|------|
| `DATA_ANALYST` | 数据分析师 Agent |
| `ETL_ENGINEER` | 数据加工工程师 Agent |
| `SQL_EXPERT` | SQL 脚本专家 |
| `GENERALIST` | 通用助手（无专业 Agent） |

点击「详情」弹窗可查看完整的 Agent 信息和任务 input_data。

### 6.3 API 查询 Agent 任务历史

```bash
# 查询某 Agent 的历史任务
GET /api/v1/agents/{agent_id}/tasks?limit=10&status=completed

# 查询 Agent 整体指标（完成/失败数量）
GET /api/v1/agents/{agent_id}/metrics

# 响应示例
{
  "agent_id": "agent-etl-001",
  "agent_type": "ETL_ENGINEER",
  "status": "IDLE",
  "metrics": {
    "total_tasks": 42,
    "completed_tasks": 38,
    "failed_tasks": 2,
    "cancelled_tasks": 2
  }
}
```

### 6.4 路由建议查询

在提交任务前，可先询问系统推荐使用哪个 Agent：

```bash
GET /api/v1/agents/routing/suggestions?query=帮我分析用户留存漏斗

# 响应示例（Top-3 推荐）
[
  {"agent_type": "DATA_ANALYST", "confidence": 0.92, "reason": "留存/漏斗关键词匹配"},
  {"agent_type": "SQL_EXPERT",   "confidence": 0.60, "reason": "查询相关"},
  {"agent_type": "GENERALIST",   "confidence": 0.30, "reason": "兜底"}
]
```

Tasks 页面在输入任务描述时会**自动调用此接口**，实时展示推荐 Agent。

---

## 7. 管理和更新 Skills

系统支持 **三层技能体系（3-Tier Skill System）**，每层的写入权限和注入优先级不同：

```
Tier 1 · 系统技能（system/）   — 开发人员 Git 维护，只读
Tier 2 · 项目技能（project/）  — 管理员通过 REST API 维护（需 X-Admin-Token）
Tier 3 · 用户技能（user/）     — 用户在前端/API 自由创建、编辑、删除
```

注入顺序（靠近指令的优先级最高）：
```
[Base System Prompt]
  ↓ 用户技能（Tier 3，触发匹配，最多 3 条）
  ↓ 项目技能（Tier 2，触发匹配，最多 3 条）
  ↓ 系统 base 技能（_base-*.md，始终注入）
  ↓ 系统触发技能（Tier 1，触发匹配，最多 3 条）
```

---

### 7.1 在前端管理技能（推荐）

```
导航栏 → Skills
```

页面分为三个标签页：

#### 系统技能标签（System）

展示所有 `.claude/skills/system/` 目录的技能，**只读**（显示锁形图标）。

| 文件名 | 技能名 | 始终注入 | 描述 |
|--------|--------|---------|------|
| `_base-safety.md` | _base-safety | ✅ | 数据写入范围、DB 操作安全、PII 保护 |
| `_base-tools.md` | _base-tools | ✅ | MCP 工具使用规范（读后写、串行高危操作等）|
| `etl-engineer.md` | etl-engineer | — | ETL 工程专业规程，触发词：etl、建表… |
| `schema-explorer.md` | schema-explorer | — | 数据库结构探索 |
| `clickhouse-analyst.md` | clickhouse-analyst | — | ClickHouse 分析规程 |
| `project-guide.md` | project-guide | — | 项目架构/功能导读 |
| `skill-creator.md` | skill-creator | — | 技能创建引导 |

> **始终注入（always_inject）**：文件名以 `_base-` 开头的技能，无论用户消息内容如何都会注入到每次对话的系统提示中。

#### 项目技能标签（Project）

展示 `.claude/skills/project/` 目录的技能，**管理员可增删改**。

操作说明：
- 点击「新增项目技能」→ 填写表单 → 输入管理员 Token → 确认创建
- 列表中点「编辑」→ 修改字段 → 输入管理员 Token → 保存
- 点「删除」→ 确认 → 输入管理员 Token → 删除
- 管理员 Token 在首次输入后自动缓存到 `sessionStorage`，当次浏览器会话内无需重复输入

管理员 Token 通过环境变量 `ADMIN_SECRET_TOKEN` 配置，放入请求头 `X-Admin-Token`。

#### 用户技能标签（My Skills）

展示当前登录用户在 `.claude/skills/user/{username}/` 目录下创建的技能，**仅自己可见、可增删改**。

> **用户技能隔离（T1–T6）**：`ENABLE_AUTH=true` 时，每位用户只能看到自己创建的技能（`owner == username`）和无主技能（`owner == ""`，遗留兼容）。其他用户的私有技能不会出现在列表中，也不会注入到你的对话 System Prompt 中。`ENABLE_AUTH=false`（匿名模式）时，所有用户技能对所有人可见（向后兼容）。

操作说明：
- 「新增技能」→ 填写名称、描述、触发词、内容 → 创建（存入 `user/{你的用户名}/`）
- 「编辑」(铅笔图标) → 修改任意字段（版本自动递增）→ 保存
- 「提升」(上升图标) → 将用户技能推广为项目技能（需管理员 Token）
- 「删除」→ 确认删除

#### 触发测试面板

页面顶部的「触发测试」展开面板：

```
输入测试消息 → 点击「测试」
→ 显示各层级触发的技能列表
→ 显示始终注入的技能列表
→ 显示预计注入字符数（> 6000 字符时橙色警告）
```

对应 REST API：`GET /api/v1/skills/preview?message=你的测试消息`

---

### 7.2 通过 REST API 管理技能（开发者/自动化）

#### 用户技能 CRUD

```bash
# 创建用户技能
POST /api/v1/skills/user-defined
Content-Type: application/json

{
  "name": "data-quality-checker",
  "description": "数据分析前先执行数据质量检查",
  "triggers": ["数据质量", "质量检查", "check quality"],
  "category": "analytics",
  "priority": "high",
  "content": "# 数据质量检查规程\n\n分析前必须先执行：\n1. 统计各字段空值率（>5% 需告警）\n2. 检查主键重复率\n3. 识别数值型字段的异常值（3σ原则）"
}

# 更新用户技能（部分字段，版本自动递增）
PUT /api/v1/skills/user-defined/{skill-name}
{
  "description": "更新后的描述",
  "triggers": ["新触发词1", "新触发词2"]
}

# 列出所有用户技能
GET /api/v1/skills/user-defined

# 删除用户技能
DELETE /api/v1/skills/user-defined/{skill-name}
```

#### 项目技能 CRUD（需管理员 Token）

```bash
# 创建项目技能
POST /api/v1/skills/project-skills
X-Admin-Token: <管理员Token>
Content-Type: application/json
{ "name": "...", "description": "...", "triggers": [...], "content": "..." }

# 更新项目技能（版本自动递增）
PUT /api/v1/skills/project-skills/{skill-name}
X-Admin-Token: <管理员Token>
{ "description": "..." }

# 删除项目技能
DELETE /api/v1/skills/project-skills/{skill-name}
X-Admin-Token: <管理员Token>

# 列出项目技能（无需 Token）
GET /api/v1/skills/project-skills
```

#### 触发测试 API

Preview API 使用**当前登录用户身份**决定哪些 user-tier skill 可见，结果与实际对话一致（T6 用户隔离）。superadmin 可通过 `view_as` 参数模拟其他用户的视角。

```bash
# 测试消息会触发哪些技能（默认 hybrid 模式，以当前用户身份过滤 user-tier skill）
GET /api/v1/skills/preview?message=帮我分析用户留存漏斗

# 强制使用纯关键词模式（跳过 LLM 语义路由）
GET /api/v1/skills/preview?message=帮我看看外呼接通情况&mode=keyword

# superadmin 专属：以 alice 的视角预览（view_as 参数，非 superadmin 调用返回 403）
GET /api/v1/skills/preview?message=xxx&view_as=alice

# 响应示例
{
  "message": "帮我分析用户留存漏斗",
  "triggered": {
    "user": [],
    "project": [],
    "system": [{"name": "clickhouse-analyst", "description": "..."}]
  },
  "always_inject": [
    {"name": "_base-safety"},
    {"name": "_base-tools"}
  ],
  "total_chars": 3240,
  "preview_prompt": "# MCP 工具使用基础规范（始终生效）\n...",
  "match_details": {
    "_base-safety":      {"method": "always_inject", "score": 1.0, "tier": "system"},
    "_base-tools":       {"method": "always_inject", "score": 1.0, "tier": "system"},
    "clickhouse-analyst": {"method": "keyword",      "score": 1.0, "tier": "system"}
  }
}
```

> **`match_details` 字段说明**：每个命中 skill 的命中方式、置信度分数和所属层级。
>
> | method | 含义 |
> |--------|------|
> | `keyword` | 关键词精确匹配命中（score 恒为 1.0） |
> | `semantic` | LLM 语义路由命中（score 为 0.45-1.0 之间的置信度） |
> | `always_inject` | 始终注入（score 恒为 1.0） |

#### 查看所有技能（含三层）

```bash
GET /api/v1/skills/md-skills

# 响应包含所有三层技能，每条含字段：
# name, tier, always_inject, is_readonly, description, triggers, version, category, priority
```

---

### 7.3 在聊天中创建技能（对话式，最简单）

直接在聊天框中描述你想要的行为，系统会自动触发 `skill-creator` 元技能：

```
"创建技能：每次分析数据时，先输出数据质量检查报告（空值率、重复率、异常值），
再给出分析结论。触发词：数据质量、质量检查、check quality"
```

引导流程：
```
第 1 步：AI 澄清需求（名称、触发词、行为规程）
   ↓
第 2 步：AI 生成 SKILL.md 内容展示给您确认
   ↓
第 3 步：您确认后，AI 调用 API 保存到 .claude/skills/user/{用户名}/ （ENABLE_AUTH=true）
         或 .claude/skills/user/ （ENABLE_AUTH=false，向后兼容）
   ↓
第 4 步：SkillWatcher 在 0.8 秒内热加载，立即生效
```

---

### 7.4 SKILL.md 文件格式规范

```yaml
---
name: my-skill-name          # 唯一标识，kebab-case，与文件名一致
version: "1.0"               # 版本号（API 更新时自动递增 1.0→1.1）
description: 一行简要描述    # ≤120 字符
triggers:                    # 触发关键词（消息含其一即激活）
  - 关键词1
  - 关键词2
  - keyword3
category: engineering        # engineering | analytics | general | system
priority: medium             # high | medium | low（多技能同时激活时排序用）
always_inject: false         # true = 始终注入，不依赖触发词（仅系统级使用）
---

# 技能标题

[Markdown 内容 — 直接注入到 Agent 系统提示词]
```

> **特殊命名规则**：文件名以 `_base-` 开头（如 `_base-tools.md`）时，
> `always_inject` 自动设为 `true`，无需在 frontmatter 中声明。

> **Context 保护**：当所有激活技能的总注入内容超过 16000 字符时，
> 系统自动切换为摘要模式，仅注入各技能的 `name + description + triggers` 摘要，
> 避免 context 爆炸。

---

### 7.5 热加载说明

```
.claude/skills/ 下任意 .md 文件变更（新建 / 修改 / 删除）
  ↓ SkillWatcher（watchdog）检测到变化（递归监听 system/ project/ user/ 子目录）
  ↓ 800ms 防抖计时器
  ↓ reload_skills() 重新扫描全部三层目录
  ↓ _skill_set_version 递增 → SkillRoutingCache.invalidate_all()（旧缓存自动失效）
  ↓ 更新内存中的技能注册表（单例 SkillLoader）
  ↓ 下一条对话消息即使用新技能
```

---

### 7.6 语义混合命中（Semantic Hybrid Routing）

**背景问题**：纯关键词匹配在以下场景会失效：
- "帮我看看上周外呼接通情况"（无 "外呼分析" 关键词，但语义明确）
- "最近的电话接通率怎么样"（用户表述多样，关键词维护成本高）

**解决方案**：系统默认采用**混合模式（hybrid）**，关键词优先 + LLM 语义补充：

```
用户消息
  ↓ Phase 1（<1ms）：关键词匹配
    → 命中 → 直接注入（score=1.0）
    → 未命中 → 进入 Phase 2

  ↓ Phase 2（~200-400ms，仅对未命中的候选技能）
    → 查询 ChromaDB 路由缓存（相同消息哈希直接复用）
    → 缓存未命中 → LLM 单次批量调用（全部候选 skill 一起评分）
      prompt: "用户消息: {msg}\n候选技能: [{name}: {desc} | {triggers}]\n返回 JSON"
      response: {"skill-name": 0.85, "other-skill": 0.32}
    → 写入缓存（TTL 24h）

  ↓ 合并：score >= 0.45（threshold）的语义命中 + 关键词命中
  ↓ _build_from_matched_skills() 按三层优先级排序注入
```

#### 配置项（`.env`）

```ini
# 匹配模式（默认 hybrid）
SKILL_MATCH_MODE=hybrid    # hybrid | keyword | llm

# LLM 路由置信度阈值（默认 0.45，低于此值不注入）
SKILL_SEMANTIC_THRESHOLD=0.45

# 路由结果缓存 TTL（秒，默认 24 小时）
SKILL_SEMANTIC_CACHE_TTL=86400

# ChromaDB 路由缓存存储路径
SKILL_ROUTING_CACHE_PATH=./data/skill_routing_cache
```

#### 使用建议

| 场景 | 推荐配置 |
|------|---------|
| 生产环境（默认） | `SKILL_MATCH_MODE=hybrid` — 最佳准确率，缓存命中后零延迟 |
| 追求零延迟、不需要语义 | `SKILL_MATCH_MODE=keyword` — 退回纯关键词 |
| 排查语义命中问题 | `GET /api/v1/skills/preview?message=xxx` 查看 `match_details.method` 字段 |
| 强制纯关键词测试 | `GET /api/v1/skills/preview?message=xxx&mode=keyword` |
| 调高/调低语义灵敏度 | 调整 `SKILL_SEMANTIC_THRESHOLD`（范围 0.0-1.0，越低越敏感） |
| 查看实时路由结果 | 直接看对话推理面板的 🧠 技能路由折叠项（无需 API） |
| 查看技能加载错误 | `GET /api/v1/skills/load-errors`（需 analyst 角色），或看推理面板 load_errors 列表 |

#### 降级保护

- LLM 不可用（网络断开、API Key 错误）→ 自动降级为纯关键词，功能不受影响
- ChromaDB 不可用 → 缓存层静默跳过，每次重新请求 LLM
- `llm_adapter=None`（如预览 API）→ 自动降级到纯关键词

---

## 8. 高危操作审批流程

### 8.1 触发条件

当 ETL 工程师 Agent 生成的 SQL 包含以下操作时，
系统**自动暂停执行并弹出审批弹窗**（在工具调用执行前）：

| 高危操作 | 示例 SQL | 风险 |
|---------|---------|------|
| `DROP TABLE/DATABASE` | `DROP TABLE old_events` | 不可恢复删除 |
| `TRUNCATE TABLE` | `TRUNCATE TABLE dim_users` | 清空全表数据 |
| `DELETE FROM` | `DELETE FROM orders WHERE dt < '2024-01-01'` | 批量删除行 |
| `ALTER TABLE...DROP/MODIFY/RENAME` | `ALTER TABLE t DROP COLUMN id` | 结构变更 |
| `OPTIMIZE TABLE` | `OPTIMIZE TABLE t FINAL` | 可能长时间锁表 |

### 8.2 审批弹窗交互

弹窗内容：

```
┌─────────────────────────────────────────────────────┐
│  ⚠️  高危操作需要确认                                │
├─────────────────────────────────────────────────────┤
│  以下 SQL 操作属于高危操作，执行后可能无法恢复数据。   │
│                                                     │
│  调用工具: [clickhouse_idn__execute]                 │
│  ⚠️ 检测到高危操作: `DROP TABLE`                    │
│                                                     │
│  SQL 内容:                                          │
│  ┌───────────────────────────────────────────────┐  │
│  │ DROP TABLE old_events_2023                    │  │
│  └───────────────────────────────────────────────┘  │
│                                                     │
│  剩余确认时间: 47 秒                                  │
│  ██████████████████░░░░░░░░░░░  78%                 │
│                                                     │
│           [ 拒绝操作 ]   [ ✅ 同意执行 ]              │
└─────────────────────────────────────────────────────┘
```

**关键行为说明**：

| 行为 | 结果 |
|------|------|
| 点击「同意执行」 | Agent 继续调用 MCP 工具执行该 SQL |
| 点击「拒绝操作」 | Agent 中止本次工具调用，回复操作中止信息 |
| 60 秒内未操作 | 自动视为拒绝，Agent 中止操作 |
| 关闭弹窗（×）| 等同于拒绝 |

> **重要**：Agent **真正暂停**在此等待，不是"执行后再警告"。
> 在您做出决定之前，数据库操作不会执行。

### 8.3 API 审批（适用于自动化场景）

```bash
# 查看待审批列表
GET /api/v1/approvals/

# 程序化批准（如：CI/CD 流水线中的自动化审批）
POST /api/v1/approvals/{approval_id}/approve

# 程序化拒绝（带拒绝原因）
POST /api/v1/approvals/{approval_id}/reject
Content-Type: application/json
{"reason": "不在维护窗口期内，禁止执行"}

# 查询某次审批的详情和当前状态
GET /api/v1/approvals/{approval_id}
```

---

## 9. 多模型支持与对话内切换

### 9.1 支持的模型

系统通过 `LLM Configs` 管理模型配置（管理员在后台配置 API Key 后启用）。前端聊天框左下角的下拉菜单仅显示**已启用**的模型。

典型可用模型组合：

| model_key | 说明 |
|-----------|------|
| `claude` | Anthropic Claude（默认，推荐）|
| `doubao` | 字节跳动豆包 |
| `qianwen` | 阿里通义千问 |
| `openai` | OpenAI GPT 系列 |
| `gemini` | Google Gemini（骨架已有，需 API Key）|

---

### 9.2 切换模型：前端操作

聊天框左下角有 **模型选择器（ModelSelector）**，列出当前所有启用模型：

```
[ 🤖 Claude Sonnet 4.6  ▼ ]   [  发送  ]
```

- **直接选择**：下拉选择其他模型，下一条消息起生效
- **选择即时生效**：同一对话内可随时切换，之前的消息历史完整保留
- **跨会话记忆**：所选模型存入 `localStorage`，刷新或重新打开页面后自动恢复

> **无需新建对话**：切换模型不会清空历史，同一对话可以一半用 Claude、一半换成豆包继续。

---

### 9.3 API 报错时的处理机制

系统具备两级容错：

#### 第一级：自动故障转移（Claude 模型族内）

Claude 适配器内置多模型故障转移。若主模型 API 调用失败，自动按 `.env` 中配置的备用列表依次尝试，对用户和 Agent 完全透明：

```ini
# .env 配置示例
ANTHROPIC_DEFAULT_MODEL=claude-sonnet-4-6
ANTHROPIC_FALLBACK_MODELS=claude-sonnet-4-6,claude-sonnet-4-5-20250929,claude-haiku-4-5-20251001
ANTHROPIC_ENABLE_FALLBACK=true
```

- 主模型失败 → 自动尝试第二个 → 第三个 → …
- 后端日志记录实际使用的模型（`used_fallback: true`）
- 全部备用模型也失败才报错给前端

#### 第二级：手动切换跨供应商（Claude → 豆包 等）

若 Claude API 整体不可用，**在当前对话内直接切换到其他供应商模型**，无需新建对话：

1. 在模型选择器下拉菜单中选择可用模型（如 `doubao`）
2. 正常输入消息发送
3. 后端用新模型处理请求，历史上下文完整传递

---

### 9.4 切换模型时的 Context 管理

#### 上下文如何传递

每次发送消息，`conversation_service._build_context()` 从 PostgreSQL 加载完整对话历史，格式为 `[{role, content}, ...]`。该格式与模型无关，每个适配器会将其转换为目标 API 的原生格式：

| 适配器 | 转换目标格式 |
|--------|-----------|
| Claude | Anthropic Messages API（`user`/`assistant`） |
| 豆包 | Doubao Chat API（OpenAI 兼容格式）|
| 通义千问 | Qianwen API（`user`/`assistant`）|

切换模型后，新模型接收到的是**完整历史文本**（所有过去消息的 content 字段），能够理解上下文并继续作答。

#### 上下文压缩

若对话历史超过阈值（`MAX_CONTEXT_MESSAGES=30`），`_maybe_summarize()` 会自动调用 **当前模型** 生成摘要，注入到 context 头部：

```
[摘要注入] 本对话从之前的对话继续，历史摘要如下：
## 对话摘要
用户目标：...
已完成的操作：...
关键发现：...
当前状态：...

[最近 10 条消息照原样保留]
```

这意味着即使切换了模型，后续模型同样能获得结构化的历史摘要，不会因为模型切换而丢失上下文。

#### 注意事项

| 场景 | 行为 |
|------|------|
| 同一对话切换模型 | 历史消息完整传递，上下文不丢失 |
| 历史消息由 Claude 工具调用产生 | tool_result 内容序列化为文本传给新模型 |
| 历史消息非常长（超过新模型 context window）| `SmartCompressionStrategy` 自动压缩，保留最近消息 |
| 多模型混用后需要精确回溯 | `GET /api/v1/conversations/{id}/messages` 中每条消息的 `model` 字段记录了生成该消息实际使用的模型名 |

---

### 9.5 查看每条消息使用的模型

每条助手消息都记录了实际使用的模型：

```bash
GET /api/v1/conversations/{conv_id}/messages
```

响应中每条助手消息包含：

```json
{
  "role": "assistant",
  "content": "...",
  "model": "claude-sonnet-4-6",
  "model_params": {
    "attempted_models": ["claude-sonnet-4-6"],
    "used_fallback": false
  }
}
```

若触发了自动故障转移：

```json
{
  "model": "claude-haiku-4-5-20251001",
  "model_params": {
    "attempted_models": ["claude-sonnet-4-6", "claude-haiku-4-5-20251001"],
    "used_fallback": true
  }
}
```

---

## 10. 用户认证与多用户管理（RBAC）

系统内置 **JWT + RBAC（基于角色的访问控制）**，支持单用户兼容模式（`ENABLE_AUTH=false`，默认）和多用户模式（`ENABLE_AUTH=true`）两种运行模式。

---

### 10.1 认证模式

#### 单用户模式（默认，`ENABLE_AUTH=false`）

```ini
# .env
ENABLE_AUTH=false   # 默认值，无需修改即可使用
```

- 所有请求以内置 **AnonymousUser**（`is_superadmin=True`）身份运行
- 无需登录，无需 Token，与旧版行为完全兼容
- 技能文件统一写入 `.claude/skills/user/`（扁平目录）

#### 多用户模式（`ENABLE_AUTH=true`）

```ini
# .env
ENABLE_AUTH=true
JWT_SECRET=your-strong-secret-key-here   # 建议 ≥32 位随机字符串
JWT_ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=120          # access_token 有效期（分钟），须 ≤ SESSION_IDLE_TIMEOUT_MINUTES
REFRESH_TOKEN_EXPIRE_DAYS=14             # refresh_token DB 记录保留时长（天），默认 14 天
SESSION_IDLE_TIMEOUT_MINUTES=120         # Session 空闲超时（分钟），默认 120 分钟
```

- 所有 API 需携带有效 Bearer JWT Token
- 技能文件按用户隔离：`.claude/skills/user/{username}/`
- 权限按角色分配，superadmin 用户绕过所有权限检查

---

### 10.2 初始化 RBAC 数据

首次启动多用户模式前，需运行初始化脚本，将 4 个预置角色和 13 个权限写入数据库：

```bash
python backend/scripts/init_rbac.py
```

脚本幂等，重复运行不会报错。

**预置角色与权限**：

| 角色 | 权限范围 | 适用对象 |
|------|---------|---------|
| `viewer` | `chat:use` | 只读访问，仅可对话 |
| `analyst` | chat + skills.user:读写 + skills.project/system:读 | 数据分析师 |
| `admin` | analyst 全部 + skills.project:写 + models:读 + settings:读 + settings:写 | 系统管理员（**无** users:* 权限）|
| `superadmin` | 全部13项权限（含 users:读写/角色分配）| 超级管理员 |

> **注意**：`admin` 角色无法管理用户（无 `users:read/write/assign_role`）。用户管理功能仅对 `is_superadmin=True` 的用户开放。

---

### 10.3 登录与 Token 管理

#### 登录（获取 Access Token + Refresh Token）

```bash
POST /api/v1/auth/login
Content-Type: application/json

{
  "username": "alice",
  "password": "YourPassword123!"
}
```

响应示例：

```json
{
  "access_token": "eyJhbGc...",
  "token_type": "bearer",
  "expires_in": 7200
}
```

同时响应头会设置 httpOnly Cookie：`Set-Cookie: refresh_token=<jti>; HttpOnly; SameSite=Lax`

#### 在请求中使用 Token

```bash
# 所有需要认证的 API 均在 Authorization 头携带 Bearer Token
GET /api/v1/auth/me
Authorization: Bearer eyJhbGc...
```

#### 刷新 Token（无感续期）

access_token 过期后，使用 refresh_token Cookie 静默换取新 Token：

```bash
POST /api/v1/auth/refresh
# Cookie: refresh_token=<jti>（浏览器自动携带）
```

响应返回新的 `access_token`，同时旧 refresh_token 自动作废（**轮换机制**），防止令牌重放攻击。

#### 登出

```bash
POST /api/v1/auth/logout
# Cookie: refresh_token=<jti>

# 响应：{"message": "已登出"}
# 同时清除 refresh_token Cookie，数据库中该 token 标记为 revoked
```

#### 前端认证初始化行为

浏览器首次打开应用时，前端自动执行 `initAuth()` 检测认证状态，分四条路径：

| 路径 | 触发条件 | 结果 |
|------|---------|------|
| 路径 1 | 内存中已有 access_token | 调用 `/auth/me` 验证，成功则恢复用户状态 |
| 路径 2 | 无 token，但浏览器有 refresh Cookie | `POST /auth/refresh` 静默换取新 token |
| 路径 3 | 无 token 无 Cookie | 无 Token 调用 `/auth/me`，返回匿名用户则说明 `ENABLE_AUTH=false`，无需登录 |
| 路径 4 | 路径 3 返回 401 或网络错误 | **失败安全**：无法确认时一律要求登录 |

> **注意**：检测期间页面显示 `初始化中...` 加载动画，防止在认证结果确认前错误跳转到 `/login`。
> 网络错误（如代理失败）与 401 错误同等处理，一律重定向到登录页。

#### 查看当前用户信息

```bash
GET /api/v1/auth/me
Authorization: Bearer <token>

# 响应示例
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "username": "alice",
  "display_name": "Alice Chen",
  "is_superadmin": false,
  "roles": ["analyst"],
  "permissions": ["chat:use", "skills.user:read", "skills.user:write", "skills.project:read", "skills.system:read"],
  "last_login_at": "2026-03-17T10:30:00"
}
```

---

### 10.4 用户管理（superadmin 权限）

以下操作需要 `users:write` 或 `users:read` 权限（仅 `is_superadmin=True` 的用户默认拥有）。

#### 创建用户

```bash
POST /api/v1/users
Authorization: Bearer <superadmin-token>

{
  "username": "bob",
  "password": "BobPass456!",
  "display_name": "Bob Liu",
  "email": "bob@example.com",
  "role_names": ["analyst"]        # 初始角色列表，可为空
}
```

响应返回 `UserOut`（201 Created），包含 `id`、`username`、`roles` 等字段。

#### 查看用户列表

```bash
GET /api/v1/users?page=1&page_size=20
Authorization: Bearer <superadmin-token>
```

#### 查看用户详情

```bash
# 任意用户可查看自己；superadmin 可查看任何人
GET /api/v1/users/{user_id}
Authorization: Bearer <token>
```

#### 修改用户信息

```bash
# 用户可修改自己的 display_name；superadmin 可修改任意用户（含 is_active）
PUT /api/v1/users/{user_id}
Authorization: Bearer <token>

{
  "display_name": "Alice (Senior)",
  "is_active": true
}
```

#### 修改密码

```bash
# 仅本人操作，需提供旧密码
PUT /api/v1/users/{user_id}/password
Authorization: Bearer <token>

{
  "old_password": "OldPass123!",
  "new_password": "NewPass789!"
}
```

#### 分配 / 撤销角色

```bash
# 分配角色（需 users:assign_role 权限，仅 superadmin）
POST /api/v1/users/{user_id}/roles
Authorization: Bearer <superadmin-token>
{"role_name": "admin"}

# 撤销角色
DELETE /api/v1/users/{user_id}/roles/{role_id}
Authorization: Bearer <superadmin-token>
```

> **重要**：通过 API 分配 `superadmin` 角色**不会**设置 `is_superadmin=True` 字段。`is_superadmin` 标志只能直接修改数据库或通过 `init_rbac.py` 脚本设置，防止权限提升漏洞。

#### 查看角色列表

```bash
GET /api/v1/roles
Authorization: Bearer <superadmin-token>

# 响应：包含所有角色名称及其关联权限列表
```

---

### 10.5 角色权限管理（前端 + API）

#### 前端页面（推荐）

```
导航栏 → 角色权限
```

**页面功能说明**：

- **角色卡片列表**：每个角色以卡片形式展示，显示角色名、描述和已授权限（彩色 Tag）
- **系统预置角色**：`viewer / analyst / admin / superadmin` 显示锁形图标，**不可删除**
- **自定义角色**：管理员可新建、删除（需 `users:write` 权限）
- **权限分配弹窗**：点击角色卡片右上角「权限」按钮，弹窗内按资源（resource）分组展示全部 13 项权限，勾选 Checkbox 即授权，取消即撤权（需 `users:assign_role` 权限）

#### 创建自定义角色

```bash
POST /api/v1/roles
Authorization: Bearer <superadmin-token>
Content-Type: application/json

{
  "name": "data_viewer",
  "description": "只读数据访问，可查询 ClickHouse 但不可写入"
}
```

响应返回 `RoleOut`（201 Created），包含 `id`、`name`、`is_system`、`permissions` 字段。

#### 删除自定义角色

```bash
DELETE /api/v1/roles/{role_id}
Authorization: Bearer <superadmin-token>

# 系统预置角色（is_system=True）无法删除，返回 403
# 响应：204 No Content
```

#### 为角色分配 / 移除权限

```bash
# 分配权限（需 users:assign_role 权限，仅 superadmin）
POST /api/v1/roles/{role_id}/permissions
Authorization: Bearer <superadmin-token>

{"permission_id": "perm-uuid-here"}

# 移除权限
DELETE /api/v1/roles/{role_id}/permissions/{perm_id}
Authorization: Bearer <superadmin-token>
```

两个接口均返回更新后的完整 `RoleOut`，含最新的 `permissions` 列表。

#### 查看所有权限定义

```bash
GET /api/v1/permissions
Authorization: Bearer <superadmin-token>

# 响应：15 条权限定义列表，每条含 id / resource / action / description
```

完整权限键列表：

| 权限键 | 说明 |
|--------|------|
| `chat:use` | 使用聊天功能 |
| `skills.user:read` | 查看用户技能 |
| `skills.user:write` | 创建/编辑/删除用户技能 |
| `skills.project:read` | 查看项目技能 |
| `skills.project:write` | 管理项目技能（需 Admin Token）|
| `skills.system:read` | 查看系统技能 |
| `models:read` | 查看模型配置 |
| `models:write` | 新增/修改/删除 LLM Config |
| `settings:read` | 查看 MCP 服务器列表/详情/统计（`GET /mcp/*`）|
| `settings:write` | 调用 MCP 工具、测试连接（`POST /mcp/*`）|
| `users:read` | 查看用户列表及角色权限页面 |
| `users:write` | 创建/修改/删除用户和角色 |
| `users:assign_role` | 为用户和角色分配/撤销权限 |
| `data:import` | Excel 数据导入到 ClickHouse（superadmin 专属）|
| `data:export` | SQL 查询结果导出为 Excel（superadmin 专属）|

---

### 10.7 多用户技能隔离

启用 `ENABLE_AUTH=true` 后，每个用户的 Tier 3（用户技能）写入独立目录：

| 模式 | 技能目录 |
|------|---------|
| `ENABLE_AUTH=false` | `.claude/skills/user/` （所有人共享） |
| `ENABLE_AUTH=true`，用户 alice | `.claude/skills/user/alice/` |
| `ENABLE_AUTH=true`，用户 bob | `.claude/skills/user/bob/` |

**隔离效果**：
- Alice 创建的技能**不会**出现在 Bob 的技能列表中
- Bob 无法删除 Alice 的技能（返回 404，而非 403）
- Tier 1（system）和 Tier 2（project）技能对所有用户可见

**CURRENT_USER 自动注入（对话式创建技能）**：

系统在每次推理时将当前登录用户名注入 Agent 的系统提示：

```
CURRENT_USER: alice（技能文件等用户专属目录请在路径中包含此用户名）
```

当您通过聊天对话创建技能时，`skill-creator` 元技能会读取 `CURRENT_USER` 并自动将文件写入正确路径（如 `.claude/skills/user/alice/my-skill.md`），无需手动填写路径。

---

### 10.8 常见安全注意事项

| 场景 | 系统行为 |
|------|---------|
| access_token 过期 | 返回 401，前端自动调用 /auth/refresh；若 Cookie 存在则静默续期，否则跳转登录页 |
| 使用已轮换的旧 refresh_token | 返回 401（令牌已 revoked） |
| 用户账号被停用（`is_active=false`）| 其现有 token 立即失效，401 |
| 用错误密钥签名的 token | 401（签名验证失败） |
| 篡改 token payload | 401（签名不匹配） |
| 路径穿越攻击（`../` 技能名）| `_slugify()` 清除路径分隔符，`path-boundary` 边界检查二次防护 |
| Session 空闲超时（超过 SESSION_IDLE_TIMEOUT_MINUTES）| /auth/refresh 返回 401 "会话已超时"，refresh_token 立即 revoked |
| 关闭浏览器后重新打开 | refresh_token Cookie 为 Session Cookie，浏览器关闭时清除，重开需重新登录 |

---

### 10.9 Session 过期行为说明

#### 空闲超时（`SESSION_IDLE_TIMEOUT_MINUTES`，默认 120 分钟）

系统通过以下机制实现空闲超时：

1. **活跃追踪**：每次认证 API 请求都会节流更新 `users.last_active_at`（每 5 分钟最多写一次 DB，后台任务不阻塞请求）
2. **超时检测**：access_token 过期（120 分钟 TTL）→ 前端自动调用 `/auth/refresh` → 后端检测 `now - last_active_at > 120min` → 若超时则 401 并立即吊销 refresh_token
3. **前端响应**：`useAuthStore` 捕获 401 → `set({user:null})` → React Router 跳转 `/login`

```
用户 2 小时未操作
  → access_token 过期（120min TTL）
  → 前端发起 /auth/refresh（Cookie 自动携带）
  → 后端：now - last_active_at = 130min > 120min → 401 "会话已超时"
  → 前端：清空用户状态 → 跳转登录页
```

> **活跃用户不受影响**：只要 2 小时内有任意 API 请求，`last_active_at` 就会更新，refresh 检测通过，session 自动续期。

#### 浏览器关闭自动登出

refresh_token Cookie 设置为 **Session Cookie**（不含 `max_age`/`expires`）：

- 浏览器关闭 → Cookie 自动清除 → 重开时无 Cookie → 路径 2（`/auth/refresh`）失败 → 路径 3/4 → 跳转登录页
- 浏览器刷新或切换标签页 **不触发** 登出（Session Cookie 在浏览器进程存活期间有效）

#### 调整配置

```ini
# .env — 将空闲超时延长到 4 小时
ACCESS_TOKEN_EXPIRE_MINUTES=240     # 须与 SESSION_IDLE_TIMEOUT_MINUTES 保持一致
SESSION_IDLE_TIMEOUT_MINUTES=240

# 禁用空闲检测（不推荐）
# 将 SESSION_IDLE_TIMEOUT_MINUTES 设为极大值，如 999999
# 浏览器关闭登出行为无法通过配置禁用（Session Cookie 机制固有特性）
```

> **关键约束**：`ACCESS_TOKEN_EXPIRE_MINUTES` **必须** ≤ `SESSION_IDLE_TIMEOUT_MINUTES`。
> 若 access_token TTL 大于空闲超时，用户可在超时后仍凭有效 token 访问，空闲检测永远不触发。

---

### 10.10 对话用户隔离（2026-03-24）

启用 `ENABLE_AUTH=true` 后，对话和分组数据按用户严格隔离：

#### 普通用户（viewer / analyst / admin）

- 侧边栏**只显示自己创建的对话**，无法看到其他用户的对话。
- 分组（文件夹）同样按用户隔离，不同用户可以有相同名称的分组，互不干扰。
- 尝试通过 API 访问他人的对话 → 返回 **403 Forbidden**。

#### superadmin

- 侧边栏顶部出现**双 Tab 切换**：「我的对话」和「其他用户(N)」（N 为其他用户的对话总数）：
  - **「我的对话」Tab**：与普通用户完全一致，可新建/管理自己的对话。
  - **「其他用户」Tab**：按用户分组展示他人对话（**只读**，无新建/重命名/删除等操作按钮）。
  - 点击他人对话可在主聊天区打开查看消息历史。
- 打开他人对话时，聊天输入框会替换为黄色横幅提示：**「👁 仅查看模式 — 当前对话属于其他用户」**，无法发送消息或重新生成回复。
- 自己的对话显示在「我的对话」Tab（正常管理），与他人对话完全分开。

#### 迁移存量数据

如果在 `ENABLE_AUTH=true` 之前已有对话记录，需执行一次性迁移脚本，将历史数据归属 superadmin：

```bash
python backend/scripts/migrate_conversation_user_isolation.py
```

脚本支持 `--dry-run` 预览，幂等，可安全重复执行。

#### ENABLE_AUTH=false 时的行为

- 所有对话/分组的 `user_id` 为 `NULL`，列表端点不按用户过滤（全部可见）。
- 与升级前完全兼容，无功能变化。

---

## 11. 快速参考卡

### 意图路由速查

| 消息包含 | 路由到 | 安全策略 |
|---------|--------|---------|
| etl / 宽表 / 建表 / 增量 / pipeline… | **ETL 工程师** | 高危 SQL 需人工审批 |
| 分析 / 留存 / 漏斗 / 趋势 / DAU… | **数据分析师** | 只读模式，写操作自动拦截 |
| 其他所有内容 | **通用（不调 MCP）** | 无 MCP 权限 |

### 功能入口速查

| 我想做的事 | 操作路径 |
|-----------|---------|
| 提交一个长时间数据处理任务 | 导航栏 → **Tasks** → 填写 → 提交 |
| 查看任务进度 | 导航栏 → **Tasks** → 点击详情 |
| 中断正在进行的 AI 生成 | 发送消息后 → 输入框上方**「停止生成」**红色按钮（见 5.6 节）|
| 发送附件（图片/PDF/文件） | 输入框右侧**「📎 回形针」**按钮或粘贴图片（见 5.7 节）|
| 下载 Agent 生成的文件（CSV/Excel/JSON 等）| 助手消息末尾 → **「📎 生成的文件」卡片** → 点击「下载」按钮（见 5.9 节）|
| 将 Excel 文件批量导入 ClickHouse | 侧边栏 → **「数据导入」**（仅 superadmin；见第 12 节）|
| 将 SQL 查询结果导出为 Excel | 侧边栏 → **「数据导出」**（仅 superadmin；见第 13 节）|
| 生成多图表交互式 HTML 报告 | 聊天框提及「图表报告」，或侧边栏 → **「图表报告」**（需 `reports:create` 权限；见第 14 节）|
| 导出报告为 PDF / PPTX | 报告列表 → 点击「导出 PDF」/「导出 PPTX」（异步任务，轮询后下载；见 14.6 节）|
| 查看本次回答调了哪些工具 | 助手消息上方 → 点击**「推理过程」** 折叠面板 |
| 刷新页面后查看历史推理过程 | 推理过程已持久化，刷新后仍可展开（见 5.4 节）|
| 了解 Agent 自动续接了几次 | 消息列表中查看 🔄 续接提示横幅（`[N/3]`）|
| 查看用了哪个 Agent | 推理面板首条事件 / 任务列表 Agent Type 列 |
| 切换大语言模型 | 聊天框左下角模型选择器 → 下拉选择 → 下条消息起生效 |
| API 报错，切换到备用模型继续对话 | 直接在选择器切换，**无需新建对话** |
| 查看某条消息实际用了哪个模型 | `GET /api/v1/conversations/{id}/messages` → `model` 字段 |
| 创建自定义 AI 行为技能 | 聊天框输入「**创建技能：**[描述]」 |
| 查看 / 删除我的自定义技能 | `GET/DELETE /api/v1/skills/user-defined` |
| 审批高危 SQL 操作 | 自动弹出审批弹窗 → 同意 / 拒绝 |
| 程序化审批 | `POST /api/v1/approvals/{id}/approve` |
| 查看系统整体健康状态 | `GET /api/v1/agents/health` |
| 登录（多用户模式） | `POST /api/v1/auth/login` |
| 查看当前登录用户信息 | `GET /api/v1/auth/me` |
| 刷新 Token | `POST /api/v1/auth/refresh`（Cookie 自动携带）|
| 创建新用户（需 superadmin） | `POST /api/v1/users` |
| 分配用户角色（需 superadmin） | `POST /api/v1/users/{id}/roles` |
| 修改自己的密码 | `PUT /api/v1/users/{id}/password`（需旧密码）|
| 查看/管理角色（前端）| 导航栏 → **角色权限**（需 `users:read`，仅 superadmin 可见）|
| 新建自定义角色 | 角色权限页 → **新建角色** 按钮，或 `POST /api/v1/roles` |
| 为角色分配/撤销权限 | 角色权限页 → 点击角色卡片「权限」按钮 → Checkbox 勾选/取消 |
| 查看所有权限定义 | `GET /api/v1/permissions`（需 superadmin）|
| 新增 ClickHouse 区域 | 在 `.env` 追加 `CLICKHOUSE_{ENV}_*` 后重启，无需改代码 |
| 查看已注册 MCP 服务器（需 admin+） | `GET /api/v1/mcp/servers` |
| 查看 MCP 统计（需 admin+） | `GET /api/v1/mcp/stats` |

### 常用 API 一览

```bash
# 任务管理
POST   /api/v1/agents/tasks                      # 提交任务
GET    /api/v1/agents/tasks/{id}/status          # 查询进度
DELETE /api/v1/agents/tasks/{id}                 # 取消任务
POST   /api/v1/agents/tasks/{id}/retry           # 重试失败任务

# Agent 管理
GET    /api/v1/agents                            # 所有 Agent 列表
GET    /api/v1/agents/{id}/tasks                 # 某 Agent 任务历史
GET    /api/v1/agents/{id}/metrics               # 完成/失败统计
GET    /api/v1/agents/routing/suggestions?query= # 路由建议

# MCP 服务器管理（需 admin+ 权限，即 settings:read / settings:write）
GET    /api/v1/mcp/servers                       # 已注册 MCP 服务器列表（含动态发现的 clickhouse-thai 等）
GET    /api/v1/mcp/servers/{name}                # 服务器详情 + 工具列表
GET    /api/v1/mcp/servers/{name}/tools          # 可用工具列表
GET    /api/v1/mcp/stats                         # 汇总统计（服务器数、工具数）
POST   /api/v1/mcp/servers/{name}/tools/{tool}   # 直接调用工具（调试用，需 settings:write）

# Skills 管理
GET    /api/v1/skills                            # 所有技能
POST   /api/v1/skills/user-defined               # 创建自定义技能
GET    /api/v1/skills/user-defined               # 用户技能列表
DELETE /api/v1/skills/user-defined/{name}        # 删除用户技能

# 审批管理
GET    /api/v1/approvals/                        # 待审批列表
GET    /api/v1/approvals/{id}                    # 审批详情
POST   /api/v1/approvals/{id}/approve            # 批准
POST   /api/v1/approvals/{id}/reject             # 拒绝

# 对话管理
GET    /api/v1/conversations                     # 对话列表
GET    /api/v1/conversations/{id}/messages       # 消息历史（含 agent_type）
POST   /api/v1/conversations/{id}/cancel         # 停止正在进行的生成（幂等）

# 认证（ENABLE_AUTH=true 时生效）
POST   /api/v1/auth/login                        # 登录，返回 access_token + 设置 refresh_token Cookie
POST   /api/v1/auth/refresh                      # 用 refresh_token Cookie 换取新 access_token（轮换机制）
POST   /api/v1/auth/logout                       # 登出，revoke refresh_token
GET    /api/v1/auth/me                           # 当前用户信息（含 roles + permissions）

# 用户管理（需 users:* 权限，仅 superadmin）
POST   /api/v1/users                             # 创建用户（201 + UserOut）
GET    /api/v1/users                             # 用户列表（分页）
GET    /api/v1/users/{id}                        # 用户详情（本人或 superadmin）
PUT    /api/v1/users/{id}                        # 修改 display_name / is_active
PUT    /api/v1/users/{id}/password               # 修改密码（仅本人，需旧密码）
POST   /api/v1/users/{id}/roles                  # 分配角色（需 users:assign_role）
DELETE /api/v1/users/{id}/roles/{role_id}        # 撤销角色（需 users:assign_role）

# 角色管理（需对应权限）
GET    /api/v1/roles                             # 角色列表（含各角色权限详情，需 users:read）
POST   /api/v1/roles                             # 新建自定义角色（需 users:write）
DELETE /api/v1/roles/{id}                        # 删除自定义角色（系统角色返回 403，需 users:write）
POST   /api/v1/roles/{id}/permissions            # 为角色分配权限（需 users:assign_role）
DELETE /api/v1/roles/{id}/permissions/{perm_id}  # 移除角色权限（需 users:assign_role）

# 权限管理（需 users:read 权限）
GET    /api/v1/permissions                       # 全量权限定义列表（共 13 条）

# Excel 数据导入（需 data:import 权限，仅 superadmin）
GET    /api/v1/data-import/connections              # 可写 ClickHouse 连接列表
GET    /api/v1/data-import/connections/{env}/databases  # 数据库列表
GET    /api/v1/data-import/connections/{env}/databases/{db}/tables  # 表列表
POST   /api/v1/data-import/upload               # 上传 Excel（multipart，上限 100MB）
POST   /api/v1/data-import/execute              # 提交导入任务（后台执行）
GET    /api/v1/data-import/jobs/{job_id}        # 查询任务进度
GET    /api/v1/data-import/jobs                 # 历史任务列表（分页）
POST   /api/v1/data-import/jobs/{job_id}/cancel # 取消 pending/running 任务
DELETE /api/v1/data-import/jobs/{job_id}        # 删除任务记录

# 系统
GET    /health                                   # 系统健康检查
```

---

## 12. Excel → ClickHouse 数据导入（superadmin 专属）

> **权限要求**：仅 `superadmin` 用户可使用此功能（`data:import` 权限）。
>
> **入口**：侧边栏导航 → **「数据导入」**（`ExportOutlined` 图标）。`ENABLE_AUTH=false` 时（单用户模式），匿名用户默认为 superadmin，可直接使用。

### 12.1 功能概述

将本地 Excel（`.xlsx` / `.xls`）文件中的数据批量导入到 ClickHouse 指定表，无需编写 ETL SQL。支持：

- **多 Sheet** 一次性配置，各 Sheet 独立指定目标 database/table
- **大文件**：单次最大 100MB，采用流式上传（1MB 分块写盘）
- **实时进度**：行级 + 批次级双进度条，可随时取消
- **历史记录**：任务列表保存所有历史导入记录，可按需删除

### 12.2 操作步骤

#### 步骤一：选择连接 + 上传文件

1. 进入数据导入页面，页面顶部显示**步骤向导**。
2. 在「选择目标环境」下拉框选择目标 ClickHouse 连接（仅显示可写连接，只读连接不在列表中）。
3. 点击「上传 Excel 文件」区域（或将文件拖入），选择本地 `.xlsx`/`.xls` 文件。
4. 后端接收到文件后自动解析每个 Sheet 的名称、估算行数和前 5 行预览。
5. 上传成功后页面自动进入**步骤二**。

> **大文件提示**：60MB 文件上传约需 30–60 秒（取决于网络），页面会显示进度条，请耐心等待。

#### 步骤二：配置 Sheet 映射

上传完成后，页面显示所有 Sheet 的配置表格：

| 字段 | 说明 |
|------|------|
| Sheet 名称 | Excel 工作表名（只读） |
| 估算行数 | 来自 Excel 元数据（O(1) 读取，非全量扫描，大文件也即时显示） |
| 目标数据库 | 下拉选择；选中连接环境下的所有数据库 |
| 目标表 | 下拉选择；选中数据库下的所有表 |
| 含表头 | 勾选后第一行作为标题跳过，不导入 |
| 启用 | 取消勾选可跳过该 Sheet（不导入） |

配置完成后点击「**开始导入**」进入步骤三。

#### 步骤三：监控进度 + 取消/删除

页面显示当前导入任务的实时状态：

| 指标 | 说明 |
|------|------|
| 状态徽标 | `等待中` / `进行中` / `取消中` / `已取消` / `已完成` / `失败` |
| Sheet 进度 | 已完成 Sheet / 总 Sheet 数 |
| 批次进度 | 已完成批次 / 总批次数（每批 5000 行） |
| 导入行数 | 累计已写入 ClickHouse 的行数 |
| 当前 Sheet | 正在处理的工作表名 |
| 错误信息 | 仅失败时显示，包含批次号和错误原因 |

**操作按钮**：

- **取消**（仅进行中/等待中）：点击后状态变为`取消中`，后台协程完成当前批次后停止（最长延迟约 2 秒）。
- **删除**（仅终止状态：已完成/失败/已取消）：删除 DB 记录，不影响已写入 ClickHouse 的数据。

> **进度刷新**：页面自动每 2 秒轮询一次最新进度，无需手动刷新。

### 12.3 历史任务列表

历史任务按创建时间倒序显示，分页浏览。每条记录包含：文件名、目标环境、创建时间、最终状态和导入行数。

已完成/失败/已取消任务可点击**「删除」**清理记录。

### 12.4 注意事项与限制

| 项目 | 说明 |
|------|------|
| 文件大小上限 | **100 MB**（超出返回 413）|
| 文件格式 | 仅 `.xlsx` 和 `.xls`（其他格式前端拒绝上传）|
| 目标表须存在 | 系统不自动建表，目标表需提前在 ClickHouse 中创建 |
| 列数/类型匹配 | Excel 列顺序与 ClickHouse 表列顺序须一致；数据类型由 ClickHouse 负责转换 |
| 批大小 | 默认每批 5000 行（`batch_size`），最小 100，最大 50000；可在 `ExecuteImportRequest` 中调整 |
| 行数估算 | 预览阶段的「估算行数」取自 Excel 元数据（`ws.max_row`），部分 Excel 文件元数据不准确；实际导入行数以「导入行数」指标为准 |
| 取消时机 | 协作式取消：每批完成后检测，已写入的批次**不会回滚**。取消仅停止后续批次，已导入数据保留 |
| 临时文件 | 上传文件保存在 `customer_data/{username}/imports/`，任务完成（无论成功/失败）后自动清理 |
| 单进程限制 | 并发导入任务无限制，但大批量并发会占用 ClickHouse 写入连接，建议顺序提交 |

### 12.5 API 快速参考（程序化调用）

```bash
# 获取可写连接列表
GET /api/v1/data-import/connections
Authorization: Bearer <superadmin_token>

# 上传 Excel 文件
POST /api/v1/data-import/upload
Content-Type: multipart/form-data
→ 返回: {"upload_id": "...", "filename": "...", "sheets": [...]}

# 提交导入任务
POST /api/v1/data-import/execute
{"upload_id": "...", "connection_env": "sg", "batch_size": 5000,
 "sheets": [{"sheet_name": "Sheet1", "database": "crm", "table": "orders",
              "has_header": true, "enabled": true}]}
→ 返回: {"job_id": "...", "status": "pending"}

# 轮询进度
GET /api/v1/data-import/jobs/{job_id}

# 取消任务
POST /api/v1/data-import/jobs/{job_id}/cancel

# 删除记录
DELETE /api/v1/data-import/jobs/{job_id}
```

---

## 13. SQL → Excel 数据导出（superadmin 专属）

> **权限要求**：仅 `superadmin` 用户可使用此功能（`data:export` 权限）。
>
> **入口**：侧边栏导航 → **「数据导出」**（`ExportOutlined` 图标，位于「数据导入」旁边）。`ENABLE_AUTH=false` 时（单用户模式），匿名用户默认为 superadmin，可直接使用。

### 13.1 功能概述

在页面输入任意 SELECT SQL，选择 ClickHouse 连接，预览前 N 行结果后一键导出为本地 Excel（`.xlsx`）文件。支持：

- **流式导出**：服务端 HTTP 流式响应 + `openpyxl` write-only 模式，峰值内存仅为 `batch_size` × 行宽；不受大数据量影响
- **多 Sheet 自动分割**：每满 100 万数据行自动创建新 Sheet（Sheet1、Sheet2…），每个 Sheet 均带标题行
- **大整数安全**：`Int64`/`UInt64`/`Int128`/`UInt128`/`Int256`/`UInt256` 类型自动转为字符串，避免 Excel 打开时显示科学计数法
- **中文无乱码**：`.xlsx` 格式（zip+XML），openpyxl 原生 UTF-8，无需编码转换
- **任务取消**：导出中途可取消；取消后已写入部分保存为残余文件（可手动删除）
- **历史记录**：任务列表保存所有导出记录，终止状态下可删除

### 13.2 操作步骤

#### 步骤一：输入 SQL + 选择连接 + 预览

1. 进入数据导出页面，上方区域输入 SELECT SQL 语句（支持复杂查询，无需手动加 `LIMIT`）。
2. 在「选择连接」下拉框选择目标 ClickHouse 连接（仅显示可写连接，只读连接不在列表中）。
3. 点击「**查询**」按钮（或 `Ctrl+Enter`），后端执行 `SELECT * FROM (...) LIMIT N` 返回前 N 行预览数据。
4. 预览表格显示列名、数据类型和前 N 行内容（`NULL` 值高亮显示）。

> **预览限制**：默认返回前 100 行，可通过 `limit` 参数调整（最大 500）；预览不会触发导出任务。

#### 步骤二：导出配置 + 提交

1. 确认预览数据无误后，点击预览区域**右上角**的「**导出**」按钮。
2. 弹出配置对话框：

   | 字段 | 说明 |
   |------|------|
   | 任务名称 | 选填；用于文件名前缀（会自动净化特殊字符）；留空则使用 `export` |
   | 批大小 | 每批从 ClickHouse 拉取的行数（默认 50000，范围 1000–200000） |

3. 点击「**开始导出**」后：
   - 后端创建导出任务（`status=pending`），返回 `job_id`
   - 后台协程立即开始流式读取 + 写入 Excel

#### 步骤三：监控进度 + 下载 / 取消 / 删除

历史任务列表（页面下方）显示所有导出记录，每 2 秒自动轮询活跃任务状态：

| 指标 | 说明 |
|------|------|
| 状态徽标 | `等待中` / `进行中` / `取消中` / `已取消` / `已完成` / `失败` |
| 已导出行数 | 累计写入 Excel 的数据行数（不含标题行）|
| Sheet 数 | 总 Sheet 数（每 100 万行新增一张）|
| 文件大小 | 完成后显示 `.xlsx` 文件大小 |

**操作按钮**：

- **下载**（仅 `已完成`）：点击触发浏览器「另存为」对话框，下载本地 `.xlsx` 文件。
- **取消**（仅 `等待中`/`进行中`）：协作式取消；后台协程在下一批次检测到信号后停止；`等待中` 任务直接置为 `已取消`。
- **删除**（仅终止状态：`已完成`/`已取消`/`失败`）：删除 DB 记录和本地文件。

> **取消注意**：取消后已写入部分会保存为残余文件；点击「删除」可一并清除。

### 13.3 多 Sheet 自动分割说明

当导出行数超过 100 万（Excel 单 Sheet 硬上限约 104 万行）时，系统自动创建新工作表：

```
Sheet1: 标题行 + 第 1–1,000,000 行数据
Sheet2: 标题行 + 第 1,000,001–2,000,000 行数据
Sheet3: 标题行 + 第 2,000,001–... 行数据
```

每个 Sheet 均保留相同的标题行，可直接跨 Sheet 使用 Excel 筛选/排序。

### 13.4 大整数处理说明

ClickHouse 的 `Int64`/`UInt64` 及更大整数类型（`Int128`/`UInt128`/`Int256`/`UInt256`）在 Excel 中会被当作浮点数显示，导致末尾精度丢失或科学计数法。系统自动将上述类型转换为**字符串**写入 Excel 单元格，保留完整精度。

示例：`12345678901234567` → Excel 单元格文本值 `"12345678901234567"`（而非 `1.23457E+16`）。

### 13.5 注意事项与限制

| 项目 | 说明 |
|------|------|
| SQL 类型 | 仅支持 `SELECT` 查询；不执行 INSERT/UPDATE/DROP 等写操作 |
| 连接类型 | 当前支持 ClickHouse；架构预留 MySQL 等扩展点（`BaseExportClient` 抽象层）|
| 单 Sheet 行上限 | 系统限制 100 万数据行/Sheet，超出自动新建；Excel 实际单 Sheet 上限约 104 万行 |
| 并发导出 | 无系统级并发限制，但大量并发会占用 ClickHouse 连接，建议顺序提交 |
| 取消后文件 | 取消后已写入部分不会自动删除；需手动点「删除」清理 |
| 文件存储路径 | 服务端存储于 `customer_data/{username}/exports/`；下载完成后可通过「删除」清理 |
| 批大小 | 默认 50000 行/批；内存峰值约为 `batch_size × 行宽`；网络较慢时可适当降低 |

### 13.6 API 快速参考（程序化调用）

```bash
# 获取可写连接列表
GET /api/v1/data-export/connections
Authorization: Bearer <superadmin_token>

# SQL 预览（不触发导出任务）
POST /api/v1/data-export/preview
{"query_sql": "SELECT id, name FROM users", "connection_env": "sg", "limit": 100}
→ 返回: {"columns": [{"name":"id","type":"Int64"},...], "rows": [...], "row_count": N}

# 提交导出任务
POST /api/v1/data-export/execute
{"query_sql": "SELECT * FROM orders", "connection_env": "sg",
 "job_name": "orders_2026", "batch_size": 50000}
→ 返回: {"job_id": "...", "status": "pending", "output_filename": "orders_2026_20260407_120000.xlsx"}

# 轮询进度
GET /api/v1/data-export/jobs/{job_id}

# 历史任务列表
GET /api/v1/data-export/jobs?page=1&page_size=10

# 取消任务
POST /api/v1/data-export/jobs/{job_id}/cancel

# 下载文件
GET /api/v1/data-export/jobs/{job_id}/download

# 删除记录（仅终止状态）
DELETE /api/v1/data-export/jobs/{job_id}
```

---

## 14. 多图表 HTML 报告生成

> **权限要求**：
> - **生成报告**：需要 `reports:create` 权限（analyst 及以上角色）
> - **查看报告列表**：需要 `reports:read` 权限（analyst 及以上角色）
> - **删除报告**：需要 `reports:delete` 权限（admin 及以上角色）
>
> **入口**：侧边栏导航 → **「图表报告」**（`BarChartOutlined` 图标）。`ENABLE_AUTH=false` 时（单用户模式），可直接使用。

### 14.1 功能概述

通过聊天触发后端生成一个**自包含 HTML 页面**，内嵌 ECharts/AntV 图表、JSON 数据和客户端筛选器。后续可导出为 PDF 或 PPTX，并可由 LLM 自动生成报告摘要。

核心能力：

- **多图表布局**：每份报告可包含多张图表（折线图、柱状图、饼图、散点图、热力图等），使用 ECharts CDN 渲染
- **内嵌筛选器**：`date_range`（日期范围）、`select`（单选）、`multi_select`（多选）、`radio`（单选按钮）四种筛选控件，全部客户端运算，无需联网
- **数据可刷新**：每份报告附带 `refresh_token`，用于调用 `GET /api/v1/reports/{id}/refresh-data` 公开接口重新查询最新数据、不需要用户 JWT
- **预览弹窗**：在报告列表页点击「预览」可直接在 Modal 中渲染 HTML，无需下载
- **PDF / PPTX 导出**：后台 Playwright Chromium 截图 → PDF；python-pptx 图表截图 → PPTX 幻灯片（每张图表一页）
- **AI 摘要**：点击「生成摘要」，LLM 根据图表规格和数据生成结构化分析文字，存储后可复用

### 14.2 通过聊天触发报告生成

在聊天框中提及"图表"、"报告"、"可视化"等关键词，Agent 会自动识别并调用报告生成工具：

```
用户：请帮我生成上季度各地区销售额的多图表报告，
      包含折线图（月度趋势）和饼图（区域占比）。
```

Agent 返回报告 ID 和预览链接后，即可在「图表报告」页面查看。

也可以直接通过 API 提交报告规格：

```bash
POST /api/v1/reports/build
Authorization: Bearer <token>
Content-Type: application/json

{
  "spec": {
    "title": "销售季度报告",
    "description": "上季度各地区销售分析",
    "charts": [
      {
        "id": "line1",
        "type": "line",
        "title": "月度趋势",
        "x_field": "month",
        "y_fields": ["revenue"],
        "data": [{"month": "1月", "revenue": 120000}, ...]
      },
      {
        "id": "pie1",
        "type": "pie",
        "title": "区域占比",
        "name_field": "region",
        "value_field": "revenue",
        "data": [{"region": "华东", "revenue": 450000}, ...]
      }
    ],
    "filters": [
      {
        "id": "region_filter",
        "type": "select",
        "label": "选择地区",
        "field": "region",
        "options": ["华东", "华南", "华北"],
        "target_charts": ["pie1"]
      }
    ]
  }
}
```

### 14.3 报告列表页操作

进入侧边栏「图表报告」后，可见所有（自己生成的）报告列表。每行显示：报告标题、创建时间、状态标签、摘要生成状态。

| 操作 | 说明 |
|------|------|
| 预览 | 在 Modal 弹窗中渲染 HTML 报告，支持全屏 |
| 导出 PDF | 后台 Playwright 截图生成 PDF，轮询完成后自动下载 |
| 导出 PPTX | 后台截图每张图表生成幻灯片，轮询完成后自动下载 |
| 生成摘要 | LLM 分析图表数据，生成结构化中文摘要；已摘要的报告直接展示 |
| 刷新数据 | 使用 `refresh_token` 调后端重查数据，更新报告内嵌 JSON |
| 删除 | 仅 `reports:delete` 权限角色（admin+）可操作 |

### 14.4 筛选器说明

HTML 报告内嵌四种客户端筛选控件，无需服务器参与：

| 筛选类型 | `type` 值 | 效果 |
|---------|-----------|------|
| 日期范围选择 | `date_range` | 按日期字段过滤，支持起止日期双端输入 |
| 下拉单选 | `select` | 从预设选项中选一个值，过滤目标图表数据 |
| 多选复选框 | `multi_select` | 可勾选多个值，OR 逻辑过滤 |
| 单选按钮组 | `radio` | 互斥选项，切换时立即更新图表 |

筛选器通过 `target_charts` 字段指定作用范围，可以跨图表联动（一个筛选器控制多张图表）。

### 14.5 数据刷新机制

报告生成时，后端为每份报告生成一个 64 字符随机 `refresh_token` 存入数据库。客户端可用此令牌调用公开接口重新查询数据：

```bash
GET /api/v1/reports/{id}/refresh-data?token=<refresh_token>
# 无需 JWT；后端用 secrets.compare_digest 验证令牌
# 返回最新数据并更新报告内嵌 JSON
```

适用场景：将报告 HTML 分享给无账号的同事后，对方仍可点击页面内「刷新数据」按钮获取最新数据。

### 14.6 PDF / PPTX 导出说明

导出为异步任务，提交后轮询进度：

```bash
# 提交导出任务
POST /api/v1/reports/{id}/export
{"format": "pdf"}   # 或 "pptx"
→ 返回: {"job_id": "...", "status": "pending"}

# 轮询进度
GET /api/v1/reports/{id}/export-status?job_id=<job_id>
→ 返回: {"status": "running"|"completed"|"failed", "progress": 0.0~1.0}

# 下载（status=completed 后）
GET /api/v1/reports/{id}/export-download?job_id=<job_id>
```

**依赖要求**（服务器端）：
- PDF 导出：需安装 `playwright` + `playwright install chromium`
- PPTX 导出：需安装 `python-pptx` + `Pillow`（`pip install -r requirements.txt` 已包含）

### 14.7 AI 摘要功能

点击「生成摘要」后，后台向 LLM 发送以下内容并生成中文分析：
- 报告标题与描述
- 各图表类型、标题、轴字段定义
- 各图表前 50 行数据样本

摘要结果存入 `reports.llm_summary` 列，标记 `is_summarized=true`，后续再次点击直接读取缓存，不重复调用 LLM。

```bash
# 触发摘要生成（异步，立即返回）
POST /api/v1/reports/{id}/summarize

# 查询摘要状态
GET /api/v1/reports/{id}/summary-status
→ 返回: {"is_summarized": true, "llm_summary": "..."}
```

### 14.8 API 快速参考

```bash
# 生成报告
POST /api/v1/reports/build
Authorization: Bearer <token>
{"spec": {...}}
→ 返回: {"id": "report-uuid", "html_content": "<!DOCTYPE html>...", "refresh_token": "..."}

# 列出报告（分页）
GET /api/v1/reports?page=1&page_size=10
Authorization: Bearer <token>

# 获取报告详情
GET /api/v1/reports/{id}
Authorization: Bearer <token>

# 删除报告（需 reports:delete 权限）
DELETE /api/v1/reports/{id}
Authorization: Bearer <token>

# 提交导出任务
POST /api/v1/reports/{id}/export
Authorization: Bearer <token>
{"format": "pdf"}

# 轮询导出进度
GET /api/v1/reports/{id}/export-status?job_id=<job_id>
Authorization: Bearer <token>

# 触发摘要生成
POST /api/v1/reports/{id}/summarize
Authorization: Bearer <token>

# 查询摘要状态
GET /api/v1/reports/{id}/summary-status
Authorization: Bearer <token>

# 公开刷新数据（无需 JWT）
GET /api/v1/reports/{id}/refresh-data?token=<refresh_token>
```

---

## ClickHouse 双权限连接与 Agent 绑定

### 概述

每个 ClickHouse 环境（IDN / SG / MX）支持两套独立的数据库连接凭据：

| 连接类型 | 服务器名格式 | 权限 | 适用角色 |
|----------|------------|------|---------|
| Admin    | `clickhouse-{env}` | 完整（DDL / DML） | ETL 工程师 |
| ReadOnly | `clickhouse-{env}-ro` | 仅 SELECT | 数据分析师、通用助手 |

### 步骤一：在 `.env` 中配置连接凭据

**Admin 连接**（现有字段，无需更改）：
```ini
CLICKHOUSE_IDN_HOST=ch-idn.example.com
CLICKHOUSE_IDN_PORT=9000
CLICKHOUSE_IDN_HTTP_PORT=8123
CLICKHOUSE_IDN_DATABASE=default
CLICKHOUSE_IDN_USER=admin_user
CLICKHOUSE_IDN_PASSWORD=admin_password
```

**ReadOnly 连接**（新增字段，同一环境可指向独立只读副本）：
```ini
# IDN ReadOnly 凭据
# HOST/PORT/DATABASE 留空时自动继承上方 Admin 的值（读副本同 host 时无需填写）
CLICKHOUSE_IDN_READONLY_HOST=ch-idn-replica.example.com   # 可留空
CLICKHOUSE_IDN_READONLY_PORT=                              # 可留空=继承 IDN_PORT
CLICKHOUSE_IDN_READONLY_DATABASE=                         # 可留空=继承 IDN_DATABASE
CLICKHOUSE_IDN_READONLY_USER=ro_user                      # ★ 填写此项才会创建 ro 服务器
CLICKHOUSE_IDN_READONLY_PASSWORD=ro_password
```

> **关键规则**：`CLICKHOUSE_{ENV}_READONLY_USER` 非空时，系统启动后会自动注册
> `clickhouse-{env}-ro` 服务器。未填写则不会创建只读服务器，析构 Agent 默认降级使用 Admin 连接（并输出 WARNING 日志）。

SG / MX 环境同理，字段前缀分别为 `CLICKHOUSE_SG_READONLY_*` / `CLICKHOUSE_MX_READONLY_*`。

### 步骤二：在 `.claude/agent_config.yaml` 中绑定 Agent 权限

```yaml
version: "1.0"

agents:

  # 数据加工工程师：需要高权限执行 DDL/DML
  etl_engineer:
    clickhouse_connection: admin      # 使用 clickhouse-idn（Admin 连接）
    clickhouse_envs:
      - idn

  # 数据分析师：只读约束，从 DB 层面防止误写
  analyst:
    clickhouse_connection: readonly   # 使用 clickhouse-idn-ro（ReadOnly 连接）
    clickhouse_envs:
      - idn

  # 通用助手：默认只读
  general:
    clickhouse_connection: readonly
    clickhouse_envs:
      - idn
```

**字段说明**：

| 字段 | 可选值 | 说明 |
|------|--------|------|
| `clickhouse_connection` | `admin` \| `readonly` | 该 Agent 使用哪级 ClickHouse 连接 |
| `clickhouse_envs` | `idn` \| `sg` \| `mx` 列表，或 `all` | 该 Agent 可访问的 ClickHouse 环境，`all` 自动包含所有已注册环境 |
| `max_iterations` | 整数 | 单次对话最多推理轮次（默认：etl=15，analyst=30，general=20） |

> `filesystem` 服务器对所有 Agent 可访问，但写操作受 **FilesystemPermissionProxy** 目录级白名单控制（见下文）。

### 权限绑定的工作原理

系统启动时 `AgentMCPBinder` 读取 `agent_config.yaml`，每次路由到某 Agent 时：

```
用户请求
  ↓ 路由
AgentOrchestrator._build_agent("analyst")
  ↓ AgentMCPBinder.get_filtered_manager("analyst", full_mcp_manager)
    → allowed = {clickhouse-idn-ro, filesystem, ...}
  ↓ FilteredMCPManager(base=full_mcp_manager, allowed=allowed)
    → .servers 只暴露 clickhouse-idn-ro
    → .call_tool("clickhouse-idn", ...) → 拒绝（非白名单）
  ↓ FilesystemPermissionProxy(base=FilteredMCPManager)       ← 自动叠加
    → write_file/create_directory/delete 路径在白名单外 → 拒绝
DataAnalystAgent(mcp_manager=FilesystemPermissionProxy)
  ↓ 又包装一层 ReadOnlyMCPProxy（ClickHouse 写 SQL 过滤）
```

### 三层安全机制

| 层级 | 组件 | 保护对象 |
|------|------|---------|
| 第一层 | FilteredMCPManager | ClickHouse 服务器可见性（ETL 看不到 readonly 服务器，Analyst 看不到 admin 服务器） |
| 第二层 | FilesystemPermissionProxy | 文件系统写权限（仅允许写 `customer_data/{username}/` 和 `.claude/skills/user/`，项目源码不可写） |
| 第三层 | ReadOnlyMCPProxy（仅 Analyst） | ClickHouse 写操作（INSERT/UPDATE/DROP 等 SQL 在执行前拦截） |

### 文件系统目录权限

Agent 通过 Filesystem MCP 工具操作文件时，遵循以下目录权限矩阵：

| 目录 | 读取 | 写入/创建/删除 | 说明 |
|------|------|--------------|------|
| `customer_data/{username}/` | ✓ | ✓ | 当前用户的数据文件（分析结果、导出文件、报告等） |
| `customer_data/` (根目录) | ✓ | ✓ | 权限层面覆盖所有子目录；LLM 路径规则约束用户只写自己目录 |
| `.claude/skills/user/{username}/` | ✓ | ✓ | 用户自定义技能（必须含用户名子目录层）|
| `.claude/skills/user/` (根目录) | ✓ | 仅 `create_directory` | 创建用户子目录合法，直接写文件/删除被拒绝 |
| `.claude/skills/` (系统/项目技能) | ✓ | ✗ | 只读，不可写入 |
| `backend/`、`frontend/` 等 | ✗ | ✗ | 项目源代码，完全隔离 |

> **安全说明**：前端用户通过对话无法修改项目源代码。`customer_data/{username}/` 是每位用户专属的数据工作区（如 `customer_data/alice/reports/`），AI 在系统提示中被告知只能写自己的目录。现有数据（迁移前产生）存储于 `customer_data/superadmin/`。

> **技能写入路径规则（Fix-4）**：AI 保存技能文件时必须写入 `.claude/skills/user/{用户名}/skill-name.md`，直接写到 `user/skill.md` 会被系统拒绝并提示正确格式。AI 收到拒绝后会自动重试并补全用户名子目录层（Fix-3 错误消息引导）。

### 验证配置是否生效

启动后端后，访问以下接口查看注册的 MCP 服务器列表：

```bash
curl http://localhost:8000/api/v1/mcp/servers
```

预期输出（配置了 IDN ReadOnly 时）：
```json
[
  {"name": "clickhouse-idn",    "type": "clickhouse", "level": "admin"},
  {"name": "clickhouse-idn-ro", "type": "clickhouse", "level": "readonly"},
  {"name": "filesystem",        "type": "filesystem"}
]
```

未配置 ReadOnly 凭据时，只会出现 `clickhouse-idn`；分析师 Agent 的日志中会看到：
```
WARNING [AgentMCPBinder] Falling back to admin connection 'clickhouse-idn'
```

---

## 如何新增 ClickHouse 环境

系统支持**零代码**添加任意新 ClickHouse 环境。新增一个连接只需两步：

### 步骤一：在 `.env` 中添加新环境的配置

以新增日本（JP）环境为例，在项目根目录的 `.env` 文件中追加：

```ini
# ── JP ClickHouse Admin ──
CLICKHOUSE_JP_HOST=jp-clickhouse.example.com
CLICKHOUSE_JP_PORT=9000
CLICKHOUSE_JP_HTTP_PORT=8123
CLICKHOUSE_JP_DATABASE=mydb
CLICKHOUSE_JP_USER=admin_jp
CLICKHOUSE_JP_PASSWORD=your_password

# ── JP ClickHouse ReadOnly（可选，不填则 Analyst/General Agent 降级使用 Admin）──
CLICKHOUSE_JP_READONLY_HOST=                # 留空=与 JP_HOST 相同
CLICKHOUSE_JP_READONLY_PORT=               # 留空=与 JP_PORT 相同
CLICKHOUSE_JP_READONLY_DATABASE=           # 留空=与 JP_DATABASE 相同
CLICKHOUSE_JP_READONLY_USER=ro_jp          # ★ 填写此项才会创建只读服务器
CLICKHOUSE_JP_READONLY_PASSWORD=your_ro_password
```

> **注意**：`{ENV}` 部分必须与 `CLICKHOUSE_` 后的名称保持一致，可以是任意大写字母+数字+下划线的组合（如 `JP`、`US_WEST`、`EU`）。

### 步骤二：重启后端服务

```bash
# 停止现有进程后重新启动
python run.py
```

重启后，系统会自动：
1. 通过扫描 `os.environ` 发现 `CLICKHOUSE_JP_HOST`，识别出 `jp` 环境
2. 注册 `clickhouse-jp`（Admin 连接）MCP 服务器
3. 若配置了 `CLICKHOUSE_JP_READONLY_USER`，额外注册 `clickhouse-jp-ro`（ReadOnly 连接）
4. `.claude/agent_config.yaml` 中所有 `clickhouse_envs: all` 的 Agent 自动包含新环境

### 验证新环境已加载

启动后查看 MCP 服务器列表：

```bash
curl http://localhost:8000/api/v1/mcp/servers
```

应看到新增的 `clickhouse-jp` 条目。

### 关键设计原则

| 场景 | 系统行为 |
|------|----------|
| 新增 `CLICKHOUSE_JP_HOST=...` | 重启后自动注册 `clickhouse-jp` |
| 新增 `CLICKHOUSE_JP_READONLY_USER=...` | 重启后自动注册 `clickhouse-jp-ro` |
| `agent_config.yaml` 用 `clickhouse_envs: all` | 新环境无需修改此文件，自动包含 |
| `agent_config.yaml` 用显式列表 `[idn, sg]` | 新环境需手动添加到列表 |
| ReadOnly 用户未填 | 分析师 Agent 降级使用 Admin 连接（WARNING 日志） |

> **推荐**：在 `agent_config.yaml` 中所有 Agent 都使用 `clickhouse_envs: all`，
> 这样以后添加新环境只需更新 `.env` 并重启，无需再改其他任何文件。

---

## Agent 路由关键词配置

系统根据用户消息中的关键词决定将请求路由给哪个 Agent：ETL 工程师、数据分析师还是通用助手。

### 关键词配置位置

路由关键词定义在两个文件中（目前是同步的副本）：

**`backend/agents/orchestrator.py`**（MasterAgent，单 Agent 流）
```python
class MasterAgent:
    _ETL_KEYWORDS = frozenset({
        "建表", "create table", "etl", "数据加工", "写入", ...
    })
    _ANALYST_KEYWORDS = frozenset({
        "分析", "查询", "统计", "报表", "select", ...
    })
```

**`backend/agents/orchestrator_v2.py`**（AgentOrchestrator，双 Agent 流）
```python
_ETL_KEYWORDS: frozenset = frozenset({...})    # 模块级常量
_ANALYST_KEYWORDS: frozenset = frozenset({...})
```

### 路由评分逻辑

每次收到用户消息时，系统统计消息中包含的 ETL 关键词数和分析关键词数，取分数高的 Agent：

```
score_etl     = count(ETL_KEYWORDS ∩ message_words)
score_analyst = count(ANALYST_KEYWORDS ∩ message_words)

if score_etl > score_analyst → 路由到 ETLEngineerAgent
elif score_analyst > 0       → 路由到 DataAnalystAgent
else                         → 路由到通用 AgenticLoop
```

### 如何添加或修改关键词

1. 打开 [backend/agents/orchestrator.py](../backend/agents/orchestrator.py)，找到 `_ETL_KEYWORDS` 和 `_ANALYST_KEYWORDS`
2. 同步修改 [backend/agents/orchestrator_v2.py](../backend/agents/orchestrator_v2.py) 中同名常量
3. 重启后端服务（关键词在内存中，无热重载）

**添加示例**：若需要把"数仓"也路由给 ETL Agent：

```python
# orchestrator.py 和 orchestrator_v2.py 中
_ETL_KEYWORDS = frozenset({
    ...,
    "数仓",          # 新增
    "data warehouse",  # 新增（英文对应）
})
```

### 关键词维护建议

| 场景 | 做法 |
|------|------|
| 新业务词汇被误路由到通用 Agent | 将其加入对应 frozenset |
| 某关键词引发误触发 | 从 frozenset 中删除或调整消息分词逻辑 |
| 需要测试路由是否正确 | 在聊天框发送含目标词的测试消息，查看日志中 `[MasterAgent]` 的路由决策输出 |
| 两组关键词分数相等 | 当前走通用 Agent（`general`），可按需调整 orchestrator.py 中的 tie-breaking 逻辑 |

---

*文档由 Claude Sonnet 4.6 生成 · data-agent v2.7 · 2026-04-13（新增：第 14 节 多图表 HTML 报告生成；更新目录新增第 13/14 节链接；版本号升至 v2.7；报告 RBAC 权限矩阵（reports:read/create/delete）；API 快速参考；14.5 数据刷新机制；14.6 PDF/PPTX 导出；14.7 AI 摘要）*
