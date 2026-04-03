# MCP服务器使用指南

## 概述

MCP (Model Context Protocol) 是Anthropic提出的协议，用于模型与外部工具和数据的交互。本项目实现了多个MCP服务器，为数据分析Agent系统提供数据访问能力。

## 架构

```
Agent
  ↓
MCP客户端
  ↓
MCP管理器 (MCPServerManager)
  ↓
┌─────────────────┬─────────────────┬─────────────────┬─────────────────┐
│ ClickHouse MCP  │   MySQL MCP    │  Filesystem MCP │   Lark MCP      │
│   Server        │   Server       │   Server       │   Server        │
│                 │                 │                 │                 │
│ - 查询数据库    │ - 查询数据库    │ - 浏览目录      │ - 获取文档      │
│ - 获取表结构    │ - 获取表结构    │ - 读写文件      │ - 获取表格      │
│ - 数据导出      │ - 数据导出      │ - 搜索文件      │ - 搜索文档      │
└─────────────────┴─────────────────┴─────────────────┴─────────────────┘
```

## 快速开始

### 1. 初始化MCP服务器

```python
from backend.mcp import initialize_mcp_servers

# 初始化所有配置的服务器
manager = await initialize_mcp_servers()

# 列出所有服务器
servers = manager.list_servers()
print(servers)
```

### 2. 使用具体服务器

```python
from backend.mcp import get_clickhouse_server, get_mysql_server, get_filesystem_server

# 获取ClickHouse服务器
ch_server = await get_clickhouse_server("idn")
if ch_server:
    # 执行查询
    result = await ch_server.call_tool("query", {
        "query": "SELECT * FROM users LIMIT 10",
        "max_rows": 10
    })
    print(result)

# 获取MySQL服务器
mysql_server = await get_mysql_server("prod")
if mysql_server:
    # 执行查询
    result = await mysql_server.call_tool("query", {
        "query": "SELECT COUNT(*) FROM orders",
        "max_rows": 100
    })
    print(result)

# 获取文件系统服务器
fs_server = await get_filesystem_server()
if fs_server:
    # 列出目录
    result = await fs_server.call_tool("list_directory", {
        "path": "data"
    })
    print(result)
```

### 3. 直接使用MCP服务器

```python
from backend.mcp.filesystem import FilesystemMCPServer

# 创建服务器
server = FilesystemMCPServer()
await server.initialize()

# 使用MCP客户端
from backend.mcp.base import MCPClient

client = MCPClient(server)

# 列出工具
tools = await client.list_tools()
print(tools)

# 调用工具
result = await client.call_tool("list_allowed_directories", {})
print(result)

# 获取资源
resources = await client.list_resources()
print(resources)
```

## 服务器详细说明

### 1. ClickHouse MCP Server

**功能**:
- 执行SQL查询
- 列出数据库和表
- 获取表结构
- 获取表概览
- 测试连接

**示例**:

```python
from backend.mcp.clickhouse import ClickHouseMCPServer

server = ClickHouseMCPServer(env="idn")
await server.initialize()

# 列出数据库
result = await server.call_tool("list_databases", {})
print(result)

# 查询数据
result = await server.call_tool("query", {
    "query": "SELECT COUNT(*) FROM user_events WHERE date >= '2024-01-01'",
    "max_rows": 100
})
print(result)

# 获取表结构
result = await server.call_tool("describe_table", {
    "table": "user_events",
    "database": "default"
})
print(result)
```

### 2. MySQL MCP Server

**功能**:
- 执行SQL查询（仅SELECT）
- 列出数据库和表
- 获取表结构
- 获取表概览
- 获取索引信息
- 测试连接

**示例**:

```python
from backend.mcp.mysql import MySQLMCPServer

server = MySQLMCPServer(env="prod")
await server.initialize()

# 列出表
result = await server.call_tool("list_tables", {
    "database": "test_db"
})
print(result)

# 查询数据（只允许SELECT）
result = await server.call_tool("query", {
    "query": "SELECT * FROM orders LIMIT 10",
    "max_rows": 10
})
print(result)

# 获取表索引
result = await server.call_tool("get_table_indexes", {
    "table": "orders",
    "database": "test_db"
})
print(result)
```

### 3. Filesystem MCP Server

