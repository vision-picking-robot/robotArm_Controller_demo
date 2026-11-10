@echo off
setlocal
cd /d "%~dp0"

echo =======================================================================
echo   [Vision Lab] ROBOTIS RH-P12-RN Gripper 5-Second Diagnostics Routine
echo =======================================================================
echo.
echo   * Verifying gripper communication and physical movement (under 5s)...
echo.

if exist "%~dp0scripts\test_gripper.py" (
    set SCRIPT_PATH=%~dp0scripts\test_gripper.py
) else (
    set SCRIPT_PATH=%~dp0robotArm_Controller_demo\scripts\test_gripper.py
)

set TARGET_MODE=mock
if not "%~1"=="" set TARGET_MODE=%~1

python "%SCRIPT_PATH%" --mode %TARGET_MODE%

if %ERRORLEVEL% equ 0 (
    echo.
    echo   [PASS] Gripper diagnostics completed successfully.
) else (
    echo.
    echo   [FAIL] Gripper diagnostics failed. Check serial port/cables.
)
echo.
pause
