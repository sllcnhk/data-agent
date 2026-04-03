@echo off
setlocal enabledelayedexpansion

echo ========================================
echo Data Agent System - Integration Test
echo ========================================
echo.
echo Test Date: %DATE% %TIME%
echo.

:: Initialize test result tracking
set "TEST_PASSED=0"
set "TEST_FAILED=0"
set "TEST_TOTAL=0"

:: Function to log test results
:log_test
set /a TEST_TOTAL+=1
echo [%DATE% %TIME%] TEST %TEST_TOTAL%: %~1 - %~2 >> integration-test.log
if "%~2"=="PASS" (
    set /a TEST_PASSED+=1
    echo   [PASS] %~1
) else (
    set /a TEST_FAILED+=1
    echo   [FAIL] %~1
    echo   Error: %~3
)
echo.
goto :eof

echo [Step 1/4] Testing Full Installation...
echo ========================================
echo.

cd /d "%~dp0"
echo Current Directory: %CD%
echo.

echo [TEST 1.1] Checking install-all.bat exists...
if exist "install-all.bat" (
    call :log_test "install-all.bat exists" "PASS"
) else (
    call :log_test "install-all.bat exists" "FAIL" "File not found"
    goto :test_summary
)

echo [TEST 1.2] Checking install-backend.bat exists...
if exist "install-backend.bat" (
    call :log_test "install-backend.bat exists" "PASS"
) else (
    call :log_test "install-backend.bat exists" "FAIL" "File not found"
    goto :test_summary
)

echo [TEST 1.3] Checking install-frontend.bat exists...
if exist "install-frontend.bat" (
    call :log_test "install-frontend.bat exists" "PASS"
) else (
    call :log_test "install-frontend.bat exists" "FAIL" "File not found"
    goto :test_summary
)

echo.
echo [Step 2/4] Testing Backend Installation...
echo ========================================
echo.

echo [TEST 2.1] Checking backend requirements.txt...
if exist "backend\requirements.txt" (
    call :log_test "backend requirements.txt exists" "PASS"
    type "backend\requirements.txt"
    echo.
) else (
    call :log_test "backend requirements.txt exists" "FAIL" "File not found"
    goto :test_summary
)

echo [TEST 2.2] Checking Python installation...
python --version >nul 2>&1
if errorlevel 1 (
    call :log_test "Python installation" "FAIL" "Python not found"
) else (
    for /f "tokens=2" %%i in ('python --version 2^>^&1') do set PYTHON_VERSION=%%i
    call :log_test "Python installation (v%PYTHON_VERSION%)" "PASS"
)

echo.
echo [Step 3/4] Testing Frontend Installation...
echo ========================================
echo.

echo [TEST 3.1] Checking frontend package.json...
if exist "frontend\package.json" (
    call :log_test "frontend package.json exists" "PASS"
) else (
    call :log_test "frontend package.json exists" "FAIL" "File not found"
    goto :test_summary
)

echo [TEST 3.2] Checking Node.js installation...
node --version >nul 2>&1
if errorlevel 1 (
    call :log_test "Node.js installation" "FAIL" "Node.js not found"
) else (
    for /f "tokens=1" %%i in ('node --version 2^>^&1') do set NODE_VERSION=%%i
    call :log_test "Node.js installation (v%NODE_VERSION%)" "PASS"
)

echo [TEST 3.3] Checking npm installation...
npm --version >nul 2>&1
if errorlevel 1 (
    call :log_test "npm installation" "FAIL" "npm not found"
) else (
    for /f %%i in ('npm --version 2^>^&1') do set NPM_VERSION=%%i
    call :log_test "npm installation (v%NPM_VERSION%)" "PASS"
)

echo [TEST 3.4] Checking frontend dependencies...
if exist "frontend\node_modules" (
    call :log_test "frontend node_modules exists" "PASS"
    echo Checking package count...
    dir /b frontend\node_modules | find /c /v "" > temp_count.txt
    set /p PKG_COUNT=<temp_count.txt
    del temp_count.txt
    call :log_test "frontend dependencies installed (!PKG_COUNT! packages)" "PASS"
) else (
    call :log_test "frontend node_modules exists" "FAIL" "Dependencies not installed"
)

echo.
echo [Step 4/4] Testing Startup Scripts...
echo ========================================
echo.

echo [TEST 4.1] Checking start-all.bat exists...
if exist "start-all.bat" (
    call :log_test "start-all.bat exists" "PASS"
) else (
    call :log_test "start-all.bat exists" "FAIL" "File not found"
    goto :test_summary
)

echo [TEST 4.2] Checking backend run script...
if exist "backend\run_simple.py" (
    call :log_test "backend run_simple.py exists" "PASS"
) else (
    call :log_test "backend run_simple.py exists" "FAIL" "File not found"
    goto :test_summary
)

echo [TEST 4.3] Checking backend vite config...
if exist "backend\vite.config.js" (
    call :log_test "backend vite.config.js exists" "PASS"
) else (
    call :log_test "backend vite.config.js exists" "FAIL" "File not found"
    goto :test_summary
)

echo.
echo ========================================
echo Integration Test Summary
echo ========================================
echo.
echo Total Tests: %TEST_TOTAL%
echo Passed: %TEST_PASSED%
echo Failed: %TEST_FAILED%
echo Success Rate: %TEST_PASSED%/%TEST_TOTAL%
echo.

if %TEST_FAILED% equ 0 (
    echo [PASS] All integration tests passed!
    echo.
    echo ========================================
    echo System is ready for deployment
    echo ========================================
    echo.
    echo Next steps:
    echo   1. Run start-all.bat to start services
    echo   2. Access http://localhost:3000 for frontend
    echo   3. Access http://localhost:8000/api/docs for API docs
    echo.
) else (
    echo [FAIL] Some integration tests failed
    echo Please check the errors above
    echo.
    echo Failed tests: %TEST_FAILED%
    echo Check integration-test.log for details
    echo.
)

echo Test completed at: %DATE% %TIME%
echo.
pause
goto :eof

:test_summary
echo.
echo ========================================
echo Test interrupted due to critical failure
echo ========================================
echo.
pause
exit /b 1
