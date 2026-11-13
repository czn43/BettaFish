@echo off
chcp 65001 >nul
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run_bettafish.ps1" %*
echo.
echo --- BettaFish process exited ---
pause
