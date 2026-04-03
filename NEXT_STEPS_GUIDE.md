# 下一步实施指南

## 📊 当前完成度: 45%

### ✅ Phase 1 完成 (聊天基础) - 100%
- [x] 聊天界面
- [x] 多模型支持
- [x] 对话管理
- [x] 流式响应
- [x] Markdown渲染

### ✅ MCP基础设施 - 90%
- [x] MCP框架完整
- [x] ClickHouse MCP Server
- [x] MySQL MCP Server
- [x] FileSystem MCP Server
- [x] Lark MCP Server
- [ ] 数据采样工具(需添加)
- [ ] MCP配置API

### 🔄 Phase 2 进行中 (Agent集成) - 20%
- [ ] Master Agent
- [ ] 对话中集成MCP
- [ ] 意图识别
- [ ] 工具调用路由

### ⏳ Phase 3 待开始 (ETL功能) - 0%
- [ ] ETL Agent
- [ ] 宽表设计
- [ ] 脚本生成

### ⏳ Phase 4 待开始 (Report系统) - 0%
- [ ] Report Agent
- [ ] 图表生成
- [ ] Report持久化

---

## 🎯 立即要做的事(优先级排序)

### 1. 添加数据采样工具 ⭐⭐⭐⭐⭐
**为什么重要**: 这是理解数据表的核心功能,是所有后续功能的基础

**文件位置**:
- `backend/mcp/clickhouse/server.py`
- `backend/mcp/mysql/server.py`

**要添加的方法**:
```python
async def _sample_table_data(
    self,
    table: str,
    database: Optional[str] = None,
    limit: int = 1000,
    sampling_method: str = "top"
) -> Dict[str, Any]:
    """获取表的示例数据"""
```

**3种采样方法**:
1. `top` - 直接取前N行
2. `random` - 随机采样
3. `recent` - 按时间字段取最新数据

### 2. 创建MCP管理API ⭐⭐⭐⭐⭐
**目的**: 让前端可以管理MCP连接

**创建文件**: `backend/api/mcp.py`

**端点**:
```python
GET /api/v1/mcp/servers - 列出所有MCP服务器
GET /api/v1/mcp/servers/{name} - 获取服务器详情
POST /api/v1/mcp/servers/{name}/tools/{tool} - 调用工具
POST /api/v1/mcp/test-connection - 测试连接
```

### 3. 实现Master Agent ⭐⭐⭐⭐⭐
**目的**: 协调对话流程,智能调用工具

**创建文件**: `backend/agents/orchestrator.py`

**核心逻辑**:
```python
class MasterAgent:
    async def process_message(self, message: str, context: Dict):
        # 1. 意图识别
        intent = await self.classify_intent(message)

        # 2. 选择工具
        if intent == "database_query":
            tools = self.select_mcp_tools(intent)
            results = await self.call_tools(tools)

        # 3. 整合结果
        return self.format_response(results)
```

### 4. 集成MCP到对话流程 ⭐⭐⭐⭐
**目的**: 在聊天中自动调用MCP

**修改文件**: `backend/services/conversation_service.py`

**添加**:
```python
from backend.mcp.manager import get_mcp_manager
from backend.agents.orchestrator import MasterAgent

async def send_message_with_agent(...):
    # 使用MasterAgent处理
    agent = MasterAgent(mcp_manager, llm_adapter)
    response = await agent.process(message, conversation_context)
```

### 5. 前端MCP状态显示 ⭐⭐⭐
**目的**: 用户可以看到连接状态

**创建**: `frontend/src/components/chat/MCPStatus.tsx`

**显示**:
- 当前连接的数据库
- 可用MCP服务器列表
- 连接状态指示器

---

## 📝 具体实施步骤(今天开始)

### Step 1: 增强MCP数据采样(预计1-2小时)

```python
# 在 backend/mcp/clickhouse/server.py 添加:

async def _sample_table_data(
    self,
    table: str,
    database: Optional[str] = None,
    limit: int = 1000,
    sampling_method: str = "top",
    order_by: Optional[str] = None
) -> Dict[str, Any]:
    """获取表的示例数据"""
    try:
        if not database:
            database = self.config["database"]

        limit = min(limit, 1000)  # 强制最大1000行

        # 根据采样方法构建查询
        if sampling_method == "random":
            query = f"""
                SELECT * FROM {database}.{table}
                ORDER BY rand()
                LIMIT {limit}
            """
        elif sampling_method == "recent" and order_by:
            query = f"""
                SELECT * FROM {database}.{table}
                ORDER BY {order_by} DESC
                LIMIT {limit}
            """
        else:  # top
            query = f"""
                SELECT * FROM {database}.{table}
                LIMIT {limit}
            """

        # 执行查询
        result = await self._execute_query(query, max_rows=limit)

        # 添加额外信息
        result["sampling_info"] = {
            "method": sampling_method,
            "requested_limit": limit,
            "actual_rows": result.get("row_count", 0)
        }

        return result

    except Exception as e:
        return {
            "error": f"数据采样失败: {str(e)}",
            "table": table,
            "database": database
        }
```

