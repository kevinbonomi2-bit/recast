@echo off
rem Starts Recast in the system tray (no console window stays open).
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
    echo Setting up Recast for the first time...
    python -m venv .venv || goto :error
    ".venv\Scripts\python.exe" -m pip install -q -r requirements.txt || goto :error
)
start "" ".venv\Scripts\pythonw.exe" -m recast
exit /b 0

:error
echo Setup failed. Make sure Python 3.12 or newer is installed.
pause
