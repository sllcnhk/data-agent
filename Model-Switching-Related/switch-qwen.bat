@echo off
echo [Switch] Qwen Coder Plus (via LiteLLM Proxy)

set CONFIG_FILE=%USERPROFILE%\.claude\settings.json
if not exist "%USERPROFILE%\.claude" mkdir "%USERPROFILE%\.claude"

(
echo {
echo   "env": {
echo     "ANTHROPIC_API_KEY": "local-proxy-secret",
echo     "ANTHROPIC_BASE_URL": "http://localhost:4000",
echo     "ANTHROPIC_MODEL": "qwen-coder-plus"
echo   }
echo }
) > "%CONFIG_FILE%"

echo.
echo Done! Active: Qwen Coder Plus (DashScope via LiteLLM)
echo Make sure start-proxy.bat is running!
echo Please reload VS Code: Ctrl+Shift+P -^> Developer: Reload Window
echo.
pause
