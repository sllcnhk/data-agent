# Admin 操作手册：Skill 管理 & 知识库维护

> **版本**：v1.0 · 2026-04-09
> **适用角色**：管理员 / Claude Code CLI 维护者
> **前置条件**：已在 `.env` 配置 `ADMIN_SECRET_TOKEN`，项目运行在本地或可访问的服务器上

---

## 目录

1. [背景与架构速览](#1-背景与架构速览)
2. [Skill 晋升操作（user → project）](#2-skill-晋升操作user--project)
3. [共享知识库维护](#3-共享知识库维护)
4. [知识库有效性检查](#4-知识库有效性检查)
5. [日常维护工作流](#5-日常维护工作流)
6. [常见问题排查](#6-常见问题排查)

---

## 1. 背景与架构速览

### 1.1 两级知识库

```
customer_data/
├── _shared/db_knowledge/       ← 共享项目知识库
│   ├── _index.md               全局索引（24 张表 + 5 个指标）
│   ├── relationships.md        表关系图
│   ├── tables/                 各表文档
│   └── metrics/                指标口径文档
└── {username}/db_knowledge/    ← 用户私有知识库（个人覆盖/补充）
```

**查找优先级**：用户私有库 → 共享项目库 → 数据库直接探索（兜底）

### 1.2 三层 Skill

```
.claude/skills/
├── system/      只读，Git 管理
├── project/     Admin Token 保护，所有用户可见
└── user/{name}/ 用户自建，ENABLE_AUTH=true 时按用户隔离（当前实现全局可见）
```

### 1.3 关键变量（系统自动注入到每次对话）

| 变量 | 值 | 含义 |
|------|-----|------|
| `{CURRENT_USER}` | 当前登录用户名 | 用户私有目录前缀 |
| `{SHARED_DATA_ROOT}` | `_shared` | 共享知识库目录前缀（相对于 customer_data/）|

---

## 2. Skill 晋升操作（user → project）

### 2.1 何时需要晋升

满足以下任一条件时，将用户 Skill 晋升至 project 层：

- 该 Skill 对多个用户有价值，不只是个人专用
- Skill 内容已经过验证，不希望普通用户 API 随意覆盖
- Skill 引用了共享知识库（`_shared/db_knowledge/`）的内容

### 2.2 晋升方式一：Web UI 快速晋升（适合内容不引用知识库的 Skill）

在 Web 界面 `/skills` 页面，找到目标用户 Skill，点击**晋升**按钮：

1. 输入 `ADMIN_SECRET_TOKEN`（首次输入后会缓存在 sessionStorage）
2. 系统自动以 `{原名}-promoted` 为名创建 project 技能
3. ⚠️ **注意**：此方式**不会替换路径**，若 Skill 内容含 `{CURRENT_USER}/db_knowledge/`，晋升后引用路径将指向原用户的私有库（仍然可用，但其他用户无法访问该路径下的内容）

**适用场景**：Skill 仅包含 SQL 规范、业务规则，不引用本地知识库文件。

### 2.3 晋升方式二：Claude Code CLI 完整晋升（推荐，处理知识库引用）

在项目根目录执行以下步骤：

#### Step 1：查看待晋升 Skill

```bash
cat .claude/skills/user/{username}/{skill-name}.md
```

记录：
- Skill 名称（frontmatter `name` 字段）
- 是否含 `{CURRENT_USER}/db_knowledge/` 引用
- 如有引用，记录涉及的具体文件路径

#### Step 2：复制到 project 层

```bash
cp .claude/skills/user/{username}/{skill-name}.md \
   .claude/skills/project/{skill-name}.md
```

#### Step 3：替换路径引用

```bash
# Windows Git Bash / Linux
sed -i 's|{CURRENT_USER}/db_knowledge/|{SHARED_DATA_ROOT}/db_knowledge/|g' \
    .claude/skills/project/{skill-name}.md
```

或在 Claude Code 中直接编辑，将所有 `{CURRENT_USER}/db_knowledge/` 替换为 `{SHARED_DATA_ROOT}/db_knowledge/`。

> 同理，若 Skill 中有写入路径引用（如报告写入路径），将 `{CURRENT_USER}/reports/` 替换为 `{CURRENT_USER}/reports/`（写入路径保留用户维度，不改）。

#### Step 4：更新 frontmatter

编辑 `.claude/skills/project/{skill-name}.md`，确认以下字段：

```yaml
---
name: {skill-name}
version: "2.0"          # 版本递增，表示已晋升
description: ...
triggers:
  - ...
category: analytics     # 检查分类是否正确
priority: high
always_inject: false
layer: workflow         # 或 scenario / knowledge / maintenance
sub_skills:             # 若有子 Skill，也需一并晋升
  - {sub-skill-name}
---
```

#### Step 5：迁移被引用的知识库文件

若 Skill 引用了用户私有知识库的文件，需将其合并到共享库：

```bash
# 合并表文档（-n 表示不覆盖已有文件，避免用旧版本替换更新的内容）
cp -n customer_data/{username}/db_knowledge/tables/*.md \
      customer_data/_shared/db_knowledge/tables/

# 合并指标文档
cp -n customer_data/{username}/db_knowledge/metrics/*.md \
      customer_data/_shared/db_knowledge/metrics/

# 若需要更新（而非只补充），去掉 -n，但要先确认共享库版本是否更新：
diff customer_data/{username}/db_knowledge/tables/{table}.md \
     customer_data/_shared/db_knowledge/tables/{table}.md
```

**冲突处理原则**：
- 若共享库已有该文件且版本更新 → **保留共享库版本**（`-n` 会自动跳过）
- 若用户私有版本有额外的业务注解/枚举说明 → **手动合并** 两个文件的内容
- 若文件只在用户私有库存在 → **直接复制**到共享库

#### Step 6：更新共享库 `_index.md`（若合并了新表文档）

```bash
# 检查哪些表是新加入的
ls customer_data/_shared/db_knowledge/tables/
```

如有新增，编辑 `customer_data/_shared/db_knowledge/_index.md`，在对应章节加入新表的链接。

#### Step 7：验证晋升结果

```bash
# 确认 project 层文件存在且路径正确
cat .claude/skills/project/{skill-name}.md | grep -E "SHARED_DATA_ROOT|CURRENT_USER"

# 预期：所有知识库引用应使用 {SHARED_DATA_ROOT}，不应出现 {CURRENT_USER}/db_knowledge/
```

在 Web 界面 `/skills` → 触发测试面板，输入典型查询，确认 Skill 被正确触发。

#### Step 8：清理用户层（可选）

若 project 层已完全替代用户层的 Skill，可删除用户层版本，避免重复触发：

```bash
rm .claude/skills/user/{username}/{skill-name}.md
```

> ⚠️ 当前实现（L9 已知问题）：用户层 Skill 对所有用户可见，若不删除，两个同名 Skill 都会参与匹配，可能导致内容重复注入。

#### Step 9：Git 提交

```bash
git add .claude/skills/project/{skill-name}.md
git add customer_data/_shared/db_knowledge/
git rm .claude/skills/user/{username}/{skill-name}.md  # 若已删除
git commit -m "promote: {skill-name} user→project + merge kb to _shared"
```

---

## 3. 共享知识库维护

共享知识库（`customer_data/_shared/db_knowledge/`）是所有用户的知识底座，维护频率建议：**表结构有变动时立即更新，无变动时每月全量核查一次**。

### 3.1 方式一：通过 Claude Code CLI 直接维护（推荐）

Claude Code 有对 `customer_data/_shared/` 的读写权限，可直接操作文件。

**探索新表并写入共享库（适合已知表名）：**

> ⚠️ Claude Code CLI 没有直接的 ClickHouse MCP 连接。先在 Web 界面获取表结构，再在 CLI 中写入文件。

在 Web 界面（任意用户）执行：
```
"描述一下 crm.new_table 的表结构"
```
Agent 会调用 `describe_table`，将结果复制后，在 Claude Code 中：

```bash
# 新建表文档
cat > customer_data/_shared/db_knowledge/tables/new_table.md << 'EOF'
# 表名：crm.new_table

> 最后同步时间：2026-04-09 | 来源环境：SG

## 基本信息
...（粘贴内容）
EOF
```

**全量核查（确认共享库与实际表结构一致）：**

在 Web 界面以管理员身份对话，说：
```
"更新共享知识库，全量核查 SG 环境 crm 数据库"
```

`db-maintainer` Skill 会触发，识别关键词"共享"后进入共享库模式，写入 `_shared/db_knowledge/`。

### 3.2 方式二：通过 Web 界面对话触发（适合 db-maintainer 工作流）

以管理员身份登录 Web 界面，在对话中说：

| 意图 | 推荐说法 |
|------|---------|
| 全量更新共享库 | "更新共享知识库，SG 环境 crm 数据库全量" |
| 新增某张表的文档 | "把 crm.xxx_table 写入共享知识库" |
| 增量更新（仅变更的表）| "同步共享知识库，检查 SG 哪些表结构有变动" |
| 用户探索结果合并到共享 | "把我刚才探索的 idn 表结构更新到共享知识库" |

`db-maintainer` Skill 会在 Step 0 检测到"共享"关键词，将 `WRITE_ROOT` 设为 `_shared`，写入到共享库路径。

### 3.3 手动编辑（适合添加业务注解）

Claude Code CLI 直接编辑表文档，补充自动探索无法获得的业务含义：

```bash
# 为某个字段补充业务含义
code customer_data/_shared/db_knowledge/tables/realtime_dwd_crm_call_record.md
```

重点补充内容：
- 枚举值的业务含义（如 `call_code_type` 各值的含义）
- 分区键和查询优化提示
- 与其他表的关联关系
- 数据质量注意事项（如 THAI 环境 AM 异常）

### 3.4 共享库目录结构规范

```
customer_data/_shared/db_knowledge/
├── _index.md              ← 必须维护！Agent 每次分析必读此文件
│                             格式：表分组 + 链接 + 版本号
├── relationships.md       ← ERD 与业务关联（可选，但推荐）
├── tables/
│   └── {database}_{table}.md  或  {table}.md
│       命名规则：优先用表的全名，用下划线分隔，全小写
└── metrics/
    └── {metric_name}.md
        命名规则：snake_case，对应业务指标名称
```

**`_index.md` 维护要点**：

每次新增/删除表文档后必须同步更新 `_index.md`：

```markdown
## 版本：v5.0 | 最后更新：2026-04-09

## 外呼通话
- [realtime_dwd_crm_call_record](tables/realtime_dwd_crm_call_record.md) — 呼叫记录宽表（核心，78字段）
- [dim_call_task](tables/dim_call_task.md) — 外呼任务维表

## 账单
- [Bill_Monthly](tables/Bill_Monthly.md) — 月账单汇总（DWS 层）
...
```

---

## 4. 知识库有效性检查

### 4.1 检查共享库是否完整

```bash
# 查看所有表文档
ls customer_data/_shared/db_knowledge/tables/ | wc -l

# 查看 _index.md 中登记的表数量（与实际文件对比）
grep -c "^\- \[" customer_data/_shared/db_knowledge/_index.md
```

若两者不一致，说明有文档未登记或已删除但 `_index.md` 未更新。

### 4.2 检查 Skill 路径引用是否有效

```bash
# 检查 project 层 Skill 中是否还有遗留的 CURRENT_USER 知识库引用
grep -r "CURRENT_USER.*db_knowledge" .claude/skills/project/
```

**预期结果**：无输出（project 层不应引用 `{CURRENT_USER}/db_knowledge/`）。

若有输出，说明该 Skill 在晋升时未完成路径替换，需按 §2.3 Step 3 补做。

```bash
# 检查 project 层 Skill 的 SHARED_DATA_ROOT 引用是否正确拼写
grep -r "SHARED_DATA_ROOT" .claude/skills/project/
```

### 4.3 检查用户私有库与共享库的差异

```bash
# 查看某用户私有库中有哪些表文档是共享库没有的
diff <(ls customer_data/{username}/db_knowledge/tables/ 2>/dev/null | sort) \
     <(ls customer_data/_shared/db_knowledge/tables/ | sort)
```

左侧（`<`）行表示用户私有库独有的文档，可考虑合并到共享库。

### 4.4 检查 sub_skills 依赖完整性

若 `clickhouse-analyst.md` 在 `sub_skills` 中声明了子 Skill，确认子 Skill 文件存在：

```bash
# 检查所有 project sub_skill 是否存在
for skill in ch-sg-specific ch-idn-specific ch-br-specific ch-my-specific ch-thai-specific ch-mx-specific ch-call-metrics ch-billing-analysis; do
  [ -f ".claude/skills/project/${skill}.md" ] && echo "✅ $skill" || echo "❌ MISSING: $skill"
done
```

---

## 5. 日常维护工作流

### 5.1 新环境上线（如新增 IDN 环境）

1. **更新共享知识库**：在 Web 界面说 "更新共享知识库，IDN 环境 crm 数据库全量"
2. **完善 `ch-idn-specific.md`**（当前为 stub）：
   ```bash
   code .claude/skills/project/ch-idn-specific.md
   # 填充 IDN 特有表、时区（UTC+7）、特有 ClickHouse 配置
   ```
3. **更新 `_index.md`**：若 IDN 有独有表，在共享库 `_index.md` 中新增 IDN 章节
4. **Git 提交**

### 5.2 用户反馈 Skill 不准确

1. 找到对应 Skill 文件：
   ```bash
   # 先确认是 project 层还是 user 层
   ls .claude/skills/project/ | grep {skill-name}
   ls .claude/skills/user/{username}/ | grep {skill-name}
   ```
2. 在 Claude Code 中直接编辑修正
3. 更新 `version` 字段（如 `2.0` → `2.1`）
4. 版本更新会使 ChromaDB 语义路由缓存自动失效，下次对话重新匹配

### 5.3 用户提交的知识库文档质量审核

用户在私有库中探索了新内容，希望合并到共享库：

```bash
# 对比差异
diff customer_data/{username}/db_knowledge/tables/{table}.md \
     customer_data/_shared/db_knowledge/tables/{table}.md

# 若用户版本更好（有更完整的字段注释），替换共享库版本
cp customer_data/{username}/db_knowledge/tables/{table}.md \
   customer_data/_shared/db_knowledge/tables/{table}.md

# 若只是用户有额外注解，手动合并
```

### 5.4 删除废弃的 Skill

```bash
# 删除 project 层 Skill
rm .claude/skills/project/{skill-name}.md
# 热加载：文件删除后 watchdog 会在 0.8s 内触发 load_all()，无需重启服务

# 验证
curl http://localhost:8000/api/v1/skills/project-skills | python -m json.tool | grep {skill-name}
# 预期：无输出
```

---

## 6. 常见问题排查

### Q1：晋升后 Skill 触发了，但 Agent 去错误的路径读知识库

**症状**：Agent 读 `{username}/db_knowledge/` 而不是 `_shared/db_knowledge/`

**原因**：晋升时未替换路径，Skill 内容中仍有 `{CURRENT_USER}/db_knowledge/`

**修复**：
```bash
grep "CURRENT_USER.*db_knowledge" .claude/skills/project/{skill-name}.md
# 若有输出，执行替换
sed -i 's|{CURRENT_USER}/db_knowledge/|{SHARED_DATA_ROOT}/db_knowledge/|g' \
    .claude/skills/project/{skill-name}.md
```

---

### Q2：Agent 说"知识库不存在"，但文件明明在 `_shared/` 下

**症状**：Agent 宣告进入"探索模式"，未读共享库

**可能原因 1**：`db-knowledge-router` Skill 未被触发（触发词未命中）

```bash
# 在 Web 界面 /skills → 触发测试，输入查询语句，确认 db-knowledge-router 是否出现
```

**可能原因 2**：Agent 读取路径有误（写了 `customer_data/_shared/` 而非 `_shared/`）

Agent 的文件系统根目录已指向 `customer_data/`，正确的相对路径是 `_shared/db_knowledge/_index.md`，**不带** `customer_data/` 前缀。检查 `db-knowledge-router.md` 中的路径示例是否正确。

**可能原因 3**：`_index.md` 文件不存在

```bash
ls customer_data/_shared/db_knowledge/_index.md
```

---

### Q3：两个同名 Skill 同时触发，内容重复注入

**症状**：系统提示中同一业务内容出现两次，接近 16000 chars 上限

**原因**：用户层与 project 层存在同名 Skill（晋升后忘记删除用户层版本）

**修复**：
```bash
# 找到重复的用户层 Skill
grep -r "name: {skill-name}" .claude/skills/user/
# 删除用户层版本
rm .claude/skills/user/{username}/{skill-name}.md
```

---

### Q4：修改了 project Skill 后未生效

**症状**：对话中 Skill 内容还是旧版本

**可能原因 1**：文件写入后 watchdog 还未触发热加载（等待 0.8s 防抖）

通常等待 1s 即可。若仍未生效，检查 `skill_watcher.py` 是否正常运行。

**可能原因 2**：ChromaDB 缓存仍命中旧版本

版本号更新会使缓存失效。若未更新版本号，可手动清除缓存：

```bash
rm -rf data/skill_routing_cache/
# 重启服务后缓存重建
```

**可能原因 3**：语义路由缓存路径未配置正确

```bash
grep SKILL_ROUTING_CACHE_PATH .env
# 确认路径存在且有写权限
```

---

### Q5：晋升后 sub_skills 展开失败

**症状**：父 Skill 触发了，但子 Skill 没有被加载

**原因**：子 Skill 未同步迁移到 project 层，或 `sub_skills` 字段名称拼写错误

**检查**：
```bash
# 确认父 Skill 的 sub_skills 声明
grep -A 10 "^sub_skills:" .claude/skills/project/{parent-skill}.md

# 确认每个子 Skill 文件存在（project 或 user 层均可）
for s in ch-sg-specific ch-call-metrics ch-billing-analysis; do
  [ -f ".claude/skills/project/$s.md" ] || [ -f ".claude/skills/user/superadmin/$s.md" ] \
    && echo "✅ $s" || echo "❌ $s NOT FOUND"
done
```

子 Skill 可以在任意层（system/project/user），`_expand_sub_skills()` 会全局查找。

---

## 附录：常用命令速查

```bash
# 查看所有 project 层 Skill
ls .claude/skills/project/

# 查看某 Skill 的 frontmatter
head -20 .claude/skills/project/{skill-name}.md

# 检查共享知识库文件数
find customer_data/_shared/db_knowledge -name "*.md" | wc -l

# 检查 project Skill 中的路径引用
grep -n "db_knowledge\|CURRENT_USER\|SHARED_DATA_ROOT" .claude/skills/project/*.md

# 通过 REST API 列出 project 技能（需服务运行）
curl http://localhost:8000/api/v1/skills/project-skills | python -m json.tool

# 通过 REST API 删除 project 技能（需 Admin Token）
curl -X DELETE http://localhost:8000/api/v1/skills/project-skills/{name} \
     -H "X-Admin-Token: ${ADMIN_SECRET_TOKEN}"

# 触发词测试（不需要 Token）
curl "http://localhost:8000/api/v1/skills/preview?message=查询SG接通率" | python -m json.tool
```
