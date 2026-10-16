@echo off
chcp 936 >nul
cd /d "%~dp0"
rem Put your OpenRouter key alone on one line in key.txt next to this file.
set TYPESAFE_KEY_FILE=%~dp0key.txt
set TYPESAFE_BASE_URL=https://openrouter.ai/api/v1/systemone
set TYPESAFE_MODEL=jev-latest
rem Proxy: default follows system proxy. Uncomment ONE line below if needed.
set TYPESAFE_PROXY=direct
rem set TYPESAFE_PROXY=http://127.0.0.1:7890
rem If you get CERTIFICATE_VERIFY_FAILED, remove "rem" below and point it at cacert.pem
rem set SSL_CERT_FILE=%~dp0cacert.pem
python -m game.cli --jev real %*
pause