**功能**:
- 列出目录内容
- 读取和写入文件
- 创建目录
- 删除文件或目录
- 搜索文件
- 获取文件信息

**安全**:
- 仅允许访问配置的目录
- 文件大小限制10MB
- 写入模式支持write/append

**示例**:

```python
from backend.mcp.filesystem import FilesystemMCPServer

server = FilesystemMCPServer()
await server.initialize()

# 列出允许的目录
result = await server.call_tool("list_allowed_directories", {})
print(result)

# 列出目录内容
result = await server.call_tool("list_directory", {
    "path": "data"
})
print(result)

# 写入文件
result = await server.call_tool("write_file", {
    "path": "data/test.txt",
    "content": "Hello, World!",
    "mode": "write"
})
print(result)

# 读取文件
result = await server.call_tool("read_file", {
    "path": "data/test.txt"
})
print(result)

# 搜索文件
result = await server.call_tool("search_files", {
    "path": "data",
    "pattern": "*.txt"
})
print(result)
```

### 4. Lark MCP Server

**功能**:
- 获取访问令牌
- 获取文档列表
- 获取文档内容
- 获取表格列表
- 获取表格内容
- 搜索文档

**注意**:
- 需要配置Lark APP ID和APP SECRET
- 部分功能需要相应权限

**示例**:

```python
from backend.mcp.lark import LarkMCPServer

server = LarkMCPServer()
await server.initialize()

# 获取访问令牌
result = await server.call_tool("get_access_token", {})
print(result)

# 列出文档
result = await server.call_tool("list_documents", {
    "count": 20
})
print(result)

# 搜索文档
result = await server.call_tool("search_documents", {
    "keyword": "数据分析"
})
print(result)
```

## 高级用法

### 1. 自定义MCP服务器

```python
from backend.mcp.base import BaseMCPServer, MCPToolType

class CustomMCPServer(BaseMCPServer):
    def __init__(self):
        super().__init__("Custom Server", "1.0.0")

    async def initialize(self):
        # 注册工具
        self.register_tool(
            name="custom_tool",
            description="自定义工具",
            input_schema={
                "type": "object",
                "properties": {
                    "param": {"type": "string"}
                },
                "required": ["param"]
            },
            callback=self.custom_tool
        )

    async def custom_tool(self, param: str) -> str:
        return f"处理参数: {param}"

# 使用
server = CustomMCPServer()
await server.initialize()
result = await server.call_tool("custom_tool", {"param": "test"})
```

### 2. 批量操作

```python
from backend.mcp import get_mcp_manager

manager = await initialize_mcp_servers()

# 批量执行查询
for env in ["idn", "sg", "mx"]:
    server = manager.get_server(f"clickhouse-{env}")
    if server:
        result = await server.call_tool("query", {
            "query": "SELECT COUNT(*) FROM events",
            "max_rows": 1
        })
        print(f"ClickHouse {env}: {result}")
```

### 3. 资源���问

```python
# 通过URI访问资源
result = await server.get_resource_content("filesystem://directories")
print(result)

# 获取资源列表
resources = server.get_resources_list()
for resource in resources:
    print(f"  - {resource['uri']}: {resource['name']}")
```

## 配置

### 环境变量

在`.env`文件中配置：

```bash
# ClickHouse
CLICKHOUSE_IDN_HOST=your-host
CLICKHOUSE_IDN_USER=default
CLICKHOUSE_IDN_PASSWORD=your-password

CLICKHOUSE_SG_HOST=your-host
CLICKHOUSE_SG_USER=default
CLICKHOUSE_SG_PASSWORD=your-password

CLICKHOUSE_MX_HOST=your-host
CLICKHOUSE_MX_USER=default
CLICKHOUSE_MX_PASSWORD=your-password

# MySQL
MYSQL_PROD_HOST=your-host
MYSQL_PROD_USER=your-user
MYSQL_PROD_PASSWORD=your-password

MYSQL_STAGING_HOST=your-host
MYSQL_STAGING_USER=your-user
MYSQL_STAGING_PASSWORD=your-password

# Lark
LARK_APP_ID=your-app-id
LARK_APP_SECRET=your-app-secret

# Filesystem
ALLOWED_DIRECTORIES=/path/to/allowed,/path/to/another

# 功能开关
ENABLE_MCP_CLICKHOUSE=true
ENABLE_MCP_MYSQL=true
ENABLE_MCP_FILESYSTEM=true
ENABLE_MCP_LARK=false
```

