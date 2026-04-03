@echo off
echo [Switch] DeepSeek Coder (via LiteLLM Proxy)

set CONFIG_FILE=%USERPROFILE%\.claude\settings.json
if not exist "%USERPROFILE%\.claude" mkdir "%USERPROFILE%\.claude"

(
echo {
echo   "env": {
echo     "ANTHROPIC_API_KEY": "local-proxy-secret",
echo     "ANTHROPIC_BASE_URL": "http://localhost:4000",
echo     "ANTHROPIC_MODEL": "deepseek-coder"
echo   }
echo }
) > "%CONFIG_FILE%"

echo.
echo Done! Active: DeepSeek Coder (via LiteLLM)
echo Make sure start-proxy.bat is running!
echo Please reload VS Code: Ctrl+Shift+P -^> Developer: Reload Window
echo.
pause
