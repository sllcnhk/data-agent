# 数据分析工作流与 Skill 体系建设框架

> **版本**: v1.0 | **创建时间**: 2026-03-26
> **作者**: 基于项目现有架构分析设计

---

## 一、背景与问题诊断

### 1.1 核心痛点

用户反映的问题：
1. **无效探索**：命中 clickhouse-analyst skill 后，Agent 仍然调用 `list_tables` / `describe_table` 重新探索数据库，浪费 token 且拖慢响应
2. **知识库未用**：`db_knowledge/` 目录下已有详细的表文档、指标口径，但 Agent 不主动读取
3. **选错表**：在多环境（SG/IDN/BR/MY/THAI）情况下可能混淆表名或查错环境
4. **Skill 无层级**：通用分析 Skill 内容膨胀，缺乏按场景动态加载子 Skill 的机制
5. **知识库无维护机制**：随着数据库变更，本地 `db_knowledge/` 文件逐渐过期

### 1.2 根本原因分析

通过审查现有代码，根本原因如下：

| 问题 | 根本原因 |
|------|---------|
| Agent 仍然探索 DB | `clickhouse-analyst.md` 说"按需加载"，但没有**明确禁止**在知识库有记录时使用探索工具 |
| `db_knowledge` 未用 | 知识库路径写在 Skill 里，但 Agent 认为 ClickHouse 工具调用是"更权威"的途径 |
| 选错表 | 无环境感知路由；各环境没有独立的 Schema 快照 |
| 无层级 Skill | `SkillLoader` 目前仅支持"消息级"一次性加载，不支持父 Skill 声明子 Skill |
| 知识库无维护 | 没有专用维护 Workflow Skill |

---

## 二、对第三方建议的评估

第三方提出了 **"三层四类"** 框架（L1原子层 / L2逻辑组 / L3场景方案 × Global/Aggregator/Local-Spec）。

### 2.1 合理之处

| 建议 | 评估 | 适配方案 |
|------|------|---------|
| Metadata_Router 元数据路由 | ✅ 核心思路正确 | 用 Project Skill 实现，非代码中间件 |
| "禁止 SHOW TABLES/DESCRIBE" 约束 | ✅ **这是解决重复探索的关键** | 直接加入 Skill 内容和 `_base-safety.md` |
| 知识同步 Workflow | ✅ 必要 | 专用 Maintenance Skill |
| 环境隔离分类 | ✅ 正确 | 映射到 scope + env_tags frontmatter |

### 2.2 不适用或需修改之处

| 建议 | 问题 | 本项目适配方案 |
|------|------|--------------|
| L1 原子层（read_local_file, execute_sql） | ❌ 本项目 MCP 工具已是原子层，无需重建 | 直接使用现有 MCP 工具 |
| L2 逻辑组（get_table_schema_from_cache） | ❌ 这应是 Skill 内容，不是新工具 | 通过 Skill 指令定义"先读缓存"协议 |
| "中间件 Skill" 代码级别 | ⚠️ 在本项目中 Skill 是提示词注入，不是可执行中间件 | Skill 通过指令约束 Agent 行为 |
| 全局统一 Namespace 优先执行 | ⚠️ 与现有 3-tier 系统（system/project/user）冲突 | 通过 frontmatter `scope` 字段扩展现有体系 |

### 2.3 本质区别

> 第三方方案假设需要**新建工具层**；本项目 Skill 是**提示词注入系统**，解决方式应是"通过更强规则约束 LLM 行为"，而非添加新工具。

---

## 三、框架设计：知识优先分析体系

### 3.1 设计原则

1. **知识优先（Knowledge-First）**：有本地知识库时，禁止重复探索数据库
2. **规则优先于代码**：大部分改进通过加强 Skill 指令实现，减少代码变更
3. **按需加载（Lazy Loading）**：父 Skill 匹配后，按场景加载子 Skill
4. **环境感知路由**：自动识别用户意图所指环境，加载对应知识
5. **知识库闭环**：有专用 Workflow 保持知识库与数据库同步

### 3.2 架构总览

