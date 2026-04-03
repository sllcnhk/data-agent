# Excel → ClickHouse 数据导入功能设计方案

> **版本**：v1.0 · 2026-04-03
> **状态**：方案草稿，待确认后执行

---

## 一、需求目标

| 需求 | 说明 |
|------|------|
| 独立功能页面 | 与日志、角色、技能等菜单并列，路径 `/data-import` |
| 连接选择 | 下拉列出系统配置中**有写入权限的非只读** ClickHouse 连接 |
| Schema/Table 选择 | 根据选中连接动态查询数据库列表 → 选库 → 查表 |
| 多 Sheet 支持 | Excel 多 Sheet 依次解析，每个 Sheet 独立配置目标表和表头选项 |
| 表头识别 | 每个 Sheet 可单独勾选"第一行是列名"（跳过列名行不导入数据） |
| 分批导入 | 后台自动分批：默认 1000 行/批；大 Sheet 分批解析+插入，避免内存峰值 |
| 进度反馈 | 导入过程实时展示：当前批次/总批次、已导入行数、错误摘要 |
| 权限控制 | 新增 `data:import` 权限；admin、superadmin 默认拥有 |

---

## 二、技术选型依据

### 2.1 Excel 解析：openpyxl（已安装 3.1.2）

**理由**：
- `openpyxl` 支持 `read_only=True` 流式迭代模式（`iter_rows()`），大文件不全量加载内存
- 可处理合并单元格、格式化等边缘情况
- 已在 `requirements.txt` 中，无需新增依赖

对比参考（行业成熟方案）：
- **pandas.read_excel**：基于 openpyxl/xlrd，适合中小文件；一次性加载到 DataFrame，大文件内存压力大
- **openpyxl iter_rows**：适合大文件分批流式读取，与本需求分批概念契合
- **本方案**：分批阶段用 openpyxl iter_rows 流式读取，每批转 list 后直接 INSERT，不依赖 pandas（减少内存占用）

### 2.2 ClickHouse 写入：直连（复用现有 HTTP Client）

**理由**：
- 项目已有 `backend/mcp/clickhouse/http_client.py`（ClickHouseHTTPClient），支持 HTTP 协议执行任意 SQL
- MCP ClickHouse 的 DDL 过滤只阻断 `CREATE/ALTER/DROP/TRUNCATE`，**INSERT 语句不受限**，但绕过 MCP 层直连更高效（省去工具调用协议开销）
- 批量 INSERT 用参数化格式：`INSERT INTO db.table VALUES (...), (...), ...`

### 2.3 进度推送：轮询（简单可靠）

**理由**：
- 项目已有 SSE（对话流式），但 SSE 连接生命周期与导入任务不一致（页面刷新即断）
- **采用前端轮询（每 1.5s GET /jobs/{job_id}）**：简单可靠，无连接管理复杂度
- 任务状态存 PostgreSQL（复用现有 Task 模型 `backend/models/task.py`）

### 2.4 文件临时存储

- 上传文件暂存于 `customer_data/{username}/imports/` 目录
- 导入完成（成功或失败）后自动清理临时文件
- 遵循现有 FilesystemPermissionProxy 写入白名单（customer_data/ 已在白名单）

---

## 三、整体架构

```
前端 DataImport.tsx
  ├── Step 1：连接选择（GET /api/v1/data-import/connections）
  ├── Step 2：上传 Excel（POST /api/v1/data-import/upload）
  │              └── 返回 sheet 列表 + 每 sheet 的列预览（前5行）
  ├── Step 3：每 Sheet 配置（目标库.表 + 是否有表头）
  └── Step 4：确认导入（POST /api/v1/data-import/execute）
                 └── 返回 job_id → 前端轮询 GET /api/v1/data-import/jobs/{job_id}

后端 DataImportService
  ├── list_writable_connections()    从 MCPServerManager 过滤非 -ro 连接
  ├── list_databases(env)            直接查 CH: SELECT name FROM system.databases
  ├── list_tables(env, database)     直接查 CH: SELECT name FROM system.tables WHERE database=?
  ├── parse_excel_sheets(file_path)  openpyxl 读取 sheet 名 + 前5行预览
  └── run_import_job(job_id, ...)    后台协程：分批读取 → 批量 INSERT → 更新进度
```

---

## 四、数据流与批次设计

```
Excel 文件（单 Sheet 示例，10000 行）
  │
  ▼ openpyxl iter_rows（流式，每次读 BATCH_SIZE=1000 行）
  │
  ├── Batch 1: rows 2-1001  → INSERT INTO db.table VALUES (...)×1000  → 更新 job.progress
  ├── Batch 2: rows 1002-2001 → INSERT ...×1000
  ├── ...
  └── Batch 10: rows 9001-10000 → INSERT ...×1000
                                   → job.status = "completed"
```

