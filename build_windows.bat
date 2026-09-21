@echo off
setlocal
cd /d "%~dp0"

echo ============================================================
echo Edge TTS batch narrator v1.0.0 - Windows Build
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

if exist build\EdgeTTSBatchNarrator rmdir /s /q build\EdgeTTSBatchNarrator
if exist dist rmdir /s /q dist

echo.
echo Building standalone executable...
python -m PyInstaller EdgeTTSBatchNarrator.spec --noconfirm --clean
if errorlevel 1 goto :failed

where go >nul 2>nul
if errorlevel 1 (
  echo.
  echo Go was not found, so the optional Setup executable was not built.
  echo Install Go and run this file again if you also want the Setup build.
  goto :success
)

echo.
echo Building lightweight Setup executable...
pushd bootstrap
go build -trimpath -ldflags="-s -w" -o ..\dist\EdgeTTSBatchNarrator-v1.0.0-Setup-raw.exe main_windows.go
if errorlevel 1 (
  popd
  goto :failed
)
popd

python build\patch_pe_resources.py ^
  dist\EdgeTTSBatchNarrator-v1.0.0-Setup-raw.exe ^
  assets\app.ico ^
  dist\EdgeTTSBatchNarrator-v1.0.0-Setup.exe ^
  --title "Edge TTS batch narrator" ^
  --version "1.0.0" ^
  --company "Mystic Story Recaps"
if errorlevel 1 goto :failed
del /q dist\EdgeTTSBatchNarrator-v1.0.0-Setup-raw.exe

:success
echo.
echo Build complete. Check the dist folder.
echo.
pause
exit /b 0

:failed
echo.
echo Build failed. Review the output above.
pause
exit /b 1