## 最佳实践

### 1. 错误处理

```python
try:
    result = await server.call_tool("query", {
        "query": "SELECT * FROM table"
    })

    if "error" in result:
        print(f"查询失败: {result['error']}")
    else:
        print(f"查询成功: {result}")
except Exception as e:
    print(f"异常: {str(e)}")
```

### 2. 批量查询优化

```python
# 避免频繁的小查询
# 合并查询以减少网络往返

# 不好的做法
for row in table_list:
    result = await server.call_tool("describe_table", {"table": row})

# 好的做法
result = await server.call_tool("list_tables", {})
table_list = result["tables"]
```

### 3. 资源管理

```python
from backend.mcp import get_mcp_manager

manager = await initialize_mcp_servers()

try:
    # 使用服务器
    server = manager.get_server("filesystem")
    result = await server.call_tool("read_file", {"path": "data.txt"})
finally:
    # 清理资源
    await manager.shutdown()
```

### 4. 安全

```python
# Filesystem MCP Server会自动检查路径
# 确保路径在允许的目录中

# 数据库查询限制
# MySQL MCP只允许SELECT查询
# 不允许DDL和DML操作

# 文件大小限制
# Filesystem MCP限制单文件10MB
```

## 故障排查

### 1. 连接失败

```python
# 测试连接
result = await server.call_tool("test_connection", {})
print(result)

# 检查配置
print(f"Host: {server.config['host']}")
print(f"Port: {server.config['port']}")
print(f"Database: {server.config['database']}")
```

### 2. 查询超时

```python
# 调整超时时间
result = await server.call_tool("query", {
    "query": "SELECT * FROM large_table",
    "max_rows": 100  # 限制返回行数
})
```

### 3. 权限错误

```python
# Filesystem MCP
# 检查路径是否在允许目录中
result = await server.call_tool("list_allowed_directories", {})
print(result)

# MySQL/ClickHouse
# 检查用户权限
```

## 性能优化

### 1. 连接池

MCP服务器内部使用连接池，无需手动管理连接。

### 2. 查询优化

```python
# 使用LIMIT限制结果集
result = await server.call_tool("query", {
    "query": "SELECT * FROM table LIMIT 1000",
    "max_rows": 1000
})

# 避免SELECT *
result = await server.call_tool("query", {
    "query": "SELECT id, name FROM table WHERE active = 1"
})
```

### 3. 缓存

```python
# 对频繁查询的结果进行缓存
# Filesystem MCP Server支持文件缓存
```

## API参考

### 工具列表

#### ClickHouse
- `query`: 执行查询
- `list_databases`: 列出数据库
- `list_tables`: 列出表
- `describe_table`: 获取表结构
- `get_table_overview`: 获取表概览
- `test_connection`: 测试连接
- `get_server_info`: 获取服务器信息

#### MySQL
- `query`: 执行查询（仅SELECT）
- `list_databases`: 列出数据库
- `list_tables`: 列出表
- `describe_table`: 获取表结构
- `get_table_overview`: 获取表概览
- `get_table_indexes`: 获取索引
- `test_connection`: 测试连接
- `get_server_info`: 获取服务器信息

#### Filesystem
- `list_directory`: 列出目录
- `read_file`: 读取文件
- `write_file`: 写入文件
- `create_directory`: 创建目录
- `delete`: 删除文件/目录
- `search_files`: 搜索文件
- `get_file_info`: 获取文件信息
- `list_allowed_directories`: 列出允许目录

#### Lark
- `get_access_token`: 获取访问令牌
- `list_documents`: 列出文档
- `get_document_content`: 获取文档内容
- `list_sheets`: 列出表格
- `get_sheet_content`: 获取表格内容
- `search_documents`: 搜索文档
- `get_user_info`: 获取用户信息

## 参考资源

- [MCP官方文档](https://modelcontextprotocol.io/)
- [ClickHouse文档](https://clickhouse.com/docs/)
- [MySQL文档](https://dev.mysql.com/doc/)
- [飞书开放平台](https://open.feishu.cn/)