**批次参数**：

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `batch_size` | 1000 | 每批行数，前端可配置（100~5000） |
| 并发 | 单 Sheet 串行 | 同一文件多 Sheet 顺序执行（避免连接竞争） |
| 错误策略 | `continue`（默认）| 单批失败记录错误，继续下一批；`abort` 模式遇错即停 |

---

## 五、API 设计

```
GET  /api/v1/data-import/connections
     → [{env, name, host, database}]  # 仅非只读连接

GET  /api/v1/data-import/connections/{env}/databases
     → ["crm", "default", ...]

GET  /api/v1/data-import/connections/{env}/databases/{db}/tables
     → ["realtime_dwd_crm_call_record", ...]

POST /api/v1/data-import/upload
     Body: multipart/form-data  file=xxx.xlsx
     → { upload_id, sheets: [{name, row_count_estimate, preview_rows: [[...]], columns: [...]} ] }

POST /api/v1/data-import/execute
     Body: {
       upload_id,
       connection_env,
       on_error: "continue"|"abort",
       batch_size: 1000,
       sheets: [
         {
           sheet_name,
           database,
           table,
           has_header: true,   # 第一行是否列名
           enabled: true       # 是否导入此 Sheet
         }
       ]
     }
     → { job_id }

GET  /api/v1/data-import/jobs/{job_id}
     → {
         job_id, status,           # pending|running|completed|failed
         total_sheets, done_sheets,
         current_sheet,
         total_rows, imported_rows,
         total_batches, done_batches,
         errors: [{sheet, batch, message}],
         created_at, finished_at
       }
```

---

## 六、RBAC 权限设计

新增权限 `data:import`：

| 角色 | 是否拥有 data:import |
|------|-------------------|
| superadmin | ✅ |
| admin | ✅ |
| analyst | ❌（只读角色，不给写入权限）|
| viewer | ❌ |

前端菜单项加 `perm: "data:import"`，无权限用户不显示入口。

---

## 七、TODO LIST

### T1 — RBAC 新增 `data:import` 权限

**改动目标**：`backend/scripts/init_rbac.py` 中 PERMISSIONS 列表新增 `("data", "import", "Excel数据导入ClickHouse")`；ROLES 中 admin 和 superadmin 加入此权限；同步更新 `backend/api/deps.py` 中的权限枚举（如有）。

**需求关系**：权限控制入口，防止低权限用户（viewer/analyst）访问导入功能，是安全前置。

---

### T2 — 后端模型：ImportJob

**改动目标**：新建 `backend/models/import_job.py`（PostgreSQL 表 `import_jobs`），字段：id(UUID), user_id(FK), upload_id(str), status, total_rows, imported_rows, total_batches, done_batches, current_sheet, errors(JSON), created_at, finished_at。注册到 `backend/models/__init__.py`。

**需求关系**：持久化任务进度，支持前端轮询查询，任务重启后状态不丢失。

---

### T3 — 后端服务：DataImportService

**改动目标**：新建 `backend/services/data_import_service.py`，实现：
- `list_writable_connections()`：从 MCPServerManager 过滤 name 不含 `-ro` 的 CH 连接
- `list_databases(env)`：执行 `SELECT name FROM system.databases ORDER BY name`
- `list_tables(env, database)`：执行 `SELECT name FROM system.tables WHERE database=? ORDER BY name`
- `parse_excel_preview(file_path)`：openpyxl 读取所有 sheet 名、前5行数据、估算行数
- `run_import_job(job_id, config)`：异步协程，分批 iter_rows → INSERT，更新 ImportJob 进度

**需求关系**：所有业务逻辑的核心，分批插入逻辑在此实现。

---

### T4 — 后端 API：data_import.py

**改动目标**：新建 `backend/api/data_import.py`，实现 §五 中所有 5 个端点；所有端点加 `Depends(get_current_user)` + `require_permission("data:import")` 保护；upload 端点将文件保存到 `customer_data/{username}/imports/`；execute 端点通过 `asyncio.create_task()` 启动后台导入协程。

**需求关系**：对外暴露 API，衔接前端与服务层，同时保证权限和用户隔离。

---

### T5 — 注册路由到 main.py

**改动目标**：`backend/main.py` 中 `import data_import_router` 并注册到 `app`，prefix=`/api/v1/data-import`。

**需求关系**：路由注册后 API 才可被前端访问。

---

### T6 — 前端 API Client：dataImportApi.ts

**改动目标**：新建 `frontend/src/services/dataImportApi.ts`，封装所有 5 个 API 调用，使用现有 `apiClient` 实例；定义 TypeScript 类型 `Connection`, `SheetPreview`, `ImportJobStatus`。

**需求关系**：前端组件与后端 API 的类型安全桥接层。

---

### T7 — 前端页面：DataImport.tsx

