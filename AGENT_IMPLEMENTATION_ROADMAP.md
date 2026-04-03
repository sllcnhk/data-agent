# 数据智能体系统 — 后续架构实施路线图

**文档版本**: v1.0
**创建日期**: 2026-02-27
**作者**: Claude Sonnet 4.6
**状态**: 🔵 规划中

---

## 一、现有基础盘点

### 已完成的核心能力

| 模块 | 状态 | 路径 | 说明 |
|------|------|------|------|
| 前端聊天界面 | ✅ | `frontend/src/pages/Chat.tsx` | React+AntDesign对话UI |
| Agent基础框架 | ✅ | `backend/agents/` | base/orchestrator/manager/impl |
| Skills基础实现 | ✅ | `backend/skills/` | 5个核心skill (Python class形式) |
| MCP服务器层 | ✅ | `backend/mcp/` | ClickHouse/MySQL/Filesystem/Lark |
| Context管理Phase1 | ✅ | `backend/core/context_manager.py` | 智能压缩，74%压缩率 |
| Context管理Phase3 | ✅ | `backend/core/vector_store.py` | ChromaDB向量存储+语义检索 |
| Model适配器 | ✅ | `backend/core/model_adapters/` | Claude/OpenAI/Gemini/国内LLM |
| REST API层 | ✅ | `backend/api/` | agents/skills/conversations/mcp |

### 现有关键缺口（待补全）

1. **Agentic Loop 缺失**: `orchestrator.py` 仅基于关键词匹配意图，无真正的 Think→Act→Observe 推理循环
2. **SKILL.md体系未建立**: Skills以Python class存在，缺乏 Markdown格式渐进式披露机制
3. **MCP未接入对话链**: MCP Server已实现但未在对话流中实际调用
4. **无流式输出**: 对话响应无法实时展示推理过程
5. **无专业领域Agent**: 缺乏数据加工工程师、数据分析师的专业能力定义
6. **无用户自定义技能**: 缺乏用户在页面创建子技能并热加载的机制
7. **无多Agent协作**: 缺乏Agent间Handoff和Sub-agent派生机制

---

## 二、总体架构规划

```
┌─────────────────────────────────────────────────────┐
│                  前端 (React)                         │
│  Chat │ Skill Dashboard │ Agent Monitor │ Approval    │
└─────────────────────────┬───────────────────────────┘
                          │ HTTP/SSE/WebSocket
┌─────────────────────────▼───────────────────────────┐
│              Gateway 网关层 (FastAPI)                  │
│  Session管理 │ 流式输出 │ 审批Hook │ 意图路由          │
└──────┬───────────────────────────────────┬───────────┘
       │                                   │
┌──────▼─────────┐               ┌─────────▼──────────┐
│  Agentic Loop  │               │   Skills 体系       │
│  (推理循环)    │               │   SKILL.md 文件库   │
│                │               │   渐进式披露索引    │
│ 1.Perceive     │               │   用户自定义技能    │
│ 2.Retrieve→Skills              └────────────────────┘
│ 3.Plan         │
│ 4.Act→MCP      │               ┌────────────────────┐
│ 5.Observe      │               │   MCP 工具层        │
└────────────────┘               │  ClickHouse/MySQL  │
                                 │  Filesystem/Lark   │
┌────────────────┐               └────────────────────┘
│  专业Agent实例  │
│ 数据加工工程师 │               ┌────────────────────┐
│ 数据分析师     │               │   Context 管理层    │
│ (未来更多)     │               │  向量检索/语义压缩  │
└────────────────┘               └────────────────────┘
```

---

## 三、实施步骤（按依赖关系排列）

---

### 阶段一：基础链路打通（必做，其他阶段的前提）

> **目标**: 将现有MCP、Skills、Agent串联成真正可运行的对话链路

#### 步骤 1.1：Agentic Loop 核心控制器重构
- **状态**: ⬜ 未开始
- **依赖**: 无
- **优先级**: P0 最高优先级
- **路径**: `backend/agents/orchestrator.py`

