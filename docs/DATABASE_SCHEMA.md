# data-agent 数据库 Schema 说明

> **适用对象**：开发者、LLM 代码助手、新接手人员
> **数据库引擎**：PostgreSQL 14+
> **ORM 框架**：SQLAlchemy 2.x + Alembic
> **最后更新**：2026-03-18

---

## 目录

1. [概览](#1-概览)
2. [连接配置](#2-连接配置)
3. [ER 关系图（文本）](#3-er-关系图文本)
4. [RBAC 认证模块](#4-rbac-认证模块)
   - [users — 用户表](#41-users--用户表)
   - [roles — 角色表](#42-roles--角色表)
   - [permissions — 权限表](#43-permissions--权限表)
   - [user_roles — 用户角色关联表](#44-user_roles--用户角色关联表)
   - [role_permissions — 角色权限关联表](#45-role_permissions--角色权限关联表)
   - [refresh_tokens — 刷新令牌表](#46-refresh_tokens--刷新令牌表)
5. [对话模块](#5-对话模块)
   - [conversations — 对话表](#51-conversations--对话表)
   - [messages — 消息表](#52-messages--消息表)
   - [context_snapshots — 上下文快照表](#53-context_snapshots--上下文快照表)
   - [conversation_groups — 对话分组表](#54-conversation_groups--对话分组表)
6. [任务模块](#6-任务模块)
   - [tasks — 任务表](#61-tasks--任务表)
   - [task_history — 任务历史表](#62-task_history--任务历史表)
7. [报表模块](#7-报表模块)
   - [reports — 报表表](#71-reports--报表表)
   - [charts — 图表表](#72-charts--图表表)
8. [模型配置模块](#8-模型配置模块)
   - [llm_configs — LLM 配置表](#81-llm_configs--llm-配置表)
9. [全局设计规范](#9-全局设计规范)
10. [初始化与迁移](#10-初始化与迁移)
11. [业务数据说明（ClickHouse）](#11-业务数据说明clickhouse)

---

## 1. 概览

data-agent 的 PostgreSQL 数据库负责存储**系统运行状态数据**，包括：

| 模块 | 用途 | 核心表 |
|------|------|--------|
| **RBAC 认证** | 用户、角色、权限、JWT 刷新令牌 | users / roles / permissions / user_roles / role_permissions / refresh_tokens |
| **对话管理** | 多轮对话历史、消息记录、分组 | conversations / messages / conversation_groups |
| **上下文压缩** | 长对话压缩快照，避免 context 溢出 | context_snapshots |
| **任务调度** | ETL/分析任务的状态跟踪与历史 | tasks / task_history |
| **报表系统** | 可视化报表与图表配置 | reports / charts |
| **模型配置** | LLM 接入参数（API Key、模型版本）| llm_configs |

**注意**：业务数据（ClickHouse 外呼记录、CRM 数据）存储在独立的 ClickHouse 实例，不在此数据库中。参见 [第 11 节](#11-业务数据说明clickhouse)。

---

## 2. 连接配置

**配置文件**：`backend/config/database.py`、`backend/config/settings.py`

```bash
# .env 配置项
POSTGRES_USER=postgres
POSTGRES_PASSWORD=<password>
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
POSTGRES_DB=data_agent
POSTGRES_POOL_SIZE=20         # 连接池大小
POSTGRES_MAX_OVERFLOW=0       # 超出 pool_size 的最大额外连接数
```

**连接 URL 格式**：
```
postgresql+asyncpg://{USER}:{PASSWORD}@{HOST}:{PORT}/{DB}
```

**连接池策略**：
- `pool_size=20`：常驻连接数
- `pool_recycle=3600`：连接每小时强制回收，防止 PostgreSQL 超时断开
- `pool_pre_ping=True`：每次借出连接前 ping 检查，自动剔除失效连接

---

## 3. ER 关系图（文本）

```
┌─────────────────────────────────────────────────────────────────────┐
│                        RBAC 认证模块                                  │
│                                                                      │
│  users (1) ──── (N) user_roles (N) ──── (1) roles                   │
│                                               │                      │
│  users (1) ──── (N) refresh_tokens            │ (N)                  │
│                                         role_permissions             │
│                                               │ (1)                  │
│                                         permissions                  │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│                        对话模块                                        │
│                                                                      │
│  conversation_groups (1) ──── (N) conversations                     │
│                                        │                            │
│                              ┌─────────┴──────────┐                 │
│                              │                    │                  │
│                           (N) messages   (N) context_snapshots      │
│                              │                    │                  │
│                              └─────────┬──────────┘                 │
│                                        │                            │
│                               (N) tasks (通过 conversation_id)       │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│                     报表 & 任务模块                                    │
│                                                                      │
│  reports (1) ──── (N) charts                                        │
│  tasks (1) ──── (N) task_history                                    │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 4. RBAC 认证模块

### 4.1 `users` — 用户表

**文件**：`backend/models/user.py`
**用途**：存储平台用户，支持本地账号（用户名+密码）和 SSO（Lark / 企业微信 / 钉钉）两种认证方式。

| 字段名 | 类型 | 约束 | 说明 |
|--------|------|------|------|
| `id` | UUID | PK, 索引 | 主键，自动生成 |
| `username` | VARCHAR(64) | UNIQUE, NOT NULL, 索引 | 登录名，全局唯一 |
| `display_name` | VARCHAR(128) | nullable | 显示名（昵称） |
| `email` | VARCHAR(256) | UNIQUE, nullable | 邮箱，SSO 账号填入 |
| `auth_source` | VARCHAR(20) | default='local' | 认证来源：`local` \| `lark` \| `wecom` \| `dingtalk` |
| `external_id` | VARCHAR(256) | nullable | SSO 外部用户 ID（本地账号为 null） |
| `hashed_password` | VARCHAR(256) | nullable | bcrypt 哈希密码（SSO 账号为 null） |
| `is_active` | BOOLEAN | default=TRUE | 账号启用状态，禁用后无法登录 |
| `is_superadmin` | BOOLEAN | default=FALSE | 超级管理员标志，**绕过所有权限检查** |
| `last_login_at` | TIMESTAMP | nullable | 最近登录时间 |
| `extra_meta` | JSONB | nullable | 扩展元数据：头像 URL、部门、Lark 用户名等 |
| `created_at` | TIMESTAMP | NOT NULL | 创建时间 |
| `updated_at` | TIMESTAMP | NOT NULL | 最后更新时间，自动维护 |

**关联关系**：
- `user_roles`：一对多，级联删除（用户删除 → 角色绑定同步删除）
- `refresh_tokens`：一对多，级联删除（用户删除 → 所有 token 同步删除）

**预置超级管理员**：
- `username = superadmin`，`is_superadmin = true`
- 初始化脚本 `backend/scripts/init_rbac.py` 创建

---

### 4.2 `roles` — 角色表

**文件**：`backend/models/role.py`
**用途**：定义角色，是权限的集合载体。

| 字段名 | 类型 | 约束 | 说明 |
|--------|------|------|------|
| `id` | UUID | PK, 索引 | 主键，自动生成 |
| `name` | VARCHAR(64) | UNIQUE, NOT NULL, 索引 | 角色名，全局唯一 |
| `description` | TEXT | nullable | 角色说明 |
| `is_system` | BOOLEAN | default=TRUE | 系统内置角色，`is_system=true` 时禁止删除 |
| `created_at` | TIMESTAMP | NOT NULL | 创建时间 |

**系统内置角色（`is_system=true`）**：

| 角色名 | 说明 | 权限列表 |
|--------|------|---------|
| `viewer` | 只读访问者 | `chat:use` |
| `analyst` | 数据分析师 | `chat:use`, `skills.user:read`, `skills.user:write`, `skills.project:read`, `skills.system:read`, `settings:read` |
| `admin` | 平台管理员 | analyst 的全部权限 + `skills.project:write`, `models:read`, `models:write`, `settings:write` |
| `superadmin` | 超级管理员 | 全部权限（通过 `is_superadmin=true` 字段绕过权限检查，而非逐一列举） |

---

### 4.3 `permissions` — 权限表

**文件**：`backend/models/permission.py`
**用途**：细粒度权限定义，采用 `{resource}:{action}` 格式。

| 字段名 | 类型 | 约束 | 说明 |
|--------|------|------|------|
| `id` | UUID | PK, 索引 | 主键，自动生成 |
| `resource` | VARCHAR(64) | NOT NULL | 资源名，如 `chat`、`skills.user`、`settings` |
| `action` | VARCHAR(64) | NOT NULL | 操作名，如 `use`、`read`、`write` |
| `description` | TEXT | nullable | 权限说明 |

**唯一约束**：`(resource, action)` 联合唯一

**当前全部权限枚举**：

| 权限标识 | 资源 | 操作 | 说明 |
|---------|------|------|------|
| `chat:use` | chat | use | 使用对话功能 |
| `skills.user:read` | skills.user | read | 读取用户自定义技能 |
| `skills.user:write` | skills.user | write | 创建/更新/删除用户技能 |
| `skills.project:read` | skills.project | read | 读取项目技能 |
| `skills.project:write` | skills.project | write | 创建/更新/删除项目技能（管理员） |
| `skills.system:read` | skills.system | read | 读取系统技能 |
| `settings:read` | settings | read | 读取系统设置（含 MCP 服务器状态） |
| `settings:write` | settings | write | 修改系统设置 |
| `models:read` | models | read | 读取 LLM 模型配置 |
| `models:write` | models | write | 修改 LLM 模型配置 |
| `users:read` | users | read | 读取用户列表 |
| `users:write` | users | write | 创建/更新/删除用户 |
| `users:assign_role` | users | assign_role | 给用户分配角色 |

---

### 4.4 `user_roles` — 用户角色关联表

**文件**：`backend/models/user_role.py`
**用途**：多对多关联，将用户绑定到一个或多个角色，并记录操作审计信息。

| 字段名 | 类型 | 约束 | 说明 |
|--------|------|------|------|
| `user_id` | UUID | PK, FK→users.id, CASCADE | 用户 ID |
| `role_id` | UUID | PK, FK→roles.id, CASCADE | 角色 ID |
| `assigned_at` | TIMESTAMP | NOT NULL | 分配时间 |
| `assigned_by` | UUID | nullable | 执行分配的操作员用户 ID（匿名场景为 null） |

**复合主键**：`(user_id, role_id)`

---

### 4.5 `role_permissions` — 角色权限关联表

**文件**：`backend/models/role_permission.py`
**用途**：多对多关联，将角色绑定到一组权限。

| 字段名 | 类型 | 约束 | 说明 |
|--------|------|------|------|
| `role_id` | UUID | PK, FK→roles.id, CASCADE | 角色 ID |
| `permission_id` | UUID | PK, FK→permissions.id, CASCADE | 权限 ID |

**复合主键**：`(role_id, permission_id)`

---

### 4.6 `refresh_tokens` — 刷新令牌表

**文件**：`backend/models/refresh_token.py`
**用途**：存储 JWT 刷新令牌的 JTI（JWT ID），用于令牌轮换和吊销检测。access_token 本身不存库，refresh_token 以 httpOnly Cookie 形式存储在浏览器。

| 字段名 | 类型 | 约束 | 说明 |
|--------|------|------|------|
| `id` | UUID | PK | 主键，自动生成 |
| `jti` | VARCHAR(64) | UNIQUE, 索引 | JWT ID，全局唯一标识一个 refresh_token |
| `user_id` | UUID | NOT NULL, 索引, FK→users.id, CASCADE | 所属用户 |
| `expires_at` | TIMESTAMP | NOT NULL | 过期时间 |
| `revoked` | BOOLEAN | default=FALSE | 是否已吊销（主动登出 / 被顶替） |
| `created_at` | TIMESTAMP | NOT NULL | 签发时间 |

**令牌轮换策略**：每次 `POST /auth/refresh` 消费旧 JTI → 签发新 JTI，旧 JTI 标记 `revoked=true`。有效期默认 14 天（`JWT_TOKEN_EXPIRE_DAYS`）。

---

## 5. 对话模块

### 5.1 `conversations` — 对话表

**文件**：`backend/models/conversation.py`
**用途**：一个对话代表用户与 AI 的一次完整交互会话，可包含多条消息，可归属于某个分组。

| 字段名 | 类型 | 约束 | 说明 |
|--------|------|------|------|
| `id` | UUID | PK, 索引 | 主键 |
| `title` | VARCHAR(500) | NOT NULL | 对话标题（通常取首条消息前 50 字） |
| `description` | TEXT | nullable | 对话摘要说明 |
| `current_model` | VARCHAR(50) | default='claude' | 当前使用的 LLM 模型标识 |
| `model_history` | JSONB | nullable | 历史模型切换记录：`[{model, changed_at}]` |
| `status` | VARCHAR(20) | default='active' | 状态：`active` \| `archived` \| `deleted` |
| `is_pinned` | BOOLEAN | default=FALSE | 是否置顶 |
| `group_id` | UUID | nullable, 索引, FK→conversation_groups.id, SET NULL | 所属分组，删除分组时置 null |
| `message_count` | INTEGER | default=0 | 消息数（冗余计数，加速列表展示） |
| `total_tokens` | INTEGER | default=0 | 累计消耗 token 数 |
| `extra_metadata` | JSONB | nullable | 扩展字段，包含 `system_prompt`、续接状态等 |
| `tags` | JSONB | nullable | 标签列表 `["数据分析", "ETL"]` |
| `created_at` | TIMESTAMP | NOT NULL, 索引 | 创建时间 |
| `updated_at` | TIMESTAMP | NOT NULL, 索引 | 最后更新时间（有新消息时自动更新） |
| `last_message_at` | TIMESTAMP | nullable | 最后一条消息时间（用于前端排序） |

**属性方法**：
- `system_prompt`：`extra_metadata['system_prompt']` 的 getter/setter 快捷访问

**索引**：
- `idx_conversations_status`
- `idx_conversations_created_at`
- `idx_conversations_updated_at`
- `idx_conversations_group_id`

---

### 5.2 `messages` — 消息表

**文件**：`backend/models/conversation.py`
**用途**：记录每一轮对话的消息，包括用户输入、AI 回复和工具调用结果。是上下文重建的原始数据来源。

| 字段名 | 类型 | 约束 | 说明 |
|--------|------|------|------|
| `id` | UUID | PK, 索引 | 主键 |
| `conversation_id` | UUID | NOT NULL, 索引, FK→conversations.id | 所属对话 |
| `role` | VARCHAR(20) | NOT NULL | 消息角色：`user` \| `assistant` \| `system` |
| `content` | TEXT | NOT NULL | 消息文本内容 |
| `model` | VARCHAR(50) | nullable | 生成此消息时使用的模型 |
| `model_params` | JSONB | nullable | 模型参数快照：`{temperature, max_tokens}` |
| `prompt_tokens` | INTEGER | default=0 | 输入消耗 token 数 |
| `completion_tokens` | INTEGER | default=0 | 输出消耗 token 数 |
| `total_tokens` | INTEGER | default=0 | 合计 token 数 |
| `artifacts` | JSONB | nullable | 产出物：SQL 脚本、图表配置、文件路径等 |
| `tool_calls` | JSONB | nullable | 工具调用记录（MCP 工具调用序列） |
| `tool_results` | JSONB | nullable | 工具执行结果（对应 tool_calls 的返回值） |
| `extra_metadata` | JSONB | nullable | 扩展字段，包含 `thinking_events`（推理过程）等 |
| `created_at` | TIMESTAMP | NOT NULL, 索引 | 消息创建时间 |

**thinking_events 说明**：
Claude 推理过程（Extended Thinking）以 `extra_metadata['thinking_events']` 存储，格式：
```json
[{"type": "thinking", "thinking": "推理文本内容"}]
```
前端 `ThoughtProcess` 组件读取此字段渲染折叠思考面板。

**索引**：
- `idx_messages_conversation_id`
- `idx_messages_role`
- `idx_messages_created_at`

---

### 5.3 `context_snapshots` — 上下文快照表

**文件**：`backend/models/conversation.py`
**用途**：当对话轮次过多导致 LLM context window 接近上限时，将历史消息压缩为摘要快照，保留关键信息同时释放 context 空间。

| 字段名 | 类型 | 约束 | 说明 |
|--------|------|------|------|
| `id` | UUID | PK, 索引 | 主键 |
| `conversation_id` | UUID | NOT NULL, 索引, FK→conversations.id | 所属对话 |
| `snapshot_type` | VARCHAR(20) | NOT NULL | 快照类型：`full`（完整备份） \| `compressed`（压缩） \| `summary`（纯摘要） |
| `message_count` | INTEGER | default=0 | 本次压缩涵盖的消息数量 |
| `start_message_id` | UUID | nullable | 压缩起始消息 ID |
| `end_message_id` | UUID | nullable | 压缩结束消息 ID |
| `content` | JSONB | NOT NULL | 快照内容（压缩后的消息列表或结构化摘要） |
| `summary` | TEXT | nullable | 人类可读摘要文本 |
| `key_facts` | JSONB | nullable | 关键事实列表（LLM 提取），用于重建上下文 |
| `artifacts` | JSONB | nullable | 对话中产生的产出物（SQL、文件路径等） |
| `extra_metadata` | JSONB | nullable | 扩展字段 |
| `created_at` | TIMESTAMP | NOT NULL, 索引 | 快照创建时间 |

**触发机制**：
- `ConversationSummarizer`（`backend/core/conversation_summarizer.py`）在消息数超过阈值时自动触发
- `AgenticLoop` 内部 `_compress_loop_messages()` 在单次 agent 循环消息超 `MAX_LOOP_CONTEXT_CHARS=60000` 时触发

---

### 5.4 `conversation_groups` — 对话分组表

**文件**：`backend/models/conversation_group.py`
**用途**：将对话归类到"文件夹"，便于用户分项目或业务场景管理历史对话。

| 字段名 | 类型 | 约束 | 说明 |
|--------|------|------|------|
| `id` | UUID | PK | 主键 |
| `name` | VARCHAR(100) | NOT NULL | 分组名称 |
| `description` | TEXT | nullable | 分组说明 |
| `icon` | VARCHAR(50) | nullable | 图标（emoji 或图标名） |
| `color` | VARCHAR(20) | nullable | 颜色标识（用于前端区分） |
| `sort_order` | INTEGER | default=0 | 排序权重，值越小越靠前 |
| `is_expanded` | BOOLEAN | default=TRUE | 前端展开/折叠状态 |
| `conversation_count` | INTEGER | default=0 | 分组下对话数（冗余字段，加速展示） |
| `created_at` | TIMESTAMP | NOT NULL | 创建时间 |
| `updated_at` | TIMESTAMP | NOT NULL | 最后更新时间 |

**索引**：
- `idx_conversation_groups_sort_order`
- `idx_conversation_groups_name`
- `idx_conversation_groups_created_at`

---

## 6. 任务模块

### 6.1 `tasks` — 任务表

**文件**：`backend/models/task.py`
**用途**：记录 Agent 执行的长时任务（ETL 设计、SQL 生成、数据分析等）的状态和进度，支持前端轮询进度展示。

| 字段名 | 类型 | 约束 | 说明 |
|--------|------|------|------|
| `id` | UUID | PK, 索引 | 主键 |
| `conversation_id` | UUID | nullable, 索引, FK→conversations.id | 关联对话（任务可独立于对话存在） |
| `name` | VARCHAR(200) | NOT NULL | 任务名称 |
| `description` | TEXT | nullable | 任务描述 |
| `task_type` | ENUM | NOT NULL | 任务类型（见下方枚举） |
| `status` | ENUM | default='pending' | 当前状态（见下方枚举） |
| `priority` | INTEGER | default=0 | 优先级 0-10，10 最高 |
| `config` | JSONB | nullable | 任务配置参数 |
| `input_data` | JSONB | nullable | 输入数据 |
| `started_at` | TIMESTAMP | nullable | 任务开始时间 |
| `completed_at` | TIMESTAMP | nullable | 任务完成时间 |
| `execution_time` | INTEGER | nullable | 执行耗时（秒） |
| `result` | JSONB | nullable | 执行结果 |
| `output_files` | JSONB | nullable | 输出文件路径列表 |
| `error_message` | TEXT | nullable | 错误信息（失败时） |
| `error_trace` | TEXT | nullable | 完整堆栈信息（调试用） |
| `progress` | INTEGER | default=0 | 进度百分比 0-100 |
| `current_step` | VARCHAR(100) | nullable | 当前执行步骤描述 |
| `total_steps` | INTEGER | nullable | 总步骤数 |
| `processed_rows` | INTEGER | default=0 | 已处理行数 |
| `total_rows` | INTEGER | nullable | 总行数 |
| `extra_metadata` | JSONB | nullable | 扩展字段 |
| `tags` | JSONB | nullable | 标签 |
| `created_at` | TIMESTAMP | NOT NULL, 索引 | 创建时间 |
| `updated_at` | TIMESTAMP | NOT NULL, 索引 | 最后更新时间 |

**`task_type` 枚举值**：

| 值 | 说明 |
|----|------|
| `data_export` | 数据导出 |
| `etl_design` | ETL 流程设计 |
| `sql_generation` | SQL 脚本生成 |
| `data_analysis` | 数据分析 |
| `report_creation` | 报表创建 |
| `database_connection` | 数据库连接测试 |
| `file_analysis` | 文件分析 |
| `custom` | 自定义任务 |

**`status` 枚举值**：

| 值 | 说明 |
|----|------|
| `pending` | 等待执行 |
| `running` | 执行中 |
| `completed` | 执行成功 |
| `failed` | 执行失败 |
| `cancelled` | 已取消 |
| `paused` | 已暂停（等待用户审批） |

**方法**：
- `update_progress(progress, current_step)`：更新进度
- `start()`：标记为 `running`，记录 `started_at`
- `complete(result, output_files)`：标记为 `completed`，计算耗时
- `fail(error_message, error_trace)`：标记为 `failed`

---

### 6.2 `task_history` — 任务历史表

**文件**：`backend/models/task.py`
**用途**：记录任务状态变更的完整审计日志，便于排查任务失败原因。

| 字段名 | 类型 | 约束 | 说明 |
|--------|------|------|------|
| `id` | UUID | PK, 索引 | 主键 |
| `task_id` | UUID | NOT NULL, 索引 | 关联任务 ID |
| `event_type` | VARCHAR(50) | NOT NULL | 事件类型：`created` \| `started` \| `progress` \| `completed` \| `failed` \| `cancelled` |
| `event_data` | JSONB | nullable | 事件附加数据（进度信息、结果摘要等） |
| `old_status` | VARCHAR(20) | nullable | 变更前状态 |
| `new_status` | VARCHAR(20) | nullable | 变更后状态 |
| `message` | TEXT | nullable | 事件说明文本 |
| `created_at` | TIMESTAMP | NOT NULL, 索引 | 事件发生时间 |

---

## 7. 报表模块

### 7.1 `reports` — 报表表

**文件**：`backend/models/report.py`
**用途**：存储可视化报表的配置信息，包括数据源、布局、图表和分享设置。

| 字段名 | 类型 | 约束 | 说明 |
|--------|------|------|------|
| `id` | UUID | PK, 索引 | 主键 |
| `conversation_id` | UUID | nullable, 索引 | 来源对话 ID（报表可从对话生成） |
| `name` | VARCHAR(200) | NOT NULL | 报表名称 |
| `description` | TEXT | nullable | 报表描述 |
| `report_type` | ENUM | NOT NULL | 报表类型：`dashboard` \| `single_chart` \| `table` \| `pivot_table` \| `custom` |
| `data_sources` | JSONB | nullable | 数据源配置数组（见下方格式说明） |
| `layout` | JSONB | nullable | 网格布局配置 |
| `charts` | JSONB | nullable | 图表配置数组 |
| `filters` | JSONB | nullable | 全局过滤器配置 |
| `theme` | VARCHAR(50) | default='light' | 主题：`light` \| `dark` \| `custom` |
| `style_config` | JSONB | nullable | 自定义样式配置 |
| `share_scope` | ENUM | NOT NULL | 分享范围：`private` \| `team` \| `public` \| `custom` |
| `allowed_users` | JSONB | nullable | 允许访问的用户 ID 列表 |
| `allowed_teams` | JSONB | nullable | 允许访问的团队 ID 列表 |
| `auto_refresh` | BOOLEAN | default=FALSE | 是否启用自动刷新 |
| `refresh_interval` | INTEGER | nullable | 刷新间隔（秒） |
| `view_count` | INTEGER | default=0 | 浏览次数 |
| `last_viewed_at` | TIMESTAMP | nullable | 最后浏览时间 |
| `cache_enabled` | BOOLEAN | default=TRUE | 是否启用数据缓存 |
| `cache_ttl` | INTEGER | default=300 | 缓存有效期（秒） |
| `tags` | JSONB | nullable | 标签 |
| `extra_metadata` | JSONB | nullable | 扩展字段 |
| `created_at` | TIMESTAMP | NOT NULL, 索引 | 创建时间 |
| `updated_at` | TIMESTAMP | NOT NULL, 索引 | 最后更新时间 |

**`data_sources` 元素格式**：
```json
{
  "id": "ds1",
  "type": "clickhouse",
  "env": "idn",
  "database": "crm",
  "query": "SELECT call_start_time, count() FROM realtime_dwd_crm_call_record GROUP BY 1",
  "refresh_interval": 300
}
```

**`layout` 格式**：
```json
{
  "type": "grid",
  "columns": 12,
  "rows": 6,
  "items": [
    {"chart_id": "chart-uuid", "x": 0, "y": 0, "w": 6, "h": 3}
  ]
}
```

---

### 7.2 `charts` — 图表表

**文件**：`backend/models/report.py`
**用途**：存储单个图表的配置和缓存数据，既可归属于报表，也可作为独立图表存在。

| 字段名 | 类型 | 约束 | 说明 |
|--------|------|------|------|
| `id` | UUID | PK, 索引 | 主键 |
| `report_id` | UUID | nullable, 索引, FK→reports.id | 所属报表（独立图表时为 null） |
| `name` | VARCHAR(200) | NOT NULL | 图表名称 |
| `description` | TEXT | nullable | 图表描述 |
| `chart_type` | ENUM | NOT NULL | 图表类型（见下方枚举） |
| `data_source_id` | VARCHAR(100) | nullable | 引用 report.data_sources 中的 id |
| `query` | TEXT | nullable | 数据查询 SQL |
| `data_config` | JSONB | nullable | 字段映射、聚合方式配置 |
| `chart_config` | JSONB | nullable | 图表渲染配置（ECharts / G2 格式） |
| `interactions` | JSONB | nullable | 交互配置：tooltip、缩放、钻取 |
| `width` | INTEGER | nullable | 宽度（像素） |
| `height` | INTEGER | nullable | 高度（像素） |
| `style` | JSONB | nullable | 自定义样式 |
| `cached_data` | JSONB | nullable | 缓存的查询结果数据 |
| `cache_expires_at` | TIMESTAMP | nullable | 缓存过期时间 |
| `created_at` | TIMESTAMP | NOT NULL, 索引 | 创建时间 |
| `updated_at` | TIMESTAMP | NOT NULL, 索引 | 最后更新时间 |

**`chart_type` 枚举值**：
`line` \| `bar` \| `pie` \| `scatter` \| `area` \| `heatmap` \| `funnel` \| `gauge` \| `radar` \| `treemap` \| `sankey` \| `table`

**`data_config` 格式**：
```json
{
  "x_field": "call_start_time",
  "y_field": "connect_count",
  "series_field": "call_mode",
  "aggregate": "sum",
  "sort": {"field": "call_start_time", "order": "asc"}
}
```

**方法**：
- `is_cache_valid() → bool`：判断缓存是否仍有效
- `update_cache(data, ttl)`：更新缓存数据和过期时间

---

## 8. 模型配置模块

### 8.1 `llm_configs` — LLM 配置表

**文件**：`backend/models/llm_config.py`
**用途**：存储各 LLM 提供商的接入配置（API Key、模型版本、参数），支持通过前端管理页面动态切换。

| 字段名 | 类型 | 约束 | 说明 |
|--------|------|------|------|
| `id` | UUID | PK, 索引 | 主键 |
| `model_key` | VARCHAR(50) | UNIQUE, 索引 | 模型标识：`claude` \| `gemini` \| `qianwen` \| `doubao` |
| `model_name` | VARCHAR(100) | nullable | 模型显示名称 |
| `model_type` | VARCHAR(50) | nullable | 适配器类型（与 ModelAdapterFactory 对应） |
| `api_base_url` | VARCHAR(500) | nullable | API 端点 URL |
| `api_key` | TEXT | nullable | API 密钥（接口返回时自动脱敏） |
| `api_secret` | TEXT | nullable | 部分模型需要的 API Secret |
| `default_model` | VARCHAR(100) | nullable | 具体模型版本，如 `claude-sonnet-4-6` |
| `temperature` | VARCHAR(10) | default='0.7' | 温度参数 |
| `max_tokens` | VARCHAR(10) | default='8192' | 最大 token 数 |
| `extra_config` | JSONB | nullable | 扩展配置：`{supports_streaming, supports_tools, supports_vision}` |
| `is_enabled` | BOOLEAN | default=TRUE | 是否启用 |
| `is_default` | BOOLEAN | default=FALSE | 是否为默认模型 |
| `description` | TEXT | nullable | 模型描述 |
| `icon` | VARCHAR(200) | nullable | 图标 URL 或 emoji |
| `created_at` | TIMESTAMP | NOT NULL | 创建时间 |
| `updated_at` | TIMESTAMP | NOT NULL | 最后更新时间 |

**预置配置（初始化时写入）**：

| `model_key` | 提供商 | 默认启用 |
|-------------|--------|---------|
| `claude` | Anthropic | ✅ 是 |
| `gemini` | Google | ❌ 否 |
| `qianwen` | 阿里云 | ❌ 否 |
| `doubao` | 字节跳动 | ❌ 否 |

**安全说明**：`api_key` 读取时通过 `_mask_api_key()` 脱敏（保留前 8 位后面替换为 `***`），完整数据通过 `to_dict_with_secrets()` 方法获取（仅内部使用）。

---

## 9. 全局设计规范

### 9.1 主键规范

所有表均使用 PostgreSQL UUID 作为主键：

```python
id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
```

- **原因**：UUID 在分布式场景下无冲突，且不暴露业务数据行数等信息
- **生成**：`uuid.uuid4()` 在 Python 层生成，不依赖数据库序列

### 9.2 时间戳规范

所有表的时间字段统一使用 UTC 时间：

```python
created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
```

### 9.3 JSONB 字段使用原则

| 使用场景 | 字段示例 | 说明 |
|---------|---------|------|
| 扩展元数据 | `extra_metadata` | 各表通用扩展字段，不影响主结构 |
| 动态配置 | `config`、`extra_config` | 每种任务/模型参数不同，不做列定义 |
| 历史记录 | `model_history`、`tool_calls` | 数组/嵌套结构，频繁追加 |
| 缓存数据 | `cached_data`、`key_facts` | 不参与关系查询，无需列级索引 |

### 9.4 索引策略

| 索引类型 | 适用场景 | 示例 |
|---------|---------|------|
| 主键索引 | 所有表，自动创建 | `id` |
| 唯一索引 | 业务唯一键 | `users.username`、`roles.name`、`llm_configs.model_key` |
| 外键索引 | JOIN 和过滤操作 | `messages.conversation_id`、`user_roles.user_id` |
| 状态/类型索引 | 高频过滤列 | `tasks.status`、`tasks.task_type`、`conversations.status` |
| 时间索引 | 列表排序 | `conversations.created_at`、`messages.created_at` |

### 9.5 级联删除策略

| 场景 | 策略 | 示例 |
|------|------|------|
| 强依赖（子无意义） | CASCADE DELETE | 删除对话 → 消息全删；删除用户 → 角色绑定全删 |
| 弱依赖（子可独立） | SET NULL | 删除分组 → 对话 group_id 置 null（对话保留） |

### 9.6 枚举字段

任务状态、任务类型、报表类型等使用 SQLAlchemy `Enum` 类型而非 VARCHAR，在数据库层面强制约束合法值：

```python
from sqlalchemy import Enum
status = Column(Enum('pending', 'running', 'completed', 'failed', 'cancelled', 'paused', name='taskstatus'))
```

---

## 10. 初始化与迁移

### 10.1 首次初始化

```bash
# 1. 创建数据库（PostgreSQL）
createdb -U postgres data_agent

# 2. 初始化表结构 + 预置数据（角色、权限、超级管理员）
cd data-agent
python backend/scripts/init_db.py

# 3. 验证
python -c "from backend.config.database import test_postgres_connection; print(test_postgres_connection())"
```

`init_db.py` 执行流程：
1. `Base.metadata.create_all(engine)` — 创建所有表
2. `init_rbac.py` — 写入 4 个系统角色 + 全部权限 + 超级管理员账号
3. 写入 LLM 配置预置数据（claude 等）

### 10.2 字段变更迁移

```bash
# 自动生成迁移脚本
alembic revision --autogenerate -m "add xxx field to yyy table"

# 应用迁移
alembic upgrade head

# 查看当前版本
alembic current

# 回滚
alembic downgrade -1
```

### 10.3 手动迁移脚本示例

`backend/migrations/add_conversation_groups.py` 是项目中的手动迁移示例，直接运行：

```bash
python backend/migrations/add_conversation_groups.py
```

### 10.4 RBAC 初始化

```bash
# 重新初始化角色和权限（不影响用户数据）
python backend/scripts/init_rbac.py
```

---

## 11. 业务数据说明（ClickHouse）

本文档描述的 PostgreSQL 数据库**不存储**业务数据。业务数据存储在独立的 ClickHouse 实例中：

| ClickHouse 数据库 | 连接标识 | 主要表 |
|-----------------|---------|--------|
| `crm` | 由 `CLICKHOUSE_*` 环境变量配置 | `realtime_dwd_crm_call_record`（呼叫记录宽表，78 字段）<br>`dim_automatic_task`（外呼策略）<br>`dim_call_task`（外呼任务）<br>等 |

**ClickHouse 连接配置**（`.env`）：

```bash
# 主区域（示例）
CLICKHOUSE_HOST=<host>
CLICKHOUSE_PORT=9000
CLICKHOUSE_DATABASE=crm
CLICKHOUSE_USER=<user>
CLICKHOUSE_PASSWORD=<password>

# 只读账号（分析 Agent 使用）
CLICKHOUSE_READONLY_USER=<readonly_user>
CLICKHOUSE_READONLY_PASSWORD=<readonly_password>
```

业务表的详细字段说明、指标口径和查询规范，请参阅：
- `customer_data/db_knowledge/_index.md` — 数据库知识库全局索引
- `customer_data/db_knowledge/tables/` — 各表详细字段文档
- `customer_data/db_knowledge/metrics/` — 核心指标口径定义

---

*文档版本：v1.0 · 2026-03-18*
*关联文档：[数据库迁移指南](database_migration.md) · [系统架构文档](ARCHITECTURE.md)*
