# Git 代码管理方案

> **版本**：v1.0 · 2026-04-03
> **适用对象**：data-agent 项目，单人/小团队维护
> **状态**：方案草稿，待确认后执行

---

## 一、现状速览

| 项目 | 现状 |
|------|------|
| Git 安装 | ✅ 已安装 v2.51.0 |
| 仓库初始化 | ❌ 未初始化 |
| 远程仓库 | ❌ 未创建 |
| .gitignore | ❌ 未创建 |
| 敏感文件 | `.env`、`backend/.env`（含 API Key） |
| 大目录 | `frontend/node_modules/`（依赖包）|
| 数据目录 | `customer_data/`（用户分析文件，441K）|
| 技能目录 | `.claude/skills/`（Skill 知识库）|
| 散落文档 | 根目录 ~86 个 .md 历史文档 |

---

## 二、方案设计

### 2.1 存储位置：本地 + 远程双备份（推荐）

```
本地仓库（日常开发）
    ↕ git push / pull
远程仓库（备份 + 协作）
  推荐：GitHub 私有仓库（Private）
  备选：Gitee（国内访问快）
```

**选择依据**：
- 仅本地：硬盘坏了代码全丢，不推荐
- 公开仓库：代码逻辑/提示词会被搜索引擎索引，不适合商业项目
- **私有仓库**：仅自己可见，安全，免费（GitHub/Gitee 均支持）

> ⚠️ **决策点 A**：选择 GitHub 还是 Gitee？
> - GitHub：全球主流，Claude Code 集成最好，但国内偶尔需要代理
> - Gitee：国内访问快，不需要代理，但 AI 工具集成稍弱

---

### 2.2 目录纳管策略

| 目录/文件 | 是否纳入 Git | 理由 |
|-----------|------------|------|
| `backend/` | ✅ 全部纳入 | 核心代码 |
| `frontend/src/` | ✅ 全部纳入 | 前端源码 |
| `frontend/public/` | ✅ 纳入 | 静态资源 |
| `frontend/package.json` | ✅ 纳入 | 依赖声明 |
| `frontend/node_modules/` | ❌ 排除 | 依赖包，`npm install` 可还原，体积大 |
| `.claude/skills/` | ✅ 纳入（推荐）| Skill 知识库是核心资产，值得版本管理 |
| `.claude/settings.json` | ✅ 纳入 | Claude Code 配置 |
| `.claude/settings.local.json` | ❌ 排除 | 本地个人设置，不同机器不同 |
| `.env` / `backend/.env` | ❌ 排除 | 含 API Key，绝不提交 |
| `customer_data/` | ⚠️ 待定 | 含用户数据，下见决策点 B |
| `docs/` | ✅ 纳入 | 设计文档 |
| `test_*.py` | ✅ 纳入 | 测试代码 |
| 根目录散落 `.md` | ✅ 纳入 | 历史记录，建议后续整理到 `docs/` |
| `__pycache__/` | ❌ 排除 | Python 编译缓存 |
| `*.pyc` | ❌ 排除 | Python 字节码 |
| ChromaDB 缓存 | ❌ 排除 | 路由缓存，可重建 |

> ⚠️ **决策点 B**：`customer_data/` 是否纳入 Git？
> - **方案 B1（纳入）**：db_knowledge 知识库、skill 触发测试结果等有价值内容一起版本管理。当前仅 441K，可接受。**缺点**：随报告增多体积会增大；含用户分析数据推送到云端有隐私风险
> - **方案 B2（排除）**：只保留代码，customer_data 完全本地。**缺点**：db_knowledge 维护的知识库无法随代码一起备份
> - **方案 B3（部分纳入）**：`customer_data/*/db_knowledge/` 纳入（知识库），`customer_data/*/reports/` 排除（报告文件）。最精细但配置稍复杂

---

### 2.3 分支策略（轻量单人模式）

```
main          ← 主分支，只放"可运行"的代码
  └── dev     ← 开发分支，日常在此提交
       └── feature/xxx  ← 较大功能开发时临时开分支
```

**规则**：
- 小改动（Skill 更新、bug fix）直接在 `dev` 上提交
- 较大功能（新 API、新 Agent）从 `dev` 开 `feature/` 分支，完成后合并回 `dev`
- 稳定后将 `dev` 合并到 `main`，并打 Tag

> ⚠️ **决策点 C**：是否需要 dev 分支，还是直接用 main 一个分支？
> - 单人项目直接用 main 也完全可以，更简单
> - 有 dev 分支的好处：main 始终保持"可部署状态"，出问题容易回滚

---

### 2.4 何时提交（Commit 时机）

推荐遵循"**一个完整工作单元提交一次**"原则：