子步骤：
- [ ] 1.1.1 设计 AgentLoop 五阶段接口 (Perceive / Retrieve / Plan / Act / Observe)
- [ ] 1.1.2 将关键词意图分类升级为 LLM `tool_use` 模式的语义意图识别
- [ ] 1.1.3 实现 Plan 阶段：LLM 基于可用 Skills 元数据生成多步执行计划 (step-by-step thinking)
- [ ] 1.1.4 实现 Act 阶段：将 LLM 的 tool_call 转发给对应 MCP Server 执行
- [ ] 1.1.5 实现 Observe 阶段：解析工具执行结果，判断是否需要重试或修正
- [ ] 1.1.6 实现自愈机制：SQL报错时自动重写，连接失败时自动切换备用配置
- [ ] 1.1.7 单元测试：验证五阶段各自独立可测

**预期产出**: `backend/agents/agentic_loop.py`

---

#### 步骤 1.2：MCP 接入对话流
- **状态**: ⬜ 未开始
- **依赖**: 1.1
- **优先级**: P0
- **路径**: `backend/mcp/client.py`（新建）

子步骤：
- [ ] 1.2.1 在后端实现 MCP Client，负责将 LLM 的 tool_call 转换为 MCP JSON-RPC 调用
- [ ] 1.2.2 实现连接池管理：多用户并发时的 ClickHouse 连接复用
- [ ] 1.2.3 实现工具调用结果的标准化格式（成功/错误/超时统一处理）
- [ ] 1.2.4 将 MCP Client 注册到 Agentic Loop 的 Act 阶段
- [ ] 1.2.5 在 `.env` 配置文件中完善 ClickHouse 连接参数模板

**预期产出**: `backend/mcp/client.py`，更新 `backend/mcp/manager.py`

---

#### 步骤 1.3：流式输出框架（SSE）
- **状态**: ⬜ 未开始
- **依赖**: 1.1
- **优先级**: P0
- **路径**: `backend/api/conversations.py`，`frontend/src/services/chatApi.ts`

子步骤：
- [ ] 1.3.1 后端新增 `/api/v1/conversations/{id}/stream` SSE 端点
- [ ] 1.3.2 定义流式事件类型：`thinking` / `tool_call` / `tool_result` / `answer` / `error`
- [ ] 1.3.3 Agentic Loop 每个阶段向 SSE 流推送进度事件
- [ ] 1.3.4 前端 chatApi.ts 改造为消费 SSE 流
- [ ] 1.3.5 前端 ChatMessages.tsx 展示推理步骤（可折叠的 Thought Process 卡片）
- [ ] 1.3.6 前端展示 MCP 工具调用日志（SQL 语句、执行耗时、返回行数）

**预期产出**: 可实时展示推理过程的聊天界面

---

### 阶段二：Skills 体系升级（专业化的前提）

> **目标**: 将 Python class形式的Skills升级为支持渐进式披露的 SKILL.md 文件体系

#### 步骤 2.1：SKILL.md 文件规范与元数据索引器
- **状态**: ⬜ 未开始
- **依赖**: 无（可与阶段一并行）
- **优先级**: P1
- **路径**: `backend/skills/` 目录，新增 `.claude/skills/` 目录

子步骤：
- [ ] 2.1.1 定义 SKILL.md 文件 YAML frontmatter 规范（name/description/triggers/tools/author/version）
- [ ] 2.1.2 实现 SkillFileLoader：扫描 `.claude/skills/` 目录，解析所有 SKILL.md 文件
- [ ] 2.1.3 实现渐进式披露：初始化时仅加载 name+description+triggers（约100 tokens），触发时才注入完整内容
- [ ] 2.1.4 实现元数据索引器：将技能元数据以向量形式存储到 ChromaDB（复用 Phase 3 的 VectorStoreManager）
- [ ] 2.1.5 实现语义技能匹配：用户意图 → embedding → 向量检索 → 返回 Top-K 相关技能
- [ ] 2.1.6 将现有 5 个 Python Skills 转换为对应的 SKILL.md 文件

