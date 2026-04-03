@echo off
setlocal enabledelayedexpansion

echo ========================================
echo Data Agent System - Simple Start
echo ========================================
echo.

if not exist logs mkdir logs

echo [Check] Starting simple backend server...
echo Starting simple backend at %date% %time% >> logs\simple-start.log
echo Log file: logs\simple-start.log
echo.

echo Starting backend service (simple mode)...
start "DataAgent-Backend-Simple" cmd /k "cd /d \"%~dp0\" && python run_simple.py >> logs\backend-simple.log 2>&1"

timeout /t 3 /nobreak >nul

echo Starting frontend service...
cd /d "%~dp0" && cd frontend
start "DataAgent-Frontend-Simple" cmd /k "cd /d \"%~dp0\" && cd frontend && npm run dev >> ../logs\frontend-simple.log 2>&1"

echo.
echo ========================================
echo SUCCESS: Services started (simple mode)!
echo ========================================
echo.
echo Backend: http://localhost:8000
echo Frontend: http://localhost:3000
echo API Docs: http://localhost:8000/api/docs
echo.
echo Logs:
echo   - logs\backend-simple.log
echo   - logs\frontend-simple.log
echo ========================================
echo.
pause
