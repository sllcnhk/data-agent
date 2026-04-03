@echo off
echo Checking anthropic version...
echo.

set PYTHONPATH=C:\Users\shiguangping\data-agent

python check_anthropic_version.py

echo.
echo Also checking pip info...
pip show anthropic

pause