```
用户消息
   │
   ▼
[消息级路由 - 现有]
SkillLoader.build_skill_prompt_async()
   ├── 关键词匹配 Phase 1
   ├── LLM语义匹配 Phase 2 (hybrid mode)
   └── 返回匹配的 Parent Skills
          │
          ▼ [新增] Sub-skill 加载
   ├── 解析 parent skill 的 sub_skills 声明
   └── 按 env_tags / scope 过滤并追加子 Skill
          │
          ▼
[System Prompt 组装]
_base-safety.md (always_inject) ← 新增"禁止重探"规则
_base-tools.md  (always_inject)
+ 父场景 Skill (clickhouse-analyst.md)
+ 子场景 Skill (sg-specific.md / idn-specific.md等)
+ 元数据路由 Skill (db-knowledge-router.md)
          │
          ▼
[AgenticLoop 执行]
Agent 收到指令：
  1. FIRST: read_file(db_knowledge/_index.md)
  2. 确认目标表 → read_file(db_knowledge/tables/xxx.md)
  3. ONLY IF not in db_knowledge → 使用 ClickHouse 探索工具
  4. 构建并执行查询
```

### 3.3 Skill 分类体系（四维标签）

在现有 3-tier（system/project/user）基础上，新增 frontmatter 字段：

#### 维度一：范围（scope）
```yaml
scope: global-ch        # 所有 ClickHouse 环境通用
scope: aggregator       # 仅汇集库环境（多环境聚合分析）
scope: env-sg           # 仅 SG 新加坡环境
scope: env-idn          # 仅 IDN 印尼环境
scope: env-br           # 仅 BR 巴西环境
scope: env-my           # 仅 MY 马来西亚环境
scope: env-thai         # 仅 THAI 泰国环境
scope: env-mx           # 仅 MX 墨西哥环境
```

#### 维度二：层次（layer）
```yaml
layer: workflow         # 顶层工作流（入口，声明子 Skill）
layer: scenario         # 场景方案（特定业务场景）
layer: knowledge        # 知识文档（db_knowledge 入口）
layer: maintenance      # 维护工作流（知识库更新）
```

#### 维度三：子 Skill 声明（sub_skills）
```yaml
sub_skills:
  - sg-specific          # 环境特化子 Skill
  - call-metrics         # 呼叫指标子 Skill
  - billing-analysis     # 账单分析子 Skill
```

#### 维度四：环境标签（env_tags）
```yaml
env_tags: [sg, sg_azure]      # 仅在询问这些环境时加载
env_tags: [idn, br, my, thai] # 多环境通用（非SG）
```

### 3.4 目标 Skill 目录结构

```
.claude/skills/
├── system/
│   ├── _base-safety.md          [always_inject] ← 新增"禁止重探"全局规则
│   ├── _base-tools.md           [always_inject]
│   ├── etl-engineer.md          [triggered]
│   ├── schema-explorer.md       [triggered]
│   └── ...
├── project/
│   ├── db-knowledge-router.md   [triggered] ← NEW: 元数据路由 Workflow
│   ├── db-maintainer.md         [triggered] ← NEW: 知识库维护 Workflow
│   └── test-guide.md
└── user/{username}/
    ├── clickhouse-analyst.md    [triggered, layer=workflow] ← 升级为父 Skill
    ├── ch-sg-specific.md        [triggered, scope=env-sg]   ← NEW: SG 特化
    ├── ch-idn-specific.md       [triggered, scope=env-idn]  ← NEW: IDN 特化
    ├── ch-call-metrics.md       [triggered, layer=scenario] ← NEW: 呼叫指标场景
    └── ch-billing-analysis.md   [triggered, layer=scenario] ← NEW: 账单分析场景
```

---

## 四、db_knowledge 体系设计

### 4.1 现有结构（已较好，需补全）

```
customer_data/{username}/db_knowledge/
├── _index.md                    ✅ 已有（SG环境索引，需扩展多环境）
├── relationships.md             ✅ 已有（ERD + 业务链路）
├── tables/
│   ├── *.md                     ✅ 已有 9 张核心表文档
│   └── ...（待补全）
└── metrics/
    ├── *.md                     ✅ 已有 5 个指标文档
    └── ...（待补全）
```

### 4.2 多环境扩展目标

```
customer_data/{username}/db_knowledge/
├── _index.md                    ← 多环境汇总索引（当前仅SG）
├── sg/                          ← 新增：SG 专属知识库
│   ├── _index.md                ← SG 详细索引
│   ├── tables/*.md
│   └── metrics/*.md
├── idn/                         ← 新增：IDN 专属知识库
│   └── ...
├── relationships.md             ← 跨表关联键（通用）
└── common/                      ← 跨环境通用知识
    └── call_record_schema.md    ← 通用字段定义（各环境相同字段）
```

