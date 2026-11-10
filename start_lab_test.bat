@echo off
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" scripts\start_lab.py
) else (
  python scripts\start_lab.py
)
exit /b %ERRORLEVEL%
