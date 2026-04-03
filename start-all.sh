#!/bin/bash

echo "========================================"
echo "数据智能分析Agent系统 - 一键启动"
echo "========================================"
echo ""

# 检查Python
echo "[准备] 检查Python环境..."
if ! command -v python3 &> /dev/null; then
    if ! command -v python &> /dev/null; then
        echo "错误: 未找到Python，请先运行 ./install-backend.sh 安装后端依赖"
        exit 1
    fi
    PYTHON_CMD="python"
else
    PYTHON_CMD="python3"
fi

$PYTHON_CMD --version > /dev/null 2>&1
if [ $? -ne 0 ]; then
    echo "错误: Python不可用"
    exit 1
fi

# 检查Node.js
echo "[准备] 检查Node.js环境..."
if ! command -v node &> /dev/null; then
    echo "错误: 未找到Node.js，请先运行 ./install-frontend.sh 安装前端依赖"
    exit 1
fi

node --version > /dev/null 2>&1
if [ $? -ne 0 ]; then
    echo "错误: Node.js不可用"
    exit 1
fi

echo ""
echo "========================================"
echo "✓ 环境检查通过"
echo "========================================"
echo ""
echo "🚀 正在启动所有服务..."
echo ""

# 启动后端服务 (后台运行)
echo "[1/2] 启动后端服务..."
cd "$(dirname "$0")/backend"
if [ -f "run.py" ]; then
    nohup $PYTHON_CMD run.py > ../backend.log 2>&1 &
else
    nohup $PYTHON_CMD -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload > ../backend.log 2>&1 &
fi
BACKEND_PID=$!
echo "后端服务已启动 (PID: $BACKEND_PID)"

# 等待后端启动
sleep 3

# 启动前端服务
echo "[2/2] 启动前端服务..."
cd "$(dirname "$0")/frontend"
nohup npm run dev > ../frontend.log 2>&1 &
FRONTEND_PID=$!
echo "前端服务已启动 (PID: $FRONTEND_PID)"

echo ""
echo "========================================"
echo "✓ 所有服务已启动！"
echo "========================================"
echo ""
echo "访问地址:"
echo "  - 前端界面: http://localhost:3000"
echo "  - API文档: http://localhost:8000/api/docs"
echo ""
echo "注意:"
echo "  - 后端服务在端口 8000"
echo "  - 前端服务在端口 3000"
echo "  - 后端日志: backend.log"
echo "  - 前端日志: frontend.log"
echo ""
echo "停止服务:"
echo "  - 后端: kill $BACKEND_PID"
echo "  - 前端: kill $FRONTEND_PID"
echo "  - 或者直接按 Ctrl+C"
echo "========================================"
echo ""

# 等待用户按Ctrl+C
trap "echo ''; echo '正在停止服务...'; kill $BACKEND_PID $FRONTEND_PID 2>/dev/null; exit 0" INT

while true; do
    sleep 1
done
