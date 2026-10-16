@echo off
chcp 936 >nul
cd /d "%~dp0"
rem 把 OpenRouter key 单独存在同目录的 key.txt（一行，不要引号）
set TYPESAFE_KEY_FILE=%~dp0key.txt
set TYPESAFE_BASE_URL=https://openrouter.ai/api/v1/systemone
set TYPESAFE_MODEL=jev-latest
rem 如果报 CERTIFICATE_VERIFY_FAILED，取消下一行注释并指向 cacert.pem
rem set SSL_CERT_FILE=%~dp0cacert.pem
python -m game.cli --jev real %*
pause
