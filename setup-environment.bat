@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

echo ============================================================
echo Data Agent - Environment Setup Script
echo ============================================================
echo.

REM Check if conda is available
where conda >nul 2>&1
if %errorLevel% neq 0 (
    echo [ERROR] Conda not found
    echo Please install Anaconda or Miniconda first
    pause
    exit /b 1
)

echo [1/5] Checking for dataagent environment...
conda env list | findstr "dataagent" >nul 2>&1
if %errorLevel% equ 0 (
    echo [OK] dataagent environment exists
    echo.
    choice /C YN /M "Recreate environment (will delete existing)"
    if !errorLevel! equ 1 (
        echo [INFO] Removing existing environment...
        call conda deactivate 2>nul
        call conda env remove -n dataagent -y
        echo [OK] Environment removed
    ) else (
        echo [INFO] Using existing environment
        goto :install_deps
    )
)

echo [2/5] Creating Python 3.8 environment...
call conda create -n dataagent python=3.8 -y
if %errorLevel% neq 0 (
    echo [ERROR] Failed to create environment
    pause
    exit /b 1
)
echo [OK] Python 3.8 environment created
echo.

:install_deps
echo [3/5] Activating dataagent environment...
call conda activate dataagent
if %errorLevel% neq 0 (
    echo [ERROR] Failed to activate environment
    pause
    exit /b 1
)

echo [OK] Environment activated
python --version
echo.

echo [4/5] Installing backend dependencies...
cd /d "%~dp0backend"
pip install -r requirements-py38.txt
if %errorLevel% neq 0 (
    echo [WARNING] Some dependencies failed, trying original requirements...
    pip install -r requirements.txt
    if %errorLevel% neq 0 (
        echo [ERROR] Failed to install backend dependencies
        pause
        exit /b 1
    )
)
echo [OK] Backend dependencies installed
echo.

echo [5/5] Checking frontend dependencies...
cd /d "%~dp0frontend"
if not exist "node_modules" (
    echo [INFO] Installing frontend dependencies...
    call npm install
    if %errorLevel% neq 0 (
        echo [ERROR] Failed to install frontend dependencies
        pause
        exit /b 1
    )
    echo [OK] Frontend dependencies installed
) else (
    echo [OK] Frontend dependencies already installed
)
echo.

echo ============================================================
echo Environment Setup Complete!
echo ============================================================
echo.
echo Next steps:
echo   1. Run start-all.bat to start the system
echo   2. Or manually activate: conda activate dataagent
echo.
echo ============================================================
pause
