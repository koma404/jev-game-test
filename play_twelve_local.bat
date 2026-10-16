@echo off
chcp 936 >nul
cd /d "%~dp0"
rem Use a self-hosted System One model (e.g. Kev served at port 8009, in WSL2 or on this PC) instead of hosted Jev.
rem No API cost; quality depends on the model - run: python -m twelve.tests.eval_hooks --jev real
set TYPESAFE_BASE_URL=http://127.0.0.1:8009/v1/systemone
set TYPESAFE_API_KEY=local
set TYPESAFE_PROXY=direct
python -m twelve.server --jev real --daily-budget 0 %*
pause
