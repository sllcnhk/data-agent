# 数据库迁移指南

> **最后更新**：2026-04-14（**对话报表手动固定 Pin**：**无 DB 迁移**，复用已有 `reports` 表 + `messages.extra_metadata` JSONB；2026-04-13：**数据管理中心 v1**：`migrate_datacenter_v1.py` 新增 3 张表（`scheduled_reports`/`schedule_run_logs`/`notification_logs`）+ `reports` 表新增 3 列（`doc_type`/`scheduled_report_id`/`version_seq`）；**多图表 HTML 报告生成**：`migrate_reports_enhancement.py` 新增 `reports` 表 5 列；`migrate_reports_permissions.py` 种子 3 条权限（`reports:read/create/delete`）；2026-04-08：**Skill 用户使用权限隔离 T1–T6**：**无 DB 迁移**；2026-04-07：**SQL→Excel 数据导出**：`migrate_data_export.py`；2026-04-05：**Excel 数据导入**：`migrate_data_import.py`；其余历次变更见下方记录表）

本文档说明数据库迁移的两种方式：
1. **项目自有手动迁移脚本**（`backend/migrations/`）— 当前主要方式，支持 up/down
2. **Alembic 参考指南**（备用，供复杂迁移场景使用）

---

## 已执行迁移记录