### 4.3 知识库 Agent 加载协议（强制规则）

以下规则将强制注入 `_base-safety.md`（always_inject）：

```
【知识库优先规则 - MANDATORY】
1. 分析 ClickHouse 数据前，必须先检查 db_knowledge 目录是否存在
2. 若存在，MUST 先 read_file(_index.md) 确认表名和字段
3. STRICTLY PROHIBITED: 在 db_knowledge 中已有表文档时，使用
   list_tables / describe_table / get_table_overview / sample_table_data
4. 仅当 db_knowledge 中明确找不到所需表时，才可使用数据库探索工具
5. 每次分析开始：declare "已读取 db_knowledge，使用本地知识库"
```

### 4.4 知识库维护 Workflow

**触发词**：`更新知识库`、`刷新表结构`、`同步表文档`、`db_knowledge 更新`

**执行步骤**：
```
Step 1: 确认目标环境（SG/IDN/BR/MY/THAI/MX）
Step 2: list_tables(database="crm") → 获取最新表清单
Step 3: 对比 db_knowledge/tables/ 目录 → 识别新增/变更表
Step 4: 对每张新增/变更表执行：
        a. describe_table → 字段清单+类型
        b. sample_table_data (LIMIT 3) → 数据示例
        c. 生成/更新 tables/<table_name>.md
Step 5: 更新 _index.md 中的表清单（版本号+1）
Step 6: 输出变更摘要：新增N张，更新M张，未变更K张
```

---

## 五、Sub-skill 动态加载机制

### 5.1 设计思路

不改变 `SkillLoader` 的核心架构（消息级加载），在现有流程末尾增加一步：**"若某 skill 声明了 sub_skills，则检查并追加子 Skill"**。

### 5.2 实现逻辑（伪代码）

```python
# 在 build_skill_prompt_async() 末尾，matched skills 确定后：
all_matched = user_skills + proj_skills + sys_skills

# Sub-skill 扩展
extra_sub_skills = []
for skill in all_matched:
    if skill.sub_skills:
        for sub_name in skill.sub_skills:
            sub = self._registry.get_by_name(sub_name)
            if sub and sub not in all_matched:
                # env_tags 过滤（可选）
                if sub.env_tags:
                    if _detect_env(message) not in sub.env_tags:
                        continue
                extra_sub_skills.append(sub)

# 追加后重新组装，受 _MAX_INJECT_CHARS 保护
```

### 5.3 环境检测函数（简单关键词映射）

```python
ENV_KEYWORDS = {
    "sg": ["sg", "新加坡", "singapore", "sg_azure"],
    "idn": ["idn", "印尼", "indonesia"],
    "br": ["br", "巴西", "brazil"],
    "my": ["my", "马来", "malaysia"],
    "thai": ["thai", "泰国", "thailand"],
    "mx": ["mx", "墨西哥", "mexico"],
}

def _detect_env(message: str) -> Optional[str]:
    msg_lower = message.lower()
    for env, keywords in ENV_KEYWORDS.items():
        if any(kw in msg_lower for kw in keywords):
            return env
    return None
```

---

## 六、实施计划与 TODO List

详见下方第七节。

---

## 七、与第三方框架的对比总结

| 维度 | 第三方方案 | 本框架方案 | 原因 |
|------|-----------|-----------|------|
| 解决重探问题 | 新建 Metadata_Router 工具 | 强化 Skill 指令中的禁止规则 | MCP 工具已完备，无需新建 |
| 元数据注入 | 代码层中间件 | `db-knowledge-router.md` Skill | Skill 系统即为提示词中间件 |
| 分层结构 | L1/L2/L3 原子→逻辑→场景 | 父 Skill + 子 Skill 声明加载 | 贴近现有 SkillLoader 架构 |
| 环境分类 | Global/Aggregator/Local-Spec | scope + env_tags frontmatter | 扩展现有 frontmatter 体系 |
| 知识库维护 | 独立 Maintenance Workflow | `db-maintainer.md` Skill | 与现有 Skill 系统统一 |
| 代码改动量 | 大（需建工具层） | 小（主要是 Skill 内容） | 最小化改动，最大化效果 |

---

*本框架文档配套 TODO List 见下方实施计划章节（独立跟踪）*
