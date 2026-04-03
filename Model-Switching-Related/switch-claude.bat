@echo off
echo [Switch] Anthropic Claude (Official)

set CONFIG_FILE=%USERPROFILE%\.claude\settings.json
if not exist "%USERPROFILE%\.claude" mkdir "%USERPROFILE%\.claude"

(
echo {
echo   "env": {
echo     "ANTHROPIC_API_KEY": "sk-ant-YOUR_ANTHROPIC_KEY_HERE",
echo     "ANTHROPIC_BASE_URL": "https://api.anthropic.com",
echo     "ANTHROPIC_MODEL": "claude-sonnet-4-6"
echo   }
echo }
) > "%CONFIG_FILE%"

echo.
echo Done! Active: Claude Sonnet 4.6 (Anthropic Official)
echo Please reload VS Code: Ctrl+Shift+P -^> Developer: Reload Window
echo.
pause
