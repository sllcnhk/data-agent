@echo off
setlocal enabledelayedexpansion

echo ========================================
echo Debug Service Startup
echo ========================================
echo.

echo [1/8] Checking environment...
cd /d "%~dp0"
echo Current directory: %CD%
echo.

echo [2/8] Checking Python...
python --version
where python
if errorlevel 1 (
    echo ERROR: Python not found
    pause
    exit /b 1
)

echo.
echo [3/8] Checking Node.js...
node --version
where node
if errorlevel 1 (
    echo ERROR: Node.js not found
    pause
    exit /b 1
)

echo.
echo [4/8] Checking backend files...
if exist backend\main.py (
    echo OK: backend\main.py exists
) else (
    echo ERROR: backend\main.py not found
    pause
    exit /b 1
)

if exist run.py (
    echo OK: run.py exists
) else (
    echo ERROR: run.py not found
    pause
    exit /b 1
)

echo.
echo [5/8] Checking frontend files...
if exist frontend\package.json (
    echo OK: frontend\package.json exists
) else (
    echo ERROR: frontend\package.json not found
    pause
    exit /b 1
)

echo.
echo [6/8] Testing Python import...
python -c "import fastapi, uvicorn; print('OK: FastAPI and Uvicorn available')"
if errorlevel 1 (
    echo ERROR: FastAPI or Uvicorn not installed
    echo Please run install-backend.bat
    pause
    exit /b 1
)

echo.
echo [7/8] Testing Node modules...
if exist frontend\node_modules (
    echo OK: frontend\node_modules exists
) else (
    echo WARNING: frontend\node_modules not found
    echo Please run install-frontend.bat
)

echo.
echo [8/8] Ready to start services!
echo.
echo ========================================
echo Press any key to start backend service...
echo (This will show detailed output)
echo ========================================
pause

echo.
echo Starting backend service...
echo Press Ctrl+C to stop
echo.
python run.py
