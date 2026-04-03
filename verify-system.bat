@echo off
setlocal enabledelayedexpansion

echo ========================================
echo Data Agent System - System Verification
echo ========================================
echo.

:: Initialize counters
set "TESTS_PASSED=0"
set "TESTS_FAILED=0"
set "TESTS_TOTAL=0"

:: Function to log test results
:log_test
set /a TESTS_TOTAL+=1
if "%~2"=="PASS" (
    set /a TESTS_PASSED+=1
    echo   [PASS] %~1
) else (
    set /a TESTS_FAILED+=1
    echo   [FAIL] %~1
    if not "%~3"=="" echo   Reason: %~3
)
echo.
goto :eof

echo [TEST 1/7] Checking Environment
echo ================================
echo.

:: Check Python
echo Checking Python installation...
python --version >nul 2>&1
if errorlevel 1 (
    call :log_test "Python installation" "FAIL" "Python not found in PATH"
) else (
    for /f "tokens=2" %%i in ('python --version 2^>^&1') do set PYTHON_VERSION=%%i
    call :log_test "Python v%PYTHON_VERSION%" "PASS"
)

:: Check Node.js
echo Checking Node.js installation...
node --version >nul 2>&1
if errorlevel 1 (
    call :log_test "Node.js installation" "FAIL" "Node.js not found in PATH"
) else (
    for /f "tokens=1" %%i in ('node --version 2^>^&1') do set NODE_VERSION=%%i
    call :log_test "Node.js v!NODE_VERSION!" "PASS"
)

:: Check npm
echo Checking npm installation...
npm --version >nul 2>&1
if errorlevel 1 (
    call :log_test "npm installation" "FAIL" "npm not found"
) else (
    for /f %%i in ('npm --version 2^>^&1') do set NPM_VERSION=%%i
    call :log_test "npm v!NPM_VERSION!" "PASS"
)

echo [TEST 2/7] Checking File Structure
echo ==================================
echo.

:: Check required files
if exist "run_simple.py" (
    call :log_test "run_simple.py exists" "PASS"
) else (
    call :log_test "run_simple.py exists" "FAIL" "File not found"
)

if exist "start-all.bat" (
    call :log_test "start-all.bat exists" "PASS"
) else (
    call :log_test "start-all.bat exists" "FAIL" "File not found"
)

if exist "frontend\package.json" (
    call :log_test "frontend/package.json exists" "PASS"
) else (
    call :log_test "frontend/package.json exists" "FAIL" "File not found"
)

if exist "backend\requirements.txt" (
    call :log_test "backend/requirements.txt exists" "PASS"
) else (
    call :log_test "backend/requirements.txt exists" "FAIL" "File not found"
)

echo [TEST 3/7] Checking Dependencies
echo =================================
echo.

:: Check node_modules
if exist "frontend\node_modules" (
    call :log_test "Frontend dependencies installed" "PASS"
) else (
    call :log_test "Frontend dependencies installed" "FAIL" "Run install-frontend.bat"
)

:: Check backend
if exist "backend\" (
    call :log_test "Backend directory exists" "PASS"
) else (
    call :log_test "Backend directory exists" "FAIL" "Backend files missing"
)

echo [TEST 4/7] Testing Backend Startup
echo ==================================
echo.

:: Kill existing processes
taskkill /f /im python.exe >nul 2>&1
taskkill /f /im node.exe >nul 2>&1
timeout /t 2 /nobreak >nul

:: Start backend in background
echo Starting backend service...
start /b python run_simple.py > logs\verify-backend.log 2>&1
echo Waiting for backend to start...
timeout /t 5 /nobreak >nul

:: Test backend
echo Testing backend API...
curl -s http://localhost:8000/health >nul 2>&1
if errorlevel 1 (
    call :log_test "Backend startup" "FAIL" "Backend not responding"
    echo   Check logs\verify-backend.log for details
) else (
    call :log_test "Backend startup" "PASS"
    echo   Backend is running on port 8000
)

echo [TEST 5/7] Testing API Endpoints
echo ================================
echo.

