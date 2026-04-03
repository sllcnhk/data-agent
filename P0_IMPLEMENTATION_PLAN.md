# P0阶段实施计划

## 当前状态分析

### ✅ 已完成
1. **MCP基础设施** - 完整实现
   - Base MCP框架
   - MCP Manager
   - ClickHouse MCP Server (完整)
   - MySQL MCP Server (完整)
   - FileSystem MCP Server (已存在)
   - Lark MCP Server (已存在)

2. **聊天基础功能** - 完整实现
   - 对话管理
   - 消息持久化
   - 多模型支持
   - 流式响应

### 🔧 需要补充的关键功能

#### 1. 数据采样增强(优先级最高)
**目标**: 自动获取表的前1000行示例数据

**实现**:
- 在 ClickHouse/MySQL MCP Server 添加 `sample_table_data` 工具
- 支持智能采样(随机/顶部/分层)
- 返回结构化数据供LLM理解

**文件**:
- `backend/mcp/clickhouse/server.py` - 添加方法
- `backend/mcp/mysql/server.py` - 添加方法

#### 2. Master Agent (Orchestrator)
**目标**: 协调对话流程,理解意图,调用MCP工具

**架构**:
```
UserMessage
    ↓
MasterAgent.process()
    ├─ 意图识别
    ├─ 任务分解
    ├─ 选择Sub-Agent或直接调用MCP
    ├─ 执行并聚合结果
    └─ 生成回复
```

**实现**:
- 创建 `backend/agents/orchestrator.py`
- 集成到ConversationService
- 实现工具选择逻辑

#### 3. 对话中的MCP集成
**目标**: 在聊天对话中自动调用MCP工具

**流程**:
```
用户: "连接ClickHouse IDN环境,查看有哪些数据库"
    ↓
MasterAgent识别意图 → 需要调用MCP
    ↓
调用 clickhouse-idn.list_databases()
    ↓
将结果整合到对话context
    ↓
LLM生成友好回复
```

**实现**:
- 在对话服务中集成MCPManager
- 实现工具调用中间件
- 工具结果格式化

#### 4. MCP配置管理
**目标**: 前端可视化配置MCP连接

**实现**:
- 创建 `backend/models/mcp_config.py` - 数据模型
- 创建 `backend/api/mcp_configs.py` - API
- 创建 `frontend/src/pages/MCPConfig.tsx` - UI

#### 5. Context增强(跨模型)
**目标**: 模型切换时保持任务上下文

**策略**:
```json
{
  "core_context": {
    "task_type": "database_analysis",
    "connected_database": "clickhouse-idn",
    "current_table": "events",
    "schema": {...},
    "sample_data": [...]
  },
  "artifacts": {
    "generated_sql": [...],
    "analysis_results": [...]
  }
}
```

**实现**:
- 扩展 Message 模型的 artifacts 字段
- 实现 context 提取和转换逻辑

---

## 实施顺序(最小可用产品)

### Phase 1: 数据采样和基础MCP集成(1-2天)

#### 步骤1.1: 增强数据采样工具
```python
# backend/mcp/clickhouse/server.py
async def _sample_table_data(
    self,
    table: str,
    database: Optional[str] = None,
    limit: int = 1000,
    sampling_method: str = "top"  # top, random, stratified
) -> Dict[str, Any]:
    """
    获取表的示例数据

    Args:
        table: 表名
        database: 数据库名
        limit: 最多返回行数 (默认1000)
        sampling_method: 采样方法

    Returns:
        包含列信息和示例数据的字典
    """
```

#### 步骤1.2: 创建MCP API端点
```python
# backend/api/mcp.py
@router.get("/mcp/servers", summary="列出所有MCP服务器")
@router.post("/mcp/servers/{server_name}/tools/{tool_name}", summary="调用MCP工具")
@router.get("/mcp/servers/{server_name}/info", summary="获取服务器信息")
```

#### 步骤1.3: 前端MCP状态显示
- 在聊天界面显示当前连接的数据库
- 显示可用的MCP服务器
- 工具调用结果展示

### Phase 2: Master Agent和意图识别(2-3天)

#### 步骤2.1: 创建Master Agent
```python
# backend/agents/orchestrator.py
class MasterAgent:
    """主协调Agent"""

    async def process_message(
        self,
        message: str,
        conversation_context: Dict
    ) -> AgentResponse:
        """
        处理用户消息

        1. 意图识别
        2. 确定是否需要调用工具
        3. 选择合适的MCP工具或Sub-Agent
        4. 执行并返回结果
        """
```

