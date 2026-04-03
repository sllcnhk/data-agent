#!/bin/bash

echo "========================================"
echo "数据智能分析Agent系统 - 停止所有服务"
echo "========================================"
echo ""

# 查找并终止Python进程
echo "[1/2] 停止后端服务..."
PYTHON_PIDS=$(pgrep -f "python.*main:app")
if [ -n "$PYTHON_PIDS" ]; then
    echo "终止Python进程: $PYTHON_PIDS"
    kill $PYTHON_PIDS 2>/dev/null
    sleep 1
    # 强制终止
    kill -9 $PYTHON_PIDS 2>/dev/null
else
    echo "未找到运行中的Python进程"
fi

# 查找并终止Node.js进程
echo "[2/2] 停止前端服务..."
NODE_PIDS=$(pgrep -f "node.*npm run dev")
if [ -n "$NODE_PIDS" ]; then
    echo "终止Node进程: $NODE_PIDS"
    kill $NODE_PIDS 2>/dev/null
    sleep 1
    # 强制终止
    kill -9 $NODE_PIDS 2>/dev/null
else
    echo "未找到运行中的Node进程"
fi

echo ""
echo "========================================"
echo "✓ 所有服务已停止"
echo "========================================"
echo ""
