# 聊天功能安装和配置指南

本指南将帮助你完成聊天功能的安装、配置和启动。

## 📋 功能概述

本次更新将原有的"任务管理"页面改造为类似 ChatGPT 的聊天界面,主要功能包括:

- ✅ 聊天主界面(类ChatGPT布局)
- ✅ 左侧历史对话列表
- ✅ 右侧聊天区域 + 消息展示
- ✅ 支持多模型切换(Claude、Gemini、千问、豆包)
- ✅ 记住最后选择的模型
- ✅ 模型配置管理页面
- ✅ 流式响应支持
- ✅ Markdown 渲染

## 🚀 快速开始

### 第一步:安装依赖

#### 1. 前端依赖

```bash
cd frontend
npm install react-markdown@^9.0.0
```

或者运行批处理文件:
```bash
install-chat-frontend.bat
```

#### 2. 后端依赖(已包含在 requirements.txt)

后端所需的依赖已经在 requirements.txt 中,无需额外安装。

### 第二步:初始化数据库

运行数据库迁移脚本创建新表:

```bash
cd backend
python scripts/init_chat_db.py
```

这个脚本会:
- 创建对话表(conversations)
- 创建消息表(messages)
- 创建上下文快照表(context_snapshots)
- 创建模型配置表(llm_configs)
- 插入默认的模型配置

### 第三步:配置模型 API 密钥

有两种方式配置:

#### 方式1: 修改默认配置文件

编辑 `backend/models/llm_config.py`,在 `DEFAULT_LLM_CONFIGS` 中修改:

```python
{
    "model_key": "claude",
    "api_key": "你的Claude API密钥",  # 修改这里
    "api_base_url": "http://10.0.3.248:3000/api",  # 修改为你的代理地址
    ...
}
```

然后重新运行初始化脚本:
```bash
python backend/scripts/init_chat_db.py
```

#### 方式2: 通过前端配置(推荐)

1. 启动后端和前端服务
2. 访问 `http://localhost:3000/model-config`
3. 点击"初始化默认配置"按钮
4. 编辑每个模型的配置,填入 API 密钥
5. 测试连接确保配置正确

### 第四步:启动服务

#### 1. 启动后端

```bash
cd backend
python main.py
```

或使用现有的启动脚本:
```bash
start-backend.bat
```

后端将运行在: `http://localhost:8000`

#### 2. 启动前端

```bash
cd frontend
npm run dev
```

或使用现有的启动脚本:
```bash
start-frontend.bat
```

前端将运行在: `http://localhost:3000`

### 第五步:开始使用

1. 打开浏览器访问 `http://localhost:3000`
2. 首页即为聊天界面
3. 点击左上角"新建对话"开始聊天
4. 右上角可以切换不同的模型

## 📁 文件结构说明

### 后端新增文件

```
backend/
├── models/
│   └── llm_config.py              # 模型配置数据模型
├── core/model_adapters/
│   ├── qianwen.py                 # 千问适配器
│   ├── doubao.py                  # 豆包适配器
│   └── factory.py                 # 已更新,支持新模型
├── api/
│   ├── conversations.py           # 对话管理API
│   └── llm_configs.py            # 模型配置API
└── scripts/
    └── init_chat_db.py           # 数据库初始化脚本
```

### 前端新增文件

```
frontend/src/
├── pages/
│   ├── Chat.tsx                   # 聊天主页面
│   └── ModelConfig.tsx           # 模型配置页面
├── components/chat/
│   ├── ConversationList.tsx      # 对话列表组件
│   ├── ChatMessages.tsx          # 消息展示组件
│   ├── ChatInput.tsx             # 输入组件
│   └── ModelSelector.tsx         # 模型选择器
├── store/
│   └── useChatStore.ts           # 聊天状态管理
└── services/
    └── chatApi.ts                # 聊天API服务
```

## 🔧 配置说明

### 模型配置参数

每个模型配置包含以下参数:

| 参数 | 说明 | 示例 |
|------|------|------|
| model_key | 模型唯一标识 | claude, gemini, qianwen, doubao |
| model_name | 模型显示名称 | Claude Code |
| model_type | 适配器类型 | claude, gemini, qianwen, doubao |
| api_base_url | API基础URL | https://api.anthropic.com |
| api_key | API密钥 | sk-xxx |
| api_secret | API密钥2(可选) | 部分模型需要 |
| default_model | 具体模型名称 | claude-3-5-sonnet-20240620 |
| temperature | 温度参数 | 0.7 |
| max_tokens | 最大token数 | 4096 |
| is_enabled | 是否启用 | true/false |
| is_default | 是否为默认模型 | true/false |

