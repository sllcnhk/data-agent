# data-agent 功能注册表

> **适用对象**：LLM 模型、产品规划、开发者
> **目的**：完整记录已实现功能、已知缺陷修复、以及待实现功能及优先级
> **最后更新**：2026-03-26（**文件写入下载**：files_written SSE 事件 + GET /api/v1/files/download 安全端点 + FileDownloadCards UI + FILE_OUTPUT_DATE_SUBFOLDER 配置；技能路由可视化：skill_matched SSE 事件 + SkillLoader._last_match_info + ThoughtProcess 🧠 面板 + GET /skills/load-errors；侧边栏 Tab UI + 只读模式 + is_shared；对话用户隔离；附件上传；ClickHouse TCP→HTTP 自动回退）

---

## 一、已实现功能

### P0 — Agentic Loop 核心（已完成）

| 功能 | 文件 | 说明 |
|------|------|------|
| AgenticLoop 推理循环 | `backend/agents/agentic_loop.py` | 5阶段认知循环：Perceive/Retrieve/Plan/Act/Observe |
| 流式事件系统 | `agentic_loop.py` | AgentEvent(thinking/tool_call/tool_result/content/error/done) |
| max_tokens 自动续写 | `agentic_loop.py` | 检测截断 → 追加 "请继续" → 循环，上限 MAX_CONTINUATION=10 |
| 停滞检测 | `agentic_loop.py` | 连续相同工具调用 >= MAX_STAGNANT(2) 次时提前终止 |
| 上下文压缩 | `agentic_loop.py:_compress_loop_messages()` | 超过 60000 字符时压缩旧 tool_result，保留最近 5 对 |
| MCP 工具格式化 | `backend/mcp/tool_formatter.py` | Claude tool_use 协议：命名空间化工具名 |
| MasterAgent 路由 | `backend/agents/orchestrator.py` | 关键词打分：ETL/分析/通用 三路分发 |
| SSE 流式端点 | `backend/api/conversations.py` | POST /{id}/messages → text/event-stream |

### P1 — SKILL.md 系统 + 专用 Agent（已完成）

| 功能 | 文件 | 说明 |
|------|------|------|
| SkillLoader 单例 | `backend/skills/skill_loader.py` | 三层加载（system/project/user）；触发词匹配；always_inject 支持 |
| SkillWatcher 热重载 | `backend/skills/skill_watcher.py` | watchdog + Debouncer，递归监听三层子目录，文件变更 0.8s 自动重载 |
| ETL 工程师 Agent | `backend/agents/etl_agent.py` | ETLEngineerAgent + ETLAgenticLoop，admin 权限，SQL 安全检测 |
| 数据分析师 Agent | `backend/agents/analyst_agent.py` | DataAnalystAgent + ReadOnlyMCPProxy，readonly 权限 |
| 系统技能文件 | `.claude/skills/system/` | etl-engineer / schema-explorer / clickhouse-analyst / project-guide / skill-creator |
| 用户自定义技能 CRUD | `backend/api/skills.py` | POST/GET/PUT/DELETE /skills/user-defined（PUT 新增，版本自动递增） |
| skill-creator 技能 | `.claude/skills/system/skill-creator.md` | 引导用户通过对话创建自定义技能 |

### 三层技能体系（已完成，2026-03-13）

| 功能 | 文件 | 说明 |
|------|------|------|
| 三层目录结构 | `.claude/skills/{system,project,user}/` | Tier1 系统（只读）/ Tier2 项目（管理员）/ Tier3 用户（普通用户）|
| _base-safety.md | `.claude/skills/system/_base-safety.md` | 始终注入：数据安全约束（PII、DB 操作、写入目录限制） |
| _base-tools.md | `.claude/skills/system/_base-tools.md` | 始终注入：MCP 工具使用规范（读后写、串行高危操作、结果验证） |
| always_inject 机制 | `skill_loader.py` | `_base-*.md` 文件名前缀自动推断 always_inject=true；frontmatter 也支持显式声明 |
| context 长度保护 | `skill_loader.py:_MAX_INJECT_CHARS=16000` | 总注入超限时降级为摘要模式（name+description+triggers 列表） |
| 项目技能 REST API | `backend/api/skills.py` | POST/GET/PUT/DELETE /skills/project-skills（需 X-Admin-Token） |
| 用户技能更新 API | `backend/api/skills.py:PUT /user-defined/{name}` | 部分字段更新，版本自动递增（1.0→1.1），path-boundary 防止目录穿越 |
| 触发测试 API | `backend/api/skills.py:GET /preview` | 测试任意消息触发哪些技能；返回 triggered/always_inject/total_chars/preview_prompt/match_details；支持 mode= 参数 |
| 前端三标签页 | `frontend/src/pages/Skills.tsx` | 系统（只读+锁图标）/ 项目（管理员 CRUD）/ 我的（用户 CRUD）三标签 |
| 前端触发测试面板 | `Skills.tsx` | Collapse 展开面板；输入消息 → 显示触发结果 + 总字符数（>6000 橙色警告） |
| 前端用户技能编辑 | `Skills.tsx` | 铅笔图标 → EditModal → PUT API → 版本递增后刷新列表 |
| 前端技能提升 | `Skills.tsx` | RiseOutlined 图标 → 提升为项目技能（调用 createProjectSkill，加 -promoted 后缀，需管理员 Token） |
| sessionStorage Token 缓存 | `Skills.tsx` | 管理员 Token 输入后写入 sessionStorage，当次会话免重复输入 |
| README 更新 | `.claude/skills/README.md` | 完整的三层架构说明、字段表、注入顺序、权限表、REST API 速查 |

### RBAC 用户认证系统（已完成，2026-03-17）

| 功能 | 文件 | 说明 |
|------|------|------|
| User / Role / Permission ORM 模型 | `backend/models/{user,role,permission,user_role,role_permission,refresh_token}.py` | PostgreSQL UUID PK，SQLAlchemy ORM，mapper 初始化顺序通过 `__init__.py` 保证 |
| bcrypt 密码哈希 | `backend/core/auth/password.py` | `hash_password()` / `verify_password()`，直接调用 bcrypt 库（避免 passlib 版本兼容问题）|
| JWT Token 工具 | `backend/core/auth/jwt.py` | `create_access_token()` / `decode_token()` / `create_refresh_token_jti()`；claims: sub/username/roles/exp/iat/jti |
| LocalAuthProvider | `backend/core/auth/providers/local.py` | `authenticate()` 验证用户名+密码，inactive 用户返回 None |
| RBAC 权限查询 | `backend/core/rbac.py` | `get_user_permissions()` / `get_user_roles()`；superadmin 返回全量权限；通过 UserRole→Role→RolePermission→Permission 链路查询 |
| 认证依赖项 | `backend/api/deps.py` | `get_current_user()`（Bearer JWT 解析）；`require_permission(resource, action)` 工厂函数（返回 `_check` coroutine，非 `Depends(_check)`）；`AnonymousUser`（ENABLE_AUTH=false 时使用）|
| 认证 REST 端点 | `backend/api/auth.py` | POST /login（颁发 token + httpOnly Cookie）；POST /refresh（轮换 refresh_token）；POST /logout（revoke）；GET /me（含 roles/permissions）|
| 用户管理 REST 端点 | `backend/api/users.py` | 完整用户 CRUD；分配/撤销角色；`_safe_assigned_by()` 处理 AnonymousUser.id 非 UUID 情况 |
| 角色列表端点 | `backend/api/users.py` | GET /roles：返回 4 个预置角色及其权限详情 |
| RBAC 初始化脚本 | `backend/scripts/init_rbac.py` | 幂等脚本，写入 4 角色（viewer/analyst/admin/superadmin）+ 13 权限；admin 无 users:* 权限 |
| 兼容模式 | `backend/api/deps.py` | `ENABLE_AUTH=false`（默认）→ AnonymousUser（is_superadmin=True），所有权限检查直接通过，旧行为完全不变 |
| 多用户技能隔离 | `backend/api/skills.py` + `backend/skills/skill_loader.py` | `ENABLE_AUTH=true` 时用户技能写入 `user/{username}/`（含 "default" 用户，已修复旧有 `username!="default"` 守卫漏洞）；SkillLoader 启用 scan_subdirs=True 扫描子目录 |
| username 全链路注入 | `backend/api/conversations.py` + `backend/services/conversation_service.py` + `backend/agents/agentic_loop.py` | `send_message` 端点提取 JWT username → `send_message_stream(username=)` → `_build_context(username=)` → `context["username"]` → `_build_system_prompt()` 注入 `CURRENT_USER: {username}` 到 filesystem 工具提示，供 skill-creator 等写技能时确定正确路径 |
| POST /skills/user-defined 返回 201 | `backend/api/skills.py` | `@router.post("/user-defined", status_code=201)` 和 `@router.post("/project-skills", status_code=201)`；符合 REST 创建资源语义（HTTP 201 Created） |
| 角色管理 REST 端点（CRUD + 权限分配）| `backend/api/users.py` | POST/DELETE `/roles`（自定义角色增删，系统角色 `is_system=True` 删除返回 403）；POST/DELETE `/roles/{id}/permissions`（权限分配/撤销，需 `users:assign_role`）；`_role_out_dict()` 序列化含权限详情的角色对象 |
| 权限列表端点 | `backend/api/users.py:permissions_router` | GET `/permissions`：返回全量 13 条权限定义（需 `users:read`）；已在 `main.py` 注册为独立 Router |
| Roles.tsx 角色管理页面 | `frontend/src/pages/Roles.tsx` | 卡片式角色列表；系统角色锁定（`LockOutlined`，仅展示）；自定义角色 CRUD（带 Popconfirm 确认）；权限分配弹窗（按 resource 分组 Checkbox，勾选即授权/取消即撤权）；权限颜色编码（`RESOURCE_COLOR` / `ROLE_TAG_COLOR`）|
| 前端认证修复：isAnonymousUser | `frontend/src/store/useAuthStore.ts` | 提取 `isAnonymousUser()` 辅助函数：`user.id==='default' && user.username==='default'`；修复旧代码误用 `username!=='anonymous'`（匿名用户 username 实为 `'default'`），导致 `ENABLE_AUTH=false` 时 `auth_enabled` 被错误设为 `'true'`，重定向至登录页死循环 |
| 前端认证修复：fail-safe initAuth | `frontend/src/store/useAuthStore.ts` | 提取 `markAuthRequired()` 为独立函数；在 initAuth 路径 3 的 catch 块中对**任何错误**（含网络错误/ECONNREFUSED，不仅限于 401）均触发登录重定向；原代码仅判断 `err?.response?.status===401`，Vite 代理连接失败时 `response` 为 `undefined` 导致认证检查静默跳过 |
| 前端认证修复：authChecked | `frontend/src/store/useAuthStore.ts` + `frontend/src/App.tsx` | 新增 `authChecked: boolean` Zustand 状态；`RequireAuth` 在 `authChecked=false` 时渲染 `<Spin>` 等待界面，防止 `initAuth()` 异步检查完成前瞬间跳转到 `/login` 造成闪烁 |
| Vite 代理修复 | `frontend/vite.config.ts` | `proxy.target` 从 `http://localhost:8000` 改为 `http://127.0.0.1:8000`；修复 Windows 11 下 `localhost` DNS 解析为 `::1`（IPv6），而后端 uvicorn 默认仅监听 IPv4 (`0.0.0.0`)，导致代理 ECONNREFUSED；需重启前端 dev server 生效 |
| 菜单权限范围：角色权限 | `frontend/src/components/AppLayout.tsx` | 导航菜单新增 `/roles`（角色权限，图标 `SafetyOutlined`）；`perm: 'users:read'`，与 `/users`（用户管理）同级，仅 superadmin 可见 |
| RBAC 测试套件 | `test_rbac.py` | 85 个测试（A-I 层）：密码/JWT/LocalAuth/RBAC helpers/FastAPI deps/认证端点/用户端点/技能隔离/兼容模式/安全边界 |

