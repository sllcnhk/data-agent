# 快速开始指南

欢迎使用数据智能分析Agent系统! 本指南将帮助你在5分钟内启动并体验系统。

---

## 🚀 5分钟快速启动

### 步骤1: 初始化数据库 (1分钟)

```bash
cd backend
python scripts/init_chat_db.py
```

**预期输出**:
```
============================================================
初始化聊天功能数据库
============================================================

1. 创建表结构...
✓ 表结构创建完成

2. 初始化默认LLM配置...
✓ 成功创建 4 个默认配置
```

### 步骤2: 配置API密钥 (1分钟)

编辑 `backend/models/llm_config.py`,找到 `DEFAULT_LLM_CONFIGS`,填入你的API密钥:

```python
{
    "model_key": "claude",
    "api_key": "你的Claude API密钥",  # ← 修改这里
    "api_base_url": "http://10.0.3.248:3000/api",  # ← 或修改为你的代理
    ...
}
```

### 步骤3: 启动后端 (1分钟)

```bash
cd backend
python main.py
```

**预期输出**:
```
INFO: Starting Data Agent System...
INFO: Initializing MCP servers...
INFO: MCP servers initialized successfully
INFO: Agent Manager initialized
INFO: System startup complete
INFO: Uvicorn running on http://0.0.0.0:8000
```

### 步骤4: 启动前端 (1分钟)

打开新终端:

```bash
cd frontend
npm run dev
```

**预期输出**:
```
VITE v4.x.x  ready in xxx ms

➜  Local:   http://localhost:3000/
➜  Network: use --host to expose
```

### 步骤5: 开始使用 (1分钟)

1. 打开浏览器访问: http://localhost:3000
2. 点击左上角 "新建对话"
3. 选择右上角的模型 (如果Claude配置正确,选择Claude Code)
4. 输入消息开始聊天!

---

## 💬 体验功能

### 测试1: 连接数据库

**输入**:
```
连接ClickHouse数据库
```

**预期回复**:
```
我已经连接了以下数据库:

- clickhouse-idn (clickhouse) - 8个工具可用
- clickhouse-sg (clickhouse) - 8个工具可用
- mysql-prod (mysql) - 9个工具可用

你可以:
1. 查看数据库列表: "有哪些数据库"
2. 查看表列表: "数据库X有哪些表"
3. 查看表结构: "表Y的结构是什么"
4. 查看示例数据: "给我看看表Z的数据"
```

### 测试2: 查看数据库列表

**输入**:
```
有哪些数据库?
```

**预期回复**:
```
ClickHouse (IDN环境) 有以下数据库:

- default
- system
- _temporary_and_external_tables

你可以继续问:
- "数据库X有哪些表?"
- "查看表Y的结构"
```

### 测试3: 一般对话

**输入**:
```
你好,请介绍一下你的功能
```

**预期**: LLM会介绍系统能力,包括数据库连接、分析等功能

### 测试4: 切换模型

1. 点击右上角的模型选择器
2. 选择不同的模型 (如Gemini, 千问, 豆包)
3. 继续对话,系统会使用新模型回复

### 测试5: 查看MCP状态

查看页面顶部的MCP服务器状态标签:
- 🏢 clickhouse-idn
- 🏢 clickhouse-sg
- 🏢 clickhouse-mx
- 🐬 mysql-prod
- 🐬 mysql-staging
- 📁 filesystem
- 📝 lark

鼠标悬浮可以看到详细信息。

---

## 🔧 高级配置

### 配置多个ClickHouse环境

编辑 `backend/config/settings.py`:

```python
# ClickHouse配置
CLICKHOUSE_IDN_HOST = "your-host-1"
CLICKHOUSE_IDN_PORT = 9000

CLICKHOUSE_SG_HOST = "your-host-2"
CLICKHOUSE_SG_PORT = 9000
```

### 配置MySQL

