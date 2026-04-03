@echo off
setlocal enabledelayedexpansion

echo ========================================
echo 测试脚本 - 检查环境
echo ========================================
echo.

:: 显示当前目录
echo [1] 当前目录: %CD%

:: 检查是否存在bat文件
echo [2] 检查install-all.bat是否存在:
if exist "install-all.bat" (
    echo ✓ install-all.bat 文件存在
) else (
    echo ✗ install-all.bat 文件不存在
)

:: 检查是否存在install-backend.bat
echo [3] 检查install-backend.bat是否存在:
if exist "install-backend.bat" (
    echo ✓ install-backend.bat 文件存在
) else (
    echo ✗ install-backend.bat 文件不存在
)

:: 检查Python
echo [4] 检查Python:
python --version >nul 2>&1
if errorlevel 1 (
    echo ✗ Python未安装或未添加到PATH
) else (
    for /f "tokens=*" %%i in ('python --version 2^>^&1') do echo ✓ Python: %%i
)

:: 检查Node.js
echo [5] 检查Node.js:
node --version >nul 2>&1
if errorlevel 1 (
    echo ✗ Node.js未安装或未添加到PATH
) else (
    for /f "tokens=*" %%i in ('node --version 2^>^&1') do echo ✓ Node.js: %%i
)

:: 检查npm
echo [6] 检查npm:
npm --version >nul 2>&1
if errorlevel 1 (
    echo ✗ npm未安装或未添加到PATH
) else (
    for /f "tokens=*" %%i in ('npm --version 2^>^&1') do echo ✓ npm: %%i
)

echo.
echo ========================================
echo 测试完成
echo ========================================
echo.
pause
