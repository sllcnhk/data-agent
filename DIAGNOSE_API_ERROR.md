# API 404 错误诊断

## 错误信息
```
Error code: 404 - {'error': 'Not Found', 'message': 'Route /api/v1/complete not found'}
```

## 问题分析

### 可能原因 1: API Base URL 配置错误 (最可能)

**问题**: 数据库中配置的 `api_base_url` 指向了一个不正确的服务器或代理

**解决方法**: 检查并修改数据库中的 LLM 配置

#### 步骤 1: 检查当前配置

打开 Anaconda Prompt:

```cmd
conda activate dataagent
cd C:\Users\shiguangping\data-agent\backend
python
```

然后在 Python 中执行:

```python
from backend.config.database import get_db
from backend.models.llm_config import LLMConfig

db = next(get_db())
configs = db.query(LLMConfig).filter(LLMConfig.model_key == 'claude').all()

for config in configs:
    print(f"Model Key: {config.model_key}")
    print(f"API Base URL: {config.api_base_url}")
    print(f"Default Model: {config.default_model}")
    print(f"Enabled: {config.enabled}")
    print("-" * 50)

db.close()
```

**预期输出**:
- 如果 `api_base_url` 是 `http://localhost:xxxx` 或其他自定义地址 → **需要修改**
- 正确的应该是: `https://api.anthropic.com` 或留空

#### 步骤 2: 修正配置 (如果需要)

如果 API Base URL 不正确，在 Python 中执行:

```python
from backend.config.database import get_db
from backend.models.llm_config import LLMConfig

db = next(get_db())
config = db.query(LLMConfig).filter(LLMConfig.model_key == 'claude').first()

if config:
    # 设置为 Anthropic 官方 API
    config.api_base_url = "https://api.anthropic.com"
    # 或者设置为 None 使用默认值
    # config.api_base_url = None

    db.commit()
    print("✓ Configuration updated")
else:
    print("✗ Configuration not found")

db.close()
```

---

### 可能原因 2: anthropic 0.7.7 模型名称不支持

**问题**: anthropic 0.7.7 不支持 `claude-3-5-sonnet-20240620` 这样的模型名称

**解决方法**: 使用 0.7.7 支持的模型名称

#### 检查并修改模型名称

在 Python 中:

```python
from backend.config.database import get_db
from backend.models.llm_config import LLMConfig

db = next(get_db())
config = db.query(LLMConfig).filter(LLMConfig.model_key == 'claude').first()

if config:
    print(f"Current model: {config.default_model}")

    # 修改为 anthropic 0.7.7 支持的模型
    # 可能的选项: "claude-v1", "claude-v1-100k", "claude-instant-v1"
    config.default_model = "claude-v1"

    db.commit()
    print("✓ Model updated")

db.close()
```

---

### 可能原因 3: API Key 无效或过期

**检查**: API Key 是否正确配置

```python
from backend.config.database import get_db
from backend.models.llm_config import LLMConfig

db = next(get_db())
config = db.query(LLMConfig).filter(LLMConfig.model_key == 'claude').first()

if config:
    api_key = config.api_key
    print(f"API Key (first 10 chars): {api_key[:10]}...")
    print(f"API Key length: {len(api_key)}")

    # Anthropic API keys 应该以 "sk-ant-" 开头
    if not api_key.startswith('sk-ant-'):
        print("⚠️ WARNING: API key format may be incorrect")

db.close()
```

---

## 诊断步骤

### 第 1 步: 运行库检查脚本

```cmd
cd C:\Users\shiguangping\data-agent\backend
python check_anthropic_version.py
```

这会显示:
- anthropic 库的版本
- 可用的 API 方法
- 客户端属性

### 第 2 步: 检查 LLM 配置

按照上面的 Python 脚本检查:
1. api_base_url - 应该是 `https://api.anthropic.com`
2. default_model - 应该是 anthropic 0.7.7 支持的模型名称
3. api_key - 应该以 `sk-ant-` 开头

### 第 3 步: 重启后端并查看详细日志

```cmd
cd C:\Users\shiguangping\data-agent\backend
set PYTHONPATH=C:\Users\shiguangping\data-agent
python main.py
```

在另一个窗口运行测试:

```cmd
conda activate dataagent
cd C:\Users\shiguangping\data-agent\backend
python test_llm_chat.py
```

### 第 4 步: 查看详细日志

```cmd
type C:\Users\shiguangping\data-agent\logs\backend.log | findstr "[INIT]"
type C:\Users\shiguangping\data-agent\logs\backend.log | findstr "[DEBUG]"
```

查找:
- `[INIT] base_url:` - 显示使用的 base URL
- `[DEBUG] Client base_url:` - 显示客户端的 base URL
- `[DEBUG] API call failed:` - 显示 API 调用失败的详细信息

---

## 快速修复建议

**最可能的问题**: API Base URL 配置错误

**快速修复**:

```python
# 在 Python 中执行
from backend.config.database import get_db
from backend.models.llm_config import LLMConfig

db = next(get_db())
config = db.query(LLMConfig).filter(LLMConfig.model_key == 'claude').first()

if config:
    # 修复 Base URL
    config.api_base_url = "https://api.anthropic.com"

    # 修复模型名称 (如果需要)
    if "claude-3" in config.default_model:
        config.default_model = "claude-v1"

    db.commit()
    print("✓ Configuration fixed")
    print(f"  - Base URL: {config.api_base_url}")
    print(f"  - Model: {config.default_model}")

db.close()
```

然后重启后端并重新测试。

---

## 备用方案: 使用环境变量

如果数据库配置修改不生效，可以使用环境变量:

在启动后端前设置:

```cmd
set ANTHROPIC_API_KEY=your-api-key-here
set ANTHROPIC_BASE_URL=https://api.anthropic.com

cd C:\Users\shiguangping\data-agent\backend
set PYTHONPATH=C:\Users\shiguangping\data-agent
python main.py
```

---

## 需要提供的信息

如果以上步骤无法解决问题，请运行并提供以下输出:

1. **库版本信息**:
   ```cmd
   python check_anthropic_version.py
   pip show anthropic
   ```

2. **配置信息** (运行上面的 Python 脚本)

3. **详细日志**:
   ```cmd
   type C:\Users\shiguangping\data-agent\logs\backend.log | findstr "[INIT]"
   type C:\Users\shiguangping\data-agent\logs\backend.log | findstr "[DEBUG] Client"
   type C:\Users\shiguangping\data-agent\logs\backend.log | findstr "[DEBUG] API"
   ```
