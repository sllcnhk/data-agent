# 独立 DB MCP Server — 维护与使用手册

**文件位置**：`backend/mcp/standalone_db_server.py`  
**协议**：MCP stdio（JSON-RPC 2.0），Python 3.8 兼容，无额外 SDK 依赖

---

## 一、用途

`standalone_db_server.py` 将 data-agent 的所有数据库连接（ClickHouse、MySQL）封装为标准 MCP Server，让 **任意项目目录** 下的 Claude Code、Codex 等 AI 工具通过全局配置复用这些连接，无需在每个项目中重复配置凭据。

```
其他项目目录
  └─ Claude Code / Codex
        └─ 全局 MCP (data-agent-db)
              └─ standalone_db_server.py
                    ├─ ClickHouse 所有已配置环境
                    └─ MySQL 所有已配置环境
```

**核心特性**：
- 凭据统一维护在 data-agent 的 `.env` 文件中
- 新增连接只需加一行 `.env`，无需改代码
- AI 工具不感知凭据，只调用工具名（如 `mysql_prod__query`）

---

## 二、工具命名规则

工具名格式：`{server_prefix}__{tool_name}`

| 服务器注册名 | 工具前缀 | 示例工具 |
|---|---|---|
| `clickhouse-idn` | `clickhouse_idn` | `clickhouse_idn__query` |
| `clickhouse-sg` | `clickhouse_sg` | `clickhouse_sg__list_tables` |
| `clickhouse-idn-ro` | `clickhouse_idn_ro` | `clickhouse_idn_ro__query` |
| `mysql-prod` | `mysql_prod` | `mysql_prod__describe_table` |
| `mysql-staging` | `mysql_staging` | `mysql_staging__sample_table_data` |

每个 server 提供的工具（ClickHouse 与 MySQL 相同）：

| 工具 | 说明 |
|---|---|
| `query` | 执行 SELECT 查询，DDL/DML 自动拦截 |
| `list_databases` | 列出所有数据库 |
| `list_tables` | 列出指定库中的所有表 |
| `describe_table` | 获取表结构（列名、类型、主键等） |
| `get_table_overview` | 获取行数、大小、引擎等概览 |
| `get_table_indexes` | 获取索引信息（仅 MySQL） |
| `sample_table_data` | 数据采样（top / random / recent 三种模式） |
| `test_connection` | 测试连接是否正常 |
| `get_server_info` | 获取数据库版本与配置信息 |

---

## 三、配置数据库连接

所有连接配置通过 `data-agent/.env` 维护。

### 3.1 ClickHouse（动态多环境）

```dotenv
# 已内置环境（idn / sg / mx）
CLICKHOUSE_IDN_HOST=your-ch-host
CLICKHOUSE_IDN_PORT=9000
CLICKHOUSE_IDN_HTTP_PORT=8123
CLICKHOUSE_IDN_DATABASE=default
CLICKHOUSE_IDN_USER=default
CLICKHOUSE_IDN_PASSWORD=your_password

# 只读副本（可选，留空则继承 admin 配置）
CLICKHOUSE_IDN_READONLY_USER=readonly_user
CLICKHOUSE_IDN_READONLY_PASSWORD=readonly_pass

# 任意新环境，只需新增一组变量，无需改代码
CLICKHOUSE_THAI_HOST=thai-ch-host
CLICKHOUSE_THAI_DATABASE=analytics
CLICKHOUSE_THAI_USER=default
CLICKHOUSE_THAI_PASSWORD=pass
```

> 新 env（如 `THAI`）会被 `get_all_clickhouse_envs()` 自动发现，注册为 `clickhouse-thai`。

### 3.2 MySQL（动态多环境）

```dotenv
# 已内置环境（prod / staging）
MYSQL_PROD_HOST=your-mysql-host
MYSQL_PROD_PORT=3306
MYSQL_PROD_DATABASE=your_database
MYSQL_PROD_USER=your_user
MYSQL_PROD_PASSWORD=your_password

MYSQL_STAGING_HOST=staging-mysql-host
MYSQL_STAGING_DATABASE=staging_db
MYSQL_STAGING_USER=staging_user
MYSQL_STAGING_PASSWORD=staging_pass

# 任意新环境，只需新增一组变量
MYSQL_ANALYTICS_HOST=analytics-mysql-host
MYSQL_ANALYTICS_DATABASE=analytics_db
MYSQL_ANALYTICS_USER=analyst
MYSQL_ANALYTICS_PASSWORD=pass
```

> 新 env（如 `ANALYTICS`）会被 `get_all_mysql_envs()` 自动发现，注册为 `mysql-analytics`。

### 3.3 禁用某类连接

```dotenv
ENABLE_MCP_CLICKHOUSE=false   # 禁用所有 ClickHouse
ENABLE_MCP_MYSQL=false        # 禁用所有 MySQL
```

---

## 四、全局 MCP 配置（一次性设置）

Claude Code 的全局 MCP 配置文件：`~/.claude/.mcp.json`

当前配置（已写入）：

```json
{
  "mcpServers": {
    "data-agent-db": {
      "command": "d:/ProgramData/Anaconda3/envs/dataagent/python.exe",
      "args": ["-m", "backend.mcp.standalone_db_server"],
      "cwd": "c:/Users/shiguangping/data-agent",
      "env": {}
    }
  }
}
```

**参数说明**：

| 字段 | 说明 |
|---|---|
| `command` | Python 解释器（dataagent conda env） |
| `args` | 以模块方式启动，`cwd` 已是 data-agent 根目录 |
| `cwd` | data-agent 项目根目录，用于 `.env` 自动加载 |
| `env` | 额外环境变量（通常留空，凭据从 `.env` 读取） |