### Session 过期管理（已完成，2026-03-19）

| 功能 | 文件 | 说明 |
|------|------|------|
| Session Cookie（浏览器关闭登出）| `backend/api/auth.py:_issue_tokens()` | `set_cookie()` 移除 `max_age`/`expires`，Cookie 变为 Session Cookie，浏览器关闭时自动清除，重开后需重新登录 |
| 空闲超时配置字段 | `backend/config/settings.py` | 新增 `session_idle_timeout_minutes: int`（默认 120，`env="SESSION_IDLE_TIMEOUT_MINUTES"`）；注释明确 `access_token_expire_minutes` 须 ≤ 此值 |
| users.last_active_at 字段 | `backend/models/user.py` | 新增 `last_active_at = Column(DateTime, nullable=True)`，记录用户最近 API 活动时间 |
| DB 迁移脚本 | `backend/migrations/add_user_last_active_at.py` | 支持 `up`/`down`；up: `ALTER TABLE users ADD COLUMN IF NOT EXISTS last_active_at` + 索引；down: 删除索引和列 |
| 活跃状态节流更新 | `backend/api/deps.py:_update_last_active()` | 每次 `get_current_user()` 节流判断（`_ACTIVITY_THROTTLE_SEC=300s`），满足条件时 `BackgroundTasks.add_task` 以独立 Session 写 DB，不阻塞响应 |
| /auth/refresh 空闲检测 | `backend/api/auth.py:refresh_token()` | `ENABLE_AUTH=true` 时检测：`idle_min > SESSION_IDLE_TIMEOUT_MINUTES` → 吊销 refresh_token → 401 `"会话已超时，请重新登录"` |
| last_login_at 兜底 | `backend/api/auth.py:refresh_token()` | `activity_ts = user.last_active_at or user.last_login_at`；二者均为 None（新账号）则跳过检测 |
| .env 配置更新 | `.env` | 新增 `SESSION_IDLE_TIMEOUT_MINUTES=120`；`ACCESS_TOKEN_EXPIRE_MINUTES` 由 480 调整为 120（须与空闲超时对齐）|
| 测试套件 | `test_session_management.py` (26) + `test_session_e2e.py` (35) | 静态分析（Cookie 属性/Settings/代码结构）+ 端到端（DB 追踪/refresh 检测/Session 生命周期/配置边界/菜单权限审计）|

### Skill 语义混合命中（已完成，2026-03-17）

| 功能 | 文件 | 说明 |
|------|------|------|
| SkillSemanticRouter | `backend/skills/skill_semantic_router.py` | 单次 LLM 调用，批量对候选 skill 打分（0-1.0），JSON 解析 + 正则提取容错 |
| SkillRoutingCache | `backend/skills/skill_routing_cache.py` | ChromaDB 磁盘持久化路由缓存；精确哈希匹配；TTL 24h；版本化失效 |
| build_skill_prompt_async() | `skill_loader.py` | 混合路由入口：keyword→cache→LLM→merge；支持 keyword/hybrid/llm 三模式 |
| _build_from_matched_skills() | `skill_loader.py` | 提取自 build_skill_prompt()，两条路径共用组装逻辑 |
| _build_system_prompt 改 async | `backend/agents/agentic_loop.py` | await build_skill_prompt_async(message, llm_adapter=self.llm_adapter) |
| preview API 增强 | `backend/api/skills.py:GET /preview` | 新增 match_details 字段（method/score/tier）+ mode= 参数 + async 调用 |
| 路由配置字段 | `backend/config/settings.py` | skill_match_mode / skill_semantic_threshold / skill_semantic_cache_ttl / skill_routing_cache_path |

### 技能路由可视化（已完成，2026-03-26）

| 功能 | 文件 | 说明 |
|------|------|------|
| _make_match_info() 快照生成 | `backend/skills/skill_loader.py` | 构造 match_info dict：mode/matched/always_inject/summary_mode/total_chars/load_errors |
| SkillLoader._last_match_info | `backend/skills/skill_loader.py` | `build_skill_prompt_async()` 完成后写入，`get_last_match_info()` 返回副本 |
| skill_matched SSE 事件 | `backend/agents/agentic_loop.py:run_streaming()` | `_build_system_prompt()` 之后、第一个 `thinking` 之前 yield；data = get_last_match_info() |
| SkillMatchInfo 类型 | `frontend/src/store/useChatStore.ts` | `SkillMatchedSkill`（name/tier/method/hit_triggers/score）+ `SkillMatchInfo` 接口 |
| skill_matched 事件路由 | `frontend/src/pages/Chat.tsx` | `chunk.type==='skill_matched'` → `addThoughtEvent()` 加入 messageThoughts |
| ThoughtProcess 🧠 技能路由面板 | `frontend/src/components/chat/ThoughtProcess.tsx` | 渲染模式标签/matched 技能（tier 徽标/hit_triggers/score）/always_inject 列表/字符数/load_errors 告警 |
| GET /skills/load-errors API | `backend/api/skills.py` | 返回加载失败的技能文件列表；权限：`settings:read`（analyst/admin/superadmin） |
| 持久化兼容 | `conversation_service.py:send_message_stream()` | skill_matched 纳入 thinking_events 收集范围，写入 extra_metadata，刷新后可回溯 |

### P2 — 审批系统 + 上下文管理（已完成）

| 功能 | 文件 | 说明 |
|------|------|------|
| 危险 SQL 检测 | `etl_agent.py:_detect_dangerous_sql()` | 正则检测 DROP/TRUNCATE/DELETE/ALTER/OPTIMIZE |
| 审批暂停/恢复 | `backend/core/approval_manager.py` | async Event 实现 Python 层真实暂停，60s 超时 |
| 审批 REST 接口 | `backend/api/approvals.py` | GET/POST approve/reject |
| ApprovalModal 前端 | `frontend/src/components/chat/ApprovalModal.tsx` | 60s 倒计时，同意/拒绝按钮 |
| ThoughtProcess 面板 | `frontend/src/components/chat/ThoughtProcess.tsx` | 可折叠推理过程展示 |
| 对话摘要 | `backend/core/conversation_summarizer.py` | LLM 摘要 + 规则兜底，永不抛出 |
| 混合上下文管理 | `backend/core/context_manager.py` | HybridContextManager（滑动窗口 + 摘要注入）+ SmartCompressionStrategy |
| 自动摘要触发 | `conversation_service.py:_maybe_summarize()` | 消息数 > max_context_messages 时触发 |
| context_compressed 事件 | SSE 事件 | 前端 toast 提示历史已压缩 |
| 向量存储 | `backend/core/vector_store.py` | ChromaDB 嵌入，相似对话检索（语义压缩辅助） |
| 语义压缩 | `backend/core/semantic_compression.py` | 基于相似度的旧消息压缩 |
| AgentOrchestrator v2 | `backend/agents/orchestrator_v2.py` | HandoffPacket 2-hop 路由 |

