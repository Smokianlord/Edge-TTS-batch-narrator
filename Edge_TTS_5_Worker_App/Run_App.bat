@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo First-time setup has not been completed.
    echo Please run Install_and_Run.bat first.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" edge_tts_batch_app.py
