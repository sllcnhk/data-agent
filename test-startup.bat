@echo off
setlocal enabledelayedexpansion

echo ========================================
echo Data Agent System - Startup Test
echo ========================================
echo.

:: Clean up any existing processes
echo [Step 1/5] Cleaning up existing processes...
taskkill /f /im python.exe >nul 2>&1
taskkill /f /im node.exe >nul 2>&1
timeout /t 2 /nobreak >nul
echo Cleaned up existing processes.
echo.

:: Wait a moment
timeout /t 3 /nobreak >nul

:: Start backend
echo [Step 2/5] Starting backend service...
echo Starting backend at %date% %time%
start "DataAgent-Backend-Test" cmd /c "cd /d \"%~dp0\" && python run_simple.py >> logs\backend-startup-test.log 2>&1"
echo Backend started. Waiting for it to be ready...
timeout /t 5 /nobreak >nul
echo.

:: Test backend
echo [Step 3/5] Testing backend API...
curl -s http://localhost:8000/health >nul 2>&1
if errorlevel 1 (
    echo ERROR: Backend not responding
    echo Check logs\backend-startup-test.log
    pause
    exit /b 1
) else (
    echo Backend is responding correctly.
)
echo.

:: Start frontend
echo [Step 4/5] Starting frontend service...
echo Starting frontend at %date% %time%
start "DataAgent-Frontend-Test" cmd /c "cd /d \"%~dp0\" && cd frontend && npm run dev >> logs\frontend-startup-test.log 2>&1"
echo Frontend started. Waiting for it to be ready...
timeout /t 8 /nobreak >nul
echo.

:: Test frontend
echo [Step 5/5] Testing frontend...
curl -s http://localhost:3000 >nul 2>&1
if errorlevel 1 (
    echo WARNING: Frontend may still be starting...
    echo Checking port 3001 (in case 3000 is in use)...
    curl -s http://localhost:3001 >nul 2>&1
    if errorlevel 1 (
        echo ERROR: Frontend not responding on port 3000 or 3001
        echo Check logs\frontend-startup-test.log
        echo.
        echo You can still access the API at: http://localhost:8000/api/docs
        pause
        exit /b 1
    ) else (
        echo Frontend is running on port 3001
        echo Please access: http://localhost:3001
    )
) else (
    echo Frontend is responding correctly on port 3000
)
echo.

echo ========================================
echo Startup Test Complete
echo ========================================
echo.
echo Access URLs:
echo   - Frontend: http://localhost:3000
echo   - API Docs: http://localhost:8000/api/docs
echo.
echo Logs:
echo   - Backend: logs\backend-startup-test.log
echo   - Frontend: logs\frontend-startup-test.log
echo.
pause