> 修改 `.mcp.json` 后，重启 Claude Code 生效。

### 4.1 为 Codex 配置（可选）

编辑 `~/.codex/config.toml`：

```toml
[mcp.data-agent-db]
command = "d:/ProgramData/Anaconda3/envs/dataagent/python.exe"
args    = ["-m", "backend.mcp.standalone_db_server"]
cwd     = "c:/Users/shiguangping/data-agent"
```

---

## 五、手动启动与调试

### 5.1 直接启动（测试协议输出）

```bash
cd c:/Users/shiguangping/data-agent
d:/ProgramData/Anaconda3/envs/dataagent/python.exe -m backend.mcp.standalone_db_server
```

启动后向 stdin 输入 JSON 可测试协议：

```bash
# 发送 initialize 请求
echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}}}' | \
  d:/ProgramData/Anaconda3/envs/dataagent/python.exe -m backend.mcp.standalone_db_server
```

### 5.2 使用自定义 .env 文件

```bash
d:/ProgramData/Anaconda3/envs/dataagent/python.exe -m backend.mcp.standalone_db_server \
  --env-file /path/to/other/.env
```

### 5.3 查看已注册的连接

日志输出到 stderr（不影响 MCP stdout 协议），运行时可见：

```
WARNING standalone_db_server: [standalone] registered clickhouse-idn
WARNING standalone_db_server: [standalone] registered clickhouse-sg
WARNING standalone_db_server: [standalone] registered mysql-prod
```

若某连接没有出现，说明对应 `_HOST` 为空或连接初始化失败（详见 stderr 错误信息）。

---

## 六、新增数据库连接（操作步骤）

### ClickHouse 新环境

1. 在 `data-agent/.env` 追加：

   ```dotenv
   CLICKHOUSE_NEWENV_HOST=host
   CLICKHOUSE_NEWENV_PORT=9000
   CLICKHOUSE_NEWENV_HTTP_PORT=8123
   CLICKHOUSE_NEWENV_DATABASE=db
   CLICKHOUSE_NEWENV_USER=user
   CLICKHOUSE_NEWENV_PASSWORD=pass
   ```

2. 重启 Claude Code（或重启 MCP server 进程）。

3. 新工具 `clickhouse_newenv__query` 等自动可用，**无需改代码**。

### MySQL 新环境

1. 在 `data-agent/.env` 追加：

   ```dotenv
   MYSQL_NEWENV_HOST=host
   MYSQL_NEWENV_PORT=3306
   MYSQL_NEWENV_DATABASE=db
   MYSQL_NEWENV_USER=user
   MYSQL_NEWENV_PASSWORD=pass
   ```

2. 重启 Claude Code。

3. 新工具 `mysql_newenv__query` 等自动可用。

---

## 七、常见问题

### Q: 工具没有出现在 Claude Code 中

**检查顺序**：
1. `~/.claude/.mcp.json` 语法是否正确（用 `python -m json.tool` 验证）
2. `cwd` 路径是否存在
3. `.env` 中对应 `_HOST` 是否非空
4. Python 解释器路径是否正确（`d:/ProgramData/...` 是 bash 路径，Windows 路径应用 `D:/...`）
5. 手动启动 standalone_db_server，看 stderr 是否有报错

### Q: ClickHouse 连接失败，但 HTTP 可用

standalone_db_server 继承了 ClickHouse MCP server 的 TCP→HTTP 自动回退：
- 先尝试 TCP（端口 9000），超时 5 秒
- TCP 失败自动切换 HTTP（端口 8123）

`test_connection` 工具会在结果中显示当前使用的协议。

### Q: MySQL 连接超时断开后工具报错

`_ensure_connection()` 在每次调用前自动 `ping(reconnect=True)`，短暂断开会自动重连。若持续失败，检查 MySQL server 的 `wait_timeout` 配置。

### Q: 在其他项目中如何知道有哪些工具可用

在 Claude Code 中提问：
> "data-agent-db MCP server 提供了哪些工具？"

或直接通过 MCP 协议查询（tools/list）。

---

## 八、技术说明

### MCP 协议实现

standalone_db_server 实现了 MCP stdio 协议的最小子集（JSON-RPC 2.0，换行分隔）：

| 方法 | 说明 |
|---|---|
| `initialize` | 握手，返回 server 能力声明 |
| `notifications/initialized` | 客户端就绪通知（无需回复） |
| `tools/list` | 返回所有已注册工具的 schema |
| `tools/call` | 执行指定工具，返回结果文本 |
| `ping` | 保活检查 |

### 与 data-agent 内部 MCP 的关系

| 维度 | 内部 MCP（MCPServerManager） | standalone_db_server |
|---|---|---|
| 用途 | data-agent agent 内部调用 | 外部 AI 工具通过 MCP 协议调用 |
| 协议 | 直接 Python 函数调用 | MCP stdio（JSON-RPC 2.0） |
| 包含 server | ClickHouse + MySQL + Filesystem + Lark + Report | ClickHouse + MySQL 仅 |
| 启动方式 | `await initialize_mcp_servers()` | subprocess（Claude Code 管理） |
| 底层实现 | 共享同一套 `BaseMCPServer` 子类 | 复用同一套 `BaseMCPServer` 子类 |

两套机制共享底层的 `ClickHouseMCPServer` 和 `MySQLMCPServer`，凭据配置源相同。

---

*最后更新：2026-05-25*
