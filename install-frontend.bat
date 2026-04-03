@echo off
echo ========================================
echo Data Agent System - Frontend Install
echo ========================================
echo.

echo [1/5] Checking Node.js...
node --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Node.js not found
    echo Please install Node.js 14+
    echo Download: https://nodejs.org/
    pause
    exit /b 1
)
echo OK: Node.js found

echo.
echo [2/5] Checking npm...
npm --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: npm not available
    pause
    exit /b 1
)
echo OK: npm found

echo.
echo [3/5] Entering frontend directory...
cd /d "%~dp0" && cd frontend
echo Current dir: %CD%

echo.
echo [4/5] Checking node_modules...
if exist node_modules (
    echo OK: node_modules already exists
    echo Checking if packages are up to date...
) else (
    echo WARNING: node_modules not found
    echo Installing dependencies...
)

echo.
echo [5/5] Installing/updating npm dependencies...
npm install

if errorlevel 1 (
    echo ERROR: Installation failed
    echo.
    echo Try running with administrator privileges
    pause
    exit /b 1
)

echo.
echo ========================================
echo SUCCESS: Frontend dependencies installed!
echo ========================================
echo.
pause
