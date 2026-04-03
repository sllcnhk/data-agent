@echo off
REM 完整重启后端服务脚本

echo ============================================
echo 1. 清理 Python 缓存
echo ============================================
cd /d %~dp0
cd ..
for /d %%i in (backend\__pycache__) do rmdir /s /q "%%i"
for /d %%i in (backend\core\__pycache__) do rmdir /s /q "%%i"
for /d %%i in (backend\core\model_adapters\__pycache__) do rmdir /s /q "%%i"
for /d %%i in (backend\services\__pycache__) do rmdir /s /q "%%i"
for /d %%i in (backend\agents\__pycache__) do rmdir /s /q "%%i"
for /d %%i in (backend\api\__pycache__) do rmdir /s /q "%%i"
echo 已清理所有 __pycache__ 目录

echo.
echo ============================================
echo 2. 检查配置
echo ============================================
python -c "from config.settings import settings; print(f'Primary model: {settings.anthropic_default_model}'); print(f'Fallback models: {settings.anthropic_fallback_models}'); print(f'Fallback enabled: {settings.anthropic_enable_fallback}')"
echo 配置检查完成

echo.
echo ============================================
echo 3. 重启服务 (按 Ctrl+C 停止)
echo ============================================
cd backend
call conda activate dataagent
python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
