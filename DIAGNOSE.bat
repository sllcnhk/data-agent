@echo off
setlocal enabledelayedexpansion

echo ========================================
echo Data Agent System - Diagnose
echo ========================================
echo.

:: Create diag directory
if not exist diag mkdir diag

:: Get timestamp
for /f "tokens=2-4 delims=/ " %%a in ('date /t') do set mydate=%%c-%%a-%%b
for /f "tokens=1-2 delims=: " %%a in ('time /t') do set mytime=%%a%%b
set diagfile=diag\diagnose-%mydate%_%mytime:.=%.txt

echo Starting diagnosis at %date% %time% > %diagfile%
echo. >> %diagfile%

echo [1/10] Checking Python installation...
python --version >> %diagfile% 2>&1
python --version
if errorlevel 1 (
    echo FAIL: Python not found >> %diagfile%
    echo FAIL: Python not found
) else (
    echo PASS: Python found >> %diagfile%
    echo PASS: Python found
)
echo. >> %diagfile%

echo [2/10] Checking Node.js installation...
node --version >> %diagfile% 2>&1
node --version
if errorlevel 1 (
    echo FAIL: Node.js not found >> %diagfile%
    echo FAIL: Node.js not found
) else (
    echo PASS: Node.js found >> %diagfile%
    echo PASS: Node.js found
)
echo. >> %diagfile%

echo [3/10] Checking npm installation...
npm --version >> %diagfile% 2>&1
npm --version
if errorlevel 1 (
    echo FAIL: npm not found >> %diagfile%
    echo FAIL: npm not found
) else (
    echo PASS: npm found >> %diagfile%
    echo PASS: npm found
)
echo. >> %diagfile%

echo [4/10] Checking port 8000...
netstat -ano | findstr :8000 >> %diagfile% 2>&1
netstat -ano | findstr :8000
if errorlevel 1 (
    echo INFO: Port 8000 is free >> %diagfile%
    echo INFO: Port 8000 is free
) else (
    echo WARNING: Port 8000 is in use >> %diagfile%
    echo WARNING: Port 8000 is in use
)
echo. >> %diagfile%

echo [5/10] Checking port 3000...
netstat -ano | findstr :3000 >> %diagfile% 2>&1
netstat -ano | findstr :3000
if errorlevel 1 (
    echo INFO: Port 3000 is free >> %diagfile%
    echo INFO: Port 3000 is free
) else (
    echo WARNING: Port 3000 is in use >> %diagfile%
    echo WARNING: Port 3000 is in use
)
echo. >> %diagfile%

echo [6/10] Checking Python processes...
tasklist /fi "imagename eq python.exe" >> %diagfile% 2>&1
tasklist /fi "imagename eq python.exe"
if errorlevel 1 (
    echo INFO: No Python processes running >> %diagfile%
    echo INFO: No Python processes running
)
echo. >> %diagfile%

echo [7/10] Checking Node processes...
tasklist /fi "imagename eq node.exe" >> %diagfile% 2>&1
tasklist /fi "imagename eq node.exe"
if errorlevel 1 (
    echo INFO: No Node processes running >> %diagfile%
    echo INFO: No Node processes running
)
echo. >> %diagfile%

echo [8/10] Checking backend directory...
if exist backend\main.py (
    echo PASS: backend\main.py exists >> %diagfile%
    echo PASS: backend\main.py exists
) else (
    echo FAIL: backend\main.py not found >> %diagfile%
    echo FAIL: backend\main.py not found
)

if exist backend\requirements.txt (
    echo PASS: backend\requirements.txt exists >> %diagfile%
    echo PASS: backend\requirements.txt exists
) else (
    echo FAIL: backend\requirements.txt not found >> %diagfile%
    echo FAIL: backend\requirements.txt not found
)
echo. >> %diagfile%

echo [9/10] Checking frontend directory...
if exist frontend\package.json (
    echo PASS: frontend\package.json exists >> %diagfile%
    echo PASS: frontend\package.json exists
) else (
    echo FAIL: frontend\package.json not found >> %diagfile%
    echo FAIL: frontend\package.json not found
)

if exist frontend\node_modules (
    echo PASS: frontend\node_modules exists >> %diagfile%
    echo PASS: frontend\node_modules exists
) else (
    echo WARNING: frontend\node_modules not found >> %diagfile%
    echo WARNING: frontend\node_modules not found - run install-frontend.bat
)
echo. >> %diagfile%

echo [10/10] Checking logs directory...
if exist logs (
    echo PASS: logs directory exists >> %diagfile%
    echo PASS: logs directory exists
    dir /b logs\*.log >> %diagfile% 2>&1
) else (
    echo WARNING: logs directory not found >> %diagfile%
    echo WARNING: logs directory not found
)
echo. >> %diagfile%

echo ========================================
echo Diagnosis Complete!
echo ========================================
echo.
echo Diagnostic file: %diagfile%
echo.
echo Press any key to view the full diagnostic report...
pause >nul

echo.
type %diagfile%
echo.
pause
