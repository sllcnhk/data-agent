# 故障排除指南

## 启动成功但无法访问 http://localhost:3000

### 快速诊断

请运行以下脚本进行诊断：

```batch
DIAGNOSE.bat
```

### 详细排查步骤

#### 步骤 1: 检查服务是否真正启动

**检查后端服务**:
```batch
TEST_BACKEND_SIMPLE.bat
```

如果后端可以正常启动，说明依赖已安装，但可能是复杂代码导致的启动失败。

**检查前端服务**:
```batch
TEST_FRONTEND_SIMPLE.bat
```

#### 步骤 2: 检查端口占用

```batch
:: 检查端口 8000 (后端)
netstat -ano | findstr :8000

:: 检查端口 3000 (前端)
netstat -ano | findstr :3000
```

如果看到 `LISTENING` 或 `ESTABLISHED` 状态，说明服务已启动。

#### 步骤 3: 检查进程

```batch
:: 查看Python进程
tasklist /fi "imagename eq python.exe"

:: 查看Node进程
tasklist /fi "imagename eq node.exe"
```

#### 步骤 4: 检查防火墙

Windows 防火墙可能阻止了连接：
1. 打开 Windows 防火墙设置
2. 允许 Python.exe 通过防火墙
3. 允许 Node.exe 通过防火墙

#### 步骤 5: 检查浏览器

1. 清除浏览器缓存
2. 尝试访问：
   - http://127.0.0.1:3000
   - http://localhost:3000
   - http://192.168.1.x:3000 (您的局域网IP)

#### 步骤 6: 检查日志文件

查看日志目录：
```batch
dir /b logs\*.log
```

查看最新日志：
```batch
type logs\backend.log
type logs\frontend.log
```

---

## 可能的问题与解决方案

### 问题 1: 端口被占用

**现象**: 提示 "Port 3000 is already in use"

**解决方案**:
```batch
:: 查找占用端口的进程
netstat -ano | findstr :3000

:: 终止进程 (替换 <PID> 为实际PID)
taskkill /pid <PID> /f
```

### 问题 2: 后端启动失败

**现象**: 后端窗口立即关闭或报错

**解决方案**:
1. 运行 `TEST_BACKEND_SIMPLE.bat` 测试基本功能
2. 检查 Python 依赖是否完整安装：
   ```batch
   pip list | findstr fastapi
   ```
3. 重新安装后端依赖：
   ```batch
   install-backend.bat
   ```

### 问题 3: 前端启动失败

**现象**: 前端窗口报错或立即关闭

**解决方案**:
1. 检查 Node.js 是否正确安装
2. 清除 npm 缓存：
   ```batch
   npm cache clean --force
   ```
3. 重新安装前端依赖：
   ```batch
   install-frontend.bat
   ```

### 问题 4: 编码错误

**现象**: Python 报错 "UnicodeDecodeError"

**解决方案**:
1. 确保所有文件使用 UTF-8 或 ASCII 编码
2. 不要在路径中使用中文或特殊字符

### 问题 5: 代理错误

**现象**: pip 或 npm 安装失败

**解决方案**:
```batch
FIX_PROXY.bat
```

### 问题 6: 权限不足

**现象**: 提示 "Access Denied"

**解决方案**:
1. 以管理员身份运行命令提示符
2. 右键点击脚本 → "以管理员身份运行"

---

## 手动启动步骤

如果脚本仍有问题，可以手动启动：

### 启动后端

1. 打开命令提示符
2. 切换到项目目录：
   ```batch
   cd /d C:\path\to\data-agent
   ```
3. 激活虚拟环境（如果使用）：
   ```batch
   venv\Scripts\activate
   ```
4. 启动服务：
   ```batch
   cd backend
   python main.py
   ```

### 启动前端

1. 打开新的命令提示符窗口
2. 切换到项目目录：
   ```batch
   cd /d C:\path\to\data-agent
   ```
3. 进入前端目录：
   ```batch
   cd frontend
   ```
4. 安装依赖：
   ```batch
   npm install
   ```
5. 启动服务：
   ```batch
   npm run dev
   ```

---

## 验证服务状态

### 检查后端 API

在浏览器中访问：
- http://localhost:8000/ - 根路径
- http://localhost:8000/health - 健康检查
- http://localhost:8000/api/docs - API 文档

### 检查前端

在浏览器中访问：
- http://localhost:3000 - 前端界面

---

## 完整重置流程

如果所有方法都失败，可以尝试完整重置：

### 1. 清理环境

```batch
:: 删除 node_modules
cd frontend
rmdir /s /q node_modules
cd ..

:: 删除 Python 缓存
for /d /r . %%d in (__pycache__) do @if exist "%%d" rd /s /q "%%d"

:: 删除 logs
rmdir /s /q logs
```

### 2. 重新安装

```batch
:: 安装后端
install-backend.bat

:: 安装前端
install-frontend.bat
```

### 3. 重新启动

```batch
start-all.bat
```

---

## 联系支持

如果问题仍然存在，请提供以下信息：

1. 运行 `DIAGNOSE.bat` 的完整输出
2. `logs\backend.log` 的内容
3. `logs\frontend.log` 的内容
4. Python 版本 (`python --version`)
5. Node.js 版本 (`node --version`)
6. 操作系统版本

---

## 常用命令速查

```batch
:: 查看端口占用
netstat -ano | findstr :3000

:: 终止进程
taskkill /pid <PID> /f

:: 查看Python进程
tasklist /fi "imagename eq python.exe"

:: 查看Node进程
tasklist /fi "imagename eq node.exe"

:: 检查Python依赖
pip list | findstr fastapi

:: 检查npm包
npm list

:: 清除npm缓存
npm cache clean --force

:: 清除Python缓存
for /d /r . %%d in (__pycache__) do @if exist "%%d" rd /s /q "%%d"
```

---

**更新日期**: 2026-01-20
**版本**: 1.0.0