#### 步骤2.2: 意图识别
使用LLM进行意图分类:
- `database_connection` - 连接数据库
- `schema_exploration` - 查看表结构
- `data_query` - 数据查询
- `data_analysis` - 数据分析
- `etl_design` - ETL设计
- `report_generation` - 报表生成
- `general_chat` - 一般对话

#### 步骤2.3: 集成到对话流程
```python
# backend/services/conversation_service.py
async def send_message_with_agent(
    self,
    conversation_id: UUID,
    content: str,
    model_key: str
):
    """使用Agent处理消息"""
    # 1. 获取对话context
    # 2. 调用MasterAgent
    # 3. Master选择tools/sub-agents
    # 4. 执行并聚合结果
    # 5. 生成最终回复
```

### Phase 3: ETL和脚本生成(3-4天)

#### 步骤3.1: 创建ETLAgent
```python
# backend/agents/etl_agent.py
class ETLAgent:
    """ETL设计Agent"""

    async def design_wide_table(
        self,
        source_tables: List[TableSchema],
        business_context: str
    ) -> WideTableDesign:
        """设计宽表"""

    async def generate_etl_script(
        self,
        design: WideTableDesign,
        target_db_type: str
    ) -> ETLScript:
        """生成ETL脚本"""
```

#### 步骤3.2: 脚本类型
- 初始化脚本 (CREATE TABLE)
- 全量加载脚本
- 增量更新脚本
- 数据校验脚本

### Phase 4: Report系统(4-5天)

#### 步骤4.1: Report数据模型
```python
# backend/models/report.py (已存在,需扩展)
class Report:
    """报表配置"""
    id: UUID
    title: str
    conversation_id: UUID  # 关联对话
    data_source: Dict  # 数据源配置
    charts: List[ChartConfig]  # 图表配置
    filters: List[FilterConfig]  # 筛选器
    refresh_schedule: Optional[str]  # 刷新计划
```

#### 步骤4.2: ReportAgent
```python
# backend/agents/report_agent.py
class ReportAgent:
    """报表生成Agent"""

    async def analyze_data_for_chart(
        self,
        data: pd.DataFrame,
        user_request: str
    ) -> ChartRecommendation:
        """分析数据并推荐图表类型"""

    async def generate_chart_config(
        self,
        data: pd.DataFrame,
        chart_type: str
    ) -> ChartConfig:
        """生成图表配置"""
```

#### 步骤4.3: 前端Report页面
- 动态图表渲染
- Report保存和编辑
- Report列表和浏览
- 数据刷新

---

## 测试策略

### 单元测试
- MCP工具调用测试
- Agent逻辑测试
- 数据采样测试

### 集成测试
- 端到端对话流程
- MCP连接测试
- ETL脚本生成测试

### 手动测试场景
1. 连接ClickHouse,查看数据库列表
2. 查看表结构和示例数据
3. 要求生成宽表设计
4. 生成ETL脚本
5. 创建自定义报表
6. 切换模型继续对话

---

## 配置文档需求

### MCP配置说明
```markdown
# MCP服务器配置

## ClickHouse
1. 编辑 `backend/config/settings.py`
2. 配置连接信息:
   - CLICKHOUSE_IDN_HOST
   - CLICKHOUSE_IDN_PORT
   - CLICKHOUSE_IDN_USER
   - CLICKHOUSE_IDN_PASSWORD
   - CLICKHOUSE_IDN_DATABASE

## MySQL
类似配置...

## 文件系统
允许的目录列表...

## Lark
API凭证配置...
```

---

## 下一步行动

### 立即开始(今天)
1. ✅ 审查现有MCP实现 - 完成
2. 🔄 添加数据采样工具 - 进行中
3. 创建MCP API端点
4. 前端显示MCP状态

### 明天
1. 实现Master Agent基础框架
2. 集成到对话流程
3. 测试数据库连接和查询

### 后续
1. ETL Agent实现
2. Report Agent实现
3. 完善文档
4. 全面测试

---

## 成功标准

### MVP (最小可用产品)
用户可以通过对话:
1. ✅ 连接ClickHouse/MySQL
2. ✅ 查看数据库、表列表
3. ✅ 查看表结构
4. ✅ 获取示例数据(1000行)
5. ✅ 执行简单查询
6. 🔄 切换模型继续对话
7. 🔄 获得LLM对数据的理解和建议

### 完整产品
1. 自动生成宽表设计
2. 生成ETL脚本
3. 创建和保存Report
4. 文件上传和解析
5. Lark文档集成

---

**状态**: 📝 规划完成,准备实施
**下一步**: 添加数据采样工具
