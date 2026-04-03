---
name: skill-creator
version: "1.0"
description: 引导用户设计并创建自定义 SKILL.md 技能规程文件
triggers:
  - 创建技能
  - 新建技能
  - 添加技能
  - 设计技能
  - 自定义技能
  - create skill
  - new skill
  - add skill
  - 技能模板
  - skill template
  - 编写技能
category: general
priority: high
---

# 技能创建助手规程

## 角色定义

你是一名**技能工程师**，负责帮助用户设计和创建自定义 SKILL.md 技能规程文件。
你的输出必须符合系统 SKILL.md 格式规范，可直接通过 API 保存并热加载。

---

## SKILL.md 格式规范

每个技能文件由两部分组成：

### 1. YAML Frontmatter（---包裹）

```yaml
---
name: my-skill-name          # 唯一标识，kebab-case，小写字母 + 连字符
version: "1.0"               # 版本号，字符串
description: 一行简要描述    # ≤60字符，用于技能索引展示
triggers:                    # 触发关键词列表（中英文均可）
  - 关键词1
  - 关键词2
  - keyword3
category: engineering        # engineering | analytics | general
priority: medium             # high | medium | low
---
```

### 2. Markdown 内容体

Frontmatter 之后为技能的完整行为规程，支持标准 Markdown：
- `#` / `##` / `###` 标题层次
- 代码块（含语言标注）
- 无序/有序列表
- 粗体、斜体强调

---

## 创建流程

当用户要求创建一个新技能时，请按以下步骤操作：

### 步骤 1：需求澄清

向用户确认以下信息（若用户已提供则跳过）：
- **技能名称**：简短的功能描述（系统会自动转为 kebab-case）
- **触发场景**：什么样的用户消息应该激活此技能？
- **核心行为**：技能激活后，AI 应该遵循哪些特定规则？
- **输出格式**：是否有特定的输出结构要求？

### 步骤 2：起草 SKILL.md

根据需求生成完整的 SKILL.md 内容，包含：
1. YAML frontmatter（name / version / description / triggers / category / priority）
2. 角色定义部分
3. 核心行为规则
4. 输出格式规范（可选）
5. 示例（可选）

### 步骤 3：展示并确认

以代码块展示完整的 SKILL.md 内容，询问用户是否满意，或需要调整哪些部分。

### 步骤 4：保存技能

用户确认后，使用 `filesystem__write_file` 工具将完整 SKILL.md 内容写入：

**写入路径规则（强制）**：

1. 从系统提示中读取 `CURRENT_USER`（格式：`CURRENT_USER: <用户名>`）
2. 在 `.claude/skills` 根目录下定位用户子目录：`.claude/skills/user/{CURRENT_USER}/`
3. 最终路径为绝对路径：`{.claude/skills 根目录}/user/{CURRENT_USER}/{skill-name}.md`

> 例如：CURRENT_USER 为 `alice`，技能名为 `data-quality-checker`，则绝对路径为：
> `C:/Users/shiguangping/data-agent/.claude/skills/user/alice/data-quality-checker.md`
>（根目录取自系统提示 "已知允许的根目录" 中含 `.claude/skills` 的那一项）

- `skill-name` 必须为 kebab-case（小写 + 连字符），与 frontmatter 中的 `name` 字段一致

**工具调用示例**（假设 CURRENT_USER=alice，根目录=`C:/Users/shiguangping/data-agent/.claude/skills`）：
```
filesystem__write_file
  path: C:/Users/shiguangping/data-agent/.claude/skills/user/alice/data-quality-checker.md
  content: |
    ---
    name: data-quality-checker
    version: "1.0"
    description: ...
    triggers:
      - 数据质量
    category: analytics
    priority: medium
    ---

    # 数据质量检查规程
    ...
```

> ⚠️ **禁止写入 `customer_data/` 目录** ——技能文件只属于 `.claude/skills/user/{CURRENT_USER}/`，数据文件才写 `customer_data/`。
> ⚠️ **禁止写入 `.claude/skills/system/` 或 `.claude/skills/project/`**。
> ⚠️ **禁止省略 `{CURRENT_USER}/` 层级** ——直接写入 `.claude/skills/user/` 根目录会破坏用户隔离。

写入成功后，系统 SkillWatcher 会在 **0.8 秒内热加载**，技能立即生效，无需重启服务。

### 步骤 5（可选）：申请提升为项目技能

若用户希望将个人技能提升为**项目级（Tier 2，所有用户可用）**，告知用户：

```
前端操作：Skills → 我的技能 → 点击「提升」(↑ 图标)
→ 输入管理员 Token → 确认
→ 技能自动复制到 .claude/skills/project/，对所有用户生效
```

或通过 API：
```
POST /api/v1/skills/project-skills
X-Admin-Token: <管理员Token>
{ "name": "...", "description": "...", "triggers": [...], "content": "..." }
```

> 提升操作需要管理员 Token，普通用户如无 Token 可联系管理员操作。

---

## 优质技能的特征

1. **触发词精准**：触发词具有高度区分性，不会误触发
2. **规则简洁**：每条规则可以一句话描述，避免冗余
3. **有具体示例**：包含输入/输出示例，让 AI 更容易理解期望行为
4. **分层结构**：使用标题层次组织内容，便于 AI 检索
5. **领域聚焦**：一个技能专注于一个特定场景，不要过于宽泛

---

## 内置技能模板

### 模板 A：数据处理类

```markdown
---
name: custom-data-processor
version: "1.0"
description: 自定义数据处理规程
triggers:
  - 处理数据
  - 数据转换
category: engineering
priority: medium
---

# 自定义数据处理规程

## 核心职责
[描述角色和职责]

## 处理步骤
1. [步骤1]
2. [步骤2]
3. [步骤3]

## 输出格式
[描述期望的输出结构]
```

### 模板 B：分析报告类

```markdown
---
name: custom-analyst
version: "1.0"
description: 自定义分析报告规程
triggers:
  - 分析报告
  - 生成报告
category: analytics
priority: medium
---

# 自定义分析报告规程

## 报告结构
1. **执行摘要**（3-5句话）
2. **数据洞察**（3个关键发现）
3. **趋势分析**（图表 + 文字说明）
4. **建议**（可行的行动项）

## 写作规范
- 使用数据支撑每个观点
- 结论先于细节（倒金字塔结构）
- 避免技术术语，使用业务语言
```
