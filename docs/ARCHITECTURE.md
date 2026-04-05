# data-agent 系统架构文档

> **适用对象**：LLM 模型（Claude Code / 其他 AI）、新加入开发者
> **目的**：快速理解系统整体架构、模块职责、数据流向和交互接口
> **最后更新**：2026-04-05（**Excel → ClickHouse 数据导入**：`migrate_data_import.py` DB 迁移 + 9 个 `/data-import/*` REST 端点 + `run_import_job` 后台协程（TabSeparated 格式 + 协作式取消）+ 大文件流式上传优化；**文件写入下载**：`files_written` SSE 事件 + `GET /api/v1/files/download` 安全下载端点 + 文件下载卡片 UI + `FILE_OUTPUT_DATE_SUBFOLDER` 月份子目录配置；技能路由可视化：`skill_matched` SSE 事件、`SkillLoader._last_match_info`、ThoughtProcess 🧠 技能路由面板、`GET /skills/load-errors`；侧边栏 Tab UI + 只读模式 + is_shared 群组框架；对话用户隔离；对话附件上传；ClickHouse TCP→HTTP 自动回退；对话打断；用户技能目录隔离修复）

---

## 1. 系统概述

**data-agent** 是一个 AI 原生的数据分析平台，以对话形式驱动 ClickHouse 数据库探索、ETL 设计和数据分析报告生成。核心特点：

- **Agentic Loop**：Claude LLM + MCP 工具调用形成多轮推理循环，每步事件（含新增 `skill_matched`）实时流式推送到前端
- **多 Agent 路由**：按用户意图自动路由到 ETL 工程师 / 数据分析师 / 通用助手三类 Agent
- **三层 SKILL.md 系统**：通过 `.claude/skills/{system,project,user}/` 三层目录动态注入领域专业知识，支持 `always_inject`、关键词+语义混合命中和 16000 字符 context 保护
- **动态多区域 ClickHouse**：`.env` 中自由增加 `CLICKHOUSE_{ENV}_*` 配置即可动态注册新区域；ETL Agent 使用 admin 权限，分析 Agent 使用 readonly 权限，独立安全层
- **RBAC 认证系统**：JWT（access_token 120min + refresh_token 14d 轮换）+ 角色权限（viewer/analyst/admin/superadmin）；`ENABLE_AUTH=false` 完全向后兼容
- **Session 过期管理**：refresh_token 为 Session Cookie（浏览器关闭自动清除）；`SESSION_IDLE_TIMEOUT_MINUTES=120`（默认）空闲超时后 `/auth/refresh` 返回 401；用户活跃状态节流写入 `users.last_active_at`（每 5 分钟最多写一次，后台任务不阻塞响应）

---

## 2. 技术栈

| 层次 | 技术 |
|------|------|
| 后端框架 | FastAPI + uvicorn，Python 3.10+ |
| LLM 适配 | Claude (Anthropic SDK)，扩展支持 OpenAI/Gemini/Doubao |
| 数据库 ORM | SQLAlchemy + PostgreSQL（对话历史、配置、RBAC 用户数据） |
| 认证 | JWT (python-jose，HS256)，bcrypt 密码哈希，httpOnly Cookie refresh_token |
| MCP 协议 | 自研 BaseMCPServer 框架，ClickHouse/MySQL/Filesystem/Lark |
| 流式通信 | SSE (Server-Sent Events)，FastAPI StreamingResponse |
| 前端框架 | React 18 + TypeScript + Ant Design 5 |
| 状态管理 | Zustand |
| 向量存储 | ChromaDB（语义压缩、相似对话检索） |

---

## 3. 目录结构

```
data-agent/
├── run.py                          # 启动入口（添加 sys.path 后启动 uvicorn）
├── backend/
│   ├── main.py                     # FastAPI app 定义，中间件，路由挂载
│   ├── agents/
│   │   ├── agentic_loop.py         # ★ 核心推理循环 AgenticLoop
│   │   ├── orchestrator.py         # ★ MasterAgent 意图路由
│   │   ├── orchestrator_v2.py      # AgentOrchestrator v2（HandoffPacket 2-hop）
│   │   ├── etl_agent.py            # ETLEngineerAgent + ETLAgenticLoop（SQL安全）
│   │   └── analyst_agent.py        # DataAnalystAgent + ReadOnlyMCPProxy
│   ├── api/
│   │   ├── conversations.py        # /conversations 路由（含 /stream SSE）
│   │   ├── approvals.py            # /approvals 审批接口
│   │   ├── skills.py               # /skills 三层技能 CRUD（用户/项目/preview/列表）
│   │   ├── auth.py                 # ★ /auth 认证端点（login/refresh/logout/me）
│   │   ├── users.py                # ★ /users + /roles 用户 CRUD 与角色分配
│   │   ├── deps.py                 # FastAPI 依赖：get_current_user / require_permission / require_admin
│   │   ├── mcp.py                  # /mcp 服务器管理
│   │   ├── groups.py               # /groups 对话分组
│   │   ├── files.py                # ★ GET /files/download — 安全文件下载端点（路径验证 + 用户隔离）
│   │   ├── data_import.py          # ★ /data-import/* — Excel → ClickHouse 数据导入（9 端点，superadmin 专属）
│   │   └── llm_configs.py          # /llm-configs LLM 配置
│   ├── core/
│   │   ├── approval_manager.py     # ApprovalManager 单例（async Event 暂停）
│   │   ├── cancel_manager.py       # ★ ConversationCancelManager（asyncio.Event 每 conv 一份，懒创建）
│   │   ├── agent_mcp_binder.py     # FilteredMCPManager + AgentMCPBinder
│   │   ├── filesystem_permission_proxy.py  # ★ 目录级写权限代理（允许写 customer_data/（覆盖所有 {username}/ 子目录）+ skills/user/）
│   │   ├── context_manager.py      # HybridContextManager + SmartCompressionStrategy
│   │   ├── conversation_summarizer.py  # LLM 摘要 + 规则兜底
│   │   ├── rbac.py                 # ★ get_user_permissions() / get_user_roles()
│   │   └── auth/
│   │       ├── jwt.py              # ★ create_access_token / decode_token / create_refresh_token_jti
│   │       ├── password.py         # ★ hash_password / verify_password（bcrypt）
│   │       └── providers/
│   │           └── local.py        # ★ LocalAuthProvider.authenticate()
│   ├── mcp/
│   │   ├── manager.py              # MCPServerManager（管理所有 MCP 实例；服务器名纯连字符规范化）
│   │   ├── tool_formatter.py       # format_mcp_tools_for_claude / parse_tool_name
│   │   └── clickhouse/
│   │       ├── server.py           # ★ ClickHouseMCPServer（TCP 9000 探测→失败自动回退 HTTP 8123）
│   │       └── http_client.py      # ★ ClickHouseHTTPClient（requests-based，execute() 兼容 clickhouse-driver）
│   ├── services/
│   │   ├── conversation_service.py # ★ SSE 流处理、自动续接、上下文压缩
│   │   └── data_import_service.py  # ★ run_import_job() 后台协程（分批 insert_tsv + 协作式取消）
│   ├── skills/
│   │   ├── skill_loader.py         # SkillLoader 单例；3层加载；build_skill_prompt_async()；_MAX_INJECT_CHARS=16000 保护
│   │   ├── skill_semantic_router.py  # ★ LLM 批量路由器（单次调用对候选 skill 打分，0-1.0）
│   │   ├── skill_routing_cache.py  # ★ ChromaDB 持久化路由缓存（TTL 24h，精确哈希匹配）
│   │   └── skill_watcher.py        # watchdog 热重载监视器（recursive=True，监听3层子目录）
│   ├── models/
│   │   ├── __init__.py             # ★ RBAC 模型必须优先导入（SQLAlchemy mapper 初始化顺序）
│   │   ├── user.py                 # ★ User（uuid PK，bcrypt hash，is_superadmin）
│   │   ├── role.py                 # ★ Role（viewer/analyst/admin/superadmin）
│   │   ├── permission.py           # ★ Permission（resource:action 键值，如 users:write）
│   │   ├── user_role.py            # ★ UserRole 关联表（user_id + role_id + assigned_by）
│   │   ├── role_permission.py      # ★ RolePermission 关联表
│   │   ├── refresh_token.py        # ★ RefreshToken（jti uuid，轮换/revoke 状态）
│   │   ├── import_job.py           # ★ ImportJob（数据导入任务，状态机 + 进度追踪 + JSONB 配置快照）
│   │   └── ...                     # 其他模型（Conversation/Message/Task/Report 等）
│   ├── scripts/
│   │   ├── init_rbac.py            # ★ 初始化 4 角色 + 13 权限（幂等，首次部署运行）
│   │   └── migrate_data_import.py  # ★ 创建 import_jobs 表 + 种子 data:import 权限（幂等）
│   └── config/
│       └── settings.py             # Pydantic Settings（环境变量映射，含 RBAC 配置项）
├── frontend/src/
│   ├── pages/Chat.tsx              # ★ 主聊天页面（SSE 消费、Modal 渲染、附件 chips 展示）
│   ├── pages/Roles.tsx             # ★ 角色权限管理页面（卡片视图 + 权限分配弹窗；users:read 权限）
│   ├── pages/DataImport.tsx        # ★ Excel 数据导入页面（3步骤向导：选连接/上传→配置Sheet→进度监控；data:import 权限）
│   ├── services/dataImportApi.ts   # ★ 数据导入 API 客户端（9 个方法，大文件 timeout=10min）
│   ├── store/useChatStore.ts       # Zustand store（消息、审批、续接、isCancelling 状态）
│   ├── store/useAuthStore.ts       # ★ JWT 认证状态（access_token 内存；initAuth 4路径；isAnonymousUser；authChecked）
│   ├── App.tsx                     # ★ 路由定义；RequireAuth（authChecked 防闪烁）；initAuth() on mount
│   ├── components/AppLayout.tsx    # 导航菜单（含 /roles 角色权限，perm: users:read）
│   └── components/chat/
│       ├── ApprovalModal.tsx       # 60秒倒计时审批 Modal
│       ├── ThoughtProcess.tsx      # 可折叠推理过程面板
│       ├── ContinuationCard.tsx    # ★ Agent 续接提示横幅（role='continuation'，非消息气泡）
│       ├── ChatInput.tsx           # ★ 聊天输入框（附件上传按钮 + 粘贴图片 + 附件预览 chips；inferMimeType 扩展名回退）
│       ├── ChatMessages.tsx        # 消息列表（含 continuation 路由渲染；历史附件 chips 展示；文件下载卡片 FileDownloadCards）
│       └── AgentBadge.tsx          # Agent 类型徽章
├── customer_data/                  # ★ Agent 文件写入区（按用户隔离子目录）
│   ├── superadmin/                 # superadmin 用户数据（含历史迁移数据）
│   │   ├── db_knowledge/           # 数据库元数据知识库
│   │   ├── imports/                # Excel 数据导入临时文件（完成/失败后自动清理）
│   │   └── reports/                # 分析报告输出
│   └── {username}/                 # 其他用户各自独立子目录
│       └── imports/                # 各用户的 Excel 导入临时文件
└── .claude/
    ├── agent_config.yaml           # Agent→MCP 绑定配置（ClickHouse 权限 + 迭代次数）
    └── skills/                     # 三层技能目录（系统/项目/用户）
        ├── system/                 # Tier 1：系统技能（只读，开发人员维护）
        │   ├── _base-safety.md     # ★ 始终注入：数据安全约束
        │   ├── _base-tools.md      # ★ 始终注入：MCP 工具使用规范
        │   ├── etl-engineer.md
        │   ├── schema-explorer.md
        │   ├── clickhouse-analyst.md
        │   ├── project-guide.md
        │   └── skill-creator.md
        ├── project/                # Tier 2：项目技能（管理员 REST API 维护）
        │   └── *.md
        └── user/                   # ★ Tier 3：用户技能（API 和前端均可增删改）
            ├── *.md                # ENABLE_AUTH=false：所有用户共用 flat 目录（向后兼容）
            └── {username}/         # ENABLE_AUTH=true：每个用户独立子目录（隔离写入）
                └── *.md
```