### P3 — 双 ClickHouse + Agent MCP 绑定（已完成）

| 功能 | 文件 | 说明 |
|------|------|------|
| FilteredMCPManager | `backend/core/agent_mcp_binder.py` | 按 Agent 类型过滤可见 MCP 服务器 |
| AgentMCPBinder | `agent_mcp_binder.py` | 读取 agent_config.yaml，路由 admin/readonly 连接 |
| 只读 ClickHouse 服务器 | `backend/mcp/manager.py:create_clickhouse_server(level)` | 启动时同时创建 admin + readonly 实例 |
| 双权限配置 | `backend/config/settings.py` | READONLY_USER/PASSWORD 字段，Optional[int] port 兼容空字符串 |
| agent_config.yaml | `.claude/agent_config.yaml` | Agent 绑定配置：max_iterations / clickhouse_connection / clickhouse_envs |

### ClickHouse 动态多区域配置 + /mcp/ API 鉴权（已完成，2026-03-18）

| 功能 | 文件 | 说明 |
|------|------|------|
| `load_dotenv(override=False)` 前置加载 | `backend/config/settings.py` | 按 `cwd→backend/→项目根` 顺序将所有 `.env` 键（含未声明字段）写入 `os.environ`；`override=False` 确保测试预设值不被 `.env` 覆盖 |
| `Config.extra = "ignore"` | `settings.py:class Config` | 允许未声明的 `CLICKHOUSE_THAI_*` 等字段，不触发 `ValidationError` |
| `get_all_clickhouse_envs()` 双源发现 | `settings.py` | Source 1: `model_fields` 扫描已声明 env（idn/sg/mx）；Source 2: `os.environ` 正则扫描 `CLICKHOUSE_*_HOST`，提取任意新增 env 名（thai/br/sg_azure/my 等）|
| `get_clickhouse_config()` / `has_readonly_credentials()` 回退 | `settings.py` | `getattr(settings, ...)` → `AttributeError` → `os.environ` 大小写不敏感扫描（兼容 Linux 混合大小写） |
| 启动汇总日志 | `backend/mcp/manager.py:initialize_all()` | 所有服务器注册完成后打印 INFO：`"[MCPManager] Initialization complete: N server(s) registered: ..."` |
| AgentMCPBinder 自动发现 | `backend/core/agent_mcp_binder.py:_extract_envs_from_manager()` | `agent_config.yaml: clickhouse_envs: all` → 自动发现全部已注册 clickhouse-* 服务器，新区域无需修改 yaml |
| /mcp/ API 鉴权 | `backend/api/mcp.py` | 全部 7 个端点补全权限依赖：GET→`settings:read`（admin+），POST→`settings:write`（admin+）；`ENABLE_AUTH=false` 时 AnonymousUser is_superadmin=True 直接通过 |
| `settings:read` 权限 | `backend/scripts/init_rbac.py` + RBAC 矩阵 | admin + superadmin 拥有此权限，用于访问 MCP 服务器列表、统计等只读端点 |
| 测试套件 | `test_ch_dynamic_env.py` | 35 个测试（G/H/I/J 节）：设置与动态发现 / MCPManager 注册 / Agent 绑定 / /mcp/ API 鉴权 |

### 文件系统权限隔离（已完成，2026-03-12）

| 功能 | 文件 | 说明 |
|------|------|------|
| FilesystemPermissionProxy | `backend/core/filesystem_permission_proxy.py` | 目录级写权限代理，拦截 write_file/create_directory/delete，路径不在白名单则拒绝 |
| filesystem_write_allowed_dirs | `backend/config/settings.py` | 新增字段，默认 `[customer_data/, .claude/skills/user/]`，可通过 env 覆盖 |
| allowed_directories 收窄 | `settings.py` + `.env` | 默认值改为 `[customer_data/, .claude/skills/]`，不再是整个项目根目录 |
| 自动代理包裹 | `agent_mcp_binder.py:get_filtered_manager()` | filesystem 在 allowed 时自动返回 FilesystemPermissionProxy |
| 技能 API 权限隔离 | `backend/api/skills.py` + `backend/api/deps.py` | 系统技能只读（API 无写端点）；用户技能独立目录，path-boundary 强制检查 |
| 前端只读标识 | `frontend/src/pages/Skills.tsx` | 系统技能显示锁形图标 + "只读" Tag，隐藏删除按钮 |

### FilesystemPermissionProxy 安全增强 Fix-1~4（已完成，2026-03-23）

| 修复 | 文件 | 说明 |
|------|------|------|
| Fix-1: LLM 路径模板注入 | `backend/agents/agentic_loop.py:_build_system_prompt()` | 从 `allowed_directories` 自动识别 `skills_root`（含 `.claude` 的目录）和 `data_root`（其余目录），向 LLM 注入精确路径模板：数据文件 → `{data_root}/{username}/`，技能文件 → `{skills_root}/user/{username}/skill-name.md`，消除路径歧义（Fix-1 原始；`{data_root}/{username}/` 子目录形式在 2026-03-24 用户隔离改造中引入）|
| Fix-2: 跨根反向写入拦截 | `backend/core/filesystem_permission_proxy.py:_is_write_allowed()` | 拦截 LLM 误将技能文件写入 `customer_data/.claude/...` 的错误路由；检测路径中出现 `.claude` 段且父路径在数据根时拒绝写入 |
| Fix-3: 拒绝消息路径模板 | `filesystem_permission_proxy.py:call_tool()` | 技能路径被拒绝时，错误消息包含正确的技能文件路径模板（含 `{用户名}/` 层），引导 LLM 自纠行为 |
| Fix-4: username 子目录深度校验（工具名感知）| `filesystem_permission_proxy.py:_check_skills_user_subdir()` | 禁止直接写入 `user/` 根目录；`create_directory` 需路径深度 ≥ 1（允许创建 `user/alice/`），`write_file`/`delete` 需深度 ≥ 2（必须为 `user/alice/skill.md`）；不同工具使用不同深度阈值 |
| 测试套件 | `test_skill_path_comprehensive.py` | 51 个测试（A-G 节：Fix-1 路径模板/Fix-2 跨根拦截/Fix-3 错误消息/Fix-4 子目录校验/端到端/RBAC/安全边界）|

### 文件系统路径可移植性（已完成，2026-03-23）

| 功能 | 文件 | 说明 |
|------|------|------|
| `_PROJECT_ROOT` 常量 | `backend/config/settings.py` | 模块顶部定义 `Path(__file__).parent.parent.parent.resolve()`，作为路径解析的单一事实来源（不依赖运行时 `cwd`）|
| `_resolve_fs_paths` Validator | `settings.py` | `@field_validator("allowed_directories", "filesystem_write_allowed_dirs", mode="after")`：相对路径以 `_PROJECT_ROOT` 为基准解析为绝对路径；绝对路径直接通过（向后兼容已有生产配置）|
| `.env` 改为相对路径 | `.env` | `ALLOWED_DIRECTORIES=["customer_data",".claude/skills"]`；`FILESYSTEM_WRITE_ALLOWED_DIRS=["customer_data",".claude/skills/user"]`；部署到任意服务器无需修改 `.env` 路径配置 |
| LLM 始终注入绝对路径 | `agentic_loop.py` | `fs_obj.allowed_directories`（已经是 resolved 绝对路径）注入到 LLM 系统提示；filesystem MCP 工具只接受绝对路径，运行行为不变 |
| 下游模块零修改 | `filesystem_permission_proxy.py` / `mcp/manager.py` / `agent_mcp_binder.py` | 所有下游模块消费 `settings.allowed_directories`（已是绝对路径），无感知配置层改动 |
| 测试套件 | `test_path_portability.py` | 41 个测试（A-I 节：`_PROJECT_ROOT` 正确性 / validator 相对路径解析 / 绝对路径向后兼容 / .env 加载验证 / FilesystemMCPServer 接收绝对路径 / proxy/binder 路径 / LLM 注入绝对路径 / RBAC 无新权限 / 安全边界）|

### 对话用户隔离（已完成，2026-03-24）