| 迁移脚本 | 日期 | 说明 | 状态 |
|---------|------|------|------|
| `add_conversation_groups.py` | 2026-03-01 | 新增对话分组功能（`conversation_groups` 表 + `conversations.group_id` 外键）| ✅ 已执行 |
| `add_user_last_active_at.py` | 2026-03-19 | 新增用户活动追踪字段（`users.last_active_at`），用于 Session 空闲超时检测 | ✅ 已执行 |
| 对话附件上传功能 | 2026-03-23 | **无迁移脚本**，附件元数据（`{name,mime_type,size}`）写入已有的 `messages.extra_metadata` JSONB 列，无需结构变更 | ✅ 无需执行 |
| 对话打断功能 | 2026-03-19 | **无迁移脚本**，打断标记（`cancelled: true`）写入已有的 `messages.extra_metadata` JSONB 列，无需结构变更 | ✅ 无需执行 |
| 用户技能目录隔离修复 | 2026-03-19 | **无迁移脚本**，`ENABLE_AUTH=true` 时 `_get_user_skill_dir()` 改为写 `user/{username}/` 子目录；文件系统层面的改变，不涉及任何数据库结构变更 | ✅ 无需执行 |
| FilesystemPermissionProxy Fix-1~4 | 2026-03-23 | **无迁移脚本**，纯代码层安全增强：Fix-1（LLM路径模板注入）/ Fix-2（跨根反向写入拦截）/ Fix-3（错误消息改进）/ Fix-4（username子目录深度校验）均为逻辑层变更，无数据库结构变更 | ✅ 无需执行 |
| 文件系统路径可移植性 | 2026-03-23 | **无迁移脚本**，`settings.py` 新增 `_PROJECT_ROOT` 常量和 `_resolve_fs_paths` validator；`.env` 改为相对路径格式；纯配置层和启动逻辑变更，无数据库结构变更 | ✅ 无需执行 |
| customer_data 用户数据隔离 | 2026-03-24 | **文件系统迁移脚本**（非 DB 迁移）：`backend/scripts/migrate_customer_data.py` 将 `customer_data/customer_data/**` 和 `customer_data/reports/` 迁移到 `customer_data/superadmin/`，将误存的 `customer_data/.claude/skills/user/*.md` 迁移到 `.claude/skills/user/superadmin/`；冲突时保留较新文件，旧文件存为 `.bak`。新增目录结构 `customer_data/{username}/`，`agentic_loop.py` 和 `analyst_agent.py` 路径注入同步更新。**无数据库结构变更**。 | ✅ 已执行（历史数据已迁移至 superadmin/；新用户数据自动存入各自子目录） |
| 对话用户隔离 | 2026-03-24 | **DB 迁移脚本**：`backend/scripts/migrate_conversation_user_isolation.py`。① `conversations` 表加 `user_id UUID REFERENCES users(id) ON DELETE SET NULL`；② `conversation_groups` 表同上；③ 存量数据 `user_id` 归属 superadmin（`UPDATE ... WHERE user_id IS NULL`）；④ 建索引 `idx_conversations_user_id` / `idx_conversation_groups_user_id`。迁移后每条对话/分组均带 `user_id`，API 层据此实现用户隔离。 | ✅ 已执行 |
| is_shared 字段（群组框架） | 2026-03-25 | **DB 迁移脚本**：`backend/scripts/migrate_add_is_shared.py`。`conversations` 表加 `is_shared BOOLEAN NOT NULL DEFAULT FALSE`（群组聊天预留扩展点）；脚本幂等（检查列存在后跳过）。所有存量对话默认 `is_shared=false`，不影响现有功能。 | ✅ 已执行 |
| 技能路由可视化（T1-T6） | 2026-03-26 | **无 DB 迁移**。变更均为代码层：`SkillLoader._last_match_info`、`skill_matched` SSE 事件、ThoughtProcess 🧠 面板、`GET /skills/load-errors` API。推理事件经由已有 `messages.extra_metadata['thinking_events']` JSONB 路径持久化，无需结构变更。 | ✅ 无需执行 |
| 文件写入下载（T1-T7） | 2026-03-26 | **无 DB 迁移**。Agent 写文件时生成的文件路径元数据（`[{path,name,size,mime_type}]`）写入已有 `messages.extra_metadata["files_written"]` JSONB 列，无需结构变更。文件实体存储于 `customer_data/{username}/` 目录（文件系统层面）。新增 `backend/api/files.py` 下载端点与 `FILE_OUTPUT_DATE_SUBFOLDER` 配置项均为代码/配置层变更。 | ✅ 无需执行 |
| Excel 数据导入 | 2026-04-05 | **DB 迁移脚本**：`backend/scripts/migrate_data_import.py`。① 新建 `import_jobs` 表（UUID PK、状态机字段、进度追踪字段、错误信息、JSONB 配置快照）；② 插入 `data:import` 权限记录；③ 将 `data:import` 权限分配给 superadmin 角色。幂等（`CREATE TABLE IF NOT EXISTS` + `INSERT ... ON CONFLICT DO NOTHING`）。 | ✅ 已执行 |
| SQL→Excel 数据导出 | 2026-04-07 | **DB 迁移脚本**：`backend/scripts/migrate_data_export.py`。① 新建 `export_jobs` 表（UUID PK、状态机字段、行级/批次/Sheet 三层进度字段、输出文件路径/大小、JSONB 配置快照）；② 插入 `data:export` 权限记录；③ 将 `data:export` 权限分配给 superadmin 角色。幂等（`CREATE TABLE IF NOT EXISTS` + `INSERT ... ON CONFLICT DO NOTHING`）。 | ✅ 已执行 |
| 多图表 HTML 报告增强 | 2026-04-13 | **DB 迁移脚本**：`backend/scripts/migrate_reports_enhancement.py`。`reports` 表新增 5 列：`refresh_token VARCHAR(64)`（公开刷新令牌，SHA-256 随机值）、`export_job_id VARCHAR(64)`（关联导出任务）、`export_format VARCHAR(10)`、`llm_summary TEXT`（LLM 生成摘要）、`is_summarized BOOLEAN DEFAULT FALSE`（摘要生成状态）。幂等（`ALTER TABLE ... ADD COLUMN IF NOT EXISTS`）。 | ✅ 已执行 |
| 多图表报告权限 | 2026-04-13 | **DB 迁移脚本**：`backend/scripts/migrate_reports_permissions.py`。① 插入 3 条权限记录：`reports:read`（查看报告列表）、`reports:create`（生成报告）、`reports:delete`（删除报告）；② 将 `reports:read` + `reports:create` 分配给 analyst 角色；③ 将全部 3 条分配给 admin 角色；④ 将全部 3 条分配给 superadmin 角色。幂等（`INSERT ... ON CONFLICT DO NOTHING`）。 | ✅ 已执行 |
| Skill 用户使用权限隔离 T1–T6 | 2026-04-08 | **无 DB 迁移**。变更均为代码层：`SkillMD.owner` 字段（filepath 解析）、`_get_visible_user_skills(username)` 方法、`build_skill_prompt_async(user_id=)` 参数、`_expand_sub_skills(user_id=)` 跨用户防护、Preview API `effective_user_id` 绑定及 `get_match_details(username=)` bug 修复。文件系统结构不变（仍为 `user/{username}/`）；如有遗留的 `user/*.md`（无 username 子目录），`init_rbac.py` 中的 `_migrate_user_skills_to_superadmin()` 可一次性迁移至 `user/superadmin/`（该函数在执行 `init_rbac.py` 时自动调用）。 | ✅ 无需执行 |
| 数据管理中心 v1 | 2026-04-13 | **DB 迁移脚本**：`backend/scripts/migrate_datacenter_v1.py`。① `reports` 表新增 3 列：`doc_type VARCHAR(20) DEFAULT 'dashboard'`（报告类型：dashboard/document）、`scheduled_report_id UUID`（关联定时任务）、`version_seq INTEGER DEFAULT 1`（版本序号）；② 新建 `scheduled_reports` 表（UUID PK、owner_username、name、doc_type、cron_expr、timezone、report_spec JSONB、notify_channels JSONB、is_active 等）；③ 新建 `schedule_run_logs` 表（UUID PK、scheduled_report_id FK、status、error_msg、duration_sec、notify_summary JSONB 等）；④ 新建 `notification_logs` 表。幂等（`ALTER TABLE ... ADD COLUMN IF NOT EXISTS` + `checkfirst=True`）。 | ✅ 已执行 |
| 对话报表手动固定（Pin） | 2026-04-14 | **无迁移脚本**。新增 `POST /reports/pin` 端点：用户点击「生成固定报表/报告」后按需创建 Report DB 记录（幂等，复用已有 `reports` 表的 `report_file_path` / `doc_type` / `refresh_token` 字段）；pin 后将 `pinned_report_id` + `refresh_token` 回写到 `messages.extra_metadata["files_written"][i]`（复用已有 JSONB 列，`flag_modified` 确保 SQLAlchemy 检测变更）；`_detect_report_type()` 在 `agentic_loop.py` 以模块级函数暴露，仅为代码层重构，无数据库结构变更。所需 RBAC 权限（`reports:create`）已在 `migrate_reports_permissions.py` 中种子，无需补录。 | ✅ 无需执行 |

