@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

echo ============================================================
echo Data Agent - 修复验证脚本
echo ============================================================
echo.
echo 此脚本用于验证所有代码修复是否正确应用
echo.

set "PASS=0"
set "FAIL=0"

echo [检查 1/5] 验证 etl_design.py 类型注解修复...
findstr /C:"from typing import Dict, List, Any, Optional, Tuple" backend\skills\etl_design.py >nul 2>&1
if %errorLevel% equ 0 (
    findstr /C:"Tuple[pd.DataFrame, List[str]]" backend\skills\etl_design.py >nul 2>&1
    if %errorLevel% equ 0 (
        echo [OK] etl_design.py 类型注解已修复
        set /a PASS+=1
    ) else (
        echo [FAIL] etl_design.py 类型注解未完全修复
        set /a FAIL+=1
    )
) else (
    echo [FAIL] etl_design.py 缺少 Tuple 导入
    set /a FAIL+=1
)
echo.

echo [检查 2/5] 验证 conversation_format.py Literal 导入...
findstr /C:"from typing_extensions import Literal" backend\core\conversation_format.py >nul 2>&1
if %errorLevel% equ 0 (
    echo [OK] conversation_format.py Literal 导入已修复
    set /a PASS+=1
) else (
    echo [FAIL] conversation_format.py Literal 导入未修复
    set /a FAIL+=1
)
echo.

echo [检查 3/5] 验证 SkillType.DATA_PROCESSING 枚举值...
findstr /C:"DATA_PROCESSING" backend\skills\base.py >nul 2>&1
if %errorLevel% equ 0 (
    echo [OK] DATA_PROCESSING 枚举值已添加
    set /a PASS+=1
) else (
    echo [FAIL] DATA_PROCESSING 枚举值缺失
    set /a FAIL+=1
)
echo.

echo [检查 4/5] 验证 start-all.bat PYTHONPATH 设置...
findstr /C:"set PYTHONPATH=%%~dp0" start-all.bat >nul 2>&1
if %errorLevel% equ 0 (
    echo [OK] start-all.bat PYTHONPATH 已设置
    set /a PASS+=1
) else (
    echo [FAIL] start-all.bat PYTHONPATH 未设置
    set /a FAIL+=1
)
echo.

echo [检查 5/5] 验证环境设置脚本存在...
if exist "setup-environment.bat" (
    echo [OK] setup-environment.bat 已创建
    set /a PASS+=1
) else (
    echo [FAIL] setup-environment.bat 不存在
    set /a FAIL+=1
)
if exist "backend\requirements-py38.txt" (
    echo [OK] requirements-py38.txt 已创建
) else (
    echo [FAIL] requirements-py38.txt 不存在
    set /a FAIL+=1
)
echo.

echo ============================================================
echo 验证结果
echo ============================================================
echo 通过: %PASS%/5
echo 失败: %FAIL%/5
echo.

if %FAIL% equ 0 (
    echo [SUCCESS] 所有修复已正确应用！
    echo.
    echo 下一步：
    echo   1. 运行 setup-environment.bat 创建 Python 3.8 环境
    echo   2. 运行 start-all.bat 启动系统
    echo   3. 访问 http://localhost:3000
) else (
    echo [WARNING] 有 %FAIL% 项修复未成功应用
    echo.
    echo 请检查上述失败的项目
)

echo.
echo ============================================================
pause