| 功能 | 文件 | 说明 |
|------|------|------|
| conversations.user_id 字段 | `backend/models/conversation.py` | 新增 `user_id = Column(UUID, ForeignKey("users.id"), nullable=True)`；nullable 兼容 ENABLE_AUTH=false 匿名模式；`to_dict()` 暴露 `user_id` |
| conversation_groups.user_id 字段 | `backend/models/conversation_group.py` | 同上；每个分组有归属用户 |
| DB 迁移脚本 | `backend/scripts/migrate_conversation_user_isolation.py` | 支持 `--dry-run`；① 两张表加 `user_id` UUID FK；② 存量数据归属 superadmin；③ 建索引 |
| service 层用户过滤 | `backend/services/conversation_service.py` | `create_conversation(user_id=)`；`list_conversations(user_id=)` 按 user_id 过滤（None 则返回全部，兼容匿名模式）；新增 `list_all_conversations_by_user(exclude_user_id=)` 按用户分组返回对话 |
| conversations API 全鉴权 | `backend/api/conversations.py` | 所有 CRUD 端点加 `Depends(get_current_user)`；`_check_conversation_ownership()` 辅助函数：非 superadmin + user_id 不匹配 → 403；`_get_user_id()` 辅助：AnonymousUser → None，UUID 用户 → UUID |
| groups API 全鉴权 | `backend/api/groups.py` | 同上模式；`create_group` 唯一性校验 + sort_order 均限定在当前用户范围 |
| superadmin 全局视图端点 | `backend/api/conversations.py:GET /conversations/all-users-view` | 需注册在 `GET /conversations/{conversation_id}` 路由之前防路径冲突；返回 `List[OtherUserConversations]`（user_id/display_name/conversations）；仅 superadmin 可访问（`_is_superadmin()` 检查） |
| 前端 fetchAllUsersConversations | `frontend/src/services/chatApi.ts` | `adminApi.fetchAllUsersConversations()` → `GET /conversations/all-users-view` |
| 前端 ConversationSidebar "其他用户" 区块 | `frontend/src/components/chat/ConversationSidebar.tsx` | 接受 `otherUsersData` prop；superadmin 时在侧边栏底部渲染 Collapse 按用户分组展示他人对话（只读，无操作按钮）|
| 前端 Chat.tsx 集成 | `frontend/src/pages/Chat.tsx` | `authUser.is_superadmin` 时加载 `otherUsersData`；传递给 ConversationSidebar |
| 安全漏洞修复（4处）| `backend/api/conversations.py` | Bug-1: `POST /{id}/messages`（send_message）有 auth 但缺 ownership 校验；Bug-2: `GET /{id}/messages`（get_messages）无 auth；Bug-3: `POST /{id}/regenerate` 无 auth；Bug-4: `POST /{id}/clear` 无 auth — 均已补全 `Depends(get_current_user)` + `_check_conversation_ownership()` |
| RBAC 范围 | — | 对话/分组端点无新权限键，仅依赖 JWT 身份验证；viewer 角色可正常使用；`all-users-view` 端点用 `is_superadmin` 标志而非 RBAC 权限键 |
| 测试套件 | `test_conversation_isolation.py` | 42 个测试（A-G 节：service 层过滤 / 对话 API 隔离 / superadmin 全局视图 / 分组 API 隔离 / ENABLE_AUTH=false 兼容 / 安全漏洞验证 / RBAC 范围）|

### customer_data 用户数据隔离（已完成，2026-03-24）

| 功能 | 文件 | 说明 |
|------|------|------|
| 数据目录按用户隔离 | `backend/agents/agentic_loop.py:_build_system_prompt()` | LLM 路径规则改为 `{data_root}/{current_username}/`（每位用户独立子目录）；`user_data_root` 变量拼接用户名，注入系统提示 |
| 分析师 Agent 路径约束 | `backend/agents/analyst_agent.py` | `path_constraint` 改为 `customer_data/{username}/`，示例路径含用户名层，指导 Agent 写到正确目录 |
| 权限代理错误提示 | `backend/core/filesystem_permission_proxy.py` | 拒绝消息从 `customer_data/ 目录` 改为 `customer_data/{用户名}/ 目录`，提示更精准 |
| 技能文件路径更新 | `.claude/skills/user/superadmin/clickhouse-analyst.md` | 知识库路径 `customer_data/db_knowledge/` → `customer_data/superadmin/db_knowledge/` |
| 系统安全技能更新 | `.claude/skills/system/_base-safety.md` + `_base-tools.md` | 写入目录规则 `customer_data/` → `customer_data/{CURRENT_USER}/` |
| 历史数据迁移脚本 | `backend/scripts/migrate_customer_data.py` | 将 `customer_data/customer_data/**` 和 `customer_data/reports/` 迁移到 `customer_data/superadmin/`；冲突保留较新文件，旧文件存为 `.bak` |
| 测试套件 | `test_customer_data_isolation.py` | 31 个测试（U-Z 节：迁移状态验证/路径注入/分析师约束/权限代理覆盖/技能文件内容/RBAC 范围）|

**权限层设计说明**：`FilesystemPermissionProxy` 的写入白名单（`filesystem_write_allowed_dirs`）仍配置为 `customer_data/`（根目录），通过 `Path.relative_to()` 检查自动覆盖所有 `customer_data/{username}/` 子目录，**无需修改权限代理配置**。LLM 层面通过路径规则约束用户只写自己的子目录（软约束），权限层面后端只验证是否在 `customer_data/` 根目录下（硬边界）。

**向后兼容**：`ENABLE_AUTH=false`（匿名用户 `username=default`）时数据目录为 `customer_data/default/`；旧数据（若存在）已通过 `migrate_customer_data.py` 迁移到 `customer_data/superadmin/`。

### 近限制综合 + 自动续接（已完成，2026-03-12）

| 功能 | 文件 | 说明 |
|------|------|------|
| 近限制检测 | `agentic_loop.py`（NEAR_LIMIT_THRESHOLD=5） | remaining <= 5 时切换综合模式 |
| _synthesize_and_wrap_up() | `agentic_loop.py` | LLM 无工具调用，输出阶段结论 + 待办 JSON |
| _parse_synthesis_output() | `agentic_loop.py` | 解析 `### 阶段性分析结论` + ` ```json ``` ` |
| near_limit SSE 事件 | AgentEvent | 携带 pending_tasks / conclusions |
| 自动续接（最多3次） | `conversation_service.py` | 检测 near_limit → 递归调用 send_message_stream（_continuation_round 参数） |
| 自动续接状态持久化 | `conversation_service.py` | auto_continue_state 存入 conversation.extra_metadata |
| continuation_approval_required | SSE 事件 | 3次耗尽后触发，前端 Modal 人工确认 |
| pendingContinuation 前端状态 | `frontend/src/store/useChatStore.ts` | Zustand 状态 |
| 续接确认 Modal | `frontend/src/pages/Chat.tsx` | 继续/暂停按钮，展示剩余任务列表 |

### 推理过程持久化 + continuation 角色（已完成，2026-03-18）

| 功能 | 文件 | 说明 |
|------|------|------|
| thinking_events 收集 | `conversation_service.py` | 流式过程中收集 thinking/tool_call/tool_result 事件列表 |
| tool_result 截断 | `conversation_service.py`（_MAX_THINKING_TOOL_RESULT_CHARS=2000） | tool_result.data > 2000 字符时截断并追加"…（已截断）" |
| thinking_events 持久化 | `conversation_service.py` → `Message.extra_metadata` | 助手消息保存时写入 `extra_metadata['thinking_events']` |
| to_dict() 顶层提升 | `backend/models/conversation.py:Message.to_dict()` | `extra_metadata['thinking_events']` 提升为顶层字段，随 `/messages` API 返回 |
| 前端恢复推理事件 | `frontend/src/pages/Chat.tsx:loadMessages()` | 加载历史消息时，遍历 `thinking_events` 字段还原 messageThoughts 状态 |
| continuation 角色保存 | `conversation_service.py` | _continuation_round > 0 时以 `role='continuation'` 保存续接提示消息（区别于普通 user 消息） |
| extra_metadata 结构化 | `conversation_service.py` | continuation 消息写入 `{continuation_round, max_rounds, pending_tasks, conclusions}` |
| _build_context 角色映射 | `conversation_service.py:_build_context()` | continuation → user 角色转换，避免 MessageRole 枚举 ValueError |
| Message.role union 扩展 | `frontend/src/store/useChatStore.ts` | role 类型增加 `'continuation'` |
| ContinuationCard 组件 | `frontend/src/components/chat/ContinuationCard.tsx` | 紧凑虚线边框横幅卡片，显示续接轮次 N/M，可折叠展开结论摘要 + 待办任务列表 |
| ChatMessages 路由渲染 | `frontend/src/components/chat/ChatMessages.tsx` | role='continuation' 时渲染 ContinuationCard（非消息气泡） |
| SSE user_message 续接处理 | `frontend/src/pages/Chat.tsx` | chunk.data.role==='continuation' 时 addMessage + 新建空 assistant placeholder |

### 对话打断功能（已完成，2026-03-19）

