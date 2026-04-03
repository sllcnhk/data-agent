# 调试指南 - 创建对话失败问题

## 问题描述
前端点击"新建对话"时报错："创建对话失败: Network Error"

## 已完成的改进

### 1. 添加详细日志记录

**文件**: [backend/main.py](backend/main.py)

- ✅ 添加日志轮转功能（最大10MB，保留5个备份）
- ✅ 日志同时输出到控制台和文件
- ✅ 日志文件位置：`logs/backend.log`

### 2. API 端点增强

**文件**: [backend/api/conversations.py](backend/api/conversations.py)

- ✅ 添加详细的错误捕获和日志记录
- ✅ 记录每次创建对话的请求参数
- ✅ 记录成功/失败的详细信息
- ✅ 返回更清晰的错误消息

### 3. 数据服务修复

**文件**: [backend/services/conversation_service.py](backend/services/conversation_service.py)

- ✅ `list_conversations` 现在返回 `(conversations, total)` 元组
- ✅ `create_conversation` 支持 `model_key` 参数

## 测试步骤

### 步骤 1：重启后端服务

**重要**：必须重启后端才能让新的日志配置生效。

在后端窗口按 `Ctrl+C` 停止服务，然后重新启动：

```cmd
cd C:\Users\shiguangping\data-agent\backend
set PYTHONPATH=C:\Users\shiguangping\data-agent
python main.py
```

### 步骤 2：运行详细测试

打开新的 Anaconda Prompt：

```cmd
conda activate dataagent
cd C:\Users\shiguangping\data-agent\backend
python test_create_conversation.py
```

### 测试脚本功能

`test_create_conversation.py` 会执行以下检查：

1. ✓ 检查后端服务是否启动
2. ✓ 获取可用的 LLM 配置
3. ✓ 详细测试创建对话 API
   - 显示完整的请求信息
   - 显示完整的响应信息
   - 捕获所有可能的错误
4. ✓ 验证对话是否真的创建
5. ✓ 显示最后 20 行日志

## 日志文件位置

```
C:\Users\shiguangping\data-agent\logs\backend.log
```

可以实时查看日志：

```cmd
# PowerShell
Get-Content C:\Users\shiguangping\data-agent\logs\backend.log -Wait -Tail 50

# CMD（需要额外工具，或者用编辑器打开）
notepad C:\Users\shiguangping\data-agent\logs\backend.log
```

## 常见问题排查

### 问题 1：Network Error

**可能原因**：
1. 后端服务未启动或崩溃
2. 端口冲突（8000端口被占用）
3. CORS 配置问题
4. 数据库连接失败

**排查方法**：
```cmd
# 检查端口是否监听
netstat -ano | findstr :8000

# 查看日志
python test_create_conversation.py
```

### 问题 2：HTTP 500 错误

**可能原因**：
1. 数据库错误
2. 模型配置不正确
3. 代码逻辑错误

**排查方法**：
查看 `logs/backend.log` 文件，搜索 "ERROR" 关键字。

### 问题 3：HTTP 422 错误

**可能原因**：
1. 请求参数格式不正确
2. 缺少必填字段
3. 类型不匹配

**排查方法**：
检查前端发送的请求格式，对比 API 文档：
```
POST /api/v1/conversations
Content-Type: application/json

{
  "title": "对话标题（可选）",
  "model_key": "claude（必填）",
  "system_prompt": "系统提示词（可选）"
}
```

## 预期的成功输出

运行 `test_create_conversation.py` 后应该看到：

```
==================================================================
详细测试创建对话功能
==================================================================

[步骤 1/5] 检查后端服务...
✓ 后端服务正常运行

[步骤 2/5] 获取可用的 LLM 配置...
  状态码: 200
✓ 找到 X 个 LLM 配置
  [1] claude - Claude 3.5 Sonnet (enabled: True)

  将使用模型: claude

[步骤 3/5] 测试创建对话...
  请求 URL: http://localhost:8000/api/v1/conversations
  请求方法: POST
  ...
  响应状态码: 200

✓ 创建对话成功
✓ 对话详情:
  - ID: xxx-xxx-xxx
  - 标题: 测试对话 - xxx
  - 模型: claude
  - 创建时间: 2026-01-23T...

[步骤 4/5] 验证对话是否创建...
✓ 当前有 X 个活跃对话

[步骤 5/5] 检查日志文件...
✓ 日志文件存在: ...
```

## 下一步

1. **如果测试失败**：
   - 复制完整的错误输出
   - 查看 `logs/backend.log` 文件
   - 提供错误信息以便进一步排查

2. **如果测试成功，但前端仍然失败**：
   - 检查前端的网络请求（浏览器开发者工具 → Network 标签）
   - 确认前端请求的 URL 和参数格式
   - 检查前端的错误日志（浏览器控制台）

3. **如果测试和前端都成功**：
   - 系统应该可以正常使用了
   - 可以开始使用对话功能

## 文件修改清单

| 文件 | 修改内容 |
|------|---------|
| [main.py](backend/main.py:13) | 添加日志轮转和文件输出 |
| [conversations.py](backend/api/conversations.py:7) | 添加 logging 和详细错误处理 |
| [conversation_service.py](backend/services/conversation_service.py:94) | 修复返回值和参数 |
| [test_create_conversation.py](backend/test_create_conversation.py) | 新增：详细测试脚本 |
| [DEBUG_GUIDE.md](DEBUG_GUIDE.md) | 新增：本调试指南 |
