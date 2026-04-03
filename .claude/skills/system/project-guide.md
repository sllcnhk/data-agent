---
name: project-guide
version: "1.0"
description: data-agent 系统架构导读与功能全景，在用户询问系统架构、已有功能、待实现功能、接口设计等时自动激活，读取架构文档后提供准确的框架理解
triggers:
  - 系统架构
  - 架构是什么
  - 架构设计
  - 整体架构
  - 架构图
  - 项目架构
  - 系统设计
  - 技术架构
  - 模块结构
  - 目录结构
  - 已有功能
  - 已实现
  - 已完成功能
  - 有哪些功能
  - 功能清单
  - 功能列表
  - 支持哪些
  - 待实现
  - 待开发
  - 规划功能
  - 下一步
  - 路线图
  - roadmap
  - 优先级
  - 待办
  - 接口文档
  - API设计
  - REST接口
  - SSE事件
  - 有哪些接口
  - 怎么工作
  - 工作原理
  - 数据流
  - 流程是
  - 代码结构
  - 项目结构
  - 从哪开始
  - 如何理解
  - 框架理解
  - 快速了解
  - 了解项目
  - 项目概览
category: system
priority: high
---

# data-agent 项目导读技能

## 使用本技能的时机

当用户询问以下任何问题时，本技能被激活，你应当首先读取项目文档再回答：

- "系统架构是什么？" / "项目整体是怎么设计的？"
- "已经实现了哪些功能？" / "有什么功能？"
- "还有什么没做？" / "下一步规划是什么？"
- "这个功能在哪个文件里？" / "代码结构怎么理解？"
- "SSE 事件有哪些类型？" / "REST 接口怎么设计的？"

---

## 必读文档路径

在回答任何架构/功能相关问题前，**必须先读取以下文档**：

1. **架构文档**（模块结构、数据流、API 端点）：
   `docs/ARCHITECTURE.md`

2. **功能注册表**（已实现/待实现功能、优先级、测试覆盖）：
   `docs/FEATURE_REGISTRY.md`

3. **内存摘要**（最近的开发进展、关键修复、已知问题）：
   `C:\Users\shiguangping\.claude\projects\c--Users-shiguangping-data-agent\memory\MEMORY.md`

---

## 回答框架

### 当用户问"系统架构"时

按以下结构回答：

```
1. 系统定位（1-2句话：这是什么系统，核心价值是什么）
2. 技术栈快照（后端/LLM/数据库/前端/MCP 各用什么）
3. 三个核心流程：
   - 用户发消息 → SSE 流式响应（AgenticLoop 推理循环）
   - ETL 高危操作 → 审批暂停/恢复流程
   - 推理接近上限 → 综合模式 + 自动续接
4. 关键文件速查（按模块列出最重要的 5-8 个文件）
5. 启动方式
```

### 当用户问"已有功能"时

按模块分组回答，参考 `FEATURE_REGISTRY.md` 第一章：
- P0: Agentic Loop 核心
- P1: SKILL.md + 专用 Agent
- P2: 审批 + 上下文管理
- P3: 双 ClickHouse + MCP 绑定
- 近限制综合 + 自动续接
- ClickHouse MCP Bug 修复

对每个功能给出：**功能名 → 所在文件 → 一句话说明**

### 当用户问"待实现功能/下一步"时

按优先级回答，参考 `FEATURE_REGISTRY.md` 第二章：
- P1 高优先级（可视化 Agent / 报表生成 / 任务调度 / 多环境切换）
- P2 中优先级
- P3 低优先级 / 探索性

每项给出：**功能描述 → 涉及模块 → 工作量估计**

### 当用户问"接口/API"时

参考 `ARCHITECTURE.md` 第 6 节（REST API 端点）和第 5 节（SSE 事件类型），
给出完整的端点列表和事件类型说明。

---

## 关键设计决策速查

| 问题 | 答案 |
|------|------|
| 为什么有两个 orchestrator？ | `orchestrator.py` (MasterAgent) 是主力，`orchestrator_v2.py` 是探索性的2-hop版本，尚未完全替换 |
| ETL 怎么防止误操作？ | 双层防护：`_DDL_KEYWORD_RE` 词边界正则(MCP层) + `_detect_dangerous_sql`(Agent层) + 审批暂停(ApprovalManager) |
| 分析 Agent 怎么防写操作？ | `ReadOnlyMCPProxy` 工具级过滤 + readonly ClickHouse 连接(DB层)，双重保险 |
| 推理太多轮怎么办？ | NEAR_LIMIT_THRESHOLD=5：剩余5轮时合成结论+待办JSON → auto_continue 最多3次 → 人工确认 |
| 技能如何动态生效？ | SkillWatcher watchdog 监听 `.claude/skills/` 变更，Debouncer 防抖后热重载 |
| ClickHouse create_time 为何不误报？ | `\bCREATE\b` 词边界正则：`create_time` 中 `_` 是单词字符，`CREATE` 前后无边界 |
| 对话历史太长怎么处理？ | `_maybe_summarize()` → LLM 摘要 → 注入 context；loop 内 `_compress_loop_messages()` 压缩旧 tool_result |

---

## 使用示例

**用户说**："我想了解这个系统的整体架构，从哪里入手比较好？"

**你应该**：
1. `read docs/ARCHITECTURE.md` — 获取完整架构说明
2. `read docs/FEATURE_REGISTRY.md` — 了解已完成和待做的功能
3. 基于文档内容，按"系统定位 → 核心数据流 → 模块速查 → 启动方式"结构回答

**用户说**："有哪些 SSE 事件类型？前端怎么处理续接？"

**你应该**：
1. `read docs/ARCHITECTURE.md` 第5节（SSE 事件类型）和第4.3节（近限制流程）
2. 结合 `backend/services/conversation_service.py` 和 `frontend/src/pages/Chat.tsx` 中的实现细节回答

**用户说**："下一步我想加可视化功能，应该怎么规划？"

**你应该**：
1. `read docs/FEATURE_REGISTRY.md` 第二章 P1 部分（可视化 Agent）
2. 参考现有 Agent 实现（`etl_agent.py` / `analyst_agent.py`）作为模板建议实现路径
