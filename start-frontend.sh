#!/bin/bash

echo "========================================"
echo "数据智能分析Agent系统 - 前端服务启动"
echo "========================================"
echo ""

# 进入前端目录
cd "$(dirname "$0")/frontend"
echo "当前目录: $(pwd)"
echo ""

# 检查Node.js是否安装
echo "[检查] 验证Node.js环境..."
if ! command -v node &> /dev/null; then
    echo "错误: 未找到Node.js，请先安装Node.js"
    exit 1
fi

node --version > /dev/null 2>&1
if [ $? -ne 0 ]; then
    echo "错误: Node.js不可用"
    exit 1
fi

echo "[检查] 验证依赖..."
if [ ! -d "node_modules" ]; then
    echo "警告: 依赖未安装，尝试自动安装..."
    npm install
    if [ $? -ne 0 ]; then
        echo "错误: 依赖安装失败，请运行 ./install-frontend.sh"
        exit 1
    fi
fi

echo ""
echo "========================================"
echo "✓ 环境检查通过"
echo "========================================"
echo ""
echo "🚀 正在启动前端服务..."
echo ""
echo "访问地址:"
echo "  - 前端界面: http://localhost:3000"
echo "  - API代理: http://localhost:3000/api (代理到 http://localhost:8000)"
echo ""
echo "按 Ctrl+C 停止服务"
echo "========================================"
echo ""

# 启动服务
npm run dev
