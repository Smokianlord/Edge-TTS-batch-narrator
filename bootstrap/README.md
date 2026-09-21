# Bootstrap EXE

`main_windows.go` is an optional lightweight Windows launcher used to produce a real `.exe` without requiring a batch file.

On first launch it:

1. Extracts the bundled application source to the current user's Local AppData directory.
2. Detects a compatible Python installation with Tk support.
3. If needed, downloads the official Python 3.12.10 Windows installer from python.org and installs it for the current user.
4. Creates a private virtual environment.
5. Installs the runtime dependencies from `requirements.txt`.
6. Launches the graphical application with `pythonw.exe`.

The GitHub Actions workflow is the preferred public-release path because it builds the fully bundled PyInstaller Windows executable.
