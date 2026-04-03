# 数据库迁移指南

> **最后更新**：2026-03-26（**文件写入下载**：**无 DB 迁移**，文件路径元数据写入已有 `messages.extra_metadata["files_written"]` JSONB 列；技能路由可视化 T1-T6：**无新增 DB 迁移**，skill_matched SSE 事件 / SkillLoader._last_match_info / ThoughtProcess 🧠 面板均为代码层变更；新增 `migrate_add_is_shared.py`：`conversations.is_shared` 字段；对话用户隔离：`migrate_conversation_user_isolation.py`；其余历次变更均无数据库结构变更）

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
