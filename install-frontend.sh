#!/bin/bash

echo "========================================"
echo "数据智能分析Agent系统 - 前端依赖安装"
echo "========================================"
echo ""

# 检查Node.js是否安装
echo "[1/4] 检查Node.js环境..."
if ! command -v node &> /dev/null; then
    echo "错误: 未找到Node.js，请先安装Node.js 14或更高版本"
    echo "下载地址: https://nodejs.org/"
    exit 1
fi

node --version
echo "✓ Node.js环境检查通过"

# 检查npm是否可用
echo ""
echo "[2/4] 检查npm..."
if ! command -v npm &> /dev/null; then
    echo "错误: npm不可用"
    exit 1
fi

npm --version
echo "✓ npm检查通过"

# 进入前端目录
echo ""
echo "[3/4] 进入前端目录..."
cd "$(dirname "$0")/frontend"
echo "当前目录: $(pwd)"

# 安装依赖
echo ""
echo "[4/4] 安装npm依赖..."
npm install

if [ $? -ne 0 ]; then
    echo "错误: 依赖安装失败"
    exit 1
fi

echo ""
echo "========================================"
echo "✓ 前端依赖安装完成！"
echo "========================================"
echo ""
