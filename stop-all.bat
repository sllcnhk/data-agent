@echo off
chcp 65001 >nul
echo ========================================
echo 数据智能分析Agent系统 - 停止所有服务
echo ========================================
echo.

:: 查找并终止后端进程
echo [1/2] 停止后端服务...
for /f "tokens=2" %%i in ('tasklist /fi "imagename eq python.exe" ^| find "python"') do (
    echo 终止Python进程: %%i
    taskkill /pid %%i /f >nul 2>&1
)

:: 查找并终止Node.js进程
echo [2/2] 停止前端服务...
for /f "tokens=2" %%i in ('tasklist /fi "imagename eq node.exe" ^| find "node"') do (
    echo 终止Node进程: %%i
    taskkill /pid %%i /f >nul 2>&1
)

echo.
echo ========================================
echo ✓ 所有服务已停止
echo ========================================
echo.
pause
