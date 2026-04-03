@echo off
echo ========================================
echo Quick Fix for Startup Issues
echo ========================================
echo.

echo This script will:
echo 1. Check if Python and Node.js are installed
echo 2. Test simple backend
echo 3. Start simplified services
echo.

pause

echo.
echo [1/5] Checking Python...
python --version
if errorlevel 1 (
    echo ERROR: Python not found
    echo Please install Python 3.7+ and add to PATH
    pause
    exit /b 1
)

echo.
echo [2/5] Checking Node.js...
node --version
if errorlevel 1 (
    echo ERROR: Node.js not found
    echo Please install Node.js 14+ and add to PATH
    pause
    exit /b 1
)

echo.
echo [3/5] Testing FastAPI...
python -c "from fastapi import FastAPI; print('OK')"
if errorlevel 1 (
    echo ERROR: FastAPI not installed
    echo Installing backend dependencies...
    install-backend.bat
)

echo.
echo [4/5] Checking frontend dependencies...
if not exist frontend\node_modules (
    echo Installing frontend dependencies...
    install-frontend.bat
)

echo.
echo [5/5] Starting simplified services...
echo.
start-simple.bat

echo.
echo Done!
echo If you still can't access http://localhost:3000
echo Please check logs directory for errors
echo.
pause
