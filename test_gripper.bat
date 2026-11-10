@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  set "PY=.venv\Scripts\python.exe"
) else if exist "..\.venv312\Scripts\python.exe" (
  set "PY=..\.venv312\Scripts\python.exe"
) else (
  set "PY=python"
)
if "%~1"=="" (
  "%PY%" scripts\test_gripper.py --mode mock --action status
) else (
  "%PY%" scripts\test_gripper.py %*
)
exit /b %ERRORLEVEL%
