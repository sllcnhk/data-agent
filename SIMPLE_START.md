# 简化启动指南

## 问题诊断

如果您运行 `start-all.bat` 后无法访问 http://localhost:3000，请按以下步骤排查：

### 第一步：运行诊断
```batch
DIAGNOSE.bat
```

### 第二步：测试简化后端
```batch
TEST_SIMPLE_BACKEND.bat
```

如果后端能正常启动，说明后端依赖已安装，但原版代码有问题。

### 第三步：启动简化版服务
```batch
start-simple.bat
```

这将启动简化版后端（仅包含基本API）和前端。

---

## 简化版功能

### 后端API
- `GET /` - 返回系统信息
- `GET /health` - 健康检查
- `GET /api/docs` - API文档

### 前端
- 空白React应用（如果依赖已安装）

---

## 如果后端无法启动

### 检查Python依赖
```batch
python -c "import fastapi, uvicorn; print('OK')"
```

如果失败，重新安装：
```batch
install-backend.bat
```

### 检查Python路径
```batch
where python
```

如果没有输出，Python未添加到PATH。

### 检查端口占用
```batch
netstat -ano | findstr :8000
```

---

## 如果前端无法启动

### 检查Node.js
```batch
node --version
npm --version
```

### 安装前端依赖
```batch
install-frontend.bat
```

---

## 常见错误

### 1. "python不是内部或外部命令"
**解决**: 添加Python到PATH或使用完整路径启动

### 2. "端口已被占用"
**解决**: 关闭占用端口的程序
```batch
netstat -ano | findstr :8000
taskkill /pid <PID> /f
```

### 3. "npm不是内部或外部命令"
**解决**: 安装Node.js并添加到PATH

---

## 手动启动

### 后端
```batch
cd /d C:\path\to\data-agent
python run_simple.py
```

### 前端（在新窗口）
```batch
cd /d C:\path\to\data-agent\frontend
npm run dev
```

---

## 关于Dashboard

**这不是一个Chat页面！**

这是一个数据分析和Agent管理的控制台，包含：
- Agent状态统计
- 任务执行情况
- 系统健康状态
- Agent管理界面

### 主要页面：
- `/` 或 `/dashboard` - 仪表盘
- `/agents` - Agent管理
- `/tasks` - 任务管理
- `/skills` - 技能中心

---

## 文件说明

- `run.py` - 完整版后端（包含Agent系统）
- `run_simple.py` - 简化版后端（仅基本API）
- `start-all.bat` - 启动完整版
- `start-simple.bat` - 启动简化版
- `TEST_SIMPLE_BACKEND.bat` - 测试简化版后端

---

## 下一步

1. 如果简化版能启动，说明依赖正常
2. 可以逐步排查完整版代码问题
3. 查看日志文件了解详细错误