:: Test all API endpoints
echo Testing GET /...
curl -s http://localhost:8000/ >nul 2>&1
if errorlevel 1 (
    call :log_test "GET /" "FAIL"
) else (
    call :log_test "GET /" "PASS"
)

echo Testing GET /api/v1/...
curl -s http://localhost:8000/api/v1/ >nul 2>&1
if errorlevel 1 (
    call :log_test "GET /api/v1/" "FAIL"
) else (
    call :log_test "GET /api/v1/" "PASS"
)

echo Testing GET /api/v1/agents...
curl -s http://localhost:8000/api/v1/agents >nul 2>&1
if errorlevel 1 (
    call :log_test "GET /api/v1/agents" "FAIL"
) else (
    call :log_test "GET /api/v1/agents" "PASS"
)

echo Testing GET /api/v1/skills...
curl -s http://localhost:8000/api/v1/skills >nul 2>&1
if errorlevel 1 (
    call :log_test "GET /api/v1/skills" "FAIL"
) else (
    call :log_test "GET /api/v1/skills" "PASS"
)

echo Testing GET /api/v1/tasks...
curl -s http://localhost:8000/api/v1/tasks >nul 2>&1
if errorlevel 1 (
    call :log_test "GET /api/v1/tasks" "FAIL"
) else (
    call :log_test "GET /api/v1/tasks" "PASS"
)

echo [TEST 6/7] Testing Frontend Startup
echo =====================================
echo.

:: Start frontend in background
echo Starting frontend service...
cd frontend
start /b npm run dev > ..\logs\verify-frontend.log 2>&1
cd ..
echo Waiting for frontend to start...
timeout /t 8 /nobreak >nul

:: Test frontend
echo Testing frontend page...
curl -s http://localhost:3000 >nul 2>&1
if errorlevel 1 (
    :: Try alternative port
    curl -s http://localhost:3001 >nul 2>&1
    if errorlevel 1 (
        call :log_test "Frontend startup" "FAIL" "Frontend not responding on port 3000 or 3001"
        echo   Check logs\verify-frontend.log for details
    ) else (
        call :log_test "Frontend startup" "PASS"
        echo   Frontend is running on port 3001
    )
) else (
    call :log_test "Frontend startup" "PASS"
    echo   Frontend is running on port 3000
)

echo [TEST 7/7] Final System Check
echo =============================
echo.

:: Overall status
if %TESTS_FAILED% equ 0 (
    call :log_test "Overall system status" "PASS" "All tests passed"
) else (
    call :log_test "Overall system status" "FAIL" "%TESTS_FAILED% tests failed"
)

:: Summary
echo ========================================
echo Verification Summary
echo ========================================
echo.
echo Total Tests: %TESTS_TOTAL%
echo Passed: %TESTS_PASSED%
echo Failed: %TESTS_FAILED%
echo Success Rate: %TESTS_PASSED%/%TESTS_TOTAL%
echo.

if %TESTS_FAILED% equ 0 (
    echo ========================================
    echo ✅ SYSTEM VERIFICATION PASSED
    echo ========================================
    echo.
    echo Your system is ready to use!
    echo.
    echo Access URLs:
    echo   - Frontend: http://localhost:3000
    echo   - Backend: http://localhost:8000
    echo   - API Docs: http://localhost:8000/api/docs
    echo.
    echo The system is running in the background.
    echo Close this window to keep it running.
    echo.
) else (
    echo ========================================
    echo ❌ SYSTEM VERIFICATION FAILED
    echo ========================================
    echo.
    echo Some tests failed. Please check the errors above.
    echo.
    echo Troubleshooting steps:
    echo   1. Run install-all.bat to install dependencies
    echo   2. Check STARTUP_VERIFICATION.md for detailed help
    echo   3. View logs\verify-*.log for detailed error info
    echo.
)

echo Verification completed at: %DATE% %TIME%
echo Log files:
echo   - Backend: logs\verify-backend.log
echo   - Frontend: logs\verify-frontend.log
echo.
pause