---

## 项目迁移脚本（`backend/migrations/`）

项目使用独立 Python 脚本管理结构变更（非 Alembic autogenerate），支持 `up`/`down` 两个操作。

### 执行方式

```bash
# Windows + Anaconda 环境（需设置 PYTHONPATH）
set PYTHONPATH=C:\Users\shiguangping\data-agent && \
  D:\ProgramData\Anaconda3\envs\dataagent\python.exe \
  backend/migrations/<script_name>.py up

# Linux/Mac
PYTHONPATH=/path/to/data-agent python backend/migrations/<script_name>.py up

# 回滚
PYTHONPATH=... python backend/migrations/<script_name>.py down
```

### add_user_last_active_at.py（2026-03-19）

**目的**：为 Session 空闲超时检测新增 `users.last_active_at` 字段。

每次经认证的 API 请求（`get_current_user`）会节流更新此字段（最多每 5 分钟写一次 DB）。`/auth/refresh` 端点使用此字段检测是否超过 `SESSION_IDLE_TIMEOUT_MINUTES`，超时则拒绝续期。

```sql
-- up
ALTER TABLE users ADD COLUMN IF NOT EXISTS last_active_at TIMESTAMP WITHOUT TIME ZONE;
CREATE INDEX IF NOT EXISTS ix_users_last_active_at ON users (last_active_at);

-- down
DROP INDEX IF EXISTS ix_users_last_active_at;
ALTER TABLE users DROP COLUMN IF EXISTS last_active_at;
```

相关代码：
- `backend/models/user.py` — `last_active_at = Column(DateTime, nullable=True)`
- `backend/api/deps.py` — `_update_last_active()` 后台任务 + `_ACTIVITY_THROTTLE_SEC=300`
- `backend/api/auth.py` — `/auth/refresh` 空闲超时检测逻辑
- `backend/config/settings.py` — `session_idle_timeout_minutes` 字段（默认 120 分钟）

---

## 一次性脚本（`backend/scripts/`）

### migrate_conversation_user_isolation.py（2026-03-24）

**目的**：为对话用户隔离功能添加 `user_id` 字段，实现多用户环境下对话/分组的数据隔离。

```bash
# 预览（不执行）
d:\ProgramData\Anaconda3\envs\dataagent\python.exe \
  backend/scripts/migrate_conversation_user_isolation.py --dry-run

# 正式执行
d:\ProgramData\Anaconda3\envs\dataagent\python.exe \
  backend/scripts/migrate_conversation_user_isolation.py
```

```sql
-- Step 1: conversations 表加 user_id 列
ALTER TABLE conversations
  ADD COLUMN user_id UUID REFERENCES users(id) ON DELETE SET NULL;

-- Step 2: conversation_groups 表加 user_id 列
ALTER TABLE conversation_groups
  ADD COLUMN user_id UUID REFERENCES users(id) ON DELETE SET NULL;

-- Step 3: 将存量数据归属 superadmin
UPDATE conversations       SET user_id = '<superadmin-uuid>' WHERE user_id IS NULL;
UPDATE conversation_groups SET user_id = '<superadmin-uuid>' WHERE user_id IS NULL;

-- Step 4: 建索引
CREATE INDEX idx_conversations_user_id       ON conversations(user_id);
CREATE INDEX idx_conversation_groups_user_id ON conversation_groups(user_id);
```

相关代码：
- `backend/models/conversation.py` — `user_id = Column(UUID, ForeignKey("users.id"), nullable=True)`
- `backend/models/conversation_group.py` — 同上
- `backend/services/conversation_service.py` — `create_conversation(user_id=)` / `list_conversations(user_id=)` / `list_all_conversations_by_user(exclude_user_id=)`
- `backend/api/conversations.py` — 所有 CRUD 端点加 `Depends(get_current_user)` + `_check_conversation_ownership()`；新增 `GET /conversations/all-users-view`（superadmin 专用）
- `backend/api/groups.py` — 同上（分组 CRUD）

---

### migrate_add_is_shared.py（2026-03-25）

**目的**：为侧边栏只读模式 + 群组聊天预留框架，在 `conversations` 表新增 `is_shared` 字段。

```bash
# 执行迁移
d:\ProgramData\Anaconda3\envs\dataagent\python.exe \
  backend/scripts/migrate_add_is_shared.py
```

```sql
-- 执行内容（幂等：先检查列是否存在，已存在则跳过）
ALTER TABLE conversations
  ADD COLUMN is_shared BOOLEAN NOT NULL DEFAULT FALSE;
```

**行为说明**：
- 所有存量对话默认 `is_shared=false`，行为与之前完全一致
- `is_shared=true` 时，superadmin 对该对话有写权限（`_check_conversation_write_permission()` 中判断）
- 当前版本无前端入口设置 `is_shared`；为群组聊天功能预留扩展点

