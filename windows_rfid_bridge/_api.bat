@echo off
rem Reads the robot PC URL from api_url.txt, asking once if it is missing.
set "API_FILE=%~dp0api_url.txt"
if not exist "%API_FILE%" (
  set /p API_URL=로봇 PC 주소 입력 ^(예: http://192.168.204.52:8000^): 
  call echo %%API_URL%%> "%API_FILE%"
)
set /p API_URL=<"%API_FILE%"
echo 로봇 PC: %API_URL%   ^(변경: windows_rfid_bridge\api_url.txt 수정 또는 삭제^)