---

## 4. 核心数据流

### 4.1 用户发消息 → SSE 流式响应

```
前端 Chat.tsx
  └─ POST /api/v1/conversations/{id}/messages (or /stream)
       └─ conversations.py → get_current_user() → 提取 _username（Bearer JWT 解析，ENABLE_AUTH=false 时为 "default"）
       └─ conversation_service.send_message_stream(username=_username, attachments=[...])
            ├─ add_message() → 保存用户消息
            │    └─ attachments 不为空 → extra_metadata["attachments"] = [{name,mime_type,size},...] (无 base64)
            ├─ _maybe_summarize() → 必要时 LLM 压缩历史
            ├─ _build_context(username=_username) → context["username"] = _username
            │    └─ 历史消息有 extra_metadata["attachments"] → content 追加 [附件: name (mime, size bytes)]
            ├─ context["current_attachments"] = attachments  ← 含完整 base64，注入到 context
            ├─ MasterAgent.process_stream(content, context)
            │    ├─ _select_agent() → 关键词打分 → ETL/Analyst/General
            │    └─ agent.process_stream()
            │         └─ AgenticLoop.run_streaming()
            │              ├─ _perceive(message, context)
            │              │    └─ context["current_attachments"] → 多模态 content blocks
            │              │         image/* → {"type":"image","source":{base64,media_type}}
            │              │         application/pdf → {"type":"document",{base64}}
            │              │         text/*/json → base64解码 → 文本块
            │              for iteration in 1..max_iterations:
            │                ├─ LLM chat_with_tools() → stop_reason
            │                ├─ [end_turn] yield content → return
            │                ├─ [tool_use]
            │                │    ├─ 近限检测: remaining <= 5 → 综合模式
            │                │    ├─ 停滞检测: 相同调用 >= 2 → yield error
            │                │    └─ _execute_tool() → yield tool_call/result
            │                └─ [max_tokens] 自动续写 → continue
            ├─ 收集 thinking/tool_call/tool_result 事件 → thinking_events 列表
            │    └─ tool_result.data > 2000字符 → 截断 + "…（已截断）"
            ├─ 捕获 near_limit 事件 → 自动续接(最多3次) or 人工确认
            └─ add_message() → 保存助手消息（extra_metadata['thinking_events'] = 事件列表）
  └─ SSE 事件流 → 前端逐事件渲染
```

### 4.2 对话打断（停止生成）流程

```
用户点击「停止生成」按钮（Chat.tsx）
  → 前端: POST /api/v1/conversations/{id}/cancel
  → 后端: cancel_manager.request_cancel(conv_id) → asyncio.Event.set()
  │
  → AgenticLoop._cancellable_await()
  │    asyncio.wait({lm_task, cancel_event.wait()}, FIRST_COMPLETED)
  │    → cancel_event 先触发 → 取消 lm_task → raise _CancelledByUser
  │
  → AgenticLoop: except _CancelledByUser
  │    → yield AgentEvent(type="cancelled", data=<已生成文本>)
  │    → return
  │
  → send_message_stream: was_cancelled=True
  │    → final_content += "\n\n---\n*（生成已被用户中断）*"
  │    → asst_extra_meta["cancelled"] = True
  │    → 保存助手消息到 DB
  │    → yield {"type":"assistant_message", ...}
  │    → yield {"type":"done"}
  │
  → 前端 onChunk(type="cancelled"): message.info("已停止生成")
  → 前端 onComplete(): setSending(false); setIsCancelling(false)
  → 1 秒后: AbortController.abort()（兜底，关闭 fetch reader）
  → 下次发消息: cancel_manager.clear(conv_id) → 状态干净，正常进行
```

**关键设计**：

| 决策 | 原因 |
|------|------|
| `asyncio.Event` per conv | 同一 uvicorn 进程单 event loop；零外部依赖 |
| `asyncio.wait(FIRST_COMPLETED)` | LLM HTTP 请求中途中断，不等待完整响应 |
| 工具调用后边界检测 | 工具调用通常 <5s，在调用后检测足够；避免中途打断 MCP 工具 |
| 保存 partial content | 避免用户丢失已生成的有价值输出 |
| 延迟 1s abort fetch | 给后端时间完成 `assistant_message` SSE 推送，前端不丢最后几个事件 |
| send_message_stream 开头 clear | 每条新消息从干净状态开始，取消不污染后续请求 |
| 无 RBAC 保护 | 取消端点与全部 12 个对话端点一致，均无服务端鉴权；前端停止按钮仅在认证用户的聊天界面可见 |

### 4.3 对话附件上传流程（2026-03-23）

```
前端 ChatInput.tsx
  ├─ PaperClipOutlined 按钮 → <input type="file" accept=".jpg,.png,.gif,.webp,.pdf,.txt,.csv,.md,.json">
  │    → processFile(file): inferMimeType(file) → ALLOWED_MIME_TYPES 校验 → fileToBase64()
  │    → 若不支持 → antMessage.error("不支持的文件类型")  ← Bug-2 修复（原仅 console.warn）
  │    → 若超过 20MB → antMessage.error("文件过大")
  ├─ 粘贴事件（ClipboardEvent）→ item.type.startsWith("image/") → processFile(getAsFile())
  └─ attachments: Array<{name, mime_type, size, data: base64}>  ← 全在内存，不落本地磁盘

chatApi.ts.sendMessageStream(content, attachments)
  └─ POST /conversations/{id}/messages
       body: { content, stream: true,
               attachments?: [{name, mime_type, size, data},...] }   ← base64 随 JSON body 传输
               (仅 attachments.length > 0 时才加入 body)

conversations.py   POST /{id}/messages
  └─ SendMessageRequest: content + stream + attachments: List[AttachmentData]
       AttachmentData: {name: str, mime_type: str, size: int, data: str}
  └─ service.send_message_stream(attachments=[a.model_dump() for a in request.attachments])

conversation_service.send_message_stream(attachments=[...])
  ├─ add_message(role='user', extra_metadata={"attachments": [{name,mime_type,size}]})
  │    ↑ 仅保留三字段元数据，base64 不写入 DB（避免数据库膨胀）
  ├─ _build_context()
  │    └─ 历史消息 extra_metadata["attachments"] 非空 →
  │         content += "\n[附件: photo.jpg (image/jpeg, 1024 bytes), ...]"
  │         （历史注解供 LLM 知晓上文曾有附件，但不重新传送 base64）
  ├─ context["current_attachments"] = attachments  ← 完整对象含 data 字段
  └─ MasterAgent → AgenticLoop._perceive(message, context)
       └─ context["current_attachments"] → messages[-1]["content"] 变为 list:
            [{"type":"text","text":"..."}, {"type":"image","source":{...}}]

Claude API (claude.py)
  └─ chat_with_tools(messages=[..., {role:"user", content:[text,image,document,...]}])
       → 原生支持多模态，无需特殊适配
```

