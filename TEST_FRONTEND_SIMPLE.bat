@echo off
setlocal enabledelayedexpansion

echo ========================================
echo Test Frontend
echo ========================================
echo.

if not exist logs mkdir logs

echo [1/4] Checking Node.js...
node --version
if errorlevel 1 (
    echo ERROR: Node.js not found
    pause
    exit /b 1
)

echo.
echo [2/4] Checking npm...
npm --version
if errorlevel 1 (
    echo ERROR: npm not found
    pause
    exit /b 1
)

echo.
echo [3/4] Checking frontend directory...
cd /d "%~dp0" && cd frontend
if not exist package.json (
    echo ERROR: package.json not found
    echo Please run: install-frontend.bat
    pause
    exit /b 1
)
echo OK: package.json found

if not exist node_modules (
    echo WARNING: node_modules not found
    echo Installing dependencies...
    npm install
    if errorlevel 1 (
        echo ERROR: npm install failed
        pause
        exit /b 1
    )
)
echo OK: node_modules found

echo.
echo [4/4] Starting frontend dev server...
echo Starting on port 3000...
echo Open browser to: http://localhost:3000/
echo.
echo Press Ctrl+C to stop
echo ========================================
npm run dev

echo.
echo Frontend stopped
pause