| 功能 | 文件 | 说明 |
|------|------|------|
| ConversationCancelManager | `backend/core/cancel_manager.py` | 每个 conversation 懒创建一个 `asyncio.Event`；`request_cancel()`/`should_cancel()`/`clear()` 三个操作；模块级单例 `cancel_manager` |
| `_CancelledByUser` 内部异常 | `backend/agents/agentic_loop.py` | AgenticLoop 内部异常类，在 `_cancellable_await()` 中抛出，被 `run_streaming()` 捕获转为 `cancelled` 事件 |
| `_cancellable_await()` 方法 | `agentic_loop.py` | `asyncio.wait({lm_task, cancel_event.wait()}, FIRST_COMPLETED)` 模式；LLM 调用中途可被立即取消（不等 HTTP 响应完成）|
| cancel_event 参数传递链 | `orchestrator.py` / `etl_agent.py` / `analyst_agent.py` | `process_stream()` 新增 `cancel_event: Optional[asyncio.Event]` 参数，逐层传至 `AgenticLoop.__init__()` |
| 工具调用后边界检测 | `agentic_loop.py` | 每次 `_execute_tool()` 后检查 `cancel_event.is_set()`，工具完成后立即响应取消信号 |
| 部分内容保存 + 中断标记 | `backend/services/conversation_service.py` | `was_cancelled=True` 时助手消息内容末尾追加 `"\n\n---\n*（生成已被用户中断）*"`；`extra_metadata["cancelled"]=True` |
| 新消息开始时清除取消状态 | `conversation_service.py` | `send_message_stream()` 开头调用 `cancel_manager.clear(conv_id)`，确保每条新消息从干净状态开始 |
| 取消 REST 端点 | `backend/api/conversations.py` | `POST /conversations/{id}/cancel` → `cancel_manager.request_cancel(str(id))` → `{"status":"cancellation_requested"}`；幂等，无需鉴权（与全部 12 个对话端点一致）|
| 前端 AbortController | `frontend/src/services/chatApi.ts` | 模块级 `_activeController`/`_activeConvId`；`sendMessageStream()` 注入 `signal`；1s 后 abort（兜底）；AbortError 静默处理 |
| `cancelConversationStream()` | `chatApi.ts` | 先 POST cancel 端点，1s 后再 abort fetch；组合双通道取消 |
| `isCancelling` 状态 | `frontend/src/store/useChatStore.ts` | 防止用户重复点击停止按钮；`onComplete`/`onError` 时自动重置为 false |
| 停止生成按钮 | `frontend/src/pages/Chat.tsx` | `sending=true` 时渲染「停止生成」按钮（`StopOutlined`，danger 样式，loading=isCancelling）|
| cancelled chunk 处理 | `Chat.tsx` | `chunk.type==='cancelled'` → `message.info("已停止生成", 2)` toast 通知 |

### 对话附件上传（已完成，2026-03-23）

| 功能 | 文件 | 说明 |
|------|------|------|
| AttachmentData Pydantic 模型 | `backend/api/conversations.py` | `AttachmentData(name,mime_type,size,data)` + `SendMessageRequest.attachments: List[AttachmentData] = []` |
| 附件元数据存储 | `backend/services/conversation_service.py` | `send_message_stream(attachments=None)`：剥离 base64，仅存 `{name,mime_type,size}` 到 `messages.extra_metadata["attachments"]`；无需数据库迁移（JSONB 列复用）|
| 历史消息附件注解 | `conversation_service.py:_build_context()` | 历史消息有附件时追加 `[附件: name (type, size bytes)]`，供 LLM 感知上文曾有附件但不重传 base64 |
| current_attachments 上下文注入 | `conversation_service.py` | `_build_context()` 后注入 `context["current_attachments"]`（含完整 base64） |
| _perceive() 多模态块构建 | `backend/agents/agentic_loop.py` | `context["current_attachments"]` → Claude API 多模态 content blocks：image/document/text；`import base64` 移至模块顶部（Bug-1 修复）|
| ChatInput 附件按钮 | `frontend/src/components/chat/ChatInput.tsx` | PaperClipOutlined 按钮触发文件选择；`accept=".jpg,.jpeg,.png,.gif,.webp,.pdf,.txt,.csv,.md,.json"` |
| ChatInput 粘贴图片 | `ChatInput.tsx` | ClipboardEvent 监听，识别 `image/*` 类型的粘贴文件，自动转 base64 |
| ChatInput 用户反馈 | `ChatInput.tsx` | 文件类型/大小不符时 `antMessage.error()`（Bug-2 修复；原仅 console.warn）|
| MIME 类型推断 | `ChatInput.tsx:inferMimeType()` | `file.type` 为空时（Windows 某些系统）按扩展名回退（Bug-3 修复）|
| 附件预览 chips | `ChatInput.tsx` | 发送前展示 Tag chips（文件名+图标），可×删除 |
| 附件 chips 历史渲染 | `frontend/src/components/chat/ChatMessages.tsx` | 历史消息中 `extra_metadata.attachments` 渲染为紧凑 Tag chips |
| Chat.tsx 联动 | `frontend/src/pages/Chat.tsx` | `handleSendMessage(content, attachments)` + `sendMessageToConversation(convId, content, attachments)` |
| chatApi 传参 | `frontend/src/services/chatApi.ts` | `sendMessageStream(..., attachments?)` → `attachments && length > 0` 时加入 POST body |
| RBAC 无新权限 | — | 走现有 `POST /{id}/messages` 端点，无新路由/菜单/权限 |
| 测试套件 | `test_attachment_upload.py` | 84 个测试（A-I：模型验证/多模态块/元数据/注解/API/RBAC回归/端到端/边界/Bug修复验证）|

### 文件写入下载（已完成，2026-03-26）

Agent 在对话中写文件后，消息末尾自动附上下载卡片；用户点击触发浏览器下载；文件路径强制限制在当前用户目录内。

| 功能 | 文件 | 说明 |
|------|------|------|
| T1: 文件写入检测 + files_written SSE 事件 | `backend/agents/agentic_loop.py` | `run_streaming()` 监听 `write_file` 工具调用成功；`written_files: List[dict]` 追踪本轮写入；`end_turn`/`near_limit` 路径结束时发出 `AgentEvent(type="files_written", data={"files": [...]})`；`_infer_mime_type()` 推断 25+ 扩展名对应 MIME 类型 |
| T2: 安全下载端点 | 新建 `backend/api/files.py` + 注册 `backend/main.py` | `GET /api/v1/files/download?path=...`；`Depends(get_current_user)` 认证（无独立 `require_permission`，viewer+ 均可）；`_resolve_download_path()` 强制路径在 `customer_data/{username}/` 下（支持含/不含前缀两种格式）；跨用户 403，穿越 403，文件不存在 404，目录路径 400；`Content-Disposition: attachment; filename*=UTF-8''...` 支持中文文件名 |
| T3: 月份子目录配置 | `backend/config/settings.py` + `agentic_loop._build_system_prompt()` + `.env` | `file_output_date_subfolder: bool`（`FILE_OUTPUT_DATE_SUBFOLDER=false`）；`true` 时向 Agent system prompt 注入月份路径建议（`YYYY-MM/`），便于历史文件按月管理和批量清理；默认 `false` 不影响 Agent 自主路径选择 |
| T4: 文件元数据持久化 | `backend/services/conversation_service.py` | `send_message_stream()` 消费 `files_written` 事件 → `asst_extra_meta["files_written"] = files_written_info`；复用 `messages.extra_metadata` JSONB 列，**无需数据库迁移** |
| T5: 前端状态管理 | `frontend/src/store/useChatStore.ts` + `frontend/src/pages/Chat.tsx` | 新增 `FileInfo`/`FilesWrittenInfo` 接口；`Message.files_written?: FileInfo[]`；`setMessageFilesWritten(messageId, files)` action；`Chat.tsx` SSE chunk 处理 `files_written` 类型；历史对话加载时从 `extra_metadata.files_written` 还原 |
| T6: 文件下载卡片 UI | `frontend/src/components/chat/ChatMessages.tsx` | `FileDownloadCards` 组件（文件图标 + 文件名 + 大小 + 下载按钮）；助手消息 `files_written` 非空时渲染在消息气泡下方（内容区之后、操作按钮之前）；`_formatFileSize()` + `_getFileIcon()`（按 MIME 类型着色）|
| T7: 浏览器下载实现 | `frontend/src/services/chatApi.ts` | `fileApi.downloadFile(filePath, filename)`：`axios GET /files/download`（`responseType: 'blob'`）→ `URL.createObjectURL` → `<a download>` 动态点击 → 触发浏览器保存对话框，无需新标签页跳转 |
| RBAC 范围 | — | **无新菜单/路由/权限键**。下载为消息内嵌功能，不是独立页面；download 端点只用 `get_current_user`（登录即可）；用户隔离通过 `_resolve_download_path` 路径校验实现，不依赖权限键 |
| 测试套件 | `test_file_download.py` + `test_file_download_e2e.py` | `test_file_download.py`：41 个单元测试（A-H：MIME 推断/写入检测/事件结构/路径安全/API 集成/日期子目录/持久化/回归）；`test_file_download_e2e.py`：38 个 E2E 测试（A-G：RBAC 鉴权/E2E 管道/跨用户隔离/消息 API 元数据/文件名编码/日期子目录/RBAC 菜单回归）|

### 侧边栏 Tab UI + 只读模式 + is_shared 群组框架兼容（已完成，2026-03-25）

