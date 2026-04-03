# PostgreSQL 连接问题修复指南

## 问题现状

✗ PostgreSQL 18 服务运行正常
✗ 但是密码验证失败: `password authentication failed for user "postgres"`
✗ 导致无法连接数据库和初始化系统

## 快速修复方案

### 方案 A: 自动化脚本修复(推荐)

**以管理员身份运行 PowerShell 或 CMD,然后执行:**

```powershell
cd C:\Users\shiguangping\data-agent\backend
python auto_setup_database.py
```

这个脚本会自动:
1. 临时修改 PostgreSQL 认证方式为信任模式
2. 重置密码为 `Sgp013013.`
3. 创建 `data_agent` 数据库
4. 恢复原始配置
5. 测试连接

**完成后直接运行初始化:**
```powershell
python scripts\init_chat_db.py
```

---

### 方案 B: 使用 pgAdmin 图形界面(最简单)

1. **打开 pgAdmin 4** (开始菜单搜索 "pgAdmin")

2. **设置 Master Password** (首次打开时会要求,随便设一个)

3. **连接 PostgreSQL**:
   - 左侧展开 **Servers**
   - 双击 **PostgreSQL 18**
   - 输入安装时设置的密码
   - 如果忘记了,尝试: `postgres`, `admin`, `123456`, 或您常用的密码

4. **创建数据库**:
   - 右键点击 **Databases**
   - 选择 **Create** → **Database...**
   - Database 名称输入: `data_agent`
   - Owner 选择: `postgres`
   - 点击 **Save**

5. **运行初始化脚本**:
   ```powershell
   cd C:\Users\shiguangping\data-agent\backend
   python scripts\init_chat_db.py
   ```

如果还是密码错误,在 pgAdmin 中修改密码:
- 展开 **PostgreSQL 18** → **Login/Group Roles**
- 右键点击 **postgres** → **Properties**
- 切换到 **Definition** 标签
- 输入新密码: `Sgp013013.`
- 点击 **Save**

然后更新 `.env` 文件中的密码,再运行初始化脚本。

---

### 方案 C: 手动命令行修复

#### 步骤 1: 修改 pg_hba.conf

**以管理员身份打开记事本:**
- 右键点击记事本 → 以管理员身份运行
- 打开文件: `C:\Program Files\PostgreSQL\18\data\pg_hba.conf`

**找到这一行:**
```
host    all             all             127.0.0.1/32            scram-sha-256
```

**临时修改为:**
```
host    all             all             127.0.0.1/32            trust
```

**保存文件**

#### 步骤 2: 重启 PostgreSQL

打开 PowerShell (管理员):
```powershell
Restart-Service postgresql-x64-18
```

等待 5 秒让服务完全启动

#### 步骤 3: 重置密码并创建数据库

```powershell
cd "C:\Program Files\PostgreSQL\18\bin"

# 连接 PostgreSQL (现在不需要密码)
.\psql.exe -U postgres -h localhost -d postgres

# 在 psql 提示符中执行:
ALTER USER postgres PASSWORD 'Sgp013013.';
CREATE DATABASE data_agent;
\q
```

#### 步骤 4: 恢复 pg_hba.conf

重新用管理员权限打开记事本,编辑 `pg_hba.conf`,把刚才的行改回:
```
host    all             all             127.0.0.1/32            scram-sha-256
```

保存文件

#### 步骤 5: 再次重启服务

```powershell
Restart-Service postgresql-x64-18
```

#### 步骤 6: 测试并初始化

```powershell
cd C:\Users\shiguangping\data-agent\backend

# 测试连接
python test_connection.py

# 初始化数据库
python scripts\init_chat_db.py
```

---

## 验证修复是否成功

运行测试脚本:
```powershell
cd C:\Users\shiguangping\data-agent\backend
python test_connection.py
```

**预期输出:**
```
============================================================
PostgreSQL Connection Test
============================================================

Testing: Default postgres database
  Host: localhost
  Port: 5432
  Database: postgres
  User: postgres
  Result: SUCCESS
  Version: PostgreSQL 18.1...

Testing: data_agent database
  Host: localhost
  Port: 5432
  Database: data_agent
  User: postgres
  Result: SUCCESS
  Version: PostgreSQL 18.1...
```

## 初始化数据库

连接成功后,运行:
```powershell
cd C:\Users\shiguangping\data-agent\backend
python scripts\init_chat_db.py
```

**预期输出:**
```
============================================================
初始化聊天功能数据库
============================================================

连接数据库: postgresql://postgres:***@localhost:5432/data_agent

1. 创建表结构...
✓ 表结构创建完成

2. 初始化默认LLM配置...
✓ 成功创建 4 个默认配置

默认配置列表:
  - 🤖 Claude Code (claude) - 启用 [默认]
  - 💬 ChatGPT (chatgpt) - 启用
  - ✨ Gemini (gemini) - 启用
  - 🌟 千问 (qwen) - 启用

============================================================
数据库初始化完成!
============================================================
```

## 常见问题

### Q1: 找不到 pg_hba.conf 文件
**A:** 检查这些位置:
- `C:\Program Files\PostgreSQL\18\data\pg_hba.conf`
- `C:\Program Files\PostgreSQL\data\pg_hba.conf`
- 或者在 pgAdmin 中查看服务器属性获取数据目录位置

### Q2: 无法编辑 pg_hba.conf (权限不足)
**A:**
1. 右键点击记事本 → **以管理员身份运行**
2. 在记事本中 File → Open
3. 浏览到 pg_hba.conf
4. 编辑并保存

### Q3: 重启服务失败
**A:**
1. 打开"服务"管理器: `services.msc`
2. 找到 `postgresql-x64-18`
3. 右键 → **重新启动**

### Q4: psql 命令找不到
**A:** 完整路径:
```powershell
"C:\Program Files\PostgreSQL\18\bin\psql.exe"
```

### Q5: 我确定密码是对的但还是失败
**A:** PostgreSQL 18 使用了更严格的认证。建议:
1. 使用方案 A 的自动化脚本
2. 或使用方案 B 的 pgAdmin 图形界面

### Q6: auto_setup_database.py 运行失败
**A:** 确保:
1. 以管理员身份运行
2. PostgreSQL 服务正在运行
3. 或者使用方案 B 的 pgAdmin 手动操作

## 需要更多帮助?

如果以上所有方案都失败,请提供以下信息:

1. **pgAdmin 能否连接?** 如果能,请用 pgAdmin 创建数据库即可
2. **安装 PostgreSQL 时的密码是什么?** 我们可以直接使用那个密码
3. **错误信息的完整内容** (运行 test_connection.py 的输出)

## 下一步

数据库初始化成功后,继续:
1. 配置 LLM API 密钥: 编辑 `backend/models/llm_config.py`
2. 启动后端服务: `python backend/main.py`
3. 启动前端服务: `cd frontend && npm run dev`
4. 访问: http://localhost:3000

查看完整指南: [QUICK_START_GUIDE.md](QUICK_START_GUIDE.md)