| 场景 | 是否提交 | 建议 commit message 格式 |
|------|---------|------------------------|
| 完成一个功能（新 API、新页面）| ✅ 立即提交 | `feat: 添加用户隔离对话功能` |
| 修复一个 bug | ✅ 立即提交 | `fix: 修复 customer_data 双层目录问题` |
| 更新 Skill 文件 | ✅ 提交 | `skill: 修正 call_code_type 枚举值` |
| 更新设计文档 | ✅ 提交 | `docs: 更新 skill_system_design.md` |
| 日常开发到一半，下班前 | ✅ 提交（标注 WIP）| `wip: 附件上传功能开发中` |
| 只是运行了程序/测试 | ❌ 不提交 | — |
| .env 修改了配置 | ❌ 不提交（已在 .gitignore）| — |

**commit message 前缀约定**（可选，可不用）：

| 前缀 | 含义 |
|------|------|
| `feat:` | 新功能 |
| `fix:` | Bug 修复 |
| `skill:` | Skill 文件更新 |
| `docs:` | 文档更新 |
| `test:` | 测试代码 |
| `refactor:` | 重构（不改功能）|
| `wip:` | 开发中，未完成 |

---

### 2.5 Tag 标记策略（版本里程碑）

Tag 用于标记**可工作的重要节点**，方便未来回溯：

**推荐 Tag 命名规则**：`v主版本.次版本.补丁` 或 `v日期-功能名`

| Tag 示例 | 含义 |
|---------|------|
| `v1.0.0` | 首次可运行版本 |
| `v1.1.0` | 新增 RBAC 权限系统 |
| `v1.2.0` | 新增对话用户隔离 |
| `v1.3.0` | 新增附件上传功能 |
| `v1.4.0` | ClickHouse TCP→HTTP 自动回退 |
| `v1.5.0` | customer_data 路径修复 |

**何时打 Tag**：
- 完成一个独立功能（可以完整演示）后
- 修复了影响核心流程的 bug 后
- 发布给他人使用前

---

## 三、执行 TODO LIST

> 确认后按顺序执行，每步可单独确认

### T1 — 创建 .gitignore 文件
**改动目标**：排除敏感文件、依赖包、缓存、运行时文件
**关键内容**：`.env*`、`node_modules/`、`__pycache__/`、`*.pyc`、`customer_data/*/reports/`（如选 B3）

### T2 — 初始化本地 Git 仓库
```bash
cd c:/Users/shiguangping/data-agent
git init
git add .
git commit -m "init: 项目首次提交"
```

### T3 — 创建远程仓库并关联
**需要**：在 GitHub/Gitee 创建 **Private** 仓库（名称建议：`data-agent`）
```bash
git remote add origin <仓库地址>
git push -u origin main
```
> 需要先在 GitHub/Gitee 注册账号并创建仓库，步骤见附录

### T4 — 补打历史 Tag（可选）
根据 memory 记录，为已完成的里程碑功能打 Tag：
```bash
git tag -a v1.0.0 -m "P0 核心 Agentic Loop"
git tag -a v1.1.0 -m "P1 Skill 系统 + 专项 Agent"
git tag -a v1.2.0 -m "RBAC 权限系统"
git tag -a v1.3.0 -m "对话用户隔离"
git tag -a v1.4.0 -m "附件上传功能"
git tag -a v1.5.0 -m "路径修复 + customer_data 重构"
git push origin --tags
```

### T5 — 配置 Git 用户信息
```bash
git config --global user.name "你的名字"
git config --global user.email "你的邮箱"
```

---

## 四、附录：GitHub 创建私有仓库步骤（T3 前置）

1. 打开 https://github.com，注册/登录
2. 右上角 `+` → `New repository`
3. Repository name: `data-agent`
4. 选择 **Private**（私有）
5. **不要**勾选 "Initialize this repository"（本地已有代码）
6. 点击 `Create repository`
7. 复制仓库地址（HTTPS 格式：`https://github.com/用户名/data-agent.git`）

**认证方式**：使用 SSH Key（已配置）

- 本机 SSH 密钥：`~/.ssh/id_ed25519`（ed25519，2026-04-03 生成）
- 远程地址：`git@github.com:sllcnhk/data-agent.git`（SSH 方式，国内 443 端口受阻时仍可用）
- 日常 push/pull 无需输入密码，一次配置永久生效

---

## 五、待确认决策点汇总

| 决策 | 选项 |
|------|------|
| **A** 远程仓库选哪个？ | GitHub（推荐）/ Gitee |
| **B** customer_data 纳管策略？ | B1全纳入 / B2全排除 / B3只含db_knowledge |
| **C** 分支策略？ | 单 main 分支（简单）/ main+dev（规范）|

---

*文档由 Claude Code 生成，确认后执行。*
