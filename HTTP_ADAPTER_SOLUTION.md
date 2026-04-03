# HTTP 适配器解决方案

## 问题分析

### 核心问题
- anthropic 0.7.7 库调用路径：`/v1/complete` (单数)
- 中转服务支持路径：`/v1/completions` (复数) - OpenAI兼容
- 错误：`Route /v1/complete not found`

### 原因
很多中转服务（包括您的）使用 OpenAI 兼容的 API 格式，但 anthropic 0.7.7 库使用不同的路径结构。

## 解决方案

### 绕过 anthropic 库，直接使用 HTTP 调用

创建了新的 `claude.py` 适配器，使用 `httpx` 直接调用中转服务。

### 关键改进

1. **直接 HTTP 调用**
   - 使用 `httpx.AsyncClient()` 代替 `anthropic.Anthropic`
   - 直接调用 `{base_url}/v1/completions` (复数)

2. **OpenAI 兼容格式**
   - 请求格式：`POST /v1/completions`
   - 认证：`Bearer {api_key}`
   - 响应格式：OpenAI 标准格式

3. **保持接口兼容**
   - 仍然返回 `UnifiedMessage`
   - 保持相同的外部接口

## 文件修改

### 新文件
- `core/model_adapters/claude_http.py` → 后重命名为 `claude.py`
- `test_http_adapter.py` - 测试脚本

### 修改文件
- `core/model_adapters/factory.py` - 使用新的 HTTP 适配器

### 备份文件
- `core/model_adapters/claude_original.py` - 原始 anthropic 库版本

## 测试

### 直接测试适配器
```cmd
python test_http_adapter.py
```

### 通过后端测试
```cmd
python main.py
# 等待启动完成
python test_llm_chat.py
```

## 预期结果

### 成功标志
- ✅ 启动日志显示：`[INIT] Using direct HTTP client (bypassing anthropic library)`
- ✅ API 调用日志显示：`[DEBUG] HTTP status: 200`
- ✅ 助手回复正常文本（如 "1+1 equals 2"）

### 如果失败
查看详细日志：
```cmd
type logs\backend.log | findstr "[DEBUG]"
```

常见错误：
1. `401 Unauthorized` - API Key 无效
2. `404 Not Found` - 路径错误（仍使用 `/complete`）
3. `Model not found` - 模型名称不支持

## 技术细节

### API 调用流程

```python
async with httpx.AsyncClient() as client:
    response = await client.post(
        f"{base_url}/v1/completions",  # 复数路径！
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        },
        json={
            "model": model,
            "prompt": prompt,
            "max_tokens": 1024,
            "temperature": 0.7
        }
    )
```

### 响应格式转换

```python
# 中转返回
{
    "choices": [
        {"text": "1+1 equals 2."}
    ]
}

# 转换为 UnifiedMessage
UnifiedMessage(
    role=MessageRole.ASSISTANT,
    content="1+1 equals 2.",
    model="claude"
)
```

## 模型配置

当前配置（已修改）：
- `api_base_url`: `http://10.0.3.248:3000`
- `default_model`: `claude`
- `api_key`: 您的中转服务 Token

## 回滚方案

如果需要回滚到原始版本：

```bash
mv core/model_adapters/claude_original.py core/model_adapters/claude.py
```

## 支持的特性

### ✅ 已实现
- 基本的对话功能
- 单轮对话
- 错误处理和日志
- 响应格式转换

### ⚠️ 简化版本
- 流式输出 - 返回完整响应（未实现真正的流式）
- 工具调用 - 暂不支持
- 高级参数 - 基础参数支持

## 兼容性

- **Python 3.8+** ✅
- **httpx** ✅ (已在 requirements.txt)
- **异步支持** ✅
- **错误处理** ✅

## 下一步

1. 重启后端服务
2. 运行测试
3. 如果成功，LLM 对话功能将正常工作
