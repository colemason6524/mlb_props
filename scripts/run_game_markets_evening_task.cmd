@echo off
setlocal

set "PROJECT_DIR=C:\Users\muski\mlb_props"
set "PYTHON_EXE=python"
set "POWERSHELL_EXE=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
set "LOG_DIR=%PROJECT_DIR%\logs"
set "BOOT_LOG=%LOG_DIR%\game_markets_evening_cmd_bootstrap.log"

if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

echo [%DATE% %TIME%] Starting game markets evening shadow CMD wrapper>> "%BOOT_LOG%"
echo [%DATE% %TIME%] PROJECT_DIR=%PROJECT_DIR%>> "%BOOT_LOG%"
echo [%DATE% %TIME%] PYTHON_EXE=%PYTHON_EXE%>> "%BOOT_LOG%"

if not exist "%PROJECT_DIR%" (
    echo [%DATE% %TIME%] Project directory not found>> "%BOOT_LOG%"
    exit /b 1
)

if not exist "%POWERSHELL_EXE%" (
    echo [%DATE% %TIME%] PowerShell executable not found>> "%BOOT_LOG%"
    exit /b 1
)

"%POWERSHELL_EXE%" -NoProfile -ExecutionPolicy Bypass -File "%PROJECT_DIR%\scripts\run_game_markets_task.ps1" -ProjectDir "%PROJECT_DIR%" -PythonExe "%PYTHON_EXE%" -ExportHistory -RefreshLines true -RunNote "scheduled evening lineup-confirmation refresh" >> "%BOOT_LOG%" 2>&1
set "EXIT_CODE=%ERRORLEVEL%"

echo [%DATE% %TIME%] Finished with exit code %EXIT_CODE%>> "%BOOT_LOG%"
exit /b %EXIT_CODE%
