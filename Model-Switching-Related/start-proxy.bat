@echo off
title LiteLLM Proxy - Port 4000
echo ================================================
echo  LiteLLM Proxy  (Anthropic ^<-^> OpenAI Bridge)
echo  Port: 4000
echo  Keep this window open while using Claude Code
echo ================================================
echo.
d:\ProgramData\Anaconda3\envs\dataagent\python.exe -m litellm ^
  --config "%~dp0litellm_config.yaml" ^
  --port 4000
pause
