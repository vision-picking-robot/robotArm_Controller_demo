@echo off
chcp 65001 >nul
cd /d "%~dp0.."
echo [1/2] Python 가상환경 생성 (.venv-win)
where py >nul 2>nul && (py -3 -m venv .venv-win) || (python -m venv .venv-win)
if errorlevel 1 (
  echo Python 3.10 이상을 설치하고 "Add python.exe to PATH"를 체크한 뒤 다시 실행하세요.
  pause & exit /b 1
)
echo [2/2] pyscard, httpx 설치
.venv-win\Scripts\python.exe -m pip install --upgrade pip
.venv-win\Scripts\python.exe -m pip install -r windows_rfid_bridge\requirements.txt
if errorlevel 1 ( echo 설치 실패 & pause & exit /b 1 )
echo.
echo 설치 완료. 다음: 2_check.bat
pause
