@echo off
chcp 65001 >nul
echo ============================================================
echo PostgreSQL Installation Script for Windows
echo ============================================================
echo.

REM Check admin privileges
net session >nul 2>&1
if %errorLevel% neq 0 (
    echo [ERROR] Please run as Administrator
    echo Right-click script and select "Run as administrator"
    pause
    exit /b 1
)

echo [1/5] Checking Chocolatey...
where choco >nul 2>&1
if %errorLevel% neq 0 (
    echo Chocolatey not installed, installing...
    @"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -InputFormat None -ExecutionPolicy Bypass -Command "[System.Net.ServicePointManager]::SecurityProtocol = 3072; iex ((New-Object System.Net.WebClient).DownloadString('https://community.chocolatey.org/install.ps1'))" && SET "PATH=%PATH%;%ALLUSERSPROFILE%\chocolatey\bin"

    if %errorLevel% neq 0 (
        echo [ERROR] Chocolatey installation failed
        echo Please install manually from https://chocolatey.org/install
        pause
        exit /b 1
    )
    echo Chocolatey installed successfully
) else (
    echo Chocolatey already installed
)

echo.
echo [2/5] Checking PostgreSQL...
where psql >nul 2>&1
if %errorLevel% neq 0 (
    echo PostgreSQL not installed, installing...
    echo This may take a few minutes...
    choco install postgresql14 -y --params '/Password:postgres'

    if %errorLevel% neq 0 (
        echo [ERROR] PostgreSQL installation failed
        pause
        exit /b 1
    )

    echo PostgreSQL installed successfully
    echo Default password: postgres

    REM Add to PATH
    setx PATH "%PATH%;C:\Program Files\PostgreSQL\14\bin" /M
    set "PATH=%PATH%;C:\Program Files\PostgreSQL\14\bin"
) else (
    echo PostgreSQL already installed
)

echo.
echo [3/5] Installing Python PostgreSQL driver...
cd /d "%~dp0backend"
pip install psycopg2-binary

if %errorLevel% neq 0 (
    echo [ERROR] psycopg2-binary installation failed
    echo Trying psycopg2...
    pip install psycopg2

    if %errorLevel% neq 0 (
        echo [ERROR] PostgreSQL Python driver installation failed
        pause
        exit /b 1
    )
)

echo Python PostgreSQL driver installed successfully

echo.
echo [4/5] Waiting for PostgreSQL service to start...
timeout /t 5 /nobreak >nul

REM Check PostgreSQL service
sc query postgresql-x64-14 | find "RUNNING" >nul
if %errorLevel% neq 0 (
    echo Starting PostgreSQL service...
    net start postgresql-x64-14
    timeout /t 3 /nobreak >nul
)

echo.
echo [5/5] Creating database...
echo.

REM Set password environment variable
set PGPASSWORD=postgres

REM Check if database exists
psql -U postgres -lqt | find "data_agent" >nul
if %errorLevel% neq 0 (
    echo Creating database data_agent...
    psql -U postgres -c "CREATE DATABASE data_agent;"

    if %errorLevel% neq 0 (
        echo [WARNING] Database creation failed, may need manual creation
    ) else (
        echo Database data_agent created successfully
    )
) else (
    echo Database data_agent already exists
)

echo.
echo ============================================================
echo PostgreSQL Installation Complete!
echo ============================================================
echo.
echo Database Information:
echo   Host: localhost
echo   Port: 5432
echo   Database: data_agent
echo   User: postgres
echo   Password: postgres
echo.
echo Next Steps:
echo   1. Run: cd backend
echo   2. Run: python scripts\init_chat_db.py
echo   3. Run: python main.py
echo.
pause
