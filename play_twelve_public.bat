@echo off
chcp 936 >nul
cd /d "%~dp0"
rem PUBLIC mode: players need an access code; Jev spending is capped per day; each IP can open a few trials per hour.
set TYPESAFE_KEY_FILE=%~dp0key.txt
set TYPESAFE_BASE_URL=https://openrouter.ai/api/v1/systemone
set TYPESAFE_MODEL=jev-latest
rem set TYPESAFE_PROXY=direct
set /p TWELVE_ACCESS_CODE=Access code for players (share it with them): 
rem --daily-budget is in USD (0.3 USD is about 2 RMB, roughly 5-30 trials).
python -m twelve.server --jev real --port 8000 --daily-budget 0.3 --max-courts-per-ip 12 %*
pause