**支持的附件类型**

| MIME 前缀 / 类型 | Claude API 块类型 | 说明 |
|-----------------|-----------------|------|
| `image/jpeg`, `image/png`, `image/gif`, `image/webp` | `type: "image"` | 直接识别图片内容 |
| `image/*`（其他，如 BMP）| `type: "image"`，media_type 回退为 `image/jpeg` | |
| `application/pdf` | `type: "document"` | PDF 原文分析 |
| `text/plain`, `text/csv`, `text/markdown`, `application/json` | `type: "text"` | base64 解码后嵌入文本 |

**存储说明**：附件元数据（name/mime_type/size）存入 `messages.extra_metadata["attachments"]`，无需数据库迁移（JSONB 列已有）。base64 数据**不**写入数据库。

### 4.4 ETL Agent 审批流

```
ETLAgenticLoop.run_streaming()
  ├─ 检测 tool_call 中的 SQL 是否含高危操作（_detect_dangerous_sql）
  ├─ 若高危：
  │    ├─ approval_manager.register() → 生成 approval_id
  │    ├─ yield approval_required 事件 → SSE → 前端 ApprovalModal 弹出（60s倒计时）
  │    └─ await approval_manager.wait_for_decision()  ← Python 异步暂停
  │         ├─ 用户点"同意" → POST /api/v1/approvals/{id}/approve → 恢复
  │         └─ 用户点"拒绝" → POST /api/v1/approvals/{id}/reject → 跳过
  └─ 继续执行工具调用
```

### 4.5 近限制综合 + 自动续接

```
AgenticLoop（iteration N，remaining <= NEAR_LIMIT_THRESHOLD=5）
  └─ stop_reason == "tool_use"
       ├─ _synthesize_and_wrap_up() → LLM 无工具调用，输出：
       │    "### 阶段性分析结论\n...\n### 待完成任务\n```json\n[...]```"
       ├─ yield content(near_limit=True)
       └─ yield near_limit{pending_tasks, conclusions}

ConversationService.send_message_stream(_continuation_round=N)
  ├─ _continuation_round > 0 → add_message(role='continuation', extra_metadata={
  │                              continuation_round: N, max_rounds: 3,
  │                              pending_tasks: [...], conclusions: "..."})
  │   → SSE yield user_message(role='continuation') → ContinuationCard 横幅渲染
  │
  └─ 检测到 near_limit 事件
       ├─ count < 3 → _set_auto_continue_state(count+1)
       │              yield auto_continuing → 前端 toast
       │              递归调用 send_message_stream(continuation_msg, _continuation_round=count+1)
       └─ count >= 3 → yield continuation_approval_required → 前端 Modal

_build_context()（LLM 上下文构建）：
  └─ role='continuation' 消息 → 转换为 role='user'（MessageRole 枚举仅含 USER/ASSISTANT/SYSTEM/TOOL）
```

### 4.6 MCP Agent 绑定 + 文件系统权限

```
.claude/agent_config.yaml
  etl_engineer: clickhouse_connection: admin
  analyst:      clickhouse_connection: readonly
  general:      clickhouse_connection: readonly

AgentMCPBinder.get_filtered_manager(agent_type, mcp_manager)
  ├─ FilteredMCPManager（只暴露该 Agent 有权限的 ClickHouse 服务器）
  └─ FilesystemPermissionProxy（当 filesystem 可访问时自动包裹，目录级写权限控制）

文件系统三层安全架构：
  AgenticLoop
    → FilesystemPermissionProxy    (目录级写权限：customer_data/ + .claude/skills/user/)
        → FilteredMCPManager       (服务器级可见性控制)
            → FilesystemMCPServer  (可访问范围：customer_data/ + .claude/skills/)

目录权限矩阵：
  .claude/skills/*.md (系统技能)               — 读 ✓  写 ✗
  .claude/skills/user/*.md  (ENABLE_AUTH=false) — 读 ✓  写 ✓  (所有用户共用 flat 目录)
  .claude/skills/user/{username}/*.md           — 读 ✓  写 ✓  (ENABLE_AUTH=true，每用户独立子目录)
  customer_data/{username}/**                   — 读 ✓  写 ✓  (用户只写自己的子目录，由 LLM 路径规则约束)
  backend/ frontend/ 等源代码                   — 读 ✗  写 ✗（FilesystemMCPServer 层拒绝）

DataAnalystAgent → ReadOnlyMCPProxy（第三层防护，ClickHouse 写 SQL 过滤）
```

### 4.7 JWT 认证流程

```
客户端
  └─ POST /api/v1/auth/login  {username, password}
       └─ LocalAuthProvider.authenticate()
            ├─ db.query(User).filter(username=..., is_active=True)
            ├─ bcrypt.checkpw(password, user.hashed_password)
            └─ _issue_tokens(user, db)
                 ├─ create_access_token({sub: user.id, username, roles}, expires=120min)
                 │    → JWT payload: {sub, username, roles, exp, iat, jti}
                 ├─ RefreshToken(jti=uuid4(), user_id=...) → db.add()
                 ├─ user.last_login_at = utcnow()
                 └─ 响应: {access_token, token_type, expires_in}
                          + Set-Cookie: refresh_token=<jti>; HttpOnly; SameSite=Lax; Path=/api/v1/auth
                            （无 max_age/expires → Session Cookie，浏览器关闭时自动清除）

请求鉴权（ENABLE_AUTH=true）：
  Authorization: Bearer <access_token>
  └─ get_current_user()
       ├─ decode_token(token, JWT_SECRET) → payload
       ├─ payload["sub"] → user_id
       └─ db.query(User).filter(id=user_id, is_active=True) → User

权限检查：
  Depends(require_permission("users", "write"))
  └─ _check(current_user=Depends(get_current_user), db)
       ├─ current_user.is_superadmin → 直接通过
       └─ get_user_permissions(current_user, db) → ["chat:use", "skills.user:write", ...]
            └─ "users:write" in perms ? 通过 : 403

Token 轮换（防重放）：
  POST /api/v1/auth/refresh  Cookie: refresh_token=<jti>
  └─ db.query(RefreshToken).filter(jti=jti, revoked=False)
       ├─ 找到 → old_token.revoked=True（旧 token 作废）
       │         new_jti = uuid4() → 新 RefreshToken 写库
       │         return {access_token: new_jwt}
       └─ 未找到/已 revoked → 401
```

### 4.8 前端认证初始化（initAuth）

```
浏览器打开页面
  └─ App.tsx useEffect → useAuthStore.initAuth()

initAuth() 四路径检测（useAuthStore.ts）：

  [路径 1] accessToken 存在（内存）
    └─ GET /auth/me  Authorization: Bearer <token>
         ├─ 200 → 解析 UserInfo
         │    ├─ isAnonymousUser(user) → ENABLE_AUTH=false → auth_enabled='false'
         │    └─ 正常用户 → auth_enabled='true'
         ├─ 401 → token 过期 → 清空 accessToken → 继续路径 2
         └─ 其他错误 → markAuthRequired()（失败安全）→ STOP

  [路径 2] 无 accessToken → 尝试 refresh Cookie
    └─ POST /auth/refresh  （浏览器自动携带 httpOnly Cookie）
         ├─ 200 → 写入新 accessToken → GET /auth/me → 恢复用户 → STOP
         └─ 失败 → 继续路径 3/4

  [路径 3] 无 Cookie → 探测 ENABLE_AUTH 状态
    └─ GET /auth/me  （无 Authorization 头）
         ├─ 200 + isAnonymousUser → ENABLE_AUTH=false → auth_enabled='false' → STOP（无需登录）
         ├─ 200 + 真实用户 → auth_enabled='true' → STOP（已登录）
         └─ 401/网络错误 → markAuthRequired()（路径 4）→ STOP

markAuthRequired()（失败安全默认值）：
  └─ localStorage.auth_enabled='true' + set({user:null, accessToken:null, authChecked:true})

isAnonymousUser(user) = user.id==='default' && user.username==='default'
  注意：AnonymousUser.username 为 'default'，非 'anonymous'

RequireAuth 组件（App.tsx）：
  ├─ authChecked=false → <Spin> 等待（防止 initAuth 完成前闪烁跳转）
  ├─ auth_enabled='true' && !accessToken && !user → <Navigate to="/login">
  └─ 其他 → 渲染子组件
```

### 4.9 Session 过期管理（空闲超时 + 浏览器关闭登出）


