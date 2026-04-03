# 快速开始指南

本指南帮助你快速搭建和运行数据分析Agent系统。

## 前置要求

### 必需软件
- **Python 3.10+** - 后端运行环境
- **Node.js 18+** - 前端运行环境
- **PostgreSQL 14+** - 主数据库
- **Redis 6+** - 缓存数据库

### 可选软件
- **Docker & Docker Compose** - 容器化部署(推荐)
- **Git** - 版本控制

## 方式一: 手动安装(开发环境)

### 1. 克隆/检查项目
```bash
cd C:\Users\shiguangping\data-agent
```

### 2. 安装后端依赖
```bash
# 创建虚拟环境(推荐)
python -m venv venv

# 激活虚拟环境
# Windows:
venv\Scripts\activate
# Linux/Mac:
source venv/bin/activate

# 安装依赖
pip install -r backend/requirements.txt
```

### 3. 配置环境变量
```bash
# 复制配置模板
copy .env.example .env

# 使用文本编辑器编辑 .env 文件
# 必须配置的项目:
```

**必须配置的环境变量:**
```bash
# PostgreSQL
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
POSTGRES_DB=data_agent
POSTGRES_USER=postgres
POSTGRES_PASSWORD=your_password_here

# Redis
REDIS_HOST=localhost
REDIS_PORT=6379
REDIS_PASSWORD=  # 如果没有密码则留空

# LLM API (至少配置一个)
ANTHROPIC_API_KEY=sk-ant-your-api-key-here
# 或
OPENAI_API_KEY=sk-your-openai-key-here
# 或
GOOGLE_API_KEY=your-google-api-key-here
```

### 4. 启动PostgreSQL和Redis
**Windows (使用服务管理器):**
1. 按 `Win + R`, 输入 `services.msc`
2. 找到并启动 "PostgreSQL" 和 "Redis" 服务

**Linux/Mac:**
```bash
# PostgreSQL
sudo systemctl start postgresql

# Redis
sudo systemctl start redis
```

**使用Docker (推荐):**
```bash
# PostgreSQL
docker run -d \
  --name postgres \
  -e POSTGRES_PASSWORD=your_password \
  -e POSTGRES_DB=data_agent \
  -p 5432:5432 \
  postgres:14

# Redis
docker run -d \
  --name redis \
  -p 6379:6379 \
  redis:6
```

### 5. 初始化数据库
```bash
# 运行初始化脚本
python backend/scripts/init_db.py
```

如果看到以下输出,说明初始化成功:
```
✓ PostgreSQL 连接成功
✓ Redis 连接成功
✓ 数据库表已创建
```

### 6. 启动后端服务
```bash
# 开发模式启动
uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000

# 或使用脚本(如果已创建)
python backend/main.py
```

后端服务将在 `http://localhost:8000` 运行

### 7. 安装前端依赖(待创建)
```bash
cd frontend
npm install
```

### 8. 启动前端服务(待创建)
```bash
npm run dev
```

前端服务将在 `http://localhost:3000` 运行

## 方式二: Docker Compose(推荐用于生产)

### 1. 配置环境变量
```bash
copy .env.example .env
# 编辑 .env 文件配置必要参数
```

### 2. 启动所有服务
```bash
docker-compose up -d
```

这将启动:
- PostgreSQL数据库
- Redis缓存
- 后端API服务
- 前端Web服务

### 3. 查看日志
```bash
# 查看所有服务日志
docker-compose logs -f

# 查看特定服务
docker-compose logs -f backend
docker-compose logs -f frontend
```

### 4. 停止服务
```bash
docker-compose down

# 停止并删除数据
docker-compose down -v
```

## 验证安装

### 1. 检查数据库连接
```bash
python -c "from backend.config.database import check_database_connections; check_database_connections()"
```

### 2. 检查后端API
访问: http://localhost:8000/docs

你应该看到FastAPI自动生成的API文档。

### 3. 检查前端应用
访问: http://localhost:3000

你应该看到数据分析Agent系统的主界面。

## 常见问题

### Q1: PostgreSQL连接失败

**错误**: `could not connect to server: Connection refused`

**解决方案**:
1. 确保PostgreSQL服务已启动
2. 检查端口5432是否被占用: `netstat -ano | findstr :5432`
3. 验证.env中的数据库配置是否正确
4. 尝试使用psql命令行连接测试: `psql -h localhost -U postgres`

### Q2: Redis连接失败

**错误**: `Error connecting to Redis`

**解决方案**:
1. 确保Redis服务已启动
2. 检查端口6379是否被占用: `netstat -ano | findstr :6379`
3. 如果设置了密码,确保.env中REDIS_PASSWORD配置正确

### Q3: 缺少Python模块

**错误**: `ModuleNotFoundError: No module named 'xxx'`

**解决方案**:
```bash
# 确保在虚拟环境中
pip install -r backend/requirements.txt

# 或单独安装缺失的包
pip install xxx
```

### Q4: Alembic迁移失败

**错误**: `Target database is not up to date`

**解决方案**:
```bash
# 查看当前迁移状态
alembic current

# 应用所有迁移
alembic upgrade head

# 如果仍然失败,尝试重新初始化
python backend/scripts/init_db.py
```

### Q5: 端口被占用

**错误**: `[Errno 10048] Address already in use`

**解决方案**:
```bash
# 查看占用端口的进程
# Windows:
netstat -ano | findstr :8000
taskkill /PID <进程ID> /F

# Linux/Mac:
lsof -i :8000
kill -9 <进程ID>

# 或修改端口
PORT=8001 uvicorn backend.main:app --reload
```

## 开发工作流

### 修改数据库模型后
```bash
# 1. 生成新的迁移脚本
alembic revision --autogenerate -m "描述变更内容"

# 2. 检查生成的迁移脚本
# 打开 alembic/versions/xxx_描述变更内容.py

# 3. 应用迁移
alembic upgrade head

# 4. 如果需要回滚
alembic downgrade -1
```

### 运行测试
```bash
# 运行所有测试
pytest

# 运行特定测试文件
pytest backend/tests/test_models.py

# 查看测试覆盖率
pytest --cov=backend --cov-report=html
```

### 代码格式化
```bash
# 格式化代码
black backend/

# 检查代码风格
flake8 backend/

# 排序导入
isort backend/
```

## 下一步

现在你已经完成了基础环境搭建,可以:

1. **阅读文档**:
   - [README.md](README.md) - 项目概述
   - [docs/database_migration.md](docs/database_migration.md) - 数据库迁移指南
   - [docs/phase1_completion_summary.md](docs/phase1_completion_summary.md) - 阶段1总结

2. **开始开发**:
   - 阶段2: 后端核心模块开发
   - 创建统一对话格式
   - 实现模型适配器
   - 实现上下文管理器

3. **运行示例**:
   - 测试对话API
   - 测试任务创建
   - 测试报表生成

## 获取帮助

如果遇到问题:
1. 查看本文档的"常见问题"部分
2. 查看 [GitHub Issues](项目地址/issues)
3. 查看项目文档目录下的其他文档

## 开发资源

- [FastAPI文档](https://fastapi.tiangolo.com/)
- [SQLAlchemy文档](https://docs.sqlalchemy.org/)
- [Alembic文档](https://alembic.sqlalchemy.org/)
- [React文档](https://react.dev/)
- [Ant Design文档](https://ant.design/)
