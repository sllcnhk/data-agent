@echo off
echo ========================================
echo Minimal Backend Install (No Network Required)
echo ========================================
echo.
echo This script installs only the essential packages
echo for basic functionality. Use install-backend.bat
echo for full installation.
echo.
pause

echo.
echo [1/3] Checking Python...
python --version

echo.
echo [2/3] Installing minimal packages...
echo Installing using mirror...
pip install fastapi==0.68.0 uvicorn==0.15.0 pydantic==1.10.12 -i https://pypi.tuna.tsinghua.edu.cn/simple --trusted-host pypi.tuna.tsinghua.edu.cn

if errorlevel 1 (
    echo.
    echo Trying alternative mirror...
    pip install fastapi==0.68.0 uvicorn==0.15.0 pydantic==1.10.12 -i https://mirrors.aliyun.com/pypi/simple --trusted-host mirrors.aliyun.com
)

if errorlevel 1 (
    echo.
    echo ERROR: Installation failed
    echo Please run FIX_PROXY.bat first
    pause
    exit /b 1
)

echo.
echo [3/3] Verifying installation...
python -c "import fastapi, uvicorn, pydantic; print('OK: Packages installed')"

echo.
echo ========================================
echo Minimal installation complete!
echo ========================================
echo.
echo Note: This is a minimal installation
echo For full functionality, upgrade to Python 3.8+
echo.
pause
