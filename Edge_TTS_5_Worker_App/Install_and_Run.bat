@echo off
setlocal
cd /d "%~dp0"

echo ============================================================
echo Edge TTS - 5 Worker Chapter Generator v2
echo ============================================================
echo.

set "PYEXE="
where py >nul 2>nul
if %errorlevel%==0 set "PYEXE=py -3"
if not defined PYEXE (
    where python >nul 2>nul
    if %errorlevel%==0 set "PYEXE=python"
)

if not defined PYEXE (
    echo Python 3 was not found.
    echo Install Python 3.11+ from https://www.python.org/downloads/
    echo During installation, enable "Add Python to PATH".
    pause
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    echo Creating local Python environment...
    %PYEXE% -m venv .venv
    if errorlevel 1 goto :failed
)

echo Installing/updating required packages...
".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 goto :failed
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto :failed

echo.
echo Starting app...
".venv\Scripts\python.exe" edge_tts_batch_app.py
exit /b 0

:failed
echo.
echo Installation failed. Please review the messages above.
pause
exit /b 1