相关代码：
- `backend/models/conversation.py` — `is_shared = Column(Boolean, default=False)`；`to_dict()` 暴露 `"is_shared": self.is_shared or False`
- `backend/api/conversations.py` — `_check_conversation_write_permission()`（send_message / regenerate / clear 调用）
- `frontend/src/store/useChatStore.ts` — `Conversation` interface 加 `is_shared?: boolean`

---

### migrate_data_import.py（2026-04-05）

**目的**：为 Excel → ClickHouse 数据导入功能创建 `import_jobs` 表，并种子 `data:import` 权限。

```bash
# 执行迁移
d:\ProgramData\Anaconda3\envs\dataagent\python.exe \
  backend/scripts/migrate_data_import.py
```

```sql
-- Step 1: 创建 import_jobs 表
CREATE TABLE IF NOT EXISTS import_jobs (
  id           UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id      VARCHAR(64) NOT NULL,
  username     VARCHAR(128) NOT NULL,
  upload_id    VARCHAR(64)  NOT NULL,
  filename     VARCHAR(256) NOT NULL,
  connection_env VARCHAR(64) NOT NULL,
  status       VARCHAR(32)  NOT NULL DEFAULT 'pending',
  -- 进度字段
  total_sheets  INT NOT NULL DEFAULT 0,
  done_sheets   INT NOT NULL DEFAULT 0,
  current_sheet VARCHAR(256),
  total_rows    BIGINT NOT NULL DEFAULT 0,
  imported_rows BIGINT NOT NULL DEFAULT 0,
  total_batches INT NOT NULL DEFAULT 0,
  done_batches  INT NOT NULL DEFAULT 0,
  -- 错误信息
  error_message TEXT,
  errors        JSONB,
  -- 配置快照（connection_env/batch_size/sheets）
  config_snapshot JSONB,
  -- 时间戳
  created_at   TIMESTAMP WITHOUT TIME ZONE DEFAULT now(),
  started_at   TIMESTAMP WITHOUT TIME ZONE,
  finished_at  TIMESTAMP WITHOUT TIME ZONE,
  updated_at   TIMESTAMP WITHOUT TIME ZONE DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_import_jobs_user_id   ON import_jobs(user_id);
CREATE INDEX IF NOT EXISTS idx_import_jobs_status    ON import_jobs(status);
CREATE INDEX IF NOT EXISTS idx_import_jobs_created_at ON import_jobs(created_at DESC);

-- Step 2: 插入 data:import 权限（幂等）
INSERT INTO permissions (id, resource, action, description)
VALUES (gen_random_uuid(), 'data', 'import', 'Excel 数据导入到 ClickHouse')
ON CONFLICT (resource, action) DO NOTHING;

-- Step 3: 将 data:import 权限分配给 superadmin 角色（幂等）
INSERT INTO role_permissions (role_id, permission_id)
SELECT r.id, p.id
FROM roles r, permissions p
WHERE r.name = 'superadmin' AND p.resource = 'data' AND p.action = 'import'
ON CONFLICT DO NOTHING;
```

**状态机**：`pending` → `running` → `completed` / `failed`；取消路径：`pending`/`running` → `cancelling` → `cancelled`。

**权限范围**：`data:import` 仅 superadmin 角色持有。前端菜单入口：侧边栏「数据导入」，需 `is_superadmin=true`。

相关代码：
- `backend/models/import_job.py` — `ImportJob` ORM 模型 + `to_dict()`
- `backend/api/data_import.py` — 9 个端点：连接列表 / 数据库 / 表 / 上传 / 执行 / 状态 / 列表 / 取消 / 删除
- `backend/services/data_import_service.py` — `run_import_job()` 后台协程（分批插入 + 协作式取消）

---

### migrate_data_export.py（2026-04-07）— SQL→Excel 数据导出

**运行方式**：

```bash
# Windows + Anaconda（推荐方式）
D:\ProgramData\Anaconda3\envs\dataagent\python.exe backend/scripts/migrate_data_export.py

# Linux/Mac
python backend/scripts/migrate_data_export.py
```

脚本幂等，多次运行无副作用（`CREATE TABLE IF NOT EXISTS` + `INSERT ... ON CONFLICT DO NOTHING`）。

**创建的表结构**：

