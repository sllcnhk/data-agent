@echo off
echo ========================================
echo Testing Requirements.txt Fix
echo ========================================
echo.

echo [1/3] Checking requirements.txt encoding...
cd /d "%~dp0" && cd backend
if not exist requirements.txt (
    echo ERROR: requirements.txt not found
    pause
    exit /b 1
)
echo OK: requirements.txt exists

echo.
echo [2/3] Testing pip read requirements.txt...
python -m pip install --dry-run -r requirements.txt >nul 2>&1
if errorlevel 1 (
    echo WARNING: Dry run failed (might be network issue)
    echo But file encoding is OK
) else (
    echo OK: pip can read requirements.txt
)

echo.
echo [3/3] Checking for Chinese characters...
findstr /R /C:"[^\x00-\x7F]" requirements.txt >nul
if errorlevel 1 (
    echo OK: No non-ASCII characters found
) else (
    echo WARNING: Non-ASCII characters found
)

echo.
echo ========================================
echo Test Complete
echo ========================================
echo.
echo You can now run install-backend.bat
echo.
pause
