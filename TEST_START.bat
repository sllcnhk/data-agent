@echo off
setlocal enabledelayedexpansion

echo ========================================
echo Data Agent System - Test Start
echo ========================================
echo.

if not exist logs mkdir logs

:: Start backend in foreground
echo [TEST] Starting backend service in foreground mode...
echo This will show any errors directly.
echo.
echo Press Ctrl+C if you see errors, then check logs directory
echo.
pause

cd /d "%~dp0"
echo Starting backend...
python run.py
echo.
echo Backend stopped. Check logs/backend.log for details.
echo.
pause
