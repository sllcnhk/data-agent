#!/bin/bash

echo "========================================"
echo "数据智能分析Agent系统 - 一键安装所有依赖"
echo "========================================"
echo ""

echo "[说明] 本脚本将安装后端和前端的所有依赖"
echo ""

# 询问是否继续
read -p "是否继续? (y/N): " -n 1 -r
echo ""
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    exit 0
fi

echo ""
echo "========================================"
echo "开始安装后端依赖"
echo "========================================"
echo ""

# 给install-backend.sh添加执行权限
chmod +x install-backend.sh
./install-backend.sh

if [ $? -ne 0 ]; then
    echo ""
    echo "错误: 后端依赖安装失败"
    exit 1
fi

echo ""
echo "========================================"
echo "开始安装前端依赖"
echo "========================================"
echo ""

# 给install-frontend.sh添加执行权限
chmod +x install-frontend.sh
./install-frontend.sh

if [ $? -ne 0 ]; then
    echo ""
    echo "错误: 前端依赖安装失败"
    exit 1
fi

echo ""
echo "========================================"
echo "✓ 所有依赖安装完成！"
echo "========================================"
echo ""
echo "下一步: 运行 ./start-all.sh 启动服务"
echo ""