| 功能 | 文件 | 说明 |
|------|------|------|
| 双 Tab 侧边栏 | `frontend/src/components/chat/ConversationSidebar.tsx` | Tab1 "我的对话"（原有功能）；Tab2 "其他用户(N)"（superadmin 专属，badge=对话总数）；无 otherUsersData 时不渲染 Tabs，兼容普通用户 |
| isViewingOtherUserConv | `frontend/src/pages/Chat.tsx` | handleSelectConversation 检测 conv 归属 → useState；handleSendMessage guard；停止按钮隐藏 |
| ChatInput readOnly 横幅 | `frontend/src/components/chat/ChatInput.tsx` | readOnly=true 时整个输入区替换为黄色警告 banner "👁 仅查看模式 — 当前对话属于其他用户" |
| 后端写权限函数 | `backend/api/conversations.py` | `_check_conversation_write_permission()`：普通用户→ownership；superadmin→自己/is_shared=True 允许，他人对话 403 |
| is_shared 字段 | `backend/models/conversation.py` | `is_shared = Column(Boolean, default=False)`；`to_dict()` 暴露；useChatStore.ts Conversation 接口加 `is_shared?: boolean` |
| DB 迁移 | `backend/scripts/migrate_add_is_shared.py` | `ALTER TABLE conversations ADD COLUMN is_shared BOOLEAN NOT NULL DEFAULT FALSE`（幂等，检查列是否存在）|
| clear_messages Bug 修复 | `backend/services/conversation_service.py` | 补全缺失的 `clear_messages()` 方法（POST /{id}/clear 端点此前调用时返回 500）|
| RBAC 范围 | — | 无新菜单/路由/权限键；只读模式通过 is_superadmin + user_id 比较实现，不新增权限键 |
| 测试套件 | `test_tab_readonly.py` | 53 个测试（A-I 节：DB is_shared 列 / 写权限函数 / send_message E2E / regenerate/clear / superadmin 只读访问 / is_shared 群组扩展 / 前端代码结构静态检查 / RBAC 范围 / 回归隔离）|

### ClickHouse TCP→HTTP 自动回退（已完成，2026-03-20）

| 功能 | 文件 | 说明 |
|------|------|------|
| ClickHouseHTTPClient | `backend/mcp/clickhouse/http_client.py` | `requests`-based，`execute()` 接口兼容 `clickhouse-driver`；SELECT → `FORMAT JSONCompact`；非 SELECT → 空结果集 |
| initialize() TCP 探测 | `backend/mcp/clickhouse/server.py` | 先尝试 TCP 9000（`connect_timeout=5`），失败自动回退 HTTP 8123；`self._protocol = "native"/"http"` |
| _test_connection() protocol 字段 | `server.py` | 连接测试结果新增 `protocol`/`active_port` 字段，区分当前使用的协议 |
| 服务器名连字符规范化 | `backend/mcp/manager.py` | `server_env = env.replace("_","-")`，注册名改为纯连字符（如 `clickhouse-sg-azure`），修复 `tool_formatter` encode/decode 往返 Bug |
| logging 修复 | `server.py` | 补充 `import logging` + `logger = getLogger(__name__)`（原缺失导致 NameError）|
| 测试套件 | `test_ch_http_fallback.py` | 78 个测试（A-L，含 RBAC 回归）|

### ClickHouse MCP Bug 修复（已完成，2026-03-12）

| 修复 | 文件 | 说明 |
|------|------|------|
| Fix-1: batch_describe_tables | `backend/mcp/clickhouse/server.py` | 批量获取最多30张表结构，减少推理轮次 |
| Fix-2: get_table_overview 兼容性 | `server.py:_get_table_overview()` | total_rows NULL 安全，旧版 CH 降级，COUNT(*) 兜底 |
| Fix-3: DDL 词边界检测 | `server.py:_DDL_KEYWORD_RE` | `\b(DROP\|TRUNCATE\|ALTER\|CREATE)\b` 避免 create_time 误报 |
| Fix-4: 移除 use_numpy | `server.py:initialize()` | 不传 use_numpy，_to_json_safe() 覆盖所有类型转换 |

---

## 二、待实现功能（按优先级）

### P1 — 高优先级

| # | 功能 | 说明 | 涉及模块 |
|---|------|------|---------|
| 1 | **可视化 Agent** | 根据分析结果自动生成图表（ECharts/Plotly），支持折线/柱/饼/散点 | 新建 `backend/agents/viz_agent.py`，前端图表组件 |
| 2 | **报表生成** | 将对话中的分析结论导出为 PDF/Excel/Markdown 报告 | `backend/services/report_service.py`，PDF 渲染 |
| 3 | **任务调度** | 定时执行 ETL SQL 脚本（cron），任务状态追踪 | `backend/services/task_service.py`，APScheduler |
| 4 | **前端多环境快速切换** | 前端下拉切换 idn/sg/mx/thai 等环境（后端动态注册已实现，前端 UI 切换待做）| `frontend/pages/Chat.tsx`，orchestrator 参数传递 |

### P2 — 中优先级

| # | 功能 | 说明 | 涉及模块 |
|---|------|------|---------|
| 5 | **对话导出** | 将完整对话（含工具调用详情）导出为 Markdown/JSON | `conversation_service.py`，导出 API |
| 6 | **文件上传分析** | 上传 CSV/Excel，注入 ClickHouse 临时表后分析 | `backend/api/conversations.py`，Filesystem MCP |
| 7 | **Agent 执行历史** | 记录每次工具调用的耗时、成功率，可视化 Agent 效率 | `backend/models/`，Dashboard 页面 |
| 8 | **Lark 集成增强** | 将分析结果直接发送到飞书文档/消息，关联 Lark 表格 | `backend/mcp/lark/`，Lark MCP Server |
| 9 | **MySQL 支持完善** | 支持 MySQL 源表同步到 ClickHouse 的 ETL 设计 | `backend/mcp/mysql/`，ETL Agent |

### P3 — 低优先级 / 探索性

| # | 功能 | 说明 | 涉及模块 |
|---|------|------|---------|
| 11 | **Gemini 适配器** | 完整支持 Google Gemini 模型（当前有骨架，缺 API 密钥测试） | `backend/core/model_adapters/` |
| 12 | **语义缓存增强（对话层）** | 对话级相似问题直接返回缓存结果，节省 LLM 调用（Skill 路由缓存已在 2026-03-17 实现）| `backend/core/semantic_cache.py` |
| 13 | **多 Agent 协作** | 两个 Agent 串行协作（ETL Agent 建表 → Analyst Agent 验证）| `orchestrator_v2.py` HandoffPacket |
| 14 | **Docker 部署** | Dockerfile + docker-compose，一键部署 | 根目录 Docker 配置 |
| 15 | **实时数据监控** | 订阅 ClickHouse 物化视图更新，异常自动告警 | MCP Server 扩展，消息推送 |

---

## 三、已知技术债务

| 项目 | 说明 | 影响程度 |
|------|------|---------|
| PROJECT_PROGRESS.md 阶段 5-9 描述已过时 | 这些阶段对应了旧的开发路线图，当前实际进展远超其描述 | 低（信息混乱） |
| Gemini adapter `google.generativeai` 未安装 | 测试中会出现 WARNING，不影响 Claude/OpenAI | 低 |
| `tiktoken` 未安装 | token_counter 降级到估算模式，精度低约 5% | 低 |
| ETL 审批：人工循环 TODO | 当前自动审批（auto-approve），真正暂停等待人工的完整前端流程已通，但 ETLAgenticLoop 还有一处 `# TODO: human-loop` 注释 | 中 |
| orchestrator_v2.py 与 orchestrator.py 并存 | v2 未完全替换 v1，两套代码逻辑需要合并 | 中 |
| 向量存储冷启动慢 | ChromaDB 首次启动嵌入加载较慢（约 5-10 秒） | 低 |

---

## 四、测试覆盖情况

