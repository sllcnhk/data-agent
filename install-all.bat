@echo off
echo ========================================
echo Data Agent System - Install All
echo ========================================
echo.

echo Step 1/2: Installing backend dependencies...
call install-backend.bat
if errorlevel 1 (
    echo ERROR: Backend installation failed
    pause
    exit /b 1
)

echo.
echo Step 2/2: Installing frontend dependencies...
call install-frontend.bat
if errorlevel 1 (
    echo ERROR: Frontend installation failed
    pause
    exit /b 1
)

echo.
echo ========================================
echo SUCCESS: All dependencies installed!
echo ========================================
echo.
echo Next: Run start-all.bat to start services
echo.
pause
