# 使用指南

## 🚀 快速启动

### 一键启动(推荐)

双击运行或命令行执行:
```bash
start-all.bat
```

脚本会自动完成以下检查:
1. ✅ Python环境 (需要Python 3.7+)
2. ✅ Node.js环境 (需要Node.js 18+)
3. ✅ PostgreSQL服务状态
4. ✅ 数据库连接和初始化
5. ✅ 前端依赖安装

然后自动启动:
- 🔧 后端服务 (http://localhost:8000)
- 🎨 前端服务 (http://localhost:3000)

### 访问系统

启动成功后,打开浏览器访问:
- **前端界面**: http://localhost:3000
- **API文档**: http://localhost:8000/docs
- **后端健康检查**: http://localhost:8000/health

---

## 📝 使用流程

### 1. 首次配置

首次使用需要配置LLM API密钥:

**方式A: 通过前端配置(推荐)**
1. 访问: http://localhost:3000/model-config
2. 选择要配置的模型
3. 填入API密钥和Base URL
4. 点击"测试"验证连接
5. 点击"保存"

**方式B: 通过代码配置**
编辑 `backend/models/llm_config.py`:
```python
DEFAULT_LLM_CONFIGS = [
    {
        "model_key": "claude",
        "api_key": "你的Claude API密钥",
        "api_base_url": "https://api.anthropic.com",
        ...
    }
]
```

### 2. 创建新对话

1. 访问首页: http://localhost:3000
2. 点击左上角 **"新建对话"** 按钮
3. 在右上角选择要使用的LLM模型
4. 开始对话!

### 3. 连接数据库

在对话框输入:
```
连接ClickHouse数据库
```

或者:
```
连接MySQL数据库
```

系统会列出所有可用的数据库连接和工具。

### 4. 数据库操作

**查看数据库列表**:
```
有哪些数据库?
```

**查看表列表**:
```
数据库X有哪些表?
```

**查看表结构**:
```
表Y的结构是什么?
```

**查看示例数据**:
```
给我看看表Z的前10条数据
```

### 5. 数据分析

**统计分析**:
```
分析订单表的数据质量
```

**查询数据**:
```
查询2024年1月的订单总金额
```

**导出数据**:
```
导出用户表的数据到Excel
```

---

## 🛠️ 常用命令

### 数据库相关

| 命令示例 | 说明 |
|---------|------|
| `连接ClickHouse` | 连接ClickHouse数据库 |
| `连接MySQL` | 连接MySQL数据库 |
| `有哪些数据库` | 列出所有数据库 |
| `表X有哪些字段` | 查看表结构 |
| `表X的数据量` | 统计表行数 |
| `给我看看表X的数据` | 查看示例数据 |

### 数据分析

| 命令示例 | 说明 |
|---------|------|
| `分析表X的数据质量` | 数据质量分析 |
| `统计表X的字段Y` | 字段统计 |
| `查询...条件` | 自然语言查询 |
| `对比表X和表Y` | 表对比分析 |

### 数据导出

| 命令示例 | 说明 |
|---------|------|
| `导出表X到Excel` | 导出为Excel |
| `导出查询结果` | 导出查询数据 |
| `导出表X到CSV` | 导出为CSV |

---

## ⚙️ 系统管理

### 查看服务状态

**后端健康检查**:
```bash
curl http://localhost:8000/health
```

**查看MCP服务器状态**:
```bash
curl http://localhost:8000/api/v1/mcp/stats
```

### 停止服务

在启动的两个终端窗口中:
1. 按 `Ctrl + C` 停止服务
2. 或直接关闭终端窗口

### 重启服务

```bash
# 再次运行启动脚本
start-all.bat
```

### 查看日志

**后端日志**:
- 位置: `backend/logs/`
- 查看: 打开 `backend` 终端窗口

**前端日志**:
- 位置: 前端终端输出
- 查看: 打开 `frontend` 终端窗口

---

## 🎯 使用技巧

### 1. 多轮对话

系统会记住上下文,可以进行多轮对话:
```
用户: 查询订单表
Agent: [返回订单数据]

用户: 按城市分组统计
Agent: [返回分组统计结果]

用户: 导出为Excel
Agent: [生成Excel文件]
```

### 2. 切换模型

在对话过程中可以随时切换模型:
1. 点击右上角的模型选择器
2. 选择其他模型
3. 继续对话

不同模型特点:
- **Claude**: 理解能力强,适合复杂分析
- **ChatGPT**: 响应快速,多用途
- **Gemini**: 多语言支持好
- **千问/豆包**: 中文支持优秀

### 3. 查看MCP工具

查看所有可用的数据库工具:
```
显示所有可用的工具
```

### 4. 自然语言查询

不需要写SQL,直接用自然语言:
```
查询最近7天注册的用户数量
```

```
统计每个城市的订单总额,按金额降序排列
```

---

## ❓ 常见问题

### Q1: 启动失败 - Python not found
**A**: 安装Python 3.7+并添加到PATH环境变量

### Q2: 启动失败 - Node.js not found
**A**: 安装Node.js 18+并添加到PATH环境变量

### Q3: 启动失败 - PostgreSQL not running
**A**:
```bash
# 启动PostgreSQL服务
net start postgresql-x64-18

# 或使用服务管理器
services.msc
```

### Q4: 启动失败 - Database not initialized
**A**:
```bash
cd backend
python scripts\init_chat_db.py
```

### Q5: 前端依赖安装失败
**A**:
```bash
cd frontend
npm install
```

### Q6: 模型无响应
**A**:
1. 检查API密钥是否正确
2. 检查网络连接
3. 查看后端终端的错误信息

### Q7: 数据库连接失败
**A**:
1. 检查PostgreSQL服务是否运行
2. 检查`.env`文件中的密码是否正确
3. 参考: [POSTGRES_FIX_GUIDE.md](POSTGRES_FIX_GUIDE.md)

---

## 📚 更多文档

| 文档 | 说明 |
|------|------|
| [START_HERE.md](START_HERE.md) | 快速开始 |
| [SETUP_SUMMARY.md](SETUP_SUMMARY.md) | 设置总结 |
| [POSTGRES_FIX_GUIDE.md](POSTGRES_FIX_GUIDE.md) | 数据库问题修复 |
| [QUICK_START_GUIDE.md](QUICK_START_GUIDE.md) | 详细启动指南 |
| [README.md](README.md) | 项目说明 |

---

## 💡 提示

1. **首次使用**: 建议先配置Claude或ChatGPT模型,体验效果最好
2. **数据库连接**: 确保数据库可访问,检查防火墙设置
3. **性能优化**: 大数据量查询建议添加限制条件
4. **安全提醒**: 不要将API密钥提交到版本控制系统

---

**祝您使用愉快! 🎉**

如有问题,请查看[常见问题](#常见问题)或查阅其他文档。