```
设计原则：ACCESS_TOKEN_EXPIRE_MINUTES（120min）= SESSION_IDLE_TIMEOUT_MINUTES（120min）
  → access_token 过期时必然触发 /auth/refresh → 空闲检测在此处执行

浏览器关闭登出：
  refresh_token Cookie 设置为 Session Cookie（无 max_age/expires）
  → 浏览器关闭时 Cookie 自动清除
  → 重新打开后无 Cookie → /auth/refresh 返回 401 → 跳转登录页

空闲超时检测（/auth/refresh 端点）：
  ENABLE_AUTH=true 时执行：
    activity_ts = user.last_active_at OR user.last_login_at（兜底）
    if activity_ts is None → 跳过检测（新账号首次登录）
    idle_min = (utcnow - activity_ts) / 60
    if idle_min > SESSION_IDLE_TIMEOUT_MINUTES:
      rt.revoked = True → db.commit()
      → 401 {"detail": "会话已超时，请重新登录"}

活跃状态追踪（get_current_user / deps.py）：
  每次认证请求 → 节流判断：
    now - user.last_active_at > _ACTIVITY_THROTTLE_SEC（5 分钟）?
      yes → background_tasks.add_task(_update_last_active, user_id)
             → 独立 DB Session 写 users.last_active_at = utcnow()
             → 不阻塞当前请求响应
      no  → 跳过（避免每请求写一次 DB）

完整空闲超时流程：
  用户停止操作 120min
    → access_token 过期（120min TTL）
    → 前端 401 → 自动调用 /auth/refresh（Cookie 携带）
    → 检测 last_active_at：距今 > 120min → 401 "会话已超时"
    → 前端 useAuthStore: set({user:null}) → React Router → 跳转 /login
```

相关配置：
```ini
ACCESS_TOKEN_EXPIRE_MINUTES=120       # 须 ≤ SESSION_IDLE_TIMEOUT_MINUTES
SESSION_IDLE_TIMEOUT_MINUTES=120      # 空闲超时（分钟）
REFRESH_TOKEN_EXPIRE_DAYS=14          # refresh_token DB 记录保留时长（非 Cookie 有效期）
```

数据库：`users.last_active_at TIMESTAMP WITHOUT TIME ZONE`（`backend/migrations/add_user_last_active_at.py`）

### 4.10 ClickHouse TCP→HTTP 自动回退（2026-03-20）

```
ClickHouseMCPServer.initialize(env)
  ├─ TCP 探测: ClickHouseClient(host, port=9000, connect_timeout=5).execute("SELECT 1")
  │    ├─ 成功 → self.client = tcp_client; self._protocol = "native"
  │    └─ 失败（ConnectionRefused / Timeout）→
  │         HTTP 探测: ClickHouseHTTPClient(host, port=8123).execute("SELECT 1")
  │           ├─ 成功 → self.client = http_client; self._protocol = "http"
  │           └─ 失败 → 抛出异常，服务器注册失败
  └─ _register_tools() / _register_resources()

_test_connection() 返回字段:
  {"connected":true, "protocol":"native"/"http", "active_port":9000/8123, ...}

服务器命名规范（manager.py）:
  env 名称中的下划线统一转为连字符 → 注册名 "clickhouse-sg-azure"（非 "clickhouse-sg_azure"）
  目的：确保 tool_formatter encode/decode 往返一致（tools 以双下划线分隔，env 中下划线会冲突）
```

**ClickHouseHTTPClient（`backend/mcp/clickhouse/http_client.py`）**：
- `requests`-based，`execute(query, with_column_types, settings)` 接口兼容 `clickhouse-driver`
- SELECT/SHOW/DESCRIBE → `FORMAT JSONCompact` 解析列名与行
- 非 SELECT → 纯 POST，返回空结果集
- 错误处理：`ConnectionError` / `TimeoutError` / HTTP 非 200 → `RuntimeError`

### 4.11 ClickHouse 动态环境发现与注册

```
启动时：settings.py 模块加载
  └─ load_dotenv(override=False) 依次读取 .env 候选：
       cwd/.env → backend/.env → 项目根/.env（高优先级先加载，override=False 先到先得）
       → 所有键（含未声明的 CLICKHOUSE_THAI_HOST 等）写入 os.environ

settings = Settings()
  ├─ Config.extra = "ignore"：未知 env 字段不报 ValidationError
  ├─ get_all_clickhouse_envs() 双源发现：
  │    Source 1: model_fields 扫描已声明的 env（idn/sg/mx）
  │    Source 2: os.environ 扫描 CLICKHOUSE_*_HOST 前缀，提取新 env 名（thai/br/sg_azure/my 等）
  └─ get_clickhouse_config(env, level) / has_readonly_credentials(env)：
       先 getattr(settings, f"clickhouse_{env}_host")
       → AttributeError → 回退 os.environ 大小写不敏感扫描

MCPServerManager.initialize_all()
  └─ for env in settings.get_all_clickhouse_envs():
       ├─ cfg["host"] 非空 → create_clickhouse_server(env, level="admin")
       │    → 注册 clickhouse-{env}
       └─ has_readonly_credentials(env) → create_clickhouse_server(env, level="readonly")
            → 注册 clickhouse-{env}-ro

  → 启动日志（INFO）：
    "[MCPManager] Initialization complete: N server(s) registered: clickhouse-idn, clickhouse-thai, ..."

AgentMCPBinder（agent_config.yaml: clickhouse_envs: all）
  └─ _extract_envs_from_manager() 自动发现所有已注册 clickhouse-* 服务器
       → 无需手动更新 yaml 即可感知新区域
```

**新增区域操作流程**（仅需重启，无需改代码）：
```
1. 在 .env 追加（以 THAI 为例）：
   CLICKHOUSE_THAI_HOST=122.8.155.77
   CLICKHOUSE_THAI_PORT=9000
   CLICKHOUSE_THAI_DATABASE=crm
   CLICKHOUSE_THAI_USER=wizadmin
   CLICKHOUSE_THAI_PASSWORD=<密码>

2. 重启后端 → MCPManager 自动注册 clickhouse-thai
3. 分析师/ETL 请求自动可用新区域（AgentMCPBinder 自动发现）
```

### 4.13 对话用户隔离（2026-03-24）

```
DB 层：
  conversations.user_id       UUID FK → users.id   ON DELETE SET NULL（nullable）
  conversation_groups.user_id UUID FK → users.id   ON DELETE SET NULL（nullable）
  索引：idx_conversations_user_id / idx_conversation_groups_user_id

service 层（conversation_service.py）：
  create_conversation(user_id=)
    └─ user_id 写入 DB（ENABLE_AUTH=false → user_id=None）

  list_conversations(user_id=None)
    ├─ user_id 有值 → WHERE user_id = uid（只返回该用户的对话）
    └─ user_id=None → 无过滤（返回全部，兼容匿名模式）

  list_all_conversations_by_user(exclude_user_id=superadmin_id)
    └─ 按 user_id 分组，排除 exclude_user_id，跳过无对话用户
       → List[{user_id, display_name, conversations: [...]}]

API 层（conversations.py）：
  _get_user_id(user)
    ├─ AnonymousUser（id="default"）→ None
    └─ 真实用户 → UUID

  _is_superadmin(user)
    └─ user.is_superadmin（superadmin 或 ENABLE_AUTH=false 的 AnonymousUser）

  _check_conversation_ownership(conversation, current_user)
    ├─ _is_superadmin(user) → 直接通过（可操作任何对话）
    ├─ conversation.user_id is None → 通过（legacy 数据，兼容）
    ├─ conversation.user_id == _get_user_id(user) → 通过
    └─ 否则 → raise HTTPException(403, "无权访问此对话")

  所有 CRUD 端点 → Depends(get_current_user)
  GET/PUT/DELETE + rename/move/group → _check_conversation_ownership()（superadmin 全通过）
  POST /{id}/messages（send_message）→ _check_conversation_write_permission()（superadmin 受限，见 4.14）
  GET /{id}/messages（get_messages）→ _check_conversation_ownership()
  POST /{id}/regenerate → _check_conversation_write_permission()
  POST /{id}/clear → _check_conversation_write_permission()

  GET /conversations/all-users-view（superadmin only）
    ├─ 注册在 GET /conversations/{id} 路由之前（防 FastAPI 路径冲突）
    └─ list_all_conversations_by_user(exclude_user_id=current_user.id)
       → [{"user_id":..., "display_name":..., "conversations":[...]}, ...]

ENABLE_AUTH=false 兼容：
  AnonymousUser.is_superadmin=True → _is_superadmin()=True → 任何端点直接通过
  _get_user_id(AnonymousUser)=None → list_conversations(user_id=None) → 无过滤（全部可见）

前端：
  Chat.tsx: authUser.is_superadmin → fetchAllUsersConversations() → otherUsersData
  ConversationSidebar: otherUsersData → "其他用户" Tab（只读，无新建/操作按钮，见 4.14）
```

### 4.14 侧边栏 Tab UI + 只读模式 + is_shared 群组框架（2026-03-25）

