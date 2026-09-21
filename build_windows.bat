@echo off
setlocal
cd /d "%~dp0"

echo ============================================================
echo Edge TTS Batch Narrator v1.0.0 - Windows Build
echo ============================================================
echo.

where py >nul 2>nul
if errorlevel 1 (
  echo Python launcher "py" was not found.
  echo Install Python 3.12+ from https://www.python.org/downloads/windows/
  pause
  exit /b 1
)

if not exist ".build-venv\Scripts\python.exe" (
  echo Creating build environment...
  py -3.12 -m venv .build-venv
  if errorlevel 1 py -3 -m venv .build-venv
)

call ".build-venv\Scripts\activate.bat"
python -m pip install --upgrade pip
python -m pip install -r requirements.txt -r requirements-build.txt

if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

python -m PyInstaller EdgeTTSBatchNarrator.spec --noconfirm --clean
if errorlevel 1 goto :failed

echo.
echo Build complete:
echo   dist\EdgeTTSBatchNarrator-v1.0.0-win64.exe
echo.
pause
exit /b 0

:failed
echo.
echo Build failed. Review the output above.
pause
exit /b 1
