@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo =======================================================================
echo   [Vision Lab] 19:00 Field Test One-Click Unified Launcher (Doosan E0509)
echo =======================================================================
echo.

if exist "%~dp0scripts\test_gripper.py" (
    set DEMO_DIR=%~dp0
    set YOLO_DIR=%~dp0..\ryankim8815_yolov8
) else (
    set DEMO_DIR=%~dp0robotArm_Controller_demo
    set YOLO_DIR=%~dp0ryankim8815_yolov8
)

set ROBOT_PRIMARY_IP=110.120.1.56
set ROBOT_SECONDARY_IP=110.120.1.63
set CONTROLLER_FOUND=0
set ACTIVE_IP=NONE

echo [Step 1/4] Checking Ethernet LAN Connection to Doosan Robot Controller
echo -----------------------------------------------------------------------
echo  * Pinging Primary IP: %ROBOT_PRIMARY_IP% (timeout 1s)
ping -n 1 -w 1000 %ROBOT_PRIMARY_IP% > nul
if %ERRORLEVEL% equ 0 (
    set CONTROLLER_FOUND=1
    set ACTIVE_IP=%ROBOT_PRIMARY_IP%
    echo    [SUCCESS] Doosan Controller found at %ROBOT_PRIMARY_IP%
    goto :network_ok
)

echo    -- Primary IP %ROBOT_PRIMARY_IP% did not respond
echo  * Pinging Secondary IP: %ROBOT_SECONDARY_IP% (timeout 1s)
ping -n 1 -w 1000 %ROBOT_SECONDARY_IP% > nul
if %ERRORLEVEL% equ 0 (
    set CONTROLLER_FOUND=1
    set ACTIVE_IP=%ROBOT_SECONDARY_IP%
    echo    [SUCCESS] Doosan Controller found at %ROBOT_SECONDARY_IP%
    goto :network_ok
)

echo    -- Secondary IP %ROBOT_SECONDARY_IP% did not respond
echo.
echo [WARNING] Cannot reach Doosan Robot Controller at 110.120.1.x subnet!
echo   - Please ensure the Ethernet cable is connected directly to the notebook.
echo   - Check your notebook wired network adapter IPv4 configuration:
echo       IP Address : 110.120.1.11 (or 110.120.1.x)
echo       Subnet Mask: 255.255.255.0
echo.
echo   [1] Start with Simulation / Mock Mode (Recommended for testing without robot)
echo   [2] Retry Ping check
echo   [3] Exit
echo.
set /p USER_CHOICE="Enter selection [1/2/3] (default: 1): "
if "!USER_CHOICE!"=="2" goto :retry
if "!USER_CHOICE!"=="3" (
    echo Exiting launcher.
    exit /b 1
)

echo Proceeding in SIMULATION / MOCK mode.
set EXEC_MODE=mock
goto :step2

:network_ok
echo.
echo [NETWORK STATUS] Real Hardware Controller is ONLINE at %ACTIVE_IP%.
set EXEC_MODE=real

:step2
echo.
echo [Step 2/4] Standalone Gripper Quick Check (RH-P12-RN)
echo -----------------------------------------------------------------------
echo Running 5-second rapid gripper open-close diagnostic...
python "!DEMO_DIR!\scripts\test_gripper.py" --mode %EXEC_MODE% --timeout 5.0

echo.
echo [Step 3/4] Starting Subsystem Services
echo -----------------------------------------------------------------------

echo 1) Starting Doosan Demo Robot Controller Service (Port: 8011)...
start "Doosan Demo Robot Controller [Port 8011]" cmd /k "cd /d "!DEMO_DIR!" && python "!DEMO_DIR!\scripts\start_controller.py""

echo 2) Starting YOLOv8 Web Application and Vision Pipeline (Port: 8080)...
set YOLO_BACKEND=mock
set YOLO_CAMERA_AUTOSTART=1
set YOLO_VOICE_ENABLED=1
start "YOLOv8 Web Application [Port 8080]" cmd /k "cd /d "!YOLO_DIR!" && uvicorn app.main:app --host 127.0.0.1 --port 8080"

echo.
echo [Step 4/4] Launching Web Dashboard Browser Interface
echo -----------------------------------------------------------------------
ping 127.0.0.1 -n 3 > nul
start http://127.0.0.1:8080/

echo.
echo =======================================================================
echo   [Vision Lab] All Systems Successfully Launched!
echo.
echo   * Web Dashboard  : http://127.0.0.1:8080/
echo   * Robot Workspace: http://127.0.0.1:8080/robot
echo   * Demo Controller: http://127.0.0.1:8011/api/demo/status
echo.
echo   [Notice] Keep the opened console windows running during your test.
echo =======================================================================
pause
exit /b 0

:retry
cls
"%~f0"