**改动目标**：新建 `frontend/src/pages/DataImport.tsx`，四步流程 UI：
- **Step1 连接选择**：Select 下拉，连接信息（env name、host、database）
- **Step2 文件上传**：Ant Design Upload Dragger，限制 `.xlsx/.xls`，显示 Sheet 预览卡片
- **Step3 Sheet 配置**：每个 Sheet 一行——启用开关、目标库选择（Select）、目标表选择（Select，级联）、有表头勾选框、批次大小 InputNumber
- **Step4 执行中**：Progress 进度条（已导入/总行数）、当前 Sheet 标签、错误列表折叠面板；完成后显示汇总报告

**需求关系**：完整的用户交互界面，覆盖所有需求场景。

---

### T8 — 前端注册路由 + 菜单项

**改动目标**：
- `frontend/src/App.tsx`：新增 `<Route path="/data-import" element={<DataImport />} />`
- `frontend/src/components/AppLayout.tsx`：ALL_MENU_ITEMS 新增 `{ key: '/data-import', icon: <ImportOutlined />, label: '数据导入', perm: 'data:import' }`

**需求关系**：让功能页面出现在导航菜单中，并受权限控制可见性。

---

### T9 — 单元测试：test_data_import.py

**改动目标**：新建 `test_data_import.py`，分 A-F 节：
- **A 节**：连接过滤逻辑（`list_writable_connections` 正确排除 `-ro` 连接）
- **B 节**：Excel 解析（单/多 Sheet、有/无表头、空 Sheet、合并单元格边缘情况）
- **C 节**：批次分割逻辑（行数整除/不整除 batch_size 时的批次数计算）
- **D 节**：API 端点权限验证（无权限 403、有权限 200）
- **E 节**：ImportJob 进度更新（状态转换 pending→running→completed/failed）
- **F 节**：用户隔离（用户A不能查询用户B的 job_id）

目标：≥40 个测试用例，全部通过。

---

### T10 — 回归测试

**改动目标**：运行以下已有测试套件，确保新功能无回归：
- `test_rbac.py` + `test_rbac_e2e.py`：RBAC 新权限不破坏现有权限矩阵
- `test_conversation_isolation.py`：用户隔离机制无受影响
- `test_file_download.py` + `test_file_download_e2e.py`：customer_data 文件操作无回归
- `test_p3.py`：FilesystemPermissionProxy 写入白名单无破坏

预期：现有测试 100% 通过。

---

## 八、文件改动一览

| 文件 | 新增/修改 | 关联 TODO |
|------|----------|-----------|
| `backend/models/import_job.py` | 新增 | T2 |
| `backend/models/__init__.py` | 修改（注册模型）| T2 |
| `backend/services/data_import_service.py` | 新增 | T3 |
| `backend/api/data_import.py` | 新增 | T4 |
| `backend/main.py` | 修改（注册路由）| T5 |
| `backend/scripts/init_rbac.py` | 修改（新增权限）| T1 |
| `frontend/src/services/dataImportApi.ts` | 新增 | T6 |
| `frontend/src/pages/DataImport.tsx` | 新增 | T7 |
| `frontend/src/App.tsx` | 修改（新增路由）| T8 |
| `frontend/src/components/AppLayout.tsx` | 修改（新增菜单项）| T8 |
| `test_data_import.py` | 新增 | T9 |

**共计：4 个文件修改，7 个文件新增，影响范围小，与现有功能解耦。**

---

## 九、风险与注意事项

| 风险 | 对策 |
|------|------|
| 大 Excel（10万行+）上传超时 | 限制文件大小（默认 50MB）；上传后立即返回 upload_id，解析在服务端进行 |
| CH INSERT 大批量超时 | batch_size 可配，单批失败不中断全局 |
| 列名不匹配（Excel列数≠表字段数）| 预览阶段展示 Excel 列数 vs 目标表列数，不一致时高亮警告，允许用户确认后继续 |
| 多用户并发导入 | 每个任务独立的 asyncio Task，job 与 user_id 绑定，互不干扰 |
| ClickHouse 只读副本误选 | API 层过滤：只返回名称不含 `-ro` 的连接 |
| 前端页面刷新丢失进度 | job_id 存入 localStorage，刷新后自动恢复轮询 |

---

## 十、待确认事项

| 项 | 问题 | 选项 |
|----|------|------|
| 1 | 文件大小上限？| 默认 50MB / 可配 |
| 2 | 导入失败策略默认值？| 继续下一批（推荐）/ 立即终止 |
| 3 | 历史任务记录是否需要在页面上展示？| 展示最近 N 条（推荐）/ 仅当前会话 |
| 4 | analyst 是否需要此权限？| 目前设计仅 admin/superadmin；如需 analyst 可追加 |

---

*文档由 Claude Code 生成，确认后执行。*