```python
# MySQL配置
MYSQL_PROD_HOST = "your-mysql-host"
MYSQL_PROD_PORT = 3306
MYSQL_PROD_USER = "your-user"
MYSQL_PROD_PASSWORD = "your-password"
```

### 通过前端配置模型

1. 访问: http://localhost:3000/model-config
2. 点击 "初始化默认配置" (如果还没有配置)
3. 编辑每个模型的配置
4. 点击 "测试" 验证连接
5. 点击 "保存"

---

## 📝 API测试

### 测试MCP API

```bash
# 列出所有MCP服务器
curl http://localhost:8000/api/v1/mcp/servers | jq

# 获取服务器详情
curl http://localhost:8000/api/v1/mcp/servers/clickhouse-idn | jq

# 调用工具
curl -X POST http://localhost:8000/api/v1/mcp/servers/clickhouse-idn/tools/list_databases \
  -H "Content-Type: application/json" \
  -d '{"arguments": {}}' | jq
```

### 测试对话API

```bash
# 创建对话
curl -X POST http://localhost:8000/api/v1/conversations \
  -H "Content-Type: application/json" \
  -d '{
    "title": "测试对话",
    "model_key": "claude"
  }' | jq

# 记录返回的 conversation_id, 然后发送消息
curl -X POST http://localhost:8000/api/v1/conversations/{conversation_id}/messages \
  -H "Content-Type: application/json" \
  -d '{
    "content": "连接ClickHouse数据库",
    "stream": false
  }' | jq
```

---

## 🐛 常见问题

### 1. 后端启动失败

**问题**: `ModuleNotFoundError: No module named 'xxx'`

**解决**:
```bash
cd backend
pip install -r requirements.txt
```

### 2. 前端启动失败

**问题**: 依赖缺失

**解决**:
```bash
cd frontend
npm install
```

### 3. 数据库连接失败

**问题**: MCP服务器初始化失败

**解决**:
- 检查 `backend/config/settings.py` 中的数据库配置
- 确保数据库可访问
- 查看后端日志获取详细错误

### 4. LLM调用失败

**问题**: API密钥无效或网络问题

**解决**:
- 检查API密钥是否正确
- 检查API base URL是否可访问
- 使用 `/model-config` 页面测试连接

### 5. 流式响应不显示

**问题**: 浏览器不支持SSE或网络代理问题

**解决**:
- 尝试使用其他浏览器 (Chrome, Firefox)
- 检查网络代理设置
- 查看浏览器控制台错误

---

## 📚 更多文档

- [聊天功能设置](CHAT_SETUP_GUIDE.md) - 详细设置指南
- [Phase 1完成总结](PHASE1_COMPLETION_SUMMARY.md) - MCP功能说明
- [Phase 2完成总结](PHASE2_COMPLETION_SUMMARY.md) - Agent功能说明
- [整体进度报告](OVERALL_PROGRESS_REPORT.md) - 项目进度概览

---

## 🎯 下一步

体验完基础功能后,可以尝试:

1. **探索数据库**: 查看表结构,获取示例数据
2. **多轮对话**: 进行复杂的多轮对话
3. **模型对比**: 尝试不同模型的回复效果
4. **API集成**: 使用API进行自定义集成

---

## 💡 提示

### 对话技巧
- 使用自然语言,不需要记住命令
- 可以多轮对话,系统会记住上下文
- 遇到问题时,系统会引导你提供更多信息

### 模型选择
- **Claude**: 理解能力强,适合复杂任务
- **Gemini**: 速度快,多语言支持好
- **千问**: 中文支持好
- **豆包**: 性价比高

### MCP工具
- 所有数据库操作都通过MCP工具完成
- 支持ClickHouse和MySQL
- 可以查看工具列表: http://localhost:8000/api/v1/mcp/stats

---

## 🎉 开始使用吧!

现在你已经了解了如何快速启动和使用系统。

有任何问题,请查看详细文档或提交Issue。

享受与AI的智能对话体验! 🚀
