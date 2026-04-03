# 数据智能分析Agent系统 - 项目总览

## 项目概述

本项目是一个完整的数据智能分析Agent系统，采用现代化的多Agent架构，支持：
- 🤖 **智能Agent系统**：5种专业Agent类型，智能任务路由
- 💬 **对话式操作**：通过自然语言完成各种数据分析任务
- 📊 **数据分析**：支持统计分析、趋势分析、异常检测
- 🔄 **ETL管道**：智能数据提取、转换、加载流程
- 📈 **数据可视化**：自动生成各类图表
- 💾 **多数据源支持**：ClickHouse、MySQL、文件系统
- 📝 **任务管理**：完整的任务生命周期管理

## 系统架构

### 核心模块

1. **MCP (Model Context Protocol) 服务器**
   - ClickHouse MCP - 数据库查询
   - MySQL MCP - MySQL数据库支持
   - Filesystem MCP - 文件系统操作
   - Lark MCP - 文档和表格支持

2. **Skills 系统**
   - 14个专业技能模块
   - 数据库查询、分析、图表生成、SQL优化、ETL设计
   - 可扩展的技能注册机制

3. **Agent 系统**
   - 5种专业Agent类型
   - 智能任务路由和调度
   - 异步任务处理

4. **API 层**
   - RESTful API接口
   - FastAPI框架
   - 完整的API文档

5. **前端界面**
   - React 18 + TypeScript
   - Ant Design 5 UI库
   - 实时数据监控

## 项目结构

```
data-agent/
├── backend/                  # 后端代码
│   ├── agents/               # Agent系统
│   │   ├── base.py          # Agent基类和接口
│   │   ├── impl.py          # 具体Agent实现
│   │   ├── manager.py       # Agent管理器
│   │   ├── router.py        # 智能路由器
│   │   └── __init__.py
│   ├── api/                 # API路由
│   │   ├── agents.py        # Agent相关API
│   │   ├── skills.py        # Skills相关API
│   │   └── __init__.py
│   ├── config/              # 配置管理
│   │   ├── settings.py      # 应用配置
│   │   └── database.py      # 数据库配置
│   ├── core/                # 核心模块
│   │   ├── __init__.py
│   │   └── exceptions.py    # 异常定义
│   ├── mcp/                 # MCP服务器
│   │   ├── base.py          # MCP基类
│   │   ├── manager.py       # MCP管理器
│   │   ├── clickhouse/      # ClickHouse MCP
│   │   ├── mysql/           # MySQL MCP
│   │   ├── filesystem/      # Filesystem MCP
│   │   └── lark/           # Lark MCP
│   ├── models/              # 数据模型
│   │   ├── __init__.py
│   │   ├── agent.py         # Agent模型
│   │   └── task.py          # 任务模型
│   ├── skills/              # Skills系统
│   │   ├── base.py          # Skills基类
│   │   ├── database_query.py # 数据库查询技能
│   │   ├── data_analysis.py  # 数据分析技能
│   │   ├── sql_generation.py # SQL生成技能
│   │   ├── chart_generation.py # 图表生成技能
│   │   ├── etl_design.py    # ETL设计技能
│   │   └── __init__.py
│   ├── tests/               # 测试文件
│   │   ├── test_agents.py   # Agent测试
│   │   ├── test_api.py      # API测试
│   │   ├── test_mcp_servers.py # MCP测试
│   │   └── test_skills.py   # Skills测试
│   └── main.py              # FastAPI应用入口
├── frontend/                # 前端代码
│   ├── src/
│   │   ├── components/      # 公共组件
│   │   │   ├── AppLayout.tsx
│   │   │   └── ChartComponent.tsx
│   │   ├── pages/          # 页面组件
│   │   │   ├── Dashboard.tsx
│   │   │   ├── Agents.tsx
│   │   │   ├── Tasks.tsx
│   │   │   └── Skills.tsx
│   │   ├── services/       # API服务
│   │   │   └── api.ts
│   │   ├── store/          # 状态管理
│   │   │   └── useAgentStore.ts
│   │   ├── hooks/          # 自定义Hooks
│   │   │   └── useApi.ts
│   │   ├── types/          # 类型定义
│   │   │   └── api.ts
│   │   ├── App.tsx
│   │   ├── main.tsx
│   │   └── index.css
│   ├── package.json
│   ├── vite.config.ts
│   ├── tsconfig.json
│   └── README.md
└── README.md               # 项目说明
```

## Agent类型

### 1. DataAnalystAgent (数据分析师)
- **能力**: 数据摘要分析、描述性统计、相关性分析、趋势分析、异常值检测
- **适用场景**: 各种数据分析任务

### 2. SQLExpertAgent (SQL专家)
- **能力**: 自然语言转SQL、SQL查询生成、SQL性能优化、查询执行
- **适用场景**: 数据库查询和SQL相关任务

### 3. ChartBuilderAgent (图表构建师)
- **能力**: 柱状图、折线图、饼图、散点图、面积图生成
- **适用场景**: 数据可视化和图表生成

### 4. ETLEngineerAgent (ETL工程师)
- **能力**: ETL管道设计、数据提取、转换、加载、数据验证、数据清洗
- **适用场景**: 数据管道和数据质量任务

### 5. GeneralistAgent (通用Agent)
- **能力**: 智能任务路由、多技能协调、复合任务处理
- **适用场景**: 复杂多步骤任务

