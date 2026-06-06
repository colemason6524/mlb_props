@echo off
set PROJECT_DIR=C:\bots\mlb_props
set PYTHON_EXE=python

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%PROJECT_DIR%\scripts\run_hot_hits_task.ps1" -ProjectDir "%PROJECT_DIR%" -PythonExe "%PYTHON_EXE%"
exit /b %ERRORLEVEL%
