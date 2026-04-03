@echo off
echo ========================================
echo Test Simple Backend
echo ========================================
echo.

cd /d "%~dp0"

echo [1/3] Checking Python...
python --version
if errorlevel 1 (
    echo ERROR: Python not found
    pause
    exit /b 1
)

echo.
echo [2/3] Testing FastAPI import...
python -c "from fastapi import FastAPI; print('OK: FastAPI imported successfully')"
if errorlevel 1 (
    echo ERROR: FastAPI not installed
    pause
    exit /b 1
)

echo.
echo [3/3] Starting simple backend...
echo This will show detailed output.
echo Open browser to: http://localhost:8000/
echo Press Ctrl+C to stop
echo.
python run_simple.py