**预期产出**: `backend/skills/skill_loader.py`，`.claude/skills/` 目录

---

#### 步骤 2.2：Skills 热加载机制
- **状态**: ⬜ 未开始
- **依赖**: 2.1
- **优先级**: P1
- **路径**: `backend/skills/watcher.py`（新建）

子步骤：
- [ ] 2.2.1 使用 `watchdog` 库实现文件监视器，监控 `.claude/skills/` 目录变动
- [ ] 2.2.2 文件新增/修改/删除时，自动更新内存中的技能索引
- [ ] 2.2.3 热加载完成后通过 WebSocket 通知前端刷新技能列表
- [ ] 2.2.4 实现技能校验：新技能加载前做 YAML 格式和安全字段检查

**预期产出**: `backend/skills/watcher.py`

---

### 阶段三：专业 Agent 实现（核心业务价值）

> **目标**: 实现数据加工工程师和数据分析师两个专业 Agent

#### 步骤 3.1：数据加工工程师 Agent（ETL Engineer）
- **状态**: ⬜ 未开始
- **依赖**: 1.1, 1.2, 2.1
- **优先级**: P1

子步骤：

**3.1.1 Schema 感知技能**
- [ ] 编写 `.claude/skills/schema-explorer.md`：定义表结构探索的规程
  - 调用 MCP `list_databases` → `list_tables` → `describe_table` 构建全局视图
  - 格式化输出表字段、类型、索引、注释的结构化摘要
  - 自动识别主键、外键关系，构建表关系图

**3.1.2 业务语义映射技能（AGENTS.md）**
- [ ] 编写 `.claude/skills/business-glossary.md`：业务字典维护规程
  - 用户输入"定义活跃用户 = 30天内有登录行为"
  - Agent 自动追加到 `data/business_glossary.yaml`
  - 后续查询时自动注入相关业务定义

**3.1.3 数据加工方案生成技能**
- [ ] 编写 `.claude/skills/etl-engineer.md`：ETL方案生成规程
  - 输入：业务需求描述 + 可用基础表列表
  - 输出模板：逻辑映射 / 性能优化建议 / 质量检查规则 / 完整DDL+SQL
  - ClickHouse特有优化：ORDER BY选择、分区策略、物化视图建议
  - 自动生成统计表 DDL 和增量更新 SQL

**3.1.4 ETL 方案 Dry-Run 校验**
- [ ] 实现执行前校验 Hook：
  - 检查 SQL 中引用的字段是否存在于已知 Schema
  - 检查表名是否符合命名规范
  - 检查 ClickHouse 语法正确性（`EXPLAIN` 语句）
  - 高危操作（DROP/TRUNCATE）强制标记 ⚠️

**预期产出**: `.claude/skills/etl-engineer.md`，`.claude/skills/schema-explorer.md`，`backend/agents/etl_agent.py`

---

#### 步骤 3.2：数据分析师 Agent（Data Analyst）
- **状态**: ⬜ 未开始
- **依赖**: 1.1, 1.2, 2.1
- **优先级**: P1

子步骤：

**3.2.1 高效 ClickHouse 查询技能**
- [ ] 编写 `.claude/skills/clickhouse-analyst.md`：ClickHouse查询规程
  - ClickHouse 专有函数指南（`uniqCombined`, `countIf`, `arrayJoin`, `toStartOfDay`）
  - 留存分析、漏斗分析、同比/环比计算模板
  - 查询性能优化规则（避免 SELECT *，合理使用 PREWHERE）
  - 仅允许 SELECT 查询（readonly 安全约束）

**3.2.2 探索性数据分析（EDA）技能**
- [ ] 编写 `.claude/skills/eda-analysis.md`：探索性分析规程
  - 自动数据概览：行数、字段分布、空值率、异常值检测
  - 趋势分析：时间序列自动识别，周期性检测
  - 多轮钻取（Drill-down）：根据分析结果自动提出下一个分析问题

