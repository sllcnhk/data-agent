@echo off
setlocal enabledelayedexpansion

echo ========================================
echo Data Agent System - Backend Start
echo ========================================
echo.

:: Create logs directory if not exists
if not exist logs mkdir logs

:: Get current timestamp
for /f "tokens=2-4 delims=/ " %%a in ('date /t') do set mydate=%%c-%%a-%%b
for /f "tokens=1-2 delims=: " %%a in ('time /t') do set mytime=%%a%%b
set logfile=logs\start-backend-%mydate%_%mytime:.=%.log

cd /d "%~dp0"
echo Current dir: %CD%
echo Log file: %logfile%
echo.

echo [Check] Verifying Python...
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found >> %logfile%
    echo ERROR: Python not found
    echo Run install-backend.bat first
    echo Log saved to: %logfile%
    pause
    exit /b 1
)
echo Python found >> %logfile%

echo [Check] Verifying dependencies...
pip show fastapi >nul 2>&1
if errorlevel 1 (
    echo WARNING: Dependencies may not be installed >> %logfile%
    echo WARNING: Dependencies may not be installed
    echo Installing from requirements.txt...
    cd /d "%~dp0" && cd backend
    pip install -r requirements.txt >> ../%logfile% 2>&1
    if errorlevel 1 (
        echo ERROR: Installation failed >> %logfile%
        echo ERROR: Installation failed
        echo Run install-backend.bat manually
        echo Log saved to: %logfile%
        pause
        exit /b 1
    )
    cd /d "%~dp0"
)

echo.
echo ========================================
echo OK: Environment verified
echo ========================================
echo.
echo Starting backend service...
echo.
echo Access URLs:
echo   - API Docs: http://localhost:8000/api/docs
echo   - ReDoc: http://localhost:8000/api/redoc
echo   - Health: http://localhost:8000/health
echo.
echo Press Ctrl+C to stop
echo Log file: %logfile%
echo ========================================
echo.

echo Starting backend at %date% %time% >> %logfile%
python run.py >> logs\backend.log 2>&1
