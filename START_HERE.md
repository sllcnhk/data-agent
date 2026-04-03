# 🚀 快速开始 - 从这里开始

## 当前状态

您已经成功安装了 PostgreSQL 18,现在需要完成数据库初始化。

## ⚡ 三步完成设置

### 第一步: 修复数据库连接

**选择以下任意一种方式:**

#### 🔧 方式A: 自动化脚本(推荐)

以**管理员身份**打开 PowerShell:

```powershell
cd C:\Users\shiguangping\data-agent\backend
python auto_setup_database.py
```

#### 🖥️ 方式B: 使用 pgAdmin(最简单)

1. 打开 **pgAdmin 4**
2. 连接到 **PostgreSQL 18**
3. 创建数据库 `data_agent`
4. 详细步骤 → [POSTGRES_FIX_GUIDE.md](POSTGRES_FIX_GUIDE.md#方案-b-使用-pgadmin-图形界面最简单)

#### 📝 方式C: 手动命令行

查看详细步骤 → [POSTGRES_FIX_GUIDE.md](POSTGRES_FIX_GUIDE.md#方案-c-手动命令行修复)

---

### 第二步: 初始化数据库

完成第一步后,运行:

```powershell
cd C:\Users\shiguangping\data-agent\backend
python scripts\init_chat_db.py
```

**预期输出:**
```
✓ 表结构创建完成
✓ 成功创建 4 个默认配置
```

---

### 第三步: 启动系统

**方式A: 一键启动(推荐)**

双击运行:
```
start-all.bat
```

或在命令行:
```powershell
cd C:\Users\shiguangping\data-agent
start-all.bat
```

脚本会自动:
- ✅ 检查Python、Node.js环境
- ✅ 检查PostgreSQL服务状态
- ✅ 验证数据库连接
- ✅ 检查前端依赖
- ✅ 同时启动后端和前端

**方式B: 手动启动**
```powershell
# 终端1: 启动后端
cd C:\Users\shiguangping\data-agent\backend
python main.py

# 终端2: 启动前端
cd C:\Users\shiguangping\data-agent\frontend
npm run dev
```

**访问**: http://localhost:3000

---

## 📚 完整文档

| 文档 | 说明 |
|------|------|
| **[USAGE_GUIDE.md](USAGE_GUIDE.md)** | 📖 详细使用指南(启动、操作、技巧) |
| **[SETUP_SUMMARY.md](SETUP_SUMMARY.md)** | 📋 设置总结和当前状态 |
| **[POSTGRES_FIX_GUIDE.md](POSTGRES_FIX_GUIDE.md)** | 🔧 PostgreSQL 连接问题详细修复指南 |
| **[QUICK_START_GUIDE.md](QUICK_START_GUIDE.md)** | ⚡ 5分钟快速启动指南 |
| **[POSTGRES_SETUP_GUIDE.md](POSTGRES_SETUP_GUIDE.md)** | 📖 PostgreSQL 完整安装指南 |
| **[README.md](README.md)** | 📘 项目完整说明文档 |

---

## 🛠️ 辅助工具

| 工具 | 用途 |
|------|------|
| `backend/test_connection.py` | 测试数据库连接 |
| `backend/auto_setup_database.py` | 自动化数据库设置 |
| `reset_postgres_password.bat` | 重置PostgreSQL密码 |

---

## ❓ 遇到问题?

### 问题1: 连接失败
➜ 查看 [POSTGRES_FIX_GUIDE.md](POSTGRES_FIX_GUIDE.md)

### 问题2: 不知道PostgreSQL密码
➜ 使用 pgAdmin 或 auto_setup_database.py 重置

### 问题3: 初始化脚本报错
➜ 先运行 `python test_connection.py` 诊断连接

---

## 📞 技术支持

如果所有方法都不work,请提供:
1. `python test_connection.py` 的输出
2. pgAdmin 能否成功连接
3. PostgreSQL 安装时使用的密码

---

**准备好了吗? 从第一步开始! 👆**