**3.2.3 数据报告生成技能**
- [ ] 编写 `.claude/skills/report-generator.md`：报告生成规程
  - Markdown 格式报告模板（执行摘要 / 关键发现 / 数据可视化 / 结论建议）
  - 自动生成图表配置 JSON（复用现有 ChartComponent.tsx）
  - 支持将报告保存为文件（复用 Filesystem MCP Server）

**3.2.4 数据分析师 Agent 实现**
- [ ] `backend/agents/analyst_agent.py`：集成上述技能的 Agent 实例
- [ ] 配置只读 MCP 工具权限（prevent DDL/DML）
- [ ] 实现会话内的分析上下文管理（记住已查询的表、已发现的洞察）

**预期产出**: `.claude/skills/clickhouse-analyst.md`，`backend/agents/analyst_agent.py`

---

### 阶段四：用户自定义技能（平台化能力）

> **目标**: 用户可在聊天中创建自己的子技能，立即生效并持久化

#### 步骤 4.1：Skill-Creator 元技能
- **状态**: ✅ 已完成（2026-03-01）
- **依赖**: 2.1, 2.2
- **优先级**: P2

子步骤：
- [ ] 4.1.1 编写 `.claude/skills/skill-creator.md`：创建技能的技能
  - 触发词："记住这个操作流程" / "以后都这样做" / "保存这个分析方法"
  - 引导用户确认技能名称、触发词、执行规程
  - 自动生成符合规范的 SKILL.md 文件内容

- [ ] 4.1.2 后端实现技能持久化 API
  - `POST /api/v1/skills/user-defined` — 接收生成的技能内容，按用户ID分目录存储
  - 存储路径：`.claude/skills/user-defined/{user_id}/`
  - 触发 SkillWatcher 热加载

- [ ] 4.1.3 技能作用域控制
  - 用户自定义技能默认仅在自己的会话中生效
  - 支持"公开技能"：管理员可将用户技能推广为全局技能

- [ ] 4.1.4 技能安全校验
  - YAML frontmatter 格式强制检查
  - 禁止在技能描述中嵌入可执行命令
  - 敏感词过滤（防止 prompt injection）

**预期产出**: `.claude/skills/skill-creator.md`，`POST /api/v1/skills/user-defined`

---

#### 步骤 4.2：前端技能仪表盘（Skill Dashboard）
- **状态**: ✅ 已完成（2026-03-03）
- **依赖**: 4.1
- **优先级**: P2
- **路径**: `frontend/src/pages/Skills.tsx`

子步骤：
- [x] 4.2.1 技能列表展示：Tabs 分组展示内置技能（SKILL.md）和用户自定义技能
- [x] 4.2.2 技能创建向导：表单（name/description/triggers/category/priority/content），触发词逗号分隔，即时热加载
- [x] 4.2.3 内容查看 Drawer：查看完整 SKILL.md 内容（代码块渲染）
- [x] 4.2.4 用户技能删除：带确认对话框，删除后自动热卸载
- [x] 4.2.5 后端新增 GET /skills/md-skills 端点，统一返回所有 SKILL.md 技能元数据

**预期产出**: 完整的技能管理页面

---

### 阶段五：多 Agent 协作（进阶能力）

> **目标**: 数据加工工程师与数据分析师能够协同完成跨领域任务

#### 步骤 5.1：Agent 编排器与 Handoff 机制
- **状态**: ✅ 已完成（2026-03-01）
- **依赖**: 3.1, 3.2
- **优先级**: P2

子步骤：
- [ ] 5.1.1 设计 Handoff Packet 数据结构
  ```json
  {
    "from_agent": "etl_engineer",
    "to_agent": "data_analyst",
    "task_summary": "已创建统计表 dws_user_daily_active",
    "artifacts": {"table_name": "...", "schema": "..."},
    "next_action": "请对该表进行留存分析"
  }
  ```
