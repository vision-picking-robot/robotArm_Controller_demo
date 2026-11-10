@echo off
chcp 65001 >nul
cd /d "%~dp0.."
call "%~dp0_api.bat"
.venv-win\Scripts\python.exe windows_rfid_bridge\bridge.py --api %API_URL% %*
pause