```
侧边栏双 Tab（ConversationSidebar.tsx）：
  activeTab: 'mine' | 'others'（useState 内部状态）
  Tab1 "我的对话"：新建对话/分组按钮 + 对话列表（原有功能）
  Tab2 "其他用户(N)"：Collapse 展开的其他用户对话（仅 otherUsersData.length > 0 时显示 Tab）
    N = otherUsersData 下所有对话总数（红色 Badge）
    defaultActiveKey 全部展开，无新建/重命名/删除按钮
  兼容：otherUsersData 为空（非 superadmin）时不渲染 Tabs，保留原有纯列表样式

只读模式链路（Chat.tsx + ChatInput.tsx）：
  isViewingOtherUserConv（useState）
    └─ handleSelectConversation() 检查 conv.id ∈ otherUsersData → setIsViewingOtherUserConv(true/false)

  handleSendMessage()：顶部 guard → if (isViewingOtherUserConv) return
  停止按钮：{sending && !isViewingOtherUserConv && (...)}（只读对话不显示）
  ChatInput：readOnly prop → 替换整个输入框为黄色警告横幅
    "👁 仅查看模式 — 当前对话属于其他用户"

后端写权限（conversations.py）：
  _check_conversation_write_permission(conversation, current_user)
    ├─ 非 superadmin → 调用 _check_conversation_ownership（原有逻辑不变）
    ├─ superadmin 且 user_id == own_id → 允许（自己的对话）
    ├─ superadmin 且 conversation.is_shared == True → 允许（群组对话，预留扩展点）
    └─ superadmin 且 user_id != own_id → raise 403（他人对话，只可查看）
  应用位置：send_message / regenerate_last_message / clear_conversation
    （get_messages / rename / move / group → 仍用 _check_conversation_ownership：superadmin 全通过）

is_shared 字段（群组聊天预留）：
  DB：conversations.is_shared BOOLEAN NOT NULL DEFAULT FALSE
  Model：is_shared = Column(Boolean, default=False)
  to_dict()：暴露 "is_shared": self.is_shared or False
  前端 Conversation interface：is_shared?: boolean
  用途：is_shared=True 时，superadmin 对该对话有写权限（群组聊天扩展点，当前版本保留占位）
  迁移：migrate_add_is_shared.py（ALTER TABLE ADD COLUMN，幂等）

Bug 修复（随本版本一起修复）：
  ConversationService.clear_messages() 此前缺失 → POST /{id}/clear 端点调用时 500
  修复：实现 clear_messages(conversation_id, keep_system=True)
        → 删除消息 + 更新 conversation.message_count
        → 无数据库迁移（纯代码层）
```

### 4.12 Skill 语义混合路由流程

```
用户消息
    │
    ▼ Phase 1（同步，<1ms）
关键词匹配（SkillMD.matches()）
    ├── 命中 → keyword_hits（score=1.0）
    │
    └── 未命中的候选 skill（剩余可触发列表）
           │
           ▼ Phase 2（异步）
    SkillRoutingCache.get(message)  ← ChromaDB 精确哈希匹配
           ├── 缓存命中 → 复用路由结果（跳过 LLM）
           │
           └── 缓存未命中
                  │
                  ▼
           SkillSemanticRouter.route()
             prompt: 消息 + 候选 skill（name: description | 触发词）
             response: JSON {"skill-name": score}（单次 LLM 调用，max_tokens=200）
                  │
                  ▼
           写入 SkillRoutingCache（TTL 24h，含 skill_set_version）
                  │
    ← 合并 Phase1 + Phase2，按 skill_semantic_threshold（默认 0.45）过滤
           │
    _build_from_matched_skills()
           │
    _MAX_INJECT_CHARS=16000 限制保护（超限 → 摘要模式）
           │
    build_skill_prompt_async() 返回 skill_injection 字符串
           │
    注入 AgenticLoop._build_system_prompt()（async）
           → 同时注入 context["username"] 为 "CURRENT_USER: {username}"
             供 skill-creator 等技能确定写入路径 (.claude/skills/user/{username}/)
```

**降级策略**：
- `SKILL_MATCH_MODE=keyword`：纯关键词，Phase 2 跳过
- `llm_adapter=None` 时：自动降级到纯关键词
- LLM 调用失败（异常/超时）：捕获异常，返回 `{}`，仅使用 Phase 1 结果
- ChromaDB 不可用：`get()` 返回 None，`put()` 静默忽略，不影响功能

`build_skill_prompt_async()` 完成后，结果写入 `SkillLoader._last_match_info`（`get_last_match_info()` 获取副本），供 `run_streaming()` 构造 `skill_matched` SSE 事件（见 4.15 节）。

---

### 4.15 技能路由可视化（2026-03-26）

每次对话推理启动时，`run_streaming()` 在第一个 `thinking` 事件之前 yield 一个 `skill_matched` 事件，携带本次技能路由的完整快照：

```
skill_matched 事件 data 结构：
{
  "mode": "hybrid",           # 匹配模式（keyword/hybrid/llm）
  "matched": [                # 触发注入的技能列表
    {
      "name": "clickhouse-analyst",
      "tier": "user",         # system/project/user
      "method": "keyword",    # keyword / semantic
      "hit_triggers": ["sg", "账单"],  # 命中的触发词（关键词模式）
      "score": 1.0            # 语义分（关键词命中固定 1.0）
    }
  ],
  "always_inject": ["_base-safety", "_base-tools"],  # 始终注入的技能
  "summary_mode": false,      # true 表示注入内容超 16000 字符降级摘要模式
  "total_chars": 4320,        # 本次注入字符总数
  "load_errors": []           # 加载失败的技能文件（如缺少 YAML frontmatter）
}
```

**链路**：

```
run_streaming() → _build_system_prompt()
                    └─ build_skill_prompt_async()
                         └─ SkillLoader._last_match_info 写入 _make_match_info()
                  ← get_last_match_info() 获取快照
                  → yield AgentEvent(type="skill_matched", data=match_info)
                  → [第一个 thinking 事件]
```

**前端处理**：

```
Chat.tsx:  chunk.type === 'skill_matched' → addThoughtEvent(messageId, chunk)
ThoughtProcess.tsx:  渲染 🧠 技能路由 折叠面板
  ├─ 模式标签（keyword/hybrid/llm）
  ├─ matched 技能列表（tier 徽标 + method + hit_triggers + score）
  ├─ always_inject 技能名列表
  ├─ 字符数（> 12000 时橙色警告；summary_mode=true 时红色警告）
  └─ load_errors 列表（有错误时显示红色告警）
```

**持久化**：`skill_matched` 事件与 `thinking/tool_call/tool_result` 一同收集进 `thinking_events`，写入 `Message.extra_metadata`，刷新后可回溯查看。

**权限**：`GET /api/v1/skills/load-errors` — 诊断接口，需 `settings:read`（analyst/admin/superadmin）。

---

### 4.16 文件写入 → 下载链接数据流（2026-03-26）

Agent 写文件后消息末尾自动展示可点击的下载卡片，流程覆盖 SSE 事件生成 → DB 持久化 → 前端渲染 → 安全下载。

```
AgenticLoop.run_streaming()
  │  ← 每轮迭代执行 call_tool("filesystem__write_file", ...)
  │  ← 成功后追加到 written_files: List[dict]
  │    { path, name, size, mime_type（_infer_mime_type 推断 25+ 扩展名）}
  │
  ├─ end_turn / near_limit 路径：written_files 非空时
  │   yield AgentEvent(type="files_written", data={"files": [...]})   ← 新 SSE 事件
  │
conversation_service.send_message_stream()
  ├─ 消费 files_written 事件 → files_written_info = event.data["files"]
  └─ 保存助手消息时注入：
       messages.extra_metadata["files_written"] = files_written_info  ← 复用 JSONB 列，无 DB 迁移

前端 Chat.tsx（SSE chunk 处理）
  ├─ chunk.type === 'files_written'
  │   → setMessageFilesWritten(currentAssistantId, files)
  │   → message.files_written = [...]
  └─ 历史消息加载：msg.extra_metadata?.files_written → 还原 message.files_written

ChatMessages.tsx（渲染层）
  └─ message.files_written?.length > 0
      → <FileDownloadCards files={...} />
         ├─ 文件图标（类型颜色区分）+ 文件名 + 大小
         └─ 「下载」按钮 → fileApi.downloadFile(path, name)
                            → axios GET /api/v1/files/download?path=...
                               (responseType: 'blob')
                            → URL.createObjectURL → <a download> 点击 → 浏览器保存对话框

GET /api/v1/files/download（backend/api/files.py）
  ├─ Depends(get_current_user)（仅认证，无 require_permission；viewer+ 均可访问自己文件）
  ├─ _resolve_download_path(path, username)：
  │    支持格式 1：customer_data/{username}/file.csv（含前缀）
  │    支持格式 2：{username}/file.csv（无前缀）
  │    安全检查：resolved_abs_path.relative_to(customer_data/{username}/) → 403/404/400
  └─ FileResponse(Content-Disposition: attachment; filename*=UTF-8''{url_encoded_name})
```

**月份子文件夹（可选）**：`FILE_OUTPUT_DATE_SUBFOLDER=true` 时，`_build_system_prompt()` 向 Agent 注入月份路径提示（如 `customer_data/{username}/2026-03/`），Agent 自动按月组织文件，便于批量清理历史数据。默认 `false`，Agent 自主决定路径。

