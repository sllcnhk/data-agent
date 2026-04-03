# SKILL.md 技能系统说明

本目录存放系统的专业技能定义文件（SKILL.md）。Agent 根据**触发关键词**匹配用户意图，
动态将对应 Skill 注入 system prompt，实现专业化行为扩展。

---

## 三层目录结构

```
.claude/skills/
├── system/            # Tier 1：系统技能（开发人员维护，只读）
│   ├── _base-safety.md    # 始终注入：安全约束（所有场景生效）
│   ├── _base-tools.md     # 始终注入：MCP 工具使用规范
│   ├── etl-engineer.md    # 触发注入：ETL 工程专业规程
│   ├── clickhouse-analyst.md
│   ├── schema-explorer.md
│   ├── project-guide.md
│   └── skill-creator.md
├── project/           # Tier 2：项目技能（管理员通过 REST API 维护）
│   └── *.md               # 业务词典、数据模型、指标口径等
├── user/              # Tier 3：用户技能（用户自定义，个人可见）
│   └── *.md               # 用户通过页面或对话创建
└── README.md          # 本文件
```

---

## 文件格式（SKILL.md 规范）

```markdown
---
name: skill-name            # 技能唯一标识（kebab-case）
version: "1.0"              # 版本号（更新时自动递增）
description: 简短描述       # ≤ 120 字
triggers:                   # 触发关键词列表（中文/英文均可）
  - 关键词1
  - keyword2
category: engineering       # engineering / analytics / general / system
priority: high              # high / medium / low
always_inject: false        # true = 始终注入（不依赖触发词）
---

# 技能标题

[Markdown 格式的技能内容 — 激活时注入 system prompt]
```

---

## 字段说明

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `name` | string | ✅ | 技能唯一 ID，与文件名（不含 `.md`）一致 |
| `version` | string | ✅ | 版本号，通过 API 更新时自动递增（1.0 → 1.1） |
| `description` | string | ✅ | 一句话功能描述，≤120 字 |
| `triggers` | list | 条件 | 触发该技能的关键词，用户消息包含其一即激活；`always_inject: true` 时可留空 |
| `category` | string | ✅ | `engineering` / `analytics` / `general` / `system` |
| `priority` | string | ✅ | `high` > `medium` > `low`，多技能同时激活时排序用 |
| `always_inject` | bool | ❌ | `true` = 每次对话始终注入，不依赖触发词（安全约束类使用）|

---

## 注入机制（三层叠加）

注入顺序（用户优先，安全约束最后靠近指令）：

```
[Base System Prompt]
    ↓ 追加
[Tier 3 · 用户技能] — 与消息匹配（最多 3 条）
    ↓ 追加
[Tier 2 · 项目技能] — 与消息匹配（最多 3 条）
    ↓ 追加
[Tier 1 · 系统 base] — always_inject=true，始终激活（_base-*.md）
    ↓ 追加
[Tier 1 · 系统触发] — 与消息匹配的专业规程（最多 3 条）
```

**context 保护**：总注入字符超过 8000 时自动降级为元数据摘要模式，只注入 name + description + triggers 摘要，避免 context 爆炸。

---

## 特殊文件命名规则

| 规则 | 说明 |
|------|------|
| `_base-*.md` | 文件名以 `_base` 开头 → 自动设置 `always_inject=true`，每次对话始终注入 |
| `README.MD`（大小写不敏感）| 不加载 |

> **注意**：与旧版不同，`_base-*.md` 文件会被正常加载（不跳过），`always_inject` 属性由文件名前缀自动推断。

---

## 权限说明

| 层级 | 写入方式 | Agent MCP 写入 |
|------|---------|--------------|
| Tier 1 系统 | 开发人员 Git 推送 + 部署 | ❌ 不允许 |
| Tier 2 项目 | 管理员 REST API（`X-Admin-Token`）| ❌ 不允许 |
| Tier 3 用户 | 用户 REST API + Agent MCP 工具 | ✅ 仅限 `user/` 目录 |

---

## REST API 速查

| 方法 | 路径 | 说明 | 权限 |
|------|------|------|------|
| GET | `/api/v1/skills/md-skills` | 列出所有三层技能 | 所有用户 |
| GET | `/api/v1/skills/preview?message=xxx` | 测试消息触发哪些技能 | 所有用户 |
| POST | `/api/v1/skills/user-defined` | 创建用户技能 | 用户 |
| PUT | `/api/v1/skills/user-defined/{name}` | 更新用户技能（自动版本递增）| 用户 |
| DELETE | `/api/v1/skills/user-defined/{name}` | 删除用户技能 | 用户 |
| GET | `/api/v1/skills/project-skills` | 列出项目技能 | 所有用户 |
| POST | `/api/v1/skills/project-skills` | 创建项目技能 | 管理员 |
| PUT | `/api/v1/skills/project-skills/{name}` | 更新项目技能（自动版本递增）| 管理员 |
| DELETE | `/api/v1/skills/project-skills/{name}` | 删除项目技能 | 管理员 |

---

## 现有技能清单

### 系统技能（system/）

| 文件 | 名称 | 始终注入 | 描述 |
|------|------|---------|------|
| `_base-safety.md` | _base-safety | ✅ | 安全基础约束：数据写入范围、DB 操作安全、PII 保护 |
| `_base-tools.md` | _base-tools | ✅ | MCP 工具使用规范：文件操作、DB 操作、结果验证 |
| `etl-engineer.md` | etl-engineer | ❌ | ETL 工程专业规程，触发词：ETL、建表、数据加工等 |
| `clickhouse-analyst.md` | clickhouse-analyst | ❌ | ClickHouse 分析规程，触发词：分析、留存、漏斗等 |
| `schema-explorer.md` | schema-explorer | ❌ | 数据库结构探索，触发词：表结构、字段、schema 等 |
| `project-guide.md` | project-guide | ❌ | 项目架构指南，触发词：架构、feature、模块等 |
| `skill-creator.md` | skill-creator | ❌ | 技能创建引导，触发词：创建技能、新建 skill 等 |

---

*最后更新：2026-03-13 — 三层架构重构 + always_inject 机制 + context 保护*
