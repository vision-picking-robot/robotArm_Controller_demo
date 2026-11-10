@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion
cd /d "%~dp0"

set "ROBOT_IP=110.120.1.56"

echo ============================================================================
echo  [Vision-Picking-Robot] 19:00 Lab One-Click Launcher (Unified System)
echo  - Web UI / Camera / Voice / Button Control : http://127.0.0.1:8000
echo  - Controller & RFID Task API               : http://127.0.0.1:8011/docs
echo ============================================================================
echo.

echo [Step 1/3] Checking network reachability to Doosan Robot Controller (%ROBOT_IP%)...
ping -n 1 -w 1000 %ROBOT_IP% >nul 2>&1
if !errorlevel! equ 0 (
    echo   [OK] Ping to %ROBOT_IP% succeeded. Hardware link detected.
) else (
    echo   [WARN] Ping to %ROBOT_IP% timed out. Continuing in safe MOCK_MODE.
)
echo.

echo [Step 2/3] Launching Browser to http://127.0.0.1:8000 (in 3s)...
start "" cmd /c "timeout /t 3 /nobreak >nul & start http://127.0.0.1:8000"

echo [Step 3/3] Starting Unified Services (Controller 8011 + Web/RFID/Camera 8000)...
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" scripts\start_lab.py
) else if exist "..\.venv312\Scripts\python.exe" (
  "..\.venv312\Scripts\python.exe" scripts\start_lab.py
) else (
  python scripts\start_lab.py
)
exit /b %ERRORLEVEL%