---

### 4.17 Excel → ClickHouse 数据导入流程（2026-04-05）

```
前端 DataImport.tsx（3 步骤向导）
  Step 1: 选择 ClickHouse 连接 + 上传 Excel 文件
    GET /data-import/connections        → 可写连接列表（从 MCPServerManager 枚举 clickhouse-* 非 -ro）
    POST /data-import/upload (multipart) → upload_id + Sheet 预览信息
      │
      ├─ 前端 dataImportApi.uploadExcel(file) — axios timeout=600000ms（10分钟）
      └─ 后端 upload_excel()
           ├─ MIME/扩展名检查（.xlsx/.xls）
           ├─ 1MB 分块流式写盘（边写边计算大小，超 100MB 立即 413）
           │    customer_data/{username}/imports/{upload_id}.xlsx
           └─ run_in_executor(parse_excel_preview)  ← openpyxl 在线程池中运行（避免阻塞事件循环）
                ├─ read_only=True, data_only=True
                ├─ ws.max_row        ← O(1) 元数据，不遍历所有行
                └─ iter_rows() 仅取前 PREVIEW_ROWS=5 行后立即 break

  Step 2: 配置各 Sheet 映射
    GET /data-import/connections/{env}/databases  → 数据库列表
    GET /data-import/connections/{env}/databases/{db}/tables → 表列表
    用户为每个 Sheet 选择 database / table / has_header / enabled

  Step 3: 提交导入 + 进度监控
    POST /data-import/execute
      │  body: {upload_id, connection_env, batch_size, sheets:[...]}
      └─ execute_import()
           ├─ 定位 customer_data/{username}/imports/{upload_id}.xlsx
           ├─ 创建 ImportJob(status="pending") → db.commit()
           └─ asyncio.create_task(run_import_job(job_id, config))  ← 后台协程，立即返回 job_id

  轮询进度（前端定时 GET /data-import/jobs/{job_id}）
    ← ImportJob.to_dict(): status / done_sheets / done_batches / imported_rows / error_message

  取消（可选）
    POST /data-import/jobs/{job_id}/cancel → status="cancelling"
    run_import_job 协程下批次检测后干净退出 → status="cancelled"

  删除（终止态后）
    DELETE /data-import/jobs/{job_id} → 删除 DB 记录（不影响已导入数据）

run_import_job(job_id, config) 后台协程（data_import_service.py）：
  ├─ status="running"; started_at=utcnow()
  ├─ 快速估算总批次：load_workbook → ws.max_row（O(1)）→ total_batches
  ├─ for sheet in sheet_configs:
  │    ├─ _is_cancelling() → True → _mark_cancelled() → return（每 Sheet 开始前检查）
  │    ├─ openpyxl.load_workbook(read_only=True)
  │    ├─ iter_rows(values_only=True) → 跳过 has_header 首行
  │    └─ for row in rows:
  │         batch_rows.append(row)
  │         if len(batch_rows) >= batch_size:
  │           client.insert_tsv(database, table, batch_rows)  ← TabSeparated 格式（3-5x 性能提升）
  │           done_batches += 1
  │           每 10 批写一次 DB 进度（减少 PostgreSQL 往返）
  │           await asyncio.sleep(0)          ← 让出事件循环
  │           _is_cancelling() → True → return cancelled
  │           Abort on first failure → status="failed"
  │    尾部剩余行 → insert_tsv
  └─ status="completed"; os.unlink(file_path)  ← 清理临时文件
```

**状态机**：

```
pending ──(协程启动)──► running ──(完成)──► completed
                   │         └──(失败)──► failed
                   │
   ┌──(POST cancel)──┘
   ▼
cancelling ──(协程检测到)──► cancelled
```

**权限**：全部 9 个 `/data-import/*` 端点均通过 `Depends(require_permission("data", "import"))` 保护。仅 superadmin 拥有 `data:import` 权限。`ENABLE_AUTH=false` 时 AnonymousUser(is_superadmin=True) 自动通过。

---

## 5. SSE 事件类型参考

前端通过 `text/event-stream` 接收以下事件（均为 JSON）：

| event.type | 触发场景 | 前端处理 |
|-----------|---------|---------|
| `user_message` | 用户消息已保存（role='user'）或续接提示已保存（role='continuation'）| role=user→消息气泡；role=continuation→ContinuationCard 横幅 |
| `skill_matched` | 技能路由完成，第一个 `thinking` 之前 | ThoughtProcess 面板 🧠 技能路由 折叠区 |
| `thinking` | LLM 正在推理 | ThoughtProcess 面板 |
| `tool_call` | 调用 MCP 工具 | 工具调用展示 |
| `tool_result` | 工具返回结果 | 工具结果展示 |
| `content` | 最终回答文本 | 主消息区域 |
| `continuation` | max_tokens 自动续写 | 静默/进度提示 |
| `near_limit` | 接近推理轮次上限，携带 pending_tasks | 触发自动续接 |
| `auto_continuing` | 开始自动续接（第 N/3 次）| message.info toast |
| `continuation_approval_required` | 3 次自动续接耗尽 | 确认 Modal |
| `files_written` | Agent 本轮写入了文件（`end_turn`/`near_limit` 路径，`written_files` 非空时） | `setMessageFilesWritten(messageId, files)` → 消息底部渲染文件下载卡片 |
| `assistant_message` | 助手消息已保存 | 消息持久化确认 |
| `approval_required` | ETL 高危 SQL 等待审批 | ApprovalModal（60s） |
| `cancelled` | 用户点击「停止生成」，推理被打断 | message.info("已停止生成")，助手气泡显示已生成部分 + 中断标记 |
| `context_compressed` | 对话历史已 LLM 压缩 | message.info toast |
| `error` | 任意异常 | 错误提示 |
| `done` | 流结束 | 关闭 SSE 连接 |

---

## 6. REST API 端点一览

基础路径：`/api/v1`，健康检查：`GET /health`

### 对话管理 `/conversations`

> 所有端点（除 `/cancel`）均需有效 JWT（`Depends(get_current_user)`）。`ENABLE_AUTH=false` 时 AnonymousUser 自动通过。非 superadmin 用户只能操作自己的对话（`_check_conversation_ownership()`），superadmin 可访问全部对话。

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/conversations` | 新建对话（`user_id` 自动绑定当前用户） |
| GET | `/conversations` | 对话列表（仅返回当前用户的对话；ENABLE_AUTH=false 时返回全部） |
| GET | `/conversations/all-users-view` | **superadmin 专用**：按用户分组返回所有其他用户的对话（须在 `/{id}` 路由之前注册） |
| GET | `/conversations/{id}` | 对话详情 |
| PUT | `/conversations/{id}` | 更新对话标题/模型 |
| DELETE | `/conversations/{id}` | 删除对话 |
| POST | `/conversations/{id}/messages` | **发送消息（SSE 流式）**，body: `{content, stream, attachments?: [{name,mime_type,size,data}]}`；需 write_permission 校验（superadmin 对他人对话返回 403，除非 is_shared=true） |
| POST | `/conversations/{id}/cancel` | **停止正在进行的生成**（设置取消信号，幂等；无鉴权要求） |
| GET | `/conversations/{id}/messages` | 获取消息列表；需 ownership 校验 |
| POST | `/conversations/{id}/regenerate` | 重新生成最后一条；需 ownership 校验 |
| POST | `/conversations/{id}/clear` | 清空消息；需 ownership 校验 |
| PUT | `/conversations/{id}/group` | 移入分组 |
| PUT | `/conversations/{id}/title` | 重命名 |

### 审批 `/approvals`
| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/approvals/` | 待审批列表 |
| GET | `/approvals/{id}` | 单条审批详情 |
| POST | `/approvals/{id}/approve` | 批准（恢复 ETL 执行） |
| POST | `/approvals/{id}/reject` | 拒绝（跳过危险操作） |

### 技能 `/skills`
| 方法 | 路径 | 权限 | 说明 |
|------|------|------|------|
| GET | `/skills/md-skills` | 所有用户 | 三层 SKILL.md 技能列表（含 tier/always_inject/is_readonly） |
| GET | `/skills/preview?message=xxx[&mode=keyword\|hybrid\|llm]` | 所有用户 | 测试消息触发哪些技能 + 预计注入字符数 + match_details |
| POST | `/skills/user-defined` | 用户 | 新建用户技能 |
| GET | `/skills/user-defined` | 用户 | 用户技能列表 |
| PUT | `/skills/user-defined/{name}` | 用户 | 更新用户技能（版本自动递增） |
| DELETE | `/skills/user-defined/{name}` | 用户 | 删除用户技能 |
| GET | `/skills/project-skills` | 所有用户 | 项目技能列表 |
| POST | `/skills/project-skills` | 管理员 | 新建项目技能 |
| PUT | `/skills/project-skills/{name}` | 管理员 | 更新项目技能（版本自动递增） |
| DELETE | `/skills/project-skills/{name}` | 管理员 | 删除项目技能 |
| GET | `/skills/load-errors` | `settings:read`（analyst+） | 返回加载失败的技能文件列表（YAML 格式错误、缺少 frontmatter 等） |