| 测试文件 | 覆盖功能 | 用例数 |
|---------|---------|--------|
| `test_e2e_p0_p1.py` | P0 AgenticLoop + P1 路由 | ~30 |
| `test_t10_t12.py` | ETL/Analyst Agent + SkillLoader | 29 |
| `test_p2.py` | 审批系统 + 上下文管理 | 71 |
| `test_p3.py` | 双 ClickHouse + MCP Binder | 82 |
| `test_context_management.py` | 摘要 + 压缩策略 | 29 |
| `test_auto_continuation.py` | max_tokens 续写 + 停滞检测 | 21 |
| `test_near_limit_continuation.py` | 近限制综合 + 自动续接 | 15 |
| `test_thinking_continuation.py` | 推理过程持久化 + continuation 角色（A-H 层）| 28 |
| `test_mcp_fixes.py` | ClickHouse MCP 修复（基础） | 15 |
| `test_mcp_fixes_comprehensive.py` | ClickHouse MCP 修复（全面） | 74 |
| `test_skills_permission.py` | 技能权限隔离（P1-P7） | 11 |
| `test_filesystem_permission.py` | 文件系统目录级写权限（F1-F14） | 15 |
| `test_skill_path_comprehensive.py` | FilesystemPermissionProxy Fix-1~4 综合（A-G 节：路径模板/跨根拦截/错误消息/子目录深度/端到端/RBAC/安全边界）| **51** |
| `test_path_portability.py` | 文件系统路径可移植性（A-I 节：_PROJECT_ROOT/validator/绝对路径向后兼容/.env加载/MCP接收路径/proxy路径/LLM注入/RBAC/安全边界）| **41** |
| `test_skill_e2e.py` | 三层技能体系端到端（A-P 节，新增 P：用户技能目录隔离）| 144 |
| `test_skill_isolation_e2e.py` | 用户技能目录隔离 E2E（A-H 节：路径逻辑/CRUD文件验证/跨用户隔离/权限矩阵/Context注入链/向后兼容/菜单RBAC/init_rbac种子数据）| 43 |
| `test_skill_semantic_router.py` | SkillSemanticRouter（15 用例）| 15 |
| `test_skill_routing_cache.py` | SkillRoutingCache（12 用例）| 12 |
| `test_skill_loader_async.py` | build_skill_prompt_async（20 用例）| 20 |
| `test_semantic_skill_routing.py` | 语义路由综合（A-F 节，39 用例）| 39 |
| `test_rbac.py` | RBAC 认证全链路（A-I 层：密码/JWT/Auth/Users/Skills 隔离/安全边界）| **85** |
| `test_rbac_e2e.py` | RBAC E2E 全链路（J-O 节：角色管理API / 权限列表 / 菜单权限范围 / 角色生命周期 / 认证流 / 权限矩阵）| **45** |
| `test_auth_flow.py` | 认证流程端到端（P-T 节：后端可达性 / initAuth 四路径 / RequireAuth 逻辑 / 菜单可见性 / 登录后权限分布）| **32** |
| `test_ch_dynamic_env.py` | ClickHouse 动态环境 + /mcp/ API 鉴权（G-J 节）| **35** |
| `test_session_management.py` | Session 过期管理静态分析（A-F 节：Cookie 属性 / Settings 配置 / 节流逻辑 / refresh 空闲检测 / 活跃用户 / 代码结构）| **26** |
| `test_session_e2e.py` | Session 过期管理端到端（U-Z 节：Session Cookie / last_active_at DB 追踪 / refresh 空闲检测 / 生命周期 / 配置边界 / 菜单权限审计）| **35** |
| `test_stream_cancel.py` | 对话打断单元（A-F 节：CancelManager / AgenticLoop 取消 / conversation_service / REST 端点 / 集成 / 回归）| **21** |
| `test_cancel_e2e.py` | 对话打断端到端（G-L 节：HTTP E2E / 状态恢复 / RBAC 一致性 / 边界 / 前端 chunk 处理 / 回归）| **20** |
| `test_attachment_upload.py` | 对话附件上传（A-I 节：AttachmentData 模型 / _perceive 多模态 / 元数据存储 / 历史注解 / API / RBAC 回归 / 端到端 / 边界 / Bug 修复验证）| **84** |
| `test_conversation_isolation.py` | 对话用户隔离（A-G 节：service 层过滤 / 对话 API 隔离 / superadmin 全局视图 / 分组 API 隔离 / ENABLE_AUTH=false 兼容 / 安全漏洞验证 / RBAC 范围）| **42** |
| `test_tab_readonly.py` | 侧边栏 Tab UI + 只读模式 + is_shared（A-I 节：DB is_shared 列 / 写权限函数 / send_message E2E / regenerate/clear / superadmin 只读访问 / is_shared 群组扩展 / 前端代码结构静态检查 / RBAC 范围 / 回归隔离）| **53** |
| `test_skill_match_visibility.py` | 技能路由可视化（A-G 节：_make_match_info 单元 / keyword 模式 match_info / hybrid 模式 match_info / get_last_match_info 行为 / skill_matched 事件发射 / 事件顺序与结构 / RBAC load-errors 端点）| **57** |
| `test_file_download.py` | 文件写入下载单元（A-H 节：MIME 推断 / 写入检测 / events 结构 / 路径安全 / API 集成 / 日期子目录 / 持久化 / 回归）| **41** |
| `test_file_download_e2e.py` | 文件写入下载 E2E（A-G 节：RBAC 鉴权 / E2E 管道 / 跨用户隔离 / 消息 API 元数据 / 文件名编码 / 日期子目录 / RBAC 菜单回归）| **38** |
| **合计** | | **~1430** |

---

## 五、功能依赖关系图

