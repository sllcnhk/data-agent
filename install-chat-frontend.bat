@echo off
echo ========================================
echo 安装聊天功能前端依赖
echo ========================================
echo.

cd frontend

echo 安装 react-markdown...
call npm install react-markdown@^9.0.0

echo.
echo ========================================
echo 前端依赖安装完成!
echo ========================================
echo.
echo 提示:
echo 1. 确保后端已启动
echo 2. 运行 npm run dev 启动前端开发服务器
echo 3. 访问 http://localhost:5173
echo.

pause
