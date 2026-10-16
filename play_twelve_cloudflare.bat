@echo off
chcp 936 >nul
cd /d "%~dp0"
rem Public via Cloudflare Quick Tunnel: no account, no phone number. Needs cloudflared.exe in this folder or in PATH.
rem The public URL (https://xxxx.trycloudflare.com) is printed below and changes every run.
where cloudflared >nul 2>nul || if not exist "%~dp0cloudflared.exe" (echo cloudflared.exe not found. Download it from Cloudflare and put it next to this file. & pause & exit /b 1)
set TYPESAFE_KEY_FILE=%~dp0key.txt
set TYPESAFE_BASE_URL=https://openrouter.ai/api/v1/systemone
set TYPESAFE_MODEL=jev-latest
set /p TWELVE_ACCESS_CODE=Access code for players (share it with them): 
start "twelve server" cmd /k python -m twelve.server --jev real --port 8000 --daily-budget 0.3 --max-courts-per-ip 6
timeout /t 3 >nul
set PATH=%~dp0;%PATH%
cloudflared tunnel --url http://localhost:8000
pause
