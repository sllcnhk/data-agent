@echo off
setlocal enabledelayedexpansion

echo ========================================
echo Data Agent System - Frontend Start
echo ========================================

:: Create logs directory if not exists
if not exist logs mkdir logs

:: Get current timestamp
for /f "tokens=2-4 delims=/ " %%a in ('date /t') do set mydate=%%c-%%a-%%b
for /f "tokens=1-2 delims=: " %%a in ('time /t') do set mytime=%%a%%b
set logfile=logs\start-frontend-%mydate%_%mytime:.=%.log

cd /d "%~dp0" && cd frontend
echo Current dir: %CD%
echo Log file: %logfile%
echo.

echo [Check] Verifying Node.js...
node --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Node.js not found >> %logfile%
    echo ERROR: Node.js not found
    echo Please install Node.js first
    echo Log saved to: %logfile%
    pause
    exit /b 1
)
echo Node.js found >> %logfile%

echo [Check] Verifying dependencies...
if not exist node_modules (
    echo WARNING: Dependencies not installed >> %logfile%
    echo WARNING: Dependencies not installed
    echo Installing npm packages...
    npm install >> ../%logfile% 2>&1
    if errorlevel 1 (
        echo ERROR: Installation failed >> %logfile%
        echo ERROR: Installation failed
        echo Run install-frontend.bat manually
        echo Log saved to: %logfile%
        pause
        exit /b 1
    )
)

echo.
echo ========================================
echo OK: Environment verified
echo ========================================
echo.
echo Starting frontend service...
echo.
echo Access URLs:
echo   - Frontend: http://localhost:3000
echo   - API Proxy: http://localhost:3000/api (proxies to http://localhost:8000)
echo.
echo Press Ctrl+C to stop
echo Log file: %logfile%
echo ========================================
echo.

echo Starting frontend at %date% %time% >> ../%logfile%
npm run dev >> ../logs\frontend.log 2>&1
