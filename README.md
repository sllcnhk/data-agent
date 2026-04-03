# 数据分析 Agent 系统

基于大语言模型的智能数据分析系统，支持多模型对话、数据库连接、ETL设计、报表定制等功能。

## 项目概述

本项目是一个完整的数据分析Agent系统，继承并扩展了 `mytools` 项目的核心功能，提供：

### 核心功能
- 🤖 **多模型对话**：支持 Claude、ChatGPT、Gemini 等多种 LLM 模型
- 💬 **对话式操作**：通过自然语言完成数据库连接、查询、分析等操作
- 📊 **智能分析**：自动理解数据库 Schema、数据质量分析、统计洞察
- 🔄 **ETL 设计**：对话式设计宽表、生成 DDL/DML 脚本、调度配置
- 📈 **定制报表**：动态创建报表、图表配置、URL 访问
- 💾 **数据导出**：支持 CSV、Excel、JSON、Parquet 等多种格式
- 📝 **Task 管理**：对话持久化、历史回溯、跨会话引用

### 继承功能（来自 mytools）
- ✅ 多数据库连接管理（ClickHouse/MySQL）
- ✅ SQL 查询执行与验证
- ✅ Excel 数据导出（支持大整数、自动轮转）
- ✅ 完整的日志系统（自动清理、轮转）
- ✅ VS Code 风格的 UI 界面

## 技术栈

### 后端
- **框架**：FastAPI + Python 3.10+
- **数据库**：PostgreSQL（元数据）+ Redis（缓存）
- **LLM**：Anthropic Claude API、OpenAI API、Google Gemini API
- **MCP**：Anthropic MCP SDK
- **数据处理**：Pandas、XlsxWriter、ClickHouse Driver

### 前端
- **框架**：React 18 + TypeScript
- **UI 库**：Ant Design 5
- **图表**：Recharts
- **状态管理**：React Context + Hooks
- **路由**：React Router v6

## 项目结构

```
data-agent/
├── backend/                    # 后端代码
│   ├── agents/                 # Agent 模块
│   │   ├── main_agent.py       # 主 Agent（对话理解与路由）
│   │   ├── database_agent.py   # 数据库 Agent（连接、查询）
│   │   ├── etl_agent.py        # ETL Agent（宽表设计）
│   │   └── visualization_agent.py  # 可视化 Agent（报表）
│   ├── mcp_servers/            # MCP Servers
│   │   ├── clickhouse_mcp.py   # ClickHouse MCP Server
│   │   ├── mysql_mcp.py        # MySQL MCP Server
│   │   ├── filesystem_mcp.py   # 文件系统 MCP Server
│   │   └── lark_mcp.py         # Lark 文档 MCP Server
│   ├── mcp_client/             # MCP 客户端
│   │   └── client.py           # 统一 MCP 调用接口
│   ├── skills/                 # Agent Skills
│   │   ├── database_query.skill    # 自然语言转 SQL
│   │   ├── data_analysis.skill     # 数据分析
│   │   ├── sql_generation.skill    # SQL 生成
│   │   ├── chart_generation.skill  # 图表配置生成
│   │   └── etl_design.skill        # ETL 设计
│   ├── context/                # 上下文管理
│   │   ├── unified_conversation.py # 统一对话格式
│   │   ├── model_adapters.py       # 模型适配器
│   │   ├── compression.py          # 上下文压缩
│   │   └── context_manager.py      # 上下文管理器
│   ├── models/                 # ORM 模型
│   │   ├── conversation.py     # 对话模型
│   │   ├── task.py             # 任务模型
│   │   └── report.py           # 报表模型
│   ├── api/                    # FastAPI 接口
│   │   ├── chat.py             # 聊天 API
│   │   ├── task.py             # Task 管理 API
│   │   ├── report.py           # 报表 API
│   │   └── database.py         # 数据库连接 API
│   ├── legacy/                 # 继承 mytools 功能
│   │   ├── clickhouse_client.py
│   │   ├── excel_writer_improved.py
│   │   └── task_manager.py
│   ├── config/                 # 配置文件
│   │   ├── database.py         # 数据库配置
│   │   └── settings.py         # 应用设置
│   ├── requirements.txt        # Python 依赖
│   └── main.py                 # FastAPI 入口
├── frontend/                   # 前端代码
│   ├── src/
│   │   ├── components/         # React 组件
│   │   │   ├── ChatInterface.tsx   # 聊天界面
│   │   │   ├── TaskList.tsx        # 任务列表
│   │   │   ├── ReportViewer.tsx    # 报表查看器
│   │   │   ├── ModelSelector.tsx   # 模型选择器
│   │   │   └── ChartRenderer.tsx   # 图表渲染
│   │   ├── pages/              # 页面
│   │   │   ├── HomePage.tsx        # 首页
│   │   │   ├── ReportPage.tsx      # 报表页面
│   │   │   └── TaskPage.tsx        # 任务详情页
│   │   ├── api/                # API 客户端
│   │   │   └── client.ts
│   │   ├── legacy/             # 继承 mytools UI
│   │   ├── App.tsx
│   │   └── index.tsx
│   ├── package.json
│   └── tsconfig.json
├── tests/                      # 测试代码
│   ├── unit/                   # 单元测试
│   ├── integration/            # 集成测试
│   └── e2e/                    # 端到端测试
├── docs/                       # 文档
│   ├── API.md                  # API 文档
│   ├── MCP.md                  # MCP 配置指南
│   ├── SKILLS.md               # Skills 使用指南
│   └── DEPLOYMENT.md           # 部署指南
├── mcp_config.json             # MCP 配置文件
├── docker-compose.yml          # Docker Compose 配置
├── .env.example                # 环境变量示例
└── README.md                   # 本文件
```

