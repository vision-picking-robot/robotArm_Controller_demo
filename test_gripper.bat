@echo off
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" scripts\test_gripper.py %*
) else (
  python scripts\test_gripper.py %*
)
exit /b %ERRORLEVEL%