同样在 `backend/mcp/mysql/server.py` 添加类似方法。

### Step 2: 创建MCP API(预计1小时)

创建 `backend/api/mcp.py`:
```python
from fastapi import APIRouter, HTTPException
from backend.mcp.manager import get_mcp_manager

router = APIRouter(prefix="/mcp", tags=["MCP管理"])

@router.get("/servers")
async def list_servers():
    """列出所有MCP服务器"""
    manager = get_mcp_manager()
    servers = manager.list_servers()
    return {
        "success": True,
        "data": servers
    }

@router.post("/servers/{server_name}/tools/{tool_name}")
async def call_tool(
    server_name: str,
    tool_name: str,
    arguments: dict
):
    """调用MCP工具"""
    manager = get_mcp_manager()
    result = await manager.call_tool(server_name, tool_name, arguments)

    if result is None:
        raise HTTPException(404, "服务器或工具不存在")

    return {
        "success": result.get("success", True),
        "data": result
    }
```

注册路由到 `backend/main.py`:
```python
from api import mcp
app.include_router(mcp.router, prefix="/api/v1")
```

### Step 3: 启动时初始化MCP(预计30分钟)

修改 `backend/main.py` 的 startup 事件:
```python
@app.on_event("startup")
async def startup_event():
    logger.info("Starting Data Agent System...")

    # 初始化MCP服务器
    from backend.mcp.manager import initialize_mcp_servers
    await initialize_mcp_servers()
    logger.info("MCP servers initialized")

    # ... 其他初始化
```

### Step 4: 前端MCP状态组件(预计1小时)

创建 `frontend/src/components/chat/MCPStatus.tsx`:
```tsx
import React, { useEffect, useState } from 'react';
import { Tag, Tooltip } from 'antd';
import { DatabaseOutlined, CheckCircleOutlined } from '@ant-design/icons';

interface MCPServer {
  name: string;
  type: string;
  tool_count: number;
}

const MCPStatus: React.FC = () => {
  const [servers, setServers] = useState<MCPServer[]>([]);

  useEffect(() => {
    fetchServers();
  }, []);

  const fetchServers = async () => {
    const res = await fetch('/api/v1/mcp/servers');
    const data = await res.json();
    if (data.success) {
      setServers(data.data);
    }
  };

  return (
    <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
      {servers.map(server => (
        <Tooltip key={server.name} title={`${server.tool_count} 个工具`}>
          <Tag icon={<DatabaseOutlined />} color="success">
            {server.name}
          </Tag>
        </Tooltip>
      ))}
    </div>
  );
};

export default MCPStatus;
```

在 `Chat.tsx` 中添加此组件。

---

## 🧪 测试场景

完成上述步骤后,测试以下场景:

### 测试1: MCP服务器列表
```
GET /api/v1/mcp/servers
```
预期: 返回ClickHouse, MySQL, FileSystem, Lark服务器列表

### 测试2: 数据采样
```
POST /api/v1/mcp/servers/clickhouse-idn/tools/sample_table_data
{
  "arguments": {
    "table": "events",
    "limit": 1000,
    "sampling_method": "top"
  }
}
```
预期: 返回1000行示例数据

### 测试3: 表结构查看
```
POST /api/v1/mcp/servers/clickhouse-idn/tools/describe_table
{
  "arguments": {
    "table": "events"
  }
}
```
预期: 返回表结构信息

---

## 📚 需要的配置文档

创建 `MCP_CONFIGURATION_GUIDE.md`:
```markdown
# MCP配置指南

## 环境变量配置

### ClickHouse连接
```bash
# IDN环境
export CLICKHOUSE_IDN_HOST=localhost
export CLICKHOUSE_IDN_PORT=9000
export CLICKHOUSE_IDN_USER=default
export CLICKHOUSE_IDN_PASSWORD=
export CLICKHOUSE_IDN_DATABASE=default
```

### MySQL连接
类似配置...

## 通过配置文件

编辑 `backend/config/settings.py`...
```

---

## ⏭️ 完成后的下一个里程碑

当以上4步完成后,你将拥有:
1. ✅ 完整的MCP基础设施
2. ✅ 数据库连接和查询能力
3. ✅ 数据采样功能(1000行)
4. ✅ 前端MCP状态显示

**然后进入Phase 2**:
- 实现Master Agent
- 智能意图识别
- 自动工具调用

---

## 💡 提示

1. **优先级**: 先让MCP工具可调用,再考虑Agent智能化
2. **测试驱动**: 每完成一个功能立即测试
3. **文档同步**: 边开发边更新配置文档
4. **渐进式**: 不要试图一次完成所有,分步实施

---

**当前状态**: 📍 Phase 1.5 - MCP增强中
**下一个检查点**: 完成数据采样和MCP API

准备好了吗?让我们开始实施! 🚀