### 默认配置的模型

系统默认提供以下模型配置:

1. **Claude Code** (默认)
   - 预配置的代理地址
   - 需要填入你的 API 密钥

2. **Google Gemini**
   - 需要 Google API Key
   - API地址: https://generativelanguage.googleapis.com

3. **通义千问**
   - 需要阿里云 DashScope API Key
   - API地址: https://dashscope.aliyuncs.com/api/v1

4. **豆包**
   - 需要字节跳动火山引擎 API Key
   - API地址: https://ark.cn-beijing.volces.com/api/v3

## 🐛 常见问题

### 1. 前端启动后显示空白页

**解决方法:**
- 检查浏览器控制台是否有错误
- 确保 react-markdown 已正确安装
- 清除浏览器缓存并刷新

### 2. 后端API报错 "module not found"

**解决方法:**
```bash
cd backend
# 确保所有依赖都已安装
pip install -r requirements.txt
```

### 3. 数据库表不存在

**解决方法:**
```bash
python backend/scripts/init_chat_db.py
```

### 4. 模型调用失败

**可能原因和解决方法:**
- 检查 API 密钥是否正确
- 检查网络连接
- 在模型配置页面点击"测试"按钮检查连接
- 查看后端日志获取详细错误信息

### 5. 流式响应不工作

**解决方法:**
- 确保前端使用的是 fetch API(不是axios)
- 检查后端是否正确返回 SSE 格式
- 检查网络代理设置

## 📝 API 文档

启动后端后,可以访问:

- Swagger UI: `http://localhost:8000/api/docs`
- ReDoc: `http://localhost:8000/api/redoc`

主要API端点:

### 对话管理
- `POST /api/v1/conversations` - 创建对话
- `GET /api/v1/conversations` - 获取对话列表
- `GET /api/v1/conversations/{id}` - 获取对话详情
- `PUT /api/v1/conversations/{id}` - 更新对话
- `DELETE /api/v1/conversations/{id}` - 删除对话
- `POST /api/v1/conversations/{id}/messages` - 发送消息(支持流式)
- `GET /api/v1/conversations/{id}/messages` - 获取消息列表

### 模型配置
- `GET /api/v1/llm-configs` - 获取配置列表
- `POST /api/v1/llm-configs` - 创建配置
- `GET /api/v1/llm-configs/{model_key}` - 获取配置详情
- `PUT /api/v1/llm-configs/{model_key}` - 更新配置
- `DELETE /api/v1/llm-configs/{model_key}` - 删除配置
- `POST /api/v1/llm-configs/init-defaults` - 初始化默认配置
- `POST /api/v1/llm-configs/{model_key}/test` - 测试配置

## 🎨 UI 特性

- 响应式布局,适配各种屏幕尺寸
- 消息支持 Markdown 渲染
- 代码块语法高亮
- 流式输出,实时显示
- 自动滚动到最新消息
- 对话历史记录
- 模型切换记忆

## 🔐 安全建议

1. **不要将 API 密钥提交到版本控制**
   - 将敏感配置添加到 .gitignore
   - 使用环境变量存储密钥

2. **生产环境配置**
   - 修改 CORS 设置,限制允许的域名
   - 启用 HTTPS
   - 使用数据库加密存储 API 密钥

3. **访问控制**
   - 添加用户认证
   - 实施请求限流
   - 监控异常调用

## 📚 开发指南

### 添加新模型支持

1. 在 `backend/core/model_adapters/` 创建新的适配器
2. 继承 `BaseModelAdapter` 基类
3. 实现必要的方法:chat, stream_chat, convert_to_native_format 等
4. 在 `factory.py` 中注册新适配器
5. 在 `llm_config.py` 添加默认配置

### 自定义聊天UI

主要文件:
- `frontend/src/pages/Chat.tsx` - 主页面布局
- `frontend/src/components/chat/ChatMessages.tsx` - 消息样式
- `frontend/src/components/chat/ChatInput.tsx` - 输入框
- `frontend/src/components/chat/ConversationList.tsx` - 对话列表

## 📞 技术支持

如果遇到问题:
1. 查看本文档的"常见问题"部分
2. 检查后端日志: `logs/` 目录
3. 查看浏览器控制台错误
4. 提交 Issue 到项目仓库

## 🎉 完成

现在你已经完成了聊天功能的安装和配置!

开始享受与 AI 的对话吧! 🚀