- [ ] 5.1.2 实现 AgentOrchestrator：主 Agent 检测任务是否需要转交，并生成 Handoff Packet
- [ ] 5.1.3 实现 Agent 会话隔离：每个 Agent 实例有独立的会话上下文，但可读取 Handoff 数据
- [ ] 5.1.4 前端展示 Agent 协作流程：任务看板式 UI，展示哪个 Agent 正在处理

- [ ] 5.1.5 Sub-agent 临时召唤：主 Agent 可临时创建"SQL优化专家"等子 Agent，任务完成后销毁

**预期产出**: `backend/agents/orchestrator_v2.py`，前端任务看板组件

---

### 阶段六：审批网关与安全加固（生产化必需）

> **目标**: 确保高危操作必须经过人工确认，防止数据事故

#### 步骤 6.1：Human-in-the-Loop 审批机制
- **状态**: ⬜ 未开始
- **依赖**: 1.2, 1.3
- **优先级**: P1（与阶段三并行）

子步骤：
- [ ] 6.1.1 定义高危操作规则集（触发审批的操作类型）
  - SQL：`DROP`, `TRUNCATE`, `DELETE`, `ALTER TABLE`
  - 文件：`delete`, 覆盖写入 `write`
  - 查询：预估扫描数据量 > 1GB 的 ClickHouse 查询

- [ ] 6.1.2 后端实现审批队列
  - Agentic Loop 执行高危操作前，暂停并推送 SSE `approval_required` 事件
  - 等待前端返回 `approve` / `reject`（超时60秒自动 reject）
  - 审批日志持久化存储

- [ ] 6.1.3 前端审批弹窗
  - 展示操作详情：具体 SQL / 文件路径 / 预计影响范围
  - 提供"执行" / "取消" / "修改后执行" 三个选项
  - 超时倒计时显示

- [ ] 6.1.4 沙箱执行环境（可选，高安全需求时启用）
  - 用户自定义技能中的脚本在 Docker 容器内执行
  - 限制网络访问、文件系统访问

**预期产出**: `backend/api/approvals.py`，前端 ApprovalModal 组件

---

## 四、技术栈说明

| 模块 | 现有技术 | 新增/升级 |
|------|---------|----------|
| 后端框架 | FastAPI (Python 3.8) | 无变化 |
| 前端框架 | React + Ant Design | 新增 SSE 消费、WebSocket |
| LLM 引擎 | Claude (claude-sonnet-4-6) | 升级为 `tool_use` 模式 |
| MCP 协议 | 自实现 MCP Server | 新增 MCP Client（接入对话链） |
| 向量检索 | ChromaDB 0.4.22 | 复用，扩展为技能索引 |
| 会话存储 | PostgreSQL | 扩展 session 表，添加 agent_state |
| 文件监视 | 无 | 新增 watchdog 库 |
| 流式输出 | 无 | 新增 SSE (Server-Sent Events) |

---

## 五、文件结构规划（新增/升级）

```
data-agent/
├── .claude/
│   └── skills/                        ← 新建：SKILL.md 文件库
│       ├── system/                    ← 系统内置技能
│       │   ├── schema-explorer.md
│       │   ├── etl-engineer.md
│       │   ├── clickhouse-analyst.md
│       │   ├── eda-analysis.md
│       │   ├── report-generator.md
│       │   └── skill-creator.md
│       └── user-defined/              ← 用户自定义技能（按用户ID分目录）
│           └── {user_id}/
│               └── custom-skill.md
│
├── data/
│   └── business_glossary.yaml         ← 新建：业务语义字典
│
├── backend/
│   ├── agents/
│   │   ├── agentic_loop.py            ← 新建：五阶段推理循环
│   │   ├── etl_agent.py               ← 新建：数据加工工程师Agent
│   │   ├── analyst_agent.py           ← 新建：数据分析师Agent
│   │   └── orchestrator_v2.py         ← 升级：多Agent编排
│   │
│   ├── skills/
│   │   ├── skill_loader.py            ← 新建：SKILL.md文件加载器
│   │   ├── skill_watcher.py           ← 新建：热加载文件监视器
│   │   └── skill_index.py             ← 新建：向量化技能索引
│   │
│   ├── mcp/
│   │   └── client.py                  ← 新建：MCP对话链客户端
│   │
│   └── api/
│       └── approvals.py               ← 新建：审批网关API
│
└── frontend/src/
    ├── components/
    │   ├── chat/
    │   │   ├── ThoughtProcess.tsx      ← 新建：推理步骤展示
    │   │   └── ApprovalModal.tsx       ← 新建：审批弹窗
    │   └── skills/
    │       ├── SkillCard.tsx           ← 新建：技能卡片
    │       └── SkillEditor.tsx         ← 新建：在线编辑器
    └── pages/
        ├── Skills.tsx                  ← 升级：技能仪表盘
        └── Agents.tsx                  ← 升级：Agent监控面板
```

