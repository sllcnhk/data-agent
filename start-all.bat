@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

echo ============================================================
echo Data Agent System - Startup Script
echo ============================================================
echo.

REM Create logs directory if not exists
if not exist logs mkdir logs

REM Check and activate conda environment
echo [1/6] Checking Conda environment...
where conda >nul 2>&1
if %errorLevel% equ 0 (
    echo [INFO] Conda found, checking dataagent environment...
    conda env list | findstr "dataagent" >nul 2>&1
    if %errorLevel% equ 0 (
        echo [OK] Activating dataagent environment...
        call conda activate dataagent
        if %errorLevel% equ 0 (
            echo [OK] dataagent environment activated
        ) else (
            echo [WARNING] Failed to activate dataagent environment
            echo [INFO] Using system Python
        )
    ) else (
        echo [WARNING] dataagent environment not found
        echo [INFO] Please run setup from Anaconda Prompt first
        echo [INFO] Continuing with system Python...
    )
) else (
    echo [INFO] Conda not found, using system Python
)
python --version
echo.

REM Check Python version
echo [2/6] Checking Python version...
python -c "import sys; exit(0 if sys.version_info >= (3, 8) else 1)" >nul 2>&1
if %errorLevel% neq 0 (
    echo [ERROR] Python 3.8+ is required
    echo [ERROR] Current version:
    python --version
    echo.
    echo Please activate dataagent environment first in Anaconda Prompt
    pause
    exit /b 1
)
echo [OK] Python version compatible
echo.

REM Check Node.js
echo [3/6] Checking Node.js...
where node >nul 2>&1
if %errorLevel% neq 0 (
    echo [ERROR] Node.js not found in PATH
    echo Please install Node.js 18+ or add it to PATH
    pause
    exit /b 1
)
node --version
echo.

REM Check PostgreSQL service
echo [4/6] Checking PostgreSQL service...
sc query postgresql-x64-18 | find "RUNNING" >nul 2>&1
if %errorLevel% neq 0 (
    echo [WARNING] PostgreSQL service is not running
    echo Attempting to start...
    net start postgresql-x64-18 >nul 2>&1
    if %errorLevel% neq 0 (
        echo [ERROR] Could not start PostgreSQL service
        echo Please start it manually: services.msc
        pause
        exit /b 1
    )
    echo [OK] PostgreSQL service started
) else (
    echo [OK] PostgreSQL service is running
)
echo.

REM Check database initialization
echo [5/6] Checking database initialization...
cd /d "%~dp0backend"
python -c "from config.settings import settings; from sqlalchemy import create_engine, text; engine = create_engine(settings.get_database_url()); conn = engine.connect(); result = conn.execute(text('SELECT COUNT(*) FROM llm_configs')); count = result.scalar(); conn.close(); print(f'Found {count} LLM configs'); exit(0 if count > 0 else 1)" 2>nul

if %errorLevel% neq 0 (
    echo [ERROR] Database not initialized or connection failed
    echo.
    echo Please run database initialization first:
    echo   cd backend
    echo   python scripts\init_chat_db.py
    echo.
    echo Or check if PostgreSQL password is correct in .env file
    pause
    exit /b 1
)
echo [OK] Database connection successful
echo.

REM Check if frontend dependencies are installed
echo [6/6] Checking frontend dependencies...
cd /d "%~dp0frontend"
if not exist "node_modules" (
    echo [WARNING] Frontend dependencies not installed
    echo Installing dependencies... This may take a few minutes...
    call npm install
    if %errorLevel% neq 0 (
        echo [ERROR] Failed to install frontend dependencies
        pause
        exit /b 1
    )
)
echo [OK] Frontend dependencies ready
echo.

echo ============================================================
echo All Checks Passed - Starting Services
echo ============================================================
echo.
echo This will open 2 new terminal windows:
echo   [1] Backend Server  - http://localhost:8000
echo   [2] Frontend Server - http://localhost:3000
echo.
echo Press Ctrl+C in each window to stop services
echo.
pause

REM Get timestamp for logs
for /f "tokens=2-4 delims=/ " %%a in ('date /t') do set mydate=%%c%%a%%b
for /f "tokens=1-2 delims=: " %%a in ('time /t') do set mytime=%%a%%b
set timestamp=%mydate%_%mytime:.=%

REM Start backend in new window
echo [1/2] Starting backend server...
set "BACKEND_DIR=%~dp0backend"
where conda >nul 2>&1
if %errorLevel% equ 0 (
    conda env list | findstr "dataagent" >nul 2>&1
    if %errorLevel% equ 0 (
        start "Data Agent Backend" cmd /k "cd /d "%~dp0" && call conda activate dataagent && set PYTHONPATH=%~dp0 && echo ============================================================ && echo Backend Server Starting... && echo ============================================================ && echo. && python run.py"
    ) else (
        start "Data Agent Backend" cmd /k "cd /d "%~dp0" && set PYTHONPATH=%~dp0 && echo ============================================================ && echo Backend Server Starting... && echo ============================================================ && echo. && python run.py"
    )
) else (
    start "Data Agent Backend" cmd /k "cd /d "%~dp0" && set PYTHONPATH=%~dp0 && echo ============================================================ && echo Backend Server Starting... && echo ============================================================ && echo. && python run.py"
)

REM Wait for backend to start
echo Waiting 5 seconds for backend to initialize...
timeout /t 5 /nobreak >nul

REM Start frontend in new window
echo [2/2] Starting frontend dev server...
start "Data Agent Frontend" cmd /k "cd /d "%~dp0frontend" && echo ============================================================ && echo Frontend Dev Server Starting... && echo ============================================================ && echo. && npm run dev"

echo.
echo ============================================================
echo Services Started Successfully!
echo ============================================================
echo.
echo Access the application:
echo   Frontend:  http://localhost:3000
echo   Backend:   http://localhost:8000
echo   API Docs:  http://localhost:8000/docs
echo.
echo [IMPORTANT] Wait 10-15 seconds for services to fully start
echo Then open:  http://localhost:3000
echo.
echo To stop services:
echo   - Press Ctrl+C in each terminal window
echo   - Or close the terminal windows
echo.
echo ============================================================
pause
