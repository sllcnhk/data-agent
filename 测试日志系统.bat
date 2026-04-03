@echo off
chcp 65001 >nul
title 测试前端日志系统

echo.
echo =============================================
echo   前端日志系统测试脚本
echo =============================================
echo.

echo [1/5] 检查前端日志服务文件...
if exist "frontend\src\services\logger.ts" (
    echo ✅ logger.ts 存在
) else (
    echo ❌ logger.ts 不存在
    pause
    exit /b 1
)

echo.
echo [2/5] 检查API拦截器文件...
if exist "frontend\src\services\api.ts" (
    echo ✅ api.ts 存在
) else (
    echo ❌ api.ts 不存在
    pause
    exit /b 1
)

echo.
echo [3/5] 检查日志查看组件...
if exist "frontend\src\components\LogsViewer.tsx" (
    echo ✅ LogsViewer.tsx 存在
) else (
    echo ❌ LogsViewer.tsx 不存在
    pause
    exit /b 1
)

echo.
echo [4/5] 检查日志页面...
if exist "frontend\src\pages\Logs.tsx" (
    echo ✅ Logs.tsx 存在
) else (
    echo ❌ Logs.tsx 不存在
    pause
    exit /b 1
)

echo.
echo [5/5] 检查文档文件...
if exist "前端日志功能使用指南.md" (
    echo ✅ 前端日志功能使用指南.md 存在
) else (
    echo ❌ 前端日志功能使用指南.md 不存在
    pause
    exit /b 1
)

echo.
echo =============================================
echo   所有文件检查通过！✅
echo =============================================
echo.
echo 日志系统功能包含:
echo   • 自动记录API请求/响应/错误
echo   • 本地存储(最多1000条)
echo   • 日志查看页面 (/logs)
echo   • 多维度筛选和搜索
echo   • 日志导出功能
echo   • 错误快速诊断
echo.
echo 现在可以启动系统进行测试:
echo   1. 运行 start-all.bat
echo   2. 访问 http://localhost:3000/logs
echo   3. 查看日志功能
echo.
pause
