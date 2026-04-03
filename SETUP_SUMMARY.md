# 数据库初始化设置总结

## 当前状态

✅ **已完成的配置:**
- `.env` 文件已创建,PostgreSQL密码设置为 `Sgp013013.`
- Python依赖已安装 (sqlalchemy, psycopg2-binary, pydantic等)
- 数据库初始化脚本已准备就绪
- Python 3.7 兼容性问题已修复
- SQLAlchemy metadata 命名冲突已解决

❌ **当前问题:**
- PostgreSQL 密码验证失败
- 无法连接到数据库
- `data_agent` 数据库尚未创建

## 下一步操作

### 选项 1: 自动化脚本(推荐)

**以管理员身份运行:**
```powershell
cd C:\Users\shiguangping\data-agent\backend
python auto_setup_database.py
```

脚本会自动完成所有设置步骤。

---

### 选项 2: 使用 pgAdmin 图形界面(最简单)

1. 打开 **pgAdmin 4** (开始菜单搜索)
2. 连接到 **PostgreSQL 18** (输入安装时的密码)
3. 如果忘记密码,在 pgAdmin 中重置:
   - Login/Group Roles → postgres → Properties → Definition
   - 设置新密码: `Sgp013013.`
4. 创建数据库:
   - 右键 Databases → Create → Database
   - 名称: `data_agent`
5. 运行初始化:
   ```powershell
   cd C:\Users\shiguangping\data-agent\backend
   python scripts\init_chat_db.py
   ```

---

### 选项 3: 手动命令行修复

详细步骤请查看: [POSTGRES_FIX_GUIDE.md](POSTGRES_FIX_GUIDE.md)

---

## 验证修复

运行测试脚本:
```powershell
cd C:\Users\shiguangping\data-agent\backend
python test_connection.py
```

成功输出应显示:
```
Testing: Default postgres database
  Result: SUCCESS

Testing: data_agent database
  Result: SUCCESS
```

## 初始化数据库

连接成功后运行:
```powershell
python scripts\init_chat_db.py
```

预期输出:
```
============================================================
初始化聊天功能数据库
============================================================

连接数据库: postgresql://postgres:***@localhost:5432/data_agent

1. 创建表结构...
✓ 表结构创建完成

2. 初始化默认LLM配置...
✓ 成功创建 4 个默认配置
```

## 已创建的辅助文件

1. **[POSTGRES_FIX_GUIDE.md](POSTGRES_FIX_GUIDE.md)** - 详细的连接问题修复指南
2. **[test_connection.py](backend/test_connection.py)** - 数据库连接测试脚本
3. **[auto_setup_database.py](backend/auto_setup_database.py)** - 自动化设置脚本
4. **[reset_postgres_password.bat](reset_postgres_password.bat)** - Windows批处理重置脚本

## 技术细节

### 已修复的问题

1. **Python 3.7 兼容性:**
   - 移除了 `str | None` 联合类型语法
   - 修改为 `Optional[str]` 或不带类型注解

2. **SQLAlchemy metadata 冲突:**
   - 将所有模型中的 `metadata` 字段重命名为 `extra_metadata`
   - 影响文件:
     - `backend/models/conversation.py`
     - `backend/models/task.py`
     - `backend/models/report.py`

3. **环境变量解析:**
   - `.env` 文件中的列表类型字段改为 JSON 格式:
     - `ALLOWED_DIRECTORIES=["C:/Users/shiguangping/offline_data"]`
     - `CORS_ORIGINS=["http://localhost:3000","http://localhost:3001","http://localhost:3000"]`
     - `ALLOWED_FILE_EXTENSIONS=[".csv",".xlsx",".xls",".json",".parquet",".txt"]`

4. **数据库连接配置:**
   - `init_chat_db.py` 现在使用 `settings.get_database_url()` 方法
   - 密码在日志中被隐藏显示为 `***`

### PostgreSQL 配置

- **版本:** PostgreSQL 18.1
- **服务名:** postgresql-x64-18
- **认证方式:** scram-sha-256
- **配置文件:** C:\Program Files\PostgreSQL\18\data\pg_hba.conf

## 常见问题

### Q: 为什么密码验证失败?
A: 可能是安装时设置的密码与 `.env` 中的不同。使用 pgAdmin 或自动化脚本重置密码。

### Q: 我可以使用不同的密码吗?
A: 可以。在 pgAdmin 中设置好密码后,更新 `.env` 文件中的 `POSTGRES_PASSWORD` 即可。

### Q: 数据库初始化失败怎么办?
A: 确保:
1. PostgreSQL 服务正在运行
2. 密码正确
3. `data_agent` 数据库已创建

### Q: Python版本太低怎么办?
A: 当前Python 3.7可以运行,但建议升级到Python 3.10+以获得完整功能。

## 下一步

数据库初始化成功后:

1. **配置LLM API密钥:**
   - 编辑 `backend/models/llm_config.py`
   - 填入您的 Claude/ChatGPT/Gemini API密钥

2. **启动系统:**

   **方式A: 一键启动(推荐)**
   ```powershell
   # 双击运行或命令行执行
   start-all.bat
   ```

   **方式B: 手动启动**
   ```powershell
   # 终端1: 启动后端
   cd C:\Users\shiguangping\data-agent\backend
   python main.py

   # 终端2: 启动前端
   cd C:\Users\shiguangping\data-agent\frontend
   npm run dev
   ```

3. **访问系统:**
   - 前端界面: http://localhost:3000
   - 后端API: http://localhost:8000
   - API文档: http://localhost:8000/docs

4. **查看完整文档:**
   - [QUICK_START_GUIDE.md](QUICK_START_GUIDE.md) - 快速开始指南
   - [README.md](README.md) - 项目概述

## 需要帮助?

如果遇到问题:
1. 查看 [POSTGRES_FIX_GUIDE.md](POSTGRES_FIX_GUIDE.md)
2. 运行 `python test_connection.py` 诊断连接问题
3. 检查 PostgreSQL 服务是否运行: `sc query postgresql-x64-18`

---

**祝您使用愉快! 🚀**
