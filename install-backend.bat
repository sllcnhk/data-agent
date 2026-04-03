@echo off
echo ========================================
echo Data Agent System - Backend Install
echo ========================================
echo.

echo [1/7] Checking Python...
python --version
echo.

for /f "tokens=2" %%i in ('python --version 2^>^&1') do set PYTHON_VERSION=%%i
echo Detected Python version: %PYTHON_VERSION%

echo.
echo [2/7] Checking pip...
pip --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: pip not available
    pause
    exit /b 1
)
echo OK: pip found

echo.
echo [3/7] Entering backend directory...
cd /d "%~dp0" && cd backend
echo Current dir: %CD%

if not exist requirements.txt (
    echo ERROR: requirements.txt not found
    echo Please ensure you are in the correct directory
    pause
    exit /b 1
)

echo.
echo [4/7] Determining compatible requirements file...

:: Extract major and minor version
for /f "tokens=1,2 delims=." %%a in ("%PYTHON_VERSION%") do (
    set MAJOR=%%a
    set MINOR=%%b
)

echo Python version: %MAJOR%.%MINOR%

:: Check if Python 3.7 or lower
if %MAJOR% LSS 3 (
    echo ERROR: Python 2.x is not supported
    pause
    exit /b 1
)

if %MINOR% LSS 7 (
    echo ERROR: Python 3.6 and below are not supported
    echo Please install Python 3.7 or higher
    pause
    exit /b 1
)

if %MINOR% EQU 7 (
    echo.
    echo WARNING: Python 3.7 detected
    echo Using requirements-py37.txt for compatibility
    set REQUIREMENTS_FILE=requirements-py37.txt
) else (
    echo.
    echo Python 3.8+ detected
    echo Using requirements.txt
    set REQUIREMENTS_FILE=requirements.txt
)

if not exist "%REQUIREMENTS_FILE%" (
    echo ERROR: %REQUIREMENTS_FILE% not found
    pause
    exit /b 1
)

echo Using: %REQUIREMENTS_FILE%

echo.
echo [5/7] Configuring pip settings...
:: Try to install without proxy first
echo Configuring pip to skip proxy...
pip config set global.trusted-host "pypi.org pypi.python.org files.pythonhosted.org" >nul 2>&1
echo OK: Pip configured

echo.
echo [6/7] Upgrading pip...
echo Upgrading pip using mirror...
python -m pip install --upgrade pip -i https://pypi.tuna.tsinghua.edu.cn/simple --trusted-host pypi.tuna.tsinghua.edu.cn
if errorlevel 1 (
    echo WARNING: pip upgrade failed, continuing anyway...
)

echo.
echo [7/7] Installing dependencies...
echo.
echo ========================================
echo Using mirror: https://pypi.tuna.tsinghua.edu.cn/simple
echo ========================================
echo.
echo Installing from %REQUIREMENTS_FILE%...
echo This may take several minutes...
echo.

pip install -r %REQUIREMENTS_FILE% -i https://pypi.tuna.tsinghua.edu.cn/simple --trusted-host pypi.tuna.tsinghua.edu.cn

if errorlevel 1 (
    echo.
    echo ========================================
    echo ERROR: Installation failed
    echo ========================================
    echo.
    echo Possible solutions:
    echo.
    echo Solution 1: Use alternative mirror
    pip install -r %REQUIREMENTS_FILE% -i https://mirrors.aliyun.com/pypi/simple --trusted-host mirrors.aliyun.com
    if errorlevel 1 (
        echo.
        echo Solution 2: Use another mirror
        pip install -r %REQUIREMENTS_FILE% -i https://pypi.douban.com/simple --trusted-host pypi.douban.com
        if errorlevel 1 (
            echo.
            echo Solution 3: Try installing core packages only
            echo FastAPI will not work with old Python version
            echo Please upgrade to Python 3.8+ for full functionality
            echo.
            pip install fastapi==0.68.0 uvicorn==0.15.0 pydantic==1.10.12 -i https://pypi.tuna.tsinghua.edu.cn/simple --trusted-host pypi.tuna.tsinghua.edu.cn
            if errorlevel 1 (
                echo.
                echo Installation failed with all methods
                echo Please check your network connection
                pause
                exit /b 1
            )
        )
    )
)

echo.
echo ========================================
echo SUCCESS: Backend dependencies installed!
echo Using: %REQUIREMENTS_FILE%
echo ========================================
echo.
pause
