@echo off
chcp 936 >nul
cd /d "%~dp0"
rem Public via Cloudflare Quick Tunnel: no account, no phone number. Needs cloudflared.exe in this folder or in PATH.
rem The public URL (https://xxxx.trycloudflare.com) is printed below and CHANGES EVERY RUN.
where cloudflared >nul 2>nul || if not exist "%~dp0cloudflared.exe" (echo cloudflared.exe not found. Download it from Cloudflare and put it next to this file. & pause & exit /b 1)
set TYPESAFE_KEY_FILE=%~dp0key.txt
set TYPESAFE_BASE_URL=https://openrouter.ai/api/v1/systemone
set TYPESAFE_MODEL=jev-latest
set /p TWELVE_ACCESS_CODE=Access code for players (share it with them): 
start "twelve server" cmd /k python -m twelve.server --jev real --port 8000 --daily-budget 0.3 --max-courts-per-ip 12
echo Waiting for the game server...
timeout /t 4 >nul
curl -s -o nul -m 5 http://127.0.0.1:8000/ || (echo. & echo [!] The game server is NOT answering on http://127.0.0.1:8000 . Check the "twelve server" window for errors. & echo.)
set PATH=%~dp0;%PATH%
rem If you see "failed to request quick Tunnel ... context deadline exceeded", cloudflared cannot reach Cloudflare directly.
rem cloudflared ignores the Windows system proxy; remove "rem" below to send it through Clash (or turn on Clash TUN mode).
rem set HTTPS_PROXY=http://127.0.0.1:7890
rem http2 over TCP is more reliable than the default QUIC/UDP on many networks and behind local proxies.
cloudflared tunnel --protocol http2 --url http://127.0.0.1:8000
pause
