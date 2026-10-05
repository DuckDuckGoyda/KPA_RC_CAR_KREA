@echo off
rem Runs the app from source (first start creates .venv and installs PyQt6 + pyserial).
cd /d "%~dp0"

if not exist .venv\Scripts\python.exe (
    py -3 -m venv .venv || goto :error
    .venv\Scripts\python.exe -m pip install --quiet -r requirements.txt || goto :error
)
start "" .venv\Scripts\pythonw.exe -m rc_controller
exit /b 0

:error
echo Could not set up Python. Install Python 3.11+ from python.org (tick "Add to PATH").
pause
exit /b 1
