@echo off
chcp 65001 >nul
cd /d "%~dp0.."
echo ACR1555U 절전(60초) 해제 - 블루투스 연결이 끊기지 않게 합니다.
.venv-win\Scripts\python.exe -m robot_rfid.rfid_power --no-sleep %*
pause
