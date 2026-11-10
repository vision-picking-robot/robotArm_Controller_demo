@echo off
chcp 65001 >nul
cd /d "%~dp0.."
echo 로봇 PC로 보내지 않고 UID만 표시합니다.
.venv-win\Scripts\python.exe windows_rfid_bridge\bridge.py --api http://127.0.0.1:8000 --read-only %*
pause
