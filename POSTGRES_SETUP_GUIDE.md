# PostgreSQL 安装指南 (Windows)

**⚠️ 遇到连接问题?** 查看 → [PostgreSQL 连接问题修复指南](POSTGRES_FIX_GUIDE.md)

有三种方式安装PostgreSQL,选择最适合你的方式:

---

## 方式1: 自动安装脚本 (推荐)

使用提供的批处理脚本自动安装:

```powershell
# 右键点击 -> "以管理员身份运行"
setup_postgres_windows.bat
```

这个脚本会:
- 安装 Chocolatey 包管理器
- 安装 PostgreSQL 14
- 安装 Python 驱动 (psycopg2-binary)
- 创建 data_agent 数据库

---

## 方式2: Docker 安装 (最简单)

如果你已经安装了 Docker Desktop:

```powershell
# 双击运行
setup_postgres_docker.bat
```

或手动运行:

```powershell
# 启动 PostgreSQL 容器
docker run -d `
    --name data-agent-postgres `
    -e POSTGRES_PASSWORD=postgres `
    -e POSTGRES_DB=data_agent `
    -p 5432:5432 `
    -v data-agent-pgdata:/var/lib/postgresql/data `
    postgres:14

# 安装 Python 驱动
pip install psycopg2-binary
```

**Docker 管理命令**:
```powershell
# 查看容器状态
docker ps

# 查看日志
docker logs data-agent-postgres

# 停止容器
docker stop data-agent-postgres

# 启动容器
docker start data-agent-postgres

# 删除容器和数据
docker rm -f data-agent-postgres
docker volume rm data-agent-pgdata
```

---

## 方式3: 手动安装

### 步骤1: 下载并安装 PostgreSQL

1. 访问: https://www.postgresql.org/download/windows/
2. 下载 PostgreSQL 14 安装包
3. 运行安装程序:
   - 设置密码: `postgres` (或其他你记得住的密码)
   - 端口: `5432` (默认)
   - 安装组件: 全部勾选

### 步骤2: 创建数据库

打开 SQL Shell (psql):

```sql
-- 输入密码 (你设置的密码)
postgres

-- 创建数据库
CREATE DATABASE data_agent;

-- 验证
\l
-- 应该能看到 data_agent 数据库

-- 退出
\q
```

### 步骤3: 安装 Python 驱动

```powershell
cd C:\Users\shiguangping\data-agent\backend
pip install psycopg2-binary
```

如果安装失败,尝试:
```powershell
pip install psycopg2
```

---

## 配置数据库连接

安装完成后,检查 `backend/config/settings.py` 中的配置:

```python
# PostgreSQL 配置
POSTGRES_HOST = os.getenv("POSTGRES_HOST", "localhost")
POSTGRES_PORT = int(os.getenv("POSTGRES_PORT", "5432"))
POSTGRES_DB = os.getenv("POSTGRES_DB", "data_agent")
POSTGRES_USER = os.getenv("POSTGRES_USER", "postgres")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD", "postgres")
```

**如果你的密码不是 `postgres`**, 需要修改:

### 方式A: 修改环境变量 (推荐)

创建 `.env` 文件在项目根目录:

```env
POSTGRES_PASSWORD=你的密码
```

### 方式B: 直接修改配置文件

编辑 `backend/config/settings.py`:

```python
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD", "你的密码")
```

---

## 验证安装

### 测试1: 连接数据库

```powershell
# 使用 psql 命令行
psql -U postgres -d data_agent

# 或使用 Python
python -c "import psycopg2; conn = psycopg2.connect('postgresql://postgres:postgres@localhost/data_agent'); print('连接成功!'); conn.close()"
```

### 测试2: 初始化数据库表

```powershell
cd C:\Users\shiguangping\data-agent\backend
python scripts\init_chat_db.py
```

预期输出:
```
============================================================
初始化聊天功能数据库
============================================================

1. 创建表结构...
✓ 表结构创建完成

2. 初始化默认LLM配置...
✓ 成功创建 4 个默认配置
```

---

## 常见问题

### 问题1: "psycopg2 模块未找到"

**解决**:
```powershell
pip install psycopg2-binary
```

### 问题2: "连接被拒绝"

**原因**: PostgreSQL 服务未启动

**解决**:
- 手动安装: 打开 Services.msc,启动 "postgresql-x64-14" 服务
- Docker: 运行 `docker start data-agent-postgres`

### 问题3: "密码验证失败"

**解决**: 检查密码是否正确,修改配置文件中的密码

### 问题4: "数据库不存在"

**解决**: 手动创建数据库
```sql
psql -U postgres
CREATE DATABASE data_agent;
```

### 问题5: Docker 端口冲突

**错误**: `port is already allocated`

**解决**: 更改端口或停止占用5432端口的服务
```powershell
# 使用其他端口
docker run -d -p 5433:5432 ...

# 然后修改配置
# settings.py: POSTGRES_PORT = 5433
```

---

## 数据库管理工具 (可选)

### pgAdmin (官方GUI工具)

- 安装PostgreSQL时已包含
- 或单独下载: https://www.pgadmin.org/download/

### DBeaver (跨平台)

- 下载: https://dbeaver.io/download/

### DataGrip (JetBrains)

- 下载: https://www.jetbrains.com/datagrip/

---

## 完整安装流程总结

```powershell
# 1. 安装 PostgreSQL (选择方式1、2或3)
setup_postgres_docker.bat

# 2. 安装 Python 驱动 (如果还没装)
pip install psycopg2-binary

# 3. 初始化数据库表
cd backend
python scripts\init_chat_db.py

# 4. 启动后端服务
python main.py

# 5. 启动前端服务
cd ..\frontend
npm install
npm run dev
```

---

## 卸载

### 手动安装的 PostgreSQL

1. 控制面板 -> 程序和功能 -> 卸载 PostgreSQL
2. 删除数据目录: `C:\Program Files\PostgreSQL\14\data`

### Docker PostgreSQL

```powershell
# 停止并删除容器
docker rm -f data-agent-postgres

# 删除数据卷
docker volume rm data-agent-pgdata
```

---

## 下一步

PostgreSQL 安装完成后,继续查看:
- [快速开始指南](QUICK_START_GUIDE.md)
- [聊天功能设置](CHAT_SETUP_GUIDE.md)

如有问题,请查看 [常见问题](#常见问题) 部分。