---

## 六、实施优先级总览

```
优先级 P0（核心链路，阻塞其他所有功能）
  └─ 1.1 Agentic Loop 重构
  └─ 1.2 MCP 接入对话流
  └─ 1.3 流式输出 SSE

优先级 P1（业务价值，与P0并行后续推进）
  ├─ 2.1 SKILL.md 规范与索引器
  ├─ 2.2 Skills 热加载
  ├─ 3.1 数据加工工程师 Agent
  ├─ 3.2 数据分析师 Agent
  └─ 6.1 Human-in-the-Loop 审批

优先级 P2（平台化能力，核心功能稳定后推进）
  ├─ 4.1 Skill-Creator 元技能
  ├─ 4.2 前端技能仪表盘
  └─ 5.1 多 Agent Handoff 协作
```

---

## 七、各阶段开发进度跟踪

> 开始具体开发时，请在此处更新各步骤状态：⬜未开始 / 🔄进行中 / ✅已完成 / ⚠️有问题

| 步骤 | 名称 | 状态 | 完成日期 | 备注 |
|------|------|------|---------|------|
| 1.1 | Agentic Loop 重构 | ✅ | 2026-02-27 | AgenticLoop 5阶段 + chat_with_tools |
| 1.2 | MCP 接入对话流 | ✅ | 2026-02-27 | tool_formatter + 工具命名空间 |
| 1.3 | 流式输出 SSE | ✅ | 2026-02-27 | thinking/tool_call/tool_result/content 事件 |
| 2.1 | SKILL.md 规范与索引 | ✅ | 2026-02-28 | skill_loader.py + 3个技能文件 + AgenticLoop集成 |
| 2.2 | Skills 热加载 | ✅ | 2026-02-28 | skill_watcher.py + watchdog + Debouncer，T12 |
| 3.1 | 数据加工工程师 Agent | ✅ | 2026-02-28 | etl_agent.py + ETLAgenticLoop + SQL安全，T10 |
| 3.2 | 数据分析师 Agent | ✅ | 2026-02-28 | analyst_agent.py + ReadOnlyMCPProxy，T11 |
| 4.1 | Skill-Creator 元技能 | ✅ | 2026-03-01 | skill-creator.md + user/ 目录 + CRUD API，T13/Phase4 |
| 4.2 | 前端技能仪表盘 | ✅ | 2026-03-03 | Skills.tsx 重写：内置/用户Tab + 创建向导 + 内容Drawer + 删除，md-skills API |
| 5.1 | 多 Agent Handoff | ✅ | 2026-03-01 | orchestrator_v2.py + HandoffPacket，T16 |
| 6.1 | Human-in-the-Loop | ✅ | 2026-03-01 | approval_manager + ApprovalModal + 真实暂停，T13/T14 |

---

## 八、关键决策点与风险

### 决策点 1：LLM 意图识别方案
- **方案 A（推荐）**: 使用 Claude `tool_use` 模式，将所有技能描述作为工具列表，由 LLM 自主决定调用哪个技能
- **方案 B（备用）**: 轻量化本地语义模型（sentence-transformers）+ 向量检索
- **决策标准**: 方案A更准确但每次需要注入技能列表；方案B更快但需要维护向量索引