```
AgenticLoop (P0)
  ├─ CancelManager (2026-03-19) → asyncio.Event per conv → _cancellable_await(lm_task)
  │    ├─ 取消信号通过 process_stream(cancel_event=) 传递给所有 Agent 类型
  │    ├─ _CancelledByUser 异常 → yield AgentEvent(type="cancelled") → return
  │    └─ send_message_stream: was_cancelled → partial 保存 + "已中断" marker
  │         → POST /conversations/{id}/cancel → cancel_manager.request_cancel()
  │         → 前端: cancelConversationStream() = POST cancel + setTimeout abort(1s)
  ├─ ETLAgenticLoop (P1) → ApprovalManager (P2) → [REST /approvals]
  │                      → DDL regex (Fix-3)
  ├─ DataAnalystAgent (P1) → ReadOnlyMCPProxy (P1)
  │
  ├─ 近限制综合 (2026-03-12) → ConversationService 自动续接（_continuation_round 参数，最多3次）
  │                          → continuation 角色消息写入 DB（extra_metadata: round/max/tasks/conclusions）
  │                          → _build_context() continuation→user 映射（避免 MessageRole 枚举 ValueError）
  │                          → thinking_events 收集（stream 期间，截断>2000字符）
  │                             → Message.extra_metadata['thinking_events']
  │                             → to_dict() 顶层提升 → GET /messages 返回
  │                             → loadMessages() → messageThoughts 恢复（刷新后可见）
  │                          → ContinuationCard.tsx（role='continuation'渲染，非气泡）
  ├─ max_tokens 续写 (P0)
  └─ 上下文压缩 (P2) → ConversationSummarizer → ChromaDB

对话用户隔离 (2026-03-24)
  ├─ DB: conversations.user_id + conversation_groups.user_id（UUID FK → users.id, nullable）
  ├─ service: list_conversations(user_id=) 过滤 / list_all_conversations_by_user(exclude_user_id=)
  ├─ API conversations.py: _get_user_id() + _is_superadmin() + _check_conversation_ownership()
  │    ├─ 所有 CRUD 端点 + Depends(get_current_user)
  │    ├─ send_message / get_messages / regenerate / clear — 补全鉴权（Bug-1~4 修复）
  │    └─ GET /all-users-view（superadmin only，注册在 /{id} 路由之前）
  ├─ API groups.py: 同步隔离（唯一性/sort_order 限定用户范围）
  ├─ ENABLE_AUTH=false: AnonymousUser.id="default" → _get_user_id()=None → 不过滤（全可见）
  └─ 前端: ConversationSidebar.otherUsersData → Collapse 按用户分组（superadmin only, read-only）

RBAC 认证系统 (2026-03-17)
  ├─ LocalAuthProvider → bcrypt.checkpw → User(is_active=True)
  ├─ JWT: create_access_token / decode_token (python-jose HS256)
  ├─ RefreshToken(jti, revoked) → 轮换机制（旧 token 即时作废）
  ├─ get_user_permissions() → UserRole → Role → RolePermission → Permission
  ├─ deps.get_current_user() → Bearer JWT → User 查询
  ├─ deps.require_permission() → _check coroutine → is_superadmin 或 perm_key 检查
  ├─ AnonymousUser(is_superadmin=True) → ENABLE_AUTH=false 全量兼容
  ├─ users.py._safe_assigned_by() → 防止 AnonymousUser.id="default" 导致 UUID 类型错误
  ├─ skills.py → per-user 技能目录（ENABLE_AUTH=true → user/{username}/，含 "default" 用户）
  │    ├─ _get_user_skill_dir() 已修复：移除 username!="default" 守卫，所有用户均得子目录
  │    ├─ conversations.py → send_message_stream(username=) → _build_context(username=)
  │    │    → context["username"] → _build_system_prompt() 注入 CURRENT_USER: {username}
  │    └─ skill_loader.py(scan_subdirs=True) → 扫描 user/{username}/*.md
  ├─ 角色 CRUD：roles_router（POST/DELETE /roles）+ 权限分配（POST/DELETE /roles/{id}/permissions）
  ├─ 权限列表：permissions_router（GET /permissions）→ 全量 13 条权限定义
  ├─ Roles.tsx 前端页面 → 角色卡片 + 权限分配弹窗（is_system 角色锁定删除）
  └─ 前端 Bug 修复：
       ├─ isAnonymousUser(user) = id==='default' && username==='default'（非 'anonymous'）
       ├─ markAuthRequired() 失败安全：任意错误 → require login（非仅 401）
       ├─ authChecked 标志：RequireAuth 在 initAuth 完成前不跳转（Spin 等待）
       └─ vite.config.ts proxy target：localhost:8000 → 127.0.0.1:8000（Windows IPv6 修复）

MasterAgent (P0)
  └─ AgentMCPBinder (P3) → agent_config.yaml → FilteredMCPManager
       └─ FilesystemPermissionProxy (2026-03-12 + Fix-1~4 2026-03-23)
            ├─ 目录级写权限白名单（customer_data/ + skills/user/）
            ├─ Fix-2: _is_write_allowed() 跨根反向拦截（customer_data/.claude/... 路由错误）
            ├─ Fix-3: call_tool() 拒绝消息含正确路径模板（含 {用户名}/ 层提示）
            └─ Fix-4: _check_skills_user_subdir(tool_name) 工具名感知深度校验
                       create_directory: depth ≥ 1（user/alice/ 允许）
                       write_file/delete: depth ≥ 2（user/alice/skill.md 必须）

AgenticLoop._build_system_prompt() (Fix-1 2026-03-23 + 用户隔离 2026-03-24)
  └─ allowed_directories → 识别 skills_root（含 .claude）+ data_root（其他）
       → 注入路径模板：数据文件 → {data_root}/{username}/（每用户独立子目录）
       → 注入路径模板：技能文件 → {skills_root}/user/{username}/skill-name.md

settings.py 路径可移植性 (2026-03-23)
  ├─ _PROJECT_ROOT = Path(__file__).parent.parent.parent.resolve()（模块级常量）
  ├─ _resolve_fs_paths validator: 相对路径 → _PROJECT_ROOT / d → 绝对路径
  │   绝对路径保持不变（向后兼容）
  └─ .env: ALLOWED_DIRECTORIES=["customer_data",".claude/skills"]（相对路径，可移植）

SkillLoader (P1 + 三层扩展 + 语义路由) → SkillWatcher (P1，热重载，recursive=True)
  ├─ system/ _base-*.md（always_inject=true，始终注入）
  ├─ project/ *.md（管理员 API 维护）
  ├─ user/ *.md（用户 CRUD，FilesystemPermissionProxy 写权限保护）
  ├─ _MAX_INJECT_CHARS=16000 context 保护（超限→摘要模式）
  ├─ build_skill_prompt_async(message, llm_adapter) → async，hybrid/keyword/llm 三模式
  │    ├─ Phase 1 关键词匹配（SkillMD.matches()，同步）
  │    ├─ Phase 2 SkillRoutingCache.get() → ChromaDB TTL 缓存
  │    │         └─ 未命中 → SkillSemanticRouter.route() → LLM 单次调用
  │    ├─ _build_from_matched_skills() 组装 skill_prompt
  │    └─ _make_match_info() 生成快照 → 写入 _last_match_info（2026-03-26）
  │         → run_streaming() 读取 get_last_match_info()
  │         → yield AgentEvent(type="skill_matched") [在第一个 thinking 之前]
  │         → 前端 ThoughtProcess 🧠 技能路由面板（模式/命中/总字符/load_errors）
  └─ reload_skills() → _skill_set_version++ → SkillRoutingCache.invalidate_all()

SkillSemanticRouter (2026-03-17)
  └─ llm_adapter.chat_plain(router_prompt) → {"skill-name": 0.8, ...}

SkillRoutingCache (2026-03-17)
  └─ ChromaDB PersistentClient → md5(message) 精确匹配 → TTL + version 检查

skills REST API (skills.py)
  ├─ GET /preview?mode= → build_skill_prompt_async() + get_match_details() → match_details
  ├─ GET /load-errors → SkillLoader.load_errors → 加载失败文件列表（settings:read）
  ├─ PUT /user-defined/{name} → _bump_version() + _FRONTMATTER_RE 解析
  └─ POST/PUT/DELETE /project-skills → require_admin（X-Admin-Token）

ClickHouseMCPServer (P0/Phase-3)
  ├─ batch_describe_tables (Fix-1)
  ├─ get_table_overview 兼容 (Fix-2)
  ├─ DDL 词边界检测 (Fix-3)
  └─ _to_json_safe / 无 use_numpy (Fix-4)

ClickHouse 动态多区域配置 (2026-03-18)
  ├─ load_dotenv(override=False) 前置 → os.environ（含未声明 CLICKHOUSE_THAI_* 等字段）
  ├─ Settings.Config.extra="ignore" → 允许任意 CLICKHOUSE_ENV_* 字段
  ├─ get_all_clickhouse_envs()
  │    ├─ Source 1: model_fields 扫描（已声明 idn/sg/mx）
  │    └─ Source 2: os.environ 正则 `CLICKHOUSE_*_HOST` → 提取新 env 名
  ├─ get_clickhouse_config(env) → getattr → AttributeError → os.environ 大小写不敏感回退
  ├─ MCPManager.initialize_all() → 注册 clickhouse-{env} + clickhouse-{env}-ro
  │    └─ 启动 INFO 汇总日志
  └─ AgentMCPBinder._extract_envs_from_manager() → 自动感知新区域（clickhouse_envs: all）

/mcp/ API 鉴权 (2026-03-18)
  ├─ GET /mcp/... → require_permission("settings", "read")（admin+）
  ├─ POST /mcp/... → require_permission("settings", "write")（admin+）
  └─ ENABLE_AUTH=false → AnonymousUser(is_superadmin=True) → 全量兼容

对话附件上传 (2026-03-23)
  ├─ AttachmentData Pydantic 模型 → SendMessageRequest.attachments: List[AttachmentData]
  ├─ send_message_stream(attachments=None) → 剥离 base64 → extra_metadata["attachments"] 存元数据
  ├─ _build_context() → 历史消息注解 [附件: name (type, size bytes)]
  ├─ context["current_attachments"] 注入 → _perceive() 多模态块
  │    ├─ image/* → {"type":"image","source":{"type":"base64","media_type":...,"data":...}}
  │    ├─ application/pdf → {"type":"document",...}
  │    └─ text/* → base64.b64decode() → text block
  ├─ ChatInput.tsx → PaperClipOutlined 按钮 + 粘贴图片 + MIME 类型推断
  ├─ ChatMessages.tsx → extra_metadata.attachments → Tag chips 渲染
  └─ 走现有 POST /{id}/messages，无新增 RBAC 权限

文件写入下载 (2026-03-26)
  ├─ agentic_loop.run_streaming() 监听 write_file 成功 → written_files 列表追踪
  ├─ end_turn / near_limit 退出时 written_files 非空 → yield files_written SSE 事件
  ├─ conversation_service → extra_metadata["files_written"] 持久化（复用 JSONB，无 DB 迁移）
  ├─ Chat.tsx SSE chunk 处理 → setMessageFilesWritten(msgId, files)
  │    └─ 历史加载：extra_metadata.files_written → message.files_written 还原
  ├─ ChatMessages.tsx FileDownloadCards → 文件图标 + 名称 + 大小 + 下载按钮
  ├─ fileApi.downloadFile() → axios blob → URL.createObjectURL → <a download>
  ├─ GET /api/v1/files/download → _resolve_download_path() 路径安全校验
  │    ├─ 仅 Depends(get_current_user)，viewer+ 均可访问自己文件
  │    └─ 跨用户 403，穿越 403，不存在 404
  ├─ FILE_OUTPUT_DATE_SUBFOLDER=false（可选月份子目录提示）
  └─ 无新增 RBAC 权限键/菜单路由

侧边栏 Tab UI + 只读模式 + is_shared 群组框架兼容 (2026-03-25)
  ├─ ConversationSidebar.tsx → 双 Tab（我的对话 / 其他用户(N)）
  │    ├─ Tab1：新建对话/分组按钮 + 对话列表（原有功能）
  │    ├─ Tab2：Collapse 展开的其他用户对话（badge = 对话总数）
  │    └─ otherUsersData 为空时不渲染 Tabs（非 superadmin 无 UI 变化）
  ├─ Chat.tsx → isViewingOtherUserConv（useState）
  │    ├─ handleSelectConversation → 检测 conv.id ∈ otherUsersData → setIsViewingOtherUserConv
  │    ├─ handleSendMessage → guard: if (isViewingOtherUserConv) return
  │    └─ 停止按钮 / ChatInput.readOnly prop 联动
  ├─ ChatInput.tsx → readOnly?: boolean → 替换输入框为黄色 banner（"👁 仅查看模式"）
  ├─ conversations.py → _check_conversation_write_permission()
  │    ├─ 非 superadmin → _check_conversation_ownership（不变）
  │    ├─ superadmin + own_id → 允许
  │    ├─ superadmin + is_shared=True → 允许（群组聊天扩展点）
  │    └─ superadmin + 他人对话 → 403
  │    应用于：send_message / regenerate / clear（get/rename/move 仍用 ownership）
  ├─ conversations.py 模型 → is_shared = Column(Boolean, default=False)
  │    + to_dict() 暴露 + migrate_add_is_shared.py（已执行）
  ├─ useChatStore.ts → Conversation interface 加 is_shared?: boolean
  └─ Bug 修复：ConversationService.clear_messages() 缺失 → POST /{id}/clear 端点 500 修复
       无数据库迁移（纯代码层实现）
```
