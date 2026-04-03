@echo off
setlocal enabledelayedexpansion

echo ========================================
echo Test Backend with Simple Server
echo ========================================
echo.

if not exist logs mkdir logs

echo [1/5] Checking Python...
python --version
if errorlevel 1 (
    echo ERROR: Python not found
    pause
    exit /b 1
)

echo.
echo [2/5] Checking if FastAPI is installed...
python -c "import fastapi; print('FastAPI version:', fastapi.__version__)" >nul 2>&1
if errorlevel 1 (
    echo ERROR: FastAPI not installed
    echo Please run: install-backend.bat
    pause
    exit /b 1
)
echo OK: FastAPI is installed

echo.
echo [3/5] Creating simple test server...
echo Creating simple_server.py...
(
echo from fastapi import FastAPI
echo import uvicorn
echo app = FastAPI^(title="Test Server"^)
echo @app.get^("/"^)
echo def read_root^(^):
echo     return {"status": "ok", "message": "Backend is working!"}
echo @app.get^("/health"^)
echo def health^(^):
echo     return {"status": "healthy"}
echo if __name__ == "__main__":
echo     uvicorn.run^(app, host="0.0.0.0", port=8000^)
) > simple_server.py

echo OK: simple_server.py created

echo.
echo [4/5] Testing simple server...
echo Starting server on port 8000...
echo Open browser to: http://localhost:8000/
echo.
echo Press Ctrl+C to stop the server
echo ========================================
python simple_server.py
echo.
echo Server stopped
echo.
echo [5/5] Cleaning up...
del simple_server.py
echo OK: Cleanup complete

pause