### 决策点 2：技能文件存储位置
- **方案 A（推荐）**: 本地文件系统 `.claude/skills/`，配合 git 版本管理
- **方案 B**: 数据库存储，支持在线编辑
- **当前选择**: A（简单优先），后期可升级为A+B混合

### 风险 1：ClickHouse 查询安全
- **风险**: Agent 生成误操作 SQL 导致数据损坏
- **缓解**: 只读 MCP 工具权限 + 高危操作审批 + 查询日志审计

### 风险 2：用户自定义技能安全
- **风险**: 用户注入恶意指令污染 Agent 行为
- **缓解**: YAML 格式校验 + 敏感词过滤 + 技能作用域隔离（只在自己会话生效）

---

**文档维护说明**: 每次开始新阶段开发前，先在第七节更新进度状态；完成一个步骤后，立即将状态改为 ✅ 并填写完成日期和备注。

---

## 九、详细开发 TodoList（当前实施阶段）

> 实时更新。完成一项立即标记 ✅，遇到问题标记 ⚠️ 并注明原因。

### P0 — 阶段一：Agentic Loop + MCP接入 + SSE流式

#### 1.1 + 1.2 Agentic Loop 与 MCP Client（合并实施）

- [x] **T1** `backend/mcp/tool_formatter.py` — MCP工具→Claude tools格式转换器 ✅
  - 命名规则：`{server_name_underscored}__{tool_name}`
  - 函数：`format_mcp_tools_for_claude()` / `parse_tool_name()`

- [x] **T2** `backend/core/model_adapters/claude.py` — 新增两个方法 ✅
  - `chat_with_tools(messages, system_prompt, tools)` → 原始API响应dict（含stop_reason/content blocks）
  - `chat_plain(messages, system_prompt)` → 同格式，不传tools

- [x] **T3** `backend/agents/agentic_loop.py` — 5阶段循环核心（新建文件）✅
  - `AgentEvent` 数据类：type/data/metadata
  - `AgenticResult` 数据类：success/content/metadata/events
  - `AgenticLoop.run_streaming()` — async generator，按阶段yield AgentEvent
  - `AgenticLoop.run()` — 收集所有事件，返回AgenticResult

- [x] **T4** `backend/agents/orchestrator.py` — 更新 MasterAgent ✅
  - `process()` 改为调用 `AgenticLoop.run()`（替换旧关键词分类）
  - 新增 `process_stream()` async generator，yield AgentEvent

#### 1.3 SSE 流式输出

- [x] **T5** `backend/services/conversation_service.py` — 更新 `send_message_stream()` ✅
  - 改为 `async for event in agent.process_stream(content, context):`
  - 每个 AgentEvent 直接 yield 给前端（type: thinking/tool_call/tool_result/content/error）
  - 最后 yield `{type: "assistant_message", data: ...}`

#### 自测 Checklist

- [x] **ST1** 启动后端服务（`start-all.bat` 或 `python run_simple.py`）✅
- [x] **ST2** 发送普通问候语，确认走 AgenticLoop → chat_plain → 正常回复 ✅
- [x] **ST3** 发送数据相关问题，确认走 AgenticLoop → chat_with_tools（如有MCP server）✅
- [x] **ST4** 验证 SSE 流：`test_e2e_stream.py` 验证通过 ✅
  - 事件序列: `user_message → thinking → content → assistant_message → done`
- [x] **ST5** 错误恢复：异常被捕获后仍保存并返回 assistant_message ✅

