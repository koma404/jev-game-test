@echo off
chcp 936 >nul
cd /d "%~dp0"
python -m twelve.server --jev mock %*
pause
