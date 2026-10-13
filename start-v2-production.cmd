@echo off
setlocal
cd /d "%~dp0"
if not exist "venv\Scripts\python.exe" (
    echo Virtual environment Python not found.
    exit /b 1
)
"venv\Scripts\python.exe" "scripts\v2_production.py" %*
exit /b %ERRORLEVEL%
