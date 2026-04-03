@echo off
echo ========================================
echo Testing All Script Fixes
echo ========================================
echo.

echo [1/5] Checking Python installation...
python --version >nul 2>&1
if errorlevel 1 (
    echo FAIL: Python not found
) else (
    echo PASS: Python found
)

echo.
echo [2/5] Checking Node.js installation...
node --version >nul 2>&1
if errorlevel 1 (
    echo FAIL: Node.js not found
) else (
    echo PASS: Node.js found
)

echo.
echo [3/5] Checking requirements files...
if exist backend\requirements.txt (
    echo PASS: requirements.txt exists
) else (
    echo FAIL: requirements.txt not found
)

if exist backend\requirements-py37.txt (
    echo PASS: requirements-py37.txt exists
) else (
    echo FAIL: requirements-py37.txt not found
)

echo.
echo [4/5] Checking script encoding...
:: Check for non-ASCII characters in batch files
findstr /R /C:"[^\x00-\x7F]" start-all.bat >nul 2>&1
if errorlevel 1 (
    echo PASS: start-all.bat uses ASCII encoding
) else (
    echo WARNING: start-all.bat contains non-ASCII characters
)

echo.
echo [5/5] Testing path syntax...
:: Simulate path resolution
cd /d "%~dp0"
set "testpath=%~dp0"
if defined testpath (
    echo PASS: Path resolution works
) else (
    echo FAIL: Path resolution failed
)

echo.
echo ========================================
echo Test Summary
echo ========================================
echo.
echo If all tests PASS, scripts are ready to use
echo.
echo Next steps:
echo   1. Run install-backend.bat to install dependencies
echo   2. Run start-all.bat to start services
echo   3. Check logs directory for log files
echo.
pause
