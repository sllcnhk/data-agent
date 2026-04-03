# Data Agent 依赖升级指南

## 当前问题分析

### 根本原因
- Python 3.8.20 不支持 anthropic >= 0.8.0（需要 Python ≥ 3.9）
- 原代码使用了 `anthropic.types.Message` 类型注解，在 0.7.7 版本中导致 AttributeError

---

## 方案1：保持 Python 3.8 + anthropic 0.7.7（推荐）

### 优点
- 无需升级 Python 环境
- 依赖变动最小，风险最低
- 快速解决问题

### 已完成的修改
1. **恢复 requirements.txt**
   - `anthropic==0.7.7` （保持原版本）

2. **修复 claude.py 类型注解**
   - 将 `response: "anthropic.types.Message"` 改为 `response: Any`
   - 避免运行时类型检查错误

### 测试步骤

```cmd
# 1. 激活环境
conda activate dataagent

# 2. 进入后端目录
cd C:\Users\shiguangping\data-agent\backend

# 3. 测试导入（不会安装任何新包）
python test_import.py

# 4. 如果测试通过，启动服务
cd ..
start-all.bat
```

### 预期结果
- test_import.py 所有检查应该显示 ✓
- 后端服务应该正常启动，不再报 AttributeError
- 前端 MCP 连接错误应该消失

---

## 方案2：升级到 Python 3.9+（备选）

### 仅在方案1失败时使用

### 需要升级的依赖清单

#### 第一步：创建新的 Python 3.9 环境

```cmd
# 创建新环境
conda create -n dataagent39 python=3.9 -y

# 激活新环境
conda activate dataagent39

# 进入项目目录
cd C:\Users\shiguangping\data-agent\backend
```

#### 第二步：更新 requirements.txt

需要修改以下包版本：

```txt
# Web framework
fastapi==0.104.1          # 保持不变
uvicorn[standard]==0.24.0 # 保持不变
python-multipart==0.0.6   # 保持不变
pydantic==2.5.0           # 保持不变
pydantic-settings==2.1.0  # 保持不变

# Database ORM
sqlalchemy==2.0.23        # 保持不变
psycopg2-binary==2.9.9    # 保持不变
alembic==1.13.0           # 保持不变

# Cache
redis==5.0.1              # 保持不变
hiredis==2.2.3            # 保持不变

# LLM SDK - 需要升级
anthropic==0.39.0         # 0.7.7 -> 0.39.0 (最后支持 Python 3.9 的版本)
openai==1.54.0            # 1.3.7 -> 1.54.0 (兼容新的API)
google-generativeai==0.8.3 # 0.3.1 -> 0.8.3 (支持 Python 3.9)

# Data processing
pandas==2.1.3             # 保持不变
numpy==1.26.2             # 保持不变
xlsxwriter==3.1.9         # 保持不变
openpyxl==3.1.2           # 保持不变

# Database drivers
clickhouse-driver==0.2.6  # 保持不变
pymysql==1.1.0            # 保持不变
cryptography==41.0.7      # 保持不变

# Utilities
python-dotenv==1.0.0      # 保持不变
pyyaml==6.0.1             # 保持不变
python-jose[cryptography]==3.3.0  # 保持不变
passlib[bcrypt]==1.7.4    # 保持不变

# Logging
loguru==0.7.2             # 保持不变

# HTTP client
httpx==0.25.2             # 保持不变
aiohttp==3.9.1            # 保持不变

# Async tasks
celery==5.3.4             # 保持不变
flower==2.0.1             # 保持不变

# File processing
python-magic==0.4.27      # 保持不变
chardet==5.2.0            # 保持不变

# Lark SDK
lark-oapi==1.2.8          # 保持不变

# Data validation
jsonschema==4.20.0        # 保持不变
marshmallow==3.20.1       # 保持不变

# Testing
pytest==7.4.3             # 保持不变
pytest-asyncio==0.21.1    # 保持不变
pytest-cov==4.1.0         # 保持不变
httpx-mock==0.11.0        # 保持不变

# Code quality
black==23.12.0            # 保持不变
flake8==6.1.0             # 保持不变
mypy==1.7.1               # 保持不变
isort==5.13.0             # 保持不变

# Documentation
mkdocs==1.5.3             # 保持不变
mkdocs-material==9.5.2    # 保持不变

# Monitoring
prometheus-client==0.19.0 # 保持不变
```

#### 第三步：安装依赖

```cmd
pip install -r requirements.txt
```

#### 第四步：可能需要调整的代码

1. **anthropic 0.39.0 API 变化**
   - 基本 API 保持兼容，无需修改

2. **openai 1.54.0 API 变化**
   - 需要检查 `backend/core/model_adapters/openai.py`
   - 可能需要调整调用方式

3. **google-generativeai 0.8.3 API 变化**
   - 需要检查 `backend/core/model_adapters/gemini.py`
   - API 可能有较大变化

#### 第五步：测试清单

```cmd
# 1. 测试导入
python test_import.py

# 2. 测试数据库连接
python -c "from backend.database import engine; print('数据库连接成功')"

# 3. 测试 LLM 配置加载
python -c "from backend.models import LLMConfig; from backend.database import SessionLocal; db = SessionLocal(); configs = db.query(LLMConfig).all(); print(f'找到 {len(configs)} 个 LLM 配置'); db.close()"

# 4. 启动服务测试
cd ..
start-all.bat
```

---

## 推荐执行流程

### 第一阶段：尝试方案1
1. 运行 `python backend/test_import.py`
2. 如果测试通过，运行 `start-all.bat`
3. 检查后端是否正常启动
4. 检查前端是否能连接 MCP 服务

### 第二阶段：如果方案1失败
1. 备份当前环境：`conda list --export > environment_backup.txt`
2. 创建新的 Python 3.9 环境
3. 按照方案2的步骤逐项执行
4. 每次修改后运行测试脚本验证

---

## 常见问题

### Q: 为什么不直接升级到最新版本？
A: Python 3.8 是限制因素，很多新版本库需要 Python 3.9+

### Q: 升级 Python 版本会影响其他项目吗？
A: 不会，conda 环境是隔离的，创建新环境不影响现有环境

### Q: 如果方案1和方案2都失败怎么办？
A:
1. 检查是否有代理或网络问题
2. 尝试清理 pip 缓存：`pip cache purge`
3. 更换 pip 镜像源
4. 联系项目维护者获取支持

---

## 当前状态

- ✓ 已修复 [claude.py](backend/core/model_adapters/claude.py) 类型注解问题
- ✓ 已恢复 requirements.txt 到兼容版本
- ⏳ 等待测试验证

请执行测试步骤，并根据结果决定是否需要方案2。