## Skills列表

### 数据库技能
- `database_query` - 执行SQL查询
- `database_list_tables` - 列出数据库表
- `database_describe_table` - 获取表结构
- `database_connection_test` - 测试数据库连接

### 数据分析技能
- `data_analysis` - 基础数据分析
- `trend_analysis` - 时间序列趋势分析
- `outlier_detection` - 异常值检测

### SQL技能
- `sql_generation` - SQL查询生成
- `sql_optimization` - SQL性能优化

### 图表技能
- `chart_generation` - 图表配置生成
- `chart_type_recommendation` - 图表类型推荐

### ETL技能
- `etl_design` - ETL管道设计
- `data_validation` - 数据质量验证
- `data_cleaning` - 数据清洗

## 快速开始

### 启动后端

```bash
cd backend
pip install -r requirements.txt
python main.py
```

或者：

```bash
python run.py
```

### 启动前端

```bash
cd frontend
npm install
npm run dev
```

### 访问系统

- **前端界面**: http://localhost:3000
- **API文档**: http://localhost:8000/api/docs
- **健康检查**: http://localhost:8000/health

## API使用示例

### 1. 提交数据分析任务

```bash
curl -X POST "http://localhost:8000/api/v1/agents/tasks" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "分析销售数据的趋势",
    "priority": "normal"
  }'
```

### 2. 执行SQL生成

```bash
curl -X POST "http://localhost:8000/api/v1/skills/sql_generation/execute" \
  -H "Content-Type: application/json" \
  -d '{
    "description": "查询所有用户",
    "table_schema": {
      "table_name": "users",
      "columns": [
        {"name": "id", "type": "int"},
        {"name": "name", "type": "string"}
      ]
    }
  }'
```

### 3. 生成图表

```bash
curl -X POST "http://localhost:8000/api/v1/skills/chart_generation/execute" \
  -H "Content-Type: application/json" \
  -d '{
    "data": [
      {"category": "A", "value": 10},
      {"category": "B", "value": 20}
    ],
    "chart_type": "bar",
    "library": "echarts"
  }'
```

### 4. 设计ETL管道

```bash
curl -X POST "http://localhost:8000/api/v1/skills/etl_design/execute" \
  -H "Content-Type: application/json" \
  -d '{
    "source_type": "database",
    "source_config": {
      "database_type": "clickhouse",
      "query": "SELECT * FROM events"
    },
    "target_config": {
      "type": "database",
      "table": "events_processed"
    }
  }'
```

## 智能任务路由

系统支持基于关键词的智能任务路由：

- **SQL关键词** (sql, 查询, select, insert, update, delete, 数据库) → SQLExpertAgent
- **图表关键词** (图表, chart, plot, 可视化, 柱状图, 折线图, 饼图, 散点图) → ChartBuilderAgent
- **分析关键词** (分析, analysis, 统计, 趋势, correlation, outlier) → DataAnalystAgent
- **ETL关键词** (etl, pipeline, 管道, 提取, 转换, 加载, 清洗, 验证) → ETLEngineerAgent

## 开发计划

### 已完成阶段 ✅

- [x] **阶段1**: 项目架构规划与环境搭建
- [x] **阶段2**: 后端核心模块开发
- [x] **阶段3**: MCP服务器开发
- [x] **阶段4**: Agent Skills开发
  - [x] 4.1 database_query skill
  - [x] 4.2 data_analysis skill
  - [x] 4.3 sql_generation skill
  - [x] 4.4 chart_generation skill
  - [x] 4.5 etl_design skill
  - [x] 4.6 Skills单元测试
- [x] **阶段5**: Agent系统开发
- [x] **阶段6**: FastAPI路由和API开发
- [x] **阶段7**: 前端开发
  - [x] 7.1 创建React前端项目
  - [x] 7.2 配置前端开发环境
  - [x] 7.3 创建API客户端
  - [x] 7.4 开发Agent管理页面
  - [x] 7.5 开发任务提交页面
  - [x] 7.6 开发数据可视化组件
  - [x] 7.7 开发系统仪表盘
  - [x] 7.8 创建前端路由
  - [x] 7.9 编写前端文档

### 下一阶段 📋

- [ ] **阶段8**: 系统测试和优化
  - [ ] 8.1 集成测试
  - [ ] 8.2 性能优化
  - [ ] 8.3 安全审计
  - [ ] 8.4 文档完善

- [ ] **阶段9**: 部署和上线
  - [ ] 9.1 Docker容器化
  - [ ] 9.2 CI/CD配置
  - [ ] 9.3 部署文档
  - [ ] 9.4 用户手册

## 技术亮点

### 1. 智能化
- 基于关键词的智能任务路由
- 自动选择合适的Agent
- 智能推荐图表类型

### 2. 高性能
- 异步任务处理
- 支持并发执行
- 工作协程池

### 3. 可扩展
- 模块化设计
- 插件化架构
- 易于添加新技能和Agent

### 4. 易维护
- 完善的单元测试
- 清晰的代码结构
- 详细的API文档

### 5. 用户友好
- 现代化的Web界面
- 实时状态监控
- 直观的操作流程

## 许可证

MIT License

## 贡献

欢迎提交Pull Request和Issue！

## 联系方式

如有问题，请联系开发团队。
