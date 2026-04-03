#!/bin/bash

echo "========================================"
echo "数据智能分析Agent系统 - 后端服务启动"
echo "========================================"
echo ""

# 进入后端目录
cd "$(dirname "$0")/backend"
echo "当前目录: $(pwd)"
echo ""

# 检查依赖是否安装
echo "[检查] 验证Python环境..."
if ! command -v python3 &> /dev/null; then
    if ! command -v python &> /dev/null; then
        echo "错误: 未找到Python，请先运行 ./install-backend.sh 安装依赖"
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

echo "[检查] 验证依赖..."
if ! pip3 show fastapi &> /dev/null && ! pip show fastapi &> /dev/null; then
    echo "警告: 依赖可能未安装，尝试自动安装..."
    $PYTHON_CMD -m pip install fastapi uvicorn pandas numpy sqlalchemy pydantic
    if [ $? -ne 0 ]; then
        echo "错误: 依赖安装失败，请运行 ./install-backend.sh"
        exit 1
    fi
fi

echo ""
echo "========================================"
echo "✓ 环境检查通过"
echo "========================================"
echo ""
echo "🚀 正在启动后端服务..."
echo ""
echo "访问地址:"
echo "  - API文档: http://localhost:8000/api/docs"
echo "  - ReDoc文档: http://localhost:8000/api/redoc"
echo "  - 健康检查: http://localhost:8000/health"
echo ""
echo "按 Ctrl+C 停止服务"
echo "========================================"
echo ""

# 启动服务
if [ -f "run.py" ]; then
    $PYTHON_CMD run.py
else
    if command -v uvicorn &> /dev/null; then
        uvicorn main:app --host 0.0.0.0 --port 8000 --reload
    else
        $PYTHON_CMD -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
    fi
fi