```sql
CREATE TABLE IF NOT EXISTS export_jobs (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- 所属用户
    user_id     VARCHAR(64) NOT NULL,
    username    VARCHAR(100) NOT NULL,

    -- 导出配置
    job_name        VARCHAR(200),
    query_sql       TEXT NOT NULL,
    connection_env  VARCHAR(50) NOT NULL,
    connection_type VARCHAR(20) NOT NULL DEFAULT 'clickhouse',
    db_name         VARCHAR(200),

    -- 任务状态：pending → running → completed/failed；取消路径 → cancelling → cancelled
    status          VARCHAR(20) NOT NULL DEFAULT 'pending',

    -- 进度追踪（行级 / 批次 / Sheet 三层）
    total_rows      INTEGER,
    exported_rows   INTEGER DEFAULT 0,
    total_batches   INTEGER,
    done_batches    INTEGER DEFAULT 0,
    current_sheet   VARCHAR(200),
    total_sheets    INTEGER DEFAULT 0,

    -- 输出文件
    output_filename VARCHAR(500),
    file_path       VARCHAR(1000),
    file_size       BIGINT,

    -- 配置快照（JSONB）
    config_snapshot JSONB,

    -- 错误信息
    error_message   TEXT,

    -- 时间戳
    created_at  TIMESTAMP NOT NULL DEFAULT NOW(),
    started_at  TIMESTAMP,
    finished_at TIMESTAMP,
    updated_at  TIMESTAMP NOT NULL DEFAULT NOW()
);

-- 索引
CREATE INDEX IF NOT EXISTS idx_export_jobs_user_id    ON export_jobs(user_id);
CREATE INDEX IF NOT EXISTS idx_export_jobs_status     ON export_jobs(status);
CREATE INDEX IF NOT EXISTS idx_export_jobs_created_at ON export_jobs(created_at);

-- Step 2: 插入 data:export 权限（幂等）
INSERT INTO permissions (id, resource, action, description)
VALUES (gen_random_uuid(), 'data', 'export', 'SQL 查询结果导出为 Excel')
ON CONFLICT (resource, action) DO NOTHING;

-- Step 3: 将 data:export 权限分配给 superadmin 角色（幂等）
INSERT INTO role_permissions (role_id, permission_id)
SELECT r.id, p.id
FROM roles r, permissions p
WHERE r.name = 'superadmin' AND p.resource = 'data' AND p.action = 'export'
ON CONFLICT DO NOTHING;
```

**状态机**：`pending` → `running` → `completed` / `failed`；取消路径：`pending` → `cancelled`（直接终态）；`running` → `cancelling` → `cancelled`（协作式，后台协程批次边界检测退出）。

**权限范围**：`data:export` 默认仅 superadmin 角色持有；可通过角色管理 API（`POST /roles/{id}/permissions`）动态授予其他角色。前端菜单入口：侧边栏「数据导出」，需 `data:export` 权限。

**删除保护**：`DELETE /data-export/jobs/{id}` 仅允许终态任务（`completed/cancelled/failed`）删除，防止后台协程写入孤儿文件（协程仍运行但 DB 记录已消失）。

相关代码：
- `backend/models/export_job.py` — `ExportJob` ORM 模型 + `to_dict()`
- `backend/api/data_export.py` — 8 个端点：连接列表 / SQL预览 / 执行 / 状态 / 取消 / 删除 / 历史列表 / 下载
- `backend/services/data_export_service.py` — `run_export_job()` 后台协程（流式写 xlsx + 多 Sheet 分割 + 大整数转换 + 协作式取消）

---

### migrate_reports_enhancement.py（2026-04-13）— 多图表 HTML 报告增强字段

**运行方式**：

```bash
# Windows + Anaconda（推荐方式）
D:\ProgramData\Anaconda3\envs\dataagent\python.exe backend/scripts/migrate_reports_enhancement.py

# Linux/Mac
python backend/scripts/migrate_reports_enhancement.py
```

脚本幂等，多次运行无副作用（`ALTER TABLE ... ADD COLUMN IF NOT EXISTS`）。

**变更列**：

```sql
-- reports 表新增 5 列
ALTER TABLE reports ADD COLUMN IF NOT EXISTS refresh_token    VARCHAR(64);
ALTER TABLE reports ADD COLUMN IF NOT EXISTS export_job_id   VARCHAR(64);
ALTER TABLE reports ADD COLUMN IF NOT EXISTS export_format   VARCHAR(10);
ALTER TABLE reports ADD COLUMN IF NOT EXISTS llm_summary     TEXT;
ALTER TABLE reports ADD COLUMN IF NOT EXISTS is_summarized   BOOLEAN NOT NULL DEFAULT FALSE;
```

| 新增列 | 类型 | 用途 |
|--------|------|------|
| `refresh_token` | `VARCHAR(64)` | SHA-256 随机令牌，用于公开刷新接口（`GET /reports/{id}/refresh-data`）鉴权，避免暴露用户 JWT |
| `export_job_id` | `VARCHAR(64)` | 关联 PDF/PPTX 导出任务 ID（写入后可轮询进度） |
| `export_format` | `VARCHAR(10)` | 最后一次导出格式：`pdf` / `pptx` |
| `llm_summary` | `TEXT` | LLM 生成的报告摘要文字，存储后可复用 |
| `is_summarized` | `BOOLEAN` | 标记摘要已生成，避免重复调 LLM（默认 `false`） |

相关代码：
- `backend/models/report.py` — `Report` ORM 模型新增 5 个字段 + `to_dict()` 导出
- `backend/services/report_service.py` — `create_report()` 生成并写入 `refresh_token`；`generate_summary()` 调 LLM 并更新 `llm_summary`/`is_summarized`

---

### migrate_reports_permissions.py（2026-04-13）— 报告 RBAC 权限种子

**运行方式**：

