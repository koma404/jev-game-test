@echo off
chcp 936 >nul
cd /d "%~dp0"
set TYPESAFE_KEY_FILE=%~dp0key.txt
set TYPESAFE_BASE_URL=https://openrouter.ai/api/v1/systemone
set TYPESAFE_MODEL=jev-latest
python -m game.netcheck
pause
