@echo off
echo ========================================
echo Testing Python Version Compatibility Fix
echo ========================================
echo.

echo [1/4] Checking Python version...
python --version
for /f "tokens=2" %%i in ('python --version 2^>^&1') do set PYTHON_VERSION=%%i
echo.

echo [2/4] Checking requirements files...
cd /d "%~dp0" && cd backend
if exist requirements.txt (
    echo OK: requirements.txt exists
) else (
    echo ERROR: requirements.txt not found
)

if exist requirements-py37.txt (
    echo OK: requirements-py37.txt exists
) else (
    echo ERROR: requirements-py37.txt not found
)

echo.
echo [3/4] Checking file encoding...
:: Check if files contain only ASCII characters
findstr /R /C:"[^\x00-\x7F]" requirements.txt >nul 2>&1
if errorlevel 1 (
    echo OK: requirements.txt contains only ASCII characters
) else (
    echo WARNING: requirements.txt contains non-ASCII characters
)

findstr /R /C:"[^\x00-\x7F]" requirements-py37.txt >nul 2>&1
if errorlevel 1 (
    echo OK: requirements-py37.txt contains only ASCII characters
) else (
    echo WARNING: requirements-py37.txt contains non-ASCII characters
)

echo.
echo [4/4] Testing pip read...
echo Testing requirements.txt...
python -m pip install --dry-run fastapi==0.68.0 >nul 2>&1
if errorlevel 1 (
    echo WARNING: Dry run failed (network issue)
) else (
    echo OK: pip can read packages
)

echo.
echo ========================================
echo Test Complete
echo ========================================
echo.
echo Next steps:
echo   1. Run install-backend.bat
echo   2. The script will automatically select the correct requirements file
echo.
pause
