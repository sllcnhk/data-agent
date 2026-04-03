@echo off
echo ========================================
echo Testing Fixed Backend API
echo ========================================
echo.

echo [1/4] Killing existing backend processes...
taskkill /IM "python.exe" /F >nul 2>&1
timeout /t 2 >nul

echo [2/4] Starting new backend server...
cd /d "%~dp0"
python run_simple.py > logs/backend-fixed.log 2>&1
echo Started backend

echo [3/4] Waiting for server to start...
timeout /t 5 >nul

echo [4/4] Testing API endpoints...
echo.
echo Test 1: Root endpoint
curl -s http://localhost:8000/
echo.

echo.
echo Test 2: Health endpoint
curl -s http://localhost:8000/health
echo.

echo.
echo Test 3: API root
curl -s http://localhost:8000/api/v1/
echo.

echo.
echo Test 4: Agents endpoint
curl -s http://localhost:8000/api/v1/agents
echo.

echo.
echo Test 5: Skills endpoint
curl -s http://localhost:8000/api/v1/skills
echo.

echo.
echo ========================================
echo Test Complete
echo ========================================
echo.
echo Check logs/backend-fixed.log for details
pause
