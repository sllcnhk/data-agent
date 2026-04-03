@echo off
echo ========================================
echo Data Agent System - Clear Logs
echo ========================================
echo.

if not exist logs (
    echo No logs directory found
    echo.
    pause
    exit /b 0
)

echo Current log files:
dir /b logs\*.log 2>nul
if errorlevel 1 (
    echo No log files to clear
    echo.
    pause
    exit /b 0
)

echo.
set /p choice="Are you sure you want to delete all log files? (Y/N): "
if /i not "%choice%"=="Y" (
    echo Operation cancelled
    echo.
    pause
    exit /b 0
)

echo.
echo Deleting log files...
del /q logs\*.log 2>nul

if exist logs\*.log (
    echo WARNING: Some log files could not be deleted
) else (
    echo SUCCESS: All log files deleted
)

echo.
pause
