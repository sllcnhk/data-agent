@echo off
echo ========================================
echo Data Agent System - View Logs
echo ========================================
echo.

if not exist logs (
    echo ERROR: Logs directory not found
    echo Run any start script first to generate logs
    echo.
    pause
    exit /b 1
)

echo Available log files:
echo.
dir /b logs\*.log 2>nul
if errorlevel 1 (
    echo No log files found
    echo.
    pause
    exit /b 1
)

echo.
echo ========================================
echo Select log file to view:
echo ========================================
echo.
echo [1] Start log (start-all.bat)
echo [2] Backend log (start-backend.bat)
echo [3] Frontend log (start-frontend.bat)
echo [4] Backend service log (backend.log)
echo [5] Frontend service log (frontend.log)
echo [0] Exit
echo.
set /p choice="Enter your choice (0-5): "

if "%choice%"=="1" (
    for /f %%i in ('dir /b /o-d logs\start-*.log 2^>nul ^| find /n /v "" ^| find "[1]:"') do set "file=%%i"
    for /f "delims=" %%i in ('dir /b /o-d logs\start-*.log 2^>nul') do set "file=%%i" & goto :view
    echo No start log found
    pause
    exit /b 1
) else if "%choice%"=="2" (
    for /f "delims=" %%i in ('dir /b /o-d logs\start-backend-*.log 2^>nul') do set "file=%%i" & goto :view
    echo No backend start log found
    pause
    exit /b 1
) else if "%choice%"=="3" (
    for /f "delims=" %%i in ('dir /b /o-d logs\start-frontend-*.log 2^>nul') do set "file=%%i" & goto :view
    echo No frontend start log found
    pause
    exit /b 1
) else if "%choice%"=="4" (
    if exist logs\backend.log (
        set "file=backend.log"
        goto :view
    )
    echo Backend service log not found
    pause
    exit /b 1
) else if "%choice%"=="5" (
    if exist logs\frontend.log (
        set "file=frontend.log"
        goto :view
    )
    echo Frontend service log not found
    pause
    exit /b 1
) else if "%choice%"=="0" (
    exit /b 0
) else (
    echo Invalid choice
    pause
    exit /b 1
)

:view
echo.
echo ========================================
echo Viewing: logs\%file%
echo ========================================
echo.
type "logs\%file%"
echo.
echo ========================================
echo End of log
echo ========================================
echo.
pause
