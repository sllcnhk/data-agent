# Claude 中转服务适配指南

## 问题分析

您的配置：
- **Base URL**: `http://10.0.3.248:3000/api`
- **支持的模型**: `claude-2`, `claude-instant-1`
- **API Token**: `cr_b1abe3aaa6cabb76eeb3c46c1f3c9294e8b825e679c2333be9e013bb129571f4`

当前错误：`Route /api/v1/complete not found`

## 修复步骤

### 步骤 1: 修复数据库配置

在 Anaconda Prompt 中执行:

```cmd
conda activate dataagent
cd C:\Users\shiguangping\data-agent\backend
python fix_model_config.py
```

这会自动:
- 设置 `api_base_url` 为 `http://10.0.3.248:3000/api`
- 设置模型为 `claude-2`
- 设置 API Token

### 步骤 2: 重启后端

```cmd
cd C:\Users\shiguangping\data-agent\backend
set PYTHONPATH=C:\Users\shiguangping\data-agent
python main.py
```

**查看启动日志**:
```
[INIT] Initializing Claude adapter
[INIT] base_url: http://10.0.3.248:3000/api
[INIT] Model: claude-2
[INIT] Client created successfully
[INIT] Client base_url: http://10.0.3.248:3000/api/
```

### 步骤 3: 测试

在**新的** Anaconda Prompt 中:

```cmd
conda activate dataagent
cd C:\Users\shiguangping\data-agent\backend
python test_llm_chat.py
```

**查看详细日志**:

```cmd
type C:\Users\shiguangping\data-agent\logs\backend.log | findstr "[INIT]"
type C:\Users\shiguangping\data-agent\logs\backend.log | findstr "[DEBUG]"
```

## 关键修复点

### 1. 模型名称兼容性

**问题**: 使用了不支持的模型名称
```python
# 错误: claude-3-5-sonnet-20240620 (新版本模型)
# 正确: claude-2 或 claude-instant-1 (中转服务支持)
```

**修复**: 默认模型改为 `claude-2`

### 2. API Base URL

**问题**: 可能使用默认的 Anthropic 官方 URL

**修复**: 使用环境变量中的中转服务 URL

### 3. API Token 格式

**问题**: Anthropic 官方使用 `sk-ant-...` 格式，中转服务使用 `cr_...` 格式

**修复**: 使用您的中转服务 Token

## 预期结果

成功后的测试输出:

```
[Step 3/4] Sending test message...
Success: Message sent
  - Assistant reply: 1+1 equals 2.    ← 正常回复！

[Step 4/4] Verifying messages saved...
Success: Messages verified
  - Total messages: 2

All tests passed!
```

## 如果仍有问题

### 常见错误

#### 1. `Route /api/v1/complete not found`

**可能原因**:
- 中转服务的 API 路径不匹配
- 需要特殊的认证方式

**解决方案**:
```python
# 检查中转服务的实际 API 路径
# 可能需要: /v1/complete 或 /complete
```

#### 2. `Model not found`

**可能原因**: 模型名称不匹配

**解决方案**:
```python
# 在 Python 中尝试其他模型
from backend.config.database import get_db
from backend.models.llm_config import LLMConfig

db = next(get_db())
config = db.query(LLMConfig).filter(LLMConfig.model_key == 'claude').first()

# 尝试这些模型名称
for model in ['claude-2', 'claude-instant-1']:
    config.default_model = model
    db.commit()
    print(f"尝试模型: {model}")

db.close()
```

#### 3. 认证失败

**可能原因**:
- API Token 格式不正确
- Token 过期
- 权限不足

**解决方案**:
```python
# 检查 Token 格式
# 中转服务格式: cr_xxxxxxxxxx
# 官方格式: sk-ant-xxxxxxxxxx
```

### 调试命令

#### 1. 检查配置

```cmd
python
```

```python
from backend.config.database import get_db
from backend.models.llm_config import LLMConfig

db = next(get_db())
config = db.query(LLMConfig).filter(LLMConfig.model_key == 'claude').first()

print(f"Base URL: {config.api_base_url}")
print(f"Model: {config.default_model}")
print(f"Token: {config.api_key[:20]}...")

db.close()
exit()
```

#### 2. 查看日志

```cmd
# 查看初始化日志
type C:\Users\shiguangping\data-agent\logs\backend.log | findstr "[INIT]"

# 查看 API 调用日志
type C:\Users\shiguangping\data-agent\logs\backend.log | findstr "[DEBUG] API"

# 查看错误日志
type C:\Users\shiguangping\data-agent\logs\backend.log | findstr "[ERROR]"
```

## 配置验证清单

- [ ] 数据库配置已更新 (`fix_model_config.py`)
- [ ] 后端重启成功
- [ ] 启动日志显示正确的 base_url 和模型
- [ ] 测试通过
- [ ] 助手回复是正常文本

## 备用方案

如果中转服务仍有兼容性问题，可以:

### 方案 1: 升级 anthropic 库

```cmd
# 在虚拟环境中
pip install --upgrade anthropic
```

### 方案 2: 使用官方 API

在 `fix_model_config.py` 中:

```python
config.api_base_url = "https://api.anthropic.com"
config.api_key = "sk-ant-your-real-api-key"
config.default_model = "claude-3-5-sonnet-20240620"
```

## 支持的模型

从您的中转服务输出版本，支持的模型:

| 模型名称 | 说明 | 适用场景 |
|---------|------|---------|
| `claude-2` | Claude 2 | 对话、分析 |
| `claude-instant-1` | Claude Instant | 快速响应 |

## 技术细节

### anthropic 0.7.7 API

```python
# 中转服务支持的调用方式
client = anthropic.Anthropic(
    api_key="cr_...",
    base_url="http://10.0.3.248:3000/api"
)

response = client.completions.create(
    prompt="Human: Hello\n\nAssistant:",
    model="claude-2",
    max_tokens_to_sample=1024,
    temperature=0.7
)

print(response.completion)
```

### 修复的文件

| 文件 | 修改内容 |
|------|---------|
| `fix_model_config.py` | 新建：修复数据库配置 |
| `core/model_adapters/claude.py` | 默认模型改为 `claude-2` |
| `core/model_adapters/claude.py` | 添加详细的 API 调试日志 |
