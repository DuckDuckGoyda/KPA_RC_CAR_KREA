@echo off
rem Builds dist\RC_Controller.exe: creates .venv, installs dependencies, runs the tests, packs the exe.
cd /d "%~dp0"

if not exist .venv\Scripts\python.exe (
    py -3 -m venv .venv || goto :error
)
call .venv\Scripts\activate.bat || goto :error

python -m pip install --quiet --upgrade pip || goto :error
python -m pip install --quiet -r requirements-dev.txt || goto :error

set QT_QPA_PLATFORM=offscreen
python -m pytest -q || goto :error
set QT_QPA_PLATFORM=

python packaging\make_icon.py || goto :error
pyinstaller --noconfirm packaging\rc_controller.spec || goto :error

echo.
echo Done: dist\RC_Controller.exe
exit /b 0

:error
echo.
echo Build FAILED, see the messages above.
exit /b 1
