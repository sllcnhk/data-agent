@echo off
echo ========================================
echo Fixing Proxy Settings for Pip
echo ========================================
echo.

echo [1/3] Clearing pip cache...
pip cache purge
echo OK: Cache cleared

echo.
echo [2/3] Configuring pip to bypass proxy...
pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple
pip config set global.trusted-host pypi.tuna.tsinghua.edu.cn
pip config set global.timeout 60
echo OK: Pip configured with mirror

echo.
echo [3/3] Testing connection...
python -m pip list >nul 2>&1
if errorlevel 1 (
    echo WARNING: Some issues detected
) else (
    echo OK: Pip is working
)

echo.
echo ========================================
echo Proxy Settings Fixed!
echo ========================================
echo.
echo Current pip configuration:
pip config list
echo.
echo You can now try installing again:
echo   install-backend.bat
echo.
pause
