# 启动脚本使用说明

本项目提供了一系列脚本，帮助您快速安装依赖和启动服务。支持 Windows (.bat) 和 Linux/Mac (.sh)。

## 脚本列表

### 📦 安装依赖脚本

#### Windows
```bash
install-all.bat        # 一键安装所有依赖
install-backend.bat    # 仅安装后端依赖
install-frontend.bat   # 仅安装前端依赖
```

#### Linux/Mac
```bash
chmod +x install-all.sh
./install-all.sh

chmod +x install-backend.sh
./install-backend.sh

chmod +x install-frontend.sh
./install-frontend.sh
```

### 🚀 启动服务脚本

#### Windows
```bash
start-all.bat         # 一键启动前后端服务
start-backend.bat      # 仅启动后端服务
start-frontend.bat     # 仅启动前端服务
stop-all.bat          # 停止所有服务
```

#### Linux/Mac
```bash
chmod +x start-all.sh
./start-all.sh

chmod +x start-backend.sh
./start-backend.sh

chmod +x start-frontend.sh
./start-frontend.sh

chmod +x stop-all.sh
./stop-all.sh
```

## 快速开始

### 方法一：一键操作（推荐）

#### Windows用户
```bash
# 1. 安装所有依赖
install-all.bat

# 2. 启动所有服务
start-all.bat

# 3. 停止所有服务
stop-all.bat
```

#### Linux/Mac用户
```bash
# 1. 给脚本添加执行权限
chmod +x *.sh

# 2. 安装所有依赖
./install-all.sh

# 3. 启动所有服务
./start-all.sh

# 4. 停止所有服务
./stop-all.sh
```

### 方法二：分别操作

#### 后端服务
```bash
# 安装依赖
install-backend.bat
# 或 Linux/Mac:
chmod +x install-backend.sh
./install-backend.sh

# 启动服务
start-backend.bat
# 或 Linux/Mac:
chmod +x start-backend.sh
./start-backend.sh
```

#### 前端服务
```bash
# 安装依赖
install-frontend.bat
# 或 Linux/Mac:
chmod +x install-frontend.sh
./install-frontend.sh

# 启动服务
start-frontend.bat
# 或 Linux/Mac:
chmod +x start-frontend.sh
./start-frontend.sh
```

## 详细说明

### 1. install-backend / install-backend.bat

**功能**: 安装Python后端依赖

**检查项**:
- Python 3.7+ 是否安装
- pip 是否可用

**安装的依赖**:
- fastapi
- uvicorn
- pandas
- numpy
- sqlalchemy
- pydantic

### 2. install-frontend / install-frontend.bat

**功能**: 安装Node.js前端依赖

**检查项**:
- Node.js 14+ 是否安装
- npm 是否可用

**安装的依赖**:
- react
- typescript
- antd
- axios
- recharts
- 等等 (详见 frontend/package.json)

### 3. start-backend / start-backend.bat

**功能**: 启动后端服务

**默认配置**:
- 端口: 8000
- 主机: 0.0.0.0
- 热重载: 启用

**访问地址**:
- http://localhost:8000/api/docs - Swagger API文档
- http://localhost:8000/api/redoc - ReDoc API文档
- http://localhost:8000/health - 健康检查

**退出方式**:
- 按 Ctrl+C

### 4. start-frontend / start-frontend.bat

**功能**: 启动前端服务

**默认配置**:
- 端口: 3000
- 主机: localhost
- API代理: /api → http://localhost:8000

**访问地址**:
- http://localhost:3000 - 前端界面

**退出方式**:
- 按 Ctrl+C

### 5. start-all / start-all.bat

**功能**: 一键启动前后端服务

**执行流程**:
1. 检查Python和Node.js环境
2. 启动后端服务 (独立窗口/后台)
3. 等待3秒
4. 启动前端服务 (独立窗口/后台)

**Windows特殊行为**:
- 后端和前端分别在独立窗口中运行
- 可以单独关闭某个窗口

**Linux/Mac特殊行为**:
- 服务在后台运行
- 生成 backend.log 和 frontend.log 日志文件
- 使用 Ctrl+C 停止服务

### 6. stop-all / stop-all.bat

**功能**: 停止所有服务

**执行操作**:
- 终止所有Python进程
- 终止所有Node.js进程

## 环境要求

### 必需软件

#### Windows
- **Python 3.7+**
  - 下载: https://www.python.org/downloads/
  - 安装时勾选 "Add Python to PATH"

- **Node.js 14+**
  - 下载: https://nodejs.org/
  - 推荐 LTS 版本

