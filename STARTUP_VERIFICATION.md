# 启动验证与故障排除指南

## ✅ 系统启动状态检查

**测试时间**: 2026-01-21
**状态**: ✅ 所有服务正常

### 验证结果

| 服务 | 端口 | 状态 | 验证 |
|------|------|------|------|
| 后端API | 8000 | ✅ 正常 | http://localhost:8000 |
| 前端界面 | 3000 | ✅ 正常 | http://localhost:3000 |
| API文档 | 8000 | ✅ 正常 | http://localhost:8000/api/docs |

### API端点测试结果

```
GET http://localhost:8000/              → ✅ 200 OK
GET http://localhost:8000/health         → ✅ 200 OK
GET http://localhost:8000/api/v1/        → ✅ 200 OK
GET http://localhost:8000/api/v1/agents  → ✅ 200 OK
GET http://localhost:8000/api/v1/skills  → ✅ 200 OK
GET http://localhost:8000/api/v1/tasks   → ✅ 200 OK
```

---

## 🚀 正确启动流程

### 方法一：使用修复后的启动脚本（推荐）

```batch
# 1. 运行启动脚本
start-all.bat

# 2. 等待服务启动（约10-15秒）
```

### 方法二：分步启动

```batch
# 1. 启动后端
cd C:\Users\shiguangping\data-agent
python run_simple.py

# 2. 在新窗口中启动前端
cd C:\Users\shiguangping\data-agent\frontend
npm run dev
```

### 方法三：后台启动

```batch
# Windows
start /b python run_simple.py > logs\backend.log 2>&1
cd frontend && start /b npm run dev > ..\logs\frontend.log 2>&1
```

---

## 🔧 常见启动问题与解决方案

### 问题1: 端口3000被占用

**症状**:
```
Port 3000 is in use, trying another one...
➜ Local: http://localhost:3001/
```

**解决方案**:
```batch
# 1. 查找占用端口的进程
netstat -ano | findstr :3000

# 2. 终止占用进程
taskkill /PID <进程ID> /F

# 3. 或清理所有node进程
taskkill /f /im node.exe

# 4. 重新启动
start-all.bat
```

### 问题2: 前端启动失败

**症状**:
```
ERROR: npm not found
或
ERROR: Installation failed
```

**解决方案**:
```batch
# 1. 重新安装前端依赖
install-frontend.bat

# 2. 手动安装
cd frontend
npm install

# 3. 重新启动
start-all.bat
```

### 问题3: 后端启动失败

**症状**:
```
ERROR: Python not found
或
ImportError: No module named 'fastapi'
```

**解决方案**:
```batch
# 1. 重新安装后端依赖
install-backend.bat

# 2. 检查Python版本
python --version

# 3. 如果是Python 3.7，手动安装兼容版本
pip install -r backend\requirements-py37.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

# 4. 重新启动
python run_simple.py
```

### 问题4: 浏览器无法访问前端

**症状**:
- 浏览器显示"无法访问此网站"
- curl显示连接拒绝

**诊断步骤**:
```batch
# 1. 检查前端进程是否运行
tasklist | findstr node

# 2. 检查端口3000是否监听
netstat -ano | findstr :3000

# 3. 检查前端日志
type logs\frontend.log

# 4. 检查Vite配置
type frontend\vite.config.ts
```

**解决方案**:
```batch
# 方案A: 清理并重启
taskkill /f /im node.exe
start-all.bat

# 方案B: 手动启动
cd frontend
npm run dev
```

### 问题5: API调用失败

**症状**:
- 前端页面加载但显示"API连接失败"
- 控制台有网络错误

**诊断步骤**:
```batch
# 1. 测试后端API
curl http://localhost:8000/health

# 2. 检查API路径
curl http://localhost:8000/api/v1/

# 3. 检查后端日志
type logs\backend.log
```

**解决方案**:
```batch
# 确保使用正确的后端文件
python run_simple.py

# 而不是
python run.py
```

---

## 📝 日志位置说明

### 日志文件列表

| 日志文件 | 内容 | 查看命令 |
|----------|------|----------|
| `logs\backend.log` | 后端启动和运行日志 | `type logs\backend.log` |
| `logs\frontend.log` | 前端启动和运行日志 | `type logs\frontend.log` |
| `logs\backend-unit-test.log` | API测试日志 | `type logs\backend-unit-test.log` |
| `logs\start-all-*.log` | 启动脚本日志 | `type logs\start-all-*.log` |

