@echo off
REM Data Agent One-Click Startup Script
REM This script must be run from Anaconda Prompt or it will activate the conda environment first

echo ============================================================
echo Data Agent - One-Click Startup
echo ============================================================
echo.

REM Check if we're in a conda environment
python --version 2>nul | findstr "3.8" >nul
if %errorLevel% equ 0 (
    echo [OK] Python 3.8 detected, environment is ready
    goto :start_services
)

echo [INFO] Attempting to activate dataagent environment...
where conda >nul 2>&1
if %errorLevel% neq 0 (
    echo [ERROR] Conda not found
    echo.
    echo Please run this script from Anaconda Prompt:
    echo   1. Open Anaconda Prompt from Start Menu
    echo   2. Run: conda activate dataagent
    echo   3. Run: cd C:\Users\shiguangping\data-agent
    echo   4. Run: start-all.bat
    echo.
    pause
    exit /b 1
)

call conda activate dataagent 2>nul
if %errorLevel% neq 0 (
    echo [ERROR] Failed to activate dataagent environment
    echo.
    echo Please ensure the environment is created:
    echo   conda create -n dataagent python=3.8 -y
    echo.
    echo Then run this script from Anaconda Prompt
    pause
    exit /b 1
)

:start_services
cd /d "%~dp0"
call start-all.bat