```bash
# Windows + Anaconda（推荐方式）
D:\ProgramData\Anaconda3\envs\dataagent\python.exe backend/scripts/migrate_reports_permissions.py

# Linux/Mac
python backend/scripts/migrate_reports_permissions.py
```

脚本幂等（`INSERT ... ON CONFLICT DO NOTHING`）。

**新增权限与角色分配**：

```sql
-- Step 1: 插入 3 条权限记录
INSERT INTO permissions (id, resource, action, description)
VALUES
  (gen_random_uuid(), 'reports', 'read',   '查看/列出图表报告'),
  (gen_random_uuid(), 'reports', 'create', '生成图表报告'),
  (gen_random_uuid(), 'reports', 'delete', '删除图表报告')
ON CONFLICT (resource, action) DO NOTHING;

-- Step 2: 角色分配
-- analyst: reports:read + reports:create
INSERT INTO role_permissions (role_id, permission_id) ...

-- admin: reports:read + reports:create + reports:delete
INSERT INTO role_permissions (role_id, permission_id) ...

-- superadmin: 全部 3 条
INSERT INTO role_permissions (role_id, permission_id) ...
```

**权限矩阵（执行后）**：

| 角色 | reports:read | reports:create | reports:delete |
|------|:---:|:---:|:---:|
| viewer | — | — | — |
| analyst | ✅ | ✅ | — |
| admin | ✅ | ✅ | ✅ |
| superadmin | ✅ | ✅ | ✅ |

**前端菜单**：侧边栏「图表报告」（`BarChartOutlined` 图标），需 `reports:read` 权限。viewer 用户不可见。

相关代码：
- `backend/api/reports.py` — 所有端点使用 `require_permission("reports", "read"|"create"|"delete")`
- `backend/scripts/init_rbac.py` — `PERMISSIONS` 列表已包含 3 条记录；角色初始化时同步分配（新环境首次 init 无需额外执行此脚本）
- `frontend/src/components/AppLayout.tsx` — `ALL_MENU_ITEMS` 新增 `{ key: '/reports', perm: 'reports:read' }` 项

---

### migrate_datacenter_v1.py（2026-04-13）— 数据管理中心 v1

**运行方式**：

```bash
# Windows + Anaconda（推荐方式）
D:\ProgramData\Anaconda3\envs\dataagent\python.exe backend/scripts/migrate_datacenter_v1.py

# Linux/Mac
python backend/scripts/migrate_datacenter_v1.py
```

脚本幂等，多次运行无副作用。

**变更内容**：

**1. `reports` 表新增字段**

```sql
-- 报告类型（dashboard=交互式报表 / document=文档报告）
ALTER TABLE reports ADD COLUMN IF NOT EXISTS doc_type VARCHAR(20) NOT NULL DEFAULT 'dashboard';

-- 关联定时任务（定时触发生成时回填）
ALTER TABLE reports ADD COLUMN IF NOT EXISTS scheduled_report_id UUID;

-- 版本序号（同一任务多次执行时递增）
ALTER TABLE reports ADD COLUMN IF NOT EXISTS version_seq INTEGER DEFAULT 1;
```

**2. 新建 `scheduled_reports` 表**

```sql
CREATE TABLE IF NOT EXISTS scheduled_reports (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name             VARCHAR(200) NOT NULL,       -- 任务名称
    description      TEXT,
    owner_username   VARCHAR(100) NOT NULL,        -- 所有者用户名
    doc_type         VARCHAR(20)  NOT NULL DEFAULT 'dashboard',
    cron_expr        VARCHAR(100) NOT NULL,        -- 5-field cron 表达式
    timezone         VARCHAR(60)  NOT NULL DEFAULT 'Asia/Shanghai',
    report_spec      JSONB        NOT NULL,        -- 报告规格 JSON
    include_summary  BOOLEAN      NOT NULL DEFAULT FALSE,
    is_active        BOOLEAN      NOT NULL DEFAULT TRUE,
    last_run_at      TIMESTAMP,
    next_run_at      TIMESTAMP,
    run_count        INTEGER      NOT NULL DEFAULT 0,
    fail_count       INTEGER      NOT NULL DEFAULT 0,
    notify_channels  JSONB,                        -- 通知渠道配置数组
    created_at       TIMESTAMP    NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMP    NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_scheduled_reports_owner      ON scheduled_reports(owner_username);
CREATE INDEX IF NOT EXISTS idx_scheduled_reports_active     ON scheduled_reports(is_active);
CREATE INDEX IF NOT EXISTS idx_scheduled_reports_created_at ON scheduled_reports(created_at);
```

**3. 新建 `schedule_run_logs` 表**

```sql
CREATE TABLE IF NOT EXISTS schedule_run_logs (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    scheduled_report_id   UUID        NOT NULL,   -- FK → scheduled_reports.id
    report_id             UUID,                   -- 本次生成的 Report ID（成功时填写）
    status                VARCHAR(20) NOT NULL DEFAULT 'pending',  -- pending/running/success/failed
    error_msg             TEXT,
    duration_sec          INTEGER,
    notify_summary        JSONB,                  -- 通知发送结果摘要
    run_at                TIMESTAMP   NOT NULL DEFAULT NOW(),
    finished_at           TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_run_logs_scheduled_report_id ON schedule_run_logs(scheduled_report_id);
CREATE INDEX IF NOT EXISTS idx_run_logs_status              ON schedule_run_logs(status);
CREATE INDEX IF NOT EXISTS idx_run_logs_run_at              ON schedule_run_logs(run_at);
```