## 快速开始

### 1. 环境要求

- Python 3.10+
- Node.js 18+
- PostgreSQL 14+
- Redis 7+

### 2. 安装依赖

**后端**：
```bash
cd backend
pip install -r requirements.txt
```

**前端**：
```bash
cd frontend
npm install
```

### 3. 配置环境变量

复制 `.env.example` 为 `.env` 并配置：

```env
# LLM API Keys
ANTHROPIC_API_KEY=your_claude_api_key
OPENAI_API_KEY=your_openai_api_key
GOOGLE_API_KEY=your_gemini_api_key

# Database
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
POSTGRES_DB=data_agent
POSTGRES_USER=postgres
POSTGRES_PASSWORD=your_password

# Redis
REDIS_HOST=localhost
REDIS_PORT=6379

# Application
DEBUG=true
HOST=0.0.0.0
PORT=8000
```

### 4. 初始化数据库

```bash
cd backend
python -m alembic upgrade head
```

### 5. 启动服务

**后端**：
```bash
cd backend
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

**前端**：
```bash
cd frontend
npm start
```

访问：http://localhost:3000

## 使用指南

### 基本对话流程

1. **创建新对话**：点击左侧边栏的"新建对话"按钮
2. **选择模型**：在顶部选择 LLM 模型（Claude/ChatGPT/Gemini）
3. **开始对话**：在输入框输入问题或指令

### 常见使用场景

#### 场景1：连接数据库并查询

```
用户: "连接我的ClickHouse数据库"
Agent: "请问要连接哪个数据库？IDN/SG/MX"
用户: "IDN"
Agent: "已成功连接到ClickHouse数据库 IDN"

用户: "查看orders表的结构"
Agent: "orders表包含以下字段：
      - order_id (UInt64): 订单ID
      - user_id (UInt32): 用户ID
      - amount (Float64): 订单金额
      - order_date (Date): 订单日期
      总行数: 1,234,567"

用户: "导出2024年1月的订单数据"
Agent: "正在查询...
      已导出 10,523 条记录到：exports/orders_202401.xlsx
      [点击下载]"
```

#### 场景2：设计宽表

```
用户: "我想设计一个用户分析宽表"
Agent: "好的，基于现有的表，我建议以下设计：

      表名: dim_user_analysis

      维度字段:
      - user_id: 用户ID
      - username: 用户名
      - city: 城市

      度量字段:
      - total_orders: 总订单数
      - total_amount: 总消费金额

      请问需要调整吗？"

用户: "增加用户等级字段"
Agent: "已更新设计，现在为您生成脚本...

      ✓ DDL脚本已生成
      ✓ 初始化脚本已生成
      ✓ 增量更新脚本已生成
      ✓ Cron调度脚本已生成

      [查看脚本] [下载全部]"
```

#### 场景3：创建报表

```
用户: "创建一个销售仪表板"
Agent: "请告诉我需要展示哪些指标？"

用户: "显示每日销售额趋势和城市占比"
Agent: "正在创建报表...

      ✓ 折线图 - 每日销售额趋势
      ✓ 饼图 - 城市销售占比

      报表已创建！
      访问链接: http://localhost:3000/reports/sales-dashboard-001

      [立即查看] [编辑报表]"
```

## 开发指南

### 添加新的 MCP Server

1. 在 `backend/mcp_servers/` 创建新文件
2. 实现 MCP Server 接口
3. 在 `mcp_config.json` 添加配置
4. 在 `backend/mcp_client/client.py` 注册

### 添加新的 Agent Skill

1. 在 `backend/skills/` 创建 `.skill` 文件
2. 定义 Skill 元数据和工具
3. 在 Agent 中引用 Skill

### 扩展 Agent 功能

1. 在 `backend/agents/` 创建新 Agent
2. 实现 `BaseAgent` 接口
3. 在 `main_agent.py` 注册路由规则

## 测试

```bash
# 运行所有测试
pytest

# 单元测试
pytest tests/unit/

# 集成测试
pytest tests/integration/

# E2E测试
pytest tests/e2e/
```

## Docker 部署

```bash
# 构建并启动所有服务
docker-compose up -d

# 查看日志
docker-compose logs -f

# 停止服务
docker-compose down
```

## 文档

- [API 文档](docs/API.md)
- [MCP 配置指南](docs/MCP.md)
- [Skills 使用指南](docs/SKILLS.md)
- [部署指南](docs/DEPLOYMENT.md)
- [开发计划](../mytools/data-agent-开发计划.md)

## 许可证

MIT License

## 贡献

欢迎提交 Issue 和 Pull Request！

## 联系方式

- 项目负责人：shiguangping
- 邮箱：your.email@example.com

---

**当前版本**: v0.1.0-alpha
**最后更新**: 2026-01-20
**开发状态**: 🚧 积极开发中