### MCP 管理 `/mcp`

> `ENABLE_AUTH=false` 时：AnonymousUser `is_superadmin=True`，所有端点直接通过（向后兼容）。

| 方法 | 路径 | 权限要求 | 说明 |
|------|------|---------|------|
| GET | `/mcp/servers` | `settings:read`（admin+） | 已注册 MCP 服务器列表 |
| GET | `/mcp/servers/{name}` | `settings:read` | 服务器详情 + 工具列表 |
| GET | `/mcp/servers/{name}/tools` | `settings:read` | 服务器工具列表 |
| GET | `/mcp/servers/{name}/resources` | `settings:read` | 服务器资源列表 |
| POST | `/mcp/servers/{name}/tools/{tool}` | `settings:write`（admin+） | 直接调用工具（调试用） |
| POST | `/mcp/test-connection` | `settings:write` | 连接测试 |
| GET | `/mcp/stats` | `settings:read` | 汇总统计（服务器数、工具数） |

### 认证 `/auth`（`ENABLE_AUTH=true` 时生效）
| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/auth/login` | 登录，返回 access_token + httpOnly refresh_token Cookie |
| POST | `/auth/refresh` | 用 refresh_token Cookie 换取新 access_token（轮换，旧 token 立即失效）|
| POST | `/auth/logout` | 登出，revoke refresh_token，清除 Cookie |
| GET | `/auth/me` | 当前用户信息（id / username / roles / permissions）|

### 用户管理 `/users` 与 `/roles`
| 方法 | 路径 | 权限要求 | 说明 |
|------|------|---------|------|
| POST | `/users` | `users:write`（superadmin）| 创建本地账号（201 + UserOut）|
| GET | `/users` | `users:read`（superadmin）| 用户列表（分页）|
| GET | `/users/{id}` | 本人 或 `users:read` | 用户详情 |
| PUT | `/users/{id}` | 本人 或 `users:write` | 修改 display_name / is_active |
| PUT | `/users/{id}/password` | 仅本人 | 修改密码（需旧密码）|
| POST | `/users/{id}/roles` | `users:assign_role`（superadmin）| 分配角色 |
| DELETE | `/users/{id}/roles/{role_id}` | `users:assign_role`（superadmin）| 撤销角色 |
| GET | `/roles` | `users:read` | 角色列表（含权限详情）|
| POST | `/roles` | `users:write` | 新建自定义角色 |
| DELETE | `/roles/{id}` | `users:write` | 删除自定义角色（`is_system=True` 时返回 403）|
| POST | `/roles/{id}/permissions` | `users:assign_role` | 为角色分配权限（`permission_id` 关联）|
| DELETE | `/roles/{id}/permissions/{perm_id}` | `users:assign_role` | 从角色移除权限 |

### 权限 `/permissions`
| 方法 | 路径 | 权限要求 | 说明 |
|------|------|---------|------|
| GET | `/permissions` | `users:read` | 全量权限定义列表（共 13 条，含 resource/action/description）|

### 文件下载 `/files`

> 仅需有效 JWT（`Depends(get_current_user)`），无独立 `require_permission`；任何已认证用户（viewer+）均可下载**自己目录**下的文件。路径安全由 `_resolve_download_path()` 强制校验，跨用户访问返回 403。

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/files/download?path=...` | 下载 `customer_data/{username}/` 下的文件。`path` 支持带/不带 `customer_data/` 前缀两种格式。响应头 `Content-Disposition: attachment; filename*=UTF-8''...`（支持中文文件名）。文件不存在返回 404，跨用户返回 403，路径穿越返回 403，目录路径返回 400。 |

### 数据导入 `/data-import`

> 全部端点需 `data:import` 权限（**仅 superadmin**）。`ENABLE_AUTH=false` 时 AnonymousUser(is_superadmin=True) 自动通过。

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/data-import/connections` | 所有可写 ClickHouse 连接（从 MCPServerManager 枚举，排除 -ro 只读服务器） |
| GET | `/data-import/connections/{env}/databases` | 查询指定环境的数据库列表（排除 system/information_schema） |
| GET | `/data-import/connections/{env}/databases/{db}/tables` | 查询指定数据库的表列表 |
| POST | `/data-import/upload` | 上传 Excel（multipart）。1MB 分块流式写盘；上限 100MB；线程池解析预览。返回 `upload_id` + Sheet 预览信息 |
| POST | `/data-import/execute` | 提交导入任务。创建 `ImportJob` 记录（status=pending）并后台启动 `run_import_job` 协程。立即返回 `job_id` |
| GET | `/data-import/jobs/{job_id}` | 查询任务状态与进度（用于前端轮询） |
| GET | `/data-import/jobs` | 历史任务列表（分页，按 created_at 倒序），参数：`page`/`page_size` |
| POST | `/data-import/jobs/{job_id}/cancel` | 请求取消 pending/running 任务（将状态置为 cancelling；协作式，非强制终止） |
| DELETE | `/data-import/jobs/{job_id}` | 删除任务记录（不影响已导入数据） |

### 分组管理 `/groups`

> 所有端点均需有效 JWT（`Depends(get_current_user)`）。分组按 `user_id` 隔离，同名分组不同用户可并存；superadmin 可访问全部分组。

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/groups` | 新建分组（`user_id` 绑定当前用户；同用户内名称唯一） |
| GET | `/groups` | 分组列表（仅当前用户；`sort_order` 按用户独立维护） |
| PUT | `/groups/{id}` | 更新分组（ownership 校验） |
| DELETE | `/groups/{id}` | 删除分组（ownership 校验） |

---

## 7. 关键配置文件

### `.env`（环境变量）
```ini
# LLM
ANTHROPIC_API_KEY=...
ANTHROPIC_DEFAULT_MODEL=claude-sonnet-4-6

# RBAC 认证（多用户模式）
ENABLE_AUTH=false                         # true 启用 JWT 认证，false（默认）单用户兼容模式
JWT_SECRET=your-strong-secret-key-here    # ≥32 位随机字符串，建议 openssl rand -hex 32 生成
JWT_ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=120           # access_token 有效期（分钟），须 ≤ SESSION_IDLE_TIMEOUT_MINUTES
REFRESH_TOKEN_EXPIRE_DAYS=14              # refresh_token DB 记录保留时长（天）
SESSION_IDLE_TIMEOUT_MINUTES=120          # Session 空闲超时（分钟），超时后 /auth/refresh 返回 401

# ClickHouse（动态多区域，格式：CLICKHOUSE_{ENV}_{FIELD}）
# 已知环境：idn/sg/mx；可任意新增：thai/br/sg_azure/my 等
# 新增区域只需追加以下 5 行，重启后自动注册 clickhouse-{env} 服务器
CLICKHOUSE_IDN_HOST=...
CLICKHOUSE_IDN_PORT=9000
CLICKHOUSE_IDN_HTTP_PORT=8123
CLICKHOUSE_IDN_USER=...
CLICKHOUSE_IDN_PASSWORD=...
CLICKHOUSE_IDN_DATABASE=...

# 只读凭据（可选，填写后自动注册 clickhouse-{env}-ro 只读服务器）
# HOST/PORT/DATABASE 留空时继承对应 admin 值
CLICKHOUSE_IDN_READONLY_USER=...
CLICKHOUSE_IDN_READONLY_PASSWORD=...

# Filesystem 权限配置
# FilesystemMCPServer 可访问的目录列表（读+写的范围上限）
# 支持相对路径（相对于项目根目录，部署可移植）和绝对路径（兼容）
ALLOWED_DIRECTORIES=["customer_data",".claude/skills"]

# 文件写入下载配置
# false（默认）：Agent 自主决定文件路径；true：向 Agent 注入月份子目录提示（如 YYYY-MM/），便于按月清理历史数据
FILE_OUTPUT_DATE_SUBFOLDER=false

# FilesystemPermissionProxy 允许写入的目录列表（在 ALLOWED_DIRECTORIES 基础上收窄写权限）
FILESYSTEM_WRITE_ALLOWED_DIRS=["customer_data",".claude/skills/user"]

# Skill 语义路由配置
# 匹配模式：hybrid（关键词+语义，默认）/ keyword（纯关键词）/ llm（纯语义）
SKILL_MATCH_MODE=hybrid
# LLM 路由置信度最低阈值，低于此值不注入（默认 0.45）
SKILL_SEMANTIC_THRESHOLD=0.45
# 路由缓存 TTL（秒），默认 24 小时
SKILL_SEMANTIC_CACHE_TTL=86400
# ChromaDB 路由缓存存储路径
SKILL_ROUTING_CACHE_PATH=./data/skill_routing_cache

# PostgreSQL（对话存储）
DATABASE_URL=postgresql://...
```

> **路径配置说明**：
> - **相对路径**（推荐）：如 `customer_data`、`.claude/skills`，由 `settings.py` 自动解析为绝对路径（基于 `Path(__file__).parent.parent.parent.resolve()`，即项目根目录），部署到任意服务器无需修改。
> - **绝对路径**（兼容）：如 `/data/customer_data`，适用于数据目录与代码目录分离的场景（如 Docker 挂载卷）。
> - **默认值**：若不设置这两个变量，`settings.py` 的 `default_factory` 自动使用相对于代码位置的路径，行为与相对路径方式一致。