**4. 新建 `notification_logs` 表**

```sql
CREATE TABLE IF NOT EXISTS notification_logs (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    schedule_run_log_id UUID,
    channel_type        VARCHAR(20),   -- email / wecom / feishu / webhook
    status              VARCHAR(20),   -- success / failed
    error_msg           TEXT,
    sent_at             TIMESTAMP NOT NULL DEFAULT NOW()
);
```

**权限与 RBAC**：本脚本不种子权限记录。`schedules:read/write/admin` 权限在 `init_rbac.py` 中维护，执行 `init_rbac.py` 时自动写入（新环境与升级均可重复执行）。

**通知渠道格式**（`notify_channels` JSONB 数组示例）：

```json
[
  {"type": "email",   "to": ["a@b.com"], "subject_tpl": "{{name}} - {{date}}"},
  {"type": "wecom",   "webhook_url": "https://qyapi.weixin.qq.com/..."},
  {"type": "feishu",  "webhook_url": "https://open.feishu.cn/..."},
  {"type": "webhook", "url": "https://hook.example.com/", "method": "POST"}
]
```

相关代码：
- `backend/models/scheduled_report.py` — `ScheduledReport` ORM 模型
- `backend/models/schedule_run_log.py` — `ScheduleRunLog` ORM 模型
- `backend/models/notification_log.py` — `NotificationLog` ORM 模型
- `backend/api/scheduled_reports.py` — 9 个 REST 端点
- `backend/services/scheduler_service.py` — APScheduler 封装（add_or_update_job / remove_job）
- `backend/services/notify_service.py` — 多渠道通知发送（email/WeCom/Feishu/Webhook）

---

### 技能路由可视化 T1-T6（2026-03-26）— 无 DB 迁移

**不需要执行任何数据库迁移脚本。**

所有变更均在代码层完成，已有的 `messages.extra_metadata` JSONB 字段自动承载新事件类型：

| 变更组件 | 变更内容 | DB 影响 |
|---------|---------|--------|
| `backend/skills/skill_loader.py` | `_last_match_info` 实例变量；`_make_match_info()`；`get_last_match_info()` | 无 |
| `backend/agents/agentic_loop.py` | `run_streaming()` yield `AgentEvent(type="skill_matched")` | 无 |
| `backend/api/skills.py` | 新增 `GET /skills/load-errors` 端点（`settings:read`） | 无 |
| `frontend/src/store/useChatStore.ts` | `SkillMatchInfo` / `SkillMatchedSkill` TypeScript 接口 | 无 |
| `frontend/src/pages/Chat.tsx` | `skill_matched` 事件路由到 `addThoughtEvent()` | 无 |
| `frontend/src/components/chat/ThoughtProcess.tsx` | 新增 🧠 技能路由折叠面板渲染 | 无 |
| `conversation_service.py` | `skill_matched` 纳入 `thinking_events` 收集范围 | 利用已有 `extra_metadata['thinking_events']` |

**持久化路径**（利用已有结构，无需迁移）：

```
skill_matched 事件
  ↓ conversation_service.send_message_stream() 收集入 thinking_events 列表
  ↓ 写入 messages.extra_metadata['thinking_events']（已有 JSONB 字段，v1.3+ 已存在）
  ↓ GET /conversations/{id}/messages → thinking_events 顶层提升
  ↓ 前端 loadMessages() → addThoughtEvent() → messageThoughts 恢复
```

---

## Alembic 参考（复杂迁移场景）

以下内容供参考，说明如何使用标准 Alembic 工具管理迁移。

### 目录结构

```
data-agent/
├── alembic/                    # Alembic配置目录
│   ├── versions/               # 迁移脚本目录
│   ├── env.py                  # Alembic环境配置
│   └── script.py.mako         # 迁移脚本模板
├── alembic.ini                 # Alembic配置文件
└── backend/
    └── scripts/
        └── init_db.py          # 数据库初始化脚本
```

## 快速开始

### 1. 初始化数据库（首次使用）

```bash
# 方法1: 使用初始化脚本（推荐）
python backend/scripts/init_db.py

# 方法2: 直接使用Alembic
alembic upgrade head
```

### 2. 创建新的迁移

当你修改了模型（添加/删除/修改字段）后，需要创建新的迁移：

```bash
# 自动生成迁移脚本（推荐）
alembic revision --autogenerate -m "描述本次变更"

# 示例
alembic revision --autogenerate -m "add user table"
alembic revision --autogenerate -m "add email field to conversation"
```

### 3. 应用迁移

```bash
# 应用所有未执行的迁移
alembic upgrade head

# 应用到指定版本
alembic upgrade <revision_id>

# 应用下一个版本
alembic upgrade +1
```

### 4. 回滚迁移

```bash
# 回滚到上一个版本
alembic downgrade -1

# 回滚到指定版本
alembic downgrade <revision_id>

# 回滚所有迁移
alembic downgrade base
```