### P0 交付总结
交付总结 — P0 Agentic Loop 实施完成
新建文件
文件	说明
backend/mcp/tool_formatter.py	MCP 工具 → Claude tools 格式转换器，命名规则 {server}__{tool}
backend/agents/agentic_loop.py	5阶段 Agentic Loop：Perceive/Retrieve/Plan/Act/Observe，含 run_streaming() async generator
修改文件
文件	改动
backend/core/model_adapters/claude.py	新增 chat_with_tools() + chat_plain()，直接返回原始 API 响应 dict
backend/agents/orchestrator.py	process() 改为调用 AgenticLoop.run()；新增 process_stream() async generator
backend/services/conversation_service.py	send_message_stream() 升级为真实 SSE：直接 yield AgentEvent，保证异常后仍保存 assistant_message
AGENT_IMPLEMENTATION_ROADMAP.md	更新进度表（1.1/1.2/1.3 ✅）+ 追加详细 TodoList 章节
自测结果
单元测试（logic tests）：28/28 通过
E2E 流式测试：SSE 事件序列 user_message → thinking → content → assistant_message → done ✅
非流式测试：正常返回 461+ 字符的 LLM 响应 ✅
错误恢复：异常被捕获后仍然保存并返回 assistant_message，不会崩溃 ✅



### P1 — 阶段二/三（P0完成后推进）

- [x] **T6** SKILL.md文件格式规范（`.claude/skills/README.md` 规范文档）✅
- [x] **T7** `backend/skills/skill_loader.py` — SkillLoader + YAML解析 + 触发词匹配 ✅
- [x] **T8** `.claude/skills/etl-engineer.md` + `.claude/skills/schema-explorer.md` ✅
- [x] **T9** `.claude/skills/clickhouse-analyst.md` — ClickHouse分析师技能 ✅
- [x] **T10** `backend/agents/etl_agent.py` — ETL工程师Agent实现 ✅
  - ETLAgenticLoop(AgenticLoop) 覆写 _execute_tool，检测危险SQL，emit approval_required
  - ETLEngineerAgent.process/process_stream，safety events 在 tool_call 前 flush
- [x] **T11** `backend/agents/analyst_agent.py` — 数据分析师Agent实现 ✅
  - ReadOnlyMCPProxy：_READ_ONLY_TOOLS 白名单 + _is_readonly_sql 双层拦截
  - DataAnalystAgent 使用 ReadOnlyMCPProxy 包装 mcp_manager
- [x] **T12** `backend/skills/skill_watcher.py` — 技能文件热加载 ✅
  - _Debouncer(0.8s) + watchdog Observer + SkillWatcher.start/stop
  - start_skill_watcher() / stop_skill_watcher() 全局单例，main.py 已集成

### P2 — 阶段四/五（P1完成后推进）

- [x] **T13** `backend/api/approvals.py` + `backend/core/approval_manager.py` ✅
  - ApprovalManager 单例：create_approval / wait_for_decision(async) / approve / reject / timeout
  - REST endpoints: GET/{id}、POST/{id}/approve、POST/{id}/reject、GET/(list)
  - ETLAgenticLoop.run_streaming 重写：tool_call 前真实暂停等待用户审批
- [x] **T14** 前端 `ApprovalModal.tsx` ✅
  - 60 秒倒计时进度条；Approve / Reject 按钮调用 REST API
  - Chat.tsx 集成：approval_required SSE 事件 → setPendingApproval → Modal 弹出
- [x] **T15** 前端 `ThoughtProcess.tsx` ✅
  - 可折叠 Collapse 面板，展示 thinking / tool_call / tool_result 事件
  - useChatStore 新增 messageThoughts / addThoughtEvent
  - ChatMessages.tsx 集成；Chat.tsx SSE handler 处理所有事件类型
- [x] **T16** `backend/agents/orchestrator_v2.py` ✅
  - HandoffPacket 数据结构 + to_context_prompt() 注入
  - AgentOrchestrator：关键词评分路由 + 隐式 Handoff 检测（正则）+ 最多 2 跳
  - create_orchestrator() 工厂函数
- [x] **Phase 4 extras** ✅
  - `.claude/skills/skill-creator.md` — 元技能：引导用户创建自定义 SKILL.md
  - `.claude/skills/user/` — 用户技能存储目录（SkillWatcher 热加载）
  - `POST /api/v1/skills/user-defined` — 创建用户技能（写入 .md 文件）
  - `GET /api/v1/skills/user-defined` — 列出用户技能
  - `DELETE /api/v1/skills/user-defined/{name}` — 删除用户技能
