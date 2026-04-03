@echo off
echo ============================================================
echo Claude 中转服务适配脚本
echo ============================================================
echo.

echo [第 1 步] 修复数据库配置...
python fix_model_config.py

echo.
echo [第 2 步] 配置已完成！
echo.
echo 现在请:
echo 1. 在后端窗口按 Ctrl+C 停止当前服务
echo 2. 重新运行: python main.py
echo 3. 在新的窗口运行: python test_llm_chat.py
echo.
echo 查看详细指南: CLAUDE_PROXY_GUIDE.md
echo.
pause