## 常用命令

### 查看迁移历史

```bash
# 查看当前版本
alembic current

# 查看迁移历史
alembic history

# 查看详细历史
alembic history --verbose
```

### 查看SQL

```bash
# 查看将要执行的SQL（不实际执行）
alembic upgrade head --sql

# 查看回滚的SQL
alembic downgrade -1 --sql
```

## 迁移脚本示例

### 自动生成的迁移脚本

```python
"""add user table

Revision ID: abc123def456
Revises:
Create Date: 2024-01-01 10:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = 'abc123def456'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.create_table('users',
    sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column('email', sa.String(length=255), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_users_email'), 'users', ['email'], unique=True)
    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_index(op.f('ix_users_email'), table_name='users')
    op.drop_table('users')
    # ### end Alembic commands ###
```

### 手动创建迁移脚本

```bash
# 创建空白迁移脚本
alembic revision -m "manual migration"
```

然后手动编辑生成的脚本：

```python
def upgrade():
    # 添加自定义SQL或操作
    op.execute("CREATE INDEX idx_custom ON table_name (column_name)")

def downgrade():
    # 回滚操作
    op.execute("DROP INDEX idx_custom")
```

## 最佳实践

### 1. 迁移前备份

在生产环境应用迁移前，务必备份数据库：

```bash
# PostgreSQL备份
pg_dump -h localhost -U postgres data_agent > backup_$(date +%Y%m%d_%H%M%S).sql

# 恢复
psql -h localhost -U postgres data_agent < backup_20240101_120000.sql
```

### 2. 测试迁移

在开发环境充分测试迁移：

```bash
# 1. 应用迁移
alembic upgrade head

# 2. 测试应用功能

# 3. 回滚测试
alembic downgrade -1

# 4. 再次应用确保可重复
alembic upgrade head
```

### 3. 审查自动生成的迁移

自动生成的迁移可能不完美，应该：
- 检查生成的SQL是否正确
- 添加必要的数据迁移逻辑
- 确保downgrade()正确实现

### 4. 使用事务

Alembic默认在事务中执行迁移，但某些操作可能需要禁用：

```python
def upgrade():
    # 需要在事务外执行的操作
    op.execute("CREATE INDEX CONCURRENTLY idx_name ON table_name (column)")

# 在文件开头添加
from alembic import op
import sqlalchemy as sa

# revision identifiers
revision = 'xxx'
down_revision = 'yyy'

# 禁用事务
def upgrade():
    connection = op.get_bind()
    # 使用原始连接执行
```

### 5. 处理数据迁移

修改数据结构时，可能需要迁移现有数据：

```python
def upgrade():
    # 1. 添加新列（允许NULL）
    op.add_column('users', sa.Column('full_name', sa.String(200), nullable=True))

    # 2. 迁移数据
    op.execute("""
        UPDATE users
        SET full_name = CONCAT(first_name, ' ', last_name)
        WHERE full_name IS NULL
    """)

    # 3. 设置为NOT NULL
    op.alter_column('users', 'full_name', nullable=False)

    # 4. 删除旧列（如果需要）
    op.drop_column('users', 'first_name')
    op.drop_column('users', 'last_name')
```

## 环境配置

### 开发环境

`.env`文件配置：
```bash
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
POSTGRES_DB=data_agent_dev
POSTGRES_USER=postgres
POSTGRES_PASSWORD=your_password
```

### 生产环境

```bash
POSTGRES_HOST=prod-db-server
POSTGRES_PORT=5432
POSTGRES_DB=data_agent_prod
POSTGRES_USER=data_agent_user
POSTGRES_PASSWORD=strong_password
```

## 故障排查

### 问题1: "Target database is not up to date"

```bash
# 解决方案: 应用所有迁移
alembic upgrade head
```

### 问题2: 迁移冲突

```bash
# 查看当前版本
alembic current

# 手动修复迁移历史
alembic stamp <correct_revision_id>
```

### 问题3: 无法连接数据库

检查配置：
```bash
# 测试数据库连接
python -c "from backend.config.database import test_postgres_connection; print(test_postgres_connection())"
```

### 问题4: 迁移执行失败

```bash
# 1. 查看详细错误
alembic upgrade head --verbose

# 2. 查看将要执行的SQL
alembic upgrade head --sql

# 3. 回滚到已知的好版本
alembic downgrade <revision_id>

# 4. 修复问题后重新应用
alembic upgrade head
```

## 多环境管理

### 使用不同的配置文件

```bash
# 开发环境
alembic -c alembic_dev.ini upgrade head

# 测试环境
alembic -c alembic_test.ini upgrade head

# 生产环境
alembic -c alembic_prod.ini upgrade head
```

### 使用环境变量

```bash
# 设置环境
export ENVIRONMENT=production
export POSTGRES_HOST=prod-server

# 执行迁移
alembic upgrade head
```

## 参考资料

- [Alembic官方文档](https://alembic.sqlalchemy.org/)
- [SQLAlchemy文档](https://docs.sqlalchemy.org/)
- [PostgreSQL文档](https://www.postgresql.org/docs/)
