# 修复 PostgreSQL 连接问题

## 问题诊断

您的 PostgreSQL 18 服务正在运行,但连接失败,原因是:**密码验证失败**

错误信息:`password authentication failed for user "postgres"`

## 解决方案

### 方案 1: 重置 PostgreSQL 密码(推荐)

#### 步骤 1: 修改认证方式为信任模式

1. 打开文件: `C:\Program Files\PostgreSQL\18\data\pg_hba.conf`

2. 找到以下行(通常在文件末尾):
   ```
   host    all             all             127.0.0.1/32            scram-sha-256
   ```

3. 临时修改为:
   ```
   host    all             all             127.0.0.1/32            trust
   ```

4. 保存文件

#### 步骤 2: 重启 PostgreSQL 服务

打开 PowerShell (管理员权限):
```powershell
Restart-Service postgresql-x64-18
```

或者使用服务管理器(services.msc)重启 `postgresql-x64-18` 服务

#### 步骤 3: 连接并重置密码

打开 CMD 或 PowerShell:
```powershell
cd "C:\Program Files\PostgreSQL\18\bin"

# 连接到PostgreSQL(现在不需要密码)
.\psql.exe -U postgres -h localhost

# 在psql提示符下执行:
ALTER USER postgres PASSWORD 'Sgp013013.';

# 退出
\q
```

#### 步骤 4: 恢复认证方式

1. 重新打开 `C:\Program Files\PostgreSQL\18\data\pg_hba.conf`

2. 将刚才修改的行改回:
   ```
   host    all             all             127.0.0.1/32            scram-sha-256
   ```

3. 保存文件

4. 再次重启 PostgreSQL 服务:
   ```powershell
   Restart-Service postgresql-x64-18
   ```

#### 步骤 5: 创建 data_agent 数据库

```powershell
cd "C:\Program Files\PostgreSQL\18\bin"

# 现在使用新密码连接
$env:PGPASSWORD="Sgp013013."
.\psql.exe -U postgres -h localhost -c "CREATE DATABASE data_agent;"

# 验证
.\psql.exe -U postgres -h localhost -c "\l" | findstr data_agent
```

### 方案 2: 确认当前密码(如果您记得安装时的密码)

如果您还记得安装 PostgreSQL 时设置的密码,请:

1. 更新 `.env` 文件中的密码:
   ```bash
   # 编辑 C:\Users\shiguangping\data-agent\.env
   POSTGRES_PASSWORD=您的实际密码
   ```

2. 使用该密码创建数据库:
   ```powershell
   cd "C:\Program Files\PostgreSQL\18\bin"
   $env:PGPASSWORD="您的实际密码"
   .\psql.exe -U postgres -h localhost -c "CREATE DATABASE data_agent;"
   ```

### 方案 3: 使用 pgAdmin 图形界面(最简单)

1. 打开 **pgAdmin 4** (随 PostgreSQL 安装)

2. 首次打开会要求设置 master password,随意设置

3. 左侧找到 **PostgreSQL 18** 服务器,双击连接

4. 输入您安装时设置的密码

5. 如果忘记密码:
   - 右键点击 **PostgreSQL 18** → Properties → Connection
   - 查看或修改密码

6. 连接成功后:
   - 右键点击 **Databases** → Create → Database
   - Database 名称: `data_agent`
   - Owner: `postgres`
   - 点击 Save

## 验证修复

完成以上步骤后,运行:

```powershell
cd C:\Users\shiguangping\data-agent\backend
python test_connection.py
```

应该看到:
```
Testing: Default postgres database
  Result: SUCCESS

Testing: data_agent database
  Result: SUCCESS
```

然后运行初始化脚本:
```powershell
python scripts\init_chat_db.py
```

## 常见问题

### Q: 找不到 pg_hba.conf 文件
**A**: 文件位置: `C:\Program Files\PostgreSQL\18\data\pg_hba.conf`
如果找不到,检查: `C:\Program Files\PostgreSQL\18\`

### Q: 无法编辑 pg_hba.conf (权限被拒绝)
**A**:
1. 右键点击记事本 → 以管理员身份运行
2. 在记事本中打开 pg_hba.conf
3. 编辑并保存

### Q: 重启服务失败
**A**:
1. 打开 services.msc
2. 找到 postgresql-x64-18
3. 右键 → 重新启动

### Q: 我确定密码是对的但还是连接失败
**A**:
可能是 PostgreSQL 18 的新特性。建议:
1. 使用方案 1 重置密码
2. 或使用方案 3 的 pgAdmin 图形界面

## 需要帮助?

如果以上方法都不行,请提供:
1. pgAdmin 能否成功连接?
2. PostgreSQL 安装时使用的密码是什么?
3. pg_hba.conf 文件的最后几行内容