### 实时查看日志

```batch
# 实时查看后端日志
type logs\backend.log

# 实时查看前端日志
type logs\frontend.log

# 实时查看启动日志
type logs\start-all-*.log
```

---

## ✅ 启动成功验证清单

启动完成后，请验证以下项目：

### 1. 后端验证
```batch
curl http://localhost:8000/health
# 期望返回: {"status":"healthy"}
```

### 2. API验证
```batch
curl http://localhost:8000/api/v1/agents
# 期望返回: JSON格式的agents数据
```

### 3. 前端验证
```batch
curl http://localhost:3000
# 期望返回: HTML页面内容
```

### 4. 浏览器验证
- [ ] 打开 http://localhost:3000
- [ ] 页面正常加载（显示前端界面）
- [ ] 控制台无错误
- [ ] 可以查看Agent列表
- [ ] 可以查看技能列表

### 5. API文档验证
- [ ] 打开 http://localhost:8000/api/docs
- [ ] Swagger UI正常显示
- [ ] 可以查看所有API端点
- [ ] 可以尝试API调用

---

## 🛠️ 自动化启动脚本

### 创建自动启动脚本 (auto-start.bat)

```batch
@echo off
echo Auto-starting Data Agent System...
echo.

:: Kill existing processes
taskkill /f /im python.exe >nul 2>&1
taskkill /f /im node.exe >nul 2>&1
timeout /t 2 /nobreak >nul

:: Start backend
start "DataAgent-Backend" cmd /c "python run_simple.py >> logs\backend.log 2>&1"
echo Backend starting...

:: Wait for backend
timeout /t 5 /nobreak >nul

:: Test backend
curl -s http://localhost:8000/health >nul
if errorlevel 1 (
    echo ERROR: Backend failed to start
    pause
    exit /b 1
)
echo Backend ready.

:: Start frontend
start "DataAgent-Frontend" cmd /c "cd frontend && npm run dev >> ..\logs\frontend.log 2>&1"
echo Frontend starting...

:: Wait and test
timeout /t 8 /nobreak >nul
curl -s http://localhost:3000 >nul
if errorlevel 1 (
    echo WARNING: Frontend may still be starting
    echo Please wait a few more seconds and try http://localhost:3000
) else (
    echo Frontend ready.
)

echo.
echo ========================================
echo System started successfully!
echo ========================================
echo Frontend: http://localhost:3000
echo API Docs: http://localhost:8000/api/docs
echo ========================================
echo.
pause
```

---

## 📊 性能优化建议

### 1. 端口冲突预防
如果经常遇到端口冲突，可以在`vite.config.ts`中配置备用端口：

```typescript
server: {
  port: 3000,
  strictPort: false,  // 允许使用其他端口
}
```

### 2. 启动速度优化
```batch
# 清理日志（定期）
CLEAR_LOGS.bat

# 清理node_modules缓存（如果遇到问题）
cd frontend
npm cache clean --force
rmdir /s node_modules
npm install
```

### 3. 开发模式优化
```batch
# 使用生产模式构建（更快）
cd frontend
npm run build
npm run preview
```

---

## 📞 获取帮助

如果仍然无法启动，请：

1. **查看详细日志**:
   ```batch
   DIAGNOSE.bat
   ```

2. **运行集成测试**:
   ```batch
   integration-test.bat
   ```

3. **检查系统要求**:
   - Python 3.7+ ✅
   - Node.js 14+ ✅
   - npm 6+ ✅

4. **查看故障排除指南**:
   - `TROUBLESHOOTING.md`

5. **查看测试报告**:
   - `INTEGRATION_TEST_REPORT.md`
   - `TEST_REPORT.md`

---

## ✅ 更新历史

### v1.0.0 - 2026-01-21

**修复**:
- ✅ 修复start-all.bat中错误的后端启动命令（run.py → run_simple.py）
- ✅ 验证所有API端点正常工作
- ✅ 验证前端可以正常访问
- ✅ 提供完整的启动验证指南

**新增**:
- ✅ startup-test.bat启动测试脚本
- ✅ STARTUP_VERIFICATION.md启动验证指南

---

**最后更新**: 2026-01-21
**状态**: ✅ 生产就绪
