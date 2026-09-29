@echo off
setlocal
cd /d "%~dp0"
if not exist "venv\Scripts\python.exe" (
    echo Virtual environment Python not found: "%CD%\venv\Scripts\python.exe"
    exit /b 1
)
"venv\Scripts\python.exe" "scripts\v2_lab.py"
exit /b %ERRORLEVEL%
