@echo off
chcp 65001 >nul
echo ============================================================
echo PostgreSQL Password Reset Helper
echo ============================================================
echo.
echo This script will help you reset PostgreSQL password
echo.
echo IMPORTANT: You need to run this as Administrator
echo.
pause

echo.
echo [Step 1/5] Checking PostgreSQL service...
sc query postgresql-x64-18 | find "RUNNING" >nul
if %errorLevel% neq 0 (
    echo ERROR: PostgreSQL service is not running
    echo Please start the service first using:
    echo   net start postgresql-x64-18
    pause
    exit /b 1
)
echo PostgreSQL service is running

echo.
echo [Step 2/5] Backing up pg_hba.conf...
set PG_DATA="C:\Program Files\PostgreSQL\18\data"
set PG_HBA=%PG_DATA%\pg_hba.conf
set PG_HBA_BACKUP=%PG_DATA%\pg_hba.conf.backup

if not exist %PG_HBA% (
    echo ERROR: Cannot find pg_hba.conf at %PG_HBA%
    pause
    exit /b 1
)

copy %PG_HBA% %PG_HBA_BACKUP% >nul
echo Backup created: pg_hba.conf.backup

echo.
echo [Step 3/5] Modifying pg_hba.conf to trust mode...
powershell -Command "(Get-Content '%PG_HBA%') -replace 'host    all             all             127.0.0.1/32            scram-sha-256', 'host    all             all             127.0.0.1/32            trust' | Set-Content '%PG_HBA%'"
echo Modified authentication to trust mode

echo.
echo [Step 4/5] Restarting PostgreSQL service...
net stop postgresql-x64-18 >nul 2>&1
timeout /t 2 /nobreak >nul
net start postgresql-x64-18 >nul 2>&1
timeout /t 3 /nobreak >nul
echo Service restarted

echo.
echo [Step 5/5] Resetting password...
set PSQL="C:\Program Files\PostgreSQL\18\bin\psql.exe"

%PSQL% -U postgres -h localhost -d postgres -c "ALTER USER postgres PASSWORD 'Sgp013013.';" 2>nul
if %errorLevel% equ 0 (
    echo Password reset successfully
) else (
    echo WARNING: Password reset may have failed
)

echo.
echo [Step 6/6] Creating data_agent database...
%PSQL% -U postgres -h localhost -d postgres -c "CREATE DATABASE data_agent;" 2>nul
if %errorLevel% equ 0 (
    echo Database created successfully
) else (
    echo Note: Database may already exist (this is OK)
)

echo.
echo [Step 7/7] Restoring pg_hba.conf...
copy %PG_HBA_BACKUP% %PG_HBA% >nul
echo Restored original pg_hba.conf

echo.
echo [Step 8/8] Final restart...
net stop postgresql-x64-18 >nul 2>&1
timeout /t 2 /nobreak >nul
net start postgresql-x64-18 >nul 2>&1
timeout /t 3 /nobreak >nul
echo Service restarted with original configuration

echo.
echo ============================================================
echo Setup Complete!
echo ============================================================
echo.
echo Now test the connection:
echo   cd backend
echo   python test_connection.py
echo.
echo If successful, run:
echo   python scripts\init_chat_db.py
echo.
pause