#### Linux/Mac
```bash
# Ubuntu/Debian
sudo apt-get install python3 python3-pip nodejs npm

# CentOS/RHEL
sudo yum install python3 python3-pip nodejs npm

# macOS (使用 Homebrew)
brew install python3 node
```

### 验证安装

```bash
# 检查Python
python --version
# 或
python3 --version

# 检查pip
pip --version
# 或
pip3 --version

# 检查Node.js
node --version

# 检查npm
npm --version
```

## 常见问题

### Q: 脚本无法执行（权限不足）

**Windows**: 右键点击脚本 → "以管理员身份运行"

**Linux/Mac**:
```bash
# 添加执行权限
chmod +x *.sh

# 如果仍有问题，使用sudo
sudo ./install-all.sh
```

### Q: Python/pip 命令不可用

**解决方案**:
1. 确认Python已正确安装
2. 将Python添加到系统PATH环境变量
3. 重启终端/命令行

### Q: Node.js/npm 命令不可用

**解决方案**:
1. 确认Node.js已正确安装
2. 将Node.js添加到系统PATH环境变量
3. 重启终端/命令行

### Q: 依赖安装失败

**可能原因**:
- 网络问题
- 权限不足
- Python/pip版本过低

**解决方案**:
```bash
# 升级pip
python -m pip install --upgrade pip

# 使用国内镜像源
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

### Q: 端口被占用

**解决方案**:

Windows:
```bash
# 查找占用端口的进程
netstat -ano | findstr :8000
netstat -ano | findstr :3000

# 终止进程
taskkill /pid <PID> /f
```

Linux/Mac:
```bash
# 查找占用端口的进程
lsof -i :8000
lsof -i :3000

# 终止进程
kill -9 <PID>
```

### Q: 前端无法访问后端API

**可能原因**:
1. 后端服务未启动
2. 端口配置不匹配
3. 防火墙拦截

**解决方案**:
1. 确认后端在端口8000正常运行
2. 检查前端vite.config.ts中的代理配置
3. 关闭防火墙或添加例外

## 自定义配置

### 修改后端端口

编辑 `backend/main.py` 或 `backend/run.py`:
```python
if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=9000,  # 修改为您想要的端口
        reload=True
    )
```

### 修改前端端口

编辑 `frontend/package.json`:
```json
{
  "scripts": {
    "dev": "vite --port 4000"
  }
}
```

或编辑 `frontend/vite.config.ts`:
```typescript
export default defineConfig({
  server: {
    port: 4000
  }
})
```

## 最佳实践

### 1. 首次使用
```bash
# 1. 安装所有依赖
./install-all.sh  # 或 install-all.bat

# 2. 启动服务
./start-all.sh  # 或 start-all.bat

# 3. 访问 http://localhost:3000
```

### 2. 日常开发
```bash
# 仅启动后端（用于API开发）
./start-backend.sh

# 仅启动前端（用于UI开发）
./start-frontend.sh
```

### 3. 停止服务
```bash
# 一键停止所有服务
./stop-all.sh  # 或 stop-all.bat

# 或在前端和后端窗口按 Ctrl+C
```

## 目录结构

```
data-agent/
├── install-all.bat          # Windows: 一键安装所有依赖
├── install-all.sh           # Linux/Mac: 一键安装所有依赖
├── install-backend.bat       # Windows: 安装后端依赖
├── install-backend.sh        # Linux/Mac: 安装后端依赖
├── install-frontend.bat      # Windows: 安装前端依赖
├── install-frontend.sh       # Linux/Mac: 安装前端依赖
├── start-all.bat            # Windows: 一键启动所有服务
├── start-all.sh            # Linux/Mac: 一键启动所有服务
├── start-backend.bat        # Windows: 启动后端服务
├── start-backend.sh         # Linux/Mac: 启动后端服务
├── start-frontend.bat       # Windows: 启动前端服务
├── start-frontend.sh        # Linux/Mac: 启动前端服务
├── stop-all.bat            # Windows: 停止所有服务
├── stop-all.sh             # Linux/Mac: 停止所有服务
├── backend/                 # 后端代码
└── frontend/                # 前端代码
```

## 技术支持

如果遇到问题：
1. 查看本使用说明
2. 检查系统要求
3. 查看项目README.md
4. 提交Issue到项目仓库

## 更新日志

### v1.0.0
- 初始版本
- 支持一键安装和启动
- 支持Windows和Linux/Mac
- 提供完整的启动脚本集合

---

**祝您使用愉快！** 🎉