### `.claude/agent_config.yaml`
控制每个 Agent 类型的最大迭代次数和 ClickHouse 连接权限，详见文件内注释。
`filesystem` 服务器不再通过 `excluded_non_ch_servers` 排除，而是由 `FilesystemPermissionProxy` 实施目录级写权限控制。

### `backend/config/settings.py`
Pydantic Settings 类，所有环境变量通过此文件统一访问。关键机制：
- **`load_dotenv(override=False)` 前置加载**：模块导入时按 `cwd → backend/ → 项目根` 顺序读取所有 `.env` 键（含未声明字段）进 `os.environ`，`override=False` 确保测试预设值不被覆盖
- **`Config.extra = "ignore"`**：未在 model 中声明的 `CLICKHOUSE_THAI_*` 等字段不触发 `ValidationError`
- **`get_all_clickhouse_envs()`**：双源发现（model_fields 扫描 + `os.environ` 正则扫描），自动感知任意新增环境
- **`get_clickhouse_config(env, level)`**：`getattr → AttributeError → os.environ 大小写不敏感回退`
- **`_PROJECT_ROOT` 常量**（2026-03-23）：模块顶部 `Path(__file__).parent.parent.parent.resolve()`，是路径解析的单一事实来源，不依赖运行时 `cwd`
- **`_resolve_fs_paths` validator**（2026-03-23）：`@field_validator("allowed_directories", "filesystem_write_allowed_dirs", mode="after")`，将 `.env` 中的相对路径（如 `customer_data`）解析为以 `_PROJECT_ROOT` 为基准的绝对路径；绝对路径直接通过，向后兼容已有生产配置

### `backend/core/filesystem_permission_proxy.py` — Fix-1~4 安全增强（2026-03-23）

| 修复 | 方法 | 行为 |
|------|------|------|
| Fix-1 | `agentic_loop._build_system_prompt()` | 识别 `skills_root`/`data_root`，注入精确路径模板到 LLM 系统提示，消除路径歧义 |
| Fix-2 | `_is_write_allowed()` | 拦截 `customer_data/.claude/...` 误路由（跨根反向写入），独立于白名单检查 |
| Fix-3 | `call_tool()` 拒绝消息 | 包含 `{skills_root}/user/{用户名}/skill-name.md` 模板，引导 LLM 自纠 |
| Fix-4 | `_check_skills_user_subdir(tool_name)` | 工具名感知深度校验：`create_directory` 需深度 ≥ 1，`write_file`/`delete` 需深度 ≥ 2 |

---

## 8. MCP 工具命名约定

工具名格式：`{server_name_underscored}__{tool_name}`

| 示例工具名 | 服务器 | 工具 |
|-----------|--------|------|
| `clickhouse_idn__query` | clickhouse-idn | query |
| `clickhouse_idn__batch_describe_tables` | clickhouse-idn | batch_describe_tables |
| `filesystem__write_file` | filesystem | write_file |
| `lark__get_document` | lark | get_document |

ClickHouse 工具列表（`server.py`）：
`query` / `list_databases` / `list_tables` / `describe_table` /
`batch_describe_tables` / `get_table_overview` / `sample_table_data` /
`test_connection` / `get_server_info`

---

## 9. SKILL.md 格式规范与注入机制

`.claude/skills/{system,project,user}/` 目录下的 `.md` 文件，YAML front matter 定义触发规则：

```yaml
---
name: skill-name          # 唯一标识（kebab-case，与文件名一致）
version: "1.0"            # API 更新时自动递增（1.0 → 1.1）
description: 技能简介     # ≤120 字符
triggers:                 # 关键词列表（含于用户消息时激活）
  - keyword1
  - 关键词2
category: engineering     # engineering | analytics | general | system
priority: high            # high | medium | low（多技能同时激活时排序用）
always_inject: false      # true = 每次对话始终注入，不依赖触发词
---

# 技能内容（注入到 system prompt）
```

**特殊规则**：
- 文件名以 `_base-` 开头 → `always_inject` 自动设为 `true`
- 总注入字符超过 `_MAX_INJECT_CHARS=16000` 时，降级为摘要模式（只注入 name + description + triggers 摘要）

**注入顺序**（`SkillLoader.build_skill_prompt_async(message, llm_adapter)`）：

```
用户消息
  │ Phase 1：关键词匹配（同步）
  → keyword_hits（Tier 1/2/3 各层分别匹配触发词）
  │
  │ Phase 2：语义补充（异步，hybrid 模式）
  → ChromaDB 缓存 / LLM 批量打分（未关键词命中的候选 skill）
  │
  → always_inject skill（始终包含）
  → 合并去重 + 阈值过滤（score >= SKILL_SEMANTIC_THRESHOLD）
  → _build_from_matched_skills() 按 tier 顺序排列：
      Tier 3 用户技能（触发匹配，最多 3 条）
      Tier 2 项目技能（触发匹配，最多 3 条）
      Tier 1 系统 base 技能（always_inject=true，始终包含）
      Tier 1 系统触发技能（触发匹配，最多 3 条）
  → 拼接为 skill_prompt，追加到 system prompt
```

**路由模式配置**（`SKILL_MATCH_MODE` 环境变量）：

| 模式 | 行为 |
|------|------|
| `hybrid`（默认） | 关键词优先 + LLM 语义补充 |
| `keyword` | 纯关键词匹配（<1ms，无 LLM 调用） |
| `llm` | 全量 LLM 路由（忽略关键词） |

---

## 10. RBAC 权限模型

### 数据模型（PostgreSQL）

```
users                    roles                    permissions
─────────────────        ─────────────────        ─────────────────
id          UUID PK      id          UUID PK      id          UUID PK
username    UNIQUE        name        UNIQUE        resource    VARCHAR  # e.g. "users"
hashed_pwd  VARCHAR       description VARCHAR       action      VARCHAR  # e.g. "write"
is_active   BOOL          is_system   BOOL          key         VARCHAR  # "users:write"
is_superadmin BOOL        created_at  TIMESTAMP     description VARCHAR
auth_source VARCHAR
last_login_at TIMESTAMP                  user_roles                role_permissions
created_at  TIMESTAMP                   ─────────────────          ─────────────────
                                        user_id     UUID FK→users  role_id  UUID FK→roles
refresh_tokens                          role_id     UUID FK→roles  perm_id  UUID FK→perms
─────────────────                       assigned_at TIMESTAMP
id          UUID PK                     assigned_by UUID（操作人）
jti         UUID UNIQUE
user_id     UUID FK→users
revoked     BOOL
created_at  TIMESTAMP
expires_at  TIMESTAMP

conversations                    conversation_groups
─────────────────                ─────────────────
id          UUID PK              id      UUID PK
title       VARCHAR              name    VARCHAR
user_id     UUID FK→users NULL   user_id UUID FK→users NULL
group_id    UUID FK→groups NULL  ...
...
（user_id=NULL：ENABLE_AUTH=false 匿名模式，list 无过滤；
  user_id 有值：用户隔离，仅本人可见/操作，superadmin 可见全部）
```

### 权限检查层级

```
ENABLE_AUTH=false                       ENABLE_AUTH=true
─────────────────                       ─────────────────
所有请求 → AnonymousUser               Bearer JWT → decode_token()
(is_superadmin=True)                        → User 查询（is_active=True）
所有 require_permission 直接通过            → require_permission
                                               ├─ is_superadmin=True → 通过
                                               └─ get_user_permissions(user, db)
                                                    → UserRole → Role → RolePermission → Permission
                                                    → perm_key in user_perms ? 通过 : 403
```

### 预置角色权限矩阵

| 权限键 | viewer | analyst | admin | superadmin |
|--------|--------|---------|-------|------------|
| `chat:use` | ✓ | ✓ | ✓ | ✓ |
| `skills.user:read` | — | ✓ | ✓ | ✓ |
| `skills.user:write` | — | ✓ | ✓ | ✓ |
| `skills.project:read` | — | ✓ | ✓ | ✓ |
| `skills.project:write` | — | — | ✓ | ✓ |
| `skills.system:read` | — | ✓ | ✓ | ✓ |
| `models:read` | — | — | ✓ | ✓ |
| `settings:read` | — | — | ✓ | ✓ |
| `settings:write` | — | — | ✓ | ✓ |
| `users:read` | — | — | — | ✓ |
| `users:write` | — | — | — | ✓ |
| `users:assign_role` | — | — | — | ✓ |
| ... | — | — | — | ✓ |

> `is_superadmin=True` 的用户跳过角色表，直接被授予系统中所有权限（`get_user_permissions` 返回全量权限列表）。

---

## 11. 启动方式

```bash
# 后端（在项目根目录）
python run.py
# 等价于：cd backend && uvicorn main:app --port 8000

# 首次启动多用户模式前，初始化 RBAC 角色和权限数据
python backend/scripts/init_rbac.py

# 前端
cd frontend && npm run dev

# 健康检查
GET http://localhost:8000/health → {"status": "healthy"}
```
