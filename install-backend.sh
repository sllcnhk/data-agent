#!/bin/bash

echo "========================================"
echo "数据智能分析Agent系统 - 后端依赖安装"
echo "========================================"
echo ""

# 检查Python是否安装
echo "[1/4] 检查Python环境..."
if ! command -v python3 &> /dev/null; then
    if ! command -v python &> /dev/null; then
        echo "错误: 未找到Python，请先安装Python 3.7或更高版本"
        exit 1
    fi
    PYTHON_CMD="python"
else
    PYTHON_CMD="python3"
fi

$PYTHON_CMD --version
echo "✓ Python环境检查通过"

# 检查pip是否可用
echo ""
echo "[2/4] 检查pip..."
if ! command -v pip3 &> /dev/null; then
    if ! command -v pip &> /dev/null; then
        echo "错误: pip不可用"
        exit 1
    fi
    PIP_CMD="pip"
else
    PIP_CMD="pip3"
fi

$PIP_CMD --version
echo "✓ pip检查通过"

# 进入后端目录
echo ""
echo "[3/4] 进入后端目录..."
cd "$(dirname "$0")/backend"
echo "当前目录: $(pwd)"

# 升级pip
echo ""
echo "[4/4] 升级pip并安装依赖..."
$PYTHON_CMD -m pip install --upgrade pip
echo ""

# 安装依赖
if [ -f "requirements.txt" ]; then
    $PIP_CMD install -r requirements.txt
    echo ""
    if [ $? -ne 0 ]; then
        echo "错误: 依赖安装失败"
        exit 1
    fi
else
    echo "警告: 未找到requirements.txt文件"
    echo "正在安装核心依赖..."
    $PIP_CMD install fastapi uvicorn pandas numpy sqlalchemy pydantic
fi

echo ""
echo "========================================"
echo "✓ 后端依赖安装完成！"
echo "========================================"
echo ""
